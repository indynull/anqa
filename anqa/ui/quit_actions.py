"""Quit action and product modal base (avoids circular imports with bindings)."""

from __future__ import annotations

from textual.binding import Binding
from textual.screen import ModalScreen

from . import text as U


class QuitActions:
    """``q`` / action ``quit`` delegates to the app from any screen or modal.

    Expects to be mixed into a Textual :class:`~textual.dom.DOMNode` (Screen /
    ModalScreen) that provides ``.app``.
    """

    async def action_quit(self) -> None:
        app = getattr(self, "app", None)
        aq = getattr(app, "action_quit", None) if app is not None else None
        if callable(aq):
            result = aq()
            if hasattr(result, "__await__"):
                await result


MODAL_CANCEL_QUIT: tuple[Binding, ...] = (
    Binding("escape", "cancel", U.bind_cancel(), show=True, id="overlay.hide"),
    Binding("q", "quit", U.bind_quit(), show=True, id="app.quit"),
)


class Modal[T](QuitActions, ModalScreen[T]):
    """Product modal. Esc dismisses. Subclass ``BINDINGS`` must keep overlay.hide."""

    BINDINGS = list(MODAL_CANCEL_QUIT)

    def action_cancel(self) -> None:
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, None)
