# agentledger — Claude Code plugin

Runs `agentledger collect` when a session ends, so usage is archived **before**
Claude Code's cleanup can delete the transcript.

## Why a plugin rather than a cron job

Transcripts are garbage-collected. A directory with a `memory/` folder and no
`.jsonl` files is what a project looks like after cleanup has run — that history
is unrecoverable. Collection therefore has to happen without the user
remembering to run it.

A `SessionEnd` hook is the cheapest way to guarantee that:

- fires exactly when the data is freshest, long before cleanup
- incremental by construction — one session's new bytes, milliseconds of work
- no daemon, no cron entry, no background process to explain
- installs from inside the tool people already have open

## Install

```bash
pipx install agentledger              # the CLI must be on PATH
```

```
/plugin marketplace add tripleaceme/agentledger
/plugin install agentledger
```

Then `agentledger report` any time.

## Notes

- The hook is `|| true`: a collection failure must never interfere with your
  session ending.
- It writes only to `~/.agentledger/agentledger.duckdb` (or `$AGENTLEDGER_HOME`).
  Nothing is uploaded.
- Other agent CLIs (Codex, Gemini) aren't covered by this hook — for those,
  run `agentledger collect` on a timer or before reporting.
