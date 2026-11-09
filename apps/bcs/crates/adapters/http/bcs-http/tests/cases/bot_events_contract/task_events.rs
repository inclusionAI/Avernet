use super::*;

#[tokio::test]
async fn managed_late_terminal_uses_durable_context_after_cache_loss() {
    let TestApp { app, provider_core, provider_bot_core, run_context, message_flow, _temp_dir, .. } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(provider_core.as_ref(), provider_bot_core.as_ref(), ProviderAuthMode::StaticBearer, "late-terminal").await;
    *message_flow.managed_context.lock().await = Some((registered.provider_id.clone(), BotRunContext {
        run_id: "durable-run".into(), bot_id: registered.bot_uuid, group_id: "original-group".into(),
        bcs_session_id: Some("original-session".into()), deadline_ms: u64::MAX, terminal: false,
    }));
    assert!(run_context.get_context("durable-run").await.is_none());
    let response = app.oneshot(Request::builder().method("POST").uri("/bot/events")
        .header("content-type", "application/json").header("X-BCN-Provider-Id", registered.provider_id)
        .header("authorization", format!("Bearer {}", registered.bot_runtime_token.unwrap()))
        .body(Body::from(json!({"run_id":"durable-run","state":"final","message":{"text":"late result"}}).to_string())).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].group_id, "original-group");
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("original-session"));
}

#[tokio::test]
async fn bot_events_defaults_missing_message_to_empty_text() {
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
            run_id: "run-missing-message".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:missing-message".to_string()),
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
                        "run_id": "run-missing-message",
                        "state": "final"
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
    assert_eq!(cmd.bot_id, registered.bot_uuid);
    assert_eq!(cmd.run_id, "run-missing-message");
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.bcs_session_id.as_deref(), Some("group-1:missing-message"));
    assert_eq!(cmd.state, ChatEventState::Final);
    assert_eq!(cmd.event_payload["state"], "final");
    assert_eq!(cmd.event_payload["message"]["content"][0]["text"], "");
    drop(events);
    assert!(run_context
        .get_context("run-missing-message")
        .await
        .unwrap()
        .terminal);
}
#[tokio::test]
async fn bot_events_rejects_delta_state() {
    let TestApp {
        app, _temp_dir, ..
    } = test_app(Arc::new(StaticAgentpassResolver::default()));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "run_id": "run-1",
                        "state": "delta",
                        "message": { "text": "partial" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["error"], "invalid_request");
}
#[tokio::test]
async fn bot_events_rejects_identity_run_mismatch() {
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
            run_id: "run-mismatch".to_string(),
            bot_id: "other-bot".to_string(),
            group_id: "group-1".to_string(),
            bcs_session_id: None,
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
                        "run_id": "run-mismatch",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(message_flow.events.lock().await.is_empty());
    assert!(!run_context
        .get_context("run-mismatch")
        .await
        .unwrap()
        .terminal);
}
#[tokio::test]
async fn bot_events_releases_terminal_processing_when_message_flow_fails() {
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
            run_id: "run-retry".to_string(),
            bot_id: registered.bot_uuid,
            group_id: "group-1".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;
    *message_flow.fail_next.lock().await = true;

    let failed_response = app
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
                        "run_id": "run-retry",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(failed_response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert!(!run_context.get_context("run-retry").await.unwrap().terminal);

    let retry_response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-retry",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(retry_response.status(), StatusCode::OK);
    assert!(run_context.get_context("run-retry").await.unwrap().terminal);
}
