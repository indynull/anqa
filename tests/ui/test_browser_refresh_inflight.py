"""Browser live refresh respects per-session inflight lock."""

from __future__ import annotations

from pathlib import Path

from anqa.job_pools import get_live_refresh_pool
from anqa.models import TraceEvent
from anqa.session_inflight import (
    KIND_REFRESH,
    clear,
    is_inflight,
    try_begin,
)
from anqa.ui.screens.browser import BrowserScreen


def setup_function() -> None:
    clear(KIND_REFRESH)


def teardown_function() -> None:
    clear(KIND_REFRESH)


def _held_event() -> TraceEvent:
    return TraceEvent(index=0, event_type="user_message_chunk", content="hi")


def _screen(sd: Path) -> BrowserScreen:
    return BrowserScreen(sd)


class _RefreshAdapter:
    """Adapter the browser actually calls via ``require_adapter``."""

    def __init__(
        self,
        *,
        stamp: tuple[float, int, int, int],
        load_meta: object | None = None,
        parse_timeline: object | None = None,
    ) -> None:
        self._stamp = stamp
        self._load_meta = load_meta
        self._parse_timeline = parse_timeline

    def timeline_stamp(self, _ref: object) -> tuple[float, int, int, int]:
        return self._stamp

    def load_meta(self, _ref: object) -> object:
        assert self._load_meta is not None
        return self._load_meta(_ref) if callable(self._load_meta) else self._load_meta

    def load_detail(self, ref: object) -> object:
        return self.load_meta(ref)

    def parse_timeline(self, _ref: object) -> object:
        assert self._parse_timeline is not None
        if callable(self._parse_timeline):
            return self._parse_timeline(_ref)
        return self._parse_timeline


def test_control_first_page_keeps_owner_total_and_skips_adapter(
    tmp_path: Path, monkeypatch
) -> None:
    """Attached open uses control only; paged length is not the event total."""
    from anqa.ui.screens import browser as browser_mod

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    screen = _screen(sd)
    screen._uses_control_data = lambda: True  # type: ignore[method-assign]
    screen._session_control_ref = lambda: "grok:s"  # type: ignore[method-assign]
    adapter_calls: list[str] = []

    def boom(ref: object) -> object:
        adapter_calls.append(str(ref))
        raise AssertionError(f"require_adapter on catalog id: {ref}")

    monkeypatch.setattr("anqa.harness.registry.require_adapter", boom)
    monkeypatch.setattr("anqa.session.control_views.require_adapter", boom)
    monkeypatch.setattr(browser_mod, "require_adapter", boom)
    monkeypatch.setattr(
        "anqa.session.control_views.overview_input_stamp",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("disk stamp on client")),
    )

    events = [{"index": i, "type": "user_message_chunk", "content": f"e{i}"} for i in range(200)]

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            return {
                "sessionId": "s",
                "meta": {
                    "sessionId": "s",
                    "path": "grok:s",
                    "harness": "grok",
                    "status": "complete",
                    "numEvents": 250,
                },
                "timeline": {"total": 250, "lazy": True},
                "notes": {"revision": "r1", "notes": []},
            }

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            return {"events": events, "total": 250}

        async def session_diff(self, _ref: str) -> object:
            return {"sessionId": "s", "points": []}

        async def notes_list(self, _ref: str) -> object:
            return {"notes": []}

    class _App:
        def session_access(self) -> _Access:
            return _Access()

        def is_control_client(self) -> bool:
            return True

    monkeypatch.setattr(browser_mod, "resolve_ui_app", lambda _s: _App())
    monkeypatch.setattr(browser_mod, "call_ui", lambda _app, cb, *a, **k: None)
    total = screen._load_control_first_page()
    assert total == 250
    screen._commit_loaded_session()
    assert adapter_calls == []
    assert screen.meta is not None
    assert screen.meta.num_events == 250
    assert len(screen.timeline) == 200


def test_live_refresh_skips_second_enqueue(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = _screen(sd)

    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    submitted: list[str] = []
    pool = get_live_refresh_pool()
    real = pool.submit
    pool.submit = lambda label, fn: submitted.append(label)  # type: ignore[method-assign]
    try:
        assert try_begin(KIND_REFRESH, sd) is True
        screen._live_refresh_from_fs()
        assert submitted == []
        assert screen._live_refresh_pending is True
        assert is_inflight(KIND_REFRESH, sd) is True
        screen._live_refresh_from_fs()
        assert submitted == []
    finally:
        pool.submit = real  # type: ignore[method-assign]


def test_live_refresh_enqueues_when_lock_free(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = _screen(sd)

    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    submitted: list[str] = []
    pool = get_live_refresh_pool()
    real = pool.submit
    pool.submit = lambda label, fn: submitted.append(label)  # type: ignore[method-assign]
    try:
        screen._live_refresh_from_fs()
        assert submitted == [f"refresh {sd.name}"]
        assert screen._live_refresh_busy is True
        assert is_inflight(KIND_REFRESH, sd) is True
    finally:
        pool.submit = real  # type: ignore[method-assign]


def test_worker_done_runs_coalesced_follow_up(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = _screen(sd)
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    from anqa.session_inflight import request_rerun

    request_rerun(KIND_REFRESH, sd)
    calls: list[str] = []
    screen._live_refresh_from_fs = (  # type: ignore[method-assign]
        lambda **kwargs: calls.append(("tick", bool(kwargs.get("heartbeat"))))
    )
    screen._live_refresh_worker_done()
    assert screen._live_refresh_busy is False
    assert calls == [("tick", False)]
    assert is_inflight(KIND_REFRESH, sd) is False


def test_live_refresh_heartbeat_coalesces_flag(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = _screen(sd)

    screen._session_needs_live_timeline = lambda: True  # type: ignore[method-assign]
    submitted: list[str] = []
    pool = get_live_refresh_pool()
    real = pool.submit
    pool.submit = lambda label, fn: submitted.append(label)  # type: ignore[method-assign]
    try:
        screen._live_refresh_from_fs()
        assert submitted == [f"refresh {sd.name}"]
        screen._live_refresh_from_fs(heartbeat=True)
        assert screen._live_refresh_pending is True
        assert screen._light_refresh_heartbeat is True
    finally:
        pool.submit = real  # type: ignore[method-assign]


def test_load_data_light_heartbeat_reloads_meta(tmp_path: Path, monkeypatch) -> None:
    """Heartbeat re-reads signals even when timeline stamp is unchanged."""
    from anqa.models import SessionMeta
    from anqa.ui.screens import browser as browser_mod

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = _screen(sd)
    screen.timeline = [_held_event()]  # non-empty so unchanged path skips parse
    screen._last_trace_mtime = (1.0, 0, 0, 0)
    screen._last_signals_mtime = 1.0
    screen._light_refresh_heartbeat = True
    screen.meta = SessionMeta(
        session_id="s",
        session_dir=sd,
        context_window_usage_pct=10,
        context_tokens_used=1,
        context_window_tokens=500000,
    )
    calls: list[str] = []

    monkeypatch.setattr(
        browser_mod,
        "require_adapter",
        lambda _ref: _RefreshAdapter(
            stamp=(1.0, 0, 0, 0),
            load_meta=lambda _p: SessionMeta(
                session_id="s",
                session_dir=sd,
                context_window_usage_pct=35,
                context_tokens_used=178996,
                context_window_tokens=500000,
            ),
            parse_timeline=lambda _p: calls.append("parse") or [],
        ),
    )

    def _call_ui(_app, cb, *a, **k):
        name = getattr(cb, "__name__", str(cb))
        calls.append(name)
        if name == "_live_refresh_worker_done":
            return cb(*a, **k)
        return None

    monkeypatch.setattr(browser_mod, "call_ui", _call_ui)
    screen._signals_mtime = lambda: 1.0  # type: ignore[method-assign]
    screen._rebuild_indices = lambda: calls.append("rebuild")  # type: ignore[method-assign]
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    screen._load_data_light_job()
    assert "parse" not in calls
    assert "rebuild" not in calls
    assert screen.meta is not None
    assert screen.meta.context_window_usage_pct == 35
    assert screen.meta.num_events == 1
    assert "_populate_ui_light" in calls
    assert "_live_refresh_worker_done" in calls
    assert is_inflight(KIND_REFRESH, sd) is False


def test_load_data_light_skips_meta_on_noise_fs_tick(tmp_path: Path, monkeypatch) -> None:
    """Unchanged stamp + signals must not re-load meta (live FS noise)."""
    from anqa.models import SessionMeta
    from anqa.ui.screens import browser as browser_mod

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = _screen(sd)
    screen.timeline = [_held_event()]
    screen._last_trace_mtime = (1.0, 0, 0, 0)
    screen._last_signals_mtime = 1.0
    screen._light_refresh_heartbeat = False
    screen.meta = SessionMeta(
        session_id="s",
        session_dir=sd,
        context_window_usage_pct=10,
        context_tokens_used=1,
        context_window_tokens=500000,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        browser_mod,
        "require_adapter",
        lambda _ref: _RefreshAdapter(
            stamp=(1.0, 0, 0, 0),
            load_meta=lambda *_a, **_k: calls.append("meta") or screen.meta,
            parse_timeline=lambda _p: calls.append("parse") or [],
        ),
    )
    monkeypatch.setattr(
        browser_mod,
        "call_ui",
        lambda _app, cb, *a, **k: (
            calls.append(getattr(cb, "__name__", str(cb)))
            or (cb(*a, **k) if getattr(cb, "__name__", "") == "_live_refresh_worker_done" else None)
        ),
    )
    screen._signals_mtime = lambda: 1.0  # type: ignore[method-assign]
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    screen._load_data_light_job()
    assert "meta" not in calls
    assert "parse" not in calls
    assert "_populate_ui_light" not in calls
    assert screen.meta.context_window_usage_pct == 10
    assert is_inflight(KIND_REFRESH, sd) is False


def test_load_data_light_always_parses_on_stamp_change(tmp_path: Path, monkeypatch) -> None:
    """Stamp change always re-parses — no second min-gap that hides new rows."""
    from anqa.models import SessionMeta, TraceEvent
    from anqa.ui.screens import browser as browser_mod

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = _screen(sd)
    screen.timeline = [
        TraceEvent(index=0, timestamp=1.0, event_type="user_message_chunk", content="hi")
    ]
    screen._last_trace_mtime = (1.0, 10, 0, 0)
    screen._last_signals_mtime = 1.0
    screen._last_timeline_parse_at = 1e18  # "just parsed" — must not block
    screen._light_refresh_heartbeat = False
    screen.meta = SessionMeta(session_id="s", session_dir=sd)
    new_ev = TraceEvent(index=1, timestamp=2.0, event_type="tool_call", content="bash")
    calls: list[str] = []
    monkeypatch.setattr(
        browser_mod,
        "require_adapter",
        lambda _ref: _RefreshAdapter(
            stamp=(2.0, 99, 0, 0),
            load_meta=lambda *_a, **_k: calls.append("meta") or screen.meta,
            parse_timeline=lambda _p: calls.append("parse") or [*screen.timeline, new_ev],
        ),
    )
    monkeypatch.setattr(
        browser_mod,
        "call_ui",
        lambda _app, cb, *a, **k: (
            calls.append(getattr(cb, "__name__", str(cb)))
            or (cb(*a, **k) if getattr(cb, "__name__", "") == "_live_refresh_worker_done" else None)
        ),
    )
    screen._signals_mtime = lambda: 1.0  # type: ignore[method-assign]
    screen._rebuild_indices = lambda: calls.append("rebuild")  # type: ignore[method-assign]
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    screen._load_data_light_job()
    assert "parse" in calls
    assert "rebuild" in calls
    assert screen._last_trace_mtime == (2.0, 99, 0, 0)
    assert len(screen.timeline) == 2
    assert "_populate_ui_light" in calls
    assert is_inflight(KIND_REFRESH, sd) is False


def test_load_data_light_control_skips_timeline_when_overview_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    """Attached light refresh always asks the owner; it does not refetch the page."""
    from anqa.models import SessionMeta
    from anqa.ui.screens import browser as browser_mod

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    screen = _screen(sd)
    screen._uses_control_data = lambda: True  # type: ignore[method-assign]
    screen._session_control_ref = lambda: "grok:s"  # type: ignore[method-assign]
    screen.meta = SessionMeta(session_id="s", session_dir=sd, num_events=1)
    screen.timeline = [_held_event()]
    screen._timeline_owner_total = 1
    screen._last_overview_stamp = (1, "complete", "", "")
    calls: list[str] = []

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            calls.append("overview")
            return {
                "sessionId": "s",
                "meta": {"sessionId": "s", "status": "complete", "numEvents": 1},
                "timeline": {"total": 1},
                "notes": {"revision": ""},
            }

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            calls.append("timeline")
            return {"events": [], "total": 1}

    class _App:
        def session_access(self) -> _Access:
            return _Access()

    monkeypatch.setattr(browser_mod, "resolve_ui_app", lambda _s: _App())
    monkeypatch.setattr(
        browser_mod,
        "call_ui",
        lambda _app, cb, *a, **k: (
            cb(*a, **k) if getattr(cb, "__name__", "") == "_live_refresh_worker_done" else None
        ),
    )
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    screen._load_data_light_job()
    assert calls == ["overview"]
    assert is_inflight(KIND_REFRESH, sd) is False


def test_load_data_light_control_refetches_when_owner_total_grows(
    tmp_path: Path, monkeypatch
) -> None:
    """A larger owner total fetches growth through control, not disk."""
    from anqa.models import SessionMeta
    from anqa.ui.screens import browser as browser_mod

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    screen = _screen(sd)
    screen._uses_control_data = lambda: True  # type: ignore[method-assign]
    screen._session_control_ref = lambda: "grok:s"  # type: ignore[method-assign]
    screen.meta = SessionMeta(session_id="s", session_dir=sd, num_events=1)
    screen.timeline = [_held_event()]
    screen._timeline_owner_total = 1
    screen._last_overview_stamp = (1, "running", "", "")
    calls: list[str] = []

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            calls.append("overview")
            return {
                "sessionId": "s",
                "meta": {"sessionId": "s", "status": "running", "numEvents": 2},
                "timeline": {"total": 2},
                "notes": {"revision": ""},
            }

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            calls.append("timeline")
            return {
                "events": [
                    {"index": 0, "type": "user_message_chunk", "content": "hi"},
                    {"index": 1, "type": "agent_message_chunk", "content": "ok"},
                ],
                "total": 2,
            }

        async def session_timeline_event(self, _ref: str, **kwargs: object) -> object:
            return {"index": 0, "type": "user_message_chunk", "content": "hi"}

    class _App:
        def session_access(self) -> _Access:
            return _Access()

    monkeypatch.setattr(browser_mod, "resolve_ui_app", lambda _s: _App())
    monkeypatch.setattr(
        browser_mod,
        "call_ui",
        lambda _app, cb, *a, **k: (
            cb(*a, **k) if getattr(cb, "__name__", "") == "_live_refresh_worker_done" else None
        ),
    )
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    screen._load_data_light_job()
    assert "overview" in calls
    assert screen.meta is not None
    assert screen.meta.num_events == 2
    assert is_inflight(KIND_REFRESH, sd) is False


def test_load_data_light_control_timeout_is_soft(tmp_path: Path, monkeypatch) -> None:
    """A hung session/overview must not crash the live-refresh worker."""
    from anqa.ui.screens import browser as browser_mod

    sd = tmp_path / "019f-sess"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    screen = _screen(sd)
    screen._uses_control_data = lambda: True  # type: ignore[method-assign]
    screen._session_control_ref = lambda: "s"  # type: ignore[method-assign]

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            raise TimeoutError

    class _App:
        def session_access(self) -> _Access:
            return _Access()

    monkeypatch.setattr(browser_mod, "resolve_ui_app", lambda _s: _App())
    monkeypatch.setattr(
        browser_mod,
        "call_ui",
        lambda _app, cb, *a, **k: (
            cb(*a, **k) if getattr(cb, "__name__", "") == "_live_refresh_worker_done" else None
        ),
    )
    assert try_begin(KIND_REFRESH, sd) is True
    screen._live_refresh_busy = True
    screen._load_data_light_job()
    assert is_inflight(KIND_REFRESH, sd) is False
