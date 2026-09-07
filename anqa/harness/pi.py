"""Pi disk adapter (``~/.pi/agent/sessions/**/*.jsonl``).

One file is one session. Header row ``type=session`` holds the id.
"""

from __future__ import annotations

import tarfile
from collections.abc import Sequence
from pathlib import Path

from ..models import JsonObject, SessionMeta, TraceEvent
from ..stamp import Stamp
from .ref import SessionRef

PI_HARNESS_ID = "pi"


def default_sessions_root() -> Path:
    """Host Pi sessions tree (resolved at call time)."""
    return Path.home() / ".pi" / "agent" / "sessions"


def _header(path: Path) -> JsonObject | None:
    from .jsonl_list import JsonlFile

    row = JsonlFile(path).first_object()
    if row is None:
        return None
    kind = str(row.get("type") or row.get("kind") or "")
    if kind in {"session", "header"}:
        return row
    return None


def _session_id_from_name(path: Path) -> str:
    stem = path.stem
    if "_" in stem:
        return stem.rsplit("_", 1)[-1]
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
        header = _header(path)
        sid = str((header or {}).get("id") or _session_id_from_name(path))
        return path, sid
    return root, path.name


def _find_file(root: Path, session_id: str) -> Path | None:
    if not root.is_dir() or not session_id:
        return None
    needle = f"_{session_id}.jsonl"
    try:
        from ..scan import find_files

        named = find_files(root, suffix=needle)
        if named:
            return named[0]
        for path in find_files(root, suffix=".jsonl"):
            if path.name.endswith(needle) or _session_id_from_name(path) == session_id:
                return path
            header = _header(path)
            if header is not None and str(header.get("id") or "") == session_id:
                return path
    except OSError:
        return None
    return None


def _collect_jsonl(roots: Sequence[Path]) -> list[Path]:
    from ..scan import find_files

    out: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        path = Path(raw).expanduser()
        files: list[Path] = []
        if path.is_file() and path.suffix == ".jsonl":
            files = [path]
        elif path.is_dir():
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
    header = _header(path)
    if header is None:
        return None
    sid = str(header.get("id") or "").strip() or _session_id_from_name(path)
    cwd = str(header.get("cwd") or "").strip()
    loc = path
    try:
        loc = path.resolve()
    except OSError:
        pass
    return SessionRef(
        harness=PI_HARNESS_ID,
        session_id=sid,
        locator=loc,
        cwd=cwd,
    )


class PiAdapter:
    """Read-only Pi jsonl adapter."""

    id: str = PI_HARNESS_ID
    product: str = "Pi"
    supported_version: str = "0.84.4"

    def root(self) -> Path:
        """Host sessions tree."""
        return default_sessions_root()

    def default_host_roots(self) -> list[Path]:
        path = self.root()
        return [path] if path.is_dir() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        scan = [self.root()] if roots is None else [Path(r) for r in roots]
        found: list[SessionRef] = []
        seen: set[str] = set()
        for file in _collect_jsonl(scan):
            ref = _ref_for_file(file)
            if ref is None or ref.session_id in seen:
                continue
            seen.add(ref.session_id)
            found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == PI_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == PI_HARNESS_ID
        path = Path(str(ref)).expanduser()
        return path.is_file() and path.suffix == ".jsonl" and _header(path) is not None

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not path.is_file() or path.suffix != ".jsonl":
            return None
        return _ref_for_file(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        from ..core import list_meta

        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"pi session not found: {sid}")
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
        return (".jsonl",)

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"pi session not found: {sid}")
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
        from ..core import detail_meta

        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"pi session not found: {sid}")
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
        from ..session.delete import unlink_file

        path, _sid = _jsonl_from_ref(ref, self.root())
        unlink_file(path, stop_at=self.root())
