"""Every product modal dismisses on Esc from the shared Modal base."""

from __future__ import annotations

import pkgutil
from collections.abc import Iterator

import pytest
from anqa.notes import NoteEntry
from anqa.session.export_spec import ExportSpec
from anqa.ui.export_profile_modal import ExportProfileModal
from anqa.ui.i18n import setup_i18n
from anqa.ui.quit_actions import Modal
from anqa.ui.widgets.help_modal import HelpModal
from anqa.ui.widgets.notes_modal import NotesPickModal
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

from .pilot_helpers import wait_until


def _walk_subclasses(cls: type) -> Iterator[type]:
    for sub in cls.__subclasses__():
        yield sub
        yield from _walk_subclasses(sub)


def _load_ui_modules() -> None:
    import anqa.ui

    for module in pkgutil.walk_packages(anqa.ui.__path__, anqa.ui.__name__ + "."):
        __import__(module.name)


def _binds_escape(cls: type) -> bool:
    for klass in cls.__mro__:
        for binding in getattr(klass, "BINDINGS", ()) or ():
            if not isinstance(binding, Binding):
                continue
            keys = {part.strip() for part in binding.key.split(",")}
            if "escape" in keys:
                return True
    return False


def test_every_product_modal_binds_escape() -> None:
    """A new modal must inherit Modal (or keep overlay.hide) so Esc dismisses."""
    _load_ui_modules()
    missing = [
        f"{cls.__module__}.{cls.__name__}"
        for cls in _walk_subclasses(ModalScreen)
        if cls.__module__.startswith("anqa.") and cls is not Modal and not _binds_escape(cls)
    ]
    assert missing == []


class _Host(App):
    def compose(self) -> ComposeResult:
        yield Static("main")


@pytest.mark.asyncio
async def test_help_modal_escape_dismisses() -> None:
    setup_i18n("en")
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(HelpModal())
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, HelpModal),
            description="HelpModal mounted",
        )
        await pilot.press("escape")
        await wait_until(
            pilot,
            lambda: not isinstance(app.screen, HelpModal),
            description="HelpModal dismissed",
        )


@pytest.mark.asyncio
async def test_export_profile_escape_dismisses() -> None:
    setup_i18n("en")
    app = _Host()
    result: list[str | None] = []
    async with app.run_test(size=(80, 24)) as pilot:
        app.push_screen(
            ExportProfileModal(
                profiles={
                    "archive-full": ExportSpec(
                        profile_id="archive-full", name="Full", renderer="markdown"
                    )
                }
            ),
            result.append,
        )
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, ExportProfileModal),
            description="ExportProfileModal mounted",
        )
        await pilot.press("escape")
        await wait_until(pilot, lambda: len(result) == 1, description="export modal dismissed")
        assert result[0] is None


@pytest.mark.asyncio
async def test_notes_pick_escape_dismisses() -> None:
    setup_i18n("en")
    app = _Host()
    result: list[NoteEntry | None] = []
    note = NoteEntry.new(turn_index=0, fields={"summary": "one"}, note_id="n-a")
    async with app.run_test(size=(80, 24)) as pilot:
        app.push_screen(NotesPickModal(notes=[note]), result.append)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, NotesPickModal),
            description="NotesPickModal mounted",
        )
        await pilot.press("escape")
        await wait_until(pilot, lambda: len(result) == 1, description="notes pick dismissed")
        assert result[0] is None
