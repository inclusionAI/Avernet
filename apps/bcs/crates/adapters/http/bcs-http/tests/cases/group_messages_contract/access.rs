use super::*;

#[tokio::test]
async fn collaboration_delivery_routes_require_authenticated_caller() {
    let (app, ..) = build_group_app_with_identity(Arc::new(NoUserIdentity)).await;
    for (method, path, body) in [
        ("GET", "/openapi/v1/collaboration/messages/m/deliveries?session_id=group-1:abcdef12", ""),
        ("POST", "/openapi/v1/collaboration/sessions/group-1:abcdef12/message-deliveries/query", r#"{"message_ids":["m"]}"#),
        ("POST", "/openapi/v1/collaboration/messages/m/deliveries/d/cancel", r#"{"session_id":"group-1:abcdef12"}"#),
        ("POST", "/openapi/v1/collaboration/messages/m/deliveries/cancel", r#"{"session_id":"group-1:abcdef12"}"#),
        ("POST", "/openapi/v1/collaboration/messages/m/deliveries/d/resolve", r#"{"session_id":"group-1:abcdef12","expected_state_version":3,"resolution":"confirmed_not_sent","reason":"verified"}"#),
    ] {
        let response = app.clone().oneshot(Request::builder().method(method).uri(path)
            .header("content-type", "application/json").body(Body::from(body)).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED, "{method} {path}");
    }
}
#[tokio::test]
async fn session_messages_without_identity_returns_unauthorized() {
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        _message_flow,
        group_message_history,
    ) = build_group_app_with_identity(Arc::new(NoUserIdentity)).await;

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

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(group_message_history.session_calls.lock().await.is_empty());
}
async fn scoped_session_history_app(
    strategy: GroupStrategy,
    scope: bcs_domain::MessageViewScope,
) -> (
    axum::Router,
    Arc<RecordingGroupMessageHistory>,
    Arc<RecordingStateMachineHistoryRuntime>,
    TempDir,
) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, owner) in [
        ("owner-bot", "123"),
        ("other-bot", "456"),
        ("outside-bot", "123"),
    ] {
        registry.register(bot_id.to_string(), BotCapabilities::default()).await.unwrap();
        registry.save_created_by(bot_id, owner, true).await.unwrap();
    }
    let mut human = Participant::human("human_123", ParticipantRole::Observer);
    human.message_view_scope = scope;
    let participants = vec![
        Participant::bot("owner-bot", ParticipantRole::Driver),
        Participant::bot("other-bot", ParticipantRole::Consultant),
        human,
        Participant::human("human_456", ParticipantRole::Observer),
    ];
    let mut group = Group::new("scoped-group", "owner-bot", participants.clone());
    group.group_strategy = strategy;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();
    let session = test_session("scoped-group:abcdef12", "scoped-group", participants);
    let history = Arc::new(RecordingGroupMessageHistory::default());
    let runtime = Arc::new(RecordingStateMachineHistoryRuntime {
        calls: Mutex::new(Vec::new()),
        human_views: Mutex::new(Vec::new()),
        result: SessionHistoryResult {
            session_id: session.id.clone(),
            messages: rich_session_messages(),
            limit: 20,
            before: None,
            next_before: None,
        },
    });
    let mut services = Services::noop();
    services.bot_query = Arc::new(bcs_bot::Bot::new(registry.clone()));
    services.registry = registry;
    services.group = group_store;
    services.session_management = Arc::new(StaticSessionManagement::new(session));
    services.group_message_history = history.clone();
    services.collaboration_runtime = runtime.clone();
    // LIVE authority chain mirroring the fixture
    // `created_by` rows (bot-authority plan Task 6.5):
    // the adapter probes consult it instead of
    // `list_bots_by_creator`.
    let authority = bcs_test_support::lazy_authority_chain().hook;
    authority.seed_owner("owner-bot", "123");
    authority.seed_owner("other-bot", "456");
    authority.seed_owner("outside-bot", "123");
    services.bot_authority_hook = authority;
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(static_auth_chain("123", "Owner")),
    )));
    (app, history, runtime, temp_dir)
}

#[tokio::test]
async fn session_messages_bot_view_is_independent_of_human_scope() {
    for strategy in [GroupStrategy::Chat, GroupStrategy::ManagerWorker, GroupStrategy::StateMachine] {
        for scope in [bcs_domain::MessageViewScope::Full, bcs_domain::MessageViewScope::Participant] {
            let (app, history, runtime, _temp_dir) = scoped_session_history_app(strategy, scope).await;
            let response = app.oneshot(
                Request::builder()
                    .uri("/sessions/scoped-group:abcdef12/messages?view_bot_id=owner-bot&limit=20")
                    .body(Body::empty()).unwrap(),
            ).await.unwrap();
            assert_eq!(response.status(), StatusCode::OK, "{strategy:?}, {scope:?}");
            let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
            assert_eq!(serde_json::from_slice::<Value>(&body).unwrap(),
                serde_json::to_value(rich_session_messages()).unwrap());
            assert!(runtime.human_views.lock().await.is_empty(), "Bot tab must not use Human projection");
            if strategy == GroupStrategy::StateMachine {
                assert!(history.session_calls.lock().await.is_empty());
                assert_eq!(runtime.calls.lock().await.len(), 1);
            } else {
                let calls = history.session_calls.lock().await;
                assert_eq!(calls.len(), 1);
                assert_eq!(calls[0].view_bot_id.as_deref(), Some("owner-bot"));
                assert!(runtime.calls.lock().await.is_empty(), "Bot tab must not merge Human snapshots");
            }
        }
    }
}

#[tokio::test]
async fn session_messages_explicit_view_requires_owned_session_bot() {
    for strategy in [GroupStrategy::Chat, GroupStrategy::ManagerWorker, GroupStrategy::StateMachine] {
        for scope in [bcs_domain::MessageViewScope::Full, bcs_domain::MessageViewScope::Participant] {
            let (app, history, runtime, _temp_dir) = scoped_session_history_app(strategy, scope).await;
            for actor in ["other-bot", "outside-bot", "missing-bot", "human_456"] {
                let response = app.clone().oneshot(
                    Request::builder()
                        .uri(format!("/sessions/scoped-group:abcdef12/messages?view_bot_id={actor}&limit=20"))
                        .body(Body::empty()).unwrap(),
                ).await.unwrap();
                assert_eq!(response.status(), StatusCode::FORBIDDEN, "{strategy:?}, {scope:?}, {actor}");
            }
            assert!(history.session_calls.lock().await.is_empty());
            assert!(runtime.calls.lock().await.is_empty());
        }
    }
}
#[tokio::test]
async fn state_machine_session_messages_non_session_human_returns_forbidden() {
    let mut group = Group::new(
        "sm-group-human-forbidden",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let session = test_session(
        "sm-group-human-forbidden:abcdef12",
        "sm-group-human-forbidden",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
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
    // LIVE authority chain with NO seeded edges: the
    // intruding Human controls nothing.
    services.bot_authority_hook = bcs_test_support::lazy_authority_chain().hook;
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(static_auth_chain("456", "Intruder")),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/sm-group-human-forbidden:abcdef12/messages?limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(collaboration_runtime.calls.lock().await.is_empty());
}

#[tokio::test]
async fn state_machine_session_messages_group_only_bot_returns_forbidden() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                name: Some("Driver".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register(
            "intruder-bot".to_string(),
            BotCapabilities {
                name: Some("Intruder".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("intruder-token".to_string(), "intruder-bot".to_string())
        .await;

    let mut group = Group::new(
        "sm-group-bot-forbidden",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("intruder-bot", ParticipantRole::Observer),
        ],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let session = test_session(
        "sm-group-bot-forbidden:abcdef12",
        "sm-group-bot-forbidden",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
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
    services.registry = registry;
    services.group = group_store;
    services.session_management = Arc::new(StaticSessionManagement::new(session));
    services.collaboration_runtime = collaboration_runtime.clone();
    let app =
        build_router(HttpAppState::new(services).with_user_identity(Arc::new(NoUserIdentity)));

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/sm-group-bot-forbidden:abcdef12/messages?limit=20")
                .header("authorization", "Bearer intruder-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(collaboration_runtime.calls.lock().await.is_empty());
}

#[tokio::test]
async fn state_machine_session_messages_without_identity_returns_unauthorized() {
    let mut group = Group::new(
        "sm-group-no-auth",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let session = test_session(
        "sm-group-no-auth:abcdef12",
        "sm-group-no-auth",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
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
    let app =
        build_router(HttpAppState::new(services).with_user_identity(Arc::new(NoUserIdentity)));

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/sm-group-no-auth:abcdef12/messages?limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(collaboration_runtime.calls.lock().await.is_empty());
}
