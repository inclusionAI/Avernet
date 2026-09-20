//! Boundary parser: raw JSON frame -> strongly-typed `StreamEvent`.
//!
//! Unknown top-level event / unknown stream / known-stream parse failure all
//! fall back to an `Unknown` variant with the full `raw` retained, plus a
//! bounded WARN log. Full raw goes only to an access-controlled audit sink.

use serde_json::Value;
use tracing::warn;

use super::agent::{ApprovalData, LifecycleData, PhaseData, ThinkingData, ToolData};
use super::event::{
    AgentData, AgentEvent, ChatEvent, ChatState, InteractionEvent, InteractionKind,
    InteractionPhase, StreamEvent,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RunEventV3Error {
    UnsupportedEvent(String),
    MissingField(&'static str),
    InvalidField(&'static str),
    InvalidEvent(String),
}

impl std::fmt::Display for RunEventV3Error {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnsupportedEvent(event) => write!(formatter, "unsupported V3 run event '{event}'"),
            Self::MissingField(field) => write!(formatter, "V3 run event missing {field}"),
            Self::InvalidField(field) => write!(formatter, "V3 run event has invalid {field}"),
            Self::InvalidEvent(event) => write!(formatter, "invalid V3 {event} event"),
        }
    }
}

impl std::error::Error for RunEventV3Error {}

/// Parse the strict Bot WebSocket V3 Run Event contract.
///
/// Provider 2.0 keeps using [`parse_stream_event`] because it accepts the
/// legacy `sessionKey` alias and optional common fields. V3 is deliberately
/// fail-closed: identity and ordering fields are required and malformed known
/// events never fall back to a legacy shape.
pub fn parse_run_event_v3(event: &str, data: Value) -> Result<StreamEvent, RunEventV3Error> {
    if !matches!(event, "agent" | "chat" | "interaction") {
        return Err(RunEventV3Error::UnsupportedEvent(event.to_string()));
    }
    require_non_empty_string(&data, "runId")?;
    require_non_empty_string(&data, "sessionId")?;
    require_u64(&data, "seq")?;
    require_u64(&data, "ts")?;

    let parsed = parse_stream_event(event, data);
    match &parsed {
        StreamEvent::Agent(agent) => {
            if matches!(agent.data, AgentData::Unknown { .. }) {
                return Err(RunEventV3Error::InvalidEvent("agent".to_string()));
            }
            if let AgentData::Tool(tool) = &agent.data {
                let name = tool.name.as_deref().map(str::trim).filter(|value| !value.is_empty());
                let call_id = tool
                    .tool_call_id
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty());
                if name.is_none() {
                    return Err(RunEventV3Error::MissingField("toolName/name"));
                }
                if call_id.is_none() {
                    return Err(RunEventV3Error::MissingField("toolCallId"));
                }
                if matches!(tool.phase, super::agent::ToolPhase::Result) {
                    if tool.result.is_none() {
                        return Err(RunEventV3Error::MissingField("result"));
                    }
                    if tool.is_error.is_none() {
                        return Err(RunEventV3Error::MissingField("isError"));
                    }
                }
            }
        }
        StreamEvent::Chat(_) | StreamEvent::Interaction(_) => {}
        StreamEvent::Ping { .. } | StreamEvent::Unknown { .. } => {
            return Err(RunEventV3Error::InvalidEvent(event.to_string()));
        }
    }
    Ok(parsed)
}

fn require_non_empty_string(data: &Value, field: &'static str) -> Result<(), RunEventV3Error> {
    match data.get(field) {
        None => Err(RunEventV3Error::MissingField(field)),
        Some(value) if value.as_str().is_some_and(|value| !value.trim().is_empty()) => Ok(()),
        Some(_) => Err(RunEventV3Error::InvalidField(field)),
    }
}

fn require_u64(data: &Value, field: &'static str) -> Result<(), RunEventV3Error> {
    match data.get(field) {
        None => Err(RunEventV3Error::MissingField(field)),
        Some(value) if value.as_u64().is_some() => Ok(()),
        Some(_) => Err(RunEventV3Error::InvalidField(field)),
    }
}

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
        "interaction" => parse_interaction(data),
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

fn parse_interaction(data: Value) -> StreamEvent {
    let run_id = str_field(&data, "runId");
    let seq = data.get("seq").and_then(Value::as_u64);
    let interaction_id = str_field(&data, "interactionId");
    let phase = data
        .get("phase")
        .cloned()
        .map(serde_json::from_value::<InteractionPhase>)
        .transpose();
    let kind = data
        .get("kind")
        .cloned()
        .map(serde_json::from_value::<InteractionKind>)
        .transpose();

    match (run_id, seq, interaction_id, phase, kind) {
        (Some(run_id), Some(seq), Some(interaction_id), Ok(Some(phase)), Ok(Some(kind))) => {
            StreamEvent::Interaction(InteractionEvent {
                run_id,
                seq: Some(seq),
                ts: data.get("ts").and_then(Value::as_u64),
                session_id: session_id(&data),
                phase,
                interaction_id,
                kind,
                raw: data,
            })
        }
        _ => {
            let (bytes, keys) = frame_meta(&data);
            warn!(bytes, ?keys, "interaction event missing/unknown common field");
            tracing::trace!(
                target: "stream_audit",
                bytes,
                ?keys,
                "redacted malformed interaction frame"
            );
            StreamEvent::Unknown {
                event: "interaction".to_string(),
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
    let session_id = session_id(&data);

    // The stream-specific data is the frame itself (flat layout in captures).
    let agent_data = parse_agent_data(&stream, &data);
    StreamEvent::Agent(AgentEvent {
        run_id,
        seq,
        ts,
        session_id,
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
        "assistant" => AgentData::Assistant { raw: data.clone() },
        "error" => AgentData::Error { raw: data.clone() },
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
        ts: data.get("ts").and_then(Value::as_u64),
        state,
        session_id: session_id(&data),
        content: str_field(&data, "content"),
        delta_text: str_field(&data, "deltaText"),
        stop_reason: str_field(&data, "stopReason"),
        error_message: str_field(&data, "errorMessage"),
        error_kind: str_field(&data, "errorKind"),
        error_code: str_field(&data, "errorCode"),
        message: data.get("message").cloned(),
        raw: data,
    })
}

fn session_id(data: &Value) -> Option<String> {
    str_field(data, "sessionId").or_else(|| str_field(data, "sessionKey"))
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
    fn parses_requested_exec_interaction() {
        let data = json!({
            "runId": "provider-run-1",
            "seq": 7,
            "ts": 1786300000000_u64,
            "sessionKey": "provider-session-1",
            "phase": "requested",
            "interactionId": "interaction-1",
            "kind": "exec",
            "command": "npm run deploy",
            "options": [
                {"decision": "allow_once", "label": "Allow once"},
                {"decision": "deny", "label": "Deny"}
            ]
        });

        match parse_stream_event("interaction", data.clone()) {
            StreamEvent::Interaction(interaction) => {
                assert_eq!(interaction.run_id, "provider-run-1");
                assert_eq!(interaction.seq, Some(7));
                assert_eq!(interaction.ts, Some(1786300000000));
                assert_eq!(interaction.session_id.as_deref(), Some("provider-session-1"));
                assert_eq!(interaction.interaction_id, "interaction-1");
                assert_eq!(interaction.phase, InteractionPhase::Requested);
                assert_eq!(interaction.kind, InteractionKind::Exec);
                assert_eq!(interaction.raw, data);
            }
            other => panic!("expected interaction, got {other:?}"),
        }
    }

    #[test]
    fn parses_requested_ask_user_and_mode_switch_interactions() {
        for (kind, expected) in [
            ("ask_user", InteractionKind::AskUser),
            ("mode_switch", InteractionKind::ModeSwitch),
        ] {
            let data = json!({
                "runId": "provider-run-2",
                "seq": 8,
                "phase": "requested",
                "interactionId": format!("interaction-{kind}"),
                "kind": kind,
                "providerExtension": {"preserved": true}
            });

            match parse_stream_event("interaction", data.clone()) {
                StreamEvent::Interaction(interaction) => {
                    assert_eq!(interaction.phase, InteractionPhase::Requested);
                    assert_eq!(interaction.kind, expected);
                    assert_eq!(interaction.raw, data);
                }
                other => panic!("expected interaction, got {other:?}"),
            }
        }
    }

    #[test]
    fn preserves_requested_mode_switch_target_and_recommendation() {
        let data = json!({
            "runId": "provider-run-3",
            "seq": 9,
            "phase": "requested",
            "interactionId": "interaction-mode-switch",
            "kind": "mode_switch",
            "fromMode": "plan",
            "targetMode": "execute",
            "options": [
                {
                    "decision": "proceed",
                    "label": "Continue to execution",
                    "targetMode": "execute",
                    "recommended": true
                },
                {
                    "decision": "stay",
                    "label": "Stay in planning"
                }
            ]
        });

        match parse_stream_event("interaction", data.clone()) {
            StreamEvent::Interaction(interaction) => {
                assert_eq!(interaction.kind, InteractionKind::ModeSwitch);
                assert_eq!(interaction.raw["targetMode"], json!("execute"));
                assert_eq!(interaction.raw["options"][0]["targetMode"], json!("execute"));
                assert_eq!(interaction.raw["options"][0]["recommended"], json!(true));
                assert_eq!(interaction.raw, data);
            }
            other => panic!("expected interaction, got {other:?}"),
        }
    }

    #[test]
    fn parses_resolved_interaction_without_kind_specific_echo() {
        let data = json!({
            "runId": "provider-run-1",
            "seq": 11,
            "ts": 1786300004000_u64,
            "phase": "resolved",
            "interactionId": "interaction-2",
            "kind": "ask_user"
        });

        match parse_stream_event("interaction", data.clone()) {
            StreamEvent::Interaction(interaction) => {
                assert_eq!(interaction.phase, InteractionPhase::Resolved);
                assert_eq!(interaction.kind, InteractionKind::AskUser);
                assert_eq!(interaction.raw, data);
            }
            other => panic!("expected interaction, got {other:?}"),
        }
    }

    #[test]
    fn interaction_without_required_seq_is_unknown_without_business_data_logging() {
        let data = json!({
            "runId": "provider-run-1",
            "phase": "requested",
            "interactionId": "interaction-1",
            "kind": "exec",
            "command": "must-not-be-audited"
        });

        assert!(matches!(
            parse_stream_event("interaction", data),
            StreamEvent::Unknown { event, .. } if event == "interaction"
        ));
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

    #[test]
    fn v3_parses_canonical_tool_result() {
        let data = json!({
            "runId": "run-1",
            "sessionId": "group-1:session-1",
            "seq": 2,
            "ts": 1789600000000_u64,
            "stream": "tool",
            "phase": "result",
            "name": "mcp__bcs__bcs_assign_task",
            "toolCallId": "call-1",
            "result": {"content": [{"type": "text", "text": "ok"}]},
            "isError": false
        });

        match parse_run_event_v3("agent", data).unwrap() {
            StreamEvent::Agent(agent) => {
                assert_eq!(agent.session_id.as_deref(), Some("group-1:session-1"));
                assert!(matches!(agent.data, AgentData::Tool(_)));
            }
            other => panic!("expected agent, got {other:?}"),
        }
    }

    #[test]
    fn v3_rejects_legacy_session_key_and_missing_timestamp() {
        let data = json!({
            "runId": "run-1",
            "sessionKey": "legacy-session",
            "seq": 1,
            "stream": "thinking",
            "delta": "working"
        });

        assert!(matches!(
            parse_run_event_v3("agent", data),
            Err(RunEventV3Error::MissingField("sessionId"))
        ));
    }
}
