use bcs_bridge_core::{encoder::{Encoder, GatewayEncoder, WebSocketV2Encoder}, run::RunEvent, sse};
use bcs_protocol::ws::protocol::{BcsFrame, RequestFrame};
use serde_json::{json, Value};

fn encoder() -> WebSocketV2Encoder {
    WebSocketV2Encoder::new("bridge-local", "worker-1", "bcs_grp_test:12345678")
}

#[test]
fn v2_decode_preserves_canonical_session_and_idempotency() {
    let req = RequestFrame::new("request-1", "chat.send", Some(json!({
        "session_key":"legacy-key", "bcs_group_id":"bcs_grp_test",
        "bcs_session_id":"bcs_grp_test:12345678", "idempotency_key":"run-1",
        "message":{"role":"user","content":[{"type":"text","text":"你好"}],"timestamp":0}
    })));
    let command = encoder().decode(req).unwrap();
    assert_eq!(command.id, "run-1");
    assert_eq!(command.session_id.as_deref(), Some("bcs_grp_test:12345678"));
    assert_eq!(command.to_bot.provider_bot_ref, "worker-1");
}

#[test]
fn gateway_replay_keeps_metadata_and_v2_uses_native_event_shape() {
    let event = RunEvent { run_id:"run-1".into(), seq:17, ts:1234, event:sse::chat_delta("run-1", "你好") };
    let gateway = GatewayEncoder.encode(&event).unwrap().unwrap();
    assert_eq!(gateway, GatewayEncoder.encode(&event).unwrap().unwrap());
    assert!(gateway.contains("\"ts\":1234"));
    let wire = serde_json::to_value(encoder().encode(&event).unwrap().unwrap()).unwrap();
    assert_eq!(wire, json!({"type":"event","event":"chat.event","seq":17,
        "payload":{"run_id":"run-1","bcs_group_id":"bcs_grp_test:12345678", "state":"delta","delta_text":"你好"}}));
}

#[test]
fn tool_result_keeps_json_and_uses_agent_envelope() {
    let data = serde_json::from_value(json!({"phase":"result","toolCallId":"call-1","result":{"answer":7}})).unwrap();
    let event = RunEvent {run_id:"run-1".into(),seq:18,ts:1235,event:sse::agent_tool("run-1",data)};
    let wire: Value = serde_json::to_value(encoder().encode(&event).unwrap().unwrap()).unwrap();
    assert_eq!(wire["event"], "agent");
    assert_eq!(wire["payload"]["stream"], "tool");
    assert_eq!(wire["payload"]["data"]["result"], json!({"answer":7}));
    assert_eq!(wire["payload"]["ts"],1235);
    assert_eq!(wire["payload"]["data"]["toolCallId"], "call-1");
    let decoded: BcsFrame = serde_json::from_value(wire).unwrap();
    assert!(matches!(decoded, BcsFrame::Event(_)));
}

#[test]
fn v2_rejects_unsupported_methods_and_attachment_only_requests() {
    // interaction.resolve is now accepted on both transports (HITL downlink);
    // it decodes without plugin-side validation, which stays in the runtime.
    let decoded = encoder().decode(RequestFrame::new("a", "interaction.resolve", Some(json!({
        "interactionId": "int-1", "idempotencyKey": "k", "decision": "deny"
    }))),).unwrap();
    assert_eq!(decoded.method, "interaction.resolve");
    let err = encoder().decode(RequestFrame::new("h", "chat.history", None)).unwrap_err();
    assert_eq!(err.code,"unsupported_method");
    let err = encoder().decode(RequestFrame::new("b","chat.send",Some(json!({
        "session_key":"s", "bcs_group_id":"bcs_grp_test",
        "message":{"role":"user","content":[{"type":"image","url":"test"}],"timestamp":0}
    })))).unwrap_err();
    assert_eq!(err.code,"invalid_request");
}

#[test]
fn real_bcs_v2_send_and_inject_builders_keep_session_and_sender() {
    use bcs_protocol::{build_chat_send_frame, build_chat_inject_frame, GroupContextInput};
    let group = GroupContextInput {session_id:"bcs_grp_example".into(),driver_bot:"driver".into(),
        originator:"driver".into(),participants:vec![],bcs_session_id:Some("bcs_grp_example:87654321".into())};
    for session in ["bcs_grp_example:87654321", "bcs_grp_example:00000000"] {
        let send = build_chat_send_frame("run-bcs","bcs_grp_example",&group,"请执行","sender-bot","观察者",
            &[],"target-bot",&[],&None,&None,false,2,None,None,Some(session));
        let inject = build_chat_inject_frame("inject-bcs","bcs_grp_example",&group,"已加入协作群","sender-bot","观察者",
            &[],"target-bot",&[],&None,false,2,None,None,Some(session));
        for frame in [send,inject] {
            let BcsFrame::Request(frame) = frame else { panic!("BCS builder must produce a request") };
            let command = encoder().decode(frame).unwrap();
            assert_eq!(command.session_id.as_deref(),Some(session));
            assert_eq!(command.from.as_ref().unwrap()["name"],"观察者(sender-bot)");
            assert!(command.message.unwrap()["content"][0]["text"].as_str().unwrap().contains("[BCS Group Context]"));
        }
    }
}

#[test]
fn v2_frames_deserialize_into_bcs_consumer_payload_types() {
    use bcs_protocol::{AgentEventPayload, ChatEventPayload};
    let events = [sse::chat_final("run", "完成".into()),sse::chat_error("run","unsupported",Some("unsupported_interaction")),
        sse::chat_aborted("run","user_cancelled"),sse::agent_thinking("run",Some("思考".into()),None)];
    for (index,event) in events.into_iter().enumerate() {
        let event = RunEvent {run_id:"run".into(),seq:index as u64+1,ts:55,event};
        let Some(BcsFrame::Event(frame)) = encoder().encode(&event).unwrap() else { panic!("expected event") };
        if frame.event == "agent" {
            let payload: AgentEventPayload = serde_json::from_value(frame.payload.unwrap()).unwrap();
            assert_eq!(payload.run_id,"run");
            assert_eq!(payload.ts,55);
        } else {
            let payload: ChatEventPayload = serde_json::from_value(frame.payload.unwrap()).unwrap();
            assert_eq!(payload.run_id,"run");
        }
    }
}

#[test]
fn v2_wire_limit_is_explicit_and_conflicting_session_scope_is_rejected() {
    let event = RunEvent {run_id:"run".into(),seq:1,ts:1,
        event:sse::chat_delta("run",&"x".repeat(sse::MAX_FRAME_BYTES))};
    assert!(matches!(encoder().encode(&event),Err(sse::FrameError::FrameTooLarge(_))));
    let request = RequestFrame::new("id","chat.send",Some(json!({
        "session_key":"legacy", "bcs_group_id":"bcs_grp_example:11111111", "bcs_session_id":"bcs_grp_example:22222222",
        "message":{"role":"user","timestamp":0,"content":[{"type":"text","text":"do something"}]}
    })));
    assert_eq!(encoder().decode(request).unwrap_err().code,"invalid_request");
}
