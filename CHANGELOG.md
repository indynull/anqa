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

- Every note has a `source`. Extra field keys are stored as sent.
  Notes (terminal and HUD) show the writer badge and the stored fields.
- Card, timeline, summary, and note stamps show the host local clock
  as `YYYY-MM-DD HH:MM:SS`.

### Terminal app

- `anqa` / `anqa tui` is the session client: session list,
  browser, notes, and export.
- Browser panes are Timeline, Summary, Diff, and Notes.
- Timeline Filter and Turn stack; Tail follows a live session.
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
  Ligatures are on (`liga` / `calt`). Search chrome uses a 64px-tall
  mark. List rows are hairline tiles; status is a pill; other facts
  are muted text. The search glass sits inside the field.
- `anqa desktop` is the summonable session palette (Overview, Turns,
  Timeline, Diff, Notes).
- It runs `anqa-hud` from `ANQA_HUD_BIN` or `PATH`; `--rebuild`
  cargo-builds this checkout.
- Default hotkey is Cmd+Shift+A (macOS) / Ctrl+Shift+A (Windows and
  X11). On Wayland bind `anqa desktop --toggle`.
- `--install-desktop` writes user-local icons and a launcher named
  anqa.
- The palette is on icedtea 0.16. Session cards highlight on release.
- Clearing catalog or Timeline search remounts the idle list (not the
  leftover short search window).

### Control

- `anqad` owns the per-user Unix socket. The four clients
  attach: terminal app, desktop HUD, Emacs, and Neovim.
- Serve arms each catalog root watch off the serve loop. The watch
  covers membership directories and session directories (not each
  plane file). Catalog ``has:goal`` / ``has:plan`` follow the goal
  and plan files on disk.
- Bare `anqa` and `anqa desktop` detach-start anqad when the socket is
  free. Quitting a client leaves anqad running.
- `protocolVersion` is semver (`1.0.0`), independent of the product
  version. Same major keeps a live owner; a major bump is the only
  incompatible handshake change.
- Emacs opens sessions as Org; Neovim opens them as Markdown.
- `notes/list` and `notes/upsert` stay responsive while a catalog store
  is building. Session resolve is a name lookup on catalog scan roots
  (no `collect_session_dirs`, no adapter `ref_for_id`). Cold
  `session/overview` and `session/timeline` resolve a session by name
  on those roots. `diagnostics` reports the active RPC and a bounded
  notes failure. `anqad -P STORE` scans that store only.

### Examples

- Supported packs live in `examples/` (not auto-loaded).

### Development

- `just` is the public development verb (`just lint`, `just test`,
  `just ci`).
- `just bump 0.1.1` sets every product version declaration and
  promotes this file.
- `anqa doctor` checks config home, catalog, control owner, and HUD seat.
