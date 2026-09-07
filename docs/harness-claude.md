# Claude Code session format

This is the anqa parse contract for the Claude Code store
(`anqa/harness/claude.py`, `core/src/stores/claude.rs`). Operator
surfaces live in [`harness-adapters.md`](harness-adapters.md#claude--claude-code).
When Claude Code ships a new session shape, update **this file and the
parser in the same change** as `supported_version`.

Pin: adapter `supported_version` (last parsed product, today **2.1.251**).
That string is the product version written on each jsonl row
(`version`) and mapped to `SessionMeta.harness_version`.

## Published source

Re-read these before bumping the pin. Disk keys and this grammar must
both match what we parse.

| Piece | Where |
|-------|--------|
| Session jsonl types | [Claude Code docs](https://docs.anthropic.com/en/docs/claude-code) plus live `~/.claude/projects/**/*.jsonl` |
| Message + tool blocks | Row `message.content[]` (`text`, `thinking`, `tool_use`, `tool_result`) |
| Built-in tools | `Read` / `Edit` / `Write` / `StrReplace` / `Bash` / `Grep` / `Glob` (arg keys `file_path`, `old_string`, `new_string`, `content`, `command`) |

Live files: `~/.claude/projects/<cwd-encoded>/<uuid>.jsonl`. Children:
`<uuid>/subagents/agent-<id>.jsonl`. Probe with `just harness-probe`
(types and roles only; no session text).

## File

One jsonl file is one parent session. Discover requires a first row
with `sessionId` and a Claude type (not a Pi `type=session` header).

Later rows are a **parent-linked tree** (`uuid` / `parentUuid`). The
timeline and list-turn follow the **leaf path**: last `user` /
`assistant` with a `uuid`, then walk `parentUuid` to root. Rewound
siblings stay off the timeline. A file with no `parentUuid` values is
read in file order (legacy / synthesized fixtures).

Chrome rows (`progress`, `file-history-snapshot`, `queue-operation`,
`system`, `mode`, `cost-state`, `permission-mode`, `last-prompt`,
`atis-latch`, `ai-title`) are not on the path.

`load_meta` reads a 64 KiB tail window (title, last turn, model).
`load_detail` and `parse_timeline` read the full file. Overview and
the browser call `load_detail`.

## Entry types → anqa

| Claude `type` | Anqa |
|---------------|------|
| `user` (text) | `turn_started` + `user_message_chunk` |
| `user` (only `tool_result` blocks) | `tool_call_update` (or `subagent_finished` when `toolUseResult.agentId` is set) |
| `assistant` `thinking` | `agent_thought_chunk` |
| `assistant` `text` | `agent_message_chunk` |
| `assistant` `tool_use` | `tool_call` (native name: `Edit`, `Write`, `Bash`, …) |
| `Agent` / `Task` tool | `subagent_spawned` / `subagent_finished` |
| `ai-title` | last `aiTitle` → title |
| other chrome | ignored |

Unknown `type` values are skipped.

## Usage

Last assistant `message.usage` on the leaf path:

`context_tokens_used` = `input_tokens` + `cache_read_input_tokens` +
`output_tokens` as the store wrote them. Cache-creation tokens are
not added. Percent / window stay unset.

## Tools

Cards keep Claude names. `Edit` / `StrReplace` use `file_path` /
`old_string` / `new_string`. `Write` uses `file_path` / `content`.
`Read` uses `file_path`. Diff lists one `Turn N` point per numbered
user turn that edited a file.

## When the product moves

1. `just harness-probe` (types and `usage` keys only).
2. Re-read this file and one live session.
3. Extend the rust parser and this contract in the same change as
   `supported_version`.
