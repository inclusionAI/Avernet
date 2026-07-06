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
    pub arguments: Map<String, Value>,
    #[serde(default)]
    pub status: String,
}

impl CoordinationCall {
    pub fn from_stdout(stdout: &str) -> Option<Self> {
        for line in stdout.lines() {
            let line = line.trim();
            if !line.contains(MAGIC_KEY) {
                continue;
            }
            if let Some(call) = Self::parse_candidate(line) {
                return Some(call);
            }
        }

        for (idx, _) in stdout.match_indices('{') {
            let candidate = &stdout[idx..];
            if !candidate.contains(MAGIC_KEY) {
                continue;
            }
            let mut stream = serde_json::Deserializer::from_str(candidate)
                .into_iter::<CoordinationCall>();
            if let Some(Ok(call)) = stream.next() {
                if let Some(call) = Self::validate(call) {
                    return Some(call);
                }
            }
        }
        None
    }

    fn parse_candidate(candidate: &str) -> Option<Self> {
        serde_json::from_str::<CoordinationCall>(candidate)
            .ok()
            .and_then(Self::validate)
    }

    fn validate(call: Self) -> Option<Self> {
        (call.magic && call.v == CONTRACT_VERSION).then_some(call)
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
    fn rejects_unknown_version() {
        let s = r#"{"__bcs_coordination__":true,"v":999,"tool":"bcs_assign_task","arguments":{},"status":"received"}"#;
        assert!(CoordinationCall::from_stdout(s).is_none());
    }
}
