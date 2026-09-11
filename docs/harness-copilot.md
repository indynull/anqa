# GitHub Copilot CLI session format

This is the anqa parse contract for the Copilot store
(`anqa/harness/copilot.py`, `core/src/stores/copilot.rs`). Operator
surfaces live in [`harness-adapters.md`](harness-adapters.md#copilot--github-copilot-cli).
When Copilot CLI ships a new session shape, update **this file and the
parser in the same change** as `supported_version`.

Pin: adapter `supported_version` (last parsed product, today **1.0.83**).

## Published source

| Piece | Where |
|-------|--------|
| Catalog | `~/.copilot/session-store.db` (`sessions` table) |
| Events | `~/.copilot/session-state/<id>/events.jsonl` |

Probe with `just harness-probe`. This host had the root and no jsonl
sample.

## File

The sqlite catalog lists sessions (id, cwd, repository, branch,
summary, timestamps). The timeline is the events jsonl. `load_meta`
is cheap. `load_detail` aliases `load_meta` until a usage object is
on disk.

## Entry types → anqa

| Copilot | Anqa |
|---------|------|
| `user.message` | `user_message_chunk` |
| `assistant.message` | `agent_message_chunk` |
| `tool.execution_start` / result | `tool_call` / `tool_call_update` |
| `subagent.started` / `subagent.completed` | spawn / finish bookends |
| `assistant.turn_start` / `assistant.turn_end` | list turn |
| `session.shutdown` | complete |

## Usage

No usage object in the probe sample. `context_tokens_used` stays
unset.

## Tools

Diff is write / replace tools on the timeline.

## When the product moves

`just harness-probe`, then extend the rust parser in the same change
as `supported_version`.
