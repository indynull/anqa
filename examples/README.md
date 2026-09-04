# Examples

**Supported reference packs** — CI and `just examples-check` refuse to break
them. Copy into `~/.anqa/` or pass paths explicitly. Nothing under
`examples/` is auto-loaded by the product.

| Pack | What it teaches | Install / use |
|------|-----------------|---------------|
| [`config/`](config/) | Prefs TOML (`config.toml`) | `~/.anqa/config.toml` |
| [`notes/`](notes/) | In-app notes form schema (`source` is required on every write; extra fields are kept) | `~/.anqa/notes_schema.toml` |
| [`keys/`](keys/) | Key overlay (`colemak.toml`) | `~/.anqa/keys.toml` |
| [`themes/`](themes/) | Named colorway (`paper.toml`) | `~/.anqa/themes/` |
| [`sway/`](sway/) | Sway overlay float + `$mod+Shift+a` toggle | `include ~/.config/anqa/sway-hud.conf` |

## Contract

```bash
just examples-check   # or: uv run python scripts/check_examples.py
```

Validates keys overlays (`anqa keys --check`), prefs, notes schema,
and pack READMEs. Part of `just ci`.
