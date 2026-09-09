"""Session-surface contract every shipped adapter must satisfy.

Surfaces: ``docs/harness-adapters.md`` (Session surfaces).
Each case is a committed fixture under ``tests/fixtures/`` — not the
operator home store, and not a new invented session.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.harness.ref import HARNESS_IDS
from anqa.harness.registry import adapter, adapter_for, adapters
from anqa.harness.views import session_diff, session_overview
from anqa.session.catalog import catalog_row_for_ref
from anqa.session.query import CatalogQueryRow, row_matches_query

_ROOT = Path(__file__).resolve().parents[1]

# Values already asserted in tests/session/test_harness_<id>.py against
# tests/fixtures/harness/<id>/ (Grok: tests/fixtures/snapshots/minimal_session).
_SURFACE = {
    "antigravity": {
        "sid": "aaaaaaaa-1111-4111-8111-000000000001",
        "title": "Reply with AGY_PROBE_OK",
        "status": "complete",
        "tool": "run_command",
        "needle": "AGY_PROBE_OK",
    },
    "claude": {
        "sid": "aaaaaaaa-bbbb-4ccc-8ddd-000000000001",
        "title": "Reply with CLAUDE_PROBE_OK",
        "status": "complete",
        "tool": "Bash",
        "needle": "CLAUDE_PROBE_OK",
    },
    "codex": {
        "sid": "aaaaaaaa-1111-4111-8111-000000000001",
        "title": "Reply with CODEX_PROBE_OK",
        "status": "complete",
        "tool": "exec",
        "needle": "CODEX_PROBE_OK",
    },
    "copilot": {
        "sid": "aaaaaaaa-1111-4111-8111-000000000001",
        "title": "Reply with COPILOT_PROBE_OK",
        "status": "complete",
        "tool": "bash",
        "needle": "COPILOT_PROBE_OK",
    },
    "cursor": {
        "sid": "aaaaaaaa-1111-4111-8111-000000000001",
        "title": "Reply with CURSOR_PROBE_OK",
        "status": "complete",
        "tool": "Read",
        "needle": "CURSOR_PROBE_OK",
    },
    "gemini": {
        "sid": "aaaaaaaa-1111-4111-8111-000000000001",
        "title": "Reply with GEMINI_PROBE_OK",
        "status": "complete",
        "tool": "run_shell_command",
        "needle": "GEMINI_PROBE_OK",
    },
    "grok": {
        "sid": "minimal_session",
        "title": "Snapshot minimal",
        "status": "complete",
        "tool": "run_terminal_command",
        "needle": "Do the thing",
    },
    "opencode": {
        "sid": "ses_probe",
        "title": "Reply with PROBE_OK",
        "status": "complete",
        "tool": "bash",
        "needle": "PROBE_OK",
    },
    "pi": {
        "sid": "019fe000-0000-7000-8000-000000000001",
        "title": "Reply with PI_PROBE_OK",
        "status": "complete",
        "tool": "bash",
        "needle": "PI_PROBE_OK",
    },
}


def _fixture_root(hid: str) -> Path:
    if hid == "grok":
        return _ROOT / "fixtures" / "snapshots" / "minimal_session"
    if hid == "antigravity":
        return _ROOT / "fixtures" / "harness" / "antigravity" / "antigravity-cli"
    return _ROOT / "fixtures" / "harness" / hid


def _bind(hid: str):
    item = adapter(hid)
    assert item is not None
    want = _SURFACE[hid]
    root = _fixture_root(hid)
    if hid == "grok":
        ref = item.bind_locator(root)
        assert ref is not None
        assert ref.session_id == want["sid"]
        return item, ref
    refs = {ref.session_id: ref for ref in item.discover([root])}
    assert want["sid"] in refs
    return item, refs[want["sid"]]


def test_registered_adapters_declare_support() -> None:
    ids = [item.id for item in adapters()]
    assert set(ids) == set(HARNESS_IDS)
    assert set(_SURFACE) == set(HARNESS_IDS)
    for item in adapters():
        assert item.product
        assert item.supported_version
        assert (Path("tests/session") / f"test_harness_{item.id}.py").is_file()
        for hint in item.watch_hints():
            assert not hint.startswith("."), f"{item.id} watch_hints must be exact basenames"
            assert "/" not in hint


def test_adapter_for_path_returns_matching_adapter(tmp_path: Path) -> None:
    sd = tmp_path / "sess-factory"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    item = adapter_for(sd)
    assert item is not None
    assert item.id == "grok"
    assert adapter_for(tmp_path / "missing") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert adapter_for(empty) is None


@pytest.mark.parametrize("hid", sorted(HARNESS_IDS))
def test_adapter_survives_python_ingest_boom(hid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catalog list and timeline come from anqa.core, not leftover Python mappers."""
    import anqa.harness.grok_parse as grok_parse

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("product path must not call leftover Python ingest")

    item, ref = _bind(hid)
    monkeypatch.setattr(grok_parse, "parse_timeline", boom, raising=False)
    monkeypatch.setattr(grok_parse, "load_session_meta_list", boom, raising=False)
    monkeypatch.setattr("anqa.json_lines.json_lines", boom)
    meta = item.load_meta(ref)
    assert meta.harness == hid
    assert meta.title == _SURFACE[hid]["title"]
    events = item.parse_timeline(ref)
    assert events


@pytest.mark.parametrize("hid", sorted(HARNESS_IDS))
def test_session_surfaces_on_committed_fixture(hid: str, tmp_path: Path) -> None:
    want = _SURFACE[hid]
    item, ref = _bind(hid)
    assert ref.harness == hid
    assert ref.ref_string() == f"{hid}:{want['sid']}"
    assert ref.overlay_dir().parts[-2:] == (hid, want["sid"])

    meta = item.load_meta(ref)
    assert meta.harness == hid
    assert meta.title == want["title"]
    assert meta.list_status_label() == want["status"]

    events = item.parse_timeline(ref)
    if hid == "grok":
        assert meta.num_events > 0
    else:
        assert meta.num_events == len(events)

    row = catalog_row_for_ref(ref)
    assert row is not None
    assert row["title"] == want["title"]
    assert row["status"] == want["status"]
    assert int(row["numEvents"]) == int(meta.num_events)
    qrow = CatalogQueryRow.from_wire(row)
    locator = ref.locator
    if locator.is_dir():
        has_goal = (locator / "goal" / "state.json").is_file()
        has_plan = (locator / "plan.json").is_file() or (locator / "plan_mode.json").is_file()
        assert row_matches_query(qrow, "has:goal") is has_goal
        assert row_matches_query(qrow, "has:plan") is has_plan
    else:
        assert row_matches_query(qrow, "has:goal") is False
        assert row_matches_query(qrow, "has:plan") is False
    types = [ev.event_type for ev in events]
    assert "user_message_chunk" in types
    tool = next(ev for ev in events if ev.event_type == "tool_call")
    assert tool.tool_name == want["tool"]
    bodies = " ".join(ev.content or "" for ev in events)
    assert want["needle"] in bodies or want["needle"] in (tool.raw_input.as_str("command") or "")

    overview = session_overview(ref)
    assert overview["sessionId"] == want["sid"]
    assert overview["meta"]["harness"] == hid
    assert overview["meta"]["title"] == want["title"]
    assert overview["turns"]["total"] >= 1
    runs = overview["turns"]["subagentRuns"]
    assert isinstance(runs, list)
    if hid == "claude":
        assert len(runs) == 1
        assert runs[0]["childSessionId"] == "explore-fixture"
        assert runs[0]["openable"] is True
    elif hid == "codex":
        assert len(runs) == 1
        assert runs[0]["subagentId"] == "child-1"
        assert runs[0]["status"] == "completed"
    elif hid == "copilot":
        assert len(runs) == 1
    elif hid == "opencode":
        assert len(runs) >= 1
    stat_tools = [row["id"] for row in overview["stats"]["tools"]]
    assert want["tool"] in stat_tools
    assert overview["backgroundJobs"] == []
    assert overview["schedules"] == []
    assert overview["workflows"] == []

    diff = session_diff(ref)
    paths = [str(f["path"]) for p in diff["points"] for f in p.get("files") or []]
    assert paths == []

    dest = tmp_path / f"{hid}.tar.gz"
    members = item.write_archive(ref, dest)
    assert dest.is_file()
    assert any(want["sid"] in name for name in members)
