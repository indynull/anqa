# TODO — repo health and Unreleased follow-ups

Contract for humans and agents. Prefer fixing debt **when editing** the
module that owns it; do not open a speculative “cleanup epic.”

Keep this file and ``CHANGELOG.md`` in step. Open work against the
first-release product notes lives here; remove the TODO when that
work ships.

## Unreleased (see CHANGELOG.md)

- icedtea `markdown_view` leaves fence ticks on a code block. HUD notes
  paint a whole-field fence with `highlighted_code` until icedtea
  renders fences cleanly.
- icedtea `VirtualClip` wheel uses `heights.at(0)` as the line step, so a
  tall first Notes card jumps a full card per notch. Notes uses
  `widget::scroll` (60 px lines) until icedtea uses `SCROLL_LINE`.
- icedtea `virtual_column` `item_press` selects on release. A child
  `mouse_area` with `on_double_click` captures the press, so the row
  never arms. List tiles send focus on `on_press` until icedtea does
  not capture that press.
- icedtea `scroll` maps Left/Right to 24 px vertical steps while focused.
  The Notes list constructs that pane disabled so it is not a focus
  target; j/k stay next/previous note.
- icedtea `VirtualClip` first layout records `cover` and leaves scroll at 0.
  A remounted Timeline / Notes list therefore paints the top until
  `operation::scroll_to` lands. HUD keeps those lists mounted under
  detail / compose (`cover_stack`) until icedtea applies cover on first
  layout (or accepts an initial scroll).
- icedtea `motion::overlay` (`OverlayLayer`) does not implement
  `Widget::overlay`, so pick lists never open while that wrap is mounted.
  `page_body` and `fade_palette` mount it only while the fade runs.


## Catalog live list

The control owner keeps one catalog snapshot true. Work must not
scale as stores × sessions × writes. One domain apply path; every
adapter uses it (directory jsonl, directory plane files, sqlite).

### Apply pipeline (`session/catalog` + `session/watch` + `control/daemon`)

- Classify each watch path: noise, membership, list-stamp, or open
  session. Discard noise before any remeta.
- Compare the adapter list stamp (`timeline_stamp` / `trace_mtime` /
  `updates_size`) to the stamp cached on the row. Same stamp: stop.
- Remeta only that session. `load_meta` stays header plus 64 KiB tail.
  `has:` and tags remeta only when their own stamp files moved.
- Notify by tier (below). Do not bump one global revision on every
  live append.

### Adapter contract (`watch_hints`)

- Exact basenames only: `updates.jsonl`, `summary.json`, `meta.json`,
  `opencode.db`, `opencode.db-wal`, `session-store.db`.
- Drop suffix hints (`.jsonl`, `.db`, `.db-wal`) from the shared
  `adapter_watch_hits` union so a transcript write is not a list hit
  for every store.
- Each adapter names noise that is never catalog: `workspace/`,
  `terminal/`, `images/`, indexer files that are not the store
  (`session_search.sqlite` and its WAL).

### Watch set

- Subscribe membership directories (session appears or vanishes).
- Subscribe file-store parents (the sqlite and its WAL only).
- Subscribe the open session (browser / desktop palette timeline).
- Do not subscribe every listed session directory.
- Idle home-list freshness: periodic stamp poll (`stat` each hint
  file, remeta movers only).

### Notify tiers

- Membership (new / gone session): bump revision; `session/list` delta.
- Chrome (title, status, model): `session/changed` with that row.
  Clients patch the row; they do not re-list the catalog.
- Meters (event count, context, `has:`, `updatedAt`): no revision
  bump. Open browser refreshes; home list waits or patches cells.
- Notes and tags: existing `notes/changed` / `tags/changed`. No
  catalog remeta.

### File stores (OpenCode, Copilot, Antigravity)

- Debounce WAL writes.
- If the database stamp moved, remeta only rows whose per-row stamp
  moved (`refresh_file_store`). A WAL checkpoint must not rebuild
  every row in that store.

### Checks

- Adapter QA: exact `watch_hints`; fixture appends a hint file with
  no membership change and asserts stamp-equal → no remeta,
  meter-only → no revision bump, chrome → one-row notify.
- `tests/session/test_catalog_cache.py` and the harness contract
  cover this path for every shipped store.

### Non-goals

- A Grok-only ignore for `session_search.sqlite`.
- A second watch API.
- Recursive watches that see `workspace/`.
- Parsing a transcript to decide whether the list changed.

## Always on (`just ci` / `just lint`)

- Keep **`just lint`** and **`just test`** green before commit.
- Default lint is ruff (F/E/W/I/UP/T20) + format check + mypy + fluent + typing policy.
- Size / complexity rules are **not** in default lint (too much historical debt).

## Size limits (edit-time)

Documented limits (ruff pylint family): args 5, returns 5, branches 12,
statements 50, public methods 20 — see `AGENTS.md` §4.6.

```bash
just lint-complexity   # report only the size-limit rules on anqa/
```

When you **touch** a function or class that already exceeds a limit: split or
simplify that unit in the same change. **No blanket `noqa`.** Do not mass-fix
unrelated hotspots (browser, orchestrator, parser) “because large.”

## Imports

- Prefer **module-level** imports when editing a file.
- Allowed lazy imports: CLI deferring the Textual app for light `--help`;
  dynamic plugin `importlib` loaders (one factual comment each).

## Out of scope here

New product features and operator-facing polish live
in issues or design docs. Follow-ups for work already in CHANGELOG
Unreleased belong in the section above.
