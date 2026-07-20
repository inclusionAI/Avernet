//! Session repository port.
//!
//! 持久化层契约。core/application 通过该 trait 操作 session 状态，不直接接触 DB。

use async_trait::async_trait;

use crate::core::ServiceError;
use crate::types::{
    Participant, ParticipantMode, ServiceResult, Session, SessionKind, SessionStatus,
};

/// Session 服务层入参（创建新 session）。
#[derive(Debug, Clone, Default)]
pub struct NewSessionParams {
    pub session_kind: SessionKind,
    pub participants: Vec<Participant>,
    pub group_version: Option<i32>,
    pub caller_id: Option<String>,
    pub caller_principal: Option<String>,
    pub input: Option<serde_json::Value>,
    pub created_by: Option<String>,
    pub session_title: Option<String>,
    /// 显式指定 session_id；不传则由实现层生成 `{group_id}:{8_hex}`。
    pub id: Option<String>,
    pub meta: Option<serde_json::Value>,
}

/// Session 持久化 port。
#[async_trait]
pub trait SessionRepoPort: Send + Sync {
    async fn create(&self, group_id: &str, params: NewSessionParams) -> ServiceResult<Session>;
    async fn get(&self, session_id: &str) -> Option<Session>;
    async fn belongs_to_group(&self, session_id: &str, group_id: &str) -> bool;
    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> Vec<Session>;
    async fn latest_running(&self, group_id: &str) -> Option<Session>;
    async fn count_running_service(&self, group_id: &str) -> u64;
    async fn list_running_service(&self, offset: u64, limit: u64) -> Vec<Session>;

    /// **CAS 完成**：仅当当前 status=Running 时落 Completed 并返回新 session；
    /// 已是 Completed 则返回 `Ok(None)`。spec §桶 8。
    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> ServiceResult<Option<Session>>;

    async fn reactivate(
        &self,
        session_id: &str,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session>;
    async fn add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> ServiceResult<Session>;
    async fn remove_participant(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Session>;
    async fn update_participant_mode(
        &self,
        session_id: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<Session>;
    async fn update_callback_status(&self, session_id: &str, status: &str) -> ServiceResult<()>;
    async fn update_title(&self, session_id: &str, title: Option<String>) -> ServiceResult<Session>;
    async fn list_group_ids_by_session_participant(&self, bot_uuid: &str) -> Vec<String>;
    async fn delete(&self, session_id: &str) -> ServiceResult<bool>;

    // ── session collection (收藏) ──────────────────────────────
    // Default impls keep existing test mocks compiling; real impls in
    // mysql + memory override these (see bcs-session-store).
    async fn collect(&self, _session_id: &str, _bot_uuid: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "collect not implemented for this SessionRepoPort".into(),
        ))
    }
    async fn uncollect(&self, _session_id: &str, _bot_uuid: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "uncollect not implemented for this SessionRepoPort".into(),
        ))
    }
    async fn list_collected_by_group(
        &self,
        _group_id: &str,
        _bot_uuid: &str,
        _status: Option<SessionStatus>,
        _title_contains: Option<&str>,
        _offset: u64,
        _limit: u64,
    ) -> Vec<Session> {
        Vec::new()
    }

    /// Batch lookup of collect-event timestamps. For each session_id in
    /// `session_ids` that `bot_uuid` has collected (collected = 1), return
    /// `(session_id, collected_at_ms)`. Sessions not collected (or with no
    /// side-table row for that bot) are omitted from the result.
    ///
    /// Used by the session-list HTTP layer to surface per-session collected
    /// state for a given participant without a per-row query.
    async fn collected_at_map(
        &self,
        _session_ids: &[&str],
        _bot_uuid: &str,
    ) -> Vec<(String, u64)> {
        Vec::new()
    }
}
