"""Chips-plus-typeahead picker for session tags."""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.suggester import Suggester
from textual.widgets import Button, Input, Label, Static

from ...tags import parse_tag
from .. import text as U
from ..bindings import FORM_SAVE
from ..i18n import t
from ..quit_actions import Modal


class TagSuggester(Suggester):
    """Complete a new chip from the known vocabulary."""

    def __init__(self, vocabulary: Sequence[str], current: Sequence[str]) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self._vocabulary = vocabulary
        self._have = {name.casefold() for name in current}

    def set_current(self, current: Sequence[str]) -> None:
        self._have = {name.casefold() for name in current}

    async def get_suggestion(self, value: str) -> str | None:
        prefix = (value or "").strip()
        if not prefix:
            return None
        folded = prefix.casefold()
        for name in self._vocabulary:
            if name.casefold() in self._have:
                continue
            if name.casefold().startswith(folded):
                return name
        return None


class TagsModal(Modal[list[str] | None]):
    """Edit a working tag set. Save returns the list; Esc returns None."""

    BINDINGS = list(FORM_SAVE)

    def __init__(
        self,
        *,
        current: Sequence[str],
        vocabulary: Sequence[str],
    ) -> None:
        super().__init__()
        self._working = list(current)
        self._vocabulary = list(vocabulary)
        self._suggester = TagSuggester(self._vocabulary, self._working)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container", classes="tags-modal"):
            yield Label(t("tags-modal-title"), id="tags-modal-title")
            yield Horizontal(id="tags-chips")
            yield Input(
                placeholder=t("tags-input-placeholder"),
                suggester=self._suggester,
                id="tags-input",
            )
            yield Static("", id="tags-hints", classes="session-query-hints")
            with Horizontal(id="tags-actions"):
                yield Button(U.bind_save(), id="tags-save", variant="primary")
                yield Button(U.bind_cancel(), id="tags-cancel")

    def on_mount(self) -> None:
        self.call_after_refresh(self._ready)

    def _ready(self) -> None:
        row = self.query_one("#tags-chips", Horizontal)
        for name in self._working:
            row.mount(Button(f"{name} ×", name=name, classes="tag-chip"))
        self._paint_hints("")
        self.query_one("#tags-input", Input).focus()

    def _paint_hints(self, raw: str) -> None:
        folded = (raw or "").strip().casefold()
        have = {name.casefold() for name in self._working}
        hits = [
            name
            for name in self._vocabulary
            if name.casefold() not in have and (not folded or name.casefold().startswith(folded))
        ]
        self.query_one("#tags-hints", Static).update("  ".join(hits[:8]))

    def _add_draft(self) -> None:
        field = self.query_one("#tags-input", Input)
        parsed = parse_tag(field.value, vocabulary=self._vocabulary)
        if parsed is None:
            if (field.value or "").strip():
                self.notify(t("ui-invalid-tag"), severity="warning")
            return
        if parsed.casefold() not in {name.casefold() for name in self._working}:
            self._working.append(parsed)
            if parsed not in self._vocabulary:
                self._vocabulary.append(parsed)
            self.query_one("#tags-chips", Horizontal).mount(
                Button(f"{parsed} ×", name=parsed, classes="tag-chip")
            )
            self._suggester.set_current(self._working)
        field.value = ""
        self._paint_hints("")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "tags-input":
            return
        self._paint_hints(event.value)

    def on_input_submitted(self) -> None:
        self._add_draft()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "tags-save":
            self.action_save()
            return
        if bid == "tags-cancel":
            self.dismiss(None)
            return
        if "tag-chip" in event.button.classes:
            name = (event.button.name or "").strip()
            self._working = [item for item in self._working if item != name]
            event.button.remove()
            self._suggester.set_current(self._working)
            draft = self.query_one("#tags-input", Input).value
            self._paint_hints(draft)

    def action_save(self) -> None:
        draft = (self.query_one("#tags-input", Input).value or "").strip()
        if draft:
            self._add_draft()
        self.dismiss(list(self._working))
