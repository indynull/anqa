//! HUD chrome built on icedtea constructors.
//!
//! Prefer icedtea public APIs directly. Helpers here name a anqa
//! layout (pane tabs, form gutter), not a missing constructor.

use iced::widget::{column, container, stack, text, Space};
use iced::{Element, Length};
use icedtea::a11y::{A11y, Role};
use icedtea::collection::Tabs;
use icedtea::theme::Tokens;
use icedtea::typo::FontFace;
use icedtea::widget;

use crate::app::Message;
use crate::model::{OverviewSection, Tab};

/// icedtea [`layout::FORM_LABEL`] gutter for Overview (and any form stacks).
pub const LABEL_GUTTER: f32 = icedtea::layout::FORM_LABEL;

/// Determinate context / fill bar — icedtea [`widget::progress`].
pub fn context_progress<'a>(frac: f32, copy: &str, tea: Tokens) -> Element<'a, Message> {
    let label = if copy.is_empty() {
        widget::progress_label(frac, None, tea.clock_digits)
    } else {
        tea.clock_digits.map_str(copy)
    };
    widget::progress(
        frac.clamp(0.0, 1.0),
        None,
        Some(label.as_str()),
        false,
        tea,
        A11y::new("context", Role::Progress).with_value(label.clone()),
    )
}

/// Empty / loading shell — icedtea [`pattern::status_page`].
pub fn status_empty<'a>(
    title: impl Into<String>,
    detail: impl Into<String>,
    tea: Tokens,
) -> Element<'a, Message> {
    let title = title.into();
    icedtea::pattern::status_page(
        title.clone(),
        detail,
        None,
        tea,
        A11y::new(title, Role::Status),
    )
}

/// Browse pane tabs via icedtea [`widget::tab_bar`].
///
/// Tabs other than Overview freeze with [`Tabs::with_disabled`] until
/// a session is loaded.
pub fn pane_tabs<'a>(
    active: Tab,
    session_ready: bool,
    tabs: &'static [Tab],
    tea: Tokens,
) -> Element<'a, Message> {
    let titles: Vec<String> = tabs.iter().map(|t| t.label().to_string()).collect();
    let active_i = tabs.iter().position(|t| *t == active).unwrap_or(0);

    let mut bar = Tabs::new(titles);
    bar.select(active_i);
    bar.closable = false;
    if !session_ready {
        for (i, tab) in tabs.iter().enumerate() {
            if *tab != Tab::Overview {
                bar = bar.with_disabled(i);
            }
        }
    }
    widget::tab_bar(
        &bar,
        |i| Message::SetTab(tabs[i.min(tabs.len() - 1)]),
        |_| Message::Noop,
        0.0,
        false,
        tea,
        A11y::new("panes", Role::Tab),
    )
}

/// Session / Tasks strip inside Overview (same labels as TUI Summary).
pub fn overview_section_tabs<'a>(active: OverviewSection, tea: Tokens) -> Element<'a, Message> {
    let titles: Vec<String> = OverviewSection::ALL
        .iter()
        .map(|s| s.label().to_string())
        .collect();
    let active_i = OverviewSection::ALL
        .iter()
        .position(|s| *s == active)
        .unwrap_or(0);
    let mut bar = Tabs::new(titles);
    bar.select(active_i);
    bar.closable = false;
    widget::tab_bar(
        &bar,
        |i| {
            Message::SetOverviewSection(OverviewSection::ALL[i.min(OverviewSection::ALL.len() - 1)])
        },
        |_| Message::Noop,
        0.0,
        false,
        tea,
        A11y::new("overview section", Role::Tab),
    )
}

/// Labeled copyable value — icedtea [`widget::value_field`] with FORM_LABEL gutter.
pub fn labeled_value<'a>(
    title: &str,
    content: &'a iced::widget::text_editor::Content,
    on_action: impl Fn(iced::widget::text_editor::Action) -> Message + 'a,
    face: FontFace,
    tea: Tokens,
    a11y: A11y,
) -> Element<'a, Message> {
    widget::value_field(
        title,
        content,
        on_action,
        None,
        face,
        LABEL_GUTTER,
        tea,
        tea.direction,
        a11y,
    )
}

/// Non-copyable labeled readout via icedtea [`layout::form`] (same gutter).
pub fn labeled_plain<'a>(
    title: &str,
    value: impl Into<String>,
    tea: Tokens,
) -> Element<'a, Message> {
    let value = value.into();
    icedtea::layout::form(
        [(
            widget::label(
                title.to_string(),
                widget::LabelFace::Meta,
                tea,
                A11y::new(title, Role::Status),
            ),
            text(value).size(tea.body()).color(tea.text).into(),
        )],
        tea.density.space,
        tea.direction,
    )
}

/// `?` help sheet: shortcut rows. Search tokens appear while you type.
///
/// icedtea [`pattern::cheatsheet`] already pads for its scroll rail.
pub fn help_modal<'a>(
    backdrop: Element<'a, Message>,
    table: &icedtea::action::ActionTable<Message>,
    tea: Tokens,
    progress: f32,
) -> Element<'a, Message> {
    let heading = format!("Keyboard shortcuts · anqa {}", crate::VERSION);
    let list = icedtea::pattern::cheatsheet(table, "", tea, A11y::new("shortcuts", Role::List));
    let search_note =
        text("Tokens appear under the box as you type. Tab completes the last token.")
            .size(tea.meta())
            .color(tea.muted);
    let body = column![list, search_note].spacing(tea.density.gap());
    let sheet = widget::group_box(
        heading.clone(),
        body.into(),
        tea,
        widget::CardFace::Elevated,
        A11y::new(heading, Role::Dialog),
        None,
    );
    let card = container(sheet)
        .width(Length::Fixed(560.0))
        .height(Length::Fixed(520.0));
    let t = icedtea::motion::visual(progress, tea.reduced_motion);
    let sheet = icedtea::motion::overlay(
        card.into(),
        t,
        icedtea::motion::Slide::Up,
        tea,
        A11y::new("help", Role::Dialog),
    );
    stack![
        backdrop,
        container(Space::new().width(Length::Fill).height(Length::Fill))
            .width(Length::Fill)
            .height(Length::Fill)
            .style(move |_| icedtea::style::dim_backdrop_at(tea, t)),
        container(sheet)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill),
    ]
    .into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn label_gutter_matches_icedtea_form_label() {
        assert!((LABEL_GUTTER - icedtea::layout::FORM_LABEL).abs() < f32::EPSILON);
        const { assert!(LABEL_GUTTER >= 96.0) };
    }

    #[test]
    fn status_empty_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = status_empty("No turns", "Nothing segmented yet.", tea);
    }

    #[test]
    fn context_progress_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = context_progress(0.42, "Context 42%", tea);
        let _ = context_progress(0.0, "", tea);
        let _ = context_progress(1.0, "Context 100% · 1k / 1k", tea);
    }

    #[test]
    fn pane_tabs_ready_uses_tab_bar_path() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = pane_tabs(Tab::Overview, true, &Tab::ALL, tea);
        let _ = pane_tabs(Tab::Timeline, false, &Tab::ALL, tea);
    }

    #[test]
    fn pick_list_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = widget::pick_list(
            &["All", "Tools"][..],
            Some("All"),
            |_| Message::Noop,
            tea,
            widget::ControlSize::Default,
            A11y::new("Filter", Role::ComboBox),
        );
        let _ = widget::pick_list(
            &["All"][..],
            Some("All"),
            |_| Message::Noop,
            tea,
            widget::ControlSize::Default,
            A11y::new("Filter", Role::ComboBox).with_disabled(true),
        );
        let _ = widget::pick_list(
            &[] as &[&str],
            None,
            |_| Message::Noop,
            tea,
            widget::ControlSize::Default,
            A11y::new("empty", Role::ComboBox),
        );
    }

    #[test]
    fn search_input_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = widget::search_input(
            "q",
            Message::SearchChanged,
            Some(Message::SearchChanged(String::new())),
            Some(Message::ActivateSelected),
            tea,
            A11y::new("Search sessions", Role::TextBox),
            None,
            &[],
        );
    }

    #[test]
    fn status_bar_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let table = crate::help::footer_table(crate::help::KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: true,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: crate::model::Tab::Timeline,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let _ = icedtea::pattern::status_bar("", None, None, &table, tea, tea.direction);
    }

    #[test]
    fn help_modal_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let table = crate::help::help_table(crate::help::KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: crate::model::Tab::Overview,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let backdrop = status_empty("HUD", "backdrop", tea);
        let _ = help_modal(backdrop, &table, tea, 1.0);
    }
}
