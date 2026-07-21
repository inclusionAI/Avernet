//! Repository port for session file metadata. BCS DB is the sole authoritative
//! source for list/metadata (never the storage backend).

use async_trait::async_trait;

use bcs_domain::{ActorRef, FileStatus, SessionFile};

use crate::ServiceResult;

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
}

#[derive(Debug, Clone, Default)]
pub struct SessionFileListParams {
    pub prefix: Option<String>,
    pub limit: u32,
    pub marker: Option<String>, // opaque cursor (created_at,file_id)
}

#[derive(Debug, Clone)]
pub struct SessionFileListPage {
    pub items: Vec<SessionFile>,
    pub truncated: bool,
    pub next_marker: Option<String>,
}

#[async_trait]
pub trait SessionFileRepoPort: Send + Sync {
    async fn insert(&self, params: NewSessionFileParams) -> ServiceResult<SessionFile>;
    async fn get(&self, session_id: &str, file_id: &str) -> ServiceResult<Option<SessionFile>>;
    async fn update_object_handle_and_status(
        &self,
        session_id: &str,
        file_id: &str,
        object_handle: &str,
        status: FileStatus,
        size: u64,
    ) -> ServiceResult<Option<SessionFile>>;
    async fn update_status(
        &self,
        session_id: &str,
        file_id: &str,
        status: FileStatus,
    ) -> ServiceResult<Option<SessionFile>>;
    async fn delete(&self, session_id: &str, file_id: &str) -> ServiceResult<bool>;
    async fn list(
        &self,
        session_id: &str,
        params: SessionFileListParams,
    ) -> ServiceResult<SessionFileListPage>;
    /// Rows that are Pending and past their expires_at (for the Pending sweep).
    async fn list_expired_pending(&self, now: u64, limit: u32) -> ServiceResult<Vec<SessionFile>>;
    async fn delete_all_for_session(&self, session_id: &str) -> ServiceResult<Vec<SessionFile>>;
}