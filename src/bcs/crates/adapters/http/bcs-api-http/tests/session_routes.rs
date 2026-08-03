use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::{
    AddGroupParticipant, AddSessionParticipant, ApplicationError, AuthenticatedCaller,
    AuthenticatedUserIdentity, BotParticipantMode, CompleteSession, CreateGroup, CreateSession,
    CreateSessionOutcome,
    DeleteGroup, DeleteGroupParticipant, DeleteResult, DeleteSession, DeleteSessionParticipant,
    GetGroup, GetSession, GroupDetail, GroupService, GroupSummary, ListGroups,
    ListSessionMessages, ListSessions, MessageSenderKind, Page, SessionCompletionResult,
    SessionDetail, SessionMessage, SessionMessageKind, SessionMessagePage, SessionMessageService, SessionParticipant,
    SessionService, SessionStatus, SessionSummary, UpdateGroup, UpdateGroupParticipant,
    UpdateSession, UpdateSessionParticipant,
};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, CreateBotFriendRequest, CreateGroupInvitation,
    CreateSessionInvitation, Friendship, FriendshipService, FriendRequest, Invitation,
    InvitationAcceptResult, InvitationService, ListBotFriendRequests, ListBotFriendships,
    RejectFriendRequest, DeleteBotFriendship,
};
use bcs_service_api::{ActorKind, ParticipantMode, ParticipantRole};
use serde_json::{Value, json};
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Shared test helpers (duplicated from group_routes.rs to keep each test
// target self-contained — see task note on shared test-support vs duplicate).
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
// Noop group service (session tests never hit group routes).
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
// Fake session + message services.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct FakeSessionService {
    created: Mutex<Option<CreateSession>>,
    reuse_create: AtomicBool,
    listed: Mutex<Option<ListSessions>>,
    got: Mutex<Option<GetSession>>,
    updated: Mutex<Option<UpdateSession>>,
    deleted: Mutex<Option<DeleteSession>>,
    completed: Mutex<Option<CompleteSession>>,
    added_participant: Mutex<Option<AddSessionParticipant>>,
    updated_participant: Mutex<Option<UpdateSessionParticipant>>,
    removed_participant: Mutex<Option<DeleteSessionParticipant>>,
}

#[async_trait]
impl SessionService for FakeSessionService {
    async fn create(
        &self,
        command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        *self.created.lock().expect("create lock") = Some(command);
        Ok(CreateSessionOutcome {
            session: session_detail(),
            created: !self.reuse_create.load(Ordering::Relaxed),
        })
    }

    async fn list(&self, command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        let offset = command.offset;
        let limit = command.limit;
        *self.listed.lock().expect("list lock") = Some(command);
        Ok(Page {
            items: vec![session_summary()],
            total: 1,
            offset,
            limit,
        })
    }

    async fn get(&self, query: GetSession) -> Result<SessionDetail, ApplicationError> {
        *self.got.lock().expect("get lock") = Some(query);
        Ok(session_detail())
    }

    async fn update(&self, command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        *self.updated.lock().expect("update lock") = Some(command);
        Ok(session_detail())
    }

    async fn delete(&self, command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        *self.deleted.lock().expect("delete lock") = Some(command);
        Ok(DeleteResult { deleted: true })
    }

    async fn complete(
        &self,
        command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        *self.completed.lock().expect("complete lock") = Some(command);
        Ok(SessionCompletionResult {
            session_id: "session-1".into(),
            status: SessionStatus::Completed,
            completed_at: 3,
        })
    }

    async fn add_participant(
        &self,
        command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        *self.added_participant.lock().expect("add participant lock") = Some(command.clone());
        Ok(SessionParticipant {
            actor_id: command.bot_uuid,
            actor_kind: ActorKind::Bot,
            name: None,
            role: ParticipantRole::Consultant,
            mode: match command.mode {
                Some(BotParticipantMode::Muted) => ParticipantMode::Muted,
                Some(BotParticipantMode::Auto) | None => ParticipantMode::Auto,
            },
            joined_at: Some(1),
        })
    }

    async fn update_participant(
        &self,
        command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        *self
            .updated_participant
            .lock()
            .expect("update participant lock") = Some(command.clone());
        Ok(SessionParticipant {
            actor_id: command.bot_uuid,
            actor_kind: ActorKind::Bot,
            name: None,
            role: ParticipantRole::Consultant,
            mode: match command.mode {
                BotParticipantMode::Muted => ParticipantMode::Muted,
                BotParticipantMode::Auto => ParticipantMode::Auto,
            },
            joined_at: Some(1),
        })
    }

    async fn delete_participant(
        &self,
        command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        *self
            .removed_participant
            .lock()
            .expect("remove participant lock") = Some(command);
        Ok(DeleteResult { deleted: true })
    }
}

#[derive(Default)]
struct FakeSessionMessageService {
    listed: Mutex<Option<ListSessionMessages>>,
    /// Optional override returned by `list` to exercise non-default
    /// `next_cursor` / `has_more` combinations (VYQHI composite cursor).
    page_override: Mutex<Option<SessionMessagePage>>,
}

#[async_trait]
impl SessionMessageService for FakeSessionMessageService {
    async fn list(
        &self,
        query: ListSessionMessages,
    ) -> Result<SessionMessagePage, ApplicationError> {
        *self.listed.lock().expect("list messages lock") = Some(query.clone());
        if let Some(page) = self.page_override.lock().expect("page override lock").take() {
            return Ok(page);
        }
        Ok(SessionMessagePage {
            messages: vec![
                SessionMessage {
                    id: "msg-1".into(),
                    session_seq: 1,
                    sender_id: "bot-1".into(),
                    sender_type: MessageSenderKind::Bot,
                    kind: SessionMessageKind::Text,
                    content: "hello".into(),
                    created_at: 10,
                },
                SessionMessage {
                    id: "msg-2".into(),
                    session_seq: 2,
                    sender_id: "bot-2".into(),
                    sender_type: MessageSenderKind::Bot,
                    kind: SessionMessageKind::Text,
                    content: "world".into(),
                    created_at: 20,
                },
            ],
            // Cursor-based page shape: no total/offset/limit round-trip.
            next_cursor: None,
            has_more: false,
        })
    }
}

// ---------------------------------------------------------------------------
// Canned data.
// ---------------------------------------------------------------------------

fn session_detail() -> SessionDetail {
    SessionDetail {
        session_id: "session-1".into(),
        version: 1,
        group_id: "group-1".into(),
        status: SessionStatus::Running,
        title: Some("Planning".into()),
        input: None,
        participants: vec![session_participant()],
        created_at: 1,
        updated_at: 2,
    }
}

fn session_summary() -> SessionSummary {
    SessionSummary {
        session_id: "session-1".into(),
        version: 1,
        group_id: "group-1".into(),
        status: SessionStatus::Running,
        title: Some("Planning".into()),
        participant_count: Some(1),
        created_at: 1,
        updated_at: 2,
    }
}

fn session_participant() -> SessionParticipant {
    SessionParticipant {
        actor_id: "bot-1".into(),
        actor_kind: ActorKind::Bot,
        name: Some("Bot 1".into()),
        role: ParticipantRole::Driver,
        mode: ParticipantMode::Auto,
        joined_at: Some(1),
    }
}

fn test_session_router(
    session: Arc<FakeSessionService>,
    message: Arc<FakeSessionMessageService>,
) -> axum::Router {
    router(ApiState::new(
        Arc::new(NoopGroupService),
        session,
        message,
        Arc::new(NoopInvitationService),
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
async fn create_session_returns_created_and_forwards_principal() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({
                "driver_bot_uuid": "bot-1",
                "participants": [
                    {"bot_uuid": "bot-1", "mode": "auto"},
                    {"bot_uuid": "bot-2"}
                ],
                "title": "Planning",
                "input": {"query": "how to coordinate?"}
            }),
        ))
        .await
        .expect("create response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["message"], "Created");
    assert_eq!(body["request_id"], "request-123");
    assert_eq!(body["data"]["session_id"], "session-1");
    assert_eq!(body["data"]["group_id"], "group-1");
    {
        let created = session.created.lock().expect("create lock");
        let created = created.as_ref().expect("create command");
        assert_eq!(caller_user_id(&created.caller), "staff-1");
        assert_eq!(created.group_id, "group-1");
        assert_eq!(created.driver_bot_uuid, "bot-1");
        assert_eq!(created.title.as_deref(), Some("Planning"));
        assert_eq!(created.input.as_ref().unwrap().query.as_deref(), Some("how to coordinate?"));
        assert_eq!(created.participants.len(), 2);
        assert_eq!(created.participants[0].bot_uuid, "bot-1");
        assert_eq!(created.participants[0].mode, Some(BotParticipantMode::Auto));
        assert_eq!(created.participants[1].bot_uuid, "bot-2");
        assert_eq!(created.participants[1].mode, None);
    }
}

#[tokio::test]
async fn create_session_reused_returns_ok() {
    let session = Arc::new(FakeSessionService::default());
    session.reuse_create.store(true, Ordering::Relaxed);
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({
                "driver_bot_uuid": "bot-1",
                "participants": [{"bot_uuid": "bot-1"}]
            }),
        ))
        .await
        .expect("reused create response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
}

#[tokio::test]
async fn list_sessions_returns_page_and_forwards_filters() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups/group-1/sessions?view_bot_id=bot-1&offset=5&limit=10&status=running",
            Value::Null,
        ))
        .await
        .expect("list response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["data"]["items"][0]["session_id"], "session-1");
    assert_eq!(body["data"]["total"], 1);
    assert_eq!(body["data"]["offset"], 5);
    assert_eq!(body["data"]["limit"], 10);
    {
        let listed = session.listed.lock().expect("list lock");
        let listed = listed.as_ref().expect("list command");
        assert_eq!(caller_user_id(&listed.caller), "staff-1");
        assert_eq!(listed.view_bot_id.as_deref(), Some("bot-1"));
        assert_eq!(listed.group_id, "group-1");
        assert_eq!(listed.offset, 5);
        assert_eq!(listed.limit, 10);
        assert_eq!(listed.status, Some(SessionStatus::Running));
    }
}

#[tokio::test]
async fn get_session_returns_detail() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1",
            Value::Null,
        ))
        .await
        .expect("get response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["session_id"], "session-1");
    {
        let got = session.got.lock().expect("get lock");
        let got = got.as_ref().expect("get command");
        assert_eq!(caller_user_id(&got.caller), "staff-1");
        assert_eq!(got.session_id, "session-1");
    }
}

#[tokio::test]
async fn update_session_returns_updated_detail() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/collaboration/sessions/session-1",
            json!({"title": "Renamed"}),
        ))
        .await
        .expect("update response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["session_id"], "session-1");
    {
        let updated = session.updated.lock().expect("update lock");
        let updated = updated.as_ref().expect("update command");
        assert_eq!(caller_user_id(&updated.caller), "staff-1");
        assert_eq!(updated.session_id, "session-1");
        assert_eq!(updated.title.as_deref(), Some("Renamed"));
    }
}

#[tokio::test]
async fn delete_session_returns_deleted() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/collaboration/sessions/session-1",
            Value::Null,
        ))
        .await
        .expect("delete response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["deleted"], true);
    {
        let deleted = session.deleted.lock().expect("delete lock");
        let deleted = deleted.as_ref().expect("delete command");
        assert_eq!(caller_user_id(&deleted.caller), "staff-1");
        assert_eq!(deleted.session_id, "session-1");
    }
}

#[tokio::test]
async fn complete_session_returns_completion_result() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/completion",
            Value::Null,
        ))
        .await
        .expect("complete response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["session_id"], "session-1");
    assert_eq!(body["data"]["status"], "completed");
    assert_eq!(body["data"]["completed_at"], 3);
    {
        let completed = session.completed.lock().expect("complete lock");
        let completed = completed.as_ref().expect("complete command");
        assert_eq!(caller_user_id(&completed.caller), "staff-1");
        assert_eq!(completed.session_id, "session-1");
    }
}

#[tokio::test]
async fn list_session_messages_returns_cursor_page() {
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
    assert_eq!(body["data"]["messages"].as_array().unwrap().len(), 2);
    assert_eq!(body["data"]["has_more"], false);
    assert!(body["data"]["next_cursor"].is_null());
    {
        let listed = message.listed.lock().expect("list messages lock");
        let listed = listed.as_ref().expect("list messages command");
        assert_eq!(caller_user_id(&listed.caller), "staff-1");
        assert_eq!(listed.session_id, "session-1");
        assert_eq!(listed.before, None);
        assert_eq!(listed.limit, 50);
    }
}

#[tokio::test]
async fn list_session_messages_passes_opaque_before_cursor_through() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message.clone());

    // The composite cursor token is opaque to the route layer; it must be
    // passed straight through to the service unchanged.
    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?before=1234567890:42&limit=10",
            Value::Null,
        ))
        .await
        .expect("list messages response");
    assert_eq!(response.status(), StatusCode::OK);
    {
        let listed = message.listed.lock().expect("list messages lock");
        let listed = listed.as_ref().expect("list messages command");
        assert_eq!(listed.session_id, "session-1");
        assert_eq!(listed.before.as_deref(), Some("1234567890:42"));
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
async fn list_session_messages_surfaces_next_cursor_when_has_more() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    {
        // Configure the fake to return a non-default composite cursor page.
        let page = SessionMessagePage {
            messages: vec![SessionMessage {
                id: "msg-9".into(),
                session_seq: 9,
                sender_id: "bot-1".into(),
                sender_type: MessageSenderKind::Bot,
                kind: SessionMessageKind::Text,
                content: "later".into(),
                created_at: 9_000,
            }],
            next_cursor: Some("9000:9".to_string()),
            has_more: true,
        };
        *message.page_override.lock().expect("page override lock") = Some(page);
    }
    let app = test_session_router(session, message.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?limit=1",
            Value::Null,
        ))
        .await
        .expect("list messages response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["has_more"], true);
    assert_eq!(body["data"]["next_cursor"], "9000:9");
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
            json!({"bot_uuid": "bot-2", "mode": "muted"}),
        ))
        .await
        .expect("add participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["actor_id"], "bot-2");
    assert_eq!(body["data"]["mode"], "muted");
    {
        let added = session.added_participant.lock().expect("add participant lock");
        let added = added.as_ref().expect("add participant command");
        assert_eq!(caller_user_id(&added.caller), "staff-1");
        assert_eq!(added.session_id, "session-1");
        assert_eq!(added.bot_uuid, "bot-2");
        assert_eq!(added.mode, Some(BotParticipantMode::Muted));
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
        let updated = session.updated_participant.lock().expect("update participant lock");
        let updated = updated.as_ref().expect("update participant command");
        assert_eq!(caller_user_id(&updated.caller), "staff-1");
        assert_eq!(updated.session_id, "session-1");
        assert_eq!(updated.bot_uuid, "bot-2");
        assert_eq!(updated.mode, BotParticipantMode::Muted);
    }
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
        let removed = session.removed_participant.lock().expect("remove participant lock");
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
                "driver_bot_uuid": "bot-1",
                "participants": [{"bot_uuid": "bot-1"}],
                "extra": 1
            }),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
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
