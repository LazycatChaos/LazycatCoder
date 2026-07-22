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
        - tool_stats: dict | None  (optional tool call statistics)
    """
    console = Console()

    table = Table(title="Eval Results")
    table.add_column("Case ID", style="cyan")
    table.add_column("Pass@1", justify="center")
    table.add_column("Rounds", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Tool Calls", justify="right")
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

        # Tool stats summary
        tool_stats = r.get("tool_stats") or {}
        total_calls = sum(t["calls"] for t in tool_stats.values())
        total_success = sum(t["success"] for t in tool_stats.values())
        total_fail = sum(t["failure"] for t in tool_stats.values())
        if total_calls > 0:
            tool_str = f"{total_calls} ({total_success}✓ {total_fail}✗)"
        else:
            tool_str = "0"

        table.add_row(
            r["case_id"],
            pass_str,
            str(r["rounds"]),
            str(r["total_tokens"]),
            tool_str,
            status,
        )

    console.print(table)

    # Summary stats
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    avg_rounds = sum(r["rounds"] for r in results) / max(total, 1)
    avg_tokens = sum(r["total_tokens"] for r in results) / max(total, 1)

    # Aggregate tool stats across all cases
    all_tool_stats: dict[str, dict] = {}
    for r in results:
        for name, stats in (r.get("tool_stats") or {}).items():
            if name not in all_tool_stats:
                all_tool_stats[name] = {"calls": 0, "success": 0, "failure": 0, "total_time": 0.0}
            all_tool_stats[name]["calls"] += stats["calls"]
            all_tool_stats[name]["success"] += stats["success"]
            all_tool_stats[name]["failure"] += stats["failure"]
            all_tool_stats[name]["total_time"] += stats["total_time"]

    total_tool_calls = sum(t["calls"] for t in all_tool_stats.values())
    total_tool_success = sum(t["success"] for t in all_tool_stats.values())
    total_tool_fail = sum(t["failure"] for t in all_tool_stats.values())

    summary_lines = [
        f"[bold]Pass Rate:[/bold] {passed}/{total} ({passed / max(total, 1) * 100:.1f}%)",
        f"[bold]Avg Rounds:[/bold] {avg_rounds:.1f}",
        f"[bold]Avg Tokens:[/bold] {avg_tokens:.0f}",
    ]
    if total_tool_calls > 0:
        summary_lines.append(
            f"[bold]Tool Calls:[/bold] {total_tool_calls} ({total_tool_success}✓ {total_tool_fail}✗)"
        )

    console.print(Panel(
        "\n".join(summary_lines),
        title="Summary",
        border_style="blue",
    ))

    # Print per-case tool details (only for cases that used tools)
    for r in results:
        tool_stats = r.get("tool_stats") or {}
        if not tool_stats:
            continue

        total_calls = sum(t["calls"] for t in tool_stats.values())
        total_success = sum(t["success"] for t in tool_stats.values())
        total_fail = sum(t["failure"] for t in tool_stats.values())

        console.print(f"\n[bold]{r['case_id']}[/bold] — Tool Details: {total_calls} calls ({total_success}✓ {total_fail}✗)")
        for name, stats in tool_stats.items():
            if stats["calls"] == 0:
                continue
            avg_time = stats["total_time"] / stats["calls"]
            status_icon = " [red]⚠️ Error[/red]" if stats["failure"] > 0 else ""
            console.print(
                f"  - {name}: {stats['calls']} ({stats['success']}✓ {stats['failure']}✗) "
                f"| avg: {avg_time:.2f}s{status_icon}"
            )
