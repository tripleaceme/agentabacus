# agentabacus

[![PyPI](https://img.shields.io/pypi/v/agentabacus)](https://pypi.org/project/agentabacus/)
[![CI](https://github.com/tripleaceme/agentabacus/actions/workflows/ci.yml/badge.svg)](https://github.com/tripleaceme/agentabacus/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/agentabacus)](https://pypi.org/project/agentabacus/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Local-first analytics for AI coding agents.** Every agent CLI writes session logs to your disk in its own format. Nothing reads all of them. `agentabacus` normalizes them into one schema and answers: what did this cost, which model actually finishes the work, and where is the spend going?

No server. No account. No network calls. It reads files that are already on your machine and writes one DuckDB file.

```bash
uvx agentabacus collect      # read new log data into the archive
uvx agentabacus report       # cost and tokens, last 30 days
```

---

## Why this exists

Three things make the naive version of this tool wrong, and all three are handled here.

### 1. Summing usage per log line overcounts by 2–3×

Claude Code writes **one JSONL line per content block** — thinking, text, each `tool_use` — and every one of those lines repeats the **full usage of the parent API response**. Measured on a real session: 16 assistant lines, 6 actual requests.

|  | naive per-line sum | deduped by `requestId` | overcount |
|---|---:|---:|---:|
| input | 5,273 | 1,759 | 3.0× |
| output | 18,555 | 7,861 | 2.4× |
| cache read | 712,283 | 264,865 | 2.7× |
| cache write | 61,219 | 25,647 | 2.4× |

The multiplier depends on how many content blocks a response happened to emit, so it can't be corrected after the fact with a constant. `agentabacus` keys the `turns` table on `request_id` and merges with `MAX()`.

### 2. Cache writes are not one number

A **1-hour** TTL cache write bills at **2×** base input. A **5-minute** write bills at **1.25×**. A cache read bills at **0.1×**. Claude Code records the split (`cache_creation.ephemeral_1h_input_tokens` vs `ephemeral_5m_input_tokens`); collapsing them into a single `cache_creation_input_tokens` figure misprices exactly the long sessions where cache tokens accumulate.

### 3. Subagent transcripts live in separate files

```
~/.claude/projects/<slug>/<uuid>.jsonl                             # main transcript
~/.claude/projects/<slug>/<uuid>/subagents/agent-*.jsonl           # plain subagent
~/.claude/projects/<slug>/<uuid>/subagents/workflows/wf_*/agent-*.jsonl   # workflow subagent
```

Two things bite here. A `projects/*/*.jsonl` glob — the obvious one — misses every subagent file. And a `*/subagents/*.jsonl` glob still misses the **workflow** subagents one level deeper, which on a machine that runs workflows are the *majority* (measured: 80 of 127). Discovery has to recurse.

Subagent files carry the **parent's** `sessionId` plus their own `agentId`, so the thread is what separates them, not the session. `agentabacus report --by thread` splits main-loop from subagent spend — a number no other tool surfaces.

---

## Install

```bash
uvx agentabacus report          # zero-install trial
pipx install agentabacus        # permanent CLI
```

Then:

```bash
agentabacus doctor              # what's discoverable, what's collected, what has no price
agentabacus collect             # incremental; safe to run repeatedly
```

### Collect automatically (Claude Code plugin)

Transcripts get garbage-collected, so collection has to happen without you remembering. The plugin registers a `SessionEnd` hook that archives each session as it closes:

```
/plugin marketplace add tripleaceme/agentabacus
/plugin install agentabacus@agentabacus
```

The CLI must be on your `PATH` (`pipx install agentabacus`). No daemon, no cron entry.

## Commands

```bash
agentabacus report --since 30d --by model      # or: source project branch day effort speed thread
agentabacus top --limit 10                     # most expensive sessions
agentabacus cache                              # read share and the 1h/5m write split, priced
agentabacus tools                              # tool-call volume and error rate
agentabacus doctor                             # health + pricing gaps
agentabacus export --format parquet            # hand the tables to dbt / Metabase
agentabacus sql "select ..."                   # the schema is yours
```

`--by thread` splits main-loop spend from subagent spend — the number most tools can't show you at all.

## Where the data lives

```
~/.agentabacus/agentabacus.duckdb     # the archive: everything, all time
```

Override with `AGENTABACUS_HOME`. The collector is incremental: it records a byte offset per file and re-reads nothing, so a repeat run over a 350 MB corpus costs one `stat()` per file.

**This matters more than it sounds.** Claude Code garbage-collects old transcripts. Project directories with a `memory/` folder and zero `.jsonl` files are what that looks like afterwards — that history is gone permanently. Once cleanup runs, this database is the only copy. `agentabacus` is an archive with a dashboard on top, not a dashboard.

## Privacy

Prompt and response bodies **never enter the pipeline**. The `prompts` table stores a SHA-256 and a character count; there is no column for the text. That's a schema property, not a filter you have to trust — "does this leak my code?" is answerable by reading `schema.py`.

Nothing is uploaded anywhere. There is no telemetry.

## Pricing

`src/agentabacus/data/pricing.csv` — effective-dated, one row per model per speed tier:

```csv
model_id,speed,valid_from,valid_to,input_per_mtok,output_per_mtok,cache_read_per_mtok,cache_write_5m_per_mtok,cache_write_1h_per_mtok,source_note
claude-opus-5,standard,2020-01-01,,5.00,25.00,0.50,6.25,10.00,anthropic list price
```

Cost is computed as **tokens × price-at-event-timestamp**, via the `turns_costed` view. Joining against a "current price" table would silently reprice last quarter's sessions.

`agentabacus doctor` lists any model seen in your data that has no pricing row — that's the alarm for "a new model shipped and the table is stale", which is otherwise a silent undercount.

**Adding a model is a one-line CSV edit.** Dates currently use an early `valid_from` so historical sessions price at today's rate; real effective dates are welcome as PRs.

## Contributing an adapter

One module exposing `parse(path, kind, start_offset) -> Batch`, a walker in `discovery.py`, one line in `adapters/__init__.py`. See `adapters/claude_code.py` for the reference and `adapters/codex.py` for the minimal template.

**The rule: be a tolerant parser.** These formats are undocumented and change without notice. Route on known shapes, count what you skipped, never raise — a vendor's routine release must not become a crash for every user. Strictness belongs in `schema.py`, not at the edges.

```bash
python tests/test_dedupe.py    # pins the dedupe contract, the TTL split, and torn-line handling
```

## Status

| Source | State |
|---|---|
| Claude Code | verified against real transcripts |
| Codex CLI | **shape-agnostic, unverified** — needs someone with real rollout files |
| Gemini CLI, Cursor, Aider, Cline | not yet written |

## Roadmap

- Edit-survival metric from `file-history-snapshot.trackedFileBackups` (pre-edit backups are already in the transcript, so no git join is needed for Claude Code)
- More adapters
- `agentabacus dash` — local static dashboard
- **Teams**: warehouse sinks (Postgres/Snowflake/BigQuery), redaction policy in version control, a GitHub Action for rollups
- **`dbt_agentabacus`**: staging models over the parquet export, pricing as a seed, tests as drift detection

## License

MIT
