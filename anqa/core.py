"""Native session store: typed events from anqa-core.

Every harness goes through this module. Store I/O lives in the crate.
A raw event is the original record string.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .models import JsonObject, SessionMeta, ToolInputBag, TraceEvent, as_json_object

try:
    from anqa import _core as _native
except ImportError as exc:  # pragma: no cover - extension must be built
    raise ImportError("anqa._core is required; rebuild with uv sync") from exc


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def store_ids() -> list[str]:
    """Shipped harness ids."""
    return list(_native.store_ids())


def timeline_events(harness: str, locator: Path | str, session_id: str) -> list[TraceEvent]:
    """Full typed timeline for *session_id* at *locator*.

    :param harness: Adapter id (``pi``, ``claude``, …).
    :param locator: Session file, directory, or database path.
    :param session_id: Store session id.
    :return: Linear events. ``TraceEvent.raw`` is the original record.
    """
    rows = _native.store_timeline(harness, str(locator), session_id)
    return [event_from_native(row) for row in rows]


def timeline_page(
    harness: str,
    locator: Path | str,
    session_id: str,
    *,
    offset: int = 0,
    limit: int = 200,
) -> tuple[list[TraceEvent], int]:
    """One page of the native timeline.

    :return: ``(events, total)``.
    """
    page = _native.store_timeline_page(harness, str(locator), session_id, offset, limit)
    raw_events = page.get("events") if isinstance(page, dict) else None
    rows = raw_events if isinstance(raw_events, list) else []
    events = [event_from_native(row) for row in rows if isinstance(row, Mapping)]
    return events, _as_int(page.get("total") if isinstance(page, dict) else 0)


def native_overview(harness: str, locator: Path | str, session_id: str) -> JsonObject:
    """Compact turn, stat, and job-bookend walk for ``session/overview``.

    :param harness: Adapter id (``pi``, ``claude``, …).
    :param locator: Session file, directory, or database path.
    :param session_id: Store session id.
    :return: ``numEvents``, ``turns``, ``stats``, ``subagentCount``, ``bookends``.
    """
    row = _native.store_overview(harness, str(locator), session_id)
    return as_json_object(row) if isinstance(row, Mapping) else {}


def store_stamp(harness: str, locator: Path | str, session_id: str) -> tuple[float, int, int, int]:
    """Per-session timeline stamp for *session_id* at *locator*.

    :param harness: Adapter id (``copilot``, ``antigravity``, …).
    :param locator: Session file, directory, or database path.
    :param session_id: Store session id.
    :return: ``(mtime, size, extra_mtime, extra_size)``.
    """
    row = _native.store_stamp(harness, str(locator), session_id)
    return (
        _as_float(row[0]),
        _as_int(row[1]),
        _as_int(row[2]),
        _as_int(row[3]),
    )


def _native_meta_row(fn, harness: str, locator: Path | str, session_id: str) -> object:
    try:
        return fn(harness, str(locator), session_id)
    except RuntimeError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise FileNotFoundError(msg) from exc
        raise


def _session_meta_from_row(
    row: object, harness: str, locator: Path | str, session_id: str
) -> SessionMeta:
    data = row if isinstance(row, Mapping) else {}
    meta = SessionMeta(
        session_id=str(data.get("session_id") or session_id),
        session_dir=Path(str(data.get("locator") or locator)),
        model_id=str(data.get("model_id") or "unknown"),
        title=str(data.get("title") or ""),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
        duration_seconds=_as_float(data.get("duration_seconds")),
        tool_call_count=_as_int(data.get("tool_call_count")),
        turn_outcome=str(data.get("turn_outcome") or ""),
        harness=str(data.get("harness") or harness),
        harness_version=str(data.get("harness_version") or ""),
        run_dir=str(data.get("run_dir") or ""),
        num_events=_as_int(data.get("num_events")),
        has_subagents=bool(data.get("has_subagents")),
        subagent_count=_as_int(data.get("subagent_count")),
        context_tokens_used=_as_int(data.get("context_tokens_used")) or None,
        context_window_usage_pct=_as_int(data.get("context_window_usage_pct")) or None,
        context_window_tokens=_as_int(data.get("context_window_tokens")) or None,
        turn_count=_as_int(data.get("turn_count")),
        error_count=_as_int(data.get("error_count")),
        tool_failure_count=_as_int(data.get("tool_failure_count")),
        lines_added=_as_int(data.get("lines_added")),
        lines_removed=_as_int(data.get("lines_removed")),
        compaction_count=_as_int(data.get("compaction_count")),
        doom_loop_warnings=_as_int(data.get("doom_loop_warnings")),
        task_id=str(data.get("task_id") or ""),
        reasoning_effort=str(data.get("reasoning_effort") or ""),
        num_messages=_as_int(data.get("num_messages")),
    )
    meta.has_failures = meta.tool_failure_count > 0 or meta.error_count > 0
    meta.has_diff = (meta.lines_added + meta.lines_removed) > 0
    meta.has_compaction = meta.compaction_count > 0
    meta.has_doom = meta.doom_loop_warnings > 0
    return meta


def list_meta(harness: str, locator: Path | str, session_id: str) -> SessionMeta:
    """List-grade meta from the native store."""
    row = _native_meta_row(_native.store_list_meta, harness, locator, session_id)
    return _session_meta_from_row(row, harness, locator, session_id)


def detail_meta(harness: str, locator: Path | str, session_id: str) -> SessionMeta:
    """Full-file metadata for the browser / overview."""
    row = _native_meta_row(_native.store_detail_meta, harness, locator, session_id)
    return _session_meta_from_row(row, harness, locator, session_id)


def event_from_native(row: object) -> TraceEvent:
    """Build a :class:`TraceEvent` from a native store row."""
    data_in = row if isinstance(row, Mapping) else {}
    raw = str(data_in.get("raw") or "")
    bag = ToolInputBag()
    if raw:
        try:
            val = json.loads(raw)
        except json.JSONDecodeError:
            val = None
        if isinstance(val, dict):
            bag = ToolInputBag(as_json_object(val))
    child = str(data_in.get("child_session_id") or "")
    if child:
        data = dict(bag.raw())
        data.setdefault("child_session_id", child)
        data.setdefault("subagent_id", child)
        if data_in.get("subagent_type"):
            data.setdefault("subagent_type", str(data_in.get("subagent_type") or ""))
        if data_in.get("description"):
            data.setdefault("description", str(data_in.get("description") or ""))
        bag = ToolInputBag(as_json_object(data))
    ts = data_in.get("timestamp")
    prompt = data_in.get("prompt_index")
    images: list[bytes] = []
    raw_imgs = data_in.get("images")
    if isinstance(raw_imgs, list):
        for item in raw_imgs:
            if isinstance(item, (bytes, bytearray)):
                images.append(bytes(item))
    ev = TraceEvent(
        index=_as_int(data_in.get("index")),
        event_type=str(data_in.get("event_type") or ""),
        timestamp=int(ts) if isinstance(ts, (int, float)) else None,
        content=str(data_in.get("content") or ""),
        tool_name=str(data_in.get("tool_name") or ""),
        tool_call_id=str(data_in.get("tool_call_id") or ""),
        raw_input=bag,
        is_error=bool(data_in.get("is_error")),
        update_index=_as_int(data_in.get("update_index")),
        prompt_index=int(prompt) if isinstance(prompt, (int, float)) else None,
        turn_number=_as_int(data_in.get("turn_number"))
        if data_in.get("turn_number") is not None
        else None,
        images=images,
        raw=raw,
    )
    return ev


def find_sessions(root: Path | str) -> list[Path]:
    """Session directories under *root*."""
    return [Path(p) for p in _native.find_sessions(str(root))]


def find_files(root: Path | str, *, suffix: str, name_prefix: str = "") -> list[Path]:
    """Files under *root* matching *suffix* and optional *name_prefix*."""
    return [Path(p) for p in _native.find_files(str(root), suffix, name_prefix)]


def keep_updates_line(line: bytes) -> bool:
    """Grok streaming-line keep/skip (that store's record shape)."""
    return bool(_native.keep_updates_line(line))


__all__ = [
    "event_from_native",
    "find_files",
    "find_sessions",
    "keep_updates_line",
    "list_meta",
    "native_overview",
    "store_ids",
    "store_stamp",
    "timeline_events",
    "timeline_page",
]
