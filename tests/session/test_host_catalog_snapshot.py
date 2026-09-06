"""Host catalog snapshot: stamp gate, updates-tail status, marker fallback."""

from __future__ import annotations

import json
from pathlib import Path

from anqa.control.contract import PROTOCOL_VERSION
from anqa.harness.grok import load_meta
from anqa.session.catalog import list_session_catalog, session_catalog_row
from anqa.session.mtime_export import write_host_catalog_export


def _host_session(
    root: Path,
    name: str,
    *,
    title: str,
    messages: int = 3,
    updates: str = "{}\n",
) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": title,
                "num_messages": messages,
            }
        ),
        encoding="utf-8",
    )
    (sd / "signals.json").write_text(
        json.dumps({"toolCallCount": 2, "turnCount": 4, "sessionDurationSeconds": 12.0}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(updates, encoding="utf-8")
    (sd / "events.jsonl").write_text('{"type":"turn_started"}\n', encoding="utf-8")
    return sd


def _turn_completed_line() -> str:
    return json.dumps({"params": {"update": {"sessionUpdate": "turn_completed"}}}) + "\n"


def _chunk_line() -> str:
    return json.dumps({"params": {"update": {"sessionUpdate": "user_message_chunk"}}}) + "\n"


def test_host_catalog_row_skips_full_timeline_parse(tmp_path: Path, monkeypatch) -> None:
    import anqa.harness.grok_parse as parser_mod

    sd = _host_session(
        tmp_path / "host",
        "019aaaa",
        title="Host title",
        messages=9,
        updates=_turn_completed_line(),
    )

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("host list must not parse the full timeline")

    monkeypatch.setattr(parser_mod, "parse_timeline", _boom, raising=False)
    row = session_catalog_row(sd)
    assert row is not None
    assert row["title"] == "Host title"
    assert row["numEvents"] == 9
    assert row["toolCallCount"] == 2
    assert row["turnCount"] == 4
    assert row["status"] == "complete"


def test_host_list_meta_tail_sets_complete_vs_running(tmp_path: Path) -> None:
    host = tmp_path / "host"
    done = _host_session(host, "done-sess", title="Done", updates=_turn_completed_line())
    live = _host_session(host, "live-sess", title="Live", updates=_chunk_line())
    complete = load_meta(done)
    running = load_meta(live)
    assert complete.list_status_label() == "complete"
    assert running.list_status_label() == "idle"
    done_row = session_catalog_row(done)
    live_row = session_catalog_row(live)
    assert done_row is not None and done_row["status"] == "complete"
    assert live_row is not None and live_row["status"] == "idle"


def test_host_export_is_stamp_gated(tmp_path: Path) -> None:
    host = tmp_path / "host"
    _host_session(host, "019cccc-1111-2222-3333-444444444444", title="Host title")
    dest = tmp_path / "out" / "host.json"
    first = write_host_catalog_export(dest, host_root=host)
    assert first == dest
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["sessionId"] == "019cccc-1111-2222-3333-444444444444"
    assert payload["sessions"][0]["title"] == "Host title"
    assert payload["sessions"][0]["numEvents"] == 3
    assert "stamps" in payload
    mtime1 = dest.stat().st_mtime
    second = write_host_catalog_export(dest, host_root=host)
    assert second == dest
    assert dest.stat().st_mtime == mtime1
    assert json.loads(dest.read_text(encoding="utf-8"))["version"] == PROTOCOL_VERSION


def test_host_export_rebuilds_stamp_fresh_row_with_empty_title(
    tmp_path: Path,
) -> None:
    host = tmp_path / "host"
    _host_session(host, "019dddd-1111-2222-3333-444444444444", title="Real title")
    dest = tmp_path / "out" / "host.json"
    write_host_catalog_export(dest, host_root=host)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    payload["sessions"][0]["title"] = ""
    payload["sessions"][0]["label"] = "019dddd-1111-2222-3"
    payload["sessions"][0]["numEvents"] = 0
    dest.write_text(json.dumps(payload), encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    rebuilt = json.loads(dest.read_text(encoding="utf-8"))
    assert rebuilt["sessions"][0]["title"] == "Real title"
    assert rebuilt["sessions"][0]["numEvents"] == 3


def test_host_export_rebuilds_when_snapshot_version_changes(tmp_path: Path) -> None:
    """A snapshot written under an older protocol version must not be reused."""
    host = tmp_path / "host"
    _host_session(host, "019ffff-1111-2222-3333-444444444444", title="Old snap")
    dest = tmp_path / "out" / "host.json"
    write_host_catalog_export(dest, host_root=host)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    payload["version"] = 1
    payload["sessions"][0]["status"] = "running"
    dest.write_text(json.dumps(payload), encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    rebuilt = json.loads(dest.read_text(encoding="utf-8"))
    assert rebuilt["version"] == PROTOCOL_VERSION
    assert rebuilt["sessions"][0]["status"] != "running"


def test_host_export_refreshes_cached_running_without_format_bump(
    tmp_path: Path,
) -> None:
    """A stamp-fresh snapshot that still says running is remapped on load."""
    host = tmp_path / "host"
    _host_session(host, "019eeee-1111-2222-3333-444444444444", title="Open", updates=_chunk_line())
    dest = tmp_path / "out" / "host.json"
    write_host_catalog_export(dest, host_root=host)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    payload["sessions"][0]["status"] = "running"
    dest.write_text(json.dumps(payload), encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    rebuilt = json.loads(dest.read_text(encoding="utf-8"))
    assert rebuilt["sessions"][0]["status"] == "idle"


def test_host_export_rebuilds_when_row_format_changes(tmp_path: Path) -> None:
    """Cached rows without harnessLabel must not be reused."""
    from anqa.session.mtime_export import SNAPSHOT_ROW_FORMAT

    host = tmp_path / "host"
    _host_session(host, "019aaaa-1111-2222-3333-444444444444", title="Need label")
    dest = tmp_path / "out" / "host.json"
    write_host_catalog_export(dest, host_root=host)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    payload["rowFormat"] = 1
    payload["sessions"][0]["origin"] = "host"
    payload["sessions"][0]["harnessLabel"] = ""
    dest.write_text(json.dumps(payload), encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    rebuilt = json.loads(dest.read_text(encoding="utf-8"))
    assert rebuilt["rowFormat"] == SNAPSHOT_ROW_FORMAT
    assert rebuilt["sessions"][0].get("harnessLabel")


def test_host_export_rebuilds_when_stamps_unreadable(tmp_path: Path) -> None:
    host = tmp_path / "host"
    _host_session(host, "019eeee-1111-2222-3333-444444444444", title="Rebuild")
    dest = tmp_path / "out" / "host.json"
    write_host_catalog_export(dest, host_root=host)
    dest.write_text("{not-json\n", encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["title"] == "Rebuild"

    dest.write_text(json.dumps({"stamps": "nope", "sessions": []}), encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    assert json.loads(dest.read_text(encoding="utf-8"))["sessions"][0]["title"] == "Rebuild"

    dest.write_text(json.dumps({"stamps": [["only-path"]], "sessions": []}), encoding="utf-8")
    write_host_catalog_export(dest, host_root=host)
    assert json.loads(dest.read_text(encoding="utf-8"))["sessions"][0]["title"] == "Rebuild"

    dest.write_text(
        json.dumps({"stamps": [["/x", True, 1, 2]], "sessions": []}),
        encoding="utf-8",
    )
    write_host_catalog_export(dest, host_root=host)
    assert json.loads(dest.read_text(encoding="utf-8"))["sessions"][0]["title"] == "Rebuild"


def test_list_session_catalog_stamp_hit_skips_session_files(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    host = tmp_path / "host"
    _host_session(host, "019dddd-1111-2222-3333-444444444444", title="Snap")
    dest = tmp_path / "snap.json"
    rows1 = list_session_catalog(include_host=True, host_root=host, host_catalog_cache=dest)
    assert rows1[0]["sessionId"] == "019dddd-1111-2222-3333-444444444444"
    mtime1 = dest.stat().st_mtime

    opened: list[str] = []
    real_open = Path.open

    def track_open(self: Path, *args: object, **kwargs: object) -> object:
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    rows2 = list_session_catalog(include_host=True, host_root=host, host_catalog_cache=dest)
    assert rows2[0]["title"] == "Snap"
    assert dest.stat().st_mtime == mtime1
    assert not any(name.endswith("summary.json") for name in opened)
    assert not any(name.endswith("signals.json") for name in opened)
    assert not any(name.endswith("updates.jsonl") for name in opened)
    assert not any(name.endswith("events.jsonl") for name in opened)


def test_host_export_rebuilds_when_overlay_notes_appear(tmp_path: Path, monkeypatch) -> None:
    import anqa.harness.ref as ref_mod
    import anqa.paths as paths_mod
    from anqa.notes import NOTES_FILENAME, NoteEntry, NotesDoc, dump_notes_toml

    home = paths_mod.APP_HOME
    monkeypatch.setattr(ref_mod, "APP_HOME", home)
    host = tmp_path / "host"
    session = _host_session(host, "019note-1111-2222-3333-444444444444", title="Noted")
    dest = tmp_path / "out" / "host.json"
    write_host_catalog_export(dest, host_root=host)
    first = json.loads(dest.read_text(encoding="utf-8"))
    assert first["sessions"][0]["hasNotes"] is False

    overlay = home / "notes" / "grok" / session.name
    overlay.mkdir(parents=True)
    doc = NotesDoc(session_id=session.name)
    doc.upsert(NoteEntry.new(turn_index=1, fields={"summary": "from overlay"}, note_id="n-ov"))
    (overlay / NOTES_FILENAME).write_text(dump_notes_toml(doc), encoding="utf-8")

    write_host_catalog_export(dest, host_root=host)
    rebuilt = json.loads(dest.read_text(encoding="utf-8"))
    assert rebuilt["sessions"][0]["hasNotes"] is True
    assert rebuilt["sessions"][0]["noteCount"] == 1


def test_list_session_catalog_events_growth_does_not_open_events(
    tmp_path: Path, monkeypatch
) -> None:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    host = tmp_path / "host"
    sd = _host_session(host, "grow-ev", title="Grow")
    dest = tmp_path / "snap.json"
    list_session_catalog(include_host=True, host_root=host, host_catalog_cache=dest)
    (sd / "events.jsonl").write_text('{"type":"turn_started"}\n' * 20, encoding="utf-8")

    opened: list[str] = []
    real_open = Path.open

    def track_open(self: Path, *args: object, **kwargs: object) -> object:
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    list_session_catalog(include_host=True, host_root=host, host_catalog_cache=dest)
    assert not any(name.endswith("events.jsonl") for name in opened)


def test_list_session_catalog_rebuilds_only_changed_host_row(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    host = tmp_path / "host"
    _host_session(host, "still-sess", title="Still")
    live = _host_session(host, "live-sess", title="Live", updates=_chunk_line())
    dest = tmp_path / "snap.json"
    rows1 = list_session_catalog(include_host=True, host_root=host, host_catalog_cache=dest)
    by_id = {str(r["sessionId"]): r for r in rows1}
    assert by_id["still-sess"]["status"] == "idle"
    assert by_id["live-sess"]["status"] == "idle"
    (live / "updates.jsonl").write_text(_turn_completed_line(), encoding="utf-8")

    built: list[str] = []
    real_row = session_catalog_row

    def track_row(session_dir: Path, *, label: str | None = None) -> object:
        built.append(session_dir.name)
        return real_row(session_dir, label=label)

    monkeypatch.setattr("anqa.session.catalog.session_catalog_row", track_row)
    rows2 = list_session_catalog(include_host=True, host_root=host, host_catalog_cache=dest)
    by_id = {str(r["sessionId"]): r for r in rows2}
    assert by_id["live-sess"]["status"] == "complete"
    assert by_id["still-sess"]["title"] == "Still"
    assert built == ["live-sess"]
