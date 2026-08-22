#!/usr/bin/env python3
"""Tell the maintainer where to actually look.

An adapter PR should be almost entirely additive: a new file under adapters/,
a fixture, a walker, and three small registrations. Anything beyond that --
edits to the shared store, the schema, the cost view, or to an adapter that
already works -- changes behaviour for every existing user, and that is the
part a human should read.

This classifies the diff so review attention goes where it matters instead of
being spread evenly over a large PR.

Usage:  python scripts/pr_review_report.py <base-sha> <head-sha>
Writes: review-report.md, plus GITHUB_STEP_SUMMARY and GITHUB_OUTPUT when in CI
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentabacus.adapters import REGISTRY  # noqa: E402

# Touching any of these changes behaviour for everyone, not just the new agent.
CORE = {
    "src/agentabacus/store.py": "the archive writer -- affects every stored row",
    "src/agentabacus/schema.py": "the canonical schema and the cost view",
    "src/agentabacus/report.py": "how every number is aggregated",
    "src/agentabacus/collect.py": "the collector and its watermarks",
    "src/agentabacus/pricing.py": "cost calculation",
    "src/agentabacus/data/pricing.csv": "the price table",
    "src/agentabacus/agents.py": "the agent registry",
    "src/agentabacus/cli.py": "the command line surface",
    "src/agentabacus/discovery.py": "which files get read",
    "src/agentabacus/adapters/base.py": "shared by every adapter",
}

ADAPTER = re.compile(r"^src/agentabacus/adapters/(?!__init__|base)([a-z0-9_]+)\.py$")
FIXTURE = re.compile(r"^tests/fixtures/([a-z0-9_]+)/")


def changed(base: str, head: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    head = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
    files = changed(base, head)

    new_adapters, touched_existing, core_hits, fixtures, other = [], [], [], set(), []
    for path in files:
        m = ADAPTER.match(path)
        if m:
            (touched_existing if m.group(1) in REGISTRY else new_adapters).append(m.group(1))
            continue
        f = FIXTURE.match(path)
        if f:
            fixtures.add(f.group(1))
            continue
        if path in CORE:
            core_hits.append(path)
            continue
        other.append(path)

    lines = ["## Review guide", ""]

    if new_adapters:
        lines.append(f"**New adapter(s):** {', '.join(sorted(set(new_adapters)))}")
        for name in sorted(set(new_adapters)):
            if name in fixtures:
                lines.append(f"- ✅ `{name}` ships fixtures")
            else:
                lines.append(
                    f"- ❌ `{name}` has **no fixtures** under `tests/fixtures/{name}/` — "
                    f"nobody can verify its output, now or later"
                )
        lines.append("")

    needs_human = bool(core_hits or touched_existing)

    if touched_existing:
        lines += [
            "### ⚠️ Modifies an adapter that already works", "",
            "These agents are supported today; changes here affect existing users' "
            "numbers, not just the new agent:", "",
        ]
        lines += [f"- `{n}`" for n in sorted(set(touched_existing))]
        lines.append("")

    if core_hits:
        lines += [
            "### ⚠️ Modifies shared code", "",
            "| file | why it matters |", "| --- | --- |",
        ]
        lines += [f"| `{p}` | {CORE[p]} |" for p in sorted(core_hits)]
        lines.append("")

    if not needs_human:
        lines += [
            "### ✅ Additive only", "",
            "No shared code and no existing adapter was modified, so this cannot "
            "change the numbers any current user already sees.", "",
        ]

    if other:
        lines += ["<details><summary>Other files changed</summary>", ""]
        lines += [f"- `{p}`" for p in sorted(other)]
        lines += ["", "</details>", ""]

    report = "\n".join(lines)
    Path("review-report.md").write_text(report, encoding="utf-8")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"needs_human_review={'true' if needs_human else 'false'}\n")
            fh.write(f"new_adapters={','.join(sorted(set(new_adapters)))}\n")
    print(report)

    # A new adapter without fixtures is a hard fail; everything else is advisory.
    missing = [n for n in set(new_adapters) if n not in fixtures]
    if missing:
        for name in missing:
            print(f"::error::adapter '{name}' ships no fixtures under tests/fixtures/{name}/")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
