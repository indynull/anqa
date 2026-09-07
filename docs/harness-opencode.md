# OpenCode session format

This is the anqa parse contract for the OpenCode store
(`anqa/harness/opencode.py`, `core/src/stores/opencode.rs`). Operator
surfaces live in [`harness-adapters.md`](harness-adapters.md#opencode--opencode).
When OpenCode ships a new session shape, update **this file and the
parser in the same change** as `supported_version`.

Pin: adapter `supported_version` (last parsed product, today **1.18.29**).

## Published source

| Piece | Where |
|-------|--------|
| Server / session | [OpenCode server](https://opencode.ai/docs/server/) |
| Live db | `~/.local/share/opencode/opencode.db` |

Probe with `just harness-probe` (table names and event type counts
only).

## File

Live 1.18 sessions are `event` rows (`session.created.1`,
`session.updated.1`, `message.updated.1`, `message.part.updated.1`)
keyed by `aggregate_id`. The ingest reads that log when the session
has event rows. The `session` / `message` / `part` tables are the
archive shape (`E` writes that JSON) and are used when the session
has no events. Discover skips `parentID` / `parent_id` children.

`load_meta` is list-grade. `load_detail` already fills tokens when
the event store wrote them.

## Entry types → anqa

| OpenCode | Anqa |
|----------|------|
| user text part | `user_message_chunk` |
| assistant text | `agent_message_chunk` |
| reasoning | `agent_thought_chunk` |
| tool parts (`bash`, `edit`, `write`, `read`, …) | `tool_call` / update |
| `task` + parent child | subagent bookends |

## Usage

OpenCode already sets `context_tokens_used` from the store's token
fields when present. Percent / window stay unset unless both used
and window were written.

## Tools

Diff prefers last user `summary.diffs` (`file`, `patch`, `status`,
`additions`, `deletions`), else edit / write input (`filePath`,
`oldString`, `newString`, `content`). Cards keep OpenCode names.

## When the product moves

`just harness-probe`, then extend the rust parser in the same change
as `supported_version`.
