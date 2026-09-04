"""Notes session resolve must not wait on a catalog store walk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from anqa.harness.registry import resolve_session_ref
from anqa.session.access import LocalSessionAccess
from anqa.session.catalog import resolve_session_locator
from anqa.session.sources import find_named_session_dir


def _write_sess(root: Path, name: str, *, title: str = "Notes") -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": title}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


def test_find_named_session_dir_direct_and_cwd_bucket(tmp_path: Path) -> None:
    store = tmp_path / "store"
    direct = _write_sess(store, "sess-direct")
    nested = store / "%2Fhome%2Fproj" / "sess-nested"
    nested.mkdir(parents=True)
    (nested / "summary.json").write_text("{}", encoding="utf-8")
    (nested / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    assert find_named_session_dir(store, "sess-direct") == direct.resolve()
    assert find_named_session_dir(store, "sess-nested") == nested.resolve()
    assert find_named_session_dir(store, "missing") is None


def test_resolve_session_locator_skips_catalog_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anqa.session.catalog as catalog_mod
    import anqa.session.sources as sources_mod

    store = tmp_path / "store"
    sess = _write_sess(store, "only-sess")

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("notes resolve walked the catalog")

    monkeypatch.setattr(catalog_mod, "list_session_catalog", _boom)
    monkeypatch.setattr(sources_mod, "collect_session_dirs", _boom)

    assert (
        resolve_session_locator("only-sess", traces_path=store, include_host=False)
        == sess.resolve()
    )
    assert (
        resolve_session_locator(str(sess), traces_path=store, include_host=False) == sess.resolve()
    )
    assert (
        resolve_session_locator("grok:only-sess", traces_path=store, include_host=False)
        == sess.resolve()
    )
    assert resolve_session_locator("missing", traces_path=store, include_host=False) is None


def test_resolve_session_ref_notes_skips_adapter_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    sess = _write_sess(store, "bare-id")

    def path_resolve(reference: str) -> Path | None:
        return resolve_session_locator(reference, traces_path=store, include_host=False)

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("notes resolve walked adapters")

    monkeypatch.setattr("anqa.harness.registry.adapters", _boom)
    found = resolve_session_ref("bare-id", path_resolve=path_resolve, walk_adapters=False)
    assert found is not None
    assert found.locator == sess.resolve()
    missing = resolve_session_ref("nope", path_resolve=path_resolve, walk_adapters=False)
    assert missing is None


def test_resolve_session_locator_harness_id_does_not_call_ref_for_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A miss on the cheap name lookup must not walk the adapter host store."""
    import anqa.session.catalog as catalog_mod

    store = tmp_path / "store"
    _write_sess(store, "in-store")

    class _Boom:
        def ref_for_id(self, _sid: str) -> object:
            raise AssertionError("harness:id resolve called ref_for_id")

    monkeypatch.setattr(catalog_mod, "adapter", lambda _hid: _Boom())
    assert resolve_session_locator("grok:missing-id", traces_path=store) is None
    found = resolve_session_locator("grok:in-store", traces_path=store)
    assert found == (store / "in-store").resolve()


def test_resolve_session_ref_harness_id_skips_ref_for_id_when_walk_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    sess = _write_sess(store, "named")

    def path_resolve(reference: str) -> Path | None:
        return resolve_session_locator(reference, traces_path=store, include_host=False)

    class _Boom:
        def ref_for_id(self, _sid: str) -> object:
            raise AssertionError("walk_adapters=False still called ref_for_id")

    monkeypatch.setattr("anqa.harness.registry.adapter", lambda _hid: _Boom())
    missing = resolve_session_ref("grok:absent", path_resolve=path_resolve, walk_adapters=False)
    assert missing is None
    hit = resolve_session_ref("grok:named", path_resolve=path_resolve, walk_adapters=False)
    assert hit is not None
    assert hit.locator == sess.resolve()


def test_resolve_session_locator_named_lookup_on_scan_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold notes resolve finds a session on other catalog roots by name."""
    grok_store = tmp_path / "grok-store"
    other = tmp_path / "other-store"
    _write_sess(grok_store, "grok-only")
    foreign = _write_sess(other, "foreign-sess")
    monkeypatch.setattr(
        "anqa.session.sources._adapter_store_roots",
        lambda: [other],
    )
    found = resolve_session_locator("foreign-sess", traces_path=grok_store, include_host=None)
    assert found == foreign.resolve()
    pinned = resolve_session_locator("foreign-sess", traces_path=grok_store, include_host=False)
    assert pinned is None


def test_notes_list_does_not_call_catalog_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    sess = _write_sess(store, "note-sess")

    def path_resolve(reference: str) -> Path | None:
        return resolve_session_locator(reference, traces_path=store, include_host=False)

    access = LocalSessionAccess(resolve_session=path_resolve)

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("notes_list waited on catalog get")

    monkeypatch.setattr("anqa.session.catalog.SessionCatalogCache.get", _boom)
    monkeypatch.setattr("anqa.session.catalog.list_session_catalog", _boom)
    snap = access.notes_list("note-sess")
    assert snap["revision"]
    assert snap["notes"] == []
    again = access.notes_list(str(sess))
    assert again["revision"] == snap["revision"]
