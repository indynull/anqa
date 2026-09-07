"""Codex adapter against the committed synthesized store fixture."""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

from anqa.harness.codex import CODEX_HARNESS_ID, CodexAdapter
from anqa.harness.registry import require_adapter
from anqa.harness.views import session_diff, session_overview, session_timeline
from anqa.session.catalog import list_session_catalog
from anqa.session.delete import delete_session_dirs
from anqa.session.export_bundle import export_session_bundle
from anqa.session.query import CatalogQueryRow, row_matches_query

from .turn_status import assert_adapter_turn

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "codex" / "sessions"
_SID = "aaaaaaaa-1111-4111-8111-000000000001"
_RUNNING_SID = "bbbbbbbb-2222-4222-8222-000000000002"
_FIXTURE_FILE = _FIXTURE_ROOT / "2026" / "08" / "30" / f"rollout-2026-08-30T12-00-00-{_SID}.jsonl"


def _install_store() -> Path:
    dest = Path.home() / ".codex" / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE_ROOT, dest, dirs_exist_ok=True)
    return dest / "2026" / "08" / "30" / _FIXTURE_FILE.name


def test_discover_and_meta() -> None:
    path = _install_store()
    refs = CodexAdapter().discover()
    by_id = {ref.session_id: ref for ref in refs}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert by_id[_SID].harness == CODEX_HARNESS_ID
    assert by_id[_SID].ref_string() == f"codex:{_SID}"
    assert by_id[_SID].locator == path.resolve()
    probe = Path(f"codex:{_SID}")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.harness == CODEX_HARNESS_ID
    assert meta.title == "Reply with CODEX_PROBE_OK"
    assert meta.run_dir == "/tmp/codex-probe-ws"
    assert meta.model_id == "gpt-5.4"
    assert meta.harness_version == "0.151.0"
    assert meta.tool_call_count >= 1
    assert meta.has_subagents
    assert meta.subagent_count == 1
    assert meta.list_status_label() == "complete"


def test_load_meta_does_not_read_the_full_transcript(monkeypatch) -> None:
    path = _install_store()

    def boom(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("list-meta must not read the full transcript")

    monkeypatch.setattr("anqa.json_lines.json_lines", boom)
    meta = CodexAdapter().load_meta(path)
    assert meta.session_id == _SID
    assert meta.title == "Reply with CODEX_PROBE_OK"
    assert meta.list_status_label() == "complete"


def test_list_meta_does_not_map_the_window() -> None:
    _install_store()
    meta = CodexAdapter().load_meta(Path(f"codex:{_SID}"))
    assert meta.title == "Reply with CODEX_PROBE_OK"
    assert meta.turn_outcome != ""


def test_session_diff_reads_exec_apply_patch(tmp_path: Path) -> None:
    path = tmp_path / "rollout-2026-08-30T12-00-00-aaaaaaaa-1111-4111-8111-000000000099.jsonl"
    payload = {
        "type": "custom_tool_call",
        "name": "exec",
        "call_id": "c1",
        "input": (
            'const patch = "*** Begin Patch\\n*** Add File: NOTE.txt\\n+WS1\\n*** End Patch";\n'
            "await tools.apply_patch(patch);\n"
        ),
    }
    path.write_text(
        json.dumps(
            {"type": "session_meta", "payload": {"id": "aaaaaaaa-1111-4111-8111-000000000099"}}
        )
        + "\n"
        + json.dumps({"type": "response_item", "payload": payload})
        + "\n",
        encoding="utf-8",
    )
    ref = CodexAdapter().bind_locator(path)
    assert ref is not None
    doc = session_diff(ref)
    assert doc["points"]
    paths = [str(f["path"]) for f in doc["points"][0]["files"]]
    assert paths == ["NOTE.txt"]
    assert "WS1" in str(doc["points"][0]["files"][0]["unified"])


def test_session_diff_one_point_per_user_turn(tmp_path: Path) -> None:
    path = tmp_path / "rollout-2026-08-30T12-00-00-aaaaaaaa-1111-4111-8111-000000000098.jsonl"
    patch1 = "*** Begin Patch\\n*** Add File: hello.py\\n+1\\n*** End Patch"
    patch2 = "*** Begin Patch\\n*** Add File: NOTE.txt\\n+hi\\n*** End Patch"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "aaaaaaaa-1111-4111-8111-000000000098"},
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "edit hello"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "custom_tool_call",
                            "name": "exec",
                            "input": f'await tools.apply_patch("{patch1}");',
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "write note"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "custom_tool_call",
                            "name": "exec",
                            "input": f'await tools.apply_patch("{patch2}");',
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    ref = CodexAdapter().bind_locator(path)
    assert ref is not None
    points = session_diff(ref)["points"]
    assert [p["promptIndex"] for p in points] == [0, 1]
    assert [f["path"] for f in points[0]["files"]] == ["hello.py"]
    assert [f["path"] for f in points[1]["files"]] == ["NOTE.txt"]


def test_catalog_lists_codex_sessions() -> None:
    _install_store()
    rows = list_session_catalog(include_host=True)
    by_id = {str(row["sessionId"]): row for row in rows}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert by_id[_SID]["harness"] == CODEX_HARNESS_ID
    assert by_id[_SID]["path"] == f"codex:{_SID}"
    assert by_id[_SID]["status"] == "complete"
    assert by_id[_RUNNING_SID]["status"] == "idle"


def test_delete_session_removes_rollout() -> None:
    path = _install_store()
    stats = delete_session_dirs([path])
    assert int(stats["deleted"] or 0) == 1
    assert not path.exists()


def test_last_open_turn_is_idle() -> None:
    _install_store()
    assert_adapter_turn(Path(f"codex:{_RUNNING_SID}"), "idle")


def test_list_status_close_bookend_and_later_start(tmp_path: Path) -> None:
    def _ev(typ: str) -> str:
        return '{"type":"event_msg","payload":{"type":"' + typ + '"}}\n'

    meta = '{"type":"session_meta","payload":{"id":"cx","session_id":"cx"}}\n'
    closed = tmp_path / "rollout-2026-08-30T12-00-00-aaaaaaaa-1111-4111-8111-0000000000c1.jsonl"
    closed.write_text(meta + _ev("task_started") + _ev("task_complete"), encoding="utf-8")
    bookend = tmp_path / "rollout-2026-08-30T12-00-00-aaaaaaaa-1111-4111-8111-0000000000c2.jsonl"
    bookend.write_text(meta + _ev("task_started"), encoding="utf-8")
    later = tmp_path / "rollout-2026-08-30T12-00-00-aaaaaaaa-1111-4111-8111-0000000000c3.jsonl"
    later.write_text(
        meta + _ev("task_complete") + _ev("task_started"),
        encoding="utf-8",
    )
    aborted = tmp_path / "rollout-2026-08-30T12-00-00-aaaaaaaa-1111-4111-8111-0000000000c4.jsonl"
    aborted.write_text(meta + _ev("turn_aborted"), encoding="utf-8")
    assert_adapter_turn(closed, "complete")
    assert_adapter_turn(bookend, "idle")
    assert_adapter_turn(later, "idle")
    assert_adapter_turn(aborted, "cancelled")


def test_overview_lists_subagent_runs() -> None:
    _install_store()
    ref = CodexAdapter().ref_for_id(_SID)
    assert ref is not None
    ov = session_overview(ref)
    assert ov["meta"]["harness"] == CODEX_HARNESS_ID
    assert ov["meta"]["harnessLabel"] == "Codex"
    stats = ov["stats"]
    types = {row["id"]: int(row["count"]) for row in stats["eventTypes"]}
    tools = {row["id"]: int(row["count"]) for row in stats["tools"]}
    assert types.get("tool_call", 0) >= 1
    assert tools.get("exec", 0) >= 1
    runs = ov["turns"]["subagentRuns"]
    assert isinstance(runs, list)
    assert len(runs) == 1
    assert runs[0]["subagentId"] == "child-1"
    assert runs[0]["status"] == "completed"


def test_timeline_user_tool_result() -> None:
    _install_store()
    ref = CodexAdapter().ref_for_id(_SID)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    types = [e.event_type for e in events]
    assert types[0] == "turn_started"
    assert "user_message_chunk" in types
    assert "agent_message_chunk" in types
    assert "tool_call" in types
    assert "tool_call_update" in types
    assert "subagent_spawned" in types
    assert "subagent_finished" in types
    tool = next(e for e in events if e.event_type == "tool_call")
    assert tool.tool_name == "exec"
    assert tool.raw_input.as_str("command") == "echo CODEX_PROBE_OK"
    texts = " ".join(e.content for e in events)
    assert "CODEX_PROBE_OK" in texts
    assert "<environment_context>" not in texts
    page = session_timeline(ref)
    assert int(page["total"] or 0) >= 5


def test_ref_for_id_and_query() -> None:
    path = _install_store()
    adapter = CodexAdapter()
    found = adapter.ref_for_id(_SID)
    assert found is not None
    assert found.session_id == _SID
    assert adapter.looks_like(found)
    assert adapter.bind_locator(path) is not None
    dest = path.parent / "codex.tar.gz"
    members = adapter.write_archive(found, dest)
    assert f"{_SID}/{path.name}" in members
    with tarfile.open(dest, "r:gz") as tf:
        assert set(tf.getnames()) == set(members)
    row = CatalogQueryRow(session_id=_SID, title="x", harness="codex")
    assert row_matches_query(row, "harness:codex")
    assert not row_matches_query(row, "harness:copilot")


def test_export_bundle_from_harness_ref(tmp_path: Path) -> None:
    _install_store()
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(Path(f"codex:{_SID}"), dest=dest)
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
    assert any(name.endswith(".jsonl") and _SID in name for name in members)
