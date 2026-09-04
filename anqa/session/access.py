"""Session data façade: one implementation for serve and all clients.

``LocalSessionAccess`` runs domain loaders in-process (the serve owner).
``RemoteSessionAccess`` wraps :class:`~anqa.control.client.ControlClient`
with the async methods the terminal app actually calls.

Control JSON-RPC is the multi-process binding of this façade — not a second
catalog/timeline stack.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ..harness import views as harness_views
from ..harness.ref import SessionRef
from ..harness.registry import resolve_session_ref
from ..models import JsonObject, JsonValue
from ..notes import (
    NoteEntry,
    NotesSnapshot,
    delete_note,
    load_schema,
    notes_snapshot,
    upsert_note,
)
from ..session.control_views import (
    DEFAULT_CONTENT_CHARS,
    DEFAULT_TIMELINE_LIMIT,
    MAX_CONTENT_CHARS,
    MAX_TIMELINE_LIMIT,
    build_session_diff,
    build_session_overview,
    build_session_timeline,
    build_session_turns,
)
from .document import SUPPORTED_FORMATS, render_editor_document

if TYPE_CHECKING:
    from ..control.client import ControlClient

type SessionResolver = Callable[[str], Path | None]
type SessionLister = Callable[[], list[JsonObject]]

DEFAULT_SESSION_LIST_LIMIT = 200


def catalog_list_next_offset(
    offset: int,
    batch_len: int,
    page: int,
    matched: int,
    *,
    stalled: bool = False,
) -> int | None:
    """Next ``session/list`` offset, or ``None`` when the drain is done.

    Stops on an empty or short page, a repeated first row (owner ignored
    ``offset``), or when accumulated rows cover ``matched``.
    """
    if stalled or batch_len <= 0 or page <= 0:
        return None
    nxt = offset + batch_len
    if batch_len < page:
        return None
    if matched > 0 and nxt >= matched:
        return None
    return nxt


def filter_session_catalog(
    sessions: list[JsonObject],
    *,
    query: str = "",
    limit: int | None = None,
    offset: int = 0,
) -> JsonObject:
    """Filter and page a catalog snapshot for ``session/list``.

    ``query`` is the catalog language (bare words plus ``is:`` / ``has:plan`` /
    ``plans:>=N`` / ``in:``). See :mod:`anqa.session.query`.

    :param sessions: Full catalog rows (already shaped for the wire).
    :param query: Catalog query string.
    :param limit: Page size after filtering; ``None`` means default cap.
    :param offset: Rows to skip after filtering (default 0). Unknown to
        older clients; omitting it keeps the first page.
    :returns: Mapping with ``sessions``, ``total``, and ``matched``.
    """
    from .catalog import public_catalog_row
    from .query import CatalogQueryRow, row_matches_query

    sessions = [public_catalog_row(row) for row in sessions]
    needle = (query or "").strip()
    if needle:
        matched = [
            row for row in sessions if row_matches_query(CatalogQueryRow.from_wire(row), needle)
        ]
    else:
        matched = list(sessions)
    # Preserve catalog newest-first order (do not re-rank by path/id here).
    cap = DEFAULT_SESSION_LIST_LIMIT if limit is None else max(0, limit)
    start = max(0, int(offset))
    sessions_out: list[JsonValue] = list(matched[start : start + cap])
    return {
        "sessions": sessions_out,
        "total": len(sessions),
        "matched": len(matched),
    }


def notes_snapshot_mapping(snapshot: NotesSnapshot) -> JsonObject:
    """Wire mapping for a notes snapshot (shared by access + control)."""
    schema = load_schema()
    return {
        "revision": snapshot.revision,
        "schema": {
            "id": schema.schema_id,
            "fields": [
                {
                    "id": field.id,
                    "label": field.label,
                    "choices": list(field.choices),
                    "pick": field.pick,
                }
                for field in schema.fields
            ],
        },
        "notes": [
            {
                "id": note.id,
                "turnIndex": note.turn_index,
                "source": note.source,
                "fields": dict(note.fields),
                "eventIndices": list(note.event_indices),
                "createdAt": note.created_at,
                "updatedAt": note.updated_at,
            }
            for note in snapshot.doc.sorted_notes()
        ],
    }


class LocalSessionAccess:
    """In-process domain façade (control owner / unit tests)."""

    def __init__(
        self,
        *,
        resolve_session: SessionResolver,
        list_sessions: SessionLister | None = None,
    ) -> None:
        self._resolve = resolve_session
        self._list = list_sessions

    def resolve_session(self, reference: str) -> Path | None:
        """Map a session id or path to a directory, or None."""
        return self._resolve(reference)

    def require_ref(self, reference: str) -> SessionRef:
        """Resolve *reference* to a :class:`SessionRef`."""
        found = resolve_session_ref(reference, path_resolve=self._resolve)
        if found is None:
            raise FileNotFoundError(f"session not found: {reference}")
        return found

    def require_session(self, reference: str) -> Path:
        """Resolve *reference* to the notes / control directory.

        Directory locators are the session tree. File or database locators
        use the anqa overlay directory.
        """
        found = resolve_session_ref(reference, path_resolve=self._resolve)
        if found is None:
            raise FileNotFoundError(f"session not found: {reference}")
        if found.locator.is_dir():
            return found.locator
        overlay = found.overlay_dir()
        overlay.mkdir(parents=True, exist_ok=True)
        return overlay

    def _directory_session(self, ref: SessionRef) -> bool:
        return ref.locator.is_dir()

    def list_sessions(
        self,
        *,
        query: str = "",
        limit: int | None = None,
        offset: int = 0,
        since_revision: int | None = None,
    ) -> JsonObject:
        """Catalog snapshot (``sessions`` / ``total`` / ``matched``)."""
        list_for_rpc = getattr(self._list, "list_for_rpc", None)
        if callable(list_for_rpc):
            return list_for_rpc(
                query=query,
                limit=limit,
                offset=offset,
                since_revision=since_revision,
            )
        catalog = list(self._list()) if self._list is not None else []
        out = filter_session_catalog(catalog, query=query, limit=limit, offset=offset)
        return {
            "sessions": out["sessions"],
            "total": out["total"],
            "matched": out["matched"],
            "revision": 0,
            "unchanged": False,
            "removed": [],
            "delta": False,
        }

    def session_overview(
        self,
        session: str,
    ) -> JsonObject:
        """Meta + turns + notes (timeline rows via session/timeline)."""
        ref = self.require_ref(session)
        if self._directory_session(ref):
            return build_session_overview(ref.locator)
        return harness_views.session_overview(ref)

    def session_timeline(
        self,
        session: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        event_type: str = "",
        kind: str = "",
        query: str = "",
        prompt_index: int | None = None,
        around_index: int | None = None,
        at_index: int | None = None,
        content_chars: int | None = None,
    ) -> JsonObject:
        """Paged timeline events."""
        ref = self.require_ref(session)
        lim = (
            DEFAULT_TIMELINE_LIMIT if limit is None else max(0, min(int(limit), MAX_TIMELINE_LIMIT))
        )
        cc = (
            DEFAULT_CONTENT_CHARS
            if content_chars is None
            else max(0, min(int(content_chars), MAX_CONTENT_CHARS))
        )
        if not self._directory_session(ref):
            return harness_views.session_timeline(
                ref,
                offset=max(0, int(offset)),
                limit=lim,
                event_type=event_type,
                kind=kind,
                query=query,
                prompt_index=prompt_index,
                around_index=around_index,
                at_index=at_index,
                content_chars=cc,
            )
        return build_session_timeline(
            ref.locator,
            offset=max(0, int(offset)),
            limit=lim,
            event_type=event_type,
            kind=kind,
            query=query,
            prompt_index=prompt_index,
            around_index=around_index,
            at_index=at_index,
            content_chars=cc,
        )

    def session_turns(self, session: str, query: str = "") -> JsonObject:
        """Turn segments."""
        ref = self.require_ref(session)
        if self._directory_session(ref):
            return build_session_turns(ref.locator, query=query)
        return harness_views.session_turns(ref, query=query)

    def session_diff(self, session: str) -> JsonObject:
        """Rewind snapshots or approximate write/edit tool patches."""
        ref = self.require_ref(session)
        if self._directory_session(ref):
            return build_session_diff(ref.locator)
        return harness_views.session_diff(ref)

    def session_import(self, path: Path | str) -> JsonObject:
        """Open an archive or export and return the catalog ref."""
        from .imports import import_session

        result = import_session(path)
        return {
            "session": result.ref.ref_string(),
            "sessionId": result.ref.session_id,
            "harness": result.ref.harness,
            "imported": True,
            "replaced": result.replaced,
            "opened": True,
        }

    def session_render(
        self,
        session: str,
        *,
        format: str = "org",
        bodies: bool = True,
        prompt_index: int | None = None,
    ) -> JsonObject:
        """Editor projection document."""
        fmt = (format or "org").strip().lower() or "org"
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"unsupported editor format: {fmt}")
        path = self.require_session(session)
        document = render_editor_document(
            path, format=fmt, bodies=bodies, prompt_index=prompt_index
        )
        return {
            "sessionId": document.session_id,
            "notesRevision": document.notes_revision,
            "promptIndexes": list(document.prompt_indexes),
            "format": document.format,
            "contentType": document.content_type,
            "text": document.text,
            "bodies": bodies,
        }

    def notes_list(self, session: str) -> JsonObject:
        """Notes snapshot mapping."""
        return notes_snapshot_mapping(notes_snapshot(self.require_session(session)))

    def notes_upsert(
        self,
        session: str,
        note: NoteEntry,
        *,
        expected_revision: str,
    ) -> JsonObject:
        """Upsert a note; return new snapshot mapping."""
        path = self.require_session(session)
        snap = upsert_note(path, note, expected_revision=expected_revision)
        return notes_snapshot_mapping(snap)

    def notes_delete(
        self,
        session: str,
        note_id: str,
        *,
        expected_revision: str,
    ) -> JsonObject:
        """Delete a note; return new snapshot mapping."""
        path = self.require_session(session)
        snap = delete_note(path, note_id, expected_revision=expected_revision)
        return notes_snapshot_mapping(snap)


class RemoteSessionAccess:
    """Async façade over :class:`~anqa.control.client.ControlClient`."""

    def __init__(self, client: ControlClient) -> None:
        self._client = client

    async def list_sessions(
        self,
        *,
        query: str = "",
        limit: int | None = None,
        offset: int = 0,
        since_revision: int | None = None,
    ) -> JsonObject:
        return await self._client.session_list(
            query=query,
            limit=limit,
            offset=offset,
            since_revision=since_revision,
        )

    async def session_import(self, path: str) -> JsonObject:
        return await self._client.session_import(path)

    async def session_overview(
        self,
        session: str,
    ) -> JsonObject:
        return await self._client.session_overview(session)

    async def session_timeline(
        self,
        session: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        event_type: str = "",
        kind: str = "",
        query: str = "",
        prompt_index: int | None = None,
        around_index: int | None = None,
        at_index: int | None = None,
        content_chars: int | None = None,
    ) -> JsonObject:
        return await self._client.session_timeline(
            session,
            offset=offset,
            limit=limit,
            event_type=event_type,
            kind=kind,
            query=query,
            prompt_index=prompt_index,
            around_index=around_index,
            at_index=at_index,
            content_chars=content_chars,
        )

    async def session_turns(self, session: str, query: str = "") -> JsonObject:
        return await self._client.session_turns(session, query=query)

    async def session_diff(self, session: str) -> JsonObject:
        return await self._client.session_diff(session)

    async def notes_list(self, session: str) -> JsonObject:
        return await self._client.notes_list(session)

    async def notes_upsert(
        self,
        session: str,
        note: JsonObject,
        *,
        expected_revision: str,
    ) -> JsonObject:
        return await self._client.notes_upsert(session, note, expected_revision=expected_revision)

    async def notes_delete(
        self,
        session: str,
        note_id: str,
        *,
        expected_revision: str,
    ) -> JsonObject:
        return await self._client.notes_delete(
            session, note_id, expected_revision=expected_revision
        )


__all__ = [
    "DEFAULT_SESSION_LIST_LIMIT",
    "LocalSessionAccess",
    "RemoteSessionAccess",
    "SessionLister",
    "SessionResolver",
    "catalog_list_next_offset",
    "filter_session_catalog",
    "notes_snapshot_mapping",
]
