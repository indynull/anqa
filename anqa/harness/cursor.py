"""Cursor disk adapter (``~/.cursor/projects/*/agent-transcripts``).

One jsonl file is one conversation. Catalog path is ``cursor:<session_id>``.
List metadata comes from ``~/.cursor/chats/*/<id>/meta.json``.
"""

from __future__ import annotations

import json
import tarfile
from collections.abc import Sequence
from pathlib import Path

from ..models import JsonObject, SessionMeta, TraceEvent, json_mapping
from ..stamp import Stamp
from .ref import SessionRef

CURSOR_HARNESS_ID = "cursor"


def default_store_root() -> Path:
    """Host Cursor config tree (resolved at call time)."""
    return Path.home() / ".cursor"


def _load_json(path: Path) -> JsonObject:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return json_mapping(raw)


def _is_transcript(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".jsonl":
        return False
    return path.parent.parent.name == "agent-transcripts"


def _collect_jsonl(roots: Sequence[Path]) -> list[Path]:
    out: list[Path] = []
    for raw in roots:
        path = Path(raw).expanduser()
        if _is_transcript(path):
            out.append(path)
            continue
        if not path.is_dir():
            continue
        from ..scan import find_files

        out.extend(
            sorted(p for p in find_files(path, suffix=".jsonl") if "agent-transcripts" in p.parts)
        )
    return out


def _find_meta(root: Path, sid: str) -> JsonObject:
    chats = root / "chats"
    if chats.is_dir():
        for path in chats.glob(f"*/{sid}/meta.json"):
            return _load_json(path)
    if root.name == sid and (root / "meta.json").is_file():
        return _load_json(root / "meta.json")
    return {}


def _ref_for_file(path: Path, cwd: str = "") -> SessionRef | None:
    if not _is_transcript(path):
        return None
    sid = path.stem.strip()
    if not sid:
        return None
    return SessionRef(
        harness=CURSOR_HARNESS_ID,
        session_id=sid,
        locator=path,
        cwd=cwd,
    )


def _jsonl_from_ref(ref: SessionRef | Path | str, root: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(str(ref))
    if parsed is not None:
        found = _find_file(root, parsed[1])
        return (found or Path(), parsed[1])
    path = Path(str(ref)).expanduser()
    if _is_transcript(path):
        return path, path.stem
    return Path(), path.name


def _find_file(root: Path, session_id: str) -> Path | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    for path in _collect_jsonl([root]):
        if path.stem == sid:
            return path
    return None


class CursorAdapter:
    """Read-only Cursor agent-transcript adapter."""

    id = CURSOR_HARNESS_ID
    product = "Cursor"
    supported_version = "2026.09.10-fd3934a"

    def root(self) -> Path:
        return default_store_root()

    def default_host_roots(self) -> list[Path]:
        path = self.root()
        return [path] if path.is_dir() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        scan = [self.root()] if roots is None else [Path(r) for r in roots]
        found: list[SessionRef] = []
        seen: set[str] = set()
        for file in _collect_jsonl(scan):
            header = _find_meta(self.root() if roots is None else scan[0], file.stem)
            ref = _ref_for_file(file, cwd=str(header.get("cwd") or "").strip())
            if ref is None or ref.session_id in seen:
                continue
            seen.add(ref.session_id)
            found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == CURSOR_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == CURSOR_HARNESS_ID
        return _is_transcript(Path(str(ref)).expanduser())

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not self.looks_like(path):
            return None
        return _ref_for_file(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        from ..core import list_meta

        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"cursor session not found: {sid}")
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
        return ("meta.json",)

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"cursor session not found: {sid}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        packed = False
        members = [f"{sid}/{path.name}"]
        extras: list[tuple[Path, str]] = []
        for meta in self.root().joinpath("chats").glob(f"*/{sid}/meta.json"):
            extras.append((meta, f"{sid}/meta.json"))
            break
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                tf.add(path, arcname=members[0])
                for extra, name in extras:
                    tf.add(extra, arcname=name)
                    members.append(name)
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
        unlink_file(path, stop_at=self.root())
        chats = self.root() / "chats"
        if chats.is_dir():
            for meta in chats.glob(f"*/{sid}"):
                if meta.is_dir():
                    rmtree_robust(meta)


__all__ = [
    "CURSOR_HARNESS_ID",
    "CursorAdapter",
    "default_store_root",
]
