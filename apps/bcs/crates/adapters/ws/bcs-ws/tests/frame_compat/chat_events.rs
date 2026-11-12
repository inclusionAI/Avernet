use super::*;

#[tokio::test]
async fn bot_chat_event_frame_is_forwarded_to_message_flow() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1",
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
    assert_eq!(events[0].bot_id, "bot-compat:staff");
    assert_eq!(events[0].run_id, "run-1");
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].state, ChatEventState::Final);
    assert_eq!(
        events[0].bcs_session_id.as_deref(),
        Some("group-1:00000000")
    );
}

#[tokio::test]
async fn direct_chat_event_scope_comes_from_run_context_for_all_session_key_shapes() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    for (index, session_id) in [
        "bcs-cli:caller-bot:abcdef12",
        "chat:abcdef12",
        "caller-supplied-session",
    ]
    .into_iter()
    .enumerate()
    {
        let run_id = format!("direct-run-{index}");
        state
            .bot_run_context
            .put_context(BotRunContext {
                run_id: run_id.clone(),
                bot_id: "bot-compat:staff".to_string(),
                group_id: String::new(),
                bcs_session_id: Some(session_id.to_string()),
                deadline_ms: u64::MAX,
                terminal: false,
            })
            .await;
        let event = BcsFrame::Event(EventFrame::new(
            "chat.event",
            Some(serde_json::json!({
                "run_id": run_id,
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
    }

    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 3);
    for (event, expected_session_id) in events.iter().zip([
        "bcs-cli:caller-bot:abcdef12",
        "chat:abcdef12",
        "caller-supplied-session",
    ]) {
        assert!(event.group_id.is_empty());
        assert_eq!(event.bcs_session_id.as_deref(), Some(expected_session_id));
    }
}

#[tokio::test]
async fn rejected_group_request_uses_canonical_message_flow_error_and_retains_lookup_failure() {
    let state = new_state();
    *state.bot_run_context.active.lock().await = Some(bcs_service_api::ActiveBotRunContext {
        canonical_run_id: "canonical".into(), downstream_run_id: "alias".into(),
        downstream_session_key: None, provider_bypass_headers: Vec::new(), deadline_ms: 1,
        scope: bcs_service_api::BotRunScope { group_id: "group".into(), session_id: "session".into(), bot_id: "bot".into() },
        transport_owner: bcs_service_api::BotRunTransportOwner::WebSocket,
    });
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered = Some("bot".to_string());
    let frame = serde_json::to_string(&BcsFrame::Response(ResponseFrame::error("alias", "LIMIT", "请求过多"))).unwrap();
    dispatch_frame(&state.dispatch_state, &frame, &tx, &mut registered).await.unwrap();
    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].run_id, "canonical");
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("session"));
    assert_eq!(events[0].state, ChatEventState::Error);
    assert_eq!(events[0].event_payload["errorMessage"], "请求过多");
    drop(events);
    assert!(rx.try_recv().is_err(), "no second direct error response");
    state.bot_run_context.fail_lookup.store(true, Ordering::SeqCst);
    assert!(dispatch_frame(&state.dispatch_state, &frame, &tx, &mut registered).await.is_err());
    assert_eq!(state.message_flow.bot_events.lock().await.len(), 1);
}

#[tokio::test]
async fn bot_chat_event_frame_unwraps_v2_session_id_group_id() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-session-1",
            "bcs_group_id": "group-1:abcdef12",
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
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("group-1:abcdef12"));
}

#[tokio::test]
async fn bot_event_frame_restores_session_id_from_sub_run_mapping() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    state
        .dispatch_state
        .run_channels
        .register_internal(
            "outer-run".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("workbench-ws".to_string()),
            Some("human_1".to_string()),
        )
        .await;

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"res","id":"outer-run","ok":true,"payload":{"run_id":"sub-run"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let event = BcsFrame::Event(EventFrame::new(
        "agent",
        Some(serde_json::json!({
            "run_id": "sub-run",
            "bcs_group_id": "group-1",
            "stream": "assistant",
            "ts": 123,
            "data": {
                "delta": "hi"
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
    assert_eq!(events[0].run_id, "sub-run");
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("group-1:abcdef12"));
}

#[tokio::test]
async fn bot_chat_event_frame_uses_explicit_bcs_session_id() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-session-2",
            "bcs_group_id": "group-1",
            "bcs_session_id": "group-1:abcdef12",
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
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("group-1:abcdef12"));
}
