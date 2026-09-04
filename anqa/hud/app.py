"""Launch the iced Sol-style session palette (control-plane client only)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from ..control.client import ControlClient
from ..control.daemon import (
    EnsureDaemonResult,
    control_socket_accepts,
    ensure_control_daemon,
    wait_until_control_accepts,
)
from ..control.server import default_socket_path
from ..paths import resolve_catalog_root
from .launch import launch_hud


async def _probe(socket_path: Path) -> None:
    client = ControlClient(socket_path, client_name="anqa-hud")
    await client.initialize()


def run_hud(
    *,
    socket_path: Path | None = None,
    catalog_root: Path | None = None,
    auto_anqad: bool = True,
    dev: bool = False,
    debug: bool = False,
    rebuild: bool = False,
    foreground: bool = False,
    restart: bool = False,
    install_desktop: bool = False,
    summon: str | None = None,
) -> int:
    """Ensure control owner is live, then launch the iced ``anqa-hud`` binary.

    In an editable checkout, missing/stale binaries rebuild with
    ``cargo build --release`` by default. Pass *debug* for an unoptimized
    binary, or *dev* for ``cargo run`` in the checkout.

    By default the HUD is detached in the background (macOS overlay starts as
    an accessory: no Dock / Cmd+Tab until pop-out). Pass *foreground* to attach
    the terminal to the process.

    *install_desktop* only writes user-local icons/launcher entries (no serve,
    no HUD process).

    *summon* is ``show`` / ``hide`` / ``toggle``: talk to a running HUD via the
    summon Unix socket (Wayland/Sway compositor binds). When the HUD is not
    running, ``show`` and ``toggle`` start it with
    ``ANQA_HUD_SHOW_ON_START``; ``hide`` is a no-op (exit 0).

    The HUD is always a **client**. A live TUI or ``anqad`` already
    holding the socket is success (attach), not an error.

    :returns: Process exit code (0 normal, 1 failure, 127 binary missing).
    """
    if install_desktop:
        from .launch import install_desktop as do_install

        code = do_install(rebuild=rebuild, debug=debug)
        if code == 127:
            sys.stderr.write(
                "error: anqa-hud binary not found.\n"
                "From a checkout with Rust installed, ``anqa desktop`` auto-builds.\n"
                "  anqa desktop --rebuild --install-desktop\n"
                "Override path with ANQA_HUD_BIN.\n"
            )
        return code

    if summon is not None:
        from .launch import (
            hud_process_running,
            send_summon_command,
            summon_socket_accepts,
        )

        word = summon.strip().lower()
        if word not in {"show", "hide", "toggle"}:
            sys.stderr.write(f"error: unknown summon action {summon!r}\n")
            return 1
        if summon_socket_accepts() or hud_process_running():
            # Prefer the socket when the process is up; brief wait if racing boot.
            if not summon_socket_accepts():
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and not summon_socket_accepts():
                    time.sleep(0.05)
            if summon_socket_accepts():
                code = send_summon_command(word)
                if code == 127:
                    sys.stderr.write(
                        "error: anqa-hud binary not found for summon.\n"
                        "Override path with ANQA_HUD_BIN.\n"
                    )
                return code
            if word == "hide":
                return 0
            # Fall through to start when process is dead but socket stale.
        if word == "hide":
            return 0
        # Start a long-lived HUD and show on boot.
        os.environ["ANQA_HUD_SHOW_ON_START"] = "1"

    from ..config import load_app_config

    load_app_config()
    sock = Path(socket_path or default_socket_path()).expanduser()
    tr = resolve_catalog_root(catalog_root)
    if auto_anqad:
        from ..control.daemon import include_host_for_explicit_store

        result = ensure_control_daemon(
            socket_path=sock,
            traces_path=tr,
            include_host=include_host_for_explicit_store(catalog_root),
        )
        # Race: spawn lost the bind to a live TUI/serve — still attach if OK.
        if not result.ok and control_socket_accepts(sock):
            result = EnsureDaemonResult(
                ok=True,
                already_running=True,
                spawned=False,
                pid=result.pid,
                socket_path=sock,
                error="",
            )
        if not result.ok:
            sys.stderr.write(f"error: control owner unavailable: {result.error}\n")
            return 1
        if result.already_running:
            sys.stderr.write(f"anqa desktop: using existing control owner at {sock}\n")
        if not wait_until_control_accepts(sock, timeout=8.0):
            sys.stderr.write(f"error: control socket not accepting: {sock}\n")
            return 1
        try:
            asyncio.run(_probe(sock))
        except Exception as exc:
            sys.stderr.write(
                f"error: control initialize failed (is an old owner still bound?): {exc}\n"
            )
            return 1

    code = launch_hud(
        socket_path=sock,
        dev=dev,
        debug=debug,
        rebuild=rebuild,
        foreground=foreground,
        restart=restart,
    )
    if code == 127:
        sys.stderr.write(
            "error: anqa-hud binary not found.\n"
            "From a checkout with Rust installed, ``anqa desktop`` auto-builds.\n"
            "  anqa desktop --rebuild\n"
            "Unoptimized binary: anqa desktop --debug\n"
            "Debug cargo run: anqa desktop --dev\n"
            "Override path with ANQA_HUD_BIN.\n"
        )
        return 127
    return code


__all__ = ["run_hud"]
