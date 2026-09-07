//! Claude Code jsonl store.

use crate::event::{Event, EventType, ListMeta, ListStatus, SessionLocator};
use crate::jsonl::{self, JsonlRow};
use crate::store::Store;
use crate::text;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

pub struct Claude;

const AGENT_TOOLS: &[&str] = &["Agent", "Task", "TaskCreate", "agent", "task"];

fn collect(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for raw in roots {
        if raw.is_file() && raw.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            if !raw
                .parent()
                .is_some_and(|p| p.file_name().and_then(|n| n.to_str()) == Some("subagents"))
            {
                out.push(raw.clone());
            }
        } else if raw.is_dir() {
            for file in crate::walk::find_files(raw, ".jsonl", "") {
                if file
                    .parent()
                    .is_some_and(|p| p.file_name().and_then(|n| n.to_str()) == Some("subagents"))
                {
                    continue;
                }
                out.push(file);
            }
        }
    }
    out
}

fn sid_of(path: &Path) -> String {
    path.file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string()
}

fn is_branch_entry(value: &Value) -> bool {
    matches!(
        text::field_str(value, "type").as_str(),
        "user" | "assistant"
    ) && !text::field_str(value, "uuid").is_empty()
}

/// Last `uuid` row, then walk `parentUuid` to root. Linear files stay in order.
fn leaf_path(rows: &[JsonlRow]) -> Vec<&JsonlRow> {
    let mut by_id: HashMap<String, usize> = HashMap::new();
    for (i, row) in rows.iter().enumerate() {
        if !is_branch_entry(&row.value) {
            continue;
        }
        let id = text::field_str(&row.value, "uuid");
        if !id.is_empty() {
            by_id.insert(id, i);
        }
    }
    if by_id.is_empty() {
        return rows.iter().collect();
    }
    let linked = rows.iter().any(|row| {
        is_branch_entry(&row.value) && !text::field_str(&row.value, "parentUuid").is_empty()
    });
    if !linked {
        return rows.iter().collect();
    }
    let Some(start) = rows.iter().enumerate().rev().find_map(|(i, row)| {
        if is_branch_entry(&row.value) && !text::field_str(&row.value, "uuid").is_empty() {
            Some(i)
        } else {
            None
        }
    }) else {
        return rows.iter().collect();
    };
    let mut chain = Vec::new();
    let mut cur = Some(start);
    let mut seen = HashSet::new();
    while let Some(i) = cur {
        if !seen.insert(i) {
            break;
        }
        chain.push(&rows[i]);
        let parent = text::field_str(&rows[i].value, "parentUuid");
        if parent.is_empty() {
            break;
        }
        cur = by_id.get(&parent).copied();
    }
    chain.reverse();
    chain
}

fn usage_tokens(msg: &Value) -> Option<i64> {
    let usage = msg.get("usage")?;
    if !usage.is_object() {
        return None;
    }
    let input = usage
        .get("input_tokens")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let cache_read = usage
        .get("cache_read_input_tokens")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let output = usage
        .get("output_tokens")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    Some(input + cache_read + output)
}

fn last_usage(rows: &[&JsonlRow]) -> Option<i64> {
    for row in rows.iter().rev() {
        if text::field_str(&row.value, "type") != "assistant" {
            continue;
        }
        let msg = row.value.get("message")?;
        if let Some(n) = usage_tokens(msg) {
            return Some(n);
        }
    }
    None
}

fn is_tool_result_user(msg: &Value) -> bool {
    msg.get("content")
        .and_then(|v| v.as_array())
        .is_some_and(|blocks| {
            blocks
                .iter()
                .any(|b| text::field_str(b, "type") == "tool_result")
        })
}

fn timeline_rows(rows: &[JsonlRow]) -> Vec<Event> {
    let path = leaf_path(rows);
    let mut names = HashMap::new();
    let mut children = HashMap::new();
    for row in &path {
        let typ = text::field_str(&row.value, "type");
        if typ == "user" {
            let tur = row
                .value
                .get("toolUseResult")
                .cloned()
                .unwrap_or(Value::Null);
            let agent = text::field_str(&tur, "agentId");
            if !agent.is_empty() {
                let atyp = text::field_str(&tur, "agentType");
                let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
                if let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) {
                    for block in blocks {
                        if text::field_str(block, "type") == "tool_result" {
                            let call_id = text::field_str(block, "tool_use_id");
                            if !call_id.is_empty() {
                                children.insert(call_id, (agent.clone(), atyp.clone()));
                            }
                        }
                    }
                }
            }
        }
        if typ != "assistant" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        if let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) {
            for block in blocks {
                if text::field_str(block, "type") != "tool_use" {
                    continue;
                }
                let id = text::field_str(block, "id");
                let name = text::field_str(block, "name");
                if !id.is_empty() {
                    names.insert(id.clone(), name.clone());
                }
            }
        }
    }
    let mut events = Vec::new();
    let mut turn = 0i32;
    for row in path {
        let typ = text::field_str(&row.value, "type");
        let ts = text::field_i64(&row.value, "timestamp");
        if typ == "user" {
            let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
            if is_tool_result_user(&msg) {
                events.push(tool_result(&row.value, ts, &names, &row.raw));
            } else {
                let text_body = text::text_of(msg.get("content").unwrap_or(&Value::Null));
                if !text_body.trim().is_empty() {
                    let mut start = Event::new(EventType::TurnStarted)
                        .with_ts(ts)
                        .with_content(format!("turn_number={turn}"))
                        .with_raw(&row.raw);
                    start.turn_number = Some(turn);
                    events.push(start);
                    events.push(
                        Event::new(EventType::UserMessageChunk)
                            .with_ts(ts)
                            .with_content(text_body)
                            .with_raw(&row.raw),
                    );
                    turn += 1;
                }
            }
        } else if typ == "assistant" {
            events.extend(assistant(&row.value, ts, &children, &row.raw));
        }
    }
    events
}

fn assistant(
    row: &Value,
    ts: Option<i64>,
    children: &std::collections::HashMap<String, (String, String)>,
    raw: &str,
) -> Vec<Event> {
    let mut out = Vec::new();
    let msg = row.get("message").cloned().unwrap_or(Value::Null);
    let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) else {
        return out;
    };
    for block in blocks {
        let kind = text::field_str(block, "type");
        if kind == "thinking" {
            let content = block
                .get("thinking")
                .or_else(|| block.get("text"))
                .map(text::as_str)
                .unwrap_or_default();
            out.push(
                Event::new(EventType::AgentThoughtChunk)
                    .with_ts(ts)
                    .with_content(content)
                    .with_raw(raw),
            );
        } else if kind == "text" {
            out.push(
                Event::new(EventType::AgentMessageChunk)
                    .with_ts(ts)
                    .with_content(text::field_str(block, "text"))
                    .with_raw(raw),
            );
        } else if kind == "tool_use" {
            let name = {
                let n = text::field_str(block, "name");
                if n.is_empty() {
                    "tool".into()
                } else {
                    n
                }
            };
            let call_id = text::field_str(block, "id");
            let input = block.get("input").cloned().unwrap_or(Value::Null);
            let raw_args = serde_json::to_string(&input).unwrap_or_default();
            let mut ev = Event::new(EventType::ToolCall)
                .with_ts(ts)
                .with_content(name.clone())
                .with_raw(raw_args);
            ev.tool_name = name.clone();
            ev.tool_call_id = call_id.clone();
            out.push(ev);
            if AGENT_TOOLS.contains(&name.as_str()) {
                let (child, typ) = children.get(&call_id).cloned().unwrap_or_else(|| {
                    (
                        text::field_str(&input, "agentId"),
                        text::field_str(&input, "subagent_type"),
                    )
                });
                if !child.is_empty() {
                    let desc = text::field_str(&input, "description");
                    let mut spawn = Event::new(EventType::SubagentSpawned)
                        .with_ts(ts)
                        .with_content(format!("spawned {typ}: {desc}").trim())
                        .with_raw(raw);
                    spawn.child_session_id = child;
                    spawn.subagent_type = typ;
                    spawn.description = desc;
                    out.push(spawn);
                }
            }
        }
    }
    out
}

fn tool_result(
    row: &Value,
    ts: Option<i64>,
    names: &std::collections::HashMap<String, String>,
    raw: &str,
) -> Event {
    let msg = row.get("message").cloned().unwrap_or(Value::Null);
    let mut call_id = String::new();
    let mut body = String::new();
    if let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) {
        for block in blocks {
            if text::field_str(block, "type") != "tool_result" {
                continue;
            }
            call_id = text::field_str(block, "tool_use_id");
            body = text::text_of(block.get("content").unwrap_or(&Value::Null));
        }
    }
    let tur = row.get("toolUseResult").cloned().unwrap_or(Value::Null);
    if body.is_empty() {
        body = text::text_of(
            tur.get("content")
                .or_else(|| tur.get("stdout"))
                .unwrap_or(&tur),
        );
    }
    let child = text::field_str(&tur, "agentId");
    let typ = text::field_str(&tur, "agentType");
    let name = names
        .get(&call_id)
        .cloned()
        .unwrap_or_else(|| "tool".into());
    if !child.is_empty() {
        let mut ev = Event::new(EventType::SubagentFinished)
            .with_ts(ts)
            .with_content(body.chars().take(400).collect::<String>())
            .with_raw(raw);
        ev.tool_name = name;
        ev.child_session_id = child;
        ev.subagent_type = typ;
        ev.tool_call_id = call_id;
        return ev;
    }
    let mut ev = Event::new(EventType::ToolCallUpdate)
        .with_ts(ts)
        .with_content(body)
        .with_raw(raw);
    ev.tool_name = name;
    ev.tool_call_id = call_id;
    ev
}

impl Store for Claude {
    fn id(&self) -> &'static str {
        "claude"
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
                    harness: "claude".into(),
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
        timeline_rows(records)
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("claude session not found: {session_id}"));
        }
        let rows = jsonl::window(locator);
        if rows.is_empty() {
            return Err(format!("claude session not found: {session_id}"));
        }
        let mut meta = meta_from_window(&rows, locator, session_id);
        meta.num_events = self.event_count(locator, session_id);
        Ok(meta)
    }

    fn detail_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("claude session not found: {session_id}"));
        }
        let rows = jsonl::cached_records(locator, None);
        if rows.is_empty() {
            return Err(format!("claude session not found: {session_id}"));
        }
        let events = self.events(&rows);
        let path = leaf_path(&rows);
        let mut meta = meta_from_window(&rows, locator, session_id);
        meta.num_events = events.len() as u32;
        meta.context_tokens_used = last_usage(&path);
        Ok(meta)
    }
}

const CHROME_TYPES: &[&str] = &[
    "queue-operation",
    "last-prompt",
    "atis-latch",
    "ai-title",
    "attachment",
    "file-history-snapshot",
    "progress",
    "system",
    "mode",
    "cost-state",
    "permission-mode",
];

fn title_from_rows(rows: &[JsonlRow]) -> String {
    for row in rows {
        if text::field_str(&row.value, "type") == "ai-title" {
            let title = text::field_str(&row.value, "aiTitle");
            if !title.is_empty() {
                return text::first_line(&title, 80);
            }
        }
    }
    for row in rows {
        if text::field_str(&row.value, "type") != "user" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        if is_tool_result_user(&msg) {
            continue;
        }
        let title = text::first_line(
            &text::text_of(msg.get("content").unwrap_or(&Value::Null)),
            80,
        );
        if !title.is_empty() {
            return title;
        }
    }
    String::new()
}

fn turn_outcome(rows: &[JsonlRow]) -> String {
    let mut last: Option<&JsonlRow> = None;
    for row in rows {
        let typ = text::field_str(&row.value, "type");
        if CHROME_TYPES.contains(&typ.as_str()) {
            continue;
        }
        if typ == "user" || typ == "assistant" {
            last = Some(row);
        }
    }
    let Some(row) = last else {
        return String::new();
    };
    let typ = text::field_str(&row.value, "type");
    if typ == "user" {
        return ListStatus::Idle.as_str().into();
    }
    let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
    let stop = text::field_str(&msg, "stop_reason");
    let compact = stop.to_ascii_lowercase().replace('_', "");
    if compact == "tooluse" {
        return ListStatus::Running.as_str().into();
    }
    ListStatus::from_token(&stop).as_str().into()
}

fn count_children(path: &Path, session_id: &str) -> u32 {
    let folder = path
        .parent()
        .unwrap_or(path)
        .join(session_id)
        .join("subagents");
    let Ok(entries) = std::fs::read_dir(folder) else {
        return 0;
    };
    entries
        .flatten()
        .filter(|e| e.path().extension().and_then(|s| s.to_str()) == Some("jsonl"))
        .count() as u32
}

fn meta_from_window(rows: &[JsonlRow], path: &Path, session_id: &str) -> ListMeta {
    let mut sid = session_id.to_string();
    let mut created = String::new();
    let mut last_ts = String::new();
    let mut version = String::new();
    let mut model = String::new();
    let mut cwd = String::new();
    let mut tools = 0u32;
    for row in rows {
        let ts = text::field_iso(&row.value, "timestamp");
        if !ts.is_empty() {
            last_ts = ts;
            if created.is_empty() {
                created = last_ts.clone();
            }
        }
        if sid.is_empty() {
            sid = text::field_str(&row.value, "sessionId");
        }
        if version.is_empty() {
            version = text::field_str(&row.value, "version");
        }
        if cwd.is_empty() {
            cwd = text::field_str(&row.value, "cwd");
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        if model.is_empty() {
            model = text::field_str(&msg, "model");
        }
        if let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) {
            tools += blocks
                .iter()
                .filter(|b| text::field_str(b, "type") == "tool_use")
                .count() as u32;
        }
    }
    let kids = count_children(path, &sid);
    ListMeta {
        session_id: sid,
        locator: path.to_path_buf(),
        model_id: if model.is_empty() {
            "unknown".into()
        } else {
            model
        },
        title: title_from_rows(rows),
        created_at: created.clone(),
        updated_at: last_ts.clone(),
        duration_seconds: text::duration_secs(
            text::epoch_secs(&Value::String(created)),
            text::epoch_secs(&Value::String(last_ts)),
        ),
        tool_call_count: tools,
        turn_outcome: turn_outcome(rows),
        harness: "claude".into(),
        harness_version: version,
        run_dir: cwd,
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
            "../tests/fixtures/harness/claude/projects/-tmp-probe-ws/aaaaaaaa-bbbb-4ccc-8ddd-000000000001.jsonl",
        );
        let meta = Claude
            .list_meta(&path, "aaaaaaaa-bbbb-4ccc-8ddd-000000000001")
            .unwrap();
        assert_eq!(meta.title, "Reply with CLAUDE_PROBE_OK");
        assert_eq!(meta.turn_outcome, "complete");
        assert_eq!(meta.model_id, "claude-opus-5");
    }

    #[test]
    fn leaf_path_drops_abandoned_branch() {
        let rows = vec![
            JsonlRow {
                raw: r#"{"type":"user","uuid":"u1","parentUuid":"","message":{"role":"user","content":"root"}}"#.into(),
                value: serde_json::json!({"type":"user","uuid":"u1","parentUuid":"","message":{"role":"user","content":"root"}}),
            },
            JsonlRow {
                raw: r#"{"type":"assistant","uuid":"old","parentUuid":"u1","message":{"role":"assistant","content":[{"type":"text","text":"abandoned"}]}}"#.into(),
                value: serde_json::json!({"type":"assistant","uuid":"old","parentUuid":"u1","message":{"role":"assistant","content":[{"type":"text","text":"abandoned"}]}}),
            },
            JsonlRow {
                raw: r#"{"type":"user","uuid":"u2","parentUuid":"u1","message":{"role":"user","content":"new"}}"#.into(),
                value: serde_json::json!({"type":"user","uuid":"u2","parentUuid":"u1","message":{"role":"user","content":"new"}}),
            },
        ];
        let path: Vec<String> = leaf_path(&rows)
            .iter()
            .map(|r| text::field_str(&r.value, "uuid"))
            .collect();
        assert_eq!(path, ["u1", "u2"]);
    }
}
