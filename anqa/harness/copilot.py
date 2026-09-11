"""GitHub Copilot CLI disk adapter (``~/.copilot/session-store.db``).

Sessions are sqlite rows. Timeline is ``session-state/<id>/events.jsonl``.
"""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
from collections.abc import Sequence
from pathlib import Path

from ..models import JsonObject, SessionMeta, TraceEvent
from .ref import SessionRef

COPILOT_HARNESS_ID = "copilot"


def default_store_root() -> Path:
    """Host Copilot config tree (resolved at call time)."""
    return Path.home() / ".copilot"


def default_db_path() -> Path:
    """Host Copilot catalog database."""
    return default_store_root() / "session-store.db"


def _connect(db: Path) -> sqlite3.Connection:
    path = Path(db).expanduser()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _assert_readable(db: Path) -> Path:
    path = Path(db).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"copilot database not found: {path}")
    return path


def _db_from_ref(ref: SessionRef | Path | str, fallback: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    text = str(ref)
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(text)
    if parsed is not None:
        return fallback, parsed[1]
    path = Path(text).expanduser()
    if path.is_file():
        return path, ""
    return fallback, path.name


def _state_dir(db: Path, session_id: str) -> Path:
    return Path(db).expanduser().resolve().parent / "session-state" / session_id


def _events_path(db: Path, session_id: str) -> Path:
    return _state_dir(db, session_id) / "events.jsonl"


def _session_row(con: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT id, cwd, repository, host_type, branch, summary, created_at, updated_at "
        "FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()


def _list_session_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        con.execute(
            "SELECT id, cwd, repository, host_type, branch, summary, created_at, updated_at "
            "FROM sessions ORDER BY updated_at DESC"
        )
    )


class CopilotAdapter:
    """``~/.copilot`` sqlite catalog plus ``session-state`` event logs."""

    id = COPILOT_HARNESS_ID
    product = "GitHub Copilot"
    supported_version = "1.0.83"

    def db(self) -> Path:
        return default_db_path()

    def default_host_roots(self) -> list[Path]:
        path = self.db()
        return [path] if path.is_file() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        found: list[SessionRef] = []
        seen: set[str] = set()
        for db in self._dbs_in(roots):
            for ref in self._discover_db(db):
                if ref.session_id in seen:
                    continue
                seen.add(ref.session_id)
                found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == COPILOT_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == COPILOT_HARNESS_ID
        path = Path(str(ref)).expanduser()
        return path.is_file() and path.name == "session-store.db"

    def bind_locator(self, locator: Path) -> SessionRef | None:
        """A database file is the store, not one session."""
        _ = Path(locator)
        return None

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        from ..core import list_meta

        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            raise FileNotFoundError("copilot session id is required")
        db = _assert_readable(db)
        return list_meta(self.id, db, sid)

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            return []
        db = _assert_readable(db)
        from ..core import timeline_events

        return timeline_events(self.id, db, sid)

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        db = self.db()
        if not db.is_file():
            return None
        try:
            with _connect(db) as con:
                row = _session_row(con, sid)
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return SessionRef(
            harness=COPILOT_HARNESS_ID,
            session_id=sid,
            locator=db,
            cwd=str(row["cwd"] or "").strip(),
        )

    def watch_hints(self) -> tuple[str, ...]:
        return ("session-store.db", "session-store.db-wal", "events.jsonl")

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            raise RuntimeError("copilot session id is required")
        db = _assert_readable(db)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        members: list[str] = []
        packed = False
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                state = _state_dir(db, sid)
                for name in ("events.jsonl", "workspace.yaml", "session.db"):
                    src = state / name
                    if not src.is_file():
                        continue
                    arc = f"{sid}/{name}"
                    tf.add(src, arcname=arc)
                    members.append(arc)
            if not members:
                raise RuntimeError(f"copilot session has no archive files: {sid}")
            tmp.replace(dest)
            packed = True
        except (OSError, tarfile.TarError) as exc:
            raise RuntimeError(f"failed to pack session archive: {exc}") from exc
        finally:
            if not packed:
                tmp.unlink(missing_ok=True)
        return members

    def open_archive(self, src: Path, dest_root: Path) -> SessionRef:
        from .grok import extract_sid_tarball

        dest = extract_sid_tarball(src, dest_root)
        db = dest / "session.db"
        events = dest / "events.jsonl"
        workspace = dest / "workspace.yaml"
        if not db.is_file() and not workspace.is_file():
            raise RuntimeError(f"archive is not a copilot session: {src}")
        sid = dest.name
        store = dest_root / "copilot.db"
        if db.is_file():
            shutil.copy2(db, store)
        else:
            store.touch()
        state = _state_dir(store, sid)
        state.mkdir(parents=True, exist_ok=True)
        if events.is_file():
            shutil.copy2(events, state / "events.jsonl")
        workspace = dest / "workspace.yaml"
        if workspace.is_file():
            shutil.copy2(workspace, state / "workspace.yaml")
        return SessionRef(harness=COPILOT_HARNESS_ID, session_id=sid, locator=store)

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        return self.load_meta(ref)

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        db, sid = _db_from_ref(ref, self.db())
        from ..core import store_stamp

        return store_stamp(self.id, db, sid)

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

        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            raise FileNotFoundError("copilot session id is required")
        db = _assert_readable(db)
        con = sqlite3.connect(str(db))
        try:
            for table in (
                "turns",
                "checkpoints",
                "session_files",
                "session_refs",
                "forge_trajectory_events",
                "assistant_usage_events",
            ):
                try:
                    con.execute(f"DELETE FROM {table} WHERE session_id = ?", (sid,))
                except sqlite3.OperationalError:
                    continue
            con.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            con.commit()
        finally:
            con.close()
        state = _state_dir(db, sid)
        if state.is_dir():
            rmtree_robust(state)

    def _dbs_in(self, roots: Sequence[Path | str] | None) -> list[Path]:
        if roots is None:
            path = self.db()
            return [path] if path.is_file() else []
        out: list[Path] = []
        for raw in roots:
            path = Path(raw).expanduser()
            if path.is_file() and path.name == "session-store.db":
                out.append(path)
            elif path.is_dir():
                cand = path / "session-store.db"
                if cand.is_file():
                    out.append(cand)
        return out

    def _discover_db(self, db: Path) -> list[SessionRef]:
        try:
            with _connect(db) as con:
                rows = _list_session_rows(con)
        except sqlite3.Error:
            return []
        found: list[SessionRef] = []
        for row in rows:
            found.append(
                SessionRef(
                    harness=COPILOT_HARNESS_ID,
                    session_id=str(row["id"]),
                    locator=db,
                    cwd=str(row["cwd"] or "").strip(),
                )
            )
        return found


__all__ = [
    "COPILOT_HARNESS_ID",
    "CopilotAdapter",
    "default_db_path",
    "default_store_root",
]
