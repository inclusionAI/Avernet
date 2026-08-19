use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::v1::openapi::SessionFileUrlProjector;
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, CreateBotFriendRequest, CreateGroupInvitation,
    CreateSessionInvitation, DeleteBotFriendship, FriendRequest, Friendship, FriendshipService,
    Invitation, InvitationAcceptResult, InvitationService, ListBotFriendRequests,
    ListBotFriendships, RejectFriendRequest,
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
    ActorKind, DeliveryType, GroupMessage, GroupMessageType, MessageRole, ParticipantMode,
    ParticipantRole, SessionCaller, SessionKind,
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
        Ok(CreateSessionOutcome {
            session: session_detail(),
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
            actor_kind: if command.bot_uuid.starts_with("human_") {
                ActorKind::Human
            } else {
                ActorKind::Bot
            },
            actor_id: command.bot_uuid,
            name: None,
            role: ParticipantRole::Consultant,
            tags: Vec::new(),
            mode: command.mode,
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
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier {
                caller: authenticated_caller,
            }),
        )
        .with_session_file_service(
            Arc::new(FakeSessionFileService),
            SessionFileUrlProjector::new("https://gateway.example.com/api/v1/collaboration".into())
                .expect("valid base"),
        ),
    )
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn get_session_file_uses_the_v1_envelope() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files/file-1",
            json!(null),
        ))
        .await
        .expect("file response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["file_id"], "file-1");
    assert_eq!(body["data"]["status"], "ready");
}

#[tokio::test]
async fn prepare_session_file_projects_the_proxy_upload_url() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/api/v1/collaboration/sessions/session-1/files",
            json!({
                "file_name": "report.txt",
                "size": 42,
                "mime_type": "text/plain"
            }),
        ))
        .await
        .expect("prepare response");

    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["data"]["file_id"], "file-1");
    assert_eq!(
        body["data"]["upload_url"],
        "https://gateway.example.com/api/v1/collaboration/sessions/session-1/files/file-1/content"
    );
}

#[tokio::test]
async fn protected_session_file_mutations_and_download_follow_v1_statuses() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    let upload = Request::builder()
        .method("PUT")
        .uri("/api/v1/collaboration/sessions/session-1/files/file-1/content?part=1")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-upload")
        .body(Body::from("abc"))
        .expect("upload request");
    let response = app.clone().oneshot(upload).await.expect("upload response");
    assert_eq!(response.status(), StatusCode::ACCEPTED);

    for (method, path, expected) in [
        (
            "POST",
            "/api/v1/collaboration/sessions/session-1/files/file-1/complete",
            StatusCode::OK,
        ),
        (
            "DELETE",
            "/api/v1/collaboration/sessions/session-1/files/file-1",
            StatusCode::OK,
        ),
        (
            "GET",
            "/api/v1/collaboration/sessions/session-1/files",
            StatusCode::OK,
        ),
    ] {
        let response = app
            .clone()
            .oneshot(authenticated_request(method, path, json!(null)))
            .await
            .expect("file operation response");
        assert_eq!(response.status(), expected, "{method} {path}");
    }

    let share = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/api/v1/collaboration/sessions/session-1/files/file-1/share",
            json!({}),
        ))
        .await
        .expect("share response");
    assert_eq!(share.status(), StatusCode::CREATED);
    let share_body = response_json(share).await;
    assert_eq!(
        share_body["data"]["share_url"],
        "https://gateway.example.com/api/v1/collaboration/sessions/shared-file/content?token=share-token"
    );

    let download = app
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files/file-1/content",
            json!(null),
        ))
        .await
        .expect("download response");
    assert_eq!(download.status(), StatusCode::FOUND);
    assert_eq!(
        download.headers()["location"],
        "https://storage.example.com/file-1"
    );
}

#[tokio::test]
async fn shared_file_content_is_public_and_token_failures_are_uniform_not_found() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    for uri in [
        "/api/v1/collaboration/sessions/shared-file/content",
        "/api/v1/collaboration/sessions/shared-file/content?token=bad-token",
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(uri)
                    .header("x-request-id", "public-request")
                    .body(Body::empty())
                    .expect("public request"),
            )
            .await
            .expect("public response");
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{uri}");
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "shared_file_not_found");
    }

    let success = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(
                    "/api/v1/collaboration/sessions/shared-file/content?token=good-token&show=true",
                )
                .body(Body::empty())
                .expect("public success request"),
        )
        .await
        .expect("public success response");
    assert_eq!(success.status(), StatusCode::OK);
    assert_eq!(
        success.headers()["content-disposition"],
        "inline; filename=\"report.txt\""
    );
    assert_eq!(
        to_bytes(success.into_body(), usize::MAX).await.unwrap(),
        Bytes::from_static(b"abc")
    );

    let protected = app
        .oneshot(
            Request::builder()
                .uri("/api/v1/collaboration/sessions/session-1/files")
                .body(Body::empty())
                .expect("protected request"),
        )
        .await
        .expect("protected response");
    assert_eq!(protected.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn shared_file_content_preserves_infrastructure_failures() {
    let app = test_session_router(
        Arc::new(FakeSessionService::default()),
        Arc::new(FakeSessionMessageService::default()),
    );

    for (token, expected) in [
        ("backend-error", StatusCode::BAD_GATEWAY),
        ("internal-error", StatusCode::INTERNAL_SERVER_ERROR),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!(
                        "/api/v1/collaboration/sessions/shared-file/content?token={token}"
                    ))
                    .body(Body::empty())
                    .expect("public request"),
            )
            .await
            .expect("public response");
        assert_eq!(response.status(), expected, "{token}");
    }
}

#[tokio::test]
async fn session_file_routes_admit_bot_and_reject_mismatched_or_app_only_callers() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let bot = AuthenticatedBotIdentity {
        bot_uuid: "bot-1".into(),
        owner_id: "staff-1".into(),
        app_id: 1,
        agent_code: "agent".into(),
    };

    let bot_only = test_session_router_for_caller(
        session.clone(),
        message.clone(),
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: Some(bot.clone()),
            app: None,
            access_key: None,
        },
    );
    let response = bot_only
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files",
            Value::Null,
        ))
        .await
        .expect("Bot-only response");
    assert_eq!(response.status(), StatusCode::OK);

    let mismatched = test_session_router_for_caller(
        session.clone(),
        message.clone(),
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: caller().user,
            bot: Some(AuthenticatedBotIdentity {
                owner_id: "someone-else".into(),
                ..bot
            }),
            app: None,
            access_key: None,
        },
    );
    let response = mismatched
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files",
            Value::Null,
        ))
        .await
        .expect("mismatched User/Bot response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    let app_only = test_session_router_for_caller(
        session,
        message,
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: None,
            app: Some(AuthenticatedAppIdentity {
                app_id: 1,
                app_name: "test-app".into(),
                owners: "staff-1".into(),
                app_type: "service".into(),
            }),
            access_key: None,
        },
    );
    let response = app_only
        .oneshot(authenticated_request(
            "GET",
            "/api/v1/collaboration/sessions/session-1/files",
            Value::Null,
        ))
        .await
        .expect("App-only response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

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
                "title": "Planning",
                "kind": "service_invocation",
                "acting_bot_id": "bot-owned",
                "creator_role": "manager",
                "input": {"query": "how to coordinate?", "custom": {"n": 1}},
                "meta": {
                    "callback_target": {"baas_session_id": "baas-1"},
                    "channel": {"source": "dingtalk", "binding_id": "binding-1"}
                },
                "context_delivery": "inject"
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
        assert_eq!(
            created.caller,
            SessionCaller::Human {
                actor_id: "human_staff-1".into(),
                owner_id: "staff-1".into(),
                display_name: None,
            }
        );
        assert_eq!(created.group_id, "group-1");
        assert_eq!(created.title.as_deref(), Some("Planning"));
        assert_eq!(
            created.input,
            Some(json!({"query": "how to coordinate?", "custom": {"n": 1}}))
        );
        assert_eq!(created.kind, Some(SessionKind::ServiceInvocation));
        assert_eq!(created.acting_bot_id.as_deref(), Some("bot-owned"));
        assert_eq!(created.creator_role, Some(ParticipantRole::Manager));
        assert_eq!(created.context_delivery, Some(DeliveryType::Inject));
        assert_eq!(
            created.meta,
            Some(json!({
                "callback_target": {"baas_session_id": "baas-1"},
                "channel": {"source": "dingtalk", "binding_id": "binding-1"}
            }))
        );
    }
}

#[tokio::test]
async fn create_session_accepts_explicit_authenticated_human_actor() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({
                "acting_bot_id": "human_staff-1",
                "creator_role": "observer"
            }),
        ))
        .await
        .expect("explicit Human create response");
    assert_eq!(response.status(), StatusCode::CREATED);

    let created = session.created.lock().expect("create lock");
    let created = created.as_ref().expect("create command");
    assert_eq!(
        created.caller,
        SessionCaller::Human {
            actor_id: "human_staff-1".into(),
            owner_id: "staff-1".into(),
            display_name: None,
        }
    );
    assert_eq!(created.acting_bot_id.as_deref(), Some("human_staff-1"));
    assert_eq!(created.creator_role, Some(ParticipantRole::Observer));
}

#[tokio::test]
async fn create_session_accepts_bot_identity_and_raw_string_input() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router_for_caller(
        session.clone(),
        message,
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: Some(AuthenticatedBotIdentity {
                bot_uuid: "bot-1".into(),
                owner_id: "staff-1".into(),
                app_id: 1,
                agent_code: "agent-1".into(),
            }),
            app: None,
            access_key: None,
        },
    );

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({"input": "run this task"}),
        ))
        .await
        .expect("Bot create response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let created = session.created.lock().expect("create lock");
    let created = created.as_ref().expect("create command");
    assert_eq!(
        created.caller,
        SessionCaller::Bot {
            bot_uuid: "bot-1".into()
        }
    );
    assert_eq!(created.input, Some(json!("run this task")));
}

#[tokio::test]
async fn create_session_accepts_any_json_object_input() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({"input": {"query": 42, "custom": [true, null]}}),
        ))
        .await
        .expect("open object create response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let created = session.created.lock().expect("create lock");
    assert_eq!(
        created.as_ref().expect("create command").input,
        Some(json!({"query": 42, "custom": [true, null]}))
    );
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
    // `collected` is surfaced for an explicitly named view actor.
    assert_eq!(body["data"]["items"][0]["collected"], true);
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
async fn complete_session_route_is_not_mounted() {
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
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert!(session.completed.lock().expect("complete lock").is_none());
}

#[tokio::test]
async fn collect_session_returns_v1_envelope_and_forwards_command() {
    let session = Arc::new(FakeSessionService::default());
    let app = test_session_router(
        session.clone(),
        Arc::new(FakeSessionMessageService::default()),
    );

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            json!({"participant": "bot-1"}),
        ))
        .await
        .expect("collect response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["request_id"], "request-123");
    assert_eq!(body["data"]["session_id"], "session-1");
    assert_eq!(body["data"]["participant"], "bot-1");
    assert_eq!(body["data"]["collected"], true);
    let command = session.collected.lock().expect("collect lock");
    let command = command.as_ref().expect("collect command");
    assert_eq!(caller_user_id(&command.caller), "staff-1");
    assert_eq!(command.session_id, "session-1");
    assert_eq!(command.participant, "bot-1");
}

#[tokio::test]
async fn uncollect_session_returns_v1_envelope_and_forwards_command() {
    let session = Arc::new(FakeSessionService::default());
    let app = test_session_router(
        session.clone(),
        Arc::new(FakeSessionMessageService::default()),
    );

    let response = app
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/collaboration/sessions/session-1/collect?participant=bot-1",
            Value::Null,
        ))
        .await
        .expect("uncollect response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["session_id"], "session-1");
    assert_eq!(body["data"]["participant"], "bot-1");
    assert_eq!(body["data"]["collected"], false);
    let command = session.uncollected.lock().expect("uncollect lock");
    let command = command.as_ref().expect("uncollect command");
    assert_eq!(caller_user_id(&command.caller), "staff-1");
    assert_eq!(command.session_id, "session-1");
    assert_eq!(command.participant, "bot-1");
}

#[tokio::test]
async fn collection_requests_reject_invalid_participant_input_before_service_call() {
    for (method, uri, body) in [
        (
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            Value::Null,
        ),
        (
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            json!({"participant": ""}),
        ),
        (
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            json!({"participant": "bot-1", "unknown": true}),
        ),
        (
            "DELETE",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            Value::Null,
        ),
        (
            "DELETE",
            "/openapi/v1/collaboration/sessions/session-1/collect?participant=",
            Value::Null,
        ),
    ] {
        let session = Arc::new(FakeSessionService::default());
        let app = test_session_router(
            session.clone(),
            Arc::new(FakeSessionMessageService::default()),
        );
        let response = app
            .oneshot(authenticated_request(method, uri, body))
            .await
            .expect("invalid collection response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{method} {uri}");
        let response_body = response_json(response).await;
        assert_eq!(response_body["data"]["error_code"], "invalid_request");
        assert!(session.collected.lock().expect("collect lock").is_none());
        assert!(
            session
                .uncollected
                .lock()
                .expect("uncollect lock")
                .is_none()
        );
    }
}

#[tokio::test]
async fn collection_application_errors_use_declared_v1_envelopes() {
    for (forbidden, expected_status, expected_code) in [
        (true, StatusCode::FORBIDDEN, "forbidden"),
        (false, StatusCode::NOT_FOUND, "session_not_found"),
    ] {
        let session = Arc::new(FakeSessionService::default());
        session
            .collection_forbidden
            .store(forbidden, Ordering::Relaxed);
        session
            .collection_not_found
            .store(!forbidden, Ordering::Relaxed);
        let app = test_session_router(session, Arc::new(FakeSessionMessageService::default()));
        let response = app
            .oneshot(authenticated_request(
                "POST",
                "/openapi/v1/collaboration/sessions/session-1/collect",
                json!({"participant": "bot-1"}),
            ))
            .await
            .expect("collection error response");
        assert_eq!(response.status(), expected_status);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], expected_code);
        assert_eq!(body["request_id"], "request-123");
    }
}

#[tokio::test]
async fn list_session_messages_wraps_legacy_group_message_array_in_envelope() {
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
    assert_eq!(body["message"], "OK");
    assert_eq!(
        body["data"],
        serde_json::to_value(rich_group_messages()).expect("serialize legacy response")
    );
    assert!(body["data"].is_array());
    assert_eq!(body["data"][0]["role"], "assistant");
    assert_eq!(body["data"][0]["historyMeta"]["assistantAggregation"], true);
    assert_eq!(body["data"][0]["attachments"][0]["type"], "image");
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
async fn list_session_messages_passes_before_timestamp_through() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?before=1234567890&limit=10",
            Value::Null,
        ))
        .await
        .expect("list messages response");
    assert_eq!(response.status(), StatusCode::OK);
    {
        let listed = message.listed.lock().expect("list messages lock");
        let listed = listed.as_ref().expect("list messages command");
        assert_eq!(listed.session_id, "session-1");
        assert_eq!(listed.before, Some(1_234_567_890));
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
async fn list_session_messages_rejects_non_numeric_before_timestamp() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?before=not-a-timestamp",
            Value::Null,
        ))
        .await
        .expect("list messages response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(message.listed.lock().expect("list messages lock").is_none());
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
            json!({"bot_uuid": "bot-2"}),
        ))
        .await
        .expect("add participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["actor_id"], "bot-2");
    assert_eq!(body["data"]["mode"], "auto");
    {
        let added = session
            .added_participant
            .lock()
            .expect("add participant lock");
        let added = added.as_ref().expect("add participant command");
        assert_eq!(caller_user_id(&added.caller), "staff-1");
        assert_eq!(added.session_id, "session-1");
        assert_eq!(added.bot_uuid, "bot-2");
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
        let updated = session
            .updated_participant
            .lock()
            .expect("update participant lock");
        let updated = updated.as_ref().expect("update participant command");
        assert_eq!(caller_user_id(&updated.caller), "staff-1");
        assert_eq!(updated.session_id, "session-1");
        assert_eq!(updated.bot_uuid, "bot-2");
        assert_eq!(updated.mode, ParticipantMode::Muted);
    }
}

#[tokio::test]
async fn update_session_human_participant_accepts_present_mode() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/collaboration/sessions/session-1/participants/human_staff-1",
            json!({"mode": "present"}),
        ))
        .await
        .expect("update Human participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["actor_kind"], "human");
    assert_eq!(body["data"]["mode"], "present");

    let updated = session
        .updated_participant
        .lock()
        .expect("update participant lock");
    let updated = updated.as_ref().expect("update participant command");
    assert_eq!(updated.bot_uuid, "human_staff-1");
    assert_eq!(updated.mode, ParticipantMode::Present);
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
        let removed = session
            .removed_participant
            .lock()
            .expect("remove participant lock");
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
                "driver_bot_uuid": "bot-1"
            }),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(session.created.lock().expect("create lock").is_none());

    let reactivation_response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({"session_id": "legacy-session"}),
        ))
        .await
        .expect("V1 reactivation field response");
    assert_eq!(reactivation_response.status(), StatusCode::BAD_REQUEST);
    let reactivation_body = response_json(reactivation_response).await;
    assert_eq!(reactivation_body["data"]["error_code"], "invalid_request");
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
async fn create_session_rejects_null_optional_fields() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    for field in [
        "title",
        "kind",
        "acting_bot_id",
        "creator_role",
        "input",
        "meta",
        "context_delivery",
    ] {
        let mut body = serde_json::Map::new();
        body.insert(field.into(), Value::Null);
        let response = app
            .clone()
            .oneshot(authenticated_request(
                "POST",
                "/openapi/v1/collaboration/groups/group-1/sessions",
                Value::Object(body),
            ))
            .await
            .expect("null field response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "field={field}");
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "invalid_request");
    }
    assert!(session.created.lock().expect("create lock").is_none());
}

#[tokio::test]
async fn create_session_rejects_contract_invalid_identity_and_metadata() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);
    let invalid_bodies = [
        json!({"acting_bot_id": ""}),
        json!({"meta": {"callback_target": null}}),
        json!({"meta": {"callback_target": {"user_id": 1}}}),
        json!({"meta": {"channel": "chat"}}),
        json!({"meta": {"channel": {"session_scope": "global"}}}),
        json!({"meta": {"context_projection": "private"}}),
    ];

    for body in invalid_bodies {
        let response = app
            .clone()
            .oneshot(authenticated_request(
                "POST",
                "/openapi/v1/collaboration/groups/group-1/sessions",
                body,
            ))
            .await
            .expect("invalid request response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "invalid_request");
    }
    assert!(session.created.lock().expect("create lock").is_none());
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
