"""Control-plane views for sessions whose locator is not a directory."""

from __future__ import annotations

from pathlib import Path

from ..models import JsonObject, JsonValue, SessionMeta, TraceEvent
from ..notes import notes_snapshot
from ..session.catalog import catalog_row_for_ref
from ..session.control_views import (
    DEFAULT_CONTENT_CHARS,
    DEFAULT_TIMELINE_LIMIT,
    MAX_CONTENT_CHARS,
    MAX_TIMELINE_LIMIT,
    SessionOverview,
    overview_stat_counts,
    session_meta_mapping,
    timeline_event_mapping,
    turn_segment_mapping,
)
from ..session.event_search import matching_indexes
from ..session.jobs import SessionJobs
from ..session.query import turn_matches_query
from ..session.subagents import subagent_run_mapping, subagent_runs_for_session
from ..session.turns import (
    event_display_turn_map,
    event_matches_timeline_kind,
    segment_timeline_turns,
)
from .ref import SessionRef
from .registry import adapter_for


def _notes_dir(ref: SessionRef, *, create: bool = False) -> Path:
    path = ref.overlay_dir()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def catalog_row_from_ref(ref: SessionRef) -> JsonObject | None:
    """Wire catalog row for an adapter session ref (no notes mkdir)."""
    return catalog_row_for_ref(ref)


def _load(ref: SessionRef) -> tuple[SessionMeta, list[TraceEvent]]:
    impl = adapter_for(ref)
    if impl is None:
        raise FileNotFoundError(f"unknown harness: {ref.harness}")
    return impl.load_detail(ref), impl.parse_timeline(ref)


def _child_catalog_path(ref: SessionRef, child_id: str) -> str:
    """``harness:id`` when *child_id* is a loadable session in this store."""
    impl = adapter_for(ref)
    if impl is None or not child_id:
        return ""
    if impl.ref_for_id(child_id) is not None:
        return f"{ref.harness}:{child_id}"
    loc = Path(ref.locator)
    if not loc.is_file():
        return ""
    parent = loc.parent
    for cand in (
        parent / f"{child_id}.jsonl",
        parent / f"agent-{child_id}.jsonl",
        parent / loc.stem / "subagents" / f"agent-{child_id}.jsonl",
        parent / loc.stem / "subagents" / f"{child_id}.jsonl",
    ):
        if impl.bind_locator(cand) is not None:
            return f"{ref.harness}:{child_id}"
    return ""


def _subagent_rows(ref: SessionRef, events: list[TraceEvent]) -> list[JsonValue]:
    segs = segment_timeline_turns(events)
    turn_map = event_display_turn_map(segs)
    runs = subagent_runs_for_session(ref.locator, events, segs, turn_map)
    out: list[JsonValue] = []
    for run in runs:
        row = subagent_run_mapping(run)
        child = (run.child_session_id or "").strip()
        if child and not str(row.get("childPath") or "").strip():
            path = _child_catalog_path(ref, child)
            if path:
                row["childPath"] = path
                row["openable"] = True
        out.append(row)
    return out


def session_overview(ref: SessionRef) -> JsonObject:
    """``session/overview`` for a file or database locator."""
    meta, events = _load(ref)
    meta.num_events = len(events)
    segs = segment_timeline_turns(events)
    sub_rows = _subagent_rows(ref, events)
    notes_rev = ""
    notes_count = 0
    notes_rows: list[JsonValue] = []
    try:
        snap = notes_snapshot(_notes_dir(ref, create=True))
        notes_rev = snap.revision
        notes_count = len(snap.doc.notes)
        for note in snap.doc.sorted_notes()[:40]:
            notes_rows.append(
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
    except OSError:
        pass
    mapped = session_meta_mapping(meta, path=None)
    mapped["path"] = ref.ref_string()
    mapped["harness"] = ref.harness
    disk = ref.locator if ref.locator.is_dir() else ref.overlay_dir()
    jobs, schedules, workflows = SessionJobs.overview_rows(disk, events, ref.ref_string())
    return {
        "sessionId": ref.session_id,
        "meta": mapped,
        "summary": (meta.summary_text or "").strip(),
        "backgroundJobs": SessionJobs.json_rows(jobs),
        "schedules": SessionJobs.json_rows(schedules),
        "workflows": SessionJobs.json_rows(workflows),
        "turns": {
            "total": len(segs),
            "turns": [
                turn_segment_mapping(
                    s,
                    include_event_indexes=False,
                    assistant_max_chars=400,
                    subagent_runs=[],
                )
                for s in segs
            ],
            "subagentRuns": sub_rows,
        },
        "timeline": {
            "total": len(events),
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
            "schema": SessionOverview.notes_schema(),
        },
        "stats": overview_stat_counts(events),
    }


def session_timeline(
    ref: SessionRef,
    *,
    offset: int = 0,
    limit: int | None = None,
    event_type: str = "",
    kind: str = "",
    query: str = "",
    prompt_index: int | None = None,
    around_index: int | None = None,
    at_index: int | None = None,
    content_chars: int | None = None,
) -> JsonObject:
    """Paged ``session/timeline`` from the adapter parse."""
    _meta, events = _load(ref)
    segs = segment_timeline_turns(events)
    turn_by_index = event_display_turn_map(segs)
    type_filter = (event_type or "").strip().casefold()
    query_hits: set[int] | None = None
    if query.strip():
        query_hits = set(
            matching_indexes(
                events,
                query,
                key=ref.ref_string(),
                stamp=(0.0, 0, 0, 0),
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
        filtered.append(ev)
    total = len(filtered)
    off = max(0, int(offset))
    lim = DEFAULT_TIMELINE_LIMIT if limit is None else max(0, min(int(limit), MAX_TIMELINE_LIMIT))
    if at_index is not None:
        target = int(at_index)
        hit = next((i for i, ev in enumerate(filtered) if int(ev.index) == target), None)
        off, lim = (0, 0) if hit is None else (hit, 1)
    elif around_index is not None:
        target = int(around_index)
        hit = next((i for i, ev in enumerate(filtered) if int(ev.index) >= target), None)
        if hit is None and filtered:
            hit = len(filtered) - 1
        if hit is not None:
            off = max(0, hit - 8)
    cc = (
        DEFAULT_CONTENT_CHARS
        if content_chars is None
        else max(0, min(int(content_chars), MAX_CONTENT_CHARS))
    )
    page = filtered[off : off + lim] if lim else []
    return {
        "sessionId": ref.session_id,
        "total": total,
        "offset": off,
        "limit": lim,
        "events": [
            timeline_event_mapping(
                ev,
                content_chars=cc,
                turn_index=turn_by_index.get(int(ev.index)),
                session_dir=Path(ref.locator),
            )
            for ev in page
        ],
    }


def session_turns(ref: SessionRef, *, query: str = "") -> JsonObject:
    """``session/turns`` from the adapted timeline."""
    _meta, events = _load(ref)
    segs = segment_timeline_turns(events)
    needle = (query or "").strip()
    if needle:
        segs = [
            seg
            for seg in segs
            if turn_matches_query(
                label=seg.label,
                summary=seg.user_prompt_preview()[0],
                outcome=seg.outcome or "",
                error_count=int(seg.error_event_count) + int(seg.tool_error_count),
                tool_count=int(seg.tool_call_count),
                event_count=int(seg.event_count),
                duration_seconds=int(seg.duration_seconds() or 0),
                subagent_count=0,
                query=needle,
            )
        ]
    return {
        "sessionId": ref.session_id,
        "total": len(segs),
        "turns": [turn_segment_mapping(s, subagent_runs=[]) for s in segs],
        "subagentRuns": _subagent_rows(ref, events),
    }


def session_diff(ref: SessionRef) -> JsonObject:
    """Approximate write/edit patches from the session timeline."""
    from ..session.workspace_diff import (
        DiffPoint,
        WorkspaceDiff,
        diff_payload,
        point_from_file_diffs,
        points_from_events,
    )

    impl = adapter_for(ref)
    events: list[TraceEvent] = []
    if impl is not None:
        events = impl.parse_timeline(ref)
    points: tuple[DiffPoint, ...] = ()
    if impl is not None and impl.id == "opencode":
        from .opencode import file_diffs_for

        native = file_diffs_for(ref)
        if native:
            one = point_from_file_diffs(native)
            points = (one,) if one is not None else ()
    if not points:
        points = points_from_events(events)
    return diff_payload(ref.session_id, WorkspaceDiff(points))


__all__ = [
    "catalog_row_from_ref",
    "session_diff",
    "session_overview",
    "session_timeline",
    "session_turns",
]
