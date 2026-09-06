//! Palette layout.

use iced::widget::{column, container, image, mouse_area, responsive, row, stack, text, Space};
use iced::{Alignment, Background, Border, Color, Element, Length, Padding};
use icedtea::a11y::{A11y, Role};
use icedtea::icon::Icon;
use icedtea::toast::ToastKind;
use icedtea::variant::Variant;

use crate::app::{ExtractKey, Hud, Message};
use crate::brand;
use crate::format::{
    body_paint_for, bookend_body_is_chrome, capped_display, context_meter_copy,
    display_message_text, display_tool_output, event_brand_role, event_is_monitor, event_raw_json,
    fenced_code_block, fmt_duration, format_note_time, format_tool_display, human_event_type_label,
    image_result_path, is_chat_message, is_tool_identity, job_command, job_description,
    job_event_id, job_event_label, job_exit_code, job_inspect_blocks, job_inspect_log,
    job_list_preview, job_output_path, job_status, list_event_detail, list_status_label,
    list_turn_bookend_title, looks_like_markdown, note_display_fields, overview_fields,
    overview_row_status, overview_subagent_rows, overview_task_rows, overview_workflow_rows,
    path_hint_from_raw, remap_turn_outcome_paren, sanitize_console_text, schedule_inspect_blocks,
    schedule_last_fire, session_duration_chip, status_tone, stills_from_session,
    subagent_inspect_blocks, subagent_list_preview, syntax_for_fence, syntax_for_tool_field,
    syntax_for_tool_output, timeline_body_text, timeline_count_caption, timeline_query_hit,
    tool_brand_role, tool_fields_from_raw, turn_chrome_face, workflow_for_event,
    workflow_name_from_raw, workflow_status_word, BodyPaint, BrandRole, ToolField,
};
use crate::kit;
use crate::live::{
    context_fraction, decode_many_choices, note_field_input_key, note_textarea_height,
    toggle_many_choice, CardMark, AGENT_OVERSCAN, NOTE_TURN_INPUT, OVERVIEW_LIST_OVERSCAN,
    STATS_ROW_H, TIMELINE_OVERSCAN, TURNS_OVERSCAN, WORKFLOW_INSPECT_H,
};
use crate::model::{DiffContext, KindFilter, OverviewSection, SchemaField, Tab};
use crate::motion::{MotionRole, PageLayer};
use crate::query::{highlight_query_spans, QuerySpanKind};
use crate::typo;
use crate::wire::{NoteRow, TimelineEvent, TurnRow, WorkflowChildRow};

fn rule(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::widget::rule_h(tea, A11y::new("rule", Role::Separator))
}

fn list_tile(tea: icedtea::theme::Tokens, selected: bool) -> iced::widget::container::Style {
    let s = tea.scheme();
    iced::widget::container::Style {
        background: Some(Background::Color(if selected {
            s.surface_container
        } else {
            Color::TRANSPARENT
        })),
        text_color: Some(s.on_surface),
        border: Border::default(),
        shadow: iced::Shadow::default(),
        snap: false,
    }
}

fn list_hairline(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    container(Space::new().height(1).width(Length::Fill))
        .width(Length::Fill)
        .style(move |_| {
            let s = tea.scheme();
            iced::widget::container::Style {
                background: Some(Background::Color(s.outline_variant.scale_alpha(0.5))),
                text_color: None,
                border: Border::default(),
                shadow: iced::Shadow::default(),
                snap: false,
            }
        })
        .into()
}

fn muted_meta(line: String, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    text(line)
        .size(tea.meta())
        .color(tea.muted)
        .font(typo::UI)
        .wrapping(iced::widget::text::Wrapping::None)
        .into()
}

#[allow(clippy::too_many_arguments)]
fn inset_search<'a>(
    value: &str,
    on_input: impl Fn(String) -> Message + 'a,
    on_clear: Option<Message>,
    on_submit: Option<Message>,
    tea: icedtea::theme::Tokens,
    a11y: A11y,
    input_id: Option<iced::widget::Id>,
    highlight: &[icedtea::widget::FieldRun],
) -> Element<'a, Message> {
    container(icedtea::widget::search_input(
        value, on_input, on_clear, on_submit, tea, a11y, input_id, highlight,
    ))
    .width(Length::Fill)
    .into()
}

fn catalog_query_runs(query: &str) -> Vec<icedtea::widget::FieldRun> {
    highlight_query_spans(query)
        .into_iter()
        .map(|mark| {
            icedtea::widget::FieldRun::new(mark.start, mark.end, catalog_query_ink(mark.kind))
        })
        .collect()
}

fn query_hint_line(hints: Vec<String>, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let line = hints.into_iter().take(8).collect::<Vec<_>>().join("   ");
    if line.is_empty() {
        Space::new().height(0).into()
    } else {
        text(line).size(tea.meta()).color(tea.muted).into()
    }
}

fn catalog_query_ink(kind: QuerySpanKind) -> icedtea::widget::FieldInk {
    match kind {
        QuerySpanKind::Field | QuerySpanKind::Operator => icedtea::widget::FieldInk::Warning,
        QuerySpanKind::Value => icedtea::widget::FieldInk::Success,
        // icedtea Error is a spelling underline, not danger body ink.
        QuerySpanKind::Unknown => icedtea::widget::FieldInk::Error,
    }
}

fn empty_sessions(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    kit::status_empty("No sessions", "Is anqad running?", tea)
}

fn no_session_matches(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    kit::status_empty(
        "No matches",
        "Try another query, or clear search for recent sessions.",
        tea,
    )
}

fn busy_pane() -> Element<'static, Message> {
    Space::new().width(Length::Fill).height(Length::Fill).into()
}

fn select_session(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    kit::status_empty(
        "Search for a session",
        "Type above, then Enter or click a match. Search again to switch.",
        tea,
    )
}

#[allow(dead_code)] // kept for footer status chrome; exercised in tests
fn status_copy(text: &str, err: bool, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let a11y = A11y::new(text.to_string(), Role::Status);
    if err {
        icedtea::widget::info_bar(ToastKind::Danger, text.to_string(), tea, a11y)
    } else {
        icedtea::widget::meta(text.to_string(), tea, a11y)
    }
}

fn tone_variant(tone: &str) -> Variant {
    match tone {
        "complete" => Variant::Success,
        "running" => Variant::Warning,
        "cancelled" => Variant::Danger,
        _ => Variant::Quiet,
    }
}

fn brand_variant(role: BrandRole) -> Variant {
    match role {
        BrandRole::Complete => Variant::Success,
        BrandRole::Running => Variant::Warning,
        BrandRole::Failed => Variant::Danger,
        BrandRole::Cream => Variant::Primary,
        BrandRole::Cancelled => Variant::Quiet,
    }
}

/// Small icedtea badge for a type or tool name (same face as session status).
fn label_badge(
    label: impl Into<String>,
    role: BrandRole,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    paint_badge(label.into(), brand_variant(role), tea)
}

/// Session / turn / severity status — same readable badge face everywhere.
fn status_chip(
    label: impl Into<String>,
    tone: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    paint_badge(label.into(), tone_variant(tone), tea)
}

fn paint_badge(
    label: String,
    variant: Variant,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let a11y = A11y::new(label.clone(), Role::Status);
    let (wash, ink, mut border, shadow) = icedtea::widget::chip_face(tea, variant);
    border.radius = tea.radius(icedtea::m3::shape::Component::Badge);
    // Pill ends eat icedtea Small [2, 5]; keep a readable inset.
    icedtea::a11y::attach(
        container(
            text(label)
                .size(tea.meta())
                .color(ink)
                .wrapping(iced::widget::text::Wrapping::None),
        )
        .padding(Padding {
            top: 4.0,
            right: 10.0,
            bottom: 4.0,
            left: 10.0,
        })
        .style(move |_| {
            let mut st = icedtea::style::fill(wash, ink);
            st.border = border;
            st.shadow = shadow;
            st
        })
        .into(),
        &a11y,
    )
}

/// Status plus identity chips — Overview, Recent cards, and the browse bar.
#[allow(clippy::too_many_arguments)]
fn session_state_row(
    status: &str,
    harness: &str,
    model: &str,
    duration: &str,
    subagent: bool,
    imported: bool,
    tea: icedtea::theme::Tokens,
    context: &str,
) -> Element<'static, Message> {
    let status_label = list_status_label(status, "");
    let mut bits: Vec<String> = Vec::new();
    if subagent {
        bits.push(String::from("subagent"));
    }
    if imported {
        bits.push(crate::format::origin_label("import").to_string());
    }
    if !harness.trim().is_empty() {
        bits.push(harness.trim().to_string());
    }
    if !model.trim().is_empty() {
        bits.push(model.trim().to_string());
    }
    if !duration.trim().is_empty() && duration != "—" {
        bits.push(duration.trim().to_string());
    }
    if !context.trim().is_empty() {
        bits.push(context.trim().to_string());
    }
    let meta = bits.join("  ·  ");
    let mut row = row![].spacing(8).align_y(Alignment::Center);
    if status_label != "—" && status_label != "idle" && !status_label.is_empty() {
        row = row.push(status_chip(
            status_label.clone(),
            status_tone(&status_label),
            tea,
        ));
    }
    if !meta.is_empty() {
        row = row.push(muted_meta(meta, tea));
    } else if status_label == "—" || status_label == "idle" {
        row = row.push(muted_meta("—".into(), tea));
    }
    row.into()
}

fn session_state_from_row(
    row: &crate::model::SessionRow,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let taken = session_duration_chip(row.duration_seconds, "");
    let harness = if !row.harness_label.is_empty() {
        row.harness_label.as_str()
    } else {
        row.harness.as_str()
    };
    session_state_row(
        &row.status_label(),
        harness,
        &row.model,
        &taken,
        false,
        row.imported || row.origin.eq_ignore_ascii_case("import"),
        tea,
        row.context_usage_compact.trim(),
    )
}

fn session_state_from_meta(
    meta: &crate::wire::SessionMeta,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let taken = session_duration_chip(meta.duration_seconds, &meta.duration);
    let harness = if !meta.harness_label.is_empty() {
        meta.harness_label.as_str()
    } else {
        meta.harness.as_str()
    };
    session_state_row(
        &meta.status_label(),
        harness,
        &meta.model,
        &taken,
        meta.is_subagent(),
        meta.imported || meta.origin.eq_ignore_ascii_case("import"),
        tea,
        "",
    )
}

fn still_paths(ev: &TimelineEvent, session_dir: &str) -> Vec<String> {
    if !ev.image_paths.is_empty() {
        return ev.image_paths.clone();
    }
    if !ev.image_path.is_empty() {
        return vec![ev.image_path.clone()];
    }
    let mut found = stills_from_session(session_dir, &ev.content);
    if found.is_empty() {
        found = stills_from_session(session_dir, &ev.preview);
    }
    found
}

fn still_image(path: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let a11y = A11y::new(path.to_string(), Role::Image);
    if std::path::Path::new(path).is_file() {
        icedtea::widget::image_slot(
            icedtea::widget::ImageSlot::Ready {
                handle: iced::widget::image::Handle::from_path(path),
                fit: iced::ContentFit::Contain,
            },
            Length::Fill,
            Length::Fixed(240.0),
            tea,
            a11y,
        )
    } else {
        icedtea::widget::image_slot(
            icedtea::widget::ImageSlot::Error(path.to_string()),
            Length::Fill,
            Length::Fixed(80.0),
            tea,
            a11y,
        )
    }
}

fn select_bound<'a>(
    hud: &'a Hud,
    id: String,
    fallback: &str,
    tea: icedtea::theme::Tokens,
    face: icedtea::typo::FontFace,
) -> Element<'a, Message> {
    let Some(buf) = hud.field(&id) else {
        return text(fallback.to_string())
            .size(tea.body())
            .font(face.font())
            .into();
    };
    let a11y_id = id.clone();
    icedtea::widget::selectable(
        buf,
        move |action| Message::Select {
            id: id.clone(),
            action,
        },
        tea,
        face,
        A11y::new(a11y_id, Role::TextBox),
    )
}

fn markdown_bound<'a>(
    hud: &'a Hud,
    id: String,
    fallback: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    let Some(doc) = hud.markdown(&id) else {
        return select_bound(hud, id, fallback, tea, icedtea::typo::FontFace::Ui);
    };
    let Some(slot) = hud.markdown_slot(&id) else {
        return select_bound(hud, id, fallback, tea, icedtea::typo::FontFace::Ui);
    };
    let span = hud.markdown_span(&id);
    icedtea::widget::markdown_view(
        &doc.items,
        span,
        move |ev| Message::MdPointer { slot, ev },
        tea,
        |_| Message::Noop,
        A11y::new(id, Role::Group),
    )
}

fn code_inset<'a>(
    hud: &'a Hud,
    id: &str,
    fallback: &str,
    syntax: &str,
    wrap: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    // Prefer the selectable bind buffer; fall back to *fallback* so a missing
    // bind (e.g. first paint before extract) does not paint an empty Code pane.
    let Some(buf) = hud.field(id) else {
        if fallback.is_empty() {
            return text(String::new()).size(tea.meta()).font(typo::MONO).into();
        }
        return text(fallback.to_string())
            .size(tea.meta())
            .font(typo::MONO)
            .into();
    };
    let id = id.to_string();
    // Real iced highlighter (syntect) — not plain mono ``code_block``.
    let lang = if syntax.is_empty() { "txt" } else { syntax };
    icedtea::widget::highlighted_code(
        buf,
        lang,
        move |action| Message::Select {
            id: id.clone(),
            action,
        },
        tea,
        hud.theme_name(),
        Length::Shrink,
        wrap,
        A11y::new("code", Role::TextBox),
    )
}

pub fn layout(hud: &Hud) -> Element<'_, Message> {
    let tok = hud.tokens();
    let tea = hud.tokens();
    let mut search = row![
        icedtea::widget::tooltip_wrap(
            mouse_area(
                image(brand::chrome_handle(crate::theme::canvas_is_dark(tok)))
                    .width(brand::chrome_width())
                    .height(brand::chrome_height()),
            )
            .on_press(Message::SessionsHome)
            .into(),
            "Session list",
            icedtea::widget::TooltipAnchor::Follow,
            tea,
            A11y::button("Session list"),
        ),
        inset_search(
            hud.query(),
            Message::SearchChanged,
            Some(Message::SearchChanged(String::new())),
            Some(Message::ActivateSelected),
            tea,
            A11y::new("Search sessions", Role::TextBox),
            Some(hud.search_id()),
            &catalog_query_runs(hud.query()),
        ),
    ]
    .spacing(12)
    .align_y(Alignment::Center);
    if !hud.window_mode() {
        search = search.push(pop_out_control(tok, tea));
    }
    let hints = hud.query_hints();
    // Keep this column always so the search field is not remounted when
    // hints appear (that drop of focus eats the next keystrokes).
    let hint: Element<'_, Message> = query_hint_line(hints, tea);
    let search: Element<'_, Message> = column![search, hint]
        .spacing(tea.density.gap() / 2.0)
        .padding(Padding::from([tea.density.gap(), tea.density.inset()]))
        .into();

    // Spotlight: search → pick → full-width browse. Type again to switch.
    let body: Element<'_, Message> = {
        let inner = if hud.browse_mode() {
            detail_pane(hud)
        } else {
            session_picker(hud)
        };
        if hud.page_layer() == PageLayer::Browse && hud.page_moving() {
            icedtea::motion::overlay(
                inner,
                hud.page_progress(),
                hud.page_slide(),
                tea,
                A11y::new("browse", Role::Group),
            )
        } else {
            inner
        }
    };

    let foot = footer(hud, tea);

    let mut stack = column![search, rule(tea), body, rule(tea), foot];
    for t in hud.toasts().iter() {
        let id = t.id;
        stack = stack.push(icedtea::widget::toast_view(
            t,
            Message::ToastDismiss(id),
            tea,
            A11y::new(t.text.clone(), Role::Status),
        ));
    }
    let shell = container(stack)
        .width(Length::Fill)
        .height(Length::Fill)
        .padding(1)
        .style(move |_| icedtea::style::shell(tea));
    let busy = icedtea::widget::busy_overlay(
        shell.into(),
        hud.page_busy(),
        hud.spin_phase(),
        tea,
        A11y::new("Loading", Role::Progress),
    );
    let chrome: Element<'_, Message> = busy;
    // Always stack the shell so opening the context menu does not remount
    // selectable editors (iced only paints a selection while they stay focused).
    let mut layers = stack![chrome];
    if let Some(origin) = hud.context_origin() {
        layers = layers.push(icedtea::pattern::context_menu(
            hud.context_actions(),
            origin,
            hud.window_size(),
            Message::ContextDismiss,
            1.0,
            tea,
        ));
    }
    let scene = layers.into();
    if hud.help_open() {
        return fade_palette(
            kit::help_modal(
                scene,
                &crate::help::help_table_for(hud.key_scope(), hud.key_overlay()),
                tea,
            ),
            hud,
            tea,
        );
    }
    fade_palette(scene, hud, tea)
}

fn fade_palette<'a>(
    child: Element<'a, Message>,
    hud: &Hud,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    // OverlayLayer still does not implement Widget::overlay, so pick lists
    // never open while this wrapper is mounted. Tokens::fade already paints
    // the show/hide. Keep the layer only while the fade is running.
    if !hud.overlay_moving() {
        return child;
    }
    icedtea::motion::overlay(
        child,
        hud.overlay_progress(),
        icedtea::motion::Slide::Up,
        tea,
        A11y::new("palette", Role::Group),
    )
}

fn page_body<'a>(
    child: Element<'a, Message>,
    hud: &Hud,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    // OverlayLayer still does not implement Widget::overlay, so pick lists
    // (Diff Turn, Timeline Filter) never open while this wrapper is mounted.
    // List clip state is kept by cover_stack under detail, not by this wrap.
    if hud.page_layer() != PageLayer::Pane
        || !hud.page_moving()
        || matches!(hud.page_role(), MotionRole::Step)
    {
        return container(child)
            .width(Length::Fill)
            .height(Length::Fill)
            .into();
    }
    container(icedtea::motion::overlay(
        child,
        hud.page_progress(),
        hud.page_slide(),
        tea,
        A11y::new("page", Role::Group),
    ))
    .width(Length::Fill)
    .height(Length::Fill)
    .clip(true)
    .into()
}

/// Keep `base` mounted when a full-pane cover is up so list clip state lives.
fn cover_stack<'a>(
    base: Element<'a, Message>,
    cover: Option<Element<'a, Message>>,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    let mut layers = stack![container(base).width(Length::Fill).height(Length::Fill)];
    if let Some(top) = cover {
        let paper = tea.scheme().surface;
        layers = layers.push(
            container(top)
                .width(Length::Fill)
                .height(Length::Fill)
                .style(move |_| iced::widget::container::Style {
                    background: Some(Background::Color(paper)),
                    ..iced::widget::container::Style::default()
                }),
        );
    }
    layers.into()
}

/// Full-width session matches (Spotlight results). No permanent left rail.
fn session_picker(hud: &Hud) -> Element<'_, Message> {
    responsive(move |size| session_picker_at(hud, size.height.max(1.0))).into()
}

fn session_picker_at(hud: &Hud, viewport: f32) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let idle = hud.query().trim().is_empty();
    if hud.sessions().is_empty() {
        if idle {
            // Catalog empty vs still loading — same honest empty; no full dump.
            return if hud.catalog_busy() {
                busy_pane()
            } else {
                empty_sessions(tea)
            };
        }
        return no_session_matches(tea);
    }
    let mut window = hud.list_window();
    window.viewport = viewport.max(1.0);
    let gap = tea.density.gap();
    let inset = tea.density.inset();
    let rows = hud.sessions();
    let selected = hud.list_selection().primary();
    let list = icedtea::widget::virtual_column(
        hud.session_heights(),
        window,
        1,
        selected,
        Message::ListScroll,
        |c| Message::FocusSession(c.id),
        Some(hud.list_scroll_id()),
        tea,
        move |i| {
            let Some(row) = rows.get(i) else {
                return Space::new().height(0).into();
            };
            session_list_card(row, i, selected == Some(i), tea)
        },
        A11y::new("Sessions", Role::List),
    );
    if idle {
        return column![
            icedtea::widget::meta("Recent", tea, A11y::new("Recent", Role::Header),),
            list,
        ]
        .spacing(gap)
        .padding(Padding::from([gap, inset]))
        .height(Length::Fill)
        .into();
    }
    container(list)
        .padding(Padding::from([gap, inset]))
        .height(Length::Fill)
        .into()
}

fn session_list_card(
    row: &crate::model::SessionRow,
    index: usize,
    selected: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let title = text(row.display_title().to_string())
        .size(tea.body())
        .font(if selected { typo::UI_BOLD } else { typo::UI })
        .color(tea.text)
        .width(Length::Fill);
    let body = column![title, session_state_from_row(row, tea)]
        .spacing(4)
        .width(Length::Fill);
    column![
        mouse_area(
            container(body)
                .padding(tea.density.inset())
                .width(Length::Fill)
                .style(move |_| list_tile(tea, selected)),
        )
        .on_double_click(Message::SelectSession(index)),
        list_hairline(tea),
        Space::new().height(crate::live::LIST_CARD_GAP - 1.0),
    ]
    .into()
}

fn detail_pane(hud: &Hud) -> Element<'_, Message> {
    let session_ready = hud.overview().is_some() || !hud.overview_pending().is_empty();
    let tea = hud.tokens();
    let tabs = container(kit::pane_tabs(
        hud.tab(),
        session_ready,
        hud.visible_tabs(),
        tea,
    ))
    .padding(Padding::from([tea.density.gap(), tea.density.inset()]));

    let mut stack = column![].spacing(0).height(Length::Fill);
    if let Some(bar) = browse_session_bar(hud, tea) {
        stack = stack.push(bar);
    }
    stack = stack.push(tabs);
    // List filters stay off while reading a full-pane event.
    if hud.tab() == Tab::Overview && hud.overview().is_some() {
        stack = stack.push(
            container(kit::overview_section_tabs(hud.overview_section(), tea)).padding(Padding {
                top: 0.0,
                right: tea.density.inset(),
                bottom: tea.density.gap(),
                left: tea.density.inset(),
            }),
        );
    }
    if hud.tab() == Tab::Timeline && hud.overview().is_some() && hud.timeline_open().is_none() {
        stack = stack.push(timeline_filter(hud));
    }
    if hud.tab() == Tab::Turns && hud.overview().is_some() {
        stack = stack.push(turns_filter(hud));
    }
    let body: Element<'_, Message> = if hud.overview().is_none() {
        if !hud.overview_pending().is_empty() {
            busy_pane()
        } else {
            select_session(hud.body_tokens())
        }
    } else {
        match hud.tab() {
            Tab::Overview => overview_tab(hud),
            Tab::Turns | Tab::Timeline | Tab::Diff => column![].into(),
            Tab::Notes => notes_tab(hud),
        }
    };
    if hud.tab() == Tab::Timeline && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(timeline_tab(hud))
                .padding([tea.density.gap(), tea.density.inset()])
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else if hud.tab() == Tab::Turns && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(turns_tab(hud))
                .padding([tea.density.gap(), tea.density.inset()])
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else if hud.tab() == Tab::Diff && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(diff_tab(hud))
                .padding(Padding {
                    top: 0.0,
                    right: tea.density.inset(),
                    bottom: tea.density.gap(),
                    left: tea.density.inset(),
                })
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else if hud.tab() == Tab::Overview
        && hud.overview().is_some()
        && overview_virtual_body(hud.overview_section())
    {
        stack = stack.push(page_body(
            container(overview_tab(hud))
                .padding([tea.density.gap(), tea.density.inset()])
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else if hud.tab() == Tab::Notes && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(notes_tab(hud))
                .padding([tea.density.gap(), tea.density.inset()])
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else {
        stack = stack.push(page_body(
            icedtea::widget::scroll(
                container(body)
                    .padding(tea.density.sheet())
                    .width(Length::Fill)
                    .into(),
                tea,
                A11y::new("Detail", Role::Group),
                false,
                None,
                None::<fn(f32) -> Message>,
            ),
            hud,
            tea,
        ));
    }
    container(stack)
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
}

/// Session identity under the search bar while browsing (no left rail).
fn browse_session_bar<'a>(
    hud: &'a Hud,
    tea: icedtea::theme::Tokens,
) -> Option<Element<'a, Message>> {
    let title = if let Some(o) = hud.overview() {
        let t = o.meta.title.trim();
        if t.is_empty() {
            let l = o.meta.label.trim();
            if l.is_empty() {
                hud.overview_sid().to_string()
            } else {
                l.to_string()
            }
        } else {
            t.to_string()
        }
    } else if !hud.overview_pending().is_empty() {
        hud.overview_pending().to_string()
    } else {
        return None;
    };
    let status = if let Some(o) = hud.overview() {
        let s = o.meta.status_label();
        if s.is_empty() {
            String::new()
        } else {
            s
        }
    } else {
        String::new()
    };
    let mut row = row![text(title)
        .size(tea.body())
        .font(typo::UI_BOLD)
        .color(tea.text),]
    .spacing(10)
    .align_y(Alignment::Center)
    .width(Length::Fill);
    if let Some(o) = hud.overview() {
        row = row.push(session_state_from_meta(&o.meta, tea));
    } else if !status.is_empty() {
        row = row.push(session_state_row(
            &status, "", "", "", false, false, tea, "",
        ));
    }
    row = row.push(Space::new().width(Length::Fill));
    row = row.push(icedtea::widget::meta(
        "Search again to switch",
        tea,
        A11y::new("Search again to switch", Role::Status),
    ));
    Some(
        container(row)
            .padding(Padding::from([tea.density.gap(), tea.density.inset()]))
            .width(Length::Fill)
            .into(),
    )
}

fn timeline_tail_toggle(hud: &Hud) -> Element<'_, Message> {
    // Compact meta + track. icedtea `widget::switch` is a form row (Fill)
    // and stretches across the picks bar.
    let tea = hud.tokens();
    let on = hud.timeline_follow_tail();
    let knob = iced::widget::toggler(on)
        .style(icedtea::style::switch_style(tea))
        .on_toggle(Message::TimelineTail);
    icedtea::a11y::attach(
        row![
            icedtea::widget::meta("Tail", tea, A11y::new("Tail", Role::Header)),
            knob,
        ]
        .spacing(tea.density.gap())
        .align_y(Alignment::Center)
        .into(),
        &A11y::new("Tail", Role::Switch).with_checked(on),
    )
}

fn timeline_filter(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    // Two rows: picks + optional range; full-width search below so it never
    // shares width with Turn/Filter (one-row bar clipped or overlapped the field).
    let mut picks = row![].spacing(tea.density.gap()).align_y(Alignment::Center);
    if !hud.hide_events_turn_pick() {
        picks = picks.push(icedtea::widget::meta(
            "Turn",
            tea,
            A11y::new("Turn", Role::Header),
        ));
        picks = picks.push(icedtea::widget::pick_list(
            hud.events_turn_options(),
            Some(hud.events_turn_selected()),
            Message::EventsTurnPicked,
            tea,
            icedtea::widget::ControlSize::Default,
            A11y::new("Turn", Role::ComboBox),
        ));
    }
    picks = picks.push(icedtea::widget::meta(
        "Filter",
        tea,
        A11y::new("Filter", Role::Header),
    ));
    picks = picks.push(icedtea::widget::pick_list(
        &KindFilter::ALL[..],
        Some(hud.timeline_kind()),
        Message::TimelineKind,
        tea,
        icedtea::widget::ControlSize::Default,
        A11y::new("Filter", Role::ComboBox),
    ));
    picks = picks
        .push(Space::new().width(Length::Fill))
        .width(Length::Fill);
    if hud.show_timeline_tail() {
        picks = picks.push(timeline_tail_toggle(hud));
    }
    if let Some(cap) = timeline_count_caption(&hud.timeline_meta()) {
        picks = picks.push(icedtea::widget::meta(
            cap.to_string(),
            tea,
            A11y::new(cap.to_string(), Role::Status),
        ));
    }
    let search = container(inset_search(
        hud.timeline_query_draft(),
        Message::TimelineQuery,
        Some(Message::TimelineQuery(String::new())),
        None,
        tea,
        A11y::new("Search events…", Role::TextBox),
        Some(hud.tl_search_id()),
        &catalog_query_runs(hud.timeline_query_draft()),
    ))
    .width(Length::Fill);
    let hint = query_hint_line(hud.timeline_query_hints(), tea);
    column![picks, search, hint]
        .spacing(tea.density.gap())
        .width(Length::Fill)
        .padding(Padding::from([tea.density.gap(), tea.density.inset()]))
        .into()
}

fn overview_virtual_body(section: OverviewSection) -> bool {
    matches!(
        section,
        OverviewSection::Tasks
            | OverviewSection::Workflows
            | OverviewSection::Subagents
            | OverviewSection::Stats
    )
}

fn overview_tab(hud: &Hud) -> Element<'_, Message> {
    match hud.overview_section() {
        OverviewSection::Session => overview_session(hud),
        OverviewSection::Tasks => overview_tasks(hud),
        OverviewSection::Workflows => overview_workflows(hud),
        OverviewSection::Subagents => overview_subagents(hud),
        OverviewSection::Stats => overview_stats(hud),
    }
}

fn overview_session(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let o = hud.overview().unwrap();
    let meta = &o.meta;
    let mut title = meta.title.clone();
    if title.is_empty() {
        title = hud.overview_sid().to_string();
    }
    if meta.is_subagent() && !title.to_ascii_lowercase().starts_with("subagent") {
        title = format!("Subagent · {title}");
    }
    let mut summary = o.summary.clone();
    if summary.is_empty() {
        summary = meta.summary.clone();
    }
    if summary.is_empty() {
        summary = "No summary text for this session.".into();
    }
    let ctx_frac = context_fraction(meta.context_window_usage_pct, meta.context_compact());
    let status_row = session_state_from_meta(meta, tea);
    // Title lives on the browse bar. Status is badges only.
    let mut col = column![status_row].spacing(8);
    // Progress only where context matters (session detail), and only when known.
    if ctx_frac > 0.0 {
        let copy = context_meter_copy(
            ctx_frac,
            meta.context_tokens_used,
            meta.context_window_tokens,
            meta.context_compact(),
        );
        col = col.push(kit::context_progress(ctx_frac, &copy, tea));
    }

    if o.notes.count > 0 {
        col = col.push(icedtea::widget::banner(
            format!("{} notes — open the Notes pane", o.notes.count),
            Some(("Notes".into(), Message::SetTab(Tab::Notes))),
            tea,
            A11y::new("notes", Role::Status),
        ));
    }
    if summary != title && summary != "No summary text for this session." {
        // One selectable buffer. markdown_view lays out every item on each
        // wheel tick (same tax as Turns cards before they went plain).
        col = col.push(select_bound(
            hud,
            "overview.summary".into(),
            &summary,
            hud.tokens(),
            icedtea::typo::FontFace::Ui,
        ));
    } else if summary == "No summary text for this session." {
        col = col.push(icedtea::widget::meta(
            summary,
            hud.tokens(),
            A11y::new("summary", Role::Status),
        ));
    }
    for field in overview_fields(meta, &o.turns) {
        col = col.push(kv(hud, &field));
    }
    col.into()
}

fn overview_tasks(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    overview_run_list(
        hud,
        overview_task_rows(&o.background_jobs, &o.schedules),
        "No tasks",
        "No background jobs or schedules.",
    )
}

fn overview_workflows(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    overview_run_list(
        hud,
        overview_workflow_rows(&o.workflows),
        "No workflows",
        "No workflow runs in this session.",
    )
}

fn overview_subagents(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    overview_run_list(
        hud,
        overview_subagent_rows(&o.turns.subagent_runs),
        "No subagents",
        "No subagent runs in this session.",
    )
}

fn overview_run_list<'a>(
    hud: &'a Hud,
    rows: Vec<crate::format::OverviewTaskRow>,
    empty_title: &'static str,
    empty_detail: &'static str,
) -> Element<'a, Message> {
    let tea = hud.body_tokens();
    if rows.is_empty() {
        return kit::status_empty(empty_title, empty_detail, tea);
    }
    let focus = hud.tasks_focus();
    icedtea::widget::virtual_column(
        hud.overview_heights(),
        hud.overview_window(),
        OVERVIEW_LIST_OVERSCAN,
        focus,
        Message::OverviewScroll,
        |c| Message::FocusOverviewRow(c.id),
        Some(hud.overview_scroll_id()),
        tea,
        move |i| {
            let Some(row) = rows.get(i) else {
                return Space::new().height(0).into();
            };
            let selected = focus == Some(i);
            let status = overview_row_status(row);
            let kind = format_tool_display(&row.kind);
            let ink = if row.openable { tea.text } else { tea.muted };
            let mut chips = row![].spacing(8).align_y(Alignment::Center);
            if status != "—" && status != "idle" && !status.is_empty() {
                chips = chips.push(status_chip(status.clone(), status_tone(&status), tea));
            }
            if !kind.is_empty() {
                chips = chips.push(muted_meta(kind, tea));
            }
            let name = text(row.label.clone())
                .size(tea.body())
                .font(typo::UI)
                .color(ink)
                .wrapping(iced::widget::text::Wrapping::Word);
            let mut header = row![chips, Space::new().width(Length::Fill)]
                .spacing(6)
                .align_y(Alignment::Center)
                .width(Length::Fill);
            if row.openable {
                header = header.push(text("›").size(tea.meta()).color(tea.muted));
            }
            let face = column![header, name].spacing(4).width(Length::Fill);
            let card = container(face)
                .padding(tea.density.inset())
                .width(Length::Fill)
                .style(move |_| list_tile(tea, selected));
            column![
                mouse_area(card).on_double_click(Message::OpenOverviewRow(i)),
                list_hairline(tea),
            ]
            .into()
        },
        A11y::new(empty_title, Role::List),
    )
}

fn overview_stats(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    if hud.timeline_loading() && hud.stats_table().rows.is_empty() {
        return busy_pane();
    }
    if hud.stats_table().rows.is_empty() {
        return kit::status_empty(
            "No stats yet",
            "Open Timeline to fill event and tool counts.",
            tea,
        );
    }
    icedtea::widget::data_table(
        hud.stats_table(),
        hud.stats_selection(),
        hud.stats_cursor(),
        hud.stats_cols(),
        true,
        hud.stats_window(),
        STATS_ROW_H,
        2,
        Message::StatsCell,
        Message::StatsSort,
        Message::StatsScroll,
        Message::StatsHScroll,
        Some(hud.stats_scroll_id()),
        |_| Message::Noop,
        tea,
        A11y::new("Stats", Role::Table),
    )
}

/// One Overview meta row via icedtea value_field / plain labeled readout.
fn kv<'a>(hud: &'a Hud, field: &crate::format::OverviewField) -> Element<'a, Message> {
    glance_row(hud, field)
}

fn glance_row<'a>(hud: &'a Hud, field: &crate::format::OverviewField) -> Element<'a, Message> {
    let tea = hud.tokens();
    let id = ExtractKey::Overview(field.key).id();
    let face = if field.mono {
        icedtea::typo::FontFace::Mono
    } else {
        icedtea::typo::FontFace::Ui
    };
    let _ = field.danger;
    if let Some(buf) = hud.field(&id) {
        let value = kit::labeled_value(
            field.label,
            buf,
            {
                let id = id.clone();
                move |action| Message::Select {
                    id: id.clone(),
                    action,
                }
            },
            face,
            tea,
            A11y::new(field.label, Role::Group),
        );
        let Some(target) = field.open.as_ref() else {
            return value;
        };
        return row![value, glance_open_btn(target, tea)]
            .spacing(tea.density.gap())
            .align_y(Alignment::Center)
            .into();
    }
    if field.danger {
        return kit::labeled_plain(field.label, field.value.clone(), tea);
    }
    kit::labeled_plain(field.label, "", tea)
}

fn footer(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let tone = if hud.status_err() {
        Some(ToastKind::Danger)
    } else {
        None
    };
    icedtea::pattern::status_bar(
        hud.status(),
        tone,
        None,
        &crate::help::footer_table_for(hud.key_scope(), hud.key_overlay()),
        tea,
        tea.direction,
    )
}

fn chip_btn(label: String, msg: Message, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::widget::chip(
        label.clone(),
        Some(msg),
        None,
        tea,
        Variant::Chip,
        icedtea::widget::ChipKind::Assist,
        icedtea::icon::Icons::NONE,
        A11y::button(label),
    )
}

fn note_add_btn(msg: Message, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::widget::tooltip_wrap(
        icedtea::widget::icon_button(
            Icon::DocumentCreate,
            Some(msg),
            tea,
            Variant::Elevated,
            icedtea::widget::ControlSize::Default,
            A11y::button("Add note"),
        ),
        "Add note",
        icedtea::widget::TooltipAnchor::Follow,
        tea,
        A11y::button("Add note"),
    )
}

const OPEN_MARK: &[u8] = br#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path fill="black" d="M4 3h5v2H6v5h5V7h2v5H4zm5 0h4v4h-1.5V5.5L8 9 7 8l3.5-3.5H9z"/></svg>"#;

fn glance_open_btn(target: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let http = target.starts_with("http");
    let label = if http { "Open" } else { "Open folder" };
    let icon = if http {
        icedtea::icon::Glyph::Bytes(OPEN_MARK)
    } else {
        Icon::FolderOpen.into()
    };
    icedtea::widget::tooltip_wrap(
        icedtea::widget::icon_button(
            icon,
            Some(Message::OpenExternal(target.to_string())),
            tea,
            Variant::Elevated,
            icedtea::widget::ControlSize::Default,
            A11y::button(label),
        ),
        label,
        icedtea::widget::TooltipAnchor::Follow,
        tea,
        A11y::button(label),
    )
}

#[allow(dead_code)]
fn command_end(child: Element<'static, Message>) -> Element<'static, Message> {
    row![Space::new().width(Length::Fill), child]
        .width(Length::Fill)
        .align_y(Alignment::Center)
        .into()
}

fn card_chips(
    hud: &Hud,
    mark: Option<CardMark>,
    note: Option<Message>,
    jump: Option<Message>,
) -> Element<'static, Message> {
    // Full-width bar (open cards / forms): marks left, commands right.
    row![
        card_marks_row(hud, mark),
        Space::new().width(Length::Fill),
        card_cmds_row(hud, note, jump),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .width(Length::Fill)
    .into()
}

fn card_marks_row(hud: &Hud, mark: Option<CardMark>) -> Element<'static, Message> {
    let tea = hud.tokens();
    let mut marks = row![].spacing(4);
    if let Some(m) = mark {
        if m.notes > 0 {
            let nid = m.first_note_id;
            marks = marks.push(chip_btn(
                format!("n{}", m.notes),
                if nid.is_empty() {
                    Message::SetTab(Tab::Notes)
                } else {
                    Message::OpenNote(nid)
                },
                tea,
            ));
        }
    }
    marks.into()
}

fn card_cmds_row(
    hud: &Hud,
    note: Option<Message>,
    jump: Option<Message>,
) -> Element<'static, Message> {
    let tea = hud.tokens();
    let tok = hud.tokens();
    let mut cmds = row![].spacing(4);
    if let Some(msg) = note {
        cmds = cmds.push(note_add_btn(msg, tea));
    }
    if let Some(msg) = jump {
        cmds = cmds.push(jump_control(msg, tok.muted, tea));
    }
    cmds.into()
}

/// Closed list tile: title plus one badge row (same face as Recent).
fn closed_list_card(
    title: String,
    badges: Element<'static, Message>,
    on_open: Message,
    selected: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let body = if title.trim().is_empty() {
        column![badges].width(Length::Fill)
    } else {
        let title = text(title)
            .size(tea.body())
            .font(if selected { typo::UI_BOLD } else { typo::UI })
            .color(tea.text)
            .width(Length::Fill)
            .wrapping(iced::widget::text::Wrapping::None);
        column![title, badges].spacing(4).width(Length::Fill)
    };
    column![
        mouse_area(
            container(body)
                .padding(tea.density.inset())
                .width(Length::Fill)
                .style(move |_| list_tile(tea, selected)),
        )
        .on_double_click(on_open),
        list_hairline(tea),
    ]
    .into()
}

fn turn_title(t: &TurnRow) -> String {
    let plain = plain_card_text(&t.summary);
    if plain.is_empty() {
        remap_turn_outcome_paren(&t.face_caption())
    } else {
        capped_display(&plain, 180)
    }
}

fn event_note(ev: &TimelineEvent) -> Message {
    Message::StartNote {
        turn: ev.turn_index.map(|n| n.to_string()).unwrap_or_default(),
        event: ev.index.to_string(),
    }
}

fn event_type_human(ev: &TimelineEvent) -> String {
    human_event_type_label(
        &ev.event_type,
        &ev.type_label,
        &ev.kind,
        event_is_monitor(&ev.raw_input),
    )
}

/// Human type + brand role for the heading badge next to ``#index``.
fn event_type_paint(ev: &TimelineEvent) -> Option<(String, BrandRole)> {
    let human = event_type_human(ev);
    if human.is_empty() {
        return None;
    }
    Some((
        human,
        event_brand_role(&ev.event_type, &ev.kind, type_badge_is_error(ev)),
    ))
}

fn type_badge_is_error(ev: &TimelineEvent) -> bool {
    ev.kind == "error" || ev.event_type.ends_with("_error") || ev.event_type == "error"
}

fn event_tool_role(ev: &TimelineEvent) -> BrandRole {
    tool_brand_role(&ev.tool_name, false).unwrap_or(BrandRole::Cancelled)
}

fn event_error_icon(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let mut scheme = tea.scheme();
    scheme.on_surface = tea.danger;
    icedtea::widget::icon_svg(
        Icon::Error,
        icedtea::theme::Tokens::from(scheme),
        A11y::new("error", Role::Image),
    )
}

/// ``#index`` + type badge on one row (turn / time muted after).
fn event_list_heading(
    ev: &TimelineEvent,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let mut head = row![muted_meta(format!("#{}", ev.index), tea),]
        .spacing(8)
        .align_y(Alignment::Center);
    if let Some((human, role)) = event_type_paint(ev) {
        head = head.push(label_badge(human, role, tea));
    }
    if is_tool_identity(&ev.kind, &ev.event_type, &ev.tool_name) {
        let name = format_tool_display(&ev.tool_name);
        if !name.is_empty() {
            head = head.push(label_badge(name, event_tool_role(ev), tea));
        }
    }
    if ev.is_error {
        head = head.push(event_error_icon(tea));
    }
    let mut rest: Vec<String> = Vec::new();
    if ev.event_type == "session_recap"
        && ev.raw_input.get("auto").and_then(|v| v.as_bool()) == Some(true)
    {
        rest.push(String::from("auto"));
    }
    if let Some(turn) = ev.turn_index {
        rest.push(format!("turn {turn}"));
    }
    let time = ev.time.trim();
    if !time.is_empty() {
        rest.push(time.to_string());
    }
    if !rest.is_empty() {
        head = head.push(muted_meta(rest.join("  ·  "), tea));
    }
    head.into()
}

fn event_list_title(ev: &TimelineEvent) -> String {
    let tool_row = is_tool_identity(&ev.kind, &ev.event_type, &ev.tool_name);
    let raw_preview = if ev.preview.is_empty() {
        ev.content.as_str()
    } else {
        ev.preview.as_str()
    };
    let raw_preview = if raw_preview.is_empty() {
        ev.heading.as_str()
    } else {
        raw_preview
    };
    let preview = if matches!(
        ev.event_type.as_str(),
        "turn_started" | "turn_ended" | "turn_completed"
    ) {
        list_turn_bookend_title(&ev.event_type, raw_preview)
    } else if job_event_label(&ev.event_type, event_is_monitor(&ev.raw_input)).is_some() {
        job_list_preview(&ev.event_type, &ev.raw_input, raw_preview)
    } else if ev.tool_name == "workflow" {
        let name = workflow_name_from_raw(&ev.raw_input);
        if name.is_empty() {
            raw_preview.to_string()
        } else {
            name
        }
    } else if ev.event_type.starts_with("subagent_") {
        subagent_list_preview(&ev.event_type, &ev.raw_input, raw_preview)
    } else if tool_row {
        list_event_detail(raw_preview, &ev.tool_name)
    } else {
        raw_preview.to_string()
    };
    let preview = turn_chrome_face(&ev.event_type, &preview);
    let preview = capped_display(&plain_card_text(&preview), 160);
    if preview.is_empty() {
        if matches!(
            ev.event_type.as_str(),
            "turn_started" | "turn_ended" | "turn_completed"
        ) {
            return String::new();
        }
        String::from("—")
    } else {
        preview
    }
}

fn event_body<'a>(
    hud: &'a Hud,
    ev: &'a TimelineEvent,
    mark: Option<CardMark>,
) -> Element<'a, Message> {
    let tok = hud.tokens();
    let mut col = column![].spacing(6);
    if ev.event_type.starts_with("subagent_") || !ev.child_session_id.is_empty() {
        let typ = ev
            .raw_input
            .get("subagentType")
            .and_then(|v| v.as_str())
            .or_else(|| ev.raw_input.get("subagent_type").and_then(|v| v.as_str()))
            .unwrap_or("")
            .trim()
            .to_string();
        let preview = subagent_list_preview(&ev.event_type, &ev.raw_input, &ev.content);
        let mut chips = row![].spacing(8).align_y(Alignment::Center);
        if !typ.is_empty() {
            chips = chips.push(status_chip(typ.clone(), "", tok));
        }
        if !ev.subagent_status.is_empty() {
            chips = chips.push(status_chip(
                list_status_label(&ev.subagent_status, &ev.subagent_status),
                status_tone(&ev.subagent_status),
                tok,
            ));
        }
        if let Some(ms) = ev.duration_ms {
            chips = chips.push(status_chip(fmt_duration(ms as f64 / 1000.0), "", tok));
        }
        col = col.push(chips);
        let happened = {
            let mut bits = Vec::new();
            if !typ.is_empty() {
                bits.push(typ.as_str());
            }
            if !ev.subagent_status.is_empty() {
                bits.push(ev.subagent_status.as_str());
            }
            bits.join("  ·  ")
        };
        let failed = if matches!(
            ev.subagent_status.as_str(),
            "failed" | "error" | "cancelled"
        ) {
            ev.subagent_status.as_str()
        } else {
            ""
        };
        for block in subagent_inspect_blocks(&preview, &happened, failed) {
            col = col.push(icedtea::widget::meta(
                block.label,
                tok,
                A11y::new(block.label, Role::Header),
            ));
            col = col.push(select_bound(
                hud,
                format!("subagent.{}.{}", ev.index, block.label.to_ascii_lowercase()),
                &block.body,
                tok,
                icedtea::typo::FontFace::Ui,
            ));
        }
    }
    if let Some(hit) = timeline_query_hit(ev, hud.timeline_query()) {
        col = col.push(icedtea::widget::meta(
            format!("matched in {}: {}", hit.field, hit.snippet),
            tok,
            A11y::new("search hit", Role::Status),
        ));
    }
    if ev.child_session_id.is_empty() && !ev.event_type.starts_with("subagent_") {
        col = col.push(event_payload(ev, true, hud));
    }
    if ev.content_truncated {
        col = col.push(icedtea::widget::info_bar(
            ToastKind::Warning,
            "Content truncated by control",
            tok,
            A11y::new("Content truncated by control", Role::Status),
        ));
    }
    col.push(card_chips(hud, mark, None, None)).into()
}

fn note_when(n: &NoteRow) -> String {
    if n.updated_at.is_empty() {
        format_note_time(&n.created_at)
    } else {
        format_note_time(&n.updated_at)
    }
}

fn note_body<'a>(
    hud: &'a Hud,
    n: &'a NoteRow,
    fields: &[(String, String)],
) -> Element<'a, Message> {
    let tea = hud.tokens();
    let gap = tea.density.gap();
    let turn = n.turn_index.map(|i| i.to_string()).unwrap_or_default();
    let place = if turn.is_empty() || turn == "null" {
        "Session".to_string()
    } else {
        format!("Turn {turn}")
    };
    let heading = text(place)
        .size(tea.body())
        .font(typo::UI_BOLD)
        .color(tea.text);
    let mut title = row![heading].spacing(gap).align_y(Alignment::Center);
    let src = n.source.trim();
    if !src.is_empty() {
        title = title.push(label_badge(src.to_string(), BrandRole::Cream, tea));
    }
    let mut card = column![title].spacing(gap).width(Length::Fill);
    let when = note_when(n);
    if !when.is_empty() {
        card = card.push(icedtea::widget::meta(
            when.clone(),
            tea,
            A11y::new(when, Role::Status),
        ));
    }
    if fields.is_empty() {
        card = card.push(icedtea::widget::meta(
            "Empty note",
            tea,
            A11y::new("Empty note", Role::Status),
        ));
    }
    for (i, (label, value)) in fields.iter().enumerate() {
        let fid = format!("note.{}.{i}", n.id);
        let body = if let Some((lang, code)) = fenced_code_block(value) {
            code_inset(hud, &fid, code, syntax_for_fence(lang), true, tea)
        } else {
            markdown_bound(hud, fid, value, tea)
        };
        card = card.push(
            column![
                icedtea::widget::meta(label.clone(), tea, A11y::new(label.clone(), Role::Status),),
                body,
            ]
            .spacing(4)
            .width(Length::Fill),
        );
    }
    card.push(
        row![
            Space::new().width(Length::Fill),
            note_edit_links(&n.id, hud.note_delete_armed(), tea),
        ]
        .spacing(gap)
        .align_y(Alignment::Center)
        .width(Length::Fill),
    )
    .into()
}

fn note_quiet_btn(
    title: &str,
    msg: Message,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let compact = tea.with_density(icedtea::density::Density::named(
        icedtea::density::DensityName::Compact,
    ));
    icedtea::widget::button(
        title.to_string(),
        Some(msg),
        compact,
        Variant::Quiet,
        icedtea::icon::Icons::NONE,
        icedtea::widget::ButtonOpts::SHRINK,
        A11y::button(title),
    )
}

fn note_edit_links(
    id: &str,
    delete_armed: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let del = if delete_armed == id {
        "Delete?"
    } else {
        "Delete"
    };
    let compact = tea.with_density(icedtea::density::Density::named(
        icedtea::density::DensityName::Compact,
    ));
    row![
        note_quiet_btn("Edit", Message::OpenNote(id.to_string()), tea),
        note_quiet_btn(del, Message::RequestDelete(id.to_string()), tea),
    ]
    .spacing(compact.density.gap())
    .align_y(Alignment::Center)
    .into()
}

/// Strip light markdown so closed cards do not show raw ``**bold**`` markers.
fn plain_card_text(summary: &str) -> String {
    let mut out = String::with_capacity(summary.len());
    let mut chars = summary.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '*' | '_' | '`' => {
                // Drop run of the same marker (**, __, ``` fence ticks).
                while chars.peek() == Some(&c) {
                    chars.next();
                }
            }
            _ => out.push(c),
        }
    }
    out.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Closed Turns tile: prompt title plus status badges (same face as Recent).
fn turn_list_card(
    t: &TurnRow,
    selected: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let title = turn_title(t);
    let status = if t.open {
        "open".to_string()
    } else {
        list_status_label(&t.outcome, &t.outcome)
    };
    let tone = if t.open {
        "running"
    } else {
        status_tone(&status)
    };
    let mut chips = row![].spacing(8).align_y(Alignment::Center);
    if status != "—" && status != "idle" && !status.is_empty() {
        chips = chips.push(status_chip(status, tone, tea));
    }
    let mut bits: Vec<String> = Vec::new();
    let caption = remap_turn_outcome_paren(&t.face_caption());
    if title != caption {
        bits.push(caption);
    }
    if let Some(taken) = t.duration_seconds.filter(|s| *s > 0.0).map(fmt_duration) {
        bits.push(taken);
    }
    if t.event_count > 0 {
        bits.push(format!("{} events", t.event_count));
    }
    if t.tool_call_count > 0 {
        bits.push(format!("{} tools", t.tool_call_count));
    }
    if t.tool_error_count > 0 {
        bits.push(format!("{} tool errors", t.tool_error_count));
    }
    if !bits.is_empty() {
        chips = chips.push(muted_meta(bits.join("  ·  "), tea));
    }
    closed_list_card(
        title,
        chips.into(),
        Message::SelectTurn(t.turn_index),
        selected,
        tea,
    )
}

fn turns_filter(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    let search = inset_search(
        hud.turns_query_draft(),
        Message::TurnsQuery,
        Some(Message::TurnsQuery(String::new())),
        None,
        tea,
        A11y::new("Search turns", Role::TextBox),
        Some(hud.turns_search_id()),
        &catalog_query_runs(hud.turns_query_draft()),
    );
    let hint = query_hint_line(hud.turns_query_hints(), tea);
    column![search, hint]
        .spacing(tea.density.gap() / 2.0)
        .width(Length::Fill)
        .padding(Padding::from([tea.density.gap(), tea.density.inset()]))
        .into()
}

fn turns_tab(hud: &Hud) -> Element<'_, Message> {
    let turns: &[TurnRow] = hud.displayed_turns();
    let tea = hud.body_tokens();
    let source_empty = hud
        .overview()
        .map(|o| o.turns.turns.is_empty())
        .unwrap_or(true);
    if source_empty {
        return kit::status_empty("No turns", "Nothing segmented yet.", tea);
    }
    if turns.is_empty() {
        return kit::status_empty("No matches", "No turns match this search.", tea);
    }
    let idxs = hud.filtered_turn_indices();
    let list = if idxs.is_empty() {
        kit::status_empty("No matches", "No turns match this search.", tea)
    } else {
        let heights = hud.turn_heights();
        icedtea::widget::virtual_column(
            heights,
            hud.turn_window(),
            TURNS_OVERSCAN,
            idxs.iter().position(|&src| {
                turns
                    .get(src)
                    .is_some_and(|t| hud.turns_focus() == Some(t.turn_index))
            }),
            Message::TurnScroll,
            |c| Message::FocusTurnRow(c.id),
            Some(hud.turn_scroll_id()),
            tea,
            move |i| {
                let Some(&src) = idxs.get(i) else {
                    return Space::new().height(0).into();
                };
                let Some(t) = turns.get(src) else {
                    return Space::new().height(0).into();
                };
                let selected = hud.turns_focus() == Some(t.turn_index);
                turn_list_card(t, selected, tea)
            },
            A11y::new("Turns", Role::List),
        )
    };
    list
}

fn timeline_event_list(hud: &Hud) -> Element<'_, Message> {
    if hud.timeline_query().trim().is_empty()
        && hud.last_timeline().is_none()
        && hud.filtered_indices().is_empty()
        && !hud.timeline_loading()
    {
        // Should be rare: SetTab/All turns loads immediately. Honest fallback.
        return busy_pane();
    }
    if hud.timeline_loading() && hud.filtered_indices().is_empty() {
        return busy_pane();
    }
    let idxs = hud.filtered_indices();
    if idxs.is_empty() {
        if hud.timeline_loading() || !hud.timeline_complete() {
            return busy_pane();
        }
        return kit::status_empty("No events", "Nothing matches this filter.", hud.tokens());
    }
    let tea = hud.tokens();
    let source = hud.timeline_events();
    let list = icedtea::widget::virtual_column(
        hud.timeline_heights(),
        hud.timeline_window(),
        TIMELINE_OVERSCAN,
        idxs.iter().position(|&src_i| {
            source
                .get(src_i)
                .is_some_and(|ev| hud.timeline_focus() == Some(ev.index))
        }),
        Message::TimelineScroll,
        |c| Message::FocusTimelineRow(c.id),
        Some(hud.timeline_scroll_id()),
        tea,
        move |i| {
            let Some(&src_i) = idxs.get(i) else {
                return Space::new().height(0).into();
            };
            let Some(ev) = source.get(src_i) else {
                return Space::new().height(0).into();
            };
            let ix = ev.index;
            let selected = hud.timeline_focus() == Some(ix);
            closed_list_card(
                event_list_title(ev),
                event_list_heading(ev, tea),
                Message::SelectTimeline(ix),
                selected,
                tea,
            )
        },
        A11y::new("Timeline", Role::List),
    );
    let more = crate::live::timeline_more_caption(
        hud.timeline_complete(),
        hud.timeline_at_live_end(),
        hud.timeline_loading(),
    );
    let caption: Element<'_, Message> = match more {
        Some(line) => text(line).size(tea.meta()).color(hud.tokens().muted).into(),
        None => Space::new().height(0).into(),
    };
    column![list, caption]
        .spacing(8)
        .height(Length::Fill)
        .into()
}

fn timeline_tab(hud: &Hud) -> Element<'_, Message> {
    let cover = if let Some(ix) = hud.timeline_open() {
        Some(event_detail_cover(hud, ix))
    } else if hud.workflow_inspect_id().is_some() {
        Some(workflow_row_inspect_pane(hud))
    } else {
        None
    };
    cover_stack(timeline_event_list(hud), cover, hud.body_tokens())
}

fn event_detail_cover(hud: &Hud, ix: i64) -> Element<'_, Message> {
    let incoming = event_detail_pane(hud, ix);
    let Some(prev) = hud.detail_leaving() else {
        return incoming;
    };
    if prev == ix || !matches!(hud.page_role(), MotionRole::Step) || !hud.page_moving() {
        return incoming;
    }
    let Some(face) = crate::motion::event_switch_face(hud.page_slide()) else {
        return incoming;
    };
    icedtea::motion::switch(
        event_detail_pane(hud, prev),
        incoming,
        hud.page_progress(),
        face,
        hud.tokens(),
        A11y::new("event step", Role::Group),
    )
}

/// Full-area event body (double-click / Enter a list row; Esc returns to the list).
///
/// Chrome (title + adjacent cards) stays **above** the scroll pane.
pub(crate) fn event_detail_pane(hud: &Hud, ix: i64) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let Some(ev) = hud.timeline_events().iter().find(|e| e.index == ix) else {
        return column![event_detail_chrome(hud, ix, None, tea), busy_pane(),]
            .spacing(10)
            .height(Length::Fill)
            .into();
    };
    let (_, ev_marks) = hud.card_marks();
    let mark = ev_marks.get(&ix).cloned();
    let children = hud.open_workflow_children();
    if hud.event_raw() {
        let json = event_raw_json(ev);
        let raw_id = format!("event.{ix}.raw");
        let scroll = icedtea::widget::scroll(
            container(code_inset(hud, &raw_id, &json, "json", true, tea))
                .width(Length::Fill)
                .padding(Padding {
                    top: 0.0,
                    right: icedtea::chrome::SCROLL_RAIL_WIDTH,
                    bottom: 8.0,
                    left: 0.0,
                })
                .into(),
            tea,
            A11y::new(format!("Event {ix} raw"), Role::Group),
            false,
            None,
            None::<fn(f32) -> Message>,
        );
        return column![event_detail_chrome(hud, ix, Some(ev), tea), scroll]
            .spacing(10)
            .height(Length::Fill)
            .into();
    }
    if ev.tool_name == "workflow" && !children.is_empty() {
        let inspect = icedtea::widget::scroll(
            container(event_body(hud, ev, mark))
                .width(Length::Fill)
                .padding(Padding {
                    top: 0.0,
                    right: icedtea::chrome::SCROLL_RAIL_WIDTH,
                    bottom: 8.0,
                    left: 0.0,
                })
                .into(),
            tea,
            A11y::new(format!("Event {ix}"), Role::Group),
            false,
            None,
            None::<fn(f32) -> Message>,
        );
        return column![
            event_detail_chrome(hud, ix, Some(ev), tea),
            container(inspect)
                .width(Length::Fill)
                .height(Length::Fixed(WORKFLOW_INSPECT_H)),
            container(workflow_child_list(hud, children))
                .width(Length::Fill)
                .height(Length::Fill),
        ]
        .spacing(10)
        .height(Length::Fill)
        .into();
    }
    let scroll = icedtea::widget::scroll(
        container(event_body(hud, ev, mark))
            .width(Length::Fill)
            .padding(Padding {
                top: 0.0,
                right: icedtea::chrome::SCROLL_RAIL_WIDTH,
                bottom: 8.0,
                left: 0.0,
            })
            .into(),
        tea,
        A11y::new(format!("Event {ix}"), Role::Group),
        false,
        None,
        None::<fn(f32) -> Message>,
    );
    column![event_detail_chrome(hud, ix, Some(ev), tea), scroll]
        .spacing(10)
        .height(Length::Fill)
        .into()
}

fn event_detail_chrome(
    hud: &Hud,
    ix: i64,
    ev: Option<&TimelineEvent>,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let head = ev
        .map(|e| event_list_heading(e, tea))
        .unwrap_or_else(|| muted_meta(format!("#{ix}"), tea));
    let note = ev.filter(|e| e.tool_name != "workflow").map(event_note);
    row![
        event_step(hud, -1, Icon::Back, "Previous event", tea),
        container(head).width(Length::Fill),
        event_raw_toggle(hud.event_raw(), tea),
        card_cmds_row(hud, note, None),
        event_step(hud, 1, Icon::Chevron, "Next event", tea),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .width(Length::Fill)
    .into()
}

fn event_raw_toggle(on: bool, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let knob = iced::widget::toggler(on)
        .style(icedtea::style::switch_style(tea))
        .on_toggle(Message::ToggleEventRaw);
    icedtea::a11y::attach(
        row![
            icedtea::widget::meta("Raw", tea, A11y::new("Raw", Role::Header)),
            knob,
        ]
        .spacing(tea.density.gap())
        .align_y(Alignment::Center)
        .into(),
        &A11y::new("Raw", Role::Switch).with_checked(on),
    )
}

fn event_step(
    hud: &Hud,
    delta: i32,
    icon: Icon,
    label: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let (at, n) = hud.timeline_detail_pos().unwrap_or((0, 0));
    let enabled = n > 0 && ((delta < 0 && at > 1) || (delta > 0 && at < n));
    icedtea::widget::icon_button(
        icon,
        enabled.then_some(Message::TimelineDetailStep(delta)),
        tea,
        Variant::Ghost,
        icedtea::widget::ControlSize::Default,
        A11y::button(label).with_disabled(!enabled),
    )
}

fn diff_tab(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    column![
        diff_chrome(hud, tea),
        diff_search(hud),
        diff_split(hud, tea),
    ]
    .spacing(6)
    .height(Length::Fill)
    .into()
}

fn diff_split(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let files = hud.visible_diff_files();
    // tree_view already scrolls; do not nest another scroll.
    let files_body: Element<'_, Message> = if files.is_empty() {
        kit::status_empty(
            "No file changes",
            "Rewind snapshots or file edits for this session.",
            tea,
        )
    } else {
        let paths: Vec<&str> = files.iter().map(|f| f.path.as_str()).collect();
        let root = crate::diff_tree::file_tree(paths, hud.diff_tree_collapsed());
        let selected = if hud.diff_file().is_empty() {
            None
        } else {
            Some(crate::diff_tree::path_id(hud.diff_file()))
        };
        icedtea::widget::tree_view(
            &root,
            selected,
            None,
            Message::DiffTreeToggle,
            |click| Message::DiffTreeSelect(click.id),
            icedtea::widget::TreeFace::Files,
            tea,
            A11y::new("Diff files", Role::Tree),
        )
    };
    let unified = hud
        .current_diff_point()
        .and_then(|p| p.files.iter().find(|f| f.path == hud.diff_file()))
        .map(|f| f.unified.as_str())
        .unwrap_or("");
    let files_pane = container(files_body)
        .width(Length::Fixed(248.0))
        .height(Length::Fill)
        .padding(tea.density.gap())
        .style(move |_| icedtea::style::card(tea, false));
    let hunk_pane = container(icedtea::widget::scroll(
        paint_unified(hud, unified, tea),
        tea,
        A11y::new("Diff hunk", Role::Group),
        false,
        Some(hud.diff_hunk_scroll_id()),
        None::<fn(_) -> Message>,
    ))
    .padding(tea.density.inset())
    .width(Length::Fill)
    .height(Length::Fill)
    .style(move |_| icedtea::style::card(tea, false));
    row![files_pane, hunk_pane]
        .spacing(tea.density.gap())
        .height(Length::Fill)
        .into()
}

fn diff_chrome(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let mut header = row![].spacing(tea.density.gap()).align_y(Alignment::Center);
    if !hud.diff_point_options().is_empty() {
        header = header.push(icedtea::widget::meta(
            "Turn",
            tea,
            A11y::new("Turn", Role::Header),
        ));
        header = header.push(icedtea::widget::pick_list(
            hud.diff_point_options(),
            hud.diff_point_selected(),
            Message::DiffPointPicked,
            tea,
            icedtea::widget::ControlSize::Default,
            A11y::new("Turn", Role::ComboBox),
        ));
    }
    header = header.push(diff_context_tabs(hud, tea));
    container(
        column![
            header,
            icedtea::widget::scroll(
                diff_context_body(hud, tea),
                tea,
                A11y::new("Diff context body", Role::Group),
                false,
                None,
                None::<fn(_) -> Message>,
            )
        ]
        .spacing(tea.density.gap())
        .height(Length::Fill),
    )
    .padding(Padding::from([tea.density.gap(), tea.density.inset()]))
    .height(Length::Fixed(112.0))
    .width(Length::Fill)
    .style(move |_| icedtea::style::card(tea, false))
    .into()
}

fn diff_context_tabs(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let active = match hud.diff_context() {
        DiffContext::Prompt => 0,
        DiffContext::Assistant => 1,
    };
    let mut bar = icedtea::collection::Tabs::new(["Prompt", "Assistant"]);
    bar.select(active);
    bar.closable = false;
    icedtea::widget::tab_bar(
        &bar,
        |i| {
            Message::DiffContext(if i == 0 {
                DiffContext::Prompt
            } else {
                DiffContext::Assistant
            })
        },
        |_| Message::Noop,
        0.0,
        true,
        tea,
        A11y::new("Diff context", Role::Tab),
    )
}

fn diff_search(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    let search = inset_search(
        hud.diff_query(),
        Message::DiffQuery,
        Some(Message::DiffQuery(String::new())),
        Some(Message::DiffHitStep(1)),
        tea,
        A11y::new("Search files and hunks", Role::TextBox),
        Some(hud.diff_search_id()),
        &[],
    );
    let q = hud.diff_query().trim();
    let count = if q.is_empty() {
        String::new()
    } else if let Some(at) = hud.diff_hit_index() {
        format!("{at} of {}", hud.diff_hit_count())
    } else {
        "No matches".into()
    };
    let has_hits = hud.diff_hit_count() > 0;
    row![
        search,
        text(count).size(tea.meta()).color(tea.muted),
        icedtea::widget::icon_button(
            Icon::Back,
            has_hits.then_some(Message::DiffHitStep(-1)),
            tea,
            Variant::Ghost,
            icedtea::widget::ControlSize::Default,
            A11y::button("Previous match").with_disabled(!has_hits),
        ),
        icedtea::widget::icon_button(
            Icon::Chevron,
            has_hits.then_some(Message::DiffHitStep(1)),
            tea,
            Variant::Ghost,
            icedtea::widget::ControlSize::Default,
            A11y::button("Next match").with_disabled(!has_hits),
        ),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .into()
}

fn diff_context_body(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    match hud.diff_context() {
        DiffContext::Prompt => {
            let src = hud
                .current_diff_point()
                .map(|p| p.prompt.as_str())
                .unwrap_or("");
            if src.trim().is_empty() {
                text("(empty)").size(tea.meta()).color(tea.muted).into()
            } else {
                markdown_bound(hud, "diff.prompt".into(), src, tea)
            }
        }
        DiffContext::Assistant => {
            let src = hud
                .current_diff_point()
                .map(|p| p.assistant.as_str())
                .unwrap_or("");
            if src.trim().is_empty() {
                text("(empty)").size(tea.meta()).color(tea.muted).into()
            } else {
                markdown_bound(hud, "diff.assistant".into(), src, tea)
            }
        }
    }
}

fn paint_unified<'a>(
    hud: &'a Hud,
    unified: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    if unified.trim().is_empty() {
        return text("(empty)").size(tea.meta()).color(tea.muted).into();
    }
    code_inset(hud, "diff.hunk", unified, "diff", false, tea)
}

fn note_one_choice<'a>(
    id: String,
    label: String,
    mut choices: Vec<String>,
    val: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    if !val.is_empty() && !choices.iter().any(|c| c == val) {
        choices.insert(0, val.to_string());
    }
    let selected = if val.is_empty() {
        None
    } else {
        Some(val.to_string())
    };
    icedtea::widget::pick_list(
        choices,
        selected,
        move |v| Message::NoteField {
            id: id.clone(),
            value: v,
        },
        tea,
        icedtea::widget::ControlSize::Default,
        A11y::new(label, Role::ComboBox),
    )
}

fn note_many_choices<'a>(
    id: String,
    choices: Vec<String>,
    val: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    let selected = decode_many_choices(val);
    let mut labels = choices.clone();
    for extra in &selected {
        if !labels.iter().any(|c| c == extra) {
            labels.push(extra.clone());
        }
    }
    let mut chips = row![].spacing(8).align_y(Alignment::Center);
    for choice in labels {
        let on = selected.iter().any(|s| s == &choice);
        let next = toggle_many_choice(val, &choice, &choices);
        chips = chips.push(icedtea::widget::chip(
            choice.clone(),
            Some(Message::NoteField {
                id: id.clone(),
                value: next,
            }),
            None,
            tea,
            if on { Variant::Primary } else { Variant::Quiet },
            icedtea::widget::ChipKind::Filter,
            icedtea::icon::Icons::NONE,
            A11y::button(choice).with_checked(on),
        ));
    }
    chips.into()
}

fn note_schema_field<'a>(hud: &'a Hud, spec: SchemaField) -> Element<'a, Message> {
    let tea = hud.tokens();
    let id = spec.id.clone();
    let label = spec.label.clone();
    let val = hud.note_draft().field(&id);
    let heading = icedtea::widget::meta(label.clone(), tea, A11y::new(label.clone(), Role::Status));
    let control = if spec.pick_many() {
        note_many_choices(id, spec.choices, val, tea)
    } else if spec.constrained() {
        note_one_choice(id, label, spec.choices, val, tea)
    } else if let Some(buf) = hud.note_draft().editor(&id) {
        let fid = id.clone();
        let height = Length::Fixed(note_textarea_height(buf.line_count()));
        icedtea::widget::textarea(
            buf,
            move |action| Message::NoteEdit {
                id: fid.clone(),
                action,
            },
            tea,
            height,
            A11y::new(note_field_input_key(&id), Role::TextBox),
        )
    } else {
        Space::new()
            .height(Length::Fixed(note_textarea_height(1)))
            .into()
    };
    column![heading, control].spacing(4).into()
}

fn notes_tab(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    let notes = hud.notes_sorted();
    let n_notes = notes.len();
    let notes_label = if n_notes == 1 {
        "1 note".to_string()
    } else {
        format!("{n_notes} notes")
    };
    let header = row![
        icedtea::widget::meta(notes_label, tea, A11y::new("Notes count", Role::Status)),
        note_add_btn(
            Message::StartNote {
                turn: String::new(),
                event: String::new(),
            },
            tea,
        ),
    ]
    .spacing(tea.density.gap())
    .align_y(Alignment::Center);
    let list: Element<'_, Message> = if notes.is_empty() {
        kit::status_empty("No notes", "Add a note to keep what you found.", tea)
    } else {
        let mut cards = column![];
        for (i, n) in notes.iter().enumerate() {
            let h = hud.note_heights().get(i).copied().unwrap_or(1.0).max(1.0);
            cards = cards.push(
                container(note_list_card(hud, n))
                    .width(Length::Fill)
                    .height(h)
                    .clip(true),
            );
        }
        // App keys own j/k. A focused icedtea scroll maps Left/Right
        // to 24 px vertical steps — skip that by not focusing the pane.
        let list = icedtea::widget::scroll(
            cards.into(),
            tea,
            A11y::new("Notes", Role::List).with_disabled(true),
            false,
            Some(hud.note_scroll_id()),
            Some(|y| {
                Message::NoteScroll(icedtea::collection::VisibleWindow {
                    start: 0,
                    end: 0,
                    scroll: y,
                    viewport: 0.0,
                })
            }),
        );
        container(list)
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    };
    let compose = if hud.composing_note() {
        let form = icedtea::widget::scroll(
            container(notes_compose_form(hud))
                .width(Length::Fill)
                .padding(Padding {
                    top: 0.0,
                    right: icedtea::chrome::SCROLL_RAIL_WIDTH,
                    bottom: 8.0,
                    left: 0.0,
                })
                .into(),
            tea,
            A11y::new("Note form", Role::Group),
            false,
            None,
            None::<fn(f32) -> Message>,
        );
        Some(
            container(form)
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
        )
    } else {
        None
    };
    cover_stack(
        column![header, list]
            .spacing(tea.density.space)
            .height(Length::Fill)
            .into(),
        compose,
        hud.body_tokens(),
    )
}

fn notes_compose_form(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    let specs = hud.note_form_schema();
    let editing = !hud.note_draft().id.is_empty();
    let mut form = column![].spacing(tea.density.gap());
    let src = hud.note_draft().source.trim();
    if !src.is_empty() {
        form = form.push(label_badge(src.to_string(), BrandRole::Cream, tea));
    }
    for spec in specs {
        form = form.push(note_schema_field(hud, spec));
    }
    form = form.push(icedtea::widget::meta(
        "Turn",
        tea,
        A11y::new("Turn", Role::Status),
    ));
    form = form.push(
        container(icedtea::widget::text_input(
            "",
            &hud.note_draft().turn_index,
            Message::NoteTurn,
            Some(Message::SaveNote),
            icedtea::widget::FieldOpts::NONE,
            hud.tokens(),
            A11y::new("Turn", Role::TextBox),
            Some(iced::widget::Id::new(NOTE_TURN_INPUT)),
        ))
        .width(Length::Fixed(120.0)),
    );
    if !hud.note_draft().event_index.is_empty() {
        form = form.push(icedtea::widget::meta(
            format!("Event #{}", hud.note_draft().event_index),
            tea,
            A11y::new("Event", Role::Status),
        ));
    }
    let save_label = if hud.note_saving() {
        "Saving…"
    } else if editing {
        "Save"
    } else {
        "Save note"
    };
    let nid = hud.note_draft().id.clone();
    let del = if hud.note_delete_armed() == nid {
        "Delete?"
    } else {
        "Delete"
    };
    let mut actions = row![
        note_quiet_btn(save_label, Message::SaveNote, tea),
        note_quiet_btn("Cancel", Message::ResetDraft, tea),
    ]
    .spacing(tea.density.gap())
    .align_y(Alignment::Center);
    if editing {
        actions = actions.push(note_quiet_btn(del, Message::RequestDelete(nid), tea));
    }
    icedtea::widget::group_box(
        if editing { "Edit note" } else { "Add note" },
        form.push(actions).into(),
        tea,
        icedtea::widget::CardFace::Outlined,
        A11y::new("Note form", Role::Group),
        None,
    )
}

fn note_list_card<'a>(hud: &'a Hud, n: &'a NoteRow) -> Element<'a, Message> {
    let tea = hud.tokens();
    let fields = note_display_fields(&hud.notes_schema(), &n.fields);
    let selected = hud.notes_focus() == Some(n.id.as_str());
    mouse_area(
        container(note_body(hud, n, &fields))
            .padding(tea.density.inset())
            .width(Length::Fill)
            .style(move |_| icedtea::style::card(tea, selected)),
    )
    .on_double_click(Message::OpenNote(n.id.clone()))
    .into()
}

fn paired_tool<'a>(hud: &'a Hud, ev: &'a TimelineEvent) -> (&'a TimelineEvent, &'a TimelineEvent) {
    let id = ev.tool_call_id.trim();
    if id.is_empty() {
        return (ev, ev);
    }
    let mut call = ev;
    let mut result = ev;
    for other in hud.timeline_events() {
        if other.tool_call_id != ev.tool_call_id {
            continue;
        }
        if other.kind == "tool" || other.event_type == "tool_call" {
            call = other;
        }
        if other.kind == "tool_result"
            || other.event_type == "tool_call_update"
            || other.event_type == "tool_result"
        {
            result = other;
        }
    }
    (call, result)
}

fn inspect_fields(call: &TimelineEvent) -> Vec<ToolField> {
    if !call.tool_fields.is_empty() {
        return call
            .tool_fields
            .iter()
            .map(|f| ToolField {
                id: f.id.clone(),
                label: f.label.clone(),
                value: f.value.clone(),
            })
            .collect();
    }
    tool_fields_from_raw(
        &call.tool_name,
        &call.raw_input,
        crate::format::EXTRACT_CHARS,
    )
}

fn workflow_row_inspect_pane(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let Some(id) = hud.workflow_inspect_id() else {
        return kit::status_empty("No workflow", "Nothing selected.", tea);
    };
    let Some(run) = hud
        .overview()
        .and_then(|o| o.workflows.iter().find(|r| r.id == id))
    else {
        return kit::status_empty("No workflow run on disk", "This run is gone.", tea);
    };
    let inspect = icedtea::widget::scroll(
        container(workflow_run_inspect(hud, run, "wf.inspect"))
            .width(Length::Fill)
            .into(),
        tea,
        A11y::new("Workflow", Role::Group),
        false,
        None,
        None::<fn(f32) -> Message>,
    );
    let children = hud.open_workflow_children();
    if children.is_empty() {
        return inspect;
    }
    column![
        container(inspect)
            .width(Length::Fill)
            .height(Length::Fixed(WORKFLOW_INSPECT_H)),
        container(workflow_child_list(hud, children))
            .width(Length::Fill)
            .height(Length::Fill),
    ]
    .spacing(10)
    .height(Length::Fill)
    .into()
}

fn workflow_event_inspect<'a>(hud: &'a Hud, ev: &'a TimelineEvent) -> Element<'a, Message> {
    let tok = hud.tokens();
    if let Some(run) = hud
        .overview()
        .and_then(|o| workflow_for_event(&o.workflows, &ev.raw_input))
    {
        return workflow_run_inspect(hud, run, &format!("event.{}", ev.index)).into();
    }
    let mut col = column![].spacing(10);
    let name = workflow_name_from_raw(&ev.raw_input);
    if !name.is_empty() {
        col = col.push(
            text(name)
                .size(tok.title())
                .font(typo::UI_BOLD)
                .color(tok.text),
        );
    }
    col = col.push(icedtea::widget::meta(
        "No workflow run on disk",
        tok,
        A11y::new("workflow missing", Role::Status),
    ));
    col.into()
}

fn workflow_run_inspect<'a>(
    hud: &'a Hud,
    run: &'a crate::wire::WorkflowRow,
    key: &str,
) -> iced::widget::Column<'a, Message> {
    let tok = hud.tokens();
    let mut col = column![].spacing(10);
    if !run.name.is_empty() {
        col = col.push(
            text(run.name.clone())
                .size(tok.title())
                .font(typo::UI_BOLD)
                .color(tok.text),
        );
    }
    if !run.objective.is_empty() {
        col = col.push(icedtea::widget::meta(
            "Asked",
            tok,
            A11y::new("Asked", Role::Header),
        ));
        col = col.push(select_bound(
            hud,
            format!("{key}.wf.obj"),
            &run.objective,
            tok,
            icedtea::typo::FontFace::Ui,
        ));
    }
    let mut happen_bits = vec![workflow_status_word(&run.status)];
    if !run.phase.is_empty() {
        happen_bits.push(run.phase.clone());
    }
    if let Some(ms) = run.elapsed_ms {
        if ms > 0 {
            happen_bits.push(fmt_duration(ms as f64 / 1000.0));
        }
    }
    col = col.push(icedtea::widget::meta(
        "Happened",
        tok,
        A11y::new("Happened", Role::Header),
    ));
    col = col.push(select_bound(
        hud,
        format!("{key}.wf.happened"),
        &happen_bits.join("  ·  "),
        tok,
        icedtea::typo::FontFace::Ui,
    ));
    if run.agents_used.is_some() || run.agent_budget.is_some() {
        let used = run
            .agents_used
            .map(|n| n.to_string())
            .unwrap_or_else(|| "—".into());
        let budget = run
            .agent_budget
            .map(|n| n.to_string())
            .unwrap_or_else(|| "—".into());
        col = col.push(select_bound(
            hud,
            format!("{key}.wf.agents"),
            &format!("{used}/{budget} agents"),
            tok,
            icedtea::typo::FontFace::Ui,
        ));
    }
    if !run.pause_message.is_empty() {
        col = col.push(icedtea::widget::meta(
            "Failed",
            tok,
            A11y::new("Failed", Role::Header),
        ));
        col = col.push(select_bound(
            hud,
            format!("{key}.wf.pause"),
            &run.pause_message,
            tok,
            icedtea::typo::FontFace::Ui,
        ));
    }
    col
}

fn workflow_child_list<'a>(hud: &'a Hud, children: &'a [WorkflowChildRow]) -> Element<'a, Message> {
    let tea = hud.body_tokens();
    let heights = hud.wf_child_heights();
    icedtea::widget::virtual_column(
        heights,
        hud.wf_child_window(),
        AGENT_OVERSCAN,
        None,
        Message::WorkflowChildScroll,
        |c| Message::OpenWorkflowChild(c.id),
        Some(hud.wf_child_scroll_id()),
        tea,
        move |i| {
            let Some(child) = children.get(i) else {
                return Space::new().height(0).into();
            };
            let mark = if child.success { "complete" } else { "failed" };
            let title = if child.label.is_empty() {
                child.id.clone()
            } else {
                child.label.clone()
            };
            let openable = !child.path.is_empty();
            let ink = if openable { tea.text } else { tea.muted };
            let badges = row![status_chip(
                mark,
                if child.success { "complete" } else { "error" },
                tea
            )]
            .spacing(8)
            .align_y(Alignment::Center);
            let title_el = text(title)
                .size(tea.body())
                .font(typo::UI)
                .color(ink)
                .width(Length::Fill);
            let body = column![title_el, badges].spacing(4).width(Length::Fill);
            container(body)
                .padding(tea.density.inset())
                .width(Length::Fill)
                .style(move |_| icedtea::style::card(tea, false))
                .into()
        },
        A11y::new("Agents", Role::List),
    )
}

fn job_event_inspect<'a>(hud: &'a Hud, ev: &'a TimelineEvent) -> Element<'a, Message> {
    let tok = hud.tokens();
    let mut col = column![].spacing(8);
    if ev.event_type.starts_with("scheduled_task_") {
        let human = ev
            .raw_input
            .get("human_schedule")
            .and_then(|v| v.as_str())
            .or_else(|| ev.raw_input.get("humanSchedule").and_then(|v| v.as_str()))
            .unwrap_or("")
            .trim();
        let prompt = ev
            .raw_input
            .get("prompt")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim();
        let next = ev
            .raw_input
            .get("next_fire_at")
            .and_then(|v| v.as_str())
            .or_else(|| ev.raw_input.get("nextFireAt").and_then(|v| v.as_str()))
            .unwrap_or("")
            .trim();
        let tid = job_event_id(&ev.raw_input, &ev.tool_call_id);
        let (last, child) = hud
            .overview()
            .and_then(|o| schedule_last_fire(&o.schedules, &tid))
            .unwrap_or(("", ""));
        let last = last.trim();
        let child = child.trim();
        for block in schedule_inspect_blocks(prompt, human, next, last, child) {
            col = col.push(icedtea::widget::meta(
                block.label,
                tok,
                A11y::new(block.label, Role::Header),
            ));
            let key = format!(
                "event.{}.sched.{}",
                ev.index,
                block.label.to_ascii_lowercase()
            );
            if block.label == "Asked" && looks_like_markdown(&block.body) {
                col = col.push(markdown_bound(hud, key, &block.body, tok));
            } else {
                col = col.push(select_bound(
                    hud,
                    key,
                    &block.body,
                    tok,
                    icedtea::typo::FontFace::Ui,
                ));
            }
        }
        return col.into();
    }
    let desc = job_description(&ev.raw_input);
    let cmd = job_command(&ev.raw_input, &ev.content);
    let mut path = job_output_path(&ev.raw_input);
    let tid = job_event_id(&ev.raw_input, &ev.tool_call_id);
    let want = match ev.event_type.as_str() {
        "task_backgrounded" => "task_completed",
        "task_completed" => "task_backgrounded",
        _ => "",
    };
    let mate = if tid.is_empty() || want.is_empty() {
        None
    } else {
        hud.timeline_events().iter().find(|other| {
            other.index != ev.index
                && other.event_type == want
                && job_event_id(&other.raw_input, &other.tool_call_id) == tid
        })
    };
    if path.is_empty() {
        if let Some(m) = mate {
            path = job_output_path(&m.raw_input);
        }
    }
    let tail = job_inspect_log(&hud.session_path(), &path);
    let status_raw = if ev.event_type == "task_completed" {
        &ev.raw_input
    } else {
        mate.map(|m| &m.raw_input).unwrap_or(&ev.raw_input)
    };
    let status = job_status(status_raw, &ev.content, &tail);
    let asked = if !desc.is_empty() {
        desc.clone()
    } else {
        cmd.clone()
    };
    let kind = if event_is_monitor(&ev.raw_input) {
        "monitor"
    } else {
        "background"
    };
    let mut happen_bits = vec![
        kind.to_string(),
        list_status_label(status, status).to_string(),
    ];
    if let Some(code) = job_exit_code(&ev.event_type, &ev.raw_input, mate.map(|m| &m.raw_input)) {
        happen_bits.push(format!("exit {code}"));
    }
    let ts = |v: &serde_json::Value| v.as_i64().or_else(|| v.as_u64().map(|n| n as i64));
    let start_ts = if ev.event_type == "task_backgrounded" {
        ts(&ev.timestamp)
    } else {
        mate.and_then(|m| ts(&m.timestamp))
    };
    let end_ts = if ev.event_type == "task_completed" {
        ts(&ev.timestamp)
    } else {
        mate.and_then(|m| ts(&m.timestamp))
    };
    if let (Some(start), Some(end)) = (start_ts, end_ts) {
        if end >= start {
            happen_bits.push(fmt_duration((end - start) as f64));
        }
    }
    let happened = happen_bits.join("  ·  ");
    let failed = if matches!(status, "failed" | "error" | "cancelled" | "interrupted") {
        tail.lines().last().unwrap_or("").trim().to_string()
    } else {
        String::new()
    };
    for block in job_inspect_blocks(&asked, &happened, &failed) {
        col = col.push(icedtea::widget::meta(
            block.label,
            tok,
            A11y::new(block.label, Role::Header),
        ));
        if block.label == "Asked" && !cmd.is_empty() {
            if !desc.is_empty() {
                col = col.push(select_bound(
                    hud,
                    format!("event.{}.desc", ev.index),
                    &desc,
                    tok,
                    icedtea::typo::FontFace::Ui,
                ));
            }
            col = col.push(code_inset(
                hud,
                &format!("event.{}.cmd", ev.index),
                &cmd,
                "bash",
                true,
                tok,
            ));
        } else {
            col = col.push(select_bound(
                hud,
                format!("event.{}.{}", ev.index, block.label.to_ascii_lowercase()),
                &block.body,
                tok,
                icedtea::typo::FontFace::Ui,
            ));
        }
    }
    let cwd = ev
        .raw_input
        .get("cwd")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if !cwd.is_empty() {
        col = col.push(text(cwd.to_string()).size(tok.meta()).color(tok.muted));
    }
    if !tail.trim().is_empty() {
        col = col.push(icedtea::widget::meta(
            "Log",
            tok,
            A11y::new("Log", Role::Header),
        ));
        col = col.push(code_inset(
            hud,
            &format!("event.{}.log", ev.index),
            &tail,
            "txt",
            true,
            tok,
        ));
    }
    if desc.is_empty() && cmd.is_empty() && tail.trim().is_empty() {
        col = col.push(text("—").size(tok.body()).color(tok.muted));
    }
    col.into()
}

fn event_payload<'a>(ev: &'a TimelineEvent, selected: bool, hud: &'a Hud) -> Element<'a, Message> {
    let kind = ev.kind.clone();
    let event_type = ev.event_type.clone();
    let tool = ev.tool_name.clone();
    let preview = ev.preview.clone();
    let content = ev.content.clone();
    let raw_body = timeline_body_text(&preview, &content, selected, 240);
    if bookend_body_is_chrome(&event_type, &raw_body) {
        return Space::new().height(0).into();
    }
    let raw_body = turn_chrome_face(&event_type, &raw_body);
    let body = display_message_text(&sanitize_console_text(&display_tool_output(
        &raw_body, &tool,
    )));
    let tok = hud.tokens();
    let field_id = ExtractKey::Event(ev.index).id();
    if !selected {
        return render_payload_text(&body, &kind, &event_type, hud, false, &field_id, "");
    }
    if ev.tool_name == "workflow" {
        return workflow_event_inspect(hud, ev);
    }
    if job_event_label(&event_type, event_is_monitor(&ev.raw_input)).is_some() {
        return job_event_inspect(hud, ev);
    }
    let mut col = column![].spacing(8);
    let call_id = ev.tool_call_id.clone();
    if !tool.is_empty() || !call_id.is_empty() {
        let mut chips = row![].spacing(8).align_y(Alignment::Center);
        if !tool.is_empty() {
            chips = chips.push(label_badge(
                format_tool_display(&tool),
                event_tool_role(ev),
                tok,
            ));
        }
        if ev.is_error {
            chips = chips.push(event_error_icon(tok));
        }
        if !call_id.is_empty() {
            chips = chips.push(icedtea::widget::meta(
                call_id,
                tok,
                A11y::new("tool call id", Role::Status),
            ));
        }
        col = col.push(chips);
    }
    if kind == "tool" || kind == "tool_result" {
        let (call, result) = paired_tool(hud, ev);
        let path_hint = {
            let mut p = path_hint_from_raw(&call.raw_input);
            if p.is_empty() {
                p = path_hint_from_raw(&result.raw_input);
            }
            if p.is_empty() {
                p = path_hint_from_raw(&ev.raw_input);
            }
            p
        };
        let fields = inspect_fields(call);
        if !fields.is_empty() {
            col = col.push(icedtea::widget::meta(
                "Input",
                tok,
                A11y::new("Input", Role::Header),
            ));
            for field in fields {
                col = col.push(icedtea::widget::meta(
                    field.label.clone(),
                    tok,
                    A11y::new(field.label.clone(), Role::Header),
                ));
                col = col.push(field_body(
                    hud,
                    &format!("event.{}.in.{}", ev.index, field.id),
                    &field.id,
                    &field.value,
                    &path_hint,
                ));
            }
        }
        let out_tool = if result.tool_name.is_empty() {
            tool.as_str()
        } else {
            result.tool_name.as_str()
        };
        let out_body = sanitize_console_text(&display_tool_output(&result.content, out_tool));
        let mut imgs = still_paths(result, &hud.session_path());
        if imgs.is_empty() {
            let from_content = image_result_path(&result.content);
            if !from_content.is_empty() {
                imgs.push(from_content);
            }
        }
        if !imgs.is_empty() {
            col = col.push(icedtea::widget::meta(
                "Output",
                tok,
                A11y::new("Output", Role::Header),
            ));
            for img in imgs {
                col = col.push(icedtea::widget::meta(
                    img.clone(),
                    hud.tokens(),
                    A11y::new(img.clone(), Role::Status),
                ));
                col = col.push(still_image(&img, hud.tokens()));
            }
        } else if !out_body.trim().is_empty() {
            let out_syn = syntax_for_tool_output(out_tool, &path_hint, &out_body);
            col = col.push(icedtea::widget::meta(
                "Output",
                tok,
                A11y::new("Output", Role::Header),
            ));
            col = col.push(render_payload_text(
                &out_body,
                &result.kind,
                &result.event_type,
                hud,
                true,
                &format!("event.{}.out", ev.index),
                out_syn,
            ));
        }
    } else {
        // Chat / thought / plan: same paint path as TUI detail (markdown for messages).
        col = col.push(render_payload_text(
            &body,
            &kind,
            &event_type,
            hud,
            true,
            &field_id,
            "",
        ));
        for img in still_paths(ev, &hud.session_path()) {
            col = col.push(still_image(&img, hud.tokens()));
        }
    }
    col.into()
}

fn field_body<'a>(
    hud: &'a Hud,
    bind_id: &str,
    field_id: &str,
    value: &str,
    path_hint: &str,
) -> Element<'a, Message> {
    let tea = hud.tokens();
    let syntax = syntax_for_tool_field(field_id, path_hint, value);
    let body = if field_id == "old_string"
        || field_id == "new_string"
        || field_id == "command"
        || field_id == "pattern"
        || crate::format::looks_like_json(value)
        || !syntax.is_empty()
    {
        let syn = if syntax.is_empty() { "txt" } else { syntax };
        code_inset(hud, bind_id, value, syn, true, tea)
    } else {
        select_bound(
            hud,
            bind_id.to_string(),
            value,
            tea,
            icedtea::typo::FontFace::Mono,
        )
    };
    container(body)
        .padding(8)
        .width(Length::Fill)
        .style(move |_| icedtea::style::card(tea, false))
        .into()
}

fn render_payload_text<'a>(
    body: &str,
    kind: &str,
    event_type: &str,
    hud: &'a Hud,
    expanded: bool,
    field_id: &str,
    syntax: &str,
) -> Element<'a, Message> {
    let tok = hud.tokens();
    let trimmed = body.trim();
    let paint = body_paint_for(kind, event_type, trimmed, expanded);
    if paint == BodyPaint::Empty {
        return text("empty").size(tok.meta()).color(tok.muted).into();
    }
    let max = if expanded {
        crate::format::EXTRACT_CHARS
    } else {
        400
    };
    let cut = capped_display(body, max);
    if !expanded {
        return text(cut)
            .size(tok.meta())
            .font(typo::UI)
            .color(tok.muted)
            .into();
    }
    match paint {
        BodyPaint::Json => code_inset(hud, field_id, &cut, "json", true, hud.tokens()),
        BodyPaint::Code => {
            let syn = if syntax.is_empty() {
                syntax_for_tool_output("", "", &cut)
            } else {
                syntax
            };
            let syn = if syn.is_empty() { "txt" } else { syn };
            code_inset(hud, field_id, &cut, syn, true, hud.tokens())
        }
        BodyPaint::Image => still_image(trimmed, hud.tokens()),
        BodyPaint::Markdown => {
            let md = markdown_bound(hud, field_id.to_string(), &cut, hud.tokens());
            if is_chat_message(kind, event_type) || kind == "subagent" {
                inset_body(md, hud)
            } else {
                md
            }
        }
        BodyPaint::Plain | BodyPaint::Empty => {
            // Prefer real highlighting when we still know a language (e.g. file path).
            if !syntax.is_empty() && (kind == "tool" || kind == "tool_result") {
                return code_inset(hud, field_id, &cut, syntax, true, hud.tokens());
            }
            let plain = if kind == "thought" {
                select_bound(
                    hud,
                    field_id.to_string(),
                    &cut,
                    tok,
                    icedtea::typo::FontFace::Ui,
                )
            } else if kind == "tool" || kind == "tool_result" {
                // Shell stdout / non-source tool bodies: monospaced like TUI.
                select_bound(
                    hud,
                    field_id.to_string(),
                    &cut,
                    tok,
                    icedtea::typo::FontFace::Mono,
                )
            } else {
                select_bound(
                    hud,
                    field_id.to_string(),
                    &cut,
                    tok,
                    icedtea::typo::FontFace::Ui,
                )
            };
            plain
        }
    }
}

fn inset_body<'a>(inner: Element<'a, Message>, hud: &'a Hud) -> Element<'a, Message> {
    let tea = hud.tokens();
    container(inner)
        .padding(tea.density.inset())
        .width(Length::Fill)
        .style(move |_| icedtea::style::card(tea, false))
        .into()
}

fn jump_control(
    msg: Message,
    _color: Color,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    // Chip, not Canvas: one 16px canvas program per closed card is still
    // more draw work than a text chip.
    icedtea::widget::tooltip_wrap(
        chip_btn("→".into(), msg, tea),
        "Go to Timeline",
        icedtea::widget::TooltipAnchor::Follow,
        tea,
        A11y::button("Go to Timeline"),
    )
}

const POP_OUT_MARK: &[u8] = br#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path fill="black" d="M3 6h7v7H3zM7 3h6v6h-2V5H7z"/></svg>"#;

fn pop_out_control(
    _tok: icedtea::theme::Tokens,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    icedtea::widget::tooltip_wrap(
        icedtea::widget::icon_button(
            icedtea::icon::Glyph::Bytes(POP_OUT_MARK),
            Some(Message::PopOutWindow),
            tea,
            Variant::Elevated,
            icedtea::widget::ControlSize::Default,
            A11y::button("Pop out"),
        ),
        "Open a desktop window",
        icedtea::widget::TooltipAnchor::Follow,
        tea,
        A11y::button("Pop out"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pop_out_uses_icedtea_icon_bytes() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        assert!(prod.contains("Glyph::Bytes(POP_OUT_MARK)"));
        assert!(!prod.contains("struct PopOutIcon"));
        let _ = pop_out_control(tea(), tea());
    }

    #[test]
    fn plain_card_text_strips_markdown_markers() {
        assert_eq!(
            plain_card_text("You are an **adversarial** verifier"),
            "You are an adversarial verifier"
        );
        assert_eq!(plain_card_text("see `code` and __x__"), "see code and x");
    }

    #[test]
    fn closed_faces_are_plain_text_not_markdown() {
        assert_eq!(plain_card_text("# heading\n\n**bold**"), "# heading bold");
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let title = prod
            .split("fn turn_title")
            .nth(1)
            .expect("turn_title")
            .split("fn event_note")
            .next()
            .expect("body");
        assert!(
            !title.contains("md_body"),
            "closed faces must not parse markdown per row"
        );
    }

    fn event_title_meta(ev: &TimelineEvent) -> String {
        let mut bits: Vec<String> = Vec::new();
        if let Some(turn) = ev.turn_index {
            bits.push(format!("turn {turn}"));
        }
        let time = ev.time.trim();
        if !time.is_empty() {
            bits.push(time.to_string());
        }
        bits.join(" · ")
    }

    #[test]
    fn event_heading_has_type_turn_and_time() {
        let ev = TimelineEvent {
            index: 12,
            event_type: "user_message_chunk".into(),
            type_label: "user message chunk".into(),
            kind: "user".into(),
            time: "10:32".into(),
            turn_index: Some(2),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title_meta(&ev), "turn 2 · 10:32");
        let no_turn = TimelineEvent {
            index: 12,
            kind: "user".into(),
            time: "10:32".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title_meta(&no_turn), "10:32");
        let bare = TimelineEvent {
            index: 3,
            kind: "user".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title_meta(&bare), "");
        assert_eq!(
            event_type_human(&ev),
            "user message chunk",
            "human type sits on the heading with #index"
        );
        assert_eq!(event_title_meta(&ev), "turn 2 · 10:32");
        let painted = event_type_paint(&ev).expect("type");
        assert_eq!(painted.0, "user message chunk");
        assert_eq!(painted.1, BrandRole::Cream);
        let _ = event_list_heading(&ev, tea());
        let tool = TimelineEvent {
            index: 4,
            event_type: "tool_call".into(),
            type_label: "tool call".into(),
            kind: "tool".into(),
            tool_name: "read_file".into(),
            preview: "src/app.rs".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_tool_role(&tool), BrandRole::Cream);
        let failed = TimelineEvent {
            is_error: true,
            ..tool.clone()
        };
        assert_eq!(event_tool_role(&failed), BrandRole::Cream);
        assert_eq!(
            event_type_paint(&failed).map(|p| p.1),
            event_type_paint(&tool).map(|p| p.1)
        );
        let _ = event_list_heading(&failed, tea());
        assert_eq!(event_list_title(&tool), "src/app.rs");
        let started = TimelineEvent {
            index: 0,
            event_type: "turn_started".into(),
            type_label: "turn started".into(),
            kind: "session".into(),
            preview: "turn_number=0".into(),
            content: "turn_number=0".into(),
            heading: "turn started".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_list_title(&started), "");
        let with_model = TimelineEvent {
            preview: "turn started  turn_number=0  model=v9".into(),
            content: "turn started  turn_number=0  model=v9".into(),
            ..started.clone()
        };
        assert_eq!(event_list_title(&with_model), "");
        let prod = include_str!("view.rs")
            .split("#[cfg(test)]")
            .next()
            .expect("prod");
        assert!(prod.contains("fn event_error_icon"));
        assert!(prod.contains("Icon::Error"));
        let body = prod
            .split("fn event_body")
            .nth(1)
            .expect("event_body")
            .split("fn note_when")
            .next()
            .expect("body");
        assert!(
            !body.contains("event_type_human"),
            "detail body must not repeat the chrome type line"
        );
        assert!(prod.contains("fn event_detail_chrome"));
    }

    #[test]
    fn turns_tab_is_fixed_cards_with_search() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let turns = prod
            .split("fn turns_tab")
            .nth(1)
            .expect("turns_tab")
            .split("fn timeline_tab")
            .next()
            .expect("turns body");
        assert!(turns.contains("turn_list_card"));
        assert!(!turns.contains("turns_filter("));
        assert!(!turns.contains("expand_card"));
        assert!(!turns.contains("fn turn_body"));
        let detail = prod
            .split("fn detail_pane")
            .nth(1)
            .expect("detail_pane")
            .split("fn browse_session_bar")
            .next()
            .expect("detail");
        assert!(detail.contains("turns_filter(hud)"));
    }

    #[test]
    fn turns_filter_uses_chrome_inset() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let filter = prod
            .split("fn turns_filter")
            .nth(1)
            .expect("turns_filter")
            .split("fn turns_tab")
            .next()
            .expect("turns_filter body");
        assert!(filter.contains("tea.density.inset()"));
        assert!(filter.contains("tea.density.gap()"));
    }

    #[test]
    fn chip_btn_builds_unsized_chip_buttons() {
        let hud = Hud::default();
        let _ = chip_btn("Add note".into(), Message::ResetDraft, tea());
        let _ = chip_btn("f2".into(), Message::JumpTimeline(3), tea());
        let _ = card_chips(
            &hud,
            Some(CardMark {
                notes: 1,
                errors: 0,
                first_note_id: "n1".into(),
            }),
            Some(Message::ResetDraft),
            None,
        );
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod source");
        let chip = prod
            .split("fn chip_btn(")
            .nth(1)
            .expect("chip_btn")
            .split("fn command_end")
            .next()
            .expect("chip_btn body");
        assert!(chip.contains("widget::chip"));
        assert!(prod.contains("Glyph::Bytes"));
        assert!(chip.contains("Some(msg)"));
        assert!(chip.contains("Variant::Chip"));
        assert!(!chip.contains("widget::button"));
        assert!(!chip.contains("Fixed(22"));
        assert!(!chip.contains("mouse_area"));
        let marks = prod
            .split("fn card_marks_row")
            .nth(1)
            .expect("card_marks_row")
            .split("fn card_cmds_row")
            .next()
            .expect("card_marks body");
        assert!(
            !marks.contains("f{}"),
            "session cards do not paint findings chips"
        );
        assert!(!marks.contains("m.findings"));
    }

    fn tea() -> icedtea::theme::Tokens {
        icedtea::theme::named("dark").tokens
    }

    #[test]
    fn empty_loading_and_select_use_icedtea_status() {
        let _ = empty_sessions(tea());
        let _ = busy_pane();
        let _ = select_session(tea());
        let _ = status_copy("control socket down · run: anqad -d", true, tea());
        let _ = status_copy("12 sessions · ready", false, tea());
        let prod = include_str!("view.rs")
            .split("#[cfg(test)]")
            .next()
            .expect("prod");
        assert!(prod.contains("fn busy_pane"));
        assert!(prod.contains("page_busy()"));
        assert!(prod.contains("busy_overlay"));
        assert!(!prod.contains("fn loading_session"));
        assert!(!prod.contains("Loading events…"));
        assert!(!prod.contains("Loading matching events…"));
        assert!(!prod.contains("Loading event…"));
        assert!(!prod.contains("\"Loading…\""));
    }

    #[test]
    fn timeline_filter_and_empty_list_build_from_hud() {
        let hud = Hud::default();
        assert!(hud.sessions().is_empty());
        let _ = timeline_filter(&hud);
        let _ = session_picker_at(&hud, 400.0);
        let _ = layout(&hud);
        let src = include_str!("view.rs");
        assert!(
            src.contains("Search events…"),
            "timeline filter keeps search"
        );
        // Search is a second row so pick lists cannot crush the field.
        let filter_src = src
            .split("fn timeline_filter")
            .nth(1)
            .unwrap_or("")
            .split("fn overview_tab")
            .next()
            .unwrap_or("");
        assert!(
            filter_src.contains("column![picks, search, hint]"),
            "search must not share the picks row"
        );
        assert!(
            filter_src.contains("timeline_count_caption"),
            "empty range must not paint a11y name"
        );
        assert!(
            filter_src.contains("timeline_tail_toggle"),
            "live Tail sits on the Timeline filter bar"
        );
        assert!(
            !filter_src.contains("widget::switch"),
            "form-row switch fills the picks row"
        );
        assert!(src.contains("kit::pane_tabs"), "session-gated tabs");
    }

    #[test]
    fn code_inset_pretty_prints_json_through_icedtea() {
        let mut hud = Hud::default();
        hud.bind_field("code.json", r#"{ "a": 1 }"#);
        hud.bind_field("code.plain", "not json");
        let _ = code_inset(&hud, "code.json", "", "json", true, tea());
        let _ = code_inset(&hud, "code.plain", "", "py", true, tea());
        let _ = code_inset(&hud, "missing", "fallback body", "txt", true, tea());
    }

    #[test]
    fn still_image_uses_slot_for_missing_and_present_files() {
        let missing = still_image("/no/such/anqa-hud-image.png", tea());
        let _ = missing;
        let path = std::env::temp_dir().join("anqa-hud-tool-image.txt");
        std::fs::write(&path, b"px").expect("temp image stand-in");
        let _ = still_image(path.to_str().expect("utf8 path"), tea());
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn still_paths_prefers_image_paths_then_image_path() {
        let ev = TimelineEvent {
            image_path: "/tmp/a.png".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(still_paths(&ev, ""), vec!["/tmp/a.png".to_string()]);
        let ev = TimelineEvent {
            image_path: ev.image_path,
            image_paths: vec!["/tmp/b.png".into(), "/tmp/c.png".into()],
            ..TimelineEvent::default()
        };
        assert_eq!(
            still_paths(&ev, ""),
            vec!["/tmp/b.png".to_string(), "/tmp/c.png".to_string()]
        );
    }

    #[test]
    fn session_status_tones_stay_distinct_and_readable() {
        assert_eq!(tone_variant("complete"), Variant::Success);
        assert_eq!(tone_variant("running"), Variant::Warning);
        assert_eq!(tone_variant("awaiting"), Variant::Quiet);
        assert_eq!(tone_variant("ending"), Variant::Quiet);
        assert_eq!(tone_variant("cancelled"), Variant::Danger);
        let _ = status_chip("complete", "complete", tea());
        let _ = status_chip("running", "running", tea());
    }

    #[test]
    fn virtual_column_sub_row_wheel_does_not_publish_scroll() {
        use iced::advanced::clipboard;
        use iced::advanced::layout::{Layout, Limits};
        use iced::advanced::widget::Tree;
        use iced::mouse;
        use iced::widget::Id;
        use iced::{Event, Font, Pixels, Point, Rectangle, Size};
        use icedtea::collection::VisibleWindow;
        use icedtea::widget::{label, virtual_column};

        let tok = tea();
        // Tall fixture rows so a 4px wheel cannot change the visible
        // range. Product tiles are shorter; this test is the clip.
        let row_h = 144.0;
        let viewport = 400.0;
        let heights: Vec<f32> = (0..40).map(|_| row_h).collect();
        let window = VisibleWindow::new(viewport);
        let mut el: iced::Element<'_, VisibleWindow> = virtual_column(
            &heights,
            window,
            TURNS_OVERSCAN,
            None,
            |w| w,
            |_| window,
            Some(Id::new("hud-turns")),
            tok,
            |i| label(format!("turn {i}"), tok, A11y::new("r", Role::ListItem)),
            A11y::new("Turns", Role::List),
        );
        let mut tree = Tree::new(el.as_widget());
        let renderer = iced::Renderer::Secondary(iced_tiny_skia::Renderer::new(
            Font::DEFAULT,
            Pixels::from(16u32),
        ));
        let limits = Limits::new(Size::ZERO, Size::new(320.0, viewport));
        let node = el.as_widget_mut().layout(&mut tree, &renderer, &limits);
        let layout = Layout::new(&node);
        let origin = layout.bounds();
        let over = Point::new(origin.x + 20.0, origin.center_y());
        let vp = Rectangle::new(Point::ORIGIN, Size::new(320.0, viewport));
        let mut clipboard = clipboard::Null;
        let mut messages = Vec::new();
        {
            let mut shell = iced::advanced::Shell::new(&mut messages);
            el.as_widget_mut().update(
                &mut tree,
                &Event::Mouse(mouse::Event::WheelScrolled {
                    delta: mouse::ScrollDelta::Pixels { x: 0.0, y: -4.0 },
                }),
                layout,
                mouse::Cursor::Available(over),
                &renderer,
                &mut clipboard,
                &mut shell,
                &vp,
            );
        }
        assert!(
            messages.is_empty(),
            "a 4px wheel must stay in virtual_clip, got {messages:?}"
        );
    }

    #[test]
    fn session_picker_is_spotlight_not_list_detail_rail() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod source");
        assert!(prod.contains("fn session_picker"));
        assert!(prod.contains("browse_mode()"));
        assert!(prod.contains("fn browse_session_bar"));
        assert!(prod.contains("Message::SessionsHome"));
        assert!(prod.contains("fn status_chip"));
        assert!(prod.contains("fn paint_badge"));
        let badge = prod
            .split("fn paint_badge")
            .nth(1)
            .expect("paint_badge")
            .split("fn session_state_row")
            .next()
            .expect("badge body");
        assert!(
            badge.contains("Wrapping::None"),
            "badge text must stay on one line"
        );
        assert!(prod.contains("chip_face"));
        assert!(prod.contains("fn session_state_row"));
        assert!(prod.contains("muted_meta(meta, tea)"));
        assert!(prod.contains("fn inset_search"));
        assert!(
            prod.contains("widget::search_input"),
            "inset search must paint FieldRun highlight"
        );
        assert!(!prod.contains("let _ = highlight"));
        assert!(prod.contains("fn list_tile"));
        assert!(prod.contains("fn session_state_from_meta"));
        assert!(prod.contains("widget::virtual_column"));
        assert!(!prod.contains("QuietColumn"));
        assert!(!prod.contains("fn tea_two_line"));
        assert!(!prod.contains("fn tea_list_view"));
        assert!(!prod.contains("SESSION_LIST_W"));
        assert!(!prod.contains("pattern::list_detail"));
        assert!(prod.contains("widget::rule_h"));
        assert!(prod.contains("widget::tooltip_wrap"));
        assert!(!prod.contains("fn query_help_tooltip"));
        assert!(prod.contains("ControlSize::Default"));
        assert!(prod.contains("icedtea::widget::pick_list"));
        assert!(prod.contains("TreeFace::Files"));
        assert!(prod.contains("icedtea::widget::text_input"));
        assert!(
            prod.contains("icedtea::widget::highlighted_code"),
            "tool code panes must use iced highlighter, not plain mono code_block"
        );
        assert!(prod.contains("icedtea::widget::selectable"));
        // Overview KV via kit (icedtea value_field + FORM_LABEL gutter).
        assert!(prod.contains("kit::labeled_value"));
        assert!(prod.contains("kit::labeled_plain"));
        assert!(prod.contains("kit::context_progress"));
        assert!(prod.contains("kit::pane_tabs"));
        assert!(prod.contains("fn inset_search"));
        assert!(!prod.contains("fn search_shell"));
        assert!(!prod.contains("kit::search_field"));
        let search = prod
            .split("fn inset_search")
            .nth(1)
            .expect("inset_search")
            .split("fn catalog_query_runs")
            .next()
            .expect("search body");
        assert!(search.contains("widget::search_input"));
        assert!(!search.contains("padding(Padding::from([6, 10]))"));
        assert!(prod.contains("pattern::status_bar"));
        assert!(!prod.contains("kit::status_footer"));
        assert!(!prod.contains("Message::ShowJobLog"));
        assert!(!prod.contains("job.inspect"));
        assert!(prod.contains("kit::help_modal"));
        assert!(!prod.contains("fn look_pane"));
        assert!(!prod.contains("pattern::drawer"));
        assert!(prod.contains("kit::status_empty"));
        assert!(prod.contains("help_open()"));
        assert!(prod.contains("overview_fields"));
        let overview = prod
            .split("fn overview_tab")
            .nth(1)
            .expect("overview_tab")
            .split("fn kv")
            .next()
            .expect("overview body");
        assert!(prod.contains("overview_section_tabs"));
        assert!(!overview.contains("overview_section_tabs"));
        assert!(overview.contains("session_state_from_meta("));
        assert!(overview.contains("overview.summary"));
        assert!(overview.contains("select_bound"));
        assert!(!overview.contains("markdown_bound"));
        assert!(overview.contains("overview_task_rows"));
        assert!(overview.contains("overview_workflow_rows"));
        assert!(overview.contains("overview_subagent_rows"));
        assert!(overview.contains("Wrapping::Word"));
        assert!(overview.contains("widget::virtual_column"));
        assert!(overview.contains("widget::data_table"));
        assert!(overview.contains("OVERVIEW_LIST_OVERSCAN"));
        assert!(!overview.contains("overview_run_jumps"));
        assert!(!overview.contains("\"{} · {} · {}\""));
        let picker = prod
            .split("fn session_picker_at")
            .nth(1)
            .expect("session_picker_at")
            .split("fn detail_pane")
            .next()
            .expect("picker body");
        assert!(picker.contains("widget::virtual_column"));
        assert!(picker.contains("session_list_card("));
        assert!(picker.contains("FocusSession(c.id)"));
        assert!(picker.contains("on_double_click"));
        assert!(picker.contains("SelectSession"));
        assert!(prod.contains(".on_double_click(on_open)"));
        assert!(prod.contains("Message::SelectTimeline(ix)"));
        assert!(prod.contains("Message::SelectTurn(t.turn_index)"));
        assert!(prod.contains("on_double_click(Message::OpenNote"));
        assert!(prod.contains("on_double_click(Message::OpenOverviewRow"));
        let detail = prod
            .split("fn detail_pane")
            .nth(1)
            .expect("detail_pane")
            .split("fn timeline_filter")
            .next()
            .expect("detail body");
        assert!(detail.contains("overview_virtual_body"));
        assert!(detail.contains("turns_tab(hud)"));
        let bar = prod
            .split("fn browse_session_bar")
            .nth(1)
            .expect("browse_session_bar")
            .split("fn timeline_filter")
            .next()
            .expect("bar body");
        assert!(bar.contains("session_state_from_meta("));
        assert!(prod.contains("fn select_bound"));
        assert!(prod.contains("event.{}.in.{}"));
        assert!(prod.contains("icedtea::widget::image_slot"));
        assert!(prod.contains("icedtea::widget::busy_overlay"));
        assert!(prod.contains("kit::status_empty"));
        assert!(prod.contains("icedtea::widget::info_bar"));
        assert!(prod.contains("fn diff_chrome"));
        assert!(prod.contains("fn diff_context_body"));
        assert!(prod.contains("fn diff_context_tabs"));
        assert!(prod.contains("fn diff_split"));
        assert!(prod.contains("widget::tree_view"));
        assert!(prod.contains("fn diff_search"));
        assert!(prod.contains("Message::DiffHitStep"));
        assert!(prod.contains("Message::DiffPointPicked"));
        assert!(prod.contains("\"diff.prompt\""));
        assert!(prod.contains("\"diff.assistant\""));
        assert!(prod.contains("BodyPaint::Markdown =>"));
        assert!(!prod.contains("chat_md_body"));
        assert!(!prod.contains("iced::widget::markdown::view"));
        assert!(prod.contains("widget::markdown_view"));
        assert!(prod.contains("icedtea::motion::overlay"));
        assert!(prod.contains("Slide::Up"));
        assert!(prod.contains("page_slide()"));
        assert!(prod.contains("fn page_body"));
        assert!(prod.contains("overlay_moving()"));
        assert!(prod.contains("page_moving()"));
        assert!(prod.contains("fn note_list_card"));
        assert!(prod.contains("fn note_edit_links"));
        assert!(prod.contains("fn card_chips"));
        assert!(prod.contains("fn command_end"));
        assert!(prod.contains("Add note"));
        assert!(prod.contains("fn note_one_choice"));
        assert!(prod.contains("fn note_many_choices"));
        assert!(prod.contains("ChipKind::Filter"));
        assert!(prod.contains("note_field_input_key"));
        assert!(prod.contains("NOTE_TURN_INPUT"));
        assert!(prod.contains("icedtea::widget::textarea"));
        assert!(prod.contains("Message::NoteEdit"));
        // Overview path is selectable; no in-pane Copy path button.
        assert!(!prod.contains("fn overview_commands"));
        assert!(!prod.contains("format!(\"f{}\""));
        assert!(prod.contains("format!(\"n{}\""));
        assert!(!prod.contains("Tab fields"));
        assert!(!prod.contains("Ctrl+1–5"));
        assert!(!prod.contains("hotkey_hint()"));
        assert!(prod.contains("icedtea::widget::button("));
        assert!(prod.contains("Variant::Chip"));
        assert!(!prod.contains("button_sized"));
        assert!(prod.contains("fn jump_control"));
        assert!(prod.contains("Go to Timeline"));
        assert!(!prod.contains("struct JumpIcon"));
        assert!(!prod.contains("chip_btn(\"Timeline\""));
        assert!(prod.contains("TURNS_OVERSCAN"));
        assert!(prod.contains("widget::virtual_column"));
        assert!(prod.contains("turns_tab(hud)"));
        assert!(!prod.contains("context_progress") || prod.contains("kit::context_progress"));
        assert!(prod.contains("pattern::context_menu"));
        assert!(prod.contains("stack![chrome]"));
        assert!(!prod.contains("fn turn_note"));
        assert!(!prod.contains("fn turn_diff"));
        assert!(!prod.contains("fn turn_jump"));
        assert!(!prod.contains("fn turn_stats_row"));
        assert!(!prod.contains("fn turn_run_chips"));
        assert!(!prod.contains("fn card_chips_inline"));
        assert!(!prod.contains("command_palette_view"));
        assert!(prod.contains("fn event_body"));
        assert!(!prod.contains("time_picker"));
        assert!(!prod.contains("fn drawer"));
        assert!(!prod.contains("fn disclosure"));
        assert!(prod.contains("fn select_bound"));
        assert!(prod.contains("fn turn_list_card"));
        assert!(!prod.contains("fn closed_turn_face"));
        assert!(prod.contains("Search events…"));
        assert!(prod.contains("Search turns"));
        assert!(!prod.contains("Session events"));
        assert!(prod.contains("fn turn_title"));
        assert!(!prod.contains("visual_lines("));
        assert!(!prod.contains(".height(height)"));
        assert!(prod.contains("matched in {}:"));
        assert!(prod.contains("fn brand_variant"));
        assert!(!prod.contains("accordion_view"));
        assert!(prod.contains("fn note_list_card"));
        assert!(!prod.contains("widget::expander"));
        assert!(!prod.contains("Peek::Lines(2)"));
        assert!(prod.contains("fn closed_list_card"));
        assert!(prod.contains("fn event_detail_pane"));
        assert!(prod.contains("fn event_step"));
        let chrome = prod
            .split("fn event_detail_chrome")
            .nth(1)
            .expect("event_detail_chrome")
            .split("fn event_step")
            .next()
            .expect("chrome body");
        assert!(chrome.contains("Icon::Back"));
        assert!(chrome.contains("Icon::Chevron"));
        assert!(chrome.contains("card_cmds_row"));
        assert!(chrome.contains("event_raw_toggle"));
        assert!(chrome.contains("ToggleEventRaw"));
        assert!(!chrome.contains("\"Previous\""));
        assert!(!chrome.contains("\"Next\""));
        assert!(!chrome.contains("{at} of {n}"));
        assert!(!prod.contains("fn neighbor_link"));
        assert!(!prod.contains("‹ {name}"));
        assert!(!prod.contains("{name} ›"));
        assert!(prod.contains("fn event_list_heading"));
        assert!(prod.contains("fn event_type_paint"));
        assert!(prod.contains("fn label_badge"));
        assert!(prod.contains("fn brand_variant"));
        let heading = prod
            .split("fn event_list_heading")
            .nth(1)
            .expect("heading")
            .split("fn event_list_title")
            .next()
            .expect("heading body");
        assert!(heading.contains("label_badge"));
        assert!(heading.contains("muted_meta(rest.join"));
        assert!(heading.contains("format_tool_display"));
        let payload = prod
            .split("fn event_payload")
            .nth(1)
            .expect("event_payload")
            .split("fn field_body")
            .next()
            .expect("payload body");
        assert!(payload.contains("job_event_inspect"));
        assert!(payload.contains("workflow_event_inspect"));
        assert!(payload.contains("label_badge("));
        let wf_card = prod
            .split("fn workflow_run_inspect")
            .nth(1)
            .expect("workflow_run_inspect")
            .split("fn workflow_child_list")
            .next()
            .expect("workflow card");
        assert!(wf_card.contains("Asked"));
        assert!(wf_card.contains("Happened"));
        assert!(wf_card.contains("Failed"));
        assert!(wf_card.contains("select_bound"));
        assert!(!wf_card.contains("virtual_column"));
        let wf_kids = prod
            .split("fn workflow_child_list")
            .nth(1)
            .expect("workflow_child_list")
            .split("fn job_event_inspect")
            .next()
            .expect("child list");
        assert!(wf_kids.contains("virtual_column"));
        assert!(wf_kids.contains("OpenWorkflowChild"));
        assert!(wf_kids.contains("\"complete\""));
        assert!(wf_kids.contains("\"failed\""));
        assert!(!wf_kids.contains("\"ok\""));
        assert!(!wf_kids.contains("select_bound"));
        assert!(wf_kids.contains("openable"));
        let job_card = prod
            .split("fn job_event_inspect")
            .nth(1)
            .expect("job_event_inspect")
            .split("fn event_payload")
            .next()
            .expect("job card");
        assert!(job_card.contains("code_inset"));
        assert!(job_card.contains("\"bash\""));
        assert!(job_card.contains("job_status"));
        assert!(job_card.contains("job_event_id"));
        assert!(job_card.contains("job_exit_code"));
        assert!(job_card.contains("job_inspect_blocks"));
        assert!(job_card.contains("schedule_inspect_blocks"));
        let sub_card = prod
            .split("fn event_body")
            .nth(1)
            .expect("event_body")
            .split("fn event_payload")
            .next()
            .expect("event_body slice");
        assert!(sub_card.contains("subagent_inspect_blocks"));
        assert!(job_card.contains("schedule_last_fire"));
        assert!(!job_card.contains("get(\"last_fired_at\")"));
        assert!(payload.contains("\"Input\""));
        assert!(!payload.contains("text(format_tool_display"));
        let turns_card = prod
            .split("fn turn_list_card")
            .nth(1)
            .expect("turn_list_card")
            .split("fn turns_filter")
            .next()
            .expect("turns card");
        assert!(turns_card.contains("status_chip("));
        assert!(turns_card.contains("closed_list_card("));
        assert!(turns_card.contains("SelectTurn"));
        assert!(!turns_card.contains("tools ·"));
        let face = prod
            .split("fn event_list_title")
            .nth(1)
            .expect("title")
            .split("fn event_body")
            .next()
            .expect("title body");
        assert!(face.contains("list_event_detail"));
        assert!(!face.contains("label_badge"));
        assert!(!face.contains("id_font"));
        assert!(prod.contains("footer_table_for(hud.key_scope(), hud.key_overlay())"));
        assert!(!prod.contains("chip_btn(\"Back\""));
        assert!(!prod.contains("is_timeline_expanded"));
        assert!(!prod.contains("TurnExpand"));
        assert!(!prod.contains("fn turn_body"));
        assert!(!prod.contains("NoteExpand"));
    }

    #[test]
    fn workflow_child_rows_use_virtual_column() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        assert!(prod.contains("fn workflow_child_list"));
        let kids = prod
            .split("fn workflow_child_list")
            .nth(1)
            .expect("list")
            .split("fn job_event_inspect")
            .next()
            .expect("body");
        assert!(kids.contains("icedtea::widget::virtual_column"));
        assert!(!kids.contains("widget::scroll("));
        assert!(!kids.contains("select_bound"));
        assert!(kids.contains("if child.success { \"complete\" } else { \"failed\" }"));
        assert!(kids.contains("let openable = !child.path.is_empty()"));
        assert!(kids.contains("Message::OpenWorkflowChild"));
        let list = prod
            .split("fn overview_run_list")
            .nth(1)
            .expect("overview_run_list")
            .split("fn overview_stats")
            .next()
            .expect("list body");
        assert!(
            list.contains("overview_row_status"),
            "Overview Workflows uses workflow_status_word via overview_row_status"
        );
        let pane = prod
            .split("fn event_detail_pane")
            .nth(1)
            .expect("detail")
            .split("fn event_detail_chrome")
            .next()
            .expect("pane");
        assert!(pane.contains("workflow_child_list"));
        assert!(pane.contains("open_workflow_children"));
        assert!(
            pane.contains("event_body("),
            "workflow-with-children keeps event_body chrome"
        );
        assert!(
            pane.contains("WORKFLOW_INSPECT_H"),
            "inspect scroll is capped so Agents get Fill"
        );
        assert!(pane.contains("Length::Fixed(WORKFLOW_INSPECT_H)"));
        assert!(pane.contains(".height(Length::Fill)"));
        let body = prod
            .split("fn event_body")
            .nth(1)
            .expect("event_body")
            .split("fn notes_tab")
            .next()
            .expect("event_body slice");
        assert!(body.contains("timeline_query_hit"));
        assert!(body.contains("content_truncated"));
        assert!(body.contains("card_chips"));
        assert!(body.contains("event_note"));
    }

    #[test]
    fn event_body_paints_search_hit_truncated_bar_and_note_chips() {
        let hud = Hud::default();
        let ev = TimelineEvent {
            index: 4,
            tool_name: "workflow".into(),
            preview: "the needle is here".into(),
            content: "the needle is here".into(),
            content_truncated: true,
            ..TimelineEvent::default()
        };
        let _ = event_body(&hud, &ev, None);
        assert!(ev.content_truncated);
        assert!(crate::format::timeline_query_hit(&ev, "needle").is_some());
        let src = include_str!("view.rs");
        let body = src
            .split("fn event_body")
            .nth(1)
            .expect("event_body")
            .split("fn notes_tab")
            .next()
            .expect("body");
        assert!(body.contains("timeline_query_hit"));
        assert!(body.contains("content_truncated"));
        assert!(body.contains("card_chips"));
        assert!(body.contains("event_note"));
    }

    #[test]
    fn note_card_uses_source_badge_not_source_field() {
        let src = include_str!("view.rs");
        let body = src
            .split("fn note_body")
            .nth(1)
            .expect("note_body")
            .split("fn note_quiet_btn")
            .next()
            .expect("body");
        assert!(body.contains("n.source.trim()"));
        assert!(body.contains("label_badge"));
        assert!(body.contains("BrandRole::Cream"));
        assert!(body.contains("fenced_code_block"));
        assert!(body.contains("code_inset"));
        assert!(!body.contains("Source  "));
    }

    #[test]
    fn notes_tab_paints_cards_in_a_pixel_scroller() {
        let src = include_str!("view.rs");
        let body = src.split("fn notes_tab").nth(1).unwrap_or("");
        assert!(body.contains("widget::scroll("));
        assert!(body.contains("with_disabled(true)"));
        assert!(body.contains("Message::NoteScroll"));
        assert!(body.contains("note_list_card"));
        assert!(
            !body
                .split("fn notes_compose_form")
                .next()
                .unwrap_or("")
                .contains("virtual_column"),
            "Notes list is a pixel scroller; virtual_column wheel steps the first card height"
        );
        assert!(body.contains("Length::Fill"));
        assert!(body.contains("composing_note"));
        assert!(body.contains("StartNote"));
        assert!(body.contains("notes_compose_form"));
        assert!(body.contains("note_form_schema"));
        assert!(body.contains("widget::scroll("));
        assert!(body.contains("note_draft().source.trim()"));
        assert!(body.contains("label_badge"));
        assert!(body.contains("CardFace::Outlined"));
        let field = src
            .split("fn note_schema_field")
            .nth(1)
            .unwrap_or("")
            .split("fn notes_tab")
            .next()
            .unwrap_or("");
        assert!(field.contains("note_textarea_height"));
        assert!(body.contains("status_empty"));
        assert!(body.contains("note_display_fields"));
        assert!(body.contains("style::card"));
        let card = src
            .split("fn note_body")
            .nth(1)
            .unwrap_or("")
            .split("fn note_list_card")
            .next()
            .unwrap_or("");
        assert!(card.contains("markdown_bound"));
        assert!(card.contains("code_inset"));
        assert!(card.contains("note_edit_links"));
        assert!(card.contains("widget::button"));
        assert!(card.contains("DensityName::Compact"));
        assert!(card.contains("Length::Fill"));
    }

    #[test]
    fn workflow_child_list_builds_from_hud() {
        let hud = Hud::default();
        let child = WorkflowChildRow {
            id: "ag-1".into(),
            label: "research".into(),
            success: true,
            session_id: "child-1".into(),
            path: "/tmp/child".into(),
        };
        let kids = [child];
        let _ = workflow_child_list(&hud, &kids);
        let closed = WorkflowChildRow {
            id: "ag-2".into(),
            label: "ghost".into(),
            success: false,
            ..WorkflowChildRow::default()
        };
        let _ = workflow_child_list(&hud, &[closed]);
    }

    #[test]
    fn closed_list_card_keeps_a_single_title_line() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let card = prod
            .split("fn closed_list_card")
            .nth(1)
            .expect("closed_list_card")
            .split("fn turn_title")
            .next()
            .expect("card body");
        assert!(card.contains("Wrapping::None"));
        assert!(card.contains("list_tile("));
        let sess = prod
            .split("fn session_list_card")
            .nth(1)
            .expect("session_list_card")
            .split("fn detail_pane")
            .next()
            .expect("session card");
        assert!(sess.contains("list_tile("));
        let turns = prod
            .split("fn turns_tab")
            .nth(1)
            .expect("turns_tab")
            .split("fn timeline_tab")
            .next()
            .expect("turns body");
        assert!(turns.contains("FocusTurnRow"));
        assert!(
            !turns.contains("LIST_GAP"),
            "row height already includes the hairline"
        );
        let timeline = prod
            .split("fn timeline_event_list")
            .nth(1)
            .expect("timeline_event_list")
            .split("fn timeline_tab")
            .next()
            .expect("timeline list body");
        assert!(timeline.contains("FocusTimelineRow"));
        assert!(!timeline.contains("LIST_GAP"));
    }

    #[test]
    fn timeline_tab_keeps_the_list_mounted_under_detail() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let tab = prod
            .split("fn timeline_tab")
            .nth(1)
            .expect("timeline_tab")
            .split("fn event_detail_pane")
            .next()
            .expect("timeline_tab body");
        assert!(tab.contains("cover_stack"));
        assert!(tab.contains("timeline_event_list"));
        assert!(tab.contains("event_detail_cover"));
        assert!(!tab.contains("return event_detail_pane"));
        let notes = prod
            .split("fn notes_tab")
            .nth(1)
            .expect("notes_tab")
            .split("fn notes_compose_form")
            .next()
            .expect("notes_tab body");
        assert!(notes.contains("cover_stack"));
        assert!(notes.contains("widget::scroll"));
        assert!(!notes.contains("virtual_column"));
        let page = prod
            .split("fn page_body")
            .nth(1)
            .expect("page_body")
            .split("fn cover_stack")
            .next()
            .expect("page_body body");
        assert!(page.contains("motion::overlay"));
        assert!(
            page.contains("return container(child)"),
            "at rest the wrap must drop so pick lists can open"
        );
        assert!(page.contains("page_moving()"));
        assert!(
            page.contains("MotionRole::Step"),
            "event step uses switch, not overlay cover"
        );
        let cover = prod
            .split("fn event_detail_cover")
            .nth(1)
            .expect("event_detail_cover")
            .split("fn event_detail_pane")
            .next()
            .expect("event_detail_cover body");
        assert!(cover.contains("motion::switch"));
        assert!(cover.contains("event_switch_face"));
    }

    #[test]
    fn glance_open_and_add_note_use_icon_buttons() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let glance = prod
            .split("fn glance_row")
            .nth(1)
            .expect("glance_row")
            .split("fn footer")
            .next()
            .expect("glance body");
        assert!(glance.contains("glance_open_btn"));
        assert!(!glance.contains("Action::new"));
        let open = prod
            .split("fn glance_open_btn")
            .nth(1)
            .expect("glance_open_btn")
            .split("fn command_end")
            .next()
            .expect("open body");
        assert!(open.contains("icon_button"));
        assert!(open.contains("FolderOpen"));
        assert!(open.contains("tooltip_wrap"));
        assert!(open.contains("Variant::Elevated"));
        let cmds = prod
            .split("fn card_cmds_row")
            .nth(1)
            .expect("card_cmds_row")
            .split("fn closed_list_card")
            .next()
            .expect("cmds body");
        assert!(cmds.contains("note_add_btn"));
        assert!(!cmds.contains("note_chip"));
        let add = prod
            .split("fn note_add_btn")
            .nth(1)
            .expect("note_add_btn")
            .split("fn glance_open_btn")
            .next()
            .expect("add body");
        assert!(add.contains("icon_button"));
        assert!(add.contains("DocumentCreate"));
        assert!(add.contains("tooltip_wrap"));
        assert!(add.contains("Variant::Elevated"));
        let notes = prod
            .split("fn notes_tab")
            .nth(1)
            .expect("notes_tab")
            .split("fn notes_compose_form")
            .next()
            .expect("notes body");
        assert!(notes.contains("note_add_btn"));
        assert!(!notes.contains("note_chip"));
        let form = prod
            .split("fn notes_compose_form")
            .nth(1)
            .expect("notes_compose_form")
            .split("fn note_list_card")
            .next()
            .expect("form body");
        assert!(
            !form.contains("\"session\""),
            "turn field must not use session as the placeholder"
        );
    }
}
