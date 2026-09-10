# Changelog

Notable product state for anqa. One first-release section until 0.1.0
is tagged. This section is the product as it ships.

## Unreleased

First release. Anqa is a session review tool: timeline, notes,
workspace diffs, and a desktop palette. The catalog lists every shipped
harness store. Grok Build (`grok`), OpenCode (`opencode`), Pi
(`pi`), Claude Code (`claude`), Gemini CLI (`gemini`), Antigravity
(`antigravity`), GitHub Copilot (`copilot`), Codex (`codex`), and
Cursor (`cursor`) are registered.

### Install

- `uv tool install --editable .` builds `anqa` and `anqa-hud` (needs Rust).
- `uv tool install git+https://github.com/indynull/anqa` installs from git.
- `uv tool install anqa` is the package name on the Python package index.
- `anqa --version` (`-V`) prints the product version (`0.1.0`).
- The same version appears on the terminal `?` heading, the desktop
  palette window and `?` sheet, and `anqa-hud --version`.
- One product version across the Python package, `anqa-hud`, and
  `anqa-core`.
- Pushes to `main`, version tags, and workflow dispatch build Linux,
  macOS, and Windows wheels plus a source distribution, then upload
  those files to TestPyPI.

### Paths and config

- Config home is `~/.anqa` (`config.toml`, optional `keys.toml`).
- The catalog is every enabled adapter store.
- `~/.anqa/config.toml` is the only prefs file (terminal app and
  desktop HUD). Default look is `theme = "auto"`: the terminal follows
  the terminal then the desktop; the desktop palette follows the
  system pair and system paper when the OS reports it. Named catalog
  themes and `~/.anqa/themes/` pin a colorway on both clients.
- Optional `~/.anqa/keys.toml` remaps chords (`anqa keys`). Footer
  and `?` use the same action words on both clients.

### Sessions

- Every shipped adapter store is listed; `harness:<id>` filters.
  `[catalog] ignore` drops a store; `[catalog.roots]` overrides a path.
- Subagent runs stay off the top list; open them from the parent
  (Summary or Timeline Subagents). Esc returns there.
- Catalog, Timeline, and Turns share a query language (`is:`, `has:`,
  counts, `tool:`, `turn:`, `duration:`, `AND` / `OR`). Tokens live in
  the published control schema. Search applies after 0.28s idle.
  `has:note` follows overlay notes and `operator_notes.toml` beside
  the session. A notes write through control rebuilds that catalog
  row and the open overview.
- Claude Code Overview reads last assistant `usage` (`input_tokens` +
  `cache_read_input_tokens` + `output_tokens`). The timeline follows
  the `parentUuid` leaf path. Tool cards keep `Edit` / `Write` /
  `StrReplace`. Format contract: `docs/harness-claude.md`.
- Codex Diff lists one `Turn N` point per user turn. The rust parser
  stamps `turn_number` on `task_started` when that bookend is present,
  and emits `turn_started` on a user message only when it is not.
- Codex Overview reads last `event_msg` `info.last_token_usage.total_tokens`.
- OpenCode Timeline reads the event log when that session has event
  rows (the live store keeps a `message` table that is empty for
  most sessions). Catalog `opencode:id` still binds after
  `Path.resolve()`. Tested **1.18.29**.
- Format contracts: `docs/harness-codex.md`, `docs/harness-gemini.md`,
  `docs/harness-cursor.md`, `docs/harness-copilot.md`,
  `docs/harness-opencode.md`, `docs/harness-antigravity.md`.
- Diff uses rewind snapshots when the store wrote them. Otherwise it
  rebuilds per-path patches from write and edit tool calls on the
  timeline (every shipped adapter). OpenCode also uses
  ``summary.diffs`` when the event store wrote them. Codex
  ``apply_patch`` follows the published Begin Patch grammar (add,
  update, delete, move).
- List Turn is `running` when a turn is in progress, `—` when the store
  wrote no list status (last user row or bookend).
- List Events is the timeline event count from native ``list_meta``.
  Grok uses ``summary.json`` ``num_messages`` when that field is
  present, otherwise the native timeline count.
- Session delete (`x`) removes every store locator: directory, file, or
  database row.
- Pi sessions follow the leaf `parentId` path. Overview and Timeline
  read `thinking_level_change`, assistant `usage` (context tokens),
  `compaction` / `branch_summary`, `session_info.name`, `bashExecution`,
  and assistant `errorMessage`. File schema `version` is not the product
  version. Tool cards render Pi `read` / `edit` / `write` / `bash`.
  Diff lists one point per turn that edited a file (`Turn N`).
  Format contract: `docs/harness-pi.md`.

- Every note has a `source`. Extra field keys are stored as sent.
  Notes (terminal and HUD) show the writer badge and the stored fields.
- Card, timeline, summary, and note stamps show the host local clock
  as `YYYY-MM-DD HH:MM:SS`.

### Terminal app

- `anqa` / `anqa tui` is the session client: session list,
  browser, notes, and export.
- Browser panes are Timeline, Summary, Diff, and Notes.
- Timeline Filter and Turn stack; Tail follows a live session.
  Drag the list/detail sash to resize the panes. Enter still
  opens a full-width event.
  Opening an event asks for the 50,000-character body.
  Search (`turn:>300`, `tool:`, …) asks the session store for
  matching rows, not only the first loaded page. The Turn column
  keeps the owner turn id. The Turn picker lists owner overview
  turns.
- Summary and Overview share Session, Tasks, Workflows, Subagents,
  and Stats. Tasks is shells, monitors, and schedules. Enter on a
  bookend or child opens that inspect or session. Last turn is the
  owner display turn id.
- Diff lists turns, Prompt/Assistant tabs, and a files/hunk
  split. `/` finds path or hunk text.
- `y` copies the selection or the pane body.
- `E` writes a session bundle under `~/.anqa/reports/`. The nested
  archive comes from the session's harness adapter.
- `Ctrl+O` (terminal and desktop) and `anqa import PATH` open a
  harness archive or anqa export into `~/.anqa/imports/`. Filter with
  `is:import`. The terminal app browses the filesystem (Up / `h` /
  Left / Backspace for the parent folder); the desktop palette uses
  the host picker and also accepts a dropped file. Host
  and import copies of the same session id stay separate; the import
  copy shows Import.

### Desktop HUD

- UI type is brand Fira Sans and Fira Code from `brand/fonts/`.
  Ligatures are on (`liga` / `calt`). Search chrome uses a 36px-tall
  mark. List rows are hairline tiles; status is a pill; other facts
  are muted text. Search fields are icedtea `search_input` (glass
  inside, one control height).
- `anqa desktop` is the summonable session palette (Overview, Turns,
  Timeline, Diff, Notes).
- It runs `anqa-hud` from `ANQA_HUD_BIN` or `PATH`; `--rebuild`
  cargo-builds this checkout.
- Default hotkey is Cmd+Shift+A (macOS) / Ctrl+Shift+A (Windows and
  X11). On Wayland bind `anqa desktop --toggle`.
- `--install-desktop` writes user-local icons and a launcher named
  anqa.
- The palette is on icedtea 0.17.0. Session cards highlight on release.
  Timeline Tail and event Raw use the compact bar switch. Enter opens
  Turn / Filter only while that pick is focused. Status, type, source,
  and session tags share one small badge (icedtea Small pad, role ink
  on a surface wash — same mix as the terminal tag pills).
  Command+C / Control+C copies the focused quote body (and writes
  the host pasteboard on macOS with `pbcopy`).
  Selected list rows use a quiet wash. A focused list has no pane ring.
  Stepping Timeline event detail (`j` / `k`) fades the incoming
  event body (chrome stays).
- Clearing catalog or Timeline search remounts the idle list (not the
  leftover short search window). Leaving a session (`u` or the logo)
  puts the catalog search back.
- Timeline `h` / `l` (Left / Right) step the Turn filter the same way
  as the terminal app, including when a turn is selected. `]` jumps
  to the next Filter hit while All turns is selected.
- Opening a session and landing on Timeline leaves catalog search, so
  `h` / `j` / `k` / `l` move events and turns. `/` focuses that pane's
  search. Type in Search sessions (or `u`) to switch sessions.

### Control

- `anqad` owns the per-user Unix socket. The four clients
  attach: terminal app, desktop HUD, Emacs, and Neovim.
- Serve arms each catalog root watch off the serve loop. The watch
  covers membership directories, file-store parents, and the open
  session. A live journal append remetas that session. Meters keep
  the current catalog revision. Idle list freshness is a stamp poll.
  Catalog ``has:goal`` / ``has:plan`` follow the goal
  and plan files on disk.
- Bare `anqa` and `anqa desktop` detach-start anqad when the socket is
  free. Quitting a client leaves anqad running. A store fault
  (OpenCode sqlite, a native panic) returns a control error and
  stays in the owner log. The owner process does not exit. Opening
  a store-backed catalog row uses `harness:id`, so two OpenCode
  sessions do not share one list key.
- `protocolVersion` is semver (`2.0.0`), independent of the product
  version. Same major keeps a live owner; a major bump is the only
  incompatible handshake change.
- Emacs opens sessions as Org; Neovim opens them as Markdown.

### Examples

- Supported packs live in `examples/` (not auto-loaded).

### Development

- `just` is the public development verb (`just lint`, `just test`,
  `just ci`).
- `just bump 0.1.1` sets every product version declaration and
  promotes this file.
- `anqa doctor` checks config home, catalog, and HUD seat.
