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
import threading
import time
import os
import re
from pathlib import Path
from typing import Optional, Callable
from .llm import LLM
from .tools import ALL_TOOLS, get_tool
from .tools.base import Tool
from .tools.agent import AgentTool
from .prompt import system_prompt
from .context import ContextManager
from .session import SessionManager, save_session, load_session


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool] | None = None,
        max_context_tokens: int = 200_000,
        max_rounds: int = 50,
        debug: bool = False,
        session_id: Optional[str] = None,
        auto_save: bool = True,
        workdir: Optional[str] = None,
        venv_path: Optional[str] = None,
    ):
        self.llm = llm
        self.tools = tools if tools is not None else ALL_TOOLS
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens, model=llm.model)
        self.max_rounds = max_rounds
        self.debug = debug
        
        # Working directory (default: current directory)
        self.workdir = workdir if workdir is not None else os.getcwd()
        
        # Virtual environment path
        self.venv_path = venv_path
        
        # System prompt with workdir
        self._system = system_prompt(self.tools, self.workdir)
        
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
        
        # Pass workdir to tools that support it (e.g., BashTool)
        for t in self.tools:
            if hasattr(t, 'workdir'):
                t.workdir = self.workdir
            if hasattr(t, 'venv_path') and self.venv_path:
                t.venv_path = self.venv_path

        # Lock to protect messages during background autocompact
        self._messages_lock = threading.Lock()
        self._autocompact_running = False
        # Cancellation flag (set by Ctrl+C or /cancel)
        self._cancelled = False

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
            try:
                self._run_async(self._session_manager.record_message(
                    self.messages, self.llm.model, is_critical=True
                ))
            except Exception as e:
                if self.debug:
                    from rich.console import Console
                    console = Console()
                    console.print(f"[yellow]⚠ Session save failed: {e}[/yellow]")

        if self.debug:
            from rich.console import Console
            console = Console()
            console.print(f"\n[bold magenta]>>> Starting chat round[/bold magenta] [dim](total messages: {len(self.messages)})[/dim]")

        round_count = 0
        consecutive_errors = 0  # 新增：记录连续错误次数

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
                debug=self.debug,
            )
            
            # Track token usage
            self._total_tokens_input += resp.prompt_tokens
            self._total_tokens_output += resp.completion_tokens

            # no tool calls -> LLM is done, return text
            if not resp.tool_calls:
                self.messages.append(resp.message)
                if self.debug:
                    from rich.console import Console
                    console = Console()
                    content_preview = resp.content[:200] + "..." if len(resp.content) > 200 else resp.content
                    console.print(f"\n[bold green]>>> LLM responded with text[/bold green] [dim]({len(resp.content)} chars)[/dim]")
                    console.print(f"[dim]{content_preview}[/dim]")
                
                # Record assistant response (non-critical, fire-and-forget)
                if self.auto_save and self._session_manager:
                    self._fire_and_forget_save()
                
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

            has_error_in_this_round = False  #记录本轮是否有错误

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
                # Check if tool returned an error
                if self._is_tool_error(result):
                    has_error_in_this_round = True
            else:
                # parallel execution for multiple tool calls
                results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                for tc, result in zip(resp.tool_calls, results):
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                    # Check if tool returned an error
                    if self._is_tool_error(result):
                        has_error_in_this_round = True

            # Auto-save after tool results (non-critical, fire-and-forget)
            # This prevents losing tool outputs if the process crashes mid-session
            if self.auto_save and self._session_manager:
                self._fire_and_forget_save()

            # 新增：防死循环 / 熔断机制核心逻辑
            if has_error_in_this_round:
                consecutive_errors += 1
                if consecutive_errors >= 2:  # 如果连续 2 轮都发生工具调用错误
                    # Detect user language from recent messages for better compliance
                    lang = self._detect_language()
                    if lang == "zh":
                        warning_msg = (
                            "系统警告：你多次调用工具失败，似乎陷入了死循环。"
                            "请立即停止重复调用同一个工具。"
                            "如果你是想向用户输出代码或文档，请直接用 Markdown 格式输出，不要再调用任何工具。"
                        )
                    else:
                        warning_msg = (
                            "SYSTEM WARNING: You have repeatedly failed to call tools correctly. "
                            "You seem to be stuck in a loop calling a tool with missing or wrong arguments. "
                            "STOP calling the same tool. "
                            "If you are trying to output documentation or code to the user, just provide it directly in plain text/Markdown right now."
                        )
                    # 注入 system 消息避免破坏 assistant→tool→assistant 的合法序列
                    self.messages.append({"role": "system", "content": warning_msg})
                    consecutive_errors = 0  # 重置计数，给它一次听话的机会

                    if self.debug:
                        from rich.console import Console
                        console = Console()
                        console.print(
                            f"\n[bold red]>>> System injected circuit-breaker warning due to loop.[/bold red]")
            else:
                consecutive_errors = 0  # 工具执行成功，重置计数
            #===================================

            # compress if tool outputs are big
            with self._messages_lock:
                self.context.maybe_compress(self.messages, self.llm)

            # Layer 4: Autocompact - trigger by token usage, not fixed rounds.
            # This adapts to any workload: short Q&A won't waste LLM calls,
            # heavy file operations compress before hitting the wall.
            if not self._autocompact_running:
                usage = self.context.token_usage(self.messages)
                if self.context.should_autocompact(usage):
                    self._autocompact_running = True
                    threading.Thread(
                        target=self._background_autocompact,
                        daemon=True,
                    ).start()

        return "(reached maximum tool-call rounds)"

    @staticmethod
    def _is_tool_error(result: str) -> bool:
        """Check if a tool result indicates an error (first line starts with 'Error:')."""
        return result.splitlines()[0].startswith("Error:") if result else False

    def _detect_language(self) -> str:
        """Detect user's language from recent messages. Returns 'zh' or 'en'."""
        # Check last 6 user messages for Chinese characters
        zh_count = 0
        checked = 0
        for m in reversed(self.messages):
            if m.get("role") == "user" and m.get("content"):
                content = m["content"]
                zh_count += sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
                checked += 1
                if checked >= 6:
                    break
        return "zh" if zh_count > 5 else "en"

    def _ensure_session_id(self):
        """Ensure session_id is set, generating one if needed."""
        if not self.session_id:
            self.session_id = save_session(self.messages, self.llm.model)
        return self.session_id

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
                from rich.panel import Panel
                console = Console()
                # Show full result in a panel for readability
                result_display = result if len(result) <= 200 else result[:200] + f"\n\n... ({len(result)} total chars, truncated)"
                console.print(Panel(
                    result_display,
                    title=f"[green]✓ {tc.name}[/green] [dim]({len(result)} chars)[/dim]",
                    border_style="green",
                ))
            
            return result
        except Exception as e:
            # Log error with watermark for turn-scoped isolation
            self._log_error(tc.name, tc.arguments, e)
            if self.debug:
                from rich.console import Console
                from rich.panel import Panel
                console = Console()
                console.print(Panel(
                    str(e),
                    title=f"[red]✗ {tc.name} (Error)[/red]",
                    border_style="red",
                ))
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

    def _background_autocompact(self):
        """Run autocompact in a background daemon thread.

        Strategy: copy messages under lock → release lock → LLM summarizes
        the copy → re-acquire lock → swap.  The main loop is only blocked
        for the brief copy/swap, never for the LLM call.
        """
        try:
            # Step 1: snapshot messages under lock
            acquired = self._messages_lock.acquire(timeout=2)
            if not acquired:
                return  # Main loop is busy, skip this round
            snapshot = list(self.messages)  # shallow copy of list
            self._messages_lock.release()

            # Step 2: delegate to context.autocompact (handles all guard checks)
            old_count = len(snapshot)
            compressed = self.context.autocompact(snapshot, self.llm)
            if not compressed:
                return  # Not enough history or already compressed recently

            # Step 3: swap under lock
            acquired = self._messages_lock.acquire(timeout=5)
            if not acquired:
                return  # Main loop is busy, discard result
            self.messages[:] = snapshot  # in-place replacement
            self.context._last_autocompact_tokens = self.context.token_usage(self.messages)
            self._messages_lock.release()

            # Step 4: save after successful compaction
            if self.auto_save and self._session_manager:
                try:
                    self._run_async(self._session_manager.record_message(
                        self.messages, self.llm.model, is_critical=True
                    ))
                except Exception:
                    pass  # Don't crash the thread on save failure
        finally:
            self._autocompact_running = False
            # Ensure lock is released if we still hold it
            if self._messages_lock.locked():
                self._messages_lock.release()

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

    def flush_session(self):
        """Flush any pending async saves before exit."""
        if self.auto_save and self._session_manager:
            try:
                self._run_async(self._session_manager.flush(
                    self.messages, self.llm.model
                ), timeout=10)
            except Exception:
                pass  # Don't crash on exit if flush fails

    def reset(self):
        """Clear conversation history."""
        self.messages.clear()
        self._error_log.clear()
        self._error_watermark = 0
        self._round_count = 0
        # Don't reset session_id or token stats - keep for the session lifetime

    def _fire_and_forget_save(self):
        """Schedule a non-critical session save without blocking.
        
        Uses a background thread so the main loop never waits on disk I/O.
        """
        if not self.auto_save or not self._session_manager:
            return
        def _save_bg():
            try:
                self._run_async(self._session_manager.record_message(
                    self.messages, self.llm.model, is_critical=False
                ))
            except Exception:
                pass  # Silent failure for non-critical saves
        threading.Thread(target=_save_bg, daemon=True).start()

    @staticmethod
    def _run_async(coro, timeout: int = 30):
        """Run an async coroutine safely, handling existing event loops."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # Already in an event loop: schedule and wait synchronously
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            future.result(timeout=timeout)
        else:
            asyncio.run(coro)
