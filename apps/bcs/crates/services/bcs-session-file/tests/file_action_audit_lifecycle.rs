//! Session-file service lifecycle audit (plan Task 11, spec §12.5).
//!
//! The delete/share flows follow the external-effect commit model:
//!
//! ```text
//! persist admitted -> perform backend I/O with no DB lock ->
//! ONE transaction(metadata result + Completed), or persist Failed
//! ```
//!
//! Pinned by the Task-11 brief:
//! - an un-recordable `admitted` refuses to start the side effect: the
//!   backend is never called (storage call count == 0);
//! - a backend success followed by a metadata/audit failure RETAINS the row,
//!   keeps the admitted row, writes NO completed, and surfaces the error
//!   (never a false rollback claim);
//! - a share mint with no metadata change still writes `admitted` then
//!   `completed`;
//! - the pending sweep is an honest System operation that never impersonates
//!   the original Human (spec §12.5 recovery honesty).

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{ActorKind, ActorRef, FileStatus, Session, SessionKind, SessionStatus};
use bcs_service_api::application::session_files::{
    DeleteFileCommand, PrepareUploadCommand, SessionFileService, ShareMintCommand,
};
use bcs_service_api::port::repo::{
    NewSessionFileParams, NewSessionParams, SessionFileListParams, SessionFileRepoPort,
    SessionRepoPort,
};
use bcs_service_api::types::bot_operation::{BotOperationActor, BotOperationContext};
use bcs_service_api::types::BotActionAuditPhase;
use bcs_session_file_store::MemorySessionFileRepo;
use bcs_storage_api::{
    ByteStream, ClientUploadTarget, PresignGetOptions, PresignGetTicket, PreparedUpload,
    StorageCapabilities, StorageError, StorageHealth, StorageObjectMeta, StoragePlugin,
    UploadHandle, UploadMode, UploadPrepareRequest,
};
use bcs_storage_api::fake::FakeStoragePlugin;

/// Wrapping storage double that counts backend `delete`/`abort_upload` calls
/// so the external-effect contract can be asserted precisely.
struct CountingStoragePlugin {
    inner: FakeStoragePlugin,
    deletes: std::sync::atomic::AtomicU64,
    aborts: std::sync::atomic::AtomicU64,
}

impl CountingStoragePlugin {
    fn new(inner: FakeStoragePlugin) -> Self {
        Self {
            inner,
            deletes: std::sync::atomic::AtomicU64::new(0),
            aborts: std::sync::atomic::AtomicU64::new(0),
        }
    }

    fn delete_calls(&self) -> u64 {
        self.deletes.load(std::sync::atomic::Ordering::SeqCst)
    }

    /// All backend external-effect calls (delete + abort) — the Pending-file
    /// delete path routes to `abort_upload`, the Ready path to `delete`.
    fn external_calls(&self) -> u64 {
        self.deletes.load(std::sync::atomic::Ordering::SeqCst)
            + self.aborts.load(std::sync::atomic::Ordering::SeqCst)
    }
}

#[async_trait]
impl StoragePlugin for CountingStoragePlugin {
    fn backend_name(&self) -> &'static str { self.inner.backend_name() }
    fn capabilities(&self) -> StorageCapabilities { self.inner.capabilities() }
    async fn prepare_upload(
        &self,
        req: UploadPrepareRequest,
        caller: Option<&ActorRef>,
    ) -> Result<PreparedUpload, StorageError> {
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
        self.aborts.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        self.inner.abort_upload(handle).await
    }
    async fn get_stream(&self, handle: &bcs_storage_api::StorageHandle) -> Result<ByteStream, StorageError> {
        self.inner.get_stream(handle).await
    }
    async fn presign_get(
        &self,
        handle: &bcs_storage_api::StorageHandle,
        opts: PresignGetOptions,
        caller: Option<&ActorRef>,
    ) -> Result<PresignGetTicket, StorageError> {
        self.inner.presign_get(handle, opts, caller).await
    }
    async fn delete(&self, handle: &bcs_storage_api::StorageHandle) -> Result<(), StorageError> {
        self.deletes.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        if handle.backend.is_empty() {
            // The fake staging is in-memory only; treat unknown handles as
            // idempotent NotFound to exercise the external-error branch.
            return Ok(());
        }
        self.inner.delete(handle).await
    }
    async fn health_check(&self) -> Result<StorageHealth, StorageError> {
        self.inner.health_check().await
    }
}

use bcs_session_file::{SessionFileServiceConfig, SessionFileServiceImpl};

fn human_operation(operation_id: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: operation_id.to_string(),
        actor: BotOperationActor::Human {
            user_id: "a".to_string(),
            effective_actor_id: "bot-x".to_string(),
        },
    }
}

/// A minimal SessionRepoPort stub — the service only reads session
/// existence at prepare time.
#[derive(Default)]
struct FakeSessionRepo {
    sessions: tokio::sync::RwLock<std::collections::HashMap<String, Session>>,
}

impl FakeSessionRepo {
    fn with_session(sid: &str) -> Self {
        let repo = Self::default();
        {
            let mut map = repo.sessions.try_write().expect("lock");
            map.insert(
                sid.to_string(),
                Session {
                    id: sid.to_string(),
                    group_id: "g1".into(),
                    session_title: None,
                    env: Some("test".into()),
                    status: SessionStatus::Running,
                    session_kind: SessionKind::Chat,
                    participants: vec![],
                    group_version: Some(1),
                    caller_id: None,
                    input: None,
                    output: None,
                    error_message: None,
                    callback_status: None,
                    activation_count: 1,
                    message_visibility_version: 1,
                    caller_principal: None,
                    created_by: Some("human_a".into()),
                    current_msg_seq: 0,
                    participant_join_seq: None,
                    created_at: 0,
                    updated_at: 0,
                    completed_at: None,
                    collected_at: None,
                    meta: None,
                },
            );
        }
        repo
    }
}

#[async_trait::async_trait]
impl SessionRepoPort for FakeSessionRepo {
    async fn get(&self, session_id: &str) -> Option<Session> {
        self.sessions.read().await.get(session_id).cloned()
    }
    async fn belongs_to_group(&self, _session_id: &str, _group_id: &str) -> bool { true }
    async fn list_running_service(&self, _offset: u64, _limit: u64) -> Vec<Session> { vec![] }
    async fn count_running_service(&self, _group_id: &str) -> u64 { 0 }
    async fn create(
        &self,
        _group_id: &str,
        _params: NewSessionParams,
    ) -> bcs_service_api::ServiceResult<Session> {
        Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
    }
    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Vec<Session> { vec![] }
    async fn latest_running(&self, _group_id: &str) -> Option<Session> { None }
    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> bcs_service_api::ServiceResult<Option<Session>> { Ok(None) }
    async fn reactivate(
        &self,
        _session_id: &str,
        _new_input: Option<serde_json::Value>,
    ) -> bcs_service_api::ServiceResult<Session> {
        Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
    }
    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: bcs_service_api::types::Participant,
        _operation: &BotOperationContext,
    ) -> bcs_service_api::ServiceResult<Session> {
        Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
    }
    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _operation: &BotOperationContext,
    ) -> bcs_service_api::ServiceResult<Session> {
        Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
    }
    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: bcs_service_api::types::ParticipantMode,
        _operation: &BotOperationContext,
    ) -> bcs_service_api::ServiceResult<Session> {
        Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
    }
    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> bcs_service_api::ServiceResult<()> { Ok(()) }
    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
        _operation: &BotOperationContext,
    ) -> bcs_service_api::ServiceResult<Session> {
        Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
    }
    async fn list_group_ids_by_session_participant(&self, _bot_uuid: &str) -> Vec<String> { vec![] }
    async fn delete(&self, _session_id: &str) -> bcs_service_api::ServiceResult<bool> { Ok(false) }
}

fn local_caps() -> StorageCapabilities {
    StorageCapabilities {
        supports_presign_put: false,
        supports_presign_download: false,
        supports_stream_put: true,
        supports_stream_get: true,
        supports_inline_view: true,
        max_object_size: 5_000_000_000,
    }
}

struct TestHarness {
    service: SessionFileServiceImpl,
    storage: Arc<CountingStoragePlugin>,
    repo: Arc<MemorySessionFileRepo>,
}

fn build_harness(storage: Arc<CountingStoragePlugin>) -> TestHarness {
    let repo = Arc::new(MemorySessionFileRepo::new());
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(FakeSessionRepo::with_session("g1:abcd1234"));
    let cfg = SessionFileServiceConfig {
        storage: storage.clone(),
        repo: repo.clone(),
        session_repo,
        env: "test".into(),
        max_size: 5_000_000_000,
        multipart_threshold: 100 * 1024 * 1024,
        bcs_base_url: "http://bcs:21000".into(),
        share_secret: b"k".to_vec(),
        share_default_ttl: 3600,
        share_link_ttl: 7777,
        share_base_url: None,
    };
    TestHarness {
        service: SessionFileServiceImpl::new(cfg),
        storage,
        repo,
    }
}

fn delete_cmd(file_id: &str, operation: &BotOperationContext) -> DeleteFileCommand {
    DeleteFileCommand {
        session_id: "g1:abcd1234".into(),
        file_id: file_id.into(),
        caller: ActorRef {
            actor_kind: ActorKind::Human,
            actor_id: "human_a".into(),
        },
        caller_identities: vec!["human_a".into(), "bot-x".into()],
        session_creator: Some("human_a".into()),
        driver_bot: None,
        operation: operation.clone(),
    }
}

async fn seeded_pending_file(repo: &Arc<MemorySessionFileRepo>, file_id: &str) {
    use bcs_service_api::port::repo::SessionFileRepoPort;
    repo
        .insert(NewSessionFileParams {
            file_id: file_id.to_string(),
            session_id: "g1:abcd1234".to_string(),
            file_name: format!("f-{file_id}.txt"),
            mime_type: "text/plain".to_string(),
            size: 5,
            owner: ActorRef {
                actor_kind: ActorKind::Human,
                actor_id: "human_a".into(),
            },
            storage_backend: "fake".to_string(),
            object_handle: r#"{"backend":"fake","key":"k","backend_handle":{"transfer_id":"t"},"expires_at":99999}"#.to_string(),
            expires_at: 99999,
            operation: human_operation(&format!("lifecycle-seed-{file_id}")),
        })
        .await
        .expect("seed metadata row");
}

async fn audits_of(
    repo: &Arc<MemorySessionFileRepo>,
    step_contains: &str,
) -> Vec<bcs_service_api::types::BotActionAuditRecord> {
    repo.session_file_action_audit_records()
        .await
        .expect("audit records")
        .into_iter()
        .filter(|record| record.step_key.contains(step_contains))
        .collect()
}

#[tokio::test]
async fn admitted_failure_refuses_to_start_the_external_side_effect() {
    // Counted storage proves the backend was never called when the admitted
    // row cannot be persisted.
    let storage = Arc::new(CountingStoragePlugin::new(FakeStoragePlugin::new(local_caps())));
    let harness = build_harness(storage.clone());
    seeded_pending_file(&harness.repo, "file-1").await;

    // Arm an audit append failure so record_operation_phase(admitted) fails.
    harness.repo.arm_action_audit_write_failure();
    let operation = human_operation("lifecycle-no-admit");
    let error = harness
        .service
        .delete_file(delete_cmd("file-1", &operation))
        .await
        .expect_err("an un-recordable admitted must refuse the delete");
    assert!(
        matches!(
            error,
            bcs_service_api::application::session_files::SessionFileUseCaseError::Internal(_)
        ),
        "got: {error:?}"
    );
    assert_eq!(
        harness.storage.external_calls(),
        0,
        "the backend MUST NOT be called when admitted failed to persist"
    );
    assert!(
        harness
            .repo
            .get("g1:abcd1234", "file-1")
            .await
            .expect("probe")
            .is_some(),
        "the metadata row remains untouched"
    );
    let failed_phase_rows = audits_of(&harness.repo, "delete/session_file/admitted").await;
    assert!(
        failed_phase_rows.is_empty(),
        "no admitted row may survive the refused attempt"
    );
}


/// Delegating file-repo wrapper whose final `delete` (metadata + completed
/// audit in one transaction on the real store) can be made to fail — to pin
/// the SERVICE's retained-row semantics after the backend already removed
/// the object.
struct FailableDeleteFileRepo {
    inner: Arc<MemorySessionFileRepo>,
    fail_delete: Arc<std::sync::atomic::AtomicBool>,
}

#[async_trait]
impl SessionFileRepoPort for FailableDeleteFileRepo {
    async fn insert(&self, params: NewSessionFileParams) -> bcs_service_api::ServiceResult<bcs_domain::SessionFile> {
        self.inner.insert(params).await
    }
    async fn get(&self, session_id: &str, file_id: &str) -> bcs_service_api::ServiceResult<Option<bcs_domain::SessionFile>> {
        self.inner.get(session_id, file_id).await
    }
    async fn get_by_file_id(&self, file_id: &str) -> bcs_service_api::ServiceResult<Option<bcs_domain::SessionFile>> {
        self.inner.get_by_file_id(file_id).await
    }
    async fn update_object_handle_and_status(
        &self,
        session_id: &str,
        file_id: &str,
        object_handle: &str,
        status: FileStatus,
        size: u64,
        operation: &BotOperationContext,
    ) -> bcs_service_api::ServiceResult<Option<bcs_domain::SessionFile>> {
        self.inner
            .update_object_handle_and_status(session_id, file_id, object_handle, status, size, operation)
            .await
    }
    async fn update_status(
        &self,
        session_id: &str,
        file_id: &str,
        status: FileStatus,
        operation: &BotOperationContext,
    ) -> bcs_service_api::ServiceResult<Option<bcs_domain::SessionFile>> {
        self.inner.update_status(session_id, file_id, status, operation).await
    }
    async fn delete(
        &self,
        session_id: &str,
        file_id: &str,
        operation: &BotOperationContext,
    ) -> bcs_service_api::ServiceResult<bool> {
        if self.fail_delete.load(std::sync::atomic::Ordering::SeqCst) {
            // Simulates the store's final metadata/audit transaction failure
            // (the injected-failure path of the store suite): nothing
            // commits, the caller surfaces the error.
            return Err(bcs_service_api::ServiceError::InternalError(
                "injected final metadata+completed audit failure".into(),
            ));
        }
        self.inner.delete(session_id, file_id, operation).await
    }
    async fn list(
        &self,
        session_id: &str,
        params: bcs_service_api::port::repo::SessionFileListParams,
    ) -> bcs_service_api::ServiceResult<bcs_service_api::port::repo::SessionFileListPage> {
        self.inner.list(session_id, params).await
    }
    async fn list_expired_pending(&self, now: u64, limit: u32) -> bcs_service_api::ServiceResult<Vec<bcs_domain::SessionFile>> {
        self.inner.list_expired_pending(now, limit).await
    }
    async fn delete_all_for_session(&self, session_id: &str) -> bcs_service_api::ServiceResult<Vec<bcs_domain::SessionFile>> {
        self.inner.delete_all_for_session(session_id).await
    }
    async fn record_operation_phase(&self, audit: bcs_service_api::types::BotActionAuditRecord) -> bcs_service_api::ServiceResult<()> {
        self.inner.record_operation_phase(audit).await
    }
}

/// Harness built over a wrapper repo whose `delete` can fail on demand.
struct FailingDeleteHarness {
    service: SessionFileServiceImpl,
    storage: Arc<CountingStoragePlugin>,
    repo: Arc<MemorySessionFileRepo>,
    fail_flag: Arc<std::sync::atomic::AtomicBool>,
}

fn build_failing_delete_harness(storage: Arc<CountingStoragePlugin>) -> FailingDeleteHarness {
    let repo = Arc::new(MemorySessionFileRepo::new());
    let fail_flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let wrapped: Arc<dyn SessionFileRepoPort> = Arc::new(FailableDeleteFileRepo {
        inner: repo.clone(),
        fail_delete: fail_flag.clone(),
    });
    let session_repo: Arc<dyn SessionRepoPort> =
        Arc::new(FakeSessionRepo::with_session("g1:abcd1234"));
    let cfg = SessionFileServiceConfig {
        storage: storage.clone(),
        repo: wrapped,
        session_repo,
        env: "test".into(),
        max_size: 5_000_000_000,
        multipart_threshold: 100 * 1024 * 1024,
        bcs_base_url: "http://bcs:21000".into(),
        share_secret: b"k".to_vec(),
        share_default_ttl: 3600,
        share_link_ttl: 7777,
        share_base_url: None,
    };
    FailingDeleteHarness {
        service: SessionFileServiceImpl::new(cfg),
        storage,
        repo,
        fail_flag,
    }
}

#[tokio::test]
async fn backend_success_then_metadata_audit_failure_retains_row_and_admits_only() {
    let storage = Arc::new(CountingStoragePlugin::new(FakeStoragePlugin::new(local_caps())));
    let harness = build_failing_delete_harness(storage.clone());
    seeded_pending_file(&harness.repo, "file-1").await;

    // Phase 1: a clean delete persists admitted and removes the metadata.
    let first_operation = human_operation("lifecycle-clean");
    harness
        .service
        .delete_file(delete_cmd("file-1", &first_operation))
        .await
        .expect("clean delete");
    assert_eq!(harness.storage.external_calls(), 1);
    let admitted_rows = audits_of(&harness.repo, "delete/session_file/admitted").await;
    assert_eq!(admitted_rows.len(), 1, "admitted persisted before the I/O");

    // Phase 2: reseed the row; the final metadata+completed transaction now
    // fails AFTER the backend call already happened.
    seeded_pending_file(&harness.repo, "file-1").await;
    harness
        .fail_flag
        .store(true, std::sync::atomic::Ordering::SeqCst);
    let second_operation = human_operation("lifecycle-retain");
    let error = harness
        .service
        .delete_file(delete_cmd("file-1", &second_operation))
        .await
        .expect_err("the metadata/audit failure surfaces");
    assert!(
        matches!(
            error,
            bcs_service_api::application::session_files::SessionFileUseCaseError::Internal(_)
        ),
        "got: {error:?}"
    );
    assert_eq!(
        harness.storage.external_calls(),
        2,
        "the backend call for the second delete DID happen (admitted proved intent)"
    );
    assert!(
        harness
            .repo
            .get("g1:abcd1234", "file-1")
            .await
            .expect("probe")
            .is_some(),
        "the metadata row is RETAINED for retry — no false completion"
    );
    let completed: Vec<_> = audits_of(&harness.repo, "delete/session_file/completed")
        .await
        .into_iter()
        .filter(|record| record.operation_id == second_operation.operation_id)
        .collect();
    assert!(
        completed.is_empty(),
        "no completed row may exist for the failed second delete"
    );
    // The admitted row for the second operation DID persist before the I/O.
    let admitted: Vec<_> = audits_of(&harness.repo, "delete/session_file/admitted")
        .await
        .into_iter()
        .filter(|record| record.operation_id == second_operation.operation_id)
        .collect();
    assert_eq!(admitted.len(), 1, "admitted stays for inspection/recovery");
}

#[tokio::test]
async fn share_mint_writes_admitted_then_completed_standalone_rows() {
    let storage = Arc::new(CountingStoragePlugin::new(FakeStoragePlugin::new(local_caps())));
    let harness = build_harness(storage.clone());
    seeded_pending_file(&harness.repo, "share-1").await;
    // Make the file Ready so sharing is allowed.
    harness
        .repo
        .update_object_handle_and_status(
            "g1:abcd1234",
            "share-1",
            r#"{"backend":"fake","key":"k","backend_handle":{"transfer_id":"t"},"expires_at":99999}"#,
            FileStatus::Ready,
            5,
            &human_operation("lifecycle-share-ready"),
        )
        .await
        .expect("mark Ready")
        .expect("row present");

    let operation = human_operation("lifecycle-share");
    let minted = harness
        .service
        .share_mint(ShareMintCommand {
            session_id: "g1:abcd1234".into(),
            file_id: "share-1".into(),
            caller: ActorRef {
                actor_kind: ActorKind::Human,
                actor_id: "human_a".into(),
            },
            ttl_seconds: Some(3600),
            caller_identities: vec!["human_a".into()],
            session_participants: vec!["human_a".into()],
            operation: operation.clone(),
        })
        .await
        .expect("share mint");

    let admitted: Vec<_> = audits_of(&harness.repo, "share/session_file/admitted")
        .await
        .into_iter()
        .filter(|record| record.operation_id == operation.operation_id)
        .collect();
    let completed: Vec<_> = audits_of(&harness.repo, "share/session_file/completed")
        .await
        .into_iter()
        .filter(|record| record.operation_id == operation.operation_id)
        .collect();
    assert_eq!(admitted.len(), 1, "share persists admitted before minting");
    assert_eq!(
        completed.len(),
        1,
        "a successful share without metadata change still writes completed"
    );
    assert_eq!(admitted[0].resource_id, "share-1");
    assert_eq!(admitted[0].operator.operator_user_id(), Some("a"));
    assert_eq!(completed[0].phase, BotActionAuditPhase::Completed);
    assert!(!minted.share_token.is_empty());
}

#[tokio::test]
async fn pending_sweep_records_honest_system_rows_never_a_forged_human() {
    let storage = Arc::new(CountingStoragePlugin::new(FakeStoragePlugin::new(local_caps())));
    let harness = build_harness(storage.clone());
    seeded_pending_file(&harness.repo, "sweep-1").await;
    // Force the pending row past expiry so the sweep takes it.
    {
        use bcs_service_api::port::repo::SessionFileRepoPort;
        let row = harness
            .repo
            .get("g1:abcd1234", "sweep-1")
            .await
            .expect("probe")
            .expect("row");
        let mut handle: serde_json::Value =
            serde_json::from_str(&row.object_handle).expect("decode handle");
        handle["expires_at"] = serde_json::json!(1);
        harness
            .repo
            .update_object_handle_and_status(
                "g1:abcd1234",
                "sweep-1",
                &handle.to_string(),
                FileStatus::Pending,
                row.size,
                &human_operation("lifecycle-sweep-expire"),
            )
            .await
            .expect("expire the handle");
    }

    let swept = harness
        .service
        .sweep_expired_pending()
        .await
        .expect("sweep");
    assert_eq!(swept, 1);

    // The sweep's applied rows are HONEST system rows: the operator is the
    // independent system action, never the Human that originally uploaded
    // (the expiry-forcing fixture update carries the fixture's own human
    // row and is deliberately filtered out here).
    let rows = audits_of(&harness.repo, "update/session_file/applied")
        .await
        .into_iter()
        .filter(|record| record.operator.operator_kind() == "system")
        .collect::<Vec<_>>();
    assert_eq!(rows.len(), 1, "the status flip writes its applied row");
    assert_eq!(rows[0].resource_id, "sweep-1");
    assert_eq!(
        rows[0]
            .operator
            .operator_id(),
        "session-file-pending-sweep",
        "the sweep records the honest system actor, never a forged Human"
    );
    let row = harness
        .repo
        .get("g1:abcd1234", "sweep-1")
        .await
        .expect("probe")
        .expect("row retained for inspection");
    assert_eq!(row.status, FileStatus::Failed);
}