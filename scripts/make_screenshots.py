#!/usr/bin/env python3
"""Regenerate the README screenshots.

Runs against a synthetic archive, never a real one. Screenshots of real output
would publish working directory names, client project names and actual spend
to a public README, which is nobody's intention when they run a docs script.

    pip install pillow          # not a dev dependency: docs-only, CI never runs this
    python scripts/make_screenshots.py

Writes assets/shot-*.png. Needs Google Chrome for the SVG -> PNG step, and
Pillow to crop each shot down to its content.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentabacus import store  # noqa: E402
from agentabacus.schema import Batch, Session, ToolCall, Turn  # noqa: E402

WIDTH = 120
OUT = ROOT / "assets"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

PROJECTS = [
    ("/Users/dev/code/checkout-api", "main"),
    ("/Users/dev/code/checkout-api", "feat/refunds"),
    ("/Users/dev/code/billing-web", "main"),
    ("/Users/dev/code/etl-pipeline", "main"),
]
MODELS = [
    ("claude-opus-5", 0.45),
    ("claude-sonnet-5", 0.40),
    ("claude-haiku-4-5", 0.15),
]
TOOLS = [
    ("Bash", 0.30, 0.04), ("Edit", 0.24, 0.03), ("Read", 0.20, 0.01),
    ("Write", 0.10, 0.02), ("Grep", 0.08, 0.00), ("WebFetch", 0.05, 0.11),
    ("TodoWrite", 0.03, 0.00),
]


def build_demo(home: Path) -> None:
    """A believable month of work: a few projects, three models, some failures."""
    rng = random.Random(20260823)          # fixed seed: reproducible screenshots
    home.mkdir(parents=True, exist_ok=True)
    conn = store.connect(home / "agentabacus.duckdb")
    now = datetime(2026, 8, 23, 18, 0, 0)

    sessions, turns, tools = [], [], []
    for index in range(34):
        cwd, branch = rng.choice(PROJECTS)
        started = now - timedelta(days=rng.randint(0, 29), hours=rng.randint(0, 9))
        sid = f"{index:08x}-1111-2222-3333-444444444444"
        is_sub = index % 5 == 4
        thread = f"a{index:016x}" if is_sub else "main"

        sessions.append(Session(
            session_id=sid, thread_id=thread, source="claude_code", cwd=cwd,
            git_branch=branch, cli_version="2.1.0", is_subagent=is_sub,
            started_at=started, ended_at=started + timedelta(minutes=40),
        ))

        for step in range(rng.randint(30, 160)):
            model = rng.choices([m for m, _ in MODELS], [w for _, w in MODELS])[0]
            ts = started + timedelta(minutes=step % 40, seconds=step)
            heavy = model == "claude-opus-5"
            turns.append(Turn(
                request_id=f"req_{index:03d}_{step:04d}", session_id=sid,
                thread_id=thread, source="claude_code", model_id=model, ts=ts,
                effort=rng.choice(["high", "medium", "low"]),
                service_tier="standard", speed="standard",
                input_tokens=rng.randint(2, 60),
                output_tokens=rng.randint(400, 2600 if heavy else 900),
                thinking_tokens=rng.randint(0, 700) if heavy else 0,
                cache_read_tokens=rng.randint(30_000, 120_000 if heavy else 60_000),
                cache_write_5m_tokens=rng.randint(0, 900),
                cache_write_1h_tokens=rng.randint(1_000, 9_000),
                block_lines=rng.randint(1, 3),
            ))
            if step % 3 == 0:
                name, _, err_rate = rng.choices(TOOLS, [w for _, w, _ in TOOLS])[0]
                tools.append(ToolCall(
                    tool_use_id=f"toolu_{index:03d}_{step:04d}", session_id=sid,
                    thread_id=thread, source="claude_code", ts=ts, tool_name=name,
                    target="…", is_error=rng.random() < err_rate,
                    result_chars=rng.randint(20, 4000),
                ))

    store.write_batch(conn, Batch(sessions=sessions, turns=turns, tool_calls=tools))
    conn.close()
    print(f"  demo archive: {len(turns):,} requests, {len(sessions)} sessions")


def build_fake_agents(home: Path) -> None:
    """Give doctor something worth showing.

    A screenshot of doctor finding nothing teaches nothing. This creates a
    machine that has Claude Code (supported, with logs) plus Codex and Gemini
    (detected, no adapter), which is the state the command exists to explain.
    """
    project = home / ".claude" / "projects" / "-Users-dev-code-checkout-api"
    project.mkdir(parents=True)
    for i in range(18):
        (project / f"{i:08x}-1111-2222-3333-444444444444.jsonl").write_text("{}\n")
    subagents = project / "00000000-1111-2222-3333-444444444444" / "subagents"
    subagents.mkdir(parents=True)
    for i in range(7):
        (subagents / f"agent-a{i:016x}.jsonl").write_text("{}\n")
    (home / ".claude" / "history.jsonl").write_text("{}\n")

    # installed, but agentabacus cannot read them yet
    (home / ".codex" / "sessions").mkdir(parents=True)
    (home / ".gemini").mkdir(parents=True)


def capture(home: Path, args: list[str]) -> str:
    env = {
        **os.environ,
        # Point HOME at the temp dir too: Cursor and Cline are detected by
        # fixed paths with no env var, and would otherwise show up as "also
        # found on this machine" in a screenshot taken on a real laptop.
        "HOME": str(home),
        "USERPROFILE": str(home),
        "AGENTABACUS_HOME": str(home / ".agentabacus"),
        "COLUMNS": str(WIDTH),
        "FORCE_COLOR": "1",
        "TERM": "xterm-256color",
    }
    for leftover in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CONFIG_DIR",
                     "OPENCODE_CONFIG_DIR"):
        env.pop(leftover, None)

    result = subprocess.run(
        [sys.executable, "-m", "agentabacus", *args],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )
    text = result.stdout or result.stderr
    # A screenshot should not show /var/folders/nh/kprmp5xn.../T/tmpXXXX
    return text.replace(str(home), "/Users/dev")


def to_png(ansi: str, name: str, title: str) -> Path:
    from rich.ansi import AnsiDecoder
    from rich.console import Console

    console = Console(record=True, width=WIDTH, file=open(os.devnull, "w"))
    for line in AnsiDecoder().decode(ansi.rstrip("\n")):
        # soft_wrap: the captured process already wrapped at COLUMNS, so
        # letting rich wrap a second time breaks lines that already fit.
        console.print(line, soft_wrap=True)

    markup = console.export_svg(title=title)

    svg = OUT / f"{name}.svg"
    OUT.mkdir(exist_ok=True)
    svg.write_text(markup, encoding="utf-8")

    png = OUT / f"{name}.png"
    # Screenshot generously, then crop to the actual content. Matching the
    # viewport to the SVG's declared size is fragile -- rich writes float
    # dimensions and reorders attributes between versions -- whereas cropping
    # to non-background pixels works whatever it emits.
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
         "--force-device-scale-factor=2",
         f"--screenshot={png}", "--window-size=1600,1200", f"file://{svg}"],
        capture_output=True,
    )
    svg.unlink()
    _crop(png)
    return png


def _crop(png: Path, pad: int = 16) -> None:
    from PIL import Image, ImageChops

    image = Image.open(png).convert("RGB")
    background = Image.new("RGB", image.size, image.getpixel((0, 0)))
    box = ImageChops.difference(image, background).getbbox()
    if not box:
        return
    left, top, right, bottom = box
    image.crop((
        max(0, left - pad), max(0, top - pad),
        min(image.width, right + pad), min(image.height, bottom + pad),
    )).save(png)


SHOTS = [
    ("shot-report", "agentabacus report --by model", ["report", "--since", "30d", "--by", "model"]),
    ("shot-doctor", "agentabacus doctor", ["doctor"]),
    ("shot-cache", "agentabacus cache", ["cache", "--since", "30d"]),
    ("shot-tools", "agentabacus tools", ["tools", "--since", "30d"]),
]


def main() -> int:
    if not Path(CHROME).exists():
        print(f"Chrome not found at {CHROME}")
        return 1

    home = Path(tempfile.mkdtemp(prefix="agentabacus-demo-"))
    try:
        print("building synthetic archive (no real data is used)")
        build_fake_agents(home)
        build_demo(home / ".agentabacus")
        for name, title, args in SHOTS:
            text = capture(home, args)
            png = to_png(text, name, title)
            print(f"  {png.relative_to(ROOT)}  ({png.stat().st_size // 1024} KB)")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
