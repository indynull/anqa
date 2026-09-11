# Pi session format

This is the anqa parse contract for the Pi store (`anqa/harness/pi.py`,
`core/src/stores/pi.rs`). Operator surfaces live in
[`harness-adapters.md`](harness-adapters.md#pi--pi). When Pi ships a new
session shape, update **this file and the parser in the same change** as
`supported_version`.

Pin: adapter `supported_version` (last parsed product, today **0.85.1**).
That is not the jsonl header `version` (schema 3) and is not written to
`SessionMeta.harness_version` — Pi does not store a product version on
the session.

## Published source

Re-read these before bumping the pin. Disk keys and this grammar must
both match what we parse.

| Piece | Where |
|-------|--------|
| On-disk v3 header + `SessionEntry` union | `@earendil-works/pi-coding-agent` `dist/core/session-manager.d.ts` (`SessionHeader`, `SessionEntry`, `CURRENT_SESSION_VERSION`) |
| Message roles + tool blocks | same package `dist/core/messages.d.ts`; `@earendil-works/pi-ai` `ImageContent` / `ToolCall` / `Usage` |
| Built-in tools | `dist/core/tools/{read,edit,write,bash,grep,find,ls}.d.ts` |
| v4 header / lane records | `@earendil-works/pi-agent-core` `dist/harness/session/types.d.ts`, `jsonl/types.d.ts` (`JsonlV4Header`, `LaneRecord`) |

Live files: `~/.pi/agent/sessions/<cwd-encoded>/*.jsonl`. Probe with
`just harness-probe` (types and roles only; no session text).

## File

One jsonl file is one session. Filename
`YYYY-MM-DDTHH-mm-ss-mmmZ_<session-id>.jsonl`. Discover requires a
header row.

| Header | Keys we read |
|--------|----------------|
| v3 `type=session` | `id`, `cwd`, `timestamp` (ISO). `version` is the **schema**, ignored for `harness_version`. Optional `parentSession` is not linked in the catalog. |
| v4 `kind=header` | `id`, `cwd`, `createdAt` (epoch ms). Discover and bind accept this; live 0.85.1 still writes v3. |

Later rows are a **parent-linked tree** (`id` / `parentId`). The
timeline and list-turn follow the **leaf path**: last branch entry,
then walk `parentId` to root. Rewound siblings stay off the timeline.
A file with no `parentId` values is read in file order (legacy /
synthesized fixtures).

Branch entries (eligible as leaf / parent): `message`,
`model_change`, `thinking_level_change`, `compaction`,
`branch_summary`, `custom`, `custom_message`, `label`,
`session_info`, `active_tools_change`. Top-level `usage` and other
lane records are not on the path.

`load_meta` reads the header plus a 64 KiB tail window (title, last
turn, last model / thinking / usage in that window). `load_detail`
and `parse_timeline` read the full file. Overview and the browser
call `load_detail`.

## Entry types → anqa

| Pi `type` | Anqa |
|-----------|------|
| `session` / `kind=header` | meta only (`session_id`, `run_dir`, `created_at`) |
| `model_change` | last `provider`/`modelId` → `model_id`; timeline `current_mode_update` |
| `thinking_level_change` | last `thinkingLevel` → `reasoning_effort`; timeline `current_mode_update` (`thinking <level>`) |
| `session_info` | last `name` → title (else first leaf user text) |
| `compaction` | `compaction_checkpoint` (`tokensBefore`, `summary`); increments `compaction_count` |
| `branch_summary` | `session_recap` (`summary`) |
| `custom_message` | `user_message_chunk` when `display` is true |
| `custom` | ignored (extension state; not LLM context) |
| `label` | ignored |
| `active_tools_change` | ignored |
| `usage` (top-level) | context-token fallback only (`totalTokens` or `inputTokens+outputTokens`) |
| `message` | see roles below |

Unknown `type` values are skipped. A new discriminant in
`SessionEntry` is a parser change, not a silent drop you leave
undocumented.

## Message roles → anqa

| `message.role` | Anqa |
|----------------|------|
| `user` | `turn_started` + `user_message_chunk`. `content[]` `text` is the body; `type=image` `data` (base64) becomes `TraceEvent.images`. |
| `assistant` | `thinking` → `agent_thought_chunk` (empty thinking omitted); `text` → `agent_message_chunk`; `toolCall` → `tool_call` (and subagent bookends). `errorMessage` → `session_error` and list Turn `cancelled`. |
| `toolResult` | `tool_call_update` (`toolName`, `toolCallId`, `content`, `isError`). `toolName=subagent` → `subagent_finished` from `details.results[]`. |
| `bashExecution` | `tool_call`/`tool_call_update` named `bash` (`command`, `output`, `exitCode`, `cancelled`) |
| `compactionSummary` | `compaction_checkpoint` |
| `branchSummary` | `session_recap` |
| `custom` | `user_message_chunk` when `display` is true |

Assistant `stopReason`: `toolUse` → list Turn `running`; `stop` /
`end_turn` → `complete`; `error` / `aborted` → `cancelled`. A last
leaf `toolResult` is `running`. A last leaf `user` is `—` (idle).

Assistant `usage` (`input`, `output`, `cacheRead`, `cacheWrite`,
`reasoning`, `totalTokens`, `cost`) fills `context_tokens_used` from
`totalTokens` (else input+output+cacheRead). Pi does not write a
context window size; percent / window stay unset.

Timestamps: prefer `message.timestamp` (ms) or the row ISO
`timestamp`. `text::epoch` accepts both.

## Tools

Tool name is `toolCall.name`. Arguments are `toolCall.arguments` and
become `TraceEvent.raw_input`. Detail / Diff / HUD inspect:

| Name | Arguments | Surface |
|------|-----------|---------|
| `read` | `path`, optional `offset` / `limit` | read card |
| `edit` | `path`, `edits[{oldText,newText}]` | old/new card; Diff |
| `write` | `path`, `content` | write card; Diff |
| `bash` | `command`, optional `timeout` | shell card |
| `grep` | `pattern`, optional `path` / `glob` / `limit` | grep card |
| `find` | `pattern`, optional `path` / `limit` | read-family |
| `ls` | optional `path` / `limit` | directory card |
| `subagent` | `tasks[{agent,task,cwd}]`; result `details.results[]` | spawn/finish bookends; not openable (no child jsonl) |

Grok ids (`read_file`, `search_replace`, `run_terminal_command`) stay
valid for other stores. Do not translate Pi names into those ids.

Diff is one point per leaf turn that called `edit` / `write` (picker
label `Turn N`, `promptIndex` = that turn). There are no rewind
snapshots.

## Meta we do not invent

Unset unless the jsonl wrote it: context window size / percent,
product `harness_version`, goals, plan, jobs, workflows, rewind
snapshots. Diff is edit/write tool calls only.

## When Pi moves

1. `pi --version` and `just harness-probe`. Probe must list every
   live top-level `type` and `message.role` (and assistant content
   block types). A type or role that is not in the tables above is
   the work.
2. Diff `SessionEntry` / `SessionHeader` / tool schemas against this
   file. New union members, new roles, or new tool argument keys
   belong in the parser **and** this file in the same commit as the
   `supported_version` bump.
3. Extend `tests/session/test_harness_pi.py` (synthesized jsonl, no
   real session text). Keep `test_harness_contract.py` green.
4. One path. Do not keep a v3-only parser “just in case” once v4 is
   what the product writes — replace the header reader.

`.grok/skills/harness-adapter-qa/SKILL.md` is the gate.
