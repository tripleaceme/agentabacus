# agentabacus

<p align="center">
  <img src="https://raw.githubusercontent.com/tripleaceme/agentabacus/main/assets/social-preview.png" alt="agentabacus — local-first analytics for AI coding agents" width="820">
</p>

[![PyPI](https://img.shields.io/pypi/v/agentabacus)](https://pypi.org/project/agentabacus/)
[![CI](https://github.com/tripleaceme/agentabacus/actions/workflows/ci.yml/badge.svg)](https://github.com/tripleaceme/agentabacus/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/agentabacus)](https://pypi.org/project/agentabacus/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Local-first analytics for AI coding agents.**

AI coding agents generate session logs, but each agent stores them differently. `agentabacus` brings those logs into one local database so you can see:

* What did I spend?
* Which models are doing the work?
* Which projects or sessions cost the most?
* How much of the usage comes from subagents?
* Where are tokens being spent?

No server. No account. No telemetry. Your data stays on your machine.

## Install

Try it without installing:

```bash
uvx agentabacus report
```

Or install it permanently:

```bash
pipx install agentabacus
```

Then check what `agentabacus` can find:

```bash
agentabacus doctor
```

Collect your agent logs:

```bash
agentabacus collect
```

Collection is incremental, so you can safely run it again as new sessions are created.

## Reports

Get a cost and usage report:

```bash
agentabacus report
```

Filter by time:

```bash
agentabacus report --since 30d
```

Group the results:

```bash
agentabacus report --by model
```

Available groupings include:

```text
source
project
branch
model
day
effort
speed
thread
```

For example, `--by thread` separates your main agent from its subagents.

## Other commands

### Find expensive sessions

```bash
agentabacus top --limit 10
```

### Understand cache usage

```bash
agentabacus cache
```

### Analyse tool calls

```bash
agentabacus tools
```

### Export your data

Export to Parquet for use with your own analytics tools:

```bash
agentabacus export --format parquet
```

### Query the data directly

The underlying data is stored in DuckDB:

```bash
agentabacus sql "SELECT * FROM turns LIMIT 10"
```

## Supported agents

| Agent       | Status          |
| ----------- | --------------- |
| Claude Code | ✅ Supported     |
| Codex CLI   | Open for contribution |
| Gemini CLI  | Open for contribution         |
| Cursor      | Open for contribution         |
| Aider       | Open for contribution         |
| Cline       | Open for contribution         |

More adapters are being added.

## How it works

`agentabacus` discovers session logs on your machine, parses them into a common schema, and stores the results in DuckDB.

```text
Agent logs
    ↓
Discovery
    ↓
Adapters
    ↓
Normalized schema
    ↓
DuckDB
    ↓
Reports / SQL / Parquet
```

It also handles a few problems that can make agent usage data misleading:

* Duplicate usage records in agent transcripts
* Different cache pricing tiers
* Subagent transcripts stored separately from the main session
* Model pricing that changes over time

## Data & privacy

Everything stays local.

By default, the database is stored at:

```text
~/.agentabacus/agentabacus.duckdb
```

You can change the location with:

```bash
export AGENTABACUS_HOME=/path/to/data
```

`agentabacus` does **not** upload your prompts, responses, code, or usage data.

Prompt and response text is not stored. The database only keeps metadata such as hashes, counts, tokens, costs, and session information.

## Claude Code auto-collection

Claude Code transcripts can be archived automatically when a session ends.

Install the plugin:

```text
/plugin marketplace add tripleaceme/agentabacus
/plugin install agentabacus@agentabacus
```

Make sure `agentabacus` is available on your `PATH`:

```bash
pipx install agentabacus
```

No daemon or cron job is required.

## For contributors

Agent logs are not stable APIs, so adapters are designed to tolerate format changes.

A new adapter generally needs:

1. A parser
2. Discovery support
3. Registration in the adapter package

See the existing adapters for examples.

Install the development dependencies and run the tests with:

```bash
pip install -e ".[dev]"
python -m pytest
```

## Roadmap

* More agent adapters
* Local dashboard
* Edit-survival metrics
* Warehouse exports
* dbt integration
* GitHub Actions for usage rollups

## License

MIT