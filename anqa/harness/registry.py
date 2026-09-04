"""Registered disk adapters and session-ref resolution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..models import JsonObject
from .ref import SessionRef, parse_session_ref_string
from .types import HarnessAdapter

type PathResolver = Callable[[str], Path | None]

_ADAPTERS: tuple[HarnessAdapter, ...] | None = None


def _grok_adapter() -> HarnessAdapter:
    from .grok import GrokAdapter

    return GrokAdapter()


def _opencode_adapter() -> HarnessAdapter:
    from .opencode import OpenCodeAdapter

    return OpenCodeAdapter()


def _pi_adapter() -> HarnessAdapter:
    from .pi import PiAdapter

    return PiAdapter()


def _claude_adapter() -> HarnessAdapter:
    from .claude import ClaudeAdapter

    return ClaudeAdapter()


def _gemini_adapter() -> HarnessAdapter:
    from .gemini import GeminiAdapter

    return GeminiAdapter()


def _antigravity_adapter() -> HarnessAdapter:
    from .antigravity import AntigravityAdapter

    return AntigravityAdapter()


def _copilot_adapter() -> HarnessAdapter:
    from .copilot import CopilotAdapter

    return CopilotAdapter()


def _codex_adapter() -> HarnessAdapter:
    from .codex import CodexAdapter

    return CodexAdapter()


def _cursor_adapter() -> HarnessAdapter:
    from .cursor import CursorAdapter

    return CursorAdapter()


def adapters() -> tuple[HarnessAdapter, ...]:
    """Installed adapters."""
    global _ADAPTERS
    if _ADAPTERS is None:
        _ADAPTERS = (
            _antigravity_adapter(),
            _claude_adapter(),
            _codex_adapter(),
            _copilot_adapter(),
            _cursor_adapter(),
            _gemini_adapter(),
            _grok_adapter(),
            _opencode_adapter(),
            _pi_adapter(),
        )
    return _ADAPTERS


def adapter(harness_id: str) -> HarnessAdapter | None:
    """Return the adapter for *harness_id*, or None."""
    hid = (harness_id or "").strip()
    for item in adapters():
        if item.id == hid:
            return item
    return None


def harness_product(harness_id: str) -> str:
    """Product name for *harness_id* (``OpenCode``), else the id."""
    item = adapter(harness_id)
    if item is not None:
        name = (item.product or "").strip()
        if name:
            return name
    return (harness_id or "").strip()


def enabled_host_ids() -> frozenset[str]:
    """Registered adapter ids minus ``[catalog].ignore``."""
    from ..config import load_app_config

    ignored = {item.casefold() for item in load_app_config().catalog.ignore}
    return frozenset(item.id for item in adapters() if item.id not in ignored)


def enabled_host_adapters() -> tuple[HarnessAdapter, ...]:
    """Registered adapters included on the host catalog."""
    wanted = enabled_host_ids()
    return tuple(item for item in adapters() if item.id in wanted)


def adapter_host_roots(item: HarnessAdapter) -> list[Path]:
    """Discover roots for *item*: ``[catalog.roots]`` override, else defaults."""
    from ..config import load_app_config

    override = load_app_config().catalog.roots.get(item.id)
    if override:
        return [Path(raw).expanduser() for raw in override]
    return item.default_host_roots()


def adapter_watch_basenames() -> frozenset[str]:
    """Adapter ``watch_hints`` names that should remeta a catalog store."""
    names: set[str] = set()
    for item in enabled_host_adapters():
        names.update(item.watch_hints())
    return frozenset(names)


def adapter_watch_hits(path: Path | str) -> bool:
    """True when *path* matches an adapter watch hint (name or suffix)."""
    name = Path(path).name
    names = adapter_watch_basenames()
    if name in names:
        return True
    return any(hint.startswith(".") and name.endswith(hint) for hint in names)


def adapter_store_watch_paths() -> list[Path]:
    """Enabled adapter stores that need a membership watch.

    Directory stores are already walked for catalog discover. File stores
    (sqlite, a transcript) are listed here so serve can watch them.
    """
    extra: list[Path] = []
    seen: set[str] = set()
    for item in enabled_host_adapters():
        for raw in adapter_host_roots(item):
            path = Path(raw).expanduser()
            if path.is_dir():
                continue
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            extra.append(path)
    return extra


def host_adapters() -> tuple[HarnessAdapter, ...]:
    """Adapters that contribute native host-store catalog rows."""
    return enabled_host_adapters()


def resolve_session_ref(
    reference: str,
    *,
    path_resolve: PathResolver | None = None,
    walk_adapters: bool = True,
) -> SessionRef | None:
    """Map a control ``session`` argument to a :class:`SessionRef`.

    Order: catalog locator (``path_resolve``) → ``harness:id`` /
    ``ref_for_id`` → directory path via ``bind_locator``.

    :param reference: Session id, directory, or ``harness:id``.
    :param path_resolve: Optional directory resolver (catalog cache).
    :param walk_adapters: When false, skip the all-adapter ``ref_for_id``
        scan. Notes RPC uses this so a cold catalog cannot stall on host
        stores.
    :returns: Locator, or None when nothing matches.
    """
    raw = (reference or "").strip()
    if not raw:
        return None
    parsed = parse_session_ref_string(raw)

    def _from_cache(key: str, *, harness: str = "", session_id: str = "") -> SessionRef | None:
        if path_resolve is None:
            return None
        path = path_resolve(key)
        if path is None or not (path.is_dir() or path.is_file()):
            return None
        if harness and session_id:
            return SessionRef(harness=harness, session_id=session_id, locator=path)
        if walk_adapters:
            return ref_from_path(path)
        return SessionRef(harness="", session_id=path.name, locator=path)

    if parsed is not None:
        hid, sid = parsed
        found = adapter(hid)
        if found is None:
            return None
        cached = _from_cache(raw, harness=hid, session_id=sid) or _from_cache(
            sid, harness=hid, session_id=sid
        )
        if cached is not None:
            return cached
        if not walk_adapters:
            return None
        return found.ref_for_id(sid)
    cached = _from_cache(raw)
    if cached is not None:
        return cached
    candidate = Path(raw).expanduser()
    if candidate.is_dir() or candidate.is_file():
        return ref_from_path(candidate)
    if not walk_adapters:
        return None
    for item in adapters():
        hit = item.ref_for_id(raw)
        if hit is not None:
            return hit
    return None


def ref_from_path(path: Path) -> SessionRef | None:
    """Ask each adapter to bind *path* as one session."""
    loc = Path(path).expanduser()
    for item in adapters():
        hit = item.bind_locator(loc)
        if hit is not None:
            return hit
    return None


def discover_dirs(root: Path | str) -> list[Path]:
    """Session directories each adapter finds under *root*."""
    found: list[Path] = []
    seen: set[str] = set()
    for item in adapters():
        for ref in item.discover([root]):
            loc = Path(ref.locator)
            try:
                key = str(loc.resolve())
            except OSError:
                key = str(loc)
            if key in seen:
                continue
            seen.add(key)
            found.append(loc)
    return found


def adapter_for(ref: SessionRef | Path | str) -> HarnessAdapter | None:
    """Return the adapter that owns *ref*, or None.

    *ref* is a :class:`SessionRef`, a session directory, or ``harness:id``.
    ``Path("opencode:ses_…")`` is the same catalog ref as the string.
    """
    if isinstance(ref, SessionRef):
        return adapter(ref.harness)
    parsed = parse_session_ref_string(str(ref))
    if parsed is not None:
        return adapter(parsed[0])
    path = Path(ref)
    bound = ref_from_path(path)
    if bound is not None:
        return adapter(bound.harness)
    for item in adapters():
        if item.looks_like(path):
            return item
    return None


def scheduler_state(state: JsonObject) -> JsonObject | None:
    """First adapter scheduler block found in *state*."""
    for item in adapters():
        block = item.scheduler_state(state)
        if block is not None:
            return block
    return None


def reported_completion_ids(state: JsonObject) -> set[str]:
    """Union of reported completion ids from every adapter."""
    found: set[str] = set()
    for item in adapters():
        found |= item.reported_completion_ids(state)
    return found


def require_adapter(ref: SessionRef | Path | str) -> HarnessAdapter:
    """Return the adapter that owns *ref*.

    :raises FileNotFoundError: No registered adapter claims *ref*.
    """
    item = adapter_for(ref)
    if item is None:
        raise FileNotFoundError(f"no adapter for session: {ref}")
    return item


__all__ = [
    "adapter",
    "adapter_for",
    "adapter_host_roots",
    "discover_dirs",
    "adapter_store_watch_paths",
    "adapter_watch_basenames",
    "adapter_watch_hits",
    "adapters",
    "harness_product",
    "enabled_host_adapters",
    "enabled_host_ids",
    "host_adapters",
    "ref_from_path",
    "reported_completion_ids",
    "require_adapter",
    "resolve_session_ref",
    "scheduler_state",
]
