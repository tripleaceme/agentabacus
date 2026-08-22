<!--
Adding an agent? The automated checks cover whether the code works. What they
cannot check is whether it read YOUR logs correctly, which is what the output
section below is for.
-->

## What this changes

<!-- One or two sentences. -->

## If you added an agent

Paste the real output from your own machine. This is the part no CI job can
produce, and it is what gets the PR merged.

<details>
<summary><code>agentabacus doctor</code></summary>

```text
paste here
```

</details>

<details>
<summary><code>agentabacus collect --&lt;your-agent&gt;</code></summary>

```text
paste here
```

</details>

<details>
<summary><code>agentabacus report --since all --by model</code></summary>

```text
paste here
```

</details>

Then confirm each of these against that output — a reviewer will read the
numbers, not just the ticks:

- [ ] Model names are real, not `(unknown)` — rows without a model price at zero
- [ ] Costs are plausible for what you actually spent
- [ ] Token counts are per request, not a running session total
- [ ] `JSONL lines collapsed into N requests` shows dedupe firing, if the format repeats usage
- [ ] Fixtures added under `tests/fixtures/<agent>/` — redacted, no prompts, no code, no keys

## Checks that run automatically

Nothing below needs doing by hand; it is listed so you know what will run.

- Full test suite on Linux and macOS, Python 3.10 and 3.13
- Adapter contract against your fixtures: implausible token counts,
  offset-derived request ids, missing model ids, unparsed timestamps,
  non-idempotent parsing
- Pricing table validation
- A review guide showing whether this PR touches shared code or an
  already-working adapter

## Anything else a reviewer should know

<!-- Formats you were unsure about, fields you could not find, decisions you made. -->
