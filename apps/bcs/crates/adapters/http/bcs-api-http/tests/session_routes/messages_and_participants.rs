use super::*;

#[tokio::test]
async fn list_session_messages_wraps_legacy_group_message_array_in_envelope() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?limit=50",
            Value::Null,
        ))
        .await
        .expect("list messages response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(
        body["data"],
        serde_json::to_value(rich_group_messages()).expect("serialize legacy response")
    );
    assert!(body["data"].is_array());
    assert_eq!(body["data"][0]["role"], "assistant");
    assert_eq!(body["data"][0]["historyMeta"]["assistantAggregation"], true);
    assert_eq!(body["data"][0]["attachments"][0]["type"], "image");
    {
        let listed = message.listed.lock().expect("list messages lock");
        let listed = listed.as_ref().expect("list messages command");
        assert_eq!(caller_user_id(&listed.caller), "staff-1");
        assert_eq!(listed.session_id, "session-1");
        assert_eq!(listed.before, None);
        assert_eq!(listed.limit, 50);
    }
    assert_eq!(
        *message.options.lock().expect("list messages options lock"),
        Some(MessageHistoryOptions::default())
    );
}
#[tokio::test]
async fn list_session_messages_passes_pending_history_opt_in_through() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?limit=50&include_pending=true",
            Value::Null,
        ))
        .await
        .expect("list messages response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        *message.options.lock().expect("list messages options lock"),
        Some(MessageHistoryOptions {
            include_pending: true,
        })
    );
}
#[tokio::test]
async fn list_session_messages_passes_before_timestamp_through() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?before=1234567890&limit=10",
            Value::Null,
        ))
        .await
        .expect("list messages response");
    assert_eq!(response.status(), StatusCode::OK);
    {
        let listed = message.listed.lock().expect("list messages lock");
        let listed = listed.as_ref().expect("list messages command");
        assert_eq!(listed.session_id, "session-1");
        assert_eq!(listed.before, Some(1_234_567_890));
        assert_eq!(listed.limit, 10);
    }
}
#[tokio::test]
async fn list_session_messages_passes_view_bot_id_through() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message.clone());

    // The optional `view_bot_id` query param must be forwarded verbatim to the
    // `ListSessionMessages` command field; the route layer must not interpret
    // or strip it (the V1 facade owns the Principal-based authz resolution).
    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?limit=50&view_bot_id=bot-xyz",
            Value::Null,
        ))
        .await
        .expect("list messages response");
    assert_eq!(response.status(), StatusCode::OK);
    {
        let listed = message.listed.lock().expect("list messages lock");
        let listed = listed.as_ref().expect("list messages command");
        assert_eq!(listed.session_id, "session-1");
        assert_eq!(listed.limit, 50);
        assert_eq!(listed.view_bot_id.as_deref(), Some("bot-xyz"));
    }
}
#[tokio::test]
async fn list_session_messages_rejects_non_numeric_before_timestamp() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?before=not-a-timestamp",
            Value::Null,
        ))
        .await
        .expect("list messages response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(message.listed.lock().expect("list messages lock").is_none());
}
#[tokio::test]
async fn add_session_participant_returns_participant() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/participants",
            json!({"bot_uuid": "bot-2"}),
        ))
        .await
        .expect("add participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["actor_id"], "bot-2");
    assert_eq!(body["data"]["mode"], "auto");
    {
        let added = session
            .added_participant
            .lock()
            .expect("add participant lock");
        let added = added.as_ref().expect("add participant command");
        assert_eq!(caller_user_id(&added.caller), "staff-1");
        assert_eq!(added.session_id, "session-1");
        assert_eq!(added.bot_uuid, "bot-2");
    }
}
#[tokio::test]
async fn update_session_participant_returns_updated_mode() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/collaboration/sessions/session-1/participants/bot-2",
            json!({"mode": "muted"}),
        ))
        .await
        .expect("update participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["mode"], "muted");
    {
        let updated = session
            .updated_participant
            .lock()
            .expect("update participant lock");
        let updated = updated.as_ref().expect("update participant command");
        assert_eq!(caller_user_id(&updated.caller), "staff-1");
        assert_eq!(updated.session_id, "session-1");
        assert_eq!(updated.bot_uuid, "bot-2");
        assert_eq!(updated.mode, Some(ParticipantMode::Muted));
    }
}
#[tokio::test]
async fn update_session_human_participant_accepts_present_mode() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/collaboration/sessions/session-1/participants/human_staff-1",
            json!({"mode": "present"}),
        ))
        .await
        .expect("update Human participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["actor_kind"], "human");
    assert_eq!(body["data"]["mode"], "present");

    let updated = session
        .updated_participant
        .lock()
        .expect("update participant lock");
    let updated = updated.as_ref().expect("update participant command");
    assert_eq!(updated.bot_uuid, "human_staff-1");
    assert_eq!(updated.mode, Some(ParticipantMode::Present));
}
#[tokio::test]
async fn remove_session_participant_returns_deleted() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/collaboration/sessions/session-1/participants/bot-2",
            Value::Null,
        ))
        .await
        .expect("remove participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["deleted"], true);
    {
        let removed = session
            .removed_participant
            .lock()
            .expect("remove participant lock");
        let removed = removed.as_ref().expect("remove participant command");
        assert_eq!(caller_user_id(&removed.caller), "staff-1");
        assert_eq!(removed.session_id, "session-1");
        assert_eq!(removed.bot_uuid, "bot-2");
    }
}
#[tokio::test]
async fn unknown_fields_rejected_with_invalid_request() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({
                "driver_bot_uuid": "bot-1"
            }),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(session.created.lock().expect("create lock").is_none());

    let reactivation_response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({"session_id": "legacy-session"}),
        ))
        .await
        .expect("V1 reactivation field response");
    assert_eq!(reactivation_response.status(), StatusCode::BAD_REQUEST);
    let reactivation_body = response_json(reactivation_response).await;
    assert_eq!(reactivation_body["data"]["error_code"], "invalid_request");
    assert!(session.created.lock().expect("create lock").is_none());

    let patch_response = app
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/collaboration/sessions/session-1",
            json!({"title": "Renamed", "extra": 1}),
        ))
        .await
        .expect("unknown patch field response");
    assert_eq!(patch_response.status(), StatusCode::BAD_REQUEST);
    let patch_body = response_json(patch_response).await;
    assert_eq!(patch_body["data"]["error_code"], "invalid_request");
}
#[tokio::test]
async fn create_session_rejects_null_optional_fields() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    for field in [
        "title",
        "kind",
        "acting_bot_id",
        "creator_role",
        "message_view_scope",
        "input",
        "meta",
        "context_delivery",
    ] {
        let mut body = serde_json::Map::new();
        body.insert(field.into(), Value::Null);
        let response = app
            .clone()
            .oneshot(authenticated_request(
                "POST",
                "/openapi/v1/collaboration/groups/group-1/sessions",
                Value::Object(body),
            ))
            .await
            .expect("null field response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "field={field}");
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "invalid_request");
    }
    assert!(session.created.lock().expect("create lock").is_none());
}
#[tokio::test]
async fn create_session_rejects_contract_invalid_identity_and_metadata() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);
    let invalid_bodies = [
        json!({"acting_bot_id": ""}),
        json!({"meta": {"callback_target": null}}),
        json!({"meta": {"callback_target": {"user_id": 1}}}),
        json!({"meta": {"channel": "chat"}}),
        json!({"meta": {"channel": {"session_scope": "global"}}}),
        json!({"meta": {"context_projection": "private"}}),
    ];

    for body in invalid_bodies {
        let response = app
            .clone()
            .oneshot(authenticated_request(
                "POST",
                "/openapi/v1/collaboration/groups/group-1/sessions",
                body,
            ))
            .await
            .expect("invalid request response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "invalid_request");
    }
    assert!(session.created.lock().expect("create lock").is_none());
}
#[tokio::test]
async fn missing_principal_returns_unauthenticated() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message);

    let response = app
        .oneshot(
            Request::builder()
                .uri("/openapi/v1/collaboration/sessions/session-1")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("missing auth response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "unauthenticated");
}
#[tokio::test]
async fn legacy_global_session_paths_are_not_mounted() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message.clone());

    for uri in [
        "/openapi/v1/sessions/session-1",
        "/openapi/v1/sessions/session-1/messages",
        "/openapi/v1/group-sessions/session-1",
    ] {
        let response = app
            .clone()
            .oneshot(authenticated_request("GET", uri, Value::Null))
            .await
            .expect("legacy path response");
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{uri}");
    }

    assert!(session.got.lock().expect("get lock").is_none());
    assert!(message.listed.lock().expect("list messages lock").is_none());
}
#[tokio::test]
async fn update_session_participant_requires_mode() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message);

    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/collaboration/sessions/session-1/participants/bot-2",
            json!({}),
        ))
        .await
        .expect("missing mode response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
}
#[tokio::test]
async fn list_sessions_uses_default_pagination_when_omitted() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            Value::Null,
        ))
        .await
        .expect("list default response");
    assert_eq!(response.status(), StatusCode::OK);
    {
        let listed = session.listed.lock().expect("list lock");
        let listed = listed.as_ref().expect("list command");
        assert_eq!(listed.offset, 0);
        assert_eq!(listed.limit, 20);
        assert_eq!(listed.status, None);
    }
}
