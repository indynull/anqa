"""CLI entry point for anqa — Typer (Click) app.

Default: interactive TUI. Optional path (``-P`` or leading argument) selects
a session store or session (default catalog store).

Commands: ``desktop``, ``doctor``, ``editor``, ``keys``, ``config``,
``import``, ``export-host``. The control process is ``anqad``.

Shell completion: ``uv run anqa --install-completion``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .ui.i18n import setup_i18n

setup_i18n()

app = typer.Typer(
    name="anqa",
    help=(
        "Inspect harness sessions.\n\n"
        "With no command: open the TUI "
        "([cyan]PATH[/cyan] or [cyan]-P PATH[/cyan] = store or session; "
        "default catalog store).\n\n"
        "[cyan]desktop[/cyan] palette · "
        "[cyan]doctor[/cyan] host checks · "
        "[cyan]editor[/cyan] Emacs/Neovim pack paths · "
        "[cyan]keys[/cyan] resolved bindings.\n"
        "Control process: [cyan]anqad[/cyan]."
    ),
    no_args_is_help=False,
    add_completion=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

config_app = typer.Typer(
    name="config",
    help=(
        "Validate ``~/.anqa/config.toml``. "
        "Schema: [cyan]https://indynull.github.io/anqa/schemas/config.schema.json[/cyan]."
    ),
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")

editor_app = typer.Typer(
    name="editor",
    help="Packaged Emacs / Neovim client paths for install snippets.",
    no_args_is_help=True,
)
app.add_typer(editor_app, name="editor")

# Subcommand names — must not be consumed as a TUI path positional.
TOOL_COMMANDS = frozenset(
    {
        "desktop",
        "tui",
        "doctor",
        "editor",
        "keys",
        "config",
        "export-host",
        "import",
    }
)


def launch_tui(
    path: Path | None,
    config: Path | None,
    *,
    socket: Path | bool | None = None,
    prompt_index: int | None = None,
    ensure_anqad: bool = True,
) -> None:
    """Start the TUI for *path* (store or session) or the default host store.

    The TUI never owns the control socket. When *ensure_anqad* is true and a
    socket path is configured, detach-start ``anqad`` if the socket is
    free, then attach as a client. When *ensure_anqad* is false, attach only if
    a control process is already live. Pass *socket* ``False`` to run without control.
    """
    from .control.daemon import ensure_control_daemon
    from .control.server import default_socket_path
    from .paths import resolve_catalog_root
    from .ui.app import AnqaApp

    cfg = config.expanduser() if config is not None else None
    tr = resolve_catalog_root(path)
    session: Path | None = None
    if path is not None:
        candidate = Path(path).expanduser()
        markers = ("updates.jsonl", "events.jsonl", "chat_history.jsonl", "summary.json")
        if candidate.is_dir() and any((candidate / marker).is_file() for marker in markers):
            session = candidate.resolve()
        elif candidate.is_file():
            from .session.imports import import_session, looks_like_import_source

            if looks_like_import_source(candidate):
                imported = import_session(candidate)
                loc = imported.ref.locator
                session = loc if loc.is_dir() else None
    socket_path = (
        None
        if socket is False
        else Path(socket).expanduser()
        if isinstance(socket, Path)
        else default_socket_path()
    )
    if socket_path is not None and ensure_anqad:
        result = ensure_control_daemon(
            socket_path=socket_path,
            traces_path=tr,
        )
        if not result.ok:
            typer.echo(
                f"anqa: warning: could not start control owner: {result.error}",
                err=True,
            )
    from .ui.terminal_reply import drain_pending_stdin

    drain_pending_stdin()
    AnqaApp(
        traces_path=tr,
        config_path=cfg,
        control_socket=socket_path,
        control_attach_only=socket_path is not None,
        initial_session=session,
        initial_prompt_index=prompt_index,
    ).run()


@app.command("desktop")
def cmd_desktop(
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Catalog store when starting anqad (default catalog store).",
            show_default=False,
        ),
    ] = None,
    socket: Annotated[
        Path | None,
        typer.Option(
            "-s",
            "--socket",
            help="Control Unix socket (default: runtime control.sock).",
            show_default=False,
        ),
    ] = None,
    ensure_anqad: Annotated[
        bool,
        typer.Option(
            "--anqad/--no-anqad",
            help="Detach-start anqad when the socket is free (default: start).",
        ),
    ] = True,
    dev: Annotated[
        bool,
        typer.Option(
            "--dev",
            help="Run cargo run (debug) in the checkout instead of a built binary.",
        ),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Use unoptimized cargo debug binary (default: release).",
        ),
    ] = False,
    rebuild: Annotated[
        bool,
        typer.Option(
            "--rebuild",
            help="Force Rust rebuild for the selected profile before launch.",
        ),
    ] = False,
    foreground: Annotated[
        bool,
        typer.Option(
            "--foreground",
            help="Keep the HUD attached to this terminal (default: detach).",
        ),
    ] = False,
    restart: Annotated[
        bool,
        typer.Option(
            "--restart",
            help="Stop any running anqa-hud process, then start a new one.",
        ),
    ] = False,
    install_desktop: Annotated[
        bool,
        typer.Option(
            "--install-desktop",
            help=(
                "Write user-local icons and a launcher (Linux .desktop, "
                "macOS ~/Applications app, Windows Start Menu). Does not start the HUD."
            ),
        ),
    ] = False,
    show: Annotated[
        bool,
        typer.Option(
            "--show",
            help="Show the palette (running HUD). Starts the HUD if needed (Wayland/Sway).",
        ),
    ] = False,
    hide: Annotated[
        bool,
        typer.Option(
            "--hide",
            help="Hide the overlay (running HUD via summon socket).",
        ),
    ] = False,
    toggle: Annotated[
        bool,
        typer.Option(
            "--toggle",
            help="Show or hide (running HUD). Preferred Sway bindsym target.",
        ),
    ] = False,
    open_session: Annotated[
        str | None,
        typer.Option(
            "--open",
            help="Show the palette and open this catalog session (running HUD).",
        ),
    ] = None,
) -> None:
    """Desktop session palette (control client).

    Starts in the background by default (macOS: no Dock, no Cmd+Tab). Summon with
    Cmd+Shift+A on macOS / X11; on Wayland use ``--toggle``, tray Show, or a
    compositor bind. Runs the iced ``anqa-hud`` binary from ``ANQA_HUD_BIN``
    or ``PATH`` (``uv tool install``). From a checkout, ``--rebuild`` cargo-builds
    this tree; ``--debug`` is unoptimized; ``--dev`` is ``cargo run``.
    ``--restart`` replaces a running HUD. ``--install-desktop`` only installs
    icons/launcher entries for this user.
    """
    from .control.server import default_socket_path
    from .hud.app import run_hud

    summon_flags = sum(1 for f in (show, hide, toggle, bool(open_session)) if f)
    if summon_flags > 1:
        typer.echo("error: use only one of --show, --hide, --toggle, --open", err=True)
        raise typer.Exit(1)
    summon: str | None = None
    if show:
        summon = "show"
    elif hide:
        summon = "hide"
    elif toggle:
        summon = "toggle"
    elif open_session:
        summon = "open"

    sock = Path(socket).expanduser() if socket is not None else default_socket_path()
    code = run_hud(
        socket_path=sock,
        catalog_root=path,
        auto_anqad=ensure_anqad,
        dev=dev,
        debug=debug,
        rebuild=rebuild,
        foreground=foreground,
        restart=restart,
        install_desktop=install_desktop,
        summon=summon,
        open_session=open_session,
    )
    raise typer.Exit(code)


@editor_app.command("emacs-path")
def editor_emacs_path() -> None:
    """Print the packaged anqa.el path."""
    typer.echo(Path(__file__).parent / "integrations" / "emacs" / "anqa.el")


@editor_app.command("vim-path")
def editor_vim_path() -> None:
    """Print the packaged Neovim runtimepath directory."""
    typer.echo(Path(__file__).parent / "integrations" / "vim")


def _tui_options(
    path: Path | None,
    config: Path | None,
    socket: Path | None,
    use_socket: bool,
    ensure_anqad: bool,
    prompt_index: int | None,
) -> None:
    """Shared TUI launch (root default and ``tui`` command)."""
    launch_tui(
        path=path,
        config=config,
        socket=socket if use_socket else False,
        prompt_index=prompt_index,
        ensure_anqad=ensure_anqad if use_socket else False,
    )


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"anqa {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help=(
                "Session store or session directory "
                "(or pass as the first argument). Default: catalog store."
            ),
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "-c",
            "--config",
            help="Path to config.toml (default: ~/.anqa/config.toml).",
            show_default=False,
        ),
    ] = None,
    socket: Annotated[
        Path | None,
        typer.Option(
            "-s",
            "--socket",
            help="Control Unix socket (default: runtime control.sock).",
            show_default=False,
        ),
    ] = None,
    no_socket: Annotated[
        bool,
        typer.Option(
            "--no-socket",
            help="Run the TUI without the control plane (no anqad attach).",
        ),
    ] = False,
    ensure_anqad: Annotated[
        bool,
        typer.Option(
            "--anqad/--no-anqad",
            help=(
                "When the control socket is free, detach-start anqad before attach "
                "(default: start). --no-anqad only attaches if a process is already live."
            ),
        ),
    ] = True,
    prompt_index: Annotated[
        int | None,
        typer.Option(
            "--prompt-index",
            help="Prompt index when PATH is a session directory.",
            show_default=False,
        ),
    ] = None,
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Print the product version and exit.",
            callback=_print_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Start the TUI when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    _tui_options(path, config, socket, not no_socket, ensure_anqad, prompt_index)


@app.command("tui")
def cmd_tui(
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Store or session (default catalog store).",
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", help="Path to config.toml.", show_default=False),
    ] = None,
    socket: Annotated[
        Path | None,
        typer.Option("-s", "--socket", help="Control Unix socket.", show_default=False),
    ] = None,
    no_socket: Annotated[
        bool,
        typer.Option(
            "--no-socket",
            help="Run without the control plane.",
        ),
    ] = False,
    ensure_anqad: Annotated[
        bool,
        typer.Option(
            "--anqad/--no-anqad",
            help="Detach-start anqad when free (default: start).",
        ),
    ] = True,
    prompt_index: Annotated[
        int | None,
        typer.Option("--prompt-index", help="Prompt index for a session path.", show_default=False),
    ] = None,
) -> None:
    """Open the interactive TUI (same as bare ``anqa``)."""
    _tui_options(path, config, socket, not no_socket, ensure_anqad, prompt_index)


@config_app.command("validate")
def cmd_config_validate(
    path: Annotated[
        Path | None,
        typer.Argument(
            help="config.toml to validate (default: ~/.anqa/config.toml).",
        ),
    ] = None,
) -> None:
    """Validate a prefs TOML file against the published schema."""
    from .config import AppConfig, validate_config_file
    from .paths import app_config_path

    target = path.expanduser() if path is not None else app_config_path()
    if path is None and not target.is_file():
        cfg = AppConfig()
        typer.echo(f"OK  {target}  (theme={cfg.theme})")
        return
    try:
        cfg = validate_config_file(target)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"OK  {target}  (theme={cfg.theme})")


@config_app.command("schema")
def cmd_config_schema(
    out: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--out",
            help="Write JSON Schema to this path (default: stdout).",
        ),
    ] = None,
) -> None:
    """Emit JSON Schema for config.toml (same as ``just schema`` / Pages publish)."""
    from .config import emit_config_schema

    text = emit_config_schema(out)
    if out is None:
        typer.echo(text, nl=False)
    else:
        typer.echo(f"Wrote {out}")


@app.command("import")
def cmd_import(
    path: Annotated[
        Path,
        typer.Argument(help="Harness archive, anqa export, or session directory."),
    ],
) -> None:
    """Copy a session archive into ``~/.anqa/imports`` and print the catalog path."""
    from .session.imports import import_session

    result = import_session(path)
    typer.echo(result.ref.ref_string())


@app.command("export-host")
def cmd_export_host(
    out: Annotated[
        Path,
        typer.Option(
            "-o",
            "--out",
            help="JSON path for the host catalog snapshot (summary + signals + tail status).",
        ),
    ],
    host_root: Annotated[
        Path | None,
        typer.Option(
            "--host-root",
            help="Override the default catalog store.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Write the host catalog snapshot serve uses. Does not start serve."""
    from .session.mtime_export import write_host_catalog_export

    path = write_host_catalog_export(out, host_root=host_root)
    typer.echo(str(path))


@app.command("keys")
def cmd_keys(
    occupancy: Annotated[
        bool,
        typer.Option(
            "--occupancy",
            help="List taken chords per scope (normalized).",
        ),
    ] = False,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Load the overlay and exit 1 on error or conflict.",
        ),
    ] = False,
) -> None:
    """Print the resolved key table (catalog defaults plus optional keys.toml)."""
    from .keys.overlay import (
        format_errors,
        format_keymap_table,
        format_occupancy,
        load_keymap,
    )

    keymap = load_keymap()
    if check:
        if keymap.ok:
            label = str(keymap.path) if keymap.loaded_overlay else "defaults"
            typer.echo(f"OK  {label}")
            raise typer.Exit(0)
        typer.echo(format_errors(keymap), err=True)
        raise typer.Exit(1)
    if not keymap.ok:
        typer.echo(format_errors(keymap), err=True)
    if occupancy:
        typer.echo(format_occupancy(keymap))
    else:
        typer.echo(format_keymap_table(keymap))
    if not keymap.ok:
        raise typer.Exit(1)


@app.command("doctor")
def cmd_doctor(
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Catalog store to probe (default catalog store).",
            show_default=False,
        ),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of text lines."),
    ] = False,
) -> None:
    """Host checks: config home, catalog, and HUD seat (no TUI)."""
    from .diagnostics import run_self_test
    from .paths import resolve_catalog_root

    report = run_self_test(catalog_root=resolve_catalog_root(path))
    if json_out:
        payload = {
            "ok": report.ok,
            "fail_count": report.fail_count,
            "warn_count": report.warn_count,
            "checks": [
                {
                    "id": c.id,
                    "name": c.name,
                    "ok": c.ok,
                    "required": c.required,
                    "detail": c.detail,
                    "level": c.level,
                }
                for c in report.checks
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        for line in report.lines():
            typer.echo(line)
    raise typer.Exit(0 if report.ok else 1)


def main(argv: list[str] | None = None) -> None:
    """Console script entry (``anqa = anqa.cli:main``)."""
    args = list(sys.argv[1:] if argv is None else argv)

    # ``anqa PATH …`` → ``anqa -P PATH …`` (not a subcommand name).
    if args and not args[0].startswith("-") and args[0] not in TOOL_COMMANDS:
        args = ["-P", args[0], *args[1:]]

    app(args=args, prog_name="anqa")


if __name__ == "__main__":
    main()
