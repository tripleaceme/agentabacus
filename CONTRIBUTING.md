## Contributing

Please follow the instructions below

### 1. Add or correct a model price

`src/agentabacus/data/pricing.csv`, one line:

```csv
model_id,speed,valid_from,valid_to,input_per_mtok,output_per_mtok,cache_read_per_mtok,cache_write_5m_per_mtok,cache_write_1h_per_mtok,source_note
claude-opus-5,standard,2020-01-01,,5.00,25.00,0.50,6.25,10.00,anthropic list price
```

Rules:

- **`valid_to` empty means "still current."** To change a price, close the old row with a `valid_to` and add a new row starting the next day. Never edit a historical row's rates.
- **Cache columns are not optional.** Reads bill at 0.1× input, 5-minute writes at 1.25×, 1-hour writes at 2×. If a provider prices them differently, put the real numbers in; the schema doesn't assume the multipliers.
- **Dated snapshot IDs need no row.** `claude-haiku-4-5-20251001` normalizes to `claude-haiku-4-5` automatically.
- Cite where the numbers came from in `source_note`.

CI validates column order, date parsing, duplicate keys, and negative values.

Run `agentabacus doctor` to see models present in your own data with no pricing row, that list is the to-do list.

**Where to get the numbers.** Always use the provider's own published rates, and link the page you read in `source_note`:

| Provider | Pricing page |
| --- | --- |
| Anthropic | <https://docs.claude.com/en/docs/about-claude/pricing> |
| OpenAI | <https://platform.openai.com/docs/pricing> |
| Google Gemini | <https://ai.google.dev/gemini-api/docs/pricing> |
| DeepSeek | <https://api-docs.deepseek.com/quick_start/pricing> |
| xAI | <https://docs.x.ai/docs/models> |
| Mistral | <https://mistral.ai/pricing> |

Two things to watch when reading those pages:

- **Prices are usually quoted per million tokens (MTok)**, which is what the CSV wants. Some pages quote per 1K, so multiply by 1000.
- **Cache and batch rates are often in a separate table or a footnote**, not next to the headline input/output numbers. If you cannot find them, say so in `source_note` rather than guessing, and the multipliers noted above are the safest default.

## 2. Add an adapter

This is the contribution that matters most, and the one CI cannot fully check for you.

### What an adapter is

One module with one function:

```python
def parse(path: Path, kind: str, start_offset: int = 0, on_bytes=None) -> Batch:
```

It reads a log file and returns a `Batch` of normalised rows. It never touches the database, never decides what a thing costs, and never filters by time. Those all happen later, from the rows you return.

| Argument | What it is |
| --- | --- |
| `path` | The file to read. |
| `kind` | Which flavour of file, from your walker: `"transcript"`, `"subagent"`, `"history"`, or whatever your agent needs. |
| `start_offset` | Resume point in bytes. Collection is incremental; everything before this has already been stored. |
| `on_bytes` | Optional. Call it with bytes consumed as you go and the progress bar moves inside large files. Pass it straight to `iter_lines()` and you get this free. |

### What to fill in

`Batch` carries four lists. `turns` is the one that matters; the rest can be empty and the tool still works.

| Field | Grain | Notes |
| --- | --- | --- |
| `turns` | **one row per API request** | The whole point. See the grain warning below. |
| `sessions` | one row per session, plus one per subagent thread | Gives reports their project, branch and time range. |
| `tool_calls` | one row per tool invocation | Powers `agentabacus tools`. Set `is_error` only when you actually know it. |
| `prompts` | one row per human prompt | **Hash and length only.** There is no column for the text. |

Also set `batch.byte_offset` to the end of the last **complete** record you read. `iter_lines()` gives you this. Get it wrong and the next run either re-reads work or skips data.

### The grain is the whole job

Everything else is plumbing. This is the part that goes wrong:

> **One `turns` row must equal one billable request, not one line, not one message, not one event.**

Log formats rarely hand you that directly. Three ways they hide it, all seen in real files:

- **Usage repeated across several records.** Claude Code writes one line per content block — thinking, text, each tool call — and *every one* carries the parent response's full usage. Summing per line overcounts by 2–3×, and the multiplier changes per response, so it cannot be corrected afterwards. Key on the request id and merge with `max()`.
- **Running totals presented as usage.** Some agents record a session counter that grows with every event. Read that as per-request and you get numbers like 67 million tokens for one request. If a value only ever increases across a session, it is a total, not a usage figure.
- **No obvious request id.** Resist inventing one from a file offset or line number. That makes every record unique, dedupe never fires, and the failure is silent. If the provider truly gives you nothing, use the response id, the message id, or a hash of the fields that identify the response — never its position in the file.

Sanity check before you open the PR: a session's total input tokens should be roughly the context window times the number of requests. If it is thousands of times more, you are summing something cumulative.

### Reading an undocumented format

Nothing about these formats is published, so start by looking:

```bash
# what kinds of record exist, and how often
cat ~/.someagent/sessions/*.jsonl | jq -r '.type // "none"' | sort | uniq -c

# which records carry token counts, and what the shape looks like
cat ~/.someagent/sessions/*.jsonl | jq 'select(.. | objects | has("input_tokens"))' | head -40
```

Then ask, in order: which record type is one request? What identifies it? Is that usage for this request or for the session so far? Where is the model name? Where is the timestamp?

### Be a tolerant parser

These formats are version-dependent and change without notice. Route on the shapes you know, count what you skip, and **never raise** — a vendor's routine release must not become a crash for every user. Strictness belongs in `schema.py`, not at the edges.

`iter_lines()` already handles the two traps here: a file caught mid-write (the torn trailing line is left for the next run rather than half-parsed) and a malformed line (skipped and counted, not fatal).

### Wiring it up

Four files. Until the fourth, users with that agent already see it — `doctor` reports their logs exist and links here — but nothing is parsed or stored.

| File | What to add |
| --- | --- |
| `agents.py` | The agent is probably already listed with `supported=False`. Fill in name, flag, label, env var and default paths. |
| `adapters/<name>.py` | Your `parse()`. |
| `discovery.py` | A walker yielding the log files, plus an entry in `_WALKERS`. Recurse rather than globbing a fixed depth — Claude Code nests subagent transcripts at two different depths and a fixed-depth glob silently dropped most of them. |
| `adapters/__init__.py` | Register the parser. Then flip `supported=True` in `agents.py` and add a `--<flag>` option to `collect` in `cli.py`. |

Two tests already guard the wiring: one fails if an agent is marked supported with no adapter, another if it has no discovery walker — because that combination passes every other test while `collect --youragent` exits 0 reporting "0 file(s) read".

### Ship fixtures

Put two or three **redacted** real records in `tests/fixtures/<agent>/`. No prompts, no code, no keys — token counts, ids, timestamps and model names are enough.

This is not a formality. It is how the adapter contract runs against your format in CI, and it is what lets someone fix your adapter in a year when the vendor changes something and you have moved on.

## Running things

```bash
uv venv && uv pip install -e ".[dev]"
python -m pytest                # the contract tests
agentabacus doctor              # what's discoverable on your machine
```

## What this project deliberately does not do

- No hosted service, no account, no telemetry.
- **Prompt and response bodies never enter the pipeline.** The `prompts` table stores a hash and a length; there is no column for the text. Please don't add one.

Team/warehouse sinks and a dbt package are on the roadmap, not in scope yet.

## What runs automatically

Opening a PR runs the following:

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