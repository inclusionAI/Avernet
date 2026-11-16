//! Reverse-sync adapter: mirrors edge-permission friendship decisions back
//! into the legacy friendship store while both systems coexist.
//!
//! Same persistence shape as `FriendCore::add_friendship` /
//! `FriendCore::remove_friendship` (repo row + relation-graph friend-edge
//! mirror) minus the edge-permission forward sync — the edge already exists
//! whenever this runs, so calling back into it would be redundant.
//!
//! Error policy split:
//! - repo failures always propagate;
//! - Human relation failures propagate because the legacy Human friend list
//!   reads that relation graph; retries must repair partial writes;
//! - Bot↔Bot relation failures retain best-effort `warn!` behavior.
//!
//! Human removal always retries relation cleanup, even if an earlier attempt
//! already removed the friendship repo row. Bot↔Bot cleanup remains gated on
//! `removed`, preserving the existing coexistence policy.
//!
//! Env: the repo resolves its own env (`resolve_env()`), the same resolution
//! the group gate (`bcs-group` `ensure_v1_reachable` → `DbFriendStore`) uses,
//! so a row written here is always readable by the gate. Do NOT pass
//! `DbConnectService`'s env through — aligning with the reader is the
//! invariant (see spec §4.2).

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::port::FriendshipWritebackPort;
use bcs_service_api::{FriendRepoPort, RelationCoreService, ServiceResult};
use tracing::warn;

/// Writes friendship pairs into the legacy `bcs_friendships` store plus the
/// `bcs_actor_relations` friend-edge mirror.
pub struct FriendshipWritebackService {
    repo: Arc<dyn FriendRepoPort>,
    relation: Arc<dyn RelationCoreService>,
}

impl FriendshipWritebackService {
    pub fn new(
        repo: Arc<dyn FriendRepoPort>,
        relation: Arc<dyn RelationCoreService>,
    ) -> Self {
        Self { repo, relation }
    }
}

#[async_trait]
impl FriendshipWritebackPort for FriendshipWritebackService {
    async fn add_friendship(&self, caller: &str, target: &str) -> ServiceResult<()> {
        self.repo.add_friendship(caller, target).await?;

        let env = bcs_config::resolve_env_str();
        if let Err(err) = self.relation.add_friend_edges(caller, target, &env).await {
            if caller.starts_with("human_") || target.starts_with("human_") {
                return Err(err);
            }
            warn!(
                left_bot = %caller,
                right_bot = %target,
                env = %env,
                error = %err,
                "friendship writeback: relation mirror add failed; friendship repo row exists"
            );
        }

        Ok(())
    }

    async fn remove_friendship(&self, caller: &str, target: &str) -> ServiceResult<bool> {
        let removed = self.repo.remove_friendship(caller, target).await?;

        let human_pair = caller.starts_with("human_") || target.starts_with("human_");
        if removed || human_pair {
            let env = bcs_config::resolve_env_str();
            if let Err(err) = self.relation.remove_friend_edges(caller, target, &env).await {
                if human_pair {
                    return Err(err);
                }
                warn!(
                    left_bot = %caller,
                    right_bot = %target,
                    env = %env,
                    error = %err,
                    "friendship writeback: relation mirror remove failed; friendship repo row removed"
                );
            }
        }

        Ok(removed)
    }
}
