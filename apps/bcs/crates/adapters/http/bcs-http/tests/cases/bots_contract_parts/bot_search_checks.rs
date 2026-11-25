use super::*;

#[tokio::test]
async fn search_bots_route_rejects_oversized_limit() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult { items: vec![], total: 0 }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?limit=101")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(query.search_commands.lock().await.is_empty());
}

#[tokio::test]
async fn search_bots_route_rejects_invalid_visibility() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult { items: vec![], total: 0 }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?visibility=internet")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(query.search_commands.lock().await.is_empty());
}

#[tokio::test]
async fn search_bots_route_defaults_to_public_and_protected_without_bearer() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult {
            items: vec![query_entry("bot-alpha"), query_entry("bot-beta")],
            total: 2,
        }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    // No user-identity port ⇒ anonymous caller.
    let app = build_router(HttpAppState::new(services));

    // Without an explicit visibility filter, search defaults to public + protected.
    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?offset=0&limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 2);
    assert_eq!(json["offset"], 0);
    assert_eq!(json["limit"], 20);
    assert_eq!(json["items"].as_array().unwrap().len(), 2);

    let commands = query.search_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0].visibility.as_ref().unwrap(),
        &vec!["public".to_string(), "protected".to_string()]
    );
    assert_eq!(
        commands[0].user_visibility.as_ref().unwrap(),
        &vec!["public".to_string(), "protected".to_string()]
    );
}

#[tokio::test]
async fn search_bots_route_infers_human_viewer_from_caller() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult {
            items: vec![query_entry("bot-alpha")],
            total: 1,
        }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    // A human caller without explicit viewer params: the viewer is inferred from
    // the caller (`human_alice`) so the returned `is_friend` marker is computed
    // against the human's friend edges; discoverability is judged by
    // `user_visibility` (default public + protected) and `visibility` is
    // neutralized (all three values ⇒ unfiltered). The friendship FILTER still
    // requires explicit viewer params (see ..._rejects_friendship_filter_without_explicit_viewer).
    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?offset=0&limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let items = json["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    // The recording stub does not compute is_friend; real computation against
    // the inferred viewer's friend edges is exercised by integration tests.
    assert!(items[0].get("is_friend").is_none());

    let commands = query.search_commands.lock().await;
    assert_eq!(commands.len(), 1);
    // `visibility` is neutralized for a human viewer (all three ⇒ unfiltered).
    assert_eq!(
        commands[0].visibility.as_ref().unwrap(),
        &vec![
            "public".to_string(),
            "protected".to_string(),
            "private".to_string()
        ]
    );
    // ...and discoverability is gated by `user_visibility` (default public + protected).
    assert_eq!(
        commands[0].user_visibility.as_ref().unwrap(),
        &vec!["public".to_string(), "protected".to_string()]
    );
    // The human caller is inferred as the viewer so `is_friend` is computed.
    assert_eq!(commands[0].viewer_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].requester_actor_id.as_deref(), Some("human_alice"));
}

#[tokio::test]
async fn search_bots_route_uses_explicit_viewer_for_is_friend_filter() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult {
            items: vec![
                query_entry_with_friend("bot-alpha", Some(true)),
                query_entry_with_friend("bot-beta", Some(false)),
            ],
            total: 2,
        }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?viewer_actor_type=bot&viewer_actor_id=viewer-bot&friendship=friends")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 2);
    let items = json["items"].as_array().unwrap();
    assert_eq!(items.len(), 2);
    assert_eq!(items[0]["bot_uuid"], "bot-alpha");
    assert_eq!(items[0]["is_friend"], true);
    assert_eq!(items[1]["bot_uuid"], "bot-beta");
    assert_eq!(items[1]["is_friend"], false);

    let commands = query.search_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].viewer_actor_id.as_deref(), Some("viewer-bot"));
    assert_eq!(commands[0].friendship, Some(bcs_service_api::BotSearchFriendshipFilter::Friends));
    // bot viewer → discoverability judged by `visibility` (public + protected default);
    // `user_visibility` stays None unless explicitly requested.
    assert_eq!(
        commands[0].visibility.as_ref().unwrap(),
        &vec!["public".to_string(), "protected".to_string()]
    );
    assert!(commands[0].user_visibility.is_none());
}

#[tokio::test]
async fn search_bots_route_normalizes_explicit_human_viewer_id() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult { items: vec![], total: 0 }),
        ..Default::default()
    });
    let build_app = || {
        let services = Services::builder()
            .bot_query(query.clone())
            .build_for_test();
        build_router(HttpAppState::new(services))
    };

    // Bare human staff id → canonical `human_<id>`, matching edge_grants.from_id and the
    // legacy bcs_friendships keys. This is the reported `/bots/search` is_friend=false bug:
    // the lookup key must be `human_152819`, not the bare `152819` supplied by the caller.
    let response = build_app()
        .oneshot(
            Request::builder()
                .uri("/bots/search?viewer_actor_type=human&viewer_actor_id=152819")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);

    // Idempotency: an already-canonical human id passes through unchanged.
    let response = build_app()
        .oneshot(
            Request::builder()
                .uri("/bots/search?viewer_actor_type=human&viewer_actor_id=human_alice")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);

    let commands = query.search_commands.lock().await;
    assert_eq!(commands.len(), 2);
    assert_eq!(
        commands[0].viewer_actor_id.as_deref(),
        Some("human_152819")
    );
    assert_eq!(
        commands[1].viewer_actor_id.as_deref(),
        Some("human_alice")
    );
}

#[tokio::test]
async fn search_bots_route_rejects_friendship_filter_without_explicit_viewer() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult { items: vec![], total: 0 }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?friendship=friends")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(query.search_commands.lock().await.is_empty());
}

#[tokio::test]
async fn search_bots_route_forwards_tc_bot_filter_param() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult { items: vec![], total: 0 }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?tc_bot=true")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let commands = query.search_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].tc_bot, Some(true));
}

#[tokio::test]
async fn search_bots_route_forwards_exact_bot_uuid_filter() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult { items: vec![], total: 0 }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?bot_uuids=bot-alpha%3Aowner-1,bot-beta%3Aowner-2")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let commands = query.search_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0].bot_uuids.as_ref().unwrap(),
        &vec![
            "bot-alpha:owner-1".to_string(),
            "bot-beta:owner-2".to_string(),
        ]
    );
}

#[tokio::test]
async fn search_bots_route_rejects_empty_exact_bot_uuid_filter_entry() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult { items: vec![], total: 0 }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?bot_uuids=bot-alpha,")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(query.search_commands.lock().await.is_empty());
}

#[tokio::test]
async fn search_bots_route_rejects_more_than_one_hundred_exact_bot_uuids() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult { items: vec![], total: 0 }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));
    let bot_uuids = (0..101)
        .map(|index| format!("bot-{index}"))
        .collect::<Vec<_>>()
        .join(",");

    let response = app
        .oneshot(
            Request::builder()
                .uri(format!("/bots/search?bot_uuids={bot_uuids}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(query.search_commands.lock().await.is_empty());
}

#[tokio::test]
async fn search_bots_route_accepts_multi_visibility_and_returns_friend_policy_fields() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult {
            items: vec![query_entry("bot-alpha")],
            total: 1,
        }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?visibility=public,protected")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let items = json["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["user_visibility"], "protected");
    assert_eq!(items[0]["friend_ext"], serde_json::json!({}));
    assert_eq!(items[0]["friend_check_in_strategy"], "APPROVAL");

    let commands = query.search_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0].visibility.as_ref().unwrap(),
        &vec!["public".to_string(), "protected".to_string()]
    );
}


#[tokio::test]
async fn search_bots_route_forwards_user_visibility_and_returns_policy_fields() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult {
            items: vec![query_entry_with_policy(
                "bot-alpha",
                UserVisibility::Public,
                serde_json::json!({"view_scope_user_friend_deps": ["dep-1"]})
                    .as_object()
                    .unwrap()
                    .clone(),
                FriendCheckInStrategy::Open,
            )],
            total: 1,
        }),
        ..Default::default()
    });
    let mut attributes = HashMap::new();
    attributes.insert(
        "bot-alpha".to_string(),
        BotInternalAttributes {
            visibility: "public".to_string(),
            user_visibility: UserVisibility::Public,
            friend_ext: serde_json::json!({"view_scope_user_friend_deps": ["dep-1"]})
                .as_object()
                .unwrap()
                .clone(),
            friend_check_in_strategy: FriendCheckInStrategy::Open,
        },
    );
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services).with_internal_bot_attributes_service(
        Arc::new(RecordingSearchPolicyService { attributes }),
    ));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?user_visibility=public")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 1);
    let items = json["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["bot_uuid"], "bot-alpha");
    assert_eq!(items[0]["user_visibility"], "public");
    assert_eq!(items[0]["friend_ext"]["view_scope_user_friend_deps"][0], "dep-1");
    assert_eq!(items[0]["friend_check_in_strategy"], "OPEN");

    let commands = query.search_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].user_visibility.as_ref().unwrap(), &vec!["public".to_string()]);
}

#[tokio::test]
async fn search_bots_route_filters_default_user_visibility() {
    let query = Arc::new(RecordingBotQueryService {
        search_result: Ok(BotSearchResult {
            items: vec![query_entry("bot-alpha")],
            total: 1,
        }),
        ..Default::default()
    });
    let services = Services::builder().bot_query(query.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/search?user_visibility=private")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 1);
    assert_eq!(json["items"].as_array().unwrap().len(), 1);

    let commands = query.search_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].user_visibility.as_ref().unwrap(), &vec!["private".to_string()]);
}
