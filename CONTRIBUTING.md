# Contributing

Two contributions matter more than any others, and both are small.

## 1. Add or correct a model price

`src/agentabacus/data/pricing.csv` — one line, no Python:

```csv
model_id,speed,valid_from,valid_to,input_per_mtok,output_per_mtok,cache_read_per_mtok,cache_write_5m_per_mtok,cache_write_1h_per_mtok,source_note
claude-opus-5,standard,2020-01-01,,5.00,25.00,0.50,6.25,10.00,anthropic list price
```

Rules:

- **`valid_to` empty means "still current."** To change a price, close the old row with a `valid_to` and add a new row starting the next day. Never edit a historical row's rates — that silently reprices everyone's past sessions.
- **Cache columns are not optional.** Reads bill at 0.1× input, 5-minute writes at 1.25×, 1-hour writes at 2×. If a provider prices them differently, put the real numbers in; the schema doesn't assume the multipliers.
- **Dated snapshot IDs need no row.** `claude-haiku-4-5-20251001` normalizes to `claude-haiku-4-5` automatically.
- Cite where the numbers came from in `source_note`.

CI validates column order, date parsing, duplicate keys, and negative values.

Run `agentabacus doctor` to see models present in your own data with no pricing row — that list is the to-do list.

## 2. Add an adapter

One module exposing `parse(path, kind, start_offset) -> Batch`, a walker in `discovery.py`, one line in `adapters/__init__.py`.

Read `adapters/claude_code.py` as the reference and `adapters/codex.py` as the minimal template.

**The one rule: be a tolerant parser.** These log formats are undocumented, version-dependent, and change without notice. Route on known shapes, count what you skip, and never raise — a vendor's routine release must not become a crash for every user. Strictness belongs in `schema.py`, not at the edges.

Things that have already bitten this codebase, so check for them in yours:

- **Repeated usage across records.** Claude Code writes one line per content block, each carrying the parent response's *full* usage. Summing per line overcounts 2–3×. Key on a request id and merge with `max()`.
- **More than one file layout.** Claude Code nests subagent transcripts at two different depths. A fixed-depth glob silently dropped the majority of them. Recurse.
- **Torn trailing lines.** A file caught mid-write must be re-read whole next run, never half-parsed. `iter_lines()` handles this; use it.
- **Model IDs in two forms.** Aliased and date-stamped. Normalize before joining to pricing.

### Verifying the Codex adapter

`adapters/codex.py` is shape-agnostic and **unverified** — it was written without access to real rollout files. If you have `~/.codex/sessions/*.jsonl`, paste two or three redacted records into a test fixture and tighten the parsing. That's the highest-value PR in the repo right now.

## Running things

```bash
uv venv && uv pip install -e ".[dev]"
python -m pytest                # the contract tests
agentabacus doctor              # what's discoverable on your machine
```

## What this project deliberately does not do

- No hosted service, no account, no telemetry.
- **Prompt and response bodies never enter the pipeline.** The `prompts` table stores a hash and a length; there is no column for the text. Please don't add one — that property is why the tool is installable inside companies.
- No agent that acts on the data.

Team/warehouse sinks and a dbt package are on the roadmap, not in scope yet.
