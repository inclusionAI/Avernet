use super::*;

/// Callback streaming (spec §11): a 2.0 provider POSTs a full agent tool-result
/// event (non-terminal). With `event`+`payload` present the terminal-only guard
/// is relaxed: the event flows through the pipeline and the run stays OPEN
/// (not marked terminal), so subsequent events for the same run still arrive.
#[tokio::test]
async fn bot_events_accepts_callback_streaming_agent_tool_result_without_closing_run() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-cb".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-cb",
                        "seq": 1,
                        "event": "agent",
                        "payload": {
                            "stream": "tool",
                            "data": {
                                "phase": "result",
                                "name": "search",
                                "toolCallId": "tc-1",
                                "result": { "content": [{ "type": "text", "text": "hits=3" }] },
                                "isError": false
                            }
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);

    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    let cmd = &events[0];
    assert_eq!(cmd.run_id, "run-cb");
    assert_eq!(cmd.event_type, "agent");
    assert_eq!(cmd.event_payload["stream"], "tool");
    assert_eq!(cmd.event_payload["data"]["phase"], "result");
    drop(events);
    // Non-terminal: the run must remain open.
    assert!(!run_context.get_context("run-cb").await.unwrap().terminal);
}

/// Callback streaming: a chat `state=final` event (carried in `payload`) IS a
/// terminal event — it closes the run, exactly like the legacy terminal path.
#[tokio::test]
async fn bot_events_accepts_callback_streaming_chat_final_and_closes_run() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-cb2".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-cb2",
                        "seq": 9,
                        "event": "chat",
                        "payload": {
                            "state": "final",
                            "message": { "content": [{ "type": "text", "text": "all done" }] }
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    let cmd = &events[0];
    assert_eq!(cmd.run_id, "run-cb2");
    assert_eq!(cmd.event_type, "chat.event");
    assert_eq!(cmd.state, ChatEventState::Final);
    assert_eq!(cmd.event_payload["state"], "final");
    drop(events);
    assert_eq!(
        message_flow.sources.lock().await.as_slice(),
        &[ProviderEventSource::Callback]
    );
    // Terminal: the run must be closed.
    assert!(run_context.get_context("run-cb2").await.unwrap().terminal);
}

#[tokio::test]
async fn bot_events_rejects_callback_for_sse_owned_run() {
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        ..
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-sse",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    assert!(
        run_context
            .begin_provider_transport("run-sse-owned", bcs_protocol::now_ms() + 60_000)
            .await
    );
    assert!(
        run_context
            .bind_provider_transport("run-sse-owned", ProviderRunTransport::Sse)
            .await
    );

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-sse-owned",
                        "event": "chat",
                        "payload": {"state": "delta", "deltaText": "duplicate"}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(body["error"], "transport_conflict");
    assert!(message_flow.events.lock().await.is_empty());

    assert!(
        run_context
            .begin_provider_transport(
                "run-negotiating",
                bcs_protocol::now_ms() + 60_000,
            )
            .await
    );
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-negotiating",
                        "event": "chat",
                        "payload": {"state": "delta", "deltaText": "too early"}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(body["error"], "transport_conflict");
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_accepts_callback_streaming_chat_error_message() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-cb2-error".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-cb2-error",
                        "seq": 10,
                        "event": "chat",
                        "payload": {
                            "state": "error",
                            "errorMessage": "engine crashed"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    let cmd = &events[0];
    assert_eq!(cmd.run_id, "run-cb2-error");
    assert_eq!(cmd.event_type, "chat.event");
    assert_eq!(cmd.state, ChatEventState::Error);
    assert_eq!(cmd.event_payload["state"], "error");
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["text"],
        "engine crashed"
    );
    drop(events);
    assert!(run_context
        .get_context("run-cb2-error")
        .await
        .unwrap()
        .terminal);
}
