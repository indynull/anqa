"""Session export embeds a nested session-file archive."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest
from anqa.session.export_bundle import (
    SESSION_ARCHIVE_NAME,
    assert_session_archive_shape,
    build_session_archive,
    export_session_bundle,
)

SID = "019f-test-session"


def _seed_session(root: Path) -> Path:
    """Layout: runs/traces/anqa-abc/%2Fworkspace/<sid>/…"""
    run = root / "runs" / "traces" / "anqa-abc-model"
    sess = run / "%2Fworkspace" / SID
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    (sess / "summary.json").write_text('{"ok":true}\n', encoding="utf-8")
    (sess / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")
    (sess / "prompt_context.json").write_text("{}\n", encoding="utf-8")
    (sess / "system_prompt.txt").write_text("sys\n", encoding="utf-8")
    (run / "run.json").write_text('{"run_id":"r1"}\n', encoding="utf-8")
    (run / "anqa-prompt.txt").write_text("hello\n", encoding="utf-8")
    (run / "%2Fworkspace" / "prompt_history.jsonl").write_text("p\n", encoding="utf-8")
    return sess


def test_build_session_archive_packs_session_files(tmp_path: Path) -> None:
    """Nested archive is the session directory."""
    sess = _seed_session(tmp_path)
    out = tmp_path / "from-disk.tar.gz"
    build_session_archive(sess, out)
    names = set(assert_session_archive_shape(out, SID))
    assert f"{SID}/events.jsonl" in names
    assert f"{SID}/summary.json" in names
    assert f"{SID}/chat_history.jsonl" in names


def test_build_session_archive_skips_workspace_and_terminal(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path)
    (sess / "workspace" / "src").mkdir(parents=True)
    (sess / "workspace" / "src" / "a.py").write_text("x\n", encoding="utf-8")
    (sess / "terminal" / "1").mkdir(parents=True)
    (sess / "terminal" / "1" / "out").write_text("y\n", encoding="utf-8")
    out = tmp_path / "skip-planes.tar.gz"
    build_session_archive(sess, out)
    names = set(assert_session_archive_shape(out, SID))
    assert not any("workspace" in n for n in names)
    assert not any("terminal" in n for n in names)
    assert f"{SID}/events.jsonl" in names


def test_export_parent_packs_openable_child_trace(tmp_path: Path) -> None:
    parent = _seed_session(tmp_path)
    token = parent.parent
    child = token / "child-exp"
    child.mkdir()
    (child / "summary.json").write_text(
        json.dumps({"info": {"id": "child-exp"}, "session_kind": "subagent"}),
        encoding="utf-8",
    )
    (child / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    (parent / "subagents" / "child-exp").mkdir(parents=True)
    (parent / "subagents" / "child-exp" / "meta.json").write_text(
        json.dumps({"child_session_id": "child-exp", "subagent_type": "coder"}),
        encoding="utf-8",
    )
    (parent / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "subagent_spawned",
                        "childSessionId": "child-exp",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dest = tmp_path / "with-child.tar.gz"
    result = export_session_bundle(parent, dest=dest)
    child_member = f"children/child-exp/{SESSION_ARCHIVE_NAME}"
    assert child_member in result.arcnames
    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
        assert child_member in names
        manifest = json.loads(tf.extractfile("manifest.json").read().decode())  # type: ignore[union-attr]
    assert manifest["schema"] == 10
    assert manifest["children"][0]["sessionId"] == "child-exp"
    assert manifest["children"][0]["member"] == child_member


def test_export_session_bundle_embeds_nested_session_archive(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path)

    from anqa.notes import NoteEntry, NotesDoc, save_notes

    notes_doc = NotesDoc(schema_id="default", session_id=SID)
    notes_doc.upsert(
        NoteEntry.new(
            turn_index=0,
            fields={"summary": "export me", "detail": "turn note"},
            event_indices=[2],
            note_id="n-export",
        )
    )
    save_notes(sess, notes_doc)

    from anqa.harness.ref import SessionRef
    from anqa.tags import save_tags

    save_tags(
        SessionRef(harness="grok", session_id=SID, locator=sess),
        ["review"],
    )

    dest = tmp_path / "out" / "bundle.tar.gz"
    result = export_session_bundle(
        sess,
        dest=dest,
    )
    assert result.path == dest.resolve()
    assert result.session_id == SID
    assert dest.is_file()

    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
        manifest = json.loads(tf.extractfile("manifest.json").read().decode())  # type: ignore[union-attr]
        nested_f = tf.extractfile(SESSION_ARCHIVE_NAME)
        assert nested_f is not None
        nested_bytes = nested_f.read()
        notes_f = tf.extractfile("notes/operator_notes.toml")
        assert notes_f is not None
        notes_text = notes_f.read().decode()

    assert "manifest.json" in names
    assert "README.txt" in names
    assert SESSION_ARCHIVE_NAME in names
    assert [n for n in names if n.endswith(".tar.gz")] == [SESSION_ARCHIVE_NAME]
    assert not any(n == SID or n.startswith(f"{SID}/") for n in names)
    assert not any(n == "feedback" or n.startswith("feedback/") for n in names)

    nested_path = tmp_path / "extracted-nested.tar.gz"
    nested_path.write_bytes(nested_bytes)
    nested_names = set(assert_session_archive_shape(nested_path, SID))
    assert f"{SID}/events.jsonl" in nested_names
    assert f"{SID}/summary.json" in nested_names
    assert f"{SID}/chat_history.jsonl" in nested_names

    assert not any(n == "run" or n.startswith("run/") for n in names)
    assert "human/summary.md" in names
    assert "notes/operator_notes.toml" in names
    assert "notes/tags.toml" in names
    assert "notes/schema.toml" not in names
    assert "export me" in notes_text
    assert "n-export" in notes_text
    assert manifest["session_id"] == SID
    assert manifest["session"] == SESSION_ARCHIVE_NAME
    assert manifest["schema"] == 10
    assert manifest["children"] == []
    assert manifest["profile"] == "archive-full"
    assert manifest["packaging"] == "tar.gz"
    assert "session" in manifest["include"]
    assert "session_dir" not in manifest
    assert "run_volume" not in manifest
    assert SESSION_ARCHIVE_NAME in manifest["members"]
    assert "notes/operator_notes.toml" in manifest["members"]
    assert set(manifest["members"]) == names
    assert set(result.arcnames) == names
    assert result.profile_id == "archive-full"
    assert result.packaging == "tar.gz"


def test_export_missing_session_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        export_session_bundle(tmp_path / "nope", dest=tmp_path / "x.tar.gz")


def test_export_trace_only_profile_skips_extras(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path)
    dest = tmp_path / "trace-only.tar.gz"
    result = export_session_bundle(
        sess,
        dest=dest,
        profile="trace-only",
    )
    with tarfile.open(result.path, "r:gz") as tf:
        names = set(tf.getnames())
    assert SESSION_ARCHIVE_NAME in names
    assert "manifest.json" in names
    assert "README.txt" in names
    assert not any(n.startswith("run/") for n in names)
    assert result.profile_id == "trace-only"


def test_export_dir_packaging(tmp_path: Path) -> None:
    from anqa.session.export_spec import ExportSpec, IncludeUnit, Packaging

    sess = _seed_session(tmp_path)
    dest = tmp_path / "out-dir"
    spec = ExportSpec(
        profile_id="dir-full",
        packaging=Packaging.DIR,
        include=frozenset(
            {
                IncludeUnit.SESSION,
                IncludeUnit.MANIFEST,
                IncludeUnit.README,
            }
        ),
    )
    result = export_session_bundle(sess, dest=dest, spec=spec)
    assert result.path.is_dir()
    assert (result.path / SESSION_ARCHIVE_NAME).is_file()
    assert (result.path / "manifest.json").is_file()
    assert result.packaging == "dir"


def test_export_archive_org_writes_org_reports(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path)
    dest = tmp_path / "org-bundle.tar.gz"
    result = export_session_bundle(
        sess,
        dest=dest,
        profile="archive-org",
    )
    assert result.profile_id == "archive-org"
    with tarfile.open(result.path, "r:gz") as tf:
        names = set(tf.getnames())
        sum_f = tf.extractfile("human/summary.org")
        assert sum_f is not None
        sum_text = sum_f.read().decode()
    assert "human/summary.org" in names
    assert "human/summary.md" not in names
    assert "#+TITLE:" in sum_text
    assert SID in sum_text
