"""Plane watch subscription and journal tail (no recursive tree watch)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from anqa.fs_watch import TraceTreeWatch
from anqa.session.watch import (
    JournalTail,
    catalog_subscribe_paths,
    membership_watch_dirs,
    plane_event_path,
    plane_file_paths,
    session_dirs_under,
    watch_target_paths,
)
from async_wait import wait_until_sync


def _write_session(root: Path, name: str) -> Path:
    session = root / name
    session.mkdir(parents=True)
    (session / "summary.json").write_text("{}", encoding="utf-8")
    (session / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    workspace = session / "workspace" / "src"
    workspace.mkdir(parents=True)
    (workspace / "a.py").write_text("print(1)\n", encoding="utf-8")
    return session


def test_subscribe_paths_are_membership_and_session_dirs(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    session = _write_session(traces, "sess")
    paths = catalog_subscribe_paths([traces], [session])
    assert traces in paths
    assert session in paths
    assert all(p.is_dir() for p in paths)
    assert not any(
        p.name in {"summary.json", "signals.json", "updates.jsonl", "operator_notes.toml"}
        for p in paths
    )
    assert not any("workspace" in p.parts for p in paths)


def test_plane_event_keeps_modified_session_dir(tmp_path: Path) -> None:
    session = _write_session(tmp_path, "live")
    assert plane_event_path(session / "updates.jsonl") is True
    assert plane_event_path(session, kind=2) is True
    assert plane_event_path(tmp_path, kind=2) is False


def test_membership_watch_keeps_opencode_store_files(tmp_path: Path) -> None:
    """Sqlite WAL writes must reach the catalog (not only Grok plane files)."""
    w = TraceTreeWatch(tmp_path, lambda: None, membership_only=True)
    assert w._keep_event(2, str(tmp_path / "opencode.db")) is True
    assert w._keep_event(2, str(tmp_path / "opencode.db-wal")) is True
    assert w._keep_event(2, str(tmp_path / "tmp-probe" / "sess.jsonl")) is True
    assert w._keep_event(2, str(tmp_path / "noise.bin")) is False


def test_path_relevant_ignores_workspace() -> None:
    sess = "/home/ali/.grok/sessions/%2Fproj/sid"
    assert not TraceTreeWatch.path_relevant(f"{sess}/workspace/src/a.py")
    assert not TraceTreeWatch.path_relevant(f"{sess}/workspace/updates.jsonl")
    assert TraceTreeWatch.path_relevant(f"{sess}/updates.jsonl")
    assert TraceTreeWatch.path_relevant(f"{sess}/summary.json")
    assert TraceTreeWatch.path_relevant(f"{sess}/signals.json")
    assert not TraceTreeWatch.path_relevant("/x/random.bin")


def _wait_watch_armed(watch: TraceTreeWatch) -> None:
    wait_until_sync(
        lambda: bool(watch.subscribed_paths()),
        description="watch thread subscribed paths",
    )


def test_watch_start_stop_fires_on_plane_write(tmp_path: Path) -> None:
    hits: list[int] = []
    session = _write_session(tmp_path, "sess")
    w = TraceTreeWatch(tmp_path, lambda: hits.append(1), session_dir=session)
    assert w.start() is True
    try:
        _wait_watch_armed(w)
        assert not any("workspace" in p.parts for p in w.subscribed_paths())
        (session / "summary.json").write_text('{"title": "x"}\n', encoding="utf-8")
        wait_until_sync(lambda: bool(hits), description="FS watch callback after write")
    finally:
        w.stop()
    assert hits


def test_watch_workspace_write_does_not_fire(tmp_path: Path) -> None:
    hits: list[list[str]] = []
    session = _write_session(tmp_path, "sess")
    w = TraceTreeWatch(
        tmp_path,
        lambda: None,
        session_dir=session,
        on_paths=lambda paths: hits.append(paths),
    )
    assert w.start() is True
    try:
        _wait_watch_armed(w)
        time.sleep(0.3)
        hits.clear()
        (session / "workspace" / "src" / "a.py").write_text("print(2)\n", encoding="utf-8")
        time.sleep(0.3)
        assert hits == []
        (session / "summary.json").write_text("{}\n", encoding="utf-8")
        wait_until_sync(lambda: bool(hits), description="summary write still fires")
    finally:
        w.stop()
    assert hits
    assert all("workspace" not in Path(p).parts for batch in hits for p in batch)


def test_journal_tail_second_append_does_not_reread(tmp_path: Path) -> None:
    path = tmp_path / "updates.jsonl"
    path.write_text("one\n", encoding="utf-8")
    tail = JournalTail(path)
    first = tail.consume()
    assert first == b"one\n"
    offset = tail.offset
    assert offset > 0
    with path.open("a", encoding="utf-8") as fh:
        fh.write("two\n")
    second = tail.consume()
    assert second == b"two\n"
    assert tail.offset > offset


def test_watch_resubscribes_plane_files_of_session_created_after_start(
    tmp_path: Path,
) -> None:
    """A session mkdir after start must subscribe that session directory."""
    traces = tmp_path / "traces"
    traces.mkdir()
    hits: list[list[str]] = []
    w = TraceTreeWatch(traces, lambda: None, on_paths=lambda paths: hits.append(list(paths)))
    assert w.start() is True
    try:
        _wait_watch_armed(w)
        assert not any(p.name == "late-sess" for p in w.subscribed_paths())
        session = traces / "late-sess"
        session.mkdir()
        (session / "summary.json").write_text("{}", encoding="utf-8")
        (session / "updates.jsonl").write_text("{}\n", encoding="utf-8")
        wait_until_sync(
            lambda: any(p.name == "late-sess" and p.is_dir() for p in w.subscribed_paths()),
            description="new session directory subscribed after mkdir",
        )
        hits.clear()
        (session / "summary.json").write_text('{"title": "late"}\n', encoding="utf-8")
        wait_until_sync(lambda: bool(hits), description="write on late session plane file")
    finally:
        w.stop()
    assert hits
    assert any(Path(p).name == "summary.json" for batch in hits for p in batch)


def test_owner_serve_source_has_no_watchdog_or_warm_timer() -> None:
    from anqa.control import daemon

    src = Path(daemon.__file__).read_text(encoding="utf-8")
    assert "watchdog" not in src
    assert "inotify_c" not in src
    assert "CATALOG_WARM_INTERVAL" not in src
    assert "CONTROL_FS_DEBOUNCE_S" not in src


def test_session_dirs_under_skips_workspace(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    session = _write_session(traces, "sess")
    found = session_dirs_under([traces])
    assert [p.resolve() for p in found] == [session.resolve()]
    assert plane_file_paths(session)[-1].name == "operator_notes.toml"


def test_session_dirs_under_finds_work_nested_session(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    session = traces / "anqa-abc" / "%2Fworkspace" / "sid"
    session.mkdir(parents=True)
    (session / "summary.json").write_text("{}", encoding="utf-8")
    (session / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    found = session_dirs_under([traces])
    assert [p.resolve() for p in found] == [session.resolve()]


def test_session_dirs_under_drops_subagent(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    parent = _write_session(traces, "parent")
    child = _write_session(traces, "child-sub")
    (child / "summary.json").write_text(
        '{"info":{"id":"child-sub"},"session_kind":"subagent"}',
        encoding="utf-8",
    )
    (parent / "subagents" / "child-sub").mkdir(parents=True)
    found = {p.name for p in session_dirs_under([traces])}
    assert found == {"parent"}


def test_start_is_false_when_root_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-store"
    w = TraceTreeWatch(missing, lambda: None)
    assert w.start() is False
    assert w._thread is None


def test_start_is_true_when_watch_never_yields(tmp_path: Path, monkeypatch) -> None:
    _write_session(tmp_path, "sess")

    def never_yield(*_args: object, **kwargs: object):
        stop = kwargs.get("stop_event")
        if stop is not None:
            stop.wait(30)
        if False:
            yield set()

    monkeypatch.setattr("watchfiles.watch", never_yield)
    w = TraceTreeWatch(tmp_path, lambda: None)
    t0 = time.perf_counter()
    assert w.start() is True
    assert time.perf_counter() - t0 < 1.0
    w.stop()


def test_start_is_true_when_collect_paths_exceeds_ready_timeout(tmp_path: Path, caplog) -> None:
    """An existing directory store keeps the watch when path collect is slow."""
    _write_session(tmp_path, "sess")
    w = TraceTreeWatch(tmp_path, lambda: None)
    real = w._collect_paths

    def slow() -> list[Path]:
        deadline = time.monotonic() + 2.3
        while time.monotonic() < deadline:
            if w._stop.is_set():
                return []
            time.sleep(0.05)
        return real()

    w._collect_paths = slow
    t0 = time.perf_counter()
    with caplog.at_level(logging.WARNING):
        assert w.start() is True
    assert time.perf_counter() - t0 < 2.0
    try:
        assert w._thread is not None and w._thread.is_alive()
        messages = [record.getMessage() for record in caplog.records]
        assert not any("watch failed" in msg for msg in messages)
        wait_until_sync(
            lambda: bool(w.subscribed_paths()),
            description="slow collect still arms subscribed paths",
            timeout=4.0,
        )
    finally:
        w.stop()
    assert w.subscribed_paths()


def test_session_dirs_under_uses_named_host_root(tmp_path: Path) -> None:
    host = tmp_path / "sessions"
    nested = host / "%2Fproj" / "sid"
    nested.mkdir(parents=True)
    (nested / "summary.json").write_text("{}", encoding="utf-8")
    junk = host / "%2Fproj" / "sid" / "workspace" / "deep"
    junk.mkdir(parents=True)
    (junk / "summary.json").write_text("{}", encoding="utf-8")
    found = session_dirs_under([host], host_root=host)
    assert [p.resolve() for p in found] == [nested.resolve()]


def test_session_dirs_under_uses_host_lister_for_non_first_adapter_store(
    tmp_path: Path, monkeypatch
) -> None:
    """A grok/host directory store that is not adapter root 0 must not fan discover."""
    first = tmp_path / "antigravity-store"
    first.mkdir()
    grok = tmp_path / "grok-sessions"
    nested = grok / "%2Fproj" / "sid"
    nested.mkdir(parents=True)
    (nested / "summary.json").write_text("{}", encoding="utf-8")
    (nested / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    junk = nested / "workspace" / "deep"
    junk.mkdir(parents=True)
    (junk / "summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "anqa.session.sources._adapter_store_roots",
        lambda: [first, grok],
    )
    walked: list[str] = []

    def boom(root: Path) -> list[Path]:
        walked.append(str(root))
        raise AssertionError("discover_dirs must not run on a host directory store")

    monkeypatch.setattr("anqa.session.watch.discover_dirs", boom)
    found = session_dirs_under([grok])
    assert [p.resolve() for p in found] == [nested.resolve()]
    assert walked == []


def test_session_dirs_under_jsonl_adapter_store_still_discovers(
    tmp_path: Path, monkeypatch
) -> None:
    """A dash-encoded jsonl store that is not adapter root 0 keeps file locators."""
    first = tmp_path / "antigravity-store"
    first.mkdir()
    jsonl_store = tmp_path / "jsonl-projects"
    project = jsonl_store / "-home-rgoswami-proj"
    project.mkdir(parents=True)
    sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    jsonl = project / f"{sid}.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": sid,
                "message": {"role": "user", "content": "hi"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "anqa.session.sources._adapter_store_roots",
        lambda: [first, jsonl_store],
    )
    walked: list[str] = []
    from anqa.session import watch as watch_mod

    real = watch_mod.discover_dirs

    def tracked(root: Path) -> list[Path]:
        walked.append(str(root))
        return real(root)

    monkeypatch.setattr(watch_mod, "discover_dirs", tracked)
    found = session_dirs_under([jsonl_store])
    resolved = {p.resolve() for p in found}
    assert jsonl.resolve() in resolved or project.resolve() in resolved
    assert walked == [str(jsonl_store)]
    targets = watch_target_paths([jsonl_store], found)
    assert all(p.is_dir() for p in targets)
    target_set = {p.resolve() for p in targets}
    assert project.resolve() in target_set
    assert jsonl_store.resolve() in target_set
    assert jsonl.resolve() not in target_set
    collected = {p.resolve() for p in TraceTreeWatch(jsonl_store, lambda: None)._collect_paths()}
    assert project.resolve() in collected
    assert jsonl.resolve() not in collected


def test_watch_target_paths_subscribes_parent_of_jsonl_locator(tmp_path: Path) -> None:
    """Non-recursive watch must subscribe the parent dir of a file locator."""
    store = tmp_path / "jsonl-projects"
    project = store / "-home-rgoswami-proj"
    project.mkdir(parents=True)
    jsonl = project / "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")
    targets = {p.resolve() for p in watch_target_paths([store], [jsonl])}
    assert store.resolve() in targets
    assert project.resolve() in targets
    assert jsonl.resolve() not in targets
    assert all(p.is_dir() for p in targets)


def test_session_dirs_under_date_bucketed_jsonl_store_still_discovers(
    tmp_path: Path, monkeypatch
) -> None:
    """A YYYY/MM/DD rollout store that is not adapter root 0 keeps discover."""
    first = tmp_path / "antigravity-store"
    first.mkdir()
    store = tmp_path / "dated-jsonl-sessions"
    day = store / "2026" / "09" / "04"
    day.mkdir(parents=True)
    sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    jsonl = day / f"rollout-2026-09-04T12-00-00-{sid}.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "anqa.session.sources._adapter_store_roots",
        lambda: [first, store],
    )
    walked: list[str] = []
    from anqa.session import watch as watch_mod

    real = watch_mod.discover_dirs

    def tracked(root: Path) -> list[Path]:
        walked.append(str(root))
        return real(root)

    monkeypatch.setattr(watch_mod, "discover_dirs", tracked)
    found = session_dirs_under([store])
    resolved = {p.resolve() for p in found}
    assert jsonl.resolve() in resolved or day.resolve() in resolved
    assert walked == [str(store)]
    targets = {p.resolve() for p in watch_target_paths([store], found)}
    assert day.resolve() in targets
    assert jsonl.resolve() not in targets


def test_jsonl_adapter_store_watch_fires_on_transcript_write(tmp_path: Path, monkeypatch) -> None:
    """A non-first jsonl store must see nested transcript writes (non-recursive)."""
    first = tmp_path / "antigravity-store"
    first.mkdir()
    jsonl_store = tmp_path / "jsonl-projects"
    project = jsonl_store / "-home-rgoswami-proj"
    project.mkdir(parents=True)
    sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    jsonl = project / f"{sid}.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": sid,
                "message": {"role": "user", "content": "hi"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "anqa.session.sources._adapter_store_roots",
        lambda: [first, jsonl_store],
    )
    hits: list[list[str]] = []
    w = TraceTreeWatch(
        jsonl_store,
        lambda: None,
        on_paths=lambda paths: hits.append(list(paths)),
    )
    assert w.start() is True
    try:
        _wait_watch_armed(w)
        subscribed = {p.resolve() for p in w.subscribed_paths()}
        assert project.resolve() in subscribed
        assert all(p.is_dir() for p in w.subscribed_paths())
        hits.clear()
        with jsonl.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": sid,
                        "message": {"role": "assistant", "content": "ok"},
                    }
                )
                + "\n"
            )
        wait_until_sync(lambda: bool(hits), description="jsonl transcript write fires")
    finally:
        w.stop()
    assert any(Path(p).name == jsonl.name for batch in hits for p in batch)


def test_plane_write_does_not_recollect_watch_paths(tmp_path: Path) -> None:
    session = _write_session(tmp_path, "sess")
    hits: list[int] = []
    w = TraceTreeWatch(tmp_path, lambda: hits.append(1), session_dir=session)
    assert w.start() is True
    _wait_watch_armed(w)
    collects = {"n": 0}
    real = w._collect_paths

    def counted() -> list[Path]:
        collects["n"] += 1
        return real()

    w._collect_paths = counted
    try:
        before = collects["n"]
        (session / "summary.json").write_text('{"title": "x"}\n', encoding="utf-8")
        wait_until_sync(lambda: bool(hits), description="plane write fires")
        assert collects["n"] == before
    finally:
        w.stop()


def test_session_dirs_under_membership_only_skips_find_sessions(
    tmp_path: Path, monkeypatch
) -> None:
    """Extra adapter stores must not walk the tree looking for session dirs."""
    store = tmp_path / "extra.db"
    store.write_bytes(b"")
    junk = tmp_path / "deep" / "nested"
    junk.mkdir(parents=True)
    (junk / "summary.json").write_text("{}", encoding="utf-8")
    (junk / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    walked: list[str] = []

    def tracked(root: Path) -> list[Path]:
        walked.append(str(root))
        return []

    monkeypatch.setattr("anqa.session.watch.discover_dirs", tracked)
    assert session_dirs_under([tmp_path], list_sessions=False) == []
    assert walked == []


def test_membership_only_watch_does_not_subscribe_sibling_dirs(tmp_path: Path) -> None:
    """A sqlite store's parent is watched; sibling project trees are not."""
    store = tmp_path / "sessions.db"
    store.write_bytes(b"")
    for i in range(40):
        child = tmp_path / f"proj-{i:02d}"
        child.mkdir()
        (child / "workspace").mkdir()
        (child / "workspace" / "a.py").write_text("x\n", encoding="utf-8")
    w = TraceTreeWatch(store, lambda: None)
    assert w._membership_only is True
    assert w.root == tmp_path
    paths = w._collect_paths()
    assert tmp_path in paths
    assert all(p == tmp_path or p.name.startswith("%2") for p in paths)
    assert not any(p.name.startswith("proj-") for p in paths)
    assert not any("workspace" in p.parts for p in paths)


def test_membership_only_dir_does_not_expand_children(tmp_path: Path, monkeypatch) -> None:
    """Jsonl / extra dir stores subscribe the membership dir only."""
    extra = tmp_path / "jsonl-store"
    extra.mkdir()
    for i in range(30):
        (extra / f"bucket-{i:02d}").mkdir()
    walked: list[str] = []

    def boom(root: Path) -> list[Path]:
        walked.append(str(root))
        raise AssertionError("find_sessions must not run for membership-only")

    monkeypatch.setattr("anqa.session.watch.discover_dirs", boom)
    w = TraceTreeWatch(extra, lambda: None, membership_only=True)
    paths = w._collect_paths()
    assert walked == []
    assert extra in paths
    assert not any(p.name.startswith("bucket-") for p in paths)


def test_file_membership_watch_dirs_is_parent_only(tmp_path: Path) -> None:
    from anqa.session.watch import membership_watch_dirs

    store = tmp_path / "store.sqlite"
    store.write_bytes(b"")
    (tmp_path / "other").mkdir()
    assert membership_watch_dirs([store]) == [tmp_path]


def test_host_shaped_new_session_plane_write_updates_subscription(tmp_path: Path) -> None:
    host = tmp_path / "sessions"
    bucket = host / "%2Fproj"
    bucket.mkdir(parents=True)
    hits: list[list[str]] = []
    w = TraceTreeWatch(
        host,
        lambda: None,
        on_paths=lambda paths: hits.append(list(paths)),
        host_root=host,
    )
    assert w.start() is True
    try:
        _wait_watch_armed(w)
        session = bucket / "late-host"
        session.mkdir()
        (session / "summary.json").write_text("{}", encoding="utf-8")
        (session / "updates.jsonl").write_text("{}\n", encoding="utf-8")
        wait_until_sync(
            lambda: any(p.resolve() == session.resolve() for p in w.subscribed_paths()),
            description="new host session dir subscribed after mkdir",
        )
        hits.clear()
        (session / "summary.json").write_text('{"title": "late"}\n', encoding="utf-8")
        wait_until_sync(lambda: bool(hits), description="plane write on new host session")
    finally:
        w.stop()
    assert any(Path(p).name == "summary.json" for batch in hits for p in batch)


def test_watch_target_paths_skips_cwd_bucket_subagent_siblings(tmp_path: Path) -> None:
    """Listed parents stay the watch set; cwd-bucket subagent siblings do not."""
    host = tmp_path / "sessions"
    bucket = host / "%2Fhome%2Fproj"
    parent = _write_session(bucket, "parent")
    sibling = _write_session(bucket, "child-sub")
    (sibling / "summary.json").write_text(
        '{"info":{"id":"child-sub"},"session_kind":"subagent"}',
        encoding="utf-8",
    )
    (parent / "subagents" / "child-sub").mkdir(parents=True)
    listed = session_dirs_under([host], host_root=host)
    assert [p.resolve() for p in listed] == [parent.resolve()]
    paths = {p.resolve() for p in watch_target_paths([host], listed)}
    assert host.resolve() in paths
    assert bucket.resolve() in paths
    assert parent.resolve() in paths
    assert sibling.resolve() not in paths
    expected = {p.resolve() for p in membership_watch_dirs([host])}
    expected.update(p.resolve() for p in listed)
    assert paths == expected
    watch = TraceTreeWatch(host, lambda: None, host_root=host)
    collected = {p.resolve() for p in watch._collect_paths()}
    assert collected == expected
    assert sibling.resolve() not in collected
