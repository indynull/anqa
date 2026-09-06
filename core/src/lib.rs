//! Typed session ingest. Every harness store implements [`store::Store`].

pub mod event;
pub mod jsonl;
pub mod overview;
pub mod scan;
pub mod store;
pub mod stores;
pub mod text;
pub mod walk;

use event::{Event, FileStamp, ListMeta, SessionLocator};
use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock, Mutex};

const TIMELINE_CACHE_CAP: usize = 32;

struct TimelineEntry {
    stamp: FileStamp,
    events: Arc<[Event]>,
}

struct TimelineCache {
    entries: HashMap<String, TimelineEntry>,
    order: VecDeque<String>,
}

impl TimelineCache {
    fn new() -> Self {
        Self {
            entries: HashMap::new(),
            order: VecDeque::new(),
        }
    }

    fn cap() -> usize {
        if cfg!(test) {
            2
        } else {
            TIMELINE_CACHE_CAP
        }
    }

    fn get(&mut self, key: &str, stamp: FileStamp) -> Option<Arc<[Event]>> {
        let hit = self
            .entries
            .get(key)
            .is_some_and(|entry| entry.stamp == stamp);
        if !hit {
            return None;
        }
        self.touch(key);
        Some(Arc::clone(&self.entries[key].events))
    }

    fn touch(&mut self, key: &str) {
        self.order.retain(|held| held != key);
        self.order.push_back(key.to_string());
    }

    fn insert(&mut self, key: String, entry: TimelineEntry) {
        if self.entries.contains_key(&key) {
            self.order.retain(|held| held != &key);
        }
        self.order.push_back(key.clone());
        self.entries.insert(key, entry);
        while self.entries.len() > Self::cap() {
            if let Some(old) = self.order.pop_front() {
                self.entries.remove(&old);
            } else {
                break;
            }
        }
    }

    #[cfg(test)]
    fn len(&self) -> usize {
        self.entries.len()
    }

    #[cfg(test)]
    fn contains_key(&self, key: &str) -> bool {
        self.entries.contains_key(key)
    }

    #[cfg(test)]
    fn events(&self, key: &str) -> Option<&Arc<[Event]>> {
        self.entries.get(key).map(|entry| &entry.events)
    }

    #[cfg(test)]
    fn clear(&mut self) {
        self.entries.clear();
        self.order.clear();
    }
}

static TIMELINE: LazyLock<Mutex<TimelineCache>> =
    LazyLock::new(|| Mutex::new(TimelineCache::new()));

fn cache_key(harness: &str, locator: &Path, session_id: &str) -> String {
    format!("{harness}\0{}\0{session_id}", locator.display())
}

pub fn discover(harness: &str, roots: &[PathBuf]) -> Result<Vec<SessionLocator>, String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    Ok(store.discover(roots))
}

pub fn list_meta(harness: &str, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    store.list_meta(locator, session_id)
}

pub fn detail_meta(harness: &str, locator: &Path, session_id: &str) -> Result<ListMeta, String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    store.detail_meta(locator, session_id)
}

fn cached_timeline(
    harness: &str,
    locator: &Path,
    session_id: &str,
) -> Result<(FileStamp, Arc<[Event]>), String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    let stamp = store.stamp(locator, session_id);
    let key = cache_key(harness, locator, session_id);
    if let Ok(mut guard) = TIMELINE.lock() {
        if let Some(evs) = guard.get(&key, stamp) {
            return Ok((stamp, evs));
        }
    }
    let mut evs = store.timeline(locator, session_id)?;
    Event::carry_turn_numbers(&mut evs);
    let evs: Arc<[Event]> = evs.into();
    if let Ok(mut guard) = TIMELINE.lock() {
        guard.insert(
            key,
            TimelineEntry {
                stamp,
                events: Arc::clone(&evs),
            },
        );
    }
    Ok((stamp, evs))
}

fn page_slice(events: &[Event], offset: usize, limit: usize) -> Vec<Event> {
    if offset >= events.len() || limit == 0 {
        return Vec::new();
    }
    let end = (offset + limit).min(events.len());
    events[offset..end].to_vec()
}

pub fn timeline(harness: &str, locator: &Path, session_id: &str) -> Result<Vec<Event>, String> {
    Ok(cached_timeline(harness, locator, session_id)?.1.to_vec())
}

pub fn timeline_page(
    harness: &str,
    locator: &Path,
    session_id: &str,
    offset: usize,
    limit: usize,
) -> Result<(Vec<Event>, usize), String> {
    let evs = cached_timeline(harness, locator, session_id)?.1;
    Ok((page_slice(&evs, offset, limit), evs.len()))
}

pub fn stamp(harness: &str, locator: &Path, session_id: &str) -> Result<FileStamp, String> {
    let store = store::by_id(harness).ok_or_else(|| format!("unknown harness: {harness}"))?;
    Ok(store.stamp(locator, session_id))
}

pub fn overview(
    harness: &str,
    locator: &Path,
    session_id: &str,
) -> Result<overview::Overview, String> {
    let evs = cached_timeline(harness, locator, session_id)?.1;
    Ok(overview::Overview::from_events(&evs))
}

#[cfg(feature = "extension-module")]
mod pybind {
    use super::*;
    use pyo3::prelude::*;
    use pyo3::types::{PyDict, PyList};

    fn event_dict<'py>(py: Python<'py>, ev: &Event) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("index", ev.index)?;
        d.set_item("event_type", ev.event_type.as_str())?;
        d.set_item("timestamp", ev.timestamp)?;
        d.set_item("content", ev.content.as_str())?;
        d.set_item("raw", ev.raw.as_str())?;
        d.set_item("tool_name", ev.tool_name.as_str())?;
        d.set_item("tool_call_id", ev.tool_call_id.as_str())?;
        d.set_item("is_error", ev.is_error)?;
        d.set_item("update_index", ev.update_index)?;
        d.set_item("prompt_index", ev.prompt_index)?;
        d.set_item("turn_number", ev.turn_number)?;
        d.set_item("child_session_id", ev.child_session_id.as_str())?;
        d.set_item("subagent_type", ev.subagent_type.as_str())?;
        d.set_item("description", ev.description.as_str())?;
        let imgs = PyList::empty(py);
        for blob in &ev.images {
            imgs.append(pyo3::types::PyBytes::new(py, blob))?;
        }
        d.set_item("images", imgs)?;
        Ok(d)
    }

    #[pyfunction]
    fn keep_updates_line(line: &[u8]) -> bool {
        crate::scan::keep_updates_line(line)
    }

    #[pyfunction]
    fn filter_updates(data: &[u8]) -> Vec<Vec<u8>> {
        crate::scan::filter_updates(data)
    }

    #[pyfunction]
    fn find_sessions(root: &str) -> Vec<String> {
        crate::walk::find_sessions(Path::new(root))
            .into_iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect()
    }

    #[pyfunction]
    fn looks_like_session_dir(path: &str) -> bool {
        crate::walk::looks_like_session_dir(Path::new(path))
    }

    #[pyfunction]
    fn find_files(root: &str, suffix: &str, name_prefix: &str) -> Vec<String> {
        crate::walk::find_files(Path::new(root), suffix, name_prefix)
            .into_iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect()
    }

    #[pyfunction]
    fn store_ids() -> Vec<&'static str> {
        crate::store::ids().to_vec()
    }

    #[pyfunction]
    fn store_discover<'py>(
        py: Python<'py>,
        harness: &str,
        roots: Vec<String>,
    ) -> PyResult<Bound<'py, PyList>> {
        let paths: Vec<PathBuf> = roots.into_iter().map(PathBuf::from).collect();
        let found = super::discover(harness, &paths)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let list = PyList::empty(py);
        for loc in found {
            let d = PyDict::new(py);
            d.set_item("harness", loc.harness)?;
            d.set_item("session_id", loc.session_id)?;
            d.set_item("locator", loc.locator.to_string_lossy().as_ref())?;
            d.set_item("cwd", loc.cwd)?;
            list.append(d)?;
        }
        Ok(list)
    }

    fn meta_dict<'py>(py: Python<'py>, meta: super::ListMeta) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("session_id", meta.session_id)?;
        d.set_item("locator", meta.locator.to_string_lossy().as_ref())?;
        d.set_item("model_id", meta.model_id)?;
        d.set_item("title", meta.title)?;
        d.set_item("created_at", meta.created_at)?;
        d.set_item("updated_at", meta.updated_at)?;
        d.set_item("duration_seconds", meta.duration_seconds)?;
        d.set_item("tool_call_count", meta.tool_call_count)?;
        d.set_item("turn_outcome", meta.turn_outcome)?;
        d.set_item("harness", meta.harness)?;
        d.set_item("harness_version", meta.harness_version)?;
        d.set_item("run_dir", meta.run_dir)?;
        d.set_item("num_events", meta.num_events)?;
        d.set_item("has_subagents", meta.has_subagents)?;
        d.set_item("subagent_count", meta.subagent_count)?;
        d.set_item("context_tokens_used", meta.context_tokens_used)?;
        d.set_item("context_window_usage_pct", meta.context_window_usage_pct)?;
        d.set_item("context_window_tokens", meta.context_window_tokens)?;
        d.set_item("turn_count", meta.turn_count)?;
        d.set_item("error_count", meta.error_count)?;
        d.set_item("tool_failure_count", meta.tool_failure_count)?;
        d.set_item("lines_added", meta.lines_added)?;
        d.set_item("lines_removed", meta.lines_removed)?;
        d.set_item("compaction_count", meta.compaction_count)?;
        d.set_item("doom_loop_warnings", meta.doom_loop_warnings)?;
        d.set_item("task_id", meta.task_id)?;
        d.set_item("reasoning_effort", meta.reasoning_effort)?;
        d.set_item("num_messages", meta.num_messages)?;
        Ok(d)
    }

    #[pyfunction]
    fn store_list_meta<'py>(
        py: Python<'py>,
        harness: &str,
        locator: &str,
        session_id: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let meta = super::list_meta(harness, Path::new(locator), session_id)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        meta_dict(py, meta)
    }

    #[pyfunction]
    fn store_detail_meta<'py>(
        py: Python<'py>,
        harness: &str,
        locator: &str,
        session_id: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let meta = super::detail_meta(harness, Path::new(locator), session_id)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        meta_dict(py, meta)
    }

    #[pyfunction]
    fn store_timeline<'py>(
        py: Python<'py>,
        harness: &str,
        locator: &str,
        session_id: &str,
    ) -> PyResult<Bound<'py, PyList>> {
        let evs = super::timeline(harness, Path::new(locator), session_id)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        let list = PyList::empty(py);
        for ev in evs {
            list.append(event_dict(py, &ev)?)?;
        }
        Ok(list)
    }

    #[pyfunction]
    fn store_timeline_page<'py>(
        py: Python<'py>,
        harness: &str,
        locator: &str,
        session_id: &str,
        offset: usize,
        limit: usize,
    ) -> PyResult<Bound<'py, PyDict>> {
        let (page, total) =
            super::timeline_page(harness, Path::new(locator), session_id, offset, limit)
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        let events = PyList::empty(py);
        for ev in page {
            events.append(event_dict(py, &ev)?)?;
        }
        let d = PyDict::new(py);
        d.set_item("events", events)?;
        d.set_item("total", total)?;
        Ok(d)
    }

    #[pyfunction]
    fn store_stamp(
        harness: &str,
        locator: &str,
        session_id: &str,
    ) -> PyResult<(f64, u64, u64, u64)> {
        super::stamp(harness, Path::new(locator), session_id)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)
    }

    fn count_list<'py>(
        py: Python<'py>,
        rows: &[crate::overview::CountRow],
    ) -> PyResult<Bound<'py, PyList>> {
        let list = PyList::empty(py);
        for row in rows {
            let d = PyDict::new(py);
            d.set_item("id", row.id.as_str())?;
            d.set_item("count", row.count)?;
            list.append(d)?;
        }
        Ok(list)
    }

    fn turn_dict<'py>(
        py: Python<'py>,
        turn: &crate::overview::Turn,
    ) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("turnIndex", turn.turn_index)?;
        d.set_item("turnNumber", turn.turn_number)?;
        d.set_item("promptIndex", turn.prompt_index)?;
        d.set_item("outcome", turn.outcome.as_str())?;
        d.set_item("open", turn.open)?;
        d.set_item("label", turn.label.as_str())?;
        d.set_item("summary", turn.summary.as_str())?;
        d.set_item("userEventIndex", turn.user_event_index)?;
        d.set_item("assistantSummary", turn.assistant_summary.as_str())?;
        d.set_item("assistantEventIndex", turn.assistant_event_index)?;
        d.set_item("eventCount", turn.event_count)?;
        d.set_item("toolCallCount", turn.tool_call_count)?;
        d.set_item("toolErrorCount", turn.tool_error_count)?;
        d.set_item("userCount", turn.user_count)?;
        d.set_item("assistantCount", turn.assistant_count)?;
        d.set_item("errorEventCount", turn.error_event_count)?;
        d.set_item("firstIndex", turn.first_index)?;
        d.set_item("lastIndex", turn.last_index)?;
        d.set_item("durationSeconds", turn.duration_seconds)?;
        Ok(d)
    }

    #[pyfunction]
    fn store_overview<'py>(
        py: Python<'py>,
        harness: &str,
        locator: &str,
        session_id: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let ov = super::overview(harness, Path::new(locator), session_id)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        let turns = PyList::empty(py);
        for turn in &ov.turns {
            turns.append(turn_dict(py, turn)?)?;
        }
        let bookends = PyList::empty(py);
        for ev in &ov.bookends {
            bookends.append(event_dict(py, ev)?)?;
        }
        let stats = PyDict::new(py);
        stats.set_item("eventTypes", count_list(py, &ov.event_types)?)?;
        stats.set_item("tools", count_list(py, &ov.tools)?)?;
        let d = PyDict::new(py);
        d.set_item("numEvents", ov.num_events)?;
        d.set_item("turns", turns)?;
        d.set_item("stats", stats)?;
        d.set_item("subagentCount", ov.subagent_count)?;
        d.set_item("bookends", bookends)?;
        Ok(d)
    }

    #[pymodule]
    fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(keep_updates_line, m)?)?;
        m.add_function(wrap_pyfunction!(filter_updates, m)?)?;
        m.add_function(wrap_pyfunction!(find_sessions, m)?)?;
        m.add_function(wrap_pyfunction!(looks_like_session_dir, m)?)?;
        m.add_function(wrap_pyfunction!(find_files, m)?)?;
        m.add_function(wrap_pyfunction!(store_ids, m)?)?;
        m.add_function(wrap_pyfunction!(store_discover, m)?)?;
        m.add_function(wrap_pyfunction!(store_list_meta, m)?)?;
        m.add_function(wrap_pyfunction!(store_detail_meta, m)?)?;
        m.add_function(wrap_pyfunction!(store_timeline, m)?)?;
        m.add_function(wrap_pyfunction!(store_timeline_page, m)?)?;
        m.add_function(wrap_pyfunction!(store_stamp, m)?)?;
        m.add_function(wrap_pyfunction!(store_overview, m)?)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::EventType;
    use std::fs;
    use std::io::Write;
    use std::path::{Path, PathBuf};
    use std::sync::Arc;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn write_pi_session(dir: &Path, sid: &str, prompt: &str) -> PathBuf {
        fs::create_dir_all(dir).unwrap();
        let path = dir.join(format!("{sid}.jsonl"));
        let mut file = fs::File::create(&path).unwrap();
        writeln!(file, r#"{{"type":"session","id":"{sid}"}}"#).unwrap();
        writeln!(
            file,
            r#"{{"type":"message","message":{{"role":"user","content":[{{"type":"text","text":"{prompt}"}}]}}}}"#
        )
        .unwrap();
        path
    }

    fn user_text(events: &[Event]) -> &str {
        events
            .iter()
            .find(|ev| ev.event_type == EventType::UserMessageChunk)
            .map(|ev| ev.content.as_str())
            .unwrap_or("")
    }

    #[test]
    fn timeline_cache_is_arc_and_bounded() {
        let root = std::env::temp_dir().join(format!(
            "anqa-tl-cache-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let sess_a = write_pi_session(&root, "sess-a", "alpha");
        let sess_b = write_pi_session(&root, "sess-b", "bravo");
        let sess_c = write_pi_session(&root, "sess-c", "charlie");
        TIMELINE.lock().unwrap().clear();

        let evs_a = crate::timeline("pi", &sess_a, "sess-a").unwrap();
        assert_eq!(user_text(&evs_a), "alpha");
        let evs_b = crate::timeline("pi", &sess_b, "sess-b").unwrap();
        assert_eq!(user_text(&evs_b), "bravo");

        let key_a = cache_key("pi", &sess_a, "sess-a");
        let key_b = cache_key("pi", &sess_b, "sess-b");
        let held_a = {
            let guard = TIMELINE.lock().unwrap();
            assert_eq!(guard.len(), 2);
            assert!(guard.contains_key(&key_a));
            Arc::clone(guard.events(&key_a).unwrap())
        };
        let held_b = {
            let guard = TIMELINE.lock().unwrap();
            Arc::clone(guard.events(&key_b).unwrap())
        };

        let (page, total) = crate::timeline_page("pi", &sess_a, "sess-a", 0, 1).unwrap();
        assert_eq!(page.len(), 1);
        assert_eq!(total, evs_a.len());
        {
            let guard = TIMELINE.lock().unwrap();
            assert_eq!(guard.len(), 2);
            let hit = guard.events(&key_a).unwrap();
            assert!(Arc::ptr_eq(hit, &held_a), "page hit must reuse the Arc");
        }

        let evs_c = crate::timeline("pi", &sess_c, "sess-c").unwrap();
        assert_eq!(user_text(&evs_c), "charlie");
        {
            let guard = TIMELINE.lock().unwrap();
            assert_eq!(guard.len(), 2, "cap evicts the coldest entry");
            assert!(
                guard.contains_key(&key_a),
                "page hit keeps A as the newest entry"
            );
            assert!(
                !guard.contains_key(&key_b),
                "ingest C must evict B so the next B rereads"
            );
        }

        let evs_b2 = crate::timeline("pi", &sess_b, "sess-b").unwrap();
        assert_eq!(user_text(&evs_b2), "bravo");
        {
            let guard = TIMELINE.lock().unwrap();
            let again = guard.events(&key_b).unwrap();
            assert!(
                !Arc::ptr_eq(again, &held_b),
                "reread must allocate a new Arc"
            );
        }

        let _ = fs::remove_dir_all(&root);
    }
}
