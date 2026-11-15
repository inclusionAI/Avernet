use super::*;

#[tokio::test]
async fn bot_events_accepts_static_bearer_final_for_matching_run() {
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
            run_id: "run-1".to_string(),
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
                        "run_id": "run-1",
                        "seq": 1,
                        "state": "final",
                        "message": { "text": "done" }
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
    assert_eq!(cmd.run_id, "run-1");
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert_eq!(cmd.state, ChatEventState::Final);
    assert_eq!(cmd.event_payload["state"], "final");
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["type"],
        "text"
    );
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["text"],
        "done"
    );
    drop(events);
    assert!(run_context.get_context("run-1").await.unwrap().terminal);
}
#[tokio::test]
async fn bot_events_accepts_agentpass_final_for_matching_run() {
    let resolver = StaticAgentpassResolver::new(HashMap::from([(
        "agentpass.header.sig".to_string(),
        "agent-code-1".to_string(),
    )]));
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(resolver));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-1",
    )
    .await;
    assert!(registered.bot_runtime_token.is_none());
    run_context
        .put_context(BotRunContext {
            run_id: "run-agentpass".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-agentpass".to_string(),
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
                .header("authorization", "Bearer agentpass.header.sig")
                .body(Body::from(
                    json!({
                        "run_id": "run-agentpass",
                        "state": "final",
                        "message": { "text": "agentpass done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].bot_id, registered.bot_uuid);
    assert_eq!(events[0].event_payload["state"], "final");
    assert_eq!(
        events[0].event_payload["message"]["content"][0]["type"],
        "text"
    );
    assert_eq!(
        events[0].event_payload["message"]["content"][0]["text"],
        "agentpass done"
    );
}

#[tokio::test]
async fn bot_events_accepts_static_bearer_error_for_matching_run() {
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
            run_id: "run-error".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:error-session".to_string()),
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
                        "run_id": "run-error",
                        "seq": 1,
                        "state": "error",
                        "message": { "text": "provider failed" }
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
    assert_eq!(cmd.run_id, "run-error");
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.bcs_session_id.as_deref(), Some("group-1:error-session"));
    assert_eq!(cmd.state, ChatEventState::Error);
    assert_eq!(cmd.event_payload["state"], "error");
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["type"],
        "text"
    );
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["text"],
        "provider failed"
    );
    drop(events);
    assert!(run_context.get_context("run-error").await.unwrap().terminal);
}
#[tokio::test]
async fn bot_events_does_not_fallback_to_agentpass_on_static_provider_mismatch() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let static_bot = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let static_token = static_bot.bot_runtime_token.expect("runtime token");
    resolver
        .tokens
        .write()
        .await
        .insert(static_token.clone(), "agent-code-2".to_string());
    let agentpass_bot = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-2",
    )
    .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-no-fallback".to_string(),
            bot_id: agentpass_bot.bot_uuid,
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
                .header("X-BCN-Provider-Id", agentpass_bot.provider_id.as_str())
                .header("authorization", format!("Bearer {static_token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-no-fallback",
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
}

#[tokio::test]
async fn bot_events_routes_jwt_shaped_token_to_agentpass_before_static_lookup() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let static_bot = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let jwt_token = "jwt.header.sig";
    registry
        .save_token(&static_bot.bot_uuid, jwt_token)
        .await
        .expect("save jwt-shaped static token");
    resolver
        .tokens
        .write()
        .await
        .insert(jwt_token.to_string(), "agent-code-2".to_string());
    let agentpass_bot = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-2",
    )
    .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-jwt-agentpass".to_string(),
            bot_id: agentpass_bot.bot_uuid.clone(),
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
                .header("X-BCN-Provider-Id", agentpass_bot.provider_id.as_str())
                .header("authorization", format!("Bearer {jwt_token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-jwt-agentpass",
                        "state": "final",
                        "message": { "text": "agentpass done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].bot_id, agentpass_bot.bot_uuid);
}
#[tokio::test]
async fn agentpass_resolve_returns_agent_code_binding_bot_and_complete_jwt() {
    let jwt_header = json!({
        "alg": "RS256",
        "kid": "agentpass-key-1",
        "typ": "JWT"
    });
    let jwt_payload = json!({
        "act": "agent-code-1",
        "sno": "001234",
        "sub": "operator-account",
        "enabled": true,
        "sequence": 7,
        "roles": ["reviewer", "planner"],
        "context": { "region": "cn", "optional": null }
    });
    let signature = b"distinct-agentpass-signature";
    let token = diagnostic_jwt(jwt_header.clone(), jwt_payload.clone(), signature);
    let resolver = StaticAgentpassResolver::new(HashMap::from([(
        token.clone(),
        "agent-code-1".to_string(),
    )]));
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        ..
    } = test_app(Arc::new(resolver));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-1",
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers/agentpass/resolve")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response
            .headers()
            .get("cache-control")
            .and_then(|value| value.to_str().ok()),
        Some("no-store")
    );
    let body = response_json(response).await;
    assert_eq!(body["agent_code"], "agent-code-1");
    assert_eq!(body["provider_bot_binding"]["provider_id"], registered.provider_id);
    assert_eq!(body["provider_bot_binding"]["provider_bot_ref"], "agent-code-1");
    assert_eq!(body["provider_bot_binding"]["bot_uuid"], registered.bot_uuid);
    assert_eq!(body["bot"]["bot_uuid"], registered.bot_uuid);
    assert_eq!(body["bot"]["capabilities"]["name"], "Code Reviewer");
    assert!(body["bot"].get("dynamic_status").is_none());
    assert_eq!(body["jwt"]["header"], jwt_header);
    assert_eq!(body["jwt"]["payload"], jwt_payload);
    assert_eq!(body["jwt"]["signature"]["present"], true);
    assert_eq!(body["jwt"]["signature"]["byte_length"], signature.len());
    assert_eq!(body["jwt"]["agentpass_resolved"], true);
    assert!(body["jwt"].get("token").is_none());
    assert!(body["jwt"]["signature"].get("value").is_none());
    let serialized = body.to_string();
    let signature_segment = token.rsplit('.').next().unwrap();
    assert!(!serialized.contains(&token));
    assert!(!serialized.contains(signature_segment));
}

#[tokio::test]
async fn agentpass_resolve_formats_jwt_when_token_cannot_be_resolved() {
    let jwt_header = json!({ "alg": "RS256", "typ": "JWT" });
    let jwt_payload = json!({
        "act": "unbound-agent",
        "sno": "009999",
        "custom": { "values": [1, false, null] }
    });
    let token = diagnostic_jwt(
        jwt_header.clone(),
        jwt_payload.clone(),
        b"unresolved-signature",
    );
    let TestApp { app, .. } = test_app(Arc::new(StaticAgentpassResolver::default()));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers/agentpass/resolve")
                .header("X-BCN-Provider-Id", "prv_missing")
                .header("authorization", format!("Bearer {token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response
            .headers()
            .get("cache-control")
            .and_then(|value| value.to_str().ok()),
        Some("no-store")
    );
    let body = response_json(response).await;
    assert!(body["agent_code"].is_null());
    assert!(body["provider_bot_binding"].is_null());
    assert!(body["bot"].is_null());
    assert_eq!(body["jwt"]["header"], jwt_header);
    assert_eq!(body["jwt"]["payload"], jwt_payload);
    assert_eq!(body["jwt"]["agentpass_resolved"], false);
}

#[tokio::test]
async fn agentpass_resolve_reports_bounded_error_for_malformed_jwt() {
    let TestApp { app, .. } = test_app(Arc::new(StaticAgentpassResolver::default()));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers/agentpass/resolve")
                .header("X-BCN-Provider-Id", "prv_missing")
                .header("authorization", "Bearer ***.e30.c2ln")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response
            .headers()
            .get("cache-control")
            .and_then(|value| value.to_str().ok()),
        Some("no-store")
    );
    let body = response_json(response).await;
    assert!(body["agent_code"].is_null());
    assert!(body["jwt"]["header"].is_null());
    assert!(body["jwt"]["payload"].is_null());
    assert_eq!(body["jwt"]["parse_error"], "invalid_header_encoding");
    assert_eq!(body["jwt"]["agentpass_resolved"], false);
}
#[tokio::test]
async fn agentpass_resolve_returns_agent_code_with_null_binding_when_mapping_is_absent() {
    let resolver = StaticAgentpassResolver::new(HashMap::from([(
        "agentpass.header.sig".to_string(),
        "missing-agent-code".to_string(),
    )]));
    let TestApp { app, .. } = test_app(Arc::new(resolver));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers/agentpass/resolve")
                .header("X-BCN-Provider-Id", "prv_missing")
                .header("authorization", "Bearer agentpass.header.sig")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["agent_code"], "missing-agent-code");
    assert!(body["provider_bot_binding"].is_null());
    assert!(body["bot"].is_null());
}

#[tokio::test]
async fn agentpass_resolve_returns_binding_with_null_bot_when_bot_row_is_missing() {
    let resolver = StaticAgentpassResolver::new(HashMap::from([(
        "agentpass.header.sig".to_string(),
        "agent-code-1".to_string(),
    )]));
    let TestApp {
        app,
        registry,
        provider_core,
        provider_bot_core,
        ..
    } = test_app(Arc::new(resolver));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-1",
    )
    .await;
    assert!(registry.unregister(&registered.bot_uuid).await);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers/agentpass/resolve")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", "Bearer agentpass.header.sig")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["agent_code"], "agent-code-1");
    assert_eq!(body["provider_bot_binding"]["bot_uuid"], registered.bot_uuid);
    assert!(body["bot"].is_null());
}
#[tokio::test]
async fn bot_events_accepts_provider_admin_with_bot_ref() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-admin".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
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
                .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-admin",
                        "state": "final",
                        "message": { "text": "admin done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].bot_id, registered.bot_uuid);
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_without_bot_ref() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-admin-noref".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
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
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-admin-noref",
                        "state": "final",
                        "message": { "text": "done" }
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
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_when_run_bot_mismatches_ref() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-mismatch".to_string(),
            bot_id: "other-bot".to_string(),
            group_id: "group-admin".to_string(),
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
                .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
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
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_provider_id_mismatch() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    let other = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "other-reviewer",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-provider-mismatch".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
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
                .header("X-BCN-Provider-Id", other.provider_id.as_str())
                .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-provider-mismatch",
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
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_id_mismatch");
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_unknown_bot_ref() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-unknown-ref".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
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
                .header("X-BCN-Provider-Bot-Ref", "missing-ref")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-unknown-ref",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = response_json(response).await;
    assert_eq!(body["error"], "bot_not_found");
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_token_for_static_bearer_provider() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-mode-mismatch".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
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
                .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-mode-mismatch",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["error"], "auth_mode_mismatch");
    assert!(message_flow.events.lock().await.is_empty());
}
