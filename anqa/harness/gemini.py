"""Gemini CLI disk adapter (``~/.gemini/tmp/<project>/chats/session-*.jsonl``).

One jsonl file is one conversation. Header (``sessionId`` / optional
``type=session_metadata``) plus ``$set`` patches and ``message_update``
rows rebuild the message list. ``kind=subagent`` files stay off the
catalog list.
"""

from __future__ import annotations

import tarfile
from collections.abc import Sequence
from pathlib import Path

from ..models import JsonObject, SessionMeta, TraceEvent, as_json_object
from ..session.tagged_blocks import operator_prompt_text
from ..stamp import Stamp
from .ref import SessionRef

GEMINI_HARNESS_ID = "gemini"
_PENDING_TOOL = frozenset(
    {"pending", "executing", "awaiting_approval", "scheduled", "in_progress", "running"}
)


def default_tmp_root() -> Path:
    """Host Gemini CLI project-temp tree (resolved at call time)."""
    return Path.home() / ".gemini" / "tmp"


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                bits.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    bits.append(text)
        return "\n".join(bits)
    return ""


def _looks_like_gemini_file(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".jsonl":
        return False
    from .jsonl_list import JsonlFile

    row = JsonlFile(path).first_object()
    if row is None or not str(row.get("sessionId") or "").strip():
        return False
    if str(row.get("type") or "") == "session":
        return False
    return bool(row.get("projectHash") or row.get("kind") or row.get("startTime"))


def _session_id_from_name(path: Path) -> str:
    stem = path.stem
    if stem.startswith("session-") and "-" in stem:
        return stem.rsplit("-", 1)[-1]
    return stem


def _jsonl_from_ref(ref: SessionRef | Path | str, root: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    text = str(ref)
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(text)
    if parsed is not None:
        found = _find_file(root, parsed[1])
        if found is None:
            return root, parsed[1]
        return found, parsed[1]
    path = Path(text).expanduser()
    if path.is_file():
        from .jsonl_list import JsonlFile

        meta = JsonlFile(path).first_object() or {}
        sid = str(meta.get("sessionId") or _session_id_from_name(path)).strip()
        return path, sid
    return root, path.name


def _header_and_messages(rows: Sequence[JsonObject]) -> tuple[JsonObject, list[JsonObject]]:
    metadata: JsonObject = {}
    messages: dict[str, JsonObject] = {}
    for row in rows:
        if str(row.get("sessionId") or "").strip() and "$set" not in row:
            for key, val in row.items():
                if key != "messages":
                    metadata[str(key)] = val
        patch = row.get("$set")
        if isinstance(patch, dict):
            raw = patch.get("messages")
            if isinstance(raw, list):
                messages = {
                    str(item["id"]): as_json_object(item)
                    for item in raw
                    if isinstance(item, dict) and item.get("id")
                }
            for key, val in patch.items():
                if key != "messages":
                    metadata[str(key)] = val
            continue
        typ = str(row.get("type") or "")
        mid = str(row.get("id") or "").strip()
        if typ == "message_update" and mid and mid in messages:
            kept = str(messages[mid].get("type") or typ)
            merged = as_json_object({**messages[mid], **row})
            merged["type"] = kept
            messages[mid] = merged
            continue
        if mid and typ in {"user", "gemini", "error"}:
            messages[mid] = row
    return metadata, list(messages.values())


def _find_file(root: Path, session_id: str) -> Path | None:
    if not root.is_dir() or not session_id:
        return None
    from .jsonl_list import JsonlFile

    try:
        for path in _collect_jsonl([root]):
            header = JsonlFile(path).first_object()
            if str((header or {}).get("sessionId") or "").strip() == session_id:
                return path
    except OSError:
        return None
    return None


def _collect_jsonl(roots: Sequence[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        path = Path(raw).expanduser()
        files: list[Path] = []
        if path.is_file() and path.suffix == ".jsonl":
            files = [path]
        elif path.is_dir():
            try:
                from ..scan import find_files

                files = find_files(path, suffix=".jsonl", name_prefix="session-")
            except OSError:
                files = []
        for file in files:
            try:
                key = str(file.resolve())
            except OSError:
                key = str(file)
            if key in seen:
                continue
            seen.add(key)
            out.append(file)
    return out


def _project_root_cwd(path: Path) -> str:
    for folder in (path.parent, path.parent.parent):
        marker = folder / ".project_root"
        if marker.is_file():
            try:
                text = marker.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
            return text.splitlines()[0].strip() if text else ""
    return ""


def _ref_for_file(path: Path) -> SessionRef | None:
    if not _looks_like_gemini_file(path):
        return None
    from .jsonl_list import JsonlFile

    meta = JsonlFile(path).first_object() or {}
    sid = str(meta.get("sessionId") or "").strip()
    if not sid:
        return None
    cwd = ""
    dirs = meta.get("directories")
    if isinstance(dirs, list) and dirs:
        cwd = str(dirs[0] or "").strip()
    if not cwd:
        cwd = _project_root_cwd(path)
    loc = path
    try:
        loc = path.resolve()
    except OSError:
        pass
    return SessionRef(
        harness=GEMINI_HARNESS_ID,
        session_id=sid,
        locator=loc,
        cwd=cwd,
    )


def _is_resumable(msg: JsonObject) -> bool:
    typ = str(msg.get("type") or "")
    if typ == "user":
        return bool(operator_prompt_text(_text_of(msg.get("content"))))
    if typ != "gemini":
        return False
    if _text_of(msg.get("content")).strip():
        return True
    thoughts = msg.get("thoughts")
    tools = msg.get("toolCalls")
    return (isinstance(thoughts, list) and bool(thoughts)) or (
        isinstance(tools, list) and bool(tools)
    )


class GeminiAdapter:
    """Read-only Gemini CLI jsonl adapter."""

    id: str = GEMINI_HARNESS_ID
    product: str = "Gemini CLI"
    supported_version: str = "0.59.0"

    def root(self) -> Path:
        """Host project-temp tree."""
        return default_tmp_root()

    def default_host_roots(self) -> list[Path]:
        path = self.root()
        return [path] if path.is_dir() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        from .jsonl_list import JsonlFile

        scan = [self.root()] if roots is None else [Path(r) for r in roots]
        found: list[SessionRef] = []
        seen: set[str] = set()
        for file in _collect_jsonl(scan):
            rows = JsonlFile(file).first_objects(limit=4)
            meta, messages = _header_and_messages(rows)
            if str(meta.get("kind") or "main") == "subagent":
                continue
            if not any(_is_resumable(msg) for msg in messages):
                continue
            ref = _ref_for_file(file)
            if ref is None or ref.session_id in seen:
                continue
            seen.add(ref.session_id)
            found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == GEMINI_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == GEMINI_HARNESS_ID
        return _looks_like_gemini_file(Path(str(ref)).expanduser())

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not path.is_file() or path.suffix != ".jsonl":
            return None
        return _ref_for_file(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        from ..core import list_meta

        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"gemini session not found: {sid}")
        return list_meta(self.id, path, sid)

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            return []
        from ..core import timeline_events

        return timeline_events(self.id, path, sid)

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        found = _find_file(self.root(), sid)
        if found is None:
            return None
        return _ref_for_file(found)

    def watch_hints(self) -> tuple[str, ...]:
        return ()

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"gemini session not found: {sid}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        packed = False
        name = f"{sid}/{path.name}"
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                tf.add(path, arcname=name)
            tmp.replace(dest)
            packed = True
        except (OSError, tarfile.TarError) as exc:
            raise RuntimeError(f"failed to pack session archive: {exc}") from exc
        finally:
            if not packed:
                tmp.unlink(missing_ok=True)
        return [name]

    def open_archive(self, src: Path, dest_root: Path) -> SessionRef:
        from .grok import open_bound_archive

        return open_bound_archive(src, dest_root, self.bind_locator, harness=self.id)

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        return self.load_meta(ref)

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        path, _sid = _jsonl_from_ref(ref, self.root())
        return Stamp.file(path)

    def trace_mtime(self, ref: SessionRef | Path | str) -> float:
        return self.timeline_stamp(ref)[0]

    def updates_size(self, ref: SessionRef | Path | str) -> int:
        return int(self.timeline_stamp(ref)[1])

    def scheduler_state(self, state: JsonObject) -> JsonObject | None:
        return None

    def reported_completion_ids(self, state: JsonObject) -> set[str]:
        return set()

    def list_turn_outcome(self, ref: SessionRef | Path | str) -> str:
        try:
            return (self.load_meta(ref).turn_outcome or "").strip()
        except FileNotFoundError:
            return ""

    def delete_session(self, ref: SessionRef | Path | str) -> None:
        from ..session.delete import unlink_file

        path, _sid = _jsonl_from_ref(ref, self.root())
        unlink_file(path, stop_at=self.root())


__all__ = [
    "GEMINI_HARNESS_ID",
    "GeminiAdapter",
    "default_tmp_root",
]
