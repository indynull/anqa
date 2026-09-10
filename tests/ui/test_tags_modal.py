"""Session tag picker."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.tags import parse_tag, shared_tags
from anqa.ui.i18n import setup_i18n
from anqa.ui.widgets.tags_modal import TagsModal, TagSuggester
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from .pilot_helpers import static_plain, wait_until

_APP_TCSS = Path(__file__).resolve().parents[2] / "anqa" / "ui" / "app.tcss"


def test_shared_tags_intersection() -> None:
    assert shared_tags([["review", "ui"], ["review", "bug"]]) == ["review"]


async def test_tag_suggester_skips_current() -> None:
    sug = TagSuggester(["review", "ui", "bug"], ["review"])
    assert await sug.get_suggestion("u") == "ui"
    assert await sug.get_suggestion("r") is None
    assert parse_tag("nope!") is None


class _TagApp(App):
    CSS_PATH = _APP_TCSS

    def compose(self) -> ComposeResult:
        yield Static("main")


@pytest.mark.asyncio
async def test_adding_a_tag_keeps_existing_chip() -> None:
    """Adding a chip must leave the already-mounted ones in place."""
    setup_i18n("en")
    app = _TagApp()
    result_holder: list[object] = []
    async with app.run_test(size=(80, 24)) as pilot:
        modal = TagsModal(current=["anqa"], vocabulary=["anqa"])
        app.push_screen(modal, callback=result_holder.append)
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, TagsModal) and bool(list(app.screen.query("#tags-input")))
            ),
            description="TagsModal input mounted",
        )
        field = app.screen.query_one("#tags-input", Input)
        field.value = "review"
        app.screen._add_draft()
        chips = list(app.screen.query("#tags-chips Button.tag-chip"))
        assert [btn.name for btn in chips] == ["anqa", "review"]
        app.screen.action_save()
        await wait_until(
            pilot,
            lambda: len(result_holder) == 1,
            description="save callback fired",
        )
        assert result_holder[0] == ["anqa", "review"]


@pytest.mark.asyncio
async def test_clicking_a_chip_removes_it() -> None:
    setup_i18n("en")
    app = _TagApp()
    async with app.run_test(size=(80, 24)) as pilot:
        app.push_screen(TagsModal(current=["anqa"], vocabulary=["anqa"]))
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, TagsModal)
                and bool(list(app.screen.query("Button.tag-chip")))
            ),
            description="existing chip mounted",
        )
        app.screen.query_one("Button.tag-chip").press()
        await wait_until(
            pilot,
            lambda: list(app.screen.query("#tags-chips Button.tag-chip")) == [],
            description="chip removed",
        )
        field = app.screen.query_one("#tags-input", Input)
        field.value = "anqa"
        app.screen._add_draft()
        chips = list(app.screen.query("#tags-chips Button.tag-chip"))
        assert [btn.name for btn in chips] == ["anqa"]


@pytest.mark.asyncio
async def test_tag_input_lists_vocabulary_matches() -> None:
    """The picker field lists unused vocabulary as you type."""
    from textual.widgets import Static

    setup_i18n("en")
    app = _TagApp()
    async with app.run_test(size=(80, 24)) as pilot:
        app.push_screen(TagsModal(current=["anqa"], vocabulary=["anqa", "ui", "review"]))
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, TagsModal) and bool(list(app.screen.query("#tags-input")))
            ),
            description="TagsModal input mounted",
        )
        field = app.screen.query_one("#tags-input", Input)
        field.value = "u"
        app.screen.on_input_changed(Input.Changed(field, "u"))
        hint = app.screen.query_one("#tags-hints", Static)
        assert static_plain(hint) == "ui"


@pytest.mark.asyncio
async def test_tags_modal_stays_compact_on_a_tall_terminal() -> None:
    """Chips plus a field: the dialog must not stretch with the terminal."""
    setup_i18n("en")
    heights: list[int] = []
    for rows in (24, 40):
        app = _TagApp()
        async with app.run_test(size=(80, rows)) as pilot:
            app.push_screen(TagsModal(current=["anqa"], vocabulary=["anqa", "ui"]))
            await wait_until(
                pilot,
                lambda: (
                    isinstance(app.screen, TagsModal)
                    and bool(list(app.screen.query("#tags-input")))
                ),
                description="TagsModal input mounted",
            )
            box = app.screen.query_one("#modal-container")
            actions = app.screen.query_one("#tags-actions")
            heights.append(box.size.height)
            assert actions.size.height <= 3
    assert heights[0] == heights[1]
    assert heights[0] <= 16
