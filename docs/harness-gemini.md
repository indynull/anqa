# Gemini CLI session format

This is the anqa parse contract for the Gemini CLI store
(`anqa/harness/gemini.py`, `core/src/stores/gemini.rs`). Operator
surfaces live in [`harness-adapters.md`](harness-adapters.md#gemini--gemini-cli).
When Gemini CLI ships a new session shape, update **this file and the
parser in the same change** as `supported_version`.

Pin: adapter `supported_version` (last parsed product, today **0.59.0**).
Gemini session files on this machine did not write a product version
onto the row; `harness_version` stays empty.

## Published source

| Piece | Where |
|-------|--------|
| Chat recording | [chatRecordingService.ts](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts) |
| Live files | `~/.gemini/tmp/<project>/chats/session-*.jsonl` |

Probe with `just harness-probe` (types only; no session text).

## File

A conversation is a header (`sessionId` + `projectHash`, or
`type=session_metadata`) plus `$set` patches, optional `$rewindTo`,
appended `user` / `gemini` / `error` messages, and `message_update`
merges. `kind=subagent` files stay off the home list. Bootstrap
dumps whose only user text is `<session_context>` are not list rows.

`load_meta` is cheap (header + tail). `load_detail` aliases that
until the store writes a usage object this machine can probe.

## Entry types → anqa

| Gemini | Anqa |
|--------|------|
| user text | `turn_started` + `user_message_chunk` |
| gemini text | `agent_message_chunk` |
| tool call / result | `tool_call` / `tool_call_update` |
| `error` | error event |
| `$rewindTo` | not a doubled transcript (file order after the rewind) |

## Usage

No `usage` / `usageMetadata` / token object on the live 0.59 files
probed here. `context_tokens_used` stays unset. Percent / window
stay unset.

## Tools

Diff is write / replace tools (`run_shell_command` is not a file
edit). Cards keep Gemini tool names.

## When the product moves

`just harness-probe`. If a token object appears, fill
`load_detail` from those keys in the same change as
`supported_version`.
