"""Workspace diff extraction from session traces.

Reads every ``rewind_points.jsonl`` record (``prompt_index``, before/after
snapshots). When that file is missing or empty, reconstructs approximate
per-path patches from write and edit tool calls on the timeline, or from
``search_replace`` rows in ``updates.jsonl``. Timeline reconstruction
is one Diff point per numbered turn.
"""

from __future__ import annotations

import difflib
import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .. import event_types as et
from ..json_lines import json_lines
from ..models import JsonObject, JsonValue, ToolInputBag, TraceEvent, as_json_object, json_as_int

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiffHunk:
    """One path in a rewind snapshot or a stacked ``search_replace`` edit."""

    path: str
    kind: str
    added: int
    removed: int
    unified: str


@dataclass(frozen=True)
class DiffPoint:
    """One ``rewind_points.jsonl`` record, or the approximate-edits bag."""

    key: str
    source: str
    prompt_index: int | None
    created_at: str | None
    files: tuple[DiffHunk, ...]
    prompt_text: str = ""
    assistant_text: str = ""

    @property
    def files_changed(self) -> int:
        return len(self.files)

    @property
    def lines_added(self) -> int:
        return sum(h.added for h in self.files)

    @property
    def lines_removed(self) -> int:
        return sum(h.removed for h in self.files)


@dataclass(frozen=True)
class WorkspaceDiff:
    """Rewind snapshots when the store wrote them; otherwise ``search_replace`` edits."""

    points: tuple[DiffPoint, ...]

    @property
    def source(self) -> str | None:
        if not self.points:
            return None
        return self.points[-1].source

    def point(self, key: str) -> DiffPoint | None:
        """Return the point with *key*, or ``None``."""
        for item in self.points:
            if item.key == key:
                return item
        return None

    def last(self) -> DiffPoint | None:
        """Latest rewind snapshot, or the approximate-edits point."""
        return self.points[-1] if self.points else None

    def meta(self) -> JsonObject:
        """Counts for the last point (same shape as :func:`load_workspace_diff`)."""
        item = self.last()
        if item is None:
            return _meta_dict(source=None, files_changed=0, lines_added=0, lines_removed=0)
        return _meta_dict(
            source=item.source,
            files_changed=item.files_changed,
            lines_added=item.lines_added,
            lines_removed=item.lines_removed,
        )


def _snap_map(block: Mapping[str, JsonValue] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (block or {}).items():
        if isinstance(v, dict):
            content = v.get("content")
            if content is None:
                continue
            path = str(v.get("path") or k).lstrip("/")
            out[path] = content if isinstance(content, str) else str(content)
        elif isinstance(v, str):
            out[str(k).lstrip("/")] = v
    return out


def _unified_diff(old: str | None, new: str | None, path: str) -> tuple[str, int, int]:
    if old is None:
        a, b = "/dev/null", f"b/{path}"
    elif new is None:
        a, b = f"a/{path}", "/dev/null"
    else:
        a, b = f"a/{path}", f"b/{path}"
    ud = list(
        difflib.unified_diff(
            (old or "").splitlines(),
            (new or "").splitlines(),
            fromfile=a,
            tofile=b,
            lineterm="",
        )
    )
    added = sum(1 for ln in ud if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in ud if ln.startswith("-") and not ln.startswith("---"))
    return ("\n".join(ud) if ud else f"(no textual diff for {path})"), added, removed


def _iter_updates(session_dir: Path) -> Iterator[JsonObject]:
    yield from json_lines(session_dir / "updates.jsonl")


def _meta_dict(
    *,
    source: str | None,
    files_changed: int,
    lines_added: int,
    lines_removed: int,
) -> JsonObject:
    return {
        "source": source,
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }


def _hunks_from_snaps(before: dict[str, str], after: dict[str, str]) -> tuple[DiffHunk, ...]:
    out: list[DiffHunk] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if old is None:
            kind = "added"
        elif new is None:
            kind = "removed"
        else:
            kind = "changed"
        text, add_n, del_n = _unified_diff(old, new, path)
        out.append(DiffHunk(path=path, kind=kind, added=add_n, removed=del_n, unified=text))
    return tuple(out)


def _prompt_index(row: Mapping[str, JsonValue]) -> int | None:
    raw = row.get("prompt_index")
    if raw is None:
        return None
    return json_as_int(raw)


def _created_at(row: Mapping[str, JsonValue]) -> str | None:
    raw = row.get("created_at")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _rewind_points(session_dir: Path) -> tuple[DiffPoint, ...]:
    rp_path = session_dir / "rewind_points.jsonl"
    out: list[DiffPoint] = []
    for i, row in enumerate(json_lines(rp_path)):
        before_raw = row.get("file_snapshots")
        after_raw = row.get("after_snapshots")
        before = _snap_map(before_raw if isinstance(before_raw, dict) else None)
        after = _snap_map(after_raw if isinstance(after_raw, dict) else None)
        if not before and not after:
            continue
        out.append(
            DiffPoint(
                key=str(i),
                source="rewind_points",
                prompt_index=_prompt_index(row),
                created_at=_created_at(row),
                files=_hunks_from_snaps(before, after),
            )
        )
    return tuple(out)


_PATH_KEYS = ("file_path", "target_file", "path", "filePath")
_OLD_KEYS = ("old_string", "oldText", "old_text", "oldString", "old")
_NEW_KEYS = ("new_string", "newText", "new_text", "newString", "new")
_CONTENT_KEYS = ("content", "contents", "file_text", "fileText")
_EDIT_TOOLS = frozenset(
    {"search_replace", "str_replace", "strreplace", "edit", "replace", "multiedit"}
)
_WRITE_TOOLS = frozenset(
    {"write", "write_file", "writefile", "create", "create_file", "createfile"}
)


def _tool_key(name: str) -> str:
    return name.strip().casefold().replace("-", "_")


def _first_str(data: Mapping[str, JsonValue], keys: tuple[str, ...]) -> str:
    for key in keys:
        raw = data.get(key)
        if isinstance(raw, str) and raw:
            return raw
    return ""


def _pairs_from_mapping(data: Mapping[str, JsonValue]) -> list[tuple[str, str]]:
    old = _first_str(data, _OLD_KEYS)
    new = _first_str(data, _NEW_KEYS)
    if old or new:
        return [(old, new)]
    return []


def _pairs_from_bag(bag: ToolInputBag) -> list[tuple[str, str]]:
    raw = bag.get("edits")
    if isinstance(raw, list):
        out: list[tuple[str, str]] = []
        for item in raw:
            if isinstance(item, dict):
                out.extend(_pairs_from_mapping(item))
        if out:
            return out
    return _pairs_from_mapping(bag.raw())


_PATCH_BEGIN = "*** Begin Patch"
_PATCH_END = "*** End Patch"
_PATCH_ENV = "*** Environment ID:"
_PATCH_MOVE = "*** Move to:"
_PATCH_EOF = "*** End of File"


def _unescape_patch_text(text: str) -> str:
    idx = text.find(_PATCH_BEGIN)
    if idx < 0 and "\\n" in text:
        expanded = text.replace("\\n", "\n")
        idx = expanded.find(_PATCH_BEGIN)
        if idx >= 0:
            text = expanded.replace("\\t", "\t")
    if idx >= 0 and "\n" not in text[idx : idx + 40] and "\\n" in text:
        return text.replace("\\n", "\n").replace("\\t", "\t")
    return text


def _finish_patch_hunk(path: str, kind: str, body: list[str]) -> DiffHunk:
    if kind == "added":
        new = "\n".join(ln[1:] if ln.startswith("+") else ln for ln in body)
        unified, added, removed = _unified_diff(None, new, path)
    elif kind == "removed":
        unified, added, removed = _unified_diff("", None, path)
    else:
        unified = "\n".join(body)
        added = sum(1 for ln in body if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in body if ln.startswith("-") and not ln.startswith("---"))
        if not unified.startswith("---"):
            unified = f"--- a/{path}\n+++ b/{path}\n{unified}"
    return DiffHunk(path=path, kind=kind, added=added, removed=removed, unified=unified)


def _hunks_from_patch_block(block: str) -> list[DiffHunk]:
    """Every Add / Update / Delete File op in one Begin Patch document."""
    out: list[DiffHunk] = []
    path = ""
    dest = ""
    kind = "edit"
    body: list[str] = []

    def flush() -> None:
        nonlocal path, dest, kind, body
        use = dest or path
        if use:
            out.append(_finish_patch_hunk(use, kind, body))
        path, dest, kind, body = "", "", "edit", []

    for line in block.splitlines():
        raw = line.strip()
        if raw.startswith(_PATCH_ENV) or raw == _PATCH_EOF:
            continue
        if raw.startswith("*** Add File:"):
            flush()
            path, kind = raw.split(":", 1)[1].strip(), "added"
            continue
        if raw.startswith("*** Update File:"):
            flush()
            path, kind = raw.split(":", 1)[1].strip(), "edit"
            continue
        if raw.startswith("*** Delete File:"):
            flush()
            path = raw.split(":", 1)[1].strip()
            kind = "removed"
            flush()
            continue
        if raw.startswith(_PATCH_MOVE):
            dest = raw.split(":", 1)[1].strip()
            continue
        if path:
            body.append(line)
    flush()
    return out


def _hunks_from_apply_patch(text: str) -> tuple[DiffHunk, ...]:
    text = _unescape_patch_text(text)
    out: list[DiffHunk] = []
    cursor = 0
    while True:
        start = text.find(_PATCH_BEGIN, cursor)
        if start < 0:
            break
        end = text.find(_PATCH_END, start)
        block = text[start + len(_PATCH_BEGIN) : end if end >= 0 else None]
        cursor = end + len(_PATCH_END) if end >= 0 else len(text)
        out.extend(_hunks_from_patch_block(block))
    return tuple(out)


def _apply_patch_hunks(ev: TraceEvent) -> tuple[DiffHunk, ...]:
    if ev.event_type != et.TOOL_CALL:
        return ()
    bag = ev.raw_input if isinstance(ev.raw_input, ToolInputBag) else ToolInputBag()
    found: list[DiffHunk] = []
    for value in bag.raw().values():
        if isinstance(value, str) and "Begin Patch" in value.replace("\\n", "\n"):
            found.extend(_hunks_from_apply_patch(value))
    return tuple(found)


def _edit_from_event(ev: TraceEvent) -> tuple[str, list[tuple[str, str]], str] | None:
    if ev.event_type != et.TOOL_CALL:
        return None
    bag = ev.raw_input if isinstance(ev.raw_input, ToolInputBag) else ToolInputBag()
    path = _first_str(bag.raw(), _PATH_KEYS)
    if not path:
        return None
    key = _tool_key(ev.tool_name)
    if key in _EDIT_TOOLS:
        pairs = _pairs_from_bag(bag)
        if pairs:
            return path, pairs, "edit"
    if key in _WRITE_TOOLS:
        content = _first_str(bag.raw(), _CONTENT_KEYS)
        if content or any(bag.has(k) for k in _CONTENT_KEYS):
            return path, [("", content)], "added"
    return None


def _hunks_from_pairs(
    grouped: Mapping[str, Sequence[tuple[str, str, str]]],
) -> tuple[DiffHunk, ...]:
    files: list[DiffHunk] = []
    for path in sorted(grouped):
        parts: list[str] = []
        add_n = 0
        del_n = 0
        kinds = {kind for _old, _new, kind in grouped[path]}
        for old_s, new_s, _kind in grouped[path]:
            text, a, r = _unified_diff(old_s if old_s else None, new_s, path)
            parts.append(text)
            add_n += a
            del_n += r
        files.append(
            DiffHunk(
                path=path,
                kind="added" if kinds == {"added"} else "edit",
                added=add_n,
                removed=del_n,
                unified="\n".join(parts),
            )
        )
    return tuple(files)


def _point_from_grouped(
    grouped: Mapping[str, Sequence[tuple[str, str, str]]],
) -> DiffPoint | None:
    if not grouped:
        return None
    return DiffPoint(
        key="edits",
        source="search_replace",
        prompt_index=None,
        created_at=None,
        files=_hunks_from_pairs(grouped),
    )


def point_from_file_diffs(rows: Sequence[Mapping[str, JsonValue]]) -> DiffPoint | None:
    """Build one edits point from product FileDiff rows (file / patch / status).

    :param rows: OpenCode ``summary.diffs`` (or the same keys).
    :returns: One edits point, or ``None`` when no path is present.
    """
    files: list[DiffHunk] = []
    for row in rows:
        path = str(row.get("file") or row.get("path") or "").strip()
        if not path:
            continue
        status = str(row.get("status") or "modified").strip().casefold()
        kind = {"added": "added", "deleted": "removed", "removed": "removed"}.get(status, "edit")
        patch = str(row.get("patch") or "")
        files.append(
            DiffHunk(
                path=path,
                kind=kind,
                added=json_as_int(row.get("additions")),
                removed=json_as_int(row.get("deletions")),
                unified=patch,
            )
        )
    if not files:
        return None
    return DiffPoint(
        key="edits",
        source="search_replace",
        prompt_index=None,
        created_at=None,
        files=tuple(sorted(files, key=lambda item: item.path)),
    )


def _files_from_events(events: Sequence[TraceEvent]) -> tuple[DiffHunk, ...]:
    grouped: dict[str, list[tuple[str, str, str]]] = {}
    extras: list[DiffHunk] = []
    for ev in events:
        parsed = _edit_from_event(ev)
        if parsed is not None:
            path, pairs, kind = parsed
            for old_s, new_s in pairs:
                grouped.setdefault(path, []).append((old_s, new_s, kind))
        extras.extend(_apply_patch_hunks(ev))
    files = list(_hunks_from_pairs(grouped))
    seen = {h.path for h in files}
    for hunk in extras:
        if hunk.path in seen:
            continue
        files.append(hunk)
        seen.add(hunk.path)
    return tuple(sorted(files, key=lambda item: item.path))


def _point_from_slice(
    events: Sequence[TraceEvent],
    *,
    key: str,
    prompt_index: int | None,
) -> DiffPoint | None:
    files = _files_from_events(events)
    if not files:
        return None
    prompt, assistant = _edits_context(events)
    return DiffPoint(
        key=key,
        source="search_replace",
        prompt_index=prompt_index,
        created_at=None,
        files=files,
        prompt_text=prompt,
        assistant_text=assistant,
    )


def points_from_events(events: Sequence[TraceEvent]) -> tuple[DiffPoint, ...]:
    """One edits point per numbered turn (write / edit / apply_patch tools).

    :param events: Timeline events (any adapter). ``turn_number`` on the
        events (or ``turn_started``) is the picker index.
    :returns: Points in turn order. Empty when no reconstructable writes exist.
    """
    buckets: dict[int, list[TraceEvent]] = {}
    order: list[int] = []
    current = 0
    numbered = False
    for ev in events:
        if ev.event_type == et.TURN_STARTED and ev.turn_number is not None:
            current = int(ev.turn_number)
            numbered = True
        elif ev.turn_number is not None:
            current = int(ev.turn_number)
            numbered = True
        buckets.setdefault(current, []).append(ev)
        if current not in order:
            order.append(current)
    out: list[DiffPoint] = []
    for turn in order:
        idx = turn if numbered else None
        key = f"edits-{turn}" if numbered else "edits"
        point = _point_from_slice(buckets[turn], key=key, prompt_index=idx)
        if point is not None:
            out.append(point)
    return tuple(out)


def point_from_events(events: Sequence[TraceEvent]) -> DiffPoint | None:
    """Approximate per-path patches from write and edit tool calls.

    :param events: Timeline events (any adapter).
    :returns: One flattened edits point, or ``None`` when no reconstructable writes exist.
    """
    return _point_from_slice(events, key="edits", prompt_index=None)


def _edits_context(events: Sequence[TraceEvent]) -> tuple[str, str]:
    from .tagged_blocks import operator_prompt_text, unwrap_for_display
    from .turns import is_operator_user_event

    prompt = ""
    assistant = ""
    for ev in events:
        if is_operator_user_event(ev):
            text = operator_prompt_text(ev.content or "")
            if text.strip():
                prompt = text
        elif ev.event_type in et.AGENT_TYPES:
            text = unwrap_for_display(ev.content or "").strip()
            if text:
                assistant = text
    return prompt, assistant


def _search_replace_raw(upd: Mapping[str, JsonValue]) -> tuple[str, str, str] | None:
    if upd.get("sessionUpdate") != "tool_call" or (upd.get("title") or "") != "search_replace":
        return None
    ri = upd.get("rawInput") or {}
    if not isinstance(ri, dict):
        return None
    path = ri.get("file_path") or ri.get("target_file") or "?"
    old_s = ri.get("old_string") or ""
    new_s = ri.get("new_string") or ""
    if not old_s and not new_s:
        return None
    return str(path), str(old_s), str(new_s)


def _search_replace_point(session_dir: Path) -> DiffPoint | None:
    grouped: dict[str, list[tuple[str, str, str]]] = {}
    for ev in _iter_updates(session_dir):
        params = ev.get("params")
        if not isinstance(params, dict):
            continue
        upd = params.get("update")
        if not isinstance(upd, dict):
            continue
        parsed = _search_replace_raw(upd)
        if parsed is None:
            continue
        path, old_s, new_s = parsed
        grouped.setdefault(path, []).append((old_s, new_s, "edit"))
    return _point_from_grouped(grouped)


def _point_markdown(point: DiffPoint) -> str:
    if point.source == "search_replace":
        if not point.files:
            return (
                "# Workspace diff\n\n"
                "_No rewind snapshots or `search_replace` edits in this session._\n"
            )
        blocks = [f"### edit `{h.path}`\n\n```diff\n{h.unified}\n```\n" for h in point.files]
        return (
            f"# Workspace diff (approximate)\n\n"
            f"_Per-edit `search_replace` patches — may not equal final files._\n\n"
            f"- **Edits:** {point.files_changed}\n"
            f"- **Lines:** +{point.lines_added} / -{point.lines_removed}\n\n---\n\n"
            + "\n".join(blocks)
        )
    if not point.files:
        return "# Workspace diff\n\n_No content differences in rewind snapshots._\n"
    blocks = [f"### `{h.path}`\n\n```diff\n{h.unified}\n```\n" for h in point.files]
    return (
        f"# Workspace diff\n\n"
        f"_From `rewind_points` snapshots (not live git)._\n\n"
        f"- **Files:** {point.files_changed}\n"
        f"- **Lines:** +{point.lines_added} / -{point.lines_removed}\n\n---\n\n" + "\n".join(blocks)
    )


def _turn_context(
    session_dir: Path, timeline: list[TraceEvent] | None = None
) -> dict[int, tuple[str, str]]:
    """prompt_index → (operator prompt, last assistant body) from the timeline."""
    from .. import event_types as et
    from ..harness.registry import require_adapter
    from .tagged_blocks import operator_prompt_text, unwrap_for_display
    from .turns import is_operator_user_event, segment_timeline_turns

    out: dict[int, tuple[str, str]] = {}
    try:
        events = (
            timeline
            if timeline is not None
            else require_adapter(session_dir).parse_timeline(session_dir)
        )
        segs = segment_timeline_turns(events)
    except (OSError, ValueError, TypeError):
        return out
    for seg in segs:
        if seg.prompt_index is None:
            continue
        prompt = ""
        assistant = ""
        for ev in seg.events:
            if is_operator_user_event(ev) and not prompt:
                prompt = operator_prompt_text(ev.content or "")
            elif ev.event_type in et.AGENT_TYPES:
                text = unwrap_for_display(ev.content or "").strip()
                if text:
                    assistant = text
        out[int(seg.prompt_index)] = (prompt, assistant)
    return out


def _with_turn_context(
    session_dir: Path,
    points: tuple[DiffPoint, ...],
    timeline: list[TraceEvent] | None = None,
) -> tuple[DiffPoint, ...]:
    ctx = _turn_context(session_dir, timeline)
    if not ctx:
        return points
    filled: list[DiffPoint] = []
    for point in points:
        if point.prompt_index is None:
            filled.append(point)
            continue
        prompt, assistant = ctx.get(int(point.prompt_index), ("", ""))
        filled.append(
            DiffPoint(
                key=point.key,
                source=point.source,
                prompt_index=point.prompt_index,
                created_at=point.created_at,
                files=point.files,
                prompt_text=prompt,
                assistant_text=assistant,
            )
        )
    return tuple(filled)


def load_workspace_diff_doc(
    session_dir: Path, timeline: list[TraceEvent] | None = None
) -> WorkspaceDiff:
    """Load every rewind snapshot, or one approximate-edits point.

    :param session_dir: Session directory with ``rewind_points.jsonl`` / ``updates.jsonl``.
    :param timeline: Already-parsed events. When omitted, the session is parsed again.
    :returns: Structured diff. Empty ``points`` when the store wrote neither source.
    """
    rewind = _rewind_points(session_dir)
    if rewind:
        return WorkspaceDiff(_with_turn_context(session_dir, rewind, timeline))
    edits = _search_replace_point(session_dir)
    if edits is not None:
        return WorkspaceDiff((edits,))
    events = timeline
    if events is None:
        try:
            from ..harness.registry import require_adapter

            events = require_adapter(session_dir).parse_timeline(session_dir)
        except (OSError, ValueError, TypeError, FileNotFoundError):
            events = []
    return WorkspaceDiff(points_from_events(events))


def load_workspace_diff(session_dir: Path) -> tuple[str, JsonObject]:
    """Return ``(markdown_body, meta)`` for the last snapshot or approximate edits.

    Meta keys: ``source`` (``rewind_points`` | ``search_replace`` | ``None``),
    ``files_changed``, ``lines_added``, ``lines_removed``.
    """
    doc = load_workspace_diff_doc(session_dir)
    last = doc.last()
    if last is None:
        return (
            "# Workspace diff\n\n_No rewind snapshots or `search_replace` edits in this session._\n",
            doc.meta(),
        )
    return _point_markdown(last), doc.meta()


def format_diff_meta_line(meta: JsonObject) -> str:
    """One-line status for titles / stats."""
    src = meta.get("source")
    if src == "rewind_points":
        return (
            f"rewind: {json_as_int(meta.get('files_changed'), 0)} files "
            f"+{json_as_int(meta.get('lines_added'), 0)}/-{json_as_int(meta.get('lines_removed'), 0)}"
        )
    if src == "search_replace":
        return (
            f"~{json_as_int(meta.get('files_changed'), 0)} search_replace edits "
            f"+{json_as_int(meta.get('lines_added'), 0)}/-{json_as_int(meta.get('lines_removed'), 0)}"
        )
    return "no diff data"


def diff_payload(session_id: str, doc: WorkspaceDiff) -> JsonObject:
    """Control ``session/diff`` body for *doc*."""
    points: list[JsonValue] = []
    for point in doc.points:
        files: list[JsonValue] = [
            as_json_object(
                {
                    "path": hunk.path,
                    "kind": hunk.kind,
                    "added": hunk.added,
                    "removed": hunk.removed,
                    "unified": hunk.unified,
                }
            )
            for hunk in point.files
        ]
        points.append(
            as_json_object(
                {
                    "key": point.key,
                    "source": point.source,
                    "promptIndex": point.prompt_index,
                    "createdAt": point.created_at,
                    "prompt": point.prompt_text,
                    "assistant": point.assistant_text,
                    "filesChanged": point.files_changed,
                    "linesAdded": point.lines_added,
                    "linesRemoved": point.lines_removed,
                    "files": files,
                }
            )
        )
    return {
        "sessionId": session_id,
        "source": doc.source,
        "points": points,
    }


def doc_from_payload(payload: JsonObject) -> WorkspaceDiff:
    """Hydrate :class:`WorkspaceDiff` from a ``session/diff`` body."""
    raw_points = payload.get("points")
    if not isinstance(raw_points, list):
        return WorkspaceDiff(())
    points: list[DiffPoint] = []
    for item in raw_points:
        if not isinstance(item, dict):
            continue
        files: list[DiffHunk] = []
        raw_files = item.get("files")
        if isinstance(raw_files, list):
            for hunk in raw_files:
                if not isinstance(hunk, dict):
                    continue
                files.append(
                    DiffHunk(
                        path=str(hunk.get("path") or ""),
                        kind=str(hunk.get("kind") or "edit"),
                        added=json_as_int(hunk.get("added"), 0),
                        removed=json_as_int(hunk.get("removed"), 0),
                        unified=str(hunk.get("unified") or ""),
                    )
                )
        pi = item.get("promptIndex")
        prompt_index = int(pi) if isinstance(pi, int) else None
        points.append(
            DiffPoint(
                key=str(item.get("key") or "edits"),
                source=str(item.get("source") or ""),
                prompt_index=prompt_index,
                created_at=str(item.get("createdAt") or "") or None,
                files=tuple(files),
                prompt_text=str(item.get("prompt") or ""),
                assistant_text=str(item.get("assistant") or ""),
            )
        )
    return WorkspaceDiff(tuple(points))
