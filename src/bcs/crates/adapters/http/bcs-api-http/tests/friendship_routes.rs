use std::collections::BTreeSet;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, AddGroupParticipant, AddSessionParticipant,
    ApplicationError, CompleteSession, CreateBotFriendRequest, CreateGroup, CreateGroupInvitation,
    CreateSession, CreateSessionInvitation, CreateSessionOutcome, DeleteGroup,
    DeleteGroupParticipant, DeleteResult, DeleteSession, DeleteSessionParticipant, Friendship,
    FriendshipService, FriendRequest, FriendRequestDirection, FriendRequestStatus, GetGroup,
    GetSession, GroupDetail, GroupService, GroupSummary, Invitation, InvitationAcceptResult,
    InvitationService, ListBotGroups, ListBotFriendRequests, ListBotFriendships, ListSessionMessages,
    ListSessions, Page, Principal, RejectFriendRequest, DeleteBotFriendship, SessionCompletionResult,
    SessionDetail, SessionMessage, SessionMessageService, SessionParticipant, SessionService,
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
    principal: Principal,
}

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(&self, headers: &HeaderMap) -> Result<Principal, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(self.principal.clone())
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

fn principal() -> Principal {
    Principal::bot("bot-1", "tenant-a", BTreeSet::new())
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
// Noop services for group / session / message / invitation (friendship tests
// never hit those routes).
// ---------------------------------------------------------------------------

struct NoopGroupService;

#[async_trait]
impl GroupService for NoopGroupService {
    async fn list_bot_groups(
        &self,
        _command: ListBotGroups,
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
    ) -> Result<Page<SessionMessage>, ApplicationError> {
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

// ---------------------------------------------------------------------------
// Fake friendship service.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct FakeFriendshipService {
    listed_friendships: Mutex<Option<ListBotFriendships>>,
    removed_friendship: Mutex<Option<DeleteBotFriendship>>,
    created_friend_request: Mutex<Option<CreateBotFriendRequest>>,
    listed_friend_requests: Mutex<Option<ListBotFriendRequests>>,
    accepted_friend_request: Mutex<Option<AcceptFriendRequest>>,
    rejected_friend_request: Mutex<Option<RejectFriendRequest>>,
}

#[async_trait]
impl FriendshipService for FakeFriendshipService {
    async fn list_bot_friendships(
        &self,
        command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        let offset = command.offset;
        let limit = command.limit;
        *self.listed_friendships.lock().expect("list friendships lock") = Some(command);
        Ok(Page {
            items: vec![friendship()],
            total: 1,
            offset,
            limit,
        })
    }

    async fn delete_bot_friendship(
        &self,
        command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        *self.removed_friendship.lock().expect("remove friendship lock") = Some(command);
        Ok(DeleteResult { deleted: true })
    }

    async fn create_bot_friend_request(
        &self,
        command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        *self
            .created_friend_request
            .lock()
            .expect("create friend request lock") = Some(command.clone());
        Ok(FriendRequest {
            request_id: "req-1".into(),
            from_bot_uuid: command.bot_uuid.clone(),
            to_bot_uuid: command.to_bot_uuid.clone(),
            status: FriendRequestStatus::Pending,
            message: None,
            created_at: 10,
            updated_at: 10,
        })
    }

    async fn list_bot_friend_requests(
        &self,
        command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        let offset = command.offset;
        let limit = command.limit;
        *self
            .listed_friend_requests
            .lock()
            .expect("list friend requests lock") = Some(command);
        Ok(Page {
            items: vec![friend_request(FriendRequestStatus::Pending)],
            total: 1,
            offset,
            limit,
        })
    }

    async fn accept_friend_request(
        &self,
        command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        let request_id = command.request_id.clone();
        *self
            .accepted_friend_request
            .lock()
            .expect("accept friend request lock") = Some(command);
        Ok(decision_result(request_id, FriendRequestStatus::Accepted))
    }

    async fn reject_friend_request(
        &self,
        command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        let request_id = command.request_id.clone();
        *self
            .rejected_friend_request
            .lock()
            .expect("reject friend request lock") = Some(command);
        Ok(decision_result(request_id, FriendRequestStatus::Rejected))
    }
}

// ---------------------------------------------------------------------------
// Canned data.
// ---------------------------------------------------------------------------

fn friendship() -> Friendship {
    Friendship {
        bot_uuid: "bot-1".into(),
        friend_bot_uuid: "bot-2".into(),
        created_at: 10,
    }
}

fn friend_request(status: FriendRequestStatus) -> FriendRequest {
    FriendRequest {
        request_id: "req-1".into(),
        from_bot_uuid: "bot-1".into(),
        to_bot_uuid: "bot-2".into(),
        status,
        message: Some("hi".into()),
        created_at: 10,
        updated_at: 20,
    }
}

fn decision_result(request_id: String, status: FriendRequestStatus) -> FriendRequest {
    FriendRequest {
        request_id,
        from_bot_uuid: "bot-1".into(),
        to_bot_uuid: "bot-2".into(),
        status,
        message: None,
        created_at: 10,
        updated_at: 20,
    }
}

fn test_router(service: Arc<FakeFriendshipService>) -> axum::Router {
    router(ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        service,
        Arc::new(HeaderVerifier {
            principal: principal(),
        }),
    ))
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn list_friendships_returns_page_and_forwards_principal() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/bots/collaboration/bot-1/friendships?offset=5&limit=10",
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
        assert_eq!(listed.principal.actor_id(), "bot-1");
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
            "/openapi/v1/bots/collaboration/bot-1/friendships",
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
            "/openapi/v1/bots/collaboration/bot-1/friendships/bot-2",
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
        assert_eq!(removed.principal.actor_id(), "bot-1");
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
            "/openapi/v1/bots/collaboration/bot-1/friend-requests",
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
        assert_eq!(created.principal.actor_id(), "bot-1");
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
            "/openapi/v1/bots/collaboration/bot-1/friend-requests?offset=3&limit=5&direction=sent&status=pending",
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
        assert_eq!(listed.principal.actor_id(), "bot-1");
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
            "/openapi/v1/bots/collaboration/bot-1/friend-requests",
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
            "/openapi/v1/friend-requests/req-1/accept",
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
        assert_eq!(accepted.principal.actor_id(), "bot-1");
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
            "/openapi/v1/friend-requests/req-1/reject",
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
        assert_eq!(rejected.principal.actor_id(), "bot-1");
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
            "/openapi/v1/bots/collaboration/bot-1/friend-requests",
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
            "/openapi/v1/bots/collaboration/bot-1/friendships?bogus=1",
            Value::Null,
        ))
        .await
        .expect("unknown query field response");
    assert_eq!(unknown_query.status(), StatusCode::BAD_REQUEST);
    let body = response_json(unknown_query).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
}

#[tokio::test]
async fn missing_principal_returns_unauthenticated() {
    let service = Arc::new(FakeFriendshipService::default());
    let app = test_router(service);

    let response = app
        .oneshot(
            Request::builder()
                .uri("/openapi/v1/bots/collaboration/bot-1/friendships")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("missing auth response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "unauthenticated");
}
