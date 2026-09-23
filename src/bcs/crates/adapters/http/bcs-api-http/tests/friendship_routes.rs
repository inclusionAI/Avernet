//! V1 `friendship_routes` boundary tests. Shared fixtures loaded from
//! `support/friendship_routes_fixtures.rs` to keep this file under 1000 lines
//! (Task 7 split).

#[path = "support/friendship_routes_fixtures.rs"]
mod fixtures;
use fixtures::*;

#[tokio::test]
async fn list_friendships_returns_page_and_forwards_principal() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-1/friendships?offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("list friendships response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["request_id"], "request-123");
    assert_eq!(body["data"]["items"][0]["bot_uuid"], "bot-1");
    assert_eq!(body["data"]["items"][0]["friend_bot_uuid"], "bot-2");
    assert_eq!(body["data"]["total"], 1);
    assert_eq!(body["data"]["offset"], 5);
    assert_eq!(body["data"]["limit"], 10);
    {
        let listed = service.listed_friendships.lock().expect("list friendships lock");
        let listed = listed.as_ref().expect("list friendships command");
        assert_eq!(caller_user_id(&listed.caller), "staff-1");
        assert_eq!(listed.bot_uuid, "bot-1");
        assert_eq!(listed.offset, 5);
        assert_eq!(listed.limit, 10);
    }
}

#[tokio::test]
async fn list_friendships_uses_default_pagination_when_omitted() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-1/friendships",
            Value::Null,
        ))
        .await
        .expect("default pagination response");
    assert_eq!(response.status(), StatusCode::OK);
    {
        let listed = service.listed_friendships.lock().expect("list friendships lock");
        let listed = listed.as_ref().expect("list friendships command");
        assert_eq!(listed.offset, 0);
        assert_eq!(listed.limit, 20);
    }
}

#[tokio::test]
async fn remove_friendship_returns_deleted_and_forwards_principal() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/collaboration/bots/bot-1/friendships/bot-2",
            Value::Null,
        ))
        .await
        .expect("remove friendship response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["deleted"], true);
    {
        let removed = service.removed_friendship.lock().expect("remove friendship lock");
        let removed = removed.as_ref().expect("remove friendship command");
        assert_eq!(caller_user_id(&removed.caller), "staff-1");
        assert_eq!(removed.bot_uuid, "bot-1");
        assert_eq!(removed.friend_bot_uuid, "bot-2");
    }
}

#[tokio::test]
async fn create_friend_request_returns_created_and_forwards_principal() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/bots/bot-1/friend-requests",
            json!({"to_bot_uuid": "bot-2"}),
        ))
        .await
        .expect("create friend request response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["message"], "Created");
    assert_eq!(body["data"]["request_id"], "req-1");
    assert_eq!(body["data"]["from_bot_uuid"], "bot-1");
    assert_eq!(body["data"]["to_bot_uuid"], "bot-2");
    assert_eq!(body["data"]["status"], "pending");
    {
        let created = service
            .created_friend_request
            .lock()
            .expect("create friend request lock");
        let created = created.as_ref().expect("create friend request command");
        assert_eq!(caller_user_id(&created.caller), "staff-1");
        assert_eq!(created.bot_uuid, "bot-1");
        assert_eq!(created.to_bot_uuid, "bot-2");
    }
}

#[tokio::test]
async fn list_friend_requests_returns_page_and_forwards_filters() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-1/friend-requests?offset=3&limit=5&direction=sent&status=pending",
            Value::Null,
        ))
        .await
        .expect("list friend requests response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["items"][0]["request_id"], "req-1");
    assert_eq!(body["data"]["total"], 1);
    assert_eq!(body["data"]["offset"], 3);
    assert_eq!(body["data"]["limit"], 5);
    {
        let listed = service
            .listed_friend_requests
            .lock()
            .expect("list friend requests lock");
        let listed = listed.as_ref().expect("list friend requests command");
        assert_eq!(caller_user_id(&listed.caller), "staff-1");
        assert_eq!(listed.bot_uuid, "bot-1");
        assert_eq!(listed.direction, FriendRequestDirection::Sent);
        assert_eq!(listed.status, Some(FriendRequestStatus::Pending));
        assert_eq!(listed.offset, 3);
        assert_eq!(listed.limit, 5);
    }
}

#[tokio::test]
async fn list_friend_requests_defaults_direction_to_received() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-1/friend-requests",
            Value::Null,
        ))
        .await
        .expect("default direction response");
    assert_eq!(response.status(), StatusCode::OK);
    {
        let listed = service
            .listed_friend_requests
            .lock()
            .expect("list friend requests lock");
        let listed = listed.as_ref().expect("list friend requests command");
        assert_eq!(listed.direction, FriendRequestDirection::Received);
        assert_eq!(listed.status, None);
        assert_eq!(listed.offset, 0);
        assert_eq!(listed.limit, 20);
    }
}

#[tokio::test]
async fn accept_friend_request_returns_ok_and_forwards_principal() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/friend-requests/req-1/accept",
            Value::Null,
        ))
        .await
        .expect("accept friend request response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["data"]["request_id"], "req-1");
    assert_eq!(body["data"]["status"], "accepted");
    {
        let accepted = service
            .accepted_friend_request
            .lock()
            .expect("accept friend request lock");
        let accepted = accepted.as_ref().expect("accept friend request command");
        assert_eq!(caller_user_id(&accepted.caller), "staff-1");
        assert_eq!(accepted.request_id, "req-1");
    }
}

#[tokio::test]
async fn reject_friend_request_returns_ok_and_forwards_principal() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/friend-requests/req-1/reject",
            Value::Null,
        ))
        .await
        .expect("reject friend request response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["request_id"], "req-1");
    assert_eq!(body["data"]["status"], "rejected");
    {
        let rejected = service
            .rejected_friend_request
            .lock()
            .expect("reject friend request lock");
        let rejected = rejected.as_ref().expect("reject friend request command");
        assert_eq!(caller_user_id(&rejected.caller), "staff-1");
        assert_eq!(rejected.request_id, "req-1");
    }
}

#[tokio::test]
async fn unknown_fields_rejected_with_invalid_request() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/bots/bot-1/friend-requests",
            json!({"to_bot_uuid": "bot-2", "extra": 1}),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(service
        .created_friend_request
        .lock()
        .expect("create friend request lock")
        .is_none());

    let unknown_query = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-1/friendships?bogus=1",
            Value::Null,
        ))
        .await
        .expect("unknown query field response");
    assert_eq!(unknown_query.status(), StatusCode::BAD_REQUEST);
    let body = response_json(unknown_query).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
}

/// Task 11 coverage transfer: the OLD test asserted raw header forwarding
/// into the auth service (RequestAuthHeaders). After the delivery-principal
/// boundary (Task 7) and the atomic AuthService switchover (Task 11),
/// `/auth/user` no longer forwards headers into the application layer — the
/// delivery adapter verifies the caller and projects the resolved Human via
/// `AuthenticatedUserQuery`. This test pins the NEW contract: the
/// verifier-resolved caller identity (never raw credentials) reaches the
/// service.
#[tokio::test]
async fn openapi_auth_user_projects_verified_caller_into_auth_service() {
    let auth_service = Arc::new(CapturingAuthService::default());
    let app = router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopSessionMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(FakeFriendshipService::default()),
            Arc::new(HeaderVerifier { caller: caller() }),
        )
        .with_auth_service(auth_service.clone(), "http://127.0.0.1/openapi/v1/auth".to_string()),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/openapi/v1/auth/user")
                .header("x-test-auth", "yes")
                .header("authorization", "Bearer forwarded-user-token")
                .header("cookie", "bcs_session=session-token")
                .body(Body::empty())
                .expect("auth user request"),
        )
        .await
        .expect("auth user response");
    assert_eq!(response.status(), StatusCode::OK);

    let request = auth_service
        .current_user_request
        .lock()
        .expect("current user request lock")
        .clone()
        .expect("captured auth request");
    // The service sees the VERIFIED caller projection, never credentials.
    assert_eq!(
        request
            .caller
            .user
            .as_ref()
            .expect("human caller projected")
            .id,
        "staff-1"
    );
    assert_eq!(request.provider_label, "test");
}

#[tokio::test]
async fn openapi_friend_connection_routes_forward_commands_and_serialize_responses() {
    let service = Arc::new(FakeFriendConnectionService::default());
    *service.create_result.lock().expect("create result lock") = FriendConnectionCreateResult {
        request_ids: vec!["99".to_string()],
        edge_ids: vec![199],
        status: FriendConnectionCreateStatus::Approved,
        auto_accepted: true,
    };
    let app = openapi_test_router(service.clone());

    let create_response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/friend-connections/requests",
            serde_json::json!({
                "from_actor": {"type": "bot", "id": "bot-9"},
                "to_actor": {"type": "bot", "id": "bot-2"},
                "message": "hello"
            }),
        ))
        .await
        .expect("create response");
    assert_eq!(create_response.status(), StatusCode::CREATED);
    let body = response_json(create_response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["data"]["status"], "approved");
    assert_eq!(body["data"]["request_ids"], serde_json::json!(["99"]));
    assert_eq!(body["data"]["edge_ids"], serde_json::json!([199]));
    assert_eq!(body["data"]["auto_accepted"], true);
    {
        let created = service.created_request.lock().expect("create request lock");
        let created = created.as_ref().expect("create command");
        assert_eq!(created.caller.user.as_ref().expect("user").id, "staff-1");
        assert_eq!(created.from_actor.as_ref().expect("from").id, "bot-9");
        assert_eq!(created.to_actor.id, "bot-2");
        assert_eq!(created.message.as_deref(), Some("hello"));
    }

    let list_response = app
        .clone()
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/friend-connections/requests?actor_type=bot&actor_id=bot-9&direction=sent&status=approved&page=2&page_size=10",
            Value::Null,
        ))
        .await
        .expect("list requests response");
    assert_eq!(list_response.status(), StatusCode::OK);
    let body = response_json(list_response).await;
    assert_eq!(body["data"]["page"], 1);
    assert_eq!(body["data"]["page_size"], 20);
    {
        let listed = service.listed_requests.lock().expect("list requests lock");
        let listed = listed.as_ref().expect("list command");
        assert_eq!(listed.caller.user.as_ref().expect("user").id, "staff-1");
        assert_eq!(listed.actor.as_ref().expect("actor").id, "bot-9");
        assert!(matches!(listed.direction, FriendConnectionRequestDirection::Sent));
        assert_eq!(listed.status, Some(FriendConnectionRequestStatus::Approved));
        assert_eq!((listed.page, listed.page_size), (2, 10));
    }

    let connections_response = app
        .clone()
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/friend-connections?actor_type=human&actor_id=1001",
            Value::Null,
        ))
        .await
        .expect("list connections response");
    assert_eq!(connections_response.status(), StatusCode::OK);
    let body = response_json(connections_response).await;
    assert_eq!(body["data"]["items"][0]["actor"]["id"], "friend-bot");
    {
        let listed = service.listed_connections.lock().expect("list connections lock");
        let listed = listed.as_ref().expect("list connections command");
        assert_eq!(listed.actor.actor_type, FriendConnectionActorType::Human);
        assert_eq!(listed.actor.id, "1001");
        assert_eq!(listed.target_type, None);
        assert_eq!(listed.page, 1);
        assert_eq!(listed.page_size, 20);
    }
}

#[tokio::test]
async fn openapi_friend_connection_create_forwards_caller_auth_headers() {
    let service = Arc::new(FakeFriendConnectionService::default());
    let app = openapi_test_router(service.clone());

    let create_response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/openapi/v1/collaboration/friend-connections/requests")
                .header("content-type", "application/json")
                .header("x-test-auth", "yes")
                .header("x-request-id", "request-123")
                .header("authorization", "Bearer forwarded-user-token")
                .header("cookie", "session=abc")
                .header("x-one-id", "uid-42")
                .body(Body::from(
                    json!({"to_actor": {"type": "bot", "id": "bot-2"}}).to_string(),
                ))
                .expect("create request"),
        )
        .await
        .expect("create response");
    assert_eq!(create_response.status(), StatusCode::CREATED);

    let created = service.created_request.lock().expect("create request lock");
    let created = created.as_ref().expect("create command");
    assert_eq!(created.to_actor.id, "bot-2");
    assert_eq!(
        created.request_auth,
        Some(RequestAuthHeaders {
            authorization: Some("Bearer forwarded-user-token".to_string()),
            cookie: Some("session=abc".to_string()),
            forwarded_headers: vec![
                ("authorization".to_string(), "Bearer forwarded-user-token".to_string()),
                ("cookie".to_string(), "session=abc".to_string()),
                ("x-one-id".to_string(), "uid-42".to_string()),
                ("x-request-id".to_string(), "request-123".to_string()),
            ],
        })
    );
}

#[tokio::test]
async fn openapi_friend_connection_routes_support_default_query_values_and_optional_reject_reason() {
    let service = Arc::new(FakeFriendConnectionService::default());
    let app = openapi_test_router(service.clone());

    let create_response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/friend-connections/requests",
            serde_json::json!({
                "to_actor": {"type": "bot", "id": "bot-2"}
            }),
        ))
        .await
        .expect("create response with implicit from_actor");
    assert_eq!(create_response.status(), StatusCode::CREATED);
    {
        let created = service.created_request.lock().expect("create request lock");
        let created = created.as_ref().expect("create command");
        assert!(created.from_actor.is_none());
    }

    let list_response = app
        .clone()
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/friend-connections/requests",
            Value::Null,
        ))
        .await
        .expect("default list requests response");
    assert_eq!(list_response.status(), StatusCode::OK);
    {
        let listed = service.listed_requests.lock().expect("list requests lock");
        let listed = listed.as_ref().expect("list command");
        assert_eq!((listed.page, listed.page_size), (1, 20));
    }

    let reject_response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/friend-connections/requests/2/reject",
            serde_json::json!({"reason": "no thanks"}),
        ))
        .await
        .expect("reject response with reason");
    assert_eq!(reject_response.status(), StatusCode::OK);
    {
        let rejected = service.rejected_request.lock().expect("reject lock");
        let rejected = rejected.as_ref().expect("reject command");
        assert_eq!(rejected.reason.as_deref(), Some("no thanks"));
        assert_eq!(rejected.request_id, "2".to_string());
    }
}

#[tokio::test]
async fn openapi_friend_connection_routes_forward_decisions_and_delete() {
    let service = Arc::new(FakeFriendConnectionService::default());
    let app = openapi_test_router(service.clone());

    let accept_response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/friend-connections/requests/1/accept",
            Value::Null,
        ))
        .await
        .expect("accept response");
    assert_eq!(accept_response.status(), StatusCode::OK);
    let body = response_json(accept_response).await;
    assert_eq!(body["data"]["request_id"], "1");
    {
        let accepted = service.accepted_request.lock().expect("accept lock");
        let accepted = accepted.as_ref().expect("accept command");
        assert_eq!(accepted.caller.user.as_ref().expect("user").id, "staff-1");
        assert_eq!(accepted.request_id, "1".to_string());
    }

    let reject_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/openapi/v1/collaboration/friend-connections/requests/1/reject")
                .header("x-test-auth", "yes")
                .header("x-request-id", "request-123")
                .body(Body::empty())
                .expect("reject request"),
        )
        .await
        .expect("reject response");
    assert_eq!(reject_response.status(), StatusCode::OK);
    {
        let rejected = service.rejected_request.lock().expect("reject lock");
        let rejected = rejected.as_ref().expect("reject command");
        assert!(rejected.reason.is_none());
    }

    let cancel_response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/friend-connections/requests/1/cancel",
            Value::Null,
        ))
        .await
        .expect("cancel response");
    assert_eq!(cancel_response.status(), StatusCode::OK);
    {
        let cancelled = service.cancelled_request.lock().expect("cancel lock");
        let cancelled = cancelled.as_ref().expect("cancel command");
        assert_eq!(cancelled.request_id, "1".to_string());
    }

    let delete_response = app
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/collaboration/friend-connections?target_actor_type=bot&target_actor_id=bot-2",
            Value::Null,
        ))
        .await
        .expect("delete response");
    assert_eq!(delete_response.status(), StatusCode::OK);
    let body = response_json(delete_response).await;
    assert_eq!(body["data"]["deleted"], true);
    {
        let deleted = service.deleted_connection.lock().expect("delete lock");
        let deleted = deleted.as_ref().expect("delete command");
        assert_eq!(deleted.target_actor.actor_type, FriendConnectionActorType::Bot);
        assert_eq!(deleted.target_actor.id, "bot-2");
    }
}

#[tokio::test]
async fn openapi_friend_connection_routes_fail_closed_when_service_missing_or_payload_invalid() {
    let app = router(ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(FakeFriendshipService::default()),
        Arc::new(HeaderVerifier { caller: caller() }),
    ));

    let missing_service = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/friend-connections/requests",
            serde_json::json!({
                "to_actor": {"type": "bot", "id": "bot-2"}
            }),
        ))
        .await
        .expect("missing service response");
    assert_eq!(missing_service.status(), StatusCode::INTERNAL_SERVER_ERROR);

    let invalid_body = openapi_test_router(Arc::new(FakeFriendConnectionService::default()))
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/openapi/v1/collaboration/friend-connections/requests")
                .header("content-type", "application/json")
                .header("x-test-auth", "yes")
                .header("x-request-id", "request-123")
                .body(Body::from("{"))
                .expect("request"),
        )
        .await
        .expect("invalid body response");
    assert_eq!(invalid_body.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn missing_principal_returns_unauthenticated() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service);

    let response = app
        .oneshot(
            Request::builder()
                .uri("/openapi/v1/collaboration/bots/bot-1/friendships")
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
async fn openapi_friend_connections_passes_filter_and_pagination() {
    for (value, kind) in [("human", FriendConnectionActorType::Human), ("bot", FriendConnectionActorType::Bot)] {
        let service = Arc::new(FakeFriendConnectionService::default());
        let response = openapi_test_router(service.clone()).oneshot(authenticated_request(
            "GET",
            &format!("/openapi/v1/collaboration/friend-connections?actor_type=bot&actor_id=bot-1&target_type={value}&page=2&page_size=10"),
            Value::Null,
        )).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let commands = service.listed_connections.lock().unwrap();
        let command = commands.as_ref().unwrap();
        assert_eq!(command.target_type, Some(kind));
        assert_eq!((command.page, command.page_size), (2, 10));
    }
}

#[tokio::test]
async fn openapi_friend_connections_rejects_invalid_query_types() {
    for suffix in ["target_type=unknown", "page=-1", "page=abc", "page=4294967296", "page_size=-1"] {
        let service = Arc::new(FakeFriendConnectionService::default());
        let response = openapi_test_router(service.clone()).oneshot(authenticated_request(
            "GET",
            &format!("/openapi/v1/collaboration/friend-connections?actor_type=bot&actor_id=bot-1&{suffix}"),
            Value::Null,
        )).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{suffix}");
        assert!(service.listed_connections.lock().unwrap().is_none());
    }
}

/// A verified Bot cannot become a Human through the legacy fallback, with or
/// without an OAuth facade configured. Pin the documented 403 envelope.
#[tokio::test]
async fn openapi_auth_user_rejects_non_human_without_legacy_fallback() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use bcs_api_http::v1::common::ChainUserProjection;
    use bcs_service_api::application::v1::AuthenticatedBotIdentity;

    #[derive(Default)]
    struct LegacyProjection(AtomicUsize);

    #[async_trait]
    impl ChainUserProjection for LegacyProjection {
        async fn current_user(&self, _headers: &HeaderMap) -> Result<AuthUserInfo, ApplicationError> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Ok(AuthUserInfo {
                user_id: "legacy-human".to_string(),
                name: None,
                provider: "legacy".to_string(),
                avatar: None,
            })
        }
    }

    for with_oauth in [false, true] {
        let projection = Arc::new(LegacyProjection::default());
        let auth_service = Arc::new(CapturingAuthService::default());
        let mut bot_caller = caller();
        bot_caller.user = None;
        bot_caller.bot = Some(AuthenticatedBotIdentity {
            bot_uuid: "bot-only".to_string(),
            owner_id: "owner".to_string(),
            app_id: 1,
            agent_code: "agent".to_string(),
        });
        let mut state = ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopSessionMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(FakeFriendshipService::default()),
            Arc::new(HeaderVerifier { caller: bot_caller }),
        ).with_chain_user_projection(projection.clone());
        if with_oauth {
            state = state.with_auth_service(
                auth_service.clone(), "http://127.0.0.1/openapi/v1/auth".to_string(),
            );
        }
        let response = router(state).oneshot(authenticated_request(
            "GET", "/openapi/v1/auth/user", Value::Null,
        )).await.expect("response");
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        let body = response_json(response).await;
        assert_eq!(body["code"], 40_300);
        assert_eq!(body["data"]["error_code"], "forbidden");
        assert_eq!(body["request_id"], "request-123");
        assert_eq!(projection.0.load(Ordering::SeqCst), 0);
        assert!(auth_service.current_user_request.lock().unwrap().is_none());
    }
}
