"""Sub-agent spawning (inspired by Claude Code's AgentTool, 1397 lines).

The idea: for complex sub-tasks, spawn an independent agent with its own
conversation history and tool access. This lets the main agent delegate
work like "go research this codebase and report back" without polluting
its own context window.

The sub-agent runs to completion and returns a text summary.
"""

from .base import Tool, ToolResult


class AgentTool(Tool):
    name = "agent"
    tier = "extended"
    description = (
        "Spawn a sub-agent to handle a complex sub-task independently. "
        "The sub-agent has its own context and tool access. Use this for: "
        "researching a codebase, implementing a multi-step change in isolation, "
        "or any task that would benefit from a fresh context window."
    )

    prompt_snippet = "Spawn a sub-agent for complex sub-tasks."
    prompt_guidelines = []

    # Sub-agent runs multiple rounds (up to 20), each with LLM calls + tool
    # execution.  120s (base default) is far too short — 600s (10 min) gives
    # it room to complete meaningful work.  If it still times out, the task
    # is likely too large for a single sub-agent.
    timeout = 600

    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What the sub-agent should accomplish",
            },
        },
        "required": ["task"],
    }

    # set by Agent.__init__ after construction
    _parent_agent = None

    def execute(self, task: str) -> ToolResult:
        if self._parent_agent is None:
            return ToolResult(success=False, content="Error: agent tool not initialized (no parent agent)", error_code=1)

        # import here to avoid circular dep
        from ..agent import Agent

        parent = self._parent_agent
        sub = Agent(
            llm=parent.llm,
            tools=[t for t in parent.tools if t.name != "agent"],  # no recursive agents
            max_context_tokens=parent.context.max_tokens,
            max_rounds=20,
            # Sub-agent starts from core tier only — does NOT inherit parent's
            # activated extended tools.  Sub-agent's activate_tool only affects
            # its own active_tool_names, not the parent's.
        )

        try:
            result = sub.chat(task)
            # Handle ToolResult from sub-agent
            if isinstance(result, ToolResult):
                content = result.content
            else:
                content = str(result)
            # trim long results to avoid blowing up parent's context
            if len(content) > 5000:
                content = content[:4500] + "\n... (sub-agent output truncated)"
            return ToolResult(success=True, content=f"[Sub-agent completed]\n{content}")
        except Exception as e:
            return ToolResult(success=False, content=f"Sub-agent error: {e}", error_code=99)
