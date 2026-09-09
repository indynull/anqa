"""Catalog query language: luqum parse tree applied to list columns.

``session/list`` ``query`` is this language. Bare words match title, id, and
label. Typed tokens (``is:``, ``has:plan``, ``plans:>=2``, ``in:``) match
catalog columns. Implicit space is AND. Unknown fields and parse failures
become ordinary words (Gmail-style).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import dateparser
from luqum.exceptions import InconsistentQueryException, ParseError
from luqum.parser import parser as luqum_parser
from luqum.tree import (
    AndOperation,
    From,
    Group,
    Item,
    Not,
    OrOperation,
    Phrase,
    Prohibit,
    Range,
    SearchField,
    To,
    UnknownOperation,
    Word,
)
from luqum.utils import UnknownOperationResolver
from pytimeparse import parse as parse_span

from ..control.contract import (
    CATALOG_QUERY_COMPARE,
    CATALOG_QUERY_OPERATORS,
    all_query_field_names,
    catalog_query_compare_fields,
    catalog_query_count_fields,
    catalog_query_field_names,
    catalog_query_flag_count,
    catalog_query_has_count_fields,
    catalog_query_values,
    list_query_compare_fields,
    list_query_field_names,
    list_query_values,
)
from ..harness.registry import scheduler_state
from ..models import (
    JsonObject,
    JsonValue,
    SessionMeta,
    TraceEvent,
    as_json_object,
    json_as_str,
    json_count,
)
from ..paths import is_import_locator
from ..stamp import Stamp

# Language comes from the published control contract. Row attributes for
# ``has:`` stay here (implementation, not the token list).
FIELD_NAMES: tuple[str, ...] = catalog_query_field_names()
ALL_FIELD_NAMES: tuple[str, ...] = all_query_field_names()
IS_VALUES: tuple[str, ...] = catalog_query_values("is")
HAS_VALUES: tuple[str, ...] = catalog_query_values("has")
HAS_VALUE_SET = frozenset(HAS_VALUES)
HAS_COUNT_FIELDS: dict[str, str] = catalog_query_has_count_fields()
COUNT_FIELDS: dict[str, str] = catalog_query_count_fields()
FLAG_COUNT: dict[str, str] = catalog_query_flag_count()
COMPARE_PREFIXES: tuple[str, ...] = CATALOG_QUERY_COMPARE
COMPARE_FIELDS: tuple[str, ...] = catalog_query_compare_fields()
HAS_FLAGS: tuple[tuple[str, str], ...] = (
    ("workflow", "has_workflows"),
    ("note", "has_notes"),
    ("goal", "has_goals"),
    ("subagent", "has_subagents"),
    ("job", "has_jobs"),
    ("schedule", "has_schedules"),
    ("plan", "has_plan"),
    ("failure", "has_failures"),
    ("diff", "has_diff"),
    ("compaction", "has_compaction"),
    ("doom", "has_doom"),
)
PRESENCE_ATTRS: tuple[tuple[str, str], ...] = (
    ("hasWorkflows", "has_workflows"),
    ("hasNotes", "has_notes"),
    ("hasGoals", "has_goals"),
    ("hasSubagents", "has_subagents"),
    ("hasJobs", "has_jobs"),
    ("hasSchedules", "has_schedules"),
    ("hasPlan", "has_plan"),
    ("hasFailures", "has_failures"),
    ("hasDiff", "has_diff"),
    ("hasCompaction", "has_compaction"),
    ("hasDoom", "has_doom"),
)

INCOMPLETE_FIELD = re.compile(
    rf"(?i)(?:^|\s)(?:{'|'.join(ALL_FIELD_NAMES)}):$",
)
TRAILING_BOOL = re.compile(r"(?i)(?:^|\s)(?:AND|OR|NOT|AN)$")
TRAILING_ENUM = re.compile(r"(?i)(?:^|\s)(is|has):(\S+)$")
IN_UNQUOTED = re.compile(r'(?i)(?<![A-Za-z0-9_])(in:)(?!")(\S+)')
WHEN_UNQUOTED = re.compile(
    r'(?i)(?<![A-Za-z0-9_])((?:after|before):)(?!")(.+?)(?=\s+(?:AND|OR|NOT)\b|\s*\)|$)'
)
COMPACT_SPAN = re.compile(r"(?i)^(\d+(?:\.\d+)?)([smhdw])$")
SPAN_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
WORD_SPLIT = re.compile(r"\s+")
SKIP_WORDS = frozenset({"and", "or", "not", "(", ")", "((", "))"})
FIELD_SET = frozenset(ALL_FIELD_NAMES)
BOOL_WORDS = frozenset({"and", "or", "not"})
HIGHLIGHT_RE = re.compile(
    r"(?P<operator>\b(?:"
    + "|".join(re.escape(op) for op in CATALOG_QUERY_OPERATORS if op != "-")
    + r")\b)"
    r"|(?P<prohibit>-)?(?P<field>(?i:"
    + "|".join(re.escape(name) for name in ALL_FIELD_NAMES)
    + r")):(?P<value>\s*\"[^\"]*\"|\s*[^\s)]*)"
)

RESOLVE_AND = UnknownOperationResolver(resolve_to=AndOperation)


class QuerySpanKind(StrEnum):
    """Highlighted slice of a catalog query."""

    FIELD = "field"
    VALUE = "value"
    UNKNOWN = "unknown"
    OPERATOR = "operator"


@dataclass(frozen=True)
class QuerySpan:
    """One highlighted slice of a catalog query (character offsets)."""

    start: int
    end: int
    kind: QuerySpanKind


@dataclass(frozen=True)
class CatalogQueryRow:
    """Column bag one catalog query can see."""

    session_id: str = ""
    title: str = ""
    label: str = ""
    model: str = ""
    status: str = ""
    outcome: str = ""
    origin: str = ""
    imported: bool = False
    harness: str = ""
    path: str = ""
    git_repo: str = ""
    run_dir: str = ""
    task_id: str = ""
    error_count: int = 0
    turn_count: int = 0
    tool_count: int = 0
    event_count: int = 0
    duration_seconds: int = 0
    updated_at: str = ""
    has_workflows: bool = False
    has_notes: bool = False
    has_goals: bool = False
    has_subagents: bool = False
    has_jobs: bool = False
    has_schedules: bool = False
    has_plan: bool = False
    has_failures: bool = False
    has_diff: bool = False
    has_compaction: bool = False
    has_doom: bool = False
    has_context: bool = False
    tags: tuple[str, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, row: JsonObject) -> CatalogQueryRow:
        """Build from a ``session/list`` row.

        :param row: One control list row.
        :return: Columns the catalog query language can see.
        """
        window = row.get("contextWindowTokens")
        has_context = (
            row.get("hasContext") is True
            or row.get("contextWindowUsagePct") is not None
            or row.get("contextTokensUsed") is not None
            or (isinstance(window, int | float) and not isinstance(window, bool) and window > 0)
        )
        return cls(
            session_id=json_as_str(row.get("sessionId")),
            title=json_as_str(row.get("title")),
            label=json_as_str(row.get("label")),
            model=json_as_str(row.get("model")),
            status=json_as_str(row.get("status")),
            outcome=json_as_str(row.get("outcome")),
            origin=json_as_str(row.get("origin")),
            imported=bool(row.get("imported")),
            harness=json_as_str(row.get("harness")),
            path=json_as_str(row.get("path")),
            git_repo=json_as_str(row.get("gitRepo")),
            run_dir=json_as_str(row.get("runDir")),
            task_id=json_as_str(row.get("taskId")),
            error_count=json_count(row.get("errorCount")),
            turn_count=json_count(row.get("turnCount")),
            tool_count=json_count(row.get("toolCallCount")),
            event_count=json_count(row.get("numEvents")),
            duration_seconds=json_count(row.get("durationSeconds")),
            updated_at=json_as_str(row.get("updatedAt") or row.get("updated_at")),
            has_workflows=bool(row.get("hasWorkflows")),
            has_notes=bool(row.get("hasNotes")),
            has_goals=bool(row.get("hasGoals")),
            has_subagents=bool(row.get("hasSubagents")),
            has_jobs=bool(row.get("hasJobs")),
            has_schedules=bool(row.get("hasSchedules")),
            has_plan=bool(row.get("hasPlan")),
            has_failures=bool(row.get("hasFailures")),
            has_diff=bool(row.get("hasDiff")),
            has_compaction=bool(row.get("hasCompaction")),
            has_doom=bool(row.get("hasDoom")),
            has_context=has_context,
            tags=_tags_from_wire(row.get("tags")),
            counts={name: json_count(row.get(wire)) for name, wire in COUNT_FIELDS.items()},
        )

    @classmethod
    def from_meta(cls, meta: SessionMeta, label: str = "") -> CatalogQueryRow:
        """Build from home-list :class:`~anqa.models.SessionMeta`.

        :param meta: Loaded list meta.
        :param label: Optional list label override.
        :return: Columns the catalog query language can see.
        """
        try:
            path = str(Path(meta.session_dir).expanduser())
        except OSError:
            path = str(meta.session_dir)
        by_wire = {
            "workflowCount": int(meta.workflow_count or 0),
            "noteCount": int(meta.note_count or 0),
            "goalCount": int(meta.goal_count or 0),
            "planCount": int(meta.plan_count or 0),
            "subagentCount": int(meta.subagent_count or 0),
            "taskCount": int(meta.task_count or 0),
            "jobCount": int(meta.job_count or 0),
            "scheduleCount": int(meta.schedule_count or 0),
            "errorCount": int(meta.error_count or 0),
            "failureCount": int(meta.tool_failure_count or 0),
            "diffLineCount": int(meta.lines_added or 0) + int(meta.lines_removed or 0),
            "compactionCount": int(meta.compaction_count or 0),
            "doomCount": int(meta.doom_loop_warnings or 0),
        }
        return cls(
            session_id=meta.session_id or "",
            title=meta.title or "",
            label=(label or meta.label or "")[:80],
            model=meta.model_display,
            status=meta.list_status_label() or "",
            outcome=meta.turn_outcome or "",
            origin=meta.origin or "",
            imported=bool(path) and is_import_locator(path),
            harness=meta.harness or "",
            path=path,
            git_repo=meta.git_repo or "",
            run_dir=meta.run_dir or "",
            task_id=meta.task_id or "",
            error_count=int(meta.error_count or 0),
            turn_count=int(meta.turn_count or 0),
            tool_count=int(meta.tool_call_count or 0),
            event_count=int(meta.num_events or 0),
            duration_seconds=int(meta.duration_seconds or 0),
            updated_at=str(meta.updated_at or ""),
            has_workflows=bool(meta.has_workflows),
            has_notes=bool(meta.has_notes),
            has_goals=bool(meta.has_goals),
            has_subagents=bool(meta.has_subagents),
            has_jobs=bool(meta.has_jobs),
            has_schedules=bool(meta.has_schedules),
            has_plan=bool(meta.has_plan),
            has_failures=bool(meta.has_failures),
            has_diff=bool(meta.has_diff),
            has_compaction=bool(meta.has_compaction),
            has_doom=bool(meta.has_doom),
            has_context=bool(meta.has_context_usage),
            tags=tuple(meta.tags),
            counts={name: int(by_wire.get(wire, 0)) for name, wire in COUNT_FIELDS.items()},
        )

    def matches_words(self, words: Sequence[str]) -> bool:
        """True when every word appears in id, title, or label.

        :param words: Bare query words.
        :return: Whether this row's haystack contains each word.
        """
        hay = " ".join(
            part for part in (self.session_id, self.title, self.label) if part
        ).casefold()
        return all(word.casefold() in hay for word in words if word)

    def has_count(self, name: str) -> int:
        """Countable value for a ``has:`` or compare field.

        :param name: Token name (``note``, ``workflows``, ``error``, …).
        :return: The count, or ``1``/``0`` for a flag.
        """
        key = FLAG_COUNT.get(name, name)
        if key in self.counts:
            return int(self.counts[key])
        if name in self.counts:
            return int(self.counts[name])
        if key == "errors" or name == "error":
            return int(self.error_count)
        if key == "tasks" or name == "task":
            return int(self.has_jobs) + int(self.has_schedules)
        flags = {
            "workflow": self.has_workflows,
            "note": self.has_notes,
            "goal": self.has_goals,
            "subagent": self.has_subagents,
            "job": self.has_jobs,
            "schedule": self.has_schedules,
            "plan": self.has_plan,
            "failure": self.has_failures,
            "diff": self.has_diff,
            "compaction": self.has_compaction,
            "doom": self.has_doom,
            "git": bool(self.git_repo.strip()),
            "context": self.has_context,
            "error": self.error_count > 0,
            "task": bool(self.has_jobs or self.has_schedules),
        }
        return 1 if flags.get(name) else 0

    def number_column(self, field: str) -> int:
        """Numeric catalog column for *field*.

        :param field: Compare-field name (``turns``, ``workflows``, …).
        :return: The integer the compare token sees.
        """
        if field in COUNT_FIELDS:
            return self.has_count(field)
        if field == "turns":
            return self.turn_count
        if field == "tools":
            return self.tool_count
        if field == "duration":
            return self.duration_seconds
        return self.event_count

    def matches_is(self, value: str) -> bool:
        """True when ``is:`` *value* holds for this row.

        :param value: Closed ``is:`` token (already casefolded).
        :return: Whether the row is in that state.
        """
        if value == "import":
            return bool(self.imported) or (bool(self.path) and is_import_locator(self.path))
        if value == "host":
            if self.imported or (bool(self.path) and is_import_locator(self.path)):
                return False
            return (self.origin or "host").strip().casefold() == "host"
        from ..models import ListStatus

        status = self.status.strip().casefold()
        if status in {"—", "-", "–"}:
            status = ListStatus.IDLE
        if value in {ListStatus.CANCELLED, "canceled"}:
            return status in {ListStatus.CANCELLED, "canceled"}
        if value in {ListStatus.IDLE, "—", "-", "–"}:
            return status == ListStatus.IDLE
        return status == value

    def matches_has(self, value: str) -> bool:
        """True when ``has:`` *value* is present.

        :param value: ``has:`` token (already casefolded).
        :return: Whether the named presence is non-zero.
        """
        name, cmp = split_has_value(value)
        if cmp or name not in HAS_VALUE_SET:
            return False
        return self.has_count(name) > 0

    def matches_tag(self, raw: str) -> bool:
        """True when this row has every comma-separated tag in *raw*.

        :param raw: ``tag:`` value (``review`` or ``review,ui``).
        :return: Whether every named tag is present.
        """
        wanted = [part.strip() for part in (raw or "").split(",") if part.strip()]
        if not wanted:
            return False
        have = {tag.casefold() for tag in self.tags}
        return all(name.casefold() in have for name in wanted)

    def matches_in(self, needle: str) -> bool:
        """True when *needle* is a prefix or substring of the run directory.

        :param needle: ``in:`` value (path or fragment).
        :return: Whether this row's run directory matches.
        """
        want = expand_path(needle)
        started = expand_path(self.run_dir)
        if not want or not started:
            raw = (self.run_dir or "").casefold()
            return bool(raw) and needle.strip().strip('"').casefold() in raw
        if want.casefold() in started.casefold():
            return True
        return started == want or started.startswith(want.rstrip("/") + "/")

    def matches_field(self, field: str, expr: Item) -> bool:
        """True when this row satisfies one typed field.

        :param field: Field name (already casefolded).
        :param expr: luqum value node.
        :return: Whether the field matches.
        """
        if field not in FIELD_NAMES:
            return self.matches_words([f"{field}:{term_text(expr)}"])
        if field == "is":
            return self.matches_is(term_text(expr).casefold())
        if field == "has":
            return self.matches_has(term_text(expr).casefold())
        if field == "in":
            return self.matches_in(term_text(expr))
        if field == "harness":
            return term_text(expr).casefold() == (self.harness or "").casefold()
        if field == "model":
            return term_text(expr).casefold() in self.model.casefold()
        if field == "task":
            return term_text(expr).casefold() in self.task_id.casefold()
        if field == "tag":
            return self.matches_tag(term_text(expr))
        if field == "after":
            return match_date(self.updated_at, term_text(expr), after=True)
        if field == "before":
            return match_date(self.updated_at, term_text(expr), after=False)
        return match_number(self.number_column(field), expr)


class CatalogQuery:
    """One catalog query string, parsed once."""

    def __init__(self, query: str) -> None:
        self.source = query or ""
        self.text = finished_prefix(self.source).strip()
        self.tree = parsed_tree(self.text) if self.text else None

    def matches(self, row: CatalogQueryRow) -> bool:
        """True when *row* satisfies this query.

        :param row: Catalog columns.
        :return: Empty query matches. Bare words match id, title, and label.
        """
        if not self.text:
            return True
        if self.tree is None:
            words = bare_words(self.text)
            return True if not words else row.matches_words(words)
        return self._eval(self.tree, row.matches_field, row.matches_words)

    def matches_bag(self, bag: ListQueryBag) -> bool:
        """True when *bag* satisfies this query.

        :param bag: Turns or Timeline columns.
        :return: Empty query matches.
        """
        if not self.text:
            return True
        if self.tree is None:
            words = bare_words(self.text)
            return True if not words else bag.matches_words(words)
        return self._eval(self.tree, bag.matches_field, bag.matches_words)

    def _eval(
        self,
        node: Item,
        field: Callable[[str, Item], bool],
        words: Callable[[Sequence[str]], bool],
    ) -> bool:
        if isinstance(node, Group):
            children = list(node.children)
            return self._eval(children[0], field, words) if children else True
        if isinstance(node, AndOperation | UnknownOperation):
            return all(self._eval(child, field, words) for child in node.children)
        if isinstance(node, OrOperation):
            return any(self._eval(child, field, words) for child in node.children)
        if isinstance(node, Not | Prohibit):
            children = list(node.children)
            return not self._eval(children[0], field, words) if children else True
        if isinstance(node, SearchField):
            return field(node.name.casefold(), node.expr)
        if isinstance(node, Word | Phrase):
            return words([term_text(node)])
        return words([str(node)])

    def needed_fields(self) -> frozenset[str]:
        """Event fields this query must load.

        :return: Field names (``hay``, ``user``, ``kinds``, …). Empty query
            loads nothing.
        """
        if not self.text:
            return frozenset()
        if self.tree is None:
            return frozenset({"hay"}) if bare_words(self.text) else frozenset()
        return frozenset(walk_event_need(self.tree))


def finished_prefix(query: str) -> str:
    """Drop a trailing empty ``field:`` or boolean so as-you-type keeps matches."""
    text = (query or "").rstrip()
    while True:
        nxt = text
        if INCOMPLETE_FIELD.search(nxt):
            nxt = INCOMPLETE_FIELD.sub("", nxt).rstrip()
        if TRAILING_BOOL.search(nxt):
            nxt = TRAILING_BOOL.sub("", nxt).rstrip()
        nxt = strip_incomplete_enum(nxt)
        if nxt == text:
            return text
        text = nxt


def strip_incomplete_enum(text: str) -> str:
    """Drop ``is:err`` while typing ``is:error`` so the last complete clause stays."""
    match = TRAILING_ENUM.search(text)
    if match is None:
        return text
    field = match.group(1).casefold()
    value = match.group(2).strip('"').casefold()
    if not value:
        return text
    closed = (
        list_query_values("timeline", field)
        or list_query_values("turns", field)
        or list_query_values("catalog", field)
    )
    if not closed:
        return text
    if value in {item.casefold() for item in closed}:
        return text
    if any(item.casefold().startswith(value) for item in closed):
        return text[: match.start()].rstrip()
    return text


def prepare_query(query: str) -> str:
    """Quote ``in:`` paths and ``after:`` / ``before:`` phrases for luqum."""
    text = IN_UNQUOTED.sub(lambda match: f'{match.group(1)}"{match.group(2)}"', query)
    return WHEN_UNQUOTED.sub(lambda match: f'{match.group(1)}"{match.group(2).strip()}"', text)


def row_matches_query(row: CatalogQueryRow, query: str) -> bool:
    """True when *row* satisfies *query* (empty query matches).

    :param row: Catalog columns.
    :param query: Catalog query language.
    :return: Whether the row matches.
    """
    return CatalogQuery(query).matches(row)


@lru_cache(maxsize=64)
def parsed_tree(text: str) -> Item | None:
    """luqum tree for a finished query.

    :param text: Finished query text.
    :return: The resolved tree, or ``None`` when the text is not a tree.
    """
    try:
        return RESOLVE_AND(luqum_parser.parse(prepare_query(text)))
    except (ParseError, InconsistentQueryException):
        return None


def suggest_last_token(
    query: str,
    *,
    models: Sequence[str] = (),
    paths: Sequence[str] = (),
    tools: Sequence[str] = (),
    tags: Sequence[str] = (),
    scope: str = "catalog",
) -> list[str]:
    """Last-token completions (field names, closed values, live model/path)."""
    token = last_token(query)
    if not token:
        return []
    names = list_query_field_names(scope)
    if ":" not in token:
        return [f"{name}:" for name in names if name.startswith(token.casefold())]
    field, _, rest = token.partition(":")
    key = field.casefold()
    if key == "tag" and scope == "catalog":
        return _suggest_tag_token(rest, tags)
    prefix = rest.casefold()
    values = values_for_field(key, models=models, paths=paths, tools=tools, tags=tags, scope=scope)
    return [f"{key}:{value}" for value in values if value.casefold().startswith(prefix)]


def _suggest_tag_token(rest: str, tags: Sequence[str]) -> list[str]:
    head, sep, last = rest.rpartition(",")
    prefix = last.casefold()
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        name = str(tag).strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        if not name.casefold().startswith(prefix):
            continue
        if sep:
            out.append(f"tag:{head},{name}")
        else:
            out.append(f"tag:{name}")
    return out


def apply_suggestion(query: str, suggestion: str) -> str:
    """Replace the last token with *suggestion* and leave a trailing space."""
    raw = query or ""
    if not raw.strip():
        return f"{suggestion} "
    stripped = raw.rstrip()
    lead = raw[: len(raw) - len(raw.lstrip())]
    if " " not in stripped:
        return f"{lead}{suggestion} "
    head, _sep, _tail = stripped.rpartition(" ")
    return f"{head} {suggestion} "


def query_has_tokens(query: str) -> bool:
    """True when the finished prefix contains a typed ``field:`` token."""
    text = finished_prefix(query)
    return any(re.search(rf"(?i)(?<![A-Za-z0-9_]){name}:", text) for name in ALL_FIELD_NAMES)


def quoted_value(raw: str) -> bool:
    return len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"'


def extend_open_value(text: str, end: int) -> int:
    """Keep unquoted open values through spaces until AND/OR/NOT or the next field."""
    n = len(text)
    while end < n:
        if text[end] == ")":
            return end
        i = end
        while i < n and text[i] in " \t":
            i += 1
        if i == end or i == n:
            return end
        j = i
        while j < n and text[j] not in " \t)":
            j += 1
        word = text[i:j]
        if word.casefold() in BOOL_WORDS:
            return end
        head, sep, _rest = word.partition(":")
        if sep and head.casefold() in FIELD_SET:
            return end
        end = j
    return end


def trim_value_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in " \t":
        start += 1
    while end > start and text[end - 1] in " \t":
        end -= 1
    return start, end


def value_known(field: str, inner: str) -> bool:
    closed = catalog_query_values(field)
    if field == "has":
        name, cmp = split_has_value(inner)
        return not cmp and name in {item.casefold() for item in closed}
    if not closed:
        return True
    key = inner.casefold()
    if key in {item.casefold() for item in closed}:
        return True
    return field == "is" and key in LIST_IS_KNOWN


def highlight_query_spans(query: str) -> tuple[QuerySpan, ...]:
    """Paint offsets for known fields, values, and operators.

    Live box color uses this scanner because the luqum tree has no source
    offsets. Matching still goes through :func:`row_matches_query`.
    """
    text = query or ""
    spans: list[QuerySpan] = []
    for match in HIGHLIGHT_RE.finditer(text):
        if match.start() > 0 and text[match.start() - 1] not in " \t\n\r(":
            continue
        if match.group("operator") is not None:
            start, end = match.span("operator")
            spans.append(QuerySpan(start, end, QuerySpanKind.OPERATOR))
            continue
        if match.group("prohibit") is not None:
            start, end = match.span("prohibit")
            spans.append(QuerySpan(start, end, QuerySpanKind.OPERATOR))
        field_start, field_end = match.span("field")
        spans.append(QuerySpan(field_start, field_end + 1, QuerySpanKind.FIELD))
        raw = match.group("value") or ""
        value_start, value_end = match.span("value")
        quoted = quoted_value(raw)
        field_key = match.group("field").casefold()
        closed = catalog_query_values(field_key)
        if not quoted and not closed:
            value_end = extend_open_value(text, value_end)
        if not quoted:
            value_start, value_end = trim_value_span(text, value_start, value_end)
        if value_start >= value_end:
            continue
        inner = text[value_start + 1 : value_end - 1] if quoted else text[value_start:value_end]
        if field_key == "has" and not quoted:
            spans.extend(has_value_spans(value_start, value_end, inner))
            continue
        kind = QuerySpanKind.VALUE if value_known(field_key, inner) else QuerySpanKind.UNKNOWN
        spans.append(QuerySpan(value_start, value_end, kind))
    return tuple(spans)


def has_value_spans(start: int, end: int, inner: str) -> tuple[QuerySpan, ...]:
    name, cmp = split_has_value(inner)
    closed = {item.casefold() for item in HAS_VALUES}
    if cmp or name not in closed:
        return (QuerySpan(start, end, QuerySpanKind.UNKNOWN),)
    return (QuerySpan(start, end, QuerySpanKind.VALUE),)


def values_for_field(
    field: str,
    *,
    models: Sequence[str],
    paths: Sequence[str],
    tools: Sequence[str] = (),
    tags: Sequence[str] = (),
    scope: str = "catalog",
) -> tuple[str, ...]:
    closed = list_query_values(scope, field)
    if closed:
        return closed
    if field in list_query_compare_fields(scope):
        return COMPARE_PREFIXES
    if field == "tool" and scope == "timeline":
        return tuple(dict.fromkeys(name for name in tools if name.strip()))
    if scope != "catalog":
        return ()
    if field == "model":
        return tuple(dict.fromkeys(m for m in models if m.strip()))
    if field == "in":
        return tuple(dict.fromkeys(short_path(p) for p in paths if p.strip()))
    if field == "tag":
        return tuple(dict.fromkeys(name.strip() for name in tags if name.strip()))
    return ()


def _tags_from_wire(raw: JsonValue) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item).strip() if item is not None else ""
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return tuple(out)


def short_path(path: str) -> str:
    home = str(Path.home())
    if path.startswith(home + "/") or path == home:
        return "~" + path[len(home) :]
    return path


def last_token(query: str) -> str:
    text = (query or "").rstrip()
    if not text:
        return ""
    if text.endswith(":"):
        piece = text.rsplit(None, 1)[-1]
        return piece
    return text.rsplit(None, 1)[-1]


def split_has_value(raw: str) -> tuple[str, str]:
    """Split ``has:note`` or ``has:workflows:>=2`` into name and compare tail.

    :param raw: Raw ``has:`` value.
    :return: ``(name, compare)``; compare is empty when there is no second colon.
    """
    name, sep, rest = (raw or "").partition(":")
    return name.casefold(), rest if sep else ""


EVENT_IS = (
    ("tool", "tools"),
    ("user", "user"),
    ("assistant", "assistant"),
    ("error", "error"),
    ("session", "session"),
    ("subagent", "subagent"),
    ("background", "background"),
    ("workflow", "workflow"),
)
LIST_IS_KNOWN = frozenset((*IS_VALUES, "canceled", *(name for name, _mode in EVENT_IS)))


def expand_path(raw: str) -> str:
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return ""
    try:
        return str(Path(text).expanduser())
    except OSError:
        return text


def match_date(updated: str, raw: str, *, after: bool) -> bool:
    stamp = float(Stamp.epoch(updated) or 0)
    bound = parse_when(raw)
    if bound <= 0:
        return True
    if stamp <= 0:
        return False
    return stamp >= bound if after else stamp <= bound


def parse_when(raw: str) -> float:
    """ISO date, compact span (``2d``), or a dateparser phrase (``yesterday``)."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return 0.0
    return parse_when_cached(text)


@lru_cache(maxsize=256)
def parse_when_cached(text: str) -> float:
    iso = float(Stamp.epoch(text) or 0)
    if iso > 0:
        return iso
    span = parse_duration_seconds(text)
    if span > 0:
        return datetime.now(tz=UTC).timestamp() - span
    if not any(ch.isalpha() for ch in text):
        return 0.0
    parsed = dateparser.parse(
        text,
        languages=["en"],
        settings={
            "TO_TIMEZONE": "UTC",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PARSERS": ["relative-time"],
        },
    )
    if parsed is None:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def parse_duration_seconds(raw: str) -> int:
    """``90``, ``1h``, ``2d``, ``30m``, or a pytimeparse phrase."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    compact = COMPACT_SPAN.fullmatch(text)
    if compact:
        return int(float(compact.group(1)) * SPAN_SECONDS[compact.group(2).lower()])
    parsed = parse_span(expand_compact_span(text))
    if parsed is None:
        return 0
    return int(parsed)


def expand_compact_span(raw: str) -> str:
    compact = COMPACT_SPAN.fullmatch(raw.strip())
    if compact is None:
        return raw
    amount, unit = compact.group(1), compact.group(2).lower()
    names = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    return f"{amount} {names[unit]} ago"


def match_number_text(actual: int, raw: str) -> bool:
    """Compare *actual* to ``>=5``, ``>2``, ``3``, or a duration (``1h``)."""
    text = (raw or "").strip().strip('"').strip("'")
    for prefix in COMPARE_PREFIXES:
        if text.startswith(prefix):
            bound = parse_duration_seconds(text[len(prefix) :])
            if prefix == ">=":
                return actual >= bound
            if prefix == "<=":
                return actual <= bound
            if prefix == ">":
                return actual > bound
            if prefix == "<":
                return actual < bound
            return actual == bound
    return actual == parse_duration_seconds(text)


def match_number(actual: int, expr: Item) -> bool:
    """Compare *actual* to a luqum number, range, or duration node.

    :param actual: Column value.
    :param expr: luqum value, range, from, or to.
    :return: Whether *actual* satisfies *expr*.
    """
    if isinstance(expr, From):
        bound = expr_number(expr.a)
        return actual >= bound if expr.include else actual > bound
    if isinstance(expr, To):
        bound = expr_number(expr.a)
        return actual <= bound if expr.include else actual < bound
    if isinstance(expr, Range):
        return in_range(actual, expr)
    return actual == expr_number(expr)


def in_range(actual: int, expr: Range) -> bool:
    """True when *actual* is inside a luqum range.

    :param actual: Column value.
    :param expr: luqum range node.
    :return: Whether *actual* is inside the range.
    """
    low = expr_number(expr.low) if str(expr.low) != "*" else None
    high = expr_number(expr.high) if str(expr.high) != "*" else None
    if low is not None and actual < low:
        return False
    return high is None or actual <= high


def expr_number(expr: Item) -> int:
    """Integer or duration from a luqum value node.

    :param expr: luqum value.
    :return: Parsed integer seconds or count, or ``0``.
    """
    text = term_text(expr)
    span = parse_duration_seconds(text)
    if span > 0:
        return span
    try:
        return int(text)
    except ValueError:
        return 0


def term_text(expr: Item) -> str:
    if isinstance(expr, Phrase):
        return str(expr.value).strip().strip('"')
    if isinstance(expr, Word):
        return str(expr.value)
    return str(expr).strip().strip('"')


def bare_words(text: str) -> list[str]:
    return [
        word
        for word in WORD_SPLIT.split(text)
        if word and word.casefold() not in SKIP_WORDS and not word.startswith("(")
    ]


class SessionDir:
    """On-disk session tree used by ``has:`` catalog columns."""

    def __init__(self, session_dir: Path | str) -> None:
        self.path = Path(session_dir)

    def _is_file(self, *parts: str) -> bool:
        try:
            return self.path.joinpath(*parts).is_file()
        except OSError:
            return False

    def _child_count(self, *parts: str) -> int:
        folder = self.path.joinpath(*parts)
        try:
            if not folder.is_dir():
                return 0
            return sum(1 for _ in folder.iterdir())
        except OSError:
            return 0

    def workflow_count(self) -> int:
        """Child entries under ``workflows/``."""
        return self._child_count("workflows")

    def note_count(self) -> int:
        """Notes in the session notes file."""
        from ..notes import load_notes

        return len(load_notes(self.path).notes)

    def goal_count(self) -> int:
        """1 when ``goal/state.json`` is present, else 0."""
        return 1 if self._is_file("goal", "state.json") else 0

    def subagent_count(self) -> int:
        """Child directories under ``subagents/``."""
        return self._child_count("subagents")

    def job_count(self) -> int:
        """Jobs in the manifest, or ``terminal/`` call logs when there is no list."""
        listed_path = self.path / "background_tasks_manifest.json"
        listed = 0
        if self._is_file("background_tasks_manifest.json"):
            try:
                raw = json.loads(listed_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                raw = None
            if isinstance(raw, list):
                listed = len(raw)
        if listed:
            return listed
        terminal = self.path / "terminal"
        try:
            if not terminal.is_dir():
                return 0
            return sum(
                1
                for child in terminal.iterdir()
                if child.is_file()
                and (child.name.startswith("call-") or child.name.startswith("monitor-call-"))
            )
        except OSError:
            return 0

    def schedule_count(self) -> int:
        """Scheduler tasks in ``resources_state.json``."""
        path = self.path / "resources_state.json"
        if not self._is_file("resources_state.json"):
            return 0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return 0
        if not isinstance(raw, dict):
            return 0
        inner = raw.get("state")
        state = inner if isinstance(inner, dict) else {}
        scheduler = scheduler_state(state)
        if not isinstance(scheduler, dict):
            return 0
        tasks = scheduler.get("tasks")
        return len(tasks) if isinstance(tasks, list) else 0

    def plan_count(self) -> int:
        """1 when a plan file exists, else 0."""
        if self._is_file("plan.json") or self._is_file("plan_mode.json"):
            return 1
        return 0

    def compaction_count(self) -> int:
        """Child entries under ``compaction/``."""
        return self._child_count("compaction")

    def has_compaction(self) -> bool:
        """True when ``compaction/`` exists and is non-empty."""
        return self.compaction_count() > 0

    def has_tasks(self) -> bool:
        """True when Overview Tasks would list a job or a schedule."""
        return self.job_count() > 0 or self.schedule_count() > 0

    def presence(self, meta: SessionMeta) -> dict[str, bool | int]:
        """``has:`` flags and counts for one catalog row (disk + loaded meta)."""
        jobs = self.job_count()
        schedules = self.schedule_count()
        workflows = self.workflow_count()
        notes = self.note_count()
        goals = self.goal_count()
        plans = self.plan_count()
        subagents = self.subagent_count()
        errors = int(meta.error_count or 0)
        failures = int(meta.tool_failure_count or 0)
        diff_lines = int(meta.lines_added or 0) + int(meta.lines_removed or 0)
        compaction = max(self.compaction_count(), int(meta.compaction_count or 0))
        doom = int(meta.doom_loop_warnings or 0)
        tasks = jobs + schedules
        counts = {
            "workflows": workflows,
            "notes": notes,
            "goals": goals,
            "plans": plans,
            "subagents": subagents,
            "tasks": tasks,
            "jobs": jobs,
            "schedules": schedules,
            "errors": errors,
            "failures": failures,
            "diff": diff_lines,
            "compaction": compaction,
            "doom": doom,
        }
        out: dict[str, bool | int] = {
            "hasWorkflows": bool(meta.has_workflows) or workflows > 0,
            "hasNotes": bool(meta.has_notes) or notes > 0,
            "hasGoals": bool(meta.has_goals) or goals > 0,
            "hasSubagents": bool(meta.has_subagents) or subagents > 0,
            "hasJobs": bool(meta.has_jobs) or jobs > 0,
            "hasSchedules": bool(meta.has_schedules) or schedules > 0,
            "hasTasks": bool(meta.has_jobs or meta.has_schedules) or tasks > 0,
            "hasPlan": bool(meta.has_plan) or plans > 0,
            "hasFailures": bool(meta.has_failures) or failures > 0,
            "hasDiff": bool(meta.has_diff) or diff_lines > 0,
            "hasCompaction": bool(meta.has_compaction) or compaction > 0,
            "hasDoom": bool(meta.has_doom) or doom > 0,
            "hasContext": bool(meta.has_context_usage),
        }
        for name, wire in COUNT_FIELDS.items():
            out[wire] = int(counts.get(name, 0))
        return out


def catalog_workflow_count(session_dir: Path) -> int:
    """Child entries under ``workflows/``."""
    return SessionDir(session_dir).workflow_count()


def catalog_has_workflows(session_dir: Path) -> bool:
    """True when ``workflows/`` exists and is non-empty."""
    return catalog_workflow_count(session_dir) > 0


def catalog_note_count(session_dir: Path) -> int:
    """Notes in the session notes file."""
    return SessionDir(session_dir).note_count()


def catalog_has_notes(session_dir: Path) -> bool:
    """True when the session notes file has at least one note."""
    return catalog_note_count(session_dir) > 0


def catalog_goal_count(session_dir: Path) -> int:
    """1 when ``goal/state.json`` is present, else 0."""
    return SessionDir(session_dir).goal_count()


def catalog_has_goals(session_dir: Path) -> bool:
    """True when the session created at least one goal."""
    return catalog_goal_count(session_dir) > 0


def catalog_subagent_count(session_dir: Path) -> int:
    """Child directories under ``subagents/``."""
    return SessionDir(session_dir).subagent_count()


def catalog_has_subagents(session_dir: Path) -> bool:
    """True when ``subagents/`` lists at least one child directory."""
    return catalog_subagent_count(session_dir) > 0


def catalog_job_count(session_dir: Path) -> int:
    """Jobs in the manifest, or ``terminal/`` call logs when there is no list."""
    return SessionDir(session_dir).job_count()


def catalog_has_jobs(session_dir: Path) -> bool:
    """True when a job manifest or ``terminal/`` call log is present."""
    return catalog_job_count(session_dir) > 0


def catalog_schedule_count(session_dir: Path) -> int:
    """Scheduler tasks in ``resources_state.json``."""
    return SessionDir(session_dir).schedule_count()


def catalog_has_schedules(session_dir: Path) -> bool:
    """True when ``resources_state.json`` lists scheduler tasks."""
    return catalog_schedule_count(session_dir) > 0


def catalog_has_tasks(session_dir: Path) -> bool:
    """True when Overview Tasks would list a job or a schedule."""
    return SessionDir(session_dir).has_tasks()


def catalog_plan_count(session_dir: Path) -> int:
    """1 when a plan file exists, else 0."""
    return SessionDir(session_dir).plan_count()


def catalog_has_plan(session_dir: Path) -> bool:
    """True when the session entered plan mode or still has a plan file."""
    return catalog_plan_count(session_dir) > 0


def catalog_has_compaction(session_dir: Path) -> bool:
    """True when ``compaction/`` exists and is non-empty."""
    return SessionDir(session_dir).has_compaction()


def catalog_presence_from_meta(meta: SessionMeta) -> dict[str, bool | int]:
    """``has:`` flags and counts from already-loaded list meta (no extra disk)."""
    workflows = int(meta.workflow_count or 0)
    notes = int(meta.note_count or 0)
    goals = int(meta.goal_count or 0)
    plans = int(meta.plan_count or 0)
    subagents = int(meta.subagent_count or 0)
    jobs = int(meta.job_count or 0)
    schedules = int(meta.schedule_count or 0)
    tasks = int(meta.task_count or 0) or (jobs + schedules)
    errors = int(meta.error_count or 0)
    failures = int(meta.tool_failure_count or 0)
    diff_lines = int(meta.lines_added or 0) + int(meta.lines_removed or 0)
    compaction = int(meta.compaction_count or 0)
    doom = int(meta.doom_loop_warnings or 0)
    counts = {
        "workflows": workflows,
        "notes": notes,
        "goals": goals,
        "plans": plans,
        "subagents": subagents,
        "tasks": tasks,
        "jobs": jobs,
        "schedules": schedules,
        "errors": errors,
        "failures": failures,
        "diff": diff_lines,
        "compaction": compaction,
        "doom": doom,
    }
    out: dict[str, bool | int] = {
        "hasWorkflows": bool(meta.has_workflows) or workflows > 0,
        "hasNotes": bool(meta.has_notes) or notes > 0,
        "hasGoals": bool(meta.has_goals) or goals > 0,
        "hasSubagents": bool(meta.has_subagents) or subagents > 0,
        "hasJobs": bool(meta.has_jobs) or jobs > 0,
        "hasSchedules": bool(meta.has_schedules) or schedules > 0,
        "hasTasks": bool(meta.has_jobs or meta.has_schedules) or tasks > 0,
        "hasPlan": bool(meta.has_plan) or plans > 0,
        "hasFailures": bool(meta.has_failures) or failures > 0,
        "hasDiff": bool(meta.has_diff) or diff_lines > 0,
        "hasCompaction": bool(meta.has_compaction) or compaction > 0,
        "hasDoom": bool(meta.has_doom) or doom > 0,
        "hasContext": bool(meta.has_context_usage),
    }
    for name, wire in COUNT_FIELDS.items():
        out[wire] = int(counts.get(name, 0))
    return out


def catalog_presence(session_dir: Path, meta: SessionMeta) -> dict[str, bool | int]:
    """``has:`` flags and counts for one catalog row (disk + loaded meta)."""
    return SessionDir(session_dir).presence(meta)


@dataclass(frozen=True)
class ListQueryBag:
    """Hay, presence, and counts one Turns or Timeline query can see."""

    hay: str
    has: dict[str, bool] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    kinds: frozenset[str] = frozenset()
    tool: str = ""
    turn: int | None = None
    user_hay: str = ""

    def matches_words(self, words: Sequence[str]) -> bool:
        """True when every word appears in this bag's haystack.

        :param words: Bare query words.
        :return: Whether the haystack contains each word.
        """
        folded = self.hay.casefold()
        return all(word.casefold() in folded for word in words if word)

    def matches_field(self, field: str, expr: Item) -> bool:
        """True when this bag satisfies one typed field.

        :param field: Field name (already casefolded).
        :param expr: luqum value node.
        :return: Whether the field matches.
        """
        value = term_text(expr).casefold()
        if field == "is":
            return value in self.kinds
        if field == "has":
            name, cmp = split_has_value(value)
            if cmp or name not in HAS_VALUE_SET:
                return False
            if name in self.has:
                return self.has[name]
            key = FLAG_COUNT.get(name, name)
            return int(self.counts.get(key, 0)) > 0
        if field == "tool":
            return value in self.tool.casefold()
        if field == "user":
            return bool(self.user_hay) and all(
                word.casefold() in self.user_hay.casefold() for word in (value,) if word
            )
        if field == "turn":
            if self.turn is None:
                return False
            return match_number_text(int(self.turn), term_text(expr))
        if field == "duration":
            if "duration" not in self.counts:
                return False
            return match_number_text(int(self.counts["duration"]), term_text(expr))
        if field in COUNT_FIELDS or field in {"tools", "events"}:
            actual = int(self.counts.get(field, 0))
            return match_number_text(actual, term_text(expr))
        return self.matches_words([f"{field}:{term_text(expr)}"])

    @classmethod
    def from_event(
        cls,
        event: TraceEvent,
        turn: int | None,
        need: frozenset[str],
        *,
        duration_seconds: int | None = None,
    ) -> ListQueryBag:
        """Build the bag one Timeline query needs from an event.

        :param event: Timeline event.
        :param turn: Prompt index when the query reads ``turn:``.
        :param need: Fields from :meth:`CatalogQuery.needed_fields`.
        :param duration_seconds: Event duration when the query reads ``duration:``.
        :return: Columns the query language can see.
        """
        kinds: frozenset[str] = frozenset()
        if "kinds" in need or "user" in need:
            from .turns import event_matches_timeline_kind

            wanted = need - {"kinds", "hay", "error", "tool", "turn", "user", "duration"}
            check = tuple(
                (name, mode)
                for name, mode in EVENT_IS
                if not wanted or name in wanted or (name == "user" and "user" in need)
            )
            kinds = frozenset(
                name for name, mode in check if event_matches_timeline_kind(event, mode)
            )
        body = ""
        if "hay" in need or "user" in need:
            body = event.content if isinstance(event.content, str) else str(event.content or "")
        hay = ""
        if "hay" in need:
            hay = " ".join(
                part
                for part in (
                    event.event_type,
                    event.type_label,
                    event.tool_name,
                    event.summary_line,
                    body,
                )
                if part
            )
        err = bool(event.is_error) if "error" in need else False
        counts: dict[str, int] = {}
        if "error" in need:
            counts["errors"] = int(err)
        if "duration" in need and duration_seconds is not None:
            counts["duration"] = int(duration_seconds)
        return cls(
            hay=hay,
            has={"error": err} if "error" in need else {},
            counts=counts,
            kinds=kinds,
            tool=(event.tool_name or "") if "tool" in need else "",
            turn=turn if "turn" in need else None,
            user_hay=body if "user" in need and "user" in kinds else "",
        )


def bag_matches_query(bag: ListQueryBag, query: str) -> bool:
    """True when *bag* satisfies the catalog query language."""
    return CatalogQuery(query).matches_bag(bag)


def event_matches_query(
    event: TraceEvent,
    query: str,
    *,
    turn: int | None = None,
    duration_seconds: int | None = None,
) -> bool:
    """True when a timeline event satisfies *query*."""
    compiled = CatalogQuery(query)
    if not compiled.text:
        return True
    return compiled.matches_bag(
        ListQueryBag.from_event(
            event, turn, compiled.needed_fields(), duration_seconds=duration_seconds
        )
    )


def walk_event_need(node: Item) -> set[str]:
    """Field names one luqum node must load from a timeline event.

    :param node: One tree node.
    :return: Field names that node needs.
    """
    if isinstance(node, Group | AndOperation | OrOperation | UnknownOperation | Not | Prohibit):
        out: set[str] = set()
        for child in node.children:
            out.update(walk_event_need(child))
        return out
    if isinstance(node, SearchField):
        name = node.name.casefold()
        if name == "is":
            value = term_text(node.expr).casefold()
            return {"kinds", value} if value else {"kinds"}
        if name in {"has", "errors"}:
            return {"error"}
        if name == "tool":
            return {"tool"}
        if name == "turn":
            return {"turn"}
        if name == "user":
            return {"user", "kinds"}
        if name == "duration":
            return {"duration"}
        return {"hay"}
    return {"hay"}


def turn_matches_query(
    *,
    label: str,
    summary: str,
    outcome: str,
    error_count: int,
    tool_count: int,
    event_count: int,
    duration_seconds: int,
    subagent_count: int,
    query: str,
) -> bool:
    """True when a turn row satisfies *query*."""
    hay = " ".join(part for part in (label, summary, outcome) if part)
    return bag_matches_query(
        ListQueryBag(
            hay=hay,
            has={
                "error": error_count > 0,
                "subagent": subagent_count > 0,
            },
            counts={
                "errors": error_count,
                "tools": tool_count,
                "events": event_count,
                "duration": duration_seconds,
                "subagents": subagent_count,
            },
        ),
        query,
    )


def compile_bag_predicate(query: str) -> Callable[[ListQueryBag], bool]:
    """Compile *query* once; the result is applied to many bags."""
    compiled = CatalogQuery(query)
    return compiled.matches_bag


def query_needs_hay(query: str) -> bool:
    """True when *query* must read event bodies or summary text."""
    return bool(CatalogQuery(query).needed_fields() & {"hay", "user"})


def event_query_predicate(
    query: str,
) -> Callable[[TraceEvent, int | None], bool]:
    """Compile a Timeline query; call the result once per loaded event."""
    compiled = CatalogQuery(query)
    if not compiled.text:
        return lambda _event, _turn: True
    need = compiled.needed_fields()

    def match(event: TraceEvent, turn: int | None) -> bool:
        return compiled.matches_bag(ListQueryBag.from_event(event, turn, need))

    return match


def apply_catalog_presence(meta: SessionMeta) -> None:
    """Set cheap ``has:`` flags on *meta* from disk and loaded counts."""
    apply_catalog_presence_row(meta, as_json_object(catalog_presence(meta.session_dir, meta)))


COUNT_META_ATTR: tuple[tuple[str, str], ...] = (
    ("workflowCount", "workflow_count"),
    ("noteCount", "note_count"),
    ("goalCount", "goal_count"),
    ("planCount", "plan_count"),
    ("subagentCount", "subagent_count"),
    ("taskCount", "task_count"),
    ("jobCount", "job_count"),
    ("scheduleCount", "schedule_count"),
)


def apply_catalog_presence_row(meta: SessionMeta, row: JsonObject) -> None:
    """Copy ``has*`` flags and countable fields onto *meta*."""
    for key, attr in PRESENCE_ATTRS:
        setattr(meta, attr, bool(row.get(key)))
    for wire, attr in COUNT_META_ATTR:
        setattr(meta, attr, json_count(row.get(wire)))
    if "failureCount" in row:
        meta.tool_failure_count = json_count(row.get("failureCount"))
    if "compactionCount" in row:
        meta.compaction_count = json_count(row.get("compactionCount"))
    if "doomCount" in row:
        meta.doom_loop_warnings = json_count(row.get("doomCount"))


__all__ = [
    "COMPARE_PREFIXES",
    "FIELD_NAMES",
    "HAS_FLAGS",
    "HAS_VALUES",
    "IS_VALUES",
    "CatalogQuery",
    "CatalogQueryRow",
    "SessionDir",
    "QuerySpan",
    "QuerySpanKind",
    "apply_catalog_presence",
    "apply_catalog_presence_row",
    "apply_suggestion",
    "bag_matches_query",
    "compile_bag_predicate",
    "event_matches_query",
    "event_query_predicate",
    "query_needs_hay",
    "ListQueryBag",
    "turn_matches_query",
    "HAS_COUNT_FIELDS",
    "catalog_has_compaction",
    "catalog_has_goals",
    "catalog_has_jobs",
    "catalog_has_notes",
    "catalog_has_plan",
    "catalog_has_schedules",
    "catalog_has_subagents",
    "catalog_has_tasks",
    "catalog_has_workflows",
    "catalog_goal_count",
    "catalog_job_count",
    "catalog_note_count",
    "catalog_plan_count",
    "catalog_schedule_count",
    "catalog_subagent_count",
    "catalog_workflow_count",
    "catalog_presence",
    "catalog_presence_from_meta",
    "finished_prefix",
    "highlight_query_spans",
    "prepare_query",
    "query_has_tokens",
    "row_matches_query",
    "suggest_last_token",
]
