# Codex session format

This is the anqa parse contract for the Codex store
(`anqa/harness/codex.py`, `core/src/stores/codex.rs`). Operator
surfaces live in [`harness-adapters.md`](harness-adapters.md#codex--codex).
When Codex ships a new session shape, update **this file and the
parser in the same change** as `supported_version`.

Pin: adapter `supported_version` (last parsed product, today **0.154.0**).
That is the `cli_version` on `session_meta` when the store wrote it.

## Published source

| Piece | Where |
|-------|--------|
| Rollout jsonl | Live `~/.codex/sessions/**/rollout-*.jsonl` |
| Begin Patch | [apply-patch parser.rs](https://github.com/openai/codex/blob/main/codex-rs/apply-patch/src/parser.rs) |

Probe with `just harness-probe` (types only; no session text).

## File

One jsonl file is one session. The id is the UUID in the filename.
First useful row is `type=session_meta` (`id` / `session_id`, `cwd`,
`cli_version`). Later rows: `response_item`, `event_msg`,
`turn_context`, `world_state`.

`load_meta` reads a 64 KiB tail. `load_detail` and `parse_timeline`
read the full file.

## Entry types → anqa

| Codex | Anqa |
|-------|------|
| `response_item` message `role=user` | `user_message_chunk` (`<environment_context>` skipped). Stamps `turn_number` on the open `task_started` bookend, or emits numbered `turn_started` when there is no bookend. |
| `response_item` message `role=assistant` | `agent_message_chunk` |
| `custom_tool_call` / `function_call` | `tool_call` |
| `event_msg` `task_started` | `turn_started` (list idle; numbered when the next user lands) |
| `event_msg` `task_complete` | `turn_completed` |
| `event_msg` `turn_aborted` | `turn_ended` |
| `item_completed` `SubAgentActivity` | `subagent_spawned` / `subagent_finished` |

## Usage

Last `event_msg` `payload.info.last_token_usage.total_tokens` (else
`total_token_usage`). Percent / window stay unset.

## Tools

Diff is `apply_patch` in a tool or `exec` argument (Begin Patch
grammar). One `Turn N` point per numbered user turn.

## When the product moves

`just harness-probe`, re-read this file and one live rollout, then
extend the rust parser in the same change as `supported_version`.
