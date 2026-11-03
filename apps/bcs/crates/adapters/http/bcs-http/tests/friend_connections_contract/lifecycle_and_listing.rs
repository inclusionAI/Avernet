use super::*;

#[tokio::test]
async fn friend_connection_request_accepts_via_recorded_service() {
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
                .method("POST")
                .uri("/collaboration/friend-connections/requests/77/accept")
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
    assert_eq!(json["data"]["edge_ids"], serde_json::json!([211]));

    let calls = connect.approve_commands.lock().await;
    assert_eq!(calls.as_slice(), &[("77".to_string(), "caller-bot".to_string())]);
}
#[tokio::test]
async fn friend_connection_request_rejects_with_reason() {
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
                .method("POST")
                .uri("/collaboration/friend-connections/requests/88/reject")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({ "reason": "nope" }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["status"], "rejected");

    let calls = connect.reject_commands.lock().await;
    assert_eq!(
        calls.as_slice(),
        &[("88".to_string(), "caller-bot".to_string(), Some("nope".to_string()))]
    );
}
#[tokio::test]
async fn friend_connection_request_cancel_and_revoke_delegate_to_service() {
    let connect = Arc::new(RecordingConnectService::default());
    connect.request_lookup.lock().await.insert(
        "99".to_string(),
        PermissionRequest {
            request_id: "99".to_string(),
            edge_id: None,
            env: "dev".to_string(),
            from_id: "caller-bot".to_string(),
            to_id: "peer-bot".to_string(),
            request_kind: RequestKind::Connect,
            requested_ref_id: None,
            requested_rules: None,
            message: None,
            status: RequestStatus::Pending,
            decision_reason: None,
            created_by: "caller-bot".to_string(),
            decided_by: None,
            decided_at: None,
        },
    );
    let temp_dir = TempDir::new().unwrap();
    let app = build_app(
        &temp_dir,
        connect.clone(),
        Arc::new(RecordingBotQueryService::default()),
        None,
        Some(("caller-token", "caller-bot")),
    )
    .await;

    let cancel_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests/99/cancel")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(cancel_response.status(), StatusCode::OK);
    let cancel_body = to_bytes(cancel_response.into_body(), usize::MAX).await.unwrap();
    let cancel_json: Value = serde_json::from_slice(&cancel_body).unwrap();
    assert_eq!(cancel_json["data"]["status"], "cancelled");

    let revoke_response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/collaboration/friend-connections/peer-bot")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(revoke_response.status(), StatusCode::OK);
    let revoke_body = to_bytes(revoke_response.into_body(), usize::MAX).await.unwrap();
    let revoke_json: Value = serde_json::from_slice(&revoke_body).unwrap();
    assert_eq!(revoke_json["data"]["revoked_edges"], serde_json::json!([311]));

    let cancel_calls = connect.cancel_commands.lock().await;
    assert_eq!(cancel_calls.as_slice(), &["99".to_string()]);
    let revoke_calls = connect.revoke_commands.lock().await;
    assert_eq!(revoke_calls.as_slice(), &[("caller-bot".to_string(), "peer-bot".to_string())]);
}
#[tokio::test]
async fn friend_connection_request_cancel_rejects_other_caller() {
    let connect = Arc::new(RecordingConnectService::default());
    connect.request_lookup.lock().await.insert(
        "100".to_string(),
        PermissionRequest {
            request_id: "100".to_string(),
            edge_id: None,
            env: "dev".to_string(),
            from_id: "other-bot".to_string(),
            to_id: "peer-bot".to_string(),
            request_kind: RequestKind::Connect,
            requested_ref_id: None,
            requested_rules: None,
            message: None,
            status: RequestStatus::Pending,
            decision_reason: None,
            created_by: "other-bot".to_string(),
            decided_by: None,
            decided_at: None,
        },
    );
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
                .method("POST")
                .uri("/collaboration/friend-connections/requests/100/cancel")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(connect.cancel_commands.lock().await.is_empty());
}
#[tokio::test]
async fn friend_connection_requests_list_routes_through_service() {
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
                .uri("/collaboration/friend-connections/requests?direction=sent&status=approved&page=2&page_size=10")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["data"]["page"], 1);
    assert_eq!(json["data"]["page_size"], 20);

    let calls = connect.list_requests_commands.lock().await;
    assert_eq!(calls.len(), 1);
    let (actor, direction, status, page, page_size) = &calls[0];
    assert_eq!(actor, "caller-bot");
    assert!(matches!(direction, RequestDirection::Sent));
    assert!(matches!(status, Some(RequestStatus::Approved)));
    assert_eq!((*page, *page_size), (2, 10));
}
#[tokio::test]
async fn friend_connection_list_by_actor_defaults_to_raw_actor_when_kind_is_missing() {
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
                .uri("/collaboration/friend-connections?actor=bot-raw")
                .header("authorization", "Bearer caller-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = connect.list_friends_commands.lock().await;
    assert_eq!(calls.as_slice(), &["bot-raw".to_string()]);
}
#[tokio::test]
async fn friend_connection_requests_route_maps_status_filters_and_default_direction() {
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

    let cases = [
        ("", None),
        ("?status=pending", Some(RequestStatus::Pending)),
        ("?status=approved", Some(RequestStatus::Approved)),
        ("?status=rejected", Some(RequestStatus::Rejected)),
        ("?status=cancelled", Some(RequestStatus::Cancelled)),
        ("?status=accepted", Some(RequestStatus::Approved)),
        ("?status=unknown", None),
    ];

    for (suffix, expected_status) in cases {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("GET")
                    .uri(format!("/collaboration/friend-connections/requests{suffix}"))
                    .header("authorization", "Bearer caller-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);

        let calls = connect.list_requests_commands.lock().await;
        let (_, direction, status, _, _) = calls.last().expect("list request command");
        if suffix.is_empty() {
            assert!(matches!(direction, RequestDirection::Received));
        }
        assert_eq!(status, &expected_status);
    }
}
