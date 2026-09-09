"""Notes bindings must not nest asyncio.run on Textual's loop."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.notes import NoteEntry, NotesDoc
from anqa.ui.screens.browser import BrowserScreen


def test_open_session_rewrites_file_locator_to_harness_id(tmp_path: Path) -> None:
    from anqa.models import SessionMeta
    from anqa.ui.app import AnqaApp

    store = tmp_path / "store.sqlite"
    store.write_bytes(b"x")
    traces = tmp_path / "traces"
    traces.mkdir()
    app = AnqaApp(traces_path=traces, control_socket=None)
    app._meta_only = [(SessionMeta(session_id="sess-1", session_dir=store, harness="codex"), "x")]
    pushed: list[Path] = []
    app._push_browser = lambda path, prompt_index=None: pushed.append(path)  # type: ignore[method-assign]
    app._open_session(str(store), notify_control=False)
    assert pushed == [Path("codex:sess-1")]


def test_open_session_keeps_directory_locator(tmp_path: Path) -> None:
    from anqa.models import SessionMeta
    from anqa.ui.app import AnqaApp

    sess = tmp_path / "sess-1"
    sess.mkdir()
    traces = tmp_path / "traces"
    traces.mkdir()
    app = AnqaApp(traces_path=traces, control_socket=None)
    app._meta_only = [(SessionMeta(session_id="sess-1", session_dir=sess, harness="grok"), "x")]
    pushed: list[Path] = []
    app._push_browser = lambda path, prompt_index=None: pushed.append(path)  # type: ignore[method-assign]
    app._open_session(str(sess), notify_control=False)
    assert pushed == [sess]


def test_notes_j_binding_does_not_load_from_check_action(tmp_path: Path) -> None:
    screen = BrowserScreen(tmp_path / "sess")
    screen._active_browser_tab = lambda: "tab-notes"  # type: ignore[method-assign]

    def boom() -> None:
        raise AssertionError("check_action must not load notes")

    screen._load_notes = boom  # type: ignore[method-assign]
    assert screen.check_action("timeline_down", ()) is False
    assert screen.check_action("timeline_up", ()) is False
    screen._notes_loaded = True
    screen._notes_doc = NotesDoc(
        notes=[NoteEntry(id="n-1", turn_index=20, fields={"summary": "x"})]
    )
    assert screen.check_action("timeline_down", ()) is True
    assert screen.check_action("timeline_up", ()) is True


@pytest.mark.asyncio
async def test_load_control_notes_from_running_loop(tmp_path: Path) -> None:
    sess = tmp_path / "sess"
    sess.mkdir()
    screen = BrowserScreen(sess)

    class _Access:
        async def notes_list(self, _ref: str) -> object:
            return {
                "notes": [
                    {
                        "id": "n-mf-01",
                        "turnIndex": 20,
                        "source": "session-notes",
                        "fields": {"summary": "x"},
                        "eventIndices": [1246],
                    }
                ]
            }

    screen._control_access = lambda: _Access()  # type: ignore[method-assign]
    screen._session_control_ref = lambda: str(sess)  # type: ignore[method-assign]
    doc = screen._load_control_notes()
    assert [n.id for n in doc.sorted_notes()] == ["n-mf-01"]
    assert doc.notes[0].turn_index == 20


def test_load_control_notes_keeps_doc_when_snapshot_is_not_a_dict(tmp_path: Path) -> None:
    sess = tmp_path / "sess"
    sess.mkdir()
    screen = BrowserScreen(sess)
    screen._notes_doc = NotesDoc(
        notes=[NoteEntry(id="n-keep", turn_index=1, fields={"summary": "stay"})]
    )

    class _Access:
        async def notes_list(self, _ref: str) -> object:
            return None

    screen._control_access = lambda: _Access()  # type: ignore[method-assign]
    screen._session_control_ref = lambda: str(sess)  # type: ignore[method-assign]
    doc = screen._load_control_notes()
    assert [n.id for n in doc.sorted_notes()] == ["n-keep"]


def test_load_control_notes_reads_turn_index_number(tmp_path: Path) -> None:
    """JSON numbers may arrive as float; the published field is turnIndex."""
    sess = tmp_path / "sess"
    sess.mkdir()
    screen = BrowserScreen(sess)

    class _Access:
        async def notes_list(self, _ref: str) -> object:
            return {
                "notes": [
                    {
                        "id": "n-float",
                        "turnIndex": 20.0,
                        "fields": {"summary": "x"},
                    }
                ]
            }

    screen._control_access = lambda: _Access()  # type: ignore[method-assign]
    screen._session_control_ref = lambda: str(sess)  # type: ignore[method-assign]
    doc = screen._load_control_notes()
    assert [n.id for n in doc.sorted_notes()] == ["n-float"]
    assert doc.notes[0].turn_index == 20


def test_load_control_notes_ignores_snake_turn_index(tmp_path: Path) -> None:
    sess = tmp_path / "sess"
    sess.mkdir()
    screen = BrowserScreen(sess)

    class _Access:
        async def notes_list(self, _ref: str) -> object:
            return {
                "notes": [
                    {
                        "id": "n-snake",
                        "turn_index": 3,
                        "fields": {"summary": "x"},
                    }
                ]
            }

    screen._control_access = lambda: _Access()  # type: ignore[method-assign]
    screen._session_control_ref = lambda: str(sess)  # type: ignore[method-assign]
    doc = screen._load_control_notes()
    assert doc.sorted_notes() == []


def test_notes_notify_matches_harness_ref_and_uuid(tmp_path: Path) -> None:
    sid = "01a07ec8-e7d9-7e23-8545-ec5ef0d95a8c"
    screen = BrowserScreen(Path(f"grok:{sid}"))
    assert screen.notes_notify_matches(sid)
    assert not screen.notes_notify_matches("other")
    named = BrowserScreen(tmp_path / sid)
    assert named.notes_notify_matches(sid)
