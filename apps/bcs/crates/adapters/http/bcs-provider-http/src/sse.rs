//! SSE frame parsing, per-run seq dedupe, and event -> ingest classification.
//! Pure logic, no IO — the read loop lives in lib.rs (Task 4).

use bcs_protocol::stream::{AgentData, ChatState, StreamEvent};
use bcs_service_api::ChatEventState as AppState;

pub(crate) use bcs_protocol::stream::sse::{parse_sse_block, SeqDedup, SeqDecision};

/// Ingest decision: NO payload — payload construction requires run-context
/// (BCN run_id + group_id) and is deferred to Task 4's read loop.
#[derive(Debug)]
pub(crate) enum IngestKind {
    /// Forward this event through the streaming pipeline.
    Pipeline { event_type: String, state: AppState },
    /// Terminal event: close the run after ingesting.
    Terminal { event_type: String, state: AppState },
    /// HITL approval requested — close stream, unsupported path.
    CloseUnsupported,
    /// Provider 2.0 top-level interaction handled by InteractionService.
    Interaction,
    /// Discard silently (ping, unknown top-level, unknown agent stream).
    Drop,
}

/// Map a strongly-typed StreamEvent to an ingest decision.
/// Returns decision + event_type + app ChatEventState ONLY — no payload
/// (payload construction is in Task 4 where run_ctx is available).
pub(crate) fn classify(event: &StreamEvent) -> IngestKind {
    match event {
        StreamEvent::Chat(c) => {
            let (state, terminal) = match c.state {
                ChatState::Delta => (AppState::Delta, false),
                ChatState::Final => (AppState::Final, true),
                ChatState::Aborted => (AppState::Aborted, true),
                ChatState::Error => (AppState::Error, true),
            };
            if terminal {
                IngestKind::Terminal { event_type: "chat.event".to_string(), state }
            } else {
                IngestKind::Pipeline { event_type: "chat.event".to_string(), state }
            }
        }
        StreamEvent::Agent(a) => match &a.data {
            AgentData::Approval(_) => IngestKind::CloseUnsupported,
            AgentData::Tool(t) => {
                let state = match t.phase {
                    bcs_protocol::stream::ToolPhase::Start => AppState::ToolCallStart,
                    bcs_protocol::stream::ToolPhase::Result => AppState::ToolCallEnd,
                    bcs_protocol::stream::ToolPhase::Update => AppState::Delta,
                };
                IngestKind::Pipeline { event_type: "agent".to_string(), state }
            }
            AgentData::Assistant { .. }
            | AgentData::Thinking(_)
            | AgentData::Lifecycle(_)
            | AgentData::Phase(_) => {
                IngestKind::Pipeline { event_type: "agent".to_string(), state: AppState::Delta }
            }
            AgentData::Error { .. } => IngestKind::Terminal {
                event_type: "chat.event".to_string(),
                state: AppState::Error,
            },
            AgentData::Unknown { stream, .. } => {
                tracing::warn!(stream, "drop unknown agent stream");
                IngestKind::Drop
            }
        },
        StreamEvent::Ping { .. } => IngestKind::Drop,
        StreamEvent::Interaction(_) => IngestKind::Interaction,
        StreamEvent::Unknown { event, .. } => {
            tracing::warn!(event, "drop unknown stream event");
            IngestKind::Drop
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::ChatEventState as AppState;
    use serde_json::json;

    #[test]
    fn parses_sse_block_with_event_id_data() {
        let block = "event: agent\nid: 7\ndata: {\"runId\":\"r\",\"seq\":7,\"stream\":\"thinking\",\"delta\":\"x\"}";
        let f = parse_sse_block(block).unwrap();
        assert_eq!(f.event, "agent");
        assert_eq!(f.id, Some(7));
        assert!(f.data.contains("thinking"));
    }

    #[test]
    fn parses_multiline_data() {
        let block = "event: chat\ndata: {\"a\":1,\ndata: \"b\":2}";
        let f = parse_sse_block(block).unwrap();
        assert_eq!(f.data, "{\"a\":1,\n\"b\":2}");
    }

    #[test]
    fn seq_dedup_drops_duplicate_and_regressed() {
        let mut d = SeqDedup::default();
        assert!(matches!(d.accept(Some(1)), SeqDecision::Accept));
        assert!(matches!(d.accept(Some(2)), SeqDecision::Accept));
        assert!(matches!(d.accept(Some(2)), SeqDecision::Duplicate));
        assert!(matches!(d.accept(Some(1)), SeqDecision::Duplicate));
        assert!(matches!(d.accept(Some(5)), SeqDecision::Gap(_)));
        assert!(matches!(d.accept(Some(6)), SeqDecision::Accept));
    }

    #[test]
    fn classify_chat_final_is_terminal() {
        let ev = bcs_protocol::stream::parse_stream_event(
            "chat",
            json!({ "runId": "r", "seq": 9, "state": "final",
                    "message": { "content": [{ "type": "text", "text": "done" }] } }),
        );
        match classify(&ev) {
            IngestKind::Terminal { event_type, state } => {
                assert_eq!(event_type, "chat.event");
                assert_eq!(state, AppState::Final);
            }
            _ => panic!("expected terminal"),
        }
    }

    #[test]
    fn classify_tool_start_is_pipeline_agent() {
        let ev = bcs_protocol::stream::parse_stream_event(
            "agent",
            json!({ "runId": "r", "seq": 2, "stream": "tool", "phase": "start", "name": "read", "toolCallId": "t1" }),
        );
        match classify(&ev) {
            IngestKind::Pipeline { event_type, state } => {
                assert_eq!(event_type, "agent");
                assert_eq!(state, AppState::ToolCallStart);
            }
            _ => panic!("expected pipeline"),
        }
    }

    #[test]
    fn classify_agent_error_is_chat_terminal() {
        let ev = bcs_protocol::stream::parse_stream_event(
            "agent",
            json!({
                "runId": "r",
                "seq": 3,
                "stream": "error",
                "errorCode": "MODEL_ERROR",
                "errorMessage": "model failed"
            }),
        );
        match classify(&ev) {
            IngestKind::Terminal { event_type, state } => {
                assert_eq!(event_type, "chat.event");
                assert_eq!(state, AppState::Error);
            }
            _ => panic!("expected terminal"),
        }
    }

    #[test]
    fn classify_approval_is_close_unsupported() {
        let ev = bcs_protocol::stream::parse_stream_event(
            "agent",
            json!({ "runId": "r", "seq": 3, "stream": "approval", "phase": "requested", "kind": "exec" }),
        );
        assert!(matches!(classify(&ev), IngestKind::CloseUnsupported));
    }

    #[test]
    fn classify_top_level_interaction_for_application_service() {
        let ev = bcs_protocol::stream::parse_stream_event(
            "interaction",
            json!({
                "runId": "provider-run-1",
                "seq": 4,
                "phase": "requested",
                "interactionId": "interaction-1",
                "kind": "exec"
            }),
        );
        assert!(matches!(classify(&ev), IngestKind::Interaction));
    }

    #[test]
    fn classify_ping_and_unknown_drop() {
        let ping = bcs_protocol::stream::parse_stream_event("ping", json!({ "ts": 1 }));
        assert!(matches!(classify(&ping), IngestKind::Drop));
        let unk = bcs_protocol::stream::parse_stream_event("foo", json!({}));
        assert!(matches!(classify(&unk), IngestKind::Drop));
    }
}
