//! Pi jsonl store (v3 session files; v4 `kind=header` is accepted).

use crate::event::{Event, EventType, ListMeta, ListStatus, SessionLocator};
use crate::jsonl::{self, JsonlRow};
use crate::store::Store;
use crate::text;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

pub struct Pi;

fn session_id_from_name(path: &Path) -> String {
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
    stem.rsplit_once('_')
        .map(|(_, id)| id.to_string())
        .unwrap_or_else(|| stem.to_string())
}

fn is_session_header(val: &Value) -> bool {
    text::field_str(val, "type") == "session" || text::field_str(val, "kind") == "header"
}

fn is_branch_entry(val: &Value) -> bool {
    matches!(
        text::field_str(val, "type").as_str(),
        "message"
            | "model_change"
            | "thinking_level_change"
            | "compaction"
            | "branch_summary"
            | "custom"
            | "custom_message"
            | "label"
            | "session_info"
            | "active_tools_change"
    )
}

fn header_id(path: &Path) -> Option<String> {
    let row = jsonl::first_object(path)?;
    if is_session_header(&row.value) {
        let id = text::field_str(&row.value, "id");
        if !id.is_empty() {
            return Some(id);
        }
    }
    None
}

fn header_created(header: &Value) -> String {
    let iso = text::field_iso(header, "timestamp");
    if !iso.is_empty() {
        return iso;
    }
    header
        .get("createdAt")
        .and_then(text::parse_ts_value)
        .map(text::iso_millis)
        .unwrap_or_default()
}

fn collect(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for raw in roots {
        let files = if raw.is_file() && raw.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            vec![raw.clone()]
        } else if raw.is_dir() {
            crate::walk::find_files(raw, ".jsonl", "")
        } else {
            continue;
        };
        for file in files {
            let key = file.canonicalize().unwrap_or_else(|_| file.clone());
            if seen.insert(key) {
                out.push(file);
            }
        }
    }
    out
}

fn row_ts(row: &JsonlRow, msg: Option<&Value>) -> Option<i64> {
    text::epoch(&row.value).or_else(|| msg.and_then(text::epoch))
}

fn entry_id(val: &Value) -> String {
    text::field_str(val, "id")
}

/// Active branch: last entry with an id, then walk `parentId` to root.
/// Entries with no ids (legacy / padding) stay in file order.
fn leaf_path(rows: &[JsonlRow]) -> Vec<&JsonlRow> {
    let mut by_id: HashMap<String, usize> = HashMap::new();
    for (i, row) in rows.iter().enumerate() {
        if !is_branch_entry(&row.value) {
            continue;
        }
        let id = entry_id(&row.value);
        if !id.is_empty() {
            by_id.insert(id, i);
        }
    }
    if by_id.is_empty() {
        return rows.iter().collect();
    }
    let linked = rows.iter().any(|row| {
        is_branch_entry(&row.value) && !text::field_str(&row.value, "parentId").is_empty()
    });
    if !linked {
        return rows.iter().collect();
    }
    let Some(start) = rows.iter().enumerate().rev().find_map(|(i, row)| {
        if !is_branch_entry(&row.value) || entry_id(&row.value).is_empty() {
            None
        } else {
            Some(i)
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
        let parent = text::field_str(&rows[i].value, "parentId");
        if parent.is_empty() {
            break;
        }
        cur = by_id.get(&parent).copied();
    }
    chain.reverse();
    chain
}

fn timeline_rows(rows: &[JsonlRow]) -> Vec<Event> {
    let path = leaf_path(rows);
    let mut events = Vec::new();
    let mut turn = 0i32;
    for row in path {
        let kind = text::field_str(&row.value, "type");
        if kind.is_empty() && text::field_str(&row.value, "kind") == "header" {
            continue;
        }
        if kind == "session" {
            continue;
        }
        let msg = row.value.get("message");
        let ts = row_ts(row, msg);
        match kind.as_str() {
            "model_change" => {
                let label = model_label(&row.value);
                if !label.is_empty() {
                    events.push(
                        Event::new(EventType::CurrentModeUpdate)
                            .with_ts(ts)
                            .with_content(label)
                            .with_raw(row.raw.clone()),
                    );
                }
            }
            "thinking_level_change" => {
                let level = text::field_str(&row.value, "thinkingLevel");
                if !level.is_empty() {
                    events.push(
                        Event::new(EventType::CurrentModeUpdate)
                            .with_ts(ts)
                            .with_content(format!("thinking {level}"))
                            .with_raw(row.raw.clone()),
                    );
                }
            }
            "compaction" => events.push(compaction_event(&row.value, ts, &row.raw)),
            "branch_summary" => {
                let summary = text::field_str(&row.value, "summary");
                events.push(
                    Event::new(EventType::SessionRecap)
                        .with_ts(ts)
                        .with_content(summary)
                        .with_raw(row.raw.clone()),
                );
            }
            "custom_message" => {
                if row.value.get("display") == Some(&Value::Bool(true)) {
                    let content = text::text_of(row.value.get("content").unwrap_or(&Value::Null));
                    if !content.trim().is_empty() {
                        events.push(
                            Event::new(EventType::UserMessageChunk)
                                .with_ts(ts)
                                .with_content(content)
                                .with_raw(row.raw.clone()),
                        );
                    }
                }
            }
            "message" => {
                let msg = msg.cloned().unwrap_or(Value::Null);
                let added = message_events(&msg, ts, &row.raw, &mut turn);
                events.extend(added);
            }
            _ => {}
        }
    }
    events
}

fn model_label(val: &Value) -> String {
    let provider = text::field_str(val, "provider");
    let model = text::field_str(val, "modelId");
    match (provider.is_empty(), model.is_empty()) {
        (false, false) => format!("{provider}/{model}"),
        (true, false) => model,
        (false, true) => provider,
        (true, true) => String::new(),
    }
}

fn compaction_event(val: &Value, ts: Option<i64>, raw: &str) -> Event {
    let summary = text::field_str(val, "summary");
    let tokens = text::field_i64(val, "tokensBefore");
    let mut bits = Vec::new();
    if let Some(n) = tokens {
        bits.push(format!("tokens_before={n}"));
    }
    if !summary.is_empty() {
        bits.push(summary.chars().take(400).collect());
    }
    let content = if bits.is_empty() {
        "compaction".into()
    } else {
        bits.join("  ")
    };
    Event::new(EventType::CompactionCheckpoint)
        .with_ts(ts)
        .with_content(content)
        .with_raw(raw)
}

fn message_events(msg: &Value, ts: Option<i64>, raw: &str, turn: &mut i32) -> Vec<Event> {
    let role = text::field_str(msg, "role");
    match role.as_str() {
        "user" => user_events(msg, ts, raw, turn),
        "toolResult" => tool_result_events(msg, ts, raw),
        "assistant" => assistant_events(msg, ts, raw),
        "bashExecution" => bash_execution_events(msg, ts, raw),
        "compactionSummary" => {
            let mut val = msg.clone();
            if val.get("tokensBefore").is_none() {
                if let Some(n) = msg.get("tokensBefore") {
                    val["tokensBefore"] = n.clone();
                }
            }
            if text::field_str(&val, "summary").is_empty() {
                if let Some(s) = msg.get("summary") {
                    val["summary"] = s.clone();
                }
            }
            vec![compaction_event(&val, ts, raw)]
        }
        "branchSummary" => {
            let summary = text::field_str(msg, "summary");
            vec![
                Event::new(EventType::SessionRecap)
                    .with_ts(ts)
                    .with_content(summary)
                    .with_raw(raw),
            ]
        }
        "custom" => {
            if msg.get("display") == Some(&Value::Bool(true)) {
                let content = text::text_of(msg.get("content").unwrap_or(&Value::Null));
                if content.trim().is_empty() {
                    Vec::new()
                } else {
                    vec![
                        Event::new(EventType::UserMessageChunk)
                            .with_ts(ts)
                            .with_content(content)
                            .with_raw(raw),
                    ]
                }
            } else {
                Vec::new()
            }
        }
        _ => Vec::new(),
    }
}

fn user_events(msg: &Value, ts: Option<i64>, raw: &str, turn: &mut i32) -> Vec<Event> {
    let content = msg.get("content").cloned().unwrap_or(Value::Null);
    let text_body = text::text_of(&content);
    let images = image_bytes(&content);
    let mut start = Event::new(EventType::TurnStarted)
        .with_ts(ts)
        .with_content(format!("turn_number={turn}"))
        .with_raw(raw);
    start.turn_number = Some(*turn);
    let mut user = Event::new(EventType::UserMessageChunk)
        .with_ts(ts)
        .with_content(text_body)
        .with_raw(raw);
    user.images = images;
    *turn += 1;
    vec![start, user]
}

fn image_bytes(content: &Value) -> Vec<Vec<u8>> {
    let Some(items) = content.as_array() else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for item in items {
        if text::field_str(item, "type") != "image" {
            continue;
        }
        let data = text::field_str(item, "data");
        if let Some(bytes) = decode_b64(&data) {
            out.push(bytes);
        }
    }
    out
}

fn decode_b64(input: &str) -> Option<Vec<u8>> {
    let s: String = input.chars().filter(|c| !c.is_ascii_whitespace()).collect();
    if s.is_empty() {
        return None;
    }
    fn val(c: u8) -> Option<u8> {
        match c {
            b'A'..=b'Z' => Some(c - b'A'),
            b'a'..=b'z' => Some(c - b'a' + 26),
            b'0'..=b'9' => Some(c - b'0' + 52),
            b'+' | b'-' => Some(62),
            b'/' | b'_' => Some(63),
            b'=' => Some(0),
            _ => None,
        }
    }
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() * 3 / 4);
    let mut i = 0;
    while i < bytes.len() {
        let a = val(bytes[i])?;
        let b = val(*bytes.get(i + 1)?)?;
        let cch = bytes.get(i + 2).copied().unwrap_or(b'=');
        let dch = bytes.get(i + 3).copied().unwrap_or(b'=');
        let c = val(cch)?;
        let d = val(dch)?;
        out.push((a << 2) | (b >> 4));
        if cch != b'=' {
            out.push((b << 4) | (c >> 2));
        }
        if dch != b'=' {
            out.push((c << 6) | d);
        }
        i += 4;
    }
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

fn assistant_events(msg: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let mut out = Vec::new();
    let err = text::field_str(msg, "errorMessage");
    if !err.is_empty() {
        let mut ev = Event::new(EventType::SessionError)
            .with_ts(ts)
            .with_content(err)
            .with_raw(raw);
        ev.is_error = true;
        out.push(ev);
    }
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
            if content.trim().is_empty() {
                continue;
            }
            out.push(
                Event::new(EventType::AgentThoughtChunk)
                    .with_ts(ts)
                    .with_content(content)
                    .with_raw(raw),
            );
        } else if kind == "text" {
            let text_body = text::field_str(block, "text");
            if text_body.is_empty() {
                continue;
            }
            out.push(
                Event::new(EventType::AgentMessageChunk)
                    .with_ts(ts)
                    .with_content(text_body)
                    .with_raw(raw),
            );
        } else if kind == "toolCall" {
            out.extend(tool_call_events(block, ts, raw));
        }
    }
    out
}

fn tool_call_events(block: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let mut out = Vec::new();
    let name = {
        let n = text::field_str(block, "name");
        if n.is_empty() {
            "tool".into()
        } else {
            n
        }
    };
    let call_id = text::field_str(block, "id");
    let args = block.get("arguments").cloned().unwrap_or(Value::Null);
    let raw_args = serde_json::to_string(&args).unwrap_or_default();
    let mut ev = Event::new(EventType::ToolCall)
        .with_ts(ts)
        .with_content(name.clone())
        .with_raw(raw_args);
    ev.tool_name = name.clone();
    ev.tool_call_id = call_id.clone();
    out.push(ev);
    if name == "subagent" {
        if let Some(tasks) = args.get("tasks").and_then(|v| v.as_array()) {
            for (i, task) in tasks.iter().enumerate() {
                let agent = {
                    let a = text::field_str(task, "agent");
                    if a.is_empty() {
                        "worker".into()
                    } else {
                        a
                    }
                };
                let desc = text::field_str(task, "task");
                let mut spawn = Event::new(EventType::SubagentSpawned)
                    .with_ts(ts)
                    .with_content(format!("spawned {agent}: {desc}").trim())
                    .with_raw(raw);
                spawn.child_session_id = format!("{call_id}:{i}");
                spawn.subagent_type = agent;
                spawn.description = desc.chars().take(320).collect();
                out.push(spawn);
            }
        }
    }
    out
}

fn tool_result_events(msg: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let name = {
        let n = text::field_str(msg, "toolName");
        if n.is_empty() {
            "tool".into()
        } else {
            n
        }
    };
    if name == "subagent" {
        return subagent_finish_events(msg, ts, raw);
    }
    let mut ev = Event::new(EventType::ToolCallUpdate)
        .with_ts(ts)
        .with_content(text::text_of(msg.get("content").unwrap_or(&Value::Null)))
        .with_raw(raw);
    ev.tool_name = name;
    ev.tool_call_id = text::field_str(msg, "toolCallId");
    if text::field_str(msg, "isError") == "true" || msg.get("isError") == Some(&Value::Bool(true)) {
        ev.is_error = true;
    }
    vec![ev]
}

fn bash_execution_events(msg: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let command = text::field_str(msg, "command");
    let output = text::field_str(msg, "output");
    let args = serde_json::json!({ "command": command });
    let raw_args = serde_json::to_string(&args).unwrap_or_default();
    let mut call = Event::new(EventType::ToolCall)
        .with_ts(ts)
        .with_content("bash")
        .with_raw(raw_args);
    call.tool_name = "bash".into();
    let mut update = Event::new(EventType::ToolCallUpdate)
        .with_ts(ts)
        .with_content(output)
        .with_raw(raw);
    update.tool_name = "bash".into();
    if msg.get("cancelled") == Some(&Value::Bool(true)) {
        update.is_error = true;
    } else if let Some(code) = text::field_i64(msg, "exitCode") {
        update.is_error = code != 0;
    }
    vec![call, update]
}

fn subagent_finish_events(msg: &Value, ts: Option<i64>, raw: &str) -> Vec<Event> {
    let call_id = text::field_str(msg, "toolCallId");
    let details = msg.get("details").cloned().unwrap_or(Value::Null);
    let results = details.get("results").and_then(|v| v.as_array());
    if results.is_none() || results.is_some_and(|r| r.is_empty()) {
        let mut ev = Event::new(EventType::SubagentFinished)
            .with_ts(ts)
            .with_content(
                text::text_of(msg.get("content").unwrap_or(&Value::Null))
                    .chars()
                    .take(400)
                    .collect::<String>(),
            )
            .with_raw(raw);
        ev.tool_name = "subagent".into();
        ev.tool_call_id = call_id.clone();
        ev.child_session_id = format!("{call_id}:0");
        ev.subagent_type = "worker".into();
        ev.is_error = msg.get("isError") == Some(&Value::Bool(true));
        return vec![ev];
    }
    let mut out = Vec::new();
    for (i, item) in results.unwrap().iter().enumerate() {
        let agent = {
            let a = text::field_str(item, "agent");
            if a.is_empty() {
                "worker".into()
            } else {
                a
            }
        };
        let text_body = last_assistant_text(item.get("messages"))
            .unwrap_or_else(|| text::text_of(item.get("task").unwrap_or(&Value::Null)));
        let mut ev = Event::new(EventType::SubagentFinished)
            .with_ts(ts)
            .with_content(text_body.chars().take(400).collect::<String>())
            .with_raw(raw);
        ev.tool_name = "subagent".into();
        ev.tool_call_id = call_id.clone();
        ev.child_session_id = format!("{call_id}:{i}");
        ev.subagent_type = agent;
        out.push(ev);
    }
    out
}

fn last_assistant_text(messages: Option<&Value>) -> Option<String> {
    let items = messages?.as_array()?;
    let mut text = None;
    for item in items {
        if text::field_str(item, "role") != "assistant" {
            continue;
        }
        let body = text::text_of(item.get("content").unwrap_or(&Value::Null));
        if !body.trim().is_empty() {
            text = Some(body);
        }
    }
    text
}

fn last_role_outcome(rows: &[&JsonlRow]) -> String {
    for row in rows.iter().rev() {
        if text::field_str(&row.value, "type") != "message" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        let role = text::field_str(&msg, "role");
        if role == "user" {
            return ListStatus::Idle.as_str().into();
        }
        if role == "toolResult" {
            return ListStatus::Running.as_str().into();
        }
        if role == "assistant" {
            let err = text::field_str(&msg, "errorMessage");
            if !err.is_empty() {
                return ListStatus::from_token("error").as_str().into();
            }
            let reason = text::field_str(&msg, "stopReason");
            let compact = reason.to_ascii_lowercase().replace('_', "");
            if compact == "tooluse" {
                return ListStatus::Running.as_str().into();
            }
            return ListStatus::from_token(&reason).as_str().into();
        }
    }
    String::new()
}

fn model_from_rows(rows: &[&JsonlRow]) -> String {
    let mut provider = String::new();
    let mut model = String::new();
    for row in rows {
        if text::field_str(&row.value, "type") != "model_change" {
            continue;
        }
        let p = text::field_str(&row.value, "provider");
        if !p.is_empty() {
            provider = p;
        }
        let m = text::field_str(&row.value, "modelId");
        if !m.is_empty() {
            model = m;
        }
    }
    match (provider.is_empty(), model.is_empty()) {
        (false, false) => format!("{provider}/{model}"),
        (true, false) => model,
        (false, true) => provider,
        (true, true) => "unknown".into(),
    }
}

fn thinking_level_from_rows(rows: &[&JsonlRow]) -> String {
    let mut level = String::new();
    for row in rows {
        if text::field_str(&row.value, "type") != "thinking_level_change" {
            continue;
        }
        let next = text::field_str(&row.value, "thinkingLevel");
        if !next.is_empty() {
            level = next;
        }
    }
    level
}

fn session_name_from_rows(rows: &[&JsonlRow]) -> String {
    let mut name = String::new();
    for row in rows {
        if text::field_str(&row.value, "type") != "session_info" {
            continue;
        }
        let next = text::field_str(&row.value, "name");
        if !next.is_empty() {
            name = next;
        }
    }
    name
}

fn first_user_title(rows: &[&JsonlRow]) -> String {
    for row in rows {
        if text::field_str(&row.value, "type") != "message" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        if text::field_str(&msg, "role") != "user" {
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

fn usage_tokens(msg: &Value) -> Option<i64> {
    let usage = msg.get("usage")?;
    if let Some(n) = text::field_i64(usage, "totalTokens") {
        return Some(n.max(0));
    }
    let input = text::field_i64(usage, "input").unwrap_or(0);
    let output = text::field_i64(usage, "output").unwrap_or(0);
    let cache = text::field_i64(usage, "cacheRead").unwrap_or(0);
    let total = input + output + cache;
    if total > 0 {
        Some(total)
    } else {
        None
    }
}

fn context_from_rows(rows: &[&JsonlRow]) -> Option<i64> {
    let mut tokens = None;
    for row in rows {
        let kind = text::field_str(&row.value, "type");
        if kind == "usage" {
            if let Some(n) = text::field_i64(&row.value, "totalTokens") {
                tokens = Some(n.max(0));
                continue;
            }
            let input = text::field_i64(&row.value, "inputTokens").unwrap_or(0);
            let output = text::field_i64(&row.value, "outputTokens").unwrap_or(0);
            if input + output > 0 {
                tokens = Some(input + output);
            }
            continue;
        }
        if kind != "message" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        if text::field_str(&msg, "role") != "assistant" {
            continue;
        }
        if let Some(n) = usage_tokens(&msg) {
            tokens = Some(n);
        }
    }
    tokens
}

fn count_on_path(rows: &[&JsonlRow]) -> (u32, u32, u32, u32, u32) {
    let mut tools = 0u32;
    let mut kids = 0u32;
    let mut messages = 0u32;
    let mut compact = 0u32;
    let mut errors = 0u32;
    for row in rows {
        let kind = text::field_str(&row.value, "type");
        if kind == "compaction" {
            compact += 1;
        }
        if kind != "message" {
            continue;
        }
        let msg = row.value.get("message").cloned().unwrap_or(Value::Null);
        let role = text::field_str(&msg, "role");
        if role == "user" || role == "assistant" {
            messages += 1;
        }
        if role == "compactionSummary" {
            compact += 1;
        }
        if role == "assistant" && !text::field_str(&msg, "errorMessage").is_empty() {
            errors += 1;
        }
        if role == "toolResult"
            && (msg.get("isError") == Some(&Value::Bool(true))
                || text::field_str(&msg, "isError") == "true")
        {
            errors += 1;
        }
        let Some(blocks) = msg.get("content").and_then(|v| v.as_array()) else {
            continue;
        };
        for block in blocks {
            if text::field_str(block, "type") != "toolCall" {
                continue;
            }
            tools += 1;
            if text::field_str(block, "name") == "subagent" {
                if let Some(tasks) = block.pointer("/arguments/tasks").and_then(|v| v.as_array()) {
                    kids += tasks.len() as u32;
                }
            }
        }
    }
    (tools, kids, messages, compact, errors)
}

fn meta_from_rows(locator: &Path, session_id: &str, rows: &[JsonlRow], num_events: u32) -> ListMeta {
    let header = rows.iter().find(|r| is_session_header(&r.value));
    let sid = header
        .map(|h| text::field_str(&h.value, "id"))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| session_id.to_string());
    let created = header
        .map(|h| header_created(&h.value))
        .unwrap_or_default();
    let mut last_ts = created.clone();
    for row in rows {
        let ts = text::field_iso(&row.value, "timestamp");
        if !ts.is_empty() {
            last_ts = ts;
        } else if let Some(n) = row.value.get("createdAt").and_then(text::parse_ts_value) {
            last_ts = text::iso_millis(n);
        }
    }
    if last_ts.is_empty() {
        let stamp = jsonl::file_stamp(locator);
        last_ts = text::iso_secs(stamp.0 as i64);
    }
    let path = leaf_path(rows);
    let (tools, kids, messages, compact, errors) = count_on_path(&path);
    let named = session_name_from_rows(&path);
    let title = if named.is_empty() {
        first_user_title(&path)
    } else {
        named
    };
    ListMeta {
        session_id: sid,
        locator: locator.to_path_buf(),
        model_id: model_from_rows(&path),
        title,
        created_at: created.clone(),
        updated_at: last_ts.clone(),
        duration_seconds: text::duration_secs(
            text::epoch_secs(&Value::String(created)),
            text::epoch_secs(&Value::String(last_ts)),
        ),
        tool_call_count: tools,
        turn_outcome: last_role_outcome(&path),
        harness: "pi".into(),
        harness_version: String::new(),
        run_dir: header
            .map(|h| text::field_str(&h.value, "cwd"))
            .unwrap_or_default(),
        num_events,
        has_subagents: kids > 0,
        subagent_count: kids,
        context_tokens_used: context_from_rows(&path),
        reasoning_effort: thinking_level_from_rows(&path),
        num_messages: messages,
        compaction_count: compact,
        error_count: errors,
        ..Default::default()
    }
}

impl Store for Pi {
    fn id(&self) -> &'static str {
        "pi"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        let mut out = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for file in collect(roots) {
            let sid = header_id(&file).unwrap_or_else(|| session_id_from_name(&file));
            if sid.is_empty() || !seen.insert(sid.clone()) {
                continue;
            }
            out.push(SessionLocator {
                harness: "pi".into(),
                session_id: sid,
                locator: file,
                cwd: String::new(),
            });
        }
        out
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
            return Err(format!("pi session not found: {session_id}"));
        }
        let rows = jsonl::window(locator);
        let mut meta = meta_from_rows(locator, session_id, &rows, 0);
        meta.num_events = self.event_count(locator, session_id);
        Ok(meta)
    }

    fn detail_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("pi session not found: {session_id}"));
        }
        let rows = jsonl::cached_records(locator, None);
        let events = self.events(&rows);
        Ok(meta_from_rows(
            locator,
            session_id,
            &rows,
            events.len() as u32,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(
            "../tests/fixtures/harness/pi/sessions/tmp-probe/2026-08-09T12-00-00-000Z_019fe000-0000-7000-8000-000000000001.jsonl",
        )
    }

    #[test]
    fn list_meta_window_title_and_last_turn() {
        let path = fixture();
        let meta = Pi
            .list_meta(&path, "019fe000-0000-7000-8000-000000000001")
            .unwrap();
        assert_eq!(meta.title, "Reply with PI_PROBE_OK");
        assert_eq!(meta.turn_outcome, "complete");
        assert_eq!(meta.model_id, "xai/grok-4.5");
        assert_eq!(meta.harness_version, "");
        assert_eq!(meta.reasoning_effort, "medium");
        assert_eq!(meta.context_tokens_used, Some(14));
        assert_eq!(meta.num_messages, 3);
    }

    #[test]
    fn timeline_emits_mode_and_iso_timestamps() {
        let path = fixture();
        let rows = jsonl::cached_records(&path, None);
        let events = timeline_rows(&rows);
        let types: Vec<_> = events.iter().map(|e| e.event_type.as_str()).collect();
        assert!(types.contains(&"current_mode_update"));
        assert!(types.contains(&"agent_thought_chunk"));
        assert!(events.iter().any(|e| e.timestamp.is_some()));
    }

    #[test]
    fn leaf_path_drops_abandoned_branch() {
        let rows = vec![
            JsonlRow {
                raw: r#"{"type":"session","id":"s"}"#.into(),
                value: serde_json::json!({"type":"session","id":"s"}),
            },
            JsonlRow {
                raw: String::new(),
                value: serde_json::json!({
                    "type":"message","id":"u1","parentId":"s",
                    "message":{"role":"user","content":[{"type":"text","text":"old"}]}
                }),
            },
            JsonlRow {
                raw: String::new(),
                value: serde_json::json!({
                    "type":"message","id":"a1","parentId":"u1",
                    "message":{"role":"assistant","content":[{"type":"text","text":"gone"}]}
                }),
            },
            JsonlRow {
                raw: String::new(),
                value: serde_json::json!({
                    "type":"message","id":"u2","parentId":"s",
                    "message":{"role":"user","content":[{"type":"text","text":"new"}]}
                }),
            },
        ];
        let events = timeline_rows(&rows);
        let texts: Vec<_> = events
            .iter()
            .filter(|e| e.event_type.as_str() == "user_message_chunk")
            .map(|e| e.content.as_str())
            .collect();
        assert_eq!(texts, ["new"]);
        assert!(!events.iter().any(|e| e.content == "gone"));
    }
}
