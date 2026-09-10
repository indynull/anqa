"""Delete native session locators (directory, file, or database row)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..models import JsonObject, as_json_object

logger = logging.getLogger(__name__)


def unlink_file(path: Path, *, stop_at: Path | None = None) -> None:
    """Remove one session file and optional empty parents up to *stop_at*."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(path)
    path.unlink()
    if stop_at is not None:
        prune_empty_parents_after_session_delete(path, stop_at=stop_at)


def rmtree_robust(path: Path) -> None:
    """Remove *path* with ``shutil.rmtree``.

    :raises PermissionError: when the tree is not writable (e.g. leftover
        root-owned session trees).
    """
    path = Path(path).expanduser()
    if not path.exists():
        return
    shutil.rmtree(path)


def session_dirs_for_delete(session_dirs: list[Path]) -> list[Path]:
    """Normalize and de-dupe locators before delete.

    ``harness:id`` catalog refs stay as that string. Filesystem paths
    are resolved.
    """
    from ..harness.ref import parse_session_ref_string

    seen: set[str] = set()
    out: list[Path] = []
    for sd in session_dirs:
        text = str(sd)
        if parse_session_ref_string(text) is not None:
            key = text
            loc = Path(text)
        else:
            try:
                loc = Path(sd).expanduser().resolve()
                key = str(loc)
            except OSError:
                loc = Path(sd)
                key = text
        if key in seen:
            continue
        seen.add(key)
        out.append(loc)
    return out


def prune_empty_parents_after_session_delete(
    session_dir: Path,
    *,
    stop_at: Path | None = None,
) -> list[Path]:
    """Remove empty parent directories up to *stop_at* (not including it)."""
    removed: list[Path] = []
    try:
        cur = Path(session_dir).expanduser()
        try:
            cur = cur.resolve()
        except OSError:
            pass
        if not cur.is_dir():
            cur = cur.parent
        stop: Path | None = None
        if stop_at is not None:
            try:
                stop = Path(stop_at).expanduser().resolve()
            except OSError:
                stop = Path(stop_at).expanduser()
    except OSError:
        return removed

    for _ in range(32):
        if not cur.is_dir():
            break
        if stop is not None and cur == stop:
            break
        if stop is not None:
            try:
                cur.relative_to(stop)
            except ValueError:
                break
        try:
            children = list(cur.iterdir())
        except OSError:
            break
        if children:
            break
        parent = cur.parent
        try:
            cur.rmdir()
            removed.append(cur)
        except OSError:
            break
        cur = parent
    return removed


def delete_session_dirs(
    session_dirs: list[Path],
    *,
    traces_root: Path | None = None,
    prune_empty_parents: bool = True,
) -> JsonObject:
    """Delete native session locators (directory, file, or database row).

    :returns: Counts and error strings.
    """
    from ..harness.registry import require_adapter, resolve_session_ref

    deleted = 0
    errors: list[str] = []
    parents_pruned: list[str] = []
    _ = (traces_root, prune_empty_parents)

    for sd in session_dirs_for_delete(session_dirs):
        try:
            found = resolve_session_ref(str(sd))
            target = found if found is not None else sd
            require_adapter(target).delete_session(target)
            deleted += 1
        except FileNotFoundError:
            errors.append(f"missing: {sd}")
        except OSError as exc:
            errors.append(f"{sd}: {exc}")

    return as_json_object(
        {
            "deleted": deleted,
            "parents_pruned": parents_pruned,
            "parents_pruned_count": len(parents_pruned),
            "errors": errors,
            "requested": len(session_dirs),
        }
    )
