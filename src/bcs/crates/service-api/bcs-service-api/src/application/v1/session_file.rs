//! Transport-neutral OpenAPI V1 session-file application contract.

use async_trait::async_trait;
use bcs_storage_api::ByteStream;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, AuthenticatedCaller, DeleteResult};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionFileActorKind {
    Human,
    Bot,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionFileStatus {
    Pending,
    Ready,
    Deleting,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionFileActor {
    pub actor_kind: SessionFileActorKind,
    pub actor_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionFileView {
    pub file_id: String,
    pub session_id: String,
    pub file_name: String,
    pub mime_type: String,
    pub size: u64,
    pub sha256: Option<String>,
    pub owner: SessionFileActor,
    pub storage_backend: String,
    pub status: SessionFileStatus,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone)]
pub struct PrepareSessionFile {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub file_name: String,
    pub size: u64,
    pub mime_type: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PrepareSessionFileResult {
    pub file: SessionFileView,
    /// Existing upload target shape. Delivery adapters project only BCN proxy
    /// URLs and leave direct storage presigned URLs untouched.
    pub upload_target: serde_json::Value,
    pub expires_at: u64,
    pub proxy_upload: bool,
}

pub struct UploadSessionFileContent {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub file_id: String,
    pub part_number: Option<u16>,
    pub body: ByteStream,
    pub content_length: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UploadSessionFileResult {
    pub file_id: String,
    pub status: SessionFileStatus,
}

#[derive(Debug, Clone)]
pub struct CompleteSessionFile {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub file_id: String,
    /// Server-constructed URL used only by the best-effort upload
    /// notification. It is never populated from request JSON.
    pub notification_content_url: String,
}

#[derive(Debug, Clone)]
pub struct DeleteSessionFile {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub file_id: String,
}

#[derive(Debug, Clone)]
pub struct GetSessionFile {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub file_id: String,
}

#[derive(Debug, Clone)]
pub struct ListSessionFiles {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub prefix: Option<String>,
    pub status: Option<SessionFileStatus>,
    pub limit: u32,
    pub offset: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionFilePage {
    pub items: Vec<SessionFileView>,
    pub total: u64,
}

#[derive(Debug, Clone)]
pub struct DownloadSessionFile {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub file_id: String,
    pub show: bool,
}

#[derive(Debug, Clone)]
pub struct ShareSessionFile {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub file_id: String,
    pub ttl_seconds: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ShareSessionFileResult {
    pub share_token: String,
    pub expires_at: u64,
}

#[derive(Debug, Clone)]
pub struct DownloadSharedSessionFile {
    pub token: String,
    pub show: bool,
}

/// Fully authorized content outcome. Streaming is returned directly so the
/// HTTP adapter never needs an authorization-bypassing second lookup.
pub enum SessionFileContent {
    Redirect {
        download_url: String,
        expires_at: u64,
    },
    Stream {
        file: SessionFileView,
        body: ByteStream,
        inline: bool,
    },
}

#[async_trait]
pub trait SessionFileApplicationService: Send + Sync {
    async fn prepare(
        &self,
        command: PrepareSessionFile,
    ) -> Result<PrepareSessionFileResult, ApplicationError>;

    async fn upload_content(
        &self,
        command: UploadSessionFileContent,
    ) -> Result<UploadSessionFileResult, ApplicationError>;

    async fn complete(
        &self,
        command: CompleteSessionFile,
    ) -> Result<SessionFileView, ApplicationError>;

    async fn delete(&self, command: DeleteSessionFile) -> Result<DeleteResult, ApplicationError>;

    async fn get(&self, command: GetSessionFile) -> Result<SessionFileView, ApplicationError>;

    async fn list(&self, command: ListSessionFiles) -> Result<SessionFilePage, ApplicationError>;

    async fn download(
        &self,
        command: DownloadSessionFile,
    ) -> Result<SessionFileContent, ApplicationError>;

    async fn share(
        &self,
        command: ShareSessionFile,
    ) -> Result<ShareSessionFileResult, ApplicationError>;

    async fn download_shared(
        &self,
        command: DownloadSharedSessionFile,
    ) -> Result<SessionFileContent, ApplicationError>;
}
