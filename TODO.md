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
