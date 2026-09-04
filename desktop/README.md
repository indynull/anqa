# anqa-hud

Summonable session palette for anqa. The idle list is **Recent**
(latest eight). Scroll down or press `j` at the bottom for the next
eight. Type `/` to search the whole catalog. Open a session for Overview,
Turns, Timeline, Diff, and Notes. Notes use the same schema as the
[terminal app](../README.md#terminal-app).

It attaches to [`anqad`](../docs/control.md) — same socket as the
[terminal app](../README.md#terminal-app), [Emacs](../README.md#emacs),
and [Neovim](../README.md#neovim-09). See [Desktop
HUD](../README.md#desktop-hud) in the main README.

## Run

```bash
anqad -d
anqa desktop
```

`anqa desktop` detaches and starts anqad when the socket is free. One
process, one tray tile: a second `anqa desktop` shows the palette. It runs
the `anqa-hud` on `PATH` from `uv tool install` (or `ANQA_HUD_BIN`).
`--rebuild` cargo-builds this checkout, then launches that binary.
`--restart` replaces a running palette (including `--dev --restart`).
`--dev` / `--debug` keep a debug binary. `--foreground` attaches to this
terminal. `anqa --version` and `anqa-hud --version` (`-V`) print the
product version.

```bash
anqa desktop --toggle    # show or hide
anqa desktop --show
anqa desktop --hide
anqa desktop --open ID   # show and open a catalog session
```

`--install-desktop` writes user-local icons and a launcher named
**anqa** (Linux `.desktop`, macOS `~/Applications/anqa.app`,
Windows Start Menu). Re-run after moving the binary.

## Hotkey

Default **Cmd+Shift+A** (macOS) / **Ctrl+Shift+A** (Windows and X11
Linux). Override with `hud.global_shortcut` in `~/.anqa/config.toml`
or `ANQA_HUD_SHORTCUT`. On Wayland bind `anqa desktop --toggle` (the
compositor sends an activation token so you can type). Tray **Show**
and a terminal `--toggle` do not steal the keyboard. Sway
places the overlay; focus is the token.

While the overlay is on screen, a live poll re-reads overview about
every **3 seconds** (idle sessions slower). An unfocused pop-out or
hidden overlay does not poll; control notifies still refresh the
catalog and fire desktop notifications. Press **?** for the shortcut
cheatsheet and the same catalog search tokens as the terminal app.
Shared keys match the terminal app (`?` `Esc` `/` `y` `j`/`k`
`h`/`l` for Timeline turns and Diff turns, `N`); panes are Tab and Ctrl+1–5 except on Notes, where Tab walks the note fields. `u` or the logo leaves an
open session for the session list (`Esc` still hides, or steps out of
Timeline detail / a child first). `g` on Turns opens Timeline for that
turn. Enter opens (or edits the focused note). An open event has a
**Raw** switch (same control as the terminal app). A `keys.toml` remap applies on both
surfaces. A configured leader (Colemak example: `;`) then one letter
runs `leader+X`; Esc or timeout cancels. Copy
`examples/keys/colemak.toml` to `~/.anqa/keys.toml`. Subagent runs
stay off the session list; open them from the parent Turns chips or by
clicking a Timeline spawn/finish tile (Enter does the same). Esc
returns to that Timeline or Turns place.

## Overlay, pop-out, tray, notify

Launch is a centered, always-on-top overlay. The pop-out icon in the
search bar opens a decorated desktop window. Close that window to leave
the HUD running; the hotkey or tray **Show** brings the overlay
back. **Esc** hides the overlay.

A tray icon appears when the host has one (Linux StatusNotifier, macOS
menu bar, Windows notification area). Left-click toggles the overlay
without taking keyboard focus. **Quit anqa** exits the palette
only; serve stays up.

Desktop notifications fire for sessions that are awaiting the operator,
cancelled, or failed. List ``complete`` is the last turn sitting idle,
not a finished session. The default click (and an **Open** action)
sends `open <sessionId>` on the summon socket so the running HUD shows
that session. Linux uses the 64px
tray tile; macOS and Windows use the square app icon
(`~/.anqa/hud-notify.png`). Disable with `ANQA_HUD_NOTIFY=0` or
`hud.desktop_notifications: false`.

## Env

| Variable | Role |
|----------|------|
| `ANQA_CONTROL_SOCKET` | Control Unix socket path |
| `ANQA_HUD_BIN` | Use this binary instead of building |
| `ANQA_HUD_SHORTCUT` | Override global summon chord |
| `ANQA_HUD_FOREGROUND` | Attach the HUD to this terminal |
| `ANQA_HUD_DEV` | Same as `--dev` |
| `ANQA_HUD_DEBUG` | Same as `--debug` |
| `ANQA_HUD_LOG` | Error log (default `~/.anqa/hud.log`) |
| `ANQA_HUD_SHOW_ON_START` | Show the palette when the process starts |
| `ANQA_HUD_NOTIFY` | `0` disables desktop notifications |
| `ANQA_HUD_SUMMON_SOCKET` | Override the show/hide summon socket |

## Develop

```bash
uv run anqa desktop             # PATH / ANQA_HUD_BIN, else checkout release
uv run anqa desktop --restart
uv run anqa desktop --rebuild   # cargo release of this tree
just hud-check                # from the repo root
```

Linux build packages: `libxkbcommon-dev`, plus Wayland or X11 for your
session.
