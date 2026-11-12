use super::*;

#[tokio::test]
async fn bot_response_registers_state_machine_delivery_alias() {
    let state = new_state();
    *state.collaboration_runtime.correlation.lock().await = Some(StateMachineDeliveryCorrelation {
        state_machine_run_id: "sm-run-1".to_string(),
        node_id: "review".to_string(),
        attempt: 1,
        assignee_bot_id: "bot-compat:staff".to_string(),
        delivery_request_id: "delivery-1".to_string(),
        bot_delivery_run_id: None,
    });
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    let response = BcsFrame::Response(ResponseFrame::ok(
        "delivery-1",
        serde_json::json!({"run_id": "bot-run-actual"}),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&response).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert_eq!(
        *state.collaboration_runtime.aliases.lock().await,
        vec![("delivery-1".to_string(), "bot-run-actual".to_string())]
    );
}

#[tokio::test]
async fn task_response_ack_from_target_worker_registers_sub_run_alias() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());

    state
        .dispatch_state
        .run_channels
        .register_internal(
            "task-1".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("workbench-ws".to_string()),
            Some("human_1".to_string()),
        )
        .await;

    let response = BcsFrame::Response(ResponseFrame::ok(
        "task-1",
        serde_json::json!({"run_id": "worker-run-1"}),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&response).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert_eq!(
        *state.message_flow.task_aliases.lock().await,
        vec![(
            "task-1".to_string(),
            "worker-run-1".to_string(),
            "bot-worker".to_string()
        )]
    );
    assert_eq!(
        state
            .dispatch_state
            .run_channels
            .session_for_run("worker-run-1")
            .await
            .as_deref(),
        Some("group-1:abcdef12")
    );
}

#[tokio::test]
async fn direct_chat_response_alias_carries_run_context_to_streaming_events() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());
    let session_id = "bcs-cli:caller-bot:abcdef12";

    state
        .dispatch_state
        .run_channels
        .register_internal(
            "direct-outer-run".to_string(),
            session_id.to_string(),
            client_tx,
            Some("http-chat-async".to_string()),
            None,
        )
        .await;
    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "direct-outer-run".to_string(),
            bot_id: "bot-compat:staff".to_string(),
            group_id: String::new(),
            bcs_session_id: Some(session_id.to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let response = BcsFrame::Response(ResponseFrame::ok(
        "direct-outer-run",
        serde_json::json!({"run_id": "direct-inner-run"}),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&response).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let alias_context = state
        .bot_run_context
        .get_context("direct-inner-run")
        .await
        .expect("accepted direct run id should inherit its context");
    assert!(alias_context.group_id.is_empty());
    assert_eq!(alias_context.bcs_session_id.as_deref(), Some(session_id));

    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "direct-inner-run",
            "bcs_group_id": session_id,
            "state": WireChatEventState::Final
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
    assert!(events[0].group_id.is_empty());
    assert_eq!(events[0].bcs_session_id.as_deref(), Some(session_id));
}

#[tokio::test]
async fn task_response_ack_from_manager_does_not_register_sub_run_alias() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());

    state
        .dispatch_state
        .run_channels
        .register_internal(
            "task-1".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("workbench-ws".to_string()),
            Some("human_1".to_string()),
        )
        .await;

    let response = BcsFrame::Response(ResponseFrame::ok(
        "task-1",
        serde_json::json!({"run_id": "manager-run-1"}),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&response).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert_eq!(
        *state.message_flow.task_aliases.lock().await,
        vec![(
            "task-1".to_string(),
            "manager-run-1".to_string(),
            "bot-manager".to_string()
        )]
    );
    assert_eq!(
        state
            .dispatch_state
            .run_channels
            .session_for_run("manager-run-1")
            .await,
        None
    );
}
