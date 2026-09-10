"""Search-as-you-type: one idle gap, one in-flight pass.

The terminal session list and Timeline (and the desktop palette) share this
idle gap. A newer query replaces the pass in flight: the running fetch is
not painted, and the latest box is fetched next.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Protocol

from textual.timer import Timer

from ..constants import SEARCH_DEBOUNCE_S


class TimerHost(Protocol):
    """Widget or app that can arm a Textual timer."""

    def set_timer(self, delay: float, callback: Callable[[], None]) -> Timer: ...


class SearchDebounce:
    """One idle-gap timer. Arming again cancels the previous fire."""

    def __init__(self) -> None:
        self._timer: Timer | None = None

    def arm(self, host: TimerHost, callback: Callable[[], None]) -> None:
        """Start (or restart) the idle gap.

        :param host: App or screen that owns ``set_timer``.
        :param callback: Runs after :data:`~anqa.constants.SEARCH_DEBOUNCE_S`.
        """
        self.cancel()
        self._timer = host.set_timer(SEARCH_DEBOUNCE_S, callback)

    def cancel(self) -> None:
        """Drop a pending idle fire."""
        timer = self._timer
        self._timer = None
        if timer is None:
            return
        with suppress(Exception):
            timer.stop()

    def flush(self, callback: Callable[[], None]) -> None:
        """Cancel the idle wait and run *callback* now (Enter)."""
        self.cancel()
        callback()


class SearchFlight:
    """At most one search pass. A newer query replaces the one in flight."""

    def __init__(self) -> None:
        self.gen = 0
        self.inflight = False
        self.again = False

    def bump(self) -> int:
        """Invalidate the current pass. Return the new generation."""
        self.gen += 1
        return self.gen

    def request(self) -> bool:
        """Mark that a pass should run.

        :returns: True when the caller should start the worker. False when a
            pass is already in flight (it will run again with the latest box).
        """
        if self.inflight:
            self.again = True
            return False
        self.inflight = True
        self.again = False
        return True

    def current(self, gen: int) -> bool:
        """True when *gen* is still the live search."""
        return gen == self.gen

    def finish(self) -> bool:
        """End the in-flight pass.

        :returns: True when the caller should start another pass for the
            latest query.
        """
        self.inflight = False
        again = self.again
        self.again = False
        return again
