//! Keyboard shortcut tables for the HUD footer and help cheatsheet.
//!
//! The footer prints [`ActionTable::footer_hints`] on one line and status
//! on the next. [`pattern::cheatsheet`] lists the full table.

use iced::keyboard::Key;
use icedtea::action::{Action, ActionTable};
use icedtea::shortcut::Shortcut;

use crate::app::Message;
use crate::keys::KeyOverlay;
use crate::model::Tab;

/// What the footer should advertise right now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct KeyScope {
    pub browse: bool,
    pub help_open: bool,
    pub timeline_detail: bool,
    pub child_open: bool,
    pub compact_child: bool,
    /// Events turn pick is shown (more than one turn).
    pub turn_pick: bool,
    /// A specific Timeline turn is selected; `]` stays off (h/l still step).
    pub turn_locked: bool,
    /// Diff turn pick (more than one rewind record).
    pub diff_pick: bool,
    pub tab: Tab,
    pub leader_armed: bool,
    /// A note card is the list highlight.
    pub note_focused: bool,
    /// HUD Notes compose form is open (j is not list motion).
    pub notes_composing: bool,
}

fn scope_tabs(scope: KeyScope) -> &'static [Tab] {
    if scope.compact_child {
        Tab::CHILD
    } else {
        &Tab::ALL
    }
}

fn shortcut_label(overlay: &KeyOverlay, id: &str, spec: &str) -> String {
    if let Some(label) = overlay.sequence_display(id, spec) {
        return label;
    }
    overlay
        .hud_spec(id, spec)
        .split(',')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .filter_map(Shortcut::parse)
        .map(|s| s.to_string())
        .collect::<Vec<_>>()
        .join(" / ")
}

fn push(
    table: &mut ActionTable<Message>,
    overlay: &KeyOverlay,
    id: &str,
    title: &str,
    spec: &str,
    msg: Message,
) {
    let label = shortcut_label(overlay, id, spec);
    if let Some(seq) = overlay.sequence_display(id, spec) {
        table.insert(
            Action::new(id, title, msg)
                .with_shortcut(Shortcut::new(
                    iced::keyboard::Modifiers::empty(),
                    Key::Character(seq.into()),
                ))
                .with_tooltip(label),
        );
        return;
    }
    let resolved = overlay.hud_spec(id, spec);
    let first = resolved.split(',').next().unwrap_or(resolved.as_str());
    let parsed = Shortcut::parse(first).expect("HUD shortcut spec");
    table.insert(
        Action::new(id, title, msg)
            .with_shortcut(parsed)
            .with_tooltip(label),
    );
}

fn push_leader(table: &mut ActionTable<Message>, overlay: &KeyOverlay) {
    let Some(leader) = overlay.leader() else {
        return;
    };
    let spec = overlay.hud_spec("leader.prefix", leader);
    let Some(parsed) = Shortcut::parse(&spec).or_else(|| Shortcut::parse(leader)) else {
        return;
    };
    table.insert(Action::new("leader.prefix", "Leader", Message::Noop).with_shortcut(parsed));
}

/// Primary keys for the status-bar footer (short, context-filtered).
pub fn footer_table(scope: KeyScope) -> ActionTable<Message> {
    footer_table_for(scope, &KeyOverlay::default())
}

/// Footer table using a resolved overlay (production HUD path).
pub fn footer_table_for(scope: KeyScope, overlay: &KeyOverlay) -> ActionTable<Message> {
    let mut table = ActionTable::new();
    if scope.leader_armed {
        push_leader(&mut table, overlay);
    }
    if scope.help_open {
        push(
            &mut table,
            overlay,
            "help.toggle",
            "Help",
            "?",
            Message::ToggleHelp,
        );
        push(
            &mut table,
            overlay,
            "overlay.hide",
            "Close",
            "escape",
            Message::Hide,
        );
        return table;
    }
    push(
        &mut table,
        overlay,
        "help.toggle",
        "Help",
        "?",
        Message::ToggleHelp,
    );
    let hide = if scope.timeline_detail {
        "Timeline"
    } else if scope.child_open {
        "Parent"
    } else {
        "Hide"
    };
    push(
        &mut table,
        overlay,
        "overlay.hide",
        hide,
        "escape",
        Message::Hide,
    );
    if !scope.browse {
        push(
            &mut table,
            overlay,
            "session.open",
            "Open",
            "enter",
            Message::ActivateSelected,
        );
        push(
            &mut table,
            overlay,
            "session.import",
            "Import",
            "ctrl+o",
            Message::ImportPicked(None),
        );
        push(
            &mut table,
            overlay,
            "list.down",
            "Down",
            "j,down",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "search.focus",
            "Search",
            "/",
            Message::Noop,
        );
        return table;
    }
    push(
        &mut table,
        overlay,
        "search.focus",
        "Search",
        "/",
        Message::Noop,
    );
    push(
        &mut table,
        overlay,
        "sessions.home",
        "Sessions",
        "u",
        Message::SessionsHome,
    );
    push(
        &mut table,
        overlay,
        "pane.next",
        "Panes",
        "ctrl+tab",
        Message::Noop,
    );
    if scope.timeline_detail {
        push(
            &mut table,
            overlay,
            "list.down",
            "Step",
            "j,down",
            Message::Noop,
        );
    } else if matches!(scope.tab, Tab::Turns | Tab::Timeline | Tab::Diff)
        || (scope.tab == Tab::Notes && !scope.notes_composing)
    {
        push(
            &mut table,
            overlay,
            "list.down",
            "Down",
            "j,down",
            Message::Noop,
        );
    }
    if matches!(scope.tab, Tab::Overview | Tab::Turns | Tab::Timeline) {
        push(
            &mut table,
            overlay,
            "session.open",
            "Open",
            "enter",
            Message::ActivateSelected,
        );
    }
    if scope.tab == Tab::Notes && scope.note_focused {
        push(
            &mut table,
            overlay,
            "session.open",
            "Edit",
            "enter",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "session.delete",
            "Delete",
            "x,delete",
            Message::Noop,
        );
    }
    push(&mut table, overlay, "edit.copy", "Copy", "y", Message::Yank);
    if scope.tab == Tab::Turns && !scope.compact_child {
        push(
            &mut table,
            overlay,
            "turns.timeline",
            "Go",
            "g",
            Message::Noop,
        );
    }
    if (scope.tab == Tab::Timeline && scope.turn_pick)
        || (scope.tab == Tab::Diff && scope.diff_pick)
    {
        push(
            &mut table,
            overlay,
            "events.prev_turn",
            "Previous",
            "h,left",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "events.next_turn",
            "Next",
            "l,right",
            Message::Noop,
        );
    }
    if scope.tab != Tab::Notes && scope.tab != Tab::Overview {
        push(
            &mut table,
            overlay,
            "pane.notes",
            "Notes",
            "shift+n",
            Message::SetTab(Tab::Notes),
        );
    }
    table
}

/// Shortcut list for the `?` cheatsheet (keys that apply in *scope*).
pub fn help_table(scope: KeyScope) -> ActionTable<Message> {
    help_table_for(scope, &KeyOverlay::default())
}

/// Cheatsheet using a resolved overlay (production HUD path).
pub fn help_table_for(scope: KeyScope, overlay: &KeyOverlay) -> ActionTable<Message> {
    let mut table = ActionTable::new();
    if overlay.leader().is_some() {
        push_leader(&mut table, overlay);
    }
    push(
        &mut table,
        overlay,
        "help.toggle",
        "Help",
        "?",
        Message::ToggleHelp,
    );
    push(
        &mut table,
        overlay,
        "overlay.hide",
        "Hide overlay",
        "escape",
        Message::Hide,
    );
    if !scope.browse
        || matches!(
            scope.tab,
            Tab::Overview | Tab::Turns | Tab::Timeline | Tab::Notes
        )
    {
        push(
            &mut table,
            overlay,
            "session.open",
            if scope.tab == Tab::Notes {
                "Edit note"
            } else {
                "Open"
            },
            "enter",
            Message::ActivateSelected,
        );
    }
    if !scope.browse {
        push(
            &mut table,
            overlay,
            "session.import",
            "Import",
            "ctrl+o",
            Message::ImportPicked(None),
        );
    }
    if !scope.browse
        || matches!(scope.tab, Tab::Turns | Tab::Timeline | Tab::Diff)
        || (scope.tab == Tab::Notes && !scope.notes_composing)
    {
        push(
            &mut table,
            overlay,
            "list.down",
            "Move down",
            "j,down",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "list.up",
            "Move up",
            "k,up",
            Message::Noop,
        );
    }
    if scope.browse && scope.tab == Tab::Notes {
        push(
            &mut table,
            overlay,
            "session.delete",
            "Delete note",
            "x,delete",
            Message::Noop,
        );
    }
    if scope.browse {
        push(
            &mut table,
            overlay,
            "pane.next",
            "Next pane",
            "ctrl+tab",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "pane.prev",
            "Previous pane",
            "ctrl+shift+tab",
            Message::Noop,
        );
    }
    for (i, tab) in scope_tabs(scope).iter().enumerate() {
        let n = i + 1;
        push(
            &mut table,
            overlay,
            &format!("pane.{n}"),
            tab.label(),
            &format!("ctrl+{n}"),
            Message::SetTab(*tab),
        );
    }
    push(&mut table, overlay, "edit.copy", "Copy", "y", Message::Yank);
    push(
        &mut table,
        overlay,
        "edit.copy_chord",
        "Copy",
        "ctrl+shift+c",
        Message::Yank,
    );
    push(
        &mut table,
        overlay,
        "search.focus",
        "Search",
        "/",
        Message::Noop,
    );
    if scope.browse {
        push(
            &mut table,
            overlay,
            "sessions.home",
            "Session list",
            "u",
            Message::SessionsHome,
        );
    }
    if scope.browse && scope.tab != Tab::Notes {
        push(
            &mut table,
            overlay,
            "pane.notes",
            "Notes",
            "shift+n",
            Message::SetTab(Tab::Notes),
        );
    }
    if scope.tab == Tab::Timeline && scope.turn_pick {
        push(
            &mut table,
            overlay,
            "events.prev_turn",
            "Previous turn",
            "h,left",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "events.next_turn",
            "Next turn",
            "l,right",
            Message::Noop,
        );
        if !scope.turn_locked {
            push(
                &mut table,
                overlay,
                "events.scope_next",
                "Next match",
                "]",
                Message::Noop,
            );
        }
        push(
            &mut table,
            overlay,
            "events.all_turns",
            "All turns",
            "[",
            Message::Noop,
        );
    }
    if scope.tab == Tab::Diff && scope.diff_pick {
        push(
            &mut table,
            overlay,
            "events.prev_turn",
            "Previous turn",
            "h,left",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "events.next_turn",
            "Next turn",
            "l,right",
            Message::Noop,
        );
    }
    if scope.tab == Tab::Turns && !scope.compact_child {
        push(
            &mut table,
            overlay,
            "turns.timeline",
            "Go to Timeline",
            "g",
            Message::Noop,
        );
    }
    table
}

#[cfg(test)]
mod tests {
    use super::*;

    fn picker() -> KeyScope {
        KeyScope {
            browse: false,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        }
    }

    #[test]
    fn help_table_lists_unique_shortcuts() {
        let table = help_table(picker());
        assert!(table.conflicts().is_empty());
        assert!(table.get("help.toggle").is_some());
        assert!(table.get("overlay.hide").is_some());
        assert!(table.get("list.down").is_some());
        assert!(table.get("list.up").is_some());
        assert!(table.get("pane.1").is_some());
        assert!(table.get("pane.5").is_some());
        assert!(table.get("pane.6").is_none());
        assert!(table.get("session.done").is_none());
        assert!(table.get("edit.copy").is_some());
        assert!(table.get("search.focus").is_some());
        let hints = table.footer_hints();
        assert!(hints.iter().any(|h| h.starts_with("? ")));
        assert!(hints.iter().any(|h| h.contains("esc")));
    }

    #[test]
    fn footer_table_picker_is_short() {
        let hints = footer_table(picker()).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("? help"));
        assert!(blob.contains("esc hide"));
        assert!(blob.contains("enter open"));
        assert!(blob.to_ascii_lowercase().contains("import"));
        assert!(blob.contains("j down"));
        assert!(blob.contains("/ search"));
        assert!(!blob.contains("tab "));
        assert!(!blob.contains("y copy"));
    }

    #[test]
    fn footer_table_browse_and_timeline_detail() {
        let browse = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let blob = browse.footer_hints().join("  ·  ");
        assert!(blob.contains("ctrl+tab panes"));
        let notes = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Notes,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let notes_blob = notes.footer_hints().join("  ·  ");
        assert!(notes_blob.contains("ctrl+tab panes"), "{notes_blob}");
        assert!(notes_blob.contains("j down"), "{notes_blob}");
        assert!(!notes_blob.contains("edit"), "{notes_blob}");
        let notes_hi = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Notes,
            leader_armed: false,
            note_focused: true,
            notes_composing: false,
        });
        let hi_blob = notes_hi.footer_hints().join("  ·  ");
        assert!(hi_blob.contains("edit"), "{hi_blob}");
        assert!(
            hi_blob.contains("delete") || hi_blob.contains("x "),
            "{hi_blob}"
        );
        assert!(blob.contains("u sessions"));
        assert!(blob.contains("y copy"));
        assert!(blob.contains("enter open"));
        assert!(
            !blob.contains("notes"),
            "Overview footer stays one row: Notes is Shift+N in help, not the rail: {blob}"
        );
        assert!(!blob.contains("j down"), "{blob}");
        assert!(!blob.contains("g timeline"), "{blob}");
        assert!(
            !browse.footer_hints().iter().any(|h| h.starts_with("h ")),
            "{blob}"
        );
        assert!(
            browse.footer_hints().len() <= 8,
            "Overview hint rail is one row: {:?}",
            browse.footer_hints()
        );

        let diff = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: true,
            tab: Tab::Diff,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let dblob = diff.footer_hints().join("  ·  ");
        assert!(dblob.contains("/ search"), "{dblob}");
        assert!(dblob.contains("j down"), "{dblob}");
        assert!(
            diff.footer_hints().iter().any(|h| h.starts_with("h ")),
            "{dblob}"
        );

        let turns = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Turns,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let tblob = turns.footer_hints().join("  ·  ");
        assert!(tblob.contains("j down"), "{tblob}");
        assert!(tblob.contains("g go"), "{tblob}");

        let report = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Notes,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let fblob = report.footer_hints().join("  ·  ");
        assert!(!fblob.contains("enter open"), "{fblob}");
        assert!(fblob.contains("j down"), "{fblob}");
        assert!(
            !report.footer_hints().iter().any(|h| h.starts_with("h ")),
            "{fblob}"
        );

        let detail = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: true,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let blob = detail.footer_hints().join("  ·  ");
        assert!(blob.contains("esc timeline"));
        assert!(blob.contains("j step"));

        let scoped = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let sblob = scoped.footer_hints().join("  ·  ");
        assert!(
            scoped.footer_hints().iter().any(|h| h.starts_with("h ")),
            "{sblob}"
        );
        assert!(sblob.contains("l ") || sblob.contains("right"), "{sblob}");
    }

    #[test]
    fn footer_table_help_open_is_close_only() {
        let hints = footer_table(KeyScope {
            browse: true,
            help_open: true,
            timeline_detail: true,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        })
        .footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("? help"));
        assert!(blob.contains("esc close"));
        assert_eq!(hints.len(), 2);
    }

    #[test]
    fn footer_table_awaiting_shows_follow_up_and_done() {
        let hints = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        })
        .footer_hints();
        let blob = hints.join("  ·  ");
        assert!(!blob.contains("follow"), "{blob}");
        assert!(!blob.contains(" done"), "{blob}");
        let sheet = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        assert!(sheet.get("session.follow").is_none());
        assert!(sheet.get("session.done").is_none());
        assert!(sheet.get("pane.notes").is_some());
        assert!(sheet.get("events.next_turn").is_some());
        assert!(sheet.get("events.scope_next").is_some());
        assert!(sheet.get("events.all_turns").is_some());
        assert!(sheet.get("turns.timeline").is_none());
        assert!(sheet.conflicts().is_empty());
        let turns_help = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Turns,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        assert!(turns_help.get("turns.timeline").is_some());
        assert!(turns_help.get("events.next_turn").is_none());
        assert!(help_table(picker()).get("pane.notes").is_none());
    }

    #[test]
    fn help_table_lists_arrow_keys_for_turn_step() {
        let sheet = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        let prev = sheet.get("events.prev_turn").expect("prev turn");
        let prev_keys = prev.tooltip.as_deref().unwrap_or("");
        assert!(prev_keys.contains("h"), "{prev_keys}");
        assert!(prev_keys.contains("left"), "{prev_keys}");
        let next = sheet.get("events.next_turn").expect("next turn");
        let next_keys = next.tooltip.as_deref().unwrap_or("");
        assert!(next_keys.contains('l'), "{next_keys}");
        assert!(next_keys.contains("right"), "{next_keys}");
        let locked = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: true,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        assert!(locked.get("events.next_turn").is_some());
        assert!(locked.get("events.prev_turn").is_some());
        assert!(locked.get("events.scope_next").is_none());
        assert!(locked.get("events.all_turns").is_some());
    }

    #[test]
    fn help_table_lists_keys_the_pane_can_run() {
        let overview = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        assert!(overview.get("session.open").is_some());
        assert!(overview.get("list.down").is_none());
        assert!(overview.get("pane.next").is_some());

        let notes = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Notes,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        assert!(notes.get("session.open").is_some());
        assert!(notes.get("list.down").is_some());
        assert!(notes.get("list.up").is_some());
        assert!(notes.get("session.open").is_some());
        assert!(notes.get("session.note_edit").is_none());
        assert!(notes.get("session.delete").is_some());
        assert!(notes.get("events.next_turn").is_none());
        assert!(notes.get("pane.next").is_some());

        let sheet = help_table(picker());
        assert!(sheet.get("session.open").is_some());
        assert!(sheet.get("list.down").is_some());
        assert!(sheet.get("pane.next").is_none());
        let awaiting_picker = help_table(KeyScope {
            browse: false,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        });
        assert!(awaiting_picker.get("session.follow").is_none());
        assert!(awaiting_picker.get("session.done").is_none());
    }

    #[test]
    fn armed_leader_shows_in_footer() {
        let overlay = crate::keys::KeyOverlay::parse(
            "leader = \";\"\n[home]\n\"search.focus\" = \"leader+slash\"\n\"list.down\" = \"n\"\n[browser]\n\"edit.copy\" = \"z\"\n",
        )
        .expect("leader overlay");
        let scope = KeyScope {
            browse: false,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: true,
            note_focused: false,
            notes_composing: false,
        };
        let hints = footer_table_for(scope, &overlay).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(
            blob.contains(';') || blob.to_lowercase().contains("leader"),
            "{blob}"
        );
        let help = help_table_for(scope, &overlay);
        assert!(help.get("leader.prefix").is_some());
    }

    #[test]
    fn sequence_actions_show_leader_plus_letter() {
        let overlay = crate::keys::KeyOverlay::parse(concat!(
            "leader = \";\"\n",
            "[home]\n",
            "\"sessions.home\" = \"leader+u\"\n",
            "[browser]\n",
            "\"edit.copy\" = \"leader+n\"\n",
        ))
        .expect("sequence overlay");
        let scope = KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        };
        let hints = footer_table_for(scope, &overlay).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("; u"), "{blob}");
        let help = help_table_for(scope, &overlay);
        let home = help.get("sessions.home").expect("sessions.home");
        let chord = home.shortcut.as_ref().map(ToString::to_string);
        assert!(
            chord.as_deref() == Some("; u") || chord.as_deref() == Some(";u"),
            "{chord:?}"
        );
    }

    #[test]
    fn overlay_remap_shows_in_footer_and_help() {
        let overlay = crate::keys::KeyOverlay::parse(
            "[home]\n\"list.down\" = \"n\"\n[browser]\n\"edit.copy\" = \"z\"\n",
        )
        .expect("valid overlay");
        let hints = footer_table_for(picker(), &overlay).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("n down"), "{blob}");
        assert!(!blob.contains("j down"), "{blob}");
        let awaiting = KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
            note_focused: false,
            notes_composing: false,
        };
        let help = help_table_for(awaiting, &overlay);
        let hints = help.footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("n "), "{blob}");
    }
}
