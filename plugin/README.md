# agentabacus — Claude Code plugin

Runs `agentabacus collect` when a session ends, so usage is archived **before**
Claude Code's cleanup can delete the transcript.

## Install

```bash
pipx install agentabacus              # the CLI must be on PATH
```

```
/plugin marketplace add tripleaceme/agentabacus
/plugin install agentabacus
```

Then `agentabacus report` any time.