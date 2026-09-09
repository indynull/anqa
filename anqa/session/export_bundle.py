"""Export a session via an :class:`~anqa.session.export_spec.ExportSpec`.

Default profile ``archive-full`` writes under ``~/.anqa/reports/`` as
``.tar.gz`` (or a directory when packaging is ``dir``).

Selected units (only written when the data exists)::

    session.tar.gz      # session directory files (``<id>/…``)
    notes/              # operator_notes.toml from the notes store
    README.txt
    manifest.json

Profiles: built-ins plus ``~/.anqa/export_profiles/*.yaml``. See
:mod:`anqa.session.export_spec`.
"""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..harness.ref import SessionRef, parse_session_ref_string
from ..harness.registry import ref_from_path, require_adapter, resolve_session_ref
from ..models import JsonObject, as_json_object
from ..notes import collect_notes_for_export
from ..paths import reports_dir
from .export_render import (
    SessionSummaryData,
    report_file_extension,
    session_summary_body,
)
from .export_spec import (
    DEFAULT_PROFILE_ID,
    ExportSpec,
    IncludeUnit,
    Packaging,
    get_export_profile,
)
from .subagents import subagent_runs_for_session
from .turns import event_display_turn_map, segment_timeline_turns

logger = logging.getLogger(__name__)

# Nested member: adapter session archive inside the operator export bundle.
SESSION_ARCHIVE_NAME = "session.tar.gz"

# Manifest schema for this outer bundle layout (bump when fields/layout change).
_MANIFEST_SCHEMA = 10


@dataclass
class ExportBundleResult:
    """Outcome of :func:`export_session_bundle`."""

    path: Path
    session_id: str
    arcnames: list[str] = field(default_factory=list)
    profile_id: str = DEFAULT_PROFILE_ID
    packaging: str = Packaging.TAR_GZ.value


def default_bundle_path(
    session_id: str,
    *,
    dest_dir: Path | None = None,
    packaging: Packaging = Packaging.TAR_GZ,
) -> Path:
    """Default outer path under reports dir for *packaging*."""
    root = Path(dest_dir) if dest_dir is not None else reports_dir()
    root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (session_id or "session"))
    base = f"{safe}-{ts}"
    if packaging is Packaging.DIR:
        return root / base
    return root / f"{base}.tar.gz"


def _export_ref(session: Path | str | SessionRef) -> SessionRef:
    """Resolve a catalog path, harness:id, or directory to a session ref."""
    if isinstance(session, SessionRef):
        return session
    raw = str(session).strip()
    found = resolve_session_ref(raw)
    if found is not None:
        return found
    path = Path(session).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    if path.is_dir():
        bound = ref_from_path(path)
        if bound is not None:
            return bound
    raise FileNotFoundError(f"session not found: {session}")


def _session_id(session_dir: Path | str | SessionRef) -> str:
    if isinstance(session_dir, SessionRef):
        return session_dir.session_id
    parsed = parse_session_ref_string(str(session_dir))
    if parsed is not None:
        return parsed[1]
    return Path(session_dir).name.strip() or "session"


def build_session_archive(session_dir: Path | str | SessionRef, out_tar: Path) -> list[str]:
    """Ask the session's harness adapter to write the native archive.

    :raises RuntimeError: No adapter, empty archive, or write failed.
    """
    ref = _export_ref(session_dir)
    item = require_adapter(ref)
    members = item.write_archive(ref, out_tar)
    if not Path(out_tar).is_file() or Path(out_tar).stat().st_size <= 0:
        raise RuntimeError(f"adapter wrote empty archive: {out_tar}")
    return members


def assert_session_archive_shape(trace_tar: Path, session_id: str) -> list[str]:
    """Validate *trace_tar* is a gzip archive rooted at ``<session_id>/``.

    :returns: Sorted member names inside the archive.
    :raises RuntimeError: Archive missing, empty, or not rooted at *session_id*.
    """
    sid = (session_id or "").strip() or "session"
    path = Path(trace_tar)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"session archive missing or empty: {path}")
    try:
        with tarfile.open(path, "r:gz") as tf:
            names = [m.name for m in tf.getmembers() if m.name]
    except tarfile.TarError as exc:
        raise RuntimeError(f"invalid session archive: {path}: {exc}") from exc

    prefix = f"{sid}/"
    if not any(n == sid or n.startswith(prefix) for n in names):
        raise RuntimeError(
            f"session archive must root under {sid}/ (got tops: "
            f"{sorted({n.split('/')[0] for n in names})[:8]})"
        )
    foreign = sorted(
        {n.split("/")[0] for n in names if n and n != sid and not n.startswith(prefix)}
    )
    if foreign:
        raise RuntimeError(f"session archive has unexpected top-level members: {foreign}")
    if not any(n.startswith(prefix) for n in names):
        raise RuntimeError(f"session archive is empty under {sid}/")
    return sorted(names)


def _add_tree(tf: tarfile.TarFile, src: Path, arc_prefix: str) -> list[str]:
    """Add *src* file or directory under *arc_prefix*; return arcnames."""
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


def _staging_member_paths(staging: Path, *, skip: frozenset[str] | set[str]) -> list[str]:
    """Relative paths under *staging* that will be packed (same rules as :func:`_add_tree`)."""
    names: list[str] = []
    for path in sorted(staging.iterdir()):
        if path.name in skip:
            continue
        if path.is_file():
            names.append(path.name)
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_symlink() or child.is_file():
                    rel = child.relative_to(staging).as_posix()
                    names.append(rel)
                elif child.is_dir() and not any(child.iterdir()):
                    rel = child.relative_to(staging).as_posix()
                    names.append(rel)
    return names


def _collect_operator_notes(session_dir: Path | str | SessionRef, staging: Path) -> None:
    """Embed turn-linked operator notes under ``notes/``."""
    from ..notes import NOTES_FILENAME

    ref = _export_ref(session_dir)
    dest = staging / "notes"
    collect_notes_for_export(ref.overlay_dir(), dest)
    if ref.locator.is_dir() and not (dest / NOTES_FILENAME).is_file():
        collect_notes_for_export(ref.locator, dest)
    from ..tags import collect_tags_for_export

    collect_tags_for_export(ref, dest)


def _gather_session_summary_data(session_dir: Path | str | SessionRef) -> SessionSummaryData:
    """Load meta / timeline / usage into :class:`SessionSummaryData`."""
    from ..utils import fmt_duration
    from .turns import segment_timeline_turns
    from .usage_stats import collect_session_usage, format_usage_markdown

    ref = _export_ref(session_dir)
    meta = require_adapter(ref).load_detail(ref)
    timeline = require_adapter(ref).parse_timeline(ref)
    tool_calls = [e for e in timeline if e.event_type == "tool_call"]
    tool_errs = sum(1 for e in tool_calls if e.is_error)
    turn_count = 0
    try:
        turn_count = len(segment_timeline_turns(timeline))
    except Exception:
        logger.debug("turn segmentation failed for export summary", exc_info=True)
    dur = ""
    if meta.duration_seconds:
        dur = fmt_duration(meta.duration_seconds)
    context = ""
    if meta.has_context_usage:
        context = (meta.context_usage_str or meta.context_usage_compact or "").strip()
    created = (meta.created_at or "").strip()
    if "T" in created and len(created) > 19:
        created = created[:19].replace("T", " ")
    usage_block = ""
    try:
        usage_src = ref.locator if ref.locator.is_dir() else ref.overlay_dir()
        usage = collect_session_usage(usage_src, timeline)
        usage_block = format_usage_markdown(usage).strip()
    except Exception:
        logger.debug("usage stats failed for export summary", exc_info=True)
    return SessionSummaryData(
        session_id=(meta.session_id or ref.session_id).strip(),
        title=(meta.title or "").strip(),
        model=(meta.model_display or meta.model_id or "").strip(),
        outcome=(meta.turn_outcome or "").strip(),
        duration_label=dur,
        summary_text=(meta.summary_text or "").strip(),
        event_count=len(timeline),
        tool_call_count=len(tool_calls),
        tool_error_count=tool_errs,
        turn_count=turn_count,
        context_label=context,
        task_id=(meta.task_id or "").strip(),
        run_id=(meta.run_id or "").strip(),
        git_repo=(meta.git_repo or "").strip(),
        git_branch=(meta.git_branch or "").strip(),
        created_at=created,
        usage_block=usage_block,
    )


def _collect_summary(session_dir: Path | str | SessionRef, staging: Path, *, renderer: str) -> None:
    """Write ``human/summary.<ext>`` via the active builtin renderer."""
    data = _gather_session_summary_data(session_dir)
    if not data.session_id and not data.summary_text and data.event_count == 0:
        return
    ext = report_file_extension(renderer)
    body = session_summary_body(data, renderer=renderer)
    dest_dir = staging / "human"
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / f"summary{ext}").write_text(body, encoding="utf-8")


def _write_readme(staging: Path, *, sid: str, spec: ExportSpec) -> None:
    units = ", ".join(sorted(u.value for u in spec.include)) or "(none)"
    text = (
        f"anqa session export\n"
        f"=====================\n\n"
        f"session_id: {sid}\n"
        f"profile: {spec.profile_id}\n"
        f"packaging: {spec.packaging.value}\n"
        f"include: {units}\n"
        f"renderer: {spec.renderer}\n\n"
        f"Contents (when selected and present)\n"
        f"------------------------------------\n"
        f"{SESSION_ARCHIVE_NAME}\n"
        f"                 Session directory files under {sid}/.\n"
        f"                 Host workspace/ and terminal/ are omitted.\n\n"
        f"human/summary.*  Session overview (meta, counts, usage) in the\n"
        f"                 profile renderer dialect (.md / .org / .txt).\n"
        f"notes/           operator_notes.toml when notes exist.\n"
        f"                 Same store file: source plus every field.\n"
        f"                 Schema: ~/.anqa/notes_schema.toml (not bundled).\n"
        f"children/<id>/{SESSION_ARCHIVE_NAME}\n"
        f"                 Session files of each openable child.\n"
        f"manifest.json    Inventory of this outer bundle.\n\n"
        f"To recover the nested session archive (when included)::\n"
        f"  tar -xzf <this-bundle>.tar.gz {SESSION_ARCHIVE_NAME}\n"
        f"  # or copy from a dir export\n"
    )
    (staging / "README.txt").write_text(text, encoding="utf-8")


def _collect_child_traces(session_dir: Path | str | SessionRef, staging: Path) -> list[JsonObject]:
    """Write ``children/<id>/session.tar.gz`` for each openable child."""
    ref = _export_ref(session_dir)
    timeline = require_adapter(ref).parse_timeline(ref)
    segs = segment_timeline_turns(timeline)
    runs = subagent_runs_for_session(ref.locator, timeline, segs, event_display_turn_map(segs))
    written: list[JsonObject] = []
    for run in runs:
        child_raw = str(run.child_path) if run.child_path is not None else ""
        if not child_raw and run.child_session_id:
            child_raw = f"{ref.harness}:{run.child_session_id}"
        if not run.openable or not child_raw:
            continue
        cid = run.child_session_id or run.subagent_id or _session_id(child_raw)
        dest = staging / "children" / cid / SESSION_ARCHIVE_NAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        build_session_archive(child_raw, dest)
        written.append(
            {
                "sessionId": cid,
                "member": f"children/{cid}/{SESSION_ARCHIVE_NAME}",
            }
        )
    return written


def _assert_outer_layout(arcnames: list[str], *, sid: str, want_session: bool) -> None:
    """Fail if the outer package drifts from the nested session-archive contract."""
    if want_session:
        if SESSION_ARCHIVE_NAME not in arcnames:
            raise RuntimeError(f"export bundle missing {SESSION_ARCHIVE_NAME}")
        nested_tars = [n for n in arcnames if n.endswith(".tar.gz")]
        if SESSION_ARCHIVE_NAME not in nested_tars:
            raise RuntimeError(f"export must embed {SESSION_ARCHIVE_NAME}")
        extra = [n for n in nested_tars if n != SESSION_ARCHIVE_NAME]
        prefix = "children/"
        suffix = f"/{SESSION_ARCHIVE_NAME}"
        bad = [n for n in extra if not (n.startswith(prefix) and n.endswith(suffix))]
        if bad:
            raise RuntimeError(f"unexpected nested archives: {bad}")
    if any(n == sid or n.startswith(f"{sid}/") for n in arcnames):
        raise RuntimeError(
            f"session files must live inside {SESSION_ARCHIVE_NAME}, not outer {sid}/"
        )


def _pack_tar_gz(staging: Path, out: Path) -> list[str]:
    """Write staging tree to *out* as ``.tar.gz``; return arcnames."""
    arcnames: list[str] = []
    tmp_out = staging / "bundle.tar.gz"
    with tarfile.open(tmp_out, "w:gz") as tf:
        for path in sorted(staging.iterdir()):
            if path.name == "bundle.tar.gz":
                continue
            if path.is_file():
                tf.add(path, arcname=path.name)
                arcnames.append(path.name)
            elif path.is_dir():
                arcnames.extend(_add_tree(tf, path, path.name))
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        if out.is_dir():
            shutil.rmtree(out)
        else:
            out.unlink()
    shutil.move(str(tmp_out), str(out))
    return arcnames


def _pack_dir(staging: Path, out: Path) -> list[str]:
    """Copy staging tree to *out* directory; return relative member paths."""
    out = Path(out)
    if out.exists():
        if out.is_dir() and any(out.iterdir()):
            raise RuntimeError(f"export directory not empty: {out}")
        if out.is_file():
            raise RuntimeError(f"export path is a file, expected directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    members = _staging_member_paths(staging, skip=frozenset())
    for name in members:
        src = staging / name
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            shutil.copy2(src, dest)
    return members


def export_session_bundle(
    session_dir: Path,
    *,
    dest: Path | None = None,
    spec: ExportSpec | None = None,
    profile: str | None = None,
) -> ExportBundleResult:
    """Build a session export from *session_dir* using an :class:`ExportSpec`.

    Default profile is :data:`~anqa.session.export_spec.DEFAULT_PROFILE_ID`
    (``archive-full``): nested ``session.tar.gz`` of the session files
    plus optional anqa extras.

    :param session_dir: Session directory, ``harness:id``, or :class:`SessionRef`.
    :param dest: Output path (``.tar.gz`` file or directory); default under
        :func:`~anqa.paths.reports_dir` according to packaging.
    :param spec: Explicit export recipe (wins over *profile*).
    :param profile: Profile id from built-ins / ``~/.anqa/export_profiles/``.
    :returns: :class:`ExportBundleResult` with the path written.
    :raises FileNotFoundError: Session missing.
    :raises KeyError: Unknown *profile*.
    :raises RuntimeError: Archive invalid, empty session, or destination
        conflicts.
    """
    ref = _export_ref(session_dir)
    resolved = spec if spec is not None else get_export_profile(profile)
    sid = ref.session_id
    out = Path(dest) if dest is not None else default_bundle_path(sid, packaging=resolved.packaging)
    if resolved.packaging is Packaging.TAR_GZ:
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)

    want_trace = resolved.includes(IncludeUnit.SESSION)
    nested_members: list[str] = []

    with tempfile.TemporaryDirectory(prefix="anqa-bundle-") as tmp:
        staging = Path(tmp)

        child_members: list[JsonObject] = []
        if want_trace:
            nested = staging / SESSION_ARCHIVE_NAME
            nested_members = build_session_archive(ref, nested)
            assert_session_archive_shape(nested, sid)
            child_members = _collect_child_traces(ref, staging)

        if resolved.includes(IncludeUnit.SUMMARY):
            try:
                _collect_summary(ref, staging, renderer=resolved.renderer)
            except OSError:
                logger.debug("session summary collect failed", exc_info=True)
            except Exception:
                logger.warning("session summary collect failed", exc_info=True)

        if resolved.includes(IncludeUnit.NOTES):
            try:
                _collect_operator_notes(ref, staging)
            except OSError:
                logger.debug("operator notes collect failed", exc_info=True)

        if resolved.includes(IncludeUnit.README):
            _write_readme(staging, sid=sid, spec=resolved)

        skip_pack = frozenset({"bundle.tar.gz"})
        want_manifest = resolved.includes(IncludeUnit.MANIFEST)
        if want_manifest:
            (staging / "manifest.json").write_text("{}\n", encoding="utf-8")

        members = _staging_member_paths(staging, skip=skip_pack)
        if want_trace and SESSION_ARCHIVE_NAME not in members:
            raise RuntimeError(f"export missing nested {SESSION_ARCHIVE_NAME}")

        if want_manifest:
            manifest: JsonObject = as_json_object(
                {
                    "schema": _MANIFEST_SCHEMA,
                    "kind": "anqa-session-export",
                    "harness": require_adapter(session_dir).id,
                    "session_id": sid,
                    "exported_at": datetime.now(UTC).isoformat(),
                    "profile": resolved.profile_id,
                    "packaging": resolved.packaging.value,
                    "include": sorted(u.value for u in resolved.include),
                    "renderer": resolved.renderer,
                    "renderer_options": resolved.renderer_options,
                    "session": SESSION_ARCHIVE_NAME if want_trace else None,
                    "session_members": nested_members if want_trace else [],
                    "children": child_members,
                    "members": members,
                }
            )
            (staging / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            members = _staging_member_paths(staging, skip=skip_pack)

        try:
            if resolved.packaging is Packaging.DIR:
                arcnames = _pack_dir(staging, out)
            else:
                arcnames = _pack_tar_gz(staging, out)
        except OSError as exc:
            raise RuntimeError(f"failed to write export bundle: {out}: {exc}") from exc

        _assert_outer_layout(arcnames, sid=sid, want_session=want_trace)

    return ExportBundleResult(
        path=out.resolve(),
        session_id=sid,
        arcnames=arcnames,
        profile_id=resolved.profile_id,
        packaging=resolved.packaging.value,
    )


__all__ = [
    "SESSION_ARCHIVE_NAME",
    "ExportBundleResult",
    "assert_session_archive_shape",
    "build_session_archive",
    "default_bundle_path",
    "export_session_bundle",
]
