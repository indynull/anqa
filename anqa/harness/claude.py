"""Claude Code disk adapter (``~/.claude/projects/**/<uuid>.jsonl``).

One file is one parent session. Children live under
``<uuid>/subagents/*.jsonl`` and stay off the catalog list.
"""

from __future__ import annotations

import tarfile
from collections.abc import Sequence
from pathlib import Path

from ..models import JsonObject, SessionMeta, TraceEvent, as_json_object
from ..stamp import Stamp
from .ref import SessionRef

CLAUDE_HARNESS_ID = "claude"
_CHROME_TYPES = frozenset(
    {
        "queue-operation",
        "last-prompt",
        "atis-latch",
        "ai-title",
        "attachment",
        "file-history-snapshot",
        "progress",
        "system",
        "mode",
        "cost-state",
        "permission-mode",
    }
)
_AGENT_TOOLS = frozenset({"Agent", "Task"})


def default_projects_root() -> Path:
    """Host Claude projects tree (resolved at call time)."""
    return Path.home() / ".claude" / "projects"


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                bits.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("thinking") or item.get("content")
                if isinstance(text, str) and text:
                    bits.append(text)
                elif isinstance(text, list):
                    nested = _text_of(text)
                    if nested:
                        bits.append(nested)
        return "\n".join(bits)
    return ""


def _is_child_file(path: Path) -> bool:
    return path.parent.name == "subagents"


def _session_id_from_name(path: Path) -> str:
    stem = path.stem
    if stem.startswith("agent-"):
        return stem.removeprefix("agent-")
    return stem


def _row_session_id(row: JsonObject, path: Path) -> str:
    sid = str(row.get("sessionId") or "").strip()
    agent = str(row.get("agentId") or "").strip()
    if _is_child_file(path) and agent:
        return agent
    return sid or _session_id_from_name(path)


def _first_row(path: Path) -> JsonObject | None:
    from .jsonl_list import JsonlFile

    return JsonlFile(path).first_object()


def _looks_like_claude_file(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".jsonl":
        return False
    row = _first_row(path)
    if row is None:
        return False
    if not str(row.get("sessionId") or "").strip():
        return False
    typ = str(row.get("type") or "")
    if typ == "session":
        return False
    return bool(typ) or isinstance(row.get("message"), dict)


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
        row = _first_row(path)
        sid = _row_session_id(row or {}, path)
        return path, sid
    return root, path.name


def _find_file(root: Path, session_id: str) -> Path | None:
    if not root.is_dir() or not session_id:
        return None
    needle = f"{session_id}.jsonl"
    child_name = f"agent-{session_id}.jsonl"
    try:
        from ..scan import find_files

        for path in find_files(root, suffix=".jsonl"):
            if path.name == needle or path.name == child_name:
                return path
        for pattern in (f"*/subagents/{child_name}", f"*/*/subagents/{child_name}"):
            for path in root.glob(pattern):
                if path.is_file():
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
            from ..scan import find_files

            files = find_files(path, suffix=".jsonl")
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


def _ref_for_file(path: Path) -> SessionRef | None:
    if not _looks_like_claude_file(path):
        return None
    from .jsonl_list import JsonlFile

    rows = JsonlFile(path).first_objects()
    row = rows[0] if rows else {}
    cwd = ""
    sid = _row_session_id(row, path)
    for item in rows:
        if not cwd:
            cwd = str(item.get("cwd") or "").strip()
        if not sid:
            sid = _row_session_id(item, path)
        if cwd and sid:
            break
    if not sid:
        return None
    loc = path
    try:
        loc = path.resolve()
    except OSError:
        pass
    return SessionRef(
        harness=CLAUDE_HARNESS_ID,
        session_id=sid,
        locator=loc,
        cwd=cwd,
    )


def _content_blocks(msg: JsonObject) -> list[JsonObject]:
    content = msg.get("content")
    if isinstance(content, list):
        return [as_json_object(b) for b in content if isinstance(b, dict)]
    return []


def _is_tool_result_user(msg: JsonObject) -> bool:
    blocks = _content_blocks(msg)
    if not blocks:
        return False
    return all(str(b.get("type") or "") == "tool_result" for b in blocks)


def _child_dir(path: Path, session_id: str) -> Path:
    return path.parent / session_id / "subagents"


class ClaudeAdapter:
    """Read-only Claude Code jsonl adapter."""

    id: str = CLAUDE_HARNESS_ID
    product: str = "Claude Code"
    supported_version: str = "2.1.251"

    def root(self) -> Path:
        """Host projects tree."""
        return default_projects_root()

    def default_host_roots(self) -> list[Path]:
        path = self.root()
        return [path] if path.is_dir() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        scan = [self.root()] if roots is None else [Path(r) for r in roots]
        found: list[SessionRef] = []
        seen: set[str] = set()
        for file in _collect_jsonl(scan):
            if _is_child_file(file):
                continue
            ref = _ref_for_file(file)
            if ref is None or ref.session_id in seen:
                continue
            seen.add(ref.session_id)
            found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == CLAUDE_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == CLAUDE_HARNESS_ID
        path = Path(str(ref)).expanduser()
        return _looks_like_claude_file(path)

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not path.is_file() or path.suffix != ".jsonl":
            return None
        return _ref_for_file(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        from ..core import list_meta

        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"claude session not found: {sid}")
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
            raise FileNotFoundError(f"claude session not found: {sid}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        packed = False
        members: list[str] = []
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                name = f"{sid}/{path.name}"
                tf.add(path, arcname=name)
                members.append(name)
                folder = _child_dir(path, sid)
                if folder.is_dir():
                    for child in sorted(folder.glob("*.jsonl")):
                        child_name = f"{sid}/subagents/{child.name}"
                        tf.add(child, arcname=child_name)
                        members.append(child_name)
            tmp.replace(dest)
            packed = True
        except (OSError, tarfile.TarError) as exc:
            raise RuntimeError(f"failed to pack session archive: {exc}") from exc
        finally:
            if not packed:
                tmp.unlink(missing_ok=True)
        return members

    def open_archive(self, src: Path, dest_root: Path) -> SessionRef:
        from .grok import open_bound_archive

        return open_bound_archive(src, dest_root, self.bind_locator, harness=self.id)

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        from ..core import detail_meta

        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"claude session not found: {sid}")
        return detail_meta(self.id, path, sid)

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
        from ..session.delete import rmtree_robust, unlink_file

        path, sid = _jsonl_from_ref(ref, self.root())
        folder = path.parent / sid
        if folder.is_dir():
            rmtree_robust(folder)
        unlink_file(path, stop_at=self.root())


__all__ = [
    "CLAUDE_HARNESS_ID",
    "ClaudeAdapter",
    "default_projects_root",
]
