"""Catalog query language: parse tree applied to list columns."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from anqa.control.contract import (
    CATALOG_QUERY_COUNTS,
    catalog_query_count_fields,
    catalog_query_flag_count,
    catalog_query_has_count_fields,
    catalog_query_values,
)
from anqa.models import SessionMeta
from anqa.session.query import (
    HAS_VALUES,
    CatalogQueryRow,
    QuerySpan,
    apply_suggestion,
    catalog_goal_count,
    catalog_has_goals,
    catalog_has_jobs,
    catalog_has_notes,
    catalog_has_plan,
    catalog_has_schedules,
    catalog_has_subagents,
    catalog_has_tasks,
    catalog_note_count,
    catalog_plan_count,
    catalog_presence,
    catalog_workflow_count,
    finished_prefix,
    highlight_query_spans,
    row_matches_query,
    suggest_last_token,
)

HAS_TOKENS = (
    "workflow",
    "note",
    "goal",
    "plan",
    "subagent",
    "task",
    "job",
    "schedule",
    "error",
    "failure",
    "diff",
    "compaction",
    "doom",
    "git",
    "context",
)


def _row(
    *,
    session_id: str = "sess-1",
    title: str = "Fix the palette",
    path: str = "/mnt/dev/_git/fubar/sess-1",
    git_repo: str = "/mnt/dev/_git/fubar",
    run_dir: str = "/mnt/dev/_git/fubar",
    error_count: int = 3,
    duration_seconds: int = 0,
    updated_at: str = "2026-08-10T12:00:00+00:00",
) -> CatalogQueryRow:
    return CatalogQueryRow(
        session_id=session_id,
        title=title,
        model="grok-4",
        status="complete",
        outcome="success",
        path=path,
        git_repo=git_repo,
        run_dir=run_dir,
        task_id="eval-a",
        error_count=error_count,
        turn_count=8,
        tool_count=12,
        event_count=40,
        duration_seconds=duration_seconds,
        updated_at=updated_at,
        has_workflows=True,
        has_notes=False,
        has_goals=False,
        has_subagents=False,
        has_jobs=False,
        has_schedules=False,
        has_plan=False,
        has_failures=False,
        has_diff=False,
        has_compaction=False,
        has_doom=False,
        has_context=False,
    )


def test_bare_words_match_title_and_id_not_path() -> None:
    row = _row()
    assert row_matches_query(row, "palette")
    assert row_matches_query(row, "SESS-1")
    assert not row_matches_query(row, "_git/fubar")
    assert not row_matches_query(row, "grok-4")


def test_implicit_and_and_or() -> None:
    row = _row()
    assert row_matches_query(row, "has:workflow is:complete")
    assert row_matches_query(row, "has:workflow AND errors:>2")
    assert row_matches_query(row, "is:complete AND errors:>0")
    assert not row_matches_query(row, "is:running AND has:workflow")


def test_has_and_numeric_and_in_path() -> None:
    row = _row()
    assert row_matches_query(row, "has:workflow AND errors:>20") is False
    assert row_matches_query(row, "has:workflow AND errors:>2")
    assert row_matches_query(row, "errors:>=3")
    assert not row_matches_query(row, "errors:>3")
    assert row_matches_query(row, "in:/mnt/dev/_git/fubar")
    assert row_matches_query(row, "in:fubar")
    assert row_matches_query(row, "in:FUBAR")
    assert not row_matches_query(
        _row(git_repo="https://github.com/x/fubar", run_dir=""),
        "in:fubar",
    )
    assert not row_matches_query(row, "in:/mnt/dev/_git/other")
    assert not row_matches_query(_row(run_dir=""), "in:/mnt/dev/_git/fubar")


def test_suggest_in_uses_run_directories() -> None:
    assert suggest_last_token("in:", paths=["/mnt/dev/_git/fubar", "/mnt/dev/_git/fubar"]) == [
        "in:/mnt/dev/_git/fubar"
    ]
    assert suggest_last_token("in:/mnt", paths=["/mnt/dev/_git/fubar"]) == [
        "in:/mnt/dev/_git/fubar"
    ]


def test_is_complete_and_running() -> None:
    row = _row()
    assert row_matches_query(row, "is:complete")
    assert not row_matches_query(row, "is:running")
    assert not row_matches_query(row, "NOT is:complete")


def test_is_import_matches_imported_row() -> None:
    host = _row()
    imported = CatalogQueryRow(
        session_id="imp-1",
        title="Imported",
        origin="host",
        imported=True,
        path="/tmp/imports/grok/imp-1",
    )
    assert row_matches_query(imported, "is:import")
    assert not row_matches_query(imported, "is:host")
    assert not row_matches_query(host, "is:import")
    assert row_matches_query(host, "is:host")


def test_model_task_dates() -> None:
    row = _row()
    assert row_matches_query(row, "model:grok")
    assert row_matches_query(row, "task:eval-a")
    assert row_matches_query(row, "after:2026-08-01")
    assert not row_matches_query(row, "before:2026-08-01")
    assert row_matches_query(row, "before:2026-08-21")


def test_duration_compare() -> None:
    row = _row(duration_seconds=4000)
    assert row_matches_query(row, "duration:>1h")
    assert row_matches_query(row, "duration:>30m")
    assert not row_matches_query(row, "duration:>2h")
    assert row_matches_query(row, "duration:>=4000")
    assert not row_matches_query(_row(duration_seconds=60), "duration:>1h")


def test_incomplete_after_does_not_hide_rows() -> None:
    row = _row()
    assert row_matches_query(row, "after:2026-")
    assert row_matches_query(row, "after:2026-08")
    assert row_matches_query(row, "before:2026-")


def test_human_after_before() -> None:
    now = datetime.now(tz=UTC)
    recent = _row(updated_at=(now - timedelta(hours=2)).isoformat())
    old = _row(updated_at=(now - timedelta(days=20)).isoformat())
    assert row_matches_query(recent, "after:yesterday")
    assert row_matches_query(recent, "after:2d")
    assert row_matches_query(recent, "after:2 days ago")
    assert not row_matches_query(old, "after:yesterday")
    assert not row_matches_query(old, "after:2d")
    assert row_matches_query(old, "before:yesterday")
    assert row_matches_query(old, "before:2 days ago")


def test_and_clause_order_does_not_change_catalog_match() -> None:
    now = datetime.now(tz=UTC)
    recent_err = _row(
        error_count=2,
        updated_at=(now - timedelta(hours=2)).isoformat(),
    )
    old_err = _row(
        error_count=2,
        updated_at=(now - timedelta(days=20)).isoformat(),
    )
    recent_clean = _row(
        error_count=0,
        updated_at=(now - timedelta(hours=2)).isoformat(),
    )
    first = 'after:"2 days ago" AND has:error'
    second = 'has:error AND after:"2 days ago"'
    assert row_matches_query(recent_err, first) is True
    assert row_matches_query(recent_err, second) is True
    assert row_matches_query(old_err, first) is False
    assert row_matches_query(old_err, second) is False
    assert row_matches_query(recent_clean, first) is False
    assert row_matches_query(recent_clean, second) is False


def test_and_clause_order_does_not_change_event_match() -> None:
    from anqa.models import TraceEvent
    from anqa.session.query import event_matches_query

    ev = TraceEvent(
        index=1,
        event_type="tool_call",
        tool_name="read_file",
        content="boom",
        is_error=True,
    )
    first = 'after:"2 days ago" AND has:error'
    second = 'has:error AND after:"2 days ago"'
    assert event_matches_query(ev, first) == event_matches_query(ev, second)


def test_forgiving_unknown_and_incomplete() -> None:
    row = _row()
    assert finished_prefix("has:workflow AND has:") == "has:workflow"
    assert finished_prefix("tool:grep AN") == "tool:grep"
    assert finished_prefix("tool:grep AND") == "tool:grep"
    assert finished_prefix("tool:grep AND is:") == "tool:grep"
    assert finished_prefix("tool:grep AND is:err") == "tool:grep"
    assert finished_prefix("tool:grep AND is:error") == "tool:grep AND is:error"
    assert finished_prefix("is:assistant") == "is:assistant"
    assert row_matches_query(row, "has:workflow AND has:")
    assert row_matches_query(row, "palette AND ((")
    assert row_matches_query(row, "unknown:zzz") is False
    assert row_matches_query(_row(title="unknown:zzz"), "unknown:zzz")


def test_catalog_query_help_lists_schema_tokens() -> None:
    from anqa.control.contract import catalog_query_help_plain

    text = catalog_query_help_plain()
    assert "Bare words match title, id, and label" in text
    from anqa.control.contract import list_query_help_plain

    timeline = list_query_help_plain("timeline")
    assert "tool:" in timeline
    assert "turn:" in timeline
    assert "duration:" in timeline
    assert "in:" not in timeline
    turns = list_query_help_plain("turns")
    assert "has: error" in turns or "has:" in turns
    assert "subagent" in turns
    assert "after:" not in turns
    assert "is: running" in text
    assert "cancelled" in text
    assert "has: workflow" in text
    assert "doom" in text
    assert "in: Directory the session was run in" in text
    assert "duration:" in text
    assert "has:plan plans:>=N" in text
    assert ">=" in text
    assert "OR" in text
    assert "\n" in text
    for line in text.splitlines():
        assert len(line) <= 72, line
    from anqa.control.contract import list_query_help_pairs

    pairs = list_query_help_pairs("timeline")
    assert any(label == "tool:" for label, _body in pairs)
    assert all(label != "after:" for label, _body in pairs)


def test_has_quantity_compare() -> None:
    row = _row(error_count=5)
    assert row_matches_query(row, "has:error")
    assert row_matches_query(row, "errors:>=5")
    assert row_matches_query(row, "errors:5")
    assert not row_matches_query(row, "errors:>=6")
    assert not row_matches_query(row, "has:error:>=5")
    rich = CatalogQueryRow(
        title="palette",
        status="running",
        has_workflows=True,
        has_notes=True,
        counts={"workflows": 3, "notes": 2, "errors": 5},
    )
    assert row_matches_query(rich, "has:workflow")
    assert row_matches_query(rich, "workflows:>=2")
    assert row_matches_query(rich, "workflows:3")
    assert not row_matches_query(rich, "workflows:>=4")
    assert row_matches_query(rich, "workflows:>=2 AND NOT is:complete")
    assert row_matches_query(rich, "notes:>=2 AND errors:>=5")
    assert not row_matches_query(rich, "notes:>=3")


def test_has_goals_quantity() -> None:
    row = CatalogQueryRow(has_goals=True, counts={"goals": 1})
    assert row_matches_query(row, "has:goal")
    assert row_matches_query(row, "goals:>=1")
    assert row_matches_query(row, "goals:1")
    assert not row_matches_query(row, "goals:2")
    assert not row_matches_query(row, "goals:>2")
    assert not row_matches_query(row, "has:goal:2")


def test_catalog_counts_goals_and_plans_from_side_files(tmp_path: Path) -> None:
    sess = tmp_path / "traced"
    sess.mkdir()
    (sess / "goal").mkdir()
    (sess / "goal" / "state.json").write_text('{"goal_id": "a"}', encoding="utf-8")
    (sess / "plan.json").write_text('{"todos": {}}', encoding="utf-8")
    (sess / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    assert catalog_goal_count(sess) == 1
    assert catalog_plan_count(sess) == 1
    row = CatalogQueryRow(has_goals=True, has_plan=True, counts={"goals": 1, "plans": 1})
    assert row_matches_query(row, "goals:1")
    assert row_matches_query(row, "plans:1")
    assert not row_matches_query(row, "goals:2")
    assert not row_matches_query(row, "has:goal:2")
    assert not row_matches_query(row, "has:plans")


def test_has_quantity_skips_boolean_names() -> None:
    row = CatalogQueryRow(git_repo="/tmp/repo")
    assert row_matches_query(row, "has:git")
    assert not row_matches_query(row, "has:git:>=1")


def test_highlight_has_quantity_spans() -> None:
    def kinds(query: str) -> list[tuple[str, str]]:
        return [(query[s.start : s.end], s.kind) for s in highlight_query_spans(query)]

    assert kinds("workflows:>=2") == [
        ("workflows:", "field"),
        (">=2", "value"),
    ]
    assert kinds("goals:2") == [
        ("goals:", "field"),
        ("2", "value"),
    ]
    assert kinds("goals:>2") == [
        ("goals:", "field"),
        (">2", "value"),
    ]
    assert kinds("has:workflow:>=2") == [
        ("has:", "field"),
        ("workflow:>=2", "unknown"),
    ]
    assert kinds("has:gooals:>=2") == [
        ("has:", "field"),
        ("gooals:>=2", "unknown"),
    ]


def test_suggest_has_quantity_from_schema() -> None:
    from anqa.control.contract import (
        CATALOG_QUERY_COMPARE,
        catalog_query_count_fields,
    )

    countable = catalog_query_count_fields()
    assert "workflows" in countable
    assert "errors" in countable
    assert "goals" in countable
    assert "plans" in countable
    assert suggest_last_token("workflows:") == [
        f"workflows:{item}" for item in CATALOG_QUERY_COMPARE
    ]
    assert suggest_last_token("workflows:>") == ["workflows:>=", "workflows:>"]
    assert suggest_last_token("goals:") == [f"goals:{item}" for item in CATALOG_QUERY_COMPARE]
    assert suggest_last_token("has:workflow:") == []


def test_has_tokens_match_published_schema() -> None:
    assert HAS_VALUES == catalog_query_values("has")
    assert HAS_VALUES == HAS_TOKENS
    assert "findings" not in HAS_VALUES
    assert suggest_last_token("has:") == [f"has:{name}" for name in HAS_TOKENS]


def test_count_tokens_are_written_pairs() -> None:
    flags = catalog_query_flag_count()
    counts = catalog_query_count_fields()
    assert flags["plan"] == "plans"
    assert flags["error"] == "errors"
    assert flags["diff"] == "diff"
    assert "plans" in counts
    assert "sheep" not in flags
    assert "sheep" not in counts
    assert CATALOG_QUERY_COUNTS == (
        ("workflow", "workflows", "workflowCount"),
        ("note", "notes", "noteCount"),
        ("goal", "goals", "goalCount"),
        ("plan", "plans", "planCount"),
        ("subagent", "subagents", "subagentCount"),
        ("task", "tasks", "taskCount"),
        ("job", "jobs", "jobCount"),
        ("schedule", "schedules", "scheduleCount"),
        ("error", "errors", "errorCount"),
        ("failure", "failures", "failureCount"),
        ("diff", "diff", "diffLineCount"),
        ("compaction", "compaction", "compactionCount"),
        ("doom", "doom", "doomCount"),
    )


def test_highlight_query_spans_uses_schema_only() -> None:
    def kinds(query: str) -> list[tuple[str, str]]:
        return [(query[s.start : s.end], s.kind) for s in highlight_query_spans(query)]

    assert highlight_query_spans("") == ()
    assert highlight_query_spans("palette") == ()
    assert kinds("has:goal") == [("has:", "field"), ("goal", "value")]
    assert kinds("has:gooals") == [("has:", "field"), ("gooals", "unknown")]
    assert kinds("has:g") == [("has:", "field"), ("g", "unknown")]
    assert kinds("AND NOT has:goal") == [
        ("AND", "operator"),
        ("NOT", "operator"),
        ("has:", "field"),
        ("goal", "value"),
    ]
    assert not row_matches_query(_row(title="palette"), "palette and has:note")
    assert row_matches_query(_row(title="palette"), "palette AND NOT has:note")
    assert not row_matches_query(_row(title="palette"), "palette AND has:note")
    assert kinds("and not has:goal") == [
        ("has:", "field"),
        ("goal", "value"),
    ]
    assert kinds("aNd nOt has:goal") == [
        ("has:", "field"),
        ("goal", "value"),
    ]
    assert kinds("-has:note") == [("-", "operator"), ("has:", "field"), ("note", "value")]
    assert kinds("after:24h") == [("after:", "field"), ("24h", "value")]
    assert kinds('after:"24 hours ago"') == [("after:", "field"), ('"24 hours ago"', "value")]
    assert kinds("duration:>20 minutes") == [
        ("duration:", "field"),
        (">20 minutes", "value"),
    ]
    assert kinds("errors:>2") == [
        ("errors:", "field"),
        (">2", "value"),
    ]
    assert kinds("is:canceled") == [("is:", "field"), ("canceled", "value")]
    assert kinds("after: 24 hours ago AND NOT has:goal") == [
        ("after:", "field"),
        ("24 hours ago", "value"),
        ("AND", "operator"),
        ("NOT", "operator"),
        ("has:", "field"),
        ("goal", "value"),
    ]
    assert highlight_query_spans("has:") == (QuerySpan(0, 4, "field"),)
    assert kinds("foo-has:goal") == []


@pytest.mark.parametrize(
    ("query", "ops"),
    [
        ("palette AND has:note", ["AND"]),
        ("palette OR has:note", ["OR"]),
        ("NOT has:note", ["NOT"]),
        ("palette AND NOT has:note", ["AND", "NOT"]),
        ("palette and has:note", []),
        ("palette or has:note", []),
        ("not has:note", []),
        ("palette aNd has:note", []),
        ("palette AnD NOT has:note", ["NOT"]),
    ],
)
def test_highlight_operators_are_uppercase_only(query: str, ops: list[str]) -> None:
    painted = [query[s.start : s.end] for s in highlight_query_spans(query) if s.kind == "operator"]
    assert painted == ops


@pytest.mark.parametrize(
    ("query", "want"),
    [
        ("palette AND NOT has:note", True),
        ("palette and not has:note", False),
        ("palette aNd NOT has:note", False),
        ("missing OR has:note", False),
        ("missing OR NOT has:note", True),
    ],
)
def test_match_operators_are_uppercase_only(query: str, want: bool) -> None:
    row = _row(title="palette")
    assert row_matches_query(row, query) is want


def test_suggest_and_apply() -> None:
    assert suggest_last_token("") == []
    assert suggest_last_token("   ") == []
    assert suggest_last_token("h") == ["has:", "harness:"]
    assert suggest_last_token("has:") == [f"has:{name}" for name in HAS_TOKENS]
    assert suggest_last_token("has:g") == ["has:goal", "has:git"]
    assert suggest_last_token("has:sub") == ["has:subagent"]
    assert suggest_last_token("has:ta") == ["has:task"]
    assert suggest_last_token("is:co") == ["is:complete"]
    assert suggest_last_token("dur") == ["duration:"]
    assert suggest_last_token("duration:") == [
        "duration:>=",
        "duration:<=",
        "duration:>",
        "duration:<",
        "duration:=",
    ]
    assert suggest_last_token("model:gr", models=["grok-4", "other"]) == ["model:grok-4"]
    assert apply_suggestion("has:", "has:workflow") == "has:workflow "


def test_suggest_timeline_tokens() -> None:
    assert suggest_last_token("h", scope="timeline") == ["has:"]
    assert suggest_last_token("has:", scope="timeline") == ["has:error"]
    assert suggest_last_token("is:", scope="timeline") == [
        "is:tool",
        "is:user",
        "is:assistant",
        "is:error",
        "is:session",
        "is:subagent",
        "is:background",
        "is:workflow",
    ]
    assert suggest_last_token("i", scope="timeline") == ["is:"]
    assert "in:" not in suggest_last_token("i", scope="timeline")
    assert suggest_last_token("errors:", scope="timeline") == [
        "errors:>=",
        "errors:<=",
        "errors:>",
        "errors:<",
        "errors:=",
    ]
    assert suggest_last_token("t", scope="timeline") == ["tool:", "turn:"]
    assert suggest_last_token("d", scope="timeline") == ["duration:"]
    assert suggest_last_token("duration:", scope="timeline") == [
        "duration:>=",
        "duration:<=",
        "duration:>",
        "duration:<",
        "duration:=",
    ]
    assert suggest_last_token("tool:", scope="timeline", tools=["read_file", "grep"]) == [
        "tool:read_file",
        "tool:grep",
    ]


def test_suggest_turns_tokens() -> None:
    assert suggest_last_token("has:", scope="turns") == ["has:error", "has:subagent"]
    assert suggest_last_token("t", scope="turns") == ["tools:"]
    assert suggest_last_token("tools:", scope="turns") == [
        "tools:>=",
        "tools:<=",
        "tools:>",
        "tools:<",
        "tools:=",
    ]
    assert suggest_last_token("has:p", scope="turns") == []
    assert "has:plan" not in suggest_last_token("has:", scope="turns")


def test_has_presence_tokens_match_row_flags() -> None:
    empty = _row(error_count=0, git_repo="")
    assert not row_matches_query(empty, "has:error")
    assert not row_matches_query(empty, "has:goal")
    assert not row_matches_query(empty, "has:subagent")
    assert not row_matches_query(empty, "has:task")
    assert not row_matches_query(empty, "has:git")
    full = CatalogQueryRow(
        session_id="sess-1",
        title="Fix the palette",
        git_repo="/mnt/dev/_git/fubar",
        error_count=1,
        has_workflows=True,
        has_notes=True,
        has_goals=True,
        has_subagents=True,
        has_jobs=True,
        has_schedules=True,
        has_plan=True,
        has_failures=True,
        has_diff=True,
        has_compaction=True,
        has_doom=True,
        has_context=True,
    )
    for name in HAS_TOKENS:
        assert row_matches_query(full, f"has:{name}"), name
    jobs_only = CatalogQueryRow(has_jobs=True)
    schedules_only = CatalogQueryRow(has_schedules=True)
    assert row_matches_query(jobs_only, "has:job")
    assert row_matches_query(jobs_only, "has:task")
    assert not row_matches_query(jobs_only, "has:schedule")
    assert row_matches_query(schedules_only, "has:schedule")
    assert row_matches_query(schedules_only, "has:task")
    assert not row_matches_query(schedules_only, "has:job")
    assert not row_matches_query(CatalogQueryRow(git_repo=""), "has:git")
    assert row_matches_query(CatalogQueryRow(git_repo="/tmp/repo"), "has:git")
    assert not row_matches_query(CatalogQueryRow(has_context=False), "has:context")
    assert row_matches_query(CatalogQueryRow(has_context=True), "has:context")


def test_catalog_has_disk_entities(tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert catalog_has_goals(empty) is False
    assert catalog_has_subagents(empty) is False
    assert catalog_has_jobs(empty) is False
    assert catalog_has_schedules(empty) is False
    assert catalog_has_tasks(empty) is False
    assert catalog_has_plan(empty) is False

    goal = tmp_path / "goal-sess"
    goal.mkdir()
    (goal / "goal").mkdir()
    (goal / "goal" / "state.json").write_text("{}", encoding="utf-8")
    assert catalog_has_goals(goal)
    assert catalog_goal_count(goal) == 1
    assert catalog_goal_count(empty) == 0

    kids = tmp_path / "kids"
    kids.mkdir()
    (kids / "subagents" / "child-1").mkdir(parents=True)
    assert catalog_has_subagents(kids)

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "background_tasks_manifest.json").write_text('[{"task_id": "j1"}]', encoding="utf-8")
    assert catalog_has_jobs(jobs)
    assert catalog_has_tasks(jobs)

    term = tmp_path / "term"
    term.mkdir()
    (term / "terminal").mkdir()
    (term / "terminal" / "call-bg.log").write_text("ok\n", encoding="utf-8")
    assert catalog_has_jobs(term)

    sched = tmp_path / "sched"
    sched.mkdir()
    (sched / "resources_state.json").write_text(
        ('{"state":{"grok_build.Scheduler":{"tasks":[{"id":"s1","intervalSecs":60}]}}}'),
        encoding="utf-8",
    )
    assert catalog_has_schedules(sched)
    assert catalog_has_tasks(sched)
    assert not catalog_has_jobs(sched)

    planned = tmp_path / "planned"
    planned.mkdir()
    (planned / "plan.json").write_text("{}", encoding="utf-8")
    assert catalog_has_plan(planned)

    flows = tmp_path / "flows"
    flows.mkdir()
    (flows / "workflows" / "a").mkdir(parents=True)
    (flows / "workflows" / "b").mkdir()
    assert catalog_workflow_count(flows) == 2
    meta = SessionMeta(
        session_id="flows",
        session_dir=flows,
        error_count=4,
        tool_failure_count=1,
    )
    row = catalog_presence(flows, meta)
    for _name, wire in catalog_query_has_count_fields().items():
        assert wire in row, wire
    assert row["workflowCount"] == 2
    assert row["hasWorkflows"] is True
    assert row["errorCount"] == 4
    assert row["failureCount"] == 1
    assert row["goalCount"] == 0

    mode = tmp_path / "plan-mode"
    mode.mkdir()
    (mode / "plan_mode.json").write_text("{}", encoding="utf-8")
    assert catalog_has_plan(mode)


def test_catalog_has_notes_reads_harness_overlay(tmp_path, monkeypatch) -> None:
    import anqa.harness.ref as ref_mod
    import anqa.paths as paths_mod
    from anqa.notes import NOTES_FILENAME, NoteEntry, NotesDoc, dump_notes_toml

    home = paths_mod.APP_HOME
    monkeypatch.setattr(ref_mod, "APP_HOME", home)
    session = tmp_path / "overlay-sid"
    session.mkdir()
    (session / "summary.json").write_text("{}", encoding="utf-8")
    overlay = home / "notes" / "grok" / session.name
    overlay.mkdir(parents=True)
    doc = NotesDoc(session_id=session.name)
    doc.upsert(NoteEntry.new(turn_index=1, fields={"summary": "from overlay"}, note_id="n-ov"))
    (overlay / NOTES_FILENAME).write_text(dump_notes_toml(doc), encoding="utf-8")

    assert catalog_has_notes(session)
    assert catalog_note_count(session) == 1
    row = catalog_presence(session, SessionMeta(session_id=session.name, session_dir=session))
    assert row["hasNotes"] is True
    assert row["noteCount"] == 1
    assert row_matches_query(CatalogQueryRow.from_wire(row), "has:note")


def test_event_and_turn_use_same_query_language() -> None:
    from anqa.models import TraceEvent
    from anqa.session.query import event_matches_query, turn_matches_query

    ev = TraceEvent(
        index=1,
        event_type="tool_call",
        tool_name="read_file",
        content="hello user",
        is_error=True,
    )
    assert event_matches_query(ev, "hello")
    assert event_matches_query(ev, "has:error")
    assert event_matches_query(ev, "errors:>=1")
    assert event_matches_query(ev, "is:tool AND has:error")
    assert event_matches_query(ev, "tool:read")
    assert event_matches_query(ev, "tool:read_file")
    assert not event_matches_query(ev, "tool:grep")
    assert event_matches_query(ev, "turn:2", turn=2)
    assert event_matches_query(ev, "turn:>=2", turn=2)
    assert not event_matches_query(ev, "turn:3", turn=2)
    assert not event_matches_query(ev, "is:user")
    assert not event_matches_query(ev, "is:workflow")
    assert not event_matches_query(ev, "has:error:>=1")
    assert not event_matches_query(ev, "user:hello")
    user_ev = TraceEvent(index=2, event_type="user_message_chunk", content="please fix hello")
    assert event_matches_query(user_ev, "user:hello")
    assert event_matches_query(user_ev, "is:user AND user:fix")
    assert not event_matches_query(user_ev, "tool:read")
    huge = TraceEvent(
        index=3,
        event_type="tool_call",
        tool_name="read_file",
        content="assistant " * 50_000,
    )
    assert event_matches_query(huge, "is:tool")
    assert not event_matches_query(huge, "is:assistant")
    grep = TraceEvent(index=4, event_type="tool_call", tool_name="grep", content="x")
    assert event_matches_query(grep, "tool:grep AN")
    assert event_matches_query(grep, "tool:grep AND is:err")
    assert not event_matches_query(grep, "tool:grep AND is:error")
    assert turn_matches_query(
        label="paint the list",
        summary="did the work",
        outcome="success",
        error_count=2,
        tool_count=4,
        event_count=10,
        duration_seconds=90,
        subagent_count=1,
        query="paint AND errors:>=2 AND has:subagent",
    )
    assert not turn_matches_query(
        label="paint the list",
        summary="did the work",
        outcome="success",
        error_count=2,
        tool_count=4,
        event_count=10,
        duration_seconds=90,
        subagent_count=0,
        query="has:subagent",
    )


def test_tag_token_matches_one_or_many() -> None:
    row = CatalogQueryRow(tags=("review", "ui"))
    assert row_matches_query(row, "tag:review")
    assert row_matches_query(row, "tag:REVIEW")
    assert row_matches_query(row, "tag:review,ui")
    assert row_matches_query(row, "tag:review tag:ui")
    assert row_matches_query(row, "tag:review OR tag:bug")
    assert not row_matches_query(row, "tag:bug")
    assert not row_matches_query(row, "tag:review,bug")
    assert not row_matches_query(CatalogQueryRow(), "tag:review")


def test_suggest_tag_completes_known_and_comma_list() -> None:
    known = ["review", "ui", "bug"]
    assert suggest_last_token("tag:", tags=known) == [
        "tag:review",
        "tag:ui",
        "tag:bug",
    ]
    assert suggest_last_token("tag:re", tags=known) == ["tag:review"]
    assert suggest_last_token("tag:review,u", tags=known) == ["tag:review,ui"]
    assert "tag:" in suggest_last_token("t")
