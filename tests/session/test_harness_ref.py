"""SessionRef parse / format."""

from __future__ import annotations

from pathlib import Path

from anqa.harness.ref import SessionRef, catalog_session_key, parse_session_ref_string
from anqa.harness.registry import resolve_session_ref


def test_parse_harness_id() -> None:
    assert parse_session_ref_string("grok:019f-uuid") == ("grok", "019f-uuid")


def test_parse_rejects_paths() -> None:
    assert parse_session_ref_string("/tmp/grok:weird") is None
    assert parse_session_ref_string("~/store.db") is None
    assert parse_session_ref_string("unknown:ses") is None
    assert parse_session_ref_string("") is None


def test_catalog_session_key_unwraps_cwd_prefixed_harness_ref() -> None:
    assert catalog_session_key("opencode:ses_probe") == "opencode:ses_probe"
    prefixed = Path.cwd() / "opencode:ses_probe"
    assert catalog_session_key(prefixed) == "opencode:ses_probe"
    assert parse_session_ref_string(str(prefixed)) is None


def test_ref_string_and_notes_are_the_same_for_every_store(tmp_path: Path) -> None:
    loc = tmp_path / "sid"
    loc.mkdir()
    store = tmp_path / "store.db"
    store.write_bytes(b"")
    directory = SessionRef(harness="grok", session_id="sid", locator=loc)
    row = SessionRef(harness="demo", session_id="ses_1", locator=store)
    assert directory.ref_string() == "grok:sid"
    assert row.ref_string() == "demo:ses_1"
    assert directory.overlay_dir().parts[-2:] == ("grok", "sid")
    assert row.overlay_dir().parts[-2:] == ("demo", "ses_1")


def test_resolve_unknown_harness_id_is_none() -> None:
    assert resolve_session_ref("unknown:ses_does_not_exist") is None
    assert resolve_session_ref("") is None


def test_require_adapter_accepts_harness_ref_as_path() -> None:
    """Browser/control pass catalog path through ``Path``; that must still bind."""
    from anqa.harness.registry import require_adapter

    raw = "opencode:ses_fb126f42fffebvoGOuSoFc457J"
    assert require_adapter(raw).id == "opencode"
    assert require_adapter(Path(raw)).id == "opencode"


def test_resolve_session_ref_uses_path_resolve_before_discover(tmp_path: Path) -> None:
    """``grok:id`` must hit the catalog locator, not walk every store."""
    loc = tmp_path / "sess"
    loc.mkdir()
    (loc / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    (loc / "summary.json").write_text("{}", encoding="utf-8")
    calls: list[str] = []

    def path_resolve(reference: str) -> Path | None:
        calls.append(reference)
        if reference in {"grok:sess", "sess"}:
            return loc
        return None

    found = resolve_session_ref("grok:sess", path_resolve=path_resolve)
    assert found is not None
    assert found.harness == "grok"
    assert found.session_id == "sess"
    assert found.locator.resolve() == loc.resolve()
    assert calls == ["grok:sess"]
