//! Codex rollout jsonl.

use crate::event::{Event, EventType, ListMeta, ListStatus, SessionLocator};
use crate::jsonl::{self, JsonlRow};
use crate::store::Store;
use crate::text;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Codex;

fn collect(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for raw in roots {
        if raw.is_file()
            && raw
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("rollout-") && n.ends_with(".jsonl"))
        {
            out.push(raw.clone());
        } else if raw.is_dir() {
            out.extend(crate::walk::find_files(raw, ".jsonl", "rollout-"));
        }
    }
    out
}

fn sid_of(path: &Path) -> String {
    jsonl::first_object(path)
        .and_then(|row| {
            let pl = row.value.get("payload").cloned().unwrap_or(Value::Null);
            let id = text::field_str(&pl, "id");
            if !id.is_empty() {
                return Some(id);
            }
            let id = text::field_str(&pl, "session_id");
            if !id.is_empty() {
                Some(id)
            } else {
                None
            }
        })
        .unwrap_or_default()
}

fn is_env_context(text: &str) -> bool {
    text.trim_start().starts_with("<environment_context>")
}

fn blocks_text(content: &Value, kind: &str) -> String {
    let Some(items) = content.as_array() else {
        return text::text_of(content);
    };
    let mut bits = Vec::new();
    for item in items {
        if text::field_str(item, "type") == kind {
            bits.push(text::field_str(item, "text"));
        }
    }
    bits.join("\n")
}

fn from_row(row: &JsonlRow) -> Vec<Event> {
    let typ = text::field_str(&row.value, "type");
    let ts = text::field_i64(&row.value, "timestamp");
    let pl = row.value.get("payload").cloned().unwrap_or(Value::Null);
    if typ == "event_msg" {
        let kind = text::field_str(&pl, "type");
        if kind == "task_started" {
            return vec![Event::new(EventType::TurnStarted)
                .with_ts(ts)
                .with_raw(&row.raw)];
        }
        if kind == "task_complete" {
            return vec![Event::new(EventType::TurnCompleted)
                .with_ts(ts)
                .with_raw(&row.raw)];
        }
        if kind == "turn_aborted" {
            return vec![Event::new(EventType::TurnEnded)
                .with_ts(ts)
                .with_content(text::field_str(&pl, "reason"))
                .with_raw(&row.raw)];
        }
        if kind == "item_completed" {
            let item = pl.get("item").cloned().unwrap_or(Value::Null);
            if text::field_str(&item, "type") == "SubAgentActivity" {
                return subagent_item(&item, ts, &row.raw);
            }
        }
        return Vec::new();
    }
    if typ != "response_item" {
        return Vec::new();
    }
    let kind = text::field_str(&pl, "type");
    if kind == "message" {
        let role = text::field_str(&pl, "role");
        if role == "user" {
            let body = blocks_text(pl.get("content").unwrap_or(&Value::Null), "input_text");
            if is_env_context(&body) {
                return Vec::new();
            }
            return vec![Event::new(EventType::UserMessageChunk)
                .with_ts(ts)
                .with_content(body)
                .with_raw(&row.raw)];
        }
        if role == "assistant" {
            let body = blocks_text(pl.get("content").unwrap_or(&Value::Null), "output_text");
            return vec![Event::new(EventType::AgentMessageChunk)
                .with_ts(ts)
                .with_content(body)
                .with_raw(&row.raw)];
        }
    }
    if kind == "custom_tool_call" || kind == "function_call" {
        let name = text::field_str(&pl, "name");
        let input = pl.get("input").cloned().unwrap_or(Value::Null);
        let raw_args = if input.is_string() {
            serde_json::to_string(&serde_json::json!({"command": text::as_str(&input)}))
                .unwrap_or_default()
        } else {
            serde_json::to_string(&input).unwrap_or_default()
        };
        let mut ev = Event::new(EventType::ToolCall)
            .with_ts(ts)
            .with_raw(raw_args);
        ev.tool_name = name;
        ev.tool_call_id = {
            let a = text::field_str(&pl, "call_id");
            if a.is_empty() {
                text::field_str(&pl, "id")
            } else {
                a
            }
        };
        return vec![ev];
    }
    if kind == "custom_tool_call_output" || kind == "function_call_output" {
        let mut ev = Event::new(EventType::ToolCallUpdate)
            .with_ts(ts)
            .with_content(text::text_of(pl.get("output").unwrap_or(&Value::Null)))
            .with_raw(&row.raw);
        ev.tool_call_id = text::field_str(&pl, "call_id");
        return vec![ev];
    }
    Vec::new()
}

fn subagent_item(item: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let kind = text::field_str(item, "kind");
    let child = text::field_str(item, "agent_thread_id");
    let path = text::field_str(item, "agent_path");
    let typ = path.rsplit('/').next().unwrap_or("").to_string();
    if kind == "started" {
        let mut ev = Event::new(EventType::SubagentSpawned)
            .with_ts(ts)
            .with_content(if path.is_empty() {
                typ.clone()
            } else {
                path.clone()
            })
            .with_raw(raw);
        ev.child_session_id = child;
        ev.subagent_type = typ;
        ev.description = path;
        return vec![ev];
    }
    if kind == "completed" || kind == "interrupted" {
        let mut ev = Event::new(EventType::SubagentFinished)
            .with_ts(ts)
            .with_content(if path.is_empty() { typ } else { path })
            .with_raw(raw);
        ev.child_session_id = child;
        return vec![ev];
    }
    Vec::new()
}

impl Store for Codex {
    fn id(&self) -> &'static str {
        "codex"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        collect(roots)
            .into_iter()
            .filter_map(|file| {
                let sid = sid_of(&file);
                if sid.is_empty() {
                    return None;
                }
                Some(SessionLocator {
                    harness: "codex".into(),
                    session_id: sid,
                    locator: file,
                    cwd: String::new(),
                })
            })
            .collect()
    }

    fn records(
        &self,
        locator: &Path,
        session_id: &str,
    ) -> Result<Vec<crate::store::Record>, String> {
        crate::store::jsonl_records(locator, self.id(), session_id)
    }

    fn events(&self, records: &[crate::store::Record]) -> Vec<Event> {
        let mut out = Vec::new();
        let mut turn = 0i32;
        for rec in records {
            for ev in from_row(rec) {
                if matches!(ev.event_type, EventType::UserMessageChunk) {
                    let mut start = Event::new(EventType::TurnStarted)
                        .with_ts(ev.timestamp)
                        .with_content(format!("turn_number={turn}"))
                        .with_raw(ev.raw.clone());
                    start.turn_number = Some(turn);
                    out.push(start);
                    let mut user = ev;
                    user.turn_number = Some(turn);
                    out.push(user);
                    turn += 1;
                } else {
                    out.push(ev);
                }
            }
        }
        out
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("codex session not found: {session_id}"));
        }
        let rows = jsonl::window(locator);
        if rows.is_empty() {
            return Err(format!("codex session not found: {session_id}"));
        }
        let mut meta = meta_from_window(&rows, locator, session_id);
        meta.num_events = self.event_count(locator, session_id);
        Ok(meta)
    }
}

const TURN_SIGNALS: &[&str] = &["task_started", "task_complete", "turn_aborted"];

fn payload(row: &Value) -> Value {
    row.get("payload").cloned().unwrap_or(Value::Null)
}

fn meta_row(rows: &[JsonlRow]) -> Value {
    for row in rows {
        if text::field_str(&row.value, "type") == "session_meta" {
            return payload(&row.value);
        }
    }
    Value::Null
}

fn first_user_title(rows: &[JsonlRow]) -> String {
    for row in rows {
        if text::field_str(&row.value, "type") != "response_item" {
            continue;
        }
        let pl = payload(&row.value);
        if text::field_str(&pl, "type") != "message" || text::field_str(&pl, "role") != "user" {
            continue;
        }
        let body = blocks_text(pl.get("content").unwrap_or(&Value::Null), "input_text");
        if body.is_empty() || is_env_context(&body) {
            continue;
        }
        return text::first_line(&body, 120);
    }
    String::new()
}

fn model_from_rows(rows: &[JsonlRow]) -> String {
    for row in rows.iter().rev() {
        let typ = text::field_str(&row.value, "type");
        let pl = payload(&row.value);
        if typ == "turn_context" {
            let mid = text::field_str(&pl, "model");
            if !mid.is_empty() {
                return mid;
            }
        }
        if typ == "event_msg" && text::field_str(&pl, "type") == "thread_settings_applied" {
            let settings = pl.get("thread_settings").cloned().unwrap_or(Value::Null);
            let mid = text::field_str(&settings, "model");
            if !mid.is_empty() {
                return mid;
            }
        }
    }
    String::new()
}

fn last_turn_signal(rows: &[JsonlRow]) -> String {
    let mut last = String::new();
    for row in rows {
        if text::field_str(&row.value, "type") != "event_msg" {
            continue;
        }
        let typ = text::field_str(&payload(&row.value), "type");
        if TURN_SIGNALS.contains(&typ.as_str()) {
            last = typ;
        }
    }
    last
}

fn count_tools(rows: &[JsonlRow]) -> u32 {
    rows.iter()
        .filter(|row| {
            if text::field_str(&row.value, "type") != "response_item" {
                return false;
            }
            let pt = text::field_str(&payload(&row.value), "type");
            pt == "custom_tool_call" || pt == "function_call"
        })
        .count() as u32
}

fn count_subagents(rows: &[JsonlRow]) -> u32 {
    rows.iter()
        .filter(|row| {
            if text::field_str(&row.value, "type") != "event_msg" {
                return false;
            }
            let pl = payload(&row.value);
            if text::field_str(&pl, "type") != "item_completed" {
                return false;
            }
            let item = pl.get("item").cloned().unwrap_or(Value::Null);
            text::field_str(&item, "type") == "SubAgentActivity"
                && text::field_str(&item, "kind") == "started"
        })
        .count() as u32
}

fn meta_from_window(rows: &[JsonlRow], path: &Path, session_id: &str) -> ListMeta {
    let header = meta_row(rows);
    let mut sid = text::field_str(&header, "session_id");
    if sid.is_empty() {
        sid = text::field_str(&header, "id");
    }
    if sid.is_empty() {
        sid = session_id.to_string();
    }
    let created = {
        let ts = text::field_iso(&header, "timestamp");
        if ts.is_empty() {
            rows.first()
                .map(|r| text::field_iso(&r.value, "timestamp"))
                .unwrap_or_default()
        } else {
            ts
        }
    };
    let mut last_ts = created.clone();
    for row in rows {
        let ts = text::field_iso(&row.value, "timestamp");
        if !ts.is_empty() {
            last_ts = ts;
        }
    }
    let kids = count_subagents(rows);
    let model = model_from_rows(rows);
    ListMeta {
        session_id: sid,
        locator: path.to_path_buf(),
        model_id: if model.is_empty() {
            "unknown".into()
        } else {
            model
        },
        title: first_user_title(rows),
        created_at: created.clone(),
        updated_at: last_ts.clone(),
        duration_seconds: text::duration_secs(
            text::epoch_secs(&Value::String(created)),
            text::epoch_secs(&Value::String(last_ts)),
        ),
        tool_call_count: count_tools(rows),
        turn_outcome: {
            let mapped = ListStatus::from_token(&last_turn_signal(rows));
            if mapped != ListStatus::Idle {
                mapped.as_str().into()
            } else {
                String::new()
            }
        },
        harness: "codex".into(),
        harness_version: text::field_str(&header, "cli_version"),
        run_dir: text::field_str(&header, "cwd"),
        num_events: 0,
        has_subagents: kids > 0,
        subagent_count: kids,
        context_tokens_used: None,
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn list_meta_window_title_and_last_turn() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(
            "../tests/fixtures/harness/codex/sessions/2026/08/30/rollout-2026-08-30T12-00-00-aaaaaaaa-1111-4111-8111-000000000001.jsonl",
        );
        let meta = Codex
            .list_meta(&path, "aaaaaaaa-1111-4111-8111-000000000001")
            .unwrap();
        assert_eq!(meta.title, "Reply with CODEX_PROBE_OK");
        assert_eq!(meta.turn_outcome, "complete");
        assert_eq!(meta.model_id, "gpt-5.4");
    }
}
