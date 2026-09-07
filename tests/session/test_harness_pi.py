"""Pi adapter against the committed synthesized store fixture."""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

from anqa.harness.pi import PI_HARNESS_ID, PiAdapter
from anqa.harness.registry import require_adapter
from anqa.harness.views import session_overview, session_timeline
from anqa.session.catalog import list_session_catalog
from anqa.session.export_bundle import export_session_bundle
from anqa.session.query import CatalogQueryRow, row_matches_query

from .turn_status import assert_adapter_turn

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "pi" / "sessions"
_SID = "019fe000-0000-7000-8000-000000000001"
_RUNNING_SID = "019fe000-0000-7000-8000-000000000002"
_FIXTURE_FILE = _FIXTURE_ROOT / "tmp-probe" / f"2026-08-09T12-00-00-000Z_{_SID}.jsonl"


def _install_store() -> Path:
    dest = Path.home() / ".pi" / "agent" / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE_ROOT, dest, dirs_exist_ok=True)
    return dest / "tmp-probe" / _FIXTURE_FILE.name


def test_discover_and_meta() -> None:
    path = _install_store()
    refs = PiAdapter().discover()
    by_id = {ref.session_id: ref for ref in refs}
    assert set(by_id) == {_SID, _RUNNING_SID}
    assert by_id[_SID].harness == PI_HARNESS_ID
    assert by_id[_SID].ref_string() == f"pi:{_SID}"
    assert by_id[_SID].locator == path.resolve()
    probe = Path(f"pi:{_SID}")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.harness == PI_HARNESS_ID
    assert meta.title == "Reply with PI_PROBE_OK"
    assert meta.model_id == "xai/grok-4.5"
    assert meta.reasoning_effort == "medium"
    assert meta.context_tokens_used == 14
    assert meta.num_messages == 3
    assert meta.harness_version == ""
    assert meta.tool_call_count >= 1
    assert meta.list_status_label() == "complete"


def test_load_meta_does_not_read_the_full_transcript(monkeypatch) -> None:
    path = _install_store()

    def boom(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("list-meta must not read the full transcript")

    monkeypatch.setattr("anqa.json_lines.json_lines", boom)
    meta = PiAdapter().load_meta(path)
    assert meta.session_id == _SID
    assert meta.title == "Reply with PI_PROBE_OK"
    assert meta.list_status_label() == "complete"


def test_load_meta_on_a_large_transcript_uses_header_and_tail(tmp_path: Path) -> None:
    """List-meta reads the header and a 64 KiB tail, not the middle."""
    path = tmp_path / "2026-08-09T12-00-00-000Z_019fe000-0000-7000-8000-aaaaaaaa.jsonl"
    header = (
        '{"type":"session","version":3,"id":"019fe000-0000-7000-8000-aaaaaaaa",'
        '"timestamp":"2026-08-09T12:00:00.000Z","cwd":"/tmp/probe-ws"}\n'
        '{"type":"message","message":{"role":"user","content":'
        '[{"type":"text","text":"HEADER_TITLE"}]}}\n'
    )
    pad = "x" * 180
    middle = "".join(
        f'{{"type":"message","message":{{"role":"user","content":'
        f'[{{"type":"text","text":"MIDDLE_{i}_{pad}"}}]}}}}\n'
        for i in range(2500)
    )
    tail = (
        '{"type":"message","message":{"role":"assistant","stopReason":"stop",'
        '"content":[{"type":"text","text":"TAIL_OK"}]}}\n'
    )
    path.write_text(header + middle + tail, encoding="utf-8")
    assert path.stat().st_size > 64 * 1024
    meta = PiAdapter().load_meta(path)
    assert meta.session_id == "019fe000-0000-7000-8000-aaaaaaaa"
    assert "MIDDLE_2000" not in (meta.title or "")
    assert meta.list_status_label() == "complete"


def test_catalog_lists_pi_sessions() -> None:
    _install_store()
    rows = list_session_catalog(include_host=True)
    by_id = {str(row["sessionId"]): row for row in rows}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert by_id[_SID]["harness"] == PI_HARNESS_ID
    assert by_id[_SID]["path"] == f"pi:{_SID}"
    assert by_id[_SID]["status"] == "complete"
    assert by_id[_RUNNING_SID]["status"] == "running"


def test_last_tool_use_is_running() -> None:
    _install_store()
    assert_adapter_turn(Path(f"pi:{_RUNNING_SID}"), "running")


def test_list_status_close_bookend_and_later_user(tmp_path: Path) -> None:
    def _write(name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    header = '{"type":"session","version":3,"id":"pi-x","timestamp":"2026-08-09T12:00:00.000Z"}\n'
    closed = _write(
        "closed.jsonl",
        header
        + '{"type":"message","message":{"role":"assistant","content":[{"type":"text","text":"ok"}],'
        '"stopReason":"stop"}}\n',
    )
    bookend = _write(
        "bookend.jsonl",
        header
        + '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"hi"}]}}\n',
    )
    later = _write(
        "later.jsonl",
        header
        + '{"type":"message","message":{"role":"assistant","stopReason":"stop"}}\n'
        + '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"again"}]}}\n',
    )
    inflight = _write(
        "inflight.jsonl",
        header + '{"type":"message","message":{"role":"assistant","stopReason":"toolUse",'
        '"content":[{"type":"toolCall","name":"bash","arguments":{"command":"echo x"}}]}}\n'
        + '{"type":"message","message":{"role":"toolResult","toolName":"bash",'
        '"content":[{"type":"text","text":"x"}]}}\n',
    )
    assert_adapter_turn(closed, "complete")
    assert_adapter_turn(bookend, "idle")
    assert_adapter_turn(later, "idle")
    assert_adapter_turn(inflight, "running")


def test_overview_stats_count_timeline_tools() -> None:
    _install_store()
    ref = PiAdapter().ref_for_id(_SID)
    assert ref is not None
    ov = session_overview(ref)
    assert ov["meta"]["harness"] == PI_HARNESS_ID
    assert ov["meta"]["harnessLabel"] == "Pi"
    stats = ov["stats"]
    types = {row["id"]: int(row["count"]) for row in stats["eventTypes"]}
    tools = {row["id"]: int(row["count"]) for row in stats["tools"]}
    assert types.get("tool_call", 0) >= 1
    assert tools.get("bash", 0) >= 1


def test_timeline_user_tool_result() -> None:
    _install_store()
    ref = PiAdapter().ref_for_id(_SID)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    types = [e.event_type for e in events]
    assert "current_mode_update" in types
    assert "turn_started" in types
    assert "user_message_chunk" in types
    assert "agent_thought_chunk" in types
    assert "tool_call" in types
    assert "tool_call_update" in types
    tool = next(e for e in events if e.event_type == "tool_call")
    assert tool.tool_name == "bash"
    assert tool.raw_input.as_str("command") == "echo PI_PROBE_OK"
    result = next(e for e in events if e.event_type == "tool_call_update")
    assert "PI_PROBE_OK" in result.content
    page = session_timeline(ref)
    assert int(page["total"] or 0) >= 5
    texts = " ".join(str(ev.get("content") or "") for ev in page["events"])
    assert "PI_PROBE_OK" in texts


def test_ref_for_id_and_query() -> None:
    path = _install_store()
    adapter = PiAdapter()
    found = adapter.ref_for_id(_SID)
    assert found is not None
    assert found.session_id == _SID
    assert adapter.looks_like(found)
    assert adapter.bind_locator(path) is not None
    dest = path.parent / "pi.tar.gz"
    members = adapter.write_archive(found, dest)
    assert members == [f"{_SID}/{path.name}"]
    with tarfile.open(dest, "r:gz") as tf:
        assert tf.getnames() == members
    row = CatalogQueryRow(session_id=_SID, title="x", harness="pi")
    assert row_matches_query(row, "harness:pi")
    assert not row_matches_query(row, "harness:opencode")


def _write_subagent_session(path: Path) -> Path:
    """Shape copied from ``01a05d05-8035-78c7-acb5-fe1d31b380fd`` (Pi 0.84.4)."""
    path.write_text(
        '{"type":"session","version":3,"id":"01a05d05-8035-78c7-acb5-fe1d31b380fd",'
        '"timestamp":"2026-09-01T12:50:36.725Z","cwd":"/mnt/dev/_git/aisandbox"}\n'
        '{"type":"message","id":"u1","timestamp":"2026-09-01T12:50:37.000Z",'
        '"message":{"role":"user","content":[{"type":"text",'
        '"text":"launch 5 subagents and get them all to propose some interesting change"}]}}\n'
        '{"type":"message","id":"a1","timestamp":"2026-09-01T12:51:00.000Z",'
        '"message":{"role":"assistant","content":['
        '{"type":"toolCall","id":"call-sub-1","name":"subagent","arguments":'
        '{"agentScope":"user","tasks":['
        '{"agent":"worker","task":"You are Proposal Agent A.","cwd":"/mnt/dev/_git/aisandbox"},'
        '{"agent":"worker","task":"You are Proposal Agent B.","cwd":"/mnt/dev/_git/aisandbox"}'
        "]}}]}}\n"
        '{"type":"message","id":"t1","timestamp":"2026-09-01T12:54:00.000Z",'
        '"message":{"role":"toolResult","toolCallId":"call-sub-1","toolName":"subagent",'
        '"content":[{"type":"text","text":"Parallel: 2/2 succeeded"}],'
        '"details":{"mode":"parallel","agentScope":"user","results":['
        '{"agent":"worker","exitCode":0,"task":"You are Proposal Agent A.",'
        '"messages":[{"role":"assistant","content":[{"type":"text",'
        '"text":"## Proposal Title\\nThe Agent Lab Notebook"}]}]},'
        '{"agent":"worker","exitCode":0,"task":"You are Proposal Agent B.",'
        '"messages":[{"role":"assistant","content":[{"type":"text",'
        '"text":"## Proposal Title\\nThe Prompt Lab Notebook"}]}]}'
        ']},"isError":false}}\n'
        '{"type":"message","id":"a2","timestamp":"2026-09-01T12:55:00.000Z",'
        '"message":{"role":"assistant","content":['
        '{"type":"toolCall","id":"call-w1","name":"write","arguments":'
        '{"path":"/mnt/dev/_git/aisandbox/README.md","content":"# aisandbox\\n"}}'
        "]}}\n"
        '{"type":"message","id":"t2","timestamp":"2026-09-01T12:55:01.000Z",'
        '"message":{"role":"toolResult","toolCallId":"call-w1","toolName":"write",'
        '"content":[{"type":"text","text":"Successfully wrote 12 bytes"}],'
        '"isError":false}}\n',
        encoding="utf-8",
    )
    return path


def test_subagent_tool_emits_spawn_and_finish_bookends(tmp_path: Path) -> None:
    """Pi ``subagent`` tasks become Overview / Timeline Subagents rows."""
    path = _write_subagent_session(tmp_path / "sess.jsonl")
    ref = PiAdapter().bind_locator(path)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    types = [e.event_type for e in events]
    assert types.count("subagent_spawned") == 2
    assert types.count("subagent_finished") == 2
    spawned = [e for e in events if e.event_type == "subagent_spawned"]
    assert spawned[0].raw_input.as_str("subagent_type") == "worker"
    assert "Proposal Agent A" in (spawned[0].raw_input.as_str("description") or "")
    finished = [e for e in events if e.event_type == "subagent_finished"]
    assert "Agent Lab Notebook" in finished[0].content
    ov = session_overview(ref)
    runs = ov["turns"]["subagentRuns"]
    assert len(runs) == 2
    assert runs[0]["subagentType"] == "worker"
    assert runs[0]["status"] == "completed"
    assert runs[0]["openable"] is False
    assert not (runs[0].get("childPath") or "")
    from anqa.harness.views import session_diff

    diff = session_diff(ref)
    paths = [str(f["path"]) for f in diff["points"][0]["files"]]
    assert paths == ["/mnt/dev/_git/aisandbox/README.md"]


def test_session_diff_empty_without_file_edits() -> None:
    """Pi probe session only ran bash, so Diff has no points."""
    from anqa.harness.views import session_diff

    _install_store()
    ref = PiAdapter().ref_for_id(_SID)
    assert ref is not None
    payload = session_diff(ref)
    assert payload["sessionId"] == _SID
    assert payload["points"] == []


def test_session_diff_uses_edit_and_write_tools(tmp_path: Path) -> None:
    """Diff pane for a Pi locator is built from edit/write tool calls."""
    from anqa.harness.views import session_diff

    path = tmp_path / "2026-08-09T12-00-00-000Z_019fe000-0000-7000-8000-00000000ed01.jsonl"
    path.write_text(
        '{"type":"session","version":3,"id":"019fe000-0000-7000-8000-00000000ed01",'
        '"timestamp":"2026-08-09T12:00:00.000Z","cwd":"/tmp/probe-ws"}\n'
        '{"type":"message","id":"u1","timestamp":"2026-08-09T12:00:01.000Z",'
        '"message":{"role":"user","content":[{"type":"text","text":"edit the file"}]}}\n'
        '{"type":"message","id":"a1","timestamp":"2026-08-09T12:00:02.000Z",'
        '"message":{"role":"assistant","content":['
        '{"type":"toolCall","id":"c1","name":"edit","arguments":'
        '{"path":"/tmp/probe-ws/hello.py","edits":[{"oldText":"return 1","newText":"return 2"}]}}'
        "]}}\n"
        '{"type":"message","id":"a2","timestamp":"2026-08-09T12:00:03.000Z",'
        '"message":{"role":"assistant","content":['
        '{"type":"toolCall","id":"c2","name":"write","arguments":'
        '{"path":"/tmp/probe-ws/NOTE.txt","content":"WS1\\n"}}'
        "]}}\n",
        encoding="utf-8",
    )
    ref = PiAdapter().bind_locator(path)
    assert ref is not None
    payload = session_diff(ref)
    assert payload["sessionId"] == "019fe000-0000-7000-8000-00000000ed01"
    assert payload["source"] == "search_replace"
    points = payload["points"]
    assert len(points) == 1
    paths = [str(f["path"]) for f in points[0]["files"]]
    assert paths == ["/tmp/probe-ws/NOTE.txt", "/tmp/probe-ws/hello.py"]
    unified = "\n".join(str(f["unified"]) for f in points[0]["files"])
    assert "return 2" in unified
    assert "WS1" in unified
    assert points[0]["promptIndex"] == 0


def test_session_diff_one_point_per_user_turn(tmp_path: Path) -> None:
    """Diff picker lists each Pi turn that edited a file."""
    from anqa.harness.views import session_diff

    path = tmp_path / "turns.jsonl"
    path.write_text(
        '{"type":"session","version":3,"id":"pi-turns","timestamp":"2026-08-09T12:00:00.000Z"}\n'
        '{"type":"message","id":"u1","parentId":"pi-turns","message":{"role":"user",'
        '"content":[{"type":"text","text":"edit hello"}]}}\n'
        '{"type":"message","id":"a1","parentId":"u1","message":{"role":"assistant",'
        '"content":[{"type":"toolCall","id":"c1","name":"edit","arguments":'
        '{"path":"/tmp/hello.py","edits":[{"oldText":"1","newText":"2"}]}}]}}\n'
        '{"type":"message","id":"u2","parentId":"a1","message":{"role":"user",'
        '"content":[{"type":"text","text":"add a note"}]}}\n'
        '{"type":"message","id":"a2","parentId":"u2","message":{"role":"assistant",'
        '"content":[{"type":"toolCall","id":"c2","name":"write","arguments":'
        '{"path":"/tmp/NOTE.txt","content":"hi\\n"}}]}}\n',
        encoding="utf-8",
    )
    ref = PiAdapter().bind_locator(path)
    assert ref is not None
    points = session_diff(ref)["points"]
    assert [p["promptIndex"] for p in points] == [0, 1]
    assert [f["path"] for f in points[0]["files"]] == ["/tmp/hello.py"]
    assert [f["path"] for f in points[1]["files"]] == ["/tmp/NOTE.txt"]
    assert "edit hello" in str(points[0]["prompt"])
    assert "add a note" in str(points[1]["prompt"])


def test_jsonl_write_is_a_list_rebuild_path() -> None:
    from anqa.control.daemon import CatalogWatchApply

    assert CatalogWatchApply.list_rebuild_path(Path("/store/tmp-probe/sess.jsonl")) is True
    assert CatalogWatchApply.list_rebuild_path(Path("/store/noise.bin")) is False


def test_jsonl_write_adds_new_pi_session(tmp_path: Path) -> None:
    """A new jsonl under the Pi store remetas so the session appears."""
    from anqa.control.daemon import apply_fs_catalog_events
    from anqa.session.catalog import SessionCatalogCache

    dest = _install_store()
    traces = tmp_path / "traces"
    traces.mkdir()
    cache = SessionCatalogCache(traces_path=traces, include_host=True, ttl=3600.0)
    cache.get(force=True)
    ids = {str(row["sessionId"]) for row in cache.get()}
    assert _SID in ids
    assert "019fe000-0000-7000-8000-000000000003" not in ids

    new = dest.parent / "2026-08-09T12-00-02-000Z_019fe000-0000-7000-8000-000000000003.jsonl"
    new.write_text(
        '{"type": "session", "version": 3, "id": "019fe000-0000-7000-8000-000000000003", '
        '"timestamp": "2026-08-09T12:00:20.000Z", "cwd": "/tmp/probe-ws"}\n'
        '{"type": "message", "id": "u3", "parentId": "019fe000-0000-7000-8000-000000000003", '
        '"timestamp": "2026-08-09T12:00:21.000Z", '
        '"message": {"role": "user", "content": [{"type": "text", "text": "new"}]}}\n',
        encoding="utf-8",
    )
    apply_fs_catalog_events(cache, [str(new)], [traces])
    assert cache._rows is not None
    ids = {str(row["sessionId"]) for row in cache._rows}
    assert "019fe000-0000-7000-8000-000000000003" in ids


def test_export_bundle_from_harness_ref(tmp_path: Path) -> None:
    _install_store()
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(Path(f"pi:{_SID}"), dest=dest)
    assert dest.is_file()
    assert result.session_id == _SID
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
    assert f"{_SID}/{_FIXTURE_FILE.name}" in members


def test_load_detail_reads_session_info_usage_and_compaction(tmp_path: Path) -> None:
    path = tmp_path / "named.jsonl"
    path.write_text(
        '{"type":"session","version":3,"id":"pi-named","timestamp":"2026-08-09T12:00:00.000Z"}\n'
        '{"type":"session_info","id":"n1","parentId":"pi-named","name":"Operator title"}\n'
        '{"type":"thinking_level_change","id":"tl","parentId":"n1","thinkingLevel":"high"}\n'
        '{"type":"message","id":"u1","parentId":"tl","message":{"role":"user",'
        '"content":[{"type":"text","text":"first prompt"}]}}\n'
        '{"type":"compaction","id":"c1","parentId":"u1","summary":"kept the plan",'
        '"tokensBefore":9000,"firstKeptEntryId":"u1"}\n'
        '{"type":"message","id":"a1","parentId":"c1","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"ok"}],"stopReason":"stop",'
        '"usage":{"input":12,"output":3,"totalTokens":15}}}\n',
        encoding="utf-8",
    )
    ref = PiAdapter().bind_locator(path)
    assert ref is not None
    meta = require_adapter(ref).load_detail(ref)
    assert meta.title == "Operator title"
    assert meta.reasoning_effort == "high"
    assert meta.context_tokens_used == 15
    assert meta.compaction_count == 1
    assert meta.has_compaction
    assert meta.num_messages == 2
    types = [e.event_type for e in require_adapter(ref).parse_timeline(ref)]
    assert "compaction_checkpoint" in types
    assert "current_mode_update" in types


def test_timeline_follows_leaf_and_keeps_error(tmp_path: Path) -> None:
    path = tmp_path / "branch.jsonl"
    path.write_text(
        '{"type":"session","version":3,"id":"pi-br","timestamp":"2026-08-09T12:00:00.000Z"}\n'
        '{"type":"message","id":"u1","parentId":"pi-br","message":{"role":"user",'
        '"content":[{"type":"text","text":"old"}]}}\n'
        '{"type":"message","id":"a1","parentId":"u1","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"abandoned"}],"stopReason":"stop"}}\n'
        '{"type":"message","id":"u2","parentId":"pi-br","message":{"role":"user",'
        '"content":[{"type":"text","text":"new"}]}}\n'
        '{"type":"message","id":"a2","parentId":"u2","message":{"role":"assistant",'
        '"content":[],"stopReason":"error","errorMessage":"OAuth refresh failed"}}\n',
        encoding="utf-8",
    )
    ref = PiAdapter().bind_locator(path)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    texts = [e.content for e in events if e.event_type == "user_message_chunk"]
    assert texts == ["new"]
    assert not any(e.content == "abandoned" for e in events)
    assert any(e.event_type == "session_error" and "OAuth" in e.content for e in events)
    assert require_adapter(ref).load_detail(ref).list_status_label() == "cancelled"


def test_bash_execution_and_v4_header(tmp_path: Path) -> None:
    path = tmp_path / "v4.jsonl"
    path.write_text(
        '{"kind":"header","version":4,"id":"pi-v4","createdAt":1788722825000,"cwd":"/tmp"}\n'
        '{"type":"message","id":"b1","parentId":"pi-v4","message":{"role":"bashExecution",'
        '"command":"echo hi","output":"hi\\n","exitCode":0}}\n',
        encoding="utf-8",
    )
    ref = PiAdapter().bind_locator(path)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    tools = [e for e in events if e.event_type == "tool_call"]
    assert len(tools) == 1
    assert tools[0].tool_name == "bash"
    assert tools[0].raw_input.as_str("command") == "echo hi"
    outs = [e.content for e in events if e.event_type == "tool_call_update"]
    assert any("hi" in c for c in outs)
    meta = require_adapter(ref).load_detail(ref)
    assert meta.session_id == "pi-v4"
    assert meta.run_dir == "/tmp"
