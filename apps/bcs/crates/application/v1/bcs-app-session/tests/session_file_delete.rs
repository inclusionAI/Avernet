//! F6: session-file delete is idempotent by metadata.
//!
//! The v1 facade (`SessionFileApplicationServiceImpl::delete`)
//! enforces the full resource gate first (`load_member`): a
//! missing/unrelated Session, a missing Group, or a caller that
//! lost every CURRENT access leg still fails. Only after that gate
//! does a "metadata already absent" read map to the idempotent
//! success — touching no storage, no tombstone lookup, no new
//! queries. Every other failure (storage, repo, decode) keeps
//! propagating as an error.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use async_trait::async_trait;
use bcs_app_session::SessionFileApplicationServiceImpl;
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_domain::SessionFile;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::session_files::SessionFileService;
use bcs_service_api::application::v1::{
    AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    CompleteSessionFile, DeleteSessionFile, GetSessionFile, PrepareSessionFile,
    SessionFileApplicationService, SessionFileInternalContentUrlProjector, UploadSessionFileContent,
};
use bcs_service_api::port::repo::{
    GroupRepoPort, NewSessionFileParams, NewSessionParams, SessionFileListPage,
    SessionFileListParams, SessionFileRepoPort, SessionRepoPort,
};
use bcs_service_api::{
    Group, GroupCoreService, Participant, ParticipantRole, ServiceResult, SessionKind,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_file::{SessionFileServiceConfig, SessionFileServiceImpl};
use bcs_session_file_store::MemorySessionFileRepo;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService as NoopSystemMessage;
use bcs_storage_api::{
    ByteStream, PresignGetOptions, PresignGetTicket, StorageCapabilities, StorageError,
    StorageHandle, StorageHealth, StorageObjectMeta, StoragePlugin, UploadHandle,
    UploadPrepareRequest, byte_stream_from_bytes, fake::FakeStoragePlugin,
};
use bytes::Bytes;
use std::sync::Mutex;

/// Storage decorator that records delete/abort keys and can be
/// armed to fail deletes: the regression pins which storage calls
/// (if any) each facade path performs.
#[derive(Clone, Default)]
struct RecordingStorage {
    inner: FakeStoragePlugin,
    deleted_keys: Arc<Mutex<Vec<String>>>,
    aborted_keys: Arc<Mutex<Vec<String>>>,
    fail_deletes: Arc<AtomicBool>,
}

impl RecordingStorage {
    fn new(caps: StorageCapabilities) -> Self {
        Self {
            inner: FakeStoragePlugin::new(caps),
            deleted_keys: Arc::new(Mutex::new(Vec::new())),
            aborted_keys: Arc::new(Mutex::new(Vec::new())),
            fail_deletes: Arc::new(AtomicBool::new(false)),
        }
    }

    fn reset_counts(&self) {
        self.deleted_keys.lock().unwrap().clear();
        self.aborted_keys.lock().unwrap().clear();
    }

    fn storage_calls(&self) -> usize {
        self.deleted_keys.lock().unwrap().len() + self.aborted_keys.lock().unwrap().len()
    }
}

#[async_trait]
impl StoragePlugin for RecordingStorage {
    fn backend_name(&self) -> &'static str {
        self.inner.backend_name()
    }

    fn capabilities(&self) -> StorageCapabilities {
        self.inner.capabilities()
    }

    async fn prepare_upload(
        &self,
        req: UploadPrepareRequest,
        caller: Option<&bcs_storage_api::ActorRef>,
    ) -> Result<bcs_storage_api::PreparedUpload, StorageError> {
        self.inner.prepare_upload(req, caller).await
    }

    async fn stream_upload(
        &self,
        handle: &UploadHandle,
        part_number: Option<u16>,
        body: ByteStream,
    ) -> Result<(), StorageError> {
        self.inner.stream_upload(handle, part_number, body).await
    }

    async fn complete_upload(
        &self,
        handle: &UploadHandle,
    ) -> Result<StorageObjectMeta, StorageError> {
        self.inner.complete_upload(handle).await
    }

    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError> {
        self.aborted_keys.lock().unwrap().push(handle.key.clone());
        self.inner.abort_upload(handle).await
    }

    async fn get_stream(&self, handle: &StorageHandle) -> Result<ByteStream, StorageError> {
        self.inner.get_stream(handle).await
    }

    async fn presign_get(
        &self,
        handle: &StorageHandle,
        opts: PresignGetOptions,
        caller: Option<&bcs_storage_api::ActorRef>,
    ) -> Result<PresignGetTicket, StorageError> {
        self.inner.presign_get(handle, opts, caller).await
    }

    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError> {
        self.deleted_keys.lock().unwrap().push(handle.key.clone());
        if self.fail_deletes.load(Ordering::SeqCst) {
            return Err(StorageError::Conflict("storage delete failed".into()));
        }
        self.inner.delete(handle).await
    }

    async fn health_check(&self) -> Result<StorageHealth, StorageError> {
        self.inner.health_check().await
    }
}

/// Repo decorator whose metadata delete fails: a recorded write
/// failure must propagate (never collapse into idempotent success).
struct FailDeleteFileRepo {
    inner: Arc<MemorySessionFileRepo>,
}

#[async_trait]
impl SessionFileRepoPort for FailDeleteFileRepo {
    async fn insert(&self, params: NewSessionFileParams) -> ServiceResult<SessionFile> {
        self.inner.insert(params).await
    }

    async fn get(&self, session_id: &str, file_id: &str) -> ServiceResult<Option<SessionFile>> {
        self.inner.get(session_id, file_id).await
    }

    async fn get_by_file_id(&self, file_id: &str) -> ServiceResult<Option<SessionFile>> {
        self.inner.get_by_file_id(file_id).await
    }

    async fn update_object_handle_and_status(
        &self,
        session_id: &str,
        file_id: &str,
        object_handle: &str,
        status: bcs_domain::FileStatus,
        size: u64,
    ) -> ServiceResult<Option<SessionFile>> {
        self.inner
            .update_object_handle_and_status(session_id, file_id, object_handle, status, size)
            .await
    }

    async fn update_status(
        &self,
        session_id: &str,
        file_id: &str,
        status: bcs_domain::FileStatus,
    ) -> ServiceResult<Option<SessionFile>> {
        self.inner.update_status(session_id, file_id, status).await
    }

    async fn delete(&self, session_id: &str, file_id: &str) -> ServiceResult<bool> {
        Err(bcs_service_api::ServiceError::InternalError(format!(
            "metadata delete failed for {session_id}/{file_id}"
        )))
    }

    async fn list(
        &self,
        session_id: &str,
        params: SessionFileListParams,
    ) -> ServiceResult<SessionFileListPage> {
        self.inner.list(session_id, params).await
    }

    async fn list_expired_pending(&self, now: u64, limit: u32) -> ServiceResult<Vec<SessionFile>> {
        self.inner.list_expired_pending(now, limit).await
    }

    async fn delete_all_for_session(&self, session_id: &str) -> ServiceResult<Vec<SessionFile>> {
        self.inner.delete_all_for_session(session_id).await
    }
}

struct ShareProjector;
impl SessionFileInternalContentUrlProjector for ShareProjector {
    fn shared_content_url(&self, token: &str) -> String {
        format!("http://share.test/content?token={token}")
    }
}

const SESSION: &str = "group-1:abcd1234";

struct Fixture {
    group_repo: Arc<dyn GroupRepoPort>,
    groups: Arc<GroupCore>,
    session_repo: Arc<dyn SessionRepoPort>,
    storage: RecordingStorage,
    authority_repo: Arc<MemoryBotRepo>,
    file_repo: Arc<MemorySessionFileRepo>,
}

impl Fixture {
    fn new() -> Self {
        let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
        let storage = RecordingStorage::new(StorageCapabilities {
            supports_presign_put: false,
            supports_presign_download: false,
            supports_stream_put: true,
            supports_stream_get: true,
            supports_inline_view: true,
            max_object_size: 1024 * 1024,
        });
        Self {
            group_repo,
            groups,
            session_repo,
            storage,
            authority_repo: Arc::new(MemoryBotRepo::new()),
            file_repo: Arc::new(MemorySessionFileRepo::new()),
        }
    }

    fn service(&self) -> SessionFileApplicationServiceImpl {
        self.service_with_file_repo(self.file_repo.clone())
    }

    fn service_with_file_repo(
        &self,
        file_repo: Arc<dyn SessionFileRepoPort>,
    ) -> SessionFileApplicationServiceImpl {
        // The legacy delegate reads Sessions/Groups through the SAME
        // repos the facade's membership gate uses.
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            self.session_repo.clone(),
            self.group_repo.clone(),
        ));
        let legacy: Arc<dyn SessionFileService> = Arc::new(SessionFileServiceImpl::new(
            SessionFileServiceConfig {
                storage: Arc::new(self.storage.clone()),
                repo: file_repo,
                session_repo: self.session_repo.clone(),
                env: "test".into(),
                max_size: 1024 * 1024,
                multipart_threshold: 1024,
                bcs_base_url: "http://legacy.test".into(),
                share_secret: b"test-secret".to_vec(),
                share_default_ttl: 3600,
                share_link_ttl: 3600,
                share_base_url: None,
            },
        ));
        let bots = Arc::new(BotCore::memory());
        SessionFileApplicationServiceImpl::new(
            legacy,
            sessions,
            self.groups.clone(),
            bots,
            Arc::new(NoopSystemMessage),
            Arc::new(ShareProjector),
        )
        .with_bot_authority_hook(bcs_test_support::authority_hook_for(
            self.authority_repo.clone(),
        ))
    }

    /// Seed group-1 + `SESSION` (bots a/b, owner alice) and the
    /// CURRENT owner edges the row-7 legs consult.
    async fn seed(&self) {
        for (bot, owner) in [("bot-a", "alice"), ("bot-b", "alice")] {
            bcs_test_support::grant_owner_edge(&self.authority_repo, bot, owner).await;
        }
        let participants = vec![
            Participant::bot("bot-a", ParticipantRole::Driver),
            Participant::bot("bot-b", ParticipantRole::Worker),
        ];
        let mut group = Group::new("group-1", "bot-a", participants.clone());
        group.originator = Some("human_alice".into());
        self.groups.upsert(group).await.expect("store group");
        self.session_repo
            .create(
                "group-1",
                NewSessionParams {
                    id: Some(SESSION.into()),
                    session_kind: SessionKind::Chat,
                    participants,
                    created_by: Some("bot-a".into()),
                    ..Default::default()
                },
            )
            .await
            .expect("store session");
    }

    /// Prepare (no upload) — the file stays `Pending`.
    async fn prepare_pending(&self, caller: &AuthenticatedCaller) -> String {
        self.service()
            .prepare(PrepareSessionFile {
                caller: caller.clone(),
                session_id: SESSION.into(),
                file_name: "pending.txt".into(),
                size: 3,
                mime_type: "text/plain".into(),
            })
            .await
            .expect("prepare file")
            .file
            .file_id
    }

    /// Prepare + upload + complete — the file becomes `Ready`.
    async fn prepare_ready(&self, caller: &AuthenticatedCaller) -> String {
        let service = self.service();
        let file_id = service
            .prepare(PrepareSessionFile {
                caller: caller.clone(),
                session_id: SESSION.into(),
                file_name: "ready.txt".into(),
                size: 3,
                mime_type: "text/plain".into(),
            })
            .await
            .expect("prepare file")
            .file
            .file_id;
        service
            .upload_content(UploadSessionFileContent {
                caller: caller.clone(),
                session_id: SESSION.into(),
                file_id: file_id.clone(),
                part_number: None,
                body: byte_stream_from_bytes(Bytes::from_static(b"abc")),
                content_length: Some(3),
            })
            .await
            .expect("upload file");
        service
            .complete(CompleteSessionFile {
                caller: caller.clone(),
                session_id: SESSION.into(),
                file_id: file_id.clone(),
            })
            .await
            .expect("complete file");
        file_id
    }
}

fn bot_caller(bot_uuid: &str, owner_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: owner_id.into(),
            app_id: 1,
            agent_code: "agent".into(),
        }),
        app: None,
        access_key: None,
    }
}

fn human_caller(user_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: user_id.into(),
            username: user_id.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn delete_cmd(caller: &AuthenticatedCaller, session_id: &str, file_id: &str) -> DeleteSessionFile {
    DeleteSessionFile {
        caller: caller.clone(),
        session_id: session_id.into(),
        file_id: file_id.into(),
    }
}

#[tokio::test]
async fn pending_file_delete_is_idempotent_and_retry_touches_no_storage() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let caller = bot_caller("bot-a", "alice");
    let file_id = fixture.prepare_pending(&caller).await;
    let service = fixture.service();

    let first = service
        .delete(delete_cmd(&caller, SESSION, &file_id))
        .await
        .expect("first delete succeeds");
    assert!(first.deleted);
    // Pending files are aborted (never object-deleted) once.
    assert_eq!(fixture.storage.storage_calls(), 1);
    fixture.storage.reset_counts();

    let retry = service
        .delete(delete_cmd(&caller, SESSION, &file_id))
        .await
        .expect("retry is the idempotent success, not a 404");
    assert!(retry.deleted);
    assert_eq!(
        fixture.storage.storage_calls(),
        0,
        "the metadata-missing retry must not touch storage delete/abort"
    );
}

#[tokio::test]
async fn ready_file_delete_is_idempotent_and_retry_touches_no_storage() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let caller = bot_caller("bot-a", "alice");
    let file_id = fixture.prepare_ready(&caller).await;
    let service = fixture.service();

    let first = service
        .delete(delete_cmd(&caller, SESSION, &file_id))
        .await
        .expect("first delete succeeds");
    assert!(first.deleted);
    assert_eq!(fixture.storage.storage_calls(), 1);
    fixture.storage.reset_counts();

    let retry = service
        .delete(delete_cmd(&caller, SESSION, &file_id))
        .await
        .expect("retry is the idempotent success");
    assert!(retry.deleted);
    assert_eq!(fixture.storage.storage_calls(), 0, "retry reaches no storage");
}

#[tokio::test]
async fn human_deletes_a_controlled_bots_file_and_retry_succeeds() {
    let fixture = Fixture::new();
    fixture.seed().await;
    // File owned by bot-a; alice controls it through the CURRENT
    // owner edge (row 7). The Human may delete and the retry is
    // the idempotent success.
    let owner_bot = bot_caller("bot-a", "alice");
    let file_id = fixture.prepare_ready(&owner_bot).await;
    let service = fixture.service();
    let human = human_caller("alice");

    let first = service
        .delete(delete_cmd(&human, SESSION, &file_id))
        .await
        .expect("the controlling Human deletes");
    assert!(first.deleted);
    let retry = service
        .delete(delete_cmd(&human, SESSION, &file_id))
        .await
        .expect("the controlling Human's retry succeeds");
    assert!(retry.deleted);
}

#[tokio::test]
async fn retry_is_denied_once_the_caller_loses_every_access_leg() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let owner_bot = bot_caller("bot-a", "alice");
    let file_id = fixture.prepare_ready(&owner_bot).await;
    let service = fixture.service();
    let human = human_caller("alice");

    service
        .delete(delete_cmd(&human, SESSION, &file_id))
        .await
        .expect("first delete succeeds");

    // Revoke alice's ONLY Session access legs (both participant
    // Bots): grant a replacement owner first, then drop alice's
    // edges — a bot with NO approved owner edge at all is an
    // authority-integrity failure (internal), not a deny. The
    // metadata-missing shortcut is gated by `load_member`, so the
    // retry can never fake success either way.
    for bot in ["bot-a", "bot-b"] {
        bcs_test_support::grant_owner_edge(&fixture.authority_repo, bot, "bob").await;
        bcs_test_support::revoke_authority_edge(
            &fixture.authority_repo,
            bot,
            "alice",
            bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner,
        )
        .await;
    }
    let retry = service
        .delete(delete_cmd(&human, SESSION, &file_id))
        .await
        .expect_err("a caller with no CURRENT access leg is still denied");
    assert_eq!(retry.code(), "forbidden");
}

#[tokio::test]
async fn metadata_present_without_mutate_permission_is_denied_not_swallowed() {
    let fixture = Fixture::new();
    fixture.seed().await;
    // bot-b is a Session participant but NOT the file owner and
    // shares no owner/manager edge with bot-a: the metadata-
    // present path keeps its current resulting deny.
    let owner_bot = bot_caller("bot-a", "alice");
    let file_id = fixture.prepare_ready(&owner_bot).await;
    let service = fixture.service();

    let denied = service
        .delete(delete_cmd(&bot_caller("bot-b", "alice"), SESSION, &file_id))
        .await
        .expect_err("a sibling Bot cannot delete another Bot's file");
    assert_eq!(denied.code(), "forbidden");
    // The file metadata survives the failed delete.
    service
        .get(GetSessionFile {
            caller: bot_caller("bot-a", "alice"),
            session_id: SESSION.into(),
            file_id,
        })
        .await
        .expect("file metadata survives the denied delete");
}

#[tokio::test]
async fn missing_session_or_group_cannot_fake_success() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let caller = bot_caller("bot-a", "alice");
    let file_id = fixture.prepare_pending(&caller).await;
    let service = fixture.service();

    let missing_session = service
        .delete(delete_cmd(&caller, "group-1:0000dead", &file_id))
        .await
        .expect_err("an unknown Session is an error, never a success");
    assert_eq!(missing_session.code(), "session_not_found");

    // A Session whose Group no longer exists: the gate has no
    // resource to authorize against → error, not idempotent Ok.
    fixture
        .session_repo
        .create(
            "ghost-group",
            NewSessionParams {
                id: Some("ghost-group:beef0001".into()),
                session_kind: SessionKind::Chat,
                participants: vec![Participant::bot("bot-a", ParticipantRole::Driver)],
                created_by: Some("bot-a".into()),
                ..Default::default()
            },
        )
        .await
        .expect("store session without a group");
    let missing_group = service
        .delete(delete_cmd(&caller, "ghost-group:beef0001", &file_id))
        .await
        .expect_err("a Session without its Group is an error");
    assert_eq!(missing_group.code(), "group_not_found");
}

#[tokio::test]
async fn storage_and_metadata_failures_never_fake_success() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let caller = bot_caller("bot-a", "alice");
    let file_id = fixture.prepare_ready(&caller).await;

    // Storage delete failure (metadata present): error.
    fixture.storage.fail_deletes.store(true, Ordering::SeqCst);
    let storage_error = fixture
        .service()
        .delete(delete_cmd(&caller, SESSION, &file_id))
        .await
        .expect_err("a storage failure propagates as an error");
    assert_ne!(storage_error.code(), "session_file_not_found");
    fixture.storage.fail_deletes.store(false, Ordering::SeqCst);

    // Metadata delete failure (storage succeeded): error.
    let failing_service = fixture.service_with_file_repo(Arc::new(FailDeleteFileRepo {
        inner: fixture.file_repo.clone(),
    }));
    let metadata_error = failing_service
        .delete(delete_cmd(&caller, SESSION, &file_id))
        .await
        .expect_err("a metadata delete failure propagates as an error");
    assert!(matches!(
        metadata_error,
        bcs_service_api::application::v1::ApplicationError::Internal(_)
            | bcs_service_api::application::v1::ApplicationError::BadGateway { .. }
    ));
    // The metadata row remains for the sweep path.
    assert!(
        fixture
            .file_repo
            .get(SESSION, &file_id)
            .await
            .expect("metadata read")
            .is_some(),
        "a failed metadata delete keeps the row"
    );
}

#[tokio::test]
async fn retry_under_another_session_never_deletes_the_other_sessions_file() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let caller = bot_caller("bot-a", "alice");
    let file_id = fixture.prepare_ready(&caller).await;

    // A second Session in the same Group where the same caller is
    // a member: deleting the (session A-only) file id under
    // session B is the idempotent B-scoped success and MUST leave
    // A's file intact.
    const SESSION_B: &str = "group-1:beef0002";
    fixture
        .session_repo
        .create(
            "group-1",
            NewSessionParams {
                id: Some(SESSION_B.into()),
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("bot-a", ParticipantRole::Driver),
                    Participant::bot("bot-b", ParticipantRole::Worker),
                ],
                created_by: Some("bot-a".into()),
                ..Default::default()
            },
        )
        .await
        .expect("store second session");
    let service = fixture.service();
    let scoped = service
        .delete(delete_cmd(&caller, SESSION_B, &file_id))
        .await
        .expect("the file is absent under session B; B-scoped delete succeeds");
    assert!(scoped.deleted);
    service
        .get(GetSessionFile {
            caller: caller.clone(),
            session_id: SESSION.into(),
            file_id: file_id.clone(),
        })
        .await
        .expect("session A's file is untouched by the B-scoped delete");
}

#[tokio::test]
async fn unauthenticated_or_unrelated_callers_are_denied_before_idempotency() {
    let fixture = Fixture::new();
    fixture.seed().await;
    let caller = bot_caller("bot-a", "alice");
    let file_id = fixture.prepare_ready(&caller).await;
    let service = fixture.service();

    // A stranger Human with no participant and no CURRENT edge:
    // even for an EXISTING file, and even for a missing file id.
    let stranger = human_caller("mallory");
    for target in [file_id.as_str(), "FILE_gone"] {
        let denied = service
            .delete(delete_cmd(&stranger, SESSION, target))
            .await
            .expect_err("strangers are denied before any idempotent shortcut");
        assert_eq!(denied.code(), "forbidden");
    }
}
