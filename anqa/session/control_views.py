"""JSON payloads for the control plane (desktop palette / editors).

Pure domain loaders → JSON-RPC payloads. No Textual. Used by
:class:`~anqa.control.server.ControlServer` handlers.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from concurrent.futures import Future
from pathlib import Path
from typing import ClassVar

from .. import event_types as et
from ..bounded_cache import BoundedCache
from ..constants import OVERVIEW_CACHE_MAXSIZE, TURN_VIEW_CACHE_MAXSIZE
from ..core import event_from_native, native_overview
from ..harness.registry import harness_product, ref_from_path, require_adapter
from ..models import (
    JsonObject,
    JsonValue,
    SessionMeta,
    ToolInputBag,
    TraceEvent,
    as_json_object,
    json_as_int,
)
from ..notes import load_schema, notes_snapshot
from ..session.tagged_blocks import unwrap_for_display
from ..session.turns import (
    TurnSegment,
    event_display_turn_map,
    event_matches_timeline_kind,
    harness_user_chrome_heading,
    segment_timeline_turns,
)
from ..tool_display import (
    display_message_text,
    display_tool_output,
    event_still_paths,
    job_list_preview,
    list_event_preview,
    preserve_primary_raw_input,
    tool_family,
    tool_input_fields,
)
from .event_search import ensure_indexed, matching_indexes
from .jobs import SessionJobs, job_input_stamp
from .query import apply_catalog_presence_row, catalog_presence, turn_matches_query
from .subagents import (
    SubagentRun,
    event_child_session_id,
    event_subagent_fields,
    spawn_fields,
    subagent_list_preview,
    subagent_run_mapping,
    subagent_runs_for_session,
)
from .workflows import workflow_list_preview

type TimelineStamp = tuple[float, int, int, int]

# Concurrent HUD open + live poll + notifies were double-building the same
# multi‑MB session overview (~12–30s each). Join one flight per path and cache
# by timeline/notes inputs so warm re-polls stay cheap.
_JobFilesStamp = tuple[tuple[str, int, int], ...]
_MonitorStatusStamp = tuple[tuple[str, str], ...]
_OverviewStamp = tuple[
    TimelineStamp,
    str,
    _JobFilesStamp,
    _MonitorStatusStamp,
]
_TurnViewCache = tuple[TimelineStamp, list[TurnSegment], dict[int, int]]

logger = logging.getLogger(__name__)

DEFAULT_TIMELINE_LIMIT = 300
MAX_TIMELINE_LIMIT = 2000
DEFAULT_CONTENT_CHARS = 4000
MAX_CONTENT_CHARS = 50_000


def overview_stat_counts(events: list[TraceEvent]) -> JsonObject:
    """Event-type and tool counts from an already-parsed timeline.

    One pass. Used by ``session/overview`` so clients do not page Timeline
    just to fill Stats.
    """
    types: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    for ev in events:
        key = (ev.event_type or "").strip()
        if key:
            types[key] += 1
        if ev.event_type == "tool_call" and (ev.tool_name or "").strip():
            tools[ev.tool_name] += 1
    return {
        "eventTypes": [{"id": name, "count": n} for name, n in types.most_common()],
        "tools": [{"id": name, "count": n} for name, n in tools.most_common()],
    }


def _stat_rows_to_counter(rows: JsonValue) -> Counter[str]:
    out: Counter[str] = Counter()
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("id") or "").strip()
        if not name:
            continue
        raw_n = row.get("count")
        if not isinstance(raw_n, int | float):
            continue
        n = int(raw_n)
        if n > 0:
            out[name] = n
    return out


def overview_stat_counters(
    payload: JsonObject | None,
) -> tuple[Counter[str], Counter[str]] | None:
    """Parse ``session/overview`` ``stats`` when that field is present."""
    if payload is None:
        return None
    raw = payload.get("stats")
    if not isinstance(raw, dict):
        return None
    return _stat_rows_to_counter(raw.get("eventTypes")), _stat_rows_to_counter(raw.get("tools"))


def session_meta_mapping(
    meta: SessionMeta,
    *,
    path: Path | None = None,
    origin: str | None = None,
) -> JsonObject:
    """Serialize :class:`SessionMeta` for catalog rows and ``session/overview``."""
    try:
        path_str = str((path or meta.session_dir).resolve())
    except OSError:
        path_str = str(path or meta.session_dir)
    origin_key = (origin or meta.origin or "host").strip() or "host"
    from ..paths import is_import_locator
    from .subagents import read_session_kind

    loc = path or meta.session_dir
    imported = origin_key == "import" or is_import_locator(loc)
    if imported:
        origin_key = "import"

    kind_path = path or meta.session_dir
    presence = catalog_presence(kind_path, meta)
    apply_catalog_presence_row(meta, as_json_object(presence))
    return {
        "sessionId": (meta.session_id or meta.session_dir.name).strip(),
        "path": path_str,
        "title": meta.title or "",
        "summary": meta.summary_text or "",
        "label": meta.label,
        "model": meta.model_display,
        "modelId": meta.model_id or "",
        "reasoningEffort": meta.reasoning_effort or "",
        "status": meta.list_status_label(),
        "outcome": meta.turn_outcome or "",
        "origin": origin_key,
        "imported": imported,
        "harness": (meta.harness or "").strip(),
        "harnessVersion": (meta.harness_version or "").strip(),
        "harnessLabel": harness_product(meta.harness),
        "createdAt": meta.created_at or "",
        "updatedAt": meta.updated_at or "",
        "numMessages": int(meta.num_messages or 0),
        "numEvents": int(meta.num_events or 0),
        "durationSeconds": float(meta.duration_seconds or 0),
        "duration": meta.duration_str,
        "toolCallCount": int(meta.tool_call_count or 0),
        "toolFailureCount": int(meta.tool_failure_count or 0),
        "errorCount": int(meta.error_count or 0),
        "doomLoopWarnings": int(meta.doom_loop_warnings or 0),
        "linesAdded": int(meta.lines_added or 0),
        "linesRemoved": int(meta.lines_removed or 0),
        "contextWindowUsagePct": meta.context_window_usage_pct,
        "contextTokensUsed": meta.context_tokens_used,
        "contextWindowTokens": meta.context_window_tokens,
        "contextUsage": meta.context_usage_str,
        "contextUsageCompact": meta.context_usage_compact,
        "compactionCount": int(meta.compaction_count or 0),
        "gitRepo": meta.git_repo or "",
        "gitBranch": meta.git_branch or "",
        "gitCommit": meta.git_commit or "",
        "taskId": meta.task_id or "",
        "runId": meta.run_id or "",
        "loopCount": int(meta.loop_count or 0),
        "turnCount": int(meta.turn_count or 0),
        "turnInProgress": bool(meta.turn_in_progress),
        "turnFailed": bool(meta.turn_failed),
        "sessionKind": read_session_kind(kind_path) if kind_path else "",
        **presence,
    }


def timeline_event_mapping(
    event: TraceEvent,
    *,
    content_chars: int = DEFAULT_CONTENT_CHARS,
    turn_index: int | None = None,
    session_dir: Path | None = None,
) -> JsonObject:
    """Serialize one timeline event for ``session/timeline`` / overview.

    Includes ``kind`` / ``toolFamily`` so palette clients can color and unpack
    the same way as the TUI without re-implementing taxonomy. Optional
    *turn_index* is the trace ``turn_started.turn_number`` for this event.
    """
    cap = max(0, min(int(content_chars), MAX_CONTENT_CHARS))
    content_raw = event.content if isinstance(event.content, str) else str(event.content or "")
    # Strip outer harness tags for display (keep raw length for truncation meta).
    content = display_message_text(unwrap_for_display(content_raw))
    tname = (event.tool_name or "").strip()
    content = display_tool_output(content, tool_name=tname)
    truncated = len(content) > cap
    body = content[:cap] if cap else ""
    raw: JsonValue = {}
    if isinstance(event.raw_input, ToolInputBag):
        inner = event.raw_input.raw()
        raw = as_json_object(inner) if isinstance(inner, dict) else {}
    elif isinstance(event.raw_input, dict):
        raw = as_json_object(event.raw_input)
    if not raw or cap <= 0 or not isinstance(raw, dict):
        raw = {}
    else:
        raw = preserve_primary_raw_input(as_json_object(raw), cap)
    kind = et.event_kind(event.event_type)
    family = (
        tool_family(tname) if kind in {et.EventKind.TOOL, et.EventKind.TOOL_RESULT} or tname else ""
    )
    chrome_heading = (
        harness_user_chrome_heading(content_raw)
        if kind is et.EventKind.USER or event.event_type in et.USER_TYPES
        else None
    )
    # Harness injects system-reminder / background-task bodies as user_message_chunk;
    # re-label so TUI/HUD do not present them as operator "User" rows.
    if chrome_heading is not None:
        kind = et.EventKind.SYSTEM
    # Prefer structured tool headline when available.
    if kind is et.EventKind.TOOL and tname:
        heading = tname if not family else f"{tname}"
    elif kind is et.EventKind.TOOL_RESULT and tname:
        heading = f"{tname} result"
    elif chrome_heading is not None:
        heading = chrome_heading
    elif kind is et.EventKind.USER:
        heading = "User"
    elif kind is et.EventKind.AGENT:
        heading = "Assistant"
    elif kind is et.EventKind.THOUGHT:
        heading = "Thought"
    elif kind is et.EventKind.ERROR:
        heading = "Error"
    elif kind is et.EventKind.SYSTEM:
        heading = "System"
    else:
        heading = event.type_label
    type_label = chrome_heading.lower() if chrome_heading else event.type_label
    raw_map = as_json_object(raw) if isinstance(raw, dict) else {}
    if event.event_type in et.TASK_TYPES or event.event_type.startswith("scheduled_task_"):
        preview = job_list_preview(event.event_type, raw_map, event.content)[:200]
    elif event.event_type in et.SUBAGENT_TYPES:
        preview = subagent_list_preview(event.event_type, raw_map, event.content)[:200]
    elif tname == "workflow":
        type_label = "workflow done" if event.event_type in et.TOOL_UPDATE_TYPES else "workflow"
        heading = type_label
        preview = (workflow_list_preview(raw_map) or list_event_preview(event.summary_line, tname))[
            :200
        ]
    else:
        preview = list_event_preview(event.summary_line, tname)[:200]
    fields = tool_input_fields(tname, raw_map, max_chars=cap) if raw_map else []
    tool_fields: list[JsonValue] = list(fields)
    stills = event_still_paths(
        content_raw,
        tool_name=tname,
        images=event.images,
        session_dir=session_dir,
        extra_paths=event.still_paths,
    )
    img_paths: list[JsonValue] = [str(path) for path in stills]
    img_path = str(img_paths[0]) if img_paths else ""
    row: JsonObject = {
        "index": int(event.index),
        "type": event.event_type or "",
        "typeLabel": type_label,
        "kind": kind,
        "toolFamily": family,
        "heading": heading,
        "harnessChrome": chrome_heading is not None,
        "timestamp": event.timestamp,
        "time": event.time_str,
        "content": body,
        "contentTruncated": truncated,
        "contentLength": len(content),
        "toolName": tname,
        "toolCallId": event.tool_call_id or "",
        "isError": bool(event.is_error),
        "updateIndex": int(event.update_index or 0),
        "promptIndex": event.prompt_index,
        "turnIndex": int(turn_index) if turn_index is not None else None,
        "preview": preview,
        "rawInput": raw,
        "toolFields": tool_fields,
        "imagePath": img_path,
        "imagePaths": img_paths,
    }
    extra = event_subagent_fields(event)
    if extra:
        row.update(extra)
        raw_out = row.get("rawInput")
        if isinstance(raw_out, dict):
            merged = dict(raw_out)
            for key, val in extra.items():
                merged.setdefault(key, val)
            row["rawInput"] = merged
    return row


def event_raw_json(
    event: TraceEvent,
    *,
    content_chars: int = MAX_CONTENT_CHARS,
    session_dir: Path | None = None,
    turn_index: int | None = None,
) -> str:
    """Pretty JSON of one timeline event for the Pretty/Raw toggle.

    :param event: Timeline event.
    :param content_chars: Body cap (clamped to ``MAX_CONTENT_CHARS``).
    :param session_dir: Session directory for still paths.
    :param turn_index: Enclosing turn number when known.
    :returns: Indented JSON (same fields as ``session/timeline``).
    """
    row = timeline_event_mapping(
        event,
        content_chars=content_chars,
        turn_index=turn_index,
        session_dir=session_dir,
    )
    return json.dumps(row, indent=2, ensure_ascii=False, default=str)


def turn_segment_mapping(
    seg: TurnSegment,
    *,
    include_event_indexes: bool = True,
    assistant_max_chars: int = 12_000,
    subagent_runs: list[SubagentRun] | None = None,
) -> JsonObject:
    """Serialize one turn segment for ``session/turns`` / overview turns.

    :param assistant_max_chars: Cap on assistant wrap-up text. Overview uses a
        short cap so large sessions stay small; full ``session/turns`` keeps
        the default.
    """
    summary, user_index = seg.user_prompt_preview()
    assistant, assistant_index = seg.assistant_preview(max_chars=assistant_max_chars)
    row: JsonObject = {
        "turnIndex": int(seg.turn_index),
        "turnNumber": seg.turn_number,
        "promptIndex": seg.prompt_index,
        "outcome": seg.outcome or "",
        "open": bool(seg.open),
        "label": seg.label,
        "summary": summary,
        "userEventIndex": user_index,
        "assistantSummary": assistant,
        "assistantEventIndex": assistant_index,
        "eventCount": int(seg.event_count),
        "toolCallCount": int(seg.tool_call_count),
        "toolErrorCount": int(seg.tool_error_count),
        "userCount": int(seg.user_count),
        "assistantCount": int(seg.assistant_count),
        "errorEventCount": int(seg.error_event_count),
        "firstIndex": seg.first_index,
        "lastIndex": seg.last_index,
        "durationSeconds": seg.duration_seconds(),
    }
    if include_event_indexes:
        row["eventIndexes"] = [int(e.index) for e in seg.events]
    if subagent_runs is not None:
        row["subagentRuns"] = [
            subagent_run_mapping(run)
            for run in subagent_runs
            if run.parent_turn_index == seg.turn_index
        ]
    return row


class SessionOverview:
    """Cached ``session/overview`` payload and stamp-keyed turn view."""

    _cache: ClassVar[BoundedCache[tuple[_OverviewStamp, JsonObject]]] = BoundedCache(
        OVERVIEW_CACHE_MAXSIZE
    )
    _inflight: ClassVar[dict[str, Future[JsonObject]]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()
    _turn_cache: ClassVar[BoundedCache[_TurnViewCache]] = BoundedCache(TURN_VIEW_CACHE_MAXSIZE)
    _turn_lock: ClassVar[threading.Lock] = threading.Lock()

    @staticmethod
    def cache_key(session_dir: Path) -> str:
        """Stable cache key for a session directory."""
        sd = Path(session_dir)
        try:
            return str(sd.expanduser().resolve())
        except OSError:
            return str(sd.expanduser())

    @classmethod
    def drop(cls, session_dir: Path) -> None:
        """Forget a cached overview so the next build rereads notes."""
        try:
            del cls._cache[cls.cache_key(session_dir)]
        except KeyError:
            pass

    @staticmethod
    def _notes_revision(payload: JsonObject) -> str:
        block = payload.get("notes")
        if not isinstance(block, dict):
            return ""
        return str(block.get("revision") or "")

    @staticmethod
    def notes_schema() -> JsonObject:
        """Operator notes schema for HUD/TUI forms (same shape as notes/list)."""
        schema = load_schema()
        return {
            "id": schema.schema_id,
            "fields": [
                {
                    "id": field.id,
                    "label": field.label or field.id,
                    "choices": list(field.choices),
                    "pick": field.pick,
                }
                for field in schema.fields
            ],
        }

    @classmethod
    def input_stamp(cls, session_dir: Path) -> _OverviewStamp:
        """Inputs that must match for a cached overview to be reused."""
        sd = Path(session_dir)
        notes_rev = ""
        try:
            notes_rev = notes_snapshot(sd).revision
        except Exception:
            logger.debug("notes stamp for overview %s", sd, exc_info=True)
        job_files, monitor_status = job_input_stamp(sd)
        try:
            stamp = require_adapter(sd).timeline_stamp(sd)
        except FileNotFoundError:
            stamp = (0.0, 0, 0, 0)
        return (
            stamp,
            notes_rev,
            job_files,
            monitor_status,
        )

    @classmethod
    def turn_view(
        cls,
        session_dir: Path,
        events: list[TraceEvent],
    ) -> tuple[list[TurnSegment], dict[int, int]]:
        """Return (segments, event_index→display_turn), stamp-cached.

        Full re-segmentation of multi‑thousand event lists is the thrash path
        for paged ``session/timeline``; reuse until the timeline stamp moves.
        """
        sd = Path(session_dir)
        key = cls.cache_key(sd)
        stamp = require_adapter(sd).timeline_stamp(sd)
        with cls._turn_lock:
            cached = cls._turn_cache.get(key)
            if cached is not None and cached[0] == stamp:
                return cached[1], cached[2]
        segs = segment_timeline_turns(events)
        turn_by_index = event_display_turn_map(segs)
        with cls._turn_lock:
            cls._turn_cache[key] = (stamp, segs, turn_by_index)
        return segs, turn_by_index

    @classmethod
    def native_payload(cls, session_dir: Path) -> JsonObject:
        """Compact native turn/stat/bookend walk for *session_dir*."""
        sd = Path(session_dir)
        bound = ref_from_path(sd)
        if bound is None:
            adapter = require_adapter(sd)
            return native_overview(adapter.id, sd, sd.name)
        return native_overview(bound.harness, bound.locator, bound.session_id)

    @classmethod
    def bookend_events(cls, native: JsonObject) -> list[TraceEvent]:
        """Job, schedule, workflow, and subagent bookends from the native walk."""
        raw = native.get("bookends")
        if not isinstance(raw, list):
            return []
        return [event_from_native(row) for row in raw if isinstance(row, dict)]

    @classmethod
    def turn_payload(cls, native: JsonObject, runs: list[SubagentRun]) -> list[JsonValue]:
        """Native turn rows with per-turn subagent runs attached."""
        raw = native.get("turns")
        if not isinstance(raw, list):
            return []
        out: list[JsonValue] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            mapped = as_json_object(row)
            ti = mapped.get("turnIndex")
            mapped["subagentRuns"] = [
                subagent_run_mapping(run) for run in runs if run.parent_turn_index == ti
            ]
            out.append(mapped)
        return out

    @classmethod
    def stat_payload(cls, native: JsonObject) -> JsonObject:
        """Event-type and tool counts from the native walk."""
        raw = native.get("stats")
        if isinstance(raw, dict):
            return as_json_object(raw)
        return {"eventTypes": [], "tools": []}

    @classmethod
    def notes_payload(cls, session_dir: Path) -> tuple[str, int, list[JsonValue]]:
        """Notes revision, count, and a capped card list."""
        try:
            snap = notes_snapshot(session_dir)
        except Exception:
            logger.debug("notes for session/overview %s", session_dir, exc_info=True)
            return "", 0, []
        rows: list[JsonValue] = []
        for note in snap.doc.sorted_notes()[:40]:
            rows.append(
                {
                    "id": note.id,
                    "turnIndex": note.turn_index,
                    "source": note.source,
                    "fields": dict(note.fields),
                    "eventIndices": list(note.event_indices),
                    "createdAt": note.created_at,
                    "updatedAt": note.updated_at,
                }
            )
        return snap.revision, len(snap.doc.notes), rows

    @classmethod
    def uncached(cls, session_dir: Path) -> JsonObject:
        """Build overview without single-flight / result cache."""
        sd = Path(session_dir)
        meta = require_adapter(sd).load_detail(sd)
        native = cls.native_payload(sd)
        meta.num_events = json_as_int(native.get("numEvents"))
        bookends = cls.bookend_events(native)
        turn_map = {
            int(ev.index): int(ev.turn_number) for ev in bookends if ev.turn_number is not None
        }
        runs = subagent_runs_for_session(sd, bookends, [], turn_map)
        notes_rev, notes_count, notes_rows = cls.notes_payload(sd)
        jobs, schedules, workflows = SessionJobs.overview_rows(sd, bookends, cls.cache_key(sd))
        summary = (meta.summary_text or "").strip()
        if len(summary) > 1200:
            summary = summary[:1197] + "…"
        turns = cls.turn_payload(native, runs)
        return {
            "sessionId": (meta.session_id or sd.name).strip(),
            "meta": session_meta_mapping(meta, path=sd),
            "summary": summary,
            "backgroundJobs": SessionJobs.json_rows(jobs),
            "schedules": SessionJobs.json_rows(schedules),
            "workflows": SessionJobs.json_rows(workflows),
            "turns": {
                "total": len(turns),
                "turns": turns,
                "subagentRuns": [subagent_run_mapping(r) for r in runs],
            },
            "timeline": {
                "total": json_as_int(native.get("numEvents")),
                "offset": 0,
                "limit": 0,
                "truncated": False,
                "events": [],
                "lazy": True,
            },
            "notes": {
                "revision": notes_rev,
                "count": notes_count,
                "notes": notes_rows,
                "schema": cls.notes_schema(),
            },
            "stats": cls.stat_payload(native),
        }

    @classmethod
    def build(cls, session_dir: Path) -> JsonObject:
        """Meta + turns + notes (timeline lazy); one in-flight per path."""
        sd = Path(session_dir)
        cache_key = cls.cache_key(sd)

        while True:
            stamp = cls.input_stamp(sd)
            cached = cls._cache.get(cache_key)
            if (
                cached is not None
                and cached[0] == stamp
                and cls._notes_revision(cached[1]) == stamp[1]
            ):
                return cached[1]

            owner = False
            with cls._lock:
                fut = cls._inflight.get(cache_key)
                if fut is None:
                    fut = Future()
                    cls._inflight[cache_key] = fut
                    owner = True

            if not owner:
                fut.result()
                continue

            try:
                out = cls.uncached(sd)
                # Stamp after build so a growth mid-flight forces a recheck.
                done_stamp = cls.input_stamp(sd)
                # Notes can land during the walk. Do not pin an empty card
                # list under the post-write revision.
                if cls._notes_revision(out) == done_stamp[1]:
                    cls._cache[cache_key] = (done_stamp, out)
                if not fut.done():
                    fut.set_result(out)
                return out
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)
                raise
            finally:
                with cls._lock:
                    if cls._inflight.get(cache_key) is fut:
                        del cls._inflight[cache_key]


def overview_input_stamp(session_dir: Path) -> _OverviewStamp:
    """Inputs that must match for a cached overview to be reused."""
    return SessionOverview.input_stamp(session_dir)


def build_session_overview(
    session_dir: Path,
) -> JsonObject:
    """Meta + turns + notes for palette clients (timeline lazy).

    Turns and stats come from the native walk. Does **not** embed event
    rows — clients call ``session/timeline`` with offset/limit (and optional
    type filter) so large sessions stay cheap.

    Concurrent callers for the same session **join one in-flight build** and
    reuse a stamp-keyed result so dual open+live-poll does not thrash multi‑MB
    host sessions.
    """
    return SessionOverview.build(session_dir)


def timeline_query_hit(event: TraceEvent, query: str) -> tuple[str, str] | None:
    """First field that contains *query*, plus a snippet that includes the needle."""
    needle = (query or "").strip().casefold()
    if not needle:
        return None
    body = event.content if isinstance(event.content, str) else str(event.content or "")
    fields = (
        ("type", event.event_type or ""),
        ("type_label", event.type_label or ""),
        ("tool", event.tool_name or ""),
        ("heading", event.summary_line or ""),
        ("preview", list_event_preview(event.summary_line, event.tool_name)[:200]),
        ("content", body[:8_000]),
    )

    def snippet(text: str, start: int) -> str:
        lo = max(0, start - 40)
        hi = min(len(text), start + max(len(needle), 1) + 40)
        chunk = text[lo:hi].replace("\n", " ").replace("\r", " ")
        if lo > 0:
            chunk = f"…{chunk}"
        if hi < len(text):
            chunk = f"{chunk}…"
        return chunk

    for field, text in fields:
        pos = text.casefold().find(needle)
        if pos >= 0:
            return field, snippet(text, pos)
    return None


class SessionTimeline:
    """Paged ``session/timeline`` payload for one session directory."""

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir)

    def build(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        event_type: str = "",
        kind: str = "",
        query: str = "",
        prompt_index: int | None = None,
        around_index: int | None = None,
        at_index: int | None = None,
        content_chars: int = DEFAULT_CONTENT_CHARS,
    ) -> JsonObject:
        """Native page when the request has no type/kind/query/turn filter."""
        if (
            (event_type or "").strip()
            or (kind or "").strip()
            or (query or "").strip()
            or prompt_index is not None
        ):
            return self.scan(
                offset=offset,
                limit=limit,
                event_type=event_type,
                kind=kind,
                query=query,
                prompt_index=prompt_index,
                around_index=around_index,
                at_index=at_index,
                content_chars=content_chars,
            )
        return self.page(
            offset=offset,
            limit=limit,
            around_index=around_index,
            at_index=at_index,
            content_chars=content_chars,
        )

    def page(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        around_index: int | None = None,
        at_index: int | None = None,
        content_chars: int = DEFAULT_CONTENT_CHARS,
    ) -> JsonObject:
        """One native slice. Does not inflate the full Python timeline."""
        from ..core import timeline_page

        t0 = time.perf_counter()
        sd = self.session_dir
        ref = ref_from_path(sd)
        if ref is None:
            raise FileNotFoundError(f"no adapter for session: {sd}")
        off = max(0, int(offset))
        lim = (
            DEFAULT_TIMELINE_LIMIT if limit is None else max(0, min(int(limit), MAX_TIMELINE_LIMIT))
        )
        if at_index is not None:
            off = max(0, int(at_index))
            lim = 1
        elif around_index is not None:
            off = max(0, int(around_index) - 8)
        events, total = timeline_page(
            ref.harness, ref.locator, ref.session_id, offset=off, limit=lim
        )
        if at_index is not None and (not events or int(events[0].index) != int(at_index)):
            events = []
            off = 0
            lim = 0
        rows: list[JsonValue] = [
            timeline_event_mapping(
                ev,
                content_chars=content_chars,
                turn_index=ev.turn_number,
                session_dir=sd,
            )
            for ev in events
        ]
        logger.debug(
            "session/timeline page harness=%s total=%s offset=%s limit=%s events=%s %.1fms",
            ref.harness,
            total,
            off,
            lim,
            len(rows),
            (time.perf_counter() - t0) * 1000,
        )
        return {
            "sessionId": sd.name,
            "total": total,
            "offset": off,
            "limit": lim,
            "events": rows,
        }

    def scan(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        event_type: str = "",
        kind: str = "",
        query: str = "",
        prompt_index: int | None = None,
        around_index: int | None = None,
        at_index: int | None = None,
        content_chars: int = DEFAULT_CONTENT_CHARS,
    ) -> JsonObject:
        """Filtered timeline: parse once, then page the matching rows."""
        t0 = time.perf_counter()
        sd = self.session_dir
        events = require_adapter(sd).parse_timeline(sd)
        _segs, turn_by_index = SessionOverview.turn_view(sd, events)
        prompt_indexes: set[int] | None = None
        if prompt_index is not None:
            prompt_indexes = TurnSegment.indexes_for_prompt(_segs, int(prompt_index))
        type_filter = (event_type or "").strip().casefold()
        query_hits: set[int] | None = None
        if query.strip():
            query_hits = set(
                matching_indexes(
                    events,
                    query,
                    key=str(sd.resolve()),
                    stamp=require_adapter(sd).timeline_stamp(sd),
                    turns=turn_by_index,
                )
            )
        filtered: list[TraceEvent] = []
        for ev in events:
            if type_filter and type_filter not in (ev.event_type or "").casefold():
                if type_filter not in (ev.type_label or "").casefold():
                    continue
            if not event_matches_timeline_kind(ev, kind):
                continue
            if query_hits is not None and int(ev.index) not in query_hits:
                continue
            if prompt_indexes is not None and int(ev.index) not in prompt_indexes:
                continue
            filtered.append(ev)
        total = len(filtered)
        off = max(0, int(offset))
        lim = (
            DEFAULT_TIMELINE_LIMIT if limit is None else max(0, min(int(limit), MAX_TIMELINE_LIMIT))
        )
        if at_index is not None:
            target = int(at_index)
            hit = next((i for i, ev in enumerate(filtered) if int(ev.index) == target), None)
            if hit is None:
                off = 0
                lim = 0
            else:
                off = hit
                lim = 1
        elif around_index is not None:
            target = int(around_index)
            hit = next((i for i, ev in enumerate(filtered) if int(ev.index) >= target), None)
            if hit is None and filtered:
                hit = len(filtered) - 1
            if hit is not None:
                off = max(0, hit - 8)
        page = filtered[off : off + lim] if lim else []
        q = (query or "").strip()
        spawn_ident: dict[str, tuple[str, str]] = {}
        for ev in events:
            if ev.event_type != "subagent_spawned":
                continue
            child = event_child_session_id(ev)
            if not child:
                continue
            bag = ev.raw_input.raw() if isinstance(ev.raw_input, ToolInputBag) else {}
            fields = spawn_fields(bag if isinstance(bag, dict) else {})
            spawn_ident[child] = (
                fields.get("subagent_type") or "",
                fields.get("description") or "",
            )
        events_out: list[JsonValue] = []
        for ev in page:
            row = timeline_event_mapping(
                ev,
                content_chars=content_chars,
                turn_index=turn_by_index.get(int(ev.index), ev.turn_number),
                session_dir=sd,
            )
            child = event_child_session_id(ev)
            ident = spawn_ident.get(child) if child else None
            if ident is not None:
                typ, desc = ident
                raw_out = row.get("rawInput")
                if isinstance(raw_out, dict):
                    if typ:
                        raw_out.setdefault("subagentType", typ)
                    if desc:
                        raw_out.setdefault("description", desc)
                if typ and not row.get("subagentType"):
                    row["subagentType"] = typ
                if desc and not row.get("description"):
                    row["description"] = desc
                if ev.event_type in et.SUBAGENT_TYPES and desc:
                    row["preview"] = desc[:200]
            if q:
                match = timeline_query_hit(ev, q)
                if match is not None:
                    field, snippet = match
                    row["matchField"] = field
                    row["matchSnippet"] = snippet
            events_out.append(row)
        logger.debug(
            "session/timeline scan total=%s filtered=%s offset=%s limit=%s events=%s %.1fms",
            len(events),
            total,
            off,
            lim,
            len(events_out),
            (time.perf_counter() - t0) * 1000,
        )
        return {
            "sessionId": sd.name,
            "total": total,
            "offset": off,
            "limit": lim,
            "events": events_out,
        }


def build_session_timeline(
    session_dir: Path,
    *,
    offset: int = 0,
    limit: int | None = None,
    event_type: str = "",
    kind: str = "",
    query: str = "",
    prompt_index: int | None = None,
    around_index: int | None = None,
    at_index: int | None = None,
    content_chars: int = DEFAULT_CONTENT_CHARS,
) -> JsonObject:
    """Paged timeline for ``session/timeline``."""
    return SessionTimeline(session_dir).build(
        offset=offset,
        limit=limit,
        event_type=event_type,
        kind=kind,
        query=query,
        prompt_index=prompt_index,
        around_index=around_index,
        at_index=at_index,
        content_chars=content_chars,
    )


def build_session_turns(session_dir: Path, *, query: str = "") -> JsonObject:
    """Turn segments for ``session/turns``."""
    sd = Path(session_dir)
    events = require_adapter(sd).parse_timeline(sd)
    segs, turn_map = SessionOverview.turn_view(sd, events)
    runs = subagent_runs_for_session(sd, events, segs, turn_map)
    needle = (query or "").strip()
    if needle:
        segs = [seg for seg in segs if _turn_segment_matches(seg, runs, needle)]
    return {
        "sessionId": sd.name,
        "total": len(segs),
        "turns": [turn_segment_mapping(s, subagent_runs=runs) for s in segs],
        "subagentRuns": [subagent_run_mapping(r) for r in runs],
    }


def _turn_segment_matches(seg: TurnSegment, runs: list[SubagentRun], query: str) -> bool:
    summary, _idx = seg.user_prompt_preview()
    kids = sum(1 for run in runs if run.parent_turn_index == seg.turn_index)
    return turn_matches_query(
        label=seg.label,
        summary=summary,
        outcome=seg.outcome or "",
        error_count=int(seg.error_event_count) + int(seg.tool_error_count),
        tool_count=int(seg.tool_call_count),
        event_count=int(seg.event_count),
        duration_seconds=int(seg.duration_seconds() or 0),
        subagent_count=kids,
        query=query,
    )


def build_session_diff(session_dir: Path) -> JsonObject:
    """Rewind snapshots or approximate edits for ``session/diff``."""
    from .workspace_diff import diff_payload, load_workspace_diff_doc

    sd = Path(session_dir)
    return diff_payload(sd.name, load_workspace_diff_doc(sd))


def warm_timeline_search(session_dir: Path) -> None:
    """Index *session_dir* so later ``session/timeline`` queries only read."""
    sd = Path(session_dir)
    events = require_adapter(sd).parse_timeline(sd)
    _segs, turns = SessionOverview.turn_view(sd, events)
    ensure_indexed(
        events,
        key=str(sd.resolve()),
        stamp=require_adapter(sd).timeline_stamp(sd),
        turns=turns,
    )


__all__ = [
    "DEFAULT_CONTENT_CHARS",
    "DEFAULT_TIMELINE_LIMIT",
    "MAX_CONTENT_CHARS",
    "MAX_TIMELINE_LIMIT",
    "build_session_diff",
    "build_session_overview",
    "build_session_timeline",
    "build_session_turns",
    "warm_timeline_search",
    "SessionOverview",
    "SessionTimeline",
    "timeline_query_hit",
    "session_meta_mapping",
    "timeline_event_mapping",
    "event_raw_json",
    "turn_segment_mapping",
]
