use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, AddGroupParticipant, AddSessionParticipant,
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, CompleteSession,
    CreateBotFriendRequest, CreateGroup, CreateGroupInvitation,
    CreateSession, CreateSessionInvitation, CreateSessionOutcome, DeleteGroup,
    DeleteGroupParticipant, DeleteResult, DeleteSession, DeleteSessionParticipant,
    Friendship, FriendshipService, FriendRequest, GetGroup, GetSession, GroupDetail, GroupService,
    GroupSummary, Invitation, InvitationAcceptResult, InvitationService, InvitationState,
    InvitationTargetType, ListGroups, ListBotFriendRequests, ListBotFriendships,
    ListSessionMessages, ListSessions, Page, RejectFriendRequest, DeleteBotFriendship,
    SessionCompletionResult,
    SessionDetail, SessionMessagePage, SessionMessageService, SessionParticipant, SessionService,
    SessionSummary, UpdateGroup, UpdateGroupParticipant, UpdateSession,
    UpdateSessionParticipant,
};
use serde_json::{Value, json};
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Shared test helpers (duplicated from group/session test files to keep each
// test target self-contained — see task note on shared test-support vs dup).
// ---------------------------------------------------------------------------

struct HeaderVerifier {
    caller: AuthenticatedCaller,
}

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(self.caller.clone())
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

fn caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: "tenant-a".into(),
        user: Some(AuthenticatedUserIdentity {
            id: "staff-1".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn caller_user_id(caller: &AuthenticatedCaller) -> &str {
    caller.user.as_ref().expect("User identity").id.as_str()
}

fn authenticated_request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-123")
        .body(Body::from(body.to_string()))
        .expect("request")
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

// ---------------------------------------------------------------------------
// Noop services for group / session / message / friendship (invitation tests
// never hit those routes).
// ---------------------------------------------------------------------------

struct NoopGroupService;

#[async_trait]
impl GroupService for NoopGroupService {
    async fn list_groups(
        &self,
        _command: ListGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        Err(ApplicationError::internal("group not configured"))
    }

    async fn create(&self, _command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("group not configured"))
    }

    async fn get(&self, _query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("group not configured"))
    }

    async fn update(&self, _command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("group not configured"))
    }

    async fn delete(&self, _command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("group not configured"))
    }

    async fn add_participant(
        &self,
        _command: AddGroupParticipant,
    ) -> Result<bcs_service_api::application::v1::Participant, ApplicationError> {
        Err(ApplicationError::internal("group not configured"))
    }

    async fn update_participant(
        &self,
        _command: UpdateGroupParticipant,
    ) -> Result<bcs_service_api::application::v1::Participant, ApplicationError> {
        Err(ApplicationError::internal("group not configured"))
    }

    async fn delete_participant(
        &self,
        _command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("group not configured"))
    }
}

struct NoopSessionService;

#[async_trait]
impl SessionService for NoopSessionService {
    async fn create(
        &self,
        _command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn list(&self, _command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn get(&self, _query: GetSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn update(&self, _command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn delete(&self, _command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn complete(
        &self,
        _command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn add_participant(
        &self,
        _command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn update_participant(
        &self,
        _command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn delete_participant(
        &self,
        _command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }
}

struct NoopSessionMessageService;

#[async_trait]
impl SessionMessageService for NoopSessionMessageService {
    async fn list(
        &self,
        _query: ListSessionMessages,
    ) -> Result<SessionMessagePage, ApplicationError> {
        Err(ApplicationError::internal("session messages not configured"))
    }
}

struct NoopFriendshipService;

#[async_trait]
impl FriendshipService for NoopFriendshipService {
    async fn list_bot_friendships(
        &self,
        _command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn delete_bot_friendship(
        &self,
        _command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn create_bot_friend_request(
        &self,
        _command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn list_bot_friend_requests(
        &self,
        _command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn accept_friend_request(
        &self,
        _command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn reject_friend_request(
        &self,
        _command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }
}

// ---------------------------------------------------------------------------
// Fake invitation service.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct FakeInvitationService {
    created_group: Mutex<Option<CreateGroupInvitation>>,
    created_session: Mutex<Option<CreateSessionInvitation>>,
    accepted: Mutex<Option<AcceptInvitation>>,
}

#[async_trait]
impl InvitationService for FakeInvitationService {
    async fn create_group_invitation(
        &self,
        command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        *self.created_group.lock().expect("create group lock") = Some(command.clone());
        Ok(invitation(
            InvitationTargetType::Group,
            &command.group_id,
        ))
    }

    async fn create_session_invitation(
        &self,
        command: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        *self.created_session.lock().expect("create session lock") = Some(command.clone());
        Ok(invitation(
            InvitationTargetType::Session,
            &command.session_id,
        ))
    }

    async fn accept_invitation(
        &self,
        command: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        *self.accepted.lock().expect("accept lock") = Some(command.clone());
        Ok(InvitationAcceptResult {
            target_type: InvitationTargetType::Group,
            target_id: "group-1".into(),
            joined: true,
            already_joined: None,
        })
    }
}

// ---------------------------------------------------------------------------
// Canned data.
// ---------------------------------------------------------------------------

fn invitation(target_type: InvitationTargetType, target_id: &str) -> Invitation {
    Invitation {
        token: "token-1".into(),
        target_type,
        target_id: target_id.into(),
        state: InvitationState::Pending,
        expires_at: Some(999),
        created_at: 1,
    }
}

fn test_router(service: Arc<FakeInvitationService>) -> axum::Router {
    router(ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        service,
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier {
            caller: caller(),
        }),
    ))
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn create_group_invitation_returns_created_and_forwards_principal() {
    let service = Arc::new(FakeInvitationService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups/group-1/invitations",
            json!({"expires_in_seconds": 3600}),
        ))
        .await
        .expect("create group invitation response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["message"], "Created");
    assert_eq!(body["request_id"], "request-123");
    assert_eq!(body["data"]["token"], "token-1");
    assert_eq!(body["data"]["target_type"], "group");
    assert_eq!(body["data"]["target_id"], "group-1");
    assert_eq!(body["data"]["state"], "pending");
    assert_eq!(body["data"]["expires_at"], 999);
    {
        let created = service.created_group.lock().expect("create group lock");
        let created = created.as_ref().expect("create group command");
        assert_eq!(caller_user_id(&created.caller), "staff-1");
        assert_eq!(created.group_id, "group-1");
        assert_eq!(created.expires_in_seconds, Some(3600));
    }
}

#[tokio::test]
async fn create_group_invitation_allows_omitted_expires_in_seconds() {
    let service = Arc::new(FakeInvitationService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups/group-1/invitations",
            json!({}),
        ))
        .await
        .expect("empty body response");
    assert_eq!(response.status(), StatusCode::CREATED);
    {
        let created = service.created_group.lock().expect("create group lock");
        let created = created.as_ref().expect("create group command");
        assert_eq!(created.expires_in_seconds, None);
    }
}

#[tokio::test]
async fn create_group_invitation_rejects_zero_expires_in_seconds() {
    // Contract declares `minimum: 1`; `Some(0)` must be rejected at the DTO
    // layer (400 `invalid_request`) rather than forwarded to the facade.
    let service = Arc::new(FakeInvitationService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups/group-1/invitations",
            json!({"expires_in_seconds": 0}),
        ))
        .await
        .expect("zero ttl response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(
        service
            .created_group
            .lock()
            .expect("create group lock")
            .is_none(),
        "facade must not be called when DTO validation fails"
    );
}

#[tokio::test]
async fn create_group_invitation_allows_one_second_expires_in_seconds() {
    let service = Arc::new(FakeInvitationService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups/group-1/invitations",
            json!({"expires_in_seconds": 1}),
        ))
        .await
        .expect("one second ttl response");
    assert_eq!(response.status(), StatusCode::CREATED);
    {
        let created = service.created_group.lock().expect("create group lock");
        let created = created.as_ref().expect("create group command");
        assert_eq!(created.expires_in_seconds, Some(1));
    }
}

#[tokio::test]
async fn create_session_invitation_returns_created_and_forwards_principal() {
    let service = Arc::new(FakeInvitationService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/sessions/session-1/invitations",
            json!({}),
        ))
        .await
        .expect("create session invitation response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["message"], "Created");
    assert_eq!(body["data"]["token"], "token-1");
    assert_eq!(body["data"]["target_type"], "session");
    assert_eq!(body["data"]["target_id"], "session-1");
    {
        let created = service.created_session.lock().expect("create session lock");
        let created = created.as_ref().expect("create session command");
        assert_eq!(caller_user_id(&created.caller), "staff-1");
        assert_eq!(created.session_id, "session-1");
    }
}

#[tokio::test]
async fn accept_invitation_returns_ok_and_forwards_principal() {
    let service = Arc::new(FakeInvitationService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/invitations/token-1/accept",
            json!({}),
        ))
        .await
        .expect("accept invitation response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["data"]["target_type"], "group");
    assert_eq!(body["data"]["target_id"], "group-1");
    assert_eq!(body["data"]["joined"], true);
    {
        let accepted = service.accepted.lock().expect("accept lock");
        let accepted = accepted.as_ref().expect("accept command");
        assert_eq!(caller_user_id(&accepted.caller), "staff-1");
        assert_eq!(accepted.token, "token-1");
    }
}

#[tokio::test]
async fn accept_invitation_allows_empty_body() {
    // V1 pivot Vcj6H: `bot_uuid` was removed from `AcceptInvitationRequest`;
    // the body is now an empty object. `deny_unknown_fields` still rejects any
    // supplied field (including a stray `bot_uuid`) with 400 invalid_request.
    let service = Arc::new(FakeInvitationService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/invitations/token-1/accept",
            json!({}),
        ))
        .await
        .expect("empty accept response");
    assert_eq!(response.status(), StatusCode::OK);
    {
        let accepted = service.accepted.lock().expect("accept lock");
        let accepted = accepted.as_ref().expect("accept command");
        assert_eq!(accepted.token, "token-1");
    }
}

#[tokio::test]
async fn unknown_fields_rejected_with_invalid_request() {
    let service = Arc::new(FakeInvitationService::default());
    let app = test_router(service.clone());

    let response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups/group-1/invitations",
            json!({"expires_in_seconds": 3600, "extra": 1}),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(service
        .created_group
        .lock()
        .expect("create group lock")
        .is_none());

    // Vcj6H: a stray `bot_uuid` is now an unknown field and must be rejected
    // at the DTO layer (the facade never sees it).
    let accept_response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/invitations/token-1/accept",
            json!({"bot_uuid": "bot-2"}),
        ))
        .await
        .expect("unknown accept field response");
    assert_eq!(accept_response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(accept_response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(
        service
            .accepted
            .lock()
            .expect("accept lock")
            .is_none(),
        "facade must not be called when DTO validation fails"
    );

    let extra_response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/invitations/token-1/accept",
            json!({"extra": 1}),
        ))
        .await
        .expect("arbitrary unknown field response");
    assert_eq!(extra_response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(extra_response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
}

#[tokio::test]
async fn missing_principal_returns_unauthenticated() {
    let service = Arc::new(FakeInvitationService::default());
    let app = test_router(service);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/openapi/v1/groups/group-1/invitations")
                .header("content-type", "application/json")
                .body(Body::from(json!({}).to_string()))
                .expect("request"),
        )
        .await
        .expect("missing auth response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "unauthenticated");
}
