//! Catalog query language. Tokens come from the published control contract
//! (`desktop/assets/catalog-query.json`, same `catalogQuery` as the schema).

use std::sync::OnceLock;

use regex::Regex;
use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct QueryScheme {
    compare: Vec<String>,
    #[serde(default)]
    operators: Vec<String>,
    #[serde(default)]
    counts: Vec<QueryCountSpec>,
    tokens: Vec<QueryTokenSpec>,
    #[serde(default)]
    scopes: std::collections::BTreeMap<String, QueryScopeSpec>,
}

#[derive(Debug, Deserialize)]
struct QueryScopeSpec {
    #[serde(default)]
    tokens: Vec<QueryTokenSpec>,
}

/// Which list last-token hints apply to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueryScope {
    Catalog,
    Turns,
    Timeline,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct QueryCountSpec {
    flag: String,
    count: String,
    #[allow(dead_code)]
    field: String,
}

/// One highlighted slice of a catalog query (byte offsets).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QuerySpan {
    pub start: usize,
    pub end: usize,
    pub kind: QuerySpanKind,
}

/// Schema token role for catalog search color.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QuerySpanKind {
    Field,
    Value,
    Unknown,
    Operator,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct QueryTokenSpec {
    name: String,
    #[serde(default)]
    role: String,
    #[serde(default)]
    values: Vec<String>,
    #[serde(default)]
    compare: bool,
    #[serde(default)]
    #[allow(dead_code)]
    count_fields: std::collections::BTreeMap<String, String>,
}

/// One catalog-search help row (label plus wrapped values or role).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueryHelpRow {
    pub label: String,
    pub body: String,
}

/// Same token list as TUI `?` / the published `catalogQuery` schema.
pub fn catalog_query_help_rows() -> Vec<QueryHelpRow> {
    query_help_rows(QueryScope::Catalog)
}

/// Token legend for one search box.
pub fn query_help_rows(scope: QueryScope) -> Vec<QueryHelpRow> {
    let scheme = scheme();
    let tokens = scope_tokens(scope);
    let mut rows = Vec::new();
    let mut compare: Vec<&str> = Vec::new();
    let intro = match scope {
        QueryScope::Catalog => "Bare words match title, id, and label. Space is AND.",
        QueryScope::Turns => "Bare words match the turn label and prompt. Space is AND.",
        QueryScope::Timeline => "Bare words match type, tool, and body. Space is AND.",
    };
    rows.push(QueryHelpRow {
        label: String::new(),
        body: intro.to_string(),
    });
    for token in tokens {
        if !token.values.is_empty() {
            rows.push(QueryHelpRow {
                label: format!("{}:", token.name),
                body: token.values.join(", "),
            });
            if scope == QueryScope::Catalog && !scheme.counts.is_empty() && token.name == "has" {
                let pairs = scheme
                    .counts
                    .iter()
                    .map(|c| format!("has:{} {}:>=N", c.flag, c.count))
                    .collect::<Vec<_>>()
                    .join(", ");
                rows.push(QueryHelpRow {
                    label: String::new(),
                    body: pairs,
                });
            }
        } else if token.compare {
            compare.push(token.name.as_str());
        } else {
            let role = token.role.trim_end_matches('.');
            rows.push(QueryHelpRow {
                label: format!("{}:", token.name),
                body: role.to_string(),
            });
        }
    }
    if !compare.is_empty() {
        let names = compare
            .iter()
            .map(|n| format!("{n}:"))
            .collect::<Vec<_>>()
            .join(" ");
        rows.push(QueryHelpRow {
            label: names,
            body: scheme.compare.join("  "),
        });
    }
    let mut ops = scheme.operators.clone();
    ops.extend(["(".into(), ")".into()]);
    rows.push(QueryHelpRow {
        label: ops.join("  "),
        body: String::new(),
    });
    rows
}

/// Plain help body for one search list (tests and schema copy).
pub fn query_help_plain(scope: QueryScope) -> String {
    query_help_rows(scope)
        .into_iter()
        .map(|row| {
            if row.label.is_empty() {
                row.body
            } else if row.body.is_empty() {
                row.label
            } else {
                format!("{}  {}", row.label, row.body)
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn scheme() -> &'static QueryScheme {
    static CELL: OnceLock<QueryScheme> = OnceLock::new();
    CELL.get_or_init(|| {
        serde_json::from_str(include_str!("../assets/catalog-query.json"))
            .expect("catalog-query.json")
    })
}

fn field_names() -> impl Iterator<Item = &'static str> {
    scope_tokens(QueryScope::Catalog)
        .iter()
        .map(|t| t.name.as_str())
}

fn all_field_names() -> &'static [String] {
    static CELL: OnceLock<Vec<String>> = OnceLock::new();
    CELL.get_or_init(|| {
        let mut names = Vec::new();
        for scope in [QueryScope::Catalog, QueryScope::Turns, QueryScope::Timeline] {
            for token in scope_tokens(scope) {
                if !names.iter().any(|have| have == &token.name) {
                    names.push(token.name.clone());
                }
            }
        }
        names
    })
}

fn scope_tokens(scope: QueryScope) -> &'static [QueryTokenSpec] {
    match scope {
        QueryScope::Catalog => scheme().tokens.as_slice(),
        QueryScope::Turns => scope_named("turns"),
        QueryScope::Timeline => scope_named("timeline"),
    }
}

fn scope_named(name: &str) -> &'static [QueryTokenSpec] {
    scheme()
        .scopes
        .get(name)
        .map(|spec| spec.tokens.as_slice())
        .unwrap_or(&[])
}

fn token_values(name: &str) -> &[String] {
    token_values_in(QueryScope::Catalog, name)
}

fn token_values_in(scope: QueryScope, name: &str) -> &[String] {
    scope_tokens(scope)
        .iter()
        .find(|t| t.name == name)
        .map(|t| t.values.as_slice())
        .unwrap_or(&[])
}

pub fn finished_prefix(query: &str) -> String {
    let mut text = query.trim_end().to_string();
    let lower = text.to_ascii_lowercase();
    for field in all_field_names() {
        let needle = format!("{field}:");
        if lower.ends_with(&needle)
            && (lower.len() == needle.len()
                || lower
                    .as_bytes()
                    .get(lower.len() - needle.len() - 1)
                    .is_some_and(|b| b.is_ascii_whitespace()))
        {
            text.truncate(text.len() - needle.len());
            text = text.trim_end().to_string();
            break;
        }
    }
    let low = text.to_ascii_lowercase();
    for op in ["and", "or", "not"] {
        if low == op
            || (low.ends_with(op)
                && low
                    .as_bytes()
                    .get(low.len() - op.len() - 1)
                    .is_some_and(|b| b.is_ascii_whitespace()))
        {
            text.truncate(text.len() - op.len());
            return text.trim_end().to_string();
        }
    }
    text
}

/// Paint offsets for known fields, values, and operators.
///
/// Live box color uses this scanner because the serve matcher has no source
/// offsets.
pub fn highlight_query_spans(query: &str) -> Vec<QuerySpan> {
    let mut spans = Vec::new();
    for caps in highlight_re().captures_iter(query) {
        let start = caps.get(0).map_or(0, |m| m.start());
        if start > 0 {
            let prev = query.as_bytes()[start - 1];
            if !prev.is_ascii_whitespace() && prev != b'(' {
                continue;
            }
        }
        if let Some(op) = caps.name("operator") {
            spans.push(QuerySpan {
                start: op.start(),
                end: op.end(),
                kind: QuerySpanKind::Operator,
            });
            continue;
        }
        if let Some(prohibit) = caps.name("prohibit") {
            spans.push(QuerySpan {
                start: prohibit.start(),
                end: prohibit.end(),
                kind: QuerySpanKind::Operator,
            });
        }
        let Some(field) = caps.name("field") else {
            continue;
        };
        spans.push(QuerySpan {
            start: field.start(),
            end: field.end() + 1,
            kind: QuerySpanKind::Field,
        });
        let Some(value) = caps.name("value") else {
            continue;
        };
        if value.as_str().is_empty() {
            continue;
        }
        let raw = value.as_str();
        let quoted = quoted_value(raw);
        let field_key = field.as_str().to_ascii_lowercase();
        let closed = token_values(&field_key);
        let mut value_start = value.start();
        let mut value_end = value.end();
        if !quoted && closed.is_empty() {
            value_end = extend_open_value(query, value_end);
        }
        if !quoted {
            (value_start, value_end) = trim_value_span(query, value_start, value_end);
        }
        if value_start >= value_end {
            continue;
        }
        let inner = if quoted {
            &query[value_start + 1..value_end - 1]
        } else {
            &query[value_start..value_end]
        };
        if field_key == "has" && !quoted {
            spans.extend(has_value_spans(value_start, value_end, inner, closed));
            continue;
        }
        spans.push(QuerySpan {
            start: value_start,
            end: value_end,
            kind: value_kind(&field_key, inner, closed),
        });
    }
    spans
}

fn has_value_spans(start: usize, end: usize, inner: &str, closed: &[String]) -> Vec<QuerySpan> {
    let (name, cmp) = split_has_value(inner);
    let known = closed.iter().any(|item| item.eq_ignore_ascii_case(&name));
    if !cmp.is_empty() || !known {
        return vec![QuerySpan {
            start,
            end,
            kind: QuerySpanKind::Unknown,
        }];
    }
    vec![QuerySpan {
        start,
        end,
        kind: QuerySpanKind::Value,
    }]
}

fn split_has_value(raw: &str) -> (String, String) {
    match raw.split_once(':') {
        Some((name, rest)) => (name.to_ascii_lowercase(), rest.to_string()),
        None => (raw.to_ascii_lowercase(), String::new()),
    }
}

fn value_kind(field: &str, inner: &str, closed: &[String]) -> QuerySpanKind {
    if closed.is_empty() || closed.iter().any(|item| item.eq_ignore_ascii_case(inner)) {
        return QuerySpanKind::Value;
    }
    if field == "is"
        && matches!(
            inner.to_ascii_lowercase().as_str(),
            "cancelled" | "canceled"
        )
    {
        return QuerySpanKind::Value;
    }
    QuerySpanKind::Unknown
}

fn quoted_value(raw: &str) -> bool {
    raw.len() >= 2 && raw.starts_with('"') && raw.ends_with('"')
}

fn is_field_token(word: &str) -> bool {
    word.split_once(':')
        .is_some_and(|(head, _)| field_names().any(|name| name.eq_ignore_ascii_case(head)))
}

fn extend_open_value(query: &str, mut end: usize) -> usize {
    let bytes = query.as_bytes();
    while end < bytes.len() {
        if bytes[end] == b')' {
            return end;
        }
        let mut i = end;
        while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
            i += 1;
        }
        if i == end || i == bytes.len() {
            return end;
        }
        let mut j = i;
        while j < bytes.len() && !matches!(bytes[j], b' ' | b'\t' | b')') {
            j += 1;
        }
        let word = &query[i..j];
        if matches!(word.to_ascii_lowercase().as_str(), "and" | "or" | "not") {
            return end;
        }
        if is_field_token(word) {
            return end;
        }
        end = j;
    }
    end
}

fn trim_value_span(query: &str, mut start: usize, mut end: usize) -> (usize, usize) {
    let bytes = query.as_bytes();
    while start < end && matches!(bytes[start], b' ' | b'\t') {
        start += 1;
    }
    while end > start && matches!(bytes[end - 1], b' ' | b'\t') {
        end -= 1;
    }
    (start, end)
}

fn highlight_re() -> &'static Regex {
    static CELL: OnceLock<Regex> = OnceLock::new();
    CELL.get_or_init(|| {
        let words: Vec<String> = scheme()
            .operators
            .iter()
            .filter(|op| op.as_str() != "-")
            .map(|op| regex::escape(op))
            .collect();
        let words = if words.is_empty() {
            vec!["AND".into(), "OR".into(), "NOT".into()]
        } else {
            words
        };
        let fields: String = all_field_names()
            .iter()
            .map(|name| regex::escape(name))
            .collect::<Vec<_>>()
            .join("|");
        let pat = format!(
            r#"(?P<operator>\b(?:{})\b)|(?P<prohibit>-)?(?P<field>(?i:{})):(?P<value>\s*"[^"]*"|\s*[^\s)]*)"#,
            words.join("|"),
            fields
        );
        Regex::new(&pat).expect("catalog highlight pattern")
    })
}

pub fn suggest_last_token(
    query: &str,
    models: &[String],
    paths: &[String],
    tags: &[String],
) -> Vec<String> {
    suggest_scope_token_tags(QueryScope::Catalog, query, models, paths, &[], tags)
}

pub fn suggest_scope_token(
    scope: QueryScope,
    query: &str,
    models: &[String],
    paths: &[String],
    tools: &[String],
) -> Vec<String> {
    suggest_scope_token_tags(scope, query, models, paths, tools, &[])
}

fn suggest_scope_token_tags(
    scope: QueryScope,
    query: &str,
    models: &[String],
    paths: &[String],
    tools: &[String],
    tags: &[String],
) -> Vec<String> {
    let token = last_token(query);
    if token.is_empty() {
        return Vec::new();
    }
    if !token.contains(':') {
        let prefix = token.to_ascii_lowercase();
        return scope_tokens(scope)
            .iter()
            .map(|t| t.name.as_str())
            .filter(|n| n.starts_with(&prefix))
            .map(|n| format!("{n}:"))
            .collect();
    }
    let (field, rest) = token.split_once(':').unwrap_or(("", ""));
    let key = field.to_ascii_lowercase();
    if key == "tag" && scope == QueryScope::Catalog {
        return suggest_tag_token(rest, tags);
    }
    let prefix = rest.to_ascii_lowercase();
    values_for_field_in(scope, &key, models, paths, tools, tags)
        .into_iter()
        .filter(|v| v.to_ascii_lowercase().starts_with(&prefix))
        .map(|v| format!("{key}:{v}"))
        .collect()
}

fn suggest_tag_token(rest: &str, tags: &[String]) -> Vec<String> {
    let (head, last) = rest.rsplit_once(',').map_or(("", rest), |(h, t)| (h, t));
    let prefix = last.to_ascii_lowercase();
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for tag in tags {
        let name = tag.trim();
        if name.is_empty() || !seen.insert(name.to_ascii_lowercase()) {
            continue;
        }
        if !name.to_ascii_lowercase().starts_with(&prefix) {
            continue;
        }
        if head.is_empty() && !rest.contains(',') {
            out.push(format!("tag:{name}"));
        } else {
            out.push(format!("tag:{head},{name}"));
        }
    }
    out
}

pub fn apply_suggestion(query: &str, suggestion: &str) -> String {
    let stripped = query.trim_end();
    if stripped.is_empty() {
        return format!("{suggestion} ");
    }
    match stripped.rsplit_once(char::is_whitespace) {
        Some((head, _)) => format!("{head} {suggestion} "),
        None => format!("{suggestion} "),
    }
}
fn last_token(query: &str) -> String {
    let text = query.trim_end();
    text.rsplit_once(char::is_whitespace)
        .map(|(_, t)| t.to_string())
        .unwrap_or_else(|| text.to_string())
}

fn values_for_field_in(
    scope: QueryScope,
    field: &str,
    models: &[String],
    paths: &[String],
    tools: &[String],
    tags: &[String],
) -> Vec<String> {
    let closed = token_values_in(scope, field);
    if !closed.is_empty() {
        return closed.to_vec();
    }
    if scope_tokens(scope)
        .iter()
        .any(|t| t.name == field && t.compare)
    {
        return scheme().compare.clone();
    }
    if field == "tool" && scope == QueryScope::Timeline {
        return unique_nonempty(tools);
    }
    if scope != QueryScope::Catalog {
        return Vec::new();
    }
    match field {
        "model" => unique_nonempty(models),
        "in" => unique_nonempty(paths)
            .into_iter()
            .map(|p| short_path(&p))
            .collect(),
        "tag" => unique_nonempty(tags),
        _ => Vec::new(),
    }
}

fn unique_nonempty(items: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    for item in items {
        let t = item.trim();
        if t.is_empty() || out.iter().any(|have: &String| have == t) {
            continue;
        }
        out.push(t.to_string());
    }
    out
}

fn short_path(path: &str) -> String {
    if let Some(home) = dirs_next_home() {
        if path == home || path.starts_with(&(home.clone() + "/")) {
            return format!("~{}", &path[home.len()..]);
        }
    }
    path.to_string()
}

fn dirs_next_home() -> Option<String> {
    std::env::var("HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("USERPROFILE").ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn highlight_spans_use_schema() {
        let marks = highlight_query_spans("has:goal AND has:gooals");
        let kinds: Vec<_> = marks
            .iter()
            .map(|s| (&"has:goal AND has:gooals"[s.start..s.end], s.kind))
            .collect();
        assert_eq!(
            kinds,
            vec![
                ("has:", QuerySpanKind::Field),
                ("goal", QuerySpanKind::Value),
                ("AND", QuerySpanKind::Operator),
                ("has:", QuerySpanKind::Field),
                ("gooals", QuerySpanKind::Unknown),
            ]
        );
        let lower = highlight_query_spans("and not has:goal");
        assert!(!lower.iter().any(|s| s.kind == QuerySpanKind::Operator));
        let mixed = highlight_query_spans("aNd has:goal");
        assert!(!mixed.iter().any(|s| s.kind == QuerySpanKind::Operator));
        let canceled = highlight_query_spans("is:canceled");
        assert_eq!(canceled.last().map(|s| s.kind), Some(QuerySpanKind::Value));
    }

    #[test]
    fn query_help_plain_is_scoped() {
        let catalog = query_help_plain(QueryScope::Catalog);
        assert!(catalog.contains("after:"));
        assert!(catalog.contains("is:"));
        let timeline = query_help_plain(QueryScope::Timeline);
        assert!(timeline.contains("tool:"));
        assert!(timeline.contains("turn:"));
        assert!(!timeline.contains("after:"));
        let turns = query_help_plain(QueryScope::Turns);
        assert!(turns.contains("has:"));
        assert!(turns.contains("subagent"));
        assert!(!turns.contains("after:"));
    }

    #[test]
    fn catalog_query_help_lists_schema_tokens() {
        let rows = catalog_query_help_rows();
        let blob = rows
            .iter()
            .map(|r| format!("{} {}", r.label, r.body))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(blob.contains("is:"));
        assert!(blob.contains("running"));
        assert!(blob.contains("has:"));
        assert!(blob.contains("workflows"));
        assert!(blob.contains("in:"));
        assert!(blob.contains("duration:"));
        assert!(blob.contains("OR"));
        assert!(blob.contains("has:plan"));
        assert!(blob.contains("plans:>=N"));
    }

    #[test]
    fn suggest_last_token_lists_has_flags() {
        assert_eq!(
            suggest_last_token("has:", &[], &[], &[]),
            vec![
                "has:workflow",
                "has:note",
                "has:goal",
                "has:plan",
                "has:subagent",
                "has:task",
                "has:job",
                "has:schedule",
                "has:error",
                "has:failure",
                "has:diff",
                "has:compaction",
                "has:doom",
                "has:git",
                "has:context",
            ]
        );
        assert!(suggest_last_token("", &[], &[], &[]).is_empty());
        assert!(suggest_last_token("   ", &[], &[], &[]).is_empty());
    }

    #[test]
    fn suggest_timeline_and_turns_tokens() {
        assert_eq!(
            suggest_scope_token(QueryScope::Timeline, "has:", &[], &[], &[]),
            vec!["has:error"]
        );
        assert_eq!(
            suggest_scope_token(QueryScope::Timeline, "is:", &[], &[], &[]),
            vec![
                "is:tool",
                "is:user",
                "is:assistant",
                "is:error",
                "is:session",
                "is:subagent",
                "is:background",
                "is:workflow",
            ]
        );
        assert_eq!(
            suggest_scope_token(QueryScope::Turns, "has:", &[], &[], &[]),
            vec!["has:error", "has:subagent"]
        );
        assert_eq!(
            suggest_scope_token(QueryScope::Turns, "t", &[], &[], &[]),
            vec!["tools:"]
        );
        assert_eq!(
            suggest_scope_token(QueryScope::Timeline, "t", &[], &[], &[]),
            vec!["tool:", "turn:"]
        );
        assert_eq!(
            suggest_scope_token(QueryScope::Timeline, "d", &[], &[], &[]),
            vec!["duration:"]
        );
        assert_eq!(
            suggest_scope_token(
                QueryScope::Timeline,
                "tool:",
                &[],
                &[],
                &["read_file".into(), "grep".into()]
            ),
            vec!["tool:read_file", "tool:grep"]
        );
        assert!(
            suggest_scope_token(QueryScope::Timeline, "i", &[], &[], &[])
                .iter()
                .all(|h| h != "in:")
        );
    }
}
