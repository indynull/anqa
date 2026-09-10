"""LocalSessionAccess domain façade (in-process, no control socket)."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.session.access import (
    LocalSessionAccess,
    RemoteSessionAccess,
    catalog_list_next_offset,
    filter_session_catalog,
)


def test_filter_session_catalog_query_and_limit() -> None:
    rows = [
        {
            "sessionId": "a",
            "path": "/tmp/a",
            "title": "Alpha Rocket",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "",
        },
        {
            "sessionId": "b",
            "path": "/tmp/b",
            "title": "Host session",
            "label": "",
            "model": "grok",
            "status": "running",
            "outcome": "",
        },
    ]
    full = filter_session_catalog(rows)
    assert full["total"] == 2
    assert full["matched"] == 2
    assert len(full["sessions"]) == 2

    host_only = filter_session_catalog(rows, query="host")
    assert host_only["matched"] == 1
    assert host_only["sessions"][0]["sessionId"] == "b"

    casefold = filter_session_catalog(rows, query="ROCKET")
    assert casefold["matched"] == 1
    assert casefold["sessions"][0]["sessionId"] == "a"

    limited = filter_session_catalog(rows, limit=1)
    assert len(limited["sessions"]) == 1
    assert limited["matched"] == 2


def test_filter_session_catalog_tag_comma_and() -> None:
    rows = [
        {
            "sessionId": "a",
            "title": "Alpha",
            "tags": ["review", "ui"],
        },
        {
            "sessionId": "b",
            "title": "Beta",
            "tags": ["review"],
        },
    ]
    both = filter_session_catalog(rows, query="tag:review,ui")
    assert both["matched"] == 1
    assert both["sessions"][0]["sessionId"] == "a"
    either = filter_session_catalog(rows, query="tag:review")
    assert either["matched"] == 2


def test_filter_session_catalog_offset_pages() -> None:
    rows = [
        {
            "sessionId": "a",
            "title": "Alpha",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "",
        },
        {
            "sessionId": "b",
            "title": "Beta",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "",
        },
        {
            "sessionId": "c",
            "title": "Gamma",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "",
        },
    ]
    first = filter_session_catalog(rows, limit=2, offset=0)
    assert [r["sessionId"] for r in first["sessions"]] == ["a", "b"]
    assert first["matched"] == 3
    second = filter_session_catalog(rows, limit=2, offset=2)
    assert [r["sessionId"] for r in second["sessions"]] == ["c"]
    assert second["matched"] == 3
    past = filter_session_catalog(rows, limit=2, offset=9)
    assert past["sessions"] == []
    assert past["matched"] == 3


def test_filter_session_catalog_before_reaches_past_the_first_page() -> None:
    """``before:`` matches old rows that sit after the newest 200."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(tz=UTC)
    rows = []
    for i in range(200):
        rows.append(
            {
                "sessionId": f"new-{i}",
                "title": f"New {i}",
                "updatedAt": (now - timedelta(days=1)).isoformat(),
            }
        )
    rows.append(
        {
            "sessionId": "old-1",
            "title": "Old session",
            "updatedAt": (now - timedelta(days=20)).isoformat(),
        }
    )
    first_page = filter_session_catalog(rows, limit=200)
    assert first_page["matched"] == 201
    assert len(first_page["sessions"]) == 200
    assert all(str(r["sessionId"]).startswith("new-") for r in first_page["sessions"])
    hit = filter_session_catalog(rows, query="before:10 days ago")
    assert hit["matched"] == 1
    assert hit["sessions"][0]["sessionId"] == "old-1"


def test_catalog_list_next_offset() -> None:
    assert catalog_list_next_offset(0, 200, 200, 450) == 200
    assert catalog_list_next_offset(200, 200, 200, 450) == 400
    assert catalog_list_next_offset(400, 50, 200, 450) is None
    assert catalog_list_next_offset(0, 200, 200, 200) is None
    assert catalog_list_next_offset(200, 200, 200, 450, stalled=True) is None
    assert catalog_list_next_offset(0, 0, 200, 10) is None


def test_filter_session_catalog_query_ignores_path() -> None:
    """Substring search must not match the filesystem path (``~/.grok/sessions``)."""
    rows = [
        {
            "sessionId": "019abc",
            "path": "/home/ali/.grok/sessions/019abc",
            "title": "Fix the palette",
            "label": "",
            "model": "other",
            "status": "complete",
            "outcome": "success",
        },
        {
            "sessionId": "work-1",
            "path": "/tmp/work/runs/traces/work-1",
            "title": "Review",
            "label": "",
            "model": "other",
            "status": "running",
            "outcome": "",
        },
    ]
    by_path = filter_session_catalog(rows, query=".grok/sessions")
    assert by_path["matched"] == 0
    by_title = filter_session_catalog(rows, query="palette")
    assert by_title["matched"] == 1
    assert by_title["sessions"][0]["sessionId"] == "019abc"
    by_id = filter_session_catalog(rows, query="019abc")
    assert by_id["matched"] == 1
    assert by_id["sessions"][0]["sessionId"] == "019abc"


def test_local_access_list_and_missing_session(tmp_path: Path) -> None:
    session = tmp_path / "sess-one"
    session.mkdir()
    (session / "summary.json").write_text("{}", encoding="utf-8")
    (session / "signals.json").write_text("{}", encoding="utf-8")

    def resolve(ref: str) -> Path | None:
        if ref in {session.name, str(session)}:
            return session
        p = Path(ref)
        return p if p.is_dir() else None

    access = LocalSessionAccess(
        resolve_session=resolve,
        list_sessions=lambda: [
            {
                "sessionId": session.name,
                "path": str(session),
                "title": "One",
            }
        ],
    )
    listed = access.list_sessions(query="one")
    assert listed["matched"] == 1
    assert listed["sessions"][0]["sessionId"] == session.name

    with pytest.raises(FileNotFoundError):
        access.session_overview("missing-id")

    got = access.session_overview(session.name)
    assert got.get("sessionId") == session.name or "path" in got

    missing = access.tags_get(session.name)
    assert missing["tags"] == []
    written = access.tags_set([session.name], ["Review", "ui"])
    assert written["tags"] == ["Review", "ui"]
    assert access.tags_get(session.name)["tags"] == ["Review", "ui"]
    assert "Review" in written["vocabulary"]


def test_local_access_delete_sessions_drops_catalog(tmp_path: Path) -> None:
    from anqa.session.catalog import SessionCatalogCache

    traces = tmp_path / "traces"
    sess = traces / "del-me"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    cache = SessionCatalogCache(traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    assert any(str(row.get("sessionId")) == "del-me" for row in cache.get())

    def resolve(ref: str) -> Path | None:
        if ref in {"del-me", str(sess), "grok:del-me"}:
            return sess if sess.exists() else None
        return None

    access = LocalSessionAccess(resolve_session=resolve, list_sessions=cache)
    stats = access.delete_sessions(["grok:del-me"])
    assert int(stats["deleted"] or 0) == 1
    assert not sess.exists()
    assert all(str(row.get("sessionId")) != "del-me" for row in cache.get())


@pytest.mark.asyncio
async def test_remote_access_session_diff_forwards() -> None:
    """Attached terminal client loads Diff through session/diff."""

    class _Client:
        async def session_diff(self, session: str) -> dict[str, object]:
            return {
                "sessionId": session,
                "source": "rewind_points",
                "points": [
                    {
                        "key": "1",
                        "source": "rewind_points",
                        "files": [
                            {
                                "path": "app.py",
                                "kind": "edit",
                                "added": 1,
                                "removed": 0,
                                "unified": "+x\n",
                            }
                        ],
                    }
                ],
            }

    access = RemoteSessionAccess(_Client())  # type: ignore[arg-type]
    body = await access.session_diff("grok:sess")
    assert body["source"] == "rewind_points"
    assert body["points"][0]["files"][0]["path"] == "app.py"
