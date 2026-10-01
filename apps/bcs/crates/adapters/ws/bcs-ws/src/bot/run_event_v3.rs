use bcs_protocol::stream::{
    AgentData, ChatState, StreamEvent, TASK_INTENT_ELIGIBLE_KEY, parse_run_event_v3,
};
use bcs_protocol::{ChatEventState, EventFrame};
use serde_json::Value;

use super::dispatcher::{BotWsDispatchError, Result};

pub(super) type NormalizedBotEvent = (
    String,
    String,
    String,
    Value,
    ChatEventState,
    bool,
    Option<String>,
    Option<u64>,
);

/// Adapt the canonical V3 Run Event contract to the existing message-flow
/// application command. Protocol validation stays at the WS boundary; the
/// application layer remains transport-agnostic.
pub(super) fn normalize_v3_event(event: &EventFrame) -> Result<NormalizedBotEvent> {
    let raw = event
        .payload
        .clone()
        .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 event payload is required".into()))?;
    let canonical = parse_run_event_v3(&event.event, raw).map_err(|error| {
        BotWsDispatchError::InvalidFrameFormat(format!("invalid V3 run event: {error}"))
    })?;
    match canonical {
        StreamEvent::Agent(agent) => normalize_agent(agent),
        StreamEvent::Chat(chat) => normalize_chat(chat),
        StreamEvent::Interaction(_) => Err(BotWsDispatchError::InvalidFrameFormat(
            "V3 interaction events are not supported on Bot WebSocket yet".into(),
        )),
        StreamEvent::Ping { .. } | StreamEvent::Unknown { .. } => Err(
            BotWsDispatchError::InvalidFrameFormat("unsupported V3 run event".into()),
        ),
    }
}

fn normalize_agent(agent: bcs_protocol::stream::AgentEvent) -> Result<NormalizedBotEvent> {
    let run_id = agent.run_id;
    let session_id = agent.session_id;
    let seq = agent.seq;
    let ts = agent.ts.unwrap_or_default();
    let (stream, data, task_intent_eligible) = match agent.data {
        AgentData::Tool(data) => {
            let eligible = matches!(data.phase, bcs_protocol::stream::ToolPhase::Result)
                && data.is_error == Some(false);
            ("tool", serde_json::to_value(data)?, eligible)
        }
        AgentData::Thinking(data) => ("thinking", serde_json::to_value(data)?, false),
        AgentData::Assistant { raw } => ("assistant", raw, false),
        AgentData::Error { raw } => {
            return Ok(normalize_agent_error(run_id, session_id, seq, ts, raw));
        }
        AgentData::Approval(data) => ("approval", serde_json::to_value(data)?, false),
        AgentData::Lifecycle(data) => ("lifecycle", serde_json::to_value(data)?, false),
        AgentData::Phase(data) => ("phase", serde_json::to_value(data)?, false),
        AgentData::Unknown { .. } => {
            return Err(BotWsDispatchError::InvalidFrameFormat(
                "unknown V3 agent stream".into(),
            ));
        }
    };
    let mut payload = serde_json::json!({
        "run_id": run_id,
        "bcs_group_id": "",
        "stream": stream,
        "ts": ts,
        "data": data,
    });
    if task_intent_eligible {
        payload[TASK_INTENT_ELIGIBLE_KEY] = Value::Bool(true);
    }
    Ok((
        run_id,
        String::new(),
        "agent".to_string(),
        payload,
        ChatEventState::Delta,
        false,
        session_id,
        seq,
    ))
}

fn normalize_agent_error(
    run_id: String,
    session_id: Option<String>,
    seq: Option<u64>,
    ts: u64,
    raw: Value,
) -> NormalizedBotEvent {
    let mut payload = serde_json::json!({
        "run_id": run_id,
        "bcs_group_id": "",
        "state": "error",
        "ts": ts,
    });
    for key in ["message", "errorMessage", "errorKind", "errorCode"] {
        if let Some(value) = raw.get(key) {
            payload[key] = value.clone();
        }
    }
    (
        run_id,
        String::new(),
        "chat.event".to_string(),
        payload,
        ChatEventState::Error,
        true,
        session_id,
        seq,
    )
}

fn normalize_chat(chat: bcs_protocol::stream::ChatEvent) -> Result<NormalizedBotEvent> {
    let run_id = chat.run_id.clone();
    let state = match chat.state {
        ChatState::Delta => ChatEventState::Delta,
        ChatState::Final => ChatEventState::Final,
        ChatState::Aborted => ChatEventState::Aborted,
        ChatState::Error => ChatEventState::Error,
    };
    let is_final = matches!(
        state,
        ChatEventState::Final | ChatEventState::Aborted | ChatEventState::Error
    );
    let message = chat.message.clone().or_else(|| {
        chat.content.as_ref().map(|content| {
            serde_json::json!({
                "role": "assistant",
                "content": [{"type": "text", "text": content}],
                "timestamp": chat.ts.unwrap_or_default(),
            })
        })
    });
    let mut payload = serde_json::json!({
        "run_id": run_id,
        "bcs_group_id": "",
        "state": match state {
            ChatEventState::Delta => "delta",
            ChatEventState::Final => "final",
            ChatEventState::Aborted => "aborted",
            ChatEventState::Error => "error",
            ChatEventState::ToolCallStart => "tool_call_start",
            ChatEventState::ToolCallEnd => "tool_call_end",
        },
    });
    if let Some(message) = message {
        payload["message"] = message;
    }
    if let Some(delta) = chat.delta_text.or_else(|| {
        matches!(state, ChatEventState::Delta)
            .then(|| chat.content.clone())
            .flatten()
    }) {
        payload["delta_text"] = Value::String(delta);
    }
    for (legacy_key, raw_key) in [
        ("stop_reason", "stopReason"),
        ("errorMessage", "errorMessage"),
        ("errorKind", "errorKind"),
        ("errorCode", "errorCode"),
        ("usage", "usage"),
        ("routing", "routing"),
    ] {
        if let Some(value) = chat.raw.get(raw_key) {
            payload[legacy_key] = value.clone();
        }
    }
    Ok((
        run_id,
        String::new(),
        "chat.event".to_string(),
        payload,
        state,
        is_final,
        chat.session_id,
        chat.seq,
    ))
}
