"""Tests for workspace diff extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from anqa.models import ToolInputBag, TraceEvent
from anqa.session.workspace_diff import (
    _snap_map,
    _unified_diff,
    doc_from_payload,
    format_diff_meta_line,
    load_workspace_diff,
    load_workspace_diff_doc,
    point_from_events,
    points_from_events,
)

# ── _snap_map ────────────────────────────────────────────────────────────


class TestSnapMap:
    def test_dict_value_format(self):
        block = {"f1": {"content": "hello", "path": "/src/f1.py"}}
        result = _snap_map(block)
        assert result == {"src/f1.py": "hello"}

    def test_string_value_format(self):
        block = {"/src/main.py": "print('hi')"}
        result = _snap_map(block)
        assert result == {"src/main.py": "print('hi')"}

    def test_none_input(self):
        assert _snap_map(None) == {}

    def test_dict_without_content_skipped(self):
        block = {"f": {"path": "/x.py"}}
        assert _snap_map(block) == {}


# ── _unified_diff ────────────────────────────────────────────────────────


class TestUnifiedDiff:
    def test_new_file(self):
        diff_text, added, removed = _unified_diff(None, "line1\nline2", "new.py")
        assert "--- /dev/null" in diff_text
        assert "+++ b/new.py" in diff_text
        assert added == 2
        assert removed == 0

    def test_deleted_file(self):
        diff_text, added, removed = _unified_diff("old content", None, "gone.py")
        assert "--- a/gone.py" in diff_text
        assert "+++ /dev/null" in diff_text
        assert added == 0
        assert removed == 1

    def test_no_change(self):
        diff_text, added, removed = _unified_diff("same", "same", "f.py")
        assert "(no textual diff for f.py)" in diff_text
        assert added == 0
        assert removed == 0


# ── format_diff_meta_line ────────────────────────────────────────────────


class TestFormatDiffMetaLine:
    def test_rewind_points_source(self):
        meta = {
            "source": "rewind_points",
            "files_changed": 3,
            "lines_added": 10,
            "lines_removed": 5,
        }
        assert format_diff_meta_line(meta) == "rewind: 3 files +10/-5"

    def test_search_replace_source(self):
        meta = {
            "source": "search_replace",
            "files_changed": 2,
            "lines_added": 4,
            "lines_removed": 1,
        }
        assert format_diff_meta_line(meta) == "~2 search_replace edits +4/-1"

    def test_no_source(self):
        assert format_diff_meta_line({"source": None}) == "no diff data"


# ── load_workspace_diff ──────────────────────────────────────────────────


class TestLoadWorkspaceDiff:
    def test_rewind_points_keeps_every_snapshot(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()
        first = {
            "prompt_index": 0,
            "file_snapshots": {"a.py": "one"},
            "after_snapshots": {"a.py": "two"},
        }
        second = {
            "prompt_index": 2,
            "created_at": "2026-08-15T12:00:00Z",
            "file_snapshots": {"a.py": "two", "b.py": "old"},
            "after_snapshots": {"a.py": "two", "b.py": "new"},
        }
        (sd / "rewind_points.jsonl").write_text(
            json.dumps(first) + "\n" + json.dumps(second) + "\n"
        )
        doc = load_workspace_diff_doc(sd)
        assert len(doc.points) == 2
        assert doc.points[0].prompt_index == 0
        assert [h.path for h in doc.points[0].files] == ["a.py"]
        assert doc.points[1].prompt_index == 2
        assert [h.path for h in doc.points[1].files] == ["b.py"]
        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "rewind_points"
        assert meta["files_changed"] == 1
        assert "b.py" in body
        assert "a.py" not in body

    def test_rewind_points_file_changes(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()
        rp = {
            "file_snapshots": {"app.py": "old line"},
            "after_snapshots": {
                "app.py": "new line",
                "added.py": "brand new",
            },
        }
        (sd / "rewind_points.jsonl").write_text(json.dumps(rp) + "\n")

        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "rewind_points"
        assert meta["files_changed"] == 2  # modified + added
        assert meta["lines_added"] > 0
        assert "app.py" in body
        assert "added.py" in body

    def test_rewind_points_deleted_file(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()
        rp = {
            "file_snapshots": {"gone.py": "will be removed"},
            "after_snapshots": {},
        }
        (sd / "rewind_points.jsonl").write_text(json.dumps(rp) + "\n")

        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "rewind_points"
        assert meta["files_changed"] == 1
        assert meta["lines_removed"] >= 1
        assert "/dev/null" in body

    def test_search_replace_fallback(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()
        update = {
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "title": "search_replace",
                    "rawInput": {
                        "file_path": "main.py",
                        "old_string": "foo",
                        "new_string": "bar",
                    },
                }
            }
        }
        (sd / "updates.jsonl").write_text(json.dumps(update) + "\n")

        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "search_replace"
        assert meta["files_changed"] == 1
        assert "main.py" in body
        doc = load_workspace_diff_doc(sd)
        assert len(doc.points) == 1
        assert doc.points[0].source == "search_replace"
        assert doc.points[0].files[0].path == "main.py"

    def test_empty_session(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()

        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None
        assert meta["files_changed"] == 0
        assert "No rewind snapshots" in body

    def test_load_workspace_diff_doc_skips_parse_when_timeline_passed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        sd = tmp_path / "session"
        sd.mkdir()
        (sd / "rewind_points.jsonl").write_text(
            json.dumps(
                {
                    "prompt_index": 0,
                    "file_snapshots": {"a.py": "old"},
                    "after_snapshots": {"a.py": "new"},
                }
            )
            + "\n"
        )

        def _boom(_path: Path) -> list:
            raise AssertionError("parse_timeline must not run when timeline is passed")

        monkeypatch.setattr("anqa.harness.grok.parse_timeline", _boom)
        doc = load_workspace_diff_doc(sd, timeline=[])
        assert len(doc.points) == 1
        assert doc.points[0].files[0].path == "a.py"

    def test_rewind_points_no_differences(self, tmp_path: Path):
        """Rewind snapshots with identical before/after → no content changes."""
        sd = tmp_path / "session"
        sd.mkdir()
        rp = {
            "file_snapshots": {"same.py": "content"},
            "after_snapshots": {"same.py": "content"},
        }
        (sd / "rewind_points.jsonl").write_text(json.dumps(rp) + "\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "rewind_points"
        assert meta["files_changed"] == 0
        assert "No content differences" in body

    def test_rewind_points_parse_error(self, tmp_path: Path):
        """Corrupt rewind_points.jsonl falls through to search_replace path."""
        sd = tmp_path / "session"
        sd.mkdir()
        (sd / "rewind_points.jsonl").write_text("not-json\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None

    def test_updates_jsonl_malformed_lines(self, tmp_path: Path):
        """Malformed JSONL lines in updates.jsonl are skipped."""
        sd = tmp_path / "session"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("not json\n{bad}\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None

    def test_search_replace_groups_edits_by_path(self, tmp_path: Path):
        sd = tmp_path / "session"
        sd.mkdir()
        updates = [
            {
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "title": "search_replace",
                        "rawInput": {
                            "file_path": "main.py",
                            "old_string": "a",
                            "new_string": "b",
                        },
                    }
                }
            },
            {
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "title": "search_replace",
                        "rawInput": {
                            "file_path": "main.py",
                            "old_string": "b",
                            "new_string": "c",
                        },
                    }
                }
            },
        ]
        (sd / "updates.jsonl").write_text("\n".join(json.dumps(u) for u in updates) + "\n")
        doc = load_workspace_diff_doc(sd)
        assert len(doc.points) == 1
        assert len(doc.points[0].files) == 1
        assert doc.points[0].files[0].path == "main.py"
        assert doc.points[0].files[0].kind == "edit"

    def test_search_replace_target_file_key(self, tmp_path: Path):
        """Fallback rawInput key 'target_file' works."""
        sd = tmp_path / "session"
        sd.mkdir()
        update = {
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "title": "search_replace",
                    "rawInput": {
                        "target_file": "alt.py",
                        "old_string": "old",
                        "new_string": "new",
                    },
                }
            }
        }
        (sd / "updates.jsonl").write_text(json.dumps(update) + "\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] == "search_replace"
        assert "alt.py" in body

    def test_non_search_replace_update_ignored(self, tmp_path: Path):
        """Updates that are not search_replace are ignored."""
        sd = tmp_path / "session"
        sd.mkdir()
        update = {
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "title": "read_file",
                    "rawInput": {"target_file": "x.py"},
                }
            }
        }
        (sd / "updates.jsonl").write_text(json.dumps(update) + "\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None

    # ── _unified_diff both sides present ────────────────────────────────────

    def test_search_replace_bad_params(self, tmp_path: Path):
        """Updates with non-dict params or update are skipped."""
        sd = tmp_path / "session"
        sd.mkdir()
        updates = [
            {"params": "not-a-dict"},
            {"params": {"update": "not-a-dict"}},
            {
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "title": "search_replace",
                        "rawInput": "not-dict",
                    }
                }
            },
            {
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "title": "search_replace",
                        "rawInput": {"old_string": "", "new_string": ""},
                    }
                }
            },
        ]
        (sd / "updates.jsonl").write_text("\n".join(json.dumps(u) for u in updates) + "\n")
        body, meta = load_workspace_diff(sd)
        assert meta["source"] is None

    def test_updates_oserror(self, tmp_path: Path):
        """_iter_updates handles OSError by returning empty."""
        from anqa.session.workspace_diff import _iter_updates

        sd = tmp_path / "session"
        sd.mkdir()
        # Write a file, then make it unreadable
        f = sd / "updates.jsonl"
        f.write_text("{}\n")
        f.chmod(0o000)
        results = list(_iter_updates(sd))
        f.chmod(0o644)  # restore so tmp_path cleanup works
        assert results == []


class TestUnifiedDiffBothSides:
    def test_modified_file(self):
        diff_text, added, removed = _unified_diff("line1", "line2", "f.py")
        assert "--- a/f.py" in diff_text
        assert "+++ b/f.py" in diff_text
        assert added == 1
        assert removed == 1


def _call(name: str, raw: dict[str, object]) -> TraceEvent:
    return TraceEvent(
        index=0,
        event_type="tool_call",
        tool_name=name,
        raw_input=ToolInputBag(raw),
    )


@pytest.mark.parametrize(
    ("name", "raw", "path", "old", "new"),
    [
        (
            "search_replace",
            {"file_path": "main.py", "old_string": "foo", "new_string": "bar"},
            "main.py",
            "foo",
            "bar",
        ),
        (
            "edit",
            {"path": "hello.py", "edits": [{"oldText": "return 1", "newText": "return 2"}]},
            "hello.py",
            "return 1",
            "return 2",
        ),
        (
            "Edit",
            {"file_path": "doc.rst", "old_string": "alpha", "new_string": "beta"},
            "doc.rst",
            "alpha",
            "beta",
        ),
        (
            "StrReplace",
            {"path": "app.ts", "old_string": "a", "new_string": "b"},
            "app.ts",
            "a",
            "b",
        ),
        (
            "edit",
            {"filePath": "pipe.py", "oldString": "old", "newString": "new"},
            "pipe.py",
            "old",
            "new",
        ),
    ],
)
def test_point_from_events_reads_replace_tools(
    name: str, raw: dict[str, object], path: str, old: str, new: str
) -> None:
    point = point_from_events([_call(name, raw)])
    assert point is not None
    assert point.source == "search_replace"
    assert [h.path for h in point.files] == [path]
    assert old in point.files[0].unified
    assert new in point.files[0].unified


@pytest.mark.parametrize(
    ("name", "raw", "path", "body"),
    [
        ("write", {"path": "NOTE.txt", "content": "WS1\n"}, "NOTE.txt", "WS1"),
        ("Write", {"file_path": "new.md", "content": "hello"}, "new.md", "hello"),
        (
            "create",
            {"path": "AUTOMEDON_TOOL.txt", "file_text": "TOOL_OK"},
            "AUTOMEDON_TOOL.txt",
            "TOOL_OK",
        ),
        ("write", {"filePath": "NOTE.txt", "content": "WS1\n"}, "NOTE.txt", "WS1"),
    ],
)
def test_point_from_events_reads_write_tools(
    name: str, raw: dict[str, object], path: str, body: str
) -> None:
    point = point_from_events([_call(name, raw)])
    assert point is not None
    assert [h.path for h in point.files] == [path]
    assert point.files[0].kind == "added"
    assert body in point.files[0].unified


def test_point_from_events_reads_codex_apply_patch() -> None:
    raw = (
        'const patch = "*** Begin Patch\\n*** Add File: NOTE.txt\\n+WS1\\n*** End Patch";\n'
        "await tools.apply_patch(patch);\n"
    )
    point = point_from_events([_call("exec", {"command": raw})])
    assert point is not None
    assert [h.path for h in point.files] == ["NOTE.txt"]
    assert point.files[0].kind == "added"
    assert "WS1" in point.files[0].unified


def test_point_from_events_reads_codex_update_patch() -> None:
    raw = "*** Begin Patch\n*** Update File: NOTE.txt\n@@\n WS1\n+WS2\n*** End Patch"
    point = point_from_events([_call("exec", {"command": raw})])
    assert point is not None
    assert [h.path for h in point.files] == ["NOTE.txt"]
    assert "WS2" in point.files[0].unified


def test_point_from_events_keeps_last_prompt_and_reply() -> None:
    events = [
        TraceEvent(
            index=0, event_type="user_message_chunk", content="do you have support for workflows?"
        ),
        TraceEvent(index=1, event_type="agent_message_chunk", content="yes, here is how"),
        _call("write", {"path": "/tmp/a.md", "content": "x\n"}),
    ]
    point = point_from_events(events)
    assert point is not None
    assert point.prompt_text == "do you have support for workflows?"
    assert point.assistant_text == "yes, here is how"


def test_point_from_events_ignores_read_and_shell() -> None:
    events = [
        _call("bash", {"command": "echo hi"}),
        _call("read", {"path": "x.py"}),
        _call("Read", {"file_path": "y.py"}),
    ]
    assert point_from_events(events) is None


def test_points_from_events_one_point_per_turn() -> None:
    events = [
        TraceEvent(
            index=0,
            event_type="turn_started",
            content="turn_number=0",
            turn_number=0,
        ),
        TraceEvent(
            index=1,
            event_type="user_message_chunk",
            content="first edit",
            turn_number=0,
        ),
        TraceEvent(
            index=2,
            event_type="tool_call",
            tool_name="edit",
            raw_input=ToolInputBag({"path": "a.py", "edits": [{"oldText": "1", "newText": "2"}]}),
            turn_number=0,
        ),
        TraceEvent(
            index=3,
            event_type="turn_started",
            content="turn_number=1",
            turn_number=1,
        ),
        TraceEvent(
            index=4,
            event_type="user_message_chunk",
            content="write a note",
            turn_number=1,
        ),
        TraceEvent(
            index=5,
            event_type="tool_call",
            tool_name="write",
            raw_input=ToolInputBag({"path": "NOTE.txt", "content": "hi\n"}),
            turn_number=1,
        ),
    ]
    points = points_from_events(events)
    assert [p.prompt_index for p in points] == [0, 1]
    assert [p.key for p in points] == ["edits-0", "edits-1"]
    assert [h.path for h in points[0].files] == ["a.py"]
    assert [h.path for h in points[1].files] == ["NOTE.txt"]
    assert points[0].prompt_text == "first edit"
    assert points[1].prompt_text == "write a note"


def test_point_from_events_groups_edits_by_path() -> None:
    events = [
        _call("edit", {"path": "a.py", "edits": [{"oldText": "1", "newText": "2"}]}),
        _call("write", {"path": "b.py", "content": "new\n"}),
        _call("edit", {"path": "a.py", "edits": [{"oldText": "2", "newText": "3"}]}),
    ]
    point = point_from_events(events)
    assert point is not None
    assert [h.path for h in point.files] == ["a.py", "b.py"]
    assert point.files[0].unified.count("@@") >= 2


def test_point_from_events_reads_apply_patch_grammar() -> None:
    """Official Codex apply_patch Lark grammar: every file op in one patch."""
    raw = (
        "*** Begin Patch\n"
        "*** Environment ID: env_1\n"
        "*** Add File: path/add.py\n"
        "+abc\n"
        "+def\n"
        "*** Delete File: path/delete.py\n"
        "*** Update File: path/update.py\n"
        "*** Move to: path/update2.py\n"
        "@@ def f():\n"
        "-    pass\n"
        "+    return 123\n"
        "*** End of File\n"
        "*** End Patch"
    )
    point = point_from_events([_call("exec", {"command": raw})])
    assert point is not None
    by_path = {h.path: h for h in point.files}
    assert set(by_path) == {"path/add.py", "path/delete.py", "path/update2.py"}
    assert by_path["path/add.py"].kind == "added"
    assert "abc" in by_path["path/add.py"].unified
    assert by_path["path/delete.py"].kind == "removed"
    assert by_path["path/update2.py"].kind == "edit"
    assert "return 123" in by_path["path/update2.py"].unified
    assert "Environment ID" not in by_path["path/update2.py"].unified
    assert "End of File" not in by_path["path/update2.py"].unified


def test_doc_from_payload_round_trips_session_diff_files() -> None:
    from anqa.session.workspace_diff import DiffHunk, DiffPoint, WorkspaceDiff, diff_payload

    doc = WorkspaceDiff(
        (
            DiffPoint(
                key="edits",
                source="search_replace",
                prompt_index=1,
                created_at="2026-09-01T00:00:00Z",
                files=(
                    DiffHunk(
                        path="/tmp/README.md",
                        kind="added",
                        added=2,
                        removed=0,
                        unified="--- /dev/null\n+++ b/README.md\n+# hi\n",
                    ),
                ),
                prompt_text="write it",
            ),
        )
    )
    payload = diff_payload("sid", doc)
    back = doc_from_payload(payload)
    assert back.source == "search_replace"
    assert back.points[0].files[0].path == "/tmp/README.md"
    assert "# hi" in back.points[0].files[0].unified
