"""Command palette helpers — thin wiring to app/screen actions."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from . import text as U
from .i18n import t

if TYPE_CHECKING:
    from textual.app import App
    from textual.screen import Screen
PaletteItem = tuple[str, str, Callable[[], None]]


def invoke_app_action(app: App, method: str) -> None:
    if callable(fn := getattr(app, method, None)):
        fn()


def invoke_screen_action(screen: Screen, method: str) -> None:
    if callable(fn := getattr(screen, method, None)):
        fn()


def palette_command(cmd: tuple[str, str], callback: Callable[[], None]) -> PaletteItem:
    return (cmd[0], cmd[1], callback)


def _app(app: App, method: str, title_help: tuple[str, str]) -> PaletteItem:

    def cb(m: str = method) -> None:
        invoke_app_action(app, m)

    return palette_command(title_help, cb)


def _scr(screen: Screen, method: str, title_help: tuple[str, str]) -> PaletteItem:

    def cb(m: str = method) -> None:
        invoke_screen_action(screen, m)

    return palette_command(title_help, cb)


def yield_app_commands(app: App, screen: Screen) -> Iterator[PaletteItem]:
    """Yield ``(title, help, callback)`` for the command palette."""
    from .screens.browser import BrowserScreen

    for method, th in (
        ("action_refresh_context", U.cmd_refresh()),
        (
            "action_self_test",
            (t("ui-self-test"), t("ui-self-test-help")),
        ),
        ("action_show_help", U.cmd_help()),
        ("action_quit", U.cmd_quit()),
        ("action_refresh_everything", U.cmd_full_refresh()),
    ):
        yield _app(app, method, th)
    match screen:
        case BrowserScreen():
            for method, th in (
                ("action_focus_timeline_filter", U.cmd_focus_timeline_view()),
                ("action_overview_section", U.cmd_overview_section()),
                ("action_clear_filters", U.cmd_clear_timeline_view()),
                ("action_copy_detail", U.cmd_copy_detail()),
                ("action_delete_session", U.cmd_delete_sessions()),
                ("action_export_bundle", U.cmd_export_bundle()),
                ("action_export_choose_profile", U.cmd_export_choose_profile()),
                ("action_tag_session", U.cmd_tag_sessions()),
                ("action_operator_note", U.cmd_operator_note()),
                ("action_edit_operator_note", U.cmd_edit_operator_note()),
                ("action_toggle_event_reader", U.cmd_event_reader()),
                ("action_toggle_event_raw", U.cmd_event_raw()),
                ("action_go_back", U.cmd_back_sessions()),
            ):
                yield _scr(screen, method, th)
        case _:
            for method, th in (
                ("action_search_sessions", U.cmd_search_sessions()),
                ("action_toggle_select", U.cmd_toggle_select()),
                ("action_select_all", U.cmd_select_all_none()),
                ("action_delete_sessions", U.cmd_delete_sessions()),
                ("action_import_session", U.cmd_import_session()),
                ("action_export_session_bundle", U.cmd_export_bundle()),
                ("action_export_session_choose_profile", U.cmd_export_choose_profile()),
                ("action_tag_sessions", U.cmd_tag_sessions()),
            ):
                if hasattr(app, method):
                    yield _app(app, method, th)
