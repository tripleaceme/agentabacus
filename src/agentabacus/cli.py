"""agentabacus CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from . import collect as collect_mod
from . import report as report_mod
from . import schedule as schedule_mod
from . import store
from .agents import AGENTS, BY_FLAG, CONTRIBUTING_URL, SUPPORTED, installed
from .config import DB_PATH
from .discovery import discover

BANNER = r"""
                        _        _
  __ _  __ _  ___ _ __ | |_ __ _| |__   __ _  ___ _   _ ___
 / _` |/ _` |/ _ \ '_ \| __/ _` | '_ \ / _` |/ __| | | / __|
| (_| | (_| |  __/ | | | || (_| | |_) | (_| | (__| |_| \__ \
 \__,_|\__, |\___|_| |_|\__\__,_|_.__/ \__,_|\___|\__,_|___/
       |___/
"""

EPILOG = """[bold]Typical first run[/bold]

  agentabacus doctor            see which agents are on this machine
  agentabacus collect           read their logs into the local archive
  agentabacus report            what the last 30 days cost

[bold]Every command takes --help[/bold], e.g. [cyan]agentabacus report --help[/cyan]

The archive is one DuckDB file at [cyan]~/.agentabacus/agentabacus.duckdb[/cyan]
(override with [cyan]$AGENTABACUS_HOME[/cyan]). Nothing is uploaded anywhere.
"""

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help=(
        "[bold]Local-first analytics for AI coding agents.[/bold]\n\n"
        "Reads the session logs your agents already write to disk and tells you "
        "what they cost. No server, no account, no telemetry."
    ),
    epilog=EPILOG,
)
console = Console()



# Widest agent label, so the two lists in `doctor` line up with each other.
_W = max(len(a.label) for a in AGENTS)


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


def _unsupported_note(prefix: str = "") -> None:
    """Tell users which of their agents we can see but cannot read yet.

    Deliberately no counts, no tokens, no cost. Someone running a tool that
    supports one of their four agents wants to know that fact and how to fix
    it -- not a half-number they have to work out whether to trust.
    """
    detected = installed(supported=False)
    if not detected:
        return
    names = ", ".join(agent.label for agent, _ in detected)
    console.print(
        f"{prefix}[yellow]Also found on this machine:[/yellow] {names}"
    )
    # OSC-8 hyperlink: modern terminals render the label and hide the URL.
    # Older ones fall back to showing the label followed by the bare link, so
    # the address is never actually lost.
    console.print(
        "[dim]agentabacus has no adapter for these yet, so their logs are not "
        f"collected. Contributions are welcome:[/dim] {_link('CONTRIBUTING.md')}"
    )


def _link(label: str, url: str = "") -> str:
    """A clickable label instead of a wall of URL."""
    return f"[link={url or CONTRIBUTING_URL}][cyan]{label}[/cyan][/link]"


def _selected_agents(flags: dict[str, bool]) -> list[str] | None:
    """Turn `--claude --codex` into a list of source names, or None for all.

    Asking for an agent we cannot read is a hard stop with a pointer to
    CONTRIBUTING, rather than a silent no-op that looks like "you have no data".
    """
    chosen = [BY_FLAG[flag] for flag, on in flags.items() if on]
    if not chosen:
        return None

    blocked = [a for a in chosen if not a.supported]
    if blocked:
        for agent in blocked:
            console.print(
                f"[red]{agent.label} is not supported yet.[/red] "
                f"agentabacus has no adapter for its logs."
            )
        console.print(f"\nContributions are welcome: {_link('CONTRIBUTING.md')}")
        raise typer.Exit(1)

    return [a.name for a in chosen]


def _open(read_only: bool = False):
    if read_only and not DB_PATH.exists():
        console.print("[yellow]No database yet. Run [bold]agentabacus collect[/bold] first.[/yellow]")
        raise typer.Exit(1)
    return store.connect()


# --------------------------------------------------------------------------


@app.command(epilog="""[bold]Examples[/bold]

  agentabacus collect               every supported agent
  agentabacus collect --claude      just Claude Code
  agentabacus collect --full        re-read everything from scratch
""")
def collect(
    # One flag per known agent. With none given, every supported agent is
    # collected; adding an agent means adding its flag here.
    claude: bool = typer.Option(False, "--claude", help="Collect Claude Code only."),
    codex: bool = typer.Option(False, "--codex", help="Collect Codex CLI only."),
    gemini: bool = typer.Option(False, "--gemini", help="Collect Gemini CLI only."),
    opencode: bool = typer.Option(False, "--opencode", help="Collect OpenCode only."),
    cursor: bool = typer.Option(False, "--cursor", help="Collect Cursor only."),
    cline: bool = typer.Option(False, "--cline", help="Collect Cline only."),
    aider: bool = typer.Option(False, "--aider", help="Collect Aider only."),
    full: bool = typer.Option(
        False, "--full",
        help=(
            "Ignore saved read positions and re-read every log from the start. "
            "Use after upgrading, or if numbers look wrong."
        ),
    ),
    session_id: str = typer.Option(
        None, "--session-id",
        help="Collect a single session by id. Used by the Claude Code SessionEnd hook.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q",
        help="Print nothing unless something failed. Also disables the progress bar.",
    ),
):
    """Read new log data into the local archive. Safe to run repeatedly.

    With no agent flag, collects every supported agent found on this machine.
    """
    sources = _selected_agents({
        "claude": claude, "codex": codex, "gemini": gemini,
        "opencode": opencode, "cursor": cursor, "cline": cline, "aider": aider,
    })
    conn = store.connect()

    # Progress only when a human is watching. Piped output and the SessionEnd
    # hook must stay clean -- ANSI control codes in a hook's stdout are noise
    # at best and corrupt a log at worst.
    show = not quiet and console.is_terminal

    if show:
        console.print("[dim]Scanning for agent logs…[/dim]")
        state: dict = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:

            def on_plan(jobs, total_bytes):
                state["total"] = len(jobs)
                # Weight the bar by BYTES, not by file count: transcripts range
                # from a few KB to ~18 MB, so a per-file bar would crawl and
                # then leap. total=1 when there is nothing to do keeps rich
                # from dividing by zero.
                state["task"] = progress.add_task(
                    "starting…", total=total_bytes or 1
                )

            def on_file_start(index, job):
                name = job.found.path.name
                if len(name) > 28:
                    name = name[:13] + "…" + name[-14:]
                progress.update(
                    state["task"],
                    description=f"[{index}/{state['total']}] {job.found.source} {name}",
                )

            def on_progress(nbytes):
                progress.update(state["task"], advance=nbytes)

            result = collect_mod.collect(
                conn, sources=sources, full=full, session_id=session_id,
                on_plan=on_plan, on_file_start=on_file_start, on_progress=on_progress,
            )
    else:
        result = collect_mod.collect(
            conn, sources=sources, full=full, session_id=session_id
        )

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

    if not sources:
        _unsupported_note(prefix="\n")


@app.command(epilog="""[bold]Examples[/bold]

  agentabacus report                              last 30 days, by model
  agentabacus report --since all                  everything ever collected
  agentabacus report --since 7d --by project      last week, by project
  agentabacus report --since all --by day         daily series, for charting
  agentabacus report --by thread                  main loop vs subagents
  agentabacus report --since 6m --main-only       6 months, excluding subagents
""")
def report(
    since: str = typer.Option("30d", "--since", help="Time window: 24h, 7d, 4w, 6m, or all. Counts back from now.  [default: 30d]"),
    by: str = typer.Option(
        "model", "--by",
        help=(
            "Group the breakdown by: model, source, project, branch, day, "
            "effort, speed, or thread. 'thread' splits your main conversation "
            "from subagent work.  [default: model]"
        ),
    ),
    source: str = typer.Option(None, "--source", help="Limit to one agent, e.g. claude_code. Default is every agent in the archive."),
    main_only: bool = typer.Option(False, "--main-only", help="Exclude subagent threads, counting only the main conversation."),
    limit: int = typer.Option(25, "--limit", help="Rows in the breakdown table."),
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
    if not source:
        _unsupported_note(prefix="\n")


@app.command(epilog="""[bold]Examples[/bold]

  agentabacus top                        10 priciest sessions, last 30 days
  agentabacus top --since all --limit 25 all time, 25 rows
""")
def top(
    since: str = typer.Option("30d", "--since", help="Time window: 24h, 7d, 4w, 6m, or all. Counts back from now.  [default: 30d]"),
    limit: int = typer.Option(10, "--limit", help="How many sessions to list."),
    source: str = typer.Option(None, "--source", help="Limit to one agent, e.g. claude_code. Default is every agent in the archive."),
    main_only: bool = typer.Option(False, "--main-only", help="Exclude subagent threads, counting only the main conversation."),
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


@app.command(epilog="""[bold]Examples[/bold]

  agentabacus cache                 last 30 days
  agentabacus cache --since all     all time

Cache reads bill at 0.1x input, 5-minute writes at 1.25x, 1-hour writes at 2x.
This is where most of the spend on a long session usually is.
""")
def cache(
    since: str = typer.Option("30d", "--since", help="Time window: 24h, 7d, 4w, 6m, or all. Counts back from now.  [default: 30d]"),
    source: str = typer.Option(None, "--source", help="Limit to one agent, e.g. claude_code. Default is every agent in the archive."),
    main_only: bool = typer.Option(False, "--main-only", help="Exclude subagent threads, counting only the main conversation."),
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


@app.command(epilog="""[bold]Examples[/bold]

  agentabacus tools                     last 30 days
  agentabacus tools --since all         all time
  agentabacus tools --main-only         exclude subagent tool calls
""")
def tools(
    since: str = typer.Option("30d", "--since", help="Time window: 24h, 7d, 4w, 6m, or all. Counts back from now.  [default: 30d]"),
    source: str = typer.Option(None, "--source", help="Limit to one agent, e.g. claude_code. Default is every agent in the archive."),
    main_only: bool = typer.Option(False, "--main-only", help="Exclude subagent threads, counting only the main conversation."),
    limit: int = typer.Option(20, "--limit", help="How many tools to list."),
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


@app.command(epilog="""[bold]Examples[/bold]

  agentabacus schedule                  show whether auto-collection is on
  agentabacus schedule --every 6h       collect every 6 hours
  agentabacus schedule --every 30m      every 30 minutes
  agentabacus schedule --every 1d       once a day
  agentabacus schedule --off            turn it off

Uses your operating system's own scheduler -- launchd on macOS, a systemd user
timer on Linux, Task Scheduler on Windows. There is no agentabacus daemon.
""")
def schedule(
    every: str = typer.Option(
        None, "--every",
        help="Run collection this often: 30m, 2h, 6h, 1d. Minimum 5m, maximum 7d.",
    ),
    off: bool = typer.Option(False, "--off", help="Remove the schedule."),
):
    """Collect automatically in the background, on a schedule you choose.

    With no options, reports the current schedule.
    """
    try:
        if off:
            removed = schedule_mod.uninstall()
            console.print(
                "[green]Auto-collection turned off.[/green]" if removed
                else "[yellow]Auto-collection was not set up.[/yellow]"
            )
            raise typer.Exit(0)

        if every:
            minutes = schedule_mod.parse_interval(every)
            where = schedule_mod.install(minutes)
            console.print(
                f"[green]Auto-collection on:[/green] "
                f"{schedule_mod.humanise(minutes)}  "
                f"[dim]({schedule_mod.backend()})[/dim]"
            )
            console.print(f"  [dim]{where}[/dim]")
            console.print(f"  [dim]log: {schedule_mod.log_path()}[/dim]")
            console.print("\n[dim]Turn it off with [bold]agentabacus schedule --off[/bold][/dim]")
            raise typer.Exit(0)

        state = schedule_mod.status()
        if not state.installed:
            console.print("[yellow]Auto-collection is off.[/yellow]")
            console.print(
                "\n[dim]Turn it on, e.g.:[/dim]  agentabacus schedule --every 6h"
            )
            console.print(
                "[dim]Collection is incremental, so a long interval costs nothing"
                " and loses nothing.[/dim]"
            )
            raise typer.Exit(0)

        cadence = (
            schedule_mod.humanise(state.minutes) if state.minutes else "on a schedule"
        )
        console.print(
            f"[green]Auto-collection is on:[/green] {cadence}  "
            f"[dim]({schedule_mod.backend()}, {state.detail})[/dim]"
        )
        console.print(f"  [dim]{state.path}[/dim]")
        log = schedule_mod.log_path()
        if log.exists() and log.stat().st_size:
            console.print(f"  [dim]log: {log}[/dim]")
    except schedule_mod.ScheduleError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)


@app.command()
def doctor():
    """What's discoverable, what's collected, and what has no price."""
    found = discover()
    by_kind: dict[tuple[str, str], int] = {}
    for f in found:
        by_kind[(f.source, f.kind)] = by_kind.get((f.source, f.kind), 0) + 1

    console.print("\n[bold]Supported agents[/bold]")
    any_supported = False
    for agent in SUPPORTED:
        root = agent.root()
        if root is None:
            continue
        any_supported = True
        total = sum(n for (src, _), n in by_kind.items() if src == agent.name)
        console.print(
            f"  [green]{agent.label:<{_W}}[/green]  {_n(total):>5} log file(s)  "
            f"[dim]{root}[/dim]"
        )
        for (src, kind), count in sorted(by_kind.items()):
            if src == agent.name:
                console.print(f"      [dim]{kind:<11}{count}[/dim]")
    if not any_supported:
        console.print("  [yellow]none found on this machine[/yellow]")

    # Detected but unreadable. No counts here on purpose -- what the user needs
    # is the fact and the fix, not a number they cannot act on.
    detected = installed(supported=False)
    if detected:
        console.print("\n[bold]Detected, not supported yet[/bold]")
        for agent, root in detected:
            console.print(f"  [yellow]{agent.label:<{_W}}[/yellow]  [dim]{root}[/dim]")
        console.print(
            "\n  [dim]These agents keep logs on this machine, but agentabacus has no\n"
            "  adapter for them yet, so nothing from them is collected.\n"
            "  Adding an adapter is a single file:[/dim]"
        )
        console.print(f"  {_link('CONTRIBUTING.md')}")

    missing = [
        a.label for a in AGENTS
        if a.root() is None and not a.supported
    ]
    if missing:
        console.print(f"\n[dim]Not installed: {', '.join(missing)}[/dim]")

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


@app.command(epilog="""[bold]Examples[/bold]

  agentabacus export                                  parquet into ./agentabacus_export
  agentabacus export --format csv --out ~/Desktop/aa  csv, somewhere else

Writes one file per table plus the costed view, ready for dbt or Metabase.
""")
def export(
    out: Path = typer.Option(
        Path("./agentabacus_export"), "--out",
        help="Directory to write into. Created if missing.  [default: ./agentabacus_export]",
    ),
    fmt: str = typer.Option(
        "parquet", "--format",
        help="parquet (typed, smaller) or csv (portable).  [default: parquet]",
    ),
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
def sql(
    query: str = typer.Argument(
        None,
        help="SQL to run. Omit it to print the schema instead.",
    ),
    schema: bool = typer.Option(
        False, "--schema", help="List every table, view and column, then exit."
    ),
):
    """Escape hatch: run SQL directly against the archive.

    Run it with no query to see what you can query.
    """
    conn = _open(read_only=True)

    # Telling someone "the schema is yours" is useless if they cannot discover
    # the table names. No query means show them, rather than an argument error.
    if schema or not query:
        _print_schema(conn)
        raise typer.Exit(0)

    try:
        result = conn.execute(query)
    except Exception as exc:
        console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
        console.print(
            "\n[dim]Run [bold]agentabacus sql[/bold] with no query to list "
            "tables and columns.[/dim]"
        )
        raise typer.Exit(2)

    if result.description is None:          # a statement returning no rows
        console.print("[dim]ok[/dim]")
        raise typer.Exit(0)

    columns = [d[0] for d in result.description]
    rows = result.fetchall()
    _table("", columns, rows, [str] * len(columns))
    console.print(f"[dim]{len(rows)} row(s)[/dim]")


# What each table is for, in one line. Column lists come from the database, so
# they cannot drift; these descriptions are the part a schema dump cannot give.
_TABLE_NOTES = {
    "turns": "One row per LLM request. The main table.",
    "turns_costed": "turns + cost_usd, priced at the rate in force when it ran.",
    "turns_normalized": "turns with model aliases resolved.",
    "sessions": "One row per session (and per subagent thread).",
    "session_totals": "Per-session rollup: requests, cost, tokens.",
    "tool_calls": "One row per tool invocation, with is_error.",
    "prompts": "One row per prompt. Hash and length only, never the text.",
    "pricing": "Effective-dated price table, joined on the request timestamp.",
}


def _print_schema(conn) -> None:
    objects = conn.execute(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name NOT LIKE '\\_%' ESCAPE '\\'
        ORDER BY table_type DESC, table_name
        """
    ).fetchall()

    console.print("\n[bold]Tables and views in your archive[/bold]\n")
    for name, kind in objects:
        columns = [
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='main' AND table_name=? ORDER BY ordinal_position",
                [name],
            ).fetchall()
        ]
        rows = conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        label = "view" if kind == "VIEW" else "table"
        console.print(f"  [cyan]{name}[/cyan] [dim]({label}, {rows:,} rows)[/dim]")
        if note := _TABLE_NOTES.get(name):
            console.print(f"    [dim]{note}[/dim]")
        console.print(f"    [dim]{', '.join(columns)}[/dim]\n")

    console.print("[bold]Examples[/bold]\n")
    console.print(
        '  agentabacus sql "SELECT model_id, count(*) FROM turns GROUP BY 1"\n'
        '  agentabacus sql "SELECT sum(cost_usd) FROM turns_costed WHERE ts >= now() - INTERVAL 7 DAY"\n'
        '  agentabacus sql "SELECT tool_name, avg(is_error::INT) FROM tool_calls GROUP BY 1"\n'
    )
    console.print(
        "[dim]It is DuckDB SQL. Prefer [bold]turns_costed[/bold] over "
        "[bold]turns[/bold] when you want money.[/dim]"
    )


if __name__ == "__main__":
    main()


def main() -> None:
    """Console-script entry point.

    The banner is printed here rather than from a Typer callback because
    `--help` short-circuits inside Click before any callback runs, and rich
    would reflow the ASCII art if it were part of the help string.
    """
    argv = sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        console.print(f"[#e0a340]{BANNER}[/#e0a340]", highlight=False)
    app()


if __name__ == "__main__":
    main()
