# Operator notes schema example

Turn-linked operator notes use a **configurable** field list. Copy this file:

```bash
mkdir -p ~/.anqa
cp examples/notes/notes_schema.example.toml ~/.anqa/notes_schema.toml
```

Then edit `id` / `label` for your workflow. Field ids must be
`^[A-Za-z_][A-Za-z0-9_-]*$`. Keep program-specific templates in your local kit,
not in the anqa package.

### Field kinds (one path)

| Schema | Terminal / desktop HUD | Stored value |
|--------|------------------------|----------------|
| no `choices` (or empty) | text area / text field | free string |
| `choices = […]` + `pick = "one-of"` (default when `pick` omitted) | dropdown | one token |
| `choices = […]` + `pick = "many"` | multi-select / filter chips | newline-joined tokens |

Existing schemas without `choices` keep working as free text.

## Session file

Notes are stored as `operator_notes.toml`. Host adapter sessions write
under `~/.anqa/notes/<session_id>/`. A session directory outside those
stores writes `<session_dir>/operator_notes.toml`. File or database
stores write under `~/.anqa/notes/<harness>/<session_id>/`.

## TUI

In the session browser:

- **`N`** — create a note (linked to the current turn and optional selected event)
- **Enter** — edit the focused note (click a card to select it)

Notes tab lists notes. `j` / `k` move among them; Enter edits the
focused note; double-press `x` deletes that note. On Timeline /
Summary / Diff, double-press `x` deletes this session. Export (`E`) includes
`notes/operator_notes.toml` when notes exist.

**Authoring** is via TUI, Emacs, Neovim, or HUD (control plane). Batch does not write notes.

## Writes

Every note must include a non-empty `source` (who wrote it: `tui`,
`hud`, `nvim`, `emacs`, or a plugin name). `fields` need not match this schema
file. Extra keys are stored as sent. A new TUI / HUD note uses this
schema. Editing a note also shows extra stored fields. Notes, HUD
Notes, and the edit form show a source badge. Session export
(`notes/operator_notes.toml`) keeps `source` and every field. Delete removes a note; keeping one does not rewrite
it into the form schema.

## Ingest

External tools can parse the TOML from the export tarball without scraping the
Notes markdown. Control `notes/upsert` takes the same `source` plus
field bag.
