//! OpenCode sqlite session store.

use crate::event::{Event, EventType, FileStamp, ListMeta, ListStatus, SessionLocator};
use crate::store::{Record, Store};
use crate::text;
use rusqlite::Connection;
use serde_json::Value;
use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};

fn event_type_of(rec: &Record) -> String {
    text::field_str(&rec.value, "type")
}

fn event_data(rec: &Record) -> Value {
    rec.value.get("data").cloned().unwrap_or(Value::Null)
}

fn events_from_event_records(records: &[Record]) -> Vec<Event> {
    let mut messages: Vec<(String, Value)> = Vec::new();
    let mut parts: HashMap<String, Vec<Value>> = HashMap::new();
    for rec in records {
        let typ = event_type_of(rec);
        let data = event_data(rec);
        if typ.starts_with("session.") {
            continue;
        }
        if typ.contains("part") {
            if let Some(part) = data.get("part") {
                let mid = text::field_str(part, "messageID");
                if !mid.is_empty() {
                    parts.entry(mid).or_default().push(part.clone());
                }
            }
            continue;
        }
        if typ.starts_with("message.") {
            if let Some(info) = data.get("info") {
                let mid = text::field_str(info, "id");
                if !mid.is_empty() {
                    messages.push((mid, info.clone()));
                }
            }
        }
    }
    let mut events = Vec::new();
    let mut turn = 0i32;
    for (mid, data) in messages {
        let role = text::field_str(&data, "role");
        let msg_parts = parts.get(&mid).cloned().unwrap_or_default();
        let raw = serde_json::to_string(&data).unwrap_or_default();
        if role == "user" {
            let mut start = Event::new(EventType::TurnStarted)
                .with_content(format!("turn_number={turn}"))
                .with_raw(&raw);
            start.turn_number = Some(turn);
            events.push(start);
            let mut text_body = String::new();
            for part in &msg_parts {
                if text::field_str(part, "type") == "text" {
                    text_body.push_str(&text::field_str(part, "text"));
                }
            }
            events.push(
                Event::new(EventType::UserMessageChunk)
                    .with_content(text_body)
                    .with_raw(raw),
            );
            turn += 1;
        } else {
            for part in msg_parts {
                let praw = serde_json::to_string(&part).unwrap_or_default();
                events.extend(map_part(&part, &praw));
            }
        }
    }
    events
}

fn events_from_message_records(records: &[Record]) -> Vec<Event> {
    let mut parts: HashMap<String, Vec<(Value, String)>> = HashMap::new();
    let mut messages: Vec<(String, Value, String)> = Vec::new();
    for rec in records {
        let table = text::field_str(&rec.value, "table");
        let data = rec.value.get("data").cloned().unwrap_or(Value::Null);
        if table == "part" {
            let mid = text::field_str(&rec.value, "message_id");
            parts.entry(mid).or_default().push((data, rec.raw.clone()));
            continue;
        }
        if table == "message" {
            messages.push((text::field_str(&rec.value, "id"), data, rec.raw.clone()));
        }
    }
    let mut events = Vec::new();
    let mut turn = 0i32;
    for (mid, data, raw) in messages {
        let role = text::field_str(&data, "role");
        let msg_parts = parts.get(&mid).cloned().unwrap_or_default();
        if role == "user" {
            let mut start = Event::new(EventType::TurnStarted)
                .with_content(format!("turn_number={turn}"))
                .with_raw(&raw);
            start.turn_number = Some(turn);
            events.push(start);
            let mut text_body = String::new();
            for (part, _) in &msg_parts {
                if text::field_str(part, "type") == "text" {
                    let t = text::field_str(part, "text");
                    if !t.is_empty() {
                        text_body.push_str(&t);
                    }
                }
            }
            if text_body.is_empty() {
                text_body = text::text_of(data.get("content").unwrap_or(&Value::Null));
            }
            events.push(
                Event::new(EventType::UserMessageChunk)
                    .with_content(text_body)
                    .with_raw(raw),
            );
            turn += 1;
        } else {
            for (part, praw) in msg_parts {
                events.extend(map_part(&part, &praw));
            }
        }
    }
    events
}

fn map_part(part: &Value, raw: &str) -> Vec<Event> {
    match text::field_str(part, "type").as_str() {
        "text" => vec![Event::new(EventType::AgentMessageChunk)
            .with_content(text::field_str(part, "text"))
            .with_raw(raw)],
        "reasoning" => vec![Event::new(EventType::AgentThoughtChunk)
            .with_content(text::field_str(part, "text"))
            .with_raw(raw)],
        "tool" => tool_events(part, raw),
        "compaction" => vec![Event::new(EventType::CompactionCheckpoint)
            .with_content(text::field_str(part, "text"))
            .with_raw(raw)],
        "permission" | "snapshot" | "step" => vec![Event::new(EventType::System)
            .with_content(text::field_str(part, "type"))
            .with_raw(raw)],
        "file" | "patch" => {
            let name = text::field_str(part, "type");
            let mut ev = Event::new(EventType::ToolCall)
                .with_content(&name)
                .with_raw(raw);
            ev.tool_name = name;
            vec![ev]
        }
        _ => Vec::new(),
    }
}

fn tool_events(part: &Value, raw: &str) -> Vec<Event> {
    let name = {
        let n = text::field_str(part, "tool");
        if n.is_empty() {
            "tool".into()
        } else {
            n
        }
    };
    let call_id = {
        let a = text::field_str(part, "callID");
        if a.is_empty() {
            text::field_str(part, "call_id")
        } else {
            a
        }
    };
    let state = part.get("state").cloned().unwrap_or(Value::Null);
    let inn = state.get("input").cloned().unwrap_or(Value::Null);
    let raw_in = serde_json::to_string(&inn).unwrap_or_else(|_| raw.to_string());
    let status = text::field_str(&state, "status").to_ascii_lowercase();
    let failed = matches!(status.as_str(), "error" | "failed") || state.get("error").is_some();
    let out = text::field_str(&state, "output");
    let mut call = Event::new(EventType::ToolCall)
        .with_content(&name)
        .with_raw(&raw_in);
    call.tool_name = name.clone();
    call.tool_call_id = call_id.clone();
    call.is_error = failed;
    let mut upd = Event::new(EventType::ToolCallUpdate)
        .with_content(&out)
        .with_raw(raw);
    upd.tool_name = name.clone();
    upd.tool_call_id = call_id;
    upd.is_error = failed;
    let mut events = vec![call, upd];
    if name == "task" {
        let meta = state.get("metadata").cloned().unwrap_or(Value::Null);
        let mut child = text::field_str(&meta, "sessionId");
        if child.is_empty() && !text::field_str(&inn, "subagent_type").is_empty() {
            if let Some(rest) = out.split_once("<task id=\"") {
                child = rest.1.split('"').next().unwrap_or("").to_string();
            }
        }
        if !child.is_empty() {
            let desc = text::field_str(&inn, "description");
            let typ = text::field_str(&inn, "subagent_type");
            let mut spawn = Event::new(EventType::SubagentSpawned)
                .with_content(&desc)
                .with_raw(&raw_in);
            spawn.child_session_id = child.clone();
            spawn.subagent_type = typ;
            spawn.description = desc;
            let mut fin = Event::new(EventType::SubagentFinished)
                .with_content(&out)
                .with_raw(raw);
            fin.child_session_id = child;
            fin.is_error = failed;
            events.push(spawn);
            events.push(fin);
        }
    }
    events
}

pub struct OpenCode;

#[derive(Default)]
struct EventCursor {
    last_seq: Option<i64>,
    records: Vec<Record>,
}

struct OpenDb {
    con: Connection,
    cursors: HashMap<String, EventCursor>,
}

static OPEN_DBS: LazyLock<Mutex<HashMap<PathBuf, OpenDb>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

fn encode_stamp(n: i64) -> FileStamp {
    let n = n.max(0) as u64;
    (n as f64, n, 0, 0)
}

fn max_seq(con: &Connection, session_id: &str) -> Option<i64> {
    con.query_row(
        "SELECT MAX(seq) FROM event WHERE aggregate_id = ?1",
        [session_id],
        |row| row.get::<_, Option<i64>>(0),
    )
    .ok()
    .flatten()
}

fn max_row_time(con: &Connection, session_id: &str) -> i64 {
    let mut best = 0i64;
    if table_exists(con, "message") {
        if let Ok(val) = con.query_row(
            "SELECT MAX(time_updated) FROM message WHERE session_id = ?1",
            [session_id],
            |row| row.get::<_, Option<i64>>(0),
        ) {
            best = best.max(val.unwrap_or(0));
        }
    }
    if table_exists(con, "part") {
        if let Ok(val) = con.query_row(
            "SELECT MAX(time_updated) FROM part WHERE session_id = ?1",
            [session_id],
            |row| row.get::<_, Option<i64>>(0),
        ) {
            best = best.max(val.unwrap_or(0));
        }
    }
    best
}

fn event_row(seq: i64, typ: String, data: String) -> Record {
    let parsed = json_object(&data);
    Record {
        raw: data,
        value: serde_json::json!({
            "seq": seq,
            "type": typ,
            "data": parsed,
        }),
    }
}

impl EventCursor {
    fn sync(&mut self, con: &Connection, session_id: &str) -> Result<(), String> {
        let max = max_seq(con, session_id);
        if self.last_seq.is_some() && self.last_seq == max {
            return Ok(());
        }
        if max.is_none()
            || self
                .last_seq
                .is_some_and(|last| max.is_some_and(|cur| cur < last))
        {
            self.records.clear();
            self.last_seq = None;
        }
        let Some(cur) = max else {
            return Ok(());
        };
        let after = self.last_seq.unwrap_or(-1);
        let mut stmt = con
            .prepare(
                "SELECT seq, type, data FROM event \
                 WHERE aggregate_id = ?1 AND seq > ?2 ORDER BY seq ASC, id ASC",
            )
            .map_err(|e| e.to_string())?;
        let rows = stmt
            .query_map(rusqlite::params![session_id, after], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1).unwrap_or_default(),
                    row.get::<_, String>(2).unwrap_or_default(),
                ))
            })
            .map_err(|e| e.to_string())?;
        for row in rows.flatten() {
            self.records.push(event_row(row.0, row.1, row.2));
        }
        self.last_seq = Some(cur);
        Ok(())
    }
}

fn open_ro(path: &Path) -> Result<Connection, String> {
    let con = Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| e.to_string())?;
    // mmap + short-lived WAL connections SIGBUS when another reader
    // remaps the -shm file. Keep mmap off; reuse the connection below.
    con.pragma_update(None, "mmap_size", 0u64)
        .map_err(|e| e.to_string())?;
    Ok(con)
}

fn with_open_db<T>(
    path: &Path,
    f: impl FnOnce(&mut OpenDb) -> Result<T, String>,
) -> Result<T, String> {
    let mut guard = OPEN_DBS.lock().unwrap_or_else(|err| err.into_inner());
    if !guard.contains_key(path) {
        let con = open_ro(path)?;
        guard.insert(
            path.to_path_buf(),
            OpenDb {
                con,
                cursors: HashMap::new(),
            },
        );
    }
    let db = guard
        .get_mut(path)
        .ok_or_else(|| format!("opencode db cache missing: {}", path.display()))?;
    f(db)
}

fn cached_event_records(db: &mut OpenDb, session_id: &str) -> Result<Vec<Record>, String> {
    let cursor = db.cursors.entry(session_id.to_string()).or_default();
    cursor.sync(&db.con, session_id)?;
    Ok(cursor.records.clone())
}

fn records_from_db(db: &mut OpenDb, session_id: &str) -> Result<Vec<Record>, String> {
    if table_exists(&db.con, "event") && max_seq(&db.con, session_id).is_some() {
        return cached_event_records(db, session_id);
    }
    if table_exists(&db.con, "message") {
        return message_records(&db.con, session_id);
    }
    Ok(Vec::new())
}

fn message_records(con: &Connection, session_id: &str) -> Result<Vec<Record>, String> {
    let mut out = Vec::new();
    let mut stmt = con
        .prepare("SELECT id, data FROM message WHERE session_id = ?1 ORDER BY time_created, id")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([session_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1).unwrap_or_default(),
            ))
        })
        .map_err(|e| e.to_string())?;
    for row in rows.flatten() {
        let parsed = json_object(&row.1);
        out.push(Record {
            raw: row.1,
            value: serde_json::json!({
                "table": "message",
                "id": row.0,
                "data": parsed,
            }),
        });
    }
    if table_exists(con, "part") {
        let mut pstmt = con
            .prepare(
                "SELECT message_id, data FROM part WHERE session_id = ?1 ORDER BY time_created, id",
            )
            .map_err(|e| e.to_string())?;
        let prows = pstmt
            .query_map([session_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1).unwrap_or_default(),
                ))
            })
            .map_err(|e| e.to_string())?;
        for prow in prows.flatten() {
            let parsed = json_object(&prow.1);
            out.push(Record {
                raw: prow.1,
                value: serde_json::json!({
                    "table": "part",
                    "message_id": prow.0,
                    "data": parsed,
                }),
            });
        }
    }
    Ok(out)
}

fn table_exists(con: &Connection, name: &str) -> bool {
    con.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1",
        [name],
        |_| Ok(()),
    )
    .is_ok()
}

fn query_text(con: &Connection, sql: &str, session_id: &str) -> Option<String> {
    con.query_row(sql, [session_id], |row| row.get::<_, String>(0))
        .ok()
}

fn json_object(raw: &str) -> Value {
    serde_json::from_str(raw).unwrap_or(Value::Null)
}

fn model_id_from_value(raw: &Value) -> String {
    let val = match raw {
        Value::String(s) => serde_json::from_str(s).unwrap_or(Value::String(s.clone())),
        other => other.clone(),
    };
    if let Value::String(s) = &val {
        return if s.is_empty() {
            "unknown".into()
        } else {
            s.clone()
        };
    }
    let mid = {
        let id = text::field_str(&val, "id");
        if id.is_empty() {
            text::field_str(&val, "modelID")
        } else {
            id
        }
    };
    let provider = {
        let id = text::field_str(&val, "providerID");
        if id.is_empty() {
            text::field_str(&val, "provider")
        } else {
            id
        }
    };
    match (mid.is_empty(), provider.is_empty()) {
        (false, false) => format!("{provider}/{mid}"),
        (false, true) => mid,
        (true, false) => provider,
        (true, true) => "unknown".into(),
    }
}

fn iso_millis(ms: i64) -> String {
    if ms <= 0 {
        return String::new();
    }
    let secs = if ms > 1_000_000_000_000 {
        ms / 1000
    } else {
        ms
    };
    iso_secs(secs)
}

fn iso_secs(secs: i64) -> String {
    if secs <= 0 {
        return String::new();
    }
    let days = secs.div_euclid(86_400);
    let tod = secs.rem_euclid(86_400);
    let hour = tod / 3600;
    let min = (tod % 3600) / 60;
    let sec = tod % 60;
    let (year, month, day) = civil_from_days(days);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}Z")
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    (year, month as i64, day as i64)
}

fn set_times(meta: &mut ListMeta, created: i64, updated: i64) {
    if created > 0 {
        meta.created_at = iso_millis(created);
    }
    let end = if updated > 0 { updated } else { created };
    if end > 0 {
        meta.updated_at = iso_millis(end);
    }
    if created > 0 && end > 0 {
        let start = if created > 1_000_000_000_000 {
            created / 1000
        } else {
            created
        };
        let stop = if end > 1_000_000_000_000 {
            end / 1000
        } else {
            end
        };
        meta.duration_seconds = (stop - start).max(0) as f64;
    }
}

fn apply_session_info(meta: &mut ListMeta, info: &Value) {
    let title = text::field_str(info, "title");
    if !title.is_empty() {
        meta.title = title;
    }
    let directory = text::field_str(info, "directory");
    if !directory.is_empty() {
        meta.run_dir = directory;
    }
    let version = text::field_str(info, "version");
    if !version.is_empty() {
        meta.harness_version = version;
    }
    if let Some(model) = info.get("model") {
        meta.model_id = model_id_from_value(model);
    }
    if let Some(time) = info.get("time") {
        let created = time
            .get("created")
            .and_then(text::parse_ts_value)
            .unwrap_or(0);
        let updated = time
            .get("updated")
            .and_then(text::parse_ts_value)
            .unwrap_or(0);
        set_times(meta, created, updated);
    }
}

fn fill_session_row(con: &Connection, session_id: &str, meta: &mut ListMeta) -> bool {
    if !table_exists(con, "session") {
        return false;
    }
    let sql = "SELECT title, directory, model, version, time_created, time_updated, \
               time_archived, tokens_input, tokens_output, tokens_reasoning \
               FROM session WHERE id = ?1";
    let row = con.query_row(sql, [session_id], |row| {
        Ok((
            row.get::<_, Option<String>>(0)?.unwrap_or_default(),
            row.get::<_, Option<String>>(1)?.unwrap_or_default(),
            row.get::<_, Option<String>>(2)?.unwrap_or_default(),
            row.get::<_, Option<String>>(3)?.unwrap_or_default(),
            row.get::<_, Option<i64>>(4)?.unwrap_or(0),
            row.get::<_, Option<i64>>(5)?.unwrap_or(0),
            row.get::<_, Option<i64>>(6)?.unwrap_or(0),
            row.get::<_, Option<i64>>(7)?.unwrap_or(0),
            row.get::<_, Option<i64>>(8)?.unwrap_or(0),
            row.get::<_, Option<i64>>(9)?.unwrap_or(0),
        ))
    });
    let Ok((title, directory, model, version, created, updated, archived, tin, tout, treason)) =
        row
    else {
        return false;
    };
    meta.title = title;
    meta.run_dir = directory;
    meta.model_id = model_id_from_value(&Value::String(model));
    meta.harness_version = version;
    set_times(meta, created, updated);
    let tokens = tin.saturating_add(tout).saturating_add(treason);
    if tokens > 0 {
        meta.context_tokens_used = Some(tokens);
    }
    if archived > 0 {
        meta.turn_outcome = ListStatus::Complete.as_str().into();
    }
    true
}

fn last_session_info(con: &Connection, session_id: &str) -> Option<Value> {
    let raw = query_text(
        con,
        "SELECT data FROM event WHERE aggregate_id = ?1 \
         AND (type LIKE 'session.updated%' OR type LIKE 'session.created%') \
         ORDER BY seq DESC LIMIT 1",
        session_id,
    )?;
    json_object(&raw).get("info").cloned()
}

fn part_status(data: &Value) -> Option<String> {
    let state = data.get("state").unwrap_or(&Value::Null);
    let status = text::field_str(state, "status");
    if status.is_empty() {
        None
    } else {
        Some(status)
    }
}

fn last_part_status_from_events(con: &Connection, session_id: &str) -> Option<String> {
    let raw = query_text(
        con,
        "SELECT data FROM event WHERE aggregate_id = ?1 AND type LIKE '%part%' \
         ORDER BY seq DESC LIMIT 1",
        session_id,
    )?;
    let data = json_object(&raw);
    part_status(data.get("part").unwrap_or(&Value::Null))
}

fn last_part_status_from_table(con: &Connection, session_id: &str) -> Option<String> {
    let raw = query_text(
        con,
        "SELECT data FROM part WHERE session_id = ?1 \
         ORDER BY time_created DESC, id DESC LIMIT 1",
        session_id,
    )?;
    part_status(&json_object(&raw))
}

fn message_outcome(data: &Value) -> String {
    let role = text::field_str(data, "role");
    if role == "assistant" {
        let completed = data.get("time").and_then(|t| t.get("completed"));
        if let Some(val) = completed {
            let empty = val.is_null() || matches!(val, Value::String(s) if s.is_empty());
            let zero = val.as_i64() == Some(0);
            if !empty && !zero {
                return ListStatus::Complete.as_str().into();
            }
        }
    }
    if role.is_empty() {
        String::new()
    } else {
        ListStatus::from_token(&role).as_str().into()
    }
}

fn last_message_outcome_from_events(con: &Connection, session_id: &str) -> String {
    let Some(raw) = query_text(
        con,
        "SELECT data FROM event WHERE aggregate_id = ?1 AND type LIKE 'message.%' \
         AND type NOT LIKE '%part%' ORDER BY seq DESC LIMIT 1",
        session_id,
    ) else {
        return String::new();
    };
    let data = json_object(&raw);
    message_outcome(data.get("info").unwrap_or(&Value::Null))
}

fn last_message_outcome_from_table(con: &Connection, session_id: &str) -> String {
    let Some(raw) = query_text(
        con,
        "SELECT data FROM message WHERE session_id = ?1 \
         ORDER BY time_created DESC, id DESC LIMIT 1",
        session_id,
    ) else {
        return String::new();
    };
    message_outcome(&json_object(&raw))
}

fn fill_turn_outcome(con: &Connection, session_id: &str, meta: &mut ListMeta) {
    if !meta.turn_outcome.is_empty() {
        return;
    }
    if table_exists(con, "event") {
        if let Some(info) = last_session_info(con, session_id) {
            let archived = info.get("time_archived").cloned().unwrap_or(Value::Null);
            let archived = if archived.is_null() {
                info.get("time")
                    .and_then(|t| t.get("archived"))
                    .cloned()
                    .unwrap_or(Value::Null)
            } else {
                archived
            };
            let set = match &archived {
                Value::Null => false,
                Value::String(s) => !s.is_empty(),
                Value::Number(n) => n.as_i64().unwrap_or(0) != 0,
                _ => true,
            };
            if set {
                meta.turn_outcome = ListStatus::Complete.as_str().into();
                return;
            }
        }
        if let Some(status) = last_part_status_from_events(con, session_id) {
            let mapped = ListStatus::from_token(&status);
            if mapped != ListStatus::Idle {
                meta.turn_outcome = mapped.as_str().into();
                return;
            }
        }
    }
    if table_exists(con, "part") {
        if let Some(status) = last_part_status_from_table(con, session_id) {
            let mapped = ListStatus::from_token(&status);
            if mapped != ListStatus::Idle {
                meta.turn_outcome = mapped.as_str().into();
                return;
            }
        }
    }
    if table_exists(con, "event") {
        let outcome = last_message_outcome_from_events(con, session_id);
        if !outcome.is_empty() {
            meta.turn_outcome = outcome;
            return;
        }
    }
    if table_exists(con, "message") {
        meta.turn_outcome = last_message_outcome_from_table(con, session_id);
    }
}

fn child_count(con: &Connection, session_id: &str) -> u32 {
    let mut ids = BTreeSet::new();
    if table_exists(con, "session") {
        if let Ok(mut stmt) = con.prepare("SELECT id FROM session WHERE parent_id = ?1") {
            if let Ok(rows) = stmt.query_map([session_id], |row| row.get::<_, String>(0)) {
                for id in rows.flatten() {
                    if !id.is_empty() {
                        ids.insert(id);
                    }
                }
            }
        }
    }
    if table_exists(con, "event") {
        if let Ok(mut stmt) =
            con.prepare("SELECT data FROM event WHERE type LIKE 'session.created%'")
        {
            if let Ok(rows) = stmt.query_map([], |row| row.get::<_, String>(0)) {
                for raw in rows.flatten() {
                    let info = json_object(&raw)
                        .get("info")
                        .cloned()
                        .unwrap_or(Value::Null);
                    if text::field_str(&info, "parentID") == session_id {
                        let id = text::field_str(&info, "id");
                        if !id.is_empty() {
                            ids.insert(id);
                        }
                    }
                }
            }
        }
    }
    ids.len() as u32
}

impl Store for OpenCode {
    fn id(&self) -> &'static str {
        "opencode"
    }

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator> {
        let mut out = Vec::new();
        for raw in roots {
            let db = if raw.is_file() {
                raw.clone()
            } else {
                raw.join("opencode.db")
            };
            if !db.is_file() {
                continue;
            }
            let _ = with_open_db(&db, |held| {
                if table_exists(&held.con, "event") {
                    if let Ok(mut stmt) = held
                        .con
                        .prepare("SELECT data FROM event WHERE type LIKE 'session.created%'")
                    {
                        if let Ok(rows) = stmt.query_map([], |row| row.get::<_, String>(0)) {
                            for raw in rows.flatten() {
                                let data: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
                                let info = data.get("info").cloned().unwrap_or(Value::Null);
                                let sid = text::field_str(&info, "id");
                                let parent = text::field_str(&info, "parentID");
                                if sid.is_empty() || !(parent.is_empty() || parent == "None") {
                                    continue;
                                }
                                out.push(SessionLocator {
                                    harness: "opencode".into(),
                                    session_id: sid,
                                    locator: db.clone(),
                                    cwd: text::field_str(&info, "directory"),
                                });
                            }
                        }
                    }
                }
                if table_exists(&held.con, "session") {
                    if let Ok(mut stmt) = held.con.prepare(
                        "SELECT id, directory FROM session WHERE parent_id IS NULL OR parent_id = ''",
                    ) {
                        if let Ok(rows) = stmt.query_map([], |row| {
                            Ok((
                                row.get::<_, String>(0)?,
                                row.get::<_, String>(1).unwrap_or_default(),
                            ))
                        }) {
                            for row in rows.flatten() {
                                out.push(SessionLocator {
                                    harness: "opencode".into(),
                                    session_id: row.0,
                                    locator: db.clone(),
                                    cwd: row.1,
                                });
                            }
                        }
                    }
                }
                Ok(())
            });
        }
        out
    }

    fn records(&self, locator: &Path, session_id: &str) -> Result<Vec<Record>, String> {
        if !locator.is_file() {
            return Err(format!("opencode session not found: {session_id}"));
        }
        with_open_db(locator, |db| records_from_db(db, session_id))
    }

    fn events(&self, records: &[Record]) -> Vec<Event> {
        if records.iter().any(|rec| rec.value.get("seq").is_some()) {
            events_from_event_records(records)
        } else {
            events_from_message_records(records)
        }
    }

    fn stamp(&self, locator: &Path, session_id: &str) -> FileStamp {
        with_open_db(locator, |db| {
            if table_exists(&db.con, "event") {
                if let Some(seq) = max_seq(&db.con, session_id) {
                    return Ok(encode_stamp(seq));
                }
            }
            Ok(encode_stamp(max_row_time(&db.con, session_id)))
        })
        .unwrap_or((0.0, 0, 0, 0))
    }

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
        if !locator.is_file() {
            return Err(format!("opencode session not found: {session_id}"));
        }
        with_open_db(locator, |db| {
            let mut meta = ListMeta::for_session(self.id(), locator, session_id);
            let mut found = fill_session_row(&db.con, session_id, &mut meta);
            if table_exists(&db.con, "event") {
                if let Some(info) = last_session_info(&db.con, session_id) {
                    found = true;
                    if meta.title.is_empty() {
                        apply_session_info(&mut meta, &info);
                    }
                }
            }
            if !found {
                return Err(format!("opencode session not found: {session_id}"));
            }
            fill_turn_outcome(&db.con, session_id, &mut meta);
            let kids = child_count(&db.con, session_id);
            meta.subagent_count = kids;
            meta.has_subagents = kids > 0;
            let recs = records_from_db(db, session_id)?;
            meta.num_events = self.events(&recs).len() as u32;
            Ok(meta)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::EventType;
    use rusqlite::Connection;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_db(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "anqa-opencode-{label}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir.join("opencode.db")
    }

    fn open_rw(path: &Path) -> Connection {
        Connection::open(path).unwrap()
    }

    fn create_event_table(con: &Connection) {
        con.execute_batch(
            "CREATE TABLE event (
                id INTEGER PRIMARY KEY,
                aggregate_id TEXT,
                seq INTEGER,
                type TEXT,
                data TEXT
            )",
        )
        .unwrap();
    }

    fn insert_event(con: &Connection, id: i64, aid: &str, seq: i64, typ: &str, data: &str) {
        con.execute(
            "INSERT INTO event (id, aggregate_id, seq, type, data) VALUES (?1, ?2, ?3, ?4, ?5)",
            rusqlite::params![id, aid, seq, typ, data],
        )
        .unwrap();
    }

    fn user_message(aid: &str, mid: &str, text: &str) -> (String, String) {
        let info = format!(
            r#"{{"sessionID":"{aid}","info":{{"id":"{mid}","role":"user","sessionID":"{aid}"}}}}"#
        );
        let part = format!(
            r#"{{"sessionID":"{aid}","part":{{"id":"prt_{mid}","messageID":"{mid}","sessionID":"{aid}","type":"text","text":"{text}"}}}}"#
        );
        (info, part)
    }

    fn user_texts(events: &[Event]) -> Vec<&str> {
        events
            .iter()
            .filter(|ev| ev.event_type == EventType::UserMessageChunk)
            .map(|ev| ev.content.as_str())
            .collect()
    }

    #[test]
    fn opencode_stamp_is_per_session() {
        let db = temp_db("stamp");
        let con = open_rw(&db);
        create_event_table(&con);
        let (a_info, a_part) = user_message("ses_a", "msg_a", "alpha");
        insert_event(
            &con,
            1,
            "ses_a",
            0,
            "session.created.1",
            r#"{"info":{"id":"ses_a"}}"#,
        );
        insert_event(&con, 2, "ses_a", 1, "message.updated.1", &a_info);
        insert_event(&con, 3, "ses_a", 2, "message.part.updated.1", &a_part);
        let (b_info, b_part) = user_message("ses_b", "msg_b", "bravo");
        insert_event(
            &con,
            4,
            "ses_b",
            0,
            "session.created.1",
            r#"{"info":{"id":"ses_b"}}"#,
        );
        insert_event(&con, 5, "ses_b", 1, "message.updated.1", &b_info);
        insert_event(&con, 6, "ses_b", 2, "message.part.updated.1", &b_part);
        drop(con);

        let stamp_a = OpenCode.stamp(&db, "ses_a");
        let stamp_b = OpenCode.stamp(&db, "ses_b");
        assert_eq!(
            stamp_a.1, 2,
            "stamp is that session MAX(seq), not the db file"
        );
        assert_eq!(stamp_b.1, 2);

        let con = open_rw(&db);
        let (b2_info, b2_part) = user_message("ses_b", "msg_b2", "bravo2");
        insert_event(&con, 7, "ses_b", 3, "message.updated.1", &b2_info);
        insert_event(&con, 8, "ses_b", 4, "message.part.updated.1", &b2_part);
        drop(con);

        assert_eq!(
            OpenCode.stamp(&db, "ses_a"),
            stamp_a,
            "writes to B must not change stamp A"
        );
        assert_eq!(OpenCode.stamp(&db, "ses_b").1, 4);

        let _ = std::fs::remove_file(&db);
        let _ = std::fs::remove_dir(db.parent().unwrap());
    }

    #[test]
    fn opencode_timeline_resumes_after_last_seq() {
        let db = temp_db("resume");
        let con = open_rw(&db);
        create_event_table(&con);
        let (info, part) = user_message("ses_a", "msg_1", "hello");
        insert_event(
            &con,
            1,
            "ses_a",
            0,
            "session.created.1",
            r#"{"info":{"id":"ses_a"}}"#,
        );
        insert_event(&con, 2, "ses_a", 1, "message.updated.1", &info);
        insert_event(&con, 3, "ses_a", 2, "message.part.updated.1", &part);
        drop(con);

        let first = crate::timeline("opencode", &db, "ses_a").unwrap();
        assert_eq!(user_texts(&first), ["hello"]);
        assert_eq!(OpenCode.stamp(&db, "ses_a").1, 2);

        let con = open_rw(&db);
        let (info2, part2) = user_message("ses_a", "msg_2", "again");
        insert_event(&con, 4, "ses_a", 3, "message.updated.1", &info2);
        insert_event(&con, 5, "ses_a", 4, "message.part.updated.1", &part2);
        drop(con);

        let appended = crate::timeline("opencode", &db, "ses_a").unwrap();
        assert_eq!(user_texts(&appended), ["hello", "again"]);
        assert_eq!(OpenCode.stamp(&db, "ses_a").1, 4);

        let con = open_rw(&db);
        con.execute("DELETE FROM event WHERE seq > 2", []).unwrap();
        drop(con);

        let replayed = crate::timeline("opencode", &db, "ses_a").unwrap();
        assert_eq!(
            user_texts(&replayed),
            ["hello"],
            "lower max seq must full replay, not keep a stale tail"
        );
        assert_eq!(OpenCode.stamp(&db, "ses_a").1, 2);

        let _ = std::fs::remove_file(&db);
        let _ = std::fs::remove_dir(db.parent().unwrap());
    }

    #[test]
    fn opencode_event_table_maps_reasoning_to_thought() {
        let db = temp_db("reason");
        let con = open_rw(&db);
        create_event_table(&con);
        insert_event(
            &con,
            1,
            "ses_a",
            0,
            "message.updated.1",
            r#"{"info":{"id":"msg_a","role":"assistant","sessionID":"ses_a"}}"#,
        );
        insert_event(
            &con,
            2,
            "ses_a",
            1,
            "message.part.updated.1",
            r#"{"part":{"id":"prt_a","messageID":"msg_a","sessionID":"ses_a","type":"reasoning","text":"think it through"}}"#,
        );
        drop(con);

        let events = crate::timeline("opencode", &db, "ses_a").unwrap();
        let thoughts: Vec<&str> = events
            .iter()
            .filter(|ev| ev.event_type == EventType::AgentThoughtChunk)
            .map(|ev| ev.content.as_str())
            .collect();
        assert_eq!(thoughts, ["think it through"]);

        let _ = std::fs::remove_file(&db);
        let _ = std::fs::remove_dir(db.parent().unwrap());
    }

    #[test]
    fn opencode_reads_event_log_when_message_table_has_no_rows() {
        let db = temp_db("event-only");
        let con = open_rw(&db);
        create_event_table(&con);
        con.execute_batch(
            "CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                time_created INTEGER,
                time_updated INTEGER,
                data TEXT
            )",
        )
        .unwrap();
        let (info, part) = user_message("ses_event", "msg_e", "from the event log");
        insert_event(
            &con,
            1,
            "ses_event",
            0,
            "session.created.1",
            r#"{"info":{"id":"ses_event"}}"#,
        );
        insert_event(&con, 2, "ses_event", 1, "message.updated.1", &info);
        insert_event(&con, 3, "ses_event", 2, "message.part.updated.1", &part);
        drop(con);

        let events = crate::timeline("opencode", &db, "ses_event").unwrap();
        assert_eq!(
            user_texts(&events),
            ["from the event log"],
            "empty message table must not hide the event log"
        );

        let _ = std::fs::remove_file(&db);
        let _ = std::fs::remove_dir(db.parent().unwrap());
    }

    #[test]
    fn opencode_wal_readers_survive_checkpoint() {
        let db = temp_db("wal-readers");
        let con = open_rw(&db);
        create_event_table(&con);
        con.pragma_update(None, "journal_mode", "WAL").unwrap();
        let (info, part) = user_message("ses_w", "msg_w", "wal body");
        insert_event(
            &con,
            1,
            "ses_w",
            0,
            "session.created.1",
            r#"{"info":{"id":"ses_w"}}"#,
        );
        insert_event(&con, 2, "ses_w", 1, "message.updated.1", &info);
        insert_event(&con, 3, "ses_w", 2, "message.part.updated.1", &part);
        drop(con);

        let db = std::sync::Arc::new(db);
        let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mut joins = Vec::new();
        for _ in 0..4 {
            let db = std::sync::Arc::clone(&db);
            let stop = std::sync::Arc::clone(&stop);
            joins.push(std::thread::spawn(move || {
                while !stop.load(std::sync::atomic::Ordering::Relaxed) {
                    let _ = crate::timeline("opencode", &db, "ses_w");
                    let _ = crate::stamp("opencode", &db, "ses_w");
                    let _ = crate::list_meta("opencode", &db, "ses_w");
                }
            }));
        }
        let writer = open_rw(&db);
        for _ in 0..20 {
            let _ = writer.pragma_update(None, "wal_checkpoint", "TRUNCATE");
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        stop.store(true, std::sync::atomic::Ordering::Relaxed);
        for join in joins {
            join.join().expect("opencode reader thread panicked");
        }
        let events = crate::timeline("opencode", &db, "ses_w").unwrap();
        assert_eq!(user_texts(&events), ["wal body"]);
        let _ = std::fs::remove_file(&*db);
        let _ = std::fs::remove_dir(db.parent().unwrap());
    }
}
