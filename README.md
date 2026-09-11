<p align="center">
  <img src="brand/png/anqa-lockup-stacked.png#gh-light-mode-only" alt="anqa" height="200" />
  <img src="brand/png/anqa-lockup-stacked-on-dark.png#gh-dark-mode-only" alt="anqa" height="200" />
</p>

<p align="center">
  <a href="https://pypi.org/project/anqa/"><img src="https://img.shields.io/pypi/v/anqa" alt="PyPI" /></a>
  <a href="https://github.com/indynull/anqa/actions/workflows/ci.yml"><img src="https://github.com/indynull/anqa/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://codecov.io/gh/indynull/anqa"><img src="https://codecov.io/gh/indynull/anqa/graph/badge.svg" alt="Codecov" /></a>
  <a href="https://indynull.github.io/anqa/"><img src="https://img.shields.io/badge/docs-pages-0A66C2" alt="Docs" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.13%2B-3776AB" alt="Python 3.13+" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT" /></a>
</p>

Anqa reviews coding-agent sessions that are already on this machine.
It lists every shipped store (Grok Build, Claude Code, Codex, Cursor,
Gemini CLI, OpenCode, Copilot, Pi, Antigravity), opens one session, and
gives you the timeline, the turn list, the workspace diff, background
work, child runs, and a place to write notes. Search is a query
language on the catalog, the timeline, and turns — not a linear scan
of whatever is on screen.

Four clients share one owner process (`anqad`) and the same catalog.

| Client | Start | Job |
|--------|-------|-----|
| Terminal app | `anqa` | Catalog, full browser, export, session delete |
| Desktop palette | `anqa desktop` | Summonable overlay: Recent, then Overview / Turns / Timeline / Diff / Notes |
| Emacs | `(load … "anqa editor emacs-path")` | Org outline of turns and notes; expand a turn for the transcript |
| Neovim 0.9+ | `require("anqa").setup()` | Markdown buffer on the same socket |

<a id="terminal-app"></a>

## First path

```bash
uv tool install anqa
anqa
```

`/` searches the list. Enter opens a session. `1`–`4` are Timeline,
Summary, Diff, Notes. `N` on Notes writes a note. `E` exports a
bundle. `q` leaves the terminal app; `anqad` keeps running.

```bash
anqa desktop              # palette (starts anqad if the socket is free)
anqa desktop --toggle     # show or hide; bind this on Wayland
anqa desktop --open ID    # show the palette on that session
```

From a clone (needs Rust): `uv tool install --editable .`

## A session

The terminal browser and the desktop palette show the same body.
What each pane can fill depends on what that store wrote.

**Catalog.** Newest activity first. Title, product, model, turn
status (`running`, `awaiting`, `ending`, `complete`, `cancelled`,
or `—` when the last row is a user message or a bookend), event
count, and a context meter when the store exported one. Operator
tags are yours. Subagent directories stay off this list; open them
from the parent.

**Timeline.** Events in order: user and assistant messages, tool
calls, session markers, children, background work, workflows,
errors. Filter (`v` in the terminal). `h` / `l` step turns. `/`
searches the whole session (`tool:read_file`, `turn:>300`,
`is:error`). Enter opens an event. On a spawn or finish bookend,
Enter opens the child. Tail follows a live session.

**Summary / Overview.** Session glance, then Tasks (shells,
monitors, schedules), Workflows, Subagents, and Stats. Enter a
child or a bookend to inspect the run (Asked / Happened / Failed)
or open that session. Esc returns.

**Turns** (desktop). One row per owner turn. `g` jumps to that turn
on Timeline. Search takes `has:error`, `tools:>=5`, `duration:>1m`.

**Diff.** Rewind snapshots when the store wrote them, otherwise
patches rebuilt from write and edit tool calls (Codex
`apply_patch` included). Prompt and Assistant tabs sit above a
files and hunk split. `/` finds a path or hunk text.

**Notes.** Operator notes on a turn, with a configurable field
schema (`~/.anqa/notes_schema.toml`). `N` creates, Enter edits,
double-press `x` deletes that note. Export includes them.

Live sessions update the list when the store changes. The open
palette re-reads overview about every three seconds while it is
on screen.

## Search

`/` on the session list. Bare words match title, id, and label.
Space is AND. `AND`, `OR`, and `NOT` must be that spelling. Tab
completes the last token. The list waits 0.28s after the last
key so each keystroke does not walk the catalog.

| Query | Meaning |
|-------|---------|
| `is:complete AND NOT has:note` | Finished sessions you have not written up |
| `has:note AND is:awaiting` | Waiting on a reply, and you already wrote notes |
| `has:error OR has:failure` | Tool errors or a failed child |
| `workflows:>=2 AND NOT is:complete` | Multi-workflow sessions still going |
| `in:~/src/app AND after:yesterday` | This repo, updated since yesterday |
| `harness:grok tag:review` | One store, tagged |

`is:` running, awaiting, ending, complete, cancelled, idle, host,
import. `has:` workflow, note, goal, plan, subagent, task, job,
schedule, error, failure, diff, git, context, compaction, doom.
Counts use the written pair (`plans:>=2`, `errors:>=5`). Also
`in:`, `model:`, `task:`, `tag:`, `after:`, `before:`,
`duration:`. `tag:review,ui` is both.

Timeline and Turns use the same operators. Tokens and more
examples: [`docs/search.md`](docs/search.md).

## Import and export

`E` on the terminal list or browser writes a session bundle under
`~/.anqa/reports/` (or the profile in `export.default_profile`).
A parent bundle includes each openable child. The palette has no
export.

`Ctrl+O` on the session list (and `anqa import PATH`) unpacks a
harness archive or an anqa export into `~/.anqa/imports/<harness>/`
and lists it with `is:import`. The terminal app browses the
filesystem; the palette uses the host picker and accepts a dropped
file.

## Keys

The footer lists the keys this screen can run. `?` is the full
list. Shared actions use the same chord on the terminal and the
palette.

| Key | Where | Action |
|-----|-------|--------|
| `/` | list, browser, palette | Search |
| j / k | lists | Down / up |
| Enter | lists | Open |
| Esc | everywhere | Back or dismiss |
| y / Ctrl+Shift+C | browser | Copy the selection or the focused body |
| N | Notes | New note (palette `N` opens the Notes pane) |
| x | list | Delete selected sessions (press twice) |
| x | Notes | Delete the focused note (press twice) |
| x | Timeline / Summary / Diff | Delete this session (press twice) |
| [ ]  1–4 | terminal browser | Timeline, Summary, Diff, Notes |
| Ctrl+Tab / Ctrl+1–5 | palette | Overview, Turns, Timeline, Diff, Notes |
| h / l | Timeline | Previous / next turn |
| v | Timeline | Filter |
| E | terminal | Export |
| Ctrl+O | list | Import |
| t | list or open session | Tag |
| Ctrl+P | terminal | Command palette |
| F5 / Ctrl+R | terminal | Refresh |
| q | terminal | Quit (`anqad` stays up) |
| u | palette | Leave the session for the list |
| g | palette Turns | Timeline for that turn |
| [ / ] | palette Timeline | All turns / next Filter hit |

On Timeline, drag the list/detail divider to resize the panes.

## Supported harnesses

A harness is a coding-agent product whose sessions anqa lists and
opens. Filter with `harness:<id>`. List Turn, Diff, children, and
the context meter come from that store. Missing product data stays
unset. Per-store surfaces:
[`docs/harness-adapters.md`](docs/harness-adapters.md#session-surfaces).

| Id | Product | Tested | Store |
|----|---------|--------|--------|
| `antigravity` | [Antigravity](https://antigravity.google/docs/cli/overview) | 1.1.22 | `~/.gemini/antigravity-cli/conversations/<uuid>.db` plus `brain/<uuid>/…/transcript.jsonl` |
| `claude` | [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | 2.1.268 | `~/.claude/projects/<cwd>/<uuid>.jsonl` (children in `<uuid>/subagents/`) |
| `copilot` | [GitHub Copilot](https://docs.github.com/en/copilot) | 1.0.83 | `~/.copilot/session-store.db` plus `session-state/<id>/events.jsonl` |
| `codex` | [Codex](https://github.com/openai/codex) | 0.154.0 | `~/.codex/sessions/**/rollout-*.jsonl` |
| `cursor` | [Cursor](https://cursor.com) | 2026.09.10-fd3934a | `~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl` plus `chats/*/<id>/meta.json` |
| `gemini` | [Gemini CLI](https://github.com/google-gemini/gemini-cli) | 0.59.0 | `~/.gemini/tmp/<project-hash>/chats/session-*.jsonl` |
| `grok` | [Grok Build](https://docs.x.ai/build/overview) | 1.0.25 | `~/.grok/sessions/<cwd>/<id>/` |
| `opencode` | [OpenCode](https://opencode.ai) | 1.18.30 | `~/.local/share/opencode/opencode.db` |
| `pi` | [Pi](https://pi.dev) | 0.85.1 | `~/.pi/agent/sessions/**/*.jsonl` |

Tested is the product version we last parsed. `[catalog] ignore`
drops a store. `[catalog.roots]` overrides a path.

<a id="desktop-hud"></a>

## Desktop palette

Summonable overlay. Idle list is Recent (scroll or `j` for more).
`/` searches the whole catalog. Open a session for Overview, Turns,
Timeline, Diff, and Notes. Same notes schema as the terminal app.
More: [`desktop/README.md`](desktop/README.md).

```bash
anqa desktop
anqa desktop --toggle
anqa desktop --open <session-id>
anqa desktop --install-desktop
```

Default hotkey **Cmd+Shift+A** (macOS) / **Ctrl+Shift+A** (Windows
and X11). Override with `hud.global_shortcut` or
`ANQA_HUD_SHORTCUT`. On Wayland bind `anqa desktop --toggle` so the
compositor forwards an activation token. Tray **Quit anqa** exits
the palette only. Clicking a desktop notification opens that
session.

## Control

`anqad` owns the per-user Unix socket. The four clients attach.
Bare `anqa` and `anqa desktop` detach-start it when the socket is
free (`--no-anqad` attaches only). Quitting a client leaves `anqad`
running. Session delete from the list or from Timeline / Summary /
Diff goes through that owner so every client drops the row.

```bash
anqad -d
anqad status
anqad stop
anqa doctor
anqa import PATH
anqa export-host -o host-catalog.json
anqa keys
anqa config validate
```

Methods and notifications: [`docs/control.md`](docs/control.md).

## Config

Prefs are `~/.anqa/config.toml`. Missing keys use defaults. Schema:
[config](https://indynull.github.io/anqa/schemas/config.schema.json).
Copy [`examples/config/config.toml`](examples/config/config.toml).

```toml
#:schema https://indynull.github.io/anqa/schemas/config.schema.json

theme = "auto"
auto_anqad = true

[hud]
window_mode = false
global_shortcut = ""
desktop_notifications = true

[catalog]
# ignore = ["pi"]
# [catalog.roots]
# grok = "~/.grok/sessions"
```

`theme = "auto"` follows the host (terminal ANSI, desktop
light/dark). A named pair (`gruvbox`) follows the desktop member.
An unpaired name pins both clients. User themes go in
`~/.anqa/themes/`.

Key remaps: `~/.anqa/keys.toml`. Esc, Enter, Tab, Shift+Tab, and
`?` are not remappable. Copy
[`examples/keys/colemak.toml`](examples/keys/colemak.toml) for
home-row list motion. `anqa keys` prints the resolved table.

Notes fields: copy
[`examples/notes/notes_schema.example.toml`](examples/notes/notes_schema.example.toml)
to `~/.anqa/notes_schema.toml`.

## Paths

| Root | Default | Holds |
|------|---------|--------|
| Config home | `~/.anqa` | `config.toml`, `keys.toml`, notes, reports, themes |
| Catalog | each enabled adapter store | listed sessions |
| Notes | `~/.anqa/notes/<harness>/<session_id>/` | operator notes |
| Import store | `~/.anqa/imports/<harness>/` | archives opened with `Ctrl+O` |
| Reports | `~/.anqa/reports/` | `E` export bundles |

## Emacs

```elisp
(load (string-trim (shell-command-to-string "anqa editor emacs-path")))
```

Opens a session as Org (turns and notes). `C-c C-e` loads that
prompt's transcript. Starts `anqad -d` when the socket is missing.

## Neovim (0.9+)

```lua
vim.opt.rtp:prepend(vim.fn.trim(vim.fn.system({ "anqa", "editor", "vim-path" })))
require("anqa").setup()
```

Opens a session as Markdown. Start `anqad` (or `anqa`) so the
socket exists.

## Examples and development

Packs under [`examples/`](examples/README.md) copy into `~/.anqa/`.
Not auto-loaded.

```bash
just install
just lint
just test
just ci
just harness-probe
```
