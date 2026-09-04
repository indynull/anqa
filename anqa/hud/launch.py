"""Locate, auto-build, and launch the iced anqa-hud binary."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SOURCE_GLOBS = (
    "src/**/*",
    "assets/**/*",
    "Cargo.toml",
    "Cargo.lock",
)


def _repo_root() -> Path:
    # anqa/hud/launch.py → parents[2] = repo root when editable checkout
    return Path(__file__).resolve().parents[2]


def hud_checkout_dir() -> Path | None:
    """Return the HUD crate dir (``desktop/``) in an editable checkout, if present."""
    cand = _repo_root() / "desktop"
    if (cand / "Cargo.toml").is_file() and (cand / "src" / "main.rs").is_file():
        return cand
    return None


def _cargo_target_dir(checkout: Path) -> Path:
    """Directory Cargo writes ``debug/`` and ``release/`` into.

    A workspace member uses the workspace-root ``target/``; a standalone
    crate manifest uses ``checkout/target``.
    """
    for parent in checkout.parents:
        manifest = parent / "Cargo.toml"
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError:
            continue
        if "[workspace]" in text:
            return parent / "target"
    return checkout / "target"


def _debug_binary(checkout: Path) -> Path:
    return _cargo_target_dir(checkout) / "debug" / "anqa-hud"


def _release_binary(checkout: Path) -> Path:
    return _cargo_target_dir(checkout) / "release" / "anqa-hud"


def _prune_target(checkout: Path) -> None:
    """Remove llvm-cov leftovers. Keep debug and release graphs for Cargo reuse."""
    target = _cargo_target_dir(checkout)
    for name in ("llvm-cov-target", "llvm-cov"):
        path = target / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def _executable(path: Path) -> Path | None:
    if path.is_file() and os.access(path, os.X_OK):
        return path
    return None


def _env_hud_binary() -> Path | None:
    env = os.environ.get("ANQA_HUD_BIN", "").strip()
    if not env:
        return None
    return _executable(Path(env).expanduser())


def _path_hud_binary() -> Path | None:
    which = shutil.which("anqa-hud")
    if which is None:
        return None
    return _executable(Path(which))


def find_hud_binary(*, debug: bool = False) -> Path | None:
    """Return path to a built ``anqa-hud`` binary, if any.

    Preference: ``ANQA_HUD_BIN``, then ``PATH`` (``uv tool install``
    places the release binary next to ``anqa``), then the checkout
    binary for the requested profile (**release** by default; debug only
    when *debug*).
    """
    found = _env_hud_binary()
    if found is not None:
        return found
    path_bin = _path_hud_binary()
    if path_bin is not None:
        return path_bin
    checkout = hud_checkout_dir()
    if checkout is None:
        return None
    candidate = _debug_binary(checkout) if debug else _release_binary(checkout)
    return _executable(candidate)


def _source_mtimes(checkout: Path) -> list[float]:
    times: list[float] = []
    for pattern in _SOURCE_GLOBS:
        for path in checkout.glob(pattern):
            if path.is_file():
                try:
                    times.append(path.stat().st_mtime)
                except OSError:
                    continue
    return times


def hud_binary_is_stale(binary: Path, checkout: Path) -> bool:
    """True when *binary* is older than any tracked HUD source file."""
    if not binary.is_file():
        return True
    try:
        bin_mtime = binary.stat().st_mtime
    except OSError:
        return True
    sources = _source_mtimes(checkout)
    if not sources:
        return False
    return max(sources) > bin_mtime


def build_hud(checkout: Path | None = None, *, debug: bool = False) -> Path | None:
    """Build ``anqa-hud`` with cargo; **release** by default, debug when *debug*.

    :param checkout: ``anqa-hud`` crate root (editable checkout).
    :param debug: When True, ``cargo build`` (unoptimized). When False (default),
        ``cargo build --release``.
    :returns: Path to the built binary, or None when cargo is missing / build fails.
    """
    root = checkout or hud_checkout_dir()
    if root is None:
        return None
    cargo = shutil.which("cargo")
    if cargo is None:
        sys.stderr.write("error: cargo not found on PATH; install Rust to auto-build anqa-hud\n")
        return None
    cmd = [cargo, "build", "--manifest-path", str(root / "Cargo.toml")]
    if not debug:
        cmd.append("--release")
    profile = "debug" if debug else "release"
    sys.stderr.write(f"anqa desktop: building {profile} anqa-hud ({' '.join(cmd[1:])})…\n")
    sys.stderr.flush()
    try:
        proc = subprocess.run(cmd, cwd=str(root), check=False)
    except OSError as exc:
        sys.stderr.write(f"error: cargo build failed to start: {exc}\n")
        return None
    if proc.returncode != 0:
        sys.stderr.write(f"error: cargo build exited {proc.returncode}\n")
        return None
    binary = _debug_binary(root) if debug else _release_binary(root)
    if binary.is_file() and os.access(binary, os.X_OK):
        _prune_target(root)
        return binary
    sys.stderr.write(f"error: build finished but binary missing: {binary}\n")
    return None


def build_hud_debug(checkout: Path | None = None) -> Path | None:
    """Build the unoptimized debug binary (``anqa desktop --debug``)."""
    return build_hud(checkout, debug=True)


def ensure_hud_binary(*, rebuild: bool = False, debug: bool = False) -> Path | None:
    """Return a runnable HUD binary for the requested profile.

    Default profile is **release**. Pass *debug* for the unoptimized binary.
    Prefer ``ANQA_HUD_BIN``, then the ``PATH`` binary from ``uv tool install``.
    Rebuild the checkout when *rebuild* is true, *debug* is requested, the
    profile binary is missing, or HUD sources are newer than that binary.
    """
    env = os.environ.get("ANQA_HUD_BIN", "").strip()
    if env:
        p = Path(env).expanduser()
        found = _executable(p)
        if found is not None:
            return found
        sys.stderr.write(f"error: ANQA_HUD_BIN not executable: {p}\n")
        return None

    if not rebuild and not debug:
        path_bin = _path_hud_binary()
        if path_bin is not None:
            return path_bin

    checkout = hud_checkout_dir()
    if checkout is None:
        return find_hud_binary(debug=debug)

    expected = _debug_binary(checkout) if debug else _release_binary(checkout)
    found = _executable(expected)
    need_build = rebuild or found is None or hud_binary_is_stale(found, checkout)
    if not need_build:
        _prune_target(checkout)
        return found

    built = build_hud(checkout, debug=debug)
    return built or found


def launch_hud_dev(
    *,
    socket_path: Path,
    extra_env: dict[str, str] | None = None,
) -> int:
    """Run ``anqa desktop --dev`` (``cargo run`` debug) in the checkout.

    :returns: Process exit code, or 127 when the checkout or cargo is unavailable.
    """
    checkout = hud_checkout_dir()
    if checkout is None:
        sys.stderr.write("error: anqa-hud checkout not found (editable install only)\n")
        return 127
    cargo = shutil.which("cargo")
    if cargo is None:
        sys.stderr.write("error: cargo not found on PATH\n")
        return 127
    env = os.environ.copy()
    env["ANQA_CONTROL_SOCKET"] = str(socket_path)
    env.update(_hud_shortcut_env())
    if extra_env:
        env.update(extra_env)
    if summon_socket_accepts():
        return send_summon_command("show")
    if hud_process_running():
        n = stop_hud_processes()
        if n:
            sys.stderr.write(
                f"anqa desktop: replaced {n} process(es); summon socket was not accepting\n"
            )
    sys.stderr.write(f"anqa desktop: cargo run (debug) in {checkout}\n")
    sys.stderr.flush()
    try:
        proc = subprocess.run(
            [cargo, "run", "--manifest-path", str(checkout / "Cargo.toml")],
            cwd=str(checkout),
            env=env,
            check=False,
        )
    except OSError as exc:
        sys.stderr.write(f"error: could not start cargo run: {exc}\n")
        return 1
    if proc.returncode == 0:
        _prune_target(checkout)
    return int(proc.returncode)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _hud_shortcut_env() -> dict[str, str]:
    """Pass config shortcut to the binary unless already set in the environment."""
    if os.environ.get("ANQA_HUD_SHORTCUT", "").strip():
        return {}
    try:
        from ..ui.prefs import hud_global_shortcut
    except Exception:
        return {}
    chord = hud_global_shortcut()
    if not chord:
        return {}
    return {"ANQA_HUD_SHORTCUT": chord}


def hud_process_running() -> bool:
    """True when a ``anqa-hud`` process is already alive on this machine."""
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "anqa-hud"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def default_summon_socket_path() -> Path:
    """Per-user summon socket (matches ``anqa-hud`` ``summon::default_socket_path``).

    ``$ANQA_HUD_SUMMON_SOCKET`` overrides. Else
    ``$XDG_RUNTIME_DIR/anqa/hud-summon.sock``, else
    ``~/.anqa/run/hud-summon.sock``.
    """
    env = os.environ.get("ANQA_HUD_SUMMON_SOCKET", "").strip()
    if env:
        return Path(env).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return Path(runtime) / "anqa" / "hud-summon.sock"
    return Path.home() / ".anqa" / "run" / "hud-summon.sock"


def summon_socket_accepts(path: Path | None = None) -> bool:
    """True when a HUD summon listener is bound on *path*."""
    sock = Path(path or default_summon_socket_path()).expanduser()
    if not sock.exists():
        return False
    try:
        import socket

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(0.4)
            client.connect(str(sock))
        finally:
            client.close()
        return True
    except OSError:
        return False


def send_summon_command(
    action: str, *, path: Path | None = None, session: str | None = None
) -> int:
    """Send ``show`` / ``hide`` / ``toggle`` / ``open`` to a running HUD.

    :param action: One of ``show``, ``hide``, ``toggle``, ``open``.
    :param session: Catalog session id when *action* is ``open``.
    :returns: Process exit code from the binary, or 1 on failure, 127 missing.
    """
    word = action.strip().lower()
    argv: list[str]
    if word == "open":
        sid = (session or "").strip()
        if not sid:
            sys.stderr.write("error: open needs a session id\n")
            return 1
        argv = ["--open", sid]
    elif word in {"show", "hide", "toggle"}:
        argv = [f"--{word}"]
    else:
        sys.stderr.write(f"error: unknown summon action {action!r}\n")
        return 1
    binary = find_hud_binary() or ensure_hud_binary()
    if binary is None:
        return 127
    env = os.environ.copy()
    if path is not None:
        env["ANQA_HUD_SUMMON_SOCKET"] = str(Path(path).expanduser())
    try:
        proc = subprocess.run([str(binary), *argv], env=env, check=False)
    except OSError as exc:
        sys.stderr.write(f"error: could not run {binary} {argv}: {exc}\n")
        return 1
    return int(proc.returncode)


def stop_hud_processes(*, wait_s: float = 1.5) -> int:
    """SIGTERM then SIGKILL any ``anqa-hud`` processes. Return how many were seen."""
    try:
        listed = subprocess.run(
            ["pgrep", "-x", "anqa-hud"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 0
    pids = [p for p in (listed.stdout or "").split() if p.isdigit()]
    if not pids:
        return 0
    subprocess.run(["kill"] + pids, check=False, capture_output=True)
    deadline = time.monotonic() + max(0.1, wait_s)
    while time.monotonic() < deadline and hud_process_running():
        time.sleep(0.05)
    if hud_process_running():
        subprocess.run(["kill", "-9"] + pids, check=False, capture_output=True)
        time.sleep(0.05)
    return len(pids)


def install_desktop(*, rebuild: bool = False, debug: bool = False) -> int:
    """Run ``anqa-hud --install-desktop`` (user-local icons + launcher).

    Ensures a binary first (same profile rules as launch). Does not start the
    control owner or the HUD process.

    :returns: Process exit code, or 127 when the binary is missing.
    """
    binary = ensure_hud_binary(rebuild=rebuild, debug=debug)
    if binary is None:
        return 127
    sys.stderr.write(f"anqa desktop: install-desktop via {binary}\n")
    sys.stderr.flush()
    try:
        proc = subprocess.run([str(binary), "--install-desktop"], check=False)
    except OSError as exc:
        sys.stderr.write(f"error: could not run {binary} --install-desktop: {exc}\n")
        return 1
    return int(proc.returncode)


def launch_hud(
    *,
    socket_path: Path,
    extra_env: dict[str, str] | None = None,
    dev: bool = False,
    debug: bool = False,
    rebuild: bool = False,
    foreground: bool | None = None,
    restart: bool = False,
) -> int:
    """Launch the iced palette (built binary, or ``cargo run`` when *dev*).

    When not *dev*, ensures a **release** binary for an editable checkout
    (auto ``cargo build --release`` if missing or sources are newer). Pass
    *debug* for an unoptimized ``cargo build`` binary instead.

    By default the binary is **detached**. Use *foreground* /
    ``ANQA_HUD_FOREGROUND=1`` to attach the terminal to the process.

    *restart* stops any existing ``anqa-hud`` first, then starts a new one.

    :returns: Process exit code when the child exits (or 0 after detach),
        or 127 if unavailable.
    """
    if restart:
        n = stop_hud_processes()
        if n:
            sys.stderr.write(f"anqa desktop: stopped {n} running process(es)\n")

    if dev or _truthy_env("ANQA_HUD_DEV"):
        return launch_hud_dev(socket_path=socket_path, extra_env=extra_env)

    want_debug = bool(debug) or _truthy_env("ANQA_HUD_DEBUG")
    binary = ensure_hud_binary(rebuild=rebuild, debug=want_debug)
    if binary is None:
        return 127
    env = os.environ.copy()
    env["ANQA_CONTROL_SOCKET"] = str(socket_path)
    env.update(_hud_shortcut_env())
    if extra_env:
        env.update(extra_env)

    attach = bool(foreground) if foreground is not None else _truthy_env("ANQA_HUD_FOREGROUND")
    chord_hint = env.get("ANQA_HUD_SHORTCUT", "").strip() or "Cmd+Shift+A / Ctrl+Shift+A"
    summon_hint = "anqa desktop --toggle (Wayland/Sway); tray Show"

    if not restart and summon_socket_accepts():
        return send_summon_command("show")
    elif hud_process_running():
        n = stop_hud_processes()
        if n:
            sys.stderr.write(
                f"anqa desktop: replaced {n} process(es); summon socket was not accepting\n"
            )

    logger.info("launching HUD binary %s (foreground=%s)", binary, attach)
    sys.stderr.write(f"anqa desktop: {binary}\n")
    hud_log = Path.home() / ".anqa" / "hud.log"
    sys.stderr.write(f"anqa desktop: errors → {hud_log}\n")
    if env.get("ANQA_HUD_SHORTCUT"):
        sys.stderr.write(f"anqa desktop: ANQA_HUD_SHORTCUT={env['ANQA_HUD_SHORTCUT']}\n")
    sys.stderr.flush()

    if attach:
        try:
            proc = subprocess.run([str(binary)], env=env, check=False)
        except OSError as exc:
            sys.stderr.write(f"error: could not launch {binary}: {exc}\n")
            return 1
        return int(proc.returncode)

    try:
        child = subprocess.Popen(
            [str(binary)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        sys.stderr.write(f"error: could not launch {binary}: {exc}\n")
        return 1
    sys.stderr.write(
        f"anqa desktop: background pid {child.pid} "
        f"(summon: {summon_hint}; hotkey {chord_hint}; not in Dock or Cmd+Tab)\n"
    )
    return 0


__all__ = [
    "build_hud",
    "build_hud_debug",
    "default_summon_socket_path",
    "ensure_hud_binary",
    "find_hud_binary",
    "hud_binary_is_stale",
    "hud_checkout_dir",
    "hud_process_running",
    "install_desktop",
    "launch_hud_dev",
    "launch_hud",
    "send_summon_command",
    "stop_hud_processes",
    "summon_socket_accepts",
]
