use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::v1::openapi::SessionFileUrlProjector;
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, BotRegistration, CreateBotFriendRequest,
    CreateGroupInvitation, CreateSessionInvitation, DeleteBotFriendship, FriendRequest,
    Friendship, FriendshipService, Invitation, InvitationAcceptResult, InvitationService,
    IssueRegisterToken, ListBotFriendRequests, ListBotFriendships, RegisterBot, RegisterService,
    RegisterTokenView, RejectFriendRequest,
};
use bcs_service_api::application::v1::{
    AddGroupParticipant, AddSessionParticipant, ApplicationError, AuthenticatedAppIdentity,
    AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity, CollectSession,
    CompleteSession, CreateGroup, CreateSession, CreateSessionOutcome, DeleteGroup,
    DeleteGroupParticipant, DeleteResult, DeleteSession, DeleteSessionParticipant, GetGroup,
    GetSession, GroupDetail, GroupService, GroupSummary, ListGroups, ListSessionMessages,
    ListSessions, Page, SessionCollectionResult, SessionCompletionResult, SessionDetail,
    SessionMessageService, SessionParticipant, SessionService, SessionStatus, SessionSummary,
    UncollectSession, UpdateGroup, UpdateGroupParticipant, UpdateSession, UpdateSessionParticipant,
};
use bcs_service_api::types::{AttachmentType, MessageAttachment};
use bcs_service_api::{
    ActorKind, DeliveryType, GroupMessage, GroupMessageType, MessageHistoryOptions, MessageRole,
    ParticipantMode, ParticipantRole, SessionKind,
};
use bcs_storage_api::byte_stream_from_bytes;
use bytes::Bytes;
use futures::StreamExt;
use serde_json::{Value, json};
use tower::ServiceExt;

struct FakeSessionFileService;

fn session_file_view() -> bcs_service_api::application::v1::SessionFileView {
    use bcs_service_api::application::v1::{
        SessionFileActor, SessionFileActorKind, SessionFileStatus, SessionFileView,
    };
    SessionFileView {
        file_id: "file-1".into(),
        session_id: "session-1".into(),
        file_name: "report.txt".into(),
        mime_type: "text/plain".into(),
        size: 42,
        sha256: None,
        owner: SessionFileActor {
            actor_kind: SessionFileActorKind::Human,
            actor_id: "human_staff-1".into(),
        },
        storage_backend: "local".into(),
        status: SessionFileStatus::Ready,
        created_at: 1,
        updated_at: 2,
    }
}

#[async_trait]
impl bcs_service_api::application::v1::SessionFileApplicationService for FakeSessionFileService {
    async fn prepare(
        &self,
        _command: bcs_service_api::application::v1::PrepareSessionFile,
    ) -> Result<bcs_service_api::application::v1::PrepareSessionFileResult, ApplicationError> {
        Ok(bcs_service_api::application::v1::PrepareSessionFileResult {
            file: session_file_view(),
            upload_target: json!({
                "mode": "single",
                "method": "PUT",
                "upload_url": "http://legacy.test/sessions/session-1/files/file-1/content",
                "expires_at": 3600
            }),
            expires_at: 3600,
            proxy_upload: true,
        })
    }

    async fn upload_content(
        &self,
        mut command: bcs_service_api::application::v1::UploadSessionFileContent,
    ) -> Result<bcs_service_api::application::v1::UploadSessionFileResult, ApplicationError> {
        let mut body = Vec::new();
        while let Some(chunk) = command.body.next().await {
            body.extend_from_slice(&chunk.expect("request body chunk"));
        }
        assert_eq!(body, b"abc");
        Ok(bcs_service_api::application::v1::UploadSessionFileResult {
            file_id: command.file_id,
            status: bcs_service_api::application::v1::SessionFileStatus::Pending,
        })
    }

    async fn complete(
        &self,
        _command: bcs_service_api::application::v1::CompleteSessionFile,
    ) -> Result<bcs_service_api::application::v1::SessionFileView, ApplicationError> {
        Ok(session_file_view())
    }

    async fn delete(
        &self,
        _command: bcs_service_api::application::v1::DeleteSessionFile,
    ) -> Result<DeleteResult, ApplicationError> {
        Ok(DeleteResult { deleted: true })
    }

    async fn get(
        &self,
        _command: bcs_service_api::application::v1::GetSessionFile,
    ) -> Result<bcs_service_api::application::v1::SessionFileView, ApplicationError> {
        Ok(session_file_view())
    }

    async fn list(
        &self,
        _command: bcs_service_api::application::v1::ListSessionFiles,
    ) -> Result<bcs_service_api::application::v1::SessionFilePage, ApplicationError> {
        Ok(bcs_service_api::application::v1::SessionFilePage {
            items: vec![session_file_view()],
            total: 1,
        })
    }

    async fn download(
        &self,
        _command: bcs_service_api::application::v1::DownloadSessionFile,
    ) -> Result<bcs_service_api::application::v1::SessionFileContent, ApplicationError> {
        Ok(
            bcs_service_api::application::v1::SessionFileContent::Redirect {
                download_url: "https://storage.example.com/file-1".into(),
                expires_at: 3600,
            },
        )
    }

    async fn share(
        &self,
        _command: bcs_service_api::application::v1::ShareSessionFile,
    ) -> Result<bcs_service_api::application::v1::ShareSessionFileResult, ApplicationError> {
        Ok(bcs_service_api::application::v1::ShareSessionFileResult {
            share_token: "share-token".into(),
            expires_at: 3600,
        })
    }

    async fn download_shared(
        &self,
        command: bcs_service_api::application::v1::DownloadSharedSessionFile,
    ) -> Result<bcs_service_api::application::v1::SessionFileContent, ApplicationError> {
        match command.token.as_str() {
            "good-token" => {
                return Ok(
                    bcs_service_api::application::v1::SessionFileContent::Stream {
                        file: session_file_view(),
                        body: byte_stream_from_bytes(Bytes::from_static(b"abc")),
                        inline: command.show,
                    },
                );
            }
            "backend-error" => {
                return Err(ApplicationError::bad_gateway(
                    "storage_backend_unavailable",
                    "Storage backend is unavailable",
                ));
            }
            "internal-error" => {
                return Err(ApplicationError::internal("database unavailable"));
            }
            _ => {}
        }
        Err(ApplicationError::not_found(
            "shared_file_not_found",
            "Shared file was not found",
        ))
    }
}

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
// Fake session + message services.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct FakeSessionService {
    state_machine_run: Mutex<Option<bcs_service_api::StateMachineRunView>>,
    created: Mutex<Option<CreateSession>>,
    reuse_create: AtomicBool,
    listed: Mutex<Option<ListSessions>>,
    got: Mutex<Option<GetSession>>,
    updated: Mutex<Option<UpdateSession>>,
    deleted: Mutex<Option<DeleteSession>>,
    completed: Mutex<Option<CompleteSession>>,
    collected: Mutex<Option<CollectSession>>,
    uncollected: Mutex<Option<UncollectSession>>,
    collection_forbidden: AtomicBool,
    collection_not_found: AtomicBool,
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
        let mut session = session_detail();
        session.state_machine_run = self.state_machine_run.lock().unwrap().clone();
        session.state_machine_run_id = session.state_machine_run.as_ref().map(|view| view.run.run_id.clone());
        Ok(CreateSessionOutcome {
            session,
            created: !self.reuse_create.load(Ordering::Relaxed),
        })
    }

    async fn list(&self, command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        let offset = command.offset;
        let limit = command.limit;
        let view_bot_id = command.view_bot_id.clone();
        *self.listed.lock().expect("list lock") = Some(command);
        let mut summary = session_summary();
        // Mirror the real app: surface `collected` only for an explicitly
        // named view actor, so the route can be asserted to serialize it.
        summary.collected = view_bot_id.as_ref().map(|_| true);
        Ok(Page {
            items: vec![summary],
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

    async fn collect(
        &self,
        command: CollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        *self.collected.lock().expect("collect lock") = Some(command.clone());
        if self.collection_forbidden.load(Ordering::Relaxed) {
            return Err(ApplicationError::forbidden("collection forbidden"));
        }
        if self.collection_not_found.load(Ordering::Relaxed) {
            return Err(ApplicationError::not_found(
                "session_not_found",
                "Session was not found",
            ));
        }
        Ok(SessionCollectionResult {
            session_id: command.session_id,
            participant: command.participant,
            collected: true,
        })
    }

    async fn uncollect(
        &self,
        command: UncollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        *self.uncollected.lock().expect("uncollect lock") = Some(command.clone());
        Ok(SessionCollectionResult {
            session_id: command.session_id,
            participant: command.participant,
            collected: false,
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
            tags: Vec::new(),
            mode: ParticipantMode::Auto,
            message_view_scope: bcs_domain::MessageViewScope::Full,
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
        let actor_kind = if command.bot_uuid.starts_with("human_") {
            ActorKind::Human
        } else {
            ActorKind::Bot
        };
        Ok(SessionParticipant {
            actor_kind,
            actor_id: command.bot_uuid,
            name: None,
            role: ParticipantRole::Consultant,
            tags: Vec::new(),
            mode: command
                .mode
                .unwrap_or_else(|| ParticipantMode::default_for(actor_kind)),
            message_view_scope: command
                .message_view_scope
                .unwrap_or(bcs_domain::MessageViewScope::Full),
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
    options: Mutex<Option<MessageHistoryOptions>>,
}

#[async_trait]
impl SessionMessageService for FakeSessionMessageService {
    async fn list(
        &self,
        query: ListSessionMessages,
    ) -> Result<Vec<GroupMessage>, ApplicationError> {
        *self.listed.lock().expect("list messages lock") = Some(query.clone());
        Ok(rich_group_messages())
    }

    async fn list_with_options(
        &self,
        query: ListSessionMessages,
        options: MessageHistoryOptions,
    ) -> Result<Vec<GroupMessage>, ApplicationError> {
        *self.options.lock().expect("list messages options lock") = Some(options);
        self.list(query).await
    }
}

fn rich_group_messages() -> Vec<GroupMessage> {
    vec![GroupMessage {
        id: "msg-1".into(),
        timestamp: 1_786_590_000_000,
        sender: "bot-1".into(),
        content: "hello".into(),
        message_type: GroupMessageType::Bot,
        bot_name: Some("Worker".into()),
        role: MessageRole::Assistant,
        run_id: "run-1".into(),
        history_meta: Some(json!({"assistantAggregation": true})),
        metadata: Some(json!({"tool": "search"})),
        attachments: Some(vec![MessageAttachment {
            attachment_id: "att-1".into(),
            attachment_type: AttachmentType::Image,
            file_name: "result.png".into(),
            mime_type: Some("image/png".into()),
            size: Some(42),
            sha256: Some("abcd".into()),
            url: Some("https://download.example/result.png".into()),
            expires_at: Some(1_786_590_060),
        }]),
    }]
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
        kind: SessionKind::Chat,
        title: Some("Planning".into()),
        input: None,
        meta: None,
        participants: vec![session_participant()],
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 2,
        state_machine_run_id: None,
        state_machine_run: None,
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
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 2,
        collected: None,
    }
}

fn session_participant() -> SessionParticipant {
    SessionParticipant {
        actor_id: "bot-1".into(),
        actor_kind: ActorKind::Bot,
        name: Some("Bot 1".into()),
        role: ParticipantRole::Driver,
        tags: Vec::new(),
        mode: ParticipantMode::Auto,
        message_view_scope: bcs_domain::MessageViewScope::Full,
        joined_at: Some(1),
    }
}

fn test_session_router(
    session: Arc<FakeSessionService>,
    message: Arc<FakeSessionMessageService>,
) -> axum::Router {
    test_session_router_for_caller(session, message, caller())
}

fn test_session_router_for_caller(
    session: Arc<FakeSessionService>,
    message: Arc<FakeSessionMessageService>,
    authenticated_caller: AuthenticatedCaller,
) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            session,
            message,
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier {
                caller: authenticated_caller,
            }),
        )
        .with_session_file_service(
            Arc::new(FakeSessionFileService),
            SessionFileUrlProjector::new(
                "https://gateway.example.com/api/v1/collaboration".into(),
            )
                .expect("valid base"),
        ),
    )
}

#[path = "session_routes/files.rs"]
mod files;

#[path = "session_routes/lifecycle.rs"]
mod lifecycle;

#[path = "session_routes/messages_and_participants.rs"]
mod messages_and_participants;
