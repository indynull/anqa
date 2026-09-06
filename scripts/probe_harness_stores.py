#!/usr/bin/env python3
"""Compare installed product versions to adapter pins; sample store record types.

Prints keys and type names only. Never prints session text, titles, or
paths inside a conversation. Not part of ``just lint`` (live stores vary).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_CLI: dict[str, tuple[str, ...]] = {
    "grok": ("grok", "--version"),
    "opencode": ("opencode", "--version"),
    "pi": ("pi", "--version"),
    "claude": ("claude", "--version"),
    "gemini": ("gemini", "--version"),
    "copilot": ("copilot", "--version"),
    "codex": ("codex", "--version"),
    "cursor": ("cursor-agent", "--version"),
}


def _cli_version(argv: tuple[str, ...]) -> str:
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "not on PATH"
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    return text[0].strip() if text else f"exit {proc.returncode}"


def _jsonl_types(paths: Iterable[Path], limit_files: int = 12) -> str:
    types: Counter[str] = Counter()
    n = 0
    for path in paths:
        if n >= limit_files:
            break
        if not path.is_file():
            continue
        n += 1
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[:80]:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            types[str(row.get("type") or row.get("role") or "")] += 1
            if str(row.get("type") or "") == "message":
                msg = row.get("message")
                if isinstance(msg, dict):
                    role = str(msg.get("role") or "")
                    if role:
                        types[f"role:{role}"] += 1
                    content = msg.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type"):
                                types[f"block:{block.get('type')}"] += 1
    if not n:
        return "no jsonl"
    return f"{n} files types={types.most_common(16)}"


def _probe_opencode(path: Path) -> str:
    if not path.is_file():
        return "missing"
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return f"sqlite error: {exc}"
    try:
        tables = [
            str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        bits: list[str] = []
        if "session" in tables:
            bits.append(f"session={con.execute('SELECT COUNT(*) FROM session').fetchone()[0]}")
        if "event" in tables:
            ev = con.execute("SELECT type, COUNT(*) FROM event GROUP BY type").fetchall()
            bits.append("event=" + ",".join(f"{t}:{n}" for t, n in ev))
        return "tables=" + ",".join(tables) + (" " + " ".join(bits) if bits else "")
    except sqlite3.Error as exc:
        return f"sqlite error: {exc}"
    finally:
        con.close()


def _probe_store(hid: str, roots: list[Path]) -> str:
    if not roots:
        return "no default root"
    if hid == "opencode":
        return _probe_opencode(roots[0])
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".jsonl":
            files.append(root)
        elif root.is_dir():
            try:
                files.extend(sorted(root.rglob("*.jsonl"))[:20])
            except OSError:
                continue
    if files:
        return _jsonl_types(files)
    return "root present, no jsonl sample"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from anqa.harness.registry import adapters

    print("harness-probe (keys and versions only; no session text)")
    for item in adapters():
        installed = _CLI.get(item.id)
        have = _cli_version(installed) if installed else "no mapped command"
        roots = item.default_host_roots()
        sample = _probe_store(item.id, roots)
        print(f"{item.id:12} pin={item.supported_version:20} cli={have}  {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
