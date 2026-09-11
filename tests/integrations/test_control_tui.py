"""TUI attaches as a control client; never owns the socket."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Callable
from contextlib import suppress
from importlib import import_module
from pathlib import Path

import pytest
from anqa.ui.app import AnqaApp
from textual.pilot import Pilot


def _short_sock(name: str) -> Path:
    """Short unique AF_UNIX path (macOS path limit + multi-user / xdist safe)."""
    root = Path(tempfile.mkdtemp(prefix="anqa-ctl-"))
    return root / name


async def _wait_until(
    pilot: Pilot,
    predicate: Callable[[], bool],
    *,
    description: str,
) -> None:
    for _ in range(80):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError(f"timed out waiting for {description}")


def _write_session(traces: Path) -> Path:
    session_dir = traces / "session-tui-control"
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": session_dir.name}, "generated_title": "TUI control"}),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1000,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "open here"},
                    "_meta": {"promptIndex": 11},
                }
            },
        },
        {
            "timestamp": 1001,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "opened"},
                }
            },
        },
    ]
    (session_dir / "updates.jsonl").write_text(
        "".join(json.dumps(update) + "\n" for update in updates),
        encoding="utf-8",
    )
    (session_dir / "rewind_points.jsonl").write_text(
        json.dumps(
            {
                "prompt_index": 11,
                "file_snapshots": {"app.py": "old"},
                "after_snapshots": {"app.py": "opened"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return session_dir


async def _rpc_call(
    socket_path: Path,
    request_id: int,
    method: str,
    params: dict,
) -> dict:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    writer.write(json.dumps(request).encode("utf-8") + b"\n")
    await writer.drain()
    while True:
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=3))
        if response.get("id") == request_id:
            break
    writer.close()
    await writer.wait_closed()
    return response


@pytest.mark.asyncio
async def test_tui_attaches_to_daemon_and_lists_via_control(tmp_path: Path) -> None:
    daemon = import_module("anqa.control.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    session_dir = _write_session(traces)
    socket_path = _short_sock("tui-attach.sock")
    owner = daemon.build_domain_control_server(
        socket_path=socket_path,
        traces_path=traces,
    )
    await owner.start()
    try:
        app = AnqaApp(
            traces_path=traces,
            control_socket=socket_path,
            control_attach_only=True,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_until(pilot, app.is_control_client, description="control client attach")
            assert not app.is_control_owner()
            listed = await app.control_session_list()
            assert listed["matched"] >= 1
            assert any(
                row.get("sessionId") == session_dir.name for row in listed.get("sessions", [])
            )
            # Headless owner notifies session/selected (no TUI open callback).
            response = await _rpc_call(
                socket_path,
                1,
                "session/open",
                {"session": session_dir.name, "promptIndex": 11},
            )
            assert response["result"] == {"opened": True}
            # Stop notify listener before Textual teardown races Header updates.
            app._prepare_clean_exit()
            await pilot.pause()
        assert socket_path.exists()
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_attach_copy_toasts_failure_only() -> None:
    """Success attach has no toast id; failure copy does not mention disk."""
    from anqa.ui.i18n import t

    app_src = Path(__file__).resolve().parents[2] / "anqa" / "ui" / "app.py"
    text = app_src.read_text(encoding="utf-8")
    assert "ui-control-socket-attached" not in text
    assert "ui-control-socket-attach-failed" in text
    assert "local disk" not in t("ui-control-socket-attach-failed").lower()


@pytest.mark.asyncio
async def test_tui_attach_does_not_toast_scanning_control(tmp_path: Path) -> None:
    """Attach catalog load must not toast ``Scanning control…`` (disk-scan copy)."""
    daemon = import_module("anqa.control.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    session_dir = _write_session(traces)
    socket_path = _short_sock("tui-scan-toast.sock")
    owner = daemon.build_domain_control_server(
        socket_path=socket_path,
        traces_path=traces,
    )
    serve = asyncio.create_task(daemon.serve_control_forever(owner, write_pid=False))
    try:
        for _ in range(50):
            if socket_path.exists():
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("control socket missing")
        app = AnqaApp(
            traces_path=traces,
            control_socket=socket_path,
            control_attach_only=True,
        )
        toasts: list[str] = []
        real_notify = app.notify

        def _spy(message: object, *args: object, **kwargs: object) -> object:
            toasts.append(str(message))
            return real_notify(message, *args, **kwargs)

        app.notify = _spy  # type: ignore[method-assign]
        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_until(pilot, app.is_control_client, description="control client attach")
            await _wait_until(
                pilot,
                lambda: any(m.session_id == session_dir.name for m, _ in app._meta_only),
                description="catalog rows from control",
            )
            assert not any("Scanning" in msg for msg in toasts)
            assert not any("Attached" in msg for msg in toasts)
            toasts.clear()
            app._control_session_changed_ui(session_dir.name)
            assert app._pending_sessions_reload_quiet is True
            assert app._sessions_reload_timer is not None
            assert not any("Scanning" in msg or "Loaded" in msg for msg in toasts)
            app._prepare_clean_exit()
            await pilot.pause()
    finally:
        serve.cancel()
        with suppress(asyncio.CancelledError):
            await serve
        await owner.close()


def test_sessions_reload_loud_wins_over_quiet(tmp_path: Path) -> None:
    """F5 (loud) after a live quiet schedule must still toast."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    app = AnqaApp(
        traces_path=traces,
        control_socket=None,
        control_attach_only=False,
    )

    class _Timer:
        def stop(self) -> None:
            return None

    fired: list[tuple[float, object]] = []

    def _set_timer(delay: float, callback: object) -> _Timer:
        fired.append((delay, callback))
        return _Timer()

    app.set_timer = _set_timer  # type: ignore[method-assign]
    app._schedule_sessions_reload(quiet=True)
    assert app._pending_sessions_reload_quiet is True
    assert fired
    app._schedule_sessions_reload(quiet=False)
    assert app._pending_sessions_reload_quiet is False
    app._schedule_sessions_reload(quiet=True)
    assert app._pending_sessions_reload_quiet is False


@pytest.mark.asyncio
async def test_tags_changed_schedules_quiet_reload(tmp_path: Path) -> None:
    """tags/changed reloads the list without a positional quiet flag."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    app = AnqaApp(
        traces_path=traces,
        control_socket=None,
        control_attach_only=False,
    )

    class _Timer:
        def stop(self) -> None:
            return None

    def _set_timer(_delay: float, _callback: object) -> _Timer:
        return _Timer()

    def _call_later(fn: object, *args: object, **kwargs: object) -> None:
        fn(*args, **kwargs)

    app.set_timer = _set_timer  # type: ignore[method-assign]
    app.call_later = _call_later  # type: ignore[method-assign]

    await app._on_control_notification("tags/changed", {"sessionIds": ["sess-1"]})

    assert app._pending_sessions_reload_quiet is True


@pytest.mark.asyncio
async def test_tui_dead_socket_does_not_claim_attach_or_disk_catalog(
    tmp_path: Path,
) -> None:
    """Dead control socket: not a client; no silent disk catalog when socket is set."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    _write_session(traces)
    dead_sock = _short_sock("dead.sock")
    app = AnqaApp(
        traces_path=traces,
        control_socket=dead_sock,
        control_attach_only=True,
    )
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(40):
            if not app._control_attached:
                break
            await pilot.pause()
        assert not app.is_control_client()
        assert app._control_attached is False
        # Product path: control socket configured ⇒ no disk catalog fallback.
        assert app._meta_only == []
        app._prepare_clean_exit()
        await pilot.pause()


@pytest.mark.asyncio
async def test_tui_offline_no_socket_still_loads_disk_catalog(tmp_path: Path) -> None:
    """Explicit offline (no control socket): local traces catalog."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    session_dir = _write_session(traces)
    app = AnqaApp(
        traces_path=traces,
        control_socket=None,
        control_attach_only=False,
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_until(
            pilot,
            lambda: bool(app._meta_only),
            description="offline disk catalog",
        )
        assert not app.is_control_client()
        names = {(m.session_id or m.session_dir.name) for m, _ in app._meta_only}
        assert session_dir.name in names
        app._prepare_clean_exit()
        await pilot.pause()


@pytest.mark.asyncio
async def test_browser_loads_timeline_via_control_when_attached(
    tmp_path: Path,
) -> None:
    """Session browser hydrates timeline from control, not a private disk parse."""
    from unittest.mock import patch

    from anqa.ui.screens.browser import BrowserScreen

    daemon = import_module("anqa.control.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    session_dir = _write_session(traces)
    socket_path = _short_sock("tui-browser.sock")
    owner = daemon.build_domain_control_server(
        socket_path=socket_path,
        traces_path=traces,
    )
    await owner.start()
    try:
        app = AnqaApp(
            traces_path=traces,
            control_socket=socket_path,
            control_attach_only=True,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_until(pilot, app.is_control_client, description="attach")
            import traceback

            from anqa.harness import registry as registry_mod

            real_require = registry_mod.require_adapter

            def _tui_must_not_require(ref: object) -> object:
                stack = "".join(traceback.format_stack())
                if "anqa/ui/screens/browser.py" in stack or "anqa/ui/app.py" in stack:
                    raise AssertionError(f"TUI require_adapter on {ref}")
                return real_require(ref)

            with (
                patch.object(registry_mod, "require_adapter", _tui_must_not_require),
                patch(
                    "anqa.ui.screens.browser.require_adapter",
                    side_effect=AssertionError("disk parse forbidden when attached"),
                ),
                patch(
                    "anqa.session.control_views.overview_input_stamp",
                    side_effect=AssertionError("client must not stamp disk"),
                ),
            ):
                app.open_session_path(session_dir)
                await _wait_until(
                    pilot,
                    lambda: (
                        isinstance(app.screen, BrowserScreen)
                        and bool(getattr(app.screen, "timeline", None))
                    ),
                    description="browser timeline via control",
                )
            screen = app.screen
            assert isinstance(screen, BrowserScreen)
            assert screen._uses_control_data()
            assert any(
                "open here" in (e.content or "") or "opened" in (e.content or "")
                for e in (screen.timeline or [])
            )
            assert [hunk.path for point in screen._diff_doc.points for hunk in point.files] == [
                "app.py"
            ]
            app._prepare_clean_exit()
            await pilot.pause()
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_confirm_control_attach_returns_false_on_dead_socket(
    tmp_path: Path,
) -> None:
    """Domain helper: initialize failure is a hard False, not silent success."""
    app = AnqaApp(
        traces_path=tmp_path / "w" / "runs" / "traces",
        control_socket=_short_sock("missing.sock"),
        control_attach_only=True,
    )
    (tmp_path / "w" / "runs" / "traces").mkdir(parents=True)
    assert await app._confirm_control_attach() is False
    assert app._control_attached is False


@pytest.mark.asyncio
async def test_tui_control_helpers_are_client_noops(tmp_path: Path) -> None:
    app = AnqaApp.__new__(AnqaApp)
    session = tmp_path / "session-publish"
    # Client path: no crash, no owner broadcast.
    app.control_session_selected(session, 9)
    app.control_session_changed(session)
    app.control_notes_changed(session)
