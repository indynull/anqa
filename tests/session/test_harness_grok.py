"""Grok disk adapter (harness contract over parser)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from anqa.harness.grok import (
    GROK_HARNESS_ID,
    GrokAdapter,
    discover,
    load_meta,
    looks_like,
    parse_timeline,
    watch_hints,
)
from anqa.harness.registry import require_adapter
from anqa.models import SessionMeta, TraceEvent
from anqa.session.export_bundle import export_session_bundle

from .turn_status import assert_adapter_turn

_MINIMAL = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "minimal_session"


def _write_summary_session(root: Path, name: str = "sess-1") -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(json.dumps({"generated_title": "t"}), encoding="utf-8")
    return sd


def test_harness_id_is_grok() -> None:
    assert GROK_HARNESS_ID == "grok"


def test_discover_finds_summary_json_session(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path)
    found = discover([tmp_path])
    assert sd.resolve() in {p.locator.resolve() for p in found}


def test_discover_host_root_uses_shallow_collector(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = host / "%2Fproj" / "host-sid"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "anqa.harness.grok.default_sessions_root",
        lambda: host,
    )
    found = discover([host])
    assert sess.resolve() in {p.locator.resolve() for p in found}


def test_looks_like_true_and_false(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert looks_like(sd) is True
    assert looks_like(str(sd)) is True
    assert looks_like(empty) is False
    assert looks_like(tmp_path / "missing") is False


def test_load_meta_returns_session_meta(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path, "meta-sid")
    meta = load_meta(sd)
    assert isinstance(meta, SessionMeta)
    assert meta.session_id == "meta-sid"
    assert meta.harness == "grok"


def test_parse_timeline_minimal_session() -> None:
    assert _MINIMAL.is_dir()
    events = parse_timeline(_MINIMAL)
    assert events
    assert all(isinstance(ev, TraceEvent) for ev in events)


def test_write_archive_packs_session_files(tmp_path: Path) -> None:
    """Grok adapter writes the session directory (workspace/terminal omitted)."""
    import tarfile

    sd = _write_summary_session(tmp_path, "pack-sid")
    (sd / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    (sd / "workspace" / "src").mkdir(parents=True)
    (sd / "workspace" / "src" / "a.py").write_text("x\n", encoding="utf-8")
    (sd / "terminal" / "1").mkdir(parents=True)
    (sd / "terminal" / "1" / "out").write_text("y\n", encoding="utf-8")
    dest = tmp_path / "sess.tar.gz"
    members = GrokAdapter().write_archive(sd, dest)
    assert "pack-sid/summary.json" in members
    assert "pack-sid/events.jsonl" in members
    assert not any("workspace" in n for n in members)
    assert not any("terminal" in n for n in members)
    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
    assert names == set(members)
    opened = GrokAdapter().open_archive(dest, tmp_path / "opened")
    assert opened.session_id == "pack-sid"
    assert (opened.locator / "summary.json").is_file()
    assert not (opened.locator / "workspace").exists()


def test_export_bundle_from_session_dir(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path, "pack-sid")
    (sd / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(sd, dest=dest)
    assert dest.is_file()
    assert result.session_id == "pack-sid"


def test_list_status_complete_and_running(tmp_path: Path) -> None:
    done = _write_summary_session(tmp_path, "done-sess")
    (done / "updates.jsonl").write_text(
        json.dumps({"params": {"update": {"sessionUpdate": "turn_completed"}}}) + "\n",
        encoding="utf-8",
    )
    live = _write_summary_session(tmp_path, "live-sess")
    (live / "updates.jsonl").write_text(
        json.dumps({"params": {"update": {"sessionUpdate": "user_message_chunk"}}}) + "\n",
        encoding="utf-8",
    )
    (live / "events.jsonl").write_text('{"type":"turn_started"}\n', encoding="utf-8")
    assert_adapter_turn(done, "complete")
    assert_adapter_turn(live, "idle")


def _session_update(kind: str) -> str:
    return json.dumps({"params": {"update": {"sessionUpdate": kind}}}) + "\n"


def test_list_status_tool_after_complete_is_running(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path, "live-next")
    (sd / "events.jsonl").write_text(
        '{"type":"turn_ended","outcome":"completed"}\n',
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(
        _session_update("turn_completed")
        + _session_update("user_message_chunk")
        + _session_update("agent_message_chunk")
        + _session_update("tool_call"),
        encoding="utf-8",
    )
    assert_adapter_turn(sd, "running")
    detail = require_adapter(sd).load_detail(sd)
    listed = require_adapter(sd).load_meta(sd)
    assert detail.list_status_label() == listed.list_status_label() == "running"


def test_list_status_events_turn_started_after_close_is_idle(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path, "events-open")
    (sd / "events.jsonl").write_text(
        '{"type":"turn_ended","outcome":"completed"}\n{"type":"turn_started"}\n',
        encoding="utf-8",
    )
    assert_adapter_turn(sd, "idle")


def test_list_status_new_user_after_complete_is_not_complete(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path, "next-user")
    (sd / "events.jsonl").write_text(
        '{"type":"turn_ended","outcome":"completed"}\n',
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(
        _session_update("turn_completed") + _session_update("user_message_chunk"),
        encoding="utf-8",
    )
    assert_adapter_turn(sd, "idle")


def test_watch_hints_include_updates_jsonl() -> None:
    assert "updates.jsonl" in watch_hints()


def _write_abort_session(root: Path, name: str, trigger: str) -> Path:
    sd = _write_summary_session(root, name)
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_started", "turn_number": 0, "ts": 1})
        + "\n"
        + json.dumps(
            {
                "type": "turn_ended",
                "outcome": "cancelled",
                "cancellation_category": "mid_turn_abort",
                "cancellation_context": {"trigger": trigger},
                "ts": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(
        _session_update("user_message_chunk") + _session_update("agent_message_chunk"),
        encoding="utf-8",
    )
    return sd


def test_send_now_mid_turn_abort_is_interjected_not_cancelled(tmp_path: Path) -> None:
    """User send-now is an interjection. Esc stays cancelled."""
    from anqa.session.turns import segment_timeline_turns

    send = _write_abort_session(tmp_path, "send-now", "send_now")
    esc = _write_abort_session(tmp_path, "esc", "esc")
    send_segs = segment_timeline_turns(parse_timeline(send))
    esc_segs = segment_timeline_turns(parse_timeline(esc))
    assert send_segs[0].outcome == "interjected"
    assert send_segs[0].label == "turn 0 (interjected)"
    assert esc_segs[0].outcome == "cancelled"
    assert esc_segs[0].label == "turn 0 (cancelled)"
    send_end = next(e for e in parse_timeline(send) if e.event_type == "turn_ended")
    esc_end = next(e for e in parse_timeline(esc) if e.event_type == "turn_ended")
    assert send_end.is_error is False
    assert esc_end.is_error is True


def test_bind_locator_and_ref_for_id(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = _write_summary_session(host / "%2Fproj", "host-sid")
    monkeypatch.setattr("anqa.harness.grok.default_sessions_root", lambda: host)
    adapter = GrokAdapter()
    bound = adapter.bind_locator(sess)
    assert bound is not None
    assert bound.session_id == "host-sid"
    assert bound.locator.resolve() == sess.resolve()
    assert adapter.bind_locator(tmp_path) is None
    found = adapter.ref_for_id("host-sid")
    assert found is not None
    assert found.locator.resolve() == sess.resolve()
    assert adapter.ref_for_id("missing") is None


def test_delete_session_accepts_catalog_ref(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = _write_summary_session(host / "%2Fproj", "host-sid")
    monkeypatch.setattr("anqa.harness.grok.default_sessions_root", lambda: host)
    GrokAdapter().delete_session(Path("grok:host-sid"))
    assert not sess.exists()


def test_list_meta_without_event_count_does_not_parse_timeline(tmp_path: Path, monkeypatch) -> None:
    """Catalog list-meta must not walk updates.jsonl for a count."""
    import anqa.harness.grok_parse as parse_mod

    sd = _write_summary_session(tmp_path)
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("list-meta must not use the Python ingest")

    monkeypatch.setattr(parse_mod, "parse_timeline", boom, raising=False)
    monkeypatch.setattr(parse_mod, "load_session_meta_list", boom, raising=False)
    meta = GrokAdapter().load_meta(sd)
    assert meta.title == "t"
    assert meta.harness == "grok"
    assert meta.num_events == 0


def test_parse_timeline_does_not_use_python_ingest(monkeypatch) -> None:
    import anqa.harness.grok_parse as parse_mod

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("adapter timeline must use anqa.core")

    monkeypatch.setattr(parse_mod, "parse_timeline", boom, raising=False)
    events = GrokAdapter().parse_timeline(_MINIMAL)
    assert events


def test_ref_for_id_does_not_discover_every_session(tmp_path: Path, monkeypatch) -> None:
    """Opening one Grok id must not list the whole host tree."""
    host = tmp_path / "sessions"
    sess = _write_summary_session(host / "%2Fproj", "host-sid")
    _write_summary_session(host / "%2Fother", "other-sid")
    monkeypatch.setattr("anqa.harness.grok.default_sessions_root", lambda: host)
    calls: list[int] = []
    real = GrokAdapter.discover

    def wrapped(self: GrokAdapter, roots: object = None) -> object:
        calls.append(1)
        return real(self, roots)  # type: ignore[misc]

    monkeypatch.setattr(GrokAdapter, "discover", wrapped)
    found = GrokAdapter().ref_for_id("host-sid")
    assert found is not None
    assert found.locator.resolve() == sess.resolve()
    assert calls == []


def test_importing_grok_adapter_does_not_import_ui() -> None:
    saved = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == "anqa.harness" or name.startswith("anqa.harness.")
    }
    for name in list(saved):
        del sys.modules[name]
    ui_before = {n for n in sys.modules if n == "anqa.ui" or n.startswith("anqa.ui.")}
    try:
        import anqa.harness.grok as grok_mod

        ui_after = {n for n in sys.modules if n == "anqa.ui" or n.startswith("anqa.ui.")}
        assert ui_after == ui_before
        assert grok_mod.GROK_HARNESS_ID == "grok"
    finally:
        for name in list(sys.modules):
            if name == "anqa.harness" or name.startswith("anqa.harness."):
                del sys.modules[name]
        sys.modules.update(saved)
