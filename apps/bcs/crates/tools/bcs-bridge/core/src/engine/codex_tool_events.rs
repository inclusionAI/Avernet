//! Maps Codex tool lifecycle notifications and reconciles completed-turn snapshots.
use std::collections::HashSet;
use std::path::Path;
use bcs_protocol::stream::{StreamEvent, ToolData, ToolPhase};
use serde_json::{Value, json};
use crate::sse;

#[derive(Default)]
pub(super) struct ToolEvents {
    pending: HashSet<String>,
}

impl ToolEvents {
    pub(super) fn started(&mut self, value: &Value, run: &str, cwd: &Path) -> Option<StreamEvent> {
        let event = map_item_started(value, run, cwd)?;
        let id = value.pointer("/params/item/id")?.as_str()?;
        self.pending.insert(id.to_owned());
        Some(event)
    }

    pub(super) fn completed(&mut self, value: &Value, run: &str, cwd: &Path) -> Option<StreamEvent> {
        let event = map_item_completed(value, run, cwd)?;
        let id = value.pointer("/params/item/id")?.as_str()?;
        self.pending.remove(id);
        Some(event)
    }

    pub(super) fn finish(&mut self, value: &Value, run: &str, cwd: &Path) -> Vec<StreamEvent> {
        if value.pointer("/params/turn/status").and_then(Value::as_str) != Some("completed") {
            return Vec::new();
        }
        let Some(items) = value.pointer("/params/turn/items").and_then(Value::as_array) else {
            return Vec::new();
        };
        let mut events = Vec::new();
        for item in items {
            let Some(id) = item.get("id").and_then(Value::as_str) else { continue; };
            if !self.pending.contains(id) { continue; }
            // An explicitly nonterminal item is not evidence of tool completion.
            if matches!(item.get("status").and_then(Value::as_str), Some("inProgress" | "in_progress" | "running" | "pending")) {
                continue;
            }
            let notification = json!({"params": {"item": item}});
            if let Some(event) = self.completed(&notification, run, cwd) {
                events.push(event);
            }
        }
        events
    }
}

fn map_item_started(value: &Value, run_id: &str, cwd: &Path) -> Option<StreamEvent> {
    let item = value.get("params")?.get("item")?;
    let item_type = item.get("type").and_then(Value::as_str)?;
    let tool_call_id = item.get("id").and_then(Value::as_str)?.to_string();
    let name = tool_item_name(item, item_type)?;
    Some(sse::agent_tool(
        run_id,
        ToolData {
            phase: ToolPhase::Start,
            name: Some(name),
            tool_call_id: Some(tool_call_id),
            is_error: None,
            exit_code: None,
            duration_ms: None,
            cwd: Some(item_string(item, "cwd").unwrap_or_else(|| cwd.to_string_lossy().into_owned())),
            args: Some(tool_item_args(item, item_type, cwd)),
            result: None,
            partial_result: None,
        },
    ))
}

fn map_item_completed(value: &Value, run_id: &str, cwd: &Path) -> Option<StreamEvent> {
    let item = value.get("params")?.get("item")?;
    let item_type = item.get("type").and_then(Value::as_str)?;
    let tool_call_id = item.get("id").and_then(Value::as_str)?.to_string();
    let status = item.get("status").and_then(Value::as_str);
    let is_error = status.is_some_and(|value| value != "completed")
        || item
            .get("exitCode")
            .and_then(Value::as_i64)
            .is_some_and(|value| value != 0)
        || item.get("success").and_then(Value::as_bool) == Some(false)
        || item.get("error").is_some_and(|value| !value.is_null())
        || item.pointer("/result/isError").and_then(Value::as_bool) == Some(true);
    let name = tool_item_name(item, item_type)?;
    let result = if item_type == "commandExecution" {
        item.get("aggregatedOutput")
            .cloned()
            .unwrap_or(Value::String(String::new()))
    } else if item_type == "webSearch" {
        // WebSearch has no generic `result` or `status` field in the app-server schema.
        json!({"query": item.get("query"), "action": item.get("action"), "results": item.get("results")})
    } else {
        // MCP failures carry `result: null` alongside a non-null `error`.
        // A null result deserializes to None and is rejected by BCS V3.
        item.get("result").filter(|value| !value.is_null())
            .or_else(|| item.get("contentItems").filter(|value| !value.is_null()))
            .or_else(|| item.get("error").filter(|value| !value.is_null()))
            .cloned()
            .unwrap_or_else(|| Value::String(String::new()))
    };
    Some(sse::agent_tool(
        run_id,
        ToolData {
            phase: ToolPhase::Result,
            name: Some(name),
            tool_call_id: Some(tool_call_id),
            is_error: Some(is_error),
            exit_code: item.get("exitCode").and_then(Value::as_i64),
            duration_ms: item.get("durationMs").and_then(Value::as_u64),
            cwd: item_string(item, "cwd"),
            args: Some(tool_item_args(item, item_type, cwd)),
            result: Some(result),
            partial_result: None,
        },
    ))
}

pub(super) fn map_tool_output_delta(value: &Value, run_id: &str) -> Option<StreamEvent> {
    let params = value.get("params")?;
    let delta = params.get("delta").and_then(Value::as_str)?;
    Some(sse::agent_tool(
        run_id,
        ToolData {
            phase: ToolPhase::Update,
            name: None,
            tool_call_id: params
                .get("itemId")
                .and_then(Value::as_str)
                .map(str::to_string),
            is_error: Some(false),
            exit_code: None,
            duration_ms: None,
            cwd: None,
            args: None,
            result: None,
            partial_result: Some(Value::String(delta.to_string())),
        },
    ))
}

pub(super) fn map_mcp_progress(value: &Value, run_id: &str) -> Option<StreamEvent> {
    let params = value.get("params")?;
    let partial = params
        .get("message")
        .cloned()
        .or_else(|| params.get("delta").cloned())
        .unwrap_or_else(|| params.clone());
    Some(sse::agent_tool(
        run_id,
        ToolData {
            phase: ToolPhase::Update,
            name: None,
            tool_call_id: params
                .get("itemId")
                .and_then(Value::as_str)
                .map(str::to_string),
            is_error: Some(false),
            exit_code: None,
            duration_ms: None,
            cwd: None,
            args: None,
            result: None,
            partial_result: Some(partial),
        },
    ))
}

fn item_string(item: &Value, key: &str) -> Option<String> {
    item.get(key).and_then(Value::as_str).map(str::to_string)
}

fn mcp_tool_name(item: &Value) -> String {
    let server = item_string(item, "server").unwrap_or_else(|| "mcp".into());
    let tool = item_string(item, "tool").unwrap_or_else(|| "tool".into());
    format!("mcp__{server}__{tool}")
}

fn tool_item_name(item: &Value, item_type: &str) -> Option<String> {
    match item_type {
        "commandExecution" => Some("Bash".into()),
        "mcpToolCall" => Some(mcp_tool_name(item)),
        "dynamicToolCall" => Some("dynamicTool".into()),
        "fileChange" => Some("FileChange".into()),
        "webSearch" => Some("WebSearch".into()),
        _ => None,
    }
}

fn tool_item_args(item: &Value, item_type: &str, cwd: &Path) -> Value {
    match item_type {
        "commandExecution" => json!({
            "command": item.get("command").cloned().unwrap_or(Value::Null),
            "cwd": item.get("cwd").cloned().unwrap_or_else(|| json!(cwd)),
        }),
        "mcpToolCall" | "dynamicToolCall" => item
            .get("arguments")
            .cloned()
            .unwrap_or(Value::Null),
        _ => item.clone(),
    }
}

#[cfg(test)]
#[path = "codex_tool_events_tests.rs"]
mod tests;
