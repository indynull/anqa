# Sway HUD include

Copy of the fragment `anqa desktop --install-desktop` writes to
`~/.config/anqa/sway-hud.conf`.

```bash
anqa desktop --install-desktop
# then in ~/.config/sway/config:
include ~/.config/anqa/sway-hud.conf
```

`$mod+Shift+a` runs `anqa desktop --toggle` and forwards
`XDG_ACTIVATION_TOKEN` so the overlay can take the keyboard. Tray
**Show** does not steal focus. A desktop notification **Open** action
sends `anqa desktop --open <session>` to the running HUD.
