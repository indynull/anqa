"""OpenCode adapter against the committed synthesized store fixture."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tarfile
from pathlib import Path

import pytest
from anqa.harness.opencode import OPENCODE_HARNESS_ID, OpenCodeAdapter
from anqa.harness.registry import require_adapter
from anqa.harness.views import session_overview, session_timeline
from anqa.session.catalog import list_session_catalog
from anqa.session.delete import delete_session_dirs
from anqa.session.export_bundle import export_session_bundle
from anqa.session.query import CatalogQueryRow, row_matches_query

from .turn_status import assert_adapter_turn

_FIXTURE_DB = (
    Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "opencode" / "opencode.db"
)


def _install_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dest = tmp_path / "opencode.db"
    shutil.copy2(_FIXTURE_DB, dest)
    monkeypatch.setattr("anqa.harness.opencode.default_db_path", lambda: dest)
    return dest


def test_discover_skips_child_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(tmp_path, monkeypatch)
    refs = OpenCodeAdapter().discover()
    assert [r.session_id for r in refs] == ["ses_running", "ses_probe"]
    assert refs[0].harness == OPENCODE_HARNESS_ID
    assert refs[1].ref_string() == "opencode:ses_probe"


def test_catalog_lists_opencode_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(tmp_path, monkeypatch)
    rows = list_session_catalog(include_host=True)
    by_id = {str(row["sessionId"]): row for row in rows}
    assert "ses_probe" in by_id
    assert "ses_running" in by_id
    assert "ses_child" not in by_id
    assert by_id["ses_probe"]["harness"] == OPENCODE_HARNESS_ID
    assert by_id["ses_probe"]["path"] == "opencode:ses_probe"
    assert by_id["ses_probe"]["status"] == "complete"
    assert by_id["ses_running"]["status"] == "running"


def test_list_meta_does_not_replay_event_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anqa.harness.opencode as oc

    db = _install_store(tmp_path, monkeypatch)
    con = sqlite3.connect(db)
    try:
        con.execute(
            "UPDATE session SET tokens_input = 10, tokens_output = 20, "
            "tokens_reasoning = 5 WHERE id = ?",
            ("ses_probe",),
        )
        con.commit()
    finally:
        con.close()

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("_load_payload must not run for list meta")

    monkeypatch.setattr(oc, "_load_payload", boom)
    adapter = oc.OpenCodeAdapter()
    ref = Path("opencode:ses_probe")
    meta = adapter.load_meta(ref)
    assert meta.title
    assert meta.harness == "opencode"
    assert meta.turn_outcome != ""
    assert meta.context_tokens_used == 35


def test_load_meta_and_timeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(tmp_path, monkeypatch)
    probe = Path("opencode:ses_probe")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.harness == OPENCODE_HARNESS_ID
    assert meta.title == "Reply with PROBE_OK"
    assert meta.model_id == "opencode/probe-model"
    assert meta.harness_version == "1.18.25"
    assert meta.list_status_label() == "complete"
    events = require_adapter(probe).parse_timeline(probe)
    types = [e.event_type for e in events]
    assert "user_message_chunk" in types
    assert "agent_message_chunk" in types
    assert "tool_call" in types
    tool = next(e for e in events if e.event_type == "tool_call")
    assert tool.tool_name == "bash"
    assert tool.raw_input.as_str("command") == "echo PROBE_OK"


def test_delete_session_removes_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(tmp_path, monkeypatch)
    stats = delete_session_dirs([Path("opencode:ses_probe")])
    assert int(stats["deleted"] or 0) == 1
    with pytest.raises(FileNotFoundError):
        require_adapter(Path("opencode:ses_probe")).load_meta(Path("opencode:ses_probe"))


def test_running_session_is_not_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(tmp_path, monkeypatch)
    assert_adapter_turn(Path("opencode:ses_probe"), "complete")
    assert_adapter_turn(Path("opencode:ses_running"), "running")


def _insert_session(db: Path, sid: str, messages: list[str]) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO session (id, title, time_created, time_updated) VALUES (?, ?, 1, 2)",
            (sid, sid),
        )
        for i, data in enumerate(messages):
            con.execute(
                "INSERT INTO message (id, session_id, time_created, time_updated, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"{sid}-m{i}", sid, i + 1, i + 1, data),
            )
        con.commit()
    finally:
        con.close()


def test_list_status_last_user_and_later_open_after_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _install_store(tmp_path, monkeypatch)
    _insert_session(db, "ses_user", ['{"role":"user","time":{"created":1}}'])
    _insert_session(
        db,
        "ses_later",
        [
            '{"role":"assistant","finish":"stop","time":{"created":1,"completed":2}}',
            '{"role":"user","time":{"created":3}}',
        ],
    )
    assert_adapter_turn(Path("opencode:ses_user"), "idle")
    assert_adapter_turn(Path("opencode:ses_later"), "idle")


def test_overview_stats_count_timeline_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_store(tmp_path, monkeypatch)
    ref = OpenCodeAdapter().ref_for_id("ses_probe")
    assert ref is not None
    ov = session_overview(ref)
    assert ov["meta"]["harness"] == OPENCODE_HARNESS_ID
    assert ov["meta"]["harnessLabel"] == "OpenCode"
    stats = ov["stats"]
    types = {row["id"]: int(row["count"]) for row in stats["eventTypes"]}
    tools = {row["id"]: int(row["count"]) for row in stats["tools"]}
    assert types.get("tool_call", 0) >= 1
    assert tools.get("bash", 0) >= 1


def test_adapted_timeline_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(tmp_path, monkeypatch)
    ref = OpenCodeAdapter().ref_for_id("ses_probe")
    assert ref is not None
    page = session_timeline(ref)
    assert page["sessionId"] == "ses_probe"
    assert int(page["total"] or 0) >= 3
    texts = " ".join(str(ev.get("content") or "") for ev in page["events"])
    assert "PROBE_OK" in texts


def test_task_tool_emits_subagent_bookends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(tmp_path, monkeypatch)
    adapter = OpenCodeAdapter()
    assert adapter.ref_for_id("ses_child") is not None
    probe = Path("opencode:ses_probe")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.has_subagents
    assert meta.subagent_count == 1
    events = require_adapter(probe).parse_timeline(probe)
    types = [e.event_type for e in events]
    assert "subagent_spawned" in types
    assert "subagent_finished" in types
    spawn = next(e for e in events if e.event_type == "subagent_spawned")
    assert spawn.raw_input.as_str("child_session_id") == "ses_child"
    assert spawn.raw_input.as_str("subagent_type") == "explore"
    bound = adapter.ref_for_id("ses_probe")
    assert bound is not None
    ov = session_overview(bound)
    runs = ov["turns"]["subagentRuns"]
    assert runs
    assert runs[0]["childSessionId"] == "ses_child"
    assert runs[0]["openable"] is True
    assert runs[0]["childPath"] == "opencode:ses_child"


def test_export_bundle_from_harness_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(tmp_path, monkeypatch)
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(Path("opencode:ses_probe"), dest=dest)
    assert dest.is_file()
    assert result.session_id == "ses_probe"
    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
    assert "session.tar.gz" in names
    inner = tmp_path / "session.tar.gz"
    with tarfile.open(dest, "r:gz") as tf:
        extracted = tf.extractfile("session.tar.gz")
        assert extracted is not None
        inner.write_bytes(extracted.read())
    with tarfile.open(inner, "r:gz") as tf:
        members = tf.getnames()
    assert "ses_probe/session.json" in members


def test_stamps_and_bind_locator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _install_store(tmp_path, monkeypatch)
    adapter = OpenCodeAdapter()
    stamp = adapter.timeline_stamp("opencode:ses_probe")
    assert stamp[0] > 0
    assert stamp[1] > 0
    assert adapter.trace_mtime("opencode:ses_probe") == stamp[0]
    assert adapter.updates_size("opencode:ses_probe") == stamp[1]
    assert adapter.bind_locator(db) is None
    assert adapter.looks_like(db) is True
    assert adapter.list_turn_outcome("opencode:ses_probe") == "complete"


def test_harness_query_token() -> None:
    row = CatalogQueryRow(session_id="ses_probe", title="x", harness="opencode")
    assert row_matches_query(row, "harness:opencode")
    assert not row_matches_query(row, "harness:grok")


def test_require_adapter_path_loads_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(tmp_path, monkeypatch)
    meta = require_adapter(Path("opencode:ses_probe")).load_meta(Path("opencode:ses_probe"))
    assert meta.session_id == "ses_probe"
    assert meta.title == "Reply with PROBE_OK"


def test_wal_write_is_a_list_rebuild_path() -> None:
    from anqa.control.daemon import CatalogWatchApply

    assert CatalogWatchApply.list_rebuild_path(Path("/store/opencode.db")) is True
    assert CatalogWatchApply.list_rebuild_path(Path("/store/opencode.db-wal")) is True
    assert CatalogWatchApply.list_rebuild_path(Path("/store/noise.bin")) is False


def test_wal_event_adds_new_opencode_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A WAL write remetas the sqlite store so a new session appears."""
    from anqa.control.daemon import apply_fs_catalog_events
    from anqa.session.catalog import SessionCatalogCache

    db = _install_store(tmp_path, monkeypatch)
    traces = tmp_path / "traces"
    traces.mkdir()
    cache = SessionCatalogCache(traces_path=traces, include_host=True, ttl=3600.0)
    cache.get(force=True)
    ids = {str(row["sessionId"]) for row in cache.get()}
    assert "ses_probe" in ids
    assert "ses_new" not in ids

    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ses_new",
            None,
            "/tmp/probe-ws",
            "new live session",
            json.dumps({"id": "probe-model", "providerID": "opencode"}),
            "1.18.25",
            1_700_000_003_000,
            1_700_000_003_000,
            None,
            0,
            0,
            0,
            0.0,
        ),
    )
    con.commit()
    con.close()
    wal = db.parent / "opencode.db-wal"
    wal.write_bytes(b"wal")
    apply_fs_catalog_events(cache, [str(wal)], [traces])
    assert cache._rows is not None
    ids = {str(row["sessionId"]) for row in cache._rows}
    assert "ses_new" in ids


def _event_store(path: Path) -> Path:
    """Minimal 1.18 event-sourced opencode.db (session/message/part empty)."""
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE event ("
            "id INTEGER PRIMARY KEY, aggregate_id TEXT, seq INTEGER, type TEXT, data TEXT)"
        )
        con.execute(
            "CREATE TABLE event_sequence (aggregate_id TEXT PRIMARY KEY, seq INTEGER, owner_id TEXT)"
        )
        parent = "ses_evt_parent"
        child = "ses_evt_child"
        rows: list[tuple[str, int, str, str]] = [
            (
                parent,
                0,
                "session.created.1",
                json.dumps(
                    {
                        "sessionID": parent,
                        "info": {
                            "id": parent,
                            "title": "Reply with EVENT_OK",
                            "directory": "/tmp/probe-ws",
                            "version": "1.18.25",
                            "model": {"id": "probe-model", "providerID": "opencode"},
                            "time": {"created": 1_700_000_100_000, "updated": 1_700_000_100_500},
                        },
                    }
                ),
            ),
            (
                parent,
                1,
                "message.updated.1",
                json.dumps(
                    {
                        "sessionID": parent,
                        "info": {
                            "id": "msg_u1",
                            "role": "user",
                            "sessionID": parent,
                            "time": {"created": 1_700_000_100_000},
                            "summary": {
                                "diffs": [
                                    {
                                        "file": "NOTE.txt",
                                        "status": "added",
                                        "additions": 1,
                                        "deletions": 0,
                                        "patch": "+++ b/NOTE.txt\n+EVENT_OK\n",
                                    }
                                ]
                            },
                        },
                    }
                ),
            ),
            (
                parent,
                2,
                "message.part.updated.1",
                json.dumps(
                    {
                        "sessionID": parent,
                        "part": {
                            "id": "prt_t1",
                            "messageID": "msg_u1",
                            "sessionID": parent,
                            "type": "text",
                            "text": "write EVENT_OK",
                        },
                    }
                ),
            ),
            (
                parent,
                3,
                "message.updated.1",
                json.dumps(
                    {
                        "sessionID": parent,
                        "info": {
                            "id": "msg_a1",
                            "role": "assistant",
                            "sessionID": parent,
                            "time": {"created": 1_700_000_100_100, "completed": 1_700_000_100_400},
                        },
                    }
                ),
            ),
            (
                parent,
                4,
                "message.part.updated.1",
                json.dumps(
                    {
                        "sessionID": parent,
                        "part": {
                            "id": "prt_w1",
                            "messageID": "msg_a1",
                            "sessionID": parent,
                            "type": "tool",
                            "tool": "write",
                            "callID": "c1",
                            "state": {
                                "status": "completed",
                                "input": {"filePath": "NOTE.txt", "content": "EVENT_OK\n"},
                                "output": "wrote NOTE.txt",
                            },
                        },
                    }
                ),
            ),
            (
                child,
                0,
                "session.created.1",
                json.dumps(
                    {
                        "sessionID": child,
                        "info": {
                            "id": child,
                            "parentID": parent,
                            "title": "child",
                            "directory": "/tmp/probe-ws",
                            "time": {"created": 1_700_000_100_200, "updated": 1_700_000_100_200},
                        },
                    }
                ),
            ),
        ]
        for i, (aid, seq, typ, data) in enumerate(rows, start=1):
            con.execute(
                "INSERT INTO event (id, aggregate_id, seq, type, data) VALUES (?,?,?,?,?)",
                (i, aid, seq, typ, data),
            )
        con.execute(
            "INSERT INTO event_sequence (aggregate_id, seq, owner_id) VALUES (?,?,?)",
            (parent, 4, ""),
        )
        con.execute(
            "INSERT INTO event_sequence (aggregate_id, seq, owner_id) VALUES (?,?,?)",
            (child, 0, ""),
        )
        con.commit()
    finally:
        con.close()
    return path


def test_event_store_discover_meta_timeline_and_diff(tmp_path: Path) -> None:
    """OpenCode 1.18 writes session.created / message.* events, not session rows."""
    from anqa.harness.views import session_diff

    db = _event_store(tmp_path / "opencode.db")
    adapter = OpenCodeAdapter()
    refs = adapter.discover(roots=[db])
    assert [r.session_id for r in refs] == ["ses_evt_parent"]
    ref = refs[0]
    assert ref.cwd == "/tmp/probe-ws"
    meta = adapter.load_meta(ref)
    assert meta.title == "Reply with EVENT_OK"
    assert meta.model_id == "opencode/probe-model"
    assert meta.harness_version == "1.18.25"
    assert meta.has_subagents
    assert meta.subagent_count == 1
    assert meta.list_status_label() == "complete"
    events = adapter.parse_timeline(ref)
    types = [e.event_type for e in events]
    assert types[0] == "turn_started"
    assert "user_message_chunk" in types
    assert "tool_call" in types
    write = next(e for e in events if e.event_type == "tool_call")
    assert write.tool_name == "write"
    assert write.raw_input.as_str("filePath") == "NOTE.txt"
    doc = session_diff(ref)
    assert doc["points"]
    paths = [str(f["path"]) for f in doc["points"][0]["files"]]
    assert paths == ["NOTE.txt"]
    assert "EVENT_OK" in str(doc["points"][0]["files"][0]["unified"])


def test_event_log_loads_when_message_table_is_empty_for_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live 1.18 keeps a message table; older sessions only have event rows."""
    db = _event_store(tmp_path / "opencode.db")
    con = sqlite3.connect(db)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS message ("
            "id TEXT, session_id TEXT, time_created INTEGER, "
            "time_updated INTEGER, data TEXT)"
        )
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr("anqa.harness.opencode.default_db_path", lambda: db)
    probe = Path("opencode:ses_evt_parent")
    events = require_adapter(probe).parse_timeline(probe)
    types = [e.event_type for e in events]
    assert "user_message_chunk" in types
    assert "tool_call" in types
    from anqa.session.access import LocalSessionAccess

    ov = LocalSessionAccess(resolve_session=lambda _s: None).session_overview(
        "opencode:ses_evt_parent"
    )
    assert int((ov.get("turns") or {}).get("total") or 0) >= 1


def test_cwd_prefixed_harness_ref_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path.resolve() of opencode:id must not lose the adapter."""
    db = _install_store(tmp_path, monkeypatch)
    _ = db
    prefixed = Path.cwd() / "opencode:ses_probe"
    meta = require_adapter(prefixed).load_meta(prefixed)
    assert meta.session_id == "ses_probe"
    events = require_adapter(prefixed).parse_timeline(prefixed)
    assert any(e.event_type == "user_message_chunk" for e in events)
