//! SSE frame parsing, per-run seq dedupe, and event -> ingest classification.
//! Pure logic, no IO — the read loop lives in lib.rs (Task 4).

use bcs_protocol::stream::{AgentData, ChatState, StreamEvent};
use bcs_service_api::ChatEventState as AppState;

#[derive(Debug, Clone)]
pub(crate) struct SseFrame {
    pub event: String,
    /// SSE `id:` field. Parsed per the SSE spec and asserted by tests, but the
    /// read loop dedupes off `StreamEvent.seq` instead (review #4), so this is
    /// not read by the ingest path.
    #[allow(dead_code)]
    pub id: Option<u64>,
    pub data: String,
}

/// Parse one SSE frame block (lines separated by `\n`, fields `event:`/`id:`/`data:`).
/// Multiple `data:` lines are joined with `\n` per the SSE spec.
pub(crate) fn parse_sse_block(block: &str) -> Option<SseFrame> {
    let mut event = String::new();
    let mut id: Option<u64> = None;
    let mut data_lines: Vec<String> = Vec::new();
    for line in block.lines() {
        let line = line.trim_end_matches('\r');
        if line.is_empty() || line.starts_with(':') {
            continue; // blank or comment
        }
        let (field, value) = match line.split_once(':') {
            Some((f, v)) => (f, v.strip_prefix(' ').unwrap_or(v)),
            None => (line, ""),
        };
        match field {
            "event" => event = value.to_string(),
            "id" => id = value.trim().parse::<u64>().ok(),
            "data" => data_lines.push(value.to_string()),
            _ => {}
        }
    }
    if event.is_empty() && data_lines.is_empty() {
        return None;
    }
    Some(SseFrame {
        event: if event.is_empty() { "message".to_string() } else { event },
        id,
        data: data_lines.join("\n"),
    })
}

#[derive(Debug, Default)]
pub(crate) struct SeqDedup {
    last_seq: Option<u64>,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum SeqDecision {
    Accept,
    Duplicate,
    Gap(u64),
}

impl SeqDedup {
    /// Compare against last accepted seq on the agent/chat counter.
    /// ping / gated approval must NOT be passed here (separate counters, D10).
    pub(crate) fn accept(&mut self, seq: Option<u64>) -> SeqDecision {
        let Some(seq) = seq else {
            // Missing seq: accept but caller records a metric.
            return SeqDecision::Accept;
        };
        match self.last_seq {
            Some(last) if seq <= last => SeqDecision::Duplicate,
            Some(last) if seq > last + 1 => {
                let gap = seq - last - 1;
                self.last_seq = Some(seq);
                SeqDecision::Gap(gap)
            }
            _ => {
                self.last_seq = Some(seq);
                SeqDecision::Accept
            }
        }
    }
}

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
            AgentData::Thinking(_) | AgentData::Lifecycle(_) | AgentData::Phase(_) => {
                IngestKind::Pipeline { event_type: "agent".to_string(), state: AppState::Delta }
            }
            AgentData::Unknown { stream, .. } => {
                tracing::warn!(stream, "drop unknown agent stream");
                IngestKind::Drop
            }
        },
        StreamEvent::Ping { .. } => IngestKind::Drop,
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
    fn classify_raw_assistant_drops_but_lifecycle_is_preserved() {
        let assistant = bcs_protocol::stream::parse_stream_event(
            "agent",
            json!({
                "runId": "r",
                "seq": 2,
                "stream": "assistant",
                "delta": "NO_REPLY"
            }),
        );
        assert!(matches!(classify(&assistant), IngestKind::Drop));

        let lifecycle = bcs_protocol::stream::parse_stream_event(
            "agent",
            json!({
                "runId": "r",
                "seq": 3,
                "stream": "lifecycle",
                "phase": "end"
            }),
        );
        match classify(&lifecycle) {
            IngestKind::Pipeline { event_type, state } => {
                assert_eq!(event_type, "agent");
                assert_eq!(state, AppState::Delta);
            }
            _ => panic!("expected lifecycle pipeline"),
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
    fn classify_ping_and_unknown_drop() {
        let ping = bcs_protocol::stream::parse_stream_event("ping", json!({ "ts": 1 }));
        assert!(matches!(classify(&ping), IngestKind::Drop));
        let unk = bcs_protocol::stream::parse_stream_event("foo", json!({}));
        assert!(matches!(classify(&unk), IngestKind::Drop));
    }
}
