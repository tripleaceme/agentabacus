"""agentabacus CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import collect as collect_mod
from . import report as report_mod
from . import store
from .config import DB_PATH, CONFIG_ROOTS, config_root
from .discovery import discover

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Local-first analytics for AI coding agents. Reads logs already on your disk.",
)
console = Console()


def _n(value) -> str:
    return "-" if value is None else f"{int(value):,}"


def _tok(value) -> str:
    """Compact token counts. Agent workloads produce billions of cache-read
    tokens; full comma form makes tables unreadable at normal terminal widths."""
    if value is None:
        return "-"
    value = int(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            return f"{value / limit:.1f}{suffix}"
    return f"{value:,}"


def _model(value) -> str:
    text = str(value)
    return text[len("claude-"):] if text.startswith("claude-") else text


def _usd(value) -> str:
    if value is None:
        return "-"
    return f"${value:,.2f}" if abs(value) >= 0.01 else f"${value:,.4f}"


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _table(title: str, columns, rows, formatters) -> None:
    table = Table(title=title, title_justify="left", header_style="bold")
    for i, name in enumerate(columns):
        table.add_column(name, justify="left" if i == 0 else "right")
    for row in rows:
        table.add_row(*[fmt(cell) for fmt, cell in zip(formatters, row)])
    console.print(table)


def _open(read_only: bool = False):
    if read_only and not DB_PATH.exists():
        console.print("[yellow]No database yet. Run [bold]agentabacus collect[/bold] first.[/yellow]")
        raise typer.Exit(1)
    return store.connect()


# --------------------------------------------------------------------------


@app.command()
def collect(
    full: bool = typer.Option(False, "--full", help="Re-read every file from byte 0."),
    source: str = typer.Option(None, "--source", help="Limit to one source, e.g. claude_code."),
    session_id: str = typer.Option(None, "--session-id", help="Collect only this session (hook mode)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print nothing on success."),
):
    """Read new log data into the local archive. Safe to run repeatedly."""
    conn = store.connect()
    sources = [source] if source else None
    result = collect_mod.collect(conn, sources=sources, full=full, session_id=session_id)
    conn.close()

    if quiet and not result.errors:
        return

    console.print(
        f"[bold]{result.files_read}[/bold] file(s) read, "
        f"{result.files_skipped_unchanged} unchanged, "
        f"{result.bytes_read / 1e6:.1f} MB parsed"
    )
    counts = result.counts
    console.print(
        f"  sessions [bold]{counts['sessions']}[/bold]   "
        f"requests [bold]{counts['turns']}[/bold]   "
        f"tool calls [bold]{counts['tool_calls']}[/bold]   "
        f"prompts [bold]{counts['prompts']}[/bold]"
    )
    if result.malformed_lines:
        console.print(f"[yellow]  {result.malformed_lines} unparseable line(s) skipped[/yellow]")
    for err in result.errors:
        console.print(f"[red]  {err}[/red]")


@app.command()
def report(
    since: str = typer.Option("30d", "--since", help="7d, 24h, 4w, or all."),
    by: str = typer.Option("model", "--by", help="model, source, project, branch, day, effort, speed, thread."),
    source: str = typer.Option(None, "--source"),
    main_only: bool = typer.Option(False, "--main-only", help="Exclude subagent threads."),
    limit: int = typer.Option(25, "--limit"),
):
    """Cost and token totals, with a breakdown."""
    conn = _open(read_only=True)
    include_sub = not main_only
    try:
        row = report_mod.summary(conn, since, source, include_sub)
        rows = report_mod.breakdown(conn, by, since, source, include_sub, limit)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)

    (requests, sessions, lines, inp, out, think, cread, cw5m, cw1h,
     cost, unpriced) = row

    if not requests:
        console.print("[yellow]No requests in this window.[/yellow]")
        raise typer.Exit(0)

    console.print(
        f"\n[bold]{_usd(cost)}[/bold] over [bold]{_n(requests)}[/bold] requests "
        f"in [bold]{_n(sessions)}[/bold] sessions  ([dim]--since {since}[/dim])"
    )
    # Surfacing this is the point: a naive per-line sum would have reported the
    # jsonl_lines figure's worth of usage, not the request figure's.
    if lines and requests:
        console.print(
            f"[dim]  {_n(lines)} JSONL lines collapsed into {_n(requests)} requests "
            f"({lines / requests:.1f} blocks per request)[/dim]"
        )
    console.print(
        f"[dim]  in {_n(inp)} · out {_n(out)} (thinking {_n(think)}) · "
        f"cache read {_n(cread)} · cache write {_n(cw5m)} @5m / {_n(cw1h)} @1h[/dim]"
    )
    if unpriced:
        console.print(
            f"[yellow]  {_n(unpriced)} request(s) have no pricing row — "
            f"run [bold]agentabacus doctor[/bold][/yellow]"
        )

    label = _model if by == "model" else str
    _table(
        f"By {by}",
        [by, "requests", "input", "output", "cache read", "cost"],
        rows,
        [label, _n, _tok, _tok, _tok, _usd],
    )


@app.command()
def top(
    since: str = typer.Option("30d", "--since"),
    limit: int = typer.Option(10, "--limit"),
    source: str = typer.Option(None, "--source"),
    main_only: bool = typer.Option(False, "--main-only"),
):
    """The most expensive sessions in the window."""
    conn = _open(read_only=True)
    rows = report_mod.top_sessions(conn, since, source, not main_only, limit)
    _table(
        f"Top sessions (--since {since})",
        ["session", "thread", "project", "started", "requests", "cost"],
        [(r[0][:8], r[1][:12], r[2], str(r[3])[:16], r[4], r[5]) for r in rows],
        [str, str, str, str, _n, _usd],
    )


@app.command()
def cache(
    since: str = typer.Option("30d", "--since"),
    source: str = typer.Option(None, "--source"),
    main_only: bool = typer.Option(False, "--main-only"),
):
    """Cache efficiency, with the 1h vs 5m write split priced separately."""
    conn = _open(read_only=True)
    rows = [r for r in report_mod.cache_report(conn, since, source, not main_only)
            if (r[1] or 0) + (r[2] or 0) + (r[3] or 0) > 0]
    _table(
        f"Cache (--since {since})",
        ["model", "read", "write 5m", "write 1h", "read share", "$ read", "$ w5m", "$ w1h"],
        rows,
        [_model, _tok, _tok, _tok, _pct, _usd, _usd, _usd],
    )
    console.print(
        "[dim]Cache reads bill at 0.1x input; 5m writes at 1.25x; 1h writes at 2x. "
        "A high read share is the single biggest lever on agent cost.[/dim]"
    )


@app.command()
def tools(
    since: str = typer.Option("30d", "--since"),
    source: str = typer.Option(None, "--source"),
    main_only: bool = typer.Option(False, "--main-only"),
    limit: int = typer.Option(20, "--limit"),
):
    """Tool-call volume and error rate — the effectiveness side."""
    conn = _open(read_only=True)
    rows = report_mod.tool_report(conn, since, source, not main_only, limit)
    _table(
        f"Tool calls (--since {since})",
        ["tool", "calls", "errors", "unresolved", "error rate", "avg result chars"],
        rows,
        [str, _n, _n, _n, _pct, _n],
    )


@app.command()
def doctor():
    """What's discoverable, what's collected, and what has no price."""
    console.print("\n[bold]Config roots[/bold]")
    for name, (env_var, default) in CONFIG_ROOTS.items():
        root = config_root(name)
        state = f"[green]{root}[/green]" if root else f"[dim]not found ({default})[/dim]"
        console.print(f"  {name:<12} {state}   [dim]override: ${env_var}[/dim]")

    found = discover()
    by_kind: dict[tuple[str, str], int] = {}
    for f in found:
        by_kind[(f.source, f.kind)] = by_kind.get((f.source, f.kind), 0) + 1
    console.print("\n[bold]Discovered files[/bold]")
    if not found:
        console.print("  [yellow]none[/yellow]")
    for (src, kind), count in sorted(by_kind.items()):
        console.print(f"  {src:<12} {kind:<10} {count}")

    if not DB_PATH.exists():
        console.print(f"\n[yellow]No archive yet at {DB_PATH}. Run agentabacus collect.[/yellow]")
        return

    conn = store.connect()
    console.print(f"\n[bold]Archive[/bold] {DB_PATH} "
                  f"({DB_PATH.stat().st_size / 1e6:.1f} MB)")
    for table in ("sessions", "turns", "tool_calls", "prompts", "_files"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        console.print(f"  {table:<12} {count:,}")

    gaps = report_mod.unpriced_models(conn)
    if gaps:
        console.print("\n[yellow][bold]Models with no pricing row[/bold] — "
                      "cost is undercounted until these are added to pricing.csv[/yellow]")
        _table("", ["model", "speed", "requests", "first seen", "last seen"], gaps,
               [str, str, _n, lambda v: str(v)[:16], lambda v: str(v)[:16]])
    else:
        console.print("\n[green]Every observed model has a pricing row.[/green]")


@app.command()
def export(
    out: Path = typer.Option(Path("./agentabacus_export"), "--out"),
    fmt: str = typer.Option("parquet", "--format", help="parquet or csv."),
):
    """Write the tables out for dbt / Metabase / anything that reads files."""
    if fmt not in {"parquet", "csv"}:
        console.print("[red]--format must be parquet or csv[/red]")
        raise typer.Exit(2)
    conn = _open(read_only=True)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("sessions", "turns", "tool_calls", "prompts", "pricing", "turns_costed"):
        target = out / f"{name}.{fmt}"
        if fmt == "parquet":
            conn.execute(f"COPY {name} TO '{target}' (FORMAT PARQUET)")
        else:
            conn.execute(f"COPY {name} TO '{target}' (HEADER, DELIMITER ',')")
        console.print(f"  wrote {target}")


@app.command()
def sql(query: str = typer.Argument(..., help="Any SQL against the archive.")):
    """Escape hatch: run SQL directly. The schema is yours."""
    conn = _open(read_only=True)
    result = conn.execute(query)
    columns = [d[0] for d in result.description]
    rows = result.fetchall()
    _table("", columns, rows, [str] * len(columns))
    console.print(f"[dim]{len(rows)} row(s)[/dim]")


if __name__ == "__main__":
    app()
