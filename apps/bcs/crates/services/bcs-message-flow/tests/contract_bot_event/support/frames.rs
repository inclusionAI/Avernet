use super::super::{
    BcsFrame,
    BcsMessageFlow,
    BotEventCommand,
    ChatEventState,
    MessageFlowService,
    SystemMessageEvent,
    TASK_INTENT_ELIGIBLE_KEY,
    Value,
    json
};

pub(in super::super) fn state_payload_name(state: &ChatEventState) -> &'static str {
    match state {
        ChatEventState::Delta => "delta",
        ChatEventState::Final => "final",
        ChatEventState::Aborted => "aborted",
        ChatEventState::Error => "error",
        ChatEventState::ToolCallStart => "tool_call_start",
        ChatEventState::ToolCallEnd => "tool_call_end",
    }
}

pub(in super::super) fn assert_ledger_notification(event: &SystemMessageEvent, pending: &str, replied: &str) {
    match event {
        SystemMessageEvent::UserNotification {
            group_id,
            message,
        } => {
            assert_eq!(group_id, "group-1");
            assert!(message.contains("[任务状态]"));
            assert!(message.contains(pending));
            assert!(message.contains(replied));
            assert!(message.contains("失败: -"));
            assert!(message.contains("超时: -"));
        }
        other => panic!("expected UserNotification, got {other:?}"),
    }
}

pub(in super::super) fn request_params(frame: &BcsFrame) -> &Value {
    match frame {
        BcsFrame::Request(req) => req.params.as_ref().expect("request params"),
        other => panic!("expected request frame, got {other:?}"),
    }
}

pub(in super::super) fn request_id(frame: &BcsFrame) -> String {
    match frame {
        BcsFrame::Request(req) => req.id.clone(),
        other => panic!("expected request frame, got {other:?}"),
    }
}

pub(in super::super) fn coordination_echo(tool: &str, arguments: Value) -> String {
    json!({
        "__bcs_coordination__": true,
        "v": 1,
        "tool": tool,
        "arguments": arguments,
        "status": "received",
    })
    .to_string()
}

pub(in super::super) fn agent_tool_result_payload(
    tool_name: Option<&str>,
    tool_call_id: &str,
    result_text: &str,
    is_error: bool,
) -> Value {
    let mut data = json!({
        "phase": "result",
        "toolCallId": tool_call_id,
        "isError": is_error,
        "result": {
            "content": [{"type": "text", "text": result_text}],
        },
    });
    if let Some(tool_name) = tool_name {
        data["name"] = Value::String(tool_name.to_string());
    }
    json!({
        TASK_INTENT_ELIGIBLE_KEY: true,
        "stream": "tool",
        "data": data,
    })
}

pub(in super::super) async fn record_matching_tool_start(
    flow: &BcsMessageFlow,
    bot_id: &str,
    run_id: &str,
    group_id: &str,
    session_id: &str,
    tool_name: &str,
    tool_call_id: &str,
) {
    flow.handle_bot_event(BotEventCommand {
        bot_id: bot_id.to_string(),
        run_id: run_id.to_string(),
        group_id: group_id.to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "tool",
            "data": {
                "phase": "start",
                "toolCallId": tool_call_id,
                "name": tool_name,
                "args": {},
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some(session_id.to_string()),
    })
    .await
    .expect("matching V3 tool start should be accepted");
}

pub(in super::super) fn assert_frame_tags(frame: &BcsFrame, expected: &[&str]) {
    let BcsFrame::Request(request) = frame else {
        panic!("expected request frame");
    };
    let params = request.params.as_ref().expect("request params");
    assert_eq!(params.get("tags"), Some(&serde_json::json!(expected)));
}
