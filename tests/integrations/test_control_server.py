"""Unix-socket control protocol for editor clients."""

from __future__ import annotations

import asyncio
import json
import tempfile
from importlib import import_module
from pathlib import Path

import pytest
from anqa.control.server import PROTOCOL_VERSION
from async_wait import wait_until


def _short_sock(name: str) -> Path:
    """Short unique AF_UNIX path (macOS path limit + multi-user / xdist safe)."""
    root = Path(tempfile.mkdtemp(prefix="anqa-ctl-"))
    return root / name


async def _request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict | None = None,
    notifications: list[dict] | None = None,
) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    writer.write(json.dumps(payload).encode("utf-8") + b"\n")
    await writer.drain()
    while True:
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        if response.get("id") == request_id:
            return response
        if notifications is not None and "method" in response:
            notifications.append(response)


async def _header_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict | None = None,
) -> dict:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    writer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
    await writer.drain()
    header = await asyncio.wait_for(reader.readline(), timeout=2)
    assert header.startswith(b"Content-Length: ")
    length = int(header.split(b":", 1)[1])
    assert await reader.readline() == b"\r\n"
    return json.loads(await reader.readexactly(length))


def _write_session(session_dir: Path) -> None:
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": session_dir.name}, "generated_title": "Socket review"}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "review"},
                        "_meta": {"promptIndex": 6},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_control_server_initializes_renders_and_opens_session(tmp_path: Path) -> None:
    control = import_module("anqa.control.server")
    session_dir = tmp_path / "session-control"
    _write_session(session_dir)
    opened: list[tuple[Path, int | None]] = []

    async def open_session(path: Path, prompt_index: int | None) -> bool:
        opened.append((path, prompt_index))
        return True

    server = control.ControlServer(
        socket_path=_short_sock("control.sock"),
        resolve_session=lambda reference: session_dir if reference == session_dir.name else None,
        open_session=open_session,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        initialized = await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "clientInfo": {"name": "test-editor"}},
        )
        assert initialized["result"]["protocolVersion"] == control.PROTOCOL_VERSION
        assert "session/render" in initialized["result"]["capabilities"]

        rendered = await _request(
            reader,
            writer,
            2,
            "session/render",
            {"session": session_dir.name},
        )
        assert rendered["result"]["sessionId"] == session_dir.name
        assert rendered["result"]["promptIndexes"] == [6]
        assert "* Prompt 6" in rendered["result"]["text"]

        opened_response = await _request(
            reader,
            writer,
            3,
            "session/open",
            {"session": session_dir.name, "promptIndex": 6},
        )
        assert opened_response["result"] == {"opened": True}
        assert opened == [(session_dir, 6)]
        selected = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert selected == {
            "jsonrpc": "2.0",
            "method": "session/selected",
            "params": {"sessionId": session_dir.name, "promptIndex": 6},
        }
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_supports_emacs_jsonrpc_framing(tmp_path: Path) -> None:
    control = import_module("anqa.control.server")
    server = control.ControlServer(socket_path=_short_sock("emacs.sock"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        initialized = await _header_request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "clientInfo": {"name": "Emacs"}},
        )
        assert initialized["result"]["protocolVersion"] == control.PROTOCOL_VERSION
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_initialize_accepts_same_major_newer_minor(tmp_path: Path) -> None:
    """A 1.x client keeps a 1.0.0 owner (additive only)."""
    control = import_module("anqa.control.server")
    server = control.ControlServer(socket_path=_short_sock("minor-init.sock"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        initialized = await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": "1.2.0", "clientInfo": {"name": "Emacs"}},
        )
        assert "result" in initialized
        assert initialized["result"]["protocolVersion"] == control.PROTOCOL_VERSION
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_initialize_rejects_newer_client_version(tmp_path: Path) -> None:
    control = import_module("anqa.control.server")
    server = control.ControlServer(socket_path=_short_sock("future-init.sock"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        response = await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": "2.0.0"},
        )
        assert "error" in response
        assert response["error"]["code"] == -32602
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_does_not_chmod_existing_socket_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = import_module("anqa.control.server")
    socket_path = _short_sock("existing-parent.sock")
    original_chmod = Path.chmod

    def reject_parent_chmod(path: Path, mode: int, **kwargs: object) -> None:
        if path == socket_path.parent:
            raise PermissionError("socket parent is not owned by this process")
        original_chmod(path, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", reject_parent_chmod)
    server = control.ControlServer(socket_path=socket_path)
    await server.start()
    try:
        assert socket_path.is_socket()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_publishes_tui_changes(tmp_path: Path) -> None:
    control = import_module("anqa.control.server")
    session_dir = tmp_path / "session-tui-change"
    _write_session(session_dir)
    server = control.ControlServer(socket_path=_short_sock("changes.sock"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "clientInfo": {"name": "test-editor"}},
        )
        await server.publish_session_changed(session_dir)
        session_message = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert session_message["method"] == "session/changed"
        assert session_message["params"] == {
            "sessionId": session_dir.name,
            "listChanged": True,
        }

        await server.publish_session_changed(session_dir, list_changed=False)
        append_message = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert append_message["method"] == "session/changed"
        assert append_message["params"] == {
            "sessionId": session_dir.name,
            "listChanged": False,
        }

        await server.publish_notes_changed(session_dir)
        notes_message = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert notes_message["method"] == "notes/changed"
        assert notes_message["params"]["sessionId"] == session_dir.name
        assert len(notes_message["params"]["revision"]) == 64
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_first_rpc_result_is_not_a_broadcast(tmp_path: Path) -> None:
    """One-shot HUD sockets must not see session/changed before their reply."""
    control = import_module("anqa.control.server")
    session_dir = tmp_path / "session-oneshot"
    _write_session(session_dir)
    server = control.ControlServer(socket_path=_short_sock("oneshot.sock"))
    await server.start()
    entered = asyncio.Event()
    orig = server._dispatch

    async def slow_dispatch(
        method: str,
        params: dict,
        after_send: list,
    ) -> object:
        if method == "session/list":
            entered.set()
            await asyncio.sleep(0.15)
        return await orig(method, params, after_send)

    server._dispatch = slow_dispatch  # type: ignore[method-assign]
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        writer.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session/list",
                    "params": {"limit": 10},
                }
            ).encode("utf-8")
            + b"\n"
        )
        await writer.drain()
        await asyncio.wait_for(entered.wait(), timeout=2)
        await server.publish_session_changed(session_dir)
        first = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert first.get("id") == 1
        assert "result" in first
        assert first["result"]["matched"] == 0
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_rejects_stale_note_mutation(tmp_path: Path) -> None:
    control = import_module("anqa.control.server")
    session_dir = tmp_path / "session-notes"
    _write_session(session_dir)
    server = control.ControlServer(
        socket_path=_short_sock("notes.sock"),
        resolve_session=lambda reference: session_dir if reference == session_dir.name else None,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        listed = await _request(
            reader,
            writer,
            1,
            "notes/list",
            {"session": session_dir.name},
        )
        original_revision = listed["result"]["revision"]
        entry = {
            "id": "n-socket",
            "turnIndex": 0,
            "source": "tui",
            "fields": {"summary": "Socket note", "detail": "Inspect the event."},
            "eventIndices": [1],
        }
        saved = await _request(
            reader,
            writer,
            2,
            "notes/upsert",
            {
                "session": session_dir.name,
                "expectedRevision": original_revision,
                "note": entry,
            },
        )
        saved_revision = saved["result"]["revision"]
        assert saved_revision != original_revision
        assert saved["result"]["notes"][0]["id"] == "n-socket"
        # The response precedes the change broadcast so the mutating client can
        # record its new revision before the notes/changed echo arrives.
        echo = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert echo["method"] == "notes/changed"
        assert echo["params"] == {
            "sessionId": session_dir.name,
            "revision": saved_revision,
        }

        stale = await _request(
            reader,
            writer,
            3,
            "notes/upsert",
            {
                "session": session_dir.name,
                "expectedRevision": original_revision,
                "note": {**entry, "fields": {"summary": "stale"}},
            },
        )
        assert stale["error"]["code"] == 409
        assert stale["error"]["data"]["kind"] == "notes_conflict"
        assert stale["error"]["data"]["currentRevision"] == saved_revision

        deleted = await _request(
            reader,
            writer,
            4,
            "notes/delete",
            {
                "session": session_dir.name,
                "expectedRevision": saved_revision,
                "noteId": "n-socket",
            },
        )
        assert deleted["result"]["notes"] == []
        delete_echo = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert delete_echo["method"] == "notes/changed"
        assert delete_echo["params"]["revision"] == deleted["result"]["revision"]
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_rejects_unroundtrippable_note_tokens(tmp_path: Path) -> None:
    control = import_module("anqa.control.server")
    session_dir = tmp_path / "session-tokens"
    _write_session(session_dir)
    server = control.ControlServer(
        socket_path=_short_sock("tokens.sock"),
        resolve_session=lambda reference: session_dir if reference == session_dir.name else None,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        listed = await _request(reader, writer, 1, "notes/list", {"session": session_dir.name})
        revision = listed["result"]["revision"]
        for request_id, note in enumerate(
            [
                {"id": "spaced id", "turnIndex": 0, "source": "tui", "fields": {"summary": "x"}},
                {"id": "n --> gone", "turnIndex": 0, "source": "tui", "fields": {"summary": "x"}},
                {"id": "n-ok", "turnIndex": 0, "source": "tui", "fields": {"bad field": "x"}},
                {
                    "id": "n-ok",
                    "turnIndex": 0,
                    "source": "tui",
                    "fields": {"summary": "x"},
                    "createdAt": "a b",
                },
            ],
            start=2,
        ):
            response = await _request(
                reader,
                writer,
                request_id,
                "notes/upsert",
                {"session": session_dir.name, "expectedRevision": revision, "note": note},
            )
            assert response["error"]["code"] == -32602
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_notes_upsert_requires_source_and_keeps_foreign_fields(
    tmp_path: Path,
) -> None:
    control = import_module("anqa.control.server")
    session_dir = tmp_path / "session-foreign"
    _write_session(session_dir)
    server = control.ControlServer(
        socket_path=_short_sock("foreign-notes.sock"),
        resolve_session=lambda reference: session_dir if reference == session_dir.name else None,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        listed = await _request(reader, writer, 1, "notes/list", {"session": session_dir.name})
        revision = listed["result"]["revision"]
        missing = await _request(
            reader,
            writer,
            2,
            "notes/upsert",
            {
                "session": session_dir.name,
                "expectedRevision": revision,
                "note": {
                    "id": "n-mf",
                    "turnIndex": 0,
                    "fields": {"rule_id": "MF-12", "title": "unchecked return"},
                },
            },
        )
        assert missing["error"]["code"] == -32602
        blank = await _request(
            reader,
            writer,
            3,
            "notes/upsert",
            {
                "session": session_dir.name,
                "expectedRevision": revision,
                "note": {
                    "id": "n-mf",
                    "turnIndex": 0,
                    "source": "  ",
                    "fields": {"rule_id": "MF-12"},
                },
            },
        )
        assert blank["error"]["code"] == -32602
        saved = await _request(
            reader,
            writer,
            4,
            "notes/upsert",
            {
                "session": session_dir.name,
                "expectedRevision": revision,
                "note": {
                    "id": "n-mf",
                    "turnIndex": 1,
                    "source": "mf-plugin",
                    "fields": {"rule_id": "MF-12", "title": "unchecked return"},
                },
            },
        )
        note = saved["result"]["notes"][0]
        assert note["source"] == "mf-plugin"
        assert note["fields"]["rule_id"] == "MF-12"
        assert note["fields"]["title"] == "unchecked return"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_notes_upsert_refreshes_catalog_and_overview(tmp_path: Path) -> None:
    from anqa.control import daemon as daemon_mod
    from anqa.control.client import ControlClient
    from anqa.session.catalog import SessionCatalogCache
    from anqa.session.control_views import SessionOverview

    traces = tmp_path / "work" / "runs" / "traces"
    traces.mkdir(parents=True)
    session_dir = traces / "noted-sess"
    _write_session(session_dir)
    sock = _short_sock("notes-cat.sock")
    server = daemon_mod.build_domain_control_server(
        socket_path=sock,
        traces_path=traces,
        include_host=False,
    )
    cache = getattr(server, "_catalog_cache", None)
    assert isinstance(cache, SessionCatalogCache)
    await server.start()
    try:
        cache.get(force=True)
        SessionOverview._cache.clear()
        client = ControlClient(sock, client_name="notes-cat", timeout=20)
        await client.initialize()
        before = await client.session_list(query=session_dir.name)
        assert before["matched"] == 1
        assert before["sessions"][0]["hasNotes"] is False
        ov = await client.session_overview(session_dir.name)
        assert ov["notes"]["count"] == 0
        listed = await client.notes_list(session_dir.name)
        saved = await client.notes_upsert(
            session_dir.name,
            {
                "id": "n-ctl",
                "turnIndex": 0,
                "source": "session-notes",
                "fields": {"summary": "through control"},
            },
            expected_revision=str(listed.get("revision") or ""),
        )
        assert saved["notes"][0]["id"] == "n-ctl"
        noted = await client.session_list(query="has:note")
        assert any(row.get("sessionId") == session_dir.name for row in noted["sessions"])
        ov2 = await client.session_overview(session_dir.name)
        assert ov2["notes"]["count"] == 1
        assert ov2["notes"]["notes"][0]["id"] == "n-ctl"
        await client.close()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_accepts_content_type_first_framing(tmp_path: Path) -> None:
    control = import_module("anqa.control.server")
    server = control.ControlServer(socket_path=_short_sock("ctype.sock"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": PROTOCOL_VERSION},
            }
        ).encode("utf-8")
        writer.write(
            b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
            + f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
            + payload
        )
        await writer.drain()
        header = await asyncio.wait_for(reader.readline(), timeout=2)
        assert header.startswith(b"Content-Length: ")
        length = int(header.split(b":", 1)[1])
        assert await reader.readline() == b"\r\n"
        response = json.loads(await reader.readexactly(length))
        assert response["result"]["protocolVersion"] == control.PROTOCOL_VERSION
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_defers_broadcasts_until_first_frame(tmp_path: Path) -> None:
    """Accepted client with no first request must not receive session/changed."""
    control = import_module("anqa.control.server")
    session_dir = tmp_path / "session-quiet"
    _write_session(session_dir)
    server = control.ControlServer(socket_path=_short_sock("quiet.sock"))
    handler_entered = asyncio.Event()
    orig_handle = server._handle_client

    async def _track_handle(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        handler_entered.set()
        await orig_handle(reader, writer)

    server._handle_client = _track_handle  # type: ignore[method-assign]
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        # Server-side handler is running and blocked on the first line; the
        # peer is not in the broadcast set until a full request completes.
        await wait_until(handler_entered.is_set, description="server accepted silent client")
        assert len(server._writers) == 0
        await server.publish_session_changed(session_dir)
        # No notify bytes on the silent stream before initialize.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(reader.read(1), timeout=0.2)
        initialized = await _header_request(
            reader, writer, 1, "initialize", {"protocolVersion": PROTOCOL_VERSION}
        )
        assert initialized["result"]["protocolVersion"] == control.PROTOCOL_VERSION
        # After the first frame, the peer is eligible for later notifies.
        await wait_until(
            lambda: len(server._writers) == 1,
            description="client joins broadcast set after initialize",
        )
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_drops_stalled_clients_from_broadcasts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = import_module("anqa.control.server")
    monkeypatch.setattr(control, "NOTIFY_TIMEOUT_SECONDS", 0.1)
    session_dir = tmp_path / "session-stalled"
    _write_session(session_dir)
    server = control.ControlServer(socket_path=_short_sock("stalled.sock"))
    await server.start()
    try:
        reader_a, writer_a = await asyncio.open_unix_connection(server.socket_path)
        await _request(reader_a, writer_a, 1, "initialize", {"protocolVersion": PROTOCOL_VERSION})
        reader_b, writer_b = await asyncio.open_unix_connection(server.socket_path)
        await _header_request(
            reader_b, writer_b, 1, "initialize", {"protocolVersion": PROTOCOL_VERSION}
        )

        stalled = next(
            peer for peer, framing in server._writer_framing.items() if framing == "headers"
        )

        async def never_drains() -> None:
            await asyncio.sleep(3600)

        stalled.drain = never_drains  # type: ignore[method-assign]
        await asyncio.wait_for(server.publish_session_changed(session_dir), timeout=1)
        assert stalled not in server._writers

        healthy = json.loads(await asyncio.wait_for(reader_a.readline(), timeout=2))
        assert healthy["method"] == "session/changed"
        writer_a.close()
        writer_b.close()
        await writer_a.wait_closed()
        await writer_b.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_returns_jsonrpc_errors(tmp_path: Path) -> None:
    control = import_module("anqa.control.server")
    server = control.ControlServer(socket_path=_short_sock("errors.sock"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        writer.write(b"not-json\n")
        await writer.drain()
        parse_error = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert parse_error["error"]["code"] == -32700

        unknown = await _request(reader, writer, 2, "missing/method")
        assert unknown["error"]["code"] == -32601
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


def test_peer_gone_treats_reset_and_groups_as_disconnect() -> None:
    """RST and exception groups of RST are a gone peer, not a server fault."""
    from anqa.control.server import _peer_gone

    assert _peer_gone(ConnectionResetError(104, "Connection reset by peer"))
    assert _peer_gone(BrokenPipeError())
    assert _peer_gone(ExceptionGroup("closed", [ConnectionResetError()]))
    assert not _peer_gone(ValueError("parse"))
    assert not _peer_gone(ExceptionGroup("mixed", [ConnectionResetError(), ValueError("x")]))


@pytest.mark.asyncio
async def test_control_server_peer_reset_stays_up() -> None:
    """A client that resets the socket must not take down the owner."""
    control = import_module("anqa.control.server")
    server = control.ControlServer(socket_path=_short_sock("rst.sock"))
    await server.start()
    try:
        _reader, writer = await asyncio.open_unix_connection(server.socket_path)
        await _request(_reader, writer, 1, "initialize", {"protocolVersion": "1.0.0"})
        transport = writer.transport
        assert transport is not None
        transport.abort()
        reader2, writer2 = await asyncio.open_unix_connection(server.socket_path)
        again = await _request(reader2, writer2, 1, "initialize", {"protocolVersion": "1.0.0"})
        assert again["result"]["protocolVersion"] == control.PROTOCOL_VERSION
        writer2.close()
        await writer2.wait_closed()
    finally:
        await server.close()
