"""Single source of truth for keyboard shortcuts and TUI navigation.

Screens import binding tuples from here — do not invent ad-hoc key lists in
banners, button labels, or one-off help strings. Shared TUI/HUD keys live
here; HUD tables live in ``desktop/src/help.rs``.
``Binding.id`` is the shared catalog id (:mod:`anqa.keys.catalog`).
"""

from __future__ import annotations

from contextlib import suppress

from textual.binding import Binding
from textual.screen import Screen
from textual.widget import Widget

from . import text as U
from .i18n import t
from .tab_panes import tab_nav_bindings
from .widgets.help_modal import notify_help


def _b(
    key: str,
    action: str,
    description: str,
    *,
    id: str,
    show: bool = True,
    priority: bool = False,
) -> Binding:
    return Binding(key, action, description, show=show, priority=priority, id=id)


def _ctrl_s(action: str, description: str = t("ui-save"), *, id: str, show: bool = True) -> Binding:
    """Ctrl+S with priority — works while focus is in Input / TextArea / Select."""
    return _b("ctrl+s", action, description, id=id, show=show, priority=True)


APP_GLOBAL_PRIORITY: tuple[Binding, ...] = ()

# Footer: Help · Back (pushed screens) · this screen's primary actions · Quit.
# F5 / Ctrl+R refresh without a footer slot. Home-only actions are gated in
# AnqaApp.check_action so they do not leak into pushed-screen footers.
# Quit is global (not priority): works on every screen; Input/TextArea still
# consume ``q`` while editing (same convention as other letter shortcuts).

GLOBAL_ALWAYS: tuple[Binding, ...] = (
    _b("?", "show_help", U.bind_help(), id="help.toggle", show=True),
    _b("f5,ctrl+r", "refresh_context", U.bind_refresh(), id="app.refresh", show=False),
    _b("ctrl+t", "self_test", t("ui-self-test"), id="app.self_test", show=False),
    _b("q", "quit", U.bind_quit(), id="app.quit", show=True),
)
LIST_SELECT: tuple[Binding, ...] = (
    _b("s,space", "toggle_select", U.bind_select(), id="list.select", show=True),
)
LIST_SELECT_ALL: tuple[Binding, ...] = (
    _b("S", "select_all_toggle", U.bind_select_all(), id="list.select_all", show=False),
)
# Sessions home only — order: Help, primary list actions, Quit last.
APP_SESSIONS: tuple[Binding, ...] = GLOBAL_ALWAYS + (
    _b("enter", "open_session", U.bind_open(), id="session.open", show=True),
    _b("ctrl+o", "import_session", U.bind_import(), id="session.import", show=False),
    _b("slash", "search_sessions", U.bind_search(), id="search.focus", show=True),
    _b("s,space", "toggle_select", U.bind_select(), id="list.select", show=True),
    _b("S", "select_all", U.bind_select_all(), id="list.select_all", show=False),
    _b("x,delete", "delete_sessions", U.bind_delete(), id="session.delete", show=False),
    _b("E", "export_session_bundle", U.bind_export_bundle(), id="session.export", show=False),
    _b("t", "tag_sessions", U.bind_tag(), id="session.tag", show=True),
)
# Pushed screens: Help · Back · Quit. Refresh stays bound, not in the rail.
SCREEN_CHROME: tuple[Binding, ...] = (
    _b("?", "show_help", U.bind_help(), id="help.toggle", show=True),
    _b("escape", "go_back", U.bind_back(), id="overlay.hide", show=True),
    _b("f5,ctrl+r", "refresh_context", U.bind_refresh(), id="app.refresh", show=False),
    _b("ctrl+t", "self_test", t("ui-self-test"), id="app.self_test", show=False),
    _b("q", "quit", U.bind_quit(), id="app.quit", show=True),
)
# App-level actions that only apply on the sessions home screen (not inherited UI).
# Quit is intentionally *not* here — it must work from Browser / etc.
SESSION_HOME_ACTIONS: frozenset[str] = frozenset(
    {
        "search_sessions",
        "open_session",
        "import_session",
        "toggle_select",
        "select_all",
        "delete_sessions",
        "export_session_bundle",
        "tag_sessions",
    }
)
# Pane counts must match TabPaneNavigation.TAB_PANES on each screen/modal.
BROWSER: tuple[Binding, ...] = (
    SCREEN_CHROME
    + tab_nav_bindings(4)
    + (
        _b("v", "focus_timeline_filter", U.bind_view(), id="browser.view_filter", show=False),
        _b(
            "enter",
            "toggle_event_reader",
            U.bind_event_reader(),
            id="browser.event_reader",
            show=True,
            priority=True,
        ),
        _b(
            "h,left",
            "prev_turn",
            U.bind_prev_turn(),
            id="events.prev_turn",
            show=True,
            priority=True,
        ),
        _b(
            "l,right",
            "next_turn",
            U.bind_next_turn(),
            id="events.next_turn",
            show=True,
            priority=True,
        ),
        _b(
            "j,down",
            "timeline_down",
            U.bind_event_down(),
            id="list.down",
            show=True,
            priority=True,
        ),
        _b(
            "k,up",
            "timeline_up",
            U.bind_event_up(),
            id="list.up",
            show=True,
            priority=True,
        ),
        _b("N", "operator_note", U.bind_note(), id="pane.notes", show=True),
        _b("O", "edit_operator_note", U.bind_edit_note(), id="session.note_edit", show=False),
        _b("slash", "search", U.bind_search(), id="search.focus", show=True),
        _b("c", "clear_filters", U.bind_clear_view(), id="browser.clear_filters", show=False),
        _b("x,delete", "delete_session", U.bind_delete(), id="session.delete", show=True),
        # y = yank detail / selection to clipboard (Textual mouse select + OSC 52).
        _b("y", "copy_detail", U.bind_copy_detail(), id="edit.copy", show=True),
        _b(
            "ctrl+shift+c",
            "copy_detail",
            U.bind_copy_detail(),
            id="edit.copy_chord",
            show=False,
            priority=True,
        ),
        _b("E", "export_bundle", U.bind_export_bundle(), id="session.export", show=True),
        _b("t", "tag_session", U.bind_tag(), id="session.tag", show=True),
    )
)
CAPABILITY_PICKER: tuple[Binding, ...] = (
    _b("escape", "cancel", U.bind_cancel(), id="overlay.hide", show=True),
    _b("q", "quit", U.bind_quit(), id="app.quit", show=True),
    _b("s,space", "toggle_select", U.bind_select(), id="list.select", show=True),
    _ctrl_s("done", U.bind_done(), id="edit.save", show=True),
)
MODAL_CANCEL_QUIT: tuple[Binding, ...] = (
    _b("escape", "cancel", U.bind_cancel(), id="overlay.hide", show=True),
    _b("q", "quit", U.bind_quit(), id="app.quit", show=True),
)
FORM_SAVE: tuple[Binding, ...] = MODAL_CANCEL_QUIT + (
    _ctrl_s("save", U.bind_save(), id="edit.save", show=True),
)
MODAL_DISMISS: tuple[Binding, ...] = (
    _b("escape", "dismiss", U.bind_cancel(), id="overlay.hide", show=True),
    _b("q", "quit", U.bind_quit(), id="app.quit", show=True),
)


def blur_focused_edit(screen: Screen) -> bool:
    """If focus is in a common edit control, blur it and return True.

    Applies to Textual ``Input``, ``TextArea``, and ``Select`` (and subclasses)
    on *any* screen — not per-field wiring. Lets Esc leave the field so Tab /
    pane keys work; a second Esc still goes back / cancels.
    """
    focused = getattr(screen, "focused", None)
    if focused is None:
        return False
    # Local import avoids circular imports with widgets at module load.
    from textual.widgets import Input, Select, TextArea

    if not isinstance(focused, (Input, TextArea, Select)):
        return False
    with suppress(Exception):
        focused.blur()
    # Clear focus so the next key uses screen-level bindings.
    with suppress(Exception):
        screen.set_focus(None)
    return True


from .quit_actions import QuitActions


class ChromeActions(QuitActions, Screen):
    """Base for screens using SCREEN_CHROME (Esc / help / refresh / quit).

    **Esc** blurs a focused Input / TextArea / Select first; then, if
    :meth:`form_is_dirty` is true, asks to discard edits; otherwise leaves.
    Override :meth:`_finish_leave` for post-confirm side effects (toasts),
    not :meth:`action_go_back`, unless you re-call :func:`blur_focused_edit`.
    """

    def action_show_help(self) -> None:
        notify_help(self)

    def action_self_test(self) -> None:
        fn = getattr(self.app, "action_self_test", None)
        if callable(fn):
            fn()

    def form_is_dirty(self) -> bool:
        """True when leaving would lose uncommitted form edits (override on editors)."""
        return False

    def action_go_back(self) -> None:
        """Esc: blur edit controls first; otherwise leave the screen."""
        if blur_focused_edit(self):
            return
        self._leave_screen()

    def action_cancel(self) -> None:
        """Esc on modals that bind cancel — same blur-then-leave as go_back."""
        if blur_focused_edit(self):
            return
        self._leave_screen()

    async def action_dismiss(self, result: object = None) -> None:  # noqa: ARG002
        """Esc on modals that bind dismiss (async to match Screen.action_dismiss)."""
        if blur_focused_edit(self):
            return
        self._leave_screen()

    def _leave_screen(self) -> None:
        """Leave after optional discard confirmation when the form is dirty."""
        if self.form_is_dirty():
            from .confirm_modal import DiscardConfirmModal

            def _done(discard: bool | None) -> None:
                if discard:
                    self._finish_leave()

            self.app.push_screen(DiscardConfirmModal(), _done)
            return
        self._finish_leave()

    def _finish_leave(self) -> None:
        """Pop this screen (override for leave side effects after confirm)."""
        with suppress(Exception):
            if len(self.app.screen_stack) > 1:
                self.app.pop_screen()


def dismiss_after_blur(screen: Screen, result: object = None) -> None:
    """Esc / Cancel on modals: leave the modal (no field-blur gate).

    Pushed full screens use :meth:`ChromeActions.action_go_back` (blur edit
    controls first). Modals always exit on Esc; dirty forms still confirm via
    :class:`~anqa.ui.confirm_modal.DiscardConfirmModal`.
    """
    dirty_fn = getattr(screen, "form_is_dirty", None)
    if callable(dirty_fn) and dirty_fn():
        from .confirm_modal import DiscardConfirmModal

        def _done(discard: bool | None) -> None:
            if discard:
                with suppress(Exception):
                    screen.dismiss(result)

        screen.app.push_screen(DiscardConfirmModal(), _done)
        return
    with suppress(Exception):
        screen.dismiss(result)


def focus_primary_list(widget: Widget) -> None:
    """Give keyboard focus to a primary list/table after populate.

    DataTable often paints a row cursor (highlight) without focus; arrows and
    Enter then appear broken. Prefer this over leaving focus on path inputs
    or inert chrome when the main work surface is a list.

    Safe to call from ``call_after_refresh`` after TabbedContent switches panes
    (focusing a hidden pane's child is unreliable until layout runs).
    """
    if widget is None:
        return
    can_focus = getattr(widget, "can_focus", None)
    if can_focus is False:
        parent = getattr(widget, "parent", None)
        if parent is not None and getattr(parent, "can_focus", False):
            widget = parent
    focus = getattr(widget, "focus", None)
    if not callable(focus):
        return
    try:
        if hasattr(widget, "cursor_type"):
            with suppress(Exception):
                widget.cursor_type = "row"
        focus()
    except Exception:
        return
    row_count = getattr(widget, "row_count", None)
    move = getattr(widget, "move_cursor", None)
    if not callable(move) or not row_count:
        return
    with suppress(Exception):
        cursor_row = getattr(widget, "cursor_row", None)
        if cursor_row is None or cursor_row < 0 or cursor_row >= row_count:
            move(row=0, column=0)
        else:
            move(row=int(cursor_row), column=0)
