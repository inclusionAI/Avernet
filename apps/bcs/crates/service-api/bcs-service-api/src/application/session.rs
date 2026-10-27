//! Session use-case service.
//!
//! HTTP / WS 通过此 trait 创建、查询、唤醒、终结 session。
//! 实现方在 `services/bcs-session` 下，依赖 `SessionRepoPort` 持久化。

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::ServiceError;
use crate::port::repo::NewSessionParams;
use crate::types::{MessageViewScope, Participant, ParticipantMode, Session, SessionStatus};

/// Use-case level error for session operations.
#[derive(Debug, thiserror::Error)]
pub enum SessionUseCaseError {
    #[error("session not found: {0}")]
    NotFound(String),
    #[error("invalid session params: {0}")]
    InvalidParams(String),
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
            ServiceError::Conflict(msg) => SessionUseCaseError::Conflict(msg),
            other => SessionUseCaseError::Internal(other),
        }
    }
}

/// 创建或唤醒 session 的入参。
#[derive(Debug, Clone)]
pub struct CreateOrReactivateCommand {
    pub group_id: String,
    /// 不为 None 时唤醒指定 session（必须是 Completed 的 Service Session）；
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
    /// Check live Provider scope before self-enrollment or accepting chat/input.
    /// Legacy implementations without Provider policy retain their old behavior.
    async fn authorize_collaboration(&self, _session_id: &str, _actor_id: &str) -> Result<(), SessionUseCaseError> {
        Ok(())
    }
    async fn validate_session_registry(&self) -> Result<(), SessionUseCaseError> {
        Err(ServiceError::InternalError("session registry validation unsupported".into()).into())
    }

    async fn ensure_direct_session(&self, _id: &str) -> Result<crate::port::repo::session_registry::SessionRegistration, SessionUseCaseError> {
        Err(ServiceError::InternalError("session registry is not configured".into()).into())
    }
    async fn session_registration(&self, _id: &str) -> Result<Option<crate::port::repo::session_registry::SessionRegistration>, SessionUseCaseError> {
        Err(ServiceError::InternalError("session registry is not configured".into()).into())
    }

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

    /// Fallible, bounded Running ServiceInvocation page ordered by Session id,
    /// exclusive of the cursor. Restart with None after the end of each sweep.
    async fn list_running_service_after(
        &self,
        _after_session_id: Option<&str>,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Err(SessionUseCaseError::Internal(ServiceError::InvalidOperation {
            message: "Session recovery pagination is not configured".into(),
            request_id: None,
        }))
    }

    /// CAS 终结：仅当 status=Running 时落 Completed 并记录完成事件；
    /// 已 Completed 返回 `Ok(None)`。
    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError>;

    /// Complete the caller's saved Running ServiceInvocation activation with
    /// the usual Session event. An observed missing/stale/kind mismatch is a
    /// no-op; a race in the eventful transaction may return a conflict. Writes
    /// remain fallible. The returned snapshot identifies the completed activation. No unguarded completion fallback is permitted.
    async fn complete_running_service_activation(
        &self,
        _session_id: &str,
        _expected_activation_count: i32,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Err(SessionUseCaseError::Internal(ServiceError::InvalidOperation {
            message: "Session completion activation CAS is not configured".into(),
            request_id: None,
        }))
    }

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

    async fn update_participant_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> Result<Session, SessionUseCaseError> {
        let _ = (session_id, actor_id, message_view_scope);
        Err(SessionUseCaseError::Internal(
            ServiceError::InvalidOperation {
                message: "Session participant scope updates are not configured".to_string(),
                request_id: None,
            },
        ))
    }

    async fn update_participant_mode_and_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        mode: Option<ParticipantMode>,
        message_view_scope: MessageViewScope,
    ) -> Result<Session, SessionUseCaseError> {
        let updated = self
            .update_participant_message_view_scope(
                session_id,
                actor_id,
                message_view_scope,
            )
            .await?;
        if let Some(mode) = mode {
            self.update_participant_mode(session_id, actor_id, mode).await
        } else {
            Ok(updated)
        }
    }

    async fn update_title(
        &self,
        session_id: &str,
        title: Option<String>,
    ) -> Result<Session, SessionUseCaseError>;

    async fn list_group_ids_by_session_participant(
        &self,
        bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError>;

    /// Whether any supplied Actor currently participates in a Session of this
    /// Group. Empty input is false. Includes completed Sessions; does not grant
    /// management rights. Implementations must batch lookups and propagate
    /// persistence failures rather than reporting absence.
    async fn has_group_session_participant(
        &self,
        _group_id: &str,
        _actor_ids: &[String],
    ) -> Result<bool, SessionUseCaseError> {
        Err(SessionUseCaseError::Internal(ServiceError::InvalidOperation {
            message: "Batch Session membership lookup is not configured".into(),
            request_id: None,
        }))
    }

    /// Abort active StateMachine runs before deleting the Session.
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

    /// Batch lookup of collected_at by bot for the given session ids. Returns
    /// `(session_id, collected_at_ms)` only for sessions the bot has collected.
    /// Used by the session-list HTTP layer to surface per-session collected
    /// state for a participant.
    async fn collected_at_map(
        &self,
        _session_ids: &[&str],
        _bot_uuid: &str,
    ) -> Result<Vec<(String, u64)>, SessionUseCaseError> {
        Ok(Vec::new())
    }
}
