//! 与 bcs-mcp（Python）共享的协同回显契约。
//! 单一事实源见 docs/contract.json（与本 crate 同目录）。任何改动两侧同步并升 VERSION。

use serde::Deserialize;
use serde_json::{Map, Value};

pub const MAGIC_KEY: &str = "__bcs_coordination__";
pub const CONTRACT_VERSION: u64 = 1;

pub const TOOL_ASSIGN_TASK: &str = "bcs_assign_task";
pub const TOOL_SEND_TASK_MESSAGE: &str = "bcs_send_task_message";
pub const TOOL_TASK_COMPLETE: &str = "bcs_task_complete";

#[derive(Debug, Clone, Deserialize)]
pub struct CoordinationCall {
    #[serde(rename = "__bcs_coordination__")]
    pub magic: bool,
    pub v: u64,
    pub tool: String,
    #[serde(default)]
    pub intent_id: Option<String>,
    #[serde(default)]
    pub arguments: Map<String, Value>,
    #[serde(default)]
    pub status: String,
}

impl CoordinationCall {
    pub fn from_stdout(stdout: &str) -> Option<Self> {
        Self::parse_stdout(stdout, false)
    }

    /// Preserve the provider callback's historical v1 normalization. V2 uses
    /// the same strict validation at both intake paths.
    pub fn from_provider_stdout(stdout: &str) -> Option<Self> {
        Self::parse_stdout(stdout, true)
    }

    fn parse_stdout(stdout: &str, legacy_provider: bool) -> Option<Self> {
        for line in stdout.lines() {
            let line = line.trim();
            if !line.contains(MAGIC_KEY) {
                continue;
            }
            if let Some(call) = serde_json::from_str::<Value>(line).ok().and_then(|v| Self::from_value(v, legacy_provider)) {
                return Some(call);
            }
        }

        for (idx, _) in stdout.match_indices('{') {
            let candidate = &stdout[idx..];
            if !candidate.contains(MAGIC_KEY) {
                continue;
            }
            let mut stream = serde_json::Deserializer::from_str(candidate)
                .into_iter::<Value>();
            if let Some(Ok(call)) = stream.next() {
                if let Some(call) = Self::from_value(call, legacy_provider) {
                    return Some(call);
                }
            }
        }
        None
    }

    fn from_value(mut value: Value, legacy_provider: bool) -> Option<Self> {
        let object = value.as_object_mut()?;
        if object.get("v").and_then(Value::as_u64) == Some(1) {
            // This was an unknown extension field before v2, so ignore it in v1.
            object.remove("intent_id");
            if legacy_provider {
                let tool = object.get("tool")?.as_str()?.trim().to_string();
                if tool.is_empty() { return None; }
                object.insert("tool".into(), Value::String(tool));
                object.remove("status");
                if !object.get("arguments").is_some_and(Value::is_object) {
                    object.insert("arguments".into(), Value::Object(Map::new()));
                }
            }
        }
        serde_json::from_value(value).ok().and_then(Self::validate)
    }

    fn validate(call: Self) -> Option<Self> {
        let valid = call.magic && match call.v {
            1 => call.intent_id.is_none(),
            2 => call.status == "stored" && call.arguments.is_empty()
                && matches!(call.tool.as_str(), TOOL_ASSIGN_TASK | TOOL_SEND_TASK_MESSAGE | TOOL_TASK_COMPLETE)
                && call.intent_id.as_deref().is_some_and(|id| {
                    id.strip_prefix("bcs_intent_").is_some_and(|suffix|
                        suffix.len() == 32 && suffix.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
                }),
            _ => false,
        };
        valid.then_some(call)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_valid_echo() {
        let s = r#"{"__bcs_coordination__":true,"v":1,"tool":"bcs_assign_task","arguments":{"target_bot":"X","message":"hi"},"status":"received"}"#;
        let call = CoordinationCall::from_stdout(s).expect("should parse");
        assert_eq!(call.tool, "bcs_assign_task");
        assert_eq!(call.arguments.get("target_bot").and_then(|v| v.as_str()), Some("X"));
    }

    #[test]
    fn extracts_echo_from_noisy_stdout() {
        let s = "some mcporter log line\n{\"__bcs_coordination__\":true,\"v\":1,\"tool\":\"bcs_task_complete\",\"arguments\":{\"summary\":\"done\"},\"status\":\"received\"}\ntrailing log";
        let call = CoordinationCall::from_stdout(s).expect("should locate echo in noise");
        assert_eq!(call.tool, "bcs_task_complete");
    }

    #[test]
    fn extracts_pretty_printed_echo_from_mcporter_stdout() {
        let s = r#"{
  "__bcs_coordination__": true,
  "v": 1,
  "tool": "bcs_assign_task",
  "arguments": {
    "target_bot": "bot_cbde12b9",
    "message": "你在干嘛？"
  },
  "status": "received"
}"#;
        let call = CoordinationCall::from_stdout(s).expect("should parse pretty echo");
        assert_eq!(call.tool, "bcs_assign_task");
        assert_eq!(
            call.arguments.get("target_bot").and_then(|v| v.as_str()),
            Some("bot_cbde12b9")
        );
    }

    #[test]
    fn ignores_non_coordination_stdout() {
        assert!(CoordinationCall::from_stdout("just regular output").is_none());
    }

    #[test]
    fn v2_reference_survives_pretty_printing_and_output_cutoff() {
        let mut value = serde_json::json!({"__bcs_coordination__": true, "v": 2,
            "tool": "bcs_assign_task", "intent_id": "bcs_intent_0123456789abcdef0123456789abcdef",
            "status": "stored"});
        let stdout = format!("log\n{}\n{}", serde_json::to_string_pretty(&value).unwrap(), "x".repeat(8000));
        let call = CoordinationCall::from_stdout(&stdout[..4000]).unwrap();
        assert!(call.intent_id.is_some());
        assert!(call.arguments.is_empty());
        value["arguments"] = serde_json::json!({"message": "must not override stored arguments"});
        assert!(CoordinationCall::from_stdout(&value.to_string()).is_none());
        value.as_object_mut().unwrap().remove("arguments");
        value["intent_id"] = serde_json::json!("../../other");
        assert!(CoordinationCall::from_stdout(&value.to_string()).is_none());
    }

    #[test]
    fn legacy_provider_v1_keeps_lenient_metadata_and_tool_normalization() {
        let value = serde_json::json!({"__bcs_coordination__": true, "v": 1,
            "tool": " bcs_task_complete ", "arguments": {"summary": "done"},
            "status": {"legacy": true}, "intent_id": 123});
        let call = CoordinationCall::from_provider_stdout(&value.to_string()).unwrap();
        assert_eq!(call.tool, "bcs_task_complete");
        assert_eq!(call.arguments["summary"], "done");
        assert!(call.intent_id.is_none());
        let stream = serde_json::json!({"__bcs_coordination__": true, "v": 1,
            "tool": "bcs_task_complete", "arguments": {"summary": "done"}, "intent_id": 123});
        assert!(CoordinationCall::from_stdout(&stream.to_string()).unwrap().intent_id.is_none());
    }

    #[test]
    fn rejects_unknown_version() {
        let s = r#"{"__bcs_coordination__":true,"v":999,"tool":"bcs_assign_task","arguments":{},"status":"received"}"#;
        assert!(CoordinationCall::from_stdout(s).is_none());
    }
}
