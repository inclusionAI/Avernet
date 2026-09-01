use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, AddGroupParticipant, AddSessionParticipant,
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, BotRegistration,
    CompleteSession, CreateBotFriendRequest, CreateGroup, CreateGroupInvitation,
    CreateSession, CreateSessionInvitation, CreateSessionOutcome, DeleteGroup,
    DeleteGroupParticipant, DeleteResult, DeleteSession, DeleteSessionParticipant,
    Friendship, FriendshipService, FriendRequest, GetGroup, GetSession, GroupDetail, GroupService,
    GroupSummary, Invitation, InvitationAcceptResult, InvitationService, IssueRegisterToken,
    ListGroups, ListBotFriendRequests, ListBotFriendships, ListSessionMessages, ListSessions, Page,
    RegisterBot, RegisterService, RegisterTokenView, RejectFriendRequest, DeleteBotFriendship,
    SessionCompletionResult,
    SessionDetail, SessionMessageService, SessionParticipant, SessionService,
    SessionSummary, UpdateGroup, UpdateGroupParticipant, UpdateSession,
    UpdateSessionParticipant,
};
use serde_json::{Value, json};
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Shared test helpers (duplicated from invitation_routes.rs to keep each
// test target self-contained).
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
        tenant: Some("tenant-a".into()),
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

fn bare_request(method: &str, uri: &str) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("x-request-id", "request-123")
        .body(Body::empty())
        .expect("request")
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

// ---------------------------------------------------------------------------
// Noop services for group / session / message / invitation / friendship
// (register tests never hit those routes). Copied from invitation_routes.rs.
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

    async fn collect(
        &self,
        _: bcs_service_api::application::v1::CollectSession,
    ) -> Result<bcs_service_api::application::v1::SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn uncollect(
        &self,
        _: bcs_service_api::application::v1::UncollectSession,
    ) -> Result<bcs_service_api::application::v1::SessionCollectionResult, ApplicationError> {
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
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Err(ApplicationError::internal("session messages not configured"))
    }
}

struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(
        &self,
        _command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }

    async fn create_session_invitation(
        &self,
        _command: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }

    async fn accept_invitation(
        &self,
        _command: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
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
// Fake register service.
// ---------------------------------------------------------------------------

struct FakeRegisterService {
    issued: Mutex<Vec<IssueRegisterToken>>,
    registered: Mutex<Vec<RegisterBot>>,
    /// when true, issue/register return a forbidden/unauthenticated error
    reject: bool,
}

#[async_trait]
impl RegisterService for FakeRegisterService {
    async fn issue_register_token(
        &self,
        command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        if self.reject {
            return Err(ApplicationError::forbidden("no human principal"));
        }
        self.issued.lock().expect("issued lock").push(command);
        Ok(RegisterTokenView {
            token: "reg-token-1".to_string(),
            expires_at: 123456,
            note: "Use this token for bot registration within 6 hours".to_string(),
        })
    }

    async fn register_bot(
        &self,
        command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        if self.reject {
            return Err(ApplicationError::Unauthenticated);
        }
        self.registered.lock().expect("registered lock").push(command.clone());
        Ok(BotRegistration {
            bot_name: command.bot_name,
            bot_uuid: "bot-new-1".to_string(),
            bot_token: "bot-token-new-1".to_string(),
        })
    }
}

fn test_router(service: Arc<FakeRegisterService>) -> axum::Router {
    router(ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        service,                          // register slot (5th service)
        Arc::new(NoopFriendshipService),  // friendship slot
        Arc::new(HeaderVerifier { caller: caller() }),
    ))
}

fn fake_service(reject: bool) -> Arc<FakeRegisterService> {
    Arc::new(FakeRegisterService {
        issued: Mutex::new(Vec::new()),
        registered: Mutex::new(Vec::new()),
        reject,
    })
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn get_register_token_returns_envelope_with_token_data() {
    let service = fake_service(false);
    let app = test_router(service.clone());
    let response = app
        .oneshot(authenticated_request("GET", "/openapi/v1/collaboration/register/token", json!({})))
        .await
        .expect("token issue response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["request_id"], "request-123");
    assert_eq!(body["data"]["token"], "reg-token-1");
    assert_eq!(body["data"]["expires_at"], 123456);
    let issued = service.issued.lock().expect("issued lock");
    assert_eq!(issued[0].caller.user.as_ref().expect("human").id, "staff-1");
}

#[tokio::test]
async fn get_register_token_maps_application_403() {
    // reject=true makes the fake return ApplicationError::Forbidden
    let service = fake_service(true);
    let app = test_router(service);
    let response = app
        .oneshot(authenticated_request("GET", "/openapi/v1/collaboration/register/token", json!({})))
        .await
        .expect("forbidden response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["code"], 40_300);
    assert_eq!(body["data"]["error_code"], "forbidden");
}

#[tokio::test]
async fn post_register_is_anonymous_and_returns_created_envelope() {
    let service = fake_service(false);
    let app = test_router(service.clone());
    // NO x-test-auth header — the public router must not require a principal.
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/register?token=abc123&bot-name=%E6%B5%8B%E8%AF%95%E6%9C%BA%E5%99%A8%E4%BA%BA")
                .header("x-request-id", "request-123")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("register response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["data"]["bot_name"], "测试机器人");
    assert_eq!(body["data"]["bot_uuid"], "bot-new-1");
    assert_eq!(body["data"]["bot_token"], "bot-token-new-1");
    let registered = service.registered.lock().expect("registered lock");
    assert_eq!(registered[0].token, "abc123");
    assert_eq!(registered[0].bot_name, "测试机器人");
}

#[tokio::test]
async fn post_register_rejects_missing_token_and_name() {
    let service = fake_service(false);
    let app = test_router(service);
    let response = app
        .clone()
        .oneshot(bare_request("POST", "/register"))
        .await
        .expect("missing params response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["code"], 40_000);

    let response = app
        .oneshot(bare_request("POST", "/register?token=abc123"))
        .await
        .expect("missing name response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn post_register_maps_unauthenticated_token_failures() {
    let service = fake_service(true);
    let app = test_router(service);
    let response = app
        .oneshot(bare_request("POST", "/register?token=abc123&bot_name=tester"))
        .await
        .expect("401 response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 40_100);
}