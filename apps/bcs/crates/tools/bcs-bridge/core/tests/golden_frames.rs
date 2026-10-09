use bcs_bridge_core::sse::*;
use bcs_protocol::stream::{parse_stream_event, ChatState, StreamEvent, ToolPhase};
use bcs_protocol::ws::MessageContent;
use serde_json::json;

#[test]
fn encodes_chat_delta_golden() {
    let frame = encode_frame(
        "chat",
        Some(605),
        r#"{"state":"delta","deltaText":"查询。","runId":"r-1","seq":605,"ts":1786276303908}"#,
    )
    .unwrap();
    let expected = "event: chat\nid: 605\ndata: {\"state\":\"delta\",\"deltaText\":\"查询。\",\"runId\":\"r-1\",\"seq\":605,\"ts\":1786276303908}\n\n";
    assert_eq!(frame, expected);
}

#[test]
fn encodes_frame_without_id() {
    let frame = encode_frame("ping", None, r#"{"ts":1}"#).unwrap();
    assert_eq!(frame, "event: ping\ndata: {\"ts\":1}\n\n");
}

#[test]
fn rejects_frame_over_8mib() {
    let big = "x".repeat(MAX_FRAME_BYTES);
    let data_json = format!(r#"{{"deltaText":"{}"}}"#, big);
    let err = encode_frame("chat", None, &data_json).unwrap_err();
    assert!(matches!(err, FrameError::FrameTooLarge(_)));
}

#[test]
fn rejects_multiline_data() {
    let err = encode_frame("chat", None, "{\"ts\":1}\n{\"ts\":2}").unwrap_err();
    assert!(matches!(err, FrameError::MultilineData));
}

#[test]
fn heartbeat_is_sse_comment() {
    assert_eq!(HEARTBEAT, ": heartbeat\n\n");
}

/// 从编码帧中抽出 event 名与 data JSON（测试辅助）
fn split_frame(frame: &str) -> (String, serde_json::Value) {
    let mut event = String::new();
    let mut data = String::new();
    for line in frame.lines() {
        if let Some(v) = line.strip_prefix("event: ") {
            event = v.to_string();
        }
        if let Some(v) = line.strip_prefix("data: ") {
            data = v.to_string();
        }
    }
    (event, serde_json::from_str(&data).unwrap())
}

#[test]
fn chat_delta_roundtrips_through_bcs_parser() {
    let frame = event_to_frame(&chat_delta("r-1", "正在分析"), 1, 100, "r-1").unwrap();
    let (event, data) = split_frame(&frame);
    assert_eq!(event, "chat");
    match parse_stream_event(&event, data) {
        StreamEvent::Chat(c) => {
            assert_eq!(c.state, ChatState::Delta);
            assert_eq!(c.delta_text.as_deref(), Some("正在分析"));
            assert_eq!(c.seq, Some(1));
        }
        other => panic!("expected chat, got {other:?}"),
    }
}

#[test]
fn chat_final_is_full_snapshot_terminal() {
    let frame = event_to_frame(&chat_final("r-1", "最终答案".to_string()), 5, 200, "r-1").unwrap();
    let (event, data) = split_frame(&frame);
    match parse_stream_event(&event, data.clone()) {
        StreamEvent::Chat(c) => {
            assert_eq!(c.state, ChatState::Final);
            assert_eq!(data["message"]["content"][0]["text"], json!("最终答案"));
        }
        other => panic!("expected final, got {other:?}"),
    }
}

/// 生产回归(2026-09-17):BCS 对 final 帧的 message 做
/// from_value::<MessageContent> 强解析,timestamp 必填;缺失时整条正文
/// 被丢弃("body omitted"),群聊历史不落库。chat_final 必须自带毫秒时间戳。
#[test]
fn chat_final_message_deserializes_into_bcs_message_content() {
    let frame = event_to_frame(&chat_final("r-1", "最终答案".to_string()), 5, 200, "r-1").unwrap();
    let (event, data) = split_frame(&frame);
    match parse_stream_event(&event, data) {
        StreamEvent::Chat(c) => {
            let raw = c.message.expect("final frame carries message");
            let parsed: MessageContent = serde_json::from_value(raw)
                .expect("message must deserialize into BCS MessageContent (timestamp required)");
            assert_eq!(parsed.role, "assistant");
            assert!(parsed.timestamp > 0, "timestamp must be epoch-ms");
        }
        other => panic!("expected chat, got {other:?}"),
    }
}

#[test]
fn tool_result_roundtrips() {
    let ev = agent_tool(
        "r-1",
        bcs_protocol::stream::ToolData {
            phase: ToolPhase::Result,
            name: Some("exec".into()),
            tool_call_id: Some("tc-1".into()),
            is_error: Some(false),
            exit_code: Some(0),
            duration_ms: Some(120),
            cwd: None,
            args: None,
            result: Some(json!({"content":[{"type":"text","text":"ok"}]})),
            partial_result: None,
        },
    );
    let frame = event_to_frame(&ev, 4, 100, "r-1").unwrap();
    let (event, data) = split_frame(&frame);
    match parse_stream_event(&event, data) {
        StreamEvent::Agent(a) => match a.data {
            bcs_protocol::stream::AgentData::Tool(t) => {
                assert_eq!(t.phase, ToolPhase::Result);
                assert_eq!(t.tool_call_id.as_deref(), Some("tc-1"));
            }
            other => panic!("expected tool, got {other:?}"),
        },
        other => panic!("expected agent, got {other:?}"),
    }
}

#[test]
fn interaction_requested_exec_roundtrips() {
    let ev = interaction_event(
        "r-1",
        bcs_protocol::stream::InteractionPhase::Requested,
        bcs_protocol::stream::InteractionKind::Exec,
        "int-1",
        json!({"title":"Run command?","command":"npm run deploy",
               "options":[{"decision":"allow-once","label":"Allow once"},
                          {"decision":"deny","label":"Deny"}]}),
    );
    let frame = event_to_frame(&ev, 7, 100, "r-1").unwrap();
    let (event, data) = split_frame(&frame);
    assert_eq!(event, "interaction");
    match parse_stream_event(&event, data.clone()) {
        StreamEvent::Interaction(i) => {
            assert_eq!(i.interaction_id, "int-1");
            assert_eq!(i.kind, bcs_protocol::stream::InteractionKind::Exec);
            assert_eq!(data["options"][0]["decision"], json!("allow-once"));
        }
        other => panic!("expected interaction, got {other:?}"),
    }
}

fn tool_result_frame(result: serde_json::Value, phase: ToolPhase) -> serde_json::Value {
    let event = agent_tool("r-1", bcs_protocol::stream::ToolData {
        phase,
        name: Some("lookup".into()),
        tool_call_id: Some("tc-1".into()),
        is_error: Some(true),
        exit_code: Some(1),
        duration_ms: Some(120),
        cwd: None,
        args: Some(json!({"result":"argument"})),
        result: Some(result),
        partial_result: Some(json!({"result":"partial"})),
    });
    let frame = event_to_frame(&event, 4, 100, "r-1").unwrap();
    split_frame(&frame).1
}

#[test]
fn tool_result_unwraps_single_field_objects_and_json_strings_once() {
    let cases = [
        (json!({"result":"完成"}), json!("完成")),
        (json!(r#"{"result":"完成"}"#), json!("完成")),
        (json!({"result":{"items":[1,2]}}), json!({"items":[1,2]})),
        (json!(r#"{"result":{"items":[1,2]}}"#), json!({"items":[1,2]})),
        (json!({"result":[1,"two"]}), json!([1,"two"])),
        (json!({"result":0}), json!(0)),
        (json!({"result":false}), json!(false)),
        (json!({"result":null}), json!(null)),
        (json!({"result":""}), json!("")),
        (json!({"result":{"result":"inner"}}), json!({"result":"inner"})),
        (json!(r#"{"result":"{\"result\":\"inner\"}"}"#), json!(r#"{"result":"inner"}"#)),
        (json!(" \n{\"result\":\"ok\"}\t"), json!("ok")),
    ];
    for (input, expected) in cases {
        let data = tool_result_frame(input.clone(), ToolPhase::Result);
        assert_eq!(data.get("result"), Some(&expected), "input: {input}");
        assert_eq!(data["toolCallId"], json!("tc-1"));
        assert_eq!(data["isError"], json!(true));
        assert_eq!(data["exitCode"], json!(1));
        assert_eq!(data["args"], json!({"result":"argument"}));
        assert_eq!(data["partialResult"], json!({"result":"partial"}));
    }
}

#[test]
fn tool_result_preserves_nonwrappers_and_objects_with_sibling_fields() {
    let cases = [
        json!("ordinary output"),
        json!(r#"{"result":"unfinished""#),
        json!(r#"{"result":"ok"} trailing output"#),
        json!("null"),
        json!("42"),
        json!("[1,2]"),
        json!(null),
        json!({}),
        json!({"message":"ok"}),
        json!(" { \"message\": \"ok\" } "),
        json!({"result":"ok","error":null}),
        json!(r#"{"result":"ok","metadata":{"count":1}}"#),
        json!({"Result":"case-sensitive"}),
        json!([{"type":"text","text":"{\"result\":\"keep\"}"}]),
        json!({"content":[{"type":"text","text":"{\"result\":\"keep\"}"}]}),
    ];
    for input in cases {
        let data = tool_result_frame(input.clone(), ToolPhase::Result);
        assert_eq!(data.get("result"), Some(&input), "input: {input}");
    }
}

#[test]
fn tool_result_wrapper_handling_does_not_change_start_or_update_events() {
    for phase in [ToolPhase::Start, ToolPhase::Update] {
        let data = tool_result_frame(json!({"result":"keep"}), phase);
        assert_eq!(data["result"], json!({"result":"keep"}));
        assert_eq!(data["partialResult"], json!({"result":"partial"}));
    }
}

#[test]
fn forbidden_event_kinds_are_rejected() {
    // 每个变体走 event_to_frame 必须返回 FrameError::Unsupported：这是
    // "禁止上线" 契约的直接断言（spec §2 旧 approval/phase 不得用于新接入；
    // ping/unknown 由调用方过滤，不可当业务帧编码）。
    use bcs_protocol::stream::{AgentData, AgentEvent, ApprovalData, ApprovalPhase, PhaseData};
    use serde_json::Value;

    fn assert_unsupported(ev: &StreamEvent) {
        match event_to_frame(ev, 1, 1, "r") {
            Err(FrameError::Unsupported) => {}
            other => panic!("expected FrameError::Unsupported, got {other:?}"),
        }
    }

    // ping 不是业务帧（调用方过滤），不可编码
    let ping = StreamEvent::Ping { ts: None };
    assert_unsupported(&ping);

    // unknown 顶层事件不可编码
    let unknown = StreamEvent::Unknown { event: "mystery".into(), raw: Value::Null };
    assert_unsupported(&unknown);

    // 旧 approval 结构禁止上线（spec：不能用于新接入）
    let approval = StreamEvent::Agent(AgentEvent {
        run_id: "r".into(),
        seq: None,
        ts: None,
        session_id: None,
        data: AgentData::Approval(ApprovalData {
            phase: ApprovalPhase::Requested,
            kind: Some("exec".into()),
            status: None,
            approval_id: None,
            tool_call_id: None,
            questions: None,
            answers: None,
        }),
        raw: Value::Null,
    });
    assert_unsupported(&approval);

    // Phase 暂不发
    let phase = StreamEvent::Agent(AgentEvent {
        run_id: "r".into(),
        seq: None,
        ts: None,
        session_id: None,
        data: AgentData::Phase(PhaseData { from_phase: None, to_phase: None }),
        raw: Value::Null,
    });
    assert_unsupported(&phase);

    // agent unknown stream 不可编码
    let agent_unknown = StreamEvent::Agent(AgentEvent {
        run_id: "r".into(),
        seq: None,
        ts: None,
        session_id: None,
        data: AgentData::Unknown { stream: "bogus".into(), raw: Value::Null },
        raw: Value::Null,
    });
    assert_unsupported(&agent_unknown);
}
