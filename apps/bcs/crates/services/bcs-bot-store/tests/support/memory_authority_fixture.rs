//! Shared fixture for contracts against the real memory authority state.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_bot_store::{MemoryAuthorityAuditRow, MemoryBotRepo};
use bcs_service_api::BotCapabilities;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::types::bot_authority::{
    AuthorityActorKind, BotAccessRelation, TransferStatus,
    human_user_id_of_actor,
};
use bcs_test_support::contract::bot_authority_contract::{
    BotAuthorityContractFixture, HarnessAuditRow, HarnessTransferDecision,
};
use bcs_test_support::contract::bot_transfer_contract::{
    BotTransferContractFixture, HarnessTransferSeed,
};

/// The memory projection resolves the process env per
/// call; seeds and reads share it.
pub async fn target_env() -> String {
    bcs_config::resolve_env_str()
}

pub struct MemoryAuthorityFixture {
    pub repo: Arc<MemoryBotRepo>,
}

impl MemoryAuthorityFixture {
    pub async fn state_write(&self) -> tokio::sync::RwLockWriteGuard<
        '_,
        bcs_bot_store::memory_authority::MemoryAuthorityState,
    > {
        self.repo.authority_state().write().await
    }

    pub async fn state_read(&self) -> tokio::sync::RwLockReadGuard<
        '_,
        bcs_bot_store::memory_authority::MemoryAuthorityState,
    > {
        self.repo.authority_state().read().await
    }
}

fn grant_kind(kind: BotAccessRelation) -> bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind {
    match kind {
        BotAccessRelation::Owner => {
            bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner
        }
        BotAccessRelation::Manager => {
            bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Manager
        }
    }
}

#[async_trait]
impl BotAuthorityContractFixture for MemoryAuthorityFixture {
    fn authority_repo(&self) -> Arc<dyn BotAuthorityRepoPort> {
        self.repo.clone()
    }

    async fn seed_bot(&self, env: &str, bot_id: &str, ownership_version: u64) {
        let _ = env;
        self.repo
            .register(
                bot_id.to_string(),
                BotCapabilities::default(),
            )
            .await
            .expect("register fixture bot");
        self.state_write()
            .await
            .ownership_versions
            .insert(
                (target_env().await, bot_id.to_string()),
                ownership_version,
            );
    }

    async fn delete_bot(&self, _env: &str, bot_id: &str) {
        self.repo.soft_delete(bot_id).await;
    }

    async fn grant_edge(&self, env: &str, bot_id: &str, actor_id: &str, kind: BotAccessRelation) {
        // Production-faithful: a granted edge implies its
        // Human actor row (spec §3.1), ensured BEFORE the
        // authority write guard per the lock order.
        if let Some(user) = human_user_id_of_actor(actor_id) {
            self.seed_human(env, &user).await;
        }
        self.state_write().await.edges.insert(
            (env.to_string(), bot_id.to_string(), actor_id.to_string(), grant_kind(kind)),
            (bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Approved, 0),
        );
    }

    async fn revoke_edge(&self, env: &str, bot_id: &str, actor_id: &str, kind: BotAccessRelation) {
        self.state_write().await.edges.insert(
            (env.to_string(), bot_id.to_string(), actor_id.to_string(), grant_kind(kind)),
            (bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Revoked, 0),
        );
    }

    async fn seed_runtime_edge(&self, env: &str, bot_id: &str, actor_id: &str) {
        self.state_write().await.edges.insert(
            (
                env.to_string(),
                bot_id.to_string(),
                actor_id.to_string(),
                bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::PermissionProfile,
            ),
            (bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Approved, 42),
        );
    }

    async fn seed_corrupt_duplicate_owner(&self, env: &str, bot_id: &str, actor_id: &str) {
        self.state_write().await.edges.insert(
            (
                env.to_string(),
                bot_id.to_string(),
                actor_id.to_string(),
                bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner,
            ),
            (bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Approved, 0),
        );
    }

    async fn seed_human(&self, env: &str, user_id: &str) {
        // Env is real: another env's Human lives in its own
        // actor row env, so the env-bound guards skip it.
        self.repo.seed_human_actor_in(env, user_id).await;
    }

    async fn remove_human(&self, _env: &str, user_id: &str) {
        self.repo.remove_human_actor_in(user_id).await;
    }

    async fn seed_audit_conflict(&self, env: &str, mutation_id: &str, sequence: u64) {
        let mut state = self.state_write().await;
        // The memory counterpart of the SQL UNIQUE
        // `(env, mutation_id, sequence)`: the write paths
        // must refuse the mutating command。
        state.audit.push(
            MemoryAuthorityAuditRow {
                env: env.to_string(),
                bot_id: "fixture-seed".to_string(),
                mutation_id: mutation_id.to_string(),
                sequence,
                action: "seed_conflict".to_string(),
                actor_kind: "system".to_string(),
                actor_id: "system_seed".to_string(),
                subject_id: "none".to_string(),
                before_state: None,
                after_state: "none".to_string(),
                created_ms: 0,
            },
        );
    }

    async fn audit_rows(&self, env: &str) -> Vec<HarnessAuditRow> {
        self.state_read()
            .await
            .audit
            .iter()
            .filter(|row| row.env == env)
            .map(|row| HarnessAuditRow {
                mutation_id: row.mutation_id.clone(),
                sequence: row.sequence,
                action: row.action.clone(),
                actor_kind: row.actor_kind.clone(),
                actor_id: row.actor_id.clone(),
                subject_id: row.subject_id.clone(),
                before_state: row.before_state.clone(),
                after_state: Some(row.after_state.clone()),
            })
            .collect()
    }

    async fn ownership_version(&self, env: &str, bot_id: &str) -> u64 {
        self.state_read()
            .await
            .ownership_versions
            .get(&(env.to_string(), bot_id.to_string()))
            .copied()
            .unwrap_or(0)
    }

    async fn seed_pending_transfer(
        &self,
        env: &str,
        bot_id: &str,
        from_owner_actor: &str,
        target_actor: &str,
        expected_version: u64,
    ) {
        let now = ms_now();
        self.state_write()
            .await
            .transfers
            .insert(
                (
                    env.to_string(),
                    bot_id.to_string(),
                    target_actor.to_string(),
                ),
                bcs_bot_store::memory_authority::MemoryTransferRow {
                    bot_id: bot_id.to_string(),
                    target_owner_actor_id: target_actor.to_string(),
                    from_owner_actor_id: from_owner_actor.to_string(),
                    expected_ownership_version: expected_version,
                    status: TransferStatus::Pending,
                    requested_at: now,
                    expires_at: now + 7 * 24 * 3600 * 1_000,
                    updated_at: now,
                    decided_at: None,
                    decided_by_kind: None,
                    decided_by_id: None,
                    reason: None,
                    result_ownership_version: None,
                    bot_name_snapshot: None,
                },
            );
    }

    async fn transfer_decision_state(
        &self,
        env: &str,
        bot_id: &str,
        target_actor: &str,
    ) -> Option<HarnessTransferDecision> {
        self.state_read()
            .await
            .transfers
            .get(&(env.to_string(), bot_id.to_string(), target_actor.to_string()))
            .map(|row| HarnessTransferDecision {
                status: row.status.as_str().to_string(),
                decided_by_kind: row
                    .decided_by_kind
                    .map(|kind| kind.as_str().to_string()),
                decided_by_id: row.decided_by_id.clone(),
                reason: row.reason.clone(),
            })
    }
}

pub(super) fn ms_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}


#[async_trait]
impl BotTransferContractFixture for MemoryAuthorityFixture {
    async fn seed_transfer(&self, env: &str, seed: HarnessTransferSeed) {
        let now = ms_now();
        let offset_ms = |offset_s: i64| {
            if offset_s >= 0 {
                now + (offset_s as u64) * 1_000
            } else {
                now.saturating_sub(((-offset_s) as u64) * 1_000)
            }
        };
        self.state_write()
            .await
            .transfers
            .insert(
                (
                    env.to_string(),
                    seed.bot_id.clone(),
                    seed.target_owner_actor_id.clone(),
                ),
                bcs_bot_store::memory_authority::MemoryTransferRow {
                    bot_id: seed.bot_id,
                    target_owner_actor_id: seed.target_owner_actor_id,
                    from_owner_actor_id: seed.from_owner_actor_id,
                    expected_ownership_version: seed.expected_ownership_version,
                    status: seed.status,
                    requested_at: offset_ms(seed.requested_at_offset_s),
                    expires_at: offset_ms(seed.expires_at_offset_s),
                    updated_at: offset_ms(seed.requested_at_offset_s),
                    decided_at: seed.decided_at_offset_s.map(offset_ms),
                    decided_by_kind: seed.decided_by_kind.map(|kind| match kind.as_str() {
                        "human" => AuthorityActorKind::Human,
                        "service" => AuthorityActorKind::Service,
                        "provider" => AuthorityActorKind::Provider,
                        _ => AuthorityActorKind::System,
                    }),
                    decided_by_id: seed.decided_by_id,
                    reason: seed.reason,
                    result_ownership_version: seed.result_ownership_version,
                    bot_name_snapshot: None,
                },
            );
    }
}
