//! Session file workspace application service trait.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use bcs_domain::{ActorRef, SessionFile, ShareTokenError};
use bcs_storage_api::{ByteStream, PresignGetTicket};

use crate::port::repo::{SessionFileListPage, SessionFileListParams};
use crate::ServiceError;

#[derive(Debug, thiserror::Error)]
pub enum SessionFileUseCaseError {
    #[error("not found: {0}")]
    NotFound(String),
    #[error("forbidden: {0}")]
    Forbidden(String),
    #[error("invalid input: {0}")]
    InvalidInput(String),
    #[error("payload too large: {0}")]
    PayloadTooLarge(String),
    #[error("invalid transition: {0}")]
    Conflict(String),
    #[error("invalid state: {0}")]
    InvalidState(String),
    #[error("storage backend error")]
    Backend,
    #[error("internal error: {0}")]
    Internal(#[from] ServiceError),
}

impl From<ShareTokenError> for SessionFileUseCaseError {
    fn from(e: ShareTokenError) -> Self {
        use bcs_domain::ShareTokenError::*;
        match e {
            InvalidEncoding | InvalidSignature | UnsupportedVersion | MalformedPayload(_) => {
                SessionFileUseCaseError::InvalidInput(format!("share token: {e}"))
            }
            Expired => SessionFileUseCaseError::InvalidState("share token expired".into()),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrepareUploadCommand {
    pub session_id: String,
    pub file_name: String,
    pub size: u64,
    pub mime_type: String,
    pub caller: ActorRef,
    // NOTE: prepare/upload/list/download are participant-gated (HTTP `ensure_session_member`),
    // NOT owner-gated — no `caller_identities` here. `owner` is recorded from `caller`.
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrepareUploadResult {
    pub file: SessionFile,
    pub client_target_json: serde_json::Value, // wire: §1.2.a / §1.2.b 响应体（去掉 file_id 重复由 handler 拼）
    pub expires_at: u64,
}

/// Mutate authz is done ENTIRELY in the service, fed by values the HTTP layer
/// pre-resolves (so the service stays transport-agnostic and free of group/bot
/// repo dependencies).
///
/// Delete authz uses `caller_identities + session_creator + driver_bot`: HTTP
/// fetches `session.created_by` (session_repo via session_management) and
/// `group.driver_bot` (group_management) before constructing `DeleteFileCommand`.
///
/// Share authz uses `caller_identities + session_participants`: any session
/// member may share. HTTP resolves `session_participants` from the session's
/// own `participants` (the same set `ensure_session_member` gates on).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeleteFileCommand {
    pub session_id: String,
    pub file_id: String,
    pub caller: ActorRef,
    pub caller_identities: Vec<String>,        // [caller.actor_id] + owned bot_uuids (HTTP `caller_identities()`)
    pub session_creator: Option<String>,       // session.created_by
    pub driver_bot: Option<String>,            // group.driver_bot
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShareMintCommand {
    pub session_id: String,
    pub file_id: String,
    pub caller: ActorRef,
    pub ttl_seconds: Option<u64>,
    pub caller_identities: Vec<String>,
    /// session 自身 participants 的 bot_uuid/actor_id，由 HTTP 从 `sess.participants`
    /// 解析（与 `ensure_session_member` 同源）。分享鉴权 = caller 是否为 session 成员。
    pub session_participants: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShareMintResult {
    pub share_url: String,
    pub share_token: String,
    pub expires_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShareConsumeResult {
    pub file: SessionFile,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadRoute {
    /// presign backend: Some(presigned url + expires_at) -> HTTP 302.
    /// local backend: None -> HTTP streams via get_stream.
    pub presign: Option<PresignGetTicket>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilitiesView {
    pub storage: String,
    pub presign_upload: bool,
    pub presign_download: bool,
    pub inline_view: bool,
    pub max_size: u64,
}

#[async_trait]
pub trait SessionFileService: Send + Sync {
    async fn capabilities(&self) -> CapabilitiesView;

    async fn prepare_upload(
        &self,
        cmd: PrepareUploadCommand,
    ) -> Result<PrepareUploadResult, SessionFileUseCaseError>;

    /// Proxy-backend byte ingestion (local). Presign backends never call this.
    async fn stream_upload(
        &self,
        session_id: &str,
        file_id: &str,
        part_number: Option<u16>,
        body: ByteStream,
        content_length: u64,
    ) -> Result<(), SessionFileUseCaseError>;

    async fn complete_upload(
        &self,
        session_id: &str,
        file_id: &str,
    ) -> Result<SessionFile, SessionFileUseCaseError>;

    async fn delete_file(
        &self,
        cmd: DeleteFileCommand,
    ) -> Result<(), SessionFileUseCaseError>;

    async fn get(&self, session_id: &str, file_id: &str) -> Result<SessionFile, SessionFileUseCaseError>;

    async fn list(
        &self,
        session_id: &str,
        params: SessionFileListParams,
    ) -> Result<SessionFileListPage, SessionFileUseCaseError>;

    /// Returns the download route for a Ready file.
    async fn download_route(
        &self,
        session_id: &str,
        file_id: &str,
        ttl_secs: Option<u64>,
        show: bool,
    ) -> Result<(SessionFile, DownloadRoute), SessionFileUseCaseError>;

    async fn share_mint(
        &self,
        cmd: ShareMintCommand,
    ) -> Result<ShareMintResult, SessionFileUseCaseError>;

    /// Server-authoritative mint for history echo. Does NOT run `can_share`
    /// (membership is enforced at the history HTTP entry; the pre-existing
    /// horizontal-privilege gap there is tracked separately — see spec §13).
    /// Still verifies file ownership via `repo.get(session_id, file_id)`.
    async fn share_mint_for_history(
        &self,
        session_id: &str,
        file_id: &str,
        ttl_seconds: u64,
    ) -> Result<ShareMintResult, SessionFileUseCaseError>;

    /// Verify share token (no session auth), return the file (must be Ready).
    async fn share_consume(
        &self,
        token: &str,
    ) -> Result<ShareConsumeResult, SessionFileUseCaseError>;

    /// Return a streaming body for a Ready file (local / fallback).
    async fn get_stream(
        &self,
        session_id: &str,
        file_id: &str,
    ) -> Result<(SessionFile, ByteStream), SessionFileUseCaseError>;

    /// Sweep Pending rows past expires_at -> Failed + abort_upload. Called by a timer (Task 11).
    async fn sweep_expired_pending(&self) -> Result<u64, SessionFileUseCaseError>;

    /// Best-effort cleanup of all files in a session (called by delete_session hook).
    async fn delete_all_for_session(&self, session_id: &str) -> Result<u64, SessionFileUseCaseError>;
}