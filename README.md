<p align="center">
  <img src="brand/png/anqa-lockup-stacked.png#gh-light-mode-only" alt="anqa" height="200" />
  <img src="brand/png/anqa-lockup-stacked-on-dark.png#gh-dark-mode-only" alt="anqa" height="200" />
</p>

[![CI](https://github.com/indynull/anqa/actions/workflows/ci.yml/badge.svg)](https://github.com/indynull/anqa/actions/workflows/ci.yml)
[![Codecov](https://codecov.io/gh/indynull/anqa/graph/badge.svg)](https://codecov.io/gh/indynull/anqa)
[![Docs](https://img.shields.io/badge/docs-pages-0A66C2)](https://indynull.github.io/anqa/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**anqa** is a session review tool: timeline, notes, workspace diffs,
and a summonable desktop palette. It reads [supported
harnesses](#supported-harnesses) from their native stores.

Four clients talk to [`anqad`](#control).

| Client | What it does |
|--------|----------------|
| [Terminal app](#terminal-app) | Browse sessions, export |
| [Desktop HUD](#desktop-hud) | Summonable session palette |
| [Emacs](#emacs) | Org buffer |
| [Neovim](#neovim-09) | Markdown buffer |

## Install

```bash
uv tool install --editable .    # clone: anqa + anqad + anqa-hud on PATH (needs Rust)
anqa                          # terminal app
anqa desktop                  # desktop palette
```

```bash
uv tool install git+https://github.com/indynull/anqa
anqa
anqa desktop
uv tool upgrade anqa
```

```bash
uv tool install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ anqa
anqa --version
```

Wheels for Linux, macOS, and Windows (Intel and ARM) are on
[TestPyPI](https://test.pypi.org/project/anqa/).

## Paths

| Root | Default | Holds |
|------|---------|--------|
| Config home | `~/.anqa` | `config.toml`, optional `keys.toml`, notes, reports |
| Catalog | each enabled adapter store | listed sessions (`[catalog.roots]` can override a path) |

```bash
anqa                      # catalog: every shipped store
```

`~/.anqa/config.toml` is the only prefs file (terminal app and desktop HUD).
Missing keys use defaults. Saves keep comments on keys they do not change.
Schema: [config](https://indynull.github.io/anqa/schemas/config.schema.json)
(`anqa config validate`, `just schema`). Copy
[`examples/config/config.toml`](examples/config/config.toml).

```toml
#:schema https://indynull.github.io/anqa/schemas/config.schema.json

theme = "auto"
follow_os = false
auto_anqad = true
live_refresh_workers = 1

[hud]
window_mode = false
global_shortcut = ""
desktop_notifications = true

[export]
default_profile = ""
```

`theme = "auto"`: the terminal app follows the terminal (`COLORFGBG`,
then the desktop) and paints the host pair paper (`ansi-light` /
`ansi-dark`). The desktop palette follows the system light/dark pair
and, when the OS reports it, system paper and ink. Picking any member
of a named pair (`gruvbox` or `gruvbox-light`) stores the family and
sets `follow_os = true`; both clients apply the desktop member. An
unpaired name (`nord`) pins both clients. Aliases `anqa` and
`anqa-light` mean `auto`. Drop a TOML file in `~/.anqa/themes/`
(see [`examples/themes/`](examples/themes/)) and point `theme` at its
stem.

Key remaps stay in `keys.toml` (below), not in this file.

Optional key diffs: `~/.anqa/keys.toml` (`ANQA_KEYS` overrides the path).
A missing file keeps the catalog defaults. Esc, Enter, Tab, Shift+Tab, and
`?` are not remappable. The product default has no leader. An overlay may
set one printable leader (recommended Colemak: `;`) and bind `leader+X`
for one extra letter. Copy [`examples/keys/colemak.toml`](examples/keys/colemak.toml)
to `~/.anqa/keys.toml` for home-row `n`/`e` list motion (leader then
letter). The TUI and HUD both use the resolved map for footer,
help, and dispatch. The footer shows the leader while it is armed.

```bash
anqa keys              # resolved table (scope, id, chord, surface)
anqa keys --occupancy  # taken chords per scope
anqa keys --check      # exit 1 on overlay errors
```

## Supported harnesses

A harness is a coding-agent product whose sessions anqa lists and
opens. Run `anqa` and the home list is every shipped store. Filter
with `harness:<id>`. OpenCode, Copilot, and Antigravity keep
sessions in SQLite (plus a transcript where that product writes
one). Claude Code, Codex, Cursor, Gemini CLI, and Pi keep one
JSONL conversation per session. Grok Build keeps a session
directory. What a session can do (rewind, context meter, next
prompt) is whatever that store wrote.

| Id | Product | Tested | Store |
|----|---------|--------|--------|
| `antigravity` | [Antigravity](https://antigravity.google/docs/cli/overview) | 1.1.22 | `~/.gemini/antigravity-cli/conversations/<uuid>.db` plus `brain/<uuid>/…/transcript.jsonl` |
| `claude` | [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | 2.1.251 | `~/.claude/projects/<cwd>/<uuid>.jsonl` (children in `<uuid>/subagents/`) |
| `copilot` | [GitHub Copilot](https://docs.github.com/en/copilot) | 1.0.82 | `~/.copilot/session-store.db` plus `session-state/<id>/events.jsonl` |
| `codex` | [Codex](https://github.com/openai/codex) | 0.151.0 | `~/.codex/sessions/**/rollout-*.jsonl` (`apply_patch` Begin Patch grammar) |
| `cursor` | [Cursor](https://cursor.com) | 2026.08.25-3e8eec8 | `~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl` plus `chats/*/<id>/meta.json` |
| `gemini` | [Gemini CLI](https://github.com/google-gemini/gemini-cli) | 0.57.0 | `~/.gemini/tmp/<project-hash>/chats/session-*.jsonl` (`$set` / `session_metadata`) |
| `grok` | [Grok Build](https://docs.x.ai/build/overview) | 1.0.5 | `~/.grok/sessions/<cwd>/<id>/` (`updates.jsonl`, `rewind_points.jsonl`, `signals.json`) |
| `opencode` | [OpenCode](https://opencode.ai) | 1.18.25 | `~/.local/share/opencode/opencode.db` (`event` rows; `session` / `message` / `part` for archives) |
| `pi` | [Pi](https://pi.dev) | 0.84.4 | `~/.pi/agent/sessions/**/*.jsonl` (`type=session` header) |

Tested is the product version we last parsed. A session may carry a
different `harnessVersion` from its own files. Each product's record
types, list Turn, children, Diff, Tasks, Workflows, and the other
session surfaces:
[`docs/harness-adapters.md`](docs/harness-adapters.md#session-surfaces).

Notes for every store live under
`~/.anqa/notes/<harness>/<session_id>/`.

The home list and the session glance show the product name. The
catalog lists every shipped adapter. `[catalog] ignore` drops a
store. `[catalog.roots]` overrides a store's default location.

## Catalog

The list is every enabled adapter store. Filter with `harness:<id>`.
Every note has a `source`
(who wrote it). Control `notes/upsert` accepts any field bag plus that
source. A new note uses `~/.anqa/notes_schema.toml`. Editing a note
also shows extra stored fields as free-text. Notes, the edit form,
and HUD Notes show a source badge plus the stored fields. Subagent runs stay off the top
list; open them from the parent (Summary run table, or Timeline
Subagents filter — Enter, or click the tile in the desktop HUD). Esc
returns to that Timeline or Turns place. Background shells, monitors, and schedules live on Summary **Tasks**.
Workflows and subagent runs have their own Summary tabs. Timeline
filters Background / Workflows / Subagents list the bookends. Open a
row or a bookend to inspect the merged run (Asked / Happened / Failed).
Enter on a workflow child or subagent opens that child session. The
desktop Overview uses the same tabs.
Failed runs are listed on Summary.

## Terminal app

`anqa` (or `anqa tui`) is the session client: session list, browser
panes, notes, and export. Diff lists rewind turns
or approximate file edits from write tools, with Prompt and
Assistant tabs above a files and hunk split. `/` searches those
files; Enter and Shift+Enter step every matching line, and the
bar shows how many hits.
The footer lists the keys that apply now; `?` is the full list.

| Key | Where | Action |
|-----|-------|--------|
| Tab | everywhere | Next control |
| Shift+Tab | everywhere | Previous control |
| Arrows | everywhere | Move in a list |
| j / k | everywhere | Move down / up in a list |
| Enter | everywhere | Open or activate |
| Esc | everywhere | Back or close |
| ? | everywhere | This panel |
| Ctrl+P | everywhere | Command palette for this screen |
| F5 | everywhere | Refresh (also Ctrl+R) |
| q | everywhere | Quit when no field is focused |
| / | sessions | Search (Tab completes the last token) |
| s / Space | sessions | Select (also Space) |
| S | sessions | Select all rows in the current filter |
| E | sessions | Export a session bundle |
| Ctrl+O | sessions / HUD | Import a harness archive or anqa export |
| x | sessions | Delete (press twice) |
| [ ]  1-4 | browser | Timeline, Summary, Diff, Notes |
| h / l / Left / Right | browser | Previous / next turn on the Timeline |
| j / k | browser | Previous / next Timeline event, or previous / next note |
| v | browser | Filter (Subagents, Background, Workflows) |
| Enter | browser / HUD | Open a Timeline event or child; edit the focused note; next Diff match |
| Shift+Enter | Diff | Previous Diff match |

| N | browser / HUD | New note (TUI Notes); Notes pane (HUD) |
| y | browser / HUD | Copy the selection or the focused / primary pane body |
| Ctrl+Shift+C | browser | Same as y |
| E | browser | Export a session bundle |
| x | browser / HUD | Delete the focused note (press twice); on the session list, delete the session (every harness store) |
| s | pickers | Select |
| Ctrl+S | pickers | Apply the selection |
| Esc | pickers | Cancel |

The [Desktop HUD](#desktop-hud) shares `?` / `Esc` / `/` / `y` / `j` `k`
/ `h` `l` (Timeline turns while All turns is selected) / `N`. HUD panes are Tab
and Ctrl+1–5 except on Notes, where Tab walks the note fields and
Ctrl+Tab or Ctrl+1–5 change panes. `[` is All turns (Filter stays).
`]` / `h` `l` jump to the next or previous turn that still matches
Filter, only while All turns is selected. `u` or the logo leaves the
open session for the session list. `g` on Turns opens Timeline for that
turn. Enter opens (or edits the focused note). An open event has a
**Raw** Switch: this event as JSON.

### Export

`E` on the list or browser writes a session bundle under
`~/.anqa/reports/` (profile in `export.default_profile`, or pick once).
A parent bundle includes `children/<id>/session.tar.gz` for each
openable child. Exporting an opened child is that child only.

### Import

`Ctrl+O` on the session list opens a file picker in the terminal
app (browse, Up / h / Left / Backspace for the parent folder, or
type a path). The desktop palette uses the host picker and also
accepts a dropped archive. `anqa import PATH` does the same from
the shell. The owner unpacks the
native harness archive (or an anqa `E` export) into
`~/.anqa/imports/<harness>/` and lists it with `is:import`. Browse
it like any other session.

### Catalog search

`/` on the session list. Last-token completions appear while you type. `?` notes that. Bare words match title, id, and label. Space is AND. `AND`, `OR`, and `NOT` must be that spelling (`and` is a word in the title). The list updates after a short pause (same 0.28s idle on the terminal and the desktop palette) so each key does not walk the catalog. The palette sends the committed query to `anqad`.

| Token | Matches |
|-------|---------|
| `is:running` `is:awaiting` `is:ending` `is:complete` `is:cancelled` `is:idle` `is:host` `is:import` | Turn status or import store. `is:running` is a turn in progress (store live flag or mid-turn work). `is:idle` is no list status (last user row or bookend), not an open window |
| `has:workflow` `has:note` `has:goal` `has:plan` `has:subagent` `has:task` `has:job` `has:schedule` `has:error` `has:failure` `has:diff` `has:git` `has:context` `has:compaction` `has:doom` | Presence (`has:plan` is at least one). Counts use the written pair (`plans:>=2`, `errors:>=5`, `goals:1`). Both words are listed in the schema; nothing is pluralized. `has:goal` is ``goal/state.json``. `has:plan` is ``plan.json`` or ``plan_mode.json``. `has:task` is Overview Tasks (shells, monitors, or schedules). `task:` is a task-id substring. Git stays yes/no. |
| `workflows:` `notes:` `goals:` `plans:` `errors:` `turns:` `tools:` `events:` | Counts, with `>` `>=` `<` `<=` `=` |
| `duration:` | Session length (`1h`, `2d`, `30m`), same compares |
| `in:~/path` | Directory the session was run in |
| `model:` `task:` | Substring |
| `after:` `before:` | `updatedAt` (ISO, `yesterday`, `2d`, `2 days ago`) |
| `OR` `NOT` `-` `( )` | Compose |

| Query | Meaning |
|-------|---------|
| `has:note AND is:awaiting` | Waiting on a reply, and you already wrote notes |
| `is:complete AND NOT has:note` | Finished sessions you have not written up |
| `has:error OR has:failure` | Tool errors or a failed child |
| `workflows:>=2 AND NOT is:complete` | Multi-workflow sessions still going |
| `errors:>=5 AND NOT has:note` | Noisy sessions you have not written up |
| `notes:>=2 AND after:yesterday` | Recently updated, more than one note |
| `has:subagent OR has:workflow` | Spawned a child or a workflow |
| `in:~/src/app AND after:yesterday` | This repo, updated since yesterday |

Timeline search (same `AND` / `OR` / `NOT`) also takes `is:tool` (or `user`, `assistant`, `error`, `session`, `subagent`, `background`, `workflow`), `has:error`, `tool:read_file`, `turn:2`, `turn:>300`, `user:hello`, and `duration:>=2` (the Dur column: tool call to result, or time to the next event). The query runs on the whole session, not only the first loaded page. Turns search (desktop) takes `has:error`, `has:subagent`, `tools:>=5`, `errors:>=2`, `events:>=20`, and `duration:>1m` (turn wall time). Last-token hints appear under the box. The Filter and Turn dropdowns stay. The Timeline search box is a full-width row under Filter / Turn / Tail.

## Desktop HUD

Summonable palette: Recent sessions (scroll or `j` for more), catalog
search (same query language as the terminal list), then Overview /
Turns / Timeline / Diff / Notes. Type is Fira Sans and Fira Code
with ligatures. `u` or the logo returns to the session list.
Details: [`desktop/README.md`](desktop/README.md).

```bash
anqad -d             # or let the client start anqad
anqa desktop         # PATH binary from uv tool install; one process + tray
anqa desktop --toggle    # show or hide (Wayland bind this)
anqa desktop --open ID   # show and open a catalog session (running HUD)
anqa desktop --restart   # replace the running palette
anqa desktop --rebuild   # cargo-build this checkout, then launch
```

`anqa desktop` runs `anqa-hud` from `ANQA_HUD_BIN` or `PATH`. From a
checkout, `--rebuild` builds this tree; `--debug` is the unoptimized
binary; `--dev` is `cargo run`.

Default hotkey **Cmd+Shift+A** (macOS) / **Ctrl+Shift+A** (Windows and
X11 Linux). Override with `hud.global_shortcut` in
`~/.anqa/config.toml` or `ANQA_HUD_SHORTCUT`. On Wayland bind
`anqa desktop --toggle`: a compositor bind forwards an activation token so
you can type immediately; tray **Show** or a terminal `--toggle`
does not steal the keyboard. Sway: `anqa desktop --install-desktop` then
`include ~/.config/anqa/sway-hud.conf` (`examples/sway/`). A desktop
notification **Open** (or the default click) shows the palette on that
session. Sway places the overlay (float/center);
focus is that token. While the overlay is on screen, a live poll
re-reads overview about every **3 seconds** (idle sessions slower).
An unfocused pop-out or hidden overlay waits on control notifies instead.

`anqa desktop --install-desktop` writes user-local icons and a launcher
named **anqa** (Linux `.desktop` `Exec` uses `--show`, macOS
`~/Applications/anqa.app`, Windows Start Menu). Re-run after moving
the binary or to refresh the launcher. Tray **Quit anqa** exits the
palette only. [Emacs](#emacs) and
[Neovim](#neovim-09) attach to the same [control](#control) socket.

## Control

`anqad` owns the per-user Unix socket. The four clients attach.

```bash
anqad -d
anqad status
anqad stop
anqa export-host -o host-catalog.json
```

`export-host` writes the host catalog snapshot anqad uses (summary,
signals, and list status from the updates tail). It does not start anqad.

Bare `anqa` and `anqa desktop` detach-start anqad when the socket is
free (`--no-anqad` attaches only). Quitting a client leaves anqad
running. Debug every method: `ANQA_SERVE_LOG_LEVEL=DEBUG anqad`
(foreground) or `ANQA_SERVE_LOG_LEVEL=DEBUG anqad restart`. Methods,
framing, and notifications: [docs/control.md](docs/control.md).

## Emacs

```elisp
(load (string-trim (shell-command-to-string "anqa editor emacs-path")))
```

Sessions open as Org. Same [control](#control) socket as the
[terminal app](#terminal-app) and [HUD](#desktop-hud).

## Neovim (0.9+)

```lua
vim.opt.rtp:prepend(vim.fn.trim(vim.fn.system({ "anqa", "editor", "vim-path" })))
require("anqa").setup()
```

Sessions open as Markdown. Start serve (or the terminal app) so the
socket exists.

Schemas: [config](https://indynull.github.io/anqa/schemas/config.schema.json),
[control](https://indynull.github.io/anqa/schemas/control.schema.json).

## Examples

Supported packs under [`examples/`](examples/README.md) — copy into
`~/.anqa/` or pass paths. Not auto-loaded.

```bash
just examples-check
```

## Development

```bash
just install
just lint
just test
just ci              # lint + schema-check + hud-check + examples-check + test
just bump 0.1.1      # version strings + CHANGELOG.md
```

Also: `anqa doctor` (config home, catalog, HUD seat), `anqa keys`,
`anqa import PATH`.
