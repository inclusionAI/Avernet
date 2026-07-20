//! In-memory SessionRepo implementation.
//!
//! Intended for tests and local single-node development.
//! Production deployments use [`crate::mysql::MySqlSessionStore`].

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::debug;

use bcs_service_api::core::session::{can_reactivate, new_session_id, validate_session_id};
use bcs_service_api::port::repo::{NewSessionParams, SessionRepoPort};
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

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

#[derive(Default)]
struct MemoryState {
    sessions: HashMap<String, Session>,
    /// (session_id, bot_uuid) -> 收藏事件时间（epoch ms）。
    /// 仅在 collect 时插入（幂等：已存在则保留原时间，不刷新），uncollect 移除。
    collected: HashMap<(String, String), u64>,
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
}

impl MemorySessionRepo {
    /// Create a new empty in-memory session repository.
    pub fn new() -> Self {
        Self::default()
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
    async fn create(&self, group_id: &str, params: NewSessionParams) -> ServiceResult<Session> {
        let now = now_ms();

        // Explicit id path: used for legacy sessions ({group_id}:00000000) etc.
        if let Some(ref id) = params.id {
            if !validate_session_id(id, group_id) {
                return Err(ServiceError::SessionInvalidParams(format!(
                    "session_id {id} not valid for group {group_id}"
                )));
            }
            let mut st = self.state.write().await;
            if st.sessions.contains_key(id) {
                return Err(ServiceError::SessionInvalidParams(format!(
                    "session {id} already exists"
                )));
            }
            let sess = Session {
                id: id.clone(),
                group_id: group_id.to_string(),
                session_title: params.session_title.clone(),
                env: None,
                status: SessionStatus::Running,
                session_kind: params.session_kind,
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
            };
            st.sessions.insert(id.clone(), sess.clone());
            return Ok(sess);
        }

        // Auto-generated id: retry 3 times to handle the ~0 probability collision.
        for _attempt in 0..3 {
            let id = new_session_id(group_id).map_err(|error| {
                ServiceError::SessionInvalidParams(error.to_string())
            })?;
            let mut st = self.state.write().await;
            if st.sessions.contains_key(&id) {
                continue;
            }
            let sess = Session {
                id: id.clone(),
                group_id: group_id.to_string(),
                session_title: params.session_title.clone(),
                env: None,
                status: SessionStatus::Running,
                session_kind: params.session_kind,
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
            };
            st.sessions.insert(id.clone(), sess.clone());
            return Ok(sess);
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
    ) -> Vec<Session> {
        let st = self.state.read().await;
        let mut v: Vec<_> = st
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
                participant_id.map_or(true, |pid| {
                    s.participants.iter().any(|p| p.bot_uuid == pid)
                })
            })
            .cloned()
            .collect();
        v.sort_by_key(|s| std::cmp::Reverse(s.created_at));
        v.into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

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

    async fn reactivate(
        &self,
        session_id: &str,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session> {
        let now = now_ms();
        let mut st = self.state.write().await;
        let sess = st
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;

        can_reactivate(
            sess.status,
            sess.session_kind,
            sess.callback_status.as_deref(),
        )
        .map_err(|msg| {
            if msg == "callback is still pending" {
                ServiceError::SessionCallbackPending(session_id.to_string())
            } else {
                ServiceError::SessionInvalidParams(format!("{session_id}: {msg}"))
            }
        })?;

        sess.status = SessionStatus::Running;
        sess.output = None;
        sess.error_message = None;
        sess.callback_status = Some("pending".to_string());
        if let Some(i) = new_input {
            sess.input = Some(i);
        }
        sess.activation_count += 1;
        sess.updated_at = now;
        sess.completed_at = None;
        debug!(session_id = %session_id, activation_count = sess.activation_count, "Session reactivated");
        Ok(sess.clone())
    }

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
        if !sess
            .participants
            .iter()
            .any(|p| p.bot_uuid == bot_uuid)
        {
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

    async fn remove_participant(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Session> {
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
            .ok_or_else(|| ServiceError::SessionInvalidParams(format!(
                "participant {bot_uuid} not in session {session_id}"
            )))?;

        p.mode = Some(mode);
        sess.updated_at = now;
        Ok(sess.clone())
    }

    async fn update_callback_status(&self, session_id: &str, status: &str) -> ServiceResult<()> {
        let mut st = self.state.write().await;
        let sess = st
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
        sess.callback_status = Some(status.to_string());
        sess.updated_at = now_ms();
        Ok(())
    }

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
        st.collected.remove(&(session_id.to_string(), bot_uuid.to_string()));
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
    ) -> Vec<Session> {
        let st = self.state.read().await;
        let q = title_contains.map(|s| s.to_ascii_lowercase());
        let mut v: Vec<_> = st
            .sessions
            .values()
            .filter(|s| s.group_id == group_id)
            .filter(|s| {
                st.collected
                    .contains_key(&(s.id.clone(), bot_uuid.to_string()))
            })
            .filter(|s| status.map(|want| s.status == want).unwrap_or(true))
            .filter(|s| {
                q.as_ref().map_or(true, |q| {
                    s.session_title
                        .as_deref()
                        .unwrap_or("")
                        .to_ascii_lowercase()
                        .contains(q)
                })
            })
            .cloned()
            .map(|mut s| {
                // Surface the collect-event time on the returned session; fall
                // back to created_at (COALESCE semantics) for Ordering safety.
                s.collected_at = st
                    .collected
                    .get(&(s.id.clone(), bot_uuid.to_string()))
                    .copied()
                    .or(Some(s.created_at));
                s
            })
            .collect();
        // 由近到远：collected_at（已 or_insert 兜底为 created_at）降序。
        v.sort_by_key(|s| std::cmp::Reverse(s.collected_at.unwrap_or(s.created_at)));
        v.into_iter().skip(offset as usize).take(limit as usize).collect()
    }

    async fn collected_at_map(
        &self,
        session_ids: &[&str],
        bot_uuid: &str,
    ) -> Vec<(String, u64)> {
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
mod tests {
    use super::*;

    fn sample_params(title: Option<&str>) -> NewSessionParams {
        NewSessionParams {
            session_title: title.map(str::to_string),
            ..Default::default()
        }
    }

    fn bot_participant(id: &str, name: &str) -> Participant {
        let mut p = Participant::bot(id, bcs_service_api::ParticipantRole::Consultant);
        p.bot_name = Some(name.to_string());
        p
    }

    #[tokio::test]
    async fn list_by_group_title_filter() {
        let repo = MemorySessionRepo::new();
        repo.create("g1", sample_params(Some("Project Alpha"))).await.unwrap();
        repo.create("g1", sample_params(Some("Project Beta"))).await.unwrap();
        repo.create("g1", sample_params(Some("Other"))).await.unwrap();

        let sessions = repo.list_by_group("g1", None, 0, 10, Some("alpha"), None).await;
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].session_title.as_deref(), Some("Project Alpha"));
    }

    #[tokio::test]
    async fn list_by_group_title_filter_case_insensitive() {
        let repo = MemorySessionRepo::new();
        repo.create("g1", sample_params(Some("PROJECT ALPHA"))).await.unwrap();
        repo.create("g1", sample_params(Some("beta"))).await.unwrap();

        let sessions = repo.list_by_group("g1", None, 0, 10, Some("alpha"), None).await;
        assert_eq!(sessions.len(), 1);
    }

    #[tokio::test]
    async fn list_by_group_participant_filter() {
        let repo = MemorySessionRepo::new();
        let mut params_a = sample_params(Some("Sess A"));
        params_a.participants = vec![bot_participant("bot_1", "Alice")];
        repo.create("g1", params_a).await.unwrap();

        let mut params_b = sample_params(Some("Sess B"));
        params_b.participants = vec![bot_participant("bot_2", "Bob")];
        repo.create("g1", params_b).await.unwrap();

        let sessions = repo.list_by_group("g1", None, 0, 10, None, Some("bot_1")).await;
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].session_title.as_deref(), Some("Sess A"));
    }

    #[tokio::test]
    async fn list_by_group_title_and_participant_combined() {
        let repo = MemorySessionRepo::new();
        let mut params = sample_params(Some("Task Review"));
        params.participants = vec![bot_participant("human_1", "Human")];
        repo.create("g1", params).await.unwrap();

        // Both filters match
        let sessions = repo.list_by_group("g1", None, 0, 10, Some("review"), Some("human_1")).await;
        assert_eq!(sessions.len(), 1);

        // participant matches but title doesn't
        let sessions = repo.list_by_group("g1", None, 0, 10, Some("xyz"), Some("human_1")).await;
        assert_eq!(sessions.len(), 0);
    }

    #[tokio::test]
    async fn latest_running_still_works_with_new_params() {
        let repo = MemorySessionRepo::new();
        repo.create("g1", sample_params(None)).await.unwrap();
        // latest_running calls list_by_group internally with None, None
        let latest = repo.latest_running("g1").await;
        assert!(latest.is_some());
    }

    /// Participant filter applies BEFORE pagination. Create 25 sessions
    /// where only the very last one has the target participant, then
    /// query with LIMIT 5 — it should still be found.
    #[tokio::test]
    async fn list_by_group_participant_filter_before_pagination() {
        let repo = MemorySessionRepo::new();
        let target = "human_test_99";
        // Create 24 sessions without the target participant
        for i in 0..24 {
            let mut params = sample_params(Some(&format!("Session {}", i)));
            params.participants = vec![Participant::bot("bot_a", bcs_service_api::ParticipantRole::Consultant)];
            repo.create("g1", params).await.unwrap();
        }
        // Create the 25th session WITH the target participant
        let mut params = sample_params(Some("Target Session"));
        params.participants = vec![
            Participant::bot("bot_a", bcs_service_api::ParticipantRole::Consultant),
            {
                let mut p = Participant::human(target, bcs_service_api::ParticipantRole::Observer);
                p.mode = Some(ParticipantMode::Present);
                p
            },
        ];
        repo.create("g1", params).await.unwrap();

        // Query with LIMIT 5 — the target is at position 25 (well past the limit)
        let sessions = repo.list_by_group("g1", None, 0, 5, None, Some(target)).await;
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].session_title.as_deref(), Some("Target Session"));
    }

    #[tokio::test]
    async fn collection_collect_then_list_then_uncollect() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group";
        let sess = repo
            .create(
                gid,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    participants: vec![Participant::bot("bot1", bcs_service_api::ParticipantRole::Driver)],
                    ..Default::default()
                },
            )
            .await
            .expect("create");

        // not collected yet
        let listed = repo.list_collected_by_group(gid, "bot1", None, None, 0, 10).await;
        assert!(listed.is_empty());

        repo.collect(&sess.id, "bot1").await.expect("collect");
        let listed = repo.list_collected_by_group(gid, "bot1", None, None, 0, 10).await;
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].id, sess.id);

        // other bot does not see bot1's collection
        let other = repo.list_collected_by_group(gid, "bot2", None, None, 0, 10).await;
        assert!(other.is_empty());

        repo.uncollect(&sess.id, "bot1").await.expect("uncollect");
        let listed = repo.list_collected_by_group(gid, "bot1", None, None, 0, 10).await;
        assert!(listed.is_empty());
    }

    #[tokio::test]
    async fn collection_non_participant_collect_errors() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group2";
        let sess = repo
            .create(
                gid,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    participants: vec![Participant::bot("bot1", bcs_service_api::ParticipantRole::Driver)],
                    ..Default::default()
                },
            )
            .await
            .expect("create");
        let err = repo.collect(&sess.id, "not-a-participant").await;
        assert!(err.is_err(), "collect by non-participant must error");
    }

    #[tokio::test]
    async fn collection_uncollect_idempotent_for_non_participant() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group3";
        let sess = repo
            .create(
                gid,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    participants: vec![Participant::bot("bot1", bcs_service_api::ParticipantRole::Driver)],
                    ..Default::default()
                },
            )
            .await
            .expect("create");
        // uncollect a never-collected, still-participant session -> Ok
        repo.uncollect(&sess.id, "bot1").await.expect("uncollect not collected ok");
        // uncollect a non-participant -> Ok (idempotent)
        repo.uncollect(&sess.id, "nobody").await.expect("uncollect non-participant ok");
    }

    #[tokio::test]
    async fn collection_respects_status_and_title_filter() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group4";
        let s_running = repo
            .create(gid, NewSessionParams {
                session_kind: SessionKind::Chat,
                session_title: Some("Alpha Report".into()),
                participants: vec![Participant::bot("bot1", bcs_service_api::ParticipantRole::Driver)],
                ..Default::default()
            })
            .await
            .expect("create");
        let s_to_complete = repo
            .create(gid, NewSessionParams {
                session_kind: SessionKind::Chat,
                session_title: Some("Beta Note".into()),
                participants: vec![Participant::bot("bot1", bcs_service_api::ParticipantRole::Driver)],
                ..Default::default()
            })
            .await
            .expect("create");
        repo.complete_if_running(&s_to_complete.id, None, None).await.expect("complete");
        repo.collect(&s_running.id, "bot1").await.expect("collect running");
        repo.collect(&s_to_complete.id, "bot1").await.expect("collect completed");

        let only_running = repo.list_collected_by_group(
            gid, "bot1", Some(SessionStatus::Running), None, 0, 10,
        ).await;
        assert_eq!(only_running.len(), 1);
        assert_eq!(only_running[0].id, s_running.id);

        let only_alpha = repo.list_collected_by_group(
            gid, "bot1", None, Some("alpha"), 0, 10,
        ).await;
        assert_eq!(only_alpha.len(), 1);
        assert_eq!(only_alpha[0].id, s_running.id);
    }

    #[tokio::test]
    async fn collection_lost_when_participant_removed() {
        let repo = MemorySessionRepo::new();
        let gid = "col-group5";
        let sess = repo
            .create(gid, NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![Participant::bot("bot1", bcs_service_api::ParticipantRole::Driver)],
                ..Default::default()
            })
            .await
            .expect("create");
        repo.collect(&sess.id, "bot1").await.expect("collect");
        assert_eq!(
            repo.list_collected_by_group(gid, "bot1", None, None, 0, 10).await.len(),
            1
        );
        repo.remove_participant(&sess.id, "bot1").await.expect("remove");
        // after leaving, collection mark is gone (memory set must be pruned)
        assert!(repo
            .list_collected_by_group(gid, "bot1", None, None, 0, 10)
            .await
            .is_empty());
    }
}
