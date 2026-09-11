"""Pilot suite for BrowserScreen: timeline, tabs, multi-turn pending bar.

Uses Textual ``App.run_test()`` so compose, workers, and bindings actually run.
Synchronisation is condition-based (``wait_until``); see AGENTS.md §4.5c.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from anqa.ui.app import AnqaApp
from anqa.ui.bindings import focus_primary_list
from anqa.ui.data_table import cursor_row_key
from anqa.ui.screens.browser import BrowserScreen
from anqa.ui.selectable_static import SelectableStatic
from anqa.ui.widgets.controls import FILTER_LABEL_CLASS
from anqa.ui.widgets.sash import VerticalSash
from anqa.ui.widgets.timeline import TimelineTable
from textual.events import MouseMove
from textual.widgets import Input, Static, Switch, TabbedContent

from .pilot_helpers import static_plain, wait_until


def _write_multi_turn_session(traces_root: Path, *, session_id: str = "browser-pilot-sess") -> Path:
    """Build a multi-turn session on the eval traces bind-mount layout."""
    container = traces_root / "anqa-pilot-run-m1"
    sess = container / "%2Fworkspace" / session_id
    sess.mkdir(parents=True)

    (sess / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": session_id, "cwd": "/workspace"},
                "session_summary": "Pilot multi-turn pilot session",
                "generated_title": "Pilot multi-turn",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:10:00Z",
                "num_messages": 6,
                "current_model_id": "pilot-model",
            }
        ),
        encoding="utf-8",
    )

    updates = [
        {
            "type": "user_message",
            "ts": "2026-06-25T00:00:01Z",
            "message": {"content": [{"type": "text", "text": "first prompt"}]},
        },
        {
            "type": "assistant_message",
            "ts": "2026-06-25T00:00:02Z",
            "message": {"content": [{"type": "text", "text": "working on it"}]},
        },
        {
            "type": "tool_call",
            "ts": "2026-06-25T00:00:03Z",
            "toolCallId": "c1",
            "toolName": "run_terminal_command",
            "input": {"command": "echo hi"},
        },
        {
            "type": "tool_result",
            "ts": "2026-06-25T00:00:04Z",
            "toolCallId": "c1",
            "toolName": "run_terminal_command",
            "output": "hi\n",
        },
        {
            "type": "user_message",
            "ts": "2026-06-25T00:05:01Z",
            "message": {"content": [{"type": "text", "text": "second prompt"}]},
        },
        {
            "type": "assistant_message",
            "ts": "2026-06-25T00:05:02Z",
            "message": {"content": [{"type": "text", "text": "done"}]},
        },
    ]
    (sess / "updates.jsonl").write_text(
        "\n".join(json.dumps(u) for u in updates) + "\n",
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-06-25T00:00:00Z",
                        "type": "turn_started",
                        "turn_number": 0,
                        "model_id": "pilot-model",
                    }
                ),
                json.dumps(
                    {"ts": "2026-06-25T00:04:00Z", "type": "turn_ended", "outcome": "success"}
                ),
                json.dumps(
                    {
                        "ts": "2026-06-25T00:05:00Z",
                        "type": "turn_started",
                        "turn_number": 1,
                        "model_id": "pilot-model",
                    }
                ),
                json.dumps(
                    {"ts": "2026-06-25T00:06:00Z", "type": "turn_ended", "outcome": "success"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    return sess


def _host_app(work: Path, traces: Path) -> AnqaApp:
    app = AnqaApp(traces_path=traces)
    return app


async def _open_browser(app: AnqaApp, pilot, sess: Path) -> BrowserScreen:
    """Push BrowserScreen and wait until timeline is loaded; stop live refresh."""
    app.push_screen(BrowserScreen(sess))

    def ready() -> bool:
        scr = app.screen
        return isinstance(scr, BrowserScreen) and bool(scr.timeline)

    await wait_until(pilot, ready, description="BrowserScreen.timeline loaded")
    screen = app.screen
    assert isinstance(screen, BrowserScreen)
    screen._stop_live_refresh()
    return screen


async def _activate_tab(pilot, screen: BrowserScreen, pane_id: str) -> None:
    """Run tab action, set active authoritatively, wait until TabbedContent agrees."""
    actions = {
        "tab-timeline": screen.action_tab_timeline,
        "tab-summary": screen.action_tab_summary,
        "tab-diff": screen.action_tab_diff,
        "tab-notes": screen.action_tab_notes,
    }
    actions[pane_id]()
    tabs = screen.query_one("#browser-tabs", TabbedContent)
    tabs.active = pane_id
    await wait_until(
        pilot,
        lambda: tabs.active == pane_id,
        description=f"tab {pane_id!r} active",
    )


@pytest.mark.asyncio
async def test_browser_mounts_timeline_without_follow_up_bar(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        assert screen.meta is not None
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0
        assert list(screen.query("#session-follow-input")) == []
        assert list(screen.query("#session-pending-bar")) == []
        assert getattr(screen.focused, "id", None) == "timeline-list"


@pytest.mark.asyncio
async def test_enter_opens_full_width_event_and_escape_restores_list(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0
        if screen._current_event is None:
            screen._current_event = tl.events[0]
        layout = screen.query_one("#browser-layout")
        assert not layout.has_class("event-reader")
        opened = cursor_row_key(tl)
        opened_event = screen._current_event.index if screen._current_event else None
        screen.action_toggle_event_reader()
        assert layout.has_class("event-reader")
        screen.action_go_back()
        assert not layout.has_class("event-reader")
        assert isinstance(app.screen, BrowserScreen)
        assert cursor_row_key(tl) == opened
        if opened_event is not None:
            assert screen._current_event is not None
            assert screen._current_event.index == opened_event


@pytest.mark.asyncio
async def test_timeline_sash_drag_resizes_the_list(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        sash = screen.query_one("#timeline-sash", VerticalSash)
        assert sash.orientation == "vertical"
        assert sash.ALLOW_SELECT is False
        assert "ew-resize" in VerticalSash.DEFAULT_CSS
        panel = screen.query_one("#timeline-panel")
        layout = screen.query_one("#browser-layout")
        before = panel.size.width
        assert before > 40
        await pilot.mouse_down(sash)
        sash.post_message(
            MouseMove(
                sash,
                0,
                0,
                0,
                0,
                1,
                False,
                False,
                False,
                screen_x=float(layout.region.x + 32),
                screen_y=float(sash.region.y),
            )
        )
        await wait_until(
            pilot,
            lambda: panel.size.width != before,
            description="sash drag changes list width",
        )
        assert 20 <= panel.size.width <= 40
        await pilot.mouse_up(sash)
        selected = screen.get_selected_text()
        assert not selected
        screen.action_toggle_event_reader()
        assert not sash.display


@pytest.mark.asyncio
async def test_turn_step_returns_focus_so_jk_still_move(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        if screen._current_event is None and tl.events:
            screen._current_event = tl.events[0]
        screen.query_one("#search-input", Input).focus()
        await wait_until(
            pilot,
            lambda: screen.focused is not tl,
            description="search field took focus",
        )
        screen._land_after_turn_step(keep=True)

        def listed() -> bool:
            focused = screen.focused
            return focused is tl or getattr(focused, "id", None) == "timeline-list"

        await wait_until(pilot, listed, description="timeline list focused after turn step")


@pytest.mark.asyncio
async def test_browser_tabs_and_stats_turns(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)

        await _activate_tab(pilot, screen, "tab-summary")
        ev_table = screen.query_one("#stats-events-table")
        screen._update_stats()
        await wait_until(
            pilot,
            lambda: ev_table.row_count >= 1,
            description="summary stats table has rows",
        )

        await _activate_tab(pilot, screen, "tab-timeline")

        screen.action_tab_next()
        await pilot.pause()
        screen.action_tab_prev()
        await pilot.pause()
        await _activate_tab(pilot, screen, "tab-timeline")


@pytest.mark.asyncio
async def test_summary_turn_row_opens_timeline_at_start(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        ev_i = screen.timeline[0].index
        screen._jump_timeline_to_event(ev_i)
        tabs = screen.query_one("#browser-tabs", TabbedContent)
        await wait_until(
            pilot,
            lambda: tabs.active == "tab-timeline",
            description="timeline tab after turn jump",
        )
        await wait_until(
            pilot,
            lambda: cursor_row_key(screen.query_one("#timeline-list", TimelineTable)) == str(ev_i),
            description="timeline cursor on turn start",
        )


@pytest.mark.asyncio
async def test_summary_pairs_stack_when_narrow(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        scroll = screen.query_one("#summary-session-scroll")
        screen._SUMMARY_STACK_WIDTH = 200
        screen._sync_summary_stack()
        assert scroll.has_class("summary-stack")
        screen._SUMMARY_STACK_WIDTH = 40
        screen._sync_summary_stack()
        assert not scroll.has_class("summary-stack")


@pytest.mark.asyncio
async def test_browser_timeline_filter_and_cursor_stable(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0

        if tl.row_count > 1:
            tl.move_cursor(row=1, animate=False)
        key_before = cursor_row_key(tl)

        tl.load_events(screen.timeline)
        await pilot.pause()
        if key_before and tl.row_count:
            key_after = cursor_row_key(tl)
            assert key_after == key_before

        screen._apply_filter(event_type="tool_call", errors_only=False)
        await pilot.pause()


@pytest.mark.asyncio
async def test_browser_timeline_view_filter_survives_reload(tmp_path: Path) -> None:
    """View filter must re-apply after load_events (live tick / F5)."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        full_n = tl.row_count
        assert full_n > 0

        # Session chrome is always present on this fixture (turn markers).
        screen._timeline_filter = "sess"
        screen._apply_timeline_filters()
        await pilot.pause()
        filtered_n = tl.row_count
        assert 0 < filtered_n <= full_n

        # Simulate full / light reload painting the unfiltered list first.
        tl.load_events(screen.timeline)
        await pilot.pause()
        assert tl.row_count == full_n  # unfiltered paint
        # Without reapply, the Select would still say "sess" while all rows show.
        screen._reapply_timeline_view_filter()
        await pilot.pause()
        assert tl.row_count == filtered_n

        # Full populate path must also reapply.
        screen._populate_ui()
        await pilot.pause()
        assert screen._timeline_filter == "sess"
        assert tl.row_count == filtered_n


@pytest.mark.asyncio
async def test_browser_notes_pane_mounts(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-notes")
        assert list(screen.query("#notes-list")) != []


@pytest.mark.asyncio
async def test_notes_mounts_one_card_per_note(tmp_path: Path) -> None:
    """Notes paints each note as its own extractable card."""
    from anqa.notes import NoteEntry, NotesDoc, dump_notes_toml

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces, session_id="note-cards-sess")
    (sess / "operator_notes.toml").write_text(
        dump_notes_toml(
            NotesDoc(
                schema_id="default",
                session_id=sess.name,
                notes=[
                    NoteEntry(id="n-a", turn_index=0, fields={"summary": "note-one"}),
                    NoteEntry(id="n-b", turn_index=0, fields={"summary": "note-two"}),
                ],
            )
        ),
        encoding="utf-8",
    )
    app = _host_app(work, traces)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-notes")
        screen._update_notes_tab()
        await pilot.pause()
        notes = list(screen.query("#notes-list > .panel-card"))
        assert len(notes) == 2
        body = screen.query_one("#note-n-a-body", SelectableStatic)
        plain = body.get_plain_text() or ""
        assert "note-one" in plain
        assert "─" not in plain


@pytest.mark.asyncio
async def test_report_notes_shows_source_badge(tmp_path: Path) -> None:
    from anqa.notes import NoteEntry, NotesDoc, dump_notes_toml

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces, session_id="note-src-sess")
    (sess / "operator_notes.toml").write_text(
        dump_notes_toml(
            NotesDoc(
                schema_id="default",
                session_id=sess.name,
                notes=[
                    NoteEntry(
                        id="n-own",
                        turn_index=0,
                        source="tui",
                        fields={"summary": "typed here"},
                    ),
                    NoteEntry(
                        id="n-ext",
                        turn_index=0,
                        source="nvim",
                        fields={"summary": "from vim"},
                    ),
                ],
            )
        ),
        encoding="utf-8",
    )
    app = _host_app(work, traces)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-notes")
        screen._update_notes_tab()
        await pilot.pause()
        own_badge = screen.query_one("#note-n-own .note-source-badge", Static)
        ext_badge = screen.query_one("#note-n-ext .note-source-badge", Static)
        assert static_plain(own_badge) == "tui"
        assert static_plain(ext_badge) == "nvim"
        own_body = screen.query_one("#note-n-own-body", SelectableStatic)
        ext_body = screen.query_one("#note-n-ext-body", SelectableStatic)
        assert "tui" not in (own_body.get_plain_text() or "")
        assert "nvim" not in (ext_body.get_plain_text() or "")
        assert "Source" not in (own_body.get_plain_text() or "")


@pytest.mark.asyncio
async def test_report_notes_keyboard_focus_edit_and_delete(tmp_path: Path) -> None:
    from anqa.notes import NoteEntry, NotesDoc, dump_notes_toml, load_notes
    from anqa.ui.widgets.notes_modal import NotesModal

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces, session_id="note-nav-sess")
    (sess / "operator_notes.toml").write_text(
        dump_notes_toml(
            NotesDoc(
                schema_id="default",
                session_id=sess.name,
                notes=[
                    NoteEntry(id="n-a", turn_index=0, fields={"summary": "note-one"}),
                    NoteEntry(id="n-b", turn_index=0, fields={"summary": "note-two"}),
                ],
            )
        ),
        encoding="utf-8",
    )
    app = _host_app(work, traces)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-notes")
        screen._update_notes_tab()
        await wait_until(
            pilot,
            lambda: bool(list(screen.query("#notes-list > .panel-card"))),
            description="note cards mounted",
        )
        screen.query_one("#notes-list").focus()
        await wait_until(
            pilot,
            lambda: getattr(screen.focused, "id", None) == "notes-list",
            description="notes list focused",
        )
        assert screen.check_action("edit_operator_note", ()) is False
        assert screen.check_action("delete_session", ()) is False
        await pilot.click("#note-n-b")
        await wait_until(
            pilot,
            lambda: "note-focused" in (screen.query_one("#note-n-b").classes),
            description="click selects the note card",
        )
        assert screen.check_action("edit_operator_note", ()) is True
        screen._notes_focus = None
        screen._paint_note_focus()
        screen.refresh_bindings()
        await pilot.press("j")
        await wait_until(
            pilot,
            lambda: "note-focused" in (screen.query_one("#note-n-a").classes),
            description="j focuses first note",
        )
        assert screen.check_action("edit_operator_note", ()) is True
        assert screen.check_action("delete_session", ()) is True
        await pilot.press("j")
        await wait_until(
            pilot,
            lambda: "note-focused" in (screen.query_one("#note-n-b").classes),
            description="j focuses second note",
        )
        await pilot.press("k")
        await wait_until(
            pilot,
            lambda: "note-focused" in (screen.query_one("#note-n-a").classes),
            description="k returns to first note",
        )
        await pilot.press("down")
        await wait_until(
            pilot,
            lambda: "note-focused" in (screen.query_one("#note-n-b").classes),
            description="down focuses second note",
        )
        await pilot.press("up")
        await wait_until(
            pilot,
            lambda: "note-focused" in (screen.query_one("#note-n-a").classes),
            description="up returns to first note",
        )
        await pilot.press("O")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, NotesModal),
            description="O opens focused note",
        )
        assert app.screen.existing is not None
        assert app.screen.existing.id == "n-a"
        app.screen.dismiss(None)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, BrowserScreen),
            description="edit modal dismissed",
        )
        browser = app.screen
        assert isinstance(browser, BrowserScreen)
        await _activate_tab(pilot, browser, "tab-notes")
        browser._update_notes_tab()
        await pilot.pause()
        browser._notes_focus = None
        browser.query_one("#notes-list").focus()
        await pilot.pause()
        await pilot.press("j")
        await wait_until(
            pilot,
            lambda: "note-focused" in (browser.query_one("#note-n-a").classes),
            description="focus first note before delete",
        )
        await pilot.press("x")
        await pilot.press("x")
        await wait_until(
            pilot,
            lambda: (
                load_notes(sess).notes == [] or all(n.id != "n-a" for n in load_notes(sess).notes)
            ),
            description="double x deletes focused note",
        )
        left = [n.id for n in load_notes(sess).notes]
        assert "n-a" not in left
        assert "n-b" in left


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("click_id", "key", "want_id"),
    [
        ("n-a", "j", "n-b"),
        ("n-b", "j", "n-c"),
        ("n-c", "k", "n-b"),
        ("n-b", "k", "n-a"),
    ],
)
async def test_notes_click_then_jk_continues_from_clicked_row(
    tmp_path: Path, click_id: str, key: str, want_id: str
) -> None:
    from anqa.notes import NoteEntry, NotesDoc, dump_notes_toml

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces, session_id=f"note-click-{click_id}-{key}")
    (sess / "operator_notes.toml").write_text(
        dump_notes_toml(
            NotesDoc(
                schema_id="default",
                session_id=sess.name,
                notes=[
                    NoteEntry(id="n-a", turn_index=0, fields={"summary": "note-one"}),
                    NoteEntry(id="n-b", turn_index=0, fields={"summary": "note-two"}),
                    NoteEntry(id="n-c", turn_index=0, fields={"summary": "note-three"}),
                ],
            )
        ),
        encoding="utf-8",
    )
    app = _host_app(work, traces)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-notes")
        screen._update_notes_tab()
        await wait_until(
            pilot,
            lambda: bool(list(screen.query("#notes-list > .panel-card"))),
            description="note cards mounted",
        )
        screen.query_one("#notes-list").focus()
        await pilot.click(f"#note-{click_id}")
        await wait_until(
            pilot,
            lambda: screen._notes_focus == click_id,
            description=f"click focuses {click_id}",
        )
        assert "note-focused" in screen.query_one(f"#note-{click_id}").classes
        await pilot.press(key)
        await wait_until(
            pilot,
            lambda: screen._notes_focus == want_id,
            description=f"{key} from {click_id} to {want_id}",
        )
        assert "note-focused" in screen.query_one(f"#note-{want_id}").classes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "key", "want"),
    [
        (1, "k", 0),
        (0, "j", 1),
    ],
)
async def test_timeline_cursor_then_jk_steps(
    tmp_path: Path, start: int, key: str, want: int
) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces, session_id=f"tl-click-{start}-{key}")
    app = _host_app(work, traces)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        if tl.row_count < 2:
            tl.load_events(screen.timeline)
            await pilot.pause()
        assert tl.row_count >= 2
        tl.focus()
        tl.move_cursor(row=start, animate=False)
        clicked = cursor_row_key(tl)
        assert clicked is not None
        assert tl.cursor_row == start
        await pilot.press(key)
        await wait_until(
            pilot,
            lambda: tl.cursor_row == want,
            description=f"{key} from row {start} to {want}",
        )
        assert cursor_row_key(tl) != clicked


@pytest.mark.asyncio
async def test_browser_summary_stats_tables(tmp_path: Path) -> None:
    """Summary pane builds event, tool, phase, and turns tables."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        screen._update_stats()
        await pilot.pause()

        from textual.widgets import DataTable as DT

        ev_table = screen.query_one("#stats-events-table", DT)
        assert ev_table.row_count >= 1
        tools_table = screen.query_one("#stats-tools-table", DT)
        assert tools_table.row_count >= 0
        phases_table = screen.query_one("#stats-phases-table", DT)
        assert phases_table.row_count >= 1


# ── Diff tab ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_diff_tab(tmp_path: Path) -> None:
    """Diff tab renders even with no diff data."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        screen._update_diff_tab()
        await pilot.pause()
        from anqa.ui.widgets.diff_view import DiffView
        from textual.widgets import Tree

        view = screen.query_one("#diff-view", DiffView)
        tree = view.query_one("#diff-file-list", Tree)
        assert len(tree.root.children) == 0
        assert view.selected_plain() == ""


@pytest.mark.asyncio
async def test_browser_diff_file_list_shows_rewind_files(tmp_path: Path) -> None:
    """Diff pane lists rewind snapshot files and yanks the highlighted hunk."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    (sess / "rewind_points.jsonl").write_text(
        json.dumps(
            {
                "prompt_index": 1,
                "file_snapshots": {"app.py": "old", "extra.py": "keep"},
                "after_snapshots": {"app.py": "new", "extra.py": "keep", "added.py": "fresh"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        from anqa.ui.widgets.diff_view import DiffView
        from textual.widgets import Tree

        view = screen.query_one("#diff-view", DiffView)
        tree = view.query_one("#diff-file-list", Tree)
        await wait_until(
            pilot,
            lambda: len(tree.root.children) == 2,
            description="diff file tree has two changed paths",
        )
        labels = {str(n.label) for n in tree.root.children}
        assert labels == {"added.py", "app.py"}
        tree.select_node(tree.root.children[0])
        await pilot.pause()
        first = str(tree.root.children[0].label)
        assert first in view.selected_plain()


@pytest.mark.asyncio
async def test_browser_diff_file_list_groups_nested_paths(tmp_path: Path) -> None:
    """Nested rewind paths show a directory header and file leaves."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    (sess / "rewind_points.jsonl").write_text(
        json.dumps(
            {
                "prompt_index": 1,
                "file_snapshots": {"src/app.py": "old", "src/extra.py": "old-extra"},
                "after_snapshots": {"src/app.py": "new", "src/extra.py": "new-extra"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        from anqa.ui.widgets.diff_view import DiffView
        from textual.widgets import Tree

        view = screen.query_one("#diff-view", DiffView)
        tree = view.query_one("#diff-file-list", Tree)
        await wait_until(
            pilot,
            lambda: bool(tree.root.children),
            description="diff file tree has rows",
        )
        dir_node = tree.root.children[0]
        assert str(dir_node.label) == "src/"
        assert dir_node.data == ("dir", "src/")
        files = {str(n.label) for n in dir_node.children}
        assert files == {"app.py", "extra.py"}


@pytest.mark.asyncio
async def test_browser_diff_search_filters_path_and_body(tmp_path: Path) -> None:
    """Slash search keeps a path hit and a body hit; h/l steps snapshots."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    (sess / "rewind_points.jsonl").write_text(
        json.dumps(
            {
                "prompt_index": 0,
                "file_snapshots": {"alpha.py": "old"},
                "after_snapshots": {"alpha.py": "needle-one"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "prompt_index": 1,
                "file_snapshots": {"beta.py": "old", "notes.md": "keep"},
                "after_snapshots": {
                    "beta.py": "changed",
                    "notes.md": "keep",
                    "zeta.py": "needle-two",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        from anqa.ui.widgets.diff_view import DiffView
        from textual.widgets import Input, Tree

        view = screen.query_one("#diff-view", DiffView)
        tree = view.query_one("#diff-file-list", Tree)
        await wait_until(
            pilot,
            lambda: view.can_step_point() is True,
            description="two rewind snapshots",
        )
        assert screen.check_action("prev_turn", ()) is True
        screen.action_search()
        await pilot.pause()
        search = view.query_one("#diff-search", Input)
        search.value = "zeta"
        await wait_until(
            pilot,
            lambda: len(tree.root.children) == 1 and str(tree.root.children[0].label) == "zeta.py",
            description="path query keeps zeta.py",
        )
        assert "zeta.py" in view.selected_plain()
        search.value = "needle-two"
        await wait_until(
            pilot,
            lambda: (
                (view.painted_hit_line() or "").startswith("> ")
                and "needle-two" in (view.painted_hit_line() or "")
            ),
            description="body query paints the matching unified line",
        )
        painted = view.painted_hit_line() or ""
        raw = view.selected_plain().splitlines()[view.hit_line() or 0]
        assert painted == f"> {raw}"
        from anqa.ui.selectable_static import SelectableStatic

        body = view.query_one("#diff-content", SelectableStatic).get_plain_text()
        assert painted in body


@pytest.mark.asyncio
async def test_browser_diff_context_above_files_hunk_split(tmp_path: Path) -> None:
    """Prompt/Assistant sit above a parent that holds files and hunk side by side."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        from anqa.session.workspace_diff import DiffHunk, DiffPoint, WorkspaceDiff
        from anqa.ui.selectable_static import SelectableStatic
        from anqa.ui.widgets.diff_view import DiffView

        view = screen.query_one("#diff-view", DiffView)
        view.set_doc(
            WorkspaceDiff(
                (
                    DiffPoint(
                        key="0",
                        source="rewind_points",
                        prompt_index=0,
                        created_at=None,
                        files=(
                            DiffHunk(
                                path="app.py",
                                kind="modified",
                                added=1,
                                removed=1,
                                unified="--- a/app.py\n+++ b/app.py\n+new\n",
                            ),
                        ),
                        prompt_text="first prompt",
                        assistant_text="## Heading\n\n**ok**",
                    ),
                )
            )
        )
        await pilot.pause()
        chrome = view.query_one("#diff-chrome")
        ctx = view.query_one("#diff-context")
        search = view.query_one("#diff-search-bar")
        split = view.query_one("#diff-layout")
        assert view.query_one("#diff-filter-bar").parent is chrome
        assert ctx.parent is chrome
        assert search.parent is view
        assert split.parent is view
        kids = [child.id for child in view.children]
        assert kids.index("diff-chrome") < kids.index("diff-search-bar") < kids.index("diff-layout")
        assert view.query_one("#diff-files").parent is split
        assert view.query_one("#diff-scroll").parent is split
        prompt = view.query_one("#diff-prompt", SelectableStatic)
        assert "first prompt" in prompt.get_plain_text()
        tabs = view.query_one("#diff-context-tabs", TabbedContent)
        tabs.active = "diff-tab-assistant"
        await pilot.pause()
        assistant = view.query_one("#diff-assistant", SelectableStatic)
        assert "## Heading" in assistant.get_plain_text()


@pytest.mark.asyncio
async def test_diff_view_absolute_paths_keep_the_hunk() -> None:
    """Tree rows drop a leading slash; the open hunk must still match the file."""
    from anqa.session.workspace_diff import DiffHunk, DiffPoint, WorkspaceDiff
    from anqa.ui.widgets.diff_view import DiffView, _tree_nodes
    from textual.app import App
    from textual.widgets import Tree

    class Host(App[None]):
        def compose(self):
            yield DiffView(id="diff-view")

    path = "/home/ali/.pi/agent/agents/reviewer.md"
    unified = "--- a/reviewer.md\n+++ b/reviewer.md\n+role: review\n"
    async with Host().run_test(size=(120, 40)) as pilot:
        view = pilot.app.query_one("#diff-view", DiffView)
        view.set_doc(
            WorkspaceDiff(
                (
                    DiffPoint(
                        key="edits",
                        source="search_replace",
                        prompt_index=None,
                        created_at=None,
                        files=(
                            DiffHunk(
                                path=path,
                                kind="edit",
                                added=1,
                                removed=0,
                                unified=unified,
                            ),
                        ),
                    ),
                )
            )
        )
        await pilot.pause()
        tree = view.query_one("#diff-file-list", Tree)
        leaves = [n for n in _tree_nodes(tree.root) if n.data and n.data[0] == "file"]
        assert leaves
        tree.select_node(leaves[0])
        await pilot.pause()
        assert "role: review" in view.selected_plain()
        body = view.query_one("#diff-content").get_plain_text()
        assert "No file changes" not in body
        assert "role: review" in body


@pytest.mark.asyncio
async def test_diff_view_find_steps_every_hit() -> None:
    """Diff find walks every matching line and opens the file that holds it."""
    from anqa.session.workspace_diff import DiffHunk, DiffPoint, WorkspaceDiff
    from anqa.ui.widgets.diff_view import DiffView
    from textual.app import App
    from textual.widgets import Input

    class Host(App[None]):
        def compose(self):
            yield DiffView(id="diff-view")

    async with Host().run_test(size=(120, 40)) as pilot:
        view = pilot.app.query_one("#diff-view", DiffView)
        view.set_doc(
            WorkspaceDiff(
                (
                    DiffPoint(
                        key="edits",
                        source="search_replace",
                        prompt_index=None,
                        created_at=None,
                        files=(
                            DiffHunk(
                                path="a.py",
                                kind="edit",
                                added=2,
                                removed=0,
                                unified="@@\n+needle one\n+keep\n+needle two\n",
                            ),
                            DiffHunk(
                                path="b.py",
                                kind="edit",
                                added=1,
                                removed=0,
                                unified="@@\n+other\n",
                            ),
                            DiffHunk(
                                path="c.py",
                                kind="edit",
                                added=1,
                                removed=0,
                                unified="@@\n+needle three\n",
                            ),
                        ),
                    ),
                )
            )
        )
        await pilot.pause()
        search = view.query_one("#diff-search", Input)
        search.value = "needle"
        await wait_until(
            pilot,
            lambda: view.hit_count() == 3 and view.hit_index() == 1,
            description="three needle hits, first selected",
        )
        assert view._file_key == "a.py"
        first = view.painted_hit_line() or ""
        assert first.startswith("> ") and "needle one" in first
        view.step_hit(1)
        assert view.hit_index() == 2
        assert "needle two" in (view.painted_hit_line() or "")
        view.step_hit(1)
        assert view._file_key == "c.py"
        assert view.hit_index() == 3
        assert "needle three" in (view.painted_hit_line() or "")
        view.step_hit(1)
        assert view.hit_index() == 1
        assert view._file_key == "a.py"
        view.step_hit(-1)
        assert view._file_key == "c.py"
        assert view.hit_index() == 3


# ── Summary tab ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_summary_tab(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        screen._update_summary_tab()
        await pilot.pause()


@pytest.mark.asyncio
async def test_browser_first_paint_defers_summary_and_notes(tmp_path: Path) -> None:
    """Opening a session paints Timeline only; Summary and Notes fill on visit."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    with (sess / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": "2026-06-25T00:07:00Z",
                    "type": "turn_started",
                    "turn_number": 2,
                    "model_id": "pilot-model",
                }
            )
            + "\n"
        )
    with (sess / "updates.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"params": {"update": {"sessionUpdate": "user_message_chunk"}}})
            + "\n"
            + json.dumps({"params": {"update": {"sessionUpdate": "agent_message_chunk"}}})
            + "\n"
            + json.dumps({"params": {"update": {"sessionUpdate": "tool_call"}}})
            + "\n"
        )
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        summary = screen.query_one("#summary-content", SelectableStatic)

        assert not (summary.get_plain_text() or "").strip()
        assert not list(screen.query("#notes-list > .panel-card"))
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0
        bar = screen.query_one("#filter-bar")
        grow = screen.query_one("#timeline-filter-grow")
        cluster = screen.query_one("#timeline-tail-cluster")
        tail = screen.query_one("#timeline-tail", Switch)
        label = screen.query_one("#timeline-tail-label", Static)
        filt = screen.query_one("#filter-view-label", Static)
        view = screen.query_one("#timeline-view-select")
        search = screen.query_one("#search-input", Input)
        assert not list(screen.query("#timeline-tail-slot"))
        assert cluster.display
        assert label.display
        assert tail.display
        assert FILTER_LABEL_CLASS in label.classes
        assert "Tail" in static_plain(label)
        assert tail.value is False
        shown = [c for c in bar.children if c.display]
        assert shown[-2] is grow
        assert shown[-1] is cluster
        assert search not in bar.children
        await wait_until(
            pilot,
            lambda: (
                cluster.region.width > 0
                and grow.region.width > 0
                and cluster.region.x > grow.region.x
            ),
            description="Tail cluster laid out to the right of the filter grow",
        )
        assert search.region.y > filt.region.y
        assert grow.region.x > view.region.x
        assert cluster.region.x > grow.region.x
        assert tail.region.x > label.region.x
        assert cluster.region.y == filt.region.y
        assert cluster.region.height == filt.region.height
        assert tail.region.center[1] == label.region.center[1]
        assert tail.region.center[1] == cluster.region.center[1]
        await pilot.click("#timeline-tail-label")
        await wait_until(
            pilot,
            lambda: screen._timeline_follow_tail() is True,
            description="Tail label click turns the switch on",
        )

        await _activate_tab(pilot, screen, "tab-summary")
        await wait_until(
            pilot,
            lambda: bool((summary.get_plain_text() or "").strip()),
            description="Summary body after first visit",
        )
        assert "Pilot" in summary.get_plain_text()

        await _activate_tab(pilot, screen, "tab-notes")
        await wait_until(
            pilot,
            lambda: screen.query_one("#notes-list") is not None,
            description="Notes pane after first visit",
        )


@pytest.mark.asyncio
async def test_browser_tab_bar_fills_summary_and_notes(tmp_path: Path) -> None:
    """Setting TabbedContent.active (tab-bar click) fills Summary and Notes."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        summary = screen.query_one("#summary-content", SelectableStatic)

        assert not (summary.get_plain_text() or "").strip()

        tabs = screen.query_one("#browser-tabs", TabbedContent)
        tabs.active = "tab-summary"
        await pilot.pause()
        await wait_until(
            pilot,
            lambda: bool((summary.get_plain_text() or "").strip()),
            description="Summary body after tab-bar activate",
        )
        assert "Pilot" in summary.get_plain_text()

        tabs.active = "tab-notes"
        await pilot.pause()
        await wait_until(
            pilot,
            lambda: tabs.active == "tab-notes" and screen.query_one("#notes-list") is not None,
            description="Notes pane after tab-bar activate",
        )


@pytest.mark.asyncio
async def test_browser_search_debounce_applies_final_query(tmp_path: Path) -> None:
    """Typing does not rebuild the table on the input handler."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        rebuilds = {"n": 0}
        orig = tl._refresh_rows

        def _count() -> None:
            rebuilds["n"] += 1
            orig()

        tl._refresh_rows = _count  # type: ignore[method-assign]
        inp = screen.query_one("#search-input", Input)
        started = {"n": 0}
        orig_start = screen._start_timeline_search_worker

        def _count_start() -> None:
            started["n"] += 1
            orig_start()

        screen._start_timeline_search_worker = _count_start  # type: ignore[method-assign]
        for needle in ("e", "ec", "ech"):
            inp.value = needle
            screen._on_search_changed(Input.Changed(inp, needle))
        assert started["n"] == 0
        assert rebuilds["n"] == 0
        screen._start_timeline_search_worker = orig_start  # type: ignore[method-assign]
        await wait_until(
            pilot,
            lambda: rebuilds["n"] >= 1,
            description="worker timeline search rebuilt the table",
        )
        filtered = tl.row_count
        tl._refresh_rows = orig  # type: ignore[method-assign]
        screen._timeline_search = "ech"
        screen._apply_timeline_filters()
        await pilot.pause()
        assert tl.row_count == filtered


@pytest.mark.asyncio
async def test_timeline_search_hints_keep_layout_slot(tmp_path: Path) -> None:
    """Hint text may change; the row stays so the table does not resize."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        hint = screen.query_one("#timeline-query-hints", Static)
        inp = screen.query_one("#search-input", Input)
        assert hint.display
        screen._on_search_changed(Input.Changed(inp, "zzzz-no-token"))
        assert hint.display
        screen._on_search_changed(Input.Changed(inp, "is:"))
        assert hint.display


@pytest.mark.asyncio
async def test_timeline_search_sits_on_its_own_row(tmp_path: Path) -> None:
    """Filter/Turn/Tail stay on one row; search is full-width below (like the HUD)."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(80, 40)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        bar = screen.query_one("#filter-bar")
        filt = screen.query_one("#filter-view-label", Static)
        search = screen.query_one("#search-input", Input)
        assert search not in bar.children
        assert search.region.y > filt.region.y
        assert search.region.width >= filt.region.width


@pytest.mark.asyncio
async def test_timeline_search_keeps_input_focus(tmp_path: Path) -> None:
    """Typing in Timeline search must not move focus to the event list."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        inp = screen.query_one("#search-input", Input)
        inp.focus()
        await pilot.pause()
        assert inp.has_focus
        inp.value = "hello"
        screen._on_search_changed(Input.Changed(inp, "hello"))
        await pilot.pause()
        assert inp.has_focus


@pytest.mark.asyncio
async def test_browser_control_paints_first_page_before_remainder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First control page is on the Timeline before the next page is fetched."""
    from anqa.session import wire_timeline as wt
    from anqa.session.control_views import build_session_overview, build_session_timeline

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    monkeypatch.setattr(wt, "TIMELINE_RPC_LIMIT", 1)
    gate = threading.Event()
    saw_first = threading.Event()
    offsets: list[int] = []

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            return build_session_overview(sess)

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            off = int(kwargs.get("offset") or 0)
            at = kwargs.get("at_index")
            offsets.append(off)
            if at is None:
                if off > 0:
                    gate.wait(timeout=5)
                else:
                    saw_first.set()
            return build_session_timeline(
                sess,
                offset=off,
                limit=int(kwargs.get("limit") or 1),
                at_index=at if isinstance(at, int) else None,
                content_chars=int(kwargs.get("content_chars") or 500),
            )

        async def notes_list(self, _ref: str) -> object:
            return {"notes": []}

    access = _Access()
    app = _host_app(work, traces)
    app.is_control_client = lambda: True  # type: ignore[method-assign]
    app.session_access = lambda: access  # type: ignore[method-assign]

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess))
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, BrowserScreen) and saw_first.is_set(),
            description="control first page returned",
        )
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        await wait_until(
            pilot,
            lambda: screen.query_one("#timeline-list", TimelineTable).row_count == 1,
            description="first timeline page painted",
        )
        first_n = len(screen.timeline)
        assert first_n == 1
        assert screen._timeline_events_complete is False
        gate.set()
        screen._fill_control_timeline_job()
        await wait_until(
            pilot,
            lambda: len(screen.timeline) > first_n,
            description="next timeline page appended on fill",
        )
        assert any(off > 0 for off in offsets)


@pytest.mark.asyncio
async def test_browser_control_end_of_list_fetches_next_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cursor on the last painted row asks the owner for the next page."""
    from anqa.session import wire_timeline as wt
    from anqa.session.control_views import build_session_overview, build_session_timeline

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    monkeypatch.setattr(wt, "TIMELINE_RPC_LIMIT", 1)
    offsets: list[int] = []

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            return build_session_overview(sess)

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            offsets.append(int(kwargs.get("offset") or 0))
            return build_session_timeline(
                sess,
                offset=int(kwargs.get("offset") or 0),
                limit=int(kwargs.get("limit") or 1),
                content_chars=int(kwargs.get("content_chars") or 500),
            )

        async def notes_list(self, _ref: str) -> object:
            return {"notes": []}

    access = _Access()
    app = _host_app(work, traces)
    app.is_control_client = lambda: True  # type: ignore[method-assign]
    app.session_access = lambda: access  # type: ignore[method-assign]

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess))
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, BrowserScreen) and bool(app.screen.timeline),
            description="control first page returned",
        )
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        await wait_until(
            pilot,
            lambda: screen.query_one("#timeline-list", TimelineTable).row_count >= 1,
            description="first timeline page painted",
        )
        first_n = len(screen.timeline)
        if first_n == 1:
            assert screen._timeline_events_complete is False
            screen.query_one("#timeline-list", TimelineTable).scroll_to_end()
        await wait_until(
            pilot,
            lambda: len(screen.timeline) > 1,
            description="next page fetched from last painted row",
        )
        assert any(off > 0 for off in offsets)


@pytest.mark.asyncio
async def test_browser_control_turn_pick_refetches_that_turn(
    tmp_path: Path,
) -> None:
    """Picking a later turn asks session/timeline for that turn, not page 1."""
    from anqa.session.control_views import build_session_overview, build_session_timeline

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    asked: list[dict[str, object]] = []

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            return build_session_overview(sess)

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            asked.append(dict(kwargs))
            return build_session_timeline(
                sess,
                offset=int(kwargs.get("offset") or 0),
                limit=int(kwargs.get("limit") or 50),
                prompt_index=kwargs.get("prompt_index")
                if isinstance(kwargs.get("prompt_index"), int)
                else None,
                content_chars=int(kwargs.get("content_chars") or 500),
            )

        async def notes_list(self, _ref: str) -> object:
            return {"notes": []}

    access = _Access()
    app = _host_app(work, traces)
    app.is_control_client = lambda: True  # type: ignore[method-assign]
    app.session_access = lambda: access  # type: ignore[method-assign]

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen._stop_live_refresh()
        from anqa.session.turns import TurnSegment

        screen._turn_segments = list(screen._turn_segments or []) + [
            TurnSegment(turn_index=9, turn_number=9, prompt_index=9)
        ]
        screen._turn_filter = "9"
        screen._start_control_timeline_scope()
        await wait_until(
            pilot,
            lambda: any(row.get("prompt_index") == 9 for row in asked),
            description="turn pick fetched promptIndex 9",
        )


@pytest.mark.asyncio
async def test_browser_open_event_asks_owner_ceiling(tmp_path: Path) -> None:
    """Selecting a timeline row refetches that event at the owner body ceiling."""
    from anqa.session.control_views import (
        MAX_CONTENT_CHARS,
        build_session_overview,
        build_session_timeline,
    )

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    asked: list[dict[str, object]] = []

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            return build_session_overview(sess)

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            asked.append(dict(kwargs))
            at = kwargs.get("at_index")
            return build_session_timeline(
                sess,
                offset=int(kwargs.get("offset") or 0),
                limit=int(kwargs.get("limit") or 50),
                at_index=at if isinstance(at, int) else None,
                content_chars=int(kwargs.get("content_chars") or 500),
            )

        async def notes_list(self, _ref: str) -> object:
            return {"notes": []}

    access = _Access()
    app = _host_app(work, traces)
    app.is_control_client = lambda: True  # type: ignore[method-assign]
    app.session_access = lambda: access  # type: ignore[method-assign]

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)

        def _asked_ceiling() -> bool:
            return any(
                isinstance(row.get("at_index"), int)
                and row.get("content_chars") == MAX_CONTENT_CHARS
                for row in asked
            )

        if screen._current_event is None:
            table = screen.query_one("#timeline-list", TimelineTable)
            ev = next(iter(table.events), None)
            assert ev is not None
            screen._current_event = ev
            screen._paint_selected_event_detail()
        await wait_until(pilot, _asked_ceiling, description="open-event ceiling fetch")
        assert any(
            isinstance(row.get("at_index"), int)
            and row.get("content_chars") == MAX_CONTENT_CHARS
            and int(row.get("limit") or 0) == 1
            for row in asked
        )


# ── Export finding ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_timeline_view_modes(tmp_path: Path) -> None:
    """Exercise all timeline View select modes."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        for mode in ("tools", "user", "asst", "sess", "errors", "all"):
            screen._apply_timeline_mode(mode)
            await pilot.pause()
        assert screen._timeline_filter == "all"


# ── Search action ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_search_action(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_search()
        await pilot.pause()
        screen.action_clear_filters()
        await pilot.pause()


# ── Refresh context ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_refresh_context(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_refresh_context()
        await pilot.pause()
        await pilot.pause()


# ── Focus timeline filter ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_focus_timeline_filter(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_focus_timeline_filter()
        await pilot.pause()


def _shown_footer_actions(screen: BrowserScreen) -> set[str]:
    return {
        ab.binding.action
        for ab in screen.active_bindings.values()
        if ab.binding.show and ab.enabled
    }


@pytest.mark.asyncio
async def test_browser_footer_hides_timeline_keys_off_timeline(tmp_path: Path) -> None:
    """Enter / h l leave the rail when the Timeline pane is not showing."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-timeline")
        tl = screen.query_one("#timeline-list", TimelineTable)
        focus_primary_list(tl)
        if screen._current_event is None and screen.timeline:
            screen._current_event = screen.timeline[0]
        screen.refresh_bindings()
        await pilot.pause()
        assert screen.check_action("toggle_event_reader", ()) is True
        shown = _shown_footer_actions(screen)
        assert "toggle_event_reader" in shown

        await _activate_tab(pilot, screen, "tab-summary")
        assert screen.check_action("toggle_event_reader", ()) is False
        assert screen.check_action("prev_turn", ()) is False
        assert screen.check_action("next_turn", ()) is False
        shown = _shown_footer_actions(screen)
        assert "toggle_event_reader" not in shown
        assert "prev_turn" not in shown
        assert "next_turn" not in shown
        assert "flag_event" not in shown
        assert "go_back" in shown
        assert "operator_note" in shown


# ── Open share (no URL) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_panes_are_timeline_summary_diff_notes(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        assert [pid for pid, _sel in screen.TAB_PANES] == [
            "tab-timeline",
            "tab-summary",
            "tab-diff",
            "tab-notes",
        ]
        tabs = screen.query_one("#browser-tabs", TabbedContent)
        for pid, _sel in screen.TAB_PANES:
            assert tabs.query_one(f"#{pid}")
