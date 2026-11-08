use super::*;

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn get_session_file_uses_the_v1_envelope() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files/file-1",
            json!(null),
        ))
        .await
        .expect("file response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["file_id"], "file-1");
    assert_eq!(body["data"]["status"], "ready");
}
#[tokio::test]
async fn prepare_session_file_projects_the_proxy_upload_url() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/api/v1/collaboration/sessions/session-1/files",
            json!({
                "file_name": "report.txt",
                "size": 42,
                "mime_type": "text/plain"
            }),
        ))
        .await
        .expect("prepare response");

    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["data"]["file_id"], "file-1");
    assert_eq!(
        body["data"]["upload_url"],
        "https://gateway.example.com/api/v1/collaboration/sessions/session-1/files/file-1/content"
    );
}
#[tokio::test]
async fn protected_session_file_mutations_and_download_follow_v1_statuses() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    let upload = Request::builder()
        .method("PUT")
        .uri("/api/v1/collaboration/sessions/session-1/files/file-1/content?part=1")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-upload")
        .body(Body::from("abc"))
        .expect("upload request");
    let response = app.clone().oneshot(upload).await.expect("upload response");
    assert_eq!(response.status(), StatusCode::ACCEPTED);

    for (method, path, expected) in [
        (
            "POST",
            "/api/v1/collaboration/sessions/session-1/files/file-1/complete",
            StatusCode::OK,
        ),
        (
            "DELETE",
            "/api/v1/collaboration/sessions/session-1/files/file-1",
            StatusCode::OK,
        ),
        (
            "GET",
            "/api/v1/collaboration/sessions/session-1/files",
            StatusCode::OK,
        ),
    ] {
        let response = app
            .clone()
            .oneshot(authenticated_request(method, path, json!(null)))
            .await
            .expect("file operation response");
        assert_eq!(response.status(), expected, "{method} {path}");
    }

    let share = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/api/v1/collaboration/sessions/session-1/files/file-1/share",
            json!({}),
        ))
        .await
        .expect("share response");
    assert_eq!(share.status(), StatusCode::CREATED);
    let share_body = response_json(share).await;
    assert_eq!(
        share_body["data"]["share_url"],
        "https://gateway.example.com/api/v1/collaboration/sessions/shared-file/content?token=share-token"
    );

    let download = app
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files/file-1/content",
            json!(null),
        ))
        .await
        .expect("download response");
    assert_eq!(download.status(), StatusCode::FOUND);
    assert_eq!(
        download.headers()["location"],
        "https://storage.example.com/file-1"
    );
}
#[tokio::test]
async fn shared_file_content_is_public_and_token_failures_are_uniform_not_found() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    for uri in [
        "/api/v1/collaboration/sessions/shared-file/content",
        "/api/v1/collaboration/sessions/shared-file/content?token=bad-token",
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(uri)
                    .header("x-request-id", "public-request")
                    .body(Body::empty())
                    .expect("public request"),
            )
            .await
            .expect("public response");
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{uri}");
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "shared_file_not_found");
    }

    let success = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(
                    "/api/v1/collaboration/sessions/shared-file/content?token=good-token&show=true",
                )
                .body(Body::empty())
                .expect("public success request"),
        )
        .await
        .expect("public success response");
    assert_eq!(success.status(), StatusCode::OK);
    assert_eq!(
        success.headers()["content-disposition"],
        "inline; filename=\"report.txt\""
    );
    assert_eq!(
        to_bytes(success.into_body(), usize::MAX).await.unwrap(),
        Bytes::from_static(b"abc")
    );

    let protected = app
        .oneshot(
            Request::builder()
                .uri("/api/v1/collaboration/sessions/session-1/files")
                .body(Body::empty())
                .expect("protected request"),
        )
        .await
        .expect("protected response");
    assert_eq!(protected.status(), StatusCode::UNAUTHORIZED);
}
#[tokio::test]
async fn shared_file_content_preserves_infrastructure_failures() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    for (token, expected) in [
        ("backend-error", StatusCode::BAD_GATEWAY),
        ("internal-error", StatusCode::INTERNAL_SERVER_ERROR),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!(
                        "/api/v1/collaboration/sessions/shared-file/content?token={token}"
                    ))
                    .body(Body::empty())
                    .expect("public request"),
            )
            .await
            .expect("public response");
        assert_eq!(response.status(), expected, "{token}");
    }
}
#[tokio::test]
async fn session_file_route_keeps_only_the_protocol_identity_check() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let bot = AuthenticatedBotIdentity {
        bot_uuid: "bot-1".into(),
        owner_id: "staff-1".into(),
        app_id: 1,
        agent_code: "agent".into(),
    };

    let bot_only = test_session_router_for_caller(
        session.clone(),
        message.clone(),
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: Some(bot.clone()),
            app: None,
            access_key: None,
        },
    );
    let response = bot_only
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files",
            Value::Null,
        ))
        .await
        .expect("Bot-only response");
    assert_eq!(response.status(), StatusCode::OK);

    let mismatched = test_session_router_for_caller(
        session.clone(),
        message.clone(),
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: caller().user,
            bot: Some(AuthenticatedBotIdentity {
                owner_id: "someone-else".into(),
                ..bot
            }),
            app: None,
            access_key: None,
        },
    );
    // The route no longer decides the Human→Bot leg
    // from claims: the mismatched-claim hybrid (whose
    // historical owner no longer says "owned") is
    // carried to the application facade — the live
    // `resolve_authorized_principal` decision lives
    // there (6c file.rs cutover; a revoked A is
    // denied and a current manager passes at the
    // facade level, cutover suite).
    let response = mismatched
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files",
            Value::Null,
        ))
        .await
        .expect("mismatched User/Bot response");
    assert_eq!(response.status(), StatusCode::OK);

    let app_only = test_session_router_for_caller(
        session,
        message,
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: None,
            app: Some(AuthenticatedAppIdentity {
                app_id: 1,
                app_name: "test-app".into(),
                owners: "staff-1".into(),
                app_type: "service".into(),
            }),
            access_key: None,
        },
    );
    let response = app_only
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files",
            Value::Null,
        ))
        .await
        .expect("App-only response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}
