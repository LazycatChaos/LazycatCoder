"""Core agent loop.

This is the heart of NanoCoder.  The pattern is simple:

    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

It keeps looping until the LLM responds with plain text (no tool calls),
which means it's done working and ready to report back.

Enhanced features (inspired by Claude Code QueryEngine):
- Real-time session persistence (auto-save on every message)
- Token usage tracking per conversation
- Error diagnostics with watermark isolation
- Context compression with automatic GC
"""

import concurrent.futures
import asyncio
import time
from typing import Optional, Callable
from .llm import LLM
from .tools import ALL_TOOLS, get_tool
from .tools.base import Tool
from .tools.agent import AgentTool
from .prompt import system_prompt
from .context import ContextManager
from .session import SessionManager, save_session


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool] | None = None,
        max_context_tokens: int = 128_000,
        max_rounds: int = 50,
        debug: bool = False,
        session_id: Optional[str] = None,
        auto_save: bool = True,
    ):
        self.llm = llm
        self.tools = tools if tools is not None else ALL_TOOLS
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self.debug = debug
        self._system = system_prompt(self.tools)
        
        # Session management (Claude Code style real-time persistence)
        self.session_id = session_id
        self.auto_save = auto_save
        self._session_manager = SessionManager(session_id) if auto_save else None
        
        # Token usage tracking
        self._total_tokens_input = 0
        self._total_tokens_output = 0
        self._round_count = 0
        
        # Error diagnostics with watermark isolation
        self._error_log: list[dict] = []
        self._error_watermark: int = 0  # Index at start of current turn

        # wire up sub-agent capability
        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self

    def _full_messages(self) -> list[dict]:
        return [{"role": "system", "content": self._system}] + self.messages

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(self, user_input: str, on_token=None, on_tool=None) -> str:
        """Process one user message. May involve multiple LLM/tool rounds."""
        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)
        
        # Record user message to session (critical - wait for completion)
        if self.auto_save and self._session_manager:
            asyncio.run(self._session_manager.record_message(
                self.messages, self.llm.model, is_critical=True
            ))

        if self.debug:
            from rich.console import Console
            console = Console()
            console.print(f"\n[bold magenta]>>> Starting chat round[/bold magenta] [dim](total messages: {len(self.messages)})[/dim]")

        round_count = 0
        for _ in range(self.max_rounds):
            round_count += 1
            self._round_count += 1
            
            # Set error watermark at start of each turn for isolation
            turn_watermark = len(self._error_log)
            
            if self.debug:
                from rich.console import Console
                console = Console()
                console.print(f"\n[dim]--- Round {round_count} ---[/dim]")
            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
            )
            
            # Track token usage
            if hasattr(resp, 'usage') and resp.usage:
                self._total_tokens_input += resp.usage.get('input_tokens', 0)
                self._total_tokens_output += resp.usage.get('output_tokens', 0)

            # no tool calls -> LLM is done, return text
            if not resp.tool_calls:
                self.messages.append(resp.message)
                if self.debug:
                    from rich.console import Console
                    console = Console()
                    content_preview = resp.content[:200] + "..." if len(resp.content) > 200 else resp.content
                    console.print(f"[bold green]>>> LLM responded with text[/bold green] [dim]({len(resp.content)} chars)[/dim]")
                    console.print(f"[dim]{content_preview}[/dim]")
                
                # Record assistant response (non-critical, lazy flush)
                if self.auto_save and self._session_manager:
                    asyncio.run(self._session_manager.record_message(
                        self.messages, self.llm.model, is_critical=False
                    ))
                
                return resp.content

            # tool calls -> execute (parallel when multiple, like Claude Code's
            # StreamingToolExecutor which runs independent tools concurrently)
            self.messages.append(resp.message)

            if self.debug:
                from rich.console import Console
                console = Console()
                console.print(f"[bold blue]>>> LLM requested {len(resp.tool_calls)} tool call(s)[/bold blue]")
                for i, tc in enumerate(resp.tool_calls, 1):
                    console.print(f"  [cyan]{i}. {tc.name}[/cyan]")

            if len(resp.tool_calls) == 1:
                tc = resp.tool_calls[0]
                if on_tool:
                    on_tool(tc.name, tc.arguments)
                result = self._exec_tool(tc)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            else:
                # parallel execution for multiple tool calls
                results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                for tc, result in zip(resp.tool_calls, results):
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

            # compress if tool outputs are big
            self.context.maybe_compress(self.messages, self.llm)

            # Layer 4: Autocompact - run periodically in background (every 10 rounds)
            if round_count % 20 == 0:
                self.context.autocompact(self.messages, self.llm)
                # After compaction, save the compressed state
                if self.auto_save and self._session_manager:
                    asyncio.run(self._session_manager.record_message(
                        self.messages, self.llm.model, is_critical=True
                    ))

        return "(reached maximum tool-call rounds)"

    def _exec_tool(self, tc) -> str:
        """Execute a single tool call, returning the result string."""
        tool = get_tool(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"
        
        if self.debug:
            from rich.console import Console
            console = Console()
            console.print(f"\n[bold cyan]>>> Executing tool:[/bold cyan] [yellow]{tc.name}[/yellow]")
            console.print(f"[dim]Arguments:[/dim]")
            for k, v in tc.arguments.items():
                val_str = repr(v) if isinstance(v, str) else str(v)
                if isinstance(v, str) and len(val_str) > 200:
                    val_str = val_str[:200] + "..."
                console.print(f"  [green]{k}[/green]: {val_str}")
            console.print(f"[dim]Tool is_read_only: {tool.is_read_only if tool else 'N/A'}[/dim]")
        
        try:
            result = tool.execute(**tc.arguments)
            
            if self.debug:
                from rich.console import Console
                console = Console()
                result_preview = result[:500] + "..." if len(result) > 500 else result
                # Replace newlines for cleaner display
                result_preview = result_preview.replace('\n', '\\n')
                console.print(f"[dim]Result ({len(result)} chars):[/dim] {result_preview}")
            
            return result
        except Exception as e:
            # Log error with watermark for turn-scoped isolation
            self._log_error(tc.name, tc.arguments, e)
            return f"Error executing {tc.name}: {e}"

    def _log_error(self, tool_name: str, arguments: dict, error: Exception):
        """Log an error with diagnostic info (Claude Code style)."""
        self._error_log.append({
            "turn": self._round_count,
            "tool": tool_name,
            "arguments": arguments,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "timestamp": time.time(),
        })

    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[str]:
        """Run multiple tool calls concurrently with safety for write operations.

        This mirrors Claude Code's concurrency model:
        - Read-only tools (read_file, grep, glob) run in parallel
        - Write tools (write_file, edit_file, bash) run sequentially to avoid race conditions
        
        Returns results in the same order as tool_calls for correct message pairing.
        """
        from .tools.base import Tool
        
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        # Separate read-only and write tools
        read_only_calls = []
        write_calls = []
        
        for tc in tool_calls:
            tool = get_tool(tc.name)
            if tool and tool.is_read_only:
                read_only_calls.append(tc)
            else:
                write_calls.append(tc)
        
        results_map = {}  # tool_call_id -> result
        
        # Execute read-only tools in parallel
        if read_only_calls:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(self._exec_tool, tc): tc for tc in read_only_calls}
                for future in concurrent.futures.as_completed(futures):
                    tc = futures[future]
                    results_map[tc.id] = future.result()
        
        # Execute write tools sequentially (order matters for correctness)
        for tc in write_calls:
            results_map[tc.id] = self._exec_tool(tc)
        
        # Return results in original order
        return [results_map[tc.id] for tc in tool_calls]

    def get_turn_errors(self) -> list[dict]:
        """Get errors from the current turn only (using watermark)."""
        return self._error_log[self._error_watermark:]

    def get_all_errors(self) -> list[dict]:
        """Get all errors from this session."""
        return self._error_log

    def get_usage_stats(self) -> dict:
        """Get token usage statistics."""
        return {
            "input_tokens": self._total_tokens_input,
            "output_tokens": self._total_tokens_output,
            "total_tokens": self._total_tokens_input + self._total_tokens_output,
            "rounds": self._round_count,
        }

    def get_session_stats(self) -> dict:
        """Get session statistics."""
        stats = {
            "session_id": self.session_id,
            "message_count": len(self.messages),
            "round_count": self._round_count,
            "error_count": len(self._error_log),
        }
        stats.update(self.get_usage_stats())
        if self._session_manager:
            stats.update(self._session_manager.get_stats())
        return stats

    def save_session(self) -> str:
        """Manually save the current session."""
        if not self.session_id:
            self.session_id = save_session(self.messages, self.llm.model)
        else:
            save_session(self.messages, self.llm.model, self.session_id)
        return self.session_id

    def reset(self):
        """Clear conversation history."""
        self.messages.clear()
        self._error_log.clear()
        self._error_watermark = 0
        self._round_count = 0
        # Don't reset session_id or token stats - keep for the session lifetime
