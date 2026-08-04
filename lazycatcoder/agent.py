"""Core agent loop.

This is the heart of LazyCatCoder.  The pattern is simple:

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
from .llm import LLM, StreamTimeoutError
from .tools import ALL_TOOLS, get_tool, registry
from .tools.base import Tool, ToolResult
from .tools.agent import AgentTool
from .tools.activate_tool import ActivateTool
from .prompt import build_system_prompt
from .context import ContextManager
from .session import SessionManager, save_session, load_session
from .message import AgentMessage, MessageRole, messages_to_llm_dicts, messages_to_dicts, dicts_to_messages

# Import LLM error types for exception handling
try:
    from openai import APITimeoutError, APIConnectionError, RateLimitError
except ImportError:
    # Fallback if openai is not installed
    APITimeoutError = Exception
    APIConnectionError = Exception
    RateLimitError = Exception


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
        system_prompt_override: Optional[str] = None,
    ):
        self.llm = llm
        self.registry = registry  # global, stateless — only tool definitions

        if tools is None:
            self.tools = list(registry.list_tools())
        else:
            self.tools = list(tools)
            # Ensure ActivateTool is always available — without it the LLM
            # has no way to activate extended tools.
            if not any(isinstance(t, ActivateTool) for t in self.tools):
                activate = registry.get("activate_tool")
                if activate is not None:
                    self.tools.append(activate)

        # Activation state — per-instance so parent/child agents don't pollute
        # each other.  Core tools are active by default; extended tools require
        # explicit activation via activate_tool.
        self.active_tool_names: set[str] = {
            t.name for t in self.tools
            if getattr(t, 'tier', 'core') == 'core'
        }
        self._dirty: bool = True  # first turn must build prompt

        self.messages: list[AgentMessage] = []
        self.context = ContextManager(max_tokens=max_context_tokens, model=llm.model)
        self.max_rounds = max_rounds
        self.debug = debug
        
        # Working directory (default: current directory)
        self.workdir = workdir if workdir is not None else os.getcwd()
        
        # Virtual environment path
        self.venv_path = venv_path
        
        # System prompt: use override if provided, otherwise generate from active tools
        if system_prompt_override:
            self._system = system_prompt_override
            self._dirty = False  # override is static, no rebuild needed
        else:
            self._system = self._build_prompt()
        
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

        # Tool call statistics (thread-safe)
        self._tool_stats: dict[str, dict] = {}
        self._stats_lock = threading.Lock()

        # wire up sub-agent capability and activate_tool callback
        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self
            if isinstance(t, ActivateTool):
                t._owner_agent = self
        
        # Pass workdir to tools that support it (e.g., BashTool)
        for t in self.tools:
            if hasattr(t, 'workdir'):
                t.workdir = self.workdir
            if hasattr(t, 'venv_path') and self.venv_path:
                t.venv_path = self.venv_path

        # Lock to protect messages during background autocompact
        self._messages_lock = threading.Lock()
        self._autocompact_running = False
        # Cancellation token (set by Ctrl+C or /cancel)
        # threading.Event is thread-safe and can be waited on with a timeout
        self._cancel_event = threading.Event()

        # Previous SIGINT handler — saved during chat() to restore later
        self._prev_sigint = None

        # Tool hooks — cross-cutting concerns (logging, permissions, audit, etc.)
        # Each hook: (name, before_execute, after_execute)
        self._tool_hooks: list[dict] = []

    @property
    def is_cancelled(self) -> bool:
        """True if the current operation has been cancelled by the user."""
        return self._cancel_event.is_set()

    def cancel(self):
        """Request cancellation of the current operation."""
        self._cancel_event.set()

    def _last_msg_id(self) -> str | None:
        """Return the id of the last message in the chain, or None if empty."""
        if self.messages:
            return self.messages[-1].id
        return None

    def _append(self, msg: AgentMessage) -> AgentMessage:
        """Append a message, auto-linking parent_id to the previous message.

        This is the single entry point for adding messages to the conversation.
        It ensures the Session Tree (id → parent_id chain) stays consistent
        without each call site having to manage parent_id manually.
        """
        if msg.parent_id is None and self.messages:
            msg.parent_id = self.messages[-1].id
        self.messages.append(msg)
        return msg

    def _cleanup_cancelled_messages(self):
        """Remove trailing messages that would violate the OpenAI message sequence.

        After cancellation, the last message might be an assistant message with
        tool_calls but no corresponding tool results — this causes a 400 error
        on the next chat() call.  We pop trailing messages until the sequence
        is valid again (ends with user/assistant-text/tool).
        """
        while self.messages:
            last = self.messages[-1]
            role = last.role if hasattr(last, 'role') else last.get('role', '')
            tool_calls = last.tool_calls if hasattr(last, 'tool_calls') else last.get('tool_calls')
            # If last message is assistant with tool_calls but no tool results
            # follow it, the sequence is incomplete — remove it.
            if role == 'assistant' and tool_calls:
                self.messages.pop()
            else:
                break

    def _start_input_listener(self):
        """Install a SIGINT handler so Ctrl+C during chat() sets the cancel
        event instead of raising KeyboardInterrupt.

        First Ctrl+C: sets _cancel_event (graceful cancel).
        Second Ctrl+C: restores default handler and raises KeyboardInterrupt
        (force-quit safety valve).

        Note: signal.signal() only works in the main thread.  If chat() is
        called from a worker thread (e.g. Web UI backend), this is a no-op
        and cancellation must be triggered by the caller via agent.cancel().
        """
        import signal

        if threading.current_thread() is not threading.main_thread():
            return  # Non-main thread: skip signal handling

        def _handler(signum, frame):
            if self._cancel_event.is_set():
                # Second Ctrl+C — restore default and force-quit
                signal.signal(signal.SIGINT, self._prev_sigint or signal.SIG_DFL)
                raise KeyboardInterrupt
            self._cancel_event.set()

        self._prev_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _handler)

    def _stop_input_listener(self):
        """Restore the previous SIGINT handler.

        Note: _prev_sigint may be prompt_toolkit's handler (saved during
        pt_prompt).  This is fine — prompt_toolkit re-installs its own
        handler on each prompt() call, so a stale restore here is harmless.
        The only risk is if chat() exits abnormally (e.g. os._exit) and
        finally doesn't run, leaving our handler installed.  That would
        cause Ctrl+C to set _cancel_event instead of interrupting pt_prompt.
        Risk is low since finally blocks run on normal exceptions.
        """
        import signal
        if threading.current_thread() is not threading.main_thread():
            return
        if self._prev_sigint is not None:
            signal.signal(signal.SIGINT, self._prev_sigint)
            self._prev_sigint = None

    def _full_messages(self) -> list[dict]:
        """Build the full message list for the LLM (system + history).

        Filters out messages where llm_visible=False (e.g. compact boundaries,
        internal notifications).  Returns plain dicts for the OpenAI API.
        """
        msgs = [AgentMessage(role=MessageRole.SYSTEM, content=self._system)]
        msgs.extend(self.messages)
        return messages_to_llm_dicts(msgs)

    def _active_tools(self) -> list[Tool]:
        """Currently active tool instances.  Single truth source for schemas and dispatch."""
        result = []
        for name in sorted(self.active_tool_names):
            tool = self.registry.get(name)
            if tool is not None:
                result.append(tool)
        return result

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self._active_tools()]

    def _build_prompt(self) -> str:
        """Build system prompt from active + inactive tools."""
        active = self._active_tools()
        all_tools = self.registry.all_tools()
        inactive = [
            t for n, t in all_tools.items()
            if n not in self.active_tool_names
        ]
        return build_system_prompt(active, inactive, self.workdir)

    def activate_tools(self, names: list[str]) -> tuple[list[str], list[str]]:
        """Activate extended tools by name.  Called by ActivateTool via callback.

        Returns (activated_names, skipped_names) where skipped includes
        already-active and unknown tools.
        """
        activated, skipped = [], []
        for name in names:
            tool = self.registry.get(name)
            if tool is None:
                skipped.append(name)
            elif name in self.active_tool_names:
                skipped.append(name)
            else:
                self.active_tool_names.add(name)
                activated.append(name)
        if activated:
            self._dirty = True
        return activated, skipped

    def chat(self, user_input: str, on_token=None, on_tool=None,
             on_reasoning=None, on_reasoning_end=None) -> str:
        """Process one user message. May involve multiple LLM/tool rounds."""
        self._append(AgentMessage(role=MessageRole.USER, content=user_input))
        self.context.maybe_compress(self.messages, self.llm)

        # Start background input listener so user can type /cancel mid-chat
        self._start_input_listener()
        
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

        try:
            for _ in range(self.max_rounds):
                round_count += 1
                self._round_count += 1

                # Check cancellation before starting this round
                if self._cancel_event.is_set():
                    self._cancel_event.clear()
                    self._cleanup_cancelled_messages()
                    return "⚠️ Operation cancelled by user."

                # Rebuild system prompt if active tool set changed (e.g. activate_tool)
                if self._dirty:
                    self._system = self._build_prompt()
                    self._dirty = False

                # Set error watermark at start of each turn for isolation
                turn_watermark = len(self._error_log)

                if self.debug:
                    from rich.console import Console
                    console = Console()
                    console.print(f"\n[dim]--- Round {round_count} ---[/dim]")

                try:
                    resp = self.llm.chat(
                        messages=self._full_messages(),
                        tools=self._tool_schemas(),
                        on_token=on_token,
                        on_reasoning=on_reasoning,
                        on_reasoning_end=on_reasoning_end,
                        debug=self.debug,
                    )
                except APIConnectionError as e:
                    self._log_error("api_error", f"Connection failed: {e}")
                    if self.debug:
                        from rich.console import Console
                        console = Console()
                        console.print(f"\n[bold red]>>> LLM connection failed: {e}[/bold red]")
                    return f"⚠️ Cannot connect to model service. Please check network or API configuration."
                except (APITimeoutError, StreamTimeoutError) as e:
                    self._log_error("api_error", f"Timeout: {e}")
                    if self.debug:
                        from rich.console import Console
                        console = Console()
                        console.print(f"\n[bold red]>>> LLM response timed out: {e}[/bold red]")
                    return f"⚠️ Model response timed out. Please try again or simplify your request."
                except RateLimitError as e:
                    self._log_error("api_error", f"Rate limit exceeded: {e}")
                    if self.debug:
                        from rich.console import Console
                        console = Console()
                        console.print(f"\n[bold red]>>> LLM rate limited: {e}[/bold red]")
                    return f"⚠️ API rate limit exceeded. Please wait and try again."
                except KeyboardInterrupt:
                    self._cancel_event.clear()
                    self._cleanup_cancelled_messages()
                    return "⚠️ Operation cancelled by user."
                except Exception as e:
                    self._log_error("api_error", f"Unexpected: {type(e).__name__}: {e}")
                    if self.debug:
                        from rich.console import Console
                        console = Console()
                        console.print(f"\n[bold red]>>> Unexpected LLM error: {e}[/bold red]")
                    return f"⚠️ Unexpected error: {e}"

                # Track token usage
                self._total_tokens_input += resp.prompt_tokens
                self._total_tokens_output += resp.completion_tokens

                # Log LLM parse errors (malformed tool call JSON from model)
                for pe in resp.parse_errors:
                    self._log_error("llm_parse_error", f"Tool '{pe['tool']}': {pe['error']}", tool=pe["tool"], arguments={"raw_args": pe.get("raw_args", "")})

                # no tool calls -> LLM is done, return text
                if not resp.tool_calls:
                    msg = AgentMessage.from_dict(resp.message)
                    if resp.reasoning:
                        MAX_REASONING_CHARS = 16000
                        r = resp.reasoning
                        if len(r) > MAX_REASONING_CHARS:
                            r = "... [前文已截断] ...\n" + r[-MAX_REASONING_CHARS:]
                        msg.metadata["reasoning"] = r
                    self._append(msg)
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
                msg = AgentMessage.from_dict(resp.message)
                if resp.reasoning:
                    MAX_REASONING_CHARS = 16000
                    r = resp.reasoning
                    if len(r) > MAX_REASONING_CHARS:
                        r = "... [前文已截断] ...\n" + r[-MAX_REASONING_CHARS:]
                    msg.metadata["reasoning"] = r
                self._append(msg)

                if self.debug:
                    from rich.console import Console
                    console = Console()
                    console.print(f"[bold blue]>>> LLM requested {len(resp.tool_calls)} tool call(s)[/bold blue]")
                    for i, tc in enumerate(resp.tool_calls, 1):
                        console.print(f"  [cyan]{i}. {tc.name}[/cyan]")

                has_error_in_this_round = False  #记录本轮是否有错误

                # Check cancellation before executing tools
                if self._cancel_event.is_set():
                    self._cancel_event.clear()
                    self._cleanup_cancelled_messages()
                    return "⚠️ Operation cancelled by user."

                try:
                    if len(resp.tool_calls) == 1:
                        tc = resp.tool_calls[0]
                        if on_tool:
                            on_tool(tc.name, tc.arguments)
                        result = self._exec_tool(tc)
                        self._append(AgentMessage(
                            role=MessageRole.TOOL,
                            content=result,
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                        ))
                        # Check if tool returned an error
                        if self._is_tool_error(result):
                            has_error_in_this_round = True
                    else:
                        # parallel execution for multiple tool calls
                        results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                        for tc, result in zip(resp.tool_calls, results):
                            self._append(AgentMessage(
                                role=MessageRole.TOOL,
                                content=result,
                                tool_call_id=tc.id,
                                tool_name=tc.name,
                            ))
                            # Check if tool returned an error
                            if self._is_tool_error(result):
                                has_error_in_this_round = True
                except KeyboardInterrupt:
                    self._cancel_event.clear()
                    self._cleanup_cancelled_messages()
                    return "⚠️ Operation cancelled by user."

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
                        self._append(AgentMessage(
                            role=MessageRole.SYSTEM, content=warning_msg,
                            llm_visible=False,
                        ))
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
        finally:
            self._stop_input_listener()

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
            if m.role == MessageRole.USER and m.content:
                content = m.content
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

    # ------------------------------------------------------------------
    # Tool hooks — before/after execute
    # ------------------------------------------------------------------

    def add_tool_hook(
        self,
        name: str,
        before_execute=None,
        after_execute=None,
    ):
        """Register a tool hook.

        Args:
            name: Unique hook identifier (for removal/debugging).
            before_execute: callable(tool, kwargs) -> dict | None | HookAbortError
                - Return dict to replace kwargs (modify arguments).
                - Return None to keep original kwargs.
                - Raise HookAbortError to abort (message becomes tool result).
            after_execute: callable(tool, kwargs, result) -> str | None
                - Return str to replace result content.
                - Return None to keep original result.
        """
        self._tool_hooks.append({
            "name": name,
            "before": before_execute,
            "after": after_execute,
        })

    def _run_before_hooks(self, tool, kwargs: dict) -> dict:
        """Run all before_execute hooks. Returns (possibly modified) kwargs."""
        from .tools.base import HookAbortError
        for hook in self._tool_hooks:
            fn = hook["before"]
            if fn is None:
                continue
            modified = fn(tool, kwargs)
            if modified is not None:
                kwargs = modified
        return kwargs

    def _run_after_hooks(self, tool, kwargs: dict, result: str) -> str:
        """Run all after_execute hooks. Returns (possibly modified) result."""
        for hook in self._tool_hooks:
            fn = hook["after"]
            if fn is None:
                continue
            modified = fn(tool, kwargs, result)
            if modified is not None:
                result = modified
        return result

    def _exec_tool(self, tc) -> str:
        """Execute a single tool call, returning the result string."""
        # Intercept calls to inactive tools — return error, not crash
        if tc.name not in self.active_tool_names:
            if self.registry.get(tc.name) is not None:
                return (
                    f"Error: Tool '{tc.name}' exists but is not active. "
                    f"Call activate_tool(tool_names=[\"{tc.name}\"]) first. "
                    f"It will be available next turn."
                )
            return f"Error: unknown tool '{tc.name}'"

        tool = self.registry.get(tc.name)
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
        
        start_time = time.perf_counter()
        is_error = False
        
        try:
            # Run before_execute hooks (may modify kwargs or abort)
            from .tools.base import HookAbortError
            try:
                kwargs = self._run_before_hooks(tool, dict(tc.arguments))
            except HookAbortError as abort:
                content = str(abort) or f"[{tc.name} aborted by hook]"
                if self.debug:
                    from rich.console import Console
                    from rich.panel import Panel
                    console = Console()
                    console.print(Panel(
                        content,
                        title=f"[yellow]⊘ {tc.name} (Hook Abort)[/yellow]",
                        border_style="yellow",
                    ))
                is_error = True
                return content

            # Execute with timeout protection (0 = no timeout)
            tool_timeout = getattr(tool, 'timeout', 120)
            if tool_timeout and tool_timeout > 0:
                with concurrent.futures.ThreadPoolExecutor(1) as pool:
                    future = pool.submit(tool.execute, **kwargs)
                    try:
                        result = future.result(timeout=tool_timeout)
                    except concurrent.futures.TimeoutError:
                        is_error = True
                        return f"Error: Tool '{tc.name}' timed out after {tool_timeout}s"
            else:
                result = tool.execute(**kwargs)

            # Handle ToolResult objects - extract content for LLM messages
            if isinstance(result, ToolResult):
                content = result.content
                is_error = not result.success
            else:
                # Legacy string return (backward compatibility)
                content = str(result)
                is_error = self._is_tool_error(content)

            # Run after_execute hooks (may modify result content)
            content = self._run_after_hooks(tool, kwargs, content)
            
            if self.debug:
                from rich.console import Console
                from rich.panel import Panel
                console = Console()
                # Show full result in a panel for readability
                result_display = content if len(content) <= 200 else content[:200] + f"\n\n... ({len(content)} total chars, truncated)"
                console.print(Panel(
                    result_display,
                    title=f"[green]✓ {tc.name}[/green] [dim]({len(content)} chars)[/dim]",
                    border_style="green" if not is_error else "red",
                ))
            
            return content
        except Exception as e:
            # Log error with watermark for turn-scoped isolation
            self._log_error("tool_crash", str(e), tool=tc.name, arguments=tc.arguments)
            if self.debug:
                from rich.console import Console
                from rich.panel import Panel
                console = Console()
                console.print(Panel(
                    str(e),
                    title=f"[red]✗ {tc.name} (Error)[/red]",
                    border_style="red",
                ))
            is_error = True
            return f"Error executing {tc.name}: {e}"
        finally:
            cost_time = time.perf_counter() - start_time
            with self._stats_lock:
                if tc.name not in self._tool_stats:
                    self._tool_stats[tc.name] = {"calls": 0, "success": 0, "failure": 0, "total_time": 0.0}
                stats = self._tool_stats[tc.name]
                stats["calls"] += 1
                stats["total_time"] += cost_time
                if is_error:
                    stats["failure"] += 1
                else:
                    stats["success"] += 1

    def _log_error(self, error_type: str, message: str, tool: str = None, arguments: dict = None):
        """Log a structured system error (not business-level tool failures).

        error_type categories:
        - "tool_crash":      tool raised an unhandled exception
        - "api_error":       LLM API failure (rate limit, timeout, connection)
        - "llm_parse_error": LLM output could not be parsed as valid tool call
        """
        self._error_log.append({
            "turn": self._round_count,
            "error_type": error_type,
            "message": message,
            "tool": tool,
            "arguments": arguments,
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
            tool = self.registry.get(tc.name)
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

    def get_error_summary(self) -> dict:
        """Get aggregated error statistics for analysis.

        Returns:
        {
            "total": 5,
            "by_type": {"api_error": 2, "tool_crash": 1, "llm_parse_error": 2},
            "by_tool": {"grep": 3, "read_file": 1, None: 1},
            "details": {
                "tool_crash": [
                    {"tool": "grep", "message": "...", "arguments": {...}, "turn": 2},
                ],
                "llm_parse_error": [
                    {"tool": "grep", "message": "...", "raw_args": "...", "turn": 3},
                ],
                "api_error": [
                    {"message": "...", "turn": 1},
                ],
            },
        }
        """
        by_type: dict[str, int] = {}
        by_tool: dict[str, int] = {}
        details: dict[str, list[dict]] = {}

        for e in self._error_log:
            etype = e.get("error_type", "unknown")
            tool = e.get("tool")

            by_type[etype] = by_type.get(etype, 0) + 1
            by_tool[tool] = by_tool.get(tool, 0) + 1

            details.setdefault(etype, []).append({
                "tool": tool,
                "message": e.get("message", ""),
                "turn": e.get("turn"),
                **({"raw_args": e.get("arguments", {}).get("raw_args", "")}
                   if etype == "llm_parse_error" and e.get("arguments") else {}),
                **({"arguments": e.get("arguments")}
                   if etype == "tool_crash" and e.get("arguments") else {}),
            })

        return {
            "total": len(self._error_log),
            "by_type": by_type,
            "by_tool": by_tool,
            "details": details,
        }

    def get_usage_stats(self) -> dict:
        """Get token usage statistics."""
        return {
            "input_tokens": self._total_tokens_input,
            "output_tokens": self._total_tokens_output,
            "total_tokens": self._total_tokens_input + self._total_tokens_output,
            "rounds": self._round_count,
        }

    def get_tool_stats(self) -> dict[str, dict]:
        """Get tool call statistics.

        Returns dict like:
        {
            "read_file": {"calls": 3, "success": 3, "failure": 0, "total_time": 1.25},
            "bash": {"calls": 2, "success": 1, "failure": 1, "total_time": 45.1},
        }
        """
        with self._stats_lock:
            return {k: dict(v) for k, v in self._tool_stats.items()}

    def clear_stats(self):
        """Clear all tool call statistics.

        Call this explicitly if reusing the same Agent instance for different tasks.
        """
        with self._stats_lock:
            self._tool_stats.clear()

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
            self.session_id = save_session(self.messages, self.llm.model, system_errors=self.get_all_errors())
        else:
            save_session(self.messages, self.llm.model, self.session_id, system_errors=self.get_all_errors())
        return self.session_id

    def flush_session(self):
        """Flush any pending async saves before exit."""
        if self.auto_save and self._session_manager:
            try:
                self._run_async(self._session_manager.flush(
                    self.messages, self.llm.model, system_errors=self.get_all_errors()
                ), timeout=10)
            except Exception:
                pass  # Don't crash on exit if flush fails

    def restore_error_history(self, historical_errors: list[dict]):
        """Restore historical error records (used for session resume).

        Ensures the error watermark is pushed past all restored entries so
        they are not mistaken for new errors in the current turn.
        """
        if historical_errors:
            self._error_log = list(historical_errors)
            self._error_watermark = len(self._error_log)

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
