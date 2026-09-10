"""Delete native session locators (directory, file, or database row)."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.harness.registry import require_adapter
from anqa.session.delete import delete_session_dirs


def test_delete_sessions_removes_codex_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "rollout-2026-08-30T12-00-00-aaaaaaaa-1111-4111-8111-00000000d001.jsonl"
    path.write_text(
        '{"type":"session_meta","payload":{"id":"aaaaaaaa-1111-4111-8111-00000000d001",'
        '"session_id":"aaaaaaaa-1111-4111-8111-00000000d001"}}\n'
        '{"type":"event_msg","payload":{"type":"task_complete"}}\n',
        encoding="utf-8",
    )
    meta = require_adapter(path).load_meta(path)
    assert meta.harness == "codex"
    stats = delete_session_dirs([path])
    assert int(stats["deleted"] or 0) == 1
    assert stats.get("errors") == []
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        require_adapter(path).load_meta(path)


def test_delete_sessions_removes_claude_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "aaaaaaaa-bbbb-4ccc-8ddd-00000000d001.jsonl"
    path.write_text(
        '{"type":"user","sessionId":"aaaaaaaa-bbbb-4ccc-8ddd-00000000d001",'
        '"message":{"role":"user","content":"hi"}}\n',
        encoding="utf-8",
    )
    stats = delete_session_dirs([path])
    assert int(stats["deleted"] or 0) == 1
    assert not path.exists()


def test_delete_sessions_removes_grok_directory(tmp_path: Path) -> None:
    sd = tmp_path / "sess-del"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    stats = delete_session_dirs([sd])
    assert int(stats["deleted"] or 0) == 1
    assert not sd.exists()


def test_delete_sessions_removes_grok_catalog_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Home list delete passes ``grok:<id>``, not the encoded-cwd directory."""
    host = tmp_path / "sessions"
    sd = host / "%2Fproj" / "del-sid"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("anqa.harness.grok.default_sessions_root", lambda: host)
    stats = delete_session_dirs([Path("grok:del-sid")])
    assert stats.get("errors") == []
    assert int(stats["deleted"] or 0) == 1
    assert not sd.exists()
