"""Interactive REPL - the user-facing terminal interface."""

import sys
import os
import argparse

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import FileHistory

from .agent import Agent
from .llm import LLM
from .config import Config
from .session import load_session, list_sessions
from . import __version__

console = Console()


def _enable_vt100():
    """Enable VT100 processing on Windows for faster terminal I/O.

    Without this, prompt_toolkit falls back to slow Win32Output (per-char
    Console API calls), making text deletion very laggy on long inputs.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _parse_args():
    p = argparse.ArgumentParser(
        prog="lazycatcoder",
        description="Minimal AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Model name (default: $LAZYCATCODER_MODEL or gpt-4o)")
    p.add_argument("--base-url", help="API base URL (default: $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (default: $OPENAI_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument("--debug", action="store_true", help="Enable debug mode with verbose output")
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-w", "--workdir", help="Working directory (default: current directory)")
    p.add_argument("--venv", help="Virtual environment path (auto-detected if not specified)")
    return p.parse_args()


def main():
    args = _parse_args()
    config = Config.from_env()

    # CLI args override env vars
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key
    if args.venv:
        config.venv_path = args.venv

    if not config.api_key:
        console.print("[red bold]No API key found.[/]")
        console.print(
            "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or LAZYCATCODER_API_KEY\n"
            "\nExamples:\n"
            "  # OpenAI\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  # DeepSeek\n"
            "  export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com\n"
            "\n"
            "  # Ollama (local)\n"
            "  export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 LAZYCATCODER_MODEL=qwen2.5-coder\n"
        )
        sys.exit(1)

    llm = LLM(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        enable_thinking=config.enable_thinking,
    )
    
    # Determine working directory: CLI arg > current directory
    workdir = args.workdir if args.workdir else os.getcwd()
    
    # Resolve virtual environment
    venv_path = config.resolve_venv(workdir)
    if venv_path and config.debug:
        console.print(f"[dim]Using virtual environment: {venv_path}[/dim]")
    
    agent = Agent(
        llm=llm,
        max_context_tokens=config.max_context_tokens,
        debug=args.debug,
        workdir=workdir,
        venv_path=venv_path,
    )

    # resume saved session
    if args.resume:
        loaded = load_session(args.resume)
        if loaded:
            agent.messages, loaded_model, metadata = loaded
            if metadata.system_errors:
                agent.restore_error_history(metadata.system_errors)
            if metadata.files_touched:
                agent.restore_touched_files(metadata.files_touched)
            console.print(f"[green]Resumed session: {args.resume}[/green]")

            # Working directory safety check
            if metadata.working_dir:
                current_dir = os.path.normpath(os.getcwd())
                saved_dir = os.path.normpath(metadata.working_dir)
                if current_dir != saved_dir:
                    console.print(f"[yellow bold]⚠ WARNING: Working directory mismatch![/yellow bold]")
                    console.print(f"[yellow]  Session was created in: {saved_dir}[/yellow]")
                    console.print(f"[yellow]  Current directory:       {current_dir}[/yellow]")
                    console.print(f"[yellow]  File operations may target the wrong project.[/yellow]")
                    try:
                        confirm = input("Continue anyway? (y/N): ").strip().lower()
                        if confirm not in ("y", "yes"):
                            console.print("[dim]Resume cancelled.[/dim]")
                            sys.exit(0)
                    except (EOFError, KeyboardInterrupt):
                        console.print("\n[dim]Resume cancelled.[/dim]")
                        sys.exit(0)

            # Show session health status
            if metadata.system_errors:
                error_types = [e.get("error_type", "") for e in metadata.system_errors]
                if "api_error" in error_types:
                    console.print(f"[yellow]🔌 This session was previously interrupted by API/Network issues ({error_types.count('api_error')} times)[/yellow]")
                if "tool_crash" in error_types:
                    crashed = set(e.get("tool", "?") for e in metadata.system_errors if e.get("error_type") == "tool_crash")
                    console.print(f"[yellow]💥 This session had tool failures: {', '.join(crashed)}[/yellow]")
                if "llm_parse_error" in error_types:
                    console.print(f"[yellow]🧠 This session had LLM parsing difficulties ({error_types.count('llm_parse_error')} times)[/yellow]")
            else:
                console.print("[green]✅ Session status: Healthy[/green]")
        else:
            console.print(f"[red]Session '{args.resume}' not found.[/red]")
            sys.exit(1)

    # one-shot mode
    if args.prompt:
        _run_once(agent, args.prompt)
        return

    # interactive REPL
    _repl(agent, config)


def make_reasoning_handlers():
    """Create on_reasoning / on_reasoning_end callbacks with state tracking.

    on_reasoning: prints reasoning text in dim grey with 💭 prefix.
    on_reasoning_end: closes the reasoning display block (only if started).

    Uses style="dim", highlight=False for performance — avoids per-chunk
    markup parsing and syntax highlight detection that causes flicker.
    """
    state = {"started": False}

    def on_reasoning(text):
        if not state["started"]:
            console.print("\n[dim]>> Thinking:[/dim]", end="")
            state["started"] = True
        console.print(text, end="", style="dim", highlight=False)

    def on_reasoning_end():
        if state["started"]:
            console.print("\n")  # 空行分隔思考与正文
            state["started"] = False

    return on_reasoning, on_reasoning_end


def _run_once(agent: Agent, prompt: str):
    """Non-interactive: run one prompt and exit."""
    on_reasoning, on_reasoning_end = make_reasoning_handlers()

    def on_token(tok):
        print(tok, end="", flush=True)

    def on_tool(name, kwargs):
        console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

    try:
        agent.chat(prompt, on_token=on_token, on_tool=on_tool,
                   on_reasoning=on_reasoning, on_reasoning_end=on_reasoning_end)
    finally:
        agent.flush_session()
    print()


def _repl(agent: Agent, config: Config):
    """Interactive read-eval-print loop."""
    _enable_vt100()
    debug_badge = " [yellow bold]DEBUG[/]" if config.debug else ""
    console.print(Panel(
        f"[bold]LazyCatCoder[/bold] v{__version__}{debug_badge}\n"
        f"Model: [cyan]{config.model}[/cyan]"
        + (f"  Base: [dim]{config.base_url}[/dim]" if config.base_url else "")
        + f"  Timeout: [dim]{config.timeout}s[/dim]"
        + (f"  Thinking: [dim]on[/dim]" if config.enable_thinking else "")
        + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit.",
        border_style="blue",
    ))

    hist_path = os.path.expanduser("~/.lazycatcoder_history")
    history = FileHistory(hist_path)

    while True:
        try:
            user_input = pt_prompt("You > ", history=history).strip()
        except (EOFError, KeyboardInterrupt):
            agent.flush_session()
            console.print("\nBye!")
            break

        if not user_input:
            continue

        # built-in commands
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            agent.flush_session()
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/reset":
            # Auto-save current session before resetting (if there's anything to save)
            if agent.messages:
                try:
                    sid = agent.save_session()
                    console.print(f"[dim]Current session saved: {sid}[/dim]")
                except Exception as e:
                    console.print(f"[yellow]⚠ Failed to save before reset: {e}[/yellow]")
            agent.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue
        if user_input == "/cancel":
            agent._cancel_event.set()
            console.print("[yellow]Cancelling current operation...[/yellow]")
            console.print("[dim]Tip: Press Ctrl+C during tool execution to cancel.[/dim]")
            continue
        if user_input == "/debug":
            config.debug = not config.debug
            agent.debug = config.debug
            status = "on" if config.debug else "off"
            console.print(f"Debug mode [bold]{status}[/bold]")
            continue
        if user_input == "/think":
            config.enable_thinking = not config.enable_thinking
            agent.llm.enable_thinking = config.enable_thinking
            status = "on" if config.enable_thinking else "off"
            console.print(f"Deep thinking [bold]{status}[/bold]")
            continue
        if user_input == "/clear_thoughts":
            count = 0
            for msg in agent.messages:
                if "reasoning" in msg.metadata:
                    del msg.metadata["reasoning"]
                    count += 1
            console.print(f"Cleared reasoning from {count} messages")
            if agent.auto_save and agent._session_manager:
                try:
                    agent._run_async(agent._session_manager.record_message(
                        agent.messages, agent.llm.model, is_critical=True
                    ))
                except Exception as e:
                    console.print(f"[yellow]⚠ Save failed: {e}[/yellow]")
            continue
        if user_input.startswith("/workdir"):
            parts = user_input.split(None, 1)
            if len(parts) < 2:
                console.print(f"Current workdir: [cyan]{agent.workdir}[/cyan]")
            else:
                new_dir = os.path.abspath(os.path.expanduser(parts[1]))
                if os.path.isdir(new_dir):
                    agent.workdir = new_dir
                    # Sync: update system prompt and tools that support workdir
                    from .prompt import system_prompt
                    agent._system = system_prompt(agent.tools, new_dir)
                    for t in agent.tools:
                        if hasattr(t, 'workdir'):
                            t.workdir = new_dir
                    console.print(f"Workdir set to [cyan]{new_dir}[/cyan]")
                else:
                    console.print(f"[red]Directory not found: {new_dir}[/red]")
            continue
        if user_input.startswith("/venv"):
            parts = user_input.split(None, 1)
            if len(parts) < 2:
                venv = agent.venv_path
                console.print(f"Current venv: [cyan]{venv or '(none, auto-detect)'}[/cyan]")
            else:
                new_venv = os.path.abspath(os.path.expanduser(parts[1]))
                from pathlib import Path
                scripts_dir = "Scripts" if os.name == "nt" else "bin"
                if (Path(new_venv) / scripts_dir).exists():
                    agent.venv_path = new_venv
                    for t in agent.tools:
                        if hasattr(t, 'venv_path'):
                            t.venv_path = new_venv
                    console.print(f"Venv set to [cyan]{new_venv}[/cyan]")
                else:
                    console.print(f"[red]Not a valid virtual environment: {new_venv}[/red]")
            continue
        if user_input == "/tokens":
            p = agent.llm.total_prompt_tokens
            c = agent.llm.total_completion_tokens
            console.print(f"Tokens used this session: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p+c}[/bold] total")
            continue
        if user_input == "/usage":
            from .context import estimate_tokens
            stats = agent.get_usage_stats()
            ctx_tokens = estimate_tokens(agent.messages)
            ctx_pct = ctx_tokens / agent.context.max_tokens * 100
            console.print(Panel(
                f"[bold]Token Usage Report[/bold]\n\n"
                f"  Prompt tokens:       [cyan]{stats['input_tokens']}[/cyan]\n"
                f"  Completion tokens:   [cyan]{stats['output_tokens']}[/cyan]\n"
                f"  Total tokens:        [bold]{stats['total_tokens']}[/bold]\n"
                f"  Rounds:              {stats['rounds']}\n"
                + (f"  Avg prompt/round:    {stats['input_tokens'] // max(stats['rounds'], 1)}\n"
                   f"  Avg completion/round: {stats['output_tokens'] // max(stats['rounds'], 1)}\n"
                   if stats['rounds'] > 0 else "")
                + f"\n  Context tokens:      {ctx_tokens} / {agent.context.max_tokens} ({ctx_pct:.1f}%)\n"
                f"  Messages in context: {len(agent.messages)}\n"
                f"  Errors this session: {agent.get_error_summary()['total']}"
                + (lambda s: (
                    "\n  └ API errors:        " + str(s["by_type"].get("api_error", 0)) +
                    "\n  └ Tool crashes:      " + str(s["by_type"].get("tool_crash", 0)) +
                    "\n  └ LLM parse errors:  " + str(s["by_type"].get("llm_parse_error", 0))
                ) if s["total"] else "")(agent.get_error_summary()),
                title="Usage",
                border_style="cyan",
            ))
            continue
        if user_input == "/errors":
            summary = agent.get_error_summary()
            if summary["total"] == 0:
                console.print("[green]✅ No errors in this session.[/green]")
            else:
                lines = [f"[bold]Error Analysis Report[/bold] ({summary['total']} total)\n"]
                # By type
                lines.append("[dim]By type:[/dim]")
                for etype, count in sorted(summary["by_type"].items(), key=lambda x: -x[1]):
                    label = {"api_error": "🔌 API errors", "tool_crash": "💥 Tool crashes", "llm_parse_error": "🧠 LLM parse errors"}.get(etype, f"⚠️ {etype}")
                    lines.append(f"  {label}: {count}")
                # By tool
                if summary["by_tool"]:
                    lines.append("\n[dim]By tool:[/dim]")
                    for tool, count in sorted(summary["by_tool"].items(), key=lambda x: -x[1]):
                        lines.append(f"  {tool or '(N/A)'}: {count}")
                # Details for tool_crash and llm_parse_error
                for etype, label in [("tool_crash", "💥 Tool crash details"), ("llm_parse_error", "🧠 LLM parse error details")]:
                    entries = summary["details"].get(etype, [])
                    if entries:
                        lines.append(f"\n[dim]{label}:[/dim]")
                        for i, e in enumerate(entries, 1):
                            lines.append(f"  [{i}] turn {e.get('turn', '?')} | {e.get('tool', '?')}: {e.get('message', '')}")
                            if etype == "llm_parse_error" and e.get("raw_args"):
                                lines.append(f"      raw_args: {e['raw_args'][:120]}")
                            elif etype == "tool_crash" and e.get("arguments"):
                                lines.append(f"      arguments: {str(e['arguments'])[:120]}")
                console.print(Panel("\n".join(lines), title="Errors", border_style="yellow"))
            continue
        if user_input.startswith("/model "):
            new_model = user_input[7:].strip()
            if new_model:
                agent.llm.model = new_model
                agent.context.model = new_model  # Sync tokenizer model
                config.model = new_model
                console.print(f"Switched to [cyan]{new_model}[/cyan]")
            continue
        if user_input.startswith("/timeout "):
            try:
                new_timeout = int(user_input[9:].strip())
                if new_timeout > 0:
                    agent.llm.client.timeout = new_timeout
                    config.timeout = new_timeout
                    console.print(f"Timeout set to [cyan]{new_timeout}s[/cyan]")
                else:
                    console.print("[red]Timeout must be positive.[/red]")
            except ValueError:
                console.print("[red]Invalid timeout value.[/red]")
            continue
        if user_input == "/compact":
            from .context import estimate_tokens
            before = estimate_tokens(agent.messages)
            compressed = agent.context.maybe_compress(agent.messages, agent.llm)
            after = estimate_tokens(agent.messages)
            if compressed:
                console.print(f"[green]Compressed: {before} → {after} tokens ({len(agent.messages)} messages)[/green]")
            else:
                console.print(f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]")
            continue
        if user_input == "/save":
            sid = agent.save_session()
            console.print(f"[green]Session saved: {sid}[/green]")
            console.print(f"Resume with: lazycatcoder -r {sid}")
            continue
        if user_input == "/sessions":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]No saved sessions.[/dim]")
            else:
                for s in sessions:
                    d = s.to_dict()
                    # Error status indicator
                    if d.get("system_errors"):
                        error_types = [e.get("error_type", "") for e in d["system_errors"]]
                        if "api_error" in error_types:
                            icon = "🔌"
                        elif "tool_crash" in error_types:
                            icon = "💥"
                        elif "llm_parse_error" in error_types:
                            icon = "🧠"
                        else:
                            icon = "⚠️"
                    else:
                        icon = "✅"
                    # File touch indicators
                    file_strs = s._file_display_list()
                    files_info = f" 📁{', '.join(file_strs[:3])}" + ("..." if len(file_strs) > 3 else "") if file_strs else ""
                    # Usage indicators (cumulative tokens)
                    usage = d.get("usage") or {}
                    tokens = usage.get("total_tokens")
                    rounds = usage.get("rounds")
                    usage_info = ""
                    if tokens:
                        usage_info = f" [magenta]⚡{tokens:,} tok"
                        if rounds:
                            usage_info += f"/{rounds}r"
                        usage_info += "[/magenta]"
                    console.print(f"  {icon} [cyan]{d['id']}[/cyan] ({d['model']}, {d['saved_at']}) {d['summary']}{files_info}{usage_info}")
            continue

        # call the agent
        streamed: list[str] = []
        on_reasoning, on_reasoning_end = make_reasoning_handlers()

        def on_token(tok):
            streamed.append(tok)
            print(tok, end="", flush=True)

        def on_tool(name, kwargs):
            console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

        try:
            response = agent.chat(user_input, on_token=on_token, on_tool=on_tool,
                                 on_reasoning=on_reasoning, on_reasoning_end=on_reasoning_end)
            if streamed:
                print()  # newline after streamed tokens
            else:
                # response wasn't streamed (came after tool calls)
                console.print(Markdown(response))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")


def _show_help():
    console.print(Panel(
        "[bold]Commands:[/bold]\n"
        "  /help          Show this help\n"
        "  /reset         Save & clear conversation history\n"
        "  /cancel        Cancel (also press Ctrl+C during execution)\n"
        "  /model <name>  Switch model mid-conversation\n"
        "  /timeout <sec> Set request timeout (default: 120s)\n"
        "  /debug         Toggle debug mode\n"
        "  /think          Toggle deep thinking (reasoning display)\n"
        "  /clear_thoughts  Clear stored reasoning from all messages (save space)\n"
        "  /workdir [dir] Show or change working directory\n"
        "  /venv [path]   Show or set virtual environment\n"
        "  /tokens        Show token usage (total)\n"
        "  /usage         Show detailed usage report (per-round avg, context %)\n"
        "  /errors        Show error analysis report (by type, by tool, details)\n"
        "  /compact       Compress conversation context\n"
        "  /save          Save session to disk\n"
        "  /sessions      List saved sessions\n"
        "  quit           Exit LazyCatCoder",
        title="LazyCatCoder Help",
        border_style="dim",
    ))


def _brief(kwargs: dict, maxlen: int = 500) -> str:
    s = ", ".join(f"{k}={repr(v)[:200]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
