"""Operator session tags: overlay file, identity, vocabulary, elision."""

from __future__ import annotations

from pathlib import Path

from anqa.harness.ref import SessionRef
from anqa.paths import app_home
from anqa.tags import (
    TAGS_FILENAME,
    format_tags_line,
    load_tags,
    load_vocabulary,
    merge_vocabulary,
    parse_tag,
    save_tags,
    shared_tags,
    tags_path,
)


def _ref(session_id: str = "sess-1") -> SessionRef:
    return SessionRef(harness="grok", session_id=session_id, locator=Path("/tmp/unused"))


def test_missing_file_is_empty() -> None:
    from anqa.tags import tags_source_mtime_ns

    assert load_tags(_ref()) == []
    assert not tags_path(_ref()).is_file()
    assert tags_source_mtime_ns(_ref()) == 0


def test_save_roundtrip_uses_vocabulary_spelling() -> None:
    ref = _ref()
    stored = save_tags(ref, ["Review", "ui"], vocabulary=["review"])
    assert stored == ["review", "ui"]
    assert load_tags(ref) == ["review", "ui"]
    text = tags_path(ref).read_text(encoding="utf-8")
    assert "review" in text
    assert (app_home() / "notes" / "grok" / "sess-1" / TAGS_FILENAME).is_file()


def test_empty_save_removes_file() -> None:
    ref = _ref()
    save_tags(ref, ["review"])
    assert tags_path(ref).is_file()
    assert save_tags(ref, []) == []
    assert not tags_path(ref).is_file()
    assert load_tags(ref) == []


def test_corrupt_file_is_empty() -> None:
    ref = _ref()
    path = tags_path(ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not toml {{{", encoding="utf-8")
    assert load_tags(ref) == []


def test_parse_tag_rejects_empty_and_illegal_shape() -> None:
    assert parse_tag("") is None
    assert parse_tag("   ") is None
    assert parse_tag("has space") is None
    assert parse_tag("under_score") is None
    assert parse_tag("ok-tag") == "ok-tag"
    assert parse_tag("v2") == "v2"
    assert parse_tag("Review", vocabulary=["review"]) == "review"


def test_duplicates_collapse_casefold() -> None:
    ref = _ref()
    assert save_tags(ref, ["ui", "UI", "ui"]) == ["ui"]


def test_vocabulary_union_keeps_first_spelling() -> None:
    assert merge_vocabulary(["Review", "ui"], ["review", "bug"]) == ["Review", "ui", "bug"]


def test_shared_tags_is_intersection() -> None:
    assert shared_tags([["review", "ui"], ["review", "bug"], ["Review"]]) == ["review"]
    assert shared_tags([]) == []
    assert shared_tags([["a"], ["b"]]) == []


def test_vocabulary_is_union_of_stored_files() -> None:
    save_tags(_ref("a"), ["Review"])
    save_tags(_ref("b"), ["review", "ui"])
    assert load_vocabulary() == ["Review", "ui"]


def test_format_tags_line_elides() -> None:
    assert format_tags_line(["review", "ui"], max_width=20) == "review, ui"
    assert format_tags_line([], max_width=20) == ""
    line = format_tags_line(["review", "tooling", "parser"], max_width=12)
    assert line.endswith("…")
    assert len(line) <= 12
