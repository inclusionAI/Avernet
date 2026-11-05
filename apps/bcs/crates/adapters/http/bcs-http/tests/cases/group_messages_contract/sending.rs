use super::*;

#[tokio::test]
async fn group_chat_missing_group_preserves_legacy_404_before_auth() {
    let (app, ..) = build_group_app_with_identity(Arc::new(ChainUserIdentityPort::new(Arc::new(
        AuthPluginChain::new(vec![]),
    ))))
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/missing-group/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "hello",
                        "from": "owner-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["error"], "Group not found: missing-group");
    assert_eq!(json["status"], 404);
}
#[tokio::test]
async fn session_chat_auto_joins_authenticated_human_and_binds_sender_identity() {
    let chain = static_auth_chain("456", "Alice");
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app_with_identity(Arc::new(ChainUserIdentityPort::new(chain))).await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:abcdef12/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "hello as Alice",
                        "from": "owner-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let group_chats = message_flow.group_chats.lock().await;
    assert_eq!(group_chats.len(), 1);
    assert_eq!(
        group_chats[0].requested_sender_id.as_deref(),
        Some("human_456")
    );
    assert!(matches!(
        &group_chats[0].caller,
        CallerContext::Human(human)
            if human.actor_id == "human_456" && human.staff_no == "456"
    ));
    drop(group_chats);

    let system_notifications = message_flow.system_notifications.lock().await;
    assert_eq!(system_notifications.len(), 1);
    assert!(matches!(
        &system_notifications[0],
        SystemMessageEvent::HumanJoined { group_id, actor }
            if group_id == "group-1"
                && actor.bot_uuid == "human_456"
                && actor.actor_kind == bcs_service_api::ActorKind::Human
    ));
    drop(system_notifications);

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let participant = json["participants"]
        .as_array()
        .unwrap()
        .iter()
        .find(|participant| participant["bot_uuid"] == "human_456")
        .expect("authenticated Human should be added to the session");
    assert_eq!(participant["bot_name"], "Alice");
    assert_eq!(participant["role"], "observer");
    assert_eq!(participant["actor_kind"], "human");
    assert_eq!(participant["mode"], "present");
}

#[tokio::test]
async fn session_chat_allows_completed_chat_session() {
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app_with_identity_and_session_status(
        Arc::new(ChainUserIdentityPort::new(static_auth_chain(
            "123", "Owner",
        ))),
        SessionStatus::Completed,
        None,
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:abcdef12/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({"message": "continue completed chat"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["session_id"], "group-1:abcdef12");
    assert_eq!(json["delivered_count"], 2);

    let group_chats = message_flow.group_chats.lock().await;
    assert_eq!(group_chats.len(), 1);
    assert_eq!(
        group_chats[0].session_id.as_deref(),
        Some("group-1:abcdef12")
    );
}

#[tokio::test]
async fn session_chat_does_not_auto_join_authenticated_bot() {
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app().await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:abcdef12/chat")
                .header("content-type", "application/json")
                .header("authorization", "Bearer intruder-token")
                .body(Body::from(
                    serde_json::json!({
                        "message": "should be rejected",
                        "from": "intruder-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["error"], "caller is not a session participant");
    assert!(message_flow.group_chats.lock().await.is_empty());

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert!(
        json["participants"]
            .as_array()
            .unwrap()
            .iter()
            .all(|participant| participant["bot_uuid"] != "intruder-bot")
    );
}
async fn post_session_chat_with_flow_error(error: ServiceError) -> (StatusCode, Value) {
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app().await;
    *message_flow.next_group_chat_error.lock().await = Some(error);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:abcdef12/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "hello from session"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    (status, json)
}

#[tokio::test]
async fn session_chat_maps_invalid_operation_to_bad_request() {
    let (status, json) = post_session_chat_with_flow_error(ServiceError::InvalidOperation {
        message: "group 'group-1' is not active".to_string(),
        request_id: None,
    })
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        json["error"],
        "Invalid operation on request unknown: group 'group-1' is not active"
    );
}

#[tokio::test]
async fn session_chat_maps_session_not_found_to_not_found() {
    let (status, json) = post_session_chat_with_flow_error(ServiceError::SessionNotFound(
        "group-1:missing".to_string(),
    ))
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(json["error"], "Session 'group-1:missing' not found");
}

#[tokio::test]
async fn session_chat_keeps_internal_error_as_server_error() {
    let (status, json) = post_session_chat_with_flow_error(ServiceError::InternalError(
        "database unavailable".to_string(),
    ))
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(json["error"], "Internal error: database unavailable");
}
#[tokio::test]
async fn persistent_send_missing_group_preserves_legacy_404_before_auth() {
    let (app, ..) = build_group_app_with_identity(Arc::new(NoUserIdentity)).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/missing-group/messages")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "sender": "owner-bot",
                        "content": "hello"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["error"], "Group not found: missing-group");
    assert_eq!(json["status"], 404);
}

#[tokio::test]
async fn group_message_routes_preserve_delivery_and_history_shapes() {
    let (
        app,
        group_store,
        routing,
        bot_delivery,
        frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app().await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/messages")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "sender": "owner-bot",
                        "content": "@Target hello",
                        "role": "user"
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
    assert_eq!(json["message_id"], "flow-message-1");
    assert_eq!(json["routed_to"], serde_json::json!(["target-bot"]));
    assert_eq!(json["mentions"], serde_json::json!(["target-bot"]));

    let persistent_sends = message_flow.persistent_sends.lock().await;
    assert_eq!(persistent_sends.len(), 1);
    assert_eq!(persistent_sends[0].group_id, "group-1");
    assert_eq!(persistent_sends[0].sender, "owner-bot");
    assert_eq!(persistent_sends[0].content, "@Target hello");
    assert!(persistent_sends[0].store_messages);
    assert_eq!(persistent_sends[0].max_group_messages, 10);
    drop(persistent_sends);
    assert!(routing.sent_to_bot.lock().await.is_empty());
    let group = group_store.get("group-1").await.unwrap();
    assert_eq!(group.messages.len(), 1);

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "@Target direct hello",
                        "from": "owner-bot"
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
    assert_eq!(json["delivered"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["driver_bot"], "owner-bot");
    assert_eq!(json["delivered_count"], 2);
    assert_eq!(json["failed_count"], 0);
    assert_eq!(json["mentions"], serde_json::json!(["target-bot"]));
    assert_eq!(json["delivery_results"][1]["bot_uuid"], "target-bot");

    let group_chats = message_flow.group_chats.lock().await;
    assert_eq!(group_chats.len(), 1);
    assert_eq!(group_chats[0].group_id, "group-1");
    assert_eq!(
        group_chats[0].requested_sender_id.as_deref(),
        Some("owner-bot")
    );
    assert_eq!(group_chats[0].message, "@Target direct hello");
    drop(group_chats);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/callback")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "system callback",
                        "mentions": ["target-bot"],
                        "metadata": {"source": "test"}
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
    assert_eq!(json["delivered"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["driver_bot"], "owner-bot");
    assert_eq!(json["delivered_count"], 1);
    assert_eq!(json["failed_count"], 0);
    assert_eq!(json["mentions"], serde_json::json!(["target-bot"]));

    let frames = bot_delivery.frames.lock().await;
    assert!(frames.is_empty());
    let frontend_events = frontend_delivery.events.lock().await;
    assert!(frontend_events.is_empty());
    let callbacks = message_flow.callbacks.lock().await;
    assert_eq!(callbacks.len(), 1);
    assert_eq!(callbacks[0].group_id, "group-1");
    assert_eq!(callbacks[0].message, "system callback");
    assert_eq!(callbacks[0].mentions, vec!["target-bot".to_string()]);
    assert_eq!(
        callbacks[0].metadata,
        Some(serde_json::json!({"source": "test"}))
    );
}

#[tokio::test]
async fn inactive_group_chat_is_delegated_to_message_flow() {
    let (
        app,
        group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app().await;
    let mut group = group_store.get("group-1").await.unwrap();
    group.status = GroupStatus::Inactive;
    group_store.upsert(group).await.unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "wake up",
                        "from": "owner-bot"
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
    assert_eq!(json["delivered"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["driver_bot"], "owner-bot");

    let group_chats = message_flow.group_chats.lock().await;
    assert_eq!(group_chats.len(), 1);
    assert_eq!(group_chats[0].group_id, "group-1");
    assert_eq!(
        group_chats[0].requested_sender_id.as_deref(),
        Some("owner-bot")
    );
    assert_eq!(group_chats[0].message, "wake up");
}

#[tokio::test]
async fn persistent_send_delegates_inactive_and_cap_paths_to_message_flow_with_legacy_errors() {
    let (
        app,
        group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app().await;

    let mut group = group_store.get("group-1").await.unwrap();
    group.status = GroupStatus::Inactive;
    group_store.upsert(group).await.unwrap();
    *message_flow.next_persistent_error.lock().await = Some(ServiceError::InvalidOperation {
        message: "Group 'group-1' is not active (status: Inactive)".to_string(),
        request_id: None,
    });

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/messages")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "sender": "owner-bot",
                        "content": "inactive send"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        json["error"],
        "Group 'group-1' is not active (status: Inactive)"
    );
    assert_eq!(json["status"], 400);

    let mut group = group_store.get("group-1").await.unwrap();
    group.status = GroupStatus::Active;
    group_store.upsert(group).await.unwrap();
    for _ in 0..10 {
        group_store
            .increment_message_count("group-1")
            .await
            .unwrap();
    }
    *message_flow.next_persistent_error.lock().await = Some(ServiceError::MessageLimitReached(
        "Group 'group-1' already has 10 messages (max 10)".to_string(),
    ));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/messages")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "sender": "owner-bot",
                        "content": "cap send"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        json["error"],
        "Group 'group-1' already has 10 messages (max 10)"
    );
    assert_eq!(json["status"], 400);

    let persistent_sends = message_flow.persistent_sends.lock().await;
    assert_eq!(persistent_sends.len(), 2);
    assert_eq!(persistent_sends[0].content, "inactive send");
    assert_eq!(persistent_sends[1].content, "cap send");
}
