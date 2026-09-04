"""Session catalog roots: adapter host stores.

Native stores (each adapter's ``default_host_roots`` and ``[catalog.roots]``)
are the catalog.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import unquote

from .subagents import drop_subagent_sessions

_HOST_SKIP_DIR_NAMES = frozenset(
    {
        "anqa-plugins",
        "anqa-skills",
        "subagents",
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "target",
        "dist",
        "build",
        ".cache",
        ".tox",
        ".anqa-resume-seed",
        ".anqa-workspace-seed",
        "workspace",
        "runs",
    }
)


class SessionOrigin(StrEnum):
    """Where a catalog row came from."""

    HOST = "host"
    IMPORT = "import"
    WORK = "work"


ORIGIN_WORK = SessionOrigin.WORK
ORIGIN_HOST = SessionOrigin.HOST
ORIGIN_IMPORT = SessionOrigin.IMPORT


def _resolved(path: Path) -> Path:
    p = Path(path).expanduser()
    try:
        return p.resolve()
    except OSError:
        return p


def default_catalog_root() -> Path:
    """First enabled adapter store (the default catalog path)."""
    roots = _adapter_store_roots()
    if roots:
        return Path(roots[0]).expanduser()
    from ..paths import default_host_sessions_root

    return default_host_sessions_root()


def _adapter_store_roots() -> list[Path]:
    from ..harness.registry import adapter_host_roots, enabled_host_adapters

    out: list[Path] = []
    for item in enabled_host_adapters():
        out.extend(adapter_host_roots(item))
    return out


def is_adapter_store_root(path: Path) -> bool:
    """True when *path* is an enabled adapter store root."""
    target = _resolved(path)
    return any(_resolved(root) == target for root in _adapter_store_roots())


def is_under_adapter_store(session_dir: Path) -> bool:
    """True when *session_dir* lives under an enabled adapter store."""
    p = _resolved(session_dir)
    for root in _adapter_store_roots():
        host = _resolved(root)
        if p == host or host in p.parents:
            return True
    return False


def classify_session_origin(
    session_dir: Path,
    *,
    host_root: Path | None = None,
) -> SessionOrigin:
    """Return import when under the import store, else host."""
    from ..paths import is_import_locator

    if is_import_locator(session_dir):
        return ORIGIN_IMPORT
    if host_root is not None:
        sd = _resolved(session_dir)
        host = _resolved(host_root)
        if sd == host or host in sd.parents:
            return ORIGIN_HOST
    if is_under_adapter_store(session_dir):
        return ORIGIN_HOST
    return ORIGIN_HOST


@dataclass(frozen=True)
class SessionScanRoot:
    """One directory to scan for operator-facing sessions."""

    path: Path
    origin: SessionOrigin = ORIGIN_HOST


def session_scan_roots(
    *,
    traces_path: Path | None = None,
    include_host: bool = True,
    host_root: Path | None = None,
) -> list[SessionScanRoot]:
    """Roots for the sessions home list.

    Enabled adapter stores. An explicit *traces_path* that is not already
    one of those stores is added as another catalog root.
    """
    out: list[SessionScanRoot] = []
    seen: set[str] = set()

    def add(path: Path, origin: SessionOrigin = ORIGIN_HOST) -> None:
        key = str(_resolved(path))
        if key in seen:
            return
        seen.add(key)
        out.append(SessionScanRoot(path=Path(path).expanduser(), origin=origin))

    if include_host:
        if host_root is not None:
            add(Path(host_root).expanduser())
        else:
            for root in _adapter_store_roots():
                add(root)
    if traces_path is not None:
        add(Path(traces_path).expanduser())
    from ..harness.ref import HARNESS_IDS
    from ..paths import imports_dir

    base = imports_dir(create=False)
    for hid in sorted(HARNESS_IDS):
        root = base / hid
        if root.is_dir():
            add(root, ORIGIN_IMPORT)
    return out


def session_dir_for_watch_path(path: Path, root: Path) -> Path | None:
    """Nearest session directory on *path* that lives under *root*.

    Host trees nest sessions under a percent-encoded cwd bucket. The first
    component under the watch root is that bucket, not the session.
    """
    try:
        cur = Path(path).expanduser().resolve()
        root_r = Path(root).expanduser().resolve()
    except OSError:
        cur = Path(path).expanduser()
        root_r = Path(root).expanduser()
    try:
        cur.relative_to(root_r)
    except ValueError:
        return None
    if cur.is_file() or not cur.exists():
        cur = cur.parent
    while True:
        try:
            if cur == root_r:
                return None
            cur.relative_to(root_r)
        except ValueError:
            return None
        if _dir_is_session(cur):
            return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def is_encoded_cwd_name(name: str) -> bool:
    """True for URL-encoded absolute paths used as host session buckets."""
    n = (name or "").casefold()
    return n.startswith("%2f") or "%2f" in n


def session_run_dir(session_dir: Path) -> str:
    """Host directory the session was run in.

    Host trees nest under a percent-encoded cwd bucket
    (``<store>/%2Fhome%2F…/<id>``). Container ``/workspace`` is skipped.
    """
    parent = Path(session_dir).parent.name
    if is_encoded_cwd_name(parent):
        decoded = unquote(parent)
        if decoded and decoded not in {"/workspace", "workspace"}:
            return decoded
    return ""


def is_host_skip_dir_name(name: str) -> bool:
    """Host-tree names that must not be descended (workspace / staging junk)."""
    low = (name or "").casefold()
    if not low:
        return True
    if low in _HOST_SKIP_DIR_NAMES or low.endswith(".stage"):
        return True
    return False


def _dir_is_session(path: Path) -> bool:
    """True when *path* itself is a session directory (no recursion)."""
    names: set[str] = set()
    try:
        with os.scandir(path) as it:
            for ent in it:
                if not ent.is_file(follow_symlinks=False):
                    continue
                names.add(ent.name)
                if names & {"summary.json", "updates.jsonl"}:
                    return True
    except OSError:
        return False
    if "events.jsonl" in names:
        try:
            return (path / "events.jsonl").stat().st_size > 0
        except OSError:
            return False
    return False


def _immediate_session_children(path: Path) -> list[Path]:
    """Session dirs that are direct children of *path* (no deeper walk)."""
    out: list[Path] = []
    try:
        with os.scandir(path) as it:
            children = list(it)
    except OSError:
        return out
    for ent in children:
        if not ent.is_dir(follow_symlinks=False):
            continue
        if is_host_skip_dir_name(ent.name):
            continue
        child = Path(ent.path)
        if _dir_is_session(child):
            out.append(child)
    return out


def is_host_directory_store(root: Path) -> bool:
    """True when *root* is a grok-shaped directory session tree.

    Host trees nest sessions under a percent-encoded cwd bucket
    (``<store>/%2Fhome%2F…/<id>``) or keep session dirs as immediate
    children. Jsonl adapter stores (dash-encoded project dirs with
    ``<uuid>.jsonl``, date-bucketed rollout files) are not this shape.
    """
    path = Path(root).expanduser()
    if not path.is_dir():
        return False
    try:
        with os.scandir(path) as it:
            tops = list(it)
    except OSError:
        return False
    for ent in tops:
        if not ent.is_dir(follow_symlinks=False):
            continue
        if is_host_skip_dir_name(ent.name):
            continue
        if is_encoded_cwd_name(ent.name):
            return True
        if _dir_is_session(Path(ent.path)):
            return True
    return False


def list_host_session_dirs(root: Path) -> list[Path]:
    """Host session dirs by tree shape: children or one encoded-cwd level.

    Uses directory entries only (no ``summary.json`` read). Does not walk
    ``workspace`` / staging junk or recurse into a session.
    """
    path = Path(root).expanduser()
    if not path.is_dir():
        return []
    found: list[Path] = []
    try:
        with os.scandir(path) as it:
            tops = list(it)
    except OSError:
        return found
    for ent in tops:
        if not ent.is_dir(follow_symlinks=False):
            continue
        name = ent.name
        if is_host_skip_dir_name(name):
            continue
        child = Path(ent.path)
        if _dir_is_session(child):
            found.append(child)
            continue
        if is_encoded_cwd_name(name):
            found.extend(_immediate_session_children(child))
    return found


def collect_host_session_dirs(root: Path) -> list[Path]:
    """Host sessions for the operator catalog (tree shape, then drop children)."""
    return drop_subagent_sessions(list_host_session_dirs(root))


def collect_session_dirs(
    roots: list[SessionScanRoot],
) -> list[Path]:
    """Find unique session directories across *roots*."""
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        path = root.path
        if not path.exists():
            continue
        if root.origin == ORIGIN_IMPORT:
            from ..harness.registry import discover_dirs

            session_dirs = discover_dirs(path)
        else:
            session_dirs = collect_host_session_dirs(path)
        for sd in session_dirs:
            try:
                key = str(sd.resolve())
            except OSError:
                key = str(sd)
            if key in seen:
                continue
            seen.add(key)
            found.append(sd)
    return drop_subagent_sessions(found)


__all__ = [
    "ORIGIN_HOST",
    "ORIGIN_IMPORT",
    "ORIGIN_WORK",
    "SessionOrigin",
    "classify_session_origin",
    "SessionScanRoot",
    "collect_host_session_dirs",
    "collect_session_dirs",
    "default_catalog_root",
    "list_host_session_dirs",
    "is_adapter_store_root",
    "is_encoded_cwd_name",
    "is_host_directory_store",
    "session_dir_for_watch_path",
    "session_run_dir",
    "is_host_skip_dir_name",
    "is_under_adapter_store",
    "session_scan_roots",
]
