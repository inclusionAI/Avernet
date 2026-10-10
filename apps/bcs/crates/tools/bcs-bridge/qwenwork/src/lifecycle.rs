//! Main-request lifecycle. Compact is a pause inside a turn, never a terminal.
use bcs_bridge_core::engine::TurnError;
use serde_json::Value;

#[derive(Default)]
pub(crate) struct Lifecycle {
    compacting: bool,
    ended: bool,
}
impl Lifecycle {
    pub fn complete(&self) -> bool {
        self.ended && !self.compacting
    }

    /// QueryEnd is fenced by request_set_id in transport. Stop and internal
    /// compact completion are not main-task completion.
    pub fn handle(&mut self, hook: &Value) -> Result<bool, TurnError> {
        match hook["hook_event_name"].as_str().unwrap_or_default() {
            "UserPromptSubmit" | "Stop" => {},
            "PreCompact" => {
                self.compacting = true;
                tracing::debug!("qwenwork context compaction started");
            },
            "PostCompact" => {
                self.compacting = false;
                tracing::debug!("qwenwork context compaction finished");
            },
            "QueryEnd" => {
                match hook.get("reason").and_then(Value::as_str) {
                    Some("end_turn" | "max_turns") => {
                        // A main QueryEnd also resolves a failed/interrupted
                        // compact attempt that never emitted PostCompact.
                        self.compacting = false;
                        self.ended = true;
                    },
                    Some("abort") => return Err(TurnError::Aborted),
                    Some(reason) => return Err(TurnError::Protocol(format!("QwenWork main request ended with {reason}"))),
                    None => return Err(TurnError::Protocol("QwenWork QueryEnd missing reason".into())),
                }
            },
            _ => return Ok(false),
        }
        Ok(true)
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn compact_and_stop_never_complete_a_main_request() {
        let mut lifecycle = Lifecycle::default();
        for event in ["PreCompact", "Stop", "PostCompact", "Stop"] {
            lifecycle.handle(&json!({"hook_event_name":event})).unwrap();
            assert!(!lifecycle.complete());
        }
    }

    #[test]
    fn main_query_end_resolves_compact_without_a_post_hook() {
        let mut lifecycle = Lifecycle::default();
        lifecycle.handle(&json!({"hook_event_name":"PreCompact"})).unwrap();
        lifecycle.handle(&json!({"hook_event_name":"QueryEnd","reason":"end_turn"})).unwrap();
        assert!(lifecycle.complete());
    }
}
