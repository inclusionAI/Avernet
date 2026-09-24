//! In-memory SessionRepo implementation.
//!
//! Intended for tests and local single-node development.
//! Production deployments use [`crate::mysql::MySqlSessionStore`].

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::debug;

use bcs_event_store::MemoryEventStore;
use bcs_service_api::core::session::{
    can_reactivate, new_channel_session_id, new_session_id, validate_session_id,
};
use bcs_service_api::port::repo::{
    AddSessionParticipantWithEvent, ClaimSessionCallback, CompleteSessionCallback,
    CompleteSessionWithEvent, CreateSessionWithEvent, NewSessionParams,
    RemoveSessionParticipantWithEvent, SessionCallbackClaim, SessionRepoPort,
    UpdateSessionParticipantMessageViewScopeWithEvent,
};
use bcs_service_api::types::MessageViewScope;
use bcs_service_api::{
    GroupSessionMetricCount, GroupSessionMetricsSnapshotPort, Participant, ParticipantMode,
    ServiceError, ServiceResult, Session, SessionKind, SessionStatus,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// ServiceInvocation sessions start with callback_status="pending"; others start with None.
fn initial_callback_status(kind: SessionKind) -> Option<String> {
    if matches!(kind, SessionKind::ServiceInvocation) {
        Some("pending".to_string())
    } else {
        None
    }
}

fn session_from_params(
    id: String,
    group_id: &str,
    params: &NewSessionParams,
    now: u64,
) -> Session {
    Session {
        id,
        group_id: group_id.to_string(),
        session_title: params.session_title.clone(),
        env: None,
        status: SessionStatus::Running,
        session_kind: params.session_kind,
        message_visibility_version: params.message_visibility_version,
        participants: params.participants.clone(),
        group_version: params.group_version,
        caller_id: params.caller_id.clone(),
        input: params.input.clone(),
        output: None,
        error_message: None,
        callback_status: initial_callback_status(params.session_kind),
        activation_count: 1,
        caller_principal: params.caller_principal.clone(),
        created_by: params.created_by.clone(),
        meta: params.meta.clone(),
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: now,
        updated_at: now,
        completed_at: None,
        collected_at: None,
    }
}

fn validate_session_event_scope(
    session: &Session,
    event: &bcs_service_api::port::repo::AppendEventRecord,
) -> ServiceResult<()> {
    if event.event.scope.group_id.as_deref() != Some(session.group_id.as_str())
        || event.event.scope.session_id.as_deref() != Some(session.id.as_str())
    {
        return Err(ServiceError::InvalidOperation {
            message: "Session Event scope does not match the mutated Session".to_string(),
            request_id: None,
        });
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

#[derive(Default)]
struct MemoryState {
    sessions: HashMap<String, Session>,
    callback_leases: HashMap<String, CallbackLeaseState>,
    /// (session_id, bot_uuid) -> 收藏事件时间（epoch ms）。
    /// 仅在 collect 时插入（幂等：已存在则保留原时间，不刷新），uncollect 移除。
    collected: HashMap<(String, String), u64>,
}

#[derive(Default)]
struct CallbackLeaseState {
    token: Option<i64>,
    owner: Option<String>,
    until_ms: Option<u64>,
}

impl CallbackLeaseState {
    fn for_session_kind(kind: SessionKind) -> Self {
        Self {
            token: matches!(kind, SessionKind::ServiceInvocation).then_some(0),
            owner: None,
            until_ms: None,
        }
    }
}

fn insert_session(state: &mut MemoryState, session: Session) {
    state.callback_leases.insert(
        session.id.clone(),
        CallbackLeaseState::for_session_kind(session.session_kind),
    );
    state.sessions.insert(session.id.clone(), session);
}

// ---------------------------------------------------------------------------
// Public type
// ---------------------------------------------------------------------------

/// In-memory implementation of [`SessionRepoPort`].
///
/// All state is held in a single `RwLock<HashMap>`. Suitable for tests and
/// local single-node development; not suitable for multi-node deployments.
#[derive(Default)]
pub struct MemorySessionRepo {
    state: Arc<RwLock<MemoryState>>,
    event_store: Option<Arc<MemoryEventStore>>,
    registry: Arc<crate::registry::MemorySessionRegistry>,
}

impl MemorySessionRepo {
    pub fn session_registry(&self) -> Arc<crate::registry::MemorySessionRegistry> { self.registry.clone() }

    /// Create a new empty in-memory session repository.
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_event_store(mut self, event_store: Arc<MemoryEventStore>) -> Self {
        self.event_store = Some(event_store);
        self
    }
}

#[async_trait]
impl GroupSessionMetricsSnapshotPort for MemorySessionRepo {
    async fn group_session_counts(&self) -> ServiceResult<Vec<GroupSessionMetricCount>> {
        let st = self.state.read().await;
        let mut counts: Vec<GroupSessionMetricCount> = Vec::new();
        for session in st.sessions.values() {
            if let Some(existing) = counts.iter_mut().find(|count| {
                count.status == session.status && count.session_kind == session.session_kind
            }) {
                existing.count = existing.count.saturating_add(1);
            } else {
                counts.push(GroupSessionMetricCount {
                    status: session.status,
                    session_kind: session.session_kind,
                    count: 1,
                });
            }
        }
        Ok(counts)
    }
}

// ---------------------------------------------------------------------------
// SessionRepoPort impl
// ---------------------------------------------------------------------------

#[async_trait]
impl SessionRepoPort for MemorySessionRepo {
    async fn validate_session_registry(&self) -> ServiceResult<()> {
        let registry = self.registry.entries.lock().await;
        for row in registry.values() { crate::registry::check_type(row, row.session_type)?; }
        for id in self.state.read().await.sessions.keys() {
            let row = registry.get(id).ok_or_else(|| ServiceError::InternalError("session_registry_missing".into()))?;
            crate::registry::check_type(row, bcs_service_api::port::repo::session_registry::SessionType::Group)?;
        }
        Ok(())
    }
    async fn ensure_direct_session(&self, id: &str) -> ServiceResult<bcs_service_api::port::repo::session_registry::SessionRegistration> {
        use bcs_service_api::port::repo::session_registry::*;
        validate_direct_session_id(id)?;
        let mut entries = self.registry.entries.lock().await;
        let row = entries.entry(id.into()).or_insert_with(|| SessionRegistration {
            session_id: id.into(), session_type: SessionType::DirectA2a, current_msg_seq: Some(0),
        });
        crate::registry::check_type(row, SessionType::DirectA2a)?;
        Ok(row.clone())
    }
    async fn session_registration(&self, id: &str) -> ServiceResult<Option<bcs_service_api::port::repo::session_registry::SessionRegistration>> {
        Ok(self.registry.entries.lock().await.get(id).cloned())
    }

    async fn create(&self, group_id: &str, params: NewSessionParams) -> ServiceResult<Session> { self.repo_create(group_id, params).await }

    async fn create_with_event(&self, command: CreateSessionWithEvent) -> ServiceResult<Session> { self.repo_create_with_event(command).await }

    async fn create_channel(
        &self,
        group_id: &str,
        channel_type: &str,
        params: NewSessionParams,
    ) -> ServiceResult<Session> {
        for _ in 0..3 {
            let id = new_channel_session_id(group_id, channel_type)
                .map_err(|error| ServiceError::SessionInvalidParams(error.to_string()))?;
            let mut registry = self.registry.entries.lock().await;
            check_group_claim(&registry, &id)?;
            let mut state = self.state.write().await;
            if state.sessions.contains_key(&id) {
                continue;
            }
            let session = session_from_params(id.clone(), group_id, &params, now_ms());
            commit_group_claim(&mut registry, &id);
            insert_session(&mut state, session.clone());
            return Ok(session);
        }
        Err(ServiceError::SessionInvalidParams(
            "session_id collision retry exhausted (3 attempts)".to_string(),
        ))
    }

    async fn get(&self, session_id: &str) -> Option<Session> {
        self.state.read().await.sessions.get(session_id).cloned()
    }

    async fn belongs_to_group(&self, session_id: &str, group_id: &str) -> bool {
        self.state
            .read()
            .await
            .sessions
            .get(session_id)
            .map(|s| s.group_id == group_id)
            .unwrap_or(false)
    }

    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> Vec<Session> { self.repo_list_by_group(group_id, status, offset, limit, title_contains, participant_id).await }

    async fn latest_running(&self, group_id: &str) -> Option<Session> {
        self.list_by_group(group_id, Some(SessionStatus::Running), 0, 1, None, None)
            .await
            .into_iter()
            .next()
    }

    async fn count_running_service(&self, group_id: &str) -> u64 {
        let st = self.state.read().await;
        st.sessions
            .values()
            .filter(|s| {
                s.group_id == group_id
                    && matches!(s.session_kind, SessionKind::ServiceInvocation)
                    && matches!(s.status, SessionStatus::Running)
            })
            .count() as u64
    }

    /// Mirrors [`SessionRepoPort::list_by_group`] filters exactly but returns
    /// the total count without applying offset/limit pagination.
    async fn count_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> ServiceResult<u64> {
        let st = self.state.read().await;
        Ok(st
            .sessions
            .values()
            .filter(|s| s.group_id == group_id)
            .filter(|s| status.map(|want| s.status == want).unwrap_or(true))
            .filter(|s| {
                title_contains.map_or(true, |q| {
                    s.session_title
                        .as_deref()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(&q.to_lowercase())
                })
            })
            .filter(|s| {
                participant_id.map_or(true, |pid| s.participants.iter().any(|p| p.bot_uuid == pid))
            })
            .count() as u64)
    }

    async fn list_running_service(&self, offset: u64, limit: u64) -> Vec<Session> {
        let st = self.state.read().await;
        let mut v: Vec<_> = st
            .sessions
            .values()
            .filter(|s| {
                matches!(s.session_kind, SessionKind::ServiceInvocation)
                    && matches!(s.status, SessionStatus::Running)
            })
            .cloned()
            .collect();
        v.sort_by_key(|s| s.created_at);
        v.into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn list_recoverable_callbacks(
        &self,
        now_ms: u64,
        after_session_id: Option<&str>,
        limit: u64,
    ) -> ServiceResult<Vec<Session>> {
        let st = self.state.read().await;
        let mut sessions: Vec<_> = st
            .sessions
            .values()
            .filter(|session| {
                matches!(session.session_kind, SessionKind::ServiceInvocation)
                    && matches!(session.status, SessionStatus::Completed)
                    && session.callback_status.as_deref() == Some("pending")
                    && after_session_id.map_or(true, |after| session.id.as_str() > after)
                    && st.callback_leases.get(&session.id).is_some_and(|lease| {
                        lease.token.is_some()
                            && lease.until_ms.map_or(true, |until_ms| until_ms <= now_ms)
                    })
            })
            .cloned()
            .collect();
        sessions.sort_by(|left, right| left.id.cmp(&right.id));
        sessions.truncate(limit as usize);
        Ok(sessions)
    }

    async fn list_running_service_after(
        &self,
        after_session_id: Option<&str>,
        limit: u64,
    ) -> ServiceResult<Vec<Session>> {
        if limit == 0 { return Ok(Vec::new()); }
        let state = self.state.read().await;
        // Keep at most one page of references; clone only the returned Sessions.
        let mut page = std::collections::BTreeMap::new();
        for session in state.sessions.values().filter(|session| {
            session.session_kind == SessionKind::ServiceInvocation
                && session.status == SessionStatus::Running
                && after_session_id.map_or(true, |cursor| session.id.as_str() > cursor)
        }) {
            page.insert(session.id.as_str(), session);
            if page.len() as u64 > limit { page.pop_last(); }
        }
        Ok(page.into_values().cloned().collect())
    }

    async fn complete_running_service_activation(
        &self,
        session_id: &str,
        expected_activation_count: i32,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> ServiceResult<Option<Session>> {
        let mut state = self.state.write().await;
        let Some(session) = state.sessions.get_mut(session_id) else { return Ok(None); };
        if session.session_kind != SessionKind::ServiceInvocation
            || session.status != SessionStatus::Running
            || session.activation_count != expected_activation_count
        {
            return Ok(None);
        }
        let now = now_ms();
        session.status = SessionStatus::Completed;
        session.output = output;
        session.error_message = error;
        session.updated_at = now;
        session.completed_at = Some(now);
        Ok(Some(session.clone()))
    }

    /// CAS complete: only flips status if currently Running.
    /// Returns `Ok(None)` if already Completed (idempotent).
    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> ServiceResult<Option<Session>> {
        let now = now_ms();
        let mut st = self.state.write().await;
        let sess = st
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;

        // CAS: already completed → no-op
        if matches!(sess.status, SessionStatus::Completed) {
            return Ok(None);
        }

        sess.status = SessionStatus::Completed;
        sess.output = output;
        sess.error_message = error;
        sess.updated_at = now;
        sess.completed_at = Some(now);
        debug!(session_id = %session_id, "Session completed");
        Ok(Some(sess.clone()))
    }

    async fn complete_if_running_with_event(
        &self,
        command: CompleteSessionWithEvent,
    ) -> ServiceResult<Option<Session>> { self.repo_complete_if_running_with_event(command).await }

    async fn reactivate(
        &self,
        session_id: &str,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session> { self.repo_reactivate(session_id, new_input).await }

    async fn reactivate_if_completed_activation(
        &self,
        session_id: &str,
        expected_activation_count: i32,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Option<Session>> { self.repo_reactivate_if_completed_activation(session_id, expected_activation_count, new_input).await }

    async fn add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> ServiceResult<Session> {
        let now = now_ms();
        let mut st = self.state.write().await;
        let sess = st
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;

        // Idempotent: skip if bot already in list.
        let bot_uuid = participant.bot_uuid.clone();
        if !sess.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
            sess.participants.push(participant);
            sess.updated_at = now;
        }

        // Record join_seq for new participant visibility window
        let join_seq = sess.current_msg_seq;
        let mut join_map: serde_json::Map<String, serde_json::Value> = sess
            .participant_join_seq
            .as_ref()
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();
        join_map.insert(
            bot_uuid,
            serde_json::Value::Number(serde_json::Number::from(join_seq)),
        );
        sess.participant_join_seq = Some(serde_json::Value::Object(join_map));

        Ok(sess.clone())
    }

    async fn add_participant_with_event(
        &self,
        command: AddSessionParticipantWithEvent,
    ) -> ServiceResult<Session> { self.repo_add_participant_with_event(command).await }

    async fn remove_participant(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<Session> {
        let now = now_ms();
        let mut st = self.state.write().await;
        let sess = st
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;

        let before = sess.participants.len();
        sess.participants.retain(|p| p.bot_uuid != bot_uuid);
        if sess.participants.len() == before {
            return Err(ServiceError::SessionInvalidParams(format!(
                "participant {bot_uuid} not in session {session_id}"
            )));
        }
        sess.updated_at = now;
        let updated = sess.clone();
        // collection mark is per-participant; leaving drops it
        st.collected
            .retain(|key, _| !(key.0 == session_id && key.1 == bot_uuid));
        Ok(updated)
    }

    async fn remove_participant_with_event(
        &self,
        command: RemoveSessionParticipantWithEvent,
    ) -> ServiceResult<Session> { self.repo_remove_participant_with_event(command).await }

    async fn update_participant_mode(
        &self,
        session_id: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<Session> {
        let now = now_ms();
        let mut st = self.state.write().await;
        let sess = st
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;

        let p = sess
            .participants
            .iter_mut()
            .find(|p| p.bot_uuid == bot_uuid)
            .ok_or_else(|| {
                ServiceError::SessionInvalidParams(format!(
                    "participant {bot_uuid} not in session {session_id}"
                ))
            })?;

        p.mode = Some(mode);
        sess.updated_at = now;
        Ok(sess.clone())
    }

    async fn update_participant_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<Session> {
        let now = now_ms();
        let mut state = self.state.write().await;
        let session = state
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
        let participant = session
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == actor_id)
            .ok_or_else(|| {
                ServiceError::SessionInvalidParams(format!(
                    "participant {actor_id} not in session {session_id}"
                ))
            })?;
        if !message_view_scope.is_valid_for(participant.actor_kind) {
            return Err(ServiceError::SessionInvalidParams(
                "Bot participants must use full message_view_scope".to_string(),
            ));
        }
        participant.message_view_scope = message_view_scope;
        session.updated_at = now;
        Ok(session.clone())
    }

    async fn update_participant_mode_and_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        mode: Option<ParticipantMode>,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<Session> {
        let now = now_ms();
        let mut state = self.state.write().await;
        let session = state
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
        let participant = session
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == actor_id)
            .ok_or_else(|| {
                ServiceError::SessionInvalidParams(format!(
                    "participant {actor_id} not in session {session_id}"
                ))
            })?;
        if !message_view_scope.is_valid_for(participant.actor_kind)
            || mode.is_some_and(|mode| !mode.is_valid_for(participant.actor_kind))
        {
            return Err(ServiceError::SessionInvalidParams(
                "Participant mode or message_view_scope is invalid for the actor kind".to_string(),
            ));
        }
        participant.message_view_scope = message_view_scope;
        if let Some(mode) = mode {
            participant.mode = Some(mode);
        }
        session.updated_at = now;
        Ok(session.clone())
    }

    async fn update_participant_message_view_scope_with_event(
        &self,
        command: UpdateSessionParticipantMessageViewScopeWithEvent,
    ) -> ServiceResult<Session> { self.repo_update_participant_message_view_scope_with_event(command).await }

    async fn update_callback_status(&self, session_id: &str, status: &str) -> ServiceResult<()> {
        let mut st = self.state.write().await;
        {
            let sess = st
                .sessions
                .get_mut(session_id)
                .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
            sess.callback_status = Some(status.to_string());
            sess.updated_at = now_ms();
        }
        if status != "pending"
            && let Some(lease) = st.callback_leases.get_mut(session_id)
        {
            lease.owner = None;
            lease.until_ms = None;
        }
        Ok(())
    }

    async fn claim_callback(
        &self,
        command: ClaimSessionCallback,
    ) -> ServiceResult<Option<SessionCallbackClaim>> { self.repo_claim_callback(command).await }

    async fn complete_callback(&self, command: CompleteSessionCallback) -> ServiceResult<bool> { self.repo_complete_callback(command).await }

    async fn update_title(
        &self,
        session_id: &str,
        title: Option<String>,
    ) -> ServiceResult<Session> {
        let mut st = self.state.write().await;
        let sess = st
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
        sess.session_title = title;
        sess.updated_at = now_ms();
        Ok(sess.clone())
    }

    async fn list_group_ids_by_session_participant(&self, bot_uuid: &str) -> Vec<String> {
        let st = self.state.read().await;
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for sess in st.sessions.values() {
            if sess.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
                seen.insert(sess.group_id.clone());
            }
        }
        seen.into_iter().collect()
    }

    async fn delete(&self, session_id: &str) -> ServiceResult<bool> {
        let mut st = self.state.write().await;
        let existed = st.sessions.remove(session_id).is_some();
        if existed {
            st.callback_leases.remove(session_id);
            st.collected.retain(|key, _| key.0 != session_id);
        }
        Ok(existed)
    }

    async fn collect(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        let mut st = self.state.write().await;
        let session = st
            .sessions
            .get(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
        if !session.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
            return Err(ServiceError::SessionNotFound(format!(
                "participant {bot_uuid} not in session {session_id}"
            )));
        }
        // Idempotent on the timestamp: a repeat collect keeps the original event time
        // (entry().or_insert) so the list ordering reflects first-collection time.
        st.collected
            .entry((session_id.to_string(), bot_uuid.to_string()))
            .or_insert(now_ms());
        Ok(())
    }

    async fn uncollect(&self, session_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        let mut st = self.state.write().await;
        // Idempotent: session must exist; otherwise no-op removal.
        if !st.sessions.contains_key(session_id) {
            return Err(ServiceError::SessionNotFound(session_id.to_string()));
        }
        st.collected
            .remove(&(session_id.to_string(), bot_uuid.to_string()));
        Ok(())
    }

    async fn list_collected_by_group(
        &self,
        group_id: &str,
        bot_uuid: &str,
        status: Option<SessionStatus>,
        title_contains: Option<&str>,
        offset: u64,
        limit: u64,
    ) -> Vec<Session> { self.repo_list_collected_by_group(group_id, bot_uuid, status, title_contains, offset, limit).await }

    async fn collected_at_map(&self, session_ids: &[&str], bot_uuid: &str) -> Vec<(String, u64)> {
        let st = self.state.read().await;
        session_ids
            .iter()
            .filter_map(|sid| {
                let ts = st
                    .collected
                    .get(&(sid.to_string(), bot_uuid.to_string()))
                    .copied()?;
                Some((sid.to_string(), ts))
            })
            .collect()
    }
}

#[cfg(test)]
#[path = "memory_tests/mod.rs"]
mod tests;

fn check_group_claim(entries: &HashMap<String, bcs_service_api::port::repo::session_registry::SessionRegistration>, id: &str) -> ServiceResult<()> {
    if let Some(row) = entries.get(id) { crate::registry::check_type(row, bcs_service_api::port::repo::session_registry::SessionType::Group)?; }
    Ok(())
}
fn commit_group_claim(entries: &mut HashMap<String, bcs_service_api::port::repo::session_registry::SessionRegistration>, id: &str) {
    use bcs_service_api::port::repo::session_registry::*;
    entries.insert(id.into(), SessionRegistration { session_id: id.into(), session_type: SessionType::Group, current_msg_seq: None });
}

#[path = "memory_operations/operations_1.rs"]
mod memory_operations_operations_1;
