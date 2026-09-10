"""Grok Build on-disk session adapter.

Public harness contract for harness id ``grok``. Implementation lives in
:mod:`anqa.harness.grok_parse` and :mod:`anqa.session.sources`; this module wraps
those APIs.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from ..fs_watch import TRACE_FILE_HINTS
from ..models import JsonObject, SessionMeta, TraceEvent, json_as_str
from ..session.sources import collect_host_session_dirs
from .grok_parse import _looks_like_session_dir, find_sessions
from .grok_paths import default_sessions_root
from .ref import SessionRef

GROK_HARNESS_ID = "grok"

# Host planes on a session directory; not part of the inspectable trace.
_ARCHIVE_SKIP_DIRS = frozenset({"workspace", "terminal"})


def _resolved(path: Path) -> Path:
    p = Path(path).expanduser()
    try:
        return p.resolve()
    except OSError:
        return p


def _is_native_store(root: Path) -> bool:
    return _resolved(root) == _resolved(default_sessions_root())


def _ref_named(root: Path, session_id: str) -> SessionRef | None:
    """Locate *session_id* under a host root without listing every session."""
    from ..scan import looks_like_session_dir
    from ..session.sources import is_encoded_cwd_name, is_host_skip_dir_name

    base = Path(root).expanduser()
    if not base.is_dir() or not session_id:
        return None
    try:
        with os.scandir(base) as it:
            tops = list(it)
    except OSError:
        return None
    for ent in tops:
        if not ent.is_dir(follow_symlinks=False) or is_host_skip_dir_name(ent.name):
            continue
        child = Path(ent.path)
        if child.name == session_id and looks_like_session_dir(child):
            return _ref_for_dir(child)
        if not is_encoded_cwd_name(ent.name):
            continue
        cand = child / session_id
        if looks_like_session_dir(cand):
            return _ref_for_dir(cand)
    return None


def _ref_for_dir(path: Path) -> SessionRef:
    loc = Path(path)
    try:
        loc = loc.resolve()
    except OSError:
        pass
    return SessionRef(
        harness=GROK_HARNESS_ID,
        session_id=loc.name,
        locator=loc,
    )


def discover(roots: Sequence[Path | str]) -> list[SessionRef]:
    """List unique Grok sessions under *roots*.

    Host ``~/.grok/sessions`` uses
    :func:`~anqa.session.sources.collect_host_session_dirs`. Every other
    root uses :func:`anqa.harness.grok_parse.find_sessions`. Duplicate resolved paths
    are dropped (first-seen wins).

    :param roots: Trees to scan.
    :returns: Session refs in first-seen order.
    """
    found: list[SessionRef] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw).expanduser()
        if _is_native_store(root):
            dirs = collect_host_session_dirs(root)
        else:
            dirs = find_sessions(root)
        for sd in dirs:
            try:
                key = str(sd.resolve())
            except OSError:
                key = str(sd)
            if key in seen:
                continue
            seen.add(key)
            found.append(_ref_for_dir(sd))
    return found


def looks_like(ref: Path | str) -> bool:
    """True when *ref* is a Grok session directory.

    Wraps :func:`anqa.harness.grok_parse._looks_like_session_dir`.

    :param ref: Session directory path.
    :returns: True when the directory has Grok session artifacts.
    """
    path = Path(ref).expanduser()
    if not path.is_dir():
        return False
    names: set[str] = set()
    try:
        with os.scandir(path) as it:
            for ent in it:
                if ent.is_file(follow_symlinks=False):
                    names.add(ent.name)
    except OSError:
        return False
    return _looks_like_session_dir(path, names)


def load_meta(ref: Path | str) -> SessionMeta:
    """Load list-grade metadata for a Grok session directory.

    :param ref: Session directory path.
    :returns: Populated :class:`~anqa.models.SessionMeta`.
    """
    from ..core import list_meta

    path = Path(ref).expanduser()
    return list_meta(GROK_HARNESS_ID, path, path.name)


def parse_timeline(ref: Path | str) -> list[TraceEvent]:
    """Parse a Grok session directory into a linear timeline.

    :param ref: Session directory path.
    :returns: Coalesced :class:`~anqa.models.TraceEvent` rows.
    """
    from ..core import timeline_events

    path = Path(ref).expanduser()
    return timeline_events(GROK_HARNESS_ID, path, path.name)


_CATALOG_HINTS = frozenset(
    {
        "updates.jsonl",
        "summary.json",
        "signals.json",
        "status.json",
        "operator_notes.toml",
    }
)


def watch_hints() -> tuple[str, ...]:
    """Catalog list-stamp files for a Grok session directory.

    :returns: Exact basenames (``updates.jsonl``, ``summary.json``, …).
    """
    return tuple(name for name in TRACE_FILE_HINTS if name in _CATALOG_HINTS)


class GrokAdapter:
    """Directory-shaped Grok Build store."""

    id: str = GROK_HARNESS_ID
    product: str = "Grok Build"
    supported_version: str = "1.0.5"

    def default_host_roots(self) -> list[Path]:
        return [default_sessions_root()]

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        return discover(self.default_host_roots() if roots is None else roots)

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == GROK_HARNESS_ID and looks_like(ref.locator)
        return looks_like(ref)

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not looks_like(path):
            return None
        return _ref_for_dir(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        from ..core import list_meta

        loc = self._session_dir(ref)
        sid = ref.session_id if isinstance(ref, SessionRef) else loc.name
        return list_meta(self.id, loc, sid)

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        from ..core import timeline_events

        loc = self._session_dir(ref)
        sid = ref.session_id if isinstance(ref, SessionRef) else loc.name
        return timeline_events(self.id, loc, sid)

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        for root in self.default_host_roots():
            hit = _ref_named(root, sid)
            if hit is not None:
                return hit
        return None

    def watch_hints(self) -> tuple[str, ...]:
        return watch_hints()

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        return write_directory_archive(self._session_dir(ref), dest)

    def open_archive(self, src: Path, dest_root: Path) -> SessionRef:
        return open_directory_archive(src, dest_root)

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        return self.load_meta(ref)

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        from ..core import store_stamp

        loc = self._session_dir(ref)
        sid = ref.session_id if isinstance(ref, SessionRef) else loc.name
        return store_stamp(self.id, loc, sid)

    def trace_mtime(self, ref: SessionRef | Path | str) -> float:
        from .grok_parse import session_trace_mtime

        return session_trace_mtime(self._session_dir(ref))

    def updates_size(self, ref: SessionRef | Path | str) -> int:
        from .grok_parse import updates_jsonl_size

        return updates_jsonl_size(self._session_dir(ref))

    def scheduler_state(self, state: JsonObject) -> JsonObject | None:
        block = state.get("grok_build.Scheduler")
        return block if isinstance(block, dict) else None

    def list_turn_outcome(self, ref: SessionRef | Path | str) -> str:
        try:
            return (self.load_meta(ref).turn_outcome or "").strip()
        except FileNotFoundError:
            return ""

    def delete_session(self, ref: SessionRef | Path | str) -> None:
        from ..session.delete import prune_empty_parents_after_session_delete, rmtree_robust

        path = self._session_dir(ref)
        if not path.is_dir():
            raise FileNotFoundError(f"grok session not found: {path}")
        parent = path.parent
        rmtree_robust(path)
        roots = self.default_host_roots()
        prune_empty_parents_after_session_delete(parent, stop_at=roots[0] if roots else None)

    def _session_dir(self, ref: SessionRef | Path | str) -> Path:
        """Directory for *ref* (``SessionRef``, folder, or ``grok:<id>``)."""
        if isinstance(ref, SessionRef):
            return Path(ref.locator)
        text = str(ref)
        from .ref import catalog_session_key, parse_session_ref_string

        parsed = parse_session_ref_string(catalog_session_key(text))
        if parsed is None:
            return Path(text).expanduser()
        found = self.ref_for_id(parsed[1])
        if found is None:
            raise FileNotFoundError(f"grok session not found: {text}")
        return Path(found.locator)

    def reported_completion_ids(self, state: JsonObject) -> set[str]:
        block = state.get("grok_build.ReportedTaskCompletions")
        if not isinstance(block, dict):
            return set()
        rows = block.get("reported")
        if not isinstance(rows, list):
            return set()
        return {json_as_str(item).strip() for item in rows if json_as_str(item).strip()}


def _add_archive_tree(tf: tarfile.TarFile, src: Path, arc_prefix: str) -> list[str]:
    names: list[str] = []
    src = Path(src)
    if not src.exists():
        return names
    if src.is_file():
        arc = arc_prefix.rstrip("/")
        tf.add(src, arcname=arc)
        names.append(arc)
        return names
    for path in sorted(src.rglob("*")):
        if path.is_symlink() or path.is_file():
            rel = path.relative_to(src).as_posix()
            arc = f"{arc_prefix.rstrip('/')}/{rel}"
            tf.add(path, arcname=arc)
            names.append(arc)
        elif path.is_dir() and not any(path.iterdir()):
            rel = path.relative_to(src).as_posix()
            arc = f"{arc_prefix.rstrip('/')}/{rel}"
            tf.add(path, arcname=arc)
            names.append(arc)
    return names


def write_directory_archive(session_dir: Path, dest: Path) -> list[str]:
    """Pack *session_dir* into *dest* as ``<session_id>/…`` (gzip tar).

    Omits ``workspace/`` and ``terminal/``.
    """
    sid = Path(session_dir).name.strip() or "session"
    session_dir = Path(session_dir).expanduser()
    try:
        session_dir = session_dir.resolve()
    except OSError:
        pass
    if not session_dir.is_dir():
        raise RuntimeError(f"session directory not found: {session_dir}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    packed = False
    try:
        names: list[str] = []
        with tarfile.open(tmp, "w:gz") as tf:
            for path in sorted(session_dir.iterdir()):
                if path.name in _ARCHIVE_SKIP_DIRS:
                    continue
                names.extend(_add_archive_tree(tf, path, f"{sid}/{path.name}"))
        if not names:
            raise RuntimeError(f"session directory has no files to export: {session_dir}")
        tmp.replace(dest)
        packed = True
    except (OSError, tarfile.TarError) as exc:
        raise RuntimeError(f"failed to pack session archive: {exc}") from exc
    finally:
        if not packed:
            tmp.unlink(missing_ok=True)
    return names


def _session_id_from_members(names: list[str]) -> str:
    tops = sorted({n.split("/", 1)[0] for n in names if n and n != "."})
    if len(tops) != 1:
        raise RuntimeError(
            f"session archive must contain one top-level session id (got {tops[:8]})"
        )
    return tops[0]


def extract_sid_tarball(src: Path, dest_root: Path) -> Path:
    """Extract a one-session-id gzip tar under *dest_root*.

    :returns: ``dest_root/<session_id>``.
    :raises RuntimeError: Missing, empty, unsafe, or not a one-id archive.
    """
    src = Path(src).expanduser()
    if not src.is_file() or src.stat().st_size <= 0:
        raise RuntimeError(f"session archive missing or empty: {src}")
    dest_root = Path(dest_root).expanduser()
    dest_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="anqa-archive-") as tmp:
        staging = Path(tmp)
        try:
            with tarfile.open(src, "r:*") as tf:
                tf.extractall(staging, filter="data")
                names = [m.name for m in tf.getmembers() if m.name]
        except (tarfile.TarError, OSError, ValueError) as exc:
            raise RuntimeError(f"invalid session archive: {src}: {exc}") from exc
        sid = _session_id_from_members(names)
        extracted = staging / sid
        if not extracted.is_dir():
            raise RuntimeError(f"session archive missing session directory: {src}")
        dest = dest_root / sid
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(extracted), str(dest))
    return dest


def open_bound_archive(
    src: Path,
    dest_root: Path,
    bind: Callable[[Path], SessionRef | None],
    *,
    harness: str,
) -> SessionRef:
    """Extract *src* and bind the first file *bind* accepts."""
    dest = extract_sid_tarball(src, dest_root)
    candidates = [dest, *sorted(p for p in dest.rglob("*") if p.is_file())]
    for candidate in candidates:
        bound = bind(candidate)
        if bound is not None:
            return bound
    raise RuntimeError(f"archive is not a {harness} session: {src}")


def open_directory_archive(src: Path, dest_root: Path) -> SessionRef:
    """Extract a Grok ``write_archive`` tarball under *dest_root*.

    :returns: Directory locator for the extracted session.
    :raises RuntimeError: Archive missing, unsafe, or not a Grok session.
    """
    dest = extract_sid_tarball(src, dest_root)
    if not looks_like(dest):
        raise RuntimeError(f"archive is not a grok session: {src}")
    return _ref_for_dir(dest)


__all__ = [
    "GROK_HARNESS_ID",
    "GrokAdapter",
    "default_sessions_root",
    "discover",
    "load_meta",
    "looks_like",
    "extract_sid_tarball",
    "open_bound_archive",
    "open_directory_archive",
    "parse_timeline",
    "watch_hints",
    "write_directory_archive",
]
