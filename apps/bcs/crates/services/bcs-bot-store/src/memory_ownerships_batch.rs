//! Memory projection of `BotAuthorityRepoPort::ownerships_for`.
//!
//! Split out of the `authority` facet at the same mechanical
//! boundary the SQL Store used (`reads.rs`/`reads_ownerships.rs`):
//! the trait impl keeps its line budget; this sibling module carries
//! the snapshot-based batch spine with semantics mirroring
//! `bcs-edge-permission-store` exactly (conformance-paired).

use std::collections::{HashMap, HashSet};

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
        // In process the whole state is one snapshot: read each facet once.
        let env = resolve_env();
        let state = self.authority.read().await;
        let deleted = self.deleted_bot_ids.read().await;
        let bots = self.bots.read().await;
        let legacy_fallback = self
            .legacy_created_by_fallback
            .load(std::sync::atomic::Ordering::SeqCst);
        let mut seen = HashSet::with_capacity(bot_ids.len());
        let mut projections = HashMap::with_capacity(bot_ids.len());
        for bot_id in bot_ids
            .iter()
            .filter(|bot_id| seen.insert(bot_id.as_str()))
        {
            let projection = if deleted.contains(bot_id)
                || !bots.get(bot_id).is_some_and(|bot| {
                    bot.actor_kind == bcs_service_api::ActorKind::Bot
                })
            {
                None
            } else {
                let version = state
                    .ownership_versions
                    .get(&(env.clone(), bot_id.clone()))
                    .copied()
                    .unwrap_or(0);
                if version == 0 {
                    // Migration-window shim parity with `roles_for`: the
                    // legacy creator fact applies only when enabled.
                    if legacy_fallback {
                        bots.get(bot_id)
                            .and_then(|bot| bot.created_by.clone())
                            .map(|creator| BotOwnershipView {
                                bot_id: bot_id.clone(),
                                owner_user_id: creator.clone(),
                                owner_actor_id: human_actor_id(&creator),
                            })
                    } else {
                        None
                    }
                } else {
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
                    Some(BotOwnershipView {
                        bot_id: bot_id.clone(),
                        owner_user_id: human_user_id_of_actor(&owner).ok_or_else(|| {
                            owner_integrity_error_message(format!(
                                "owner edge subject '{owner}' is not a Human actor id"
                            ))
                        })?,
                        owner_actor_id: owner,
                    })
                }
            };
            projections.insert(bot_id.as_str(), projection);
        }
        Ok(bot_ids
            .iter()
            .map(|bot_id| projections[bot_id.as_str()].clone())
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    use bcs_service_api::BotCapabilities;
    use bcs_service_api::port::repo::BotRepoPort;

    #[tokio::test]
    async fn repeated_ids_share_one_registry_snapshot() {
        let repo = Arc::new(MemoryBotRepo::new());
        let bot_id = "bot_snapshot";
        repo.register(bot_id.to_string(), BotCapabilities::default())
            .await
            .expect("register fixture bot");
        let env = resolve_env();
        {
            let mut state = repo.authority_state().write().await;
            state
                .ownership_versions
                .insert((env.clone(), bot_id.to_string()), 1);
            state.edges.insert(
                (
                    env,
                    bot_id.to_string(),
                    human_actor_id("own"),
                    MemoryAuthorityEdgeKind::Owner,
                ),
                (MemoryAuthorityEdgeStatus::Approved, 0),
            );
        }

        // Queue the batch's first registry read, then queue a delete behind it.
        // Per-item reads observe the tombstone on the second occurrence; one
        // snapshot must return the same owner at both positions.
        let registry_guard = repo.bots.write().await;
        let batch = {
            let repo = repo.clone();
            tokio::spawn(async move {
                repo.memory_ownership_projection(&[
                    bot_id.to_string(),
                    bot_id.to_string(),
                ])
                .await
            })
        };
        for _ in 0..16 {
            tokio::task::yield_now().await;
        }
        let deletion = {
            let repo = repo.clone();
            tokio::spawn(async move { repo.soft_delete(bot_id).await })
        };
        for _ in 0..16 {
            tokio::task::yield_now().await;
        }
        drop(registry_guard);

        let views = batch
            .await
            .expect("batch joins")
            .expect("owner batch succeeds");
        deletion.await.expect("deletion joins");
        let owners: Vec<Option<String>> = views
            .iter()
            .map(|view| view.as_ref().map(|view| view.owner_user_id.clone()))
            .collect();
        assert_eq!(owners, vec![Some("own".to_string()), Some("own".to_string())]);
    }
}
