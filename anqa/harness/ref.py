"""Session locator: directory, transcript file, or database.

Catalog path and notes path are the same shape for every adapter:
``harness:session_id`` and ``~/.anqa/notes/<harness>/<session_id>/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..paths import APP_HOME

# Shipped adapter ids. Parse does not import the registry.
HARNESS_IDS: frozenset[str] = frozenset(
    {
        "grok",
        "opencode",
        "pi",
        "claude",
        "gemini",
        "antigravity",
        "copilot",
        "codex",
        "cursor",
    }
)


@dataclass(frozen=True)
class SessionRef:
    """One session the catalog or control plane can reopen.

    :ivar harness: Adapter id.
    :ivar session_id: Stable product id (directory name or store row id).
    :ivar locator: Directory, transcript file, or database the adapter reads.
    :ivar cwd: Workspace path when the store recorded one.
    """

    harness: str
    session_id: str
    locator: Path
    cwd: str = ""

    def ref_string(self) -> str:
        """Control / catalog path: ``harness:id`` for every store."""
        return f"{self.harness}:{self.session_id}"

    def overlay_dir(self) -> Path:
        """Operator notes: ``~/.anqa/notes/<harness>/<session_id>/``."""
        return APP_HOME / "notes" / self.harness / self.session_id

    @classmethod
    def path(cls, ref: SessionRef | Path | str) -> Path:
        """Directory or file path from a ref, path, or string."""
        if isinstance(ref, SessionRef):
            return Path(ref.locator)
        return Path(ref).expanduser()


def parse_session_ref_string(raw: str) -> tuple[str, str] | None:
    """Return ``(harness, session_id)`` for ``harness:id``, else None.

    Rejects filesystem paths (``/``, ``~``, ``\\``) so a Windows drive or
    a POSIX path is never treated as a harness id.

    :param raw: Catalog path or control ``session`` argument.
    :returns: Pair when *raw* is a known ``harness:id``.
    """
    text = (raw or "").strip()
    if not text or text.startswith("~") or "/" in text or "\\" in text:
        return None
    head, sep, tail = text.partition(":")
    if not sep or not tail or head not in HARNESS_IDS:
        return None
    return head, tail


__all__ = [
    "HARNESS_IDS",
    "SessionRef",
    "parse_session_ref_string",
]
