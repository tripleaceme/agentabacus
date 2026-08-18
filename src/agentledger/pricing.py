"""Effective-dated pricing.

Two rules that separate a real cost engine from a toy:

1. Price at EVENT time, not at query time. A model's rate can change; joining
   against a "current price" table silently reprices last quarter's sessions.
   Hence valid_from / valid_to on every row.

2. Cache writes are NOT one number. A 1-hour-TTL write costs 2x base input;
   a 5-minute-TTL write costs 1.25x; a cache read costs 0.1x. Claude Code
   records the split (`cache_creation.ephemeral_1h_input_tokens` vs
   `ephemeral_5m_input_tokens`), so collapsing them into a single
   `cache_creation_input_tokens` figure misprices every long session --
   and long sessions are where the cache tokens actually are.

The table is a CSV on purpose: adding a model is a one-line PR that needs no
Python and reviews at a glance. That matters, because new-model pricing is the
highest-frequency maintenance task this project has.

Dates: rows currently use an early `valid_from` so that historical sessions
price at today's rate rather than falling out of the join. Real effective dates
are welcome as PRs -- that is exactly the kind of correction the schema exists
to absorb.
"""

from __future__ import annotations

import csv
from importlib import resources
from pathlib import Path

PRICING_COLUMNS = [
    "model_id",
    "speed",
    "valid_from",
    "valid_to",
    "input_per_mtok",
    "output_per_mtok",
    "cache_read_per_mtok",
    "cache_write_5m_per_mtok",
    "cache_write_1h_per_mtok",
    "source_note",
]


def _bundled_csv() -> str:
    return resources.files("agentledger.data").joinpath("pricing.csv").read_text()


def load_rows(override: Path | None = None) -> list[tuple]:
    text = override.read_text() if override else _bundled_csv()
    rows: list[tuple] = []
    for r in csv.DictReader(text.splitlines()):
        rows.append(
            (
                r["model_id"].strip(),
                (r.get("speed") or "standard").strip() or "standard",
                r["valid_from"].strip(),
                (r.get("valid_to") or "").strip() or None,
                float(r["input_per_mtok"]),
                float(r["output_per_mtok"]),
                float(r["cache_read_per_mtok"]),
                float(r["cache_write_5m_per_mtok"]),
                float(r["cache_write_1h_per_mtok"]),
                (r.get("source_note") or "").strip() or None,
            )
        )
    return rows


def sync(conn, override: Path | None = None) -> int:
    """Replace the pricing table from the CSV. Idempotent."""
    rows = load_rows(override)
    conn.execute("DELETE FROM pricing")
    conn.executemany(
        "INSERT INTO pricing VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)
