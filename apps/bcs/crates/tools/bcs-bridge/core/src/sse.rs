use bcs_protocol::stream::{
    AgentData, AgentEvent, ChatEvent, ChatState, InteractionEvent, InteractionKind,
    InteractionPhase, LifecycleData, StreamEvent, ThinkingData, ToolData,
};
use serde_json::{json, Value};

pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
pub const HEARTBEAT: &str = ": heartbeat\n\n";

#[derive(Debug, thiserror::Error)]
pub enum FrameError {
    #[error("wire frame too large: {0} bytes")]
    FrameTooLarge(usize),
    #[error("serialize SSE data: {0}")]
    Json(#[from] serde_json::Error),
    #[error("SSE data must be single-line JSON")]
    MultilineData,
    #[error("event kind is not emittable on the wire")]
    Unsupported,
}

/// Encode one SSE frame from a pre-serialized single-line JSON string.
///
/// `data_json` must already be compact, single-line JSON (no `\n`/`\r`).
/// Callers holding a `serde_json::Value` should serialize it first with
/// `serde_json::to_string`; its error converts into `FrameError::Json` via `?`.
pub fn encode_frame(event: &str, id: Option<u64>, data_json: &str) -> Result<String, FrameError> {
    // SSE data: 行必须单行；先拒绝内嵌换行，避免拆成多帧
    if data_json.contains('\n') || data_json.contains('\r') {
        return Err(FrameError::MultilineData);
    }
    let mut frame = String::with_capacity(event.len() + data_json.len() + 24);
    frame.push_str("event: ");
    frame.push_str(event);
    frame.push('\n');
    if let Some(id) = id {
        frame.push_str("id: ");
        frame.push_str(&id.to_string());
        frame.push('\n');
    }
    frame.push_str("data: ");
    frame.push_str(data_json);
    frame.push_str("\n\n");
    if frame.len() > MAX_FRAME_BYTES {
        return Err(FrameError::FrameTooLarge(frame.len()));
    }
    Ok(frame)
}

// ---- emit-side constructors (driver uses these; seq is None, run loop stamps it)

pub fn chat_delta(run_id: &str, text: &str) -> StreamEvent {
    StreamEvent::Chat(ChatEvent {
        run_id: run_id.into(),
        seq: None,
        ts: None,
        state: ChatState::Delta,
        session_id: None,
        content: None,
        delta_text: Some(text.into()),
        stop_reason: None,
        error_message: None,
        error_kind: None,
        error_code: None,
        message: None,
        raw: Value::Null,
    })
}

pub fn chat_final(run_id: &str, text: String) -> StreamEvent {
    // message.timestamp 是 BCS MessageContent 的必填字段(缺失会整条丢弃),勿删。
    let message = json!({"role":"assistant","content":[{"type":"text","text":text}],"timestamp": bcs_protocol::now_ms()});
    StreamEvent::Chat(ChatEvent {
        run_id: run_id.into(),
        seq: None,
        ts: None,
        state: ChatState::Final,
        session_id: None,
        content: None,
        delta_text: None,
        stop_reason: Some("completed".into()),
        error_message: None,
        error_kind: None,
        error_code: None,
        message: Some(message),
        raw: Value::Null,
    })
}

pub fn chat_error(run_id: &str, message: &str, kind: Option<&str>) -> StreamEvent {
    StreamEvent::Chat(ChatEvent {
        run_id: run_id.into(),
        seq: None,
        ts: None,
        state: ChatState::Error,
        session_id: None,
        content: None,
        delta_text: None,
        stop_reason: None,
        error_message: Some(message.into()),
        error_kind: kind.map(str::to_string),
        error_code: None,
        message: None,
        raw: Value::Null,
    })
}

pub fn chat_aborted(run_id: &str, stop_reason: &str) -> StreamEvent {
    StreamEvent::Chat(ChatEvent {
        run_id: run_id.into(),
        seq: None,
        ts: None,
        state: ChatState::Aborted,
        session_id: None,
        content: None,
        delta_text: None,
        stop_reason: Some(stop_reason.into()),
        error_message: None,
        error_kind: None,
        error_code: None,
        message: None,
        raw: Value::Null,
    })
}

pub fn agent_tool(run_id: &str, mut data: ToolData) -> StreamEvent {
    if data.phase == bcs_protocol::stream::ToolPhase::Result {
        data.result = data.result.map(unwrap_result_wrapper);
    }
    StreamEvent::Agent(AgentEvent {
        run_id: run_id.into(),
        seq: None,
        ts: None,
        session_id: None,
        data: AgentData::Tool(data),
        raw: Value::Null,
    })
}

/// Remove one top-level `{ "result": ... }` wrapper from tool output. A
/// JSON-encoded object is also accepted, but unmatched strings stay byte-for-byte
/// intact. Sibling fields, content blocks, and the inner value are never traversed.
fn unwrap_result_wrapper(mut value: Value) -> Value {
    let mut decoded = value.as_str()
        .and_then(|text| serde_json::from_str::<Value>(text).ok());
    let candidate = decoded.as_mut().unwrap_or(&mut value);
    if let Some(object) = candidate.as_object_mut() {
        if object.len() == 1 {
            if let Some(inner) = object.remove("result") {
                return inner;
            }
        }
    }
    value
}

pub fn agent_thinking(run_id: &str, delta: Option<String>, text: Option<String>) -> StreamEvent {
    StreamEvent::Agent(AgentEvent {
        run_id: run_id.into(),
        seq: None,
        ts: None,
        session_id: None,
        data: AgentData::Thinking(ThinkingData { delta, text }),
        raw: Value::Null,
    })
}

pub fn agent_lifecycle(run_id: &str, phase: &str, model: Option<String>) -> StreamEvent {
    StreamEvent::Agent(AgentEvent {
        run_id: run_id.into(),
        seq: None,
        ts: None,
        session_id: None,
        data: AgentData::Lifecycle(LifecycleData {
            phase: phase.into(),
            model,
            agent_mode: None,
        }),
        raw: Value::Null,
    })
}

pub fn interaction_event(
    run_id: &str,
    phase: InteractionPhase,
    kind: InteractionKind,
    interaction_id: &str,
    extra: Value,
) -> StreamEvent {
    StreamEvent::Interaction(InteractionEvent {
        run_id: run_id.into(),
        seq: None,
        ts: None,
        session_id: None,
        phase,
        interaction_id: interaction_id.into(),
        kind,
        raw: extra,
    })
}

// ---- StreamEvent -> Provider 2.0 wire frame
//
// 线格式 camelCase 键：runId/deltaText/toolCallId/...。ChatEvent/AgentEvent 不
// derive Serialize，故 encoder 手工构造 Value（"复用协议类型做语义、
// encoder 独占线格式" 的边界）。调用方保证 seq 单调；ts 由 run loop 注入。
pub fn event_to_frame(ev: &StreamEvent, seq: u64, ts: u64, run_id: &str) -> Result<String, FrameError> {
    let (event, data) = event_to_data(ev, seq, ts, run_id)?;
    let data_json = serde_json::to_string(&data)?;
    encode_frame(event, Some(seq), &data_json)
}

/// Structured gateway payload, shared without serializing or parsing SSE.
pub(crate) fn event_to_data(ev: &StreamEvent, seq: u64, ts: u64, run_id: &str) -> Result<(&'static str, Value), FrameError> {
    let (event, data): (&str, Value) = match ev {
        StreamEvent::Chat(c) => {
            let mut d = json!({ "runId": run_id, "seq": seq, "ts": ts });
            let obj = d.as_object_mut().ok_or(FrameError::Unsupported)?;
            match c.state {
                ChatState::Delta => {
                    obj.insert("state".into(), json!("delta"));
                    if let Some(t) = &c.delta_text {
                        obj.insert("deltaText".into(), json!(t));
                    }
                }
                ChatState::Final => {
                    obj.insert("state".into(), json!("final"));
                    if let Some(m) = &c.message {
                        obj.insert("message".into(), m.clone());
                    }
                    if let Some(s) = &c.stop_reason {
                        obj.insert("stopReason".into(), json!(s));
                    }
                }
                ChatState::Error => {
                    obj.insert("state".into(), json!("error"));
                    if let Some(m) = &c.error_message {
                        obj.insert("errorMessage".into(), json!(m));
                    }
                    if let Some(k) = &c.error_kind {
                        obj.insert("errorKind".into(), json!(k));
                    }
                }
                ChatState::Aborted => {
                    obj.insert("state".into(), json!("aborted"));
                    if let Some(s) = &c.stop_reason {
                        obj.insert("stopReason".into(), json!(s));
                    }
                }
            }
            ("chat", d)
        }
        StreamEvent::Agent(a) => {
            let mut d = json!({ "runId": run_id, "seq": seq, "ts": ts });
            let obj = d.as_object_mut().ok_or(FrameError::Unsupported)?;
            match &a.data {
                AgentData::Tool(t) => {
                    obj.insert("stream".into(), json!("tool"));
                    let v = serde_json::to_value(t)?;
                    merge(obj, v);
                }
                AgentData::Thinking(t) => {
                    obj.insert("stream".into(), json!("thinking"));
                    let v = serde_json::to_value(t)?;
                    merge(obj, v);
                }
                AgentData::Lifecycle(l) => {
                    obj.insert("stream".into(), json!("lifecycle"));
                    let v = serde_json::to_value(l)?;
                    merge(obj, v);
                }
                // Approval 属旧兼容结构，禁止输出（spec §2）；Phase 暂不发；
                // Unknown 不可线编码。Assistant/Error 文本由 chat 事件承载，
                // 本桥 engine 适配器不产生这两种 agent 变体。
                AgentData::Approval(_) | AgentData::Phase(_) | AgentData::Unknown { .. } | AgentData::Assistant { .. } | AgentData::Error { .. } => {
                    return Err(FrameError::Unsupported);
                }
            }
            ("agent", d)
        }
        StreamEvent::Interaction(i) => {
            let mut d = json!({
                "runId": run_id, "seq": seq, "ts": ts,
                "phase": match i.phase {
                    InteractionPhase::Requested => "requested",
                    InteractionPhase::Resolved => "resolved",
                },
                "interactionId": i.interaction_id,
                "kind": match i.kind {
                    InteractionKind::Exec => "exec",
                    InteractionKind::AskUser => "ask_user",
                    InteractionKind::ModeSwitch => "mode_switch",
                },
            });
            let obj = d.as_object_mut().ok_or(FrameError::Unsupported)?;
            // raw 承载 kind 专有三白名单字段（options/questions/…）
            merge(obj, i.raw.clone());
            ("interaction", d)
        }
        // Ping 由 run loop 直发注释帧；Unknown 不可线编码。
        StreamEvent::Ping { .. } | StreamEvent::Unknown { .. } => {
            return Err(FrameError::Unsupported);
        }
    };
    Ok((event, data))
}

fn merge(obj: &mut serde_json::Map<String, Value>, v: Value) {
    if let Value::Object(m) = v {
        for (k, val) in m {
            obj.insert(k, val);
        }
    }
}
