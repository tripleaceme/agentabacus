"""Claude Code adapter.

Reads three shapes, all JSONL:

  <slug>/<uuid>.jsonl                     main session transcript
  <slug>/<uuid>/subagents/agent-*.jsonl   one per subagent thread
  history.jsonl                           every prompt typed, survives GC

Two things this adapter exists to get right:

1. **Dedupe by requestId.** Consecutive assistant lines are content blocks of
   ONE API response and each repeats the full usage. Aggregating with MAX per
   requestId is the difference between correct numbers and numbers that are
   2-3x too high. Verified against real transcripts: 16 assistant lines
   collapsed to 6 requests, a 2.4-3.0x overcount if summed naively.

2. **The cache TTL split.** `cache_creation.ephemeral_1h_input_tokens` and
   `ephemeral_5m_input_tokens` bill at different multipliers (2x vs 1.25x of
   base input). We keep them apart all the way to the cost view.

Subagent files carry the PARENT's sessionId plus their own `agentId`, so the
thread_id is what separates them -- not the session id.
"""

from __future__ import annotations

from pathlib import Path

from ..schema import Batch, Prompt, Session, ToolCall, Turn
from .base import as_int, content_chars, iter_lines, parse_ts, sha256, tool_target

SOURCE = "claude_code"


def _project_slug(path: Path) -> str | None:
    """The slug is the directory directly under `projects/`.

    Derived by walking up rather than by a fixed parent index, because subagent
    transcripts sit at two different depths (plain vs workflow subagents).
    Note this is only a label: the slug encoding ('/' -> '-') is lossy and
    irreversible, so `cwd` from inside the file is the authoritative path.
    """
    for parent in path.parents:
        if parent.parent.name == "projects":
            return parent.name
    return None


def _usage_fields(usage: dict) -> dict:
    """Pull the token counts, keeping the two cache-write TTLs separate."""
    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        w1h = as_int(creation.get("ephemeral_1h_input_tokens"))
        w5m = as_int(creation.get("ephemeral_5m_input_tokens"))
    else:
        # Older/other shapes give only the total. Attribute it to the 5m rate,
        # which is the cheaper of the two -- under-report rather than inflate.
        w1h, w5m = 0, as_int(usage.get("cache_creation_input_tokens"))

    details = usage.get("output_tokens_details")
    thinking = as_int(details.get("thinking_tokens")) if isinstance(details, dict) else 0

    server = usage.get("server_tool_use")
    server = server if isinstance(server, dict) else {}

    return {
        "input_tokens": as_int(usage.get("input_tokens")),
        "output_tokens": as_int(usage.get("output_tokens")),
        "thinking_tokens": thinking,
        "cache_read_tokens": as_int(usage.get("cache_read_input_tokens")),
        "cache_write_5m_tokens": w5m,
        "cache_write_1h_tokens": w1h,
        "web_search_requests": as_int(server.get("web_search_requests")),
        "web_fetch_requests": as_int(server.get("web_fetch_requests")),
    }


def parse_history(path: Path, start_offset: int = 0, on_bytes=None) -> Batch:
    """history.jsonl: prompt text, project, sessionId, epoch-ms timestamp.

    Only a hash and a length are kept. The prompt body never enters the
    pipeline, so "does this leak my code?" is answerable from the schema
    instead of from trust in a downstream filter.
    """
    batch = Batch(byte_offset=start_offset)
    for record, offset in iter_lines(path, start_offset, on_bytes):
        batch.byte_offset = offset
        if record.get("__unparsed__"):
            batch.skipped_lines += 1
            continue
        display = record.get("display")
        if not isinstance(display, str):
            continue
        batch.prompts.append(
            Prompt(
                prompt_sha256=sha256(display),
                session_id=record.get("sessionId"),
                source=SOURCE,
                ts=parse_ts(record.get("timestamp")),
                project=record.get("project"),
                char_len=len(display),
            )
        )
    return batch


def parse_transcript(
    path: Path, start_offset: int = 0, is_subagent: bool = False, on_bytes=None
) -> Batch:
    batch = Batch(byte_offset=start_offset)

    turns: dict[str, Turn] = {}
    tools: dict[str, ToolCall] = {}
    meta: dict = {}
    first_ts = last_ts = None
    session_id = thread_id = None

    for record, offset in iter_lines(path, start_offset, on_bytes):
        batch.byte_offset = offset
        if record.get("__unparsed__"):
            batch.skipped_lines += 1
            continue

        rec_type = record.get("type")
        ts = parse_ts(record.get("timestamp"))
        if ts:
            first_ts = ts if first_ts is None or ts < first_ts else first_ts
            last_ts = ts if last_ts is None or ts > last_ts else last_ts

        if record.get("sessionId"):
            session_id = record["sessionId"]
        # Subagent threads are identified by agentId, NOT by a distinct session id.
        if record.get("agentId"):
            thread_id = record["agentId"]
        for key in ("cwd", "version", "gitBranch", "entrypoint", "attributionAgent"):
            if record.get(key):
                meta.setdefault(key, record[key])
        if record.get("isSidechain"):
            is_subagent = True

        message = record.get("message")
        message = message if isinstance(message, dict) else {}
        blocks = message.get("content")
        blocks = blocks if isinstance(blocks, list) else []

        if rec_type == "assistant":
            request_id = record.get("requestId") or message.get("id")
            usage = message.get("usage")
            if request_id and isinstance(usage, dict):
                fields = _usage_fields(usage)
                existing = turns.get(request_id)
                if existing is None:
                    turns[request_id] = Turn(
                        request_id=request_id,
                        session_id=session_id or "",
                        thread_id=thread_id or "main",
                        source=SOURCE,
                        model_id=message.get("model"),
                        ts=ts,
                        effort=record.get("effort"),
                        service_tier=usage.get("service_tier"),
                        speed=usage.get("speed") or "standard",
                        block_lines=1,
                        **fields,
                    )
                else:
                    # THE DEDUPE. Same request, another content block, same usage
                    # repeated. MAX (not +=) so re-reads and partial lines are safe.
                    for key, value in fields.items():
                        setattr(existing, key, max(getattr(existing, key), value))
                    existing.block_lines += 1
                    if existing.ts is None or (ts and ts < existing.ts):
                        existing.ts = ts

            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_id = block.get("id")
                    if not tool_id:
                        continue
                    tools[tool_id] = ToolCall(
                        tool_use_id=tool_id,
                        session_id=session_id or "",
                        thread_id=thread_id or "main",
                        source=SOURCE,
                        request_id=request_id,
                        ts=ts,
                        tool_name=block.get("name"),
                        target=tool_target(block.get("input")),
                    )

        elif rec_type == "user":
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_id = block.get("tool_use_id")
                call = tools.get(tool_id) if tool_id else None
                if call is None:
                    continue
                call.is_error = bool(block.get("is_error"))
                call.result_chars = content_chars(block.get("content"))

    if session_id is None:
        return batch

    thread = thread_id or "main"
    batch.sessions.append(
        Session(
            session_id=session_id,
            thread_id=thread,
            source=SOURCE,
            cwd=meta.get("cwd"),
            project_slug=_project_slug(path),
            git_branch=meta.get("gitBranch"),
            cli_version=meta.get("version"),
            entrypoint=meta.get("entrypoint"),
            agent_type=meta.get("attributionAgent"),
            parent_session_id=session_id if is_subagent else None,
            is_subagent=is_subagent,
            started_at=first_ts,
            ended_at=last_ts,
            transcript_path=str(path),
        )
    )
    batch.turns = list(turns.values())
    batch.tool_calls = list(tools.values())
    return batch


def parse(path: Path, kind: str, start_offset: int = 0, on_bytes=None) -> Batch:
    if kind == "history":
        return parse_history(path, start_offset, on_bytes)
    return parse_transcript(
        path, start_offset, is_subagent=(kind == "subagent"), on_bytes=on_bytes
    )
