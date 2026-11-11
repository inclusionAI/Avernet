//! Memory projection of `BotAuthorityRepoPort::ownerships_for`.
//!
//! Split out of the `authority` facet at the same mechanical
//! boundary the SQL Store used (`reads.rs`/`reads_ownerships.rs`):
//! the trait impl keeps its line budget; this sibling module carries
//! the snapshot-based batch spine with semantics mirroring
//! `bcs-edge-permission-store` exactly (conformance-paired).

use bcs_service_api::ServiceResult;
use bcs_service_api::types::bot_authority::{
    BotOwnershipView, human_actor_id, human_user_id_of_actor,
};

use super::MemoryBotRepo;
use super::authority::{
    MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus, owner_integrity_error,
    owner_integrity_error_message, resolve_env,
};

impl MemoryBotRepo {
    /// The memory mirror of the batched owner projection: one state
    /// snapshot, same length/order as `bot_ids`; per-item `None` for
    /// missing/deleted/version-0 ids (legacy creator projection only
    /// when the migration-window shim is enabled); integrity `Err`
    /// when an initialized bot has no or several approved owners.
    pub(super) async fn memory_ownership_projection(
        &self,
        bot_ids: &[String],
    ) -> ServiceResult<Vec<Option<BotOwnershipView>>> {
        if bot_ids.is_empty() {
            return Ok(Vec::new());
        }
        // In process the whole state is one snapshot: read it once.
        let env = resolve_env();
        let state = self.authority.read().await;
        let legacy_fallback = self
            .legacy_created_by_fallback
            .load(std::sync::atomic::Ordering::SeqCst);
        let mut results = Vec::with_capacity(bot_ids.len());
        for bot_id in bot_ids {
            if self.deleted_bot_ids.read().await.contains(bot_id)
                || !self.bots.read().await.get(bot_id).is_some_and(|bot| bot.actor_kind == bcs_service_api::ActorKind::Bot)
            {
                results.push(None);
                continue;
            }
            let version = state
                .ownership_versions
                .get(&(env.clone(), bot_id.clone()))
                .copied()
                .unwrap_or(0);
            if version == 0 {
                // Migration-window shim parity with `roles_for`: the
                // legacy creator fact applies only when enabled.
                let legacy = if legacy_fallback {
                    self.bots
                        .read()
                        .await
                        .get(bot_id)
                        .and_then(|bot| bot.created_by.clone())
                        .map(|creator| BotOwnershipView {
                            bot_id: bot_id.clone(),
                            owner_user_id: creator.clone(),
                            owner_actor_id: human_actor_id(&creator),
                        })
                } else {
                    None
                };
                results.push(legacy);
                continue;
            }
            let owners: Vec<&String> = state
                .edges
                .iter()
                .filter(|(key, (status, ref_id))| {
                    // Env-scoped like every other read: another
                    // env's owner identity must never leak.
                    key.0 == env
                        && key.1 == *bot_id
                        && key.3 == MemoryAuthorityEdgeKind::Owner
                        && *status == MemoryAuthorityEdgeStatus::Approved
                        && *ref_id == 0
                })
                .map(|(key, _)| &key.2)
                .collect();
            if owners.len() != 1 {
                return Err(owner_integrity_error(bot_id, owners.len()));
            }
            let owner = owners[0].clone();
            results.push(Some(BotOwnershipView {
                bot_id: bot_id.clone(),
                owner_user_id: human_user_id_of_actor(&owner).ok_or_else(|| {
                    owner_integrity_error_message(format!(
                        "owner edge subject '{owner}' is not a Human actor id"
                    ))
                })?,
                owner_actor_id: owner,
            }));
        }
        Ok(results)
    }
}
