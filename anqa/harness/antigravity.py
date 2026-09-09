"""Antigravity disk adapter (``~/.gemini/antigravity-cli``).

One conversation is ``conversations/<uuid>.db``. The readable timeline is
``brain/<uuid>/.system_generated/logs/transcript.jsonl``.
"""

from __future__ import annotations

import json
import sqlite3
import tarfile
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

from ..models import JsonObject, SessionMeta, TraceEvent
from .ref import SessionRef

ANTIGRAVITY_HARNESS_ID = "antigravity"
_USER_REQUEST = "USER_REQUEST"
_USER_PLAN = "PLAN"


def default_store_root() -> Path:
    """Host Antigravity tree (resolved at call time)."""
    return Path.home() / ".gemini" / "antigravity-cli"


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(db).expanduser()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _looks_like_conversation_db(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".db":
        return False
    if path.name in {"conversation_summaries.db"}:
        return False
    try:
        with _connect(path) as con:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'trajectory_meta'"
            ).fetchone()
    except sqlite3.Error:
        return False
    return rows is not None


def _tag_body(text: str, tag: str) -> str:
    open_tag = f"<{tag}>"
    close_tag = f"</{tag}>"
    start = text.find(open_tag)
    if start < 0:
        return ""
    start += len(open_tag)
    end = text.find(close_tag, start)
    if end < 0:
        return text[start:].strip()
    return text[start:end].strip()


def _transcript_path(root: Path, session_id: str) -> Path:
    base = root / "brain" / session_id / ".system_generated" / "logs"
    primary = base / "transcript.jsonl"
    if primary.is_file():
        return primary
    return base / "transcript_full.jsonl"


def _summaries_db(root: Path) -> Path:
    return root / "conversation_summaries.db"


def _load_summary(root: Path, session_id: str) -> JsonObject:
    db = _summaries_db(root)
    if not db.is_file():
        return {}
    try:
        with _connect(db) as con:
            row = con.execute(
                "SELECT conversation_id, title, preview, step_count, last_modified_time, "
                "workspace_uris, status, source, project_id, agent_name, "
                "parent_conversation_id, nesting_depth, not_fully_idle, killed, "
                "last_user_input_time FROM conversation_summaries WHERE conversation_id = ?",
                (session_id,),
            ).fetchone()
    except sqlite3.Error:
        return {}
    if row is None:
        return {}
    return {str(k): row[k] for k in row.keys()}


def _child_ids(root: Path, session_id: str) -> list[str]:
    db = _summaries_db(root)
    if not db.is_file():
        return []
    try:
        with _connect(db) as con:
            rows = con.execute(
                "SELECT conversation_id FROM conversation_summaries WHERE parent_conversation_id = ?",
                (session_id,),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [str(r[0]) for r in rows if r[0]]


def _parent_id(root: Path, session_id: str) -> str:
    return str(_load_summary(root, session_id).get("parent_conversation_id") or "").strip()


def _cwd_from_uris(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        val = json.loads(text)
    except json.JSONDecodeError:
        val = [text]
    if not isinstance(val, list):
        return ""
    for item in val:
        uri = str(item or "").strip()
        if uri.startswith("file://"):
            return urlparse(uri).path or ""
        if uri.startswith("/"):
            return uri
    return ""


def _cwd_from_last_conversations(root: Path, session_id: str) -> str:
    path = root / "cache" / "last_conversations.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    for key, val in data.items():
        if str(val) == session_id:
            return str(key)
    return ""


def _conversation_db(root: Path, session_id: str) -> Path:
    return root / "conversations" / f"{session_id}.db"


def _paths_from_ref(ref: SessionRef | Path | str, root: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    text = str(ref)
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(text)
    if parsed is not None:
        return _conversation_db(root, parsed[1]), parsed[1]
    path = Path(text).expanduser()
    if path.is_file() and path.suffix == ".db":
        return path, path.stem
    return _conversation_db(root, path.name), path.name


class AntigravityAdapter:
    """Read-only Antigravity conversation adapter."""

    id: str = ANTIGRAVITY_HARNESS_ID
    product: str = "Antigravity"
    supported_version: str = "1.1.22"

    def root(self) -> Path:
        """Host store tree."""
        return default_store_root()

    def default_host_roots(self) -> list[Path]:
        path = self.root()
        return [path] if path.is_dir() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        scan = [self.root()] if roots is None else [Path(r) for r in roots]
        found: list[SessionRef] = []
        seen: set[str] = set()
        for raw in scan:
            root = Path(raw).expanduser()
            conv = root / "conversations" if (root / "conversations").is_dir() else root
            try:
                files = list(conv.glob("*.db"))
            except OSError:
                files = []
            for db in files:
                ref = self.bind_locator(db)
                if ref is None or ref.session_id in seen:
                    continue
                if _parent_id(self._store_root_for(db), ref.session_id):
                    continue
                seen.add(ref.session_id)
                found.append(ref)
        return found

    def _store_root_for(self, locator: Path) -> Path:
        path = Path(locator).expanduser()
        if path.parent.name == "conversations":
            return path.parent.parent
        return self.root()

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == ANTIGRAVITY_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == ANTIGRAVITY_HARNESS_ID
        return _looks_like_conversation_db(Path(str(ref)).expanduser())

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not _looks_like_conversation_db(path):
            return None
        sid = path.stem
        root = self._store_root_for(path)
        loc = path
        try:
            loc = path.resolve()
        except OSError:
            pass
        cwd = _cwd_from_uris(_load_summary(root, sid).get("workspace_uris"))
        if not cwd:
            cwd = _cwd_from_last_conversations(root, sid)
        return SessionRef(
            harness=ANTIGRAVITY_HARNESS_ID,
            session_id=sid,
            locator=loc,
            cwd=cwd,
        )

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        from ..core import list_meta

        db, sid = _paths_from_ref(ref, self.root())
        if not db.is_file():
            raise FileNotFoundError(f"antigravity session not found: {sid}")
        return list_meta(self.id, db, sid)

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        db, sid = _paths_from_ref(ref, self.root())
        if not sid:
            return []
        root = self._store_root_for(db) if db.is_file() else self.root()
        from ..core import timeline_events

        return timeline_events(self.id, root, sid)

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        db = _conversation_db(self.root(), sid)
        if not db.is_file():
            return None
        return self.bind_locator(db)

    def watch_hints(self) -> tuple[str, ...]:
        return ()

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        db, sid = _paths_from_ref(ref, self.root())
        if not db.is_file():
            raise FileNotFoundError(f"antigravity session not found: {sid}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        packed = False
        members: list[str] = []
        root = self._store_root_for(db)
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                name = f"{sid}/{db.name}"
                tf.add(db, arcname=name)
                members.append(name)
                transcript = _transcript_path(root, sid)
                if transcript.is_file():
                    tname = f"{sid}/transcript.jsonl"
                    tf.add(transcript, arcname=tname)
                    members.append(tname)
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
        return self.load_meta(ref)

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        db, sid = _paths_from_ref(ref, self.root())
        root = self._store_root_for(db) if db.is_file() else self.root()
        from ..core import store_stamp

        return store_stamp(self.id, root, sid)

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
        from ..session.delete import rmtree_robust

        db, sid = _paths_from_ref(ref, self.root())
        if not sid:
            raise FileNotFoundError("antigravity session id is required")
        root = self._store_root_for(db) if db.is_file() else self.root()
        if db.is_file():
            db.unlink()
        brain = root / "brain" / sid
        if brain.is_dir():
            rmtree_robust(brain)
        summaries = _summaries_db(root)
        if summaries.is_file():
            con = sqlite3.connect(str(summaries))
            try:
                con.execute(
                    "DELETE FROM conversation_summaries WHERE conversation_id = ?",
                    (sid,),
                )
                con.commit()
            finally:
                con.close()


__all__ = [
    "ANTIGRAVITY_HARNESS_ID",
    "AntigravityAdapter",
    "default_store_root",
]
