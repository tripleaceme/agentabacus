#!/usr/bin/env python3
"""Machine-checkable adapter contract.

Reviewing an adapter by reading it does not work. The Codex adapter that
reported 291,007,361,482 input tokens was perfectly well-formed code -- it
summed cumulative counters instead of per-request usage, which is invisible
in a diff and obvious in the output.

So every adapter must ship a fixture, and every fixture is run through these
checks. They encode the mistakes actually made so far:

  fixture-present   an adapter with no sample logs cannot be verified by
                    anyone, now or later
  dedupe-fires      the Codex adapter synthesised request ids from byte
                    offsets, so every record was unique and dedupe silently
                    never ran
  plausible-tokens  67 million input tokens for one request is impossible;
                    a request cannot exceed the context window
  model-identified  rows with no model_id price at zero and vanish from cost
  timestamps-sane   unparsed timestamps break every time filter
  idempotent        parsing twice must not double the totals, or re-running
                    collect inflates history

Run locally:   python scripts/validate_adapters.py
CI runs this on every pull request.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentabacus.adapters import REGISTRY  # noqa: E402
from agentabacus.agents import BY_NAME  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

# No single request can plausibly exceed this. Real context windows are ~1M;
# the headroom is deliberate so the check flags only genuine nonsense.
MAX_TOKENS_PER_REQUEST = 5_000_000
MIN_MODEL_COVERAGE = 0.90
SANE_YEARS = range(2020, 2100)

# "sess-abc:41231" -- an id built from a file offset. Unique per record, so
# dedupe can never collapse anything.
OFFSET_ID = re.compile(r":\d{3,}$")


class Failure(Exception):
    pass


def _kind_for(path: Path) -> str:
    name = path.name
    if name == "history.jsonl":
        return "history"
    return "subagent" if "subagent" in path.parts or name.startswith("agent-") else "transcript"


def _parse_all(source: str, files: list[Path]):
    parse = REGISTRY[source]
    turns, tool_calls, sessions = [], [], []
    for path in files:
        batch = parse(path, _kind_for(path), 0)
        turns.extend(batch.turns)
        tool_calls.extend(batch.tool_calls)
        sessions.extend(batch.sessions)
    return turns, tool_calls, sessions


def validate_turns(turns) -> list[str]:
    """The checks themselves, separated so they can be unit-tested against
    deliberately broken input rather than only against fixtures that pass."""
    notes: list[str] = []
    if not turns:
        raise Failure("fixtures parsed to zero requests; the adapter reads nothing")

    # --- dedupe actually fires -------------------------------------------
    ids = [t.request_id for t in turns]
    if len(ids) != len(set(ids)):
        raise Failure("duplicate request_ids returned; dedupe is not collapsing them")

    offsetish = [i for i in ids if OFFSET_ID.search(i)]
    if offsetish:
        raise Failure(
            f"request ids look derived from byte offsets (e.g. {offsetish[0]!r}). "
            f"Every record then gets a unique id and dedupe never fires -- this is "
            f"exactly how an earlier adapter overcounted by 3 orders of magnitude. "
            f"Use the id the provider assigns to a request."
        )

    collapsed = sum(t.block_lines for t in turns)
    if collapsed > len(turns):
        notes.append(f"dedupe collapsed {collapsed} record(s) into {len(turns)} request(s)")

    # --- token plausibility ----------------------------------------------
    worst = max(turns, key=lambda t: t.input_tokens + t.output_tokens)
    biggest = worst.input_tokens + worst.output_tokens
    if biggest > MAX_TOKENS_PER_REQUEST:
        raise Failure(
            f"request {worst.request_id!r} claims {biggest:,} tokens. No single "
            f"request can exceed the context window -- this usually means a "
            f"cumulative session counter is being read as per-request usage."
        )
    notes.append(f"largest request {biggest:,} tokens (ceiling {MAX_TOKENS_PER_REQUEST:,})")

    if any(t.input_tokens < 0 or t.output_tokens < 0 for t in turns):
        raise Failure("negative token counts")

    # --- model identified -------------------------------------------------
    named = sum(1 for t in turns if t.model_id)
    coverage = named / len(turns)
    if coverage < MIN_MODEL_COVERAGE:
        raise Failure(
            f"only {coverage:.0%} of requests have a model_id. Rows without one "
            f"price at zero and disappear from cost reports."
        )
    notes.append(f"model_id present on {coverage:.0%} of requests")

    # --- timestamps -------------------------------------------------------
    dated = [t for t in turns if t.ts is not None]
    if not dated:
        raise Failure("no request carries a timestamp; every --since filter would drop them")
    for t in dated:
        if t.ts.year not in SANE_YEARS:
            raise Failure(f"implausible timestamp {t.ts} on {t.request_id!r}")
    notes.append(f"timestamps parsed on {len(dated)}/{len(turns)} requests")
    return notes


def check(source: str) -> list[str]:
    """Returns human-readable notes; raises Failure on a violation."""
    folder = FIXTURES / source

    if not folder.is_dir() or not any(folder.rglob("*")):
        raise Failure(
            f"no fixtures at tests/fixtures/{source}/. Every adapter must ship "
            f"two or three redacted real records so its format is pinned and a "
            f"future change to it fails loudly."
        )

    files = sorted(p for p in folder.rglob("*") if p.is_file())
    turns, tool_calls, _ = _parse_all(source, files)
    notes = [f"{len(files)} fixture file(s) -> {len(turns)} request(s), "
             f"{len(tool_calls)} tool call(s)"]
    notes += validate_turns(turns)

    # --- idempotence ------------------------------------------------------
    again, _, _ = _parse_all(source, files)
    if {t.request_id: t.output_tokens for t in again} != {
        t.request_id: t.output_tokens for t in turns
    }:
        raise Failure("parsing the same fixture twice gave different results")
    notes.append("re-parse is identical (safe to re-collect)")

    return notes


def main() -> int:
    if not REGISTRY:
        print("no adapters registered")
        return 1

    failed = False
    lines = []
    for source in sorted(REGISTRY):
        agent = BY_NAME.get(source)
        label = agent.label if agent else source
        try:
            notes = check(source)
        except Failure as exc:
            failed = True
            lines.append(f"### ❌ {label} (`{source}`)\n\n**{exc}**\n")
            print(f"::error::{source}: {exc}")
        except Exception as exc:  # a crash is a failure too
            failed = True
            lines.append(f"### ❌ {label} (`{source}`)\n\n**crashed: {type(exc).__name__}: {exc}**\n")
            print(f"::error::{source}: crashed: {exc}")
        else:
            body = "\n".join(f"- {n}" for n in notes)
            lines.append(f"### ✅ {label} (`{source}`)\n\n{body}\n")
            print(f"{source}: OK")
            for n in notes:
                print(f"  - {n}")

    report = "## Adapter contract\n\n" + "\n".join(lines)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    Path("adapter-report.md").write_text(report, encoding="utf-8")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
