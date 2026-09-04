#!/usr/bin/env python3
"""Hard gate: ``examples/`` must stay loadable and schema-valid.

Run via ``just examples-check`` or CI. Exit 0 only when every pack is sound.
Nothing under ``examples/`` is auto-loaded by the product; this script is the
contract that copy/paste references do not rot.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class _Fail(Exception):
    """Single validation failure with a path and message."""


def _ok(msg: str) -> None:
    print(f"OK  {msg}")


def _err(path: Path | str, msg: str) -> None:
    raise _Fail(f"{path}: {msg}")


def _repo_rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def check_readmes() -> None:
    required = [
        EXAMPLES / "README.md",
        EXAMPLES / "notes" / "README.md",
        EXAMPLES / "keys" / "README.md",
        EXAMPLES / "config" / "README.md",
        EXAMPLES / "themes" / "README.md",
        EXAMPLES / "sway" / "README.md",
    ]
    for path in required:
        if not path.is_file() or path.stat().st_size < 40:
            _err(path, "missing or empty README")
        text = path.read_text(encoding="utf-8")
        for stale in (
            "all-plugins.json",
            "security-only.json",
            "code-quality.json",
            "teachx-v2-mf.json",
        ):
            if stale in text:
                _err(path, f"prefs samples are .toml; found {stale}")
        _ok(f"{_repo_rel(path)}")


def check_keys_overlay() -> None:
    """Validate examples/keys overlays load cleanly."""
    from anqa.keys import load_keymap

    keys_dir = EXAMPLES / "keys"
    files = sorted(keys_dir.glob("*.toml"))
    if not files:
        _err(keys_dir, "no keys.toml overlays")
    for path in files:
        keymap = load_keymap(path)
        if not keymap.ok:
            msgs = "; ".join(err.message for err in keymap.errors) or "refused"
            _err(path, msgs)
        if not keymap.loaded_overlay:
            _err(path, "overlay did not apply")
        _ok(f"{_repo_rel(path)}  leader={keymap.leader or '-'} bindings={len(keymap.bindings)}")


def check_app_config() -> None:
    """Validate examples/config/config.toml against AppConfig."""
    from anqa.config import SCHEMA_ID, validate_config_file

    path = EXAMPLES / "config" / "config.toml"
    if not path.is_file():
        _err(path, "missing prefs example")
    text = path.read_text(encoding="utf-8")
    if SCHEMA_ID not in text:
        _err(path, f"missing schema comment {SCHEMA_ID}")
    try:
        cfg = validate_config_file(path)
    except ValueError as exc:
        _err(path, str(exc))
    if cfg.theme != "auto":
        _err(path, f"expected default theme auto, got {cfg.theme!r}")
    _ok(f"{_repo_rel(path)}  theme={cfg.theme}")


def check_user_theme() -> None:
    """Validate examples/themes/*.toml load as catalog themes."""
    from anqa.ui.theme import load_user_themes

    folder = EXAMPLES / "themes"
    themes = load_user_themes(folder)
    if not themes:
        _err(folder, "no loadable theme TOML")
    names = {t.name for t in themes}
    if "paper" not in names:
        _err(folder, f"expected paper theme, got {sorted(names)}")
    _ok(f"{_repo_rel(folder)}  ({', '.join(sorted(names))})")


def check_notes_schema() -> None:
    """Validate examples/notes schema example loads with non-empty fields."""
    from anqa.notes import load_schema

    path = EXAMPLES / "notes" / "notes_schema.example.toml"
    if not path.is_file():
        _err(path, "missing notes schema example")
    schema = load_schema(path=path)
    if not schema.fields:
        _err(path, "schema has no fields")
    for spec in schema.fields:
        if not (spec.id or "").strip() or not (spec.label or "").strip():
            _err(path, f"empty field id/label in {spec!r}")
    _ok(f"{_repo_rel(path)}  schema_id={schema.schema_id} fields={len(schema.fields)}")


def check_sway_hud() -> None:
    """Validate examples/sway overlay fragment."""
    path = EXAMPLES / "sway" / "sway-hud.conf"
    if not path.is_file():
        _err(path, "missing Sway HUD include")
    text = path.read_text(encoding="utf-8")
    if "dev.indynull.anqa-hud.overlay" not in text:
        _err(path, "missing overlay app_id")
    if "anqa desktop --toggle" not in text:
        _err(path, "missing compositor toggle bind")
    _ok(f"{_repo_rel(path)}")


def main() -> int:
    if not EXAMPLES.is_dir():
        print(f"error: missing {EXAMPLES}", file=sys.stderr)
        return 1
    try:
        check_readmes()
        check_notes_schema()
        check_keys_overlay()
        check_app_config()
        check_user_theme()
        check_sway_hud()
    except _Fail as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("check_examples: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
