use super::*;

#[tokio::test]
async fn bot_v3_native_mcp_declaration_does_not_self_authorize_task_intent() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-untrusted-profile",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-untrusted-profile",
            "protocol_version": 3,
            "client_kind": "native_mcp"
        })),
    ));

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    let payload = connected.payload.unwrap();
    assert_eq!(payload["protocol_version"], 3);
    assert_eq!(payload["capabilities"]["unified_run_events"], true);
    assert_eq!(payload["capabilities"]["tool_result_task_intent"], false);
    assert_eq!(
        state
            .dispatch_state
            .bot_connections
            .protocol("bot-untrusted-profile")
            .await
            .expect("connected protocol")
            .client_kind,
        None
    );
}

#[tokio::test]
async fn bot_v3_connect_negotiates_canonical_events_and_validates_run_sequence() {
    let state = new_state();
    state
        .bot_runtime
        .trust_native_mcp
        .store(true, Ordering::Relaxed);
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-v3",
            "protocol_version": 3,
            "client_kind": " Native_MCP "
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    let payload = connected.payload.unwrap();
    assert_eq!(payload["protocol_version"], 3);
    assert_eq!(payload["capabilities"]["unified_run_events"], true);
    assert_eq!(payload["capabilities"]["tool_result_task_intent"], true);
    assert_eq!(payload["capabilities"]["canonical_session_id"], true);
    assert_eq!(
        state
            .dispatch_state
            .bot_connections
            .protocol("bot-v3")
            .await
            .expect("connected protocol")
            .client_kind
            .as_deref(),
        Some("native_mcp")
    );

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-v3".to_string(),
            bot_id: "bot-v3".to_string(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    for (seq, phase) in [(1, "start"), (2, "result")] {
        let data = if phase == "start" {
            serde_json::json!({
                "phase": phase,
                "name": "mcp__bcs__bcs_assign_task",
                "toolCallId": "call-v3",
                "args": {"target_bot": "bot-worker", "message": "review"}
            })
        } else {
            serde_json::json!({
                "phase": phase,
                "name": "mcp__bcs__bcs_assign_task",
                "toolCallId": "call-v3",
                "isError": false,
                "result": {"content": [{"type": "text", "text": "ok"}]}
            })
        };
        let mut payload = serde_json::json!({
            "runId": "run-v3",
            "sessionId": "group-1:abcdef12",
            "seq": seq,
            "ts": 123,
            "stream": "tool"
        });
        payload
            .as_object_mut()
            .unwrap()
            .extend(data.as_object().unwrap().clone());
        let event = BcsFrame::Event(EventFrame::new("agent", Some(payload), Some(seq)));
        dispatch_frame(
            &state.dispatch_state,
            &serde_json::to_string(&event).unwrap(),
            &tx,
            &mut registered_bot_id,
        )
        .await
        .unwrap();
    }

    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 2);
    assert_eq!(events[1].group_id, "group-1");
    assert_eq!(events[1].bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert_eq!(events[1].event_payload["_bcs_task_intent_eligible"], true);
    drop(events);

    let duplicate = BcsFrame::Event(EventFrame::new(
        "chat",
        Some(serde_json::json!({
            "runId": "run-v3",
            "sessionId": "group-1:abcdef12",
            "seq": 2,
            "ts": 124,
            "state": "delta",
            "content": "duplicate"
        })),
        Some(2),
    ));
    let error = dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&duplicate).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect_err("V3 sequence must be strictly increasing per run");
    assert!(error.to_string().contains("duplicate or regressed"));
}

#[tokio::test]
async fn bot_v3_agent_error_is_adapted_to_terminal_chat_error() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3-error",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-v3-error",
            "protocol_version": 3
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-v3-error".to_string(),
            bot_id: "bot-v3-error".to_string(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let event = BcsFrame::Event(EventFrame::new(
        "agent",
        Some(serde_json::json!({
            "runId": "run-v3-error",
            "sessionId": "group-1:abcdef12",
            "seq": 1,
            "ts": 123,
            "stream": "error",
            "errorCode": "MODEL_ERROR",
            "errorMessage": "model failed"
        })),
        Some(1),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].event_type, "chat.event");
    assert_eq!(events[0].state, ChatEventState::Error);
    assert_eq!(events[0].event_payload["state"], "error");
    assert_eq!(events[0].event_payload["errorCode"], "MODEL_ERROR");
    assert_eq!(events[0].event_payload["errorMessage"], "model failed");
}

#[tokio::test]
async fn bot_v3_releases_sequence_when_business_processing_fails() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3-retry",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-v3-retry",
            "protocol_version": 3
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);
    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-v3-retry".to_string(),
            bot_id: "bot-v3-retry".to_string(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;
    let event = BcsFrame::Event(EventFrame::new(
        "chat",
        Some(serde_json::json!({
            "runId": "run-v3-retry",
            "sessionId": "group-1:abcdef12",
            "seq": 1,
            "ts": 123,
            "state": "delta",
            "content": "retry me"
        })),
        Some(1),
    ));
    let encoded = serde_json::to_string(&event).unwrap();
    state
        .message_flow
        .fail_bot_event_once
        .store(true, Ordering::Relaxed);

    let first = dispatch_frame(
        &state.dispatch_state,
        &encoded,
        &tx,
        &mut registered_bot_id,
    )
    .await;
    assert!(first.is_err());
    dispatch_frame(
        &state.dispatch_state,
        &encoded,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect("same sequence should be retryable after processing failure");
    assert_eq!(state.message_flow.bot_events.lock().await.len(), 1);
}

#[tokio::test]
async fn bot_v3_duplicate_terminal_frame_is_idempotent() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3-terminal",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-v3-terminal",
            "protocol_version": 3
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);
    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-v3-terminal".to_string(),
            bot_id: "bot-v3-terminal".to_string(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;
    let event = BcsFrame::Event(EventFrame::new(
        "chat",
        Some(serde_json::json!({
            "runId": "run-v3-terminal",
            "sessionId": "group-1:abcdef12",
            "seq": 1,
            "ts": 123,
            "state": "final",
            "content": "done"
        })),
        Some(1),
    ));
    let encoded = serde_json::to_string(&event).unwrap();

    dispatch_frame(
        &state.dispatch_state,
        &encoded,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    dispatch_frame(
        &state.dispatch_state,
        &encoded,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .expect("duplicate terminal frame should be ignored");
    assert_eq!(state.message_flow.bot_events.lock().await.len(), 1);
}
