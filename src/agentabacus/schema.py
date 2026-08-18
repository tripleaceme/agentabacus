"""The canonical schema.

Adapters are permissive parsers; this is the strict contract they must produce.
Every downstream metric depends on these grains holding:

  sessions   : one row per (session_id, thread_id)
  turns      : one row per REQUEST  -- see the dedupe note below
  tool_calls : one row per tool invocation
  prompts    : one row per human prompt

THE DEDUPE CONTRACT
-------------------
Claude Code writes one JSONL line per *content block* (thinking, text, tool_use),
and every one of those lines carries the FULL usage of the parent API response.
Summing usage per line overcounts by 2-3x -- the multiplier varies with how many
blocks a response happened to emit, so it cannot be corrected after the fact.

`turns` is therefore keyed on request_id, and the loader aggregates with MAX()
per field across all lines sharing a request_id. MAX (not FIRST) because a
partially-written line can appear before the complete one; token counts only
ever grow within a request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id        VARCHAR NOT NULL,
    thread_id         VARCHAR NOT NULL,   -- 'main', or the subagent id
    source            VARCHAR NOT NULL,   -- claude_code | codex | ...
    cwd               VARCHAR,            -- verbatim from the log, never decoded from the slug
    project_slug      VARCHAR,
    git_branch        VARCHAR,
    cli_version       VARCHAR,
    entrypoint        VARCHAR,
    agent_type        VARCHAR,            -- subagent type, when known
    parent_session_id VARCHAR,
    is_subagent       BOOLEAN NOT NULL DEFAULT FALSE,
    started_at        TIMESTAMP,
    ended_at          TIMESTAMP,
    transcript_path   VARCHAR,
    PRIMARY KEY (session_id, thread_id)
);

CREATE TABLE IF NOT EXISTS turns (
    request_id             VARCHAR PRIMARY KEY,
    session_id             VARCHAR NOT NULL,
    thread_id              VARCHAR NOT NULL,
    source                 VARCHAR NOT NULL,
    model_id               VARCHAR,
    ts                     TIMESTAMP,
    effort                 VARCHAR,
    service_tier           VARCHAR,
    speed                  VARCHAR,        -- 'standard' | 'fast'; drives pricing
    input_tokens           BIGINT DEFAULT 0,
    output_tokens          BIGINT DEFAULT 0,
    thinking_tokens        BIGINT DEFAULT 0,
    cache_read_tokens      BIGINT DEFAULT 0,
    cache_write_5m_tokens  BIGINT DEFAULT 0,
    cache_write_1h_tokens  BIGINT DEFAULT 0,
    web_search_requests    BIGINT DEFAULT 0,
    web_fetch_requests     BIGINT DEFAULT 0,
    block_lines            BIGINT DEFAULT 0  -- JSONL lines collapsed into this row
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_use_id  VARCHAR PRIMARY KEY,
    session_id   VARCHAR NOT NULL,
    thread_id    VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    request_id   VARCHAR,
    ts           TIMESTAMP,
    tool_name    VARCHAR,
    target       VARCHAR,     -- file path / command / url, when the input exposes one
    is_error     BOOLEAN,
    result_chars BIGINT
);

CREATE TABLE IF NOT EXISTS prompts (
    prompt_id     VARCHAR PRIMARY KEY,   -- hash(body_hash, session, ts): dedupes re-reads
    prompt_sha256 VARCHAR NOT NULL,      -- body hash only; the body itself is never stored
    session_id    VARCHAR,
    source        VARCHAR NOT NULL,
    ts            TIMESTAMP,
    project       VARCHAR,
    char_len      BIGINT
);

-- Collector watermarks. JSONL is append-only, so re-reads resume at the byte
-- offset of the last COMPLETE line; a torn trailing line is re-read next run.
CREATE TABLE IF NOT EXISTS _files (
    path          VARCHAR PRIMARY KEY,
    source        VARCHAR NOT NULL,
    mtime         DOUBLE,
    size_bytes    BIGINT,
    byte_offset   BIGINT DEFAULT 0,
    collected_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS _meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR
);

CREATE TABLE IF NOT EXISTS pricing (
    model_id        VARCHAR NOT NULL,
    speed           VARCHAR NOT NULL DEFAULT 'standard',
    valid_from      DATE NOT NULL,
    valid_to        DATE,               -- NULL = still current
    input_per_mtok         DOUBLE NOT NULL,
    output_per_mtok        DOUBLE NOT NULL,
    cache_read_per_mtok    DOUBLE NOT NULL,
    cache_write_5m_per_mtok DOUBLE NOT NULL,
    cache_write_1h_per_mtok DOUBLE NOT NULL,
    source_note     VARCHAR
);
"""

# Cost is a VIEW, not a stored column: when the pricing table is corrected or a
# model's rate changes, every historical row reprices correctly. Joining on
# ts BETWEEN valid_from AND valid_to is what makes it price-at-event-time
# rather than price-today.
VIEWS = """
CREATE OR REPLACE VIEW turns_normalized AS
SELECT
    *,
    -- Dated snapshot IDs (claude-haiku-4-5-20251001) price the same as their
    -- alias (claude-haiku-4-5). Normalizing here means the pricing table holds
    -- one row per model instead of one per release date.
    regexp_replace(COALESCE(model_id, ''), '-[0-9]{8}$', '') AS model_key,
    -- '<synthetic>' and friends are placeholders Claude Code writes for turns
    -- that never hit the API. They are not a pricing gap and must not be
    -- reported as one -- otherwise the stale-pricing alarm cries wolf forever.
    (model_id IS NULL OR model_id LIKE '<%') AS non_billable
FROM turns;

CREATE OR REPLACE VIEW turns_costed AS
SELECT
    t.*,
    (p.input_per_mtok IS NOT NULL OR t.non_billable) AS priced,
    (t.input_tokens          / 1e6) * COALESCE(p.input_per_mtok, 0)          AS cost_input,
    (t.output_tokens         / 1e6) * COALESCE(p.output_per_mtok, 0)         AS cost_output,
    (t.cache_read_tokens     / 1e6) * COALESCE(p.cache_read_per_mtok, 0)     AS cost_cache_read,
    (t.cache_write_5m_tokens / 1e6) * COALESCE(p.cache_write_5m_per_mtok, 0) AS cost_cache_write_5m,
    (t.cache_write_1h_tokens / 1e6) * COALESCE(p.cache_write_1h_per_mtok, 0) AS cost_cache_write_1h,
    (t.input_tokens          / 1e6) * COALESCE(p.input_per_mtok, 0)
  + (t.output_tokens         / 1e6) * COALESCE(p.output_per_mtok, 0)
  + (t.cache_read_tokens     / 1e6) * COALESCE(p.cache_read_per_mtok, 0)
  + (t.cache_write_5m_tokens / 1e6) * COALESCE(p.cache_write_5m_per_mtok, 0)
  + (t.cache_write_1h_tokens / 1e6) * COALESCE(p.cache_write_1h_per_mtok, 0) AS cost_usd
FROM turns_normalized t
LEFT JOIN pricing p
       ON p.model_id = t.model_key
      AND p.speed    = COALESCE(t.speed, 'standard')
      AND CAST(t.ts AS DATE) >= p.valid_from
      AND (p.valid_to IS NULL OR CAST(t.ts AS DATE) <= p.valid_to);

CREATE OR REPLACE VIEW session_totals AS
SELECT
    s.session_id,
    s.thread_id,
    s.source,
    s.cwd,
    s.git_branch,
    s.is_subagent,
    s.started_at,
    s.ended_at,
    COUNT(t.request_id)                      AS requests,
    COALESCE(SUM(t.cost_usd), 0)             AS cost_usd,
    COALESCE(SUM(t.input_tokens), 0)         AS input_tokens,
    COALESCE(SUM(t.output_tokens), 0)        AS output_tokens,
    COALESCE(SUM(t.cache_read_tokens), 0)    AS cache_read_tokens,
    COALESCE(SUM(t.cache_write_5m_tokens
              + t.cache_write_1h_tokens), 0) AS cache_write_tokens
FROM sessions s
LEFT JOIN turns_costed t
       ON t.session_id = s.session_id AND t.thread_id = s.thread_id
GROUP BY ALL;
"""


@dataclass
class Session:
    session_id: str
    thread_id: str
    source: str
    cwd: str | None = None
    project_slug: str | None = None
    git_branch: str | None = None
    cli_version: str | None = None
    entrypoint: str | None = None
    agent_type: str | None = None
    parent_session_id: str | None = None
    is_subagent: bool = False
    started_at: datetime | None = None
    ended_at: datetime | None = None
    transcript_path: str | None = None


@dataclass
class Turn:
    request_id: str
    session_id: str
    thread_id: str
    source: str
    model_id: str | None = None
    ts: datetime | None = None
    effort: str | None = None
    service_tier: str | None = None
    speed: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0
    block_lines: int = 0


@dataclass
class ToolCall:
    tool_use_id: str
    session_id: str
    thread_id: str
    source: str
    request_id: str | None = None
    ts: datetime | None = None
    tool_name: str | None = None
    target: str | None = None
    is_error: bool | None = None
    result_chars: int | None = None


@dataclass
class Prompt:
    prompt_sha256: str
    session_id: str | None
    source: str
    ts: datetime | None = None
    project: str | None = None
    char_len: int = 0


@dataclass
class Batch:
    """What an adapter returns for one file."""

    sessions: list[Session] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompts: list[Prompt] = field(default_factory=list)
    byte_offset: int = 0
    skipped_lines: int = 0
