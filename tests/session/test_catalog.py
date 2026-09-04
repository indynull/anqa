"""Domain session catalog (control / headless owner; no TUI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from anqa.session.catalog import (
    list_session_catalog,
    resolve_session_reference,
    session_catalog_row,
)


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


def test_resolve_session_reference_does_not_collect_all_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Id resolve is a name lookup. It must not list every session dir."""
    from anqa.session import sources as sources_mod

    store = tmp_path / "sessions"
    sess = _write_session(store, "only-me")
    nested = store / "%2Fproj" / "cwd-sess"
    nested.mkdir(parents=True)
    (nested / "summary.json").write_text(
        json.dumps({"info": {"id": "cwd-sess"}, "generated_title": "cwd"}),
        encoding="utf-8",
    )
    (nested / "updates.jsonl").write_text("", encoding="utf-8")
    (nested / "events.jsonl").write_text("{}\n", encoding="utf-8")

    def hang(*_a: object, **_k: object) -> object:
        raise AssertionError("collect_session_dirs must not run")

    monkeypatch.setattr(sources_mod, "collect_session_dirs", hang)

    found = resolve_session_reference("only-me", traces_path=store, include_host=False)
    assert found == sess.resolve()
    found_ref = resolve_session_reference("grok:only-me", traces_path=store, include_host=False)
    assert found_ref == sess.resolve()
    found_cwd = resolve_session_reference("cwd-sess", traces_path=store, include_host=False)
    assert found_cwd == nested.resolve()


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
