use super::*;

/// The batch query route is POST-only: a GET against the same
/// path must never reach the handler (the single-message lookup
/// lives on a different path) — axum answers 405 on the method
/// mismatch, pinning the GET/POST contract surface.
#[tokio::test]
async fn collaboration_delivery_query_batch_route_is_post_only() {
    let (app, ..) = build_group_app().await;
    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/openapi/v1/collaboration/sessions/group-1:abcdef12/message-deliveries/query")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::METHOD_NOT_ALLOWED);
}

/// An authenticated POST reaches the flow and returns its
/// success body (the recording flow answers an empty page).
#[tokio::test]
async fn collaboration_delivery_query_post_succeeds_for_authenticated_caller() {
    let (app, ..) = build_group_app().await;
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/openapi/v1/collaboration/sessions/group-1:abcdef12/message-deliveries/query")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"message_ids":["m"]}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
}
#[tokio::test]
async fn session_messages_authenticated_full_human_preserves_legacy_default_view() {
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        _message_flow,
        group_message_history,
    ) = build_group_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12/messages")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let session_calls = group_message_history.session_calls.lock().await;
    assert_eq!(session_calls.len(), 1);
    assert_eq!(session_calls[0].view_bot_id, None);
    assert!(matches!(
        &session_calls[0].caller,
        CallerContext::Human(human)
            if human.actor_id == "human_123" && human.staff_no == "123"
    ));
}

#[tokio::test]
async fn session_messages_full_human_owning_session_bot_preserves_legacy_access() {
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        _message_flow,
        group_message_history,
    ) = build_group_app_with_identity_and_session_status(
        Arc::new(ChainUserIdentityPort::new(static_auth_chain("123", "Owner"))),
        SessionStatus::Running,
        Some(vec![
            Participant::bot("owner-bot", ParticipantRole::Driver),
            Participant::bot("target-bot", ParticipantRole::Consultant),
        ]),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12/messages")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let session_calls = group_message_history.session_calls.lock().await;
    assert_eq!(session_calls.len(), 1);
    assert_eq!(session_calls[0].view_bot_id, None);
}

#[tokio::test]
async fn session_messages_uses_history_service() {
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        _message_flow,
        group_message_history,
    ) = build_group_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12/messages?view_bot_id=owner-bot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        json,
        serde_json::to_value(rich_session_messages()).expect("serialize expected history")
    );
    assert!(json.is_array());
    assert_eq!(json[0]["historyMeta"]["assistantAggregation"], true);
    assert_eq!(json[0]["attachments"][0]["type"], "image");
    assert!(json.get("code").is_none());
    assert!(json.get("data").is_none());

    // Verify the history service was called with the correct session ID
    let session_calls = group_message_history.session_calls.lock().await;
    assert_eq!(session_calls.len(), 1);
    assert_eq!(session_calls[0].session_id, "group-1:abcdef12");
    assert_eq!(session_calls[0].group_id, "group-1");
    assert_eq!(session_calls[0].view_bot_id, Some("owner-bot".to_string()));
    assert!(matches!(
        &session_calls[0].caller,
        CallerContext::Human(human)
            if human.actor_id == "human_123" && human.staff_no == "123"
    ));
}
#[tokio::test]
async fn state_machine_session_messages_use_runtime_history() {
    let mut group = Group::new(
        "sm-group",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let session = test_session(
        "sm-group:abcdef12",
        "sm-group",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::human("human_123", ParticipantRole::Observer),
        ],
    );
    let group_message_history = Arc::new(RecordingGroupMessageHistory::default());
    let collaboration_runtime = Arc::new(RecordingStateMachineHistoryRuntime {
        calls: Mutex::new(Vec::new()),
        human_views: Mutex::new(Vec::new()),
        result: SessionHistoryResult {
            session_id: session.id.clone(),
            messages: vec![GroupMessage {
                id: "sm-msg-1".to_string(),
                timestamp: 42,
                sender: "bcs_state_machine".to_string(),
                content: "<AixPanel />".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: Some("BCS State Machine".to_string()),
                role: MessageRole::Assistant,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
                attachments: None,
            }],
            limit: 20,
            before: None,
            next_before: None,
        },
    });
    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = Arc::new(StaticSessionManagement::new(session));
    services.group_message_history = group_message_history.clone();
    services.collaboration_runtime = collaboration_runtime.clone();
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(static_auth_chain("123", "Owner")),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/sm-group:abcdef12/messages?limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json[0]["id"], "sm-msg-1");
    assert_eq!(json[0]["sender"], "bcs_state_machine");
    assert_eq!(json[0]["content"], "<AixPanel />");
    assert_eq!(json[0]["role"], "assistant");
    assert_eq!(json[0]["message_type"], "bot");
    assert!(
        group_message_history.session_calls.lock().await.is_empty(),
        "state-machine sessions must not fetch history from a single driver bot"
    );
    let calls = collaboration_runtime.calls.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("sm-group:abcdef12".to_string(), 20, None)]
    );
    drop(calls);
    let views = collaboration_runtime.human_views.lock().await;
    assert!(views.is_empty());
}

#[tokio::test]
async fn participant_session_history_merges_own_legacy_one_shot_response_snapshot() {
    let mut group = Group::new(
        "mw-group",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let mut human = Participant::human("human_123", ParticipantRole::Observer);
    human.message_view_scope = bcs_domain::MessageViewScope::Participant;
    let session = test_session(
        "mw-group:abcdef12",
        "mw-group",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            human,
        ],
    );
    let group_message_history = Arc::new(RecordingGroupMessageHistory::default());
    let collaboration_runtime = Arc::new(RecordingStateMachineHistoryRuntime {
        calls: Mutex::new(Vec::new()),
        human_views: Mutex::new(Vec::new()),
        result: SessionHistoryResult {
            session_id: session.id.clone(),
            messages: vec![GroupMessage {
                id: "run-1:review:0:1-output".to_string(),
                timestamp: 84,
                sender: "human_123".to_string(),
                content: "通过".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: None,
                role: MessageRole::User,
                history_meta: None,
                metadata: Some(serde_json::json!({
                    "state_machine": {"node_id": "review", "event": "output"}
                })),
                run_id: "run-1".to_string(),
                attachments: None,
            }],
            limit: 20,
            before: None,
            next_before: None,
        },
    });
    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = Arc::new(StaticSessionManagement::new(session));
    services.group_message_history = group_message_history.clone();
    services.collaboration_runtime = collaboration_runtime.clone();
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(static_auth_chain("123", "Owner")),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/mw-group:abcdef12/messages?limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json.as_array().map(Vec::len), Some(2));
    assert_eq!(json[0]["id"], "run-1:review:0:1-output");
    assert_eq!(json[0]["sender"], "human_123");
    assert_eq!(json[0]["content"], "通过");
    assert_eq!(json[0]["role"], "user");
    assert_eq!(json[1]["id"], "hist-1");
    assert_eq!(group_message_history.session_calls.lock().await.len(), 1);
    assert_eq!(collaboration_runtime.calls.lock().await.len(), 1);
    let views = collaboration_runtime.human_views.lock().await;
    assert_eq!(views.len(), 1);
    assert_eq!(views[0].actor_id, "human_123");
    assert_eq!(views[0].scope, bcs_domain::MessageViewScope::Participant);
}
#[tokio::test]
async fn legacy_state_machine_session_allows_participant_human_history() {
    let mut group = Group::new(
        "sm-group-legacy",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let mut human = Participant::human("human_123", ParticipantRole::Observer);
    human.message_view_scope = bcs_domain::MessageViewScope::Participant;
    let mut session = test_session(
        "sm-group-legacy:abcdef12",
        "sm-group-legacy",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            human,
        ],
    );
    session.message_visibility_version = 0;
    let collaboration_runtime = Arc::new(RecordingStateMachineHistoryRuntime {
        calls: Mutex::new(Vec::new()),
        human_views: Mutex::new(Vec::new()),
        result: SessionHistoryResult {
            session_id: session.id.clone(),
            messages: vec![],
            limit: 20,
            before: None,
            next_before: None,
        },
    });
    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = Arc::new(StaticSessionManagement::new(session));
    services.collaboration_runtime = collaboration_runtime.clone();
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(static_auth_chain("123", "Owner")),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/sm-group-legacy:abcdef12/messages?limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json, serde_json::json!([]));
    assert_eq!(collaboration_runtime.calls.lock().await.len(), 1);
    let views = collaboration_runtime.human_views.lock().await;
    assert_eq!(views.len(), 1);
    assert_eq!(views[0].actor_id, "human_123");
    assert_eq!(views[0].scope, bcs_domain::MessageViewScope::Participant);
}
