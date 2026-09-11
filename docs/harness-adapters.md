# Supported harnesses

Anqa inspects coding-agent sessions from each product's own store.
One adapter under `anqa/harness/` owns one store. Run `anqa` and the
home list is every shipped store. Filter with `harness:<id>`. The
catalog, timeline, notes, desktop HUD, and control clients all use
the same session id (`harness:<session_id>`). Notes for every store
live under `~/.anqa/notes/<harness>/<session_id>/`.

A shipped adapter lists operator-facing sessions from the default
store (or `[catalog.roots]`), reopens that session from any client,
builds list meta and a timeline in anqa event names, watches the
store (exact `watch_hints` basenames), writes and opens an archive
(`E` / `Ctrl+O`), deletes the
session (`x`), and builds Diff from rewind snapshots when the store
wrote them, else from write and edit tool calls (and the product
Diff rows named below). List Turn comes from that store's own last
signal. `running` is a turn in progress. `—` is no list status
(last user row or bookend). Missing product data stays unset. Next
prompt, end session, rewind, and the context meter appear when that
store wrote them.

OpenCode, Copilot, and Antigravity keep sessions in SQLite (plus a
transcript where that product writes one). Claude Code, Codex,
Cursor, Gemini CLI, and Pi keep one JSONL conversation per session.
Grok Build keeps a session directory (`updates.jsonl`).

Adding or re-checking an adapter, and any change that touches
control, catalog, or a store: `.grok/skills/harness-adapter-qa/SKILL.md`.
`just lint` runs `scripts/check_harness_adapters.py`.
`just harness-probe` compares each installed product version to
`supported_version` and samples on-disk record types (no session text).
Those two commands are required before a control, catalog, or store
commit.
The session surfaces below are the adapter contract:
`tests/session/test_harness_contract.py` drives them on every shipped
store fixture.

## Session surfaces

The terminal app and the desktop palette show the same session
body. Each row is one domain call. The adapter supplies the
timeline and list meta; missing product data stays unset.

| Surface | Where | Domain | Adapter supplies |
|---------|-------|--------|------------------|
| List Turn | Home list | `load_meta` / `list_turn_outcome` → `from_last` | Last store signal. Labels: `running`, `awaiting`, `ending`, `complete`, `cancelled`, `—`. |
| Title, model, product | List + Overview Session | `load_meta` | Title, `model_id`, `harness`, `harness_version`. |
| Context | List + Overview Session | `SessionMeta` context fields | `signals.json` on a directory store, else unset. |
| Timeline | Browser pane 1 / HUD Events | `parse_timeline` | `TraceEvent` list in anqa type names. |
| Turns | HUD Turns / Timeline Turn | `segment_timeline_turns` | `turn_started` plus user / assistant rows. |
| Timeline Filter | Timeline | `event_matches_timeline_kind` | Event types (tools, user, assistant, session, subagents, background, workflows, errors). |
| Subagents | Overview Subagents + Timeline filter | `subagent_runs_for_session` | `subagent_spawned` / `subagent_finished` bookends (and `subagents/` children on a directory store). |
| Tasks | Overview Tasks | `SessionJobs` | `task_backgrounded` / `task_completed` on the timeline; a directory store also reads `background_tasks_manifest.json`, `terminal/` logs, and `resources_state.json` schedules. |
| Workflows | Overview Workflows | `load_session_workflows` + bookends | `workflows/wf_*` on a directory store; workflow events on the timeline. |
| Goals / Plan | List `has:goal` / `has:plan` | `catalog_presence` | `goal/state.json`, `plan.json` / `plan_mode.json` on a directory store; else meta counts. |
| Diff | Browser pane 3 / HUD Diff | `session/diff` | `rewind_points.jsonl` when present, else write / edit tool calls, else OpenCode `summary.diffs`. |
| Notes | Browser pane 4 / HUD Notes | `notes_snapshot(overlay_dir)` | Nothing from the product store. Path is `~/.anqa/notes/<harness>/<id>/`. |
| Stats | Overview Stats | `overview_stat_counts` | Counts of timeline event types and tool names. |
| Export | `E` | `write_archive` | Native archive members. |
| Next prompt / Done | Awaiting turn | Store files those keys read | Only when the store wrote them. |

## Config

The catalog lists every shipped adapter. Narrow with `harness:<id>`.
Opt out of a store in `~/.anqa/config.toml`:

```toml
[catalog]
ignore = ["pi"]
```

A store that is not in its default place gets a root override:

```toml
[catalog.roots]
antigravity = "~/.gemini/antigravity-cli"
claude = "~/.claude/projects"
copilot = "~/.copilot"
codex = "~/.codex/sessions"
cursor = "~/.cursor"
gemini = "~/.gemini/tmp"
grok = "~/.grok/sessions"
opencode = "~/.local/share/opencode/opencode.db"
pi = "~/.pi/agent/sessions"
```

## antigravity — Antigravity

SQLite conversation plus JSONL transcript. Tested **1.1.22**.

Default root: `~/.gemini/antigravity-cli/`. The conversation is
`conversations/<uuid>.db` (`trajectory_meta`). The readable
timeline is `brain/<uuid>/.system_generated/logs/transcript.jsonl`
(or `transcript_full.jsonl`). List title, idle flags, and children
come from `conversation_summaries.db`. Model is `agent_name` or a
`gemini-*` id in the conversation blobs. Working directory comes
from `workspace_uris` or `cache/last_conversations.json`. Discover
skips rows with `parent_conversation_id`. Timeline types include
`USER_INPUT`, `PLANNER_RESPONSE`, and tool calls on those rows.
List Turn is `cancelled` when `killed`, `running` when
`not_fully_idle`, else the last row `status`. Diff is write /
replace tools on the timeline.

## claude — Claude Code

JSONL store. Tested **2.1.251**.

Default root: `~/.claude/projects/<cwd-encoded>/<uuid>.jsonl`. One
file is the parent session. Children live under
`<uuid>/subagents/*.jsonl` and stay off the home list. Timeline
rows are `user` and `assistant` (`tool_use` / `tool_result`).
Chrome rows (`progress`, `file-history-snapshot`, `queue-operation`,
`system`, `mode`, `cost-state`, `permission-mode`, `last-prompt`,
`atis-latch`, …) do not become timeline events. List Turn is
`running` when the last assistant `stop_reason` is tool use;
otherwise that stop reason. Agent / Task tools emit subagent
bookends. Diff is Edit / Write / StrReplace on the timeline.

## copilot — GitHub Copilot CLI

SQLite catalog plus JSONL events. Tested **1.0.82**.

Default files: `~/.copilot/session-store.db` (`sessions` table:
id, cwd, repository, branch, summary, timestamps) and
`~/.copilot/session-state/<id>/events.jsonl`. Timeline types
include `user.message`, `assistant.message`, `tool.execution_start`
/ result, `subagent.started` / `subagent.completed`,
`assistant.turn_start` / `assistant.turn_end`, `session.shutdown`.
List Turn follows the last of those turn signals. Title is the
session `summary`. Diff is write / replace tools on the timeline.

## codex — Codex

JSONL store. Tested **0.151.0**.

Default root: `~/.codex/sessions/**/rollout-*.jsonl`. The session
id is the UUID in the filename. Rows: `session_meta`,
`response_item` (user / assistant messages, `custom_tool_call`,
`function_call`), `event_msg` (`task_started`, `task_complete`,
`turn_aborted`, `item_completed` / `SubAgentActivity`).
`<environment_context>` user blocks are not the title. Model comes
from `turn_context` or `thread_settings_applied`. List Turn is
`task_complete` → complete, `turn_aborted` → cancelled,
`task_started` → idle (a turn bookend, same as a last user row).
Child threads are `SubAgentActivity`
bookends. Diff is `apply_patch` in a tool or `exec` argument: the
published Begin Patch grammar (`*** Add File:`, `*** Update File:`,
`*** Delete File:`, `*** Move to:`, `*** Environment ID:`,
`*** End of File`).

## cursor — Cursor

JSONL transcript plus chat meta. Tested **2026.08.25-3e8eec8**.

Default files: `~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl`
and `~/.cursor/chats/*/<id>/meta.json` (title, cwd, timestamps).
Model is the last `modelName` in `chats/*/<id>/store.db` blobs.
Transcript rows are `role=user` / `role=assistant` (`tool_use`
parts) and `type=turn_ended`. List Turn is the last `turn_ended`
status, or complete when that row has no mapped status. Diff is
write / replace tools on the timeline.

## gemini — Gemini CLI

JSONL store. Tested **0.57.0**.

Default root: `~/.gemini/tmp/<project-hash>/chats/session-*.jsonl`.
A conversation is a header line (`sessionId` + `projectHash`, or
`type=session_metadata`) plus `$set` patches, optional
`$rewindTo`, appended `user` / `gemini` / `error` messages, and
`message_update` merges (tokens and the like; the original message
type stays). `kind=subagent` files stay off the home list.
Bootstrap dumps whose only user text is `<session_context>` are
not list rows. Title is `summary` or the first user text. Model is
the last gemini `model`. List Turn is `running` while a tool is
`pending` / `executing` / `in_progress`; a finished gemini row is
`complete`; a last user row is `—`. Diff is write / replace tools
on the timeline (`run_shell_command` is not a file edit).

## grok — Grok Build

Directory store. Tested **1.0.25**.

Default root: `~/.grok/sessions/<cwd>/<id>/`. The inspectable
trace is `updates.jsonl` (and `events.jsonl` when present).
`signals.json` feeds the context meter. `rewind_points.jsonl`
feeds Diff. `workspace/` and `terminal/` are host planes, not
list events. Subagent session directories stay off the home list;
open them from the parent Summary or Timeline Subagents filter.

List Turn follows live signals on the updates tail. Diff prefers
rewind snapshots, else write / `search_replace` tools.

## opencode — OpenCode

SQLite store. Tested **1.18.29**.

Default file: `~/.local/share/opencode/opencode.db`. Live 1.18
sessions are `event` rows (`session.created.1`, `session.updated.1`,
`message.updated.1`, `message.part.updated.1`) keyed by
`aggregate_id`. The ingest reads that log when the session has
event rows. The `session` / `message` / `part` tables are the
archive shape (`E` writes that JSON; `open_archive` imports it).

Discover skips rows with `parentID` / `parent_id`. Those children
open from the parent `task` bookend. Timeline is user text,
assistant text, reasoning, and tool parts (`bash`, `edit`, `write`,
`read`, …). List Turn is the last part `state.status`, or a
finished assistant, or archived. Diff prefers the last user
`summary.diffs` (`file`, `patch`, `status`, `additions`,
`deletions`), else edit / write tool input (`filePath`,
`oldString`, `newString`, `content`).

## pi — Pi

JSONL store. Tested **0.84.4**. Parse contract: [`harness-pi.md`](harness-pi.md).

Default root: `~/.pi/agent/sessions/**/*.jsonl`. One file is one
session. The first row is `type=session` (id, cwd, version) or a v4
`kind=header`. Later rows are a parent-linked entry tree: `message`
(roles `user`, `assistant`, `toolResult`, `bashExecution`,
`compactionSummary`, `branchSummary`, `custom`), `model_change`,
`thinking_level_change`, `compaction`, `branch_summary`,
`session_info`, and `custom_message`. Title is the last
`session_info.name`, else the first user text on the active leaf.
Model is the last `provider` / `modelId`. Thinking level is the last
`thinking_level_change`. Context tokens come from the last assistant
`usage.totalTokens`. List Turn is `running` when the last leaf
message is `toolResult` or an assistant `stopReason` of tool use; an
assistant `errorMessage` is `cancelled`. The timeline follows the
leaf `parentId` path (rewound branches stay off it). Diff is edit /
write tools on the timeline. A ``subagent`` tool with ``tasks[]``
emits one spawn and one finish bookend per task from
``details.results`` (no child jsonl; runs are listed and not
openable).

## How each store fills the surfaces

Same domain calls as the table above. A blank cell is unset (the
store did not write that product data).

| Id | Timeline | List Turn | Diff | Subagents | Tasks / Workflows / Goals / Plan / Context |
|----|----------|-----------|------|-----------|---------------------------------------------|
| `antigravity` | transcript.jsonl | `killed` / `not_fully_idle` / last row status | write / replace tools | `parent_conversation_id` children | Timeline bookends only |
| `claude` | user / assistant jsonl | assistant `stop_reason` | Edit / Write / StrReplace | Agent / Task bookends + `subagents/` files | Timeline bookends only |
| `copilot` | `events.jsonl` | last turn signal | write / replace tools | `subagent.started` / `completed` | Timeline bookends only |
| `codex` | rollout jsonl | `task_started` / `task_complete` / `turn_aborted` | `apply_patch` Begin Patch | `SubAgentActivity` | Timeline bookends only |
| `cursor` | agent-transcripts jsonl | `turn_ended` status | write / replace tools | — | Timeline bookends only |
| `gemini` | `$set` / `session_metadata` jsonl | tool `status` / last user | write / replace tools | `kind=subagent` files (off the list) | Timeline bookends only |
| `grok` | `updates.jsonl` | updates tail | `rewind_points.jsonl` or `search_replace` | `subagents/` + spawn bookends | Directory files + timeline (`terminal/`, `workflows/wf_*`, `goal/state.json`, `plan.json`, `signals.json`) |
| `opencode` | `event` / `part` rows | last part `state.status` | `summary.diffs` or edit / write | `task` + `parentID` | Timeline bookends only |
| `pi` | leaf-path jsonl (`message`, compaction, thinking level) | `stopReason` / last `toolResult` / `errorMessage` | edit / write tools, one Diff point per turn | `subagent` tasks + `details.results` | Assistant `usage`, `thinking_level_change`, `compaction`, `session_info` |

## Filter

`harness:antigravity`, `harness:claude`, `harness:copilot`,
`harness:codex`, `harness:cursor`, `harness:gemini`, `harness:grok`,
`harness:opencode`, `harness:pi`.

## Keeping adapters current

A shipped adapter tracks the product we last parsed. When that
product moves, re-read **both** the live store on this machine and
the published parser or grammar, then bump `supported_version` in
the same change.

| Id | Product command | Published source |
|----|-----------------|------------------|
| `antigravity` | product about string | [`docs/harness-antigravity.md`](harness-antigravity.md) |
| `claude` | `claude --version` | [`docs/harness-claude.md`](harness-claude.md) — jsonl `user` / `assistant` + `parentUuid` |
| `copilot` | `copilot --version` | [`docs/harness-copilot.md`](harness-copilot.md) |
| `codex` | `codex --version` | [`docs/harness-codex.md`](harness-codex.md) |
| `cursor` | `cursor-agent --version` | [`docs/harness-cursor.md`](harness-cursor.md) |
| `gemini` | `gemini --version` | [`docs/harness-gemini.md`](harness-gemini.md) |
| `grok` | `grok --version` | Session directory + `updates.jsonl` |
| `opencode` | `opencode --version` | [`docs/harness-opencode.md`](harness-opencode.md) |
| `pi` | `pi --version` | [`docs/harness-pi.md`](harness-pi.md) — `session-manager.d.ts` `SessionEntry` + on-disk `~/.pi/agent/sessions/**/*.jsonl` |

Probe first: `just harness-probe`. Then extend `parse_timeline` /
`load_meta` for any new key in the same commit as the version bump.
