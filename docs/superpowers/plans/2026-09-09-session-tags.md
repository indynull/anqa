# Session tags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operators can tag catalog sessions from the list or an open session, and search with `tag:`.

**Architecture:** Operator `tags.toml` beside notes. Domain module owns the files. Catalog rows carry `tags`. Control `tags/get` and `tags/set` are the client path. TUI and HUD share `session.tag` (`t`) and a chips-plus-typeahead picker.

**Tech Stack:** Python 3.13, Textual, iced / icedtea, control JSON-RPC, pytest, cargo test.

---

## Files

| File | Role |
|------|------|
| `anqa/tags.py` | Load/save, identity, vocabulary, elision, intersection |
| `tests/tags/test_tags_store.py` | Domain tests |
| `anqa/session/query.py` | `tag:` match + comma AND + hints |
| `anqa/control/contract.py` | `tag` token, methods, `tags/changed` |
| `anqa/session/catalog.py` | Row `tags`, sig key, hydrate SessionMeta |
| `anqa/models.py` | `SessionMeta.tags` |
| `anqa/control/server.py` `access.py` `client.py` | RPC |
| `anqa/session/export_bundle.py` `imports.py` | Bundle tags.toml |
| `anqa/keys/catalog.py` `ui/bindings.py` | `session.tag` / `t` |
| `anqa/ui/widgets/tags_modal.py` `app.py` `screens/browser.py` | Picker + list face |
| `desktop/src/{wire,query,view,app,help,keys}.rs` | HUD parity |
| README, `docs/search.md`, Fluent, `help.rich.txt` | Operator docs |

Spec: `docs/superpowers/specs/2026-09-09-session-tags-design.md`

### Task 1: Domain store

- [ ] Failing tests in `tests/tags/test_tags_store.py`
- [ ] `anqa/tags.py`
- [ ] Commit

### Task 2: Query + catalog

- [ ] `tag:` tests then contract token, row field, match, hints
- [ ] Commit

### Task 3: Control + export

- [ ] `tags/get` `tags/set` `tags/changed`, schema emit
- [ ] Export/import `tags.toml`
- [ ] Commit (harness-probe + lint)

### Task 4: TUI

- [ ] Key, modal, list elision, Fluent, help, palette
- [ ] Commit

### Task 5: HUD + docs

- [ ] Same key, chips, list face, cheatsheet, README
- [ ] Commit
