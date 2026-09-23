//! Shared fixtures for `friendship_routes` V1 tests (Task 7 split).
pub use std::sync::{Arc, Mutex};

pub use async_trait::async_trait;
pub use axum::body::{Body, to_bytes};
pub use axum::http::{HeaderMap, Request, StatusCode};
pub use bcs_api_http::{
    ApiState, AuthenticationContext, CredentialKind, PrincipalVerificationError, PrincipalVerifier,
    VerifiedRequestIdentity, router,
};
pub use bcs_service_api::RequestAuthHeaders;
pub use bcs_service_api::application::v1::{
    AuthFlowReply, AuthProviderUrl, AuthenticatedUserQuery, PresentedSession,
};
pub use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptFriendConnectionRequest, AcceptInvitation, AddGroupParticipant,
    AddSessionParticipant, ApplicationError, AuthProviderUrlList, AuthRedirect,
    AuthService as ApplicationAuthService, AuthUserInfo, AuthenticatedCaller,
    AuthenticatedUserIdentity, BotRegistration, BuildLoginUrls, CancelFriendConnectionRequest,
    CompleteOAuthLogin, CompleteSession, CreateBotFriendRequest, CreateFriendConnectionRequest,
    CreateGroup, CreateGroupInvitation, CreateSession, CreateSessionInvitation,
    CreateSessionOutcome, DeleteBotFriendship, DeleteFriendConnection, DeleteGroup,
    DeleteGroupParticipant, DeleteResult, DeleteSession, DeleteSessionParticipant, Friendship,
    FriendshipService, FriendConnectionActor, FriendConnectionActorType,
    FriendConnectionCreateResult, FriendConnectionCreateStatus, FriendConnectionPage,
    FriendConnectionRequestDirection, FriendConnectionRequestPage,
    FriendConnectionRequestStatus, FriendConnectionRequestView, FriendConnectionService,
    FriendConnectionView, FriendRequest, FriendRequestDirection, FriendRequestStatus, GetGroup,
    GetSession, GroupDetail, GroupService, GroupSummary, Invitation, InvitationAcceptResult,
    InvitationService, IssueRegisterToken, ListBotFriendRequests, ListBotFriendships,
    ListFriendConnectionRequests, ListFriendConnections, ListGroups, ListSessionMessages,
    ListSessions, Page,
    RegisterBot, RegisterService, RegisterTokenView, RejectFriendConnectionRequest,
    RejectFriendRequest, SessionCompletionResult, SessionDetail, SessionMessageService,
    SessionParticipant, SessionRenewal, SessionService, SessionSummary, UpdateGroup,
    UpdateGroupParticipant, UpdateSession, UpdateSessionParticipant,
};
pub use serde_json::{Value, json};
pub use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Shared test helpers (duplicated from group/session test files to keep each
// test target self-contained — see task note on shared test-support vs dup).
// ---------------------------------------------------------------------------

pub struct HeaderVerifier {
    pub caller: AuthenticatedCaller,
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
            Ok(VerifiedRequestIdentity {
                caller: self.caller.clone(),
                authentication_context: AuthenticationContext {
                    source: "test".to_string(),
                    credential_kind: CredentialKind::GatewayPrincipalHeader,
                },
                display: Default::default(),
            })
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

pub fn caller() -> AuthenticatedCaller {
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

pub fn caller_user_id(caller: &AuthenticatedCaller) -> &str {
    caller.user.as_ref().expect("User identity").id.as_str()
}

pub fn authenticated_request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-123")
        .body(Body::from(body.to_string()))
        .expect("request")
}

pub async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

// ---------------------------------------------------------------------------
// Noop services for group / session / message / invitation (friendship tests
// never hit those routes).
// ---------------------------------------------------------------------------

pub struct NoopGroupService;

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

pub struct NoopSessionService;

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

pub struct NoopSessionMessageService;

#[async_trait]
impl SessionMessageService for NoopSessionMessageService {
    async fn list(
        &self,
        _query: ListSessionMessages,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Err(ApplicationError::internal("session messages not configured"))
    }
}

pub struct NoopInvitationService;

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

pub struct NoopRegisterService;

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
pub struct FakeFriendshipService {
    pub listed_friendships: Mutex<Option<ListBotFriendships>>,
    pub removed_friendship: Mutex<Option<DeleteBotFriendship>>,
    pub created_friend_request: Mutex<Option<CreateBotFriendRequest>>,
    pub listed_friend_requests: Mutex<Option<ListBotFriendRequests>>,
    pub accepted_friend_request: Mutex<Option<AcceptFriendRequest>>,
    pub rejected_friend_request: Mutex<Option<RejectFriendRequest>>,
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

pub struct FakeFriendConnectionService {
    pub created_request: Mutex<Option<CreateFriendConnectionRequest>>,
    pub listed_requests: Mutex<Option<ListFriendConnectionRequests>>,
    pub accepted_request: Mutex<Option<AcceptFriendConnectionRequest>>,
    pub rejected_request: Mutex<Option<RejectFriendConnectionRequest>>,
    pub cancelled_request: Mutex<Option<CancelFriendConnectionRequest>>,
    pub listed_connections: Mutex<Option<ListFriendConnections>>,
    pub deleted_connection: Mutex<Option<DeleteFriendConnection>>,
    pub create_result: Mutex<FriendConnectionCreateResult>,
    pub request_page: Mutex<FriendConnectionRequestPage>,
    pub request_view: Mutex<FriendConnectionRequestView>,
    pub connection_page: Mutex<FriendConnectionPage>,
    pub delete_result: Mutex<DeleteResult>,
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

pub fn friend_connection_create_result() -> FriendConnectionCreateResult {
    FriendConnectionCreateResult {
        request_ids: vec!["1".to_string()],
        edge_ids: vec![11],
        status: FriendConnectionCreateStatus::Pending,
        auto_accepted: false,
    }
}

pub fn friend_connection_request_view() -> FriendConnectionRequestView {
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

pub fn friend_connection_request_page() -> FriendConnectionRequestPage {
    FriendConnectionRequestPage {
        items: vec![friend_connection_request_view()],
        total: 1,
        page: 1,
        page_size: 20,
    }
}

pub fn friend_connection_page() -> FriendConnectionPage {
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

pub fn friendship() -> Friendship {
    Friendship {
        bot_uuid: "bot-1".into(),
        friend_bot_uuid: "bot-2".into(),
        created_at: 10,
    }
}

pub fn friend_request(status: FriendRequestStatus) -> FriendRequest {
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

pub fn decision_result(request_id: String, status: FriendRequestStatus) -> FriendRequest {
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

pub fn test_router(service: Arc<FakeFriendshipService>) -> axum::Router {
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
// Tests.
// ---------------------------------------------------------------------------


pub fn openapi_test_router(service: Arc<FakeFriendConnectionService>) -> axum::Router {
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

#[derive(Default)]
pub struct CapturingAuthService {
    pub current_user_request: Mutex<Option<AuthenticatedUserQuery>>,
}

#[async_trait]
impl ApplicationAuthService for CapturingAuthService {
    async fn login_urls(&self, _command: BuildLoginUrls) -> AuthFlowReply<Vec<AuthProviderUrl>> {
        AuthFlowReply {
            result: Err(ApplicationError::internal("unused")),
            cookie_changes: Vec::new(),
        }
    }

    async fn complete_login(&self, _command: CompleteOAuthLogin) -> AuthFlowReply<AuthRedirect> {
        AuthFlowReply {
            result: Err(ApplicationError::internal("unused")),
            cookie_changes: Vec::new(),
        }
    }

    async fn current_user(
        &self,
        query: AuthenticatedUserQuery,
    ) -> Result<AuthUserInfo, ApplicationError> {
        *self
            .current_user_request
            .lock()
            .expect("current user request lock") = Some(query);
        Ok(AuthUserInfo {
            user_id: "staff-1".to_string(),
            name: Some("alice".to_string()),
            provider: "chain".to_string(),
            avatar: None,
        })
    }

    async fn refresh_session(
        &self,
        _command: PresentedSession,
    ) -> AuthFlowReply<SessionRenewal> {
        AuthFlowReply {
            result: Err(ApplicationError::internal("unused")),
            cookie_changes: Vec::new(),
        }
    }

    async fn logout(&self, _command: Option<PresentedSession>) -> AuthFlowReply<()> {
        AuthFlowReply {
            result: Ok(()),
            cookie_changes: Vec::new(),
        }
    }
}
