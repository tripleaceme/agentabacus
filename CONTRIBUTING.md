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

One module exposing `parse(path, kind, start_offset, on_bytes=None) -> Batch`, a walker in `discovery.py`, one line in `adapters/__init__.py`.

`on_bytes(n)` is optional — pass it straight through to `iter_lines()` and progress reporting works for free. Omit it and your files still work; they just advance the progress bar once on completion instead of continuously. That matters for large files: a single 236 MB transcript would otherwise leave the bar frozen for seconds while it appears hung.

Read `adapters/claude_code.py` as the reference — it is the only adapter, and it documents every trap found so far.

**The one rule: be a tolerant parser.** These log formats are undocumented, version-dependent, and change without notice. Route on known shapes, count what you skip, and never raise — a vendor's routine release must not become a crash for every user. Strictness belongs in `schema.py`, not at the edges.

Things that have already bitten this codebase, so check for them in yours:

- **Repeated usage across records.** Claude Code writes one line per content block, each carrying the parent response's *full* usage. Summing per line overcounts 2–3×. Key on a request id and merge with `max()`.
- **More than one file layout.** Claude Code nests subagent transcripts at two different depths. A fixed-depth glob silently dropped the majority of them. Recurse.
- **Torn trailing lines.** A file caught mid-write must be re-read whole next run, never half-parsed. `iter_lines()` handles this; use it.
- **Model IDs in two forms.** Aliased and date-stamped. Normalize before joining to pricing.

### Wiring up a new agent

Four steps, and step 4 is one line:

1. **`agents.py`** — the agent is probably already listed with `supported=False`. If not, add it: name, flag, label, env var, and the default paths its data lives in.
2. **`adapters/<name>.py`** — the parser. `adapters/claude_code.py` is the reference.
3. **`discovery.py`** — a walker that yields the log files, and an entry in `_WALKERS`.
4. **`adapters/__init__.py`** — register the parser, then flip `supported=True` in `agents.py` and add a `--<flag>` option to `collect` in `cli.py`.

Until step 4, users with that agent still see it: `doctor` reports the logs exist and links here. Nothing is parsed or stored until the adapter is real.

### A note on the Codex adapter

An early draft of a Codex adapter shipped in 0.1.x and was removed in 0.2.0. It reported **4,318 requests carrying 291,007,361,482 input tokens** — about 67 million per request, which is impossible. Two things went wrong, and they are worth knowing before writing a replacement:

- It appeared to sum **cumulative** token counters. Codex records running session totals; a parser that matches any dict with input/output token keys counts each snapshot of a growing total as a fresh request.
- Its fallback request id (`f"{session_id}:{offset}"`) made every record unique, so the dedupe that would have caught this never fired.

A good sanity check for any new adapter: a session's total input tokens should be in the same order of magnitude as context window × requests, not thousands of times larger.

### Before you mark it supported

Check all four against real logs, not against what the format looks like it should be:

1. **Which field is per-request usage**, as opposed to a session running total. Getting this wrong inflates totals by orders of magnitude.
2. **The stable per-request identifier**, so dedupe actually fires. If you find yourself synthesising an id from a file offset, every record becomes unique and dedupe silently stops working.
3. **The model id**, or every row prices at zero.
4. **Paste two or three redacted records into a fixture** under `tests/` so the format is pinned and a future change to it fails loudly.

Then register the adapter, set `supported=True`, and add the `collect` flag.

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

## What runs automatically

You do not need to ask for a review checklist — opening a PR runs it:

| Check | What it rejects |
| --- | --- |
| Test suite | Linux + macOS, Python 3.10 and 3.13 |
| Adapter contract | Cumulative counters read as per-request usage, request ids built from byte offsets, missing model ids, unparsed timestamps, non-idempotent parsing, adapters with no fixtures |
| Pricing table | Bad columns, dates, duplicates, negative rates |
| Review guide | Comments on the PR saying whether it touches shared code or an already-working adapter |

Run the same checks locally before pushing:

```bash
python -m pytest
python scripts/validate_adapters.py
```

The one thing CI cannot do is confirm the adapter read *your* logs correctly, which is why the PR template asks for your real `agentabacus report` output. That output is what gets a PR merged.

## Releases

Merging to `main` publishes to PyPI. Nobody tags anything by hand.

The version comes from the git tag via `hatch-vcs`, and the size of the bump comes from the PR title:

| PR title | Result |
| --- | --- |
| `feat: add gemini adapter` | minor — `0.2.0` → `0.3.0` |
| `fix: handle empty transcripts` | patch — `0.2.0` → `0.2.1` |
| `feat!: drop python 3.9` | breaking — minor while pre-1.0, major after |
| anything with `[skip release]` | merges without publishing |

Merges that change nothing under `src/` do not cut a release.
