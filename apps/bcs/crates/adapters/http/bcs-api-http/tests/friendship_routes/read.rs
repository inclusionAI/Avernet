//! Friendship route behavioral tests: reads (GET) and the auth-forwarding
//! `/auth/user` boundary. Split from the former top-level target (Task 7).

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs_api_http::{ApiState, router};
use bcs_service_api::application::v1::{
    ApplicationError, AuthFlowReply, AuthProviderUrlList, AuthRedirectLocation,
    AuthenticatedUserQuery, AuthService as ApplicationAuthService, AuthUserInfo, BuildLoginUrls,
    CompleteOAuthLogin, FriendConnectionActorType, FriendRequestDirection, FriendRequestStatus,
    LogoutData, PresentedSession, SessionRenewalData,
};
use serde_json::Value;
use tower::ServiceExt;

use super::fixtures::*;

// ---------------------------------------------------------------------------
// Captured auth-service request: moved verbatim beside the /auth/user test.

#[derive(Default)]
struct CapturingAuthService {
    current_user_query: Mutex<Option<AuthenticatedUserQuery>>,
}

#[async_trait]
impl ApplicationAuthService for CapturingAuthService {
    async fn login_urls(
        &self,
        _request: BuildLoginUrls,
    ) -> AuthFlowReply<AuthProviderUrlList> {
        AuthFlowReply {
            result: Err(ApplicationError::internal("unused")),
            cookie_changes: Vec::new(),
        }
    }

    async fn complete_login(
        &self,
        _request: CompleteOAuthLogin,
    ) -> AuthFlowReply<AuthRedirectLocation> {
        AuthFlowReply {
            result: Err(ApplicationError::internal("unused")),
            cookie_changes: Vec::new(),
        }
    }

    async fn current_user(&self, query: AuthenticatedUserQuery) -> Result<AuthUserInfo, ApplicationError> {
        *self
            .current_user_query
            .lock()
            .expect("current user query lock") = Some(query);
        Ok(AuthUserInfo {
            user_id: "staff-1".to_string(),
            name: Some("alice".to_string()),
            provider: "chain".to_string(),
            avatar: None,
        })
    }

    async fn refresh_session(
        &self,
        _session: PresentedSession,
    ) -> AuthFlowReply<SessionRenewalData> {
        AuthFlowReply {
            result: Err(ApplicationError::internal("unused")),
            cookie_changes: Vec::new(),
        }
    }

    async fn logout(&self, _session: Option<PresentedSession>) -> AuthFlowReply<LogoutData> {
        AuthFlowReply {
            result: Err(ApplicationError::internal("unused")),
            cookie_changes: Vec::new(),
        }
    }
}

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
async fn openapi_auth_user_projects_the_verified_caller_through_auth_service() {
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
                .body(Body::empty())
                .expect("auth user request"),
        )
        .await
        .expect("auth user response");
    assert_eq!(response.status(), StatusCode::OK);

    // The new contract (V1 API auth plan §8.2): `/user` receives the
    // VERIFIED caller plus its pinned provenance source — never raw
    // request headers (the service must not re-run a credential chain).
    let queried = auth_service
        .current_user_query
        .lock()
        .expect("current user query lock")
        .clone()
        .expect("captured auth query");
    let user = queried.caller.user.expect("authenticated human caller");
    assert_eq!(user.id, "staff-1");
    assert_eq!(queried.provider_label, "test-double");
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
