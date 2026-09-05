use std::sync::Arc;

use async_trait::async_trait;
use bcs_friend_store::MemoryFriendRepo;
use bcs_service_api::{
    EdgePermissionFriendSyncService, FriendCoreService, FriendRepoPort, RelationCoreService,
    ServiceError, ServiceResult, Friendship,
};
use tracing::{error, info, warn};

/// Core friendship service implementation.
///
/// `FriendCore` owns friendship behavior and relation-graph side effects, and
/// delegates persistence to a repository.
#[derive(Clone)]
pub struct FriendCore {
    repo: Arc<dyn FriendRepoPort>,
    relation: Option<Arc<dyn RelationCoreService>>,
    edge_permission_sync: Option<Arc<dyn EdgePermissionFriendSyncService>>,
}

impl FriendCore {
    pub fn new() -> Self {
        Self::memory()
    }

    pub fn with_repo(repo: Arc<dyn FriendRepoPort>) -> Self {
        Self {
            repo,
            relation: None,
            edge_permission_sync: None,
        }
    }

    pub fn memory() -> Self {
        Self::with_repo(Arc::new(MemoryFriendRepo::new()))
    }

    /// Inject the relation graph service for dual-write (F.1 + F.2).
    pub fn with_relation(mut self, relation: Arc<dyn RelationCoreService>) -> Self {
        self.relation = Some(relation);
        self
    }

    /// Inject edge-permission friend-edge synchronization for the migration
    /// window where legacy friendships and edge grants coexist.
    pub fn with_edge_permission_sync(
        mut self,
        sync: Arc<dyn EdgePermissionFriendSyncService>,
    ) -> Self {
        self.edge_permission_sync = Some(sync);
        self
    }
}

impl Default for FriendCore {
    fn default() -> Self {
        Self::memory()
    }
}

#[async_trait]
impl FriendCoreService for FriendCore {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        match self.repo.list_friends(bot_id).await {
            Ok(friends) => friends,
            Err(err) => {
                warn!(bot_id = %bot_id, error = %err, "Friend repo failed to list friends");
                Vec::new()
            }
        }
    }

    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> bool {
        match self.repo.are_friends(bot_a, bot_b).await {
            Ok(are_friends) => are_friends,
            Err(err) => {
                warn!(bot_a = %bot_a, bot_b = %bot_b, error = %err, "Friend repo failed to check friendship");
                false
            }
        }
    }

    async fn try_are_friends(&self, bot_a: &str, bot_b: &str) -> ServiceResult<bool> {
        self.repo.are_friends(bot_a, bot_b).await
    }

    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()> {
        let mut non_friends = Vec::new();
        for other in others {
            match self.repo.are_friends(bot_id, other).await {
                Ok(true) => {}
                Ok(false) => non_friends.push(other.clone()),
                Err(err) => {
                    warn!(bot_id = %bot_id, other = %other, error = %err, "Friend repo failed to check friendship");
                    non_friends.push(other.clone());
                }
            }
        }
        if non_friends.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(non_friends))
        }
    }

    async fn add_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<()> {
        self.repo.add_friendship(bot_a, bot_b).await?;

        if let Some(ref relation) = self.relation {
            let env = bcs_config::resolve_env_str();
            if let Err(err) = relation.add_friend_edges(bot_a, bot_b, &env).await {
                error!(
                    left_bot = %bot_a,
                    right_bot = %bot_b,
                    env = %env,
                    step = "add_friend_edges",
                    error = %err,
                    "F.1: friendship dual-write failed; friendship repo already inserted, relation graph is inconsistent"
                );
                return Err(ServiceError::InternalError(format!(
                    "friendship dual-write failed at step=add_friend_edges: {}",
                    err
                )));
            }
        }

        if let Some(ref sync) = self.edge_permission_sync {
            if let Err(err) = sync.sync_add_friendship(bot_a, bot_b).await {
                warn!(
                    left_bot = %bot_a,
                    right_bot = %bot_b,
                    step = "edge_permission_sync_add_friendship",
                    error = %err,
                    "friendship edge-permission sync failed; friendship repo already inserted"
                );
            }
        }

        info!(left_bot = %bot_a, right_bot = %bot_b, "Friendship established");
        Ok(())
    }

    async fn remove_all_friendships(&self, bot_id: &str) -> ServiceResult<usize> {
        let edge_permission_friends = if self.edge_permission_sync.is_some() {
            self.repo.list_friends(bot_id).await?
        } else {
            Vec::new()
        };
        let removed = self.repo.remove_all_friendships(bot_id).await?;

        if let Some(ref relation) = self.relation {
            let env = bcs_config::resolve_env_str();
            if let Err(err) = relation.remove_all_friend_edges(bot_id, &env).await {
                warn!(
                    bot_id = %bot_id,
                    env = %env,
                    error = %err,
                    "F.2: relation.remove_all_friend_edges failed; will be reconciled on next remove"
                );
            }
        }

        if let Some(ref sync) = self.edge_permission_sync {
            for friend_id in edge_permission_friends {
                if let Err(err) = sync.sync_remove_friendship(bot_id, &friend_id).await {
                    warn!(
                        left_bot = %bot_id,
                        right_bot = %friend_id,
                        error = %err,
                        "edge_permission_sync.remove_friendship failed during remove_all; friendship repo already removed"
                    );
                }
            }
        }

        Ok(removed)
    }

    async fn list_friendships_paginated(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<(Vec<Friendship>, u64)> {
        self.repo
            .list_friendships_paginated(bot_id, offset, limit)
            .await
    }

    async fn remove_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<bool> {
        let removed = self.repo.remove_friendship(bot_a, bot_b).await?;

        // Mirror remove_all_friendships: best-effort relation-graph cleanup.
        // The repo already removed the friendship row; a relation-graph failure
        // is logged and surfaced for reconciliation rather than failing the call.
        if let Some(ref relation) = self.relation {
            let env = bcs_config::resolve_env_str();
            if let Err(err) = relation.remove_friend_edges(bot_a, bot_b, &env).await {
                warn!(
                    left_bot = %bot_a,
                    right_bot = %bot_b,
                    env = %env,
                    error = %err,
                    "F.2: relation.remove_friend_edges failed; friendship repo already removed, relation graph is inconsistent"
                );
            }
        }

        if removed {
            if let Some(ref sync) = self.edge_permission_sync {
                if let Err(err) = sync.sync_remove_friendship(bot_a, bot_b).await {
                    warn!(
                        left_bot = %bot_a,
                        right_bot = %bot_b,
                        error = %err,
                        "edge_permission_sync.remove_friendship failed; friendship repo already removed"
                    );
                }
            }
        }

        if removed {
            info!(left_bot = %bot_a, right_bot = %bot_b, "Friendship removed");
        }
        Ok(removed)
    }
}
