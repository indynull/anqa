"""notes/list and notes/upsert stay responsive while catalog discovery builds."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path

import pytest
from anqa.control import daemon as daemon_mod
from anqa.control.client import ControlClient
from anqa.control.server import NOTES_RPC_TIMEOUT, ControlServer
from anqa.session.catalog import SessionCatalogCache


def _short_sock(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix="anqa-notes-rpc-")) / name


def _write_sess(root: Path, name: str) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": name}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


@pytest.mark.asyncio
async def test_notes_list_and_upsert_while_catalog_scan_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    sess = _write_sess(store, "live-sess")
    release = threading.Event()
    started = threading.Event()

    import anqa.session.catalog as catalog_mod

    real = catalog_mod.list_session_catalog

    def blocked(*args: object, **kwargs: object) -> object:
        started.set()
        if not release.wait(timeout=8):
            raise AssertionError("catalog scan still blocked")
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_mod, "list_session_catalog", blocked)

    sock = _short_sock("notes.sock")
    server = daemon_mod.build_domain_control_server(
        socket_path=sock,
        traces_path=store,
        include_host=False,
    )
    cache = getattr(server, "_catalog_cache", None)
    assert isinstance(cache, SessionCatalogCache)
    await server.start()
    try:
        cache.start_rebuild(force=True)
        assert started.wait(timeout=2)
        client = ControlClient(sock, client_name="notes-warm", timeout=2.0)
        await client.initialize()
        t0 = time.perf_counter()
        listed = await client.notes_list("live-sess")
        listed_ms = (time.perf_counter() - t0) * 1000
        assert listed_ms < 500, f"notes/list blocked {listed_ms:.1f}ms"
        assert listed["notes"] == []
        t0 = time.perf_counter()
        saved = await client.notes_upsert(
            "live-sess",
            {
                "id": "n-warm",
                "turnIndex": 0,
                "source": "tui",
                "fields": {"summary": "while catalog builds"},
                "eventIndices": [],
            },
            expected_revision=str(listed["revision"]),
        )
        upsert_ms = (time.perf_counter() - t0) * 1000
        assert upsert_ms < 500, f"notes/upsert blocked {upsert_ms:.1f}ms"
        assert any(n["id"] == "n-warm" for n in saved["notes"])
        by_path = await client.notes_list(str(sess))
        assert any(n["id"] == "n-warm" for n in by_path["notes"])
        diag = await client.diagnostics()
        assert "active" in diag
        assert "failures" in diag
        assert "catalogBuilding" in diag
        assert diag["catalogBuilding"] is True
    finally:
        release.set()
        await server.close()


@pytest.mark.asyncio
async def test_catalog_warm_once_does_not_join_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    _write_sess(store, "warm-sess")
    release = threading.Event()
    started = threading.Event()
    import anqa.session.catalog as catalog_mod

    real = catalog_mod.list_session_catalog

    def blocked(*args: object, **kwargs: object) -> object:
        started.set()
        if not release.wait(timeout=8):
            raise AssertionError("catalog scan still blocked")
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_mod, "list_session_catalog", blocked)
    cache = SessionCatalogCache(traces_path=store, include_host=False, ttl=3600.0)
    t0 = time.perf_counter()
    await daemon_mod._catalog_warm_once(cache)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.25, f"warm joined rebuild ({elapsed:.3f}s)"
    assert started.wait(timeout=2)
    release.set()


@pytest.mark.asyncio
async def test_diagnostics_exposes_active_rpc_and_bounded_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_dir = tmp_path / "slow-notes"
    _write_sess(session_dir, "ignored")
    # ControlServer resolve uses the directory itself.
    server = ControlServer(
        socket_path=_short_sock("diag.sock"),
        resolve_session=lambda ref: (
            session_dir if ref in {session_dir.name, str(session_dir)} else None
        ),
    )
    orig = server._access.notes_list
    gate = threading.Event()

    def slow_list(session: str) -> object:
        gate.wait(timeout=5)
        return orig(session)

    server._access.notes_list = slow_list  # type: ignore[method-assign]
    monkeypatch.setattr("anqa.control.server.NOTES_RPC_TIMEOUT", 0.15)
    await server.start()
    try:
        watcher = ControlClient(server.socket_path, client_name="diag-watch", timeout=2.0)
        await watcher.initialize()
        waiter = ControlClient(server.socket_path, client_name="diag-wait", timeout=2.0)
        await waiter.initialize()

        async def _list() -> object:
            return await waiter.notes_list(session_dir.name)

        task = asyncio.create_task(_list())
        await asyncio.sleep(0.05)
        diag = await watcher.diagnostics()
        methods = {str(row.get("method")) for row in diag["active"]}
        assert "notes/list" in methods
        with pytest.raises(Exception):
            await task
        late = await watcher.diagnostics()
        messages = [str(row.get("message") or "") for row in late["failures"]]
        assert any("timed out" in msg for msg in messages)
        assert NOTES_RPC_TIMEOUT > 0
    finally:
        gate.set()
        await server.close()


def test_explicit_store_skips_host_include() -> None:
    assert daemon_mod.include_host_for_explicit_store(Path("/tmp/store")) is False
    assert daemon_mod.include_host_for_explicit_store(None) is None


def test_domain_resolve_is_name_lookup_and_keeps_host_include(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold overview/timeline resolve by name. Auto-start does not pin host off."""
    from anqa.session import sources as sources_mod

    store = tmp_path / "store"
    sess = _write_sess(store, "owner-sess")

    def hang(*_a: object, **_k: object) -> object:
        raise AssertionError("collect_session_dirs must not run")

    monkeypatch.setattr(sources_mod, "collect_session_dirs", hang)
    server = daemon_mod.build_domain_control_server(
        socket_path=tmp_path / "sock",
        traces_path=store,
        include_host=None,
    )
    cache = getattr(server, "_catalog_cache", None)
    assert isinstance(cache, SessionCatalogCache)
    assert cache._include_host is None
    found = server._resolve_session("owner-sess")
    assert found == sess.resolve()
    found_ref = server._resolve_session("grok:owner-sess")
    assert found_ref == sess.resolve()


def test_cold_notes_resolve_finds_session_on_other_catalog_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default owner (include_host unset) names a session outside traces_path."""
    from anqa.session import sources as sources_mod

    grok_store = tmp_path / "grok-store"
    other = tmp_path / "other-store"
    _write_sess(grok_store, "grok-only")
    foreign = _write_sess(other, "foreign-sess")
    monkeypatch.setattr(sources_mod, "_adapter_store_roots", lambda: [other])
    monkeypatch.setattr(
        sources_mod,
        "collect_session_dirs",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("cold notes resolve listed sibling sessions")
        ),
    )
    server = daemon_mod.build_domain_control_server(
        socket_path=tmp_path / "sock",
        traces_path=grok_store,
        include_host=None,
    )
    assert server._resolve_session("foreign-sess") == foreign.resolve()
    pinned = daemon_mod.build_domain_control_server(
        socket_path=tmp_path / "sock-pin",
        traces_path=grok_store,
        include_host=False,
    )
    assert pinned._resolve_session("foreign-sess") is None


def test_domain_notes_resolve_skips_adapter_ref_for_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A harness:id miss must not walk the adapter host store."""
    store = tmp_path / "store"
    sess = _write_sess(store, "in-store")

    def _boom(self: object, _sid: str) -> object:
        raise AssertionError("notes resolve called adapter.ref_for_id")

    monkeypatch.setattr("anqa.harness.grok.GrokAdapter.ref_for_id", _boom)
    server = daemon_mod.build_domain_control_server(
        socket_path=tmp_path / "sock",
        traces_path=store,
        include_host=False,
    )
    assert server._resolve_session("grok:in-store") == sess.resolve()
    assert server._resolve_session("grok:missing-id") is None
