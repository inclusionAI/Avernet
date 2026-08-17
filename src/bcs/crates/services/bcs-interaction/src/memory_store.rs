use std::collections::{HashMap, HashSet};

use async_trait::async_trait;
use bcs_service_api::{
    InteractionInsertResult, InteractionKey, InteractionRecord, InteractionResolveClaim,
    InteractionResolveCommit, InteractionStatus, InteractionStorePort, ServiceResult,
};
use tokio::sync::RwLock;

/// Multiple concurrent prompts are supported, but a single Provider/run must
/// not be able to grow the process-local store without bound.
pub const MAX_ACTIVE_INTERACTIONS_PER_RUN: usize = 32;
pub const MAX_ACTIVE_INTERACTIONS_PER_SESSION: usize = 256;

#[derive(Debug, Default)]
struct MemoryInteractionState {
    records: HashMap<InteractionKey, InteractionRecord>,
    by_session: HashMap<String, HashSet<InteractionKey>>,
    by_run: HashMap<String, HashSet<InteractionKey>>,
}

#[derive(Debug, Default)]
pub struct MemoryInteractionStore {
    state: RwLock<MemoryInteractionState>,
}

impl MemoryInteractionStore {
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl InteractionStorePort for MemoryInteractionStore {
    async fn insert_requested(
        &self,
        record: InteractionRecord,
    ) -> ServiceResult<InteractionInsertResult> {
        let mut state = self.state.write().await;
        if let Some(existing) = state.records.get(&record.key) {
            if existing.status.is_terminal() {
                return Ok(InteractionInsertResult::TerminalPreserved);
            }
            return Ok(if existing.requested_payload == record.requested_payload {
                InteractionInsertResult::IdenticalDuplicate
            } else {
                InteractionInsertResult::ConflictingDuplicate
            });
        }

        let active_in_run = state
            .by_run
            .get(&record.key.bcs_run_id)
            .into_iter()
            .flatten()
            .filter_map(|key| state.records.get(key))
            .filter(|existing| existing.status.is_active())
            .count();
        let active_in_session = state
            .by_session
            .get(&record.bcs_session_id)
            .into_iter()
            .flatten()
            .filter_map(|key| state.records.get(key))
            .filter(|existing| existing.status.is_active())
            .count();
        if active_in_run >= MAX_ACTIVE_INTERACTIONS_PER_RUN
            || active_in_session >= MAX_ACTIVE_INTERACTIONS_PER_SESSION
        {
            return Ok(InteractionInsertResult::CapacityExceeded);
        }

        state
            .by_session
            .entry(record.bcs_session_id.clone())
            .or_default()
            .insert(record.key.clone());
        state
            .by_run
            .entry(record.key.bcs_run_id.clone())
            .or_default()
            .insert(record.key.clone());
        state.records.insert(record.key.clone(), record);
        Ok(InteractionInsertResult::Stored)
    }

    async fn get(&self, key: &InteractionKey) -> ServiceResult<Option<InteractionRecord>> {
        Ok(self.state.read().await.records.get(key).cloned())
    }

    async fn list_pending(&self, bcs_session_id: &str) -> ServiceResult<Vec<InteractionRecord>> {
        let state = self.state.read().await;
        let mut records = state
            .by_session
            .get(bcs_session_id)
            .into_iter()
            .flatten()
            .filter_map(|key| state.records.get(key))
            .filter(|record| record.status == InteractionStatus::Pending)
            .cloned()
            .collect::<Vec<_>>();
        records.sort_by(|left, right| {
            left.requested_at_ms
                .cmp(&right.requested_at_ms)
                .then_with(|| left.key.bcs_run_id.cmp(&right.key.bcs_run_id))
                .then_with(|| left.key.interaction_id.cmp(&right.key.interaction_id))
        });
        Ok(records)
    }

    async fn claim_resolution(
        &self,
        key: &InteractionKey,
        idempotency_key: &str,
        resolution_fingerprint: &str,
    ) -> ServiceResult<InteractionResolveClaim> {
        let mut state = self.state.write().await;
        let Some(record) = state.records.get_mut(key) else {
            return Ok(InteractionResolveClaim::NotFound);
        };
        match record.status {
            InteractionStatus::Pending if record.in_flight => {
                Ok(InteractionResolveClaim::InFlight(record.status))
            }
            InteractionStatus::Pending => {
                record.in_flight = true;
                Ok(InteractionResolveClaim::Acquired(record.clone()))
            }
            InteractionStatus::Accepted | InteractionStatus::Resolved
                if record.accepted_idempotency_key.as_deref() == Some(idempotency_key)
                    && record.accepted_resolution_fingerprint.as_deref()
                        == Some(resolution_fingerprint) =>
            {
                Ok(InteractionResolveClaim::AlreadyAccepted(record.clone()))
            }
            InteractionStatus::Accepted => {
                Ok(InteractionResolveClaim::AcceptedDifferent(record.clone()))
            }
            InteractionStatus::Resolved | InteractionStatus::Invalidated => {
                Ok(InteractionResolveClaim::Terminal(record.clone()))
            }
        }
    }

    async fn finish_resolution(
        &self,
        key: &InteractionKey,
        commit: InteractionResolveCommit,
    ) -> ServiceResult<Option<InteractionRecord>> {
        let mut state = self.state.write().await;
        let Some(record) = state.records.get_mut(key) else {
            return Ok(None);
        };
        record.in_flight = false;
        match commit {
            InteractionResolveCommit::Accepted {
                idempotency_key,
                resolution_fingerprint,
                resolver_actor_id,
                accepted_at_ms,
            } if matches!(
                record.status,
                InteractionStatus::Pending | InteractionStatus::Resolved
            ) =>
            {
                if record.status == InteractionStatus::Pending {
                    record.status = InteractionStatus::Accepted;
                }
                record.accepted_idempotency_key = Some(idempotency_key);
                record.accepted_resolution_fingerprint = Some(resolution_fingerprint);
                record.resolved_by_actor_id = Some(resolver_actor_id);
                record.accepted_at_ms = Some(accepted_at_ms);
            }
            InteractionResolveCommit::RetryableFailure => {}
            InteractionResolveCommit::Invalidated {
                resolver_actor_id,
                reason,
                invalidated_at_ms,
            } if record.status.is_active() => {
                record.status = InteractionStatus::Invalidated;
                record.resolved_by_actor_id = Some(resolver_actor_id);
                record.invalidation_reason = Some(reason);
                record.terminal_at_ms = Some(invalidated_at_ms);
            }
            _ => {}
        }
        Ok(Some(record.clone()))
    }

    async fn mark_resolved(
        &self,
        key: &InteractionKey,
        resolved_at_ms: u64,
    ) -> ServiceResult<Option<InteractionRecord>> {
        let mut state = self.state.write().await;
        let Some(record) = state.records.get_mut(key) else {
            return Ok(None);
        };
        if record.status.is_active() {
            record.status = InteractionStatus::Resolved;
            record.in_flight = false;
            record.terminal_at_ms = Some(resolved_at_ms);
        }
        Ok(Some(record.clone()))
    }

    async fn invalidate_run(
        &self,
        bcs_run_id: &str,
        reason: &str,
        invalidated_at_ms: u64,
    ) -> ServiceResult<Vec<InteractionRecord>> {
        let mut state = self.state.write().await;
        let keys = state.by_run.get(bcs_run_id).cloned().unwrap_or_default();
        let mut invalidated = Vec::new();
        for key in keys {
            let Some(record) = state.records.get_mut(&key) else {
                continue;
            };
            if !record.status.is_active() {
                continue;
            }
            record.status = InteractionStatus::Invalidated;
            record.in_flight = false;
            record.invalidation_reason = Some(reason.to_string());
            record.terminal_at_ms = Some(invalidated_at_ms);
            invalidated.push(record.clone());
        }
        Ok(invalidated)
    }

    async fn cleanup_terminal(&self, terminal_before_ms: u64) -> ServiceResult<usize> {
        let mut state = self.state.write().await;
        let remove = state
            .records
            .iter()
            .filter(|(_, record)| {
                record.status.is_terminal()
                    && record
                        .terminal_at_ms
                        .is_some_and(|terminal_at| terminal_at < terminal_before_ms)
            })
            .map(|(key, _)| key.clone())
            .collect::<Vec<_>>();

        for key in &remove {
            let Some(record) = state.records.remove(key) else {
                continue;
            };
            if let Some(keys) = state.by_session.get_mut(&record.bcs_session_id) {
                keys.remove(key);
                if keys.is_empty() {
                    state.by_session.remove(&record.bcs_session_id);
                }
            }
            if let Some(keys) = state.by_run.get_mut(&key.bcs_run_id) {
                keys.remove(key);
                if keys.is_empty() {
                    state.by_run.remove(&key.bcs_run_id);
                }
            }
        }
        Ok(remove.len())
    }
}

#[cfg(test)]
mod tests {
    use bcs_domain::{BotDeliveryTarget, RedactedToken};
    use bcs_service_api::{
        InteractionInsertResult, InteractionKey, InteractionKind, InteractionRecord,
        InteractionResolveClaim, InteractionResolveCommit, InteractionStatus, InteractionStorePort,
    };
    use serde_json::json;

    use super::{
        MAX_ACTIVE_INTERACTIONS_PER_RUN, MAX_ACTIVE_INTERACTIONS_PER_SESSION,
        MemoryInteractionStore,
    };

    fn record(run_id: &str, interaction_id: &str, session_id: &str) -> InteractionRecord {
        InteractionRecord {
            key: InteractionKey {
                bcs_run_id: run_id.to_string(),
                interaction_id: interaction_id.to_string(),
            },
            provider_run_id: format!("provider-{run_id}"),
            kind: InteractionKind::Exec,
            bcs_session_id: session_id.to_string(),
            group_id: "group-1".to_string(),
            bot_id: "bot-1".to_string(),
            run_deadline_ms: 10_000,
            provider_target: BotDeliveryTarget::HttpProvider {
                bot_id: "bot-1".to_string(),
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "ref-1".to_string(),
                webhook_url: "https://provider.example/webhook".to_string(),
                bcs_to_provider_token: RedactedToken::new("secret"),
                protocol_version: "2.0".to_string(),
            },
            provider_bypass_headers: Vec::new(),
            requested_payload: json!({"phase":"requested","interactionId":interaction_id}),
            status: InteractionStatus::Pending,
            in_flight: false,
            accepted_idempotency_key: None,
            accepted_resolution_fingerprint: None,
            resolved_by_actor_id: None,
            requested_at_ms: 100,
            accepted_at_ms: None,
            terminal_at_ms: None,
            invalidation_reason: None,
        }
    }

    #[tokio::test]
    async fn inserts_duplicates_and_preserves_first_conflicting_payload() {
        let store = MemoryInteractionStore::new();
        let first = record("run-1", "interaction-1", "session-1");
        assert_eq!(
            store.insert_requested(first.clone()).await.unwrap(),
            InteractionInsertResult::Stored
        );
        assert_eq!(
            store.insert_requested(first.clone()).await.unwrap(),
            InteractionInsertResult::IdenticalDuplicate
        );

        let mut conflicting = first.clone();
        conflicting.requested_payload = json!({"different":true});
        assert_eq!(
            store.insert_requested(conflicting).await.unwrap(),
            InteractionInsertResult::ConflictingDuplicate
        );
        assert_eq!(store.get(&first.key).await.unwrap(), Some(first));
    }

    #[tokio::test]
    async fn indexes_multiple_pending_interactions_by_session_and_run() {
        let store = MemoryInteractionStore::new();
        let first = record("run-1", "interaction-1", "session-1");
        let second = record("run-1", "interaction-2", "session-1");
        store.insert_requested(first).await.unwrap();
        store.insert_requested(second).await.unwrap();

        let pending = store.list_pending("session-1").await.unwrap();
        assert_eq!(pending.len(), 2);
    }

    #[tokio::test]
    async fn bounds_active_interactions_per_run_without_rejecting_duplicates() {
        let store = MemoryInteractionStore::new();
        for index in 0..MAX_ACTIVE_INTERACTIONS_PER_RUN {
            assert_eq!(
                store
                    .insert_requested(record(
                        "run-cap",
                        &format!("interaction-{index}"),
                        "session-cap",
                    ))
                    .await
                    .unwrap(),
                InteractionInsertResult::Stored
            );
        }
        let duplicate = record("run-cap", "interaction-0", "session-cap");
        assert_eq!(
            store.insert_requested(duplicate).await.unwrap(),
            InteractionInsertResult::IdenticalDuplicate
        );
        assert_eq!(
            store
                .insert_requested(record("run-cap", "interaction-over-cap", "session-cap",))
                .await
                .unwrap(),
            InteractionInsertResult::CapacityExceeded
        );
    }

    #[tokio::test]
    async fn bounds_active_interactions_across_runs_in_one_session() {
        let store = MemoryInteractionStore::new();
        for index in 0..MAX_ACTIVE_INTERACTIONS_PER_SESSION {
            assert_eq!(
                store
                    .insert_requested(record(
                        &format!("run-{index}"),
                        &format!("interaction-{index}"),
                        "session-cap",
                    ))
                    .await
                    .unwrap(),
                InteractionInsertResult::Stored
            );
        }
        assert_eq!(
            store
                .insert_requested(record(
                    "run-over-session-cap",
                    "interaction-over-session-cap",
                    "session-cap",
                ))
                .await
                .unwrap(),
            InteractionInsertResult::CapacityExceeded
        );
    }

    #[tokio::test]
    async fn resolve_guard_is_per_interaction_and_commits_accepted() {
        let store = MemoryInteractionStore::new();
        let first = record("run-1", "interaction-1", "session-1");
        let second = record("run-1", "interaction-2", "session-1");
        store.insert_requested(first.clone()).await.unwrap();
        store.insert_requested(second.clone()).await.unwrap();

        assert!(matches!(
            store
                .claim_resolution(&first.key, "idem-1", "fingerprint-1")
                .await
                .unwrap(),
            InteractionResolveClaim::Acquired(_)
        ));
        assert!(matches!(
            store
                .claim_resolution(&first.key, "idem-1", "fingerprint-1")
                .await
                .unwrap(),
            InteractionResolveClaim::InFlight(InteractionStatus::Pending)
        ));
        assert!(matches!(
            store
                .claim_resolution(&second.key, "idem-2", "fingerprint-2")
                .await
                .unwrap(),
            InteractionResolveClaim::Acquired(_)
        ));

        let accepted = store
            .finish_resolution(
                &first.key,
                InteractionResolveCommit::Accepted {
                    idempotency_key: "idem-1".to_string(),
                    resolution_fingerprint: "fingerprint-1".to_string(),
                    resolver_actor_id: "human-1".to_string(),
                    accepted_at_ms: 200,
                },
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(accepted.status, InteractionStatus::Accepted);
        assert!(!accepted.in_flight);
        assert!(matches!(
            store
                .claim_resolution(&first.key, "idem-1", "fingerprint-1")
                .await
                .unwrap(),
            InteractionResolveClaim::AlreadyAccepted(_)
        ));
        assert!(matches!(
            store
                .claim_resolution(&first.key, "idem-2", "fingerprint-2")
                .await
                .unwrap(),
            InteractionResolveClaim::AcceptedDifferent(_)
        ));
    }

    #[tokio::test]
    async fn invalidates_all_active_in_run_then_cleans_only_terminal_records() {
        let store = MemoryInteractionStore::new();
        let first = record("run-1", "interaction-1", "session-1");
        let second = record("run-1", "interaction-2", "session-1");
        let other = record("run-2", "interaction-3", "session-1");
        store.insert_requested(first).await.unwrap();
        store.insert_requested(second).await.unwrap();
        store.insert_requested(other.clone()).await.unwrap();

        let invalidated = store
            .invalidate_run("run-1", "run_terminal", 500)
            .await
            .unwrap();
        assert_eq!(invalidated.len(), 2);
        assert!(
            invalidated
                .iter()
                .all(|record| record.status == InteractionStatus::Invalidated)
        );

        assert_eq!(store.cleanup_terminal(501).await.unwrap(), 2);
        assert_eq!(store.list_pending("session-1").await.unwrap(), vec![other]);
    }
}
