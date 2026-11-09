//! Shared fixtures for the friendship / friend-connection route tests.
//! Split from the former 1_329-line top-level target (2026-11 V1 API auth
//! plan, Task 7): fixtures here, behavioral read/write suites beside.

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request};
use bcs_api_http::{ApiState, CredentialKind, PrincipalVerificationError, PrincipalVerifier, VerifiedRequestIdentity, router};
use bcs_service_api::application::v1::{
    AcceptFriendConnectionRequest, AcceptFriendRequest, AcceptInvitation,
    AddGroupParticipant, AddSessionParticipant, ApplicationError, AuthenticatedCaller,
    AuthenticatedUserIdentity, BotRegistration, CancelFriendConnectionRequest,
    CompleteSession, CreateBotFriendRequest, CreateFriendConnectionRequest, CreateGroup,
    CreateGroupInvitation, CreateSession, CreateSessionInvitation, CreateSessionOutcome,
    DeleteBotFriendship, DeleteFriendConnection, DeleteGroup, DeleteGroupParticipant,
    DeleteResult, DeleteSession, DeleteSessionParticipant, Friendship, FriendshipService,
    FriendConnectionActor, FriendConnectionActorType, FriendConnectionCreateResult,
    FriendConnectionCreateStatus, FriendConnectionPage, FriendConnectionRequestPage,
    FriendConnectionRequestStatus, FriendConnectionRequestView, FriendConnectionService,
    FriendConnectionView, FriendRequest, FriendRequestStatus, GetGroup, GetSession,
    GroupDetail, GroupService, GroupSummary, Invitation, InvitationAcceptResult,
    InvitationService, IssueRegisterToken, ListBotFriendRequests, ListBotFriendships,
    ListFriendConnectionRequests, ListFriendConnections, ListGroups, ListSessionMessages,
    ListSessions, Page, RegisterBot, RegisterService, RegisterTokenView,
    RejectFriendConnectionRequest, RejectFriendRequest,
    SessionCompletionResult, SessionDetail, SessionMessageService, SessionParticipant,
    SessionService, SessionSummary, UpdateGroup, UpdateGroupParticipant, UpdateSession,
    UpdateSessionParticipant,
};
use serde_json::Value;

// ---------------------------------------------------------------------------
// Shared test helpers (duplicated from group/session test files to keep each
// test target self-contained — see task note on shared test-support vs dup).
// ---------------------------------------------------------------------------

pub(crate) struct HeaderVerifier {
    pub(crate) caller: AuthenticatedCaller,
}

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(VerifiedRequestIdentity::pin(self.caller.clone(), "test-double", CredentialKind::GatewayPrincipalHeader))
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

pub(crate) fn caller() -> AuthenticatedCaller {
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

pub(crate) fn caller_user_id(caller: &AuthenticatedCaller) -> &str {
    caller.user.as_ref().expect("User identity").id.as_str()
}

pub(crate) fn authenticated_request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-123")
        .body(Body::from(body.to_string()))
        .expect("request")
}

pub(crate) async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

// ---------------------------------------------------------------------------
// Noop services for group / session / message / invitation (friendship tests
// never hit those routes).
// ---------------------------------------------------------------------------

pub(crate) struct NoopGroupService;

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

pub(crate) struct NoopSessionService;

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

pub(crate) struct NoopSessionMessageService;

#[async_trait]
impl SessionMessageService for NoopSessionMessageService {
    async fn list(
        &self,
        _query: ListSessionMessages,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Err(ApplicationError::internal("session messages not configured"))
    }
}

pub(crate) struct NoopInvitationService;

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

pub(crate) struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(
        &self,
        _command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
    }

    async fn register_bot(
        &self,
        _command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
    }
}

// ---------------------------------------------------------------------------
// Fake friendship service.
// ---------------------------------------------------------------------------

#[derive(Default)]
pub(crate) struct FakeFriendshipService {
    pub(crate) listed_friendships: Mutex<Option<ListBotFriendships>>,
    pub(crate) removed_friendship: Mutex<Option<DeleteBotFriendship>>,
    pub(crate) created_friend_request: Mutex<Option<CreateBotFriendRequest>>,
    pub(crate) listed_friend_requests: Mutex<Option<ListBotFriendRequests>>,
    pub(crate) accepted_friend_request: Mutex<Option<AcceptFriendRequest>>,
    pub(crate) rejected_friend_request: Mutex<Option<RejectFriendRequest>>,
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

pub(crate) struct FakeFriendConnectionService {
    pub(crate) created_request: Mutex<Option<CreateFriendConnectionRequest>>,
    pub(crate) listed_requests: Mutex<Option<ListFriendConnectionRequests>>,
    pub(crate) accepted_request: Mutex<Option<AcceptFriendConnectionRequest>>,
    pub(crate) rejected_request: Mutex<Option<RejectFriendConnectionRequest>>,
    pub(crate) cancelled_request: Mutex<Option<CancelFriendConnectionRequest>>,
    pub(crate) listed_connections: Mutex<Option<ListFriendConnections>>,
    pub(crate) deleted_connection: Mutex<Option<DeleteFriendConnection>>,
    pub(crate) create_result: Mutex<FriendConnectionCreateResult>,
    pub(crate) request_page: Mutex<FriendConnectionRequestPage>,
    pub(crate) request_view: Mutex<FriendConnectionRequestView>,
    pub(crate) connection_page: Mutex<FriendConnectionPage>,
    pub(crate) delete_result: Mutex<DeleteResult>,
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

pub(crate) fn friend_connection_create_result() -> FriendConnectionCreateResult {
    FriendConnectionCreateResult {
        request_ids: vec!["1".to_string()],
        edge_ids: vec![11],
        status: FriendConnectionCreateStatus::Pending,
        auto_accepted: false,
    }
}

pub(crate) fn friend_connection_request_view() -> FriendConnectionRequestView {
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

pub(crate) fn friend_connection_request_page() -> FriendConnectionRequestPage {
    FriendConnectionRequestPage {
        items: vec![friend_connection_request_view()],
        total: 1,
        page: 1,
        page_size: 20,
    }
}

pub(crate) fn friend_connection_page() -> FriendConnectionPage {
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
        page: 1,
        page_size: 20,
    }
}

pub(crate) fn friendship() -> Friendship {
    Friendship {
        bot_uuid: "bot-1".into(),
        friend_bot_uuid: "bot-2".into(),
        created_at: 10,
    }
}

pub(crate) fn friend_request(status: FriendRequestStatus) -> FriendRequest {
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

pub(crate) fn decision_result(request_id: String, status: FriendRequestStatus) -> FriendRequest {
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

pub(crate) fn test_router(service: Arc<FakeFriendshipService>) -> axum::Router {
    router(ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        service,
        Arc::new(HeaderVerifier {
            caller: caller(),
        }),
    ))
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------

pub(crate) fn openapi_test_router(service: Arc<FakeFriendConnectionService>) -> axum::Router {
    router(ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(FakeFriendshipService::default()),
        Arc::new(HeaderVerifier {
            caller: caller(),
        }),
    )
    .with_friend_connection_service(service))
}
