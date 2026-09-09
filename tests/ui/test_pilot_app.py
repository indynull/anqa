"""Headless Textual tests via App.run_test() + Pilot.

See https://textual.textualize.io/guide/testing/ and AGENTS.md §4.5c.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from anqa.diagnostics.self_test import CheckResult, SelfTestReport
from anqa.ui.app import AnqaApp
from anqa.ui.widgets.activity_bar import (
    ActivityBar,
    activity_counters_from_app,
    build_activity_line,
)
from anqa.ui.widgets.self_test_modal import SelfTestModal

from .pilot_helpers import wait_until


def _minimal_traces(work: Path) -> Path:
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sd = traces / "pilot-sess-1"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "pilot-sess-1", "cwd": "/workspace"},
                "session_summary": "pilot",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m1",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "ts": "2026-06-25T00:01:00Z", "outcome": "success"})
        + "\n",
        encoding="utf-8",
    )
    return traces


@pytest.mark.asyncio
async def test_launch_focuses_session_table_with_empty_filter(tmp_path: Path) -> None:
    """Home compose focuses the list. Filter stays empty."""
    from textual.widgets import DataTable, Input

    traces = _minimal_traces(tmp_path / "w")
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: app.query_one("#session-table", DataTable) is not None,
            description="session table mounted",
        )
        assert app.focused is app.query_one("#session-table", DataTable)
        assert app.query_one("#session-search-input", Input).value == ""


@pytest.mark.asyncio
async def test_filter_clears_terminal_device_reply(tmp_path: Path) -> None:
    """A Device Attributes + Kitty ack pasted into Filter does not stay."""
    from textual.widgets import Input

    traces = _minimal_traces(tmp_path / "w")
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        inp = app.query_one("#session-search-input", Input)
        inp.value = "?62;52;c^_Gi=1768635629;OK^\\"
        await wait_until(pilot, lambda: inp.value == "", description="probe reply cleared")
        assert app._session_search == ""


@pytest.mark.asyncio
async def test_session_query_hints_complete_tag_token(tmp_path: Path) -> None:
    """The hint row under Filter completes ``tag:`` from catalog tags."""
    from anqa.tags import save_tags
    from textual.widgets import Static

    from .pilot_helpers import static_plain

    traces = _minimal_traces(tmp_path / "w")
    save_tags(traces / "pilot-sess-1", ["review"])
    app = AnqaApp(traces_path=traces, control_socket=None)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: bool(app._meta_only), description="sessions loaded")
        meta, label = app._meta_only[0]
        app._meta_only = [(meta, label)]
        meta.tags = ("review",)
        app._session_search = "tag:"
        app._refresh_query_hints()
        hint = app.query_one("#session-query-hints", Static)
        assert "tag:review" in static_plain(hint)


@pytest.mark.asyncio
async def test_app_mounts_activity_bar(tmp_path: Path) -> None:
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: bool(list(app.query(ActivityBar))),
            description="ActivityBar mounted",
        )
        bar = list(app.query(ActivityBar))[0]
        bar.refresh_activity()
        await pilot.pause()
        counts = activity_counters_from_app(app)
        line = build_activity_line(sessions_loaded=counts["sessions"])
        plain = line.plain.lower()
        assert "sessions" in plain


@pytest.mark.asyncio
async def test_self_test_modal_applies_report(tmp_path: Path) -> None:
    work = tmp_path / "w"
    work.mkdir()
    (work / "runs" / "traces").mkdir(parents=True)
    app = AnqaApp(traces_path=work / "runs" / "traces")
    report = SelfTestReport(
        checks=[
            CheckResult("app_home", "Config home writable", True, "ok"),
            CheckResult("catalog", "Session store", True, "ok"),
        ]
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(SelfTestModal())
        await wait_until(
            pilot,
            lambda: any(isinstance(s, SelfTestModal) for s in app.screen_stack),
            description="SelfTestModal on stack",
        )
        modal = next(s for s in app.screen_stack if isinstance(s, SelfTestModal))
        modal._apply_report(report)
        await wait_until(
            pilot,
            lambda: bool(getattr(app, "_self_test_summary", "")),
            description="self-test summary cached on app",
        )
        assert str(app._self_test_summary).startswith("self-test")
