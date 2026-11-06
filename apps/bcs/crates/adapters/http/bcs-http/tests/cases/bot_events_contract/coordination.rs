use super::*;

#[tokio::test]
async fn bot_events_returns_200_before_state_machine_final_processing_completes() {
    let collaboration_runtime = Arc::new(BlockingCollaborationRuntime::default());
    let runtime_port: Arc<dyn CollaborationRuntimeService> = collaboration_runtime.clone();
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        ..
    } = test_app_with_collaboration_runtime(
        Arc::new(StaticAgentpassResolver::default()),
        Some(runtime_port),
    );
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "async-state-machine-bot",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    collaboration_runtime
        .insert_correlation("provider-run-async", &registered.bot_uuid)
        .await;

    let app = app.layer(axum::middleware::from_fn(bcs_http::gateway_trace::observe_request));
    let mut request = state_machine_final_request(&registered.provider_id, &token, "provider-run-async");
    request.headers_mut().insert("x-request-id", "async-final-42".parse().unwrap());
    let response = tokio::time::timeout(
        Duration::from_secs(1),
        app.oneshot(request),
    )
    .await
    .expect("state-machine final callback should not wait for background processing")
    .expect("state-machine final callback response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);
    assert_eq!(body["failed_count"], 0);

    collaboration_runtime.wait_for_terminal_start().await;
    collaboration_runtime.release_terminal();
    collaboration_runtime.wait_for_terminal_completion().await;
    assert_eq!(collaboration_runtime.terminal_request_ids.lock().await.as_slice(), ["async-final-42"]);
}

#[tokio::test]
async fn bot_events_coalesces_duplicate_state_machine_final_while_processing() {
    let collaboration_runtime = Arc::new(BlockingCollaborationRuntime::default());
    let runtime_port: Arc<dyn CollaborationRuntimeService> = collaboration_runtime.clone();
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        ..
    } = test_app_with_collaboration_runtime(
        Arc::new(StaticAgentpassResolver::default()),
        Some(runtime_port),
    );
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "deduplicated-state-machine-bot",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    collaboration_runtime
        .insert_correlation("provider-run-first", &registered.bot_uuid)
        .await;
    collaboration_runtime
        .insert_correlation("provider-run-retry", &registered.bot_uuid)
        .await;

    let first = app
        .clone()
        .oneshot(state_machine_final_request(
            &registered.provider_id,
            &token,
            "provider-run-first",
        ))
        .await
        .expect("first callback response");
    assert_eq!(first.status(), StatusCode::OK);
    collaboration_runtime.wait_for_terminal_start().await;

    let duplicate = app
        .oneshot(state_machine_final_request(
            &registered.provider_id,
            &token,
            "provider-run-retry",
        ))
        .await
        .expect("duplicate callback response");
    assert_eq!(duplicate.status(), StatusCode::OK);
    let body = response_json(duplicate).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);
    assert_eq!(collaboration_runtime.terminal_call_count().await, 1);

    collaboration_runtime.release_terminal();
    collaboration_runtime.wait_for_terminal_completion().await;
}

#[tokio::test]
async fn bot_events_rejects_state_machine_identity_mismatch_before_spawning() {
    let collaboration_runtime = Arc::new(BlockingCollaborationRuntime::default());
    let runtime_port: Arc<dyn CollaborationRuntimeService> = collaboration_runtime.clone();
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        ..
    } = test_app_with_collaboration_runtime(
        Arc::new(StaticAgentpassResolver::default()),
        Some(runtime_port),
    );
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "mismatched-state-machine-bot",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    collaboration_runtime
        .insert_correlation("provider-run-mismatch", "different-bot")
        .await;

    let response = app
        .oneshot(state_machine_final_request(
            &registered.provider_id,
            &token,
            "provider-run-mismatch",
        ))
        .await
        .expect("identity mismatch response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(collaboration_runtime.terminal_call_count().await, 0);
}

#[tokio::test]
async fn bot_events_accepts_empty_state_machine_final_for_runtime_failure_handling() {
    let collaboration_runtime = Arc::new(BlockingCollaborationRuntime::default());
    let runtime_port: Arc<dyn CollaborationRuntimeService> = collaboration_runtime.clone();
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        ..
    } = test_app_with_collaboration_runtime(
        Arc::new(StaticAgentpassResolver::default()),
        Some(runtime_port),
    );
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "empty-state-machine-bot",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    collaboration_runtime
        .insert_correlation("provider-run-empty", &registered.bot_uuid)
        .await;

    let response = app
        .oneshot(state_machine_final_request_with_text(
            &registered.provider_id,
            &token,
            "provider-run-empty",
            "   ",
        ))
        .await
        .expect("empty final response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    collaboration_runtime.wait_for_terminal_start().await;
    assert_eq!(collaboration_runtime.terminal_call_count().await, 1);
    collaboration_runtime.release_terminal();
    collaboration_runtime.wait_for_terminal_completion().await;
}
