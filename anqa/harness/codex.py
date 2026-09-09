"""Codex disk adapter (``~/.codex/sessions/**/rollout-*.jsonl``).

One jsonl file is one conversation. Catalog path is ``codex:<session_id>``.
"""

from __future__ import annotations

import re
import tarfile
from collections.abc import Sequence
from pathlib import Path

from ..models import JsonObject, SessionMeta, TraceEvent, json_mapping
from ..stamp import Stamp
from .ref import SessionRef

CODEX_HARNESS_ID = "codex"
_ROLL_ID = re.compile(
    r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)
_TURN_SIGNALS = frozenset({"task_started", "task_complete", "turn_aborted"})


def default_sessions_root() -> Path:
    """Host Codex sessions tree (resolved at call time)."""
    return Path.home() / ".codex" / "sessions"


def _session_id_from_name(path: Path) -> str:
    match = _ROLL_ID.search(path.name)
    return match.group(1) if match else path.stem


def _blocks_text(raw: object, *, kinds: frozenset[str]) -> str:
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, list):
        return ""
    bits: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") not in kinds:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            bits.append(text)
    return "\n".join(bits)


def _collect_jsonl(roots: Sequence[Path]) -> list[Path]:
    out: list[Path] = []
    for raw in roots:
        path = Path(raw).expanduser()
        if path.is_file() and path.name.startswith("rollout-") and path.suffix == ".jsonl":
            out.append(path)
            continue
        if not path.is_dir():
            continue
        from ..scan import find_files

        out.extend(sorted(find_files(path, suffix=".jsonl", name_prefix="rollout-")))
    return out


def _meta_row(rows: Sequence[JsonObject]) -> JsonObject:
    for row in rows:
        if str(row.get("type") or "") == "session_meta":
            return json_mapping(row.get("payload"))
    return {}


def _ref_for_file(path: Path) -> SessionRef | None:
    if not path.is_file():
        return None
    from .jsonl_list import JsonlFile

    header = _meta_row(JsonlFile(path).first_objects())
    sid = str(header.get("session_id") or header.get("id") or _session_id_from_name(path)).strip()
    if not sid:
        return None
    loc = path
    try:
        loc = path.resolve()
    except OSError:
        pass
    return SessionRef(
        harness=CODEX_HARNESS_ID,
        session_id=sid,
        locator=loc,
        cwd=str(header.get("cwd") or "").strip(),
    )


def _jsonl_from_ref(ref: SessionRef | Path | str, root: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    text = str(ref)
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(text)
    if parsed is not None:
        found = _find_file(root, parsed[1])
        return (found or Path(), parsed[1])
    path = Path(text).expanduser()
    if path.is_file():
        return path, _session_id_from_name(path)
    return Path(), path.name


def _find_file(root: Path, session_id: str) -> Path | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    for path in _collect_jsonl([root]):
        if sid in path.name:
            return path
    return None


class CodexAdapter:
    """Read-only Codex rollout jsonl adapter."""

    id = CODEX_HARNESS_ID
    product = "Codex"
    supported_version = "0.151.0"

    def root(self) -> Path:
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
            return ref.harness == CODEX_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == CODEX_HARNESS_ID
        path = Path(str(ref)).expanduser()
        return path.is_file() and path.name.startswith("rollout-") and path.suffix == ".jsonl"

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not self.looks_like(path):
            return None
        return _ref_for_file(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        from ..core import list_meta

        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"codex session not found: {sid}")
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
        return ()

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"codex session not found: {sid}")
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
            raise FileNotFoundError(f"codex session not found: {sid}")
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

        path, _sid = _jsonl_from_ref(ref, default_sessions_root())
        unlink_file(path, stop_at=default_sessions_root())


__all__ = [
    "CODEX_HARNESS_ID",
    "CodexAdapter",
    "default_sessions_root",
]
