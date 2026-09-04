"""Session catalog roots: adapter host stores."""

from __future__ import annotations

import json
from pathlib import Path

from anqa.session.sources import (
    collect_session_dirs,
    default_catalog_root,
    is_adapter_store_root,
    is_host_directory_store,
    is_under_adapter_store,
    session_dir_for_watch_path,
    session_run_dir,
    session_scan_roots,
)


def _seed_session(root: Path, *, cwd_token: str, sid: str, title: str = "t") -> Path:
    sess = root / cwd_token / sid
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text(
        json.dumps({"session_id": sid, "generated_title": title}),
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    (sess / "updates.jsonl").write_text("", encoding="utf-8")
    return sess


def test_default_catalog_root_uses_adapter() -> None:
    from anqa.harness.grok import default_sessions_root

    assert default_catalog_root() == default_sessions_root()


def test_session_run_dir_decodes_host_cwd(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path, cwd_token="%2Fmnt%2Fdev%2F_git%2Ffubar", sid="s1")
    assert session_run_dir(sess) == "/mnt/dev/_git/fubar"


def test_session_run_dir_skips_container_workspace(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path, cwd_token="%2Fworkspace", sid="s1")
    assert session_run_dir(sess) == ""


def test_session_scan_roots_host_only(tmp_path: Path) -> None:
    host = tmp_path / "host-sessions"
    host.mkdir()
    roots = session_scan_roots(include_host=True, host_root=host)
    assert len(roots) == 1
    assert roots[0].path == host


def test_session_scan_roots_adds_traces_path(tmp_path: Path) -> None:
    host = tmp_path / "host"
    extra = tmp_path / "extra"
    host.mkdir()
    extra.mkdir()
    roots = session_scan_roots(traces_path=extra, include_host=True, host_root=host)
    assert [r.path for r in roots] == [host, extra]
    assert roots[0].path == host
    assert roots[1].path == extra


def test_collect_session_dirs_union(tmp_path: Path) -> None:
    host = tmp_path / "host"
    extra = tmp_path / "extra"
    h_sess = _seed_session(host, cwd_token="%2Fproj", sid="host-sid")
    e_sess = _seed_session(extra, cwd_token="%2Fproj", sid="extra-sid")
    roots = session_scan_roots(traces_path=extra, include_host=True, host_root=host)
    found = {str(p.resolve()) for p in collect_session_dirs(roots)}
    assert str(h_sess.resolve()) in found
    assert str(e_sess.resolve()) in found


def test_under_adapter_store(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = _seed_session(host, cwd_token="%2Fa", sid="s1")
    monkeypatch.setattr(
        "anqa.session.sources._adapter_store_roots",
        lambda: [host],
    )
    assert is_under_adapter_store(sess)
    other = _seed_session(tmp_path / "elsewhere", cwd_token="%2Fb", sid="o1")
    assert not is_under_adapter_store(other)


def test_classify_import_locator(tmp_path: Path, monkeypatch) -> None:
    import anqa.paths as paths
    from anqa.session.sources import ORIGIN_IMPORT, classify_session_origin

    home = tmp_path / "home"
    monkeypatch.setattr(paths, "APP_HOME", home)
    imported = home / "imports" / "grok" / "sid"
    imported.mkdir(parents=True)
    (imported / "summary.json").write_text("{}", encoding="utf-8")
    assert classify_session_origin(imported) == ORIGIN_IMPORT


def test_is_host_directory_store_by_shape(tmp_path: Path) -> None:
    """Encoded-cwd buckets are host trees; dash-encoded jsonl projects are not."""
    grok = tmp_path / "grok-sessions"
    nested = grok / "%2Fproj" / "sid"
    nested.mkdir(parents=True)
    (nested / "summary.json").write_text("{}", encoding="utf-8")
    jsonl_store = tmp_path / "jsonl-projects"
    project = jsonl_store / "-home-rgoswami-proj"
    project.mkdir(parents=True)
    (project / "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.jsonl").write_text("{}\n", encoding="utf-8")
    assert is_host_directory_store(grok)
    assert not is_host_directory_store(jsonl_store)


def test_is_adapter_store_root(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / ".grok" / "sessions"
    host.mkdir(parents=True)
    monkeypatch.setattr(
        "anqa.session.sources._adapter_store_roots",
        lambda: [host],
    )
    assert is_adapter_store_root(host)
    assert not is_adapter_store_root(tmp_path)


def test_watch_path_maps_encoded_cwd_to_session_not_bucket(tmp_path: Path) -> None:
    host = tmp_path / "sessions"
    sess = _seed_session(host, cwd_token="%2FUsers%2Fali%2F_dev%2F_git%2Fanqa", sid="019abc")
    ev = sess / "updates.jsonl"
    got = session_dir_for_watch_path(ev, host)
    assert got is not None
    assert got.resolve() == sess.resolve()
    bucket = host / "%2FUsers%2Fali%2F_dev%2F_git%2Fanqa"
    assert session_dir_for_watch_path(bucket, host) is None


def test_watch_path_maps_flat_session(tmp_path: Path) -> None:
    store = tmp_path / "sessions"
    sess = store / "one"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    (sess / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    got = session_dir_for_watch_path(sess / "updates.jsonl", store)
    assert got is not None
    assert got.resolve() == sess.resolve()
