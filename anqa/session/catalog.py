"""Domain session catalog for control plane and headless owners.

Builds wire-shaped catalog rows and resolves session references from disk
without Textual app state. Shared by the control daemon and any client that
needs the same discovery rules as the TUI home list.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ..harness.ref import SessionRef, parse_session_ref_string
from ..harness.registry import adapter, harness_product, ref_from_path, require_adapter
from ..models import (
    JsonObject,
    JsonValue,
    SessionMeta,
    json_count,
    json_count_float,
    json_count_or_none,
)
from ..paths import is_import_locator
from ..stamp import Stamp
from .mtime_export import (
    default_catalog_snapshot,
    load_or_rebuild_catalog,
    load_or_rebuild_refs,
    read_catalog_snapshot_rows,
)
from .query import apply_catalog_presence_row, catalog_presence, catalog_presence_from_meta
from .sources import (
    SessionOrigin,
    SessionScanRoot,
    collect_session_dirs,
    session_run_dir,
    session_scan_roots,
)
from .subagents import (
    drop_subagent_sessions,
    is_subagent_session_dir,
    nested_child_ids,
)

logger = logging.getLogger(__name__)


def locator_index_from_rows(rows: list[JsonObject]) -> dict[str, str]:
    """Map session id, ``harness:id``, and locator strings to the locator."""
    index: dict[str, str] = {}
    for row in rows:
        loc = str(row.get("locator") or "").strip()
        if not loc:
            continue
        sid = str(row.get("sessionId") or "").strip()
        path = str(row.get("path") or "").strip()
        harness = str(row.get("harness") or "").strip()
        for key in (loc, sid, path):
            if key:
                index[key] = loc
        if harness and sid:
            index[f"{harness}:{sid}"] = loc
    return index


def catalog_row_sort_epoch(row: JsonObject, *, session_dir: Path | None = None) -> float:
    """Best-effort “latest activity” epoch for newest-first catalog order."""
    for key in ("sortEpoch", "updatedAt", "createdAt", "updated_at", "created_at"):
        if key == "sortEpoch":
            raw = row.get(key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return float(raw)
            continue
        ts = float(Stamp.epoch(row.get(key)) or 0)
        if ts > 0:
            return ts
    path = session_dir
    if path is None:
        path_raw = str(row.get("path") or "").strip()
        if path_raw:
            path = Path(path_raw)
    if path is not None:
        try:
            mt = require_adapter(path).trace_mtime(path)
            if mt > 0:
                return float(mt)
        except (OSError, FileNotFoundError):
            pass
        try:
            return float(path.stat().st_mtime)
        except OSError:
            pass
    return 0.0


def effective_include_host(include_host: bool | None) -> bool:
    """Resolve catalog host inclusion: explicit flag, else always include.

    Adapter stores stay in the catalog.
    """
    if include_host is not None:
        return bool(include_host)
    return True


def catalog_scan_roots(
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> list[SessionScanRoot]:
    """Scan roots for the control/domain session catalog.

    :param traces_path: Optional extra store path (CLI ``-P`` override).
    :param include_host: When false, skip adapter host stores.
    :param host_root: Override for the host sessions root.
    :returns: Ordered scan roots.
    """
    want_host = effective_include_host(include_host)
    return session_scan_roots(
        traces_path=traces_path,
        include_host=want_host,
        host_root=host_root,
    )


def catalog_row_for_ref(ref: SessionRef, *, label: str | None = None) -> JsonObject | None:
    """Build one ``session/list`` row from a :class:`SessionRef`.

    Does not create a notes directory. Directory locators fill ``has:``
    flags from the session tree (``goal/state.json``, ``plan.json``, …)
    plus counts already on list meta.
    """
    impl = adapter(ref.harness)
    if impl is None:
        try:
            impl = require_adapter(ref.locator if ref.locator.exists() else ref)
        except FileNotFoundError:
            return None
    try:
        meta = impl.load_meta(ref)
    except (OSError, FileNotFoundError):
        logger.debug("catalog meta failed for %s", ref.ref_string(), exc_info=True)
        return None
    locator = Path(ref.locator)
    if locator.is_dir():
        meta.run_dir = meta.run_dir or session_run_dir(locator)
    elif not meta.run_dir:
        meta.run_dir = ref.cwd or ""
    try:
        locator_str = str(locator.resolve())
    except OSError:
        locator_str = str(locator)
    imported = is_import_locator(locator)
    path_str = locator_str if imported else ref.ref_string()
    meta.origin = SessionOrigin.IMPORT if imported else SessionOrigin.HOST
    if not (meta.harness or "").strip():
        meta.harness = ref.harness
    session_id = (meta.session_id or ref.session_id).strip()
    created = str(meta.created_at or "").strip()
    updated = str(meta.updated_at or "").strip()
    sort_epoch = float(Stamp.epoch(updated) or 0) or float(Stamp.epoch(created) or 0)
    if sort_epoch <= 0:
        try:
            sort_epoch = float(impl.trace_mtime(ref))
        except OSError:
            sort_epoch = 0.0
    if sort_epoch <= 0:
        try:
            sort_epoch = float(locator.stat().st_mtime)
        except OSError:
            sort_epoch = 0.0
    presence = (
        catalog_presence(locator, meta) if locator.is_dir() else catalog_presence_from_meta(meta)
    )
    return {
        "sessionId": session_id,
        "path": path_str,
        "locator": locator_str,
        "title": (meta.title or "").strip(),
        "label": label if label is not None else meta.label,
        "model": meta.model_display,
        "status": meta.list_status_label(),
        "outcome": meta.turn_outcome or "",
        "origin": meta.origin,
        "imported": imported,
        "harness": (meta.harness or "").strip(),
        "harnessVersion": (meta.harness_version or "").strip(),
        "harnessLabel": harness_product(meta.harness),
        "taskId": meta.task_id or "",
        "gitRepo": meta.git_repo or "",
        "runDir": meta.run_dir or "",
        "durationSeconds": float(meta.duration_seconds or 0),
        "numEvents": int(meta.num_events or 0),
        "contextUsageCompact": meta.context_usage_compact or "",
        "contextWindowUsagePct": meta.context_window_usage_pct,
        "contextTokensUsed": meta.context_tokens_used,
        "contextWindowTokens": meta.context_window_tokens,
        "toolCallCount": int(meta.tool_call_count or 0),
        "turnCount": int(meta.turn_count or 0),
        "errorCount": int(meta.error_count or 0),
        "createdAt": created,
        "updatedAt": updated,
        "sortEpoch": sort_epoch,
        **presence,
    }


def session_catalog_row(
    session_dir: Path,
    *,
    label: str | None = None,
) -> JsonObject | None:
    """Build one ``session/list`` wire row for *session_dir*, or None on failure.

    :param session_dir: Session directory on disk.
    :param label: Optional display label; defaults to meta label.
    :returns: Wire row mapping, or None when meta cannot be loaded.
    """
    loc = Path(session_dir)
    bound = ref_from_path(loc)
    if bound is None:
        try:
            item = require_adapter(loc)
        except FileNotFoundError:
            logger.debug("catalog meta failed for %s", loc, exc_info=True)
            return None
        bound = SessionRef(
            harness=item.id,
            session_id=loc.name,
            locator=loc,
        )
    return catalog_row_for_ref(bound, label=label)


def list_session_catalog(
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
    host_catalog_cache: Path | None = None,
) -> list[JsonObject]:
    """Scan catalog roots and return rows for ``session/list``.

    :param traces_path: Optional store path override.
    :param include_host: Host inclusion (True/False force; None includes host).
    :param host_root: Optional host root override.
    :param host_catalog_cache: Optional host snapshot path.
    :returns: Catalog rows sorted newest activity first (``sortEpoch`` desc).
    """
    roots = catalog_scan_roots(
        traces_path=traces_path,
        include_host=include_host,
        host_root=host_root,
    )
    host_paths = [root.path for root in roots]
    rows: list[JsonObject] = []
    seen_host: set[str] = set()
    for hroot in host_paths:
        key = str(hroot)
        if key in seen_host:
            continue
        seen_host.add(key)
        dest = (
            host_catalog_cache
            if host_catalog_cache is not None
            else default_catalog_snapshot(hroot)
        )
        rows.extend(
            load_or_rebuild_catalog(
                hroot,
                dest=dest,
                build_row=session_catalog_row,
            )
        )
    if effective_include_host(include_host):
        rows.extend(_adapter_host_catalog_rows(host_catalog_cache=host_catalog_cache))
    rows.sort(
        key=lambda r: (
            -catalog_row_sort_epoch(r),
            str(r.get("sessionId") or ""),
        )
    )
    return rows


_DEAD_LIST_KEYS = ("originTag", "isHost", "origin", "locator")


def public_catalog_row(row: JsonObject) -> JsonObject:
    """List row as clients should see it: harness label plus import origin."""
    out = dict(row)
    for key in _DEAD_LIST_KEYS:
        out.pop(key, None)
    hid = str(out.get("harness") or "").strip()
    if not hid:
        parsed = parse_session_ref_string(str(out.get("path") or ""))
        hid = parsed[0] if parsed is not None else ""
        out["harness"] = hid
    if not str(out.get("harnessLabel") or "").strip():
        out["harnessLabel"] = harness_product(hid)
    return out


# List-visible fields. Exclude ``sortEpoch`` / ``path`` so an ``updates.jsonl``
# append that only moves mtime does not bump the catalog revision.
_LIST_ROW_SIG_KEYS: tuple[str, ...] = (
    "sessionId",
    "title",
    "label",
    "model",
    "status",
    "outcome",
    "origin",
    "imported",
    "harness",
    "harnessVersion",
    "harnessLabel",
    "taskId",
    "gitRepo",
    "runDir",
    "durationSeconds",
    "numEvents",
    "contextUsageCompact",
    "contextWindowUsagePct",
    "contextTokensUsed",
    "contextWindowTokens",
    "toolCallCount",
    "turnCount",
    "errorCount",
    "workflowCount",
    "noteCount",
    "goalCount",
    "planCount",
    "subagentCount",
    "taskCount",
    "jobCount",
    "scheduleCount",
    "failureCount",
    "diffLineCount",
    "compactionCount",
    "doomCount",
    "hasWorkflows",
    "hasNotes",
    "hasGoals",
    "hasSubagents",
    "hasJobs",
    "hasSchedules",
    "hasTasks",
    "hasPlan",
    "hasFailures",
    "hasDiff",
    "hasCompaction",
    "hasDoom",
    "hasContext",
    "createdAt",
    "updatedAt",
)


def list_row_fingerprint(row: JsonObject) -> tuple[JsonValue, ...]:
    """Stable identity of the fields a catalog client paints."""
    return tuple(row.get(key) for key in _LIST_ROW_SIG_KEYS)


def list_refresh_delta(
    current: list[JsonObject],
    replacements: dict[str, JsonObject],
    appended: list[JsonObject],
    drop: set[str],
) -> tuple[list[JsonObject], list[str], dict[str, bool]]:
    """Compare painted fields. Return upserts, removed ids, and per-id change flags."""
    old_by_path = {str(row.get("path") or "").strip(): row for row in current}
    upserts: list[JsonObject] = []
    list_changed: dict[str, bool] = {}
    for path, new in replacements.items():
        old = old_by_path.get(path)
        sid = str(new.get("sessionId") or "").strip()
        moved = old is None or list_row_fingerprint(old) != list_row_fingerprint(new)
        if sid:
            list_changed[sid] = moved
        if moved:
            upserts.append(new)
    for new in appended:
        sid = str(new.get("sessionId") or "").strip()
        if sid:
            list_changed[sid] = True
        upserts.append(new)
    removed_ids = [
        str(row.get("sessionId") or "").strip()
        for row in current
        if str(row.get("path") or "").strip() in drop
    ]
    for sid in removed_ids:
        if sid:
            list_changed[sid] = True
    return upserts, removed_ids, list_changed


def _watch_session_hidden(session_dir: Path, child_ids: set[str]) -> bool:
    """True when a filesystem-watch hit is a harness child, not a catalog row."""
    return is_subagent_session_dir(session_dir) or session_dir.name in child_ids


def catalog_roots_fingerprint(
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> tuple[tuple[str, int], ...]:
    """Cheap identity for catalog roots (path, mtime_ns).

    Directory mtime changes when children are added or removed. In-place file
    writes inside a session dir do not bump the root; those use FS-watch
    :meth:`SessionCatalogCache.refresh_rows` instead of a full rescan.
    """
    roots = catalog_scan_roots(
        traces_path=traces_path,
        include_host=include_host,
        host_root=host_root,
    )
    parts: list[tuple[str, int]] = []
    for root in roots:
        path = Path(root.path)
        try:
            st = path.stat()
            mtime_ns = int(st.st_mtime_ns)
        except OSError:
            parts.append((str(path), 0))
            continue
        parts.append((str(path), mtime_ns))
    return tuple(parts)


@dataclass
class _CatalogDelta:
    """One catalog revision: upserted rows and removed session ids."""

    revision: int
    upserted: dict[str, JsonObject] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)


def _hint_match(name: str, hints: tuple[str, ...]) -> bool:
    if name in hints:
        return True
    return any(hint.startswith(".") and name.endswith(hint) for hint in hints)


def _file_store_event(store: Path, events: list[Path], hints: tuple[str, ...]) -> bool:
    """True when a watch path is *store* or a hint sibling (``*.db-wal``)."""
    store_s = str(store)
    parent = store.parent
    for ev in events:
        if str(ev) == store_s or ev.name == store.name:
            return True
        if ev.parent == parent and _hint_match(ev.name, hints):
            return True
    return False


def _dir_store_event(store: Path, events: list[Path], hints: tuple[str, ...]) -> bool:
    """True when a watch path is a matching file under a directory store."""
    try:
        root = store.expanduser().resolve()
    except OSError:
        root = store.expanduser()
    for ev in events:
        try:
            ev.resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        if _hint_match(ev.name, hints):
            return True
    return False


class SessionCatalogCache:
    """Single-flight TTL + root-fingerprint cache for ``session/list`` rows.

    Shared by the headless control owner so warm-on-start, periodic refresh, and
    client RPCs share one scan instead of serial full walks.
    """

    DEFAULT_TTL = 300.0
    GET_WAIT_S = 120.0
    _DELTA_KEEP = 48

    def __init__(
        self,
        *,
        traces_path: Path | None = None,
        include_host: bool | None = None,
        host_root: Path | None = None,
        ttl: float = DEFAULT_TTL,
    ) -> None:
        import secrets
        import threading
        import time

        self._traces_path = Path(traces_path).expanduser() if traces_path is not None else None
        self._include_host = include_host
        self._host_root = host_root
        self._ttl = max(1.0, float(ttl))
        self._lock = threading.Lock()
        self._rows: list[JsonObject] | None = None
        self._rows_seeded = False
        self._locator_index: dict[str, str] = {}
        self._mono = 0.0
        self._host_key: bool | None = None
        self._fingerprint: tuple[tuple[str, int], ...] | None = None
        self._building = False
        self._build_done = threading.Event()
        self._build_done.set()
        self._ref_stamps: dict[str, tuple[str, int, int, int]] = {}
        self._time = time
        # High 31 bits identify this owner instance so a restarted serve cannot
        # treat a client's leftover sinceRevision as "unchanged".
        self._gen = secrets.randbits(31)
        self._seq = 0
        self._revision = 0
        self._deltas: deque[_CatalogDelta] = deque(maxlen=self._DELTA_KEEP)
        self._on_rebuilt: object | None = None

    def __call__(self) -> list[JsonObject]:
        """Return the warm catalog snapshot (``SessionLister``)."""
        return self.get()

    @property
    def revision(self) -> int:
        """Monotonic catalog revision; bumps on full rebuild or row patch."""
        with self._lock:
            return int(self._revision)

    @property
    def building(self) -> bool:
        """True while a catalog rebuild thread is running."""
        with self._lock:
            return bool(self._building)

    def _host_key_now(self) -> bool:
        return effective_include_host(self._include_host)

    def _fp_now(self) -> tuple[tuple[str, int], ...]:
        return catalog_roots_fingerprint(
            traces_path=self._traces_path,
            include_host=self._include_host,
            host_root=self._host_root,
        )

    def _bump_locked(
        self,
        *,
        upserted: list[JsonObject] | None = None,
        removed: list[str] | None = None,
        clear_deltas: bool = False,
    ) -> int:
        self._seq += 1
        self._revision = (int(self._gen) << 32) | int(self._seq)
        if clear_deltas:
            self._deltas.clear()
        else:
            by_id: dict[str, JsonObject] = {}
            for row in upserted or []:
                sid = str(row.get("sessionId") or "").strip()
                if sid:
                    by_id[sid] = row
            self._deltas.append(
                _CatalogDelta(
                    revision=self._revision,
                    upserted=by_id,
                    removed=[sid for sid in (removed or []) if sid],
                )
            )
        return self._revision

    def delta_since(self, since_revision: int) -> tuple[list[JsonObject], list[str]] | None:
        """Rows upserted and ids removed after *since_revision*, or None if gapped."""
        with self._lock:
            rev = self._revision
            if since_revision <= 0:
                return None
            if (int(since_revision) >> 32) != int(self._gen):
                return None
            if since_revision > rev:
                return None
            if since_revision == rev:
                return [], []
            if not self._deltas or self._deltas[0].revision > since_revision + 1:
                return None
            upserted: dict[str, JsonObject] = {}
            removed: set[str] = set()
            for delta in self._deltas:
                if delta.revision <= since_revision:
                    continue
                for sid in delta.removed:
                    removed.add(sid)
                    upserted.pop(sid, None)
                for sid, row in delta.upserted.items():
                    removed.discard(sid)
                    upserted[sid] = row
            return list(upserted.values()), list(removed)

    def _install_rows_locked(self, rows: list[JsonObject] | None) -> None:
        self._rows = rows
        self._locator_index = locator_index_from_rows(rows or [])

    def invalidate(self) -> None:
        """Drop cached rows so the next :meth:`get` rebuilds."""
        with self._lock:
            self._install_rows_locked(None)
            self._rows_seeded = False
            self._mono = 0.0
            self._fingerprint = None
            self._deltas.clear()

    def _is_fresh_locked(
        self,
        *,
        force: bool,
        host_key: bool,
        fp: tuple[tuple[str, int], ...],
        now: float,
    ) -> bool:
        return (
            not force
            and self._rows is not None
            and self._host_key is host_key
            and self._fingerprint == fp
            and (now - self._mono) < self._ttl
        )

    def _seed_from_snapshots(self) -> None:
        """Install on-disk snapshot rows when the in-memory cache is empty."""
        with self._lock:
            if self._rows is not None:
                return
        roots = catalog_scan_roots(
            traces_path=self._traces_path,
            include_host=self._include_host,
            host_root=self._host_root,
        )
        rows: list[JsonObject] = []
        seen: set[str] = set()
        for root in roots:
            dest = default_catalog_snapshot(root.path)
            for row in read_catalog_snapshot_rows(dest):
                sid = str(row.get("sessionId") or "").strip()
                path = str(row.get("path") or "").strip()
                key = sid or path
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                rows.append(row)
        if not rows:
            return
        rows.sort(
            key=lambda r: (
                -catalog_row_sort_epoch(r),
                str(r.get("sessionId") or ""),
            )
        )
        with self._lock:
            if self._rows is not None:
                return
            self._install_rows_locked(rows)
            self._rows_seeded = True

    def _kick_rebuild(self, *, force: bool = False) -> None:
        """Start a single-flight rebuild if the snapshot is missing or stale."""
        import threading

        host_key = self._host_key_now()
        fp = self._fp_now()
        now = self._time.monotonic()
        with self._lock:
            if self._is_fresh_locked(force=force, host_key=host_key, fp=fp, now=now):
                return
            if self._building:
                return
            self._building = True
            self._build_done.clear()
        worker = threading.Thread(
            target=self._run_rebuild,
            args=(host_key, fp),
            name="anqa-catalog-rebuild",
            daemon=True,
        )
        worker.start()

    def _run_rebuild(
        self,
        host_key: bool,
        fp: tuple[tuple[str, int], ...],
    ) -> None:
        try:
            rows = list_session_catalog(
                traces_path=self._traces_path,
                include_host=self._include_host,
                host_root=self._host_root,
            )
            with self._lock:
                prev_ids = (
                    {str(row.get("sessionId") or "").strip() for row in self._rows}
                    if self._rows is not None and not self._rows_seeded
                    else None
                )
                self._install_rows_locked(rows)
                self._rows_seeded = False
                self._mono = self._time.monotonic()
                self._host_key = host_key
                self._fingerprint = fp
                self._bump_locked(clear_deltas=True)
            new_ids = {str(row.get("sessionId") or "").strip() for row in rows}
            cb = self._on_rebuilt
            if callable(cb) and (prev_ids is None or prev_ids != new_ids):
                cb()
        finally:
            with self._lock:
                self._building = False
            self._build_done.set()

    def get(self, *, force: bool = False) -> list[JsonObject]:
        """Return catalog rows, rebuilding when stale, forced, or roots changed.

        Callers that must not stall (``session/list``) use :meth:`list_for_rpc`.
        Seeds from :func:`default_catalog_snapshot` files before waiting so a
        cold owner does not return empty while a long rebuild is still running.
        """
        self._seed_from_snapshots()
        self._kick_rebuild(force=force)
        deadline = self._time.monotonic() + self.GET_WAIT_S
        while self._time.monotonic() < deadline:
            with self._lock:
                if not self._building:
                    if self._rows is not None:
                        return list(self._rows)
                    break
            remaining = deadline - self._time.monotonic()
            if remaining <= 0:
                break
            self._build_done.wait(timeout=min(0.25, remaining))
        with self._lock:
            return list(self._rows or [])

    def resolve(self, reference: str) -> Path | None:
        """Map a session id or path to a directory using the warm snapshot.

        Does not wait for a rebuild and does not load session meta. Missing
        cache or unknown id returns None so callers can fall back to a
        name-only directory walk.
        """
        ref = (reference or "").strip()
        if not ref:
            return None
        candidate = Path(ref).expanduser()
        if candidate.is_dir() or candidate.is_file():
            try:
                return candidate.resolve()
            except OSError:
                return candidate
        with self._lock:
            loc_raw = self._locator_index.get(ref, "")
        if loc_raw:
            path = Path(loc_raw)
            if path.is_dir() or path.is_file():
                try:
                    return path.resolve()
                except OSError:
                    return path
        return None

    def refresh_rows(self, session_dirs: list[Path]) -> tuple[list[JsonObject], dict[str, bool]]:
        """Rebuild catalog rows for *session_dirs* without a full tree scan.

        Used on filesystem watches so a live ``updates.jsonl`` write updates
        that session's status immediately. Missing dirs are dropped; new dirs
        are appended. Falls back to a full :meth:`get` when the cache is empty.

        :param session_dirs: Session directories that changed.
        :returns: Updated catalog snapshot (newest-first) and a map of
            session id → whether painted list fields changed.
        """
        dirs = [Path(p).expanduser() for p in session_dirs if str(p).strip()]
        if not dirs:
            return self.get(), {}
        with self._lock:
            if self._building or self._rows is None:
                current = None
                snap_rev = -1
            else:
                current = list(self._rows)
                snap_rev = self._revision
        if current is None:
            return self.get(force=True), {}
        known_paths = {str(row.get("path") or "").strip() for row in current}
        locators = [Path(raw) for row in current if (raw := str(row.get("locator") or "").strip())]
        child_ids = nested_child_ids([*locators, *dirs])
        drop: set[str] = set()
        replacements: dict[str, JsonObject] = {}
        appended: list[JsonObject] = []
        for session_dir in dirs:
            try:
                resolved = str(session_dir.resolve())
            except OSError:
                resolved = str(session_dir)
            if _watch_session_hidden(session_dir, child_ids):
                drop.add(resolved)
                drop.add(str(session_dir))
                drop.add(session_dir.name)
                continue
            row = session_catalog_row(session_dir)
            if row is None:
                drop.add(resolved)
                drop.add(str(session_dir))
                drop.add(session_dir.name)
                continue
            path_key = str(row.get("path") or resolved).strip()
            if path_key in known_paths:
                replacements[path_key] = row
            else:
                appended.append(row)
                known_paths.add(path_key)
        upserts, removed_ids, list_changed = list_refresh_delta(
            current, replacements, appended, drop
        )
        rows = [
            replacements.get(str(row.get("path") or "").strip(), row)
            for row in current
            if str(row.get("path") or "").strip() not in drop
            and str(row.get("locator") or "").strip() not in drop
            and str(row.get("sessionId") or "").strip() not in drop
        ]
        rows.extend(appended)
        rows.sort(
            key=lambda r: (
                -catalog_row_sort_epoch(r),
                str(r.get("sessionId") or ""),
            )
        )
        with self._lock:
            if self._building or self._revision != snap_rev:
                return list(self._rows or rows), list_changed
            self._install_rows_locked(rows)
            self._mono = self._time.monotonic()
            if upserts or removed_ids:
                self._bump_locked(upserted=upserts, removed=removed_ids)
        return list(rows), list_changed

    def refresh_file_store(self, store_paths: list[Path]) -> dict[str, bool]:
        """Remeta file/database locators whose stamp moved. Leaves others."""
        from ..harness.registry import adapter_host_roots, enabled_host_adapters
        from .mtime_export import ref_source_stamp

        events = [Path(p).expanduser() for p in store_paths]
        list_changed: dict[str, bool] = {}
        with self._lock:
            if self._building or self._rows is None:
                return {}
            current = list(self._rows)
            snap_rev = self._revision
        replacements: dict[str, JsonObject] = {}
        appended: list[JsonObject] = []
        drop: set[str] = set()
        for item in enabled_host_adapters():
            roots = [Path(raw).expanduser() for raw in adapter_host_roots(item)]
            files = [root for root in roots if root.is_file()]
            dirs = [root for root in roots if root.is_dir()]
            hints = item.watch_hints()
            hit_files = any(_file_store_event(root, events, hints) for root in files)
            hit_dirs = any(_dir_store_event(root, events, hints) for root in dirs)
            if not hit_files and not hit_dirs:
                continue
            scan = files if hit_files and not hit_dirs else roots
            for ref in item.discover(scan):
                stamp = ref_source_stamp(ref)
                sid = ref.session_id
                if self._ref_stamps.get(sid) == stamp:
                    continue
                self._ref_stamps[sid] = stamp
                row = catalog_row_for_ref(ref)
                if row is None:
                    for old in current:
                        if str(old.get("sessionId") or "") == sid:
                            drop.add(str(old.get("path") or sid))
                            break
                    else:
                        drop.add(sid)
                    continue
                prior = next(
                    (
                        str(old.get("path") or "")
                        for old in current
                        if str(old.get("sessionId") or "") == sid
                    ),
                    "",
                )
                if prior:
                    replacements[prior] = row
                else:
                    appended.append(row)
        if not replacements and not appended and not drop:
            return {}
        upserts, removed_ids, list_changed = list_refresh_delta(
            current, replacements, appended, drop
        )
        rows = [
            replacements.get(str(row.get("path") or "").strip(), row)
            for row in current
            if str(row.get("sessionId") or "").strip() not in drop
            and str(row.get("path") or "").strip() not in drop
        ]
        rows.extend(appended)
        rows.sort(
            key=lambda r: (
                -catalog_row_sort_epoch(r),
                str(r.get("sessionId") or ""),
            )
        )
        with self._lock:
            if self._building or self._revision != snap_rev:
                return list_changed
            self._install_rows_locked(rows)
            self._mono = self._time.monotonic()
            if upserts or removed_ids:
                self._bump_locked(upserted=upserts, removed=removed_ids)
        return list_changed

    def drop_subagent_rows(self) -> list[JsonObject]:
        """Remove harness child sessions from the warm snapshot.

        Full scans already omit these. A filesystem watch can still append a
        sibling mirror (``session_kind: subagent``, or a basename listed under
        a parent's ``subagents/``). The owner warm loop calls this so those
        rows leave ``session/list`` without a full tree walk.
        """
        with self._lock:
            if self._building or self._rows is None:
                return list(self._rows or [])
            current = list(self._rows)
            snap_rev = self._revision
        locators = [
            Path(raw)
            for row in current
            if (raw := str(row.get("locator") or row.get("path") or "").strip())
            and Path(raw).exists()
        ]
        kept = {str(path.resolve()) for path in drop_subagent_sessions(locators)}

        def _kept(row: JsonObject) -> bool:
            raw = str(row.get("locator") or "").strip()
            if not raw:
                return True
            try:
                return str(Path(raw).resolve()) in kept
            except OSError:
                return raw in kept

        rows = [row for row in current if _kept(row)]
        if len(rows) == len(current):
            return rows
        removed_ids = [str(row.get("sessionId") or "").strip() for row in current if not _kept(row)]
        with self._lock:
            if self._building or self._revision != snap_rev:
                return list(self._rows or rows)
            self._install_rows_locked(rows)
            self._mono = self._time.monotonic()
            self._bump_locked(removed=removed_ids)
        return list(rows)

    def list_for_rpc(
        self,
        *,
        query: str = "",
        limit: int | None = None,
        offset: int = 0,
        since_revision: int | None = None,
    ) -> JsonObject:
        """Page or delta ``session/list`` from the current snapshot.

        Never waits for a cold full-tree scan. When the cache is empty or
        stale, on-disk :func:`default_catalog_snapshot` rows are installed
        first, a background rebuild is started, and this call returns the
        current rows with ``incomplete`` / ``building`` set.

        When *since_revision* matches :attr:`revision`, no rows are transferred.
        When the client is one or more tracked revisions behind, return only
        upserted/removed rows (``delta`` true). Older clients omit
        *since_revision* and get the usual paged snapshot.
        """
        from .access import filter_session_catalog

        self._seed_from_snapshots()
        self._kick_rebuild(force=False)
        with self._lock:
            rows = list(self._rows) if self._rows is not None else []
            rev = int(self._revision)
            building = bool(self._building)
            incomplete = self._rows is None or building
        if since_revision is not None and int(since_revision) > 0:
            if int(since_revision) == rev:
                full = filter_session_catalog(rows, query=query, limit=0)
                return {
                    "sessions": [],
                    "total": full["total"],
                    "matched": full["matched"],
                    "revision": rev,
                    "unchanged": True,
                    "removed": [],
                    "delta": True,
                    "building": building,
                    "incomplete": incomplete,
                }
            delta = self.delta_since(int(since_revision))
            if delta is not None:
                upserted, removed = delta
                page = filter_session_catalog(
                    upserted,
                    query=query,
                    limit=max(len(upserted), 1),
                    offset=0,
                )
                full = filter_session_catalog(rows, query=query, limit=0)
                removed_vals: list[JsonValue] = [sid for sid in removed]
                return {
                    "sessions": page["sessions"],
                    "total": full["total"],
                    "matched": full["matched"],
                    "revision": rev,
                    "unchanged": False,
                    "removed": removed_vals,
                    "delta": True,
                    "building": building,
                    "incomplete": incomplete,
                }
        out = filter_session_catalog(rows, query=query, limit=limit, offset=offset)
        return {
            "sessions": out["sessions"],
            "total": out["total"],
            "matched": out["matched"],
            "revision": rev,
            "unchanged": False,
            "removed": [],
            "delta": False,
            "building": building,
            "incomplete": incomplete,
        }


def session_meta_from_catalog_row(row: JsonObject) -> SessionMeta | None:
    """Hydrate a minimal :class:`~anqa.models.SessionMeta` from a list wire row.

    Used when the TUI attaches as a control client and must not re-scan disk for
    the home list. Status strings map back to outcomes so
    :meth:`~anqa.models.SessionMeta.list_status_label` stays consistent.
    """
    path_raw = str(row.get("path") or "").strip()
    loc_raw = str(row.get("locator") or "").strip()
    sid = str(row.get("sessionId") or "").strip()
    if not path_raw and not loc_raw and not sid:
        return None
    if loc_raw:
        session_dir = Path(loc_raw)
    elif path_raw and (Path(path_raw).exists() or parse_session_ref_string(path_raw) is not None):
        session_dir = Path(path_raw)
    else:
        session_dir = Path(sid)
    harness = str(row.get("harness") or "").strip()
    meta = SessionMeta(
        session_id=sid or session_dir.name,
        session_dir=session_dir,
        harness=harness,
        harness_version=str(row.get("harnessVersion") or "").strip(),
    )
    title = str(row.get("title") or "").strip()
    if title:
        meta.title = title
    model = str(row.get("model") or "").strip()
    if model:
        if ":" in model:
            mid, _, eff = model.partition(":")
            meta.model_id = mid or "unknown"
            meta.reasoning_effort = eff
        else:
            meta.model_id = model
    from ..models import ListStatus

    outcome = str(row.get("outcome") or "").strip()
    status = str(row.get("status") or "").strip().lower()
    if outcome:
        meta.turn_outcome = outcome
    elif status:
        if status in {"—", "-", "–"}:
            status = ListStatus.IDLE
        meta.turn_outcome = ListStatus.from_token(status)
    task_id = str(row.get("taskId") or "").strip()
    if task_id:
        meta.task_id = task_id
    origin = str(row.get("origin") or "").strip()
    if origin:
        meta.origin = origin
    elif row.get("imported"):
        meta.origin = "import"
    created = str(row.get("createdAt") or row.get("created_at") or "").strip()
    if created:
        meta.created_at = created
    updated = str(row.get("updatedAt") or row.get("updated_at") or "").strip()
    if updated:
        meta.updated_at = updated

    meta.duration_seconds = json_count_float(row.get("durationSeconds"))
    meta.num_events = json_count(row.get("numEvents"))
    meta.tool_call_count = json_count(row.get("toolCallCount"))
    meta.turn_count = json_count(row.get("turnCount"))
    meta.error_count = json_count(row.get("errorCount"))
    apply_catalog_presence_row(meta, row)
    git_repo = str(row.get("gitRepo") or "").strip()
    if git_repo:
        meta.git_repo = git_repo
    run_dir = str(row.get("runDir") or "").strip()
    if run_dir:
        meta.run_dir = run_dir

    pct = json_count_or_none(row.get("contextWindowUsagePct"))
    if pct is not None:
        meta.context_window_usage_pct = max(0, pct)
    used = json_count_or_none(row.get("contextTokensUsed"))
    if used is not None:
        meta.context_tokens_used = max(0, used)
    window = json_count_or_none(row.get("contextWindowTokens"))
    if window is not None and window > 0:
        meta.context_window_tokens = window
    return meta


def _adapter_host_catalog_rows(
    *,
    host_catalog_cache: Path | None = None,
) -> list[JsonObject]:
    """Host rows whose locators are files or database rows.

    Directory locators come from the session-directory walk. File and
    database locators go through ``discover`` +
    :func:`catalog_row_for_ref` here.
    """
    from ..harness.registry import adapter_host_roots, enabled_host_adapters

    rows: list[JsonObject] = []
    for item in enabled_host_adapters():
        roots = adapter_host_roots(item)
        from ..paths import imports_dir

        scan = list(roots)
        imported_root = imports_dir(create=False) / item.id
        if imported_root.is_dir():
            scan.append(imported_root)
        refs = [ref for ref in item.discover(scan) if not Path(ref.locator).is_dir()]
        if not refs:
            continue
        dest_root = Path(roots[0]).expanduser() if roots else Path(item.id)
        dest = (
            host_catalog_cache / item.id
            if host_catalog_cache is not None and host_catalog_cache.is_dir()
            else host_catalog_cache
            if host_catalog_cache is not None
            else default_catalog_snapshot(dest_root)
        )
        rows.extend(
            load_or_rebuild_refs(
                refs,
                dest=dest,
                build_row=catalog_row_for_ref,
                root=dest_root,
            )
        )
    return rows


def resolve_session_reference(
    reference: str,
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> Path | None:
    """Resolve a path or catalog session id to an existing session directory.

    :param reference: Absolute/relative path, or a session directory name / id.
    :param traces_path: Optional store path override.
    :param include_host: Host inclusion (True/False force; None includes host).
    :param host_root: Optional host root override.
    :returns: Resolved directory path, or None when not found.
    """
    ref = (reference or "").strip()
    if not ref:
        return None
    candidate = Path(ref).expanduser()
    if candidate.is_dir():
        try:
            return candidate.resolve()
        except OSError:
            return candidate
    roots = catalog_scan_roots(
        traces_path=traces_path,
        include_host=include_host,
        host_root=host_root,
    )
    for root in roots:
        direct = root.path / ref
        if direct.is_dir():
            try:
                return direct.resolve()
            except OSError:
                return direct
    # Directory name only. List-meta for every sibling is a multi-second tax on
    # each session/overview and session/timeline call. Id≠dirname uses the
    # warm catalog on the control owner (SessionCatalogCache.resolve).
    for session_dir in collect_session_dirs(roots):
        if session_dir.name == ref:
            try:
                return session_dir.resolve()
            except OSError:
                return session_dir
    return None


__all__ = [
    "SessionCatalogCache",
    "catalog_roots_fingerprint",
    "catalog_scan_roots",
    "effective_include_host",
    "list_session_catalog",
    "resolve_session_reference",
    "catalog_row_for_ref",
    "public_catalog_row",
    "session_catalog_row",
    "catalog_row_sort_epoch",
    "session_meta_from_catalog_row",
]
