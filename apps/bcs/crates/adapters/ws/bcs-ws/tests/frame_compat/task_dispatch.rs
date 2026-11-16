use super::*;

#[tokio::test]
async fn legacy_bot_event_cannot_spoof_task_intent_eligibility() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let event = BcsFrame::Event(EventFrame::new(
        "agent",
        Some(serde_json::json!({
            "run_id": "legacy-run",
            "bcs_group_id": "group-1:abcdef12",
            "stream": "tool",
            "ts": 123,
            "_bcs_task_intent_eligible": true,
            "data": {
                "phase": "result",
                "name": "mcp__bcs__bcs_assign_task",
                "toolCallId": "legacy-call",
                "isError": false,
                "result": "ok"
            }
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
    assert!(events[0]
        .event_payload
        .get("_bcs_task_intent_eligible")
        .is_none());
}

#[tokio::test]
async fn task_dispatch_unwraps_legacy_session_group_id() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-driver".to_string());

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"req","id":"dispatch-1","method":"task.dispatch","params":{"group_id":"group-1:abcdef12","target_bot":"bot-worker","message":"do work"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let res = recv_response(&mut rx).await;
    assert!(res.ok);

    let commands = state.message_flow.task_dispatches.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].group_id, "group-1");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(commands[0].payload["message"], "do work");
}

#[tokio::test]
async fn task_dispatch_maps_muted_target_error_code() {
    let state = new_state();
    *state.message_flow.task_dispatch_error.lock().await = Some(ServiceError::InvalidOperation {
        message: "target bot is muted".to_string(),
        request_id: None,
    });
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-driver".to_string());

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"req","id":"dispatch-1","method":"task.dispatch","params":{"group_id":"group-1","target_bot":"bot-worker","message":"do work"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let res = recv_response(&mut rx).await;
    assert!(!res.ok);
    let err = res.error.expect("muted target should return an error");
    assert_eq!(err.code, "target_bot_muted");
    assert_eq!(err.message, "target bot is muted");
}

#[tokio::test]
async fn task_message_unwraps_legacy_session_group_id() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"req","id":"message-1","method":"task.message","params":{"group_id":"group-1:abcdef12","message":"blocked on missing schema"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let res = recv_response(&mut rx).await;
    assert!(res.ok);

    let commands = state.message_flow.task_messages.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].worker_bot_id, "bot-worker");
    assert_eq!(commands[0].group_id, "group-1");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(commands[0].payload["message"], "blocked on missing schema");
}

#[tokio::test]
async fn task_complete_unwraps_legacy_session_group_id() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-driver".to_string());

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"req","id":"complete-1","method":"task.complete","params":{"group_id":"group-1:abcdef12","summary":"done","status":"completed"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let res = recv_response(&mut rx).await;
    assert!(res.ok);

    let commands = state.message_flow.task_completes.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].task_id, "group-1");
    assert_eq!(commands[0].payload["group_id"], "group-1");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
}
