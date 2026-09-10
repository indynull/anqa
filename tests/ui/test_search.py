"""One in-flight search pass; shared idle gap."""

from __future__ import annotations

from pathlib import Path

from anqa.constants import SEARCH_DEBOUNCE_S
from anqa.ui.search import SearchDebounce, SearchFlight


class _FakeTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeHost:
    def __init__(self) -> None:
        self.delay: float | None = None
        self.callback: object | None = None
        self.timer = _FakeTimer()

    def set_timer(self, delay: float, callback: object) -> _FakeTimer:
        self.delay = delay
        self.callback = callback
        self.timer = _FakeTimer()
        return self.timer


def test_search_idle_gap_matches_desktop() -> None:
    assert SEARCH_DEBOUNCE_S * 1000 == 280


def test_search_debounce_arms_shared_gap_and_replaces_the_timer() -> None:
    host = _FakeHost()
    box = SearchDebounce()
    fired: list[int] = []
    box.arm(host, lambda: fired.append(1))
    first = host.timer
    assert host.delay == SEARCH_DEBOUNCE_S
    box.arm(host, lambda: fired.append(2))
    assert first.stopped
    box.flush(lambda: fired.append(3))
    assert fired == [3]
    assert host.timer.stopped


def test_search_flight_runs_one_pass_and_replaces_it() -> None:
    flight = SearchFlight()
    assert flight.request()
    assert flight.inflight
    assert not flight.request()
    assert flight.again
    gen = flight.gen
    flight.bump()
    assert not flight.current(gen)
    assert flight.finish()
    assert not flight.inflight
    assert flight.request()
    assert not flight.finish()


def test_session_search_apply_while_inflight_keeps_the_latest_box(tmp_path: Path) -> None:
    from anqa.ui.app import AnqaApp

    traces = tmp_path / "traces"
    traces.mkdir()
    app = AnqaApp(
        traces_path=traces,
        control_socket=tmp_path / "control.sock",
        control_attach_only=True,
    )
    app._control_attached = True
    app._session_flight.inflight = True
    app._session_search = "in:anqa"
    app._apply_debounced_session_search()
    assert app._session_search_applied == "in:anqa"
    assert app._session_flight.again
    assert app._session_flight.inflight
