"""Browse for a harness archive, anqa export, or session directory."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, DirectoryTree, Input, Static, Tree
from textual.widgets.directory_tree import DirEntry

from ..session.imports import looks_like_import_source
from .bindings import MODAL_CANCEL_QUIT
from .i18n import t
from .quit_actions import Modal

_SKIP_DIR_NAMES = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", "target"})


def _looks_like_archive_name(name: str) -> bool:
    low = name.casefold()
    return low.endswith((".tar.gz", ".tgz", ".tar", ".tar.bz2", ".tar.xz"))


def parent_location(path: Path) -> Path | None:
    """Filesystem parent of *path*, or None at the volume root."""
    try:
        cur = Path(path).expanduser().resolve()
    except OSError:
        cur = Path(path).expanduser()
    parent = cur.parent
    if parent == cur:
        return None
    return parent


def start_location(raw: Path | str | None = None) -> Path:
    """Directory the import tree opens on (cwd, else home)."""
    if raw is not None:
        cand = Path(raw).expanduser()
    else:
        cand = Path.cwd()
    try:
        if cand.is_dir():
            return cand
    except OSError:
        pass
    return Path.home()


def visible_import_paths(paths: Iterable[Path]) -> list[Path]:
    """Directories and archive names; skip common junk folders."""
    kept: list[Path] = []
    for path in paths:
        if path.name in _SKIP_DIR_NAMES:
            continue
        try:
            is_dir = path.is_dir()
        except OSError:
            continue
        if is_dir or _looks_like_archive_name(path.name):
            kept.append(path)
    return kept


class ImportSourceTree(DirectoryTree):
    """Show directories and archive files; j / k step like other lists."""

    # Textual DirectoryTree defaults to emoji folders; use the same
    # chevrons as Tree so the face follows the theme.
    ICON_NODE = "▶ "
    ICON_NODE_EXPANDED = "▼ "
    ICON_FILE = "  "

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding(
            "h,left,backspace",
            "parent_dir",
            t("import-path-up"),
            show=True,
        ),
    ]

    class PathChanged(Message):
        """Posted after the tree root moves to a new directory."""

        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return visible_import_paths(paths)

    def action_parent_dir(self) -> None:
        nxt = parent_location(Path(self.path))
        if nxt is not None:
            self.path = nxt

    async def watch_path(self) -> None:
        await super().watch_path()
        if self.is_attached:
            self.post_message(self.PathChanged(Path(self.path)))


class ImportPathModal(Modal[str | None]):
    """Pick an import source from the tree, or type a path."""

    BINDINGS = [
        *MODAL_CANCEL_QUIT,
        Binding(
            "ctrl+s",
            "commit",
            t("import-path-open"),
            show=True,
            priority=True,
            id="edit.save",
        ),
    ]

    def __init__(self, location: Path | str | None = None) -> None:
        super().__init__()
        self._location = start_location(location)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container", classes="import-path"):
            yield Static("[bold]" + t("import-path-title") + "[/bold]")
            yield Static(t("import-path-hint"), classes="dim")
            yield Static(str(self._location), id="import-path-here", classes="dim")
            yield ImportSourceTree(str(self._location), id="import-path-tree")
            yield Input(placeholder=t("import-path-hint"), id="import-path-input")
            with Horizontal(id="import-path-buttons", classes="modal-footer"):
                yield Button(t("import-path-open"), variant="primary", id="import-path-ok")
                yield Button(t("import-path-up"), id="import-path-up")
                yield Button(t("ui-cancel"), id="import-path-cancel")

    def on_mount(self) -> None:
        self.query_one("#import-path-tree", ImportSourceTree).focus()

    def _set_here(self, path: Path) -> None:
        self.query_one("#import-path-here", Static).update(str(path))

    def action_commit(self) -> None:
        self._commit()

    def _set_input(self, path: Path) -> None:
        field = self.query_one("#import-path-input", Input)
        field.value = str(path)

    def _commit(self) -> None:
        raw = self.query_one("#import-path-input", Input).value.strip()
        self.dismiss(raw or None)

    def _open_or_commit(self, path: Path) -> None:
        if looks_like_import_source(path):
            self._set_input(path)
            self._commit()
            return
        if path.is_dir():
            tree = self.query_one("#import-path-tree", ImportSourceTree)
            tree.path = path
            self._set_here(Path(tree.path))
        self._set_input(path)

    def on_import_source_tree_path_changed(self, event: ImportSourceTree.PathChanged) -> None:
        if self.is_mounted:
            self._set_here(event.path)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self._set_input(event.path)
        self._commit()

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self._open_or_commit(event.path)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[object]) -> None:
        if not self.is_mounted:
            return
        data = event.node.data
        if isinstance(data, DirEntry):
            self._set_input(data.path)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "import-path-input":
            return
        raw = event.input.value.strip()
        if not raw:
            return
        self._open_or_commit(Path(raw).expanduser())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "import-path-ok":
            self._commit()
        elif event.button.id == "import-path-up":
            self.query_one("#import-path-tree", ImportSourceTree).action_parent_dir()
        elif event.button.id == "import-path-cancel":
            self.dismiss(None)
