"""Import a harness archive or anqa export into ``~/.anqa/imports``."""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..harness.ref import SessionRef
from ..harness.registry import adapter, adapters, ref_from_path
from ..models import JsonObject, as_json_object
from ..notes import NOTES_FILENAME
from ..paths import imports_dir
from ..tags import TAGS_FILENAME
from .export_bundle import SESSION_ARCHIVE_NAME

logger = logging.getLogger(__name__)

IMPORT_SIDECAR = "anqa-import.toml"
ANQA_BUNDLE_KIND = "anqa-session-export"


@dataclass(frozen=True)
class ImportResult:
    """Outcome of :func:`import_session`."""

    ref: SessionRef
    source: Path
    replaced: bool


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _write_sidecar(ref: SessionRef, source: Path) -> None:
    loc = Path(ref.locator)
    dest = (loc / IMPORT_SIDECAR) if loc.is_dir() else (ref.overlay_dir() / IMPORT_SIDECAR)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        (
            f"source = {_toml_string(str(source))}\n"
            f"imported_at = {_toml_string(datetime.now(UTC).isoformat())}\n"
            f"harness = {_toml_string(ref.harness)}\n"
            f"session_id = {_toml_string(ref.session_id)}\n"
        ),
        encoding="utf-8",
    )


def _read_json_mapping(raw: bytes | str) -> JsonObject | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return as_json_object(data) if isinstance(data, dict) else None


def bundle_manifest(path: Path) -> JsonObject | None:
    """Return the anqa export manifest when *path* is that bundle."""
    loc = Path(path).expanduser()
    if loc.is_dir():
        man = loc / "manifest.json"
        if not man.is_file():
            return None
        try:
            parsed = _read_json_mapping(man.read_text(encoding="utf-8"))
        except OSError:
            return None
    elif loc.is_file():
        try:
            with tarfile.open(loc, "r:*") as tf:
                member = tf.getmember("manifest.json")
                handle = tf.extractfile(member)
                if handle is None:
                    return None
                parsed = _read_json_mapping(handle.read())
        except (KeyError, tarfile.TarError, OSError):
            return None
    else:
        return None
    if parsed is None or str(parsed.get("kind") or "") != ANQA_BUNDLE_KIND:
        return None
    return parsed


def looks_like_import_source(path: Path) -> bool:
    """True when *path* is a session directory, anqa bundle, or archive file."""
    loc = Path(path).expanduser()
    if loc.is_dir():
        return ref_from_path(loc) is not None
    if not loc.is_file():
        return False
    if bundle_manifest(loc) is not None:
        return True
    try:
        with tarfile.open(loc, "r:*") as tf:
            return any(m.name for m in tf.getmembers())
    except (tarfile.TarError, OSError):
        return False


def _dest_existed(dest_root: Path, session_id: str) -> bool:
    hit = dest_root / session_id
    return hit.exists()


def _archive_session_id(src: Path) -> str:
    try:
        with tarfile.open(src, "r:*") as tf:
            names = [m.name for m in tf.getmembers() if m.name]
    except (tarfile.TarError, OSError):
        return ""
    tops = {n.split("/", 1)[0] for n in names if n and n != "."}
    if len(tops) != 1:
        return ""
    return next(iter(tops))


def _copy_bound_session(ref: SessionRef, dest_root: Path) -> SessionRef:
    loc = Path(ref.locator)
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / ref.session_id
    try:
        if dest.exists() and dest.resolve() == loc.resolve():
            return ref
    except OSError:
        pass
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    if loc.is_dir():
        shutil.copytree(loc, dest, symlinks=False)
        copied = dest
    else:
        shutil.copy2(loc, dest)
        copied = dest
    item = adapter(ref.harness)
    if item is None:
        raise RuntimeError(f"no adapter for harness: {ref.harness}")
    bound = item.bind_locator(copied)
    if bound is None:
        raise RuntimeError(f"adapter could not bind imported locator: {copied}")
    return bound


def _restore_notes(ref: SessionRef, notes_file: Path) -> None:
    dest_dir = ref.overlay_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(notes_file, dest_dir / NOTES_FILENAME)


def _restore_tags(ref: SessionRef, tags_file: Path) -> None:
    import tomllib

    from ..tags import save_tags

    raw = tomllib.loads(tags_file.read_text(encoding="utf-8"))
    values = raw.get("tags")
    tags = [str(item) for item in values] if isinstance(values, list) else []
    save_tags(ref, tags)


def _open_with_adapter(src: Path, dest_root: Path, harness_id: str) -> SessionRef:
    item = adapter(harness_id)
    if item is None:
        raise RuntimeError(f"unknown harness: {harness_id}")
    dest_root.mkdir(parents=True, exist_ok=True)
    return item.open_archive(src, dest_root)


def _open_native(src: Path, dest_home: Path) -> SessionRef:
    errors: list[str] = []
    for item in adapters():
        with tempfile.TemporaryDirectory(prefix=f"anqa-open-{item.id}-") as tmp:
            try:
                ref = item.open_archive(src, Path(tmp))
            except (RuntimeError, FileNotFoundError, OSError, tarfile.TarError) as exc:
                errors.append(f"{item.id}: {exc}")
                continue
            return _copy_bound_session(ref, dest_home / item.id)
    detail = "; ".join(errors) if errors else "no adapters"
    raise RuntimeError(f"not a session archive: {src} ({detail})")


def _extract_bundle_tree(path: Path, staging: Path) -> Path:
    if path.is_dir():
        return path
    root = staging / "bundle"
    root.mkdir(parents=True)
    with tarfile.open(path, "r:*") as tf:
        tf.extractall(root, filter="data")
    return root


def _import_children(bundle_root: Path, dest_root: Path, harness_id: str) -> None:
    children = bundle_root / "children"
    if not children.is_dir():
        return
    item = adapter(harness_id)
    if item is None:
        return
    for child in sorted(children.iterdir()):
        nested = child / SESSION_ARCHIVE_NAME
        if nested.is_file():
            item.open_archive(nested, dest_root)


def _import_bundle(path: Path, dest_home: Path, manifest: JsonObject) -> SessionRef:
    hid = str(manifest.get("harness") or "").strip()
    with tempfile.TemporaryDirectory(prefix="anqa-import-bundle-") as tmp:
        root = _extract_bundle_tree(path, Path(tmp))
        nested = root / SESSION_ARCHIVE_NAME
        if not nested.is_file():
            raise RuntimeError(f"export bundle missing {SESSION_ARCHIVE_NAME}")
        if hid:
            dest_root = dest_home / hid
            ref = _open_with_adapter(nested, dest_root, hid)
        else:
            ref = _open_native(nested, dest_home)
            dest_root = dest_home / ref.harness
        notes = root / "notes" / NOTES_FILENAME
        if notes.is_file():
            _restore_notes(ref, notes)
        tags_file = root / "notes" / TAGS_FILENAME
        if tags_file.is_file():
            _restore_tags(ref, tags_file)
        _import_children(root, dest_root, ref.harness)
        return ref


def import_session(path: Path | str, *, dest_home: Path | None = None) -> ImportResult:
    """Open *path* as an imported session under the import store.

    Accepts a session directory, an anqa ``E`` export, or a native
    adapter archive. Re-import of the same harness + id replaces.

    :param path: Archive, bundle, or session locator.
    :param dest_home: Import root (default ``~/.anqa/imports``).
    :returns: Bound session and whether a previous import was replaced.
    :raises FileNotFoundError: *path* does not exist.
    :raises RuntimeError: Not a session archive this host can open.
    """
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"import path not found: {src}")
    try:
        src = src.resolve()
    except OSError:
        pass
    home = Path(dest_home) if dest_home is not None else imports_dir()
    home.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        bound = ref_from_path(src)
        if bound is None:
            raise RuntimeError(f"not a session directory: {src}")
        dest_root = home / bound.harness
        replaced = _dest_existed(dest_root, bound.session_id)
        ref = _copy_bound_session(bound, dest_root)
        _write_sidecar(ref, src)
        return ImportResult(ref=ref, source=src, replaced=replaced)

    manifest = bundle_manifest(src)
    if manifest is not None:
        hid = str(manifest.get("harness") or "").strip()
        sid = str(manifest.get("session_id") or "").strip()
        dest_root = home / hid if hid else home
        replaced = bool(sid) and bool(hid) and _dest_existed(home / hid, sid)
        ref = _import_bundle(src, home, manifest)
        _write_sidecar(ref, src)
        return ImportResult(ref=ref, source=src, replaced=replaced)

    peek = _archive_session_id(src)
    replaced = bool(peek) and any(_dest_existed(home / item.id, peek) for item in adapters())
    ref = _open_native(src, home)
    _write_sidecar(ref, src)
    return ImportResult(ref=ref, source=src, replaced=replaced)


__all__ = [
    "ANQA_BUNDLE_KIND",
    "IMPORT_SIDECAR",
    "ImportResult",
    "bundle_manifest",
    "import_session",
    "looks_like_import_source",
]
