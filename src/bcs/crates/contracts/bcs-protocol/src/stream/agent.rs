//! Engine-neutral strongly-typed `agent` stream payloads.
//!
//! Modeling rule: strong-typed flat routing scalars + `Value` content fields
//! + a full `raw` snapshot. Nested content (args/result/...) stays `Value`
//! so engine-specific shapes are zero-loss and forward-compatible.

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolPhase {
    Start,
    Update,
    Result,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolData {
    pub phase: ToolPhase,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(rename = "toolCallId", default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    #[serde(rename = "isError", default, skip_serializing_if = "Option::is_none")]
    pub is_error: Option<bool>,
    #[serde(rename = "exitCode", default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i64>,
    #[serde(rename = "durationMs", default, skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cwd: Option<String>,
    // content fields kept as Value (zero-loss, forward-compatible)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub args: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(rename = "partialResult", default, skip_serializing_if = "Option::is_none")]
    pub partial_result: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ThinkingData {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delta: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalPhase {
    Requested,
    Resolved,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalData {
    pub phase: ApprovalPhase,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(rename = "approvalId", default, skip_serializing_if = "Option::is_none")]
    pub approval_id: Option<String>,
    #[serde(rename = "toolCallId", default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    // content kept as Value
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub questions: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub answers: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LifecycleData {
    pub phase: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(rename = "agentMode", default, skip_serializing_if = "Option::is_none")]
    pub agent_mode: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PhaseData {
    #[serde(rename = "fromPhase", default, skip_serializing_if = "Option::is_none")]
    pub from_phase: Option<String>,
    #[serde(rename = "toPhase", default, skip_serializing_if = "Option::is_none")]
    pub to_phase: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn tool_data_deserializes_routing_scalars_and_keeps_content_as_value() {
        let v = json!({
            "phase": "result",
            "name": "read",
            "toolCallId": "fc-1",
            "isError": false,
            "result": { "content": [{ "type": "text", "text": "hi" }], "newInner": 1 },
            "exitCode": 0,
            "durationMs": 12,
            "newTopField": "x"
        });
        let t: ToolData = serde_json::from_value(v.clone()).unwrap();
        assert_eq!(t.phase, ToolPhase::Result);
        assert_eq!(t.name.as_deref(), Some("read"));
        assert_eq!(t.tool_call_id.as_deref(), Some("fc-1"));
        assert_eq!(t.is_error, Some(false));
        assert_eq!(t.exit_code, Some(0));
        // 内容字段是 Value:嵌套新增字段零丢失
        assert_eq!(t.result.unwrap()["newInner"], json!(1));
    }

    #[test]
    fn tool_data_optional_scalars_default_to_none() {
        let v = json!({ "phase": "start" });
        let t: ToolData = serde_json::from_value(v).unwrap();
        assert_eq!(t.phase, ToolPhase::Start);
        assert!(t.name.is_none());
        assert!(t.result.is_none());
    }
}
