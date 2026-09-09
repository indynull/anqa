#!/usr/bin/env python3
"""Fail when a registered disk adapter is incomplete (AGENTS harness gate)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from anqa.harness.ref import HARNESS_IDS
    from anqa.harness.registry import adapters

    errs: list[str] = []
    hud = _read("desktop/src/live.rs")
    readme = _read("README.md")
    contract = _read("anqa/control/contract.py")
    docs = (
        _read("docs/harness-adapters.md") if (ROOT / "docs/harness-adapters.md").is_file() else ""
    )
    registered: list[str] = []
    for item in adapters():
        hid = item.id
        registered.append(hid)
        if hid not in HARNESS_IDS:
            errs.append(f"{hid}: missing from anqa.harness.ref.HARNESS_IDS")
        if not (item.product or "").strip():
            errs.append(f"{hid}: empty product")
        if not (item.supported_version or "").strip():
            errs.append(f"{hid}: empty supported_version")
        for hint in item.watch_hints():
            if hint.startswith(".") or "/" in hint:
                errs.append(f"{hid}: watch_hints must be exact basenames, got {hint!r}")
        test = ROOT / "tests" / "session" / f"test_harness_{hid}.py"
        if not test.is_file():
            errs.append(f"{hid}: missing {test.relative_to(ROOT)}")
        else:
            body = test.read_text(encoding="utf-8")
            if "export_session_bundle" not in body:
                errs.append(f"{hid}: {test.name} must call export_session_bundle")
            if "list_status_label" not in body:
                errs.append(f"{hid}: {test.name} must assert list_status_label")
        if not re.search(rf"\b{re.escape(hid)}\b", hud):
            errs.append(f"{hid}: desktop/src/live.rs is_harness_ref omits this id")
        if f'"{hid}"' not in contract and f"({hid}" not in contract:
            errs.append(f"{hid}: control_contract harness token omits this id")
        if hid not in readme:
            errs.append(f"{hid}: README does not mention this adapter")
        if hid not in docs:
            errs.append(f"{hid}: docs/harness-adapters.md does not mention this adapter")
    if not registered:
        errs.append("no adapters registered")
    if errs:
        print("check_harness_adapters:", file=sys.stderr)
        for err in errs:
            print(f"  {err}", file=sys.stderr)
        return 1
    print(f"check_harness_adapters: ok ({', '.join(registered)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
