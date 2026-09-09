"""Semantic style helpers: severity, tool families, syntax theme."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from anqa.ui.styles import (
    EMPHASIS,
    SEVERITY_LABEL,
    SEVERITY_STYLE,
    TOOL_FAMILY_STYLE,
    severity_style,
    syntax_theme_for_app,
    tag_pills,
    tag_rich_style,
    tool_family,
    tool_label,
    tool_style,
)


class TestToolFamily:
    def test_read_tools(self) -> None:
        for name in ("read_file", "grep", "list_dir", "web_search", "search_tool"):
            assert tool_family(name) == "read"

    def test_write_tools(self) -> None:
        for name in ("search_replace", "write_file", "todo_write", "image_gen"):
            assert tool_family(name) == "write"

    def test_shell_tools(self) -> None:
        for name in ("run_terminal_command", "monitor", "scheduler_create"):
            assert tool_family(name) == "shell"

    def test_agent_tools(self) -> None:
        for name in ("spawn_subagent", "ask_user_question", "enter_plan_mode"):
            assert tool_family(name) == "agent"

    def test_leftover_mcp_wrapper(self) -> None:
        assert tool_family("use_tool") == "mcp"
        assert tool_family("call_mcp") == "mcp"

    def test_mcp_qualified(self) -> None:
        assert tool_family("playwright__browser_navigate") == "mcp"
        assert tool_family("context7__query-docs") == "mcp"

    def test_unknown_defaults_other(self) -> None:
        assert tool_family("some_random_tool") == "other"

    def test_empty(self) -> None:
        assert tool_family("") == "other"

    def test_heuristic_read(self) -> None:
        assert tool_family("custom_read_data") == "read"
        assert tool_family("get_info") == "read"

    def test_heuristic_write(self) -> None:
        assert tool_family("custom_write_output") == "write"
        assert tool_family("save_data") == "write"

    def test_heuristic_shell(self) -> None:
        assert tool_family("run_script") == "shell"


class TestSyntaxThemeForApp:
    def test_dark_theme_default(self) -> None:
        app = SimpleNamespace(theme="textual-dark")
        assert syntax_theme_for_app(app) == "monokai"

    def test_light_theme(self) -> None:
        app = SimpleNamespace(theme="textual-light")
        assert syntax_theme_for_app(app) == "friendly"

    def test_solarized_uses_pygments_solarized(self) -> None:
        assert syntax_theme_for_app(SimpleNamespace(theme="solarized")) == "solarized-dark"
        assert syntax_theme_for_app(SimpleNamespace(theme="solarized-light")) == "solarized-light"

    def test_no_theme_attr(self) -> None:
        app = SimpleNamespace()
        assert syntax_theme_for_app(app) == "monokai"

    def test_none_theme(self) -> None:
        app = SimpleNamespace(theme=None)
        assert syntax_theme_for_app(app) == "monokai"


class TestToolStyle:
    def test_known_tool_returns_family_color(self) -> None:
        assert tool_style("read_file") == "default"
        assert tool_style("search_replace") == "green"
        assert tool_style("run_terminal_command") == "yellow"

    def test_unknown_tool_dim(self) -> None:
        assert tool_style("unknown") == "dim"

    def test_empty_name(self) -> None:
        assert tool_style("") == "dim"


class TestToolLabel:
    def test_label_contains_name(self) -> None:
        label = tool_label("read_file")
        assert "read file" in label

    def test_mcp_uses_middle_dot(self) -> None:
        from anqa.ui.styles import format_tool_display

        assert format_tool_display("playwright__browser_navigate") == (
            "playwright · browser navigate"
        )
        label = tool_label("playwright__browser_navigate")
        assert "playwright" in label
        assert "browser navigate" in label
        assert "dim" in label

    def test_truncates_long_names(self) -> None:
        label = tool_label("a" * 50, max_len=10)
        assert len(label) < 100

    def test_empty_name(self) -> None:
        label = tool_label("")
        assert "?" in label


class TestSeverityStyle:
    def test_known(self) -> None:
        assert severity_style("high") == "red bold"
        assert severity_style("medium") == "yellow bold"
        assert severity_style("low") == "yellow"

    def test_unknown_fallback(self) -> None:
        assert severity_style("critical") == "dim"


class TestLightFaces:
    def test_user_and_read_use_emphasis_not_hex(self) -> None:
        from anqa.ui.styles import event_type_markup

        user = event_type_markup("user_message_chunk", light=True)
        read = tool_style("read_file", light=True)
        assert "#" not in user
        assert "#" not in read
        assert user
        assert read


def test_tag_pills_are_padded_and_cap_with_count() -> None:
    empty = tag_pills([])
    assert empty.plain == ""
    two = tag_pills(["review", "ui"])
    assert two.plain == " review   ui "
    four = tag_pills(["review", "ui", "bug", "docs"])
    assert four.plain == " review   ui   bug  +1 "


def test_tag_rich_style_is_theme_primary_not_a_status_role() -> None:
    """No running app: primary falls back to emphasis, never success/caution/danger."""
    style = tag_rich_style()
    assert style == EMPHASIS
    assert "green" not in style
    assert "yellow" not in style
    assert "red" not in style


@pytest.mark.asyncio
async def test_tag_pills_wash_primary_like_the_desktop_badge() -> None:
    """ansi-dark: primary ink on a surface wash, not a solid primary fill."""
    from anqa.ui.styles import tag_pill_style
    from anqa.ui.theme import register_catalog_themes
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    class _ThemeApp(App):
        def compose(self) -> ComposeResult:
            yield Static("x")

        def on_mount(self) -> None:
            register_catalog_themes(self)
            self.theme = "ansi-dark"

    app = _ThemeApp()
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        face = tag_pill_style()
        assert face == "bold #6B9EFF on #39445A"


class TestConstants:
    def test_severity_label_keys(self) -> None:
        assert set(SEVERITY_LABEL.keys()) == {"high", "medium", "low"}

    def test_severity_style_keys(self) -> None:
        assert set(SEVERITY_STYLE.keys()) == {"high", "medium", "low"}

    def test_tool_family_style_keys(self) -> None:
        assert "read" in TOOL_FAMILY_STYLE
        assert "write" in TOOL_FAMILY_STYLE
        assert "other" in TOOL_FAMILY_STYLE
