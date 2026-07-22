"""Report: simple terminal table output for eval results."""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel


def print_report(results: list[dict]):
    """Print a summary table of eval results.

    Each result dict should have:
        - case_id: str
        - passed: bool
        - rounds: int
        - total_tokens: int
        - error: str | None
        - test_output: str | None  (truncated for display)
    """
    console = Console()

    table = Table(title="Eval Results")
    table.add_column("Case ID", style="cyan")
    table.add_column("Pass@1", justify="center")
    table.add_column("Rounds", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Status", style="dim")

    for r in results:
        pass_str = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"

        if r["error"]:
            status = f"[red]Error: {r['error'][:40]}[/red]"
        elif not r["passed"] and r.get("test_output"):
            # Show first line of test failure
            first_line = r["test_output"].splitlines()[0] if r["test_output"] else ""
            status = f"[yellow]{first_line[:50]}[/yellow]"
        elif r.get("timed_out"):
            status = "[yellow]Timeout[/yellow]"
        else:
            status = "[green]OK[/green]"

        table.add_row(
            r["case_id"],
            pass_str,
            str(r["rounds"]),
            str(r["total_tokens"]),
            status,
        )

    console.print(table)

    # Summary stats
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    avg_rounds = sum(r["rounds"] for r in results) / max(total, 1)
    avg_tokens = sum(r["total_tokens"] for r in results) / max(total, 1)

    console.print(Panel(
        f"[bold]Pass Rate:[/bold] {passed}/{total} ({passed / max(total, 1) * 100:.1f}%)\n"
        f"[bold]Avg Rounds:[/bold] {avg_rounds:.1f}\n"
        f"[bold]Avg Tokens:[/bold] {avg_tokens:.0f}",
        title="Summary",
        border_style="blue",
    ))
