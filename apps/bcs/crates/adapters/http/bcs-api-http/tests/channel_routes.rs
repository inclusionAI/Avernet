use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_domain::{BindingStatus, BindingTarget, ChannelBinding, ChannelType, ConversationSessionMap};
use bcs_service_api::application::channel::{
    ChannelInboundError, ChannelService, ChannelUseCaseError, CreateBindingCommand, InboundMessage,
    OutboundMessage,
};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, AddGroupParticipant, AddSessionParticipant,
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, BotRegistration,
    CompleteSession, CreateBotFriendRequest, CreateGroup, CreateGroupInvitation, CreateSession,
    CreateSessionInvitation, CreateSessionOutcome, DeleteBotFriendship, DeleteGroup,
    DeleteGroupParticipant, DeleteResult, DeleteSession, DeleteSessionParticipant, Friendship,
    FriendshipService, FriendRequest, GetGroup, GetSession, GroupDetail, GroupService,
    GroupSummary, Invitation, InvitationAcceptResult, InvitationService, IssueRegisterToken,
    ListBotFriendRequests, ListBotFriendships, ListGroups, ListSessionMessages, ListSessions, Page,
    RegisterBot, RegisterService, RegisterTokenView, RejectFriendRequest,
    SessionCompletionResult, SessionDetail, SessionMessageService, SessionParticipant,
    SessionService, SessionSummary, UpdateGroup, UpdateGroupParticipant, UpdateSession,
    UpdateSessionParticipant,
};
use serde_json::{Value, json};
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Principal + request helpers (duplicated per target to keep the test target
// self-contained — same idiom as invitation_routes.rs).
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

fn caller_no_user() -> AuthenticatedCaller {
    let mut value = caller();
    value.user = None;
    value
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

const BASE: &str = "/openapi/v1/collaboration/channels/bindings";

fn create_body() -> Value {
    json!({
        "channel_type": "dingtalk",
        "account_ref": "robot_1",
        "target": { "bot": { "bot_id": "b1" } },
        "outbound_visibility": "full_transcript",
        "config": { "robot_code": "x" }
    })
}

// ---------------------------------------------------------------------------
// Noop services for group / session / message / friendship / invitation
// (channel routes never touch them; they only satisfy ApiState::new).
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

    async fn get(&self, query: GetSession) -> Result<SessionDetail, ApplicationError> {
        use bcs_service_api::application::v1::{select_principal, IdentityPolicy, SessionKind, SessionStatus};
        select_principal(&query.caller, IdentityPolicy::HumanOrOwnedBot)?;
        match query.session_id.as_str() {
            "denied" => return Err(ApplicationError::forbidden("not a participant")),
            "missing" => return Err(ApplicationError::not_found("session_not_found", "missing")),
            "unavailable" => return Err(ApplicationError::internal("session read failed")),
            _ => {}
        }
        Ok(SessionDetail {
            session_id: query.session_id,
            version: 1, group_id: "group-1".into(), status: SessionStatus::Running,
            kind: SessionKind::Chat, title: None, input: None, meta: None,
            participants: vec![], caller_principal: None, created_by: None,
            created_at: 1, updated_at: 2, state_machine_run_id: None, state_machine_run: None,
        })
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

struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(
        &self,
        _: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }
    async fn create_session_invitation(
        &self,
        _: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }
    async fn accept_invitation(
        &self,
        _: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }
}

struct NoopRegisterService;

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
// Fake channel service.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct FakeChannel {
    created: Arc<Mutex<Option<CreateBindingCommand>>>,
    next_create_error: Arc<Mutex<Option<ChannelUseCaseError>>>,
    list_calls: Arc<Mutex<u32>>,
    conversation_calls: Mutex<Vec<(String, Option<ChannelType>)>>,
    conversation_mappings: Vec<ConversationSessionMap>,
    conversation_error: Mutex<Option<ChannelUseCaseError>>,
    set_status: Arc<Mutex<Option<(String, bool)>>>,
    deleted: Arc<Mutex<Option<String>>>,
}

#[async_trait]
impl ChannelService for FakeChannel {
    async fn handle_inbound(&self, _: InboundMessage) -> Result<(), ChannelInboundError> {
        Ok(())
    }

    async fn try_outbound(&self, _: OutboundMessage) -> Result<(), ChannelUseCaseError> {
        Ok(())
    }

    async fn create_binding(
        &self,
        cmd: CreateBindingCommand,
    ) -> Result<ChannelBinding, ChannelUseCaseError> {
        if let Some(error) = self.next_create_error.lock().expect("create error lock").take() {
            return Err(error);
        }
        *self.created.lock().expect("created lock") = Some(cmd.clone());
        Ok(ChannelBinding {
            id: "b-1".to_string(),
            channel_type: cmd.channel_type,
            account_ref: cmd.account_ref,
            target: cmd.target,
            group_chat_scope: cmd.group_chat_scope,
            outbound_visibility: cmd.outbound_visibility,
            env: String::new(),
            status: BindingStatus::Active,
            created_by: cmd.created_by,
            config: cmd.config,
        })
    }

    async fn list_bindings(&self) -> Result<Vec<ChannelBinding>, ChannelUseCaseError> {
        *self.list_calls.lock().expect("list lock") += 1;
        Ok(Vec::new())
    }

    async fn list_bindings_by_target(
        &self,
        _: BindingTarget,
        _: Option<ChannelType>,
    ) -> Result<Vec<ChannelBinding>, ChannelUseCaseError> {
        Ok(Vec::new())
    }

    async fn list_conversations_by_session(
        &self,
        session_id: &str,
        channel_type: Option<ChannelType>,
    ) -> Result<Vec<ConversationSessionMap>, ChannelUseCaseError> {
        self.conversation_calls.lock().unwrap().push((session_id.to_string(), channel_type));
        if let Some(error) = self.conversation_error.lock().unwrap().take() {
            return Err(error);
        }
        Ok(self.conversation_mappings.clone())
    }

    async fn set_binding_status(
        &self,
        id: &str,
        active: bool,
    ) -> Result<(), ChannelUseCaseError> {
        *self.set_status.lock().expect("set status lock") = Some((id.to_string(), active));
        Ok(())
    }

    async fn update_binding_config(
        &self,
        _: &str,
        _: Value,
    ) -> Result<(), ChannelUseCaseError> {
        Ok(())
    }

    async fn delete_binding(&self, id: &str) -> Result<(), ChannelUseCaseError> {
        *self.deleted.lock().expect("deleted lock") = Some(id.to_string());
        Ok(())
    }
}

fn test_router(service: Arc<dyn ChannelService>, caller: AuthenticatedCaller) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopSessionMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier { caller }),
        )
        .with_channel_service(service),
    )
}

fn fixture() -> Arc<FakeChannel> {
    Arc::new(FakeChannel::default())
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn create_binding_returns_201_envelope_and_records_human_caller() {
    let service = fixture();
    let app = test_router(service.clone(), caller());
    let response = app
        .oneshot(authenticated_request("POST", BASE, create_body()))
        .await
        .expect("create response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["message"], "Created");
    assert_eq!(body["data"]["id"], "b-1");
    let recorded = service
        .created
        .lock()
        .expect("created lock")
        .clone()
        .expect("command recorded");
    assert_eq!(recorded.created_by.as_deref(), Some("staff-1"));
}

#[tokio::test]
async fn create_binding_duplicate_maps_to_409_conflict() {
    let service = Arc::new(FakeChannel {
        next_create_error: Arc::new(Mutex::new(Some(ChannelUseCaseError::Conflict(
            "active binding already exists".to_string(),
        )))),
        ..FakeChannel::default()
    });
    let app = test_router(service, caller());
    let response = app
        .oneshot(authenticated_request("POST", BASE, create_body()))
        .await
        .expect("dup response");
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "channel_binding_conflict");
}

#[tokio::test]
async fn missing_principal_returns_401() {
    let app = test_router(fixture(), caller());
    let request = Request::builder()
        .method("POST")
        .uri(BASE)
        .header("content-type", "application/json")
        .body(Body::from(json!({}).to_string()))
        .expect("request");
    let response = app.oneshot(request).await.expect("no-auth response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn app_only_caller_is_rejected_as_403() {
    let app = test_router(fixture(), caller_no_user());
    let response = app
        .oneshot(authenticated_request("POST", BASE, create_body()))
        .await
        .expect("app-only response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "forbidden");
}

#[tokio::test]
async fn list_bindings_returns_200_page_envelope() {
    let service = fixture();
    let app = test_router(service.clone(), caller());
    let response = app
        .oneshot(authenticated_request("GET", BASE, json!({})))
        .await
        .expect("list response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert!(body["data"]["items"].is_array());
    assert_eq!(*service.list_calls.lock().expect("list lock"), 1);
}

#[tokio::test]
async fn update_binding_active_routes_to_set_binding_status() {
    let service = fixture();
    let app = test_router(service.clone(), caller());
    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            &format!("{BASE}/b-1"),
            json!({ "active": true }),
        ))
        .await
        .expect("patch response");
    assert_eq!(response.status(), StatusCode::OK);
    let recorded = service
        .set_status
        .lock()
        .expect("set status lock")
        .clone();
    assert_eq!(recorded, Some(("b-1".to_string(), true)));
}

#[tokio::test]
async fn update_binding_with_both_active_and_config_is_400() {
    let app = test_router(fixture(), caller());
    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            &format!("{BASE}/b-1"),
            json!({ "active": true, "config": {} }),
        ))
        .await
        .expect("both response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn update_binding_with_neither_is_400() {
    let app = test_router(fixture(), caller());
    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            &format!("{BASE}/b-1"),
            json!({}),
        ))
        .await
        .expect("neither response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn delete_binding_returns_200_and_records_id() {
    let service = fixture();
    let app = test_router(service.clone(), caller());
    let response = app
        .oneshot(authenticated_request("DELETE", &format!("{BASE}/b-1"), json!({})))
        .await
        .expect("delete response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        service.deleted.lock().expect("deleted lock").clone(),
        Some("b-1".to_string())
    );
}


const CONVERSATIONS: &str = "/openapi/v1/collaboration/channels/conversations/by-session";

fn conversation_fixture() -> Arc<FakeChannel> {
    Arc::new(FakeChannel {
        conversation_mappings: ["binding-1", "binding-2"].into_iter().map(|binding| ConversationSessionMap {
            binding_id: binding.into(), im_conversation_id: "cid-1".into(),
            im_conversation_type: "2".into(), session_scope: bcs_domain::SessionScope::Conversation,
            im_user_id: Some("private-user".into()), bcs_session_id: "session:1".into(),
            last_active_at: 123,
        }).collect(),
        ..FakeChannel::default()
    })
}

#[tokio::test]
async fn conversation_lookup_projects_all_mappings_and_normalizes_query() {
    let service = conversation_fixture();
    let response = test_router(service.clone(), caller()).oneshot(authenticated_request(
        "GET", &format!("{CONVERSATIONS}?bcs_session_id=%20session%3A1%20&channel_type=%20dingtalk%20"), json!(null),
    )).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20000);
    assert_eq!(body["request_id"], "request-123");
    let items = body["data"]["items"].as_array().unwrap();
    assert_eq!(items.len(), 2);
    assert_eq!(items[0]["conversation_id"], "cid-1");
    assert_eq!(items[0]["session_scope"], "conversation");
    assert_eq!(items[0]["last_active_at"], 123);
    assert!(items[0].get("im_user_id").is_none());
    assert_eq!(*service.conversation_calls.lock().unwrap(), vec![("session:1".into(), Some("dingtalk".into()))]);
}

#[tokio::test]
async fn conversation_lookup_accepts_bot_principal_and_empty_mappings() {
    let mut bot = caller_no_user();
    bot.bot = Some(bcs_service_api::application::v1::AuthenticatedBotIdentity {
        bot_uuid: "b1".into(), owner_id: "staff-1".into(), app_id: 1, agent_code: "agent-1".into(),
    });
    let service = fixture();
    let response = test_router(service.clone(), bot).oneshot(authenticated_request(
        "GET", &format!("{CONVERSATIONS}?bcs_session_id=session-1"), json!(null),
    )).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response_json(response).await["data"]["items"], json!([]));
    assert_eq!(*service.conversation_calls.lock().unwrap(), vec![("session-1".into(), None)]);
}

#[tokio::test]
async fn conversation_lookup_denials_never_query_mappings() {
    for (identity, session, status) in [
        (caller_no_user(), "session-1", StatusCode::FORBIDDEN),
        (caller(), "denied", StatusCode::FORBIDDEN),
        (caller(), "missing", StatusCode::NOT_FOUND),
        (caller(), "unavailable", StatusCode::INTERNAL_SERVER_ERROR),
    ] {
        let service = fixture();
        let response = test_router(service.clone(), identity).oneshot(authenticated_request(
            "GET", &format!("{CONVERSATIONS}?bcs_session_id={session}"), json!(null),
        )).await.unwrap();
        assert_eq!(response.status(), status);
        assert!(service.conversation_calls.lock().unwrap().is_empty());
    }
}

#[tokio::test]
async fn conversation_lookup_rejects_invalid_query_and_missing_principal() {
    for query in ["", "?bcs_session_id=%20", "?bcs_session_id=s&channel_type=%20", "?bcs_session_id=s&unknown=x"] {
        let service = fixture();
        let response = test_router(service.clone(), caller()).oneshot(authenticated_request(
            "GET", &format!("{CONVERSATIONS}{query}"), json!(null),
        )).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert!(service.conversation_calls.lock().unwrap().is_empty());
    }
    let service = fixture();
    let response = test_router(service.clone(), caller()).oneshot(Request::builder()
        .uri(format!("{CONVERSATIONS}?bcs_session_id=s")).body(Body::empty()).unwrap()
    ).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(service.conversation_calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn conversation_lookup_propagates_storage_failure() {
    let service = Arc::new(FakeChannel {
        conversation_error: Mutex::new(Some(ChannelUseCaseError::Internal(
            bcs_service_api::ServiceError::InternalError("storage unavailable".into()),
        ))),
        ..FakeChannel::default()
    });
    let response = test_router(service.clone(), caller()).oneshot(authenticated_request(
        "GET", &format!("{CONVERSATIONS}?bcs_session_id=s"), json!(null),
    )).await.unwrap();
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "internal_error");
    assert_eq!(service.conversation_calls.lock().unwrap().len(), 1);
}
