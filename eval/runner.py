"""Runner: use the real LazyCatCoder Agent to execute a task in a sandbox."""

import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so we can import lazycatcoder
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from lazycatcoder.llm import LLM
from lazycatcoder.agent import Agent
from lazycatcoder.config import Config


class EvalRunner:
    """Run a single eval case using the real Agent.

    Each instance creates a fresh Agent to avoid state pollution.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_rounds: int = 10,
        timeout: int = 120,
        debug: bool = False,
        system_prompt: str | None = None,
    ):
        # Resolve config from env, then override with explicit args
        config = Config.from_env()
        self.model = model or config.model
        self.api_key = api_key or config.api_key
        self.base_url = base_url or config.base_url
        self.max_rounds = max_rounds
        self.timeout = timeout
        self.debug = debug
        self.system_prompt = system_prompt

    def run(self, workdir: str, prompt: str) -> dict:
        """Run the agent against a prompt in the given workdir.

        IMPORTANT: We os.chdir() into the sandbox so that tools like
        read_file, glob, edit_file (which default to Path.cwd()) operate
        inside the sandbox instead of the project root.

        Returns:
            {
                "success": bool,       # Did the agent finish (not hit max_rounds)?
                "response": str,       # Final text response from the agent
                "rounds": int,         # Number of agent loop rounds used
                "total_tokens": int,   # Total tokens consumed
                "error": str | None,   # Error message if any
            }
        """
        original_cwd = os.getcwd()
        os.chdir(workdir)

        llm = LLM(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            temperature=0.0,  # Deterministic for eval
        )

        agent = Agent(
            llm=llm,
            max_rounds=self.max_rounds,
            debug=self.debug,
            workdir=workdir,
            system_prompt_override=self.system_prompt,
        )

        try:
            response = agent.chat(prompt)
            usage = agent.get_usage_stats()
            tool_stats = agent.get_tool_stats()
            return {
                "success": True,
                "response": response,
                "rounds": usage["rounds"],
                "total_tokens": usage["total_tokens"],
                "tool_stats": tool_stats,
                "error": None,
            }
        except Exception as e:
            return {
                "success": False,
                "response": "",
                "rounds": 0,
                "total_tokens": 0,
                "tool_stats": {},
                "error": str(e),
            }
        finally:
            # Always restore original cwd
            os.chdir(original_cwd)
