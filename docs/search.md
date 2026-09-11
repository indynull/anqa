# Search

Session list, Timeline, and Turns search use the same boolean operators.
Bare words are ANDed. Last-token hints appear under the box on the
[terminal app](../README.md#first-path) and [desktop palette](../README.md#desktop-palette).
Press `?` on the session list for the in-app legend.

Implementation: `anqa/control/contract.py` (`catalogQuery` in the control
schema and `desktop/assets/catalog-query.json`).

## Session list

Bare words match title, id, and label. Space is AND. `AND`, `OR`, and `NOT` must be that spelling.

| Token | Meaning |
|--------|------|
| is: | running, awaiting, ending, complete, cancelled, idle |
| has: | workflow, note, goal, plan, subagent, task, job, schedule, error, failure, diff, compaction, doom, git, context |
|  | has:workflow workflows:>=N, has:note notes:>=N, has:goal goals:>=N, has:plan plans:>=N, has:subagent subagents:>=N, has:task tasks:>=N, has:job jobs:>=N, has:schedule schedules:>=N, has:error errors:>=N, has:failure failures:>=N, has:diff diff:>=N, has:compaction compaction:>=N, has:doom doom:>=N |
| in: | Directory the session was run in |
| harness: | grok, opencode, pi, claude, gemini, antigravity, copilot, codex, cursor |
| model: | Model id substring |
| task: | Task id substring |
| tag: | Operator tag. `tag:review,ui` is both (comma is AND) |
| after: | updatedAt on or after this time (ISO, yesterday, 2d, 2 days ago) |
| before: | updatedAt on or before this time (ISO, yesterday, 2d, 2 days ago) |
| workflows: notes: goals: plans: subagents: tasks: jobs: schedules: errors: failures: turns: tools: events: duration: diff: compaction: doom: | >=  <=  >  <  = |
| AND  OR  NOT  -  (  ) |  |

## Timeline

Bare words match type, tool, and body. Space is AND. `AND`, `OR`, and `NOT` must be that spelling.

| Token | Meaning |
|--------|------|
| is: | tool, user, assistant, error, session, subagent, background, workflow |
| has: | error |
| tool: | Tool name substring |
| user: | User-message text substring |
| turn: errors: duration: | >=  <=  >  <  = |
| AND  OR  NOT  -  (  ) |  |

## Turns

Bare words match the turn label and prompt. Space is AND. `AND`, `OR`, and `NOT` must be that spelling.

| Token | Meaning |
|--------|------|
| has: | error, subagent |
| errors: tools: events: duration: subagents: | >=  <=  >  <  = |
| AND  OR  NOT  -  (  ) |  |

## Examples

| Query | Meaning |
|--------|------|
| `has:note AND is:awaiting` | Waiting on a reply, and you already wrote notes |
| `is:complete AND NOT has:note` | Finished sessions you have not written up |
| `has:error OR has:failure` | Tool errors or a failed child |
| `workflows:>=2 AND NOT is:complete` | Multi-workflow sessions still going |
| `errors:>=5 AND NOT has:note` | Noisy sessions you have not written up |
| `notes:>=2 AND after:yesterday` | Recently updated, more than one note |
| `has:subagent OR has:workflow` | Spawned a child or a workflow |
| `in:~/src/app AND after:yesterday` | This repo, updated since yesterday |
