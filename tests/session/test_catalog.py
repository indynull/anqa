"""Domain session catalog (control / headless owner; no TUI)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from anqa.session.catalog import (
    SessionCatalogCache,
    list_session_catalog,
    resolve_session_reference,
    session_catalog_row,
)
from anqa.session.mtime_export import default_catalog_snapshot, read_catalog_snapshot_rows


def _write_session(root: Path, name: str, *, title: str = "Catalog session") -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": title}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hi"},
                        "_meta": {"promptIndex": 1},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (session_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return session_dir


def test_list_session_catalog_discovers_store(tmp_path: Path) -> None:
    store = tmp_path / "sessions"
    _write_session(store, "session-catalog-a", title="Alpha review")
    rows = list_session_catalog(traces_path=store, include_host=False)
    assert len(rows) == 1
    row = rows[0]
    assert row["sessionId"] == "session-catalog-a"
    assert row["path"] == "grok:session-catalog-a"
    assert row["title"] == "Alpha review"
    assert "status" in row
    assert "model" in row


def test_list_session_catalog_empty_without_sessions(tmp_path: Path) -> None:
    store = tmp_path / "empty-store"
    store.mkdir()
    assert list_session_catalog(traces_path=store, include_host=False) == []


def test_resolve_session_reference_by_path_and_id(tmp_path: Path) -> None:
    store = tmp_path / "sessions"
    sess = _write_session(store, "session-resolve-me")
    by_path = resolve_session_reference(str(sess), traces_path=store, include_host=False)
    assert by_path == sess.resolve()
    by_name = resolve_session_reference("session-resolve-me", traces_path=store, include_host=False)
    assert by_name == sess.resolve()
    assert resolve_session_reference("missing-session-xyz", traces_path=store) is None
    assert resolve_session_reference("", traces_path=store) is None


def test_list_session_catalog_includes_host_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless catalog includes host sessions; ``include_host=False`` drops them."""
    store = tmp_path / "store"
    host = tmp_path / "host-sessions"
    _write_session(store, "store-only-sess", title="Store")
    h_sess = host / "%2Fproj" / "host-sess"
    h_sess.mkdir(parents=True)
    (h_sess / "summary.json").write_text(
        '{"info":{"id":"host-sess"},"generated_title":"Host"}',
        encoding="utf-8",
    )
    (h_sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (h_sess / "updates.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "anqa.session.sources._adapter_store_roots",
        lambda: [host],
    )
    cache = tmp_path / "host-catalog-cache"
    cache.mkdir()
    monkeypatch.setattr("anqa.session.mtime_export.cache_dir", lambda: cache)

    # include_host=None includes host
    rows = list_session_catalog(traces_path=store, include_host=None)
    ids = {r["sessionId"] for r in rows}
    assert "store-only-sess" in ids
    assert "host-sess" in ids

    # Force off ignores the host store
    rows_store = list_session_catalog(traces_path=store, include_host=False)
    assert {r["sessionId"] for r in rows_store} == {"store-only-sess"}


def test_resolve_by_id_does_not_load_meta_for_other_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening one host session must not re-read every other session's meta.

    ``session/overview`` and each ``session/timeline`` page resolve the id
    through :func:`resolve_session_reference`. Loading list-meta for every
    sibling on that path made TUI attach open ~10× slower than a local parse.
    """
    from anqa.session import catalog as catalog_mod

    host = tmp_path / "host-sessions"
    target_id = "sess-0099"
    for i in range(100):
        name = f"sess-{i:04d}"
        bucket = host / "%2Fproj" / name
        bucket.mkdir(parents=True)
        (bucket / "summary.json").write_text(
            json.dumps({"info": {"id": name}, "generated_title": name}),
            encoding="utf-8",
        )
        (bucket / "updates.jsonl").write_text("", encoding="utf-8")
        (bucket / "events.jsonl").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(
        "anqa.session.sources._adapter_store_roots",
        lambda: [host],
    )
    cache = tmp_path / "host-catalog-cache"
    cache.mkdir()
    monkeypatch.setattr("anqa.session.mtime_export.cache_dir", lambda: cache)

    calls: list[str] = []
    real_row = catalog_mod.session_catalog_row

    def _count_row(session_dir: Path, *, label: str | None = None):
        calls.append(session_dir.name)
        return real_row(session_dir, label=label)

    monkeypatch.setattr(catalog_mod, "session_catalog_row", _count_row)

    found = resolve_session_reference(target_id, include_host=True)
    assert found is not None
    assert found.name == target_id
    assert calls == []


def test_catalog_cache_resolves_id_from_warm_rows(tmp_path: Path) -> None:
    """Serve must resolve session ids from the warm catalog, not a second walk."""
    from anqa.session.catalog import SessionCatalogCache

    store = tmp_path / "sessions"
    sess = _write_session(store, "cached-resolve")
    cache = SessionCatalogCache(traces_path=store, include_host=False)
    rows = cache.get(force=True)
    assert len(rows) == 1
    assert cache.resolve("cached-resolve") == sess.resolve()
    assert cache.resolve(f"grok:{sess.name}") == sess.resolve()
    assert cache.resolve(str(sess.resolve())) == sess.resolve()
    assert cache.resolve("missing") is None


def test_catalog_scan_roots_includes_every_grok_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[catalog.roots] grok`` may list several directory trees."""
    from anqa.config import invalidate_config_cache, parse_app_config
    from anqa.session.catalog import catalog_scan_roots

    first = tmp_path / "grok-a"
    second = tmp_path / "grok-b"
    first.mkdir()
    second.mkdir()
    cfg = parse_app_config({"catalog": {"roots": {"grok": [str(first), str(second)]}}})
    monkeypatch.setattr("anqa.config.load_app_config", lambda: cfg)
    invalidate_config_cache()
    roots = catalog_scan_roots()
    host_paths = [r.path.resolve() for r in roots]
    assert first.resolve() in host_paths
    assert second.resolve() in host_paths


def test_adapter_store_watch_paths_skip_grok_walk(tmp_path: Path, monkeypatch) -> None:
    """Sqlite / extra dirs are watch membership targets, not find_sessions trees."""
    from anqa.harness import registry
    from anqa.harness.registry import adapter_store_watch_paths

    grok_root = tmp_path / "grok-sessions"
    extra_file = tmp_path / "other" / "store.db"
    extra_dir = tmp_path / "jsonl"
    grok_root.mkdir()
    extra_file.parent.mkdir()
    extra_file.write_bytes(b"")
    extra_dir.mkdir()

    class _Extra:
        id = "demo"

        def default_host_roots(self) -> list[Path]:
            return [extra_file, extra_dir]

    grok = registry.adapter("grok")
    assert grok is not None
    monkeypatch.setattr(
        registry,
        "adapter_host_roots",
        lambda item: [grok_root] if item.id == "grok" else item.default_host_roots(),
    )
    monkeypatch.setattr(registry, "enabled_host_adapters", lambda: (grok, _Extra()))
    extras = adapter_store_watch_paths()
    assert grok_root not in extras
    assert extra_file in extras
    assert extra_dir not in extras


def test_session_catalog_row_none_on_bad_dir(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-session"
    empty.mkdir()
    missing = tmp_path / "nope"
    assert session_catalog_row(empty) is None
    assert session_catalog_row(missing) is None


def _write_row_format_2_snapshot(root: Path, rows: list[dict[str, object]]) -> Path:
    dest = default_catalog_snapshot(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "rowFormat": 2,
                "root": str(root),
                "stamps": [],
                "sessions": rows,
            }
        ),
        encoding="utf-8",
    )
    return dest


def test_read_catalog_snapshot_rows_serves_row_format_2(tmp_path: Path) -> None:
    """Seed must read operator cache files that still use rowFormat 2."""
    dest = _write_row_format_2_snapshot(
        tmp_path / "sessions",
        [
            {
                "sessionId": "fmt2-a",
                "path": "grok:fmt2-a",
                "title": "Format two",
                "status": "complete",
                "harness": "grok",
            }
        ],
    )
    rows = read_catalog_snapshot_rows(dest)
    assert [str(row.get("sessionId")) for row in rows] == ["fmt2-a"]


def test_list_for_rpc_seeds_existing_row_format_2_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold session/list must return existing snapshot rows without a rebuild."""
    import anqa.session.catalog as catalog_mod

    traces = tmp_path / "sessions"
    traces.mkdir()
    _write_row_format_2_snapshot(
        traces,
        [
            {
                "sessionId": "old-fmt-sess",
                "path": "grok:old-fmt-sess",
                "title": "From format 2",
                "status": "complete",
                "harness": "grok",
                "sortEpoch": 1_700_000_000,
            }
        ],
    )
    release = threading.Event()
    started = threading.Event()

    def blocked(*args: object, **kwargs: object) -> object:
        started.set()
        if not release.wait(timeout=8):
            raise AssertionError("scan still blocked")
        return []

    monkeypatch.setattr(catalog_mod, "list_session_catalog", blocked)
    cache = SessionCatalogCache(traces_path=traces, include_host=False)
    listed = cache.list_for_rpc(limit=50)
    assert started.wait(timeout=2)
    assert listed["total"] == 1
    assert listed["matched"] == 1
    assert listed["building"] is True
    assert listed["incomplete"] is True
    assert {str(row.get("sessionId")) for row in listed["sessions"]} == {"old-fmt-sess"}
    release.set()


def test_get_force_seeds_existing_row_format_2_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold get(force=True) must serve rowFormat 2 snapshots while rebuild runs."""
    import anqa.session.catalog as catalog_mod

    traces = tmp_path / "sessions"
    traces.mkdir()
    _write_row_format_2_snapshot(
        traces,
        [
            {
                "sessionId": "old-fmt-sess",
                "path": "grok:old-fmt-sess",
                "title": "From format 2",
                "status": "complete",
                "harness": "grok",
                "sortEpoch": 1_700_000_000,
            }
        ],
    )
    release = threading.Event()
    started = threading.Event()

    def blocked(*args: object, **kwargs: object) -> object:
        started.set()
        if not release.wait(timeout=8):
            raise AssertionError("scan still blocked")
        return []

    monkeypatch.setattr(catalog_mod, "list_session_catalog", blocked)
    cache = SessionCatalogCache(traces_path=traces, include_host=False)

    class _JumpClock:
        def __init__(self) -> None:
            self._n = 0

        def monotonic(self) -> float:
            self._n += 1
            return float(self._n * 1000.0)

    cache._time = _JumpClock()
    rows = cache.get(force=True)
    assert started.wait(timeout=2)
    assert {str(row.get("sessionId")) for row in rows} == {"old-fmt-sess"}
    with cache._lock:
        assert cache._building is True
    listed = cache.list_for_rpc(limit=50)
    assert listed["total"] == 1
    assert listed["building"] is True
    assert listed["incomplete"] is True
    release.set()
