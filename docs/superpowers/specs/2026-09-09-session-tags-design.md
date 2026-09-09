# Session tags

Operator labels on catalog sessions. Add and remove them from the home list
or an open session. Search with `tag:`.

## What it is

A session may carry a set of tags. Tags are operator data: they live under
config home, next to notes, and never in a harness store. The same set is
what the list shows, what the picker edits, and what `tag:` matches.

## Storage

Path: `~/.anqa/notes/<harness>/<session_id>/tags.toml`

```toml
tags = ["review", "ui"]
```

- Missing file: empty set.
- Empty set after a save: delete the file.
- Corrupt file: treat as empty, log, next save replaces it.

A tag is one token: letters, digits, hyphen. Identity is case-insensitive.
If the vocabulary already has that tag, new writes reuse that spelling.
Otherwise the operator's spelling is stored. The field rejects empty names
and names that fail that shape. Duplicates collapse.

Vocabulary for autocomplete is the union of tags still present on catalog
sessions (every `tags.toml` the catalog already walks).

## Catalog and search

Each `session/list` row includes `tags: ["review", "ui"]`.

| Query | Match |
|-------|--------|
| `tag:review` | Session has `review` |
| `tag:review,ui` | Session has both (comma is AND) |
| `tag:review tag:ui` | Same (space is already AND) |
| `tag:review OR tag:bug` | Either |

Last-token hints after `tag:` offer known tags (and complete a comma list).
There is no `has:tag` and no `tags:` count.

## Control

Additive methods on the current protocol:

| Method | Role |
|--------|------|
| `tags/get` | Tags on one session, plus the vocabulary |
| `tags/set` | Replace the set on one or more sessions |

`tags/set` takes session ids and the new list. Last write wins (no revision).
Unknown session: control error.

Notification `tags/changed` lists the session ids. List clients refresh
those rows; an open session refreshes its chips.

TUI, HUD, and editor clients use these methods. Domain module owns the
files (`anqa` tags store next to `notes.py`).

## Surfaces

Action `session.tag`, default `t`, shared by the terminal and the desktop
palette. Works on the home list and on an open session. Footer, `?`,
command palette, README, and HUD cheatsheet list it.

Target: marked rows; otherwise the focused row or the open session.

Picker: chips plus a field.

- Chips are the working set; dismiss removes.
- Field autocompletes the vocabulary and creates on Enter.
- Save (`Ctrl+S`, or Enter when the field is empty) calls `tags/set`.
- Esc discards.

A mixed selection starts from the intersection. Save writes that working
set onto every selected session.

List face: tags after the title, muted. Elide with an ellipsis when they
would overflow the title cell or HUD card title line. Open-session chrome
shows the same chips (not inside Timeline).

Terminal: Textual `Input` with a suggester, chips as compact buttons.
Desktop: icedtea chips and `text_input` with the existing suggestion list.

## Export

Anqa export includes `tags.toml` next to notes when the session has tags.
Opening that bundle restores them.

## Tests and docs

- Domain: read/write, identity, missing/corrupt file, `tag:` / comma AND,
  vocabulary union.
- TUI Pilot: `t` opens picker; add, dismiss, create; mixed intersection;
  title elision.
- HUD: `t` / `session.tag` on list and open session.

Same change: README search table, `docs/search.md`, Fluent, `help.rich.txt`,
HUD footer + cheatsheet, `just schema` for the new methods and the `tags`
list field.

## Non-goals

Product SQLite for tags. `has:tag` and `tags:` count tokens. HUD session-list
multi-select. Emacs and Neovim tag pickers (the control methods exist; those
packs do not grow a picker in this unit).
