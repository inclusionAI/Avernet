use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::RequestAuthHeaders;
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptFriendConnectionRequest, AcceptInvitation, AddGroupParticipant,
    AddSessionParticipant, ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity,
    CancelFriendConnectionRequest, CompleteSession, CreateBotFriendRequest,
    CreateFriendConnectionRequest, CreateGroup, CreateGroupInvitation, CreateSession,
    CreateSessionInvitation, CreateSessionOutcome, DeleteFriendConnection,
    DeleteGroup, DeleteGroupParticipant, DeleteResult, DeleteSession, DeleteSessionParticipant,
    Friendship, FriendshipService, FriendConnectionActor, FriendConnectionActorType,
    FriendConnectionCreateResult, FriendConnectionCreateStatus, FriendConnectionPage,
    FriendConnectionRequestDirection, FriendConnectionRequestPage,
    FriendConnectionRequestStatus, FriendConnectionRequestView, FriendConnectionService,
    FriendConnectionView,
    FriendRequest, FriendRequestDirection, FriendRequestStatus, GetGroup, GetSession, GroupDetail,
    GroupService, GroupSummary, Invitation, InvitationAcceptResult, InvitationService, ListGroups,
    ListBotFriendRequests, ListBotFriendships, ListFriendConnectionRequests,
    ListFriendConnections, ListSessionMessages, ListSessions, Page, RejectFriendConnectionRequest,
    RejectFriendRequest, DeleteBotFriendship, SessionCompletionResult, SessionDetail,
    SessionMessageService, SessionParticipant, SessionService, SessionSummary, UpdateGroup,
    UpdateGroupParticipant, UpdateSession, UpdateSessionParticipant,
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
// Noop services for group / session / message / invitation (friendship tests
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
// Fake friend-connection service.
// ---------------------------------------------------------------------------

struct FakeFriendConnectionService {
    created_request: Mutex<Option<CreateFriendConnectionRequest>>,
    listed_requests: Mutex<Option<ListFriendConnectionRequests>>,
    accepted_request: Mutex<Option<AcceptFriendConnectionRequest>>,
    rejected_request: Mutex<Option<RejectFriendConnectionRequest>>,
    cancelled_request: Mutex<Option<CancelFriendConnectionRequest>>,
    listed_connections: Mutex<Option<ListFriendConnections>>,
    deleted_connection: Mutex<Option<DeleteFriendConnection>>,
    create_result: Mutex<FriendConnectionCreateResult>,
    request_page: Mutex<FriendConnectionRequestPage>,
    request_view: Mutex<FriendConnectionRequestView>,
    connection_page: Mutex<FriendConnectionPage>,
    delete_result: Mutex<DeleteResult>,
}

impl Default for FakeFriendConnectionService {
    fn default() -> Self {
        Self {
            created_request: Mutex::new(None),
            listed_requests: Mutex::new(None),
            accepted_request: Mutex::new(None),
            rejected_request: Mutex::new(None),
            cancelled_request: Mutex::new(None),
            listed_connections: Mutex::new(None),
            deleted_connection: Mutex::new(None),
            create_result: Mutex::new(friend_connection_create_result()),
            request_page: Mutex::new(friend_connection_request_page()),
            request_view: Mutex::new(friend_connection_request_view()),
            connection_page: Mutex::new(friend_connection_page()),
            delete_result: Mutex::new(DeleteResult { deleted: true }),
        }
    }
}


#[async_trait]
impl FriendConnectionService for FakeFriendConnectionService {
    async fn create_friend_connection_request(
        &self,
        command: CreateFriendConnectionRequest,
    ) -> Result<FriendConnectionCreateResult, ApplicationError> {
        *self.created_request.lock().expect("create request lock") = Some(command);
        Ok(self.create_result.lock().expect("create result lock").clone())
    }

    async fn list_friend_connection_requests(
        &self,
        command: ListFriendConnectionRequests,
    ) -> Result<FriendConnectionRequestPage, ApplicationError> {
        *self.listed_requests.lock().expect("list requests lock") = Some(command);
        Ok(self.request_page.lock().expect("request page lock").clone())
    }

    async fn accept_friend_connection_request(
        &self,
        command: AcceptFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError> {
        *self.accepted_request.lock().expect("accept request lock") = Some(command);
        Ok(self.request_view.lock().expect("request view lock").clone())
    }

    async fn reject_friend_connection_request(
        &self,
        command: RejectFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError> {
        *self.rejected_request.lock().expect("reject request lock") = Some(command);
        Ok(self.request_view.lock().expect("request view lock").clone())
    }

    async fn cancel_friend_connection_request(
        &self,
        command: CancelFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError> {
        *self.cancelled_request.lock().expect("cancel request lock") = Some(command);
        Ok(self.request_view.lock().expect("request view lock").clone())
    }

    async fn list_friend_connections(
        &self,
        command: ListFriendConnections,
    ) -> Result<FriendConnectionPage, ApplicationError> {
        *self.listed_connections.lock().expect("list connections lock") = Some(command);
        Ok(self.connection_page.lock().expect("connection page lock").clone())
    }

    async fn delete_friend_connection(
        &self,
        command: DeleteFriendConnection,
    ) -> Result<DeleteResult, ApplicationError> {
        *self.deleted_connection.lock().expect("delete connection lock") = Some(command);
        Ok(self.delete_result.lock().expect("delete result lock").clone())
    }
}

// ---------------------------------------------------------------------------
// Canned data.
// ---------------------------------------------------------------------------

fn friend_connection_create_result() -> FriendConnectionCreateResult {
    FriendConnectionCreateResult {
        request_ids: vec!["1".to_string()],
        edge_ids: vec![11],
        status: FriendConnectionCreateStatus::Pending,
        auto_accepted: false,
    }
}

fn friend_connection_request_view() -> FriendConnectionRequestView {
    FriendConnectionRequestView {
        request_id: "1".to_string(),
        edge_id: Some(11),
        from_actor: FriendConnectionActor {
            actor_type: FriendConnectionActorType::Bot,
            id: "bot-1".into(),
        },
        to_actor: FriendConnectionActor {
            actor_type: FriendConnectionActorType::Bot,
            id: "bot-2".into(),
        },
        message: Some("hi".into()),
        status: FriendConnectionRequestStatus::Pending,
        decision_reason: None,
        created_by: FriendConnectionActor {
            actor_type: FriendConnectionActorType::Bot,
            id: "bot-1".into(),
        },
        decided_by: None,
        decided_at: None,
    }
}

fn friend_connection_request_page() -> FriendConnectionRequestPage {
    FriendConnectionRequestPage {
        items: vec![friend_connection_request_view()],
        total: 1,
        page: 1,
        page_size: 20,
    }
}

fn friend_connection_page() -> FriendConnectionPage {
    FriendConnectionPage {
        items: vec![FriendConnectionView {
            actor: FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: "friend-bot".into(),
            },
            name: Some("Friend Bot".into()),
            summary: Some("friend summary".into()),
            is_online: true,
        }],
        total: 1,
    }
}

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
            caller: caller(),
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

fn openapi_test_router(service: Arc<FakeFriendConnectionService>) -> axum::Router {
    router(ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(FakeFriendshipService::default()),
        Arc::new(HeaderVerifier {
            caller: caller(),
        }),
    )
    .with_friend_connection_service(service))
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
