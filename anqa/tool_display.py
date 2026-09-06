"""Pure display transforms for tool inputs and results (TUI + HUD).

Shared so parse, control mapping, and the TUI detail pane agree on
``read_file`` line prefixes, ``web_search`` action flatten, and image paths.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path

from .models import JsonObject, JsonValue, as_json_object, json_as_str


class ToolFamily(StrEnum):
    """Action family for tool-name color. Stored tool ids stay snake_case."""

    READ = "read"
    WRITE = "write"
    SHELL = "shell"
    AGENT = "agent"
    MCP = "mcp"
    OTHER = "other"


_STILL_SUFFIX = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_STILL_TOOLS = frozenset({"image_gen", "image_edit"})
_IMAGE_MARK = re.compile(r"\s*\[Image #\d+\]")
_MD_STILL_LINK = re.compile(
    r"\[(?:images/)?[\w.-]+\.(?:png|jpe?g|gif|webp)\]\((?:images/)?[\w.-]+\.(?:png|jpe?g|gif|webp)\)",
    re.IGNORECASE,
)
_BARE_STILL_LINE = re.compile(
    r"(?m)^\s*(?:images/)?[\w.-]+\.(?:png|jpe?g|gif|webp)\s*$",
    re.IGNORECASE,
)
_STILL_REF = re.compile(
    r"(?<![\w./])((?:images/)?[\w.-]+\.(?:png|jpe?g|gif|webp))",
    re.IGNORECASE,
)

# Action family for tool-name color (TUI + HUD). Stored ids stay snake_case.
_TOOL_FAMILY_READ = frozenset(
    {
        "read",
        "read_file",
        "grep",
        "find",
        "ls",
        "list_dir",
        "web_search",
        "read_resource",
        "list_resources",
        "search_tool",
        "search_mcp",
    }
)
_TOOL_FAMILY_WRITE = frozenset(
    {
        "search_replace",
        "edit",
        "write",
        "write_file",
        "create_file",
        "todo_write",
        "update_goal",
        "image_gen",
        "image_edit",
        "image_to_video",
        "reference_to_video",
    }
)
_TOOL_FAMILY_SHELL = frozenset(
    {
        "bash",
        "run_terminal_command",
        "get_command_or_subagent_output",
        "kill_command_or_subagent",
        "wait_commands_or_subagents",
        "monitor",
        "scheduler_create",
        "scheduler_delete",
        "scheduler_list",
    }
)
_TOOL_FAMILY_AGENT = frozenset(
    {
        "spawn_subagent",
        "subagent",
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode",
    }
)
_TOOL_FAMILY_MCP_WRAPPER = frozenset(
    {
        "use_tool",
        "call_mcp",
        "call_mcp_tool",
        "mcp_tool",
    }
)


def tool_family(name: str) -> ToolFamily:
    """Map a tool id to a :class:`ToolFamily` member."""
    n = (name or "").strip()
    if "__" in n or n.startswith("mcp_") or n in _TOOL_FAMILY_MCP_WRAPPER:
        return ToolFamily.MCP
    if n in _TOOL_FAMILY_READ:
        return ToolFamily.READ
    if n in _TOOL_FAMILY_WRITE:
        return ToolFamily.WRITE
    if n in _TOOL_FAMILY_SHELL:
        return ToolFamily.SHELL
    if n in _TOOL_FAMILY_AGENT:
        return ToolFamily.AGENT
    low = n.lower()
    if any(k in low for k in ("read", "get", "list", "search", "grep", "find", "view")):
        return ToolFamily.READ
    if any(k in low for k in ("write", "edit", "create", "update", "delete", "save")):
        return ToolFamily.WRITE
    if any(k in low for k in ("run", "exec", "shell", "terminal", "wait", "kill")):
        return ToolFamily.SHELL
    return ToolFamily.OTHER


def _human_tool_token(part: str) -> str:
    return part.replace("_", " ").replace("-", " ").strip()


def format_tool_display(name: str) -> str:
    """Operator tool label: spaces, not snake_case; marketplace ``server · method``."""
    n = (name or "").strip()
    if not n:
        return "?"
    if "__" in n:
        server, method = n.split("__", 1)
        server, method = _human_tool_token(server), _human_tool_token(method)
        if server and method:
            return f"{server} · {method}"
    if n.startswith("mcp_") and len(n) > 4:
        return f"mcp · {_human_tool_token(n[4:])}"
    return _human_tool_token(n)


def list_event_preview(summary: str, tool_name: str = "") -> str:
    """Timeline summary text: same words as ``summary_line``, tool id humanized."""
    s = display_message_text(summary or "")
    n = (tool_name or "").strip()
    if n and s.startswith(n):
        rest = s[len(n) :].lstrip()
        if rest:
            first, _, tail = rest.partition(" ")
            if first and "__" in first:
                human = format_tool_display(first)
                return f"{format_tool_display(n)} {human}" + (f" {tail}" if tail else "")
        return f"{format_tool_display(n)}{s[len(n) :]}"
    return s


def list_event_detail(summary: str, tool_name: str = "") -> str:
    """Remainder after the tool label (name already shown beside it)."""
    s = list_event_preview(summary, tool_name)
    label = format_tool_display(tool_name) if tool_name else ""
    if label and s.startswith(label):
        return s[len(label) :].strip()
    return s


_TASK_DUMP_KEYS = (
    "tool_call_id",
    "task_id",
    "command",
    "cwd",
    "prompt_id",
    "mode",
    "state",
)


def task_fields_from_content(content: str) -> dict[str, str]:
    """Recover command/cwd from the origin/main ``key=value`` bookend dump."""
    text = (content or "").strip()
    if not text.startswith("task_backgrounded") and not text.startswith("task_completed"):
        return {}
    marks: list[tuple[int, str]] = []
    for key in _TASK_DUMP_KEYS:
        token = f"{key}="
        idx = text.find(token)
        if idx < 0:
            continue
        if idx > 0 and not text[idx - 1].isspace():
            continue
        marks.append((idx, key))
    marks.sort()
    out: dict[str, str] = {}
    for i, (start, key) in enumerate(marks):
        val_start = start + len(key) + 1
        val_end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out[key] = text[val_start:val_end].strip()
    brace = text.find("{")
    if brace >= 0:
        try:
            extra = json.loads(text[brace:])
        except json.JSONDecodeError:
            extra = None
        if isinstance(extra, dict):
            for key in ("command", "cwd", "description", "output_file", "display_command"):
                val = extra.get(key)
                if val is not None and str(val).strip() and key not in out:
                    out[key] = str(val)
    return out


def job_list_preview(
    event_type: str,
    raw: Mapping[str, JsonValue] | None,
    content: str = "",
    *,
    max_chars: int = 80,
) -> str:
    """Summary column for a background / monitor / schedule bookend.

    Prefers structured fields. Recovers ``command`` from the origin/main
    dump when ``raw_input`` is empty. Never repeats the event type.
    """
    bag = as_json_object(raw) if isinstance(raw, dict) else {}
    dump = task_fields_from_content(content)
    command = (
        json_as_str(bag.get("command") or bag.get("display_command")).strip()
        or dump.get("command", "").strip()
        or dump.get("display_command", "").strip()
    )
    desc = json_as_str(bag.get("description") or bag.get("monitor_description")).strip()
    if event_type.startswith("scheduled_task_"):
        bits = [
            json_as_str(bag.get("human_schedule")).strip(),
            json_as_str(bag.get("prompt")).replace("\n", " ").strip()[:48],
        ]
        text = " · ".join(b for b in bits if b) or json_as_str(bag.get("task_id")).strip()
        return clip_preview(text, max_chars)
    if command:
        return clip_preview(f"$ {command.replace(chr(10), ' ').strip()}", max_chars)
    if desc:
        return clip_preview(desc, max_chars)
    one = (content or "").replace("\n", " ").strip()
    if not one or one.startswith(event_type) or one.startswith("{"):
        return ""
    return clip_preview(one, max_chars)


def clip_preview(text: str, max_chars: int) -> str:
    """One-line preview, ellipsis when longer than *max_chars*."""
    one = (text or "").replace("\n", " ").strip()
    if max_chars <= 0 or len(one) <= max_chars:
        return one
    return one[: max(1, max_chars - 1)] + "…"


# Host read_file dumps ``1→`` / ``12->`` before each source line.
_LINE_PREFIX = re.compile(r"^(\s*)(\d+)(?:→|->)[ \t]?", re.MULTILINE)

_PRIMARY_INPUT_KEYS = (
    "command",
    "old_string",
    "new_string",
    "target_file",
    "file_path",
    "target_directory",
    "path",
    "pattern",
    "query",
    "url",
    "prompt",
    "description",
    "question",
    "image",
    "run_id",
    "resume_from_run_id",
    "name",
    "script_path",
    "task_id",
)


def strip_inline_line_prefixes(text: str) -> str:
    """Remove ``N→`` / ``N->`` prefixes from a ``read_file`` body.

    :param text: Raw tool result, possibly with numbered prefixes.
    :returns: Source text with prefixes stripped; unchanged when none match.
    """
    if not text or ("→" not in text and "->" not in text):
        return text or ""
    return _LINE_PREFIX.sub(r"\1", text)


def looks_like_numbered_file(text: str) -> bool:
    """True when *text* looks like a numbered ``read_file`` dump."""
    if not text:
        return False
    hits = 0
    for i, line in enumerate(text.splitlines()):
        if i >= 12:
            break
        if _LINE_PREFIX.match(line):
            hits += 1
            if hits >= 2:
                return True
    if hits == 1 and text.count("\n") == 0:
        return True
    return False


def display_tool_output(text: str, *, tool_name: str = "") -> str:
    """Body text for inspect: strip numbered prefixes on file dumps."""
    raw = text or ""
    if (tool_name or "").strip() == "read_file" or looks_like_numbered_file(raw):
        return strip_inline_line_prefixes(raw)
    return raw


def web_search_from_action(action: object) -> tuple[str, str, str]:
    """Flatten ``rawOutput.action`` to (display body, query, page url).

    Host traces use ``type=search`` (query + ``sources[]``) and
    ``type=open_page`` (single ``url``).

    :param action: Host ``tool_call_update.rawOutput.action`` object.
    :returns: ``(body, query, url)``.
    """
    if not isinstance(action, dict):
        return "", "", ""
    query = json_as_str(action.get("query")).strip()
    page_url = json_as_str(action.get("url") or action.get("link")).strip()
    lines: list[str] = []
    if query:
        lines.append(query)
    sources = action.get("sources")
    urls: list[str] = []
    if isinstance(sources, list):
        for item in sources:
            url = ""
            title = ""
            if isinstance(item, dict):
                url = json_as_str(item.get("url") or item.get("link")).strip()
                title = json_as_str(item.get("title")).strip()
            elif isinstance(item, str):
                url = item.strip()
            if not url:
                continue
            urls.append(f"{title}  {url}".strip() if title else url)
    if page_url and page_url not in urls:
        urls.append(page_url)
    if urls:
        if lines:
            lines.append("")
        lines.extend(urls)
    return "\n".join(lines), query, page_url


def web_search_from_raw_output(raw_output: object) -> tuple[str, str, str]:
    """Read query + URLs from ``rawOutput`` (``action`` or top-level)."""
    if not isinstance(raw_output, dict):
        return "", "", ""
    body, query, url = web_search_from_action(raw_output.get("action"))
    if body or query or url:
        return body, query, url
    return web_search_from_action(raw_output)


def image_result_path(content: str, raw_output: object | None = None) -> str:
    """Filesystem path from an ``image_gen`` / ``image_edit`` result.

    :param content: Tool result text (often JSON with ``path``).
    :param raw_output: Optional ``rawOutput`` object with a ``path`` key.
    :returns: Path string, or empty when none is present.
    """
    if isinstance(raw_output, dict):
        path = json_as_str(raw_output.get("path")).strip()
        if path:
            return path
    s = (content or "").strip()
    if not s or s[0] not in "{[":
        return ""
    try:
        obj = json.loads(s)
    except (TypeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    path = json_as_str(obj.get("path")).strip()
    return path


def display_message_text(text: str) -> str:
    """Operator-facing message text without paste tokens or still-file links."""
    cleaned = _IMAGE_MARK.sub("", text or "")
    cleaned = _MD_STILL_LINK.sub("", cleaned)
    cleaned = _BARE_STILL_LINE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def cache_still_bytes(blob: bytes) -> Path:
    """Write pasted still bytes into the app cache; return the file path."""
    from .paths import cache_dir

    digest = hashlib.sha256(blob).hexdigest()[:24]
    dest = cache_dir() / "images" / f"{digest}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        dest.write_bytes(blob)
    return dest


def _add_still(out: list[Path], seen: set[str], path: Path, *, require_file: bool) -> None:
    try:
        key = str(path.expanduser().resolve()) if path.exists() else str(path.expanduser())
    except OSError:
        key = str(path)
    if key in seen:
        return
    if require_file and not path.is_file():
        return
    if path.suffix.lower() not in _STILL_SUFFIX and require_file:
        return
    if path.suffix.lower() not in _STILL_SUFFIX and path.suffix:
        return
    seen.add(key)
    out.append(path)


def event_still_paths(
    content: str,
    *,
    tool_name: str = "",
    images: Sequence[bytes] = (),
    session_dir: Path | None = None,
    extra_paths: Sequence[str] = (),
) -> list[Path]:
    """Still files for one event: paste cache, tool result, session ``images/``."""
    found: list[Path] = []
    seen: set[str] = set()
    for blob in images:
        if blob:
            _add_still(found, seen, cache_still_bytes(blob), require_file=True)
    tname = (tool_name or "").strip()
    if tname in _STILL_TOOLS:
        raw = image_result_path(content)
        if raw:
            _add_still(found, seen, Path(raw).expanduser(), require_file=False)
    if session_dir is not None:
        root = Path(session_dir)
        for ref in _STILL_REF.findall(content or ""):
            _add_still(found, seen, root / ref, require_file=True)
    for raw in extra_paths:
        text = (raw or "").strip()
        if text:
            _add_still(found, seen, Path(text).expanduser(), require_file=False)
    return found


def image_result_message(content: str) -> str:
    """Human message from image-result JSON, if present."""
    s = (content or "").strip()
    if not s or s[0] not in "{[":
        return ""
    try:
        obj = json.loads(s)
    except (TypeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    return json_as_str(obj.get("message")).strip()


def tool_input_fields(
    tool_name: str,
    raw_input: Mapping[str, JsonValue] | None,
    *,
    max_chars: int = 8_000,
) -> list[JsonObject]:
    """TUI-aligned inspect fields for HUD / tests (not one JSON bag).

    :param tool_name: Resolved tool id (``search_replace``, ``grep``, …).
    :param raw_input: Tool arguments.
    :param max_chars: Per-field cap (primary fields stay readable).
    :returns: ``[{id, label, value}, …]`` in display order.
    """
    ri: JsonObject = as_json_object(raw_input) if raw_input else {}
    tname = (tool_name or "").strip()
    cap = max(0, int(max_chars))

    def _cut(value: JsonValue) -> str:
        text = value if isinstance(value, str) else json_as_str(value)
        if cap and len(text) > cap:
            return text[:cap]
        return text

    fields: list[JsonObject] = []

    def _add(fid: str, label: str, value: JsonValue) -> None:
        text = _cut(value)
        if text:
            fields.append({"id": fid, "label": label, "value": text})

    if tname in ("search_replace", "edit"):
        path = ri.get("file_path") or ri.get("target_file") or ri.get("path") or ""
        _add("file_path", "File", path)
        if ri.get("old_string"):
            _add("old_string", "old_string", ri.get("old_string"))
        if ri.get("new_string"):
            _add("new_string", "new_string", ri.get("new_string"))
        edits = ri.get("edits")
        if isinstance(edits, list):
            for i, item in enumerate(edits):
                if not isinstance(item, dict):
                    continue
                old_t = item.get("oldText") or item.get("old_string")
                new_t = item.get("newText") or item.get("new_string")
                if old_t:
                    _add(f"old_{i}", "oldText", old_t)
                if new_t:
                    _add(f"new_{i}", "newText", new_t)
        extra = {
            k: v
            for k, v in ri.items()
            if k not in ("file_path", "target_file", "path", "old_string", "new_string", "edits")
        }
        if extra:
            _add("extra", "extra", json.dumps(extra, indent=2, ensure_ascii=False))
        return fields
    if tname in ("run_terminal_command", "bash"):
        _add("command", "command", ri.get("command"))
        extra = {k: v for k, v in ri.items() if k != "command"}
        if extra:
            _add("extra", "extra", json.dumps(extra, indent=2, ensure_ascii=False))
        return fields
    if tname in ("read_file", "read"):
        _add(
            "target_file",
            "target_file",
            ri.get("target_file") or ri.get("file_path") or ri.get("path"),
        )
        extra = {k: v for k, v in ri.items() if k not in ("target_file", "file_path", "path")}
        if extra:
            _add("extra", "extra", json.dumps(extra, indent=2, ensure_ascii=False))
        return fields
    if tname in ("write", "write_file", "create_file"):
        _add("file_path", "File", ri.get("path") or ri.get("file_path") or ri.get("target_file"))
        if ri.get("content"):
            _add("content", "content", ri.get("content"))
        extra = {
            k: v for k, v in ri.items() if k not in ("path", "file_path", "target_file", "content")
        }
        if extra:
            _add("extra", "extra", json.dumps(extra, indent=2, ensure_ascii=False))
        return fields
    if tname == "list_dir":
        _add(
            "target_directory",
            "target_directory",
            ri.get("target_directory") or ri.get("path"),
        )
        extra = {k: v for k, v in ri.items() if k not in ("target_directory", "path")}
        if extra:
            _add("extra", "extra", json.dumps(extra, indent=2, ensure_ascii=False))
        return fields
    if tname == "grep":
        _add("pattern", "pattern", ri.get("pattern"))
        extra = {k: v for k, v in ri.items() if k != "pattern"}
        if extra:
            _add("extra", "extra", json.dumps(extra, indent=2, ensure_ascii=False))
        return fields
    if tname == "web_search":
        _add("query", "query", ri.get("query"))
        _add("url", "url", ri.get("url"))
        extra = {k: v for k, v in ri.items() if k not in ("query", "url", "variant", "backend")}
        if extra:
            _add("extra", "extra", json.dumps(extra, indent=2, ensure_ascii=False))
        return fields
    if not ri:
        return fields
    for key in _PRIMARY_INPUT_KEYS:
        if key in ri and ri[key] not in (None, "", [], {}):
            _add(key, key, ri[key])
    leftover = {k: v for k, v in ri.items() if k not in _PRIMARY_INPUT_KEYS}
    if leftover and not fields:
        _add("json", "input", json.dumps(leftover, indent=2, ensure_ascii=False))
    elif leftover:
        _add("extra", "extra", json.dumps(leftover, indent=2, ensure_ascii=False))
    return fields


def preserve_primary_raw_input(raw: JsonObject, max_chars: int) -> JsonObject:
    """Cap *raw* without dropping command / old-new / path / pattern / query."""
    if max_chars <= 0:
        return {}
    try:
        dumped = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return {}
    if len(dumped) <= max_chars:
        return raw
    kept: JsonObject = {}
    for key in _PRIMARY_INPUT_KEYS:
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(val, str) and len(val) > max_chars:
            kept[key] = val[:max_chars]
        else:
            kept[key] = val
    extras = {k: v for k, v in raw.items() if k not in kept}
    if extras:
        try:
            extra_dump = json.dumps(extras, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            extra_dump = ""
        kept["_truncated"] = True
        if extra_dump:
            kept["preview"] = extra_dump[: max(80, max_chars // 4)]
    return kept


__all__ = [
    "cache_still_bytes",
    "display_message_text",
    "display_tool_output",
    "event_still_paths",
    "image_result_message",
    "image_result_path",
    "looks_like_numbered_file",
    "preserve_primary_raw_input",
    "strip_inline_line_prefixes",
    "tool_input_fields",
    "web_search_from_action",
    "web_search_from_raw_output",
]
