//! Repository port for session file metadata. BCS DB is the sole authoritative
//! source for list/metadata (never the storage backend).
//!
//! Audit contract (spec §12.5, plan Task 11): every metadata mutation that
//! changes persisted state commits its `applied`/`completed` audit row in the
//! SAME transaction as the business write. [`SessionFileRepoPort::record_operation_phase`]
//! is the ONLY standalone audit lane and exists solely for the file service
//! to bracket external backend I/O with `admitted` (before the I/O) and
//! `failed`/`failed`-terminal/`unknown` phases; a successful metadata change
//! must NEVER be "business write first, audit call afterwards".

use async_trait::async_trait;

use bcs_domain::{ActorRef, FileStatus, SessionFile};

use crate::ServiceResult;
use crate::types::BotOperationContext;

#[derive(Debug, Clone)]
pub struct NewSessionFileParams {
    pub file_id: String,
    pub session_id: String,
    pub file_name: String,
    pub mime_type: String,
    pub size: u64,
    pub owner: ActorRef,
    pub storage_backend: String,
    pub object_handle: String, // serialized UploadHandle
    pub expires_at: u64,
    /// REQUIRED audit identity (spec §12.5, plan Task 11): the
    /// `create/session_file/applied` audit row commits with the metadata
    /// INSERT in one transaction. No `None` fallback exists.
    pub operation: BotOperationContext,
}

#[derive(Debug, Clone, Default)]
pub struct SessionFileListParams {
    pub prefix: Option<String>,
    pub status: Option<FileStatus>,
    pub limit: u32,   // 0 => 100, clamped to [1, 1000] in impls
    pub offset: u32,  // skip this many (in created_at DESC, file_id DESC order)
}

#[derive(Debug, Clone)]
pub struct SessionFileListPage {
    pub items: Vec<SessionFile>,
    pub total: u64,    // full count matching (env, session_id, [prefix], [status]) ignoring limit/offset
}

#[async_trait]
pub trait SessionFileRepoPort: Send + Sync {
    /// Insert new file metadata; the `create/session_file/applied` audit row
    /// (from `params.operation`, spec §12.5) commits in the SAME transaction.
    async fn insert(&self, params: NewSessionFileParams) -> ServiceResult<SessionFile>;
    async fn get(&self, session_id: &str, file_id: &str) -> ServiceResult<Option<SessionFile>>;
    /// Look up a file by its globally-unique file_id (used by share_consume,
    /// which has no session id — the share token only carries file_id).
    async fn get_by_file_id(&self, file_id: &str) -> ServiceResult<Option<SessionFile>>;
    /// Atomically replace the object handle + status + size and commit the
    /// `update/session_file/applied` audit row in the SAME transaction. An
    /// idempotent no-change update writes no audit row.
    async fn update_object_handle_and_status(
        &self,
        session_id: &str,
        file_id: &str,
        object_handle: &str,
        status: FileStatus,
        size: u64,
        operation: &BotOperationContext,
    ) -> ServiceResult<Option<SessionFile>>;
    /// Atomically set the status and commit the `update/session_file/applied`
    /// audit row in the SAME transaction. An idempotent no-change update
    /// writes no audit row.
    async fn update_status(
        &self,
        session_id: &str,
        file_id: &str,
        status: FileStatus,
        operation: &BotOperationContext,
    ) -> ServiceResult<Option<SessionFile>> {
        Err(crate::core::ServiceError::InvalidOperation {
            message: "Session file status update is not configured".to_string(),
            request_id: None,
        })
    }
    /// The final metadata DELETE commits in ONE transaction together with the
    /// `delete/session_file/completed` audit row (the external object removal
    /// already happened under the `delete/session_file/admitted` phase). A
    /// missing row is an idempotent no-op that writes no audit row.
    async fn delete(
        &self,
        session_id: &str,
        file_id: &str,
        operation: &BotOperationContext,
    ) -> ServiceResult<bool>;
    /// Standalone audit-phase recorder, SOLELY for the file service to
    /// bracket external backend I/O (spec §12.5): `admitted` is persisted
    /// BEFORE any external side effect, and an explicit `failed`/`unknown`
    /// terminal is persisted afterwards. A SUCCESSFUL metadata change must
    /// use the atomic mutations above (same-transaction audit) — calling
    /// this method after a successful business write is the forbidden
    /// "business first, audit later" anti-pattern.
    async fn record_operation_phase(
        &self,
        _audit: crate::types::BotActionAuditRecord,
    ) -> ServiceResult<()> {
        Err(crate::core::ServiceError::InvalidOperation {
            message: "Session file operation phase recording is not configured".to_string(),
            request_id: None,
        })
    }
    async fn list(
        &self,
        session_id: &str,
        params: SessionFileListParams,
    ) -> ServiceResult<SessionFileListPage>;
    /// Rows that are Pending and past their expires_at (for the Pending sweep).
    async fn list_expired_pending(&self, now: u64, limit: u32) -> ServiceResult<Vec<SessionFile>>;
    async fn delete_all_for_session(&self, session_id: &str) -> ServiceResult<Vec<SessionFile>>;
}