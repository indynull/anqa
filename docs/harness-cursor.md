# Cursor session format

This is the anqa parse contract for the Cursor store
(`anqa/harness/cursor.py`, `core/src/stores/cursor.rs`). Operator
surfaces live in [`harness-adapters.md`](harness-adapters.md#cursor--cursor).
When Cursor ships a new session shape, update **this file and the
parser in the same change** as `supported_version`.

Pin: adapter `supported_version` (last parsed product, today
**2026.08.25-3e8eec8**).

## Published source

| Piece | Where |
|-------|--------|
| Transcript | `~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl` |
| Chat meta | `~/.cursor/chats/*/<id>/meta.json` (title, cwd, timestamps) |
| Model | last `modelName` in `chats/*/<id>/store.db` blobs |

Probe with `just harness-probe` (types only; no session text). This
host had a Cursor root and no jsonl sample.

## File

One transcript file is one session. Meta.json supplies title and cwd
when present. `load_meta` is cheap. `load_detail` aliases `load_meta`
until a usage object is on disk.

## Entry types → anqa

| Cursor | Anqa |
|--------|------|
| `role=user` | `user_message_chunk` |
| `role=assistant` text | `agent_message_chunk` |
| `tool_use` parts | `tool_call` |
| `type=turn_ended` | list turn from that status |

## Usage

No usage object in the probe sample. `context_tokens_used` stays
unset.

## Tools

Diff is write / replace tools on the timeline. Cards keep Cursor
tool names.

## When the product moves

`just harness-probe`, then extend the rust parser in the same change
as `supported_version`.
