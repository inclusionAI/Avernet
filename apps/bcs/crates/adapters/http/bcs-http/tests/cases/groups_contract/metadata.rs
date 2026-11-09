use super::*;

#[tokio::test]
async fn get_group_preserves_legacy_detail_payload_from_query_service() {
    let group_store = Arc::new(GroupStore::new());
    let query = Arc::new(bcs_group::GroupManagement::with_defaults(
        group_store.clone(),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
    ));

    let mut group = Group::new(
        "group-with-detail",
        "driver-bot",
        vec![Participant {
            bot_uuid: "driver-bot".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: Some(ParticipantKind::Bot),
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
            tags: Vec::new(),
            message_view_scope: bcs_domain::MessageViewScope::Full,
        }],
    );
    group.messages = vec![GroupMessage {
        id: "msg-1".to_string(),
        timestamp: 42,
        sender: "driver-bot".to_string(),
        content: "hello".to_string(),
        message_type: Default::default(),
        bot_name: None,
        role: Default::default(),
        history_meta: None,
        metadata: None,
        run_id: String::new(),
        attachments: None,
    }];
    group.service_group_uuid = Some("service-group-1".to_string());
    group.service_mode = Some("master_slave".to_string());
    group.dm_pair_key = Some("bot-a|bot-b".to_string());
    group_store.upsert(group).await.unwrap();

    let services = Services::builder().group_query(query).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/group-with-detail")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "group-with-detail");
    assert_eq!(json["message_count"], 1);
    assert!(json.get("workspace").is_none());
    assert_eq!(json["service_group_uuid"], "service-group-1");
    assert_eq!(json["service_mode"], "master_slave");
    assert_eq!(json["dm_pair_key"], "bot-a|bot-b");
    assert_eq!(json["human_mention_notify_mode"], "all");
    assert_eq!(json["participants"][0]["type"], "bot");
}
#[tokio::test]
async fn put_group_status_delegates_to_group_management_status_and_preserves_response_shape() {
    let (app, recorder, _temp_dir) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-1/status")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "status": "completed",
                        "reason": "release finished"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["updated"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["status"], "completed");
    assert_eq!(json["reason"], "release finished");
    assert_eq!(json["changed_by"], "driver-bot");

    let calls = recorder.status_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("driver-bot"));
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.status, "completed");
}
#[tokio::test]
async fn delete_group_route_delegates_to_group_management_and_preserves_response_shape() {
    let (app, recorder, _temp_dir) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/groups/group-1?bot_id=driver-bot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["deleted"], true);
    assert_eq!(json["id"], "group-1");

    let calls = recorder.delete_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.caller_actor_id, "driver-bot");
}

#[tokio::test]
async fn group_secondary_routes_delegate_to_group_management() {
    let (app, recorder, _temp_dir) = test_app().await;

    let label_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-1/label")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({ "label": "Renamed group" }).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(label_response.status(), StatusCode::OK);
    let body = to_bytes(label_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["updated"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["label"], "Renamed group");

    let routing_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-1/routing-policy")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "mode": "structured",
                        "default_bot_final_delivery": "inject_observers",
                        "sender_routes": {
                            "driver-bot": ["target-bot"]
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(routing_response.status(), StatusCode::OK);
    let body = to_bytes(routing_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["ok"], true);
    assert_eq!(json["routing_policy"]["mode"], "structured");

    let terminate_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/terminate")
                .header("authorization", "Bearer driver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(terminate_response.status(), StatusCode::OK);
    let body = to_bytes(terminate_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["terminated"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["terminated_by"], "driver-bot");

    let label_calls = recorder.label_calls.lock().await;
    assert_eq!(label_calls.len(), 1);
    assert_eq!(label_calls[0].caller_actor_id, "driver-bot");
    assert_eq!(label_calls[0].group_id, "group-1");
    assert_eq!(label_calls[0].label.as_deref(), Some("Renamed group"));
    drop(label_calls);

    let routing_calls = recorder.routing_policy_calls.lock().await;
    assert_eq!(routing_calls.len(), 1);
    assert_eq!(
        routing_calls[0].caller_actor_id.as_deref(),
        Some("driver-bot")
    );
    assert_eq!(routing_calls[0].group_id, "group-1");
    assert_eq!(routing_calls[0].mode, Some(RoutingMode::Structured));
    assert_eq!(
        routing_calls[0]
            .sender_routes
            .as_ref()
            .and_then(|routes| routes.get("driver-bot")),
        Some(&vec!["target-bot".to_string()])
    );
    drop(routing_calls);

    let terminate_calls = recorder.terminate_calls.lock().await;
    assert_eq!(terminate_calls.len(), 1);
    assert_eq!(terminate_calls[0].caller_actor_id, "driver-bot");
    assert_eq!(terminate_calls[0].group_id, "group-1");
}
async fn test_app_with_real_group_store() -> (
    axum::Router,
    Arc<bcs_group::GroupManagement>,
    Arc<GroupStore>,
    TempDir,
) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    let g = Group::new(
        "group-1",
        "driver-bot",
        vec![Participant {
            bot_uuid: "driver-bot".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
            tags: Vec::new(),
            message_view_scope: bcs_domain::MessageViewScope::Full,
        }],
    );
    group_store.upsert(g).await.unwrap();

    // A real `GroupManagement` use case owns the patch validation and persists
    // through the same `GroupStore` core, so the route-level test observes the
    // store write end-to-end. The empty session service reports zero running
    // service-invocation sessions, so route-field changes are allowed.
    let management = Arc::new(bcs_group::GroupManagement::with_defaults(
        group_store.clone(),
        registry.clone(),
        Arc::new(NoopFriendCoreService),
    ));
    let services = Services::builder()
        .registry(registry)
        .group(group_store.clone())
        .group_management(management.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));
    (app, management, group_store, temp_dir)
}
#[tokio::test]
async fn retired_group_settings_return_gone() {
    let (app, _, _, _temp_dir) = test_app_with_real_group_store().await;
    let response = app.oneshot(Request::builder().method("PATCH")
        .uri("/groups/group-1/settings").header("content-type", "application/json")
        .body(Body::from(r#"{"service_spec":{"max_concurrency":1}}"#)).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::GONE);
}
