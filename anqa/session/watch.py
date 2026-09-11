"""Non-recursive catalog watch: membership dirs and session dirs.

Plane writes show up as children of the session directory. ``workspace/``
is never subscribed. ``anqad`` and the TUI share this path set.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..harness.registry import discover_dirs
from .sources import (
    is_host_directory_store,
    is_host_skip_dir_name,
    list_host_session_dirs,
)
from .subagents import drop_subagent_sessions

PLANE_FILE_NAMES: tuple[str, ...] = (
    "summary.json",
    "signals.json",
    "updates.jsonl",
    "operator_notes.toml",
)


def plane_file_paths(session_dir: Path) -> list[Path]:
    """The four session-plane files under *session_dir*."""
    root = Path(session_dir)
    return [root / name for name in PLANE_FILE_NAMES]


def membership_watch_dirs(roots: list[Path], *, child_dirs: bool = True) -> list[Path]:
    """Directories whose direct children appearing or vanishing change membership.

    A file root (sqlite store) contributes only its parent directory.
    A directory root also includes one level of project folders (Pi
    ``--cwd--``, Claude projects, Grok ``%2F…`` buckets) when
    *child_dirs* is true.
    """
    out: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw).expanduser()
        if root.is_file():
            parent = root.parent
            key = str(parent)
            if parent.is_dir() and key not in seen:
                seen.add(key)
                out.append(parent)
            continue
        if not root.is_dir():
            continue
        key = str(root)
        if key not in seen:
            seen.add(key)
            out.append(root)
        if not child_dirs:
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            if is_host_skip_dir_name(child.name):
                continue
            child_key = str(child)
            if child_key not in seen:
                seen.add(child_key)
                out.append(child)
    return out


def _is_named_host_root(root: Path, host_root: Path) -> bool:
    """True when *root* is the named host sessions tree (not a %2F sniff)."""
    try:
        return root.expanduser().resolve() == host_root.expanduser().resolve()
    except OSError:
        return False


def _use_host_lister(root: Path, host_root: Path | None) -> bool:
    """True for an explicit host root or a grok-shaped directory session tree."""
    if host_root is not None and _is_named_host_root(root, host_root):
        return True
    return is_host_directory_store(root)


def session_dirs_under(
    roots: list[Path],
    *,
    host_root: Path | None = None,
    list_sessions: bool = True,
) -> list[Path]:
    """Listed session directories under catalog *roots* (no workspace descent).

    The named host root uses the shallow host lister. Other *directory
    session* roots use adapter discover. Extra adapter stores
    (``list_sessions=False``) contribute no session dirs — membership
    watch only, never a recursive walk.
    """
    if not list_sessions:
        return []
    named = Path(host_root).expanduser() if host_root is not None else None
    found: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw).expanduser()
        if not root.is_dir():
            continue
        listed = (
            list_host_session_dirs(root) if _use_host_lister(root, named) else discover_dirs(root)
        )
        listed = drop_subagent_sessions(listed)
        for session in listed:
            key = str(session)
            if key in seen:
                continue
            seen.add(key)
            found.append(session)
    return found


def _no_workspace(path: Path) -> bool:
    return all(part.casefold() != "workspace" for part in path.parts)


def watch_target_paths(
    roots: list[Path],
    session_dirs: list[Path],
    *,
    expand_children: bool = True,
    membership_children: bool = True,
) -> list[Path]:
    """Directories passed to watchfiles (non-recursive). Never ``workspace/``.

    *expand_children* is a second level under membership dirs (Grok
    session directories). *membership_children* is the first level
    (project folders). A sqlite file store passes both false.
    """
    out: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        if not _no_workspace(path):
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        out.append(path)

    for path in membership_watch_dirs(roots, child_dirs=membership_children):
        _add(path)
        if not expand_children:
            continue
        try:
            children = list(path.iterdir())
        except OSError:
            children = []
        for child in children:
            if child.is_dir() and not is_host_skip_dir_name(child.name):
                _add(child)
    for session in session_dirs:
        loc = Path(session)
        _add(loc.parent if loc.is_file() else loc)
    return out


def catalog_subscribe_paths(roots: list[Path], session_dirs: list[Path]) -> list[Path]:
    """Membership dirs and session dirs. Never includes ``workspace/``."""
    return watch_target_paths(roots, session_dirs)


# watchfiles.Change.modified — nested writes often report the session dir.
_WATCH_MODIFIED = 2


def plane_event_path(path: Path, *, kind: int | None = None) -> bool:
    """True when *path* is a plane file or a membership add/delete."""
    if not _no_workspace(path):
        return False
    if path.name.casefold() == "workspace":
        return False
    if path.name in PLANE_FILE_NAMES:
        return True
    if kind == _WATCH_MODIFIED:
        # Nested plane writes often report the session directory.
        return path.is_dir() and any((path / name).is_file() for name in PLANE_FILE_NAMES)
    return path.is_dir() or not path.suffix


class JournalTail:
    """Byte offset into one ``updates.jsonl``. Second consume does not seek 0."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.inode: int | None = None
        self.offset: int = 0

    def consume(self) -> bytes:
        """Return bytes after the last offset. Updates :attr:`offset`."""
        try:
            fd = os.open(self.path, os.O_RDONLY)
        except OSError:
            return b""
        try:
            st = os.fstat(fd)
            inode = int(st.st_ino)
            if self.inode is not None and inode != self.inode:
                self.offset = 0
            self.inode = inode
            if self.offset > st.st_size:
                self.offset = 0
            os.lseek(fd, self.offset, os.SEEK_SET)
            data = os.read(fd, max(0, st.st_size - self.offset))
            self.offset = int(os.lseek(fd, 0, os.SEEK_CUR))
            return data
        finally:
            os.close(fd)
