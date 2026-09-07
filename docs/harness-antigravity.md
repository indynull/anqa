# Antigravity session format

This is the anqa parse contract for the Antigravity store
(`anqa/harness/antigravity.py`, `core/src/stores/antigravity.rs`).
Operator surfaces live in
[`harness-adapters.md`](harness-adapters.md#antigravity--antigravity).
When Antigravity ships a new session shape, update **this file and
the parser in the same change** as `supported_version`.

Pin: adapter `supported_version` (last parsed product, today **1.1.22**).
This host has no mapped CLI command; the pin is the last parsed
product about string.

## Published source

| Piece | Where |
|-------|--------|
| Conversation | `~/.gemini/antigravity-cli/conversations/<uuid>.db` (`trajectory_meta`) |
| Transcript | `brain/<uuid>/.system_generated/logs/transcript.jsonl` (or `transcript_full.jsonl`) |
| Summaries | `conversation_summaries.db` |

Probe with `just harness-probe` (types only).

## File

Discover skips rows with `parent_conversation_id`. Title, idle
flags, and children come from the summaries db. Model is
`agent_name` or a `gemini-*` id in the conversation blobs. Working
directory comes from `workspace_uris` or
`cache/last_conversations.json`.

`load_meta` is cheap. `load_detail` aliases `load_meta` until a
usage object is on disk.

## Entry types → anqa

| Antigravity | Anqa |
|-------------|------|
| `USER_INPUT` | `user_message_chunk` |
| `PLANNER_RESPONSE` | assistant / tool rows |
| `ERROR_MESSAGE` | error |
| `slash_command` | chrome / user as written |

## Usage

No usage object in the probe sample. `context_tokens_used` stays
unset.

## Tools

Diff is write / replace tools on the timeline.

## When the product moves

`just harness-probe`, then extend the rust parser in the same change
as `supported_version`.
