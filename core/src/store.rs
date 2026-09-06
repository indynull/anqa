//! One store trait. Every harness implements the same ingest path.

use crate::event::{Event, FileStamp, ListMeta, SessionLocator};
use crate::jsonl::JsonlRow;
use crate::text;
use std::path::{Path, PathBuf};

/// One native record. `raw` is the original line or row text.
pub type Record = JsonlRow;

pub trait Store: Send + Sync {
    fn id(&self) -> &'static str;

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator>;

    /// Native records for this session, in store order.
    fn records(&self, locator: &Path, session_id: &str) -> Result<Vec<Record>, String>;

    /// Map native records to typed events. Do not index; [`timeline`] does.
    fn events(&self, records: &[Record]) -> Vec<Event>;

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        Ok(ListMeta::for_session(self.id(), locator, session_id))
    }

    /// Full-file metadata for the browser / overview. Defaults to [`Store::list_meta`].
    fn detail_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        self.list_meta(locator, session_id)
    }

    /// Timeline event count for the catalog Events column.
    fn event_count(&self, locator: &Path, session_id: &str) -> u32 {
        self.records(locator, session_id)
            .map(|records| self.events(&records).len() as u32)
            .unwrap_or(0)
    }

    fn stamp(&self, locator: &Path, session_id: &str) -> FileStamp {
        let _ = session_id;
        crate::jsonl::file_stamp(locator)
    }

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
        let records = self.records(locator, session_id)?;
        let mut events = self.events(&records);
        Event::carry_turn_numbers(&mut events);
        text::index_events(&mut events);
        Ok(events)
    }
}

/// Read a jsonl file as records, or error when the locator is not a file.
pub fn jsonl_records(
    locator: &Path,
    harness: &str,
    session_id: &str,
) -> Result<Vec<Record>, String> {
    if !locator.is_file() {
        return Err(format!("{harness} session not found: {session_id}"));
    }
    Ok(crate::jsonl::cached_records(locator, None))
}

pub fn by_id(id: &str) -> Option<&'static dyn Store> {
    match id {
        "pi" => Some(&crate::stores::pi::Pi),
        "claude" => Some(&crate::stores::claude::Claude),
        "codex" => Some(&crate::stores::codex::Codex),
        "cursor" => Some(&crate::stores::cursor::Cursor),
        "gemini" => Some(&crate::stores::gemini::Gemini),
        "grok" => Some(&crate::stores::grok::Grok),
        "opencode" => Some(&crate::stores::opencode::OpenCode),
        "copilot" => Some(&crate::stores::copilot::Copilot),
        "antigravity" => Some(&crate::stores::antigravity::Antigravity),
        _ => None,
    }
}

pub fn ids() -> &'static [&'static str] {
    &[
        "antigravity",
        "claude",
        "codex",
        "copilot",
        "cursor",
        "gemini",
        "grok",
        "opencode",
        "pi",
    ]
}
