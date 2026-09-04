"""Non-recursive plane watch for live session / timeline refresh.

Uses :mod:`watchfiles` on membership directories and session directories.
Plane writes land in those directories. ``workspace/`` is never subscribed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .session.watch import (
    PLANE_FILE_NAMES,
    plane_event_path,
    session_dirs_under,
    watch_target_paths,
)

logger = logging.getLogger(__name__)

# Basenames that should reload a directory session (harness + tests).
# The owner watches session directories; these names classify events.
TRACE_FILE_HINTS: tuple[str, ...] = (
    "updates.jsonl",
    "events.jsonl",
    "summary.json",
    "signals.json",
    "chat_history.jsonl",
    "anqa-interrupted.json",
    "status.json",
    "command",
    "operator_notes.toml",
)
_TRACE_NAME_HINTS = TRACE_FILE_HINTS


class TraceTreeWatch:
    """Watch *root* (or one *session_dir*) without descending ``workspace/``.

    *on_change* is called from the watch thread — callers must marshal to
    the UI thread themselves (``call_from_thread`` / ``post_message``).

    When *on_paths* is set, it receives the changed absolute paths.
    """

    def __init__(
        self,
        root: Path,
        on_change: Callable[[], None],
        *,
        debounce_s: float = 0.05,
        on_paths: Callable[[list[str]], None] | None = None,
        session_dir: Path | None = None,
        host_root: Path | None = None,
        membership_only: bool = False,
    ) -> None:
        self._root = Path(root)
        self._session_dir = Path(session_dir) if session_dir is not None else None
        self._host_root = Path(host_root) if host_root is not None else None
        self._membership_only = bool(membership_only)
        if self._session_dir is None and self._root.is_file():
            self._root = self._root.parent
            self._membership_only = True
        self._on_change = on_change
        self._on_paths = on_paths
        self._debounce_s = max(0.0, float(debounce_s))
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._paths: list[Path] = []

    @property
    def root(self) -> Path:
        return self._root

    def subscribed_paths(self) -> list[Path]:
        """Paths currently handed to watchfiles (no ``workspace/``)."""
        return list(self._paths)

    @staticmethod
    def path_relevant(path: str) -> bool:
        """True when *path* is a plane file outside ``workspace/``."""
        return path_relevant(path)

    def _collect_paths(self) -> list[Path]:
        if self._session_dir is not None:
            return watch_target_paths([self._session_dir], [self._session_dir])
        sessions = session_dirs_under(
            [self._root],
            host_root=self._host_root,
            list_sessions=not self._membership_only,
        )
        return watch_target_paths([self._root], sessions)

    def start(self) -> bool:
        """Start watching. True when the watch thread is up.

        Ready is set when the watch thread is running. Path collect
        continues in that thread, so a large tree cannot make ``start()``
        return false or abandon the watch.
        """
        if not self._root.is_dir() and self._session_dir is None:
            return False
        self._stop.clear()
        self._ready.clear()
        thread = threading.Thread(target=self._run, name="anqa-plane-watch", daemon=True)
        self._thread = thread
        thread.start()
        return self._ready.wait(2.0)

    def _run(self) -> None:
        self._ready.set()
        try:
            from watchfiles import watch
        except ImportError:
            logger.warning("watchfiles not installed; live FS watch disabled")
            return
        debounce_ms = int(self._debounce_s * 1000)
        while not self._stop.is_set():
            paths = self._collect_paths()
            if not paths:
                if self._stop.wait(0.25):
                    return
                continue
            self._paths = paths
            try:
                for changes in watch(
                    *paths,
                    recursive=False,
                    debounce=debounce_ms,
                    stop_event=self._stop,
                    yield_on_timeout=True,
                    rust_timeout=200,
                    step=50,
                ):
                    if self._stop.is_set():
                        return
                    if not changes:
                        continue
                    fired = [path for kind, path in changes if self._keep_event(kind, path)]
                    if fired:
                        self._emit(fired)
                    if not any(self._membership_event(kind, path) for kind, path in changes):
                        continue
                    nxt = self._collect_paths()
                    if {str(p) for p in nxt} != {str(p) for p in paths}:
                        break
            except Exception:
                logger.debug("FS watch iteration failed", exc_info=True)
                if self._stop.wait(0.25):
                    return

    def _membership_event(self, kind: object, path: str) -> bool:
        """True for directory add/delete (not a plane-file write)."""
        if Path(path).name in PLANE_FILE_NAMES:
            return False
        return isinstance(kind, int) and kind != 2

    def _keep_event(self, kind: object, path: str) -> bool:
        p = Path(path)
        if any(part.casefold() == "workspace" for part in p.parts):
            return False
        if p.name in PLANE_FILE_NAMES:
            return True
        from .harness.registry import adapter_watch_hits

        if adapter_watch_hits(p):
            return True
        if self._session_dir is not None:
            return False
        if not isinstance(kind, int):
            return False
        return plane_event_path(p, kind=kind)

    def _emit(self, paths: list[str]) -> None:
        try:
            self._on_change()
        except Exception:
            logger.debug("FS watch callback failed", exc_info=True)
        if self._on_paths is not None:
            try:
                self._on_paths(paths)
            except Exception:
                logger.debug("FS watch path callback failed", exc_info=True)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)


def path_relevant(path: str) -> bool:
    """True when *path* is a plane file (not under ``workspace/``)."""
    p = Path(path)
    if any(part.casefold() == "workspace" for part in p.parts):
        return False
    return p.name in PLANE_FILE_NAMES
