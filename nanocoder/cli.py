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
from .session import save_session, load_session, list_sessions
from . import __version__

console = Console()


def _parse_args():
    p = argparse.ArgumentParser(
        prog="nanocoder",
        description="Minimal AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Model name (default: $NANOCODER_MODEL or gpt-4o)")
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
            "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or NANOCODER_API_KEY\n"
            "\nExamples:\n"
            "  # OpenAI\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  # DeepSeek\n"
            "  export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com\n"
            "\n"
            "  # Ollama (local)\n"
            "  export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 NANOCODER_MODEL=qwen2.5-coder\n"
        )
        sys.exit(1)

    llm = LLM(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
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
            console.print(f"[green]Resumed session: {args.resume}[/green]")
        else:
            console.print(f"[red]Session '{args.resume}' not found.[/red]")
            sys.exit(1)

    # one-shot mode
    if args.prompt:
        _run_once(agent, args.prompt)
        return

    # interactive REPL
    _repl(agent, config)


def _run_once(agent: Agent, prompt: str):
    """Non-interactive: run one prompt and exit."""
    def on_token(tok):
        print(tok, end="", flush=True)

    def on_tool(name, kwargs):
        console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

    agent.chat(prompt, on_token=on_token, on_tool=on_tool)
    print()


def _repl(agent: Agent, config: Config):
    """Interactive read-eval-print loop."""
    debug_badge = " [yellow bold]DEBUG[/]" if config.debug else ""
    console.print(Panel(
        f"[bold]NanoCoder[/bold] v{__version__}{debug_badge}\n"
        f"Model: [cyan]{config.model}[/cyan]"
        + (f"  Base: [dim]{config.base_url}[/dim]" if config.base_url else "")
        + f"  Timeout: [dim]{config.timeout}s[/dim]"
        + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit.",
        border_style="blue",
    ))

    hist_path = os.path.expanduser("~/.nanocoder_history")
    history = FileHistory(hist_path)

    while True:
        try:
            user_input = pt_prompt("You > ", history=history).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        if not user_input:
            continue

        # built-in commands
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/reset":
            agent.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue
        if user_input == "/cancel":
            agent._cancelled = True
            console.print("[yellow]Cancelling current operation...[/yellow]")
            continue
        if user_input == "/debug":
            config.debug = not config.debug
            agent.debug = config.debug
            status = "on" if config.debug else "off"
            console.print(f"Debug mode [{status}]")
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
                f"  Errors this session: {len(agent.get_all_errors())}",
                title="Usage",
                border_style="cyan",
            ))
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
            sid = save_session(agent.messages, config.model)
            console.print(f"[green]Session saved: {sid}[/green]")
            console.print(f"Resume with: nanocoder -r {sid}")
            continue
        if user_input == "/sessions":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]No saved sessions.[/dim]")
            else:
                for s in sessions:
                    s = s.to_dict()
                    console.print(f"  [cyan]{s['id']}[/cyan] ({s['model']}, {s['saved_at']}) {s['summary']}")
            continue

        # call the agent
        streamed: list[str] = []

        def on_token(tok):
            streamed.append(tok)
            print(tok, end="", flush=True)

        def on_tool(name, kwargs):
            console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

        try:
            response = agent.chat(user_input, on_token=on_token, on_tool=on_tool)
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
        "  /reset         Clear conversation history\n"
        "  /cancel        Cancel current tool execution\n"
        "  /model <name>  Switch model mid-conversation\n"
        "  /timeout <sec> Set request timeout (default: 120s)\n"
        "  /debug         Toggle debug mode\n"
        "  /workdir [dir] Show or change working directory\n"
        "  /venv [path]   Show or set virtual environment\n"
        "  /tokens        Show token usage (total)\n"
        "  /usage         Show detailed usage report (per-round avg, context %)\n"
        "  /compact       Compress conversation context\n"
        "  /save          Save session to disk\n"
        "  /sessions      List saved sessions\n"
        "  quit           Exit NanoCoder",
        title="NanoCoder Help",
        border_style="dim",
    ))


def _brief(kwargs: dict, maxlen: int = 500) -> str:
    s = ", ".join(f"{k}={repr(v)[:200]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
