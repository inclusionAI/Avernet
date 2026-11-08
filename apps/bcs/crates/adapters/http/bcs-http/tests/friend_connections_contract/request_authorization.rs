use super::*;

#[tokio::test]
async fn friend_connection_request_requires_bearer_even_with_from_actor() {
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        Arc::new(RecordingConnectService::default()),
        Arc::new(RecordingBotQueryService::default()),
        None,
        None,
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_actor": "bot-owned",
                        "actor_kind": "bot",
                        "to_bot": "peer-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}
#[tokio::test]
async fn friend_connection_request_forwards_whitelisted_auth_headers_to_connect_service() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        Some(Arc::new(StaticUserIdentityPort::human("10001"))),
        None,
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("content-type", "application/json")
                .header("authorization", "Bearer caller-token")
                .header("cookie", "SSO=caller-cookie")
                .header("x-avernet-principal", "signed-principal")
                .header("x-request-id", "req-1")
                .header("x-ignore-me", "nope")
                .body(Body::from(
                    serde_json::json!({
                        "to_bot": "peer-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let request_auths = connect.create_request_auths.lock().await;
    assert_eq!(request_auths.len(), 1);
    let request_auth = request_auths[0].as_ref().expect("request auth forwarded");
    assert_eq!(request_auth.authorization.as_deref(), Some("Bearer caller-token"));
    assert_eq!(request_auth.cookie.as_deref(), Some("SSO=caller-cookie"));
    assert_eq!(
        request_auth.forwarded_headers,
        vec![
            ("authorization".to_string(), "Bearer caller-token".to_string()),
            ("cookie".to_string(), "SSO=caller-cookie".to_string()),
            ("x-avernet-principal".to_string(), "signed-principal".to_string()),
            ("x-request-id".to_string(), "req-1".to_string()),
        ]
    );
}
#[tokio::test]
async fn friend_connection_request_allows_human_to_act_as_owned_bot() {
    // LIVE authority: the act-as judgment follows the
    // current owner edge; no `created_by` mirror.
    let connect = Arc::new(RecordingConnectService::default());
    let authority = bcs_test_support::lazy_authority_chain().hook;
    authority.seed_owner("bot-owned", "10001");
    let bot_query = Arc::new(RecordingBotQueryService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app_with_authority(
        &temp_dir,
        connect.clone(),
        bot_query.clone(),
        Some(Arc::new(StaticUserIdentityPort::human("10001"))),
        None,
        Some(authority),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer human-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_actor": "bot-owned",
                        "actor_kind": "bot",
                        "to_bot": "peer-bot",
                        "message": "hello"
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
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["status"], "pending");
    assert_eq!(json["data"]["request_ids"], serde_json::json!(["1"]));

    let calls = connect.create_commands.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("bot-owned".to_string(), "peer-bot".to_string(), Some("hello".to_string()))]
    );
    // The legacy creator listing is no longer consulted.
    let owner_calls = bot_query.list_bots_by_creator_calls.lock().await;
    assert!(owner_calls.is_empty());
}
#[tokio::test]
async fn friend_connection_request_rejects_unowned_bot_impersonation() {
    // LIVE authority: no edge for the impersonating
    // Bot → clean 403 (the unprovisioned reader
    // never consults authority, so an unwired-suite
    // chain cannot turn this into a 500).
    let connect = Arc::new(RecordingConnectService::default());
    let authority = bcs_test_support::lazy_authority_chain().hook;
    authority.seed_manager("decoy-bot", "10001");
    authority.revoke_manager("decoy-bot", "10001");
    let bot_query = Arc::new(RecordingBotQueryService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app_with_authority(
        &temp_dir,
        connect.clone(),
        bot_query.clone(),
        Some(Arc::new(StaticUserIdentityPort::human("10001"))),
        None,
        Some(authority),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer human-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_actor": "bot-owned",
                        "actor_kind": "bot",
                        "to_bot": "peer-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(connect.create_commands.lock().await.is_empty());
}
#[tokio::test]
async fn friend_connection_request_rejects_bot_behalf_of_other_bot() {
    let connect = Arc::new(RecordingConnectService::default());
    let bot_query = Arc::new(RecordingBotQueryService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        bot_query,
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_actor": "other-bot",
                        "actor_kind": "bot",
                        "to_bot": "peer-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(connect.create_commands.lock().await.is_empty());
}
#[tokio::test]
async fn friend_connection_list_by_actor_uses_requested_actor() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/collaboration/friend-connections?actor=target-bot&actor_kind=bot")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["total"], 1);
    assert_eq!(json["data"]["items"][0]["actor_id"], "friend-bot");

    let calls = connect.list_friends_commands.lock().await;
    assert_eq!(calls.as_slice(), &["target-bot".to_string()]);
}
#[tokio::test]
async fn friend_connection_list_by_actor_translates_human_actor_kind() {
    let connect = Arc::new(RecordingConnectService::default());
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/collaboration/friend-connections?actor=10001&actor_kind=human")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = connect.list_friends_commands.lock().await;
    assert_eq!(calls.as_slice(), &["human_10001".to_string()]);
}
#[tokio::test]
async fn friend_connection_request_maps_connect_status_variants_and_accepted_alias() {
    let connect_approved = Arc::new(RecordingConnectService {
        create_result: ConnectResult {
            request_ids: vec!["101".to_string()],
            edge_ids: vec![111],
            status: ConnectStatus::Approved,
            auto_accepted: true,
        },
        ..RecordingConnectService::default()
    });
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect_approved.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "to_bot": "peer-bot"
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
    assert_eq!(json["data"]["status"], "approved");

    let connect_public = Arc::new(RecordingConnectService {
        create_result: ConnectResult {
            request_ids: vec!["102".to_string()],
            edge_ids: vec![112],
            status: ConnectStatus::PublicNoEdge,
            auto_accepted: true,
        },
        ..RecordingConnectService::default()
    });
    let app = build_app(
        &temp_dir,
        connect_public.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "to_bot": "peer-bot"
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
    assert_eq!(json["data"]["status"], "public_no_edge");

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/collaboration/friend-connections/requests?direction=all&status=accepted&page=2&page_size=10")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let calls = connect_public.list_requests_commands.lock().await;
    assert_eq!(calls.len(), 1);
    let (_, direction, status, page, page_size) = &calls[0];
    assert!(matches!(direction, RequestDirection::All));
    assert!(matches!(status, Some(RequestStatus::Approved)));
    assert_eq!((*page, *page_size), (2, 10));
}
