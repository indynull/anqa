"""Notes bindings must not nest asyncio.run on Textual's loop."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.notes import NoteEntry, NotesDoc
from anqa.ui.screens.browser import BrowserScreen


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
