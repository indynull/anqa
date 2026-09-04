"""anqad — control process for anqa clients.

Owns the per-user Unix socket. The terminal app, desktop palette,
Emacs, and Neovim attach as clients.
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
    name="anqad",
    help=(
        "Control process: owns the local JSON-RPC Unix socket.\n\n"
        "With no subcommand: start in the foreground. "
        "[cyan]-d[/cyan] detaches. "
        "Lifecycle: [cyan]stop[/cyan] · [cyan]restart[/cyan] · [cyan]status[/cyan]. "
        "Clients are [cyan]anqa[/cyan] and [cyan]anqa desktop[/cyan]."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

_Path = Annotated[
    Path | None,
    typer.Option(
        "-P",
        "--path",
        help="Catalog store (default catalog store).",
        show_default=False,
    ),
]
_Socket = Annotated[
    Path | None,
    typer.Option(
        "-s",
        "--socket",
        help="Control Unix socket (default: runtime control.sock).",
        show_default=False,
    ),
]
_Daemon = Annotated[
    bool,
    typer.Option(
        "-d",
        "--daemon/--foreground",
        help="Run in the background; return when the socket accepts.",
    ),
]
_Timeout = Annotated[
    float,
    typer.Option(
        "-t",
        "--timeout",
        help="Seconds to wait for stop/restart.",
    ),
]


def _socket_option(control_socket: Path | None) -> Path:
    from .control.server import default_socket_path

    return (
        Path(control_socket).expanduser() if control_socket is not None else default_socket_path()
    )


def _run_start(
    *,
    path: Path | None,
    control_socket: Path | None,
    daemonize: bool,
) -> int:
    """Start the control process (foreground or detached)."""
    from .control.daemon import (
        include_host_for_explicit_store,
        run_control_daemon,
        start_control_daemon_detached,
    )

    sock = _socket_option(control_socket)
    host = include_host_for_explicit_store(path)
    if daemonize:
        result = start_control_daemon_detached(
            socket_path=sock,
            traces_path=path,
            include_host=host,
        )
        if result.already_running and result.ok:
            typer.echo(f"already running  pid={result.pid}  socket={sock}", err=True)
            return 0
        if not result.ok:
            typer.echo(f"failed to start: {result.error}", err=True)
            return 1
        typer.echo(f"started  pid={result.pid}  socket={sock}", err=True)
        return 0
    return run_control_daemon(
        socket_path=sock,
        traces_path=path,
        include_host=host,
    )


def _run_stop(*, control_socket: Path | None, timeout: float) -> int:
    from .control.daemon import stop_control_daemon

    sock = _socket_option(control_socket)
    return stop_control_daemon(sock, timeout=timeout)


def _run_restart(
    *,
    path: Path | None,
    control_socket: Path | None,
    daemonize: bool,
    timeout: float,
) -> int:
    """Stop if running, then start (default background)."""
    from .control.daemon import control_daemon_status

    sock = _socket_option(control_socket)
    st = control_daemon_status(sock)
    if st.live or st.pid is not None or st.stale_lock or st.lock_pid is not None:
        code = _run_stop(control_socket=control_socket, timeout=timeout)
        if code != 0 and st.live:
            typer.echo("warning: stop returned non-zero; attempting start", err=True)
    return _run_start(
        path=path,
        control_socket=control_socket,
        daemonize=daemonize,
    )


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"anqa {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    path: _Path = None,
    control_socket: _Socket = None,
    daemonize: _Daemon = False,
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
    """With no subcommand: start the control process (foreground unless ``-d``)."""
    if ctx.invoked_subcommand is not None:
        return
    raise typer.Exit(
        _run_start(
            path=path,
            control_socket=control_socket,
            daemonize=daemonize,
        )
    )


@app.command("stop")
def cmd_stop(
    control_socket: _Socket = None,
    timeout: _Timeout = 5.0,
) -> None:
    """Stop the control process (pid file and/or stale lock holders)."""
    raise typer.Exit(_run_stop(control_socket=control_socket, timeout=timeout))


@app.command("restart")
def cmd_restart(
    path: _Path = None,
    control_socket: _Socket = None,
    daemonize: _Daemon = True,
    timeout: _Timeout = 5.0,
) -> None:
    """Stop then start (``-d`` by default)."""
    raise typer.Exit(
        _run_restart(
            path=path,
            control_socket=control_socket,
            daemonize=daemonize,
            timeout=timeout,
        )
    )


@app.command("status")
def cmd_status(
    control_socket: _Socket = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Machine-readable status."),
    ] = False,
) -> None:
    """Print process status (exit 0 if live and accepting)."""
    from .control.daemon import control_daemon_status

    sock = _socket_option(control_socket)
    status = control_daemon_status(sock)
    if as_json:
        typer.echo(json.dumps(status.as_mapping(), indent=2, sort_keys=True))
    elif status.live:
        pid = status.pid if status.pid is not None else "?"
        typer.echo(f"running  pid={pid}  socket={status.socket_path}")
    else:
        typer.echo(f"stopped  socket={status.socket_path}")
        if status.pid is not None and not status.pid_alive:
            typer.echo(f"  stale pid file  pid={status.pid}", err=True)
        if status.stale_lock:
            lp = status.lock_pid if status.lock_pid is not None else "?"
            typer.echo(
                f"  stale lock  pid={lp}  (run: anqad stop)",
                err=True,
            )
    raise typer.Exit(0 if status.live else 1)


def main(argv: list[str] | None = None) -> None:
    """Console script entry (``anqad = anqa.anqad:main``)."""
    args = list(sys.argv[1:] if argv is None else argv)
    app(args=args, prog_name="anqad")


if __name__ == "__main__":
    main()
