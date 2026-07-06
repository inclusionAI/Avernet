//! Boundary parser: raw JSON frame -> strongly-typed `StreamEvent`.
//!
//! Unknown top-level event / unknown stream / known-stream parse failure all
//! fall back to an `Unknown` variant with the full `raw` retained, plus a
//! bounded WARN log. Full raw goes only to an access-controlled audit sink.

use serde_json::Value;
use tracing::warn;

use super::agent::{ApprovalData, LifecycleData, PhaseData, ThinkingData, ToolData};
use super::event::{AgentData, AgentEvent, ChatEvent, ChatState, StreamEvent};

/// Bounded metadata for a frame: byte size + top-level key names. No content.
fn frame_meta(raw: &Value) -> (usize, Vec<String>) {
    let bytes = serde_json::to_string(raw).map(|s| s.len()).unwrap_or(0);
    let keys = raw
        .as_object()
        .map(|m| m.keys().cloned().collect())
        .unwrap_or_default();
    (bytes, keys)
}

/// Send the full raw frame to an access-controlled, sampled audit sink.
/// This round: a dedicated trace target distinct from general WARN logs.
/// TODO(stream-audit): replace with a sampled, retention-bounded sink.
pub fn audit_raw(raw: &Value) {
    tracing::trace!(target: "stream_audit", raw = %raw, "stream raw frame");
}

pub fn parse_stream_event(event: &str, data: Value) -> StreamEvent {
    match event {
        "agent" => parse_agent(data),
        "chat" => parse_chat(data),
        "ping" => StreamEvent::Ping {
            ts: data.get("ts").and_then(Value::as_u64),
        },
        other => {
            let (bytes, keys) = frame_meta(&data);
            warn!(event = other, bytes, ?keys, "unknown top-level stream event");
            audit_raw(&data);
            StreamEvent::Unknown {
                event: other.to_string(),
                raw: data,
            }
        }
    }
}

fn str_field(data: &Value, key: &str) -> Option<String> {
    data.get(key).and_then(Value::as_str).map(str::to_string)
}

fn parse_agent(data: Value) -> StreamEvent {
    let run_id = match str_field(&data, "runId") {
        Some(r) => r,
        None => {
            let (bytes, keys) = frame_meta(&data);
            warn!(bytes, ?keys, "agent event missing runId");
            audit_raw(&data);
            return StreamEvent::Unknown {
                event: "agent".to_string(),
                raw: data,
            };
        }
    };
    let stream = match str_field(&data, "stream") {
        Some(s) => s,
        None => {
            let (bytes, keys) = frame_meta(&data);
            warn!(bytes, ?keys, "agent event missing stream");
            audit_raw(&data);
            return StreamEvent::Unknown {
                event: "agent".to_string(),
                raw: data,
            };
        }
    };
    let seq = data.get("seq").and_then(Value::as_u64);
    let ts = data.get("ts").and_then(Value::as_u64);
    let session_key = str_field(&data, "sessionKey");

    // The stream-specific data is the frame itself (flat layout in captures).
    let agent_data = parse_agent_data(&stream, &data);
    StreamEvent::Agent(AgentEvent {
        run_id,
        seq,
        ts,
        session_key,
        data: agent_data,
        raw: data,
    })
}

fn parse_agent_data(stream: &str, data: &Value) -> AgentData {
    macro_rules! typed {
        ($ty:ty, $variant:path) => {
            match serde_json::from_value::<$ty>(data.clone()) {
                Ok(parsed) => $variant(parsed),
                Err(e) => {
                    let (bytes, keys) = frame_meta(data);
                    warn!(stream, %e, bytes, ?keys, "known stream parse failed");
                    audit_raw(data);
                    AgentData::Unknown {
                        stream: stream.to_string(),
                        raw: data.clone(),
                    }
                }
            }
        };
    }
    match stream {
        "tool" => typed!(ToolData, AgentData::Tool),
        "thinking" => typed!(ThinkingData, AgentData::Thinking),
        "approval" => typed!(ApprovalData, AgentData::Approval),
        "lifecycle" => typed!(LifecycleData, AgentData::Lifecycle),
        "phase" => typed!(PhaseData, AgentData::Phase),
        other => {
            let (bytes, keys) = frame_meta(data);
            warn!(stream = other, bytes, ?keys, "unknown agent stream");
            audit_raw(data);
            AgentData::Unknown {
                stream: other.to_string(),
                raw: data.clone(),
            }
        }
    }
}

fn parse_chat(data: Value) -> StreamEvent {
    let run_id = str_field(&data, "runId").unwrap_or_default();
    let state = match data.get("state").and_then(Value::as_str) {
        Some("delta") => ChatState::Delta,
        Some("final") => ChatState::Final,
        Some("aborted") => ChatState::Aborted,
        Some("error") => ChatState::Error,
        _ => {
            let (bytes, keys) = frame_meta(&data);
            warn!(bytes, ?keys, "chat event missing/unknown state");
            audit_raw(&data);
            return StreamEvent::Unknown {
                event: "chat".to_string(),
                raw: data,
            };
        }
    };
    StreamEvent::Chat(ChatEvent {
        run_id,
        seq: data.get("seq").and_then(Value::as_u64),
        state,
        session_key: str_field(&data, "sessionKey"),
        delta_text: str_field(&data, "deltaText"),
        stop_reason: str_field(&data, "stopReason"),
        error_message: str_field(&data, "errorMessage"),
        error_kind: str_field(&data, "errorKind"),
        message: data.get("message").cloned(),
        raw: data,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn agent_frame(stream: &str, data: Value) -> Value {
        let mut obj = json!({ "runId": "engine-run-1", "seq": 3, "ts": 0, "stream": stream });
        obj.as_object_mut().unwrap().extend(data.as_object().unwrap().clone());
        obj
    }

    #[test]
    fn parses_known_tool_stream() {
        let data = agent_frame("tool", json!({ "phase": "result", "name": "read", "toolCallId": "fc-1" }));
        match parse_stream_event("agent", data) {
            StreamEvent::Agent(a) => {
                assert_eq!(a.run_id, "engine-run-1");
                assert_eq!(a.seq, Some(3));
                assert!(matches!(a.data, AgentData::Tool(_)));
            }
            _ => panic!("expected agent/tool"),
        }
    }

    #[test]
    fn unknown_top_event_falls_back() {
        match parse_stream_event("foobar", json!({ "x": 1 })) {
            StreamEvent::Unknown { event, raw } => {
                assert_eq!(event, "foobar");
                assert_eq!(raw["x"], json!(1)); // raw 全量保留
            }
            _ => panic!("expected Unknown"),
        }
    }

    #[test]
    fn unknown_agent_stream_falls_back_to_agent_unknown() {
        let data = agent_frame("plan", json!({ "foo": 1 }));
        match parse_stream_event("agent", data) {
            StreamEvent::Agent(a) => match a.data {
                AgentData::Unknown { stream, raw } => {
                    assert_eq!(stream, "plan");
                    assert_eq!(raw["foo"], json!(1));
                }
                _ => panic!("expected AgentData::Unknown"),
            },
            _ => panic!("expected agent"),
        }
    }

    #[test]
    fn known_stream_parse_failure_falls_back() {
        // tool 的判别字段 phase 是枚举越界 → 解析失败 → AgentData::Unknown(D4 严格)
        let data = agent_frame("tool", json!({ "phase": "frobnicate", "name": "read" }));
        match parse_stream_event("agent", data) {
            StreamEvent::Agent(a) => assert!(matches!(a.data, AgentData::Unknown { .. })),
            _ => panic!("expected agent"),
        }
    }

    #[test]
    fn agent_missing_run_id_is_unknown_top() {
        // 信封必填 runId 缺失 → 整帧坏帧
        let data = json!({ "seq": 1, "stream": "tool", "phase": "start" });
        assert!(matches!(parse_stream_event("agent", data), StreamEvent::Unknown { .. }));
    }

    #[test]
    fn chat_delta_parses() {
        let data = json!({ "runId": "r", "seq": 5, "state": "delta", "deltaText": "hi",
                           "message": { "role": "assistant", "content": [] } });
        match parse_stream_event("chat", data) {
            StreamEvent::Chat(c) => {
                assert_eq!(c.state, ChatState::Delta);
                assert_eq!(c.delta_text.as_deref(), Some("hi"));
            }
            _ => panic!("expected chat"),
        }
    }

    #[test]
    fn ping_parses() {
        assert!(matches!(parse_stream_event("ping", json!({ "ts": 99 })), StreamEvent::Ping { ts: Some(99) }));
    }
}
