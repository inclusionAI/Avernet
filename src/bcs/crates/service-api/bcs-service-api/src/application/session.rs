//! Session use-case service.
//!
//! HTTP / WS 通过此 trait 创建、查询、唤醒、终结 session。
//! 实现方在 `services/bcs-session` 下，依赖 `SessionRepoPort` 持久化。

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::ServiceError;
use crate::port::repo::NewSessionParams;
use crate::types::{Participant, ParticipantMode, Session, SessionStatus};

/// Use-case level error for session operations.
#[derive(Debug, thiserror::Error)]
pub enum SessionUseCaseError {
    #[error("session not found: {0}")]
    NotFound(String),
    #[error("invalid session params: {0}")]
    InvalidParams(String),
    #[error("session callback pending: {0}")]
    CallbackPending(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error(transparent)]
    Internal(ServiceError),
}

impl From<ServiceError> for SessionUseCaseError {
    fn from(e: ServiceError) -> Self {
        match e {
            ServiceError::SessionNotFound(msg) => SessionUseCaseError::NotFound(msg),
            ServiceError::SessionInvalidParams(msg) => SessionUseCaseError::InvalidParams(msg),
            ServiceError::SessionCallbackPending(msg) => SessionUseCaseError::CallbackPending(msg),
            ServiceError::Conflict(msg) => SessionUseCaseError::Conflict(msg),
            other => SessionUseCaseError::Internal(other),
        }
    }
}

/// 创建或唤醒 session 的入参。
#[derive(Debug, Clone)]
pub struct CreateOrReactivateCommand {
    pub group_id: String,
    /// 不为 None 时唤醒指定 session（必须 Completed 且 callback 终态）；
    /// 为 None 时新建。
    pub session_id: Option<String>,
    pub params: NewSessionParams,
}

/// 唤醒结果。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateOrReactivateOutcome {
    pub session: Session,
    /// 本次是新建还是唤醒。
    pub created: bool,
}

#[async_trait]
pub trait SessionManagementService: Send + Sync {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError>;

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError>;

    /// Check whether `session_id` belongs to `group_id`. Delegates to
    /// `SessionRepoPort::belongs_to_group`. Use this rather than comparing
    /// `session.group_id` directly so future stores can layer in env scoping
    /// or soft-delete filtering without callers silently bypassing it.
    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError>;

    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError>;

    /// 统计 group 下 running service_invocation session 数（用于 max_concurrency / 路由字段锁）。
    async fn count_running_service(&self, group_id: &str) -> Result<u64, SessionUseCaseError>;

    /// 列出全 BCS 节点下所有 running service_invocation session（用于 timeout 扫描）。
    async fn list_running_service(
        &self,
        offset: u64,
        limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError>;

    /// 更新 session 的 callback_status（供 callback dispatcher 调用）。
    async fn update_callback_status(
        &self,
        session_id: &str,
        status: &str,
    ) -> Result<(), SessionUseCaseError>;

    /// CAS 终结：仅当 status=Running 时落 Completed 并触发 callback；
    /// 已 Completed 返回 `Ok(None)`。
    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError>;

    async fn add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> Result<Session, SessionUseCaseError>;

    async fn remove_participant(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError>;

    async fn update_participant_mode(
        &self,
        session_id: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError>;

    async fn update_title(
        &self,
        session_id: &str,
        title: Option<String>,
    ) -> Result<Session, SessionUseCaseError>;

    async fn list_group_ids_by_session_participant(
        &self,
        bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError>;

    async fn delete(&self, session_id: &str) -> Result<bool, SessionUseCaseError>;

    // ── session collection (收藏) ──────────────────────────────
    /// Mark a session as collected by `bot_uuid`. The bot must be a
    /// participant of the session (the side-table row exists).
    async fn collect(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<(), SessionUseCaseError> {
        Err(SessionUseCaseError::Internal(ServiceError::InternalError(
            "collect not implemented for this SessionManagementService".into(),
        )))
    }
    /// Remove the collection mark. Idempotent: only session-not-found is an
    /// error; non-participant / not-collected returns Ok.
    async fn uncollect(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<(), SessionUseCaseError> {
        Err(SessionUseCaseError::Internal(ServiceError::InternalError(
            "uncollect not implemented for this SessionManagementService".into(),
        )))
    }
    async fn list_collected_by_group(
        &self,
        _group_id: &str,
        _bot_uuid: &str,
        _status: Option<SessionStatus>,
        _title_contains: Option<&str>,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }
}
