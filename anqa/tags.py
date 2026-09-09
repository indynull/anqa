"""Operator session tags (overlay TOML next to notes).

Path: ``~/.anqa/notes/<harness>/<session_id>/tags.toml``.
A tag is letters, digits, and hyphen. Identity is case-insensitive.
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path

from .harness.ref import SessionRef
from .harness.registry import ref_from_path
from .paths import app_home

logger = logging.getLogger(__name__)

TAGS_FILENAME = "tags.toml"
TAG_TOKEN = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")


def parse_tag(raw: str, *, vocabulary: Sequence[str] = ()) -> str | None:
    """Return a stored tag spelling, or None when *raw* is not a token.

    :param raw: Operator input.
    :param vocabulary: Known spellings; a case-insensitive hit is reused.
    :returns: Canonical token, or None.
    """
    token = (raw or "").strip()
    if not token or not TAG_TOKEN.fullmatch(token):
        return None
    folded = token.casefold()
    for known in vocabulary:
        if known.casefold() == folded:
            return known
    return token


def parse_tags(values: Iterable[str], *, vocabulary: Sequence[str] = ()) -> list[str]:
    """Unique valid tags, vocabulary spelling preferred, order preserved."""
    out: list[str] = []
    seen: set[str] = set()
    vocab = list(vocabulary)
    for item in values:
        parsed = parse_tag(item, vocabulary=vocab)
        if parsed is None:
            continue
        key = parsed.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(parsed)
        if parsed not in vocab:
            vocab.append(parsed)
    return out


def merge_vocabulary(*groups: Sequence[str]) -> list[str]:
    """Union of tag groups; first spelling wins."""
    return parse_tags(tag for group in groups for tag in group)


def shared_tags(groups: Sequence[Sequence[str]]) -> list[str]:
    """Intersection of tag groups; first group's order and spelling."""
    if not groups:
        return []
    first = parse_tags(groups[0])
    if not first:
        return []
    rest = [{t.casefold() for t in parse_tags(group)} for group in groups[1:]]
    if not rest:
        return first
    return [tag for tag in first if all(tag.casefold() in other for other in rest)]


def format_tags_line(tags: Sequence[str], *, max_width: int) -> str:
    """Join tags with ``, `` and elide when they would exceed *max_width*."""
    if max_width <= 0:
        return ""
    kept: list[str] = []
    for tag in tags:
        trial = ", ".join([*kept, tag])
        if len(trial) <= max_width:
            kept.append(tag)
            continue
        if not kept:
            if max_width == 1:
                return "…"
            return f"{tag[: max_width - 1]}…"
        base = ", ".join(kept)
        if len(base) < max_width:
            return f"{base}…"
        if max_width == 1:
            return "…"
        return f"{base[: max_width - 1]}…"
    return ", ".join(kept)


def tags_path(session: SessionRef | Path | str) -> Path:
    """Overlay ``tags.toml`` for *session*."""
    ref = _as_ref(session)
    return app_home() / "notes" / ref.harness / ref.session_id / TAGS_FILENAME


def tags_source_mtime_ns(session: SessionRef | Path | str) -> int:
    """mtime of the overlay tags file, or 0 when it is missing."""
    try:
        path = tags_path(session)
    except FileNotFoundError:
        return 0
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return 0


def load_vocabulary() -> list[str]:
    """Union of tags still stored under the notes overlay."""
    root = app_home() / "notes"
    if not root.is_dir():
        return []
    groups: list[list[str]] = []
    for path in sorted(root.glob("*/*/" + TAGS_FILENAME)):
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue
        values = raw.get("tags")
        if isinstance(values, list):
            groups.append([str(item) for item in values])
    return merge_vocabulary(*groups)


def collect_tags_for_export(session: SessionRef | Path | str, dest_dir: Path) -> list[str]:
    """Copy ``tags.toml`` into *dest_dir* when the session has tags.

    :returns: Relative member paths written (may be empty).
    """
    tags = load_tags(session)
    if not tags:
        return []
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / TAGS_FILENAME
    target.write_text(tags_path(session).read_text(encoding="utf-8"), encoding="utf-8")
    return [f"notes/{TAGS_FILENAME}"]


def load_tags(session: SessionRef | Path | str) -> list[str]:
    """Tags stored for *session*. Missing or corrupt file is an empty set."""
    path = tags_path(session)
    if not path.is_file():
        return []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read tags %s: %s", path, exc)
        return []
    values = raw.get("tags")
    if not isinstance(values, list):
        logger.warning("Failed to read tags %s: tags is not a list", path)
        return []
    return parse_tags(str(item) for item in values)


def save_tags(
    session: SessionRef | Path | str,
    tags: Sequence[str],
    *,
    vocabulary: Sequence[str] = (),
) -> list[str]:
    """Write *tags* for *session*. Empty set deletes the file.

    :param session: Session ref, locator, or ``harness:id``.
    :param tags: Desired set (invalid tokens dropped).
    :param vocabulary: Preferred spellings.
    :returns: Stored list.
    """
    stored = parse_tags(tags, vocabulary=vocabulary)
    path = tags_path(session)
    if not stored:
        if path.is_file():
            path.unlink()
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    inner = ", ".join(json.dumps(tag) for tag in stored)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(f"tags = [{inner}]\n", encoding="utf-8")
    tmp.replace(path)
    return stored


def _as_ref(session: SessionRef | Path | str) -> SessionRef:
    if isinstance(session, SessionRef):
        return session
    if isinstance(session, str) and ":" in session:
        from .harness.ref import parse_session_ref_string

        parsed = parse_session_ref_string(session)
        if parsed is not None:
            harness, session_id = parsed
            return SessionRef(harness=harness, session_id=session_id, locator=Path(session))
    loc = Path(session)
    bound = ref_from_path(loc)
    if bound is None:
        raise FileNotFoundError(f"unknown session: {session}")
    return bound
