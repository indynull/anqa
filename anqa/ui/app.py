"""Main Textual application for anqa.

UI entry point only: catalog, notes, and control attach.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.suggester import Suggester
from textual.theme import Theme
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Input,
    Static,
    TextArea,
)

if TYPE_CHECKING:
    from ..keys import Keymap
from ..control.client import (
    HEAVY_RPC_TIMEOUT,
    ControlClient,
    listen_control_notifications,
)
from ..harness.registry import require_adapter
from ..models import JsonObject, ListStatus, SessionMeta, as_json_object, json_as_str
from ..paths import app_config_path
from ..session.access import (
    DEFAULT_SESSION_LIST_LIMIT,
    RemoteSessionAccess,
    catalog_list_next_offset,
)
from ..session.query import (
    CatalogQueryRow,
    apply_catalog_presence,
    apply_suggestion,
    row_matches_query,
    suggest_last_token,
)
from . import text as U
from .appearance import Appearance, appearance, tui_appearance
from .bindings import (
    APP_SESSIONS,
    SESSION_HOME_ACTIONS,
    focus_primary_list,
)
from .brand_mark import AppChrome, AppFooter
from .control_notice import control_operator_text
from .data_table import (
    cursor_row_key,
    preserving_scroll,
    restore_cursor,
    set_marker_column,
    style_data_table,
    update_row_cell,
)
from .i18n import join_ui, setup_i18n, t
from .keys import format_key_chord
from .query_highlight import CatalogQueryHighlighter
from .screens.browser import BrowserScreen
from .search import SearchDebounce, SearchFlight
from .theme import (
    AUTO_NAMES,
    family_of_theme,
    register_catalog_themes,
    resolve_theme,
)
from .threads import call_ui
from .widgets.controls import FILTER_BAR_CLASS, FILTER_LABEL_CLASS

logger = logging.getLogger(__name__)


def _coerce_select_value(value, *, default=None):
    """Normalize Textual Select values (sentinel / None) to a plain choice."""
    if value is None:
        return default
    try:
        from textual.widgets import Select as _Select

        if value == getattr(_Select, "BLANK", object()):
            return default
    except Exception:
        pass
    name = type(value).__name__
    if name in ("NoSelection", "MissingValue", "Null", "_NoSelection"):
        return default
    if not isinstance(value, (str, int, float, bool)):
        return default
    return value


def _attach_catalog_flags(meta: SessionMeta) -> None:
    """Set cheap ``has:`` flags from disk (offline home list)."""
    from ..session.sources import session_run_dir

    apply_catalog_presence(meta)
    if not (meta.run_dir or "").strip():
        meta.run_dir = session_run_dir(meta.session_dir)


class SessionQuerySuggester(Suggester):
    """Last-token completion for the home catalog query."""

    def __init__(self, app: AnqaApp) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self._app = app

    async def get_suggestion(self, value: str) -> str | None:
        hits = suggest_last_token(
            value,
            models=self._app.query_model_values(),
            paths=self._app.query_path_values(),
            tags=self._app.query_tag_values(),
        )
        if not hits:
            return None
        return apply_suggestion(value, hits[0]).rstrip()


def first_home_list_fetch() -> dict[str, int | bool]:
    """First attach ``session/list``: one page, no matched drain."""
    return {
        "drain": False,
        "limit": int(DEFAULT_SESSION_LIST_LIMIT),
        "offset": 0,
        "since_revision": 0,
    }


class AnqaApp(App):
    """anqa — inspect coding-agent harness sessions."""

    TITLE = "anqa"
    SUB_TITLE = ""
    CSS_PATH = "app.tcss"
    # Filter is first in compose; default ``*`` would type leftover CSI/Kitty
    # replies into the search box on launch.
    AUTO_FOCUS = "#session-table"
    BINDINGS = [*APP_SESSIONS]
    COMMAND_PALETTE_DISPLAY = "Ctrl+P"
    # Textual text selection (drag) + OSC 52 copy; default is True but be explicit.
    ALLOW_SELECT = True
    # Debounce for copy toasts (see notify_copied).
    _copy_notify_at: float = 0.0
    _copy_notify_msg: str = ""

    def get_key_display(self, binding: Binding) -> str:
        """Footer / key panel: Ctrl+S, not caret ^s or unicode glyphs."""
        if binding.key_display:
            return binding.key_display
        bid = getattr(binding, "id", None)
        keymap = getattr(self, "_resolved_keymap", None)
        if bid and keymap is not None:
            from anqa.keys import chord_has_sequence, format_leader_chord

            try:
                chord = keymap.binding(bid).chord
            except KeyError:
                chord = ""
            if chord and chord_has_sequence(chord):
                raw = format_leader_chord(keymap.leader, chord)
                return " ".join(format_key_chord(part) for part in raw.split())
        return format_key_chord(binding.key)

    def get_system_commands(self, screen: Screen):
        """Populate Ctrl+P palette with context-aware actions."""
        yield from super().get_system_commands(screen)
        from .commands import yield_app_commands

        for title, help_text, callback in yield_app_commands(self, screen):
            yield SystemCommand(title, help_text, callback)

    def notify_copied(self, message: str) -> None:
        """Show a copy toast, suppressing rapid repeats of the same message.

        Drag-end auto-copy and ``y`` can fire often; stacked toasts are noise.
        """
        now = time.monotonic()
        last_at = float(getattr(self, "_copy_notify_at", 0.0) or 0.0)
        last_msg = str(getattr(self, "_copy_notify_msg", "") or "")
        if message == last_msg and (now - last_at) < 1.5:
            return
        self._copy_notify_at = now
        self._copy_notify_msg = message
        self.notify(message, severity="information", timeout=2.0)

    def _copy_live_selection(self) -> bool:
        """Copy screen text selection when non-empty.

        :returns: True when text was placed on the clipboard.
        """
        try:
            selected = self.screen.get_selected_text()
        except Exception:
            return False
        if selected is None or selected == "":
            return False
        self.copy_to_clipboard(selected)
        self.notify_copied(t("ui-copied-selection"))
        return True

    def on_text_selected(self, event: events.TextSelected) -> None:
        """Auto-copy when a mouse drag selection ends (Textual posts on mouse-up).

        Pure clicks clear the selection before this event, so they no-op.
        Extract uses unwrapped Content plain (soft-wrap spans stay complete).
        """
        self._copy_live_selection()

    def action_help_quit(self) -> None:
        """Ctrl+C: copy live selection when present, else Textual's quit hint.

        Textual binds Ctrl+C on the app to ``help_quit`` (system). That shadows
        the screen's ``copy_text`` binding. With a drag selection, copy that
        plain text (same as ``y`` / Ctrl+Shift+C for selections). Without a
        selection, show the quit hint — full-pane yank stays on ``y``.
        """
        if self._copy_live_selection():
            return
        # Prefer screen copy_detail when the focused browser can yank a body
        # via the same path as ``y`` (no selection). Keeps Ctrl+C aligned with
        # multipane polish without always fighting the quit chord.
        screen = self.screen
        copy_detail = getattr(screen, "action_copy_detail", None)
        if callable(copy_detail):
            try:
                focused = getattr(screen, "focused", None)
                from .selectable_static import is_extractable_static

                if is_extractable_static(focused):
                    copy_detail()
                    return
            except Exception:
                logger.debug("Ctrl+C focused-body copy failed", exc_info=True)
        for key, active_binding in self.active_bindings.items():
            if active_binding.binding.action in ("quit", "app.quit"):
                self.notify(
                    t("ui-press-key-to-quit", key=key),
                    title=t("ui-want-to-quit-title"),
                )
                return

    def __init__(
        self,
        traces_path: Path | None = None,
        *,
        config_path: Path | None = None,
        control_socket: Path | None = None,
        control_attach_only: bool = False,
        initial_session: Path | None = None,
        initial_prompt_index: int | None = None,
        **kwargs,
    ) -> None:
        setup_i18n()
        super().__init__(**kwargs)
        from ..paths import resolve_catalog_root

        self.traces_path: Path | None = (
            resolve_catalog_root(traces_path) if traces_path is not None else None
        )
        self._run_status_timer: Timer | None = None
        self._live_sessions_timer: Timer | None = None
        self._live_sessions_heartbeat_timer: Timer | None = None
        self._traces_watch: object | None = None
        self._live_sessions_busy = False
        self._live_meta_heartbeat_busy = False
        self._live_sessions_last_scan: float = 0.0
        # session_dir key → last session_trace_mtime seen on a live poll.
        self._session_mtimes: dict[str, float] = {}
        self._live_full_walk_last: float = 0.0
        self._share_notified: set[str] = set()
        self._populate_busy = False
        self._sessions_table_primed = False
        self._session_row_fp: dict[str, str] = {}
        self._exiting = False
        self._config_path = Path(config_path).expanduser() if config_path else None
        self._control_socket = (
            Path(control_socket).expanduser() if control_socket is not None else None
        )
        # True when attached to a live control owner (TUI never owns the socket).
        self._control_attached: bool = False
        self._import_opening_sid: str = ""
        # When true, load catalog via session/list and never bind the socket.
        self._control_attach_only: bool = bool(control_attach_only)
        self._control_notify_stop: asyncio.Event | None = None
        self._catalog_revision: int = 0
        if initial_session is None:
            self._initial_session = None
        else:
            from ..harness.ref import catalog_session_key, parse_session_ref_string

            raw = Path(initial_session)
            key = catalog_session_key(raw)
            self._initial_session = (
                Path(key)
                if parse_session_ref_string(key) is not None
                else raw.expanduser().resolve()
            )
        self._initial_prompt_index = initial_prompt_index
        self._self_test_summary: str = ""
        self._copy_notify_at = 0.0
        self._copy_notify_msg = ""
        self._meta_only: list[tuple[SessionMeta, str]] = []
        # Bumps when a sessions catalog load starts; stale workers skip applying.
        self._sessions_load_gen: int = 0
        self._sessions_catalog_busy: bool = False
        self._sessions_reload_timer: Timer | None = None
        self._appearance_timer: Timer | None = None
        self._host_look: Appearance = "dark"
        self._applying_saved_theme = False
        self._pending_include_host: bool | None = None
        self._pending_sessions_reload_quiet: bool = False
        self._selected: set[str] = set()
        self._select_all_scope: set[str] = set()
        self._session_search: str = ""
        self._session_search_applied: str = ""
        self._session_search_debounce = SearchDebounce()
        self._session_flight = SearchFlight()
        self._catalog_query_slice: bool = False
        self._delete_pending_paths: list[Path] | None = None
        self._delete_cursor_key: str | None = None
        self._delete_row_keys_snapshot: list[str] | None = None
        self._config: JsonObject = self._load_config()
        self._theme_persist = False
        register_catalog_themes(self)
        early = str(self._config.get("theme") or "").strip() or "auto"
        self._host_look = self._look_for_pref()
        try:
            self.theme = self._resolved_theme(early)
        except Exception:
            logger.debug(t("ui-failed-to-apply-saved-theme-r"), early)
        self._resolved_keymap: Keymap | None = None
        self._leader_armed = False
        self._leader_timer: Timer | None = None
        self._apply_resolved_keymap()

    def compose(self) -> ComposeResult:
        yield AppChrome()
        with Vertical():
            yield Static("", id="session-summary")
            with Horizontal(id="session-filter-bar", classes=FILTER_BAR_CLASS):
                yield Static(U.filter_label(), classes=FILTER_LABEL_CLASS)
                yield Input(
                    placeholder=U.search_sessions_placeholder(),
                    id="session-search-input",
                    highlighter=CatalogQueryHighlighter(),
                    suggester=SessionQuerySuggester(self),
                )
            yield Static("", id="session-query-hints", classes="session-query-hints")
            yield DataTable(id="session-table")
        yield AppFooter()

    def _session_traces_root(self) -> Path:
        """Traces directory fixed for this process (CLI / constructor only)."""
        if self.traces_path:
            return Path(self.traces_path).expanduser()
        from ..paths import default_host_sessions_root

        return default_host_sessions_root()

    def _load_config(self) -> JsonObject:
        """Load the canonical app config (defaults when the file is missing)."""
        from ..config import config_dump, load_app_config
        from ..job_pools import configure_job_pools

        cfg = load_app_config(self._config_path)
        configure_job_pools(live_refresh_workers=cfg.live_refresh_workers)
        return config_dump(cfg)

    def _save_config(self) -> None:
        """Write shared prefs through :mod:`anqa.config` (canonical object)."""
        from ..config import config_dump, load_app_config, update_app_config

        try:
            update_app_config(
                self._config_path,
                theme=str(self._config.get("theme") or "auto"),
                follow_os=self._config.get("follow_os") is True,
                auto_anqad=self._config.get("auto_anqad") is not False,
            )
        except OSError:
            logger.warning(
                t("ui-failed-to-write-prefs-to-s"),
                app_config_path(),
                exc_info=True,
            )
            return
        self._config = config_dump(load_app_config(self._config_path))

    def _theme_names(self) -> list[str]:
        try:
            return sorted(self.available_themes.keys())
        except Exception:
            return []

    def _follow_os(self) -> bool:
        return self._config.get("follow_os") is True

    def _look_for_pref(self) -> Appearance:
        """Terminal look for ``auto``; desktop look for a named ``follow_os`` pair."""
        if self._theme_pref_is_auto():
            return tui_appearance()
        return appearance()

    def _resolved_theme(self, pref: str) -> str:
        return resolve_theme(
            pref,
            self._host_look,
            follow_os=self._follow_os(),
        )

    def _theme_pref_is_auto(self) -> bool:
        return str(self._config.get("theme") or "").strip().casefold() in AUTO_NAMES

    def apply_saved_theme(self, *, save: bool = False) -> str | None:
        """Restore theme from config.toml (or keep current). Re-applied after refresh.

        Textual can reset ``self.theme`` during App/mount; setting only once in
        ``on_mount`` is unreliable. A pair pick stores the family and applies
        the desktop member.
        """
        pref = str(self._config.get("theme") or "").strip() or "auto"
        names = set(self._theme_names())
        self._host_look = self._look_for_pref()
        name = self._resolved_theme(pref)
        if name not in names:
            if not names:
                return None
            name = self.theme if self.theme in names else next(iter(sorted(names)))
        self._applying_saved_theme = True
        try:
            self.theme = name
        except Exception:
            logger.exception("failed to apply theme %s", name)
            return None
        finally:
            self._applying_saved_theme = False
        if save:
            self._save_config()
        return name

    def _enable_theme_persist(self) -> None:
        """Re-apply saved theme, then persist any later theme changes to disk.

        Covers Ctrl+P → Change theme and any other path that sets ``App.theme``.
        Subscribe only while the app is running — ``call_after_refresh`` can
        fire after a short Pilot unmount.
        """
        self.apply_saved_theme(save=False)
        if self._theme_persist:
            return
        if not getattr(self, "is_running", False):
            return
        self._theme_persist = True
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        if (self._follow_os() or self._theme_pref_is_auto()) and self._appearance_timer is None:
            self._appearance_timer = self.set_interval(2.0, self._follow_desktop_appearance)

    def _follow_desktop_appearance(self) -> None:
        """Repaint when the look that owns this pref changes."""
        if not (self._follow_os() or self._theme_pref_is_auto()):
            return
        if self._look_for_pref() != self._host_look:
            self.apply_saved_theme(save=False)

    def _apply_pair_member(self) -> None:
        """Paint the desktop member of the stored pair without rewriting config."""
        want = self._resolved_theme(str(self._config.get("theme") or ""))
        if not want or want == self.theme:
            return
        self._applying_saved_theme = True
        try:
            self.theme = want
        except Exception:
            return
        finally:
            self._applying_saved_theme = False

    def _on_theme_changed(self, theme: Theme) -> None:
        """Persist a pick. Pair names store the family and apply the desktop member."""
        if not self._theme_persist or self._applying_saved_theme:
            return
        name = (theme.name or self.theme or "").strip()
        if not name:
            return
        if name.casefold() in AUTO_NAMES:
            return
        family = family_of_theme(name)
        if family is not None:
            pref, follow = family, True
        else:
            pref, follow = name, False
        changed = self._config.get("theme") != pref or self._config.get("follow_os") is not follow
        if changed:
            self._config["theme"] = pref
            self._config["follow_os"] = follow
            self._save_config()
        if follow:
            if self._appearance_timer is None:
                self._appearance_timer = self.set_interval(2.0, self._follow_desktop_appearance)
            self._host_look = self._look_for_pref()
            self._apply_pair_member()

    def _apply_resolved_keymap(self) -> None:
        """Apply ``keys.toml`` remaps via Textual ``set_keymap``.

        A refused or missing overlay leaves catalog defaults (``load_keymap``).
        Sequence chords are unbound here and dispatched by the leader prefix.
        """
        from anqa.keys import load_keymap, textual_keymap

        keymap = load_keymap()
        self._resolved_keymap = keymap
        self.set_keymap(textual_keymap(keymap))
        if keymap.leader:
            self.bind(
                keymap.leader,
                "leader_idle",
                description=t("ui-leader"),
                show=True,
                key_display=keymap.leader,
            )

    def _leader_editing_focus(self) -> bool:
        """True when a typing field owns the key (Input / TextArea / notes)."""
        focused = self.focused
        return isinstance(focused, (Input, TextArea))

    def _leader_disarm(self) -> None:
        if self._leader_timer is not None:
            self._leader_timer.stop()
            self._leader_timer = None
        if self._leader_armed:
            self._leader_armed = False
            self.refresh_bindings()

    def _leader_arm(self) -> None:
        keymap = self._resolved_keymap
        timeout_ms = 800
        if keymap is not None and keymap.leader_timeout_ms:
            timeout_ms = keymap.leader_timeout_ms
        self._leader_disarm()
        self._leader_armed = True
        self._leader_timer = self.set_timer(timeout_ms / 1000.0, self._leader_disarm)
        self.refresh_bindings()

    def _leader_event_suffix(self, event: object) -> str:
        character = getattr(event, "character", None)
        if isinstance(character, str) and character:
            return character
        key = str(getattr(event, "key", "") or "")
        if key.startswith("shift+") and len(key) > 6:
            return key
        return key

    def _leader_is_leader_key(self, event: object) -> bool:
        keymap = self._resolved_keymap
        if keymap is None or not keymap.leader:
            return False
        leader = keymap.leader
        character = getattr(event, "character", None)
        if isinstance(character, str) and character == leader:
            return True
        key = str(getattr(event, "key", "") or "")
        if key == leader:
            return True
        punct = {";": "semicolon", "semicolon": ";"}
        if punct.get(key) == leader or punct.get(leader) == key:
            return True
        from anqa.keys import normalize_chord

        return normalize_chord(key) == normalize_chord(leader)

    async def _run_binding_id(self, action_id: str) -> None:
        """Dispatch *action_id* from the screen chain (home vs browser action)."""
        chain = getattr(self.screen, "_modal_binding_chain", ())
        for namespace, bindings in chain:
            for _key, binding in bindings:
                if getattr(binding, "id", None) != action_id:
                    continue
                if await self.run_action(binding.action, namespace):
                    return

    async def _handle_leader_key(self, event: object) -> bool:
        """Consume a leader prefix or ``leader+X`` dispatch. True when handled."""
        keymap = self._resolved_keymap
        if keymap is None or not keymap.leader:
            return False
        if self._leader_editing_focus():
            if self._leader_armed:
                self._leader_disarm()
            return False
        key = str(getattr(event, "key", "") or "")
        if key in {"escape", "esc"}:
            if self._leader_armed:
                self._leader_disarm()
                return True
            return False
        if self._leader_armed:
            self._leader_disarm()
            if self._leader_is_leader_key(event):
                return True
            suffix = self._leader_event_suffix(event)
            action_id = keymap.lookup_sequence(suffix)
            if action_id is not None:
                await self._run_binding_id(action_id)
            return True
        if self._leader_is_leader_key(event):
            self._leader_arm()
            return True
        return False

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, events.Key) and await self._handle_leader_key(event):
            event.stop()
            event.prevent_default()
            return
        await super().on_event(event)

    def action_leader_idle(self) -> None:
        """Footer slot while the leader is armed; dispatch is in on_event."""
        return

    def on_mount(self) -> None:
        self._apply_resolved_keymap()
        self.apply_saved_theme(save=False)
        self.call_after_refresh(self._enable_theme_persist)
        table = self.query_one("#session-table", DataTable)
        style_data_table(table)
        table.add_columns(
            " ",
            t("ui-title"),
            t("ui-harness"),
            t("ui-model"),
            t("ui-status"),
            t("ui-duration"),
            t("ui-context"),
            t("ui-events"),
        )
        self.sub_title = ""
        self._refresh_query_hints()
        # Attach-only: start control client first so home list loads via RPC.
        self._start_control_service()
        self._load_sessions(include_host=None)
        table.focus()
        self._schedule_live_sessions_poll()
        if self._initial_session is not None:
            self.call_after_refresh(
                self.open_session_path,
                self._initial_session,
                prompt_index=self._initial_prompt_index,
            )

    def _start_control_service(self) -> None:
        """Try attach to the control owner; the TUI never binds the socket.

        Does **not** mark attached until :meth:`_attach_control_client` succeeds
        at ``initialize``. Catalog load uses control only after that.
        """
        if self._control_socket is None:
            return
        # Intent: prefer control catalog when attach succeeds (never own socket).
        self._control_attach_only = True
        self._control_attached = False
        self.run_worker(
            self._attach_control_client(),
            name="editor-control-attach",
            group="editor-control-service",
            exclusive=True,
        )

    async def _attach_control_client(self) -> None:
        """Confirm the live owner, then start notify + switch catalog to control.

        On initialize failure, leave ``_control_attached`` false and toast.
        Catalog stays empty until attach succeeds (no disk fallback).
        """
        if self._control_socket is None:
            return
        ok = await self._confirm_control_attach()
        if not ok:
            self._control_attached = False
            with suppress(Exception):
                self.notify(
                    t("ui-control-socket-attach-failed"),
                    severity="error",
                    timeout=8,
                )
            return
        self._control_attached = True
        stop = asyncio.Event()
        self._control_notify_stop = stop
        # Separate long-lived worker so attach itself can finish cleanly.
        self.run_worker(
            self._control_notify_loop(stop),
            name="editor-control-notify",
            group="editor-control-notify",
            exclusive=True,
        )
        # First on_mount catalog is empty until attach; reload quietly.
        self._load_sessions(include_host=None, quiet=True)

    async def _control_notify_loop(self, stop: asyncio.Event) -> None:
        """Background: stay connected for session and notes notifies."""
        if self._control_socket is None:
            return
        await listen_control_notifications(
            self._control_socket,
            self._on_control_notification,
            client_name="anqa-tui-notify",
            stop=stop,
        )

    async def _on_control_notification(self, method: str, params: JsonObject) -> None:
        """Handle serve-side notify (session/selected, changed, notes)."""
        from ..models import json_as_int

        if self._exiting:
            return
        if method == "session/selected":
            sid = json_as_str(params.get("sessionId")).strip()
            if not sid:
                return
            if sid == getattr(self, "_import_opening_sid", ""):
                return
            raw_pi = params.get("promptIndex")
            prompt_index = None if raw_pi is None else json_as_int(raw_pi)
            # Resolve id → path via catalog rows or traces root.
            path = self._resolve_session_id_for_control(sid)
            if path is None:
                return
            self.call_later(
                self.open_session_path,
                path,
                prompt_index=prompt_index,
                notify_control=False,
            )
            return
        if method == "session/changed":
            sid = json_as_str(params.get("sessionId")).strip()
            raw = params.get("listChanged")
            self.call_later(self._control_session_changed_ui, sid, raw is not False)
            return
        if method == "notes/changed":
            sid = json_as_str(params.get("sessionId")).strip()
            self.call_later(self._control_notes_changed_ui, sid)
            return
        if method == "tags/changed":
            self.call_later(self._control_tags_changed_ui)
            return

    def _resolve_session_id_for_control(self, session_id: str) -> Path | None:
        """Map a session id from control notify to a local directory."""
        for meta, _label in self._meta_only:
            if session_id in (meta.session_id, meta.session_dir.name):
                return meta.session_dir
        for root in self._session_catalog_roots():
            candidate = root.path / session_id
            if candidate.is_dir():
                return candidate
        return None

    def _control_session_changed_ui(self, session_id: str, list_changed: bool = True) -> None:
        """Reload the home list only when list fields changed; refresh an open browser."""
        if not session_id or list_changed:
            self._schedule_sessions_reload(quiet=True)
        screen = self.screen
        if isinstance(screen, BrowserScreen) and session_id:
            try:
                if screen.session_dir.name == session_id:
                    screen._live_refresh_from_fs(heartbeat=False)
            except Exception:
                logger.debug("browser refresh on session/changed failed", exc_info=True)

    def _control_tags_changed_ui(self) -> None:
        """Reload the home list after ``tags/changed``."""
        self._schedule_sessions_reload(quiet=True)

    def _control_notes_changed_ui(self, session_id: str) -> None:
        screen = self.screen
        if not isinstance(screen, BrowserScreen) or not session_id:
            return
        try:
            if screen.notes_notify_matches(session_id):
                screen._load_notes()
                screen._update_notes_tab()
        except Exception:
            logger.debug("notes refresh on notes/changed failed", exc_info=True)

    def is_control_client(self) -> bool:
        """True only after successful control ``initialize`` against a live owner."""
        return bool(self._control_attached and self._control_socket is not None)

    def is_control_owner(self) -> bool:
        """Always false: headless ``anqad`` is the sole socket owner."""
        return False

    def control_client(self) -> ControlClient | None:
        """Return a client for the control socket when configured."""
        if self._control_socket is None:
            return None
        return ControlClient(
            self._control_socket,
            client_name="anqa-tui",
            timeout=HEAVY_RPC_TIMEOUT,
        )

    def session_access(self) -> RemoteSessionAccess | None:
        """Remote façade over the control owner (None when socket disabled)."""
        client = self.control_client()
        if client is None:
            return None
        return RemoteSessionAccess(client)

    async def control_session_list(
        self,
        *,
        query: str = "",
        limit: int | None = None,
    ) -> JsonObject:
        """Session catalog via control ``session/list`` (same path as HUD/editors)."""
        access = self.session_access()
        if access is None:
            return {"sessions": [], "total": 0, "matched": 0}
        return await access.list_sessions(query=query, limit=limit)

    async def _confirm_control_attach(self) -> bool:
        """Verify the live owner speaks our protocol.

        :returns: True when ``initialize`` succeeds; False when the socket is
            missing, dead, or the RPC fails.
        """
        client = self.control_client()
        if client is None:
            return False
        try:
            from ..control.server import PROTOCOL_VERSION, protocol_compatible

            result = await client.initialize()
            ver = result.get("protocolVersion")
            if not protocol_compatible(ver):
                logger.warning(
                    "Control owner at %s speaks protocol %s (need major of %s)",
                    self._control_socket,
                    ver,
                    PROTOCOL_VERSION,
                )
                return False
            logger.info(
                "Attached to control owner at %s (protocol %s)",
                self._control_socket,
                ver,
            )
            return True
        except Exception:
            logger.warning(
                "Control attach initialize failed at %s",
                self._control_socket,
                exc_info=True,
            )
            return False

    def control_session_selected(
        self,
        session_dir: Path,
        prompt_index: int | None,
    ) -> None:
        """TUI selection notify (serve broadcasts when notify RPC lands)."""
        _ = (session_dir, prompt_index)

    def control_session_changed(self, session_dir: Path) -> None:
        """TUI change notify (serve owns broadcast; no-op as client)."""
        _ = session_dir

    def control_notes_changed(self, session_dir: Path) -> None:
        """TUI notes notify (serve owns broadcast; no-op as client)."""
        _ = session_dir

    def _session_catalog_roots(self):
        """Adapter store roots for the home list."""
        return self._catalog_roots_for_load(include_host=None)

    def _origin_for_dir(self, session_dir: Path) -> str:
        """Host adapter store from the directory, or import when under the import store."""
        from ..paths import is_import_locator
        from ..session.sources import classify_session_origin

        if is_import_locator(session_dir):
            return "import"
        return classify_session_origin(session_dir)

    def _label_for_session(self, session_dir: Path) -> str:
        """Display path fragment relative to the catalog root."""
        from ..session.sources import default_catalog_root

        return self._derive_label(session_dir, default_catalog_root())

    def _begin_sessions_load(self) -> int:
        """Mark a new catalog load; return generation for stale-worker checks."""
        self._sessions_load_gen += 1
        self._sessions_catalog_busy = True
        return self._sessions_load_gen

    def _sessions_load_current(self, gen: int) -> bool:
        return gen == self._sessions_load_gen

    def _finish_sessions_load(self, gen: int) -> None:
        """Clear the catalog-loading flag when *gen* is still the active load."""
        if self._sessions_load_current(gen):
            self._sessions_catalog_busy = False

    def _schedule_sessions_reload(self, *, delay: float = 0.15, quiet: bool = False) -> None:
        """Debounce catalog reloads; snapshot host-pref for the pending fire.

        :param quiet: Skip scan/loaded toasts. A later loud request wins.
        """
        pending_quiet = True
        if self._sessions_reload_timer is not None:
            with suppress(Exception):
                self._sessions_reload_timer.stop()
            self._sessions_reload_timer = None
            pending_quiet = bool(self._pending_sessions_reload_quiet)
        self._pending_sessions_reload_quiet = bool(quiet and pending_quiet)
        self._pending_include_host = True
        self._sessions_reload_timer = self.set_timer(delay, self._fire_sessions_reload)

    def _fire_sessions_reload(self) -> None:
        self._sessions_reload_timer = None
        if self._exiting:
            return
        quiet = bool(self._pending_sessions_reload_quiet)
        self._pending_sessions_reload_quiet = False
        self._load_sessions(include_host=True, quiet=quiet)

    def _build_session_meta_rows(
        self,
        unique: list[Path],
        *,
        gen: int | None = None,
    ) -> list[tuple[SessionMeta, str]]:
        """Build list metas for *unique* dirs."""
        rows: list[tuple[SessionMeta, str]] = []
        for sd in unique:
            if gen is not None and not self._sessions_load_current(gen):
                return rows
            try:
                meta = require_adapter(sd).load_meta(sd)
            except Exception:
                logger.debug(t("ui-failed-to-load-session-meta-for-s"), sd, exc_info=True)
                continue
            _attach_catalog_flags(meta)
            label = self._label_for_session(sd)
            rows.append((meta, label))
        return rows

    def _apply_session_meta_rows(self, gen: int, rows: list[tuple[SessionMeta, str]]) -> bool:
        """Install *rows* if *gen* is still current. Returns False when superseded."""
        if not self._sessions_load_current(gen):
            return False
        self._meta_only = rows
        return True

    def _load_sessions_sync(self, root: Path | None = None) -> int:
        """Load session metas into ``_meta_only`` (any thread; no UI calls).

        :returns: Number of sessions loaded.
        """
        _ = root
        from ..session.sources import collect_session_dirs

        gen = self._begin_sessions_load()
        try:
            unique = collect_session_dirs(self._session_catalog_roots())
            if not unique:
                self._apply_session_meta_rows(gen, [])
                return 0
            rows = self._build_session_meta_rows(unique, gen=gen)
            if not self._apply_session_meta_rows(gen, rows):
                return 0
            return len(rows)
        finally:
            self._finish_sessions_load(gen)

    def _catalog_roots_for_load(self, *, include_host: bool | None = None):
        """Build scan roots. Host is included unless *include_host* is false."""
        from ..session.sources import is_adapter_store_root, session_scan_roots

        if include_host is None:
            include_host = True
        traces = self.traces_path
        if traces is not None and is_adapter_store_root(Path(traces)):
            include_host = True
        return session_scan_roots(
            traces_path=Path(traces) if traces is not None else None,
            include_host=bool(include_host),
        )

    def _fetch_control_catalog_sync(
        self,
        *,
        query: str = "",
        since_revision: int = 0,
        drain: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> JsonObject:
        """Blocking ``session/list`` (one page, delta poll, or full drain)."""

        from ..control.client import ControlClient

        sock = self._control_socket
        if sock is None:
            return {
                "sessions": [],
                "total": 0,
                "matched": 0,
                "revision": 0,
                "unchanged": False,
                "removed": [],
                "delta": False,
            }

        async def _run() -> JsonObject:
            client = ControlClient(sock, client_name="anqa-tui", timeout=HEAVY_RPC_TIMEOUT)
            if since_revision > 0 and not drain:
                return await client.session_list(
                    query=query,
                    limit=limit if limit is not None else 10_000,
                    offset=offset,
                    since_revision=since_revision,
                )
            if drain:
                return await client.session_list_all(query=query)
            return await client.session_list(
                query=query,
                limit=DEFAULT_SESSION_LIST_LIMIT if limit is None else limit,
                offset=offset,
            )

        return asyncio.run(_run())

    def _delete_sessions_via_control(self, paths: list[Path]) -> JsonObject:
        """Blocking ``session/delete`` against the live owner."""
        from ..control.client import ControlClient
        from ..control.server import ControlError, is_unknown_method

        sock = self._control_socket
        refs = [str(path) for path in paths]
        if sock is None:
            return {
                "deleted": 0,
                "requested": len(refs),
                "errors": ["control socket missing"],
            }

        async def _delete() -> JsonObject:
            client = ControlClient(sock, client_name="anqa-tui", timeout=HEAVY_RPC_TIMEOUT)
            return await client.session_delete(refs)

        try:
            return asyncio.run(_delete())
        except ControlError as exc:
            if is_unknown_method(exc):
                self._replace_stale_control_owner()
                try:
                    return asyncio.run(_delete())
                except ControlError as retry:
                    return {
                        "deleted": 0,
                        "requested": len(refs),
                        "errors": [retry.message],
                    }
            return {"deleted": 0, "requested": len(refs), "errors": [exc.message]}

    def _replace_stale_control_owner(self) -> None:
        """Stop an older owner and start one that speaks this protocol."""
        from ..control.daemon import ensure_control_daemon

        sock = self._control_socket
        if sock is None:
            return
        ensure_control_daemon(socket_path=sock, traces_path=self.traces_path)

    def _rows_from_catalog_wire(self, wire_rows: list[JsonObject]) -> list[tuple[SessionMeta, str]]:
        from ..session.catalog import session_meta_from_catalog_row

        rows: list[tuple[SessionMeta, str]] = []
        for raw in wire_rows:
            meta = session_meta_from_catalog_row(raw)
            if meta is None:
                continue
            label = str(raw.get("label") or meta.label)
            rows.append((meta, label))
        return rows

    def _merge_control_catalog_rows(
        self,
        incoming: list[JsonObject],
        removed: list[str],
    ) -> list[tuple[SessionMeta, str]]:
        drop = {sid for sid in removed if sid}
        merged = [
            (meta, label)
            for meta, label in self._meta_only
            if meta.session_id not in drop and meta.session_dir.name not in drop
        ]
        by_id = {meta.session_id: i for i, (meta, _label) in enumerate(merged)}
        for meta, label in self._rows_from_catalog_wire(incoming):
            idx = by_id.get(meta.session_id)
            if idx is None:
                by_id[meta.session_id] = len(merged)
                merged.append((meta, label))
            else:
                merged[idx] = (meta, label)
        return merged

    def _fill_remaining_catalog_pages(self, gen: int, listed: JsonObject, offset: int) -> None:
        """Fetch later ``session/list`` pages after first paint. Never drains."""
        page = int(DEFAULT_SESSION_LIST_LIMIT)
        raw = listed.get("sessions")
        batch_len = len(raw) if isinstance(raw, list) else 0
        matched_raw = listed.get("matched")
        matched = matched_raw if isinstance(matched_raw, int) else 0
        while True:
            nxt = catalog_list_next_offset(offset, batch_len, page, matched, stalled=False)
            if nxt is None or not self._sessions_load_current(gen):
                return
            nxt_listed = self._fetch_control_catalog_sync(drain=False, limit=page, offset=nxt)
            nxt_raw = nxt_listed.get("sessions")
            wire = (
                [as_json_object(r) for r in nxt_raw if isinstance(r, dict)]
                if isinstance(nxt_raw, list)
                else []
            )
            if not wire:
                return
            rows = self._merge_control_catalog_rows(wire, [])
            if not self._apply_session_meta_rows(gen, rows):
                return
            call_ui(self, self._rebuild_session_filters)
            call_ui(self, self._populate_session_table, force=True)
            rev_raw = nxt_listed.get("revision")
            if isinstance(rev_raw, int) and rev_raw > 0:
                self._catalog_revision = rev_raw
            offset = nxt
            batch_len = len(wire)
            nxt_matched = nxt_listed.get("matched")
            if isinstance(nxt_matched, int):
                matched = nxt_matched

    def _load_sessions_via_control(
        self,
        gen: int,
        *,
        quiet: bool = False,
    ) -> None:
        """Populate home list from control ``session/list`` (attach client path).

        Quiet/live polls send ``sinceRevision`` so an unchanged owner returns no
        rows and the table is not rebuilt. An applied Filter queries the full
        catalog (not the first newest page). Clearing that Filter refetches
        the unfiltered home page even when the owner revision is unchanged.

        :param quiet: Skip loaded/error notifications (live refresh / attach).
        """
        try:
            query = (self._session_search_applied or "").strip()
            since = int(self._catalog_revision or 0)
            use_delta = bool(quiet and since > 0 and not query and not self._catalog_query_slice)
            first: dict[str, int | bool] | None = None
            if query:
                result = self._fetch_control_catalog_sync(query=query, drain=True)
            elif use_delta:
                result = self._fetch_control_catalog_sync(
                    since_revision=since,
                    drain=False,
                )
            else:
                first = first_home_list_fetch()
                result = self._fetch_control_catalog_sync(
                    since_revision=int(first["since_revision"]),
                    drain=bool(first["drain"]),
                    limit=int(first["limit"]),
                    offset=int(first["offset"]),
                )
            if not self._sessions_load_current(gen):
                return
            rev_raw = result.get("revision")
            same_rev = isinstance(rev_raw, int) and rev_raw == since
            if result.get("unchanged") and same_rev:
                return
            is_delta = bool(result.get("delta")) and isinstance(rev_raw, int) and rev_raw > 0
            if use_delta and not is_delta:
                result = self._fetch_control_catalog_sync(drain=True)
                if not self._sessions_load_current(gen):
                    return
                rev_raw = result.get("revision")
                is_delta = False
            if isinstance(rev_raw, int) and rev_raw > 0:
                self._catalog_revision = rev_raw
            raw = result.get("sessions")
            wire_rows = (
                [as_json_object(r) for r in raw if isinstance(r, dict)]
                if isinstance(raw, list)
                else []
            )
            removed_raw = result.get("removed")
            removed = (
                [str(x) for x in removed_raw if str(x)] if isinstance(removed_raw, list) else []
            )
            if is_delta:
                rows = self._merge_control_catalog_rows(wire_rows, removed)
            else:
                rows = self._rows_from_catalog_wire(wire_rows)
            if not self._apply_session_meta_rows(gen, rows):
                return
            if not is_delta:
                self._catalog_query_slice = bool(query)
            n = len(rows)
            call_ui(self, self._rebuild_session_filters)
            call_ui(self, self._populate_session_table, force=True)
            if first is not None:
                self._fill_remaining_catalog_pages(gen, result, int(first["offset"]))
                n = len(self._meta_only)
            if not quiet:
                call_ui(
                    self,
                    self.notify,
                    t("notify-loaded-sessions", n=n),
                    severity="information",
                )
        except Exception as exc:
            logger.exception("control session/list failed for attach catalog")
            if not quiet:
                call_ui(
                    self,
                    self.notify,
                    control_operator_text(exc, fallback_id="notify-control-list-failed"),
                    severity="error",
                )
        finally:
            call_ui(self, self._finish_sessions_load, gen)

    @work(thread=True, exclusive=True, group="sessions-catalog")
    def _load_sessions(
        self,
        root: Path | None = None,
        *,
        include_host: bool | None = None,
        quiet: bool = False,
    ) -> None:
        """Load the home session list.

        Normal product path (control socket configured): only ``session/list``
        after a successful attach. Offline (``control_socket`` None / --no-serve):
        walk local work/traces. No silent dual path when attach is intended.

        :param quiet: Skip scan/loaded toasts (live refresh / attach).
        """
        _ = root
        gen = self._begin_sessions_load()
        if self._control_socket is not None:
            if self._control_attached:
                if (self._session_search_applied or "").strip():
                    call_ui(self, self._refresh_session_search)
                    call_ui(self, self._finish_sessions_load, gen)
                    return
                self._load_sessions_via_control(gen, quiet=quiet)
                return
            # Socket configured but not yet attached: do not scan disk (would
            # reintroduce a second catalog stack). Empty list until attach or error.
            if not self._sessions_load_current(gen):
                return
            if self._apply_session_meta_rows(gen, []):
                call_ui(self, self._rebuild_session_filters)
                call_ui(self, self._populate_session_table, force=True)
            call_ui(self, self._finish_sessions_load, gen)
            return
        roots = self._catalog_roots_for_load(include_host=include_host)
        scan_desc = ", ".join(str(r.path) for r in roots)
        try:
            if not quiet:
                call_ui(
                    self,
                    self.notify,
                    t("notify-scanning", path=scan_desc),
                    severity="information",
                )
            from ..session.sources import collect_session_dirs

            unique = collect_session_dirs(roots)
            if not self._sessions_load_current(gen):
                return
            if not unique:
                if self._apply_session_meta_rows(gen, []):
                    call_ui(self, self._rebuild_session_filters)
                    call_ui(self, self._populate_session_table, force=True)
                    if not quiet:
                        call_ui(
                            self,
                            self.notify,
                            t("notify-no-sessions", path=scan_desc),
                            severity="warning",
                        )
                return

            rows = self._build_session_meta_rows(unique, gen=gen)
            if not self._sessions_load_current(gen):
                return
            if not self._apply_session_meta_rows(gen, rows):
                return
            n = len(rows)
            call_ui(self, self._rebuild_session_filters)
            call_ui(self, self._populate_session_table, force=True)
            if not quiet:
                call_ui(
                    self,
                    self.notify,
                    t("notify-loaded-sessions", n=n),
                    severity="information",
                )
        finally:
            call_ui(self, self._finish_sessions_load, gen)

    def action_self_test(self) -> None:
        """Open the host self-test (config home, catalog, HUD seat)."""
        from .widgets.self_test_modal import SelfTestModal

        self.push_screen(SelfTestModal())

    def _derive_label(self, session_dir: Path, root: Path) -> str:
        """Derive a display label from directory path."""
        try:
            rel = session_dir.relative_to(root)
            parts = list(rel.parts)
            meaningful = [p for p in parts if p != "sessions" and (not p.startswith("%"))]
            if meaningful:
                return "/".join(meaningful[:2])
        except ValueError:
            pass
        return session_dir.name[:20]

    def query_model_values(self) -> list[str]:
        """Models on the loaded catalog (last-token ``model:`` hints)."""
        return sorted(
            {
                meta.model_display
                for meta, _ in self._meta_only
                if meta.model_id and meta.model_id != "unknown"
            }
        )

    def query_tag_values(self) -> list[str]:
        """Tags on the loaded catalog (last-token ``tag:`` hints)."""
        from ..tags import load_vocabulary, merge_vocabulary

        return merge_vocabulary(
            load_vocabulary(),
            *[list(meta.tags) for meta, _ in self._meta_only],
        )

    def query_path_values(self) -> list[str]:
        """Run directories on the loaded catalog (last-token ``in:`` hints)."""
        out: list[str] = []
        seen: set[str] = set()
        for meta, _label in self._meta_only:
            path = (meta.run_dir or "").strip()
            if path and path not in seen:
                seen.add(path)
                out.append(path)
        return out

    def _rebuild_session_filters(self) -> None:
        """Refresh last-token hints from the loaded catalog."""
        self._refresh_query_hints()

    @staticmethod
    def _cursor_key_after_deletes(
        row_keys_in_order: list[str], cursor_key: str | None, gone: set[str]
    ) -> str | None:
        """Pick a sensible post-delete cursor: next row, else previous, else first remaining."""
        if not row_keys_in_order:
            return None
        remaining = [k for k in row_keys_in_order if k not in gone]
        if not remaining:
            return None
        if cursor_key and cursor_key not in gone and (cursor_key in remaining):
            return cursor_key
        try:
            idx = row_keys_in_order.index(cursor_key) if cursor_key else 0
        except ValueError:
            idx = 0
        for k in row_keys_in_order[idx + 1 :]:
            if k not in gone:
                return k
        for k in reversed(row_keys_in_order[:idx]):
            if k not in gone:
                return k
        return remaining[0]

    def _session_row_keys_in_order(self, table: DataTable | None = None) -> list[str]:
        table = table or self.query_one("#session-table", DataTable)
        try:
            return [str(rk.value) for rk in table.rows.keys()]
        except Exception:
            return []

    @staticmethod
    def _session_sort_ts(meta: SessionMeta) -> float:
        """Best-effort epoch seconds for newest-first session ordering."""
        for raw in (meta.updated_at, meta.created_at):
            if not raw:
                continue
            try:
                s = str(raw).replace("Z", "+00:00")
                from datetime import datetime

                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.timestamp()
            except Exception:
                pass
        try:
            mt = require_adapter(meta.session_dir).trace_mtime(Path(meta.session_dir))
            if mt > 0:
                return mt
        except Exception:
            pass
        try:
            return Path(meta.session_dir).stat().st_mtime
        except OSError:
            return 0.0

    def _populate_session_table(
        self, *, restore_key: str | None = None, force: bool = False
    ) -> None:
        """Rebuild sessions table on the UI thread.

        *force* is accepted for call sites that used debounce; ignored — if a
        rebuild is already in progress we skip (no timer storm).
        """
        _ = force
        if self._populate_busy:
            return
        self._populate_busy = True
        try:
            self._populate_session_table_inner(restore_key=restore_key)
        finally:
            self._populate_busy = False

    def _filtered_session_rows(self) -> list[tuple[SessionMeta, str]]:
        from ..session_inflight import session_dir_key

        search_q = (self._session_search_applied or "").strip()
        seen_keys: set[str] = set()
        rows: list[tuple[SessionMeta, str]] = []
        for meta, label in self._meta_only:
            if search_q and not row_matches_query(CatalogQueryRow.from_meta(meta, label), search_q):
                continue
            sd_key = session_dir_key(meta.session_dir)
            if sd_key in seen_keys:
                continue
            seen_keys.add(sd_key)
            rows.append((meta, label))

        def sort_key(item: tuple[SessionMeta, str]) -> tuple[float, str, str, str]:
            meta, _label = item
            return (
                -self._session_sort_ts(meta),
                meta.model_display,
                meta.task_id or "",
                meta.session_id or "",
            )

        rows.sort(key=sort_key)
        return rows

    @staticmethod
    def _session_home_fp(cells: tuple[str | Text, ...]) -> str:
        return "\u0001".join(str(c) for c in cells)

    def _session_status_cell(self, meta: SessionMeta) -> Text:
        from .styles import status_rich_style, theme_is_light

        light = theme_is_light(str(self.theme or ""))
        status = meta.list_status_label()
        if status is ListStatus.AWAITING:
            return Text(t("status-waiting-prompt"), style=status_rich_style(status, light=light))
        if status is ListStatus.ENDING:
            return Text(t("status-ending"), style=status_rich_style(status, light=light))
        if status is ListStatus.RUNNING:
            return Text(t("status-running"), style=status_rich_style(status, light=light))
        if status is ListStatus.CANCELLED:
            return Text(t("status-cancelled"), style=status_rich_style("failed", light=light))
        if status is ListStatus.COMPLETE:
            return Text(t("status-complete"), style=status_rich_style("completed", light=light))
        return Text(t("status-unknown"), style=status_rich_style("idle", light=light))

    def _session_home_cells(
        self,
        meta: SessionMeta,
        *,
        selected: bool,
    ) -> tuple[str | Text, ...]:
        from ..harness.registry import harness_product
        from ..session.sources import SessionOrigin

        harness = harness_product(meta.harness) or "—"
        origin = self._origin_for_dir(Path(meta.session_dir)) or (meta.origin or "").strip()
        if origin == SessionOrigin.IMPORT:
            harness = join_ui(harness, t("ui-origin-import"), sep=" · ")
        title = (meta.label or meta.session_id)[:40]
        from .styles import tag_pills

        pills = tag_pills(meta.tags)
        title_cell: str | Text = title
        if pills.plain:
            painted = Text(title)
            painted.append(" ")
            painted.append_text(pills)
            title_cell = painted
        return (
            Text("*", style="bold green") if selected else Text(" "),
            title_cell,
            harness,
            meta.model_display[:40],
            self._session_status_cell(meta),
            meta.duration_str,
            (meta.context_usage_compact or "—")[:24],
            str(meta.num_events),
        )

    def _table_row_keys(self, table: DataTable) -> list[str]:
        with suppress(Exception):
            return [str(k.value) for k in table.rows.keys()]
        return []

    def _patch_session_table_rows(
        self, table: DataTable, painted: list[tuple[str, tuple[str | Text, ...]]]
    ) -> None:
        for key, cells in painted:
            fp = self._session_home_fp(cells)
            if self._session_row_fp.get(key) == fp:
                continue
            for i, cell in enumerate(cells):
                update_row_cell(table, key, i, cell)
            self._session_row_fp[key] = fp

    def _rebuild_session_table_rows(
        self,
        table: DataTable,
        painted: list[tuple[str, tuple[str | Text, ...]]],
        restore_key: str | None,
    ) -> None:
        with preserving_scroll(table):
            table.clear()
            self._session_row_fp.clear()
            for key, cells in painted:
                try:
                    table.add_row(*cells, key=key)
                    self._session_row_fp[key] = self._session_home_fp(cells)
                except Exception:
                    logger.debug(t("ui-failed-to-add-row-for-s"), key, exc_info=True)
            if restore_key:
                restore_cursor(table, restore_key, scroll=False)

    def _populate_session_table_inner(self, *, restore_key: str | None = None) -> None:
        try:
            table = self.query_one("#session-table", DataTable)
        except NoMatches:
            return
        keep_search = False
        with suppress(Exception):
            keep_search = bool(self.query_one("#session-search-input", Input).has_focus)
        if restore_key is None:
            restore_key = self._session_row_key_at_cursor(table)
        rows = self._filtered_session_rows()
        painted: list[tuple[str, tuple[str | Text, ...]]] = []
        for meta, _label in rows:
            sd_key = str(meta.session_dir)
            painted.append(
                (
                    sd_key,
                    self._session_home_cells(meta, selected=sd_key in self._selected),
                )
            )
        existing = self._table_row_keys(table)
        new_keys = [key for key, _cells in painted]
        with preserving_scroll(table):
            if existing == new_keys and existing:
                self._patch_session_table_rows(table, painted)
                if restore_key:
                    restore_cursor(table, restore_key, scroll=False)
            else:
                self._rebuild_session_table_rows(table, painted, restore_key)
        if restore_key or existing:
            self._sessions_table_primed = True
        elif not self._sessions_table_primed:
            focus_primary_list(table)
            self._sessions_table_primed = True
        if keep_search:
            with suppress(Exception):
                self.query_one("#session-search-input", Input).focus()
        self._update_summary_lazy(len(self._meta_only))
        with suppress(Exception):
            self.refresh_bindings()

    def _update_summary_lazy(self, total: int) -> None:
        from .i18n import join_ui

        sel_count = len(self._selected)
        extras: list[str] = []
        if sel_count:
            extras.append(t("sessions-selected-count", n=sel_count))
        core = t("sessions-home-summary", total=total)
        summary = f"[bold]{join_ui(core, *extras, sep=' · ')}"
        self.query_one("#session-summary", Static).update(summary)

    def _restore_cursor(self, table: DataTable, row_key_value: str) -> None:
        """Move cursor back to the row with the given key after a table repopulate."""
        restore_cursor(table, row_key_value)

    def _session_row_key_at_cursor(self, table: DataTable | None = None) -> str | None:
        """Stable row key for the highlighted session row (session_dir path)."""
        table = table or self.query_one("#session-table", DataTable)
        return cursor_row_key(table)

    def _set_session_sel_cell(self, table: DataTable, row_key: str, selected: bool) -> None:
        """Update only the selection marker column (avoids table.clear / cursor jump)."""
        from rich.text import Text

        mark = Text("*", style="bold green") if selected else Text(" ")
        try:
            cols = list(table.columns.keys())
            if not cols:
                return
            table.update_cell(row_key, cols[0], mark)
        except Exception:
            set_marker_column(table, row_key, selected, on="*", off=" ")

    def _refresh_session_selection_markers(self, table: DataTable | None = None) -> None:
        """Refresh all Sel cells from ``self._selected`` without rebuilding rows."""
        table = table or self.query_one("#session-table", DataTable)
        for rk in table.rows.keys():
            key = str(rk.value)
            self._set_session_sel_cell(table, key, key in self._selected)

    def action_toggle_select(self) -> None:
        """Toggle selection on the current row (in-place; cursor stays put)."""
        table = self.query_one("#session-table", DataTable)
        cursor_key = self._session_row_key_at_cursor(table)
        if not cursor_key:
            return
        if cursor_key in self._selected:
            self._selected.discard(cursor_key)
            now_on = False
        else:
            self._selected.add(cursor_key)
            now_on = True
        self._set_session_sel_cell(table, cursor_key, now_on)
        self._refresh_selection_summary_only()
        with suppress(Exception):
            self.refresh_bindings()

    def action_select_all(self) -> None:
        """Select or clear every row in the current filter (not the whole catalog)."""
        table = self.query_one("#session-table", DataTable)
        preserve = self._session_row_key_at_cursor(table)
        visible = {str(meta.session_dir) for meta, _ in self._filtered_session_rows()}
        scope = self._select_all_scope
        if scope and self._selected == scope:
            self._selected -= scope
            self._select_all_scope = set()
        elif visible and visible <= self._selected:
            self._selected -= visible
            self._select_all_scope = set()
        else:
            self._selected |= visible
            self._select_all_scope = set(visible)
        self._refresh_session_selection_markers(table)
        if preserve:
            self._restore_cursor(table, preserve)
        self._refresh_selection_summary_only()
        with suppress(Exception):
            self.refresh_bindings()

    def _refresh_selection_summary_only(self) -> None:
        """Refresh the home summary from the current list (no table rebuild)."""
        try:
            self._update_summary_lazy(len(self._meta_only))
        except Exception:
            pass

    def action_search_sessions(self) -> None:
        """Focus the sessions search field (filter as you type, same as Timeline)."""

        def _focus_search() -> None:
            with suppress(Exception):
                self.query_one("#session-search-input", Input).focus()

        self.call_after_refresh(lambda: self.call_after_refresh(_focus_search))

    @on(Input.Changed, "#session-search-input")
    def _on_session_search_changed(self, event: Input.Changed) -> None:
        """Hold the draft; apply the matcher after the shared idle gap."""
        from .terminal_reply import is_terminal_probe_text

        raw = event.value or ""
        if is_terminal_probe_text(raw):
            event.input.value = ""
            self._session_search = ""
            self._refresh_query_hints()
            return
        self._session_search = raw
        self._refresh_query_hints()
        self._session_search_debounce.arm(self, self._apply_debounced_session_search)

    @on(Input.Submitted, "#session-search-input")
    def _on_session_search_submitted(self, event: Input.Submitted) -> None:
        """Apply the filter now and move focus back to the session list."""
        self._session_search = event.value or ""
        self._refresh_query_hints()
        self._session_search_debounce.flush(self._apply_debounced_session_search)
        with suppress(Exception):
            focus_primary_list(self.query_one("#session-table", DataTable))

    def _apply_debounced_session_search(self) -> None:
        """Commit the search box and rebuild the sessions table."""
        self._session_search_debounce.cancel()
        self._session_search_applied = self._session_search
        self._session_flight.bump()
        if self._control_socket is not None and self._control_attached:
            if (self._session_search_applied or "").strip():
                self._ensure_session_search()
                return
            self._load_sessions(include_host=True, quiet=True)
            return
        self._populate_session_table(force=True)

    def _refresh_session_search(self) -> None:
        """Re-run the committed catalog query (live list tick)."""
        if self._session_flight.inflight:
            self._session_flight.bump()
            return
        self._session_flight.bump()
        self._ensure_session_search()

    def _ensure_session_search(self) -> None:
        """Start the catalog search worker, or mark the in-flight pass dirty."""
        if not (self._session_search_applied or "").strip():
            return
        if not self._session_flight.request():
            return
        self._run_session_search()

    @work(thread=True, group="sessions-search")
    def _run_session_search(self) -> None:
        """Fetch the latest catalog query. Loop when a newer box replaces it."""
        try:
            while True:
                self._session_flight.again = False
                gen = self._session_flight.gen
                query = (self._session_search_applied or "").strip()
                if not query:
                    return
                result = self._fetch_control_catalog_sync(query=query, drain=True)
                if not self._session_flight.current(gen):
                    continue
                raw = result.get("sessions")
                wire_rows = (
                    [as_json_object(row) for row in raw if isinstance(row, dict)]
                    if isinstance(raw, list)
                    else []
                )
                rows = self._rows_from_catalog_wire(wire_rows)
                rev_raw = result.get("revision")
                rev = rev_raw if isinstance(rev_raw, int) else 0

                def _apply() -> None:
                    if not self._session_flight.current(gen):
                        return
                    self._meta_only = rows
                    self._catalog_query_slice = True
                    if rev > 0:
                        self._catalog_revision = rev
                    self._rebuild_session_filters()
                    self._populate_session_table(force=True)

                call_ui(self, _apply)
                if not self._session_flight.current(gen):
                    continue
                return
        finally:

            def _done() -> None:
                if self._session_flight.finish():
                    self._ensure_session_search()

            call_ui(self, _done)

    def _refresh_query_hints(self) -> None:
        """Paint last-token completions under the search box."""
        try:
            hint = self.query_one("#session-query-hints", Static)
        except NoMatches:
            return
        hits = suggest_last_token(
            self._session_search,
            models=self.query_model_values(),
            paths=self.query_path_values(),
            tags=self.query_tag_values(),
        )
        hint.display = True
        hint.update("  ".join(hits[:8]) if hits else "")

    def _set_session_query(self, query: str) -> None:
        """Write the search box and apply the matcher now."""
        self._session_search = query
        with suppress(Exception):
            self.query_one("#session-search-input", Input).value = query
        self._refresh_query_hints()
        self._apply_debounced_session_search()

    def _cursor_session_meta(self) -> SessionMeta | None:
        """SessionMeta for the sessions-home table cursor, or None."""
        table = self.query_one("#session-table", DataTable)
        if table.cursor_row is None:
            return None
        try:
            row_key = list(table.rows.keys())[table.cursor_row]
            cursor_key = row_key.value
        except (IndexError, KeyError):
            return None
        for m, _label in self._meta_only:
            if str(m.session_dir) == cursor_key:
                return m
        return None

    def _sessions_home_active(self) -> bool:
        """True when the sessions list screen is on top (not a pushed screen/modal)."""
        try:
            return self.screen is self.screen_stack[0]
        except Exception:
            return True

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],  # Textual Screen.check_action
    ) -> bool | None:
        """Gate session-home bindings so they do not leak into pushed-screen footers."""
        if action == "leader_idle":
            return bool(self._leader_armed)
        if action in SESSION_HOME_ACTIONS and not self._sessions_home_active():
            return False
        return True

    def action_delete_sessions(self) -> None:
        """Delete selected sessions (or current row if none selected). Removes traces only."""
        targets: list[Path] = []
        table = self.query_one("#session-table", DataTable)
        cursor_key = self._session_row_key_at_cursor(table)
        if self._selected:
            targets = [Path(p) for p in self._selected]
        elif cursor_key:
            targets = [Path(cursor_key)]
        if not targets:
            self.notify(
                t("ui-select-sessions-with-s-or-highlight-a-row-then-p"), severity="warning"
            )
            return
        from .delete_confirm import second_press_armed

        n = len(targets)
        commit, pending = second_press_armed(
            [str(p) for p in (self._delete_pending_paths or [])],
            [str(p) for p in targets],
        )
        if not commit:
            self._delete_pending_paths = [Path(p) for p in pending]
            self._delete_cursor_key = cursor_key
            self._delete_row_keys_snapshot = self._session_row_keys_in_order(table)
            self.notify(
                t("notify-press-again-delete-sessions", n=n),
                severity="warning",
                timeout=10,
            )
            return
        gone_preview = {str(p) for p in targets}
        snap = self._delete_row_keys_snapshot or self._session_row_keys_in_order(table)
        cur = self._delete_cursor_key or cursor_key
        restore_key = self._cursor_key_after_deletes(snap, cur, gone_preview)
        self._delete_pending_paths = None
        self._delete_cursor_key = None
        self._delete_row_keys_snapshot = None
        self._do_delete_sessions(targets, restore_key=restore_key)

    @work(thread=True)
    def _do_delete_sessions(
        self, targets: list[Path] | None = None, *, restore_key: str | None = None
    ) -> None:
        if not targets:
            return
        from ..session.delete import delete_session_dirs, session_dirs_for_delete

        paths = session_dirs_for_delete(targets)
        if self._control_socket is not None and self._control_attached:
            stats = self._delete_sessions_via_control(paths)
        else:
            stats = delete_session_dirs(
                paths, traces_root=self.traces_path, prune_empty_parents=True
            )
        gone = {str(p) for p in paths}

        def _refresh() -> None:
            self._selected -= gone
            self._meta_only = [
                (m, lab) for m, lab in self._meta_only if str(m.session_dir) not in gone
            ]
            for g in gone:
                self._session_mtimes.pop(g, None)
                # Also drop resolve()-style keys that contain the path
                for mk in list(self._session_mtimes):
                    if mk == g or mk.endswith(g) or g.endswith(mk):
                        self._session_mtimes.pop(mk, None)

            try:
                self._populate_session_table(restore_key=restore_key)
            except Exception:
                pass
            errors_raw = stats.get("errors")
            errors_list = list(errors_raw) if isinstance(errors_raw, list) else []
            err_n = len(errors_list)
            err_hint = ""
            if err_n:
                sample = str(errors_list[0]) if errors_list else ""
                err_hint = f" — {sample[:120]}"
            self.notify(
                (
                    t(
                        "notify-deleted-sessions-errors",
                        deleted=stats["deleted"],
                        requested=stats["requested"],
                        errors=err_n,
                        hint=err_hint or "",
                    )
                    if err_n
                    else t(
                        "notify-deleted-sessions",
                        deleted=stats["deleted"],
                        requested=stats["requested"],
                    )
                ),
                severity="warning" if err_n else "information",
                timeout=12,
            )

        call_ui(self, _refresh)

    def action_refresh_everything(self) -> None:
        """Full refresh: rescan traces and rebuild the session list."""
        from ..paths import traces_root_for_reload

        root = traces_root_for_reload(self.traces_path)
        if not root.exists():
            self.notify(t("notify-no-traces-refresh", path=str(root)), severity="error")
            return
        self._meta_only = []
        self._session_mtimes.clear()
        self._selected = set()
        self._select_all_scope = set()
        self.notify(
            t("notify-full-refresh", path=str(root)),
            severity="warning",
            timeout=12,
        )
        self._run_refresh_everything(root)

    @work(thread=True)
    def _run_refresh_everything(self, traces_root: Path | None = None) -> None:
        if traces_root is None:
            return
        summary: dict = {"sessions_loaded": 0, "error": ""}
        try:
            # Sync load — do not nest @work _load_sessions (would not run inline).
            summary["sessions_loaded"] = self._load_sessions_sync(traces_root)
            call_ui(self, self._populate_session_table)
        except Exception as exc:
            summary["error"] = str(exc)

        def _done() -> None:
            try:
                self.traces_path = traces_root
            except Exception:
                pass
            try:
                self._populate_session_table()
            except Exception:
                pass
            if summary.get("error"):
                self.notify(
                    t("notify-refresh-all-failed", error=str(summary["error"])),
                    severity="error",
                    timeout=15,
                )
                return
            self.notify(
                t("notify-refresh-done", sessions=summary.get("sessions_loaded", 0)),
                severity="information",
                timeout=16,
            )

        call_ui(self, _done)

    def _session_meta_for_export(self) -> SessionMeta | None:
        """Highlighted or first selected session for export actions."""
        meta = None
        if self._selected:
            key = next(iter(self._selected))
            for m, _ in self._meta_only:
                if str(m.session_dir) == key:
                    meta = m
                    break
        if meta is None:
            cursor_key = self._session_row_key_at_cursor()
            if cursor_key:
                for m, _ in self._meta_only:
                    if str(m.session_dir) == cursor_key:
                        meta = m
                        break
        return meta

    def _target_session_metas(self) -> list[SessionMeta]:
        """Marked sessions, or the focused row."""
        keys: list[str] = []
        if self._selected:
            keys = list(self._selected)
        else:
            cursor = self._session_row_key_at_cursor()
            if cursor:
                keys = [cursor]
        by_key = {str(meta.session_dir): meta for meta, _ in self._meta_only}
        return [by_key[key] for key in keys if key in by_key]

    def _session_tag_ref(self, meta: SessionMeta) -> str:
        harness = (meta.harness or "").strip()
        sid = (meta.session_id or "").strip()
        if harness and sid:
            return f"{harness}:{sid}"
        return str(meta.session_dir)

    def action_tag_sessions(self) -> None:
        """t — add or remove tags on the marked rows or the focused row."""
        from ..tags import load_vocabulary, merge_vocabulary, shared_tags
        from .widgets.tags_modal import TagsModal

        metas = self._target_session_metas()
        if not metas:
            self.notify(t("ui-tags-none"), severity="warning")
            return
        current = shared_tags([list(meta.tags) for meta in metas])
        vocab = merge_vocabulary(
            load_vocabulary(),
            *[list(meta.tags) for meta, _ in self._meta_only],
        )

        def _done(tags: list[str] | None) -> None:
            if tags is None:
                return
            self.run_worker(self._write_session_tags(metas, tags), exclusive=False)

        self.push_screen(TagsModal(current=current, vocabulary=vocab), _done)

    async def _write_session_tags(self, metas: list[SessionMeta], tags: list[str]) -> None:
        from ..tags import load_vocabulary, save_tags

        refs = [self._session_tag_ref(meta) for meta in metas]
        access = self.session_access()
        try:
            if access is not None and self.is_control_client():
                await access.tags_set(refs, tags)
            else:
                from ..harness.registry import resolve_session_ref

                vocab = load_vocabulary()
                for raw in refs:
                    found = resolve_session_ref(raw)
                    if found is None:
                        raise FileNotFoundError(raw)
                    save_tags(found, tags, vocabulary=vocab)
        except Exception:
            logger.exception("tags/set failed")
            self.notify(t("ui-tags-none"), severity="error")
            return
        stored = tuple(tags)
        for meta in metas:
            meta.tags = stored
        self._populate_session_table()
        self.notify(t("ui-tags-saved"))

    def action_import_session(self) -> None:
        """Ctrl+O — open a harness archive or anqa export."""
        from .import_modal import ImportPathModal

        def _done(raw: str | None) -> None:
            path = (raw or "").strip()
            if path:
                self.run_worker(self._import_and_open(path), exclusive=False)

        self.push_screen(ImportPathModal(), _done)

    async def _import_and_open(self, path: str) -> None:
        """Import *path* via control when attached, else locally, then open it."""
        from ..session.access import LocalSessionAccess
        from .i18n import t

        try:
            access = self.session_access()
            if access is not None and self.is_control_client():
                result = await access.session_import(path)
            else:
                result = LocalSessionAccess(
                    resolve_session=lambda _ref: None,
                ).session_import(path)
        except Exception as exc:
            self.notify(t("import-failed", exc=str(exc)), severity="error", timeout=12)
            return
        sid = str(result.get("sessionId") or "").strip()
        loc = str(result.get("session") or "").strip()
        self._import_opening_sid = sid
        self.notify(t("import-opened", session=sid or loc), severity="information", timeout=4)
        self._schedule_sessions_reload(quiet=False)
        if loc:
            target = Path(loc)
            if target.is_dir():
                self._push_browser(target)

    def action_export_session_bundle(self) -> None:
        """Export session: use configured profile, or ask if none is set."""
        meta = self._session_meta_for_export()
        if meta is None:
            self.notify(t("export-bundle-no-session"), severity="warning")
            return
        from .export_session import start_export_smart

        start_export_smart(self, meta.session_dir)

    def action_export_session_choose_profile(self) -> None:
        """Palette: pick an export profile for this export only (does not change default)."""
        meta = self._session_meta_for_export()
        if meta is None:
            self.notify(t("export-bundle-no-session"), severity="warning")
            return
        from .export_session import start_export_with_profile_picker

        start_export_with_profile_picker(self, meta.session_dir, remember_as_default=False)

    @staticmethod
    def _extract_task_and_model(trace_dir_name: str) -> tuple[str, str]:
        """Extract (task_id, model_suffix) from a trace directory name.

        Convention: anqa-{run_id}-{model_suffix}. The model_suffix is only used
        as a fallback for grouping when the full model_id is unavailable.
        """
        from ..paths import strip_run_prefix

        name = strip_run_prefix(trace_dir_name)
        for suffix in ("-build", "-s80", "-s140"):
            if name.endswith(suffix):
                return (name[: -len(suffix)], suffix[1:])
        if "-" in name:
            parts = name.rsplit("-", 1)
            return (parts[0], parts[1])
        return (trace_dir_name, "unknown")

    @on(DataTable.RowHighlighted, "#session-table")
    def _on_session_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Refresh footer ``n`` / ``e`` when the cursor moves."""
        _ = event
        with suppress(Exception):
            self.refresh_bindings()

    @on(DataTable.RowSelected, "#session-table")
    def _on_session_selected(self, event: DataTable.RowSelected) -> None:
        row_key = str(event.row_key.value)
        self._open_session(row_key)

    def open_session_path(
        self,
        session_dir: Path | str,
        *,
        live: bool | None = None,
        prompt_index: int | None = None,
        notify_control: bool = True,
    ) -> None:
        """Open a session path in the trace browser (main list, Jobs modal, etc.)."""
        self._open_session(
            str(session_dir),
            live=live,
            prompt_index=prompt_index,
            notify_control=notify_control,
        )

    def _open_session(
        self,
        row_key: str,
        live: bool | None = None,
        prompt_index: int | None = None,
        notify_control: bool = True,
    ) -> None:
        """Open a session in the browser immediately."""
        session_path = Path(row_key)
        hid_sid = ""
        for meta, _label in self._meta_only:
            if str(meta.session_dir) == row_key or meta.session_id == row_key:
                hid = (meta.harness or "").strip()
                if hid and meta.session_id:
                    hid_sid = f"{hid}:{meta.session_id}"
                break
        if hid_sid and not session_path.is_dir():
            session_path = Path(hid_sid)
        self._push_browser(session_path, prompt_index=prompt_index)
        if notify_control:
            self.control_session_selected(session_path, prompt_index)

    def _push_runner_with_prefill(self, prefill: object) -> None:
        return

    def _push_browser(
        self,
        session_path: Path,
        *,
        prompt_index: int | None = None,
    ) -> None:
        """Construct and push BrowserScreen on the main thread."""
        self._pause_home_traces_watch(pause=True)
        self.push_screen(BrowserScreen(session_path, prompt_index=prompt_index))

    def update_run_status(self) -> None:
        """Keep the window title as the wordmark; the activity strip owns status."""
        self.title = t("help-brand-name")

    def _schedule_run_status_update(self) -> None:
        """Debounce title updates."""
        if self._run_status_timer is not None:
            self._run_status_timer.stop()
        self._run_status_timer = self.set_timer(0.6, self.update_run_status)

    def _runner_traces_root(self) -> Path:
        """Catalog store this process lists (host sessions or ``-P``)."""
        return self._session_traces_root()

    def _schedule_live_sessions_poll(self) -> None:
        """Watch the catalog store and arm a read-only 60s meta heartbeat.

        FS events discover sessions / turn status. The heartbeat reloads
        ``signals.json`` context fields for in-progress rows without writing
        the meta cache or traces tree. Control-attached home lists follow
        ``session/changed`` instead.
        """
        if self._control_socket is not None:
            return
        root = self._runner_traces_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        from ..constants import (
            LIVE_POLL_HEARTBEAT_INTERVAL,
            LIVE_POLL_WATCH_FALLBACK_INTERVAL,
        )
        from ..fs_watch import TraceTreeWatch

        if self._traces_watch is None and self._live_sessions_timer is None:

            def _on_fs() -> None:
                if self._exiting:
                    return
                try:
                    if self.is_running:
                        self.call_from_thread(self._live_sessions_tick)
                except Exception:
                    pass

            watch = TraceTreeWatch(root, _on_fs, debounce_s=0.5)
            if watch.start():
                self._traces_watch = watch
            else:
                self._live_sessions_timer = self.set_interval(
                    LIVE_POLL_WATCH_FALLBACK_INTERVAL,
                    self._live_sessions_tick,
                )
        if self._live_sessions_heartbeat_timer is None:
            self._live_sessions_heartbeat_timer = self.set_interval(
                LIVE_POLL_HEARTBEAT_INTERVAL,
                self._live_sessions_heartbeat,
            )

    def _browser_live_screen_open(self) -> bool:
        """True when a session browser is top of stack (live refresh owns the tree)."""
        with suppress(Exception):
            top = self.screen
            # Browser screens always expose session_dir + live refresh.
            if getattr(top, "session_dir", None) is not None and hasattr(
                top, "_live_refresh_from_fs"
            ):
                return True
        return False

    def _pause_home_traces_watch(self, *, pause: bool) -> None:
        """Stop or restart the home-list FS observer (not just skip ticks).

        ``call_from_thread`` on every traces write still floods the UI loop even
        when the tick handler returns immediately. Fully stop the observer while
        a browser is open.
        """
        if pause:
            w = self._traces_watch
            self._traces_watch = None
            stop = getattr(w, "stop", None)
            if callable(stop):
                with suppress(Exception):
                    stop()
            if self._live_sessions_timer is not None:
                with suppress(Exception):
                    self._live_sessions_timer.stop()
                self._live_sessions_timer = None
            return
        if not self._exiting:
            self._schedule_live_sessions_poll()

    def _live_sessions_tick(self) -> None:
        """UI thread: at most one background scan at a time (from FS events)."""
        if self._live_sessions_busy or self._exiting:
            return
        if self._browser_live_screen_open():
            return
        self._live_sessions_busy = True
        self._scan_live_sessions_worker()

    def _live_sessions_heartbeat(self) -> None:
        """UI thread: periodic read-only reload of live row metas (context meter)."""
        if self._exiting or self._live_meta_heartbeat_busy:
            return
        if self._browser_live_screen_open():
            return
        live_rows = [
            (meta, label)
            for meta, label in list(self._meta_only)
            if meta.turn_in_progress
            or meta.list_status_label()
            in (
                "running",
                "ending",
                "awaiting",
            )
        ]
        if not live_rows:
            return
        self._live_meta_heartbeat_busy = True
        self._live_meta_heartbeat_worker(live_rows)

    def _dispatch_refresh_rerun(self, session_dir: Path) -> None:
        """UI thread: hand a coalesced refresh back to an open browser, if any."""
        from ..session_inflight import session_dir_key

        target = session_dir_key(session_dir)
        try:
            stack = list(self.screen_stack)
        except Exception:
            stack = []
        for screen in stack:
            browser_sd = getattr(screen, "session_dir", None)
            refresh = getattr(screen, "_live_refresh_from_fs", None)
            if browser_sd is None or not callable(refresh):
                continue
            if session_dir_key(browser_sd) == target:
                refresh(heartbeat=True)
                return

    @work(thread=True)
    def _live_meta_heartbeat_worker(self, live_rows: list[tuple[SessionMeta, str]]) -> None:
        """Read-only list meta for in-progress sessions.

        Uses per-session inflight locks so browser light reloads coalesce safely.
        Never writes session artifacts.
        """
        from ..session_inflight import KIND_REFRESH, end, request_rerun, try_begin

        updates: list[tuple[str, SessionMeta, str]] = []
        pending_reruns: list[Path] = []
        try:
            for meta, label in live_rows:
                sd = Path(meta.session_dir)
                if not try_begin(KIND_REFRESH, sd):
                    request_rerun(KIND_REFRESH, sd)
                    continue
                try:
                    fresh = require_adapter(sd).load_meta(sd)
                    fresh.num_events = meta.num_events
                    try:
                        key = str(sd.resolve())
                    except OSError:
                        key = str(sd)
                    if (
                        fresh.context_usage_compact != meta.context_usage_compact
                        or fresh.turn_outcome != meta.turn_outcome
                        or fresh.list_status_label() != meta.list_status_label()
                        or fresh.duration_seconds != meta.duration_seconds
                        # Host fills generated_title after start; list must refresh.
                        or (fresh.title or "") != (meta.title or "")
                        or (fresh.summary_text or "") != (meta.summary_text or "")
                    ):
                        updates.append((key, fresh, label))
                finally:
                    if end(KIND_REFRESH, sd):
                        pending_reruns.append(sd)
        finally:

            def _apply() -> None:
                self._live_meta_heartbeat_busy = False
                if self._exiting:
                    return
                if updates:
                    by_key: dict[str, int] = {}
                    for idx, (m, _lab) in enumerate(self._meta_only):
                        try:
                            by_key[str(Path(m.session_dir).resolve())] = idx
                        except OSError:
                            by_key[str(m.session_dir)] = idx
                    changed = False
                    for key, fresh, label in updates:
                        row_idx = by_key.get(key)
                        if row_idx is None:
                            continue
                        self._meta_only[row_idx] = (fresh, label)
                        changed = True
                    if changed:
                        with suppress(Exception):
                            self._populate_session_table()
                for sd in pending_reruns:
                    self._dispatch_refresh_rerun(sd)

            try:
                call_ui(self, _apply)
            except Exception:
                self._live_meta_heartbeat_busy = False

    @work(thread=True)
    def _scan_live_sessions_worker(self) -> None:
        """Find/peek session dirs off the UI thread."""
        try:
            self._scan_live_sessions_into_table()
        except Exception:
            logger.debug("live sessions scan failed", exc_info=True)
        finally:
            self._live_sessions_busy = False

    def _scan_live_sessions_into_table(self) -> None:
        """Discover new sessions and refresh turn status.

        Offline: adapter discover at most every ``LIVE_POLL_ACTIVE_INTERVAL``.
        """
        import time

        from ..constants import LIVE_POLL_ACTIVE_INTERVAL, LIVE_POLL_FULL_WALK_INTERVAL
        from ..harness.registry import discover_dirs

        if self._sessions_catalog_busy:
            return

        now = time.time()
        min_gap = LIVE_POLL_ACTIVE_INTERVAL
        if now - self._live_sessions_last_scan < min_gap:
            return
        self._live_sessions_last_scan = now

        runner_traces = self._runner_traces_root()
        if not runner_traces.exists():
            return

        found: list[Path] = []
        if now - self._live_full_walk_last >= LIVE_POLL_FULL_WALK_INTERVAL:
            self._live_full_walk_last = now
            try:
                found.extend(discover_dirs(runner_traces))
            except Exception:
                pass

        if not found:
            return

        # Snapshot previous outcomes by path key (read-only).
        prev_outcome: dict[str, str] = {}
        existing_keys: set[str] = set()
        for meta, _lab in list(self._meta_only):
            try:
                k = str(Path(meta.session_dir).resolve())
            except Exception:
                k = str(meta.session_dir)
            existing_keys.add(k)
            prev_outcome[k] = meta.turn_outcome or ""

        new_metas: list[tuple[str, SessionMeta, str]] = []
        outcome_updates: list[tuple[str, str]] = []  # key, new outcome
        changed_sessions: dict[str, Path] = {}

        for sd in found:
            try:
                sd_res = sd if sd.is_absolute() else runner_traces / sd
                if not sd_res.is_dir():
                    continue
                key = str(sd_res.resolve())
            except Exception:
                continue
            try:
                mtime = require_adapter(sd_res).trace_mtime(sd_res)
            except Exception:
                mtime = 0.0

            if key not in existing_keys:
                try:
                    meta = require_adapter(sd_res).load_detail(sd_res)
                except Exception:
                    continue
                label = self._label_for_session(sd_res)
                self._session_mtimes[key] = mtime
                new_metas.append((key, meta, label))
                continue

            # Known session: last-turn store probe + light meta for live rows
            # (title, status). Always allow live outcomes even when the row was
            # ``completed`` — a later turn can be running / awaiting again.
            # Never apply non-live probe results (that would invent
            # interrupted/cancelled).
            if mtime > 0:
                previous_mtime = self._session_mtimes.get(key)
                if previous_mtime is not None and mtime > previous_mtime:
                    changed_sessions[key] = sd_res
                self._session_mtimes[key] = mtime
            try:
                outcome = require_adapter(sd_res).list_turn_outcome(sd_res)
            except Exception:
                continue
            oc = (outcome or "").strip().lower().replace(" ", "_")
            live_oc = oc in (
                "running",
                "ending",
                "in_progress",
                "pending",
                "awaiting_follow_up",
            )
            # While live, light meta reload so generated_title / status update
            # without restarting the app (outcome-only probe skips summary.json).
            prev = (prev_outcome.get(key) or "").strip().lower().replace(" ", "_")
            prev_live = prev in (
                "running",
                "ending",
                "in_progress",
                "pending",
                "awaiting_follow_up",
            )
            if not live_oc and prev_live:
                try:
                    fresh = require_adapter(sd_res).load_meta(sd_res)
                    label = self._label_for_session(sd_res)
                    new_metas.append((key, fresh, label))
                except Exception:
                    logger.debug("settle list row %s", sd_res, exc_info=True)
                continue
            if live_oc:
                try:
                    fresh = require_adapter(sd_res).load_meta(sd_res)
                    # List probe is authoritative for live turn status (gate/freshness).
                    if outcome:
                        fresh.turn_outcome = outcome
                    for meta0, _lab0 in list(self._meta_only):
                        try:
                            if str(Path(meta0.session_dir).resolve()) == key:
                                fresh.num_events = meta0.num_events
                                break
                        except OSError:
                            if str(meta0.session_dir) == key:
                                fresh.num_events = meta0.num_events
                                break
                    label = self._label_for_session(sd_res)
                    new_metas.append((key, fresh, label))  # replace existing row in _apply
                except Exception:
                    if outcome != prev_outcome.get(key):
                        outcome_updates.append((key, outcome))
                continue

        if not new_metas and not outcome_updates and not changed_sessions:
            return

        def _apply() -> None:
            by_key: dict[str, int] = {}
            for idx, (meta, _lab) in enumerate(self._meta_only):
                try:
                    by_key[str(Path(meta.session_dir).resolve())] = idx
                except Exception:
                    by_key[str(meta.session_dir)] = idx
            changed = False
            for key, meta, label in new_metas:
                idx_opt = by_key.get(key)
                if idx_opt is not None:
                    # Known live row: replace meta (title / status / context).
                    prev_m, prev_lab = self._meta_only[idx_opt]
                    if (
                        prev_m.title != meta.title
                        or prev_m.turn_outcome != meta.turn_outcome
                        or prev_m.list_status_label() != meta.list_status_label()
                        or prev_m.context_usage_compact != meta.context_usage_compact
                        or prev_m.duration_seconds != meta.duration_seconds
                        or prev_m.summary_text != meta.summary_text
                    ):
                        self._meta_only[idx_opt] = (meta, prev_lab)
                        changed = True
                    continue
                self._meta_only.append((meta, label))
                by_key[key] = len(self._meta_only) - 1
                changed = True
            for key, outcome in outcome_updates:
                idx_opt = by_key.get(key)
                if idx_opt is None:
                    continue
                idx = idx_opt
                meta, label = self._meta_only[idx]
                if meta.turn_outcome != outcome:
                    meta.turn_outcome = outcome
                    self._meta_only[idx] = (meta, label)
                    changed = True
            if changed:
                with suppress(Exception):
                    self._populate_session_table()
            for session_dir in changed_sessions.values():
                self.control_session_changed(session_dir)

        try:
            call_ui(self, _apply)
        except Exception:
            with suppress(Exception):
                _apply()

    def _merge_session_dirs(
        self, session_dirs: list[Path], *, traces_root: Path | None = None
    ) -> None:
        """Add new session dirs (full meta once). Safe from tests / one-off callers.

        Live polling uses :meth:`_scan_live_sessions_into_table` instead.
        """
        if not session_dirs:
            return
        root = traces_root or self._runner_traces_root()
        existing: set[str] = set()
        for meta, _lab in list(self._meta_only):
            try:
                existing.add(str(Path(meta.session_dir).resolve()))
            except Exception:
                existing.add(str(meta.session_dir))
        added = False
        for sd in session_dirs:
            try:
                sd_res = sd if sd.is_absolute() else root / sd
                if not sd_res.is_dir():
                    continue
                key = str(sd_res.resolve())
            except Exception:
                continue
            if key in existing:
                continue
            try:
                meta = require_adapter(sd_res).load_detail(sd_res)
            except Exception:
                continue
            label = self._label_for_session(sd_res)
            self._meta_only.append((meta, label))
            existing.add(key)
            added = True
        if added:
            with suppress(Exception):
                self._populate_session_table(force=True)

    def _prepare_clean_exit(self) -> None:
        """Stop timers and watches so ``q`` returns promptly."""
        self._exiting = True
        stop = self._control_notify_stop
        if stop is not None:
            stop.set()
        for attr in (
            "_run_status_timer",
            "_live_sessions_timer",
            "_live_sessions_heartbeat_timer",
        ):
            timer = getattr(self, attr, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
                setattr(self, attr, None)
        w = self._traces_watch
        self._traces_watch = None
        stop = getattr(w, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        try:
            for screen in list(self.screen_stack):
                stop = getattr(screen, "_stop_live_refresh", None)
                if callable(stop):
                    stop()
        except Exception:
            logger.debug(t("ui-stop-live-refresh-on-quit-failed"), exc_info=True)
        try:
            workers_cancel = getattr(self, "workers", None)
            if workers_cancel is not None and hasattr(workers_cancel, "cancel_all"):
                workers_cancel.cancel_all()
        except Exception:
            logger.debug(t("ui-workers-cancel-on-quit-failed"), exc_info=True)

    async def action_quit(self) -> None:
        """Quit the TUI."""
        self._prepare_clean_exit()
        self.exit()

    def action_refresh_context(self) -> None:
        """Refresh whatever screen/context is active (F5 / Ctrl+R globally)."""
        from .screens.browser import BrowserScreen

        screen = self.screen
        if isinstance(screen, BrowserScreen):
            screen.action_refresh_context()
            return
        self._refresh_sessions_list()

    def _refresh_sessions_list(self) -> None:
        """Reload the sessions table from the catalog store.

        Debounced + exclusive catalog worker — do not race populate here.
        """
        roots = self._session_catalog_roots()
        desc = ", ".join(str(r.path) for r in roots)
        if not any(r.path.exists() for r in roots):
            self.notify(t("notify-nothing-to-refresh", path=desc), severity="warning")
            return
        self._schedule_sessions_reload(delay=0.05)

    def action_open_session(self) -> None:
        """Open the highlighted session (same as Enter on sessions table)."""
        try:
            table = self.query_one("#session-table", DataTable)
            if not table.row_count:
                return
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            if row_key and row_key.value:
                self._open_session(str(row_key.value))
        except Exception:
            pass

    def action_show_help(self) -> None:
        from .bindings import notify_help

        notify_help(self.screen)
