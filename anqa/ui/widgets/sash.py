"""Drag sash between two horizontal panes.

Textual has no split container. The usual pattern is a vertical
:class:`~textual.widgets.Rule` as the separator (see Textualize/textual
discussion 4834): one cell, ``pointer: ew-resize``, drag sets the
start pane's width in cells.
"""

from __future__ import annotations

from textual.css.query import NoMatches
from textual.events import MouseDown, MouseMove, MouseUp
from textual.widget import Widget
from textual.widgets import Rule


class VerticalSash(Rule):
    """Vertical :class:`Rule` that resizes the pane on its start side.

    :param target_id: Widget id of the pane to the start side (the list).
    :param min_size: Smallest width either pane may shrink to.
    """

    DEFAULT_CSS = """
    VerticalSash {
        color: $text-muted;
        background: transparent;
        pointer: ew-resize;
    }
    VerticalSash.-vertical {
        width: 1;
        height: 1fr;
        margin: 0;
    }
    VerticalSash:hover {
        color: $text;
    }
    """

    def __init__(
        self,
        target_id: str,
        *,
        min_size: int = 20,
        id: str | None = None,
    ) -> None:
        super().__init__(orientation="vertical", line_style="solid", id=id)
        self.target_id = target_id
        self.min_size = min_size
        self._dragging = False

    def on_mouse_down(self, event: MouseDown) -> None:
        event.stop()
        self._dragging = True
        self.capture_mouse()

    def on_mouse_up(self, event: MouseUp) -> None:
        if not self._dragging:
            return
        event.stop()
        self._dragging = False
        self.release_mouse()

    def on_mouse_move(self, event: MouseMove) -> None:
        if not self._dragging:
            return
        event.stop()
        parent = self.parent
        if not isinstance(parent, Widget):
            return
        try:
            pane = parent.query_one(f"#{self.target_id}")
        except NoMatches:
            return
        inner = parent.content_size.width
        left = int(event.screen_x) - parent.region.x
        max_w = max(self.min_size, inner - self.min_size - self.size.width)
        pane.styles.width = max(self.min_size, min(max_w, left))
