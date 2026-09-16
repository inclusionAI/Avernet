//! `EdgeGrantRepoPort` — persistence port for `edge_grants`.
use async_trait::async_trait;
use bcs_domain::edge_permission::EdgeGrant;
use bcs_domain::ActorKind;

use crate::core::error::ServiceResult;

/// Transport-neutral friend query. None includes both actor kinds.
/// Callers supply limit in 1..=100 and offset <= i64::MAX.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FriendListQuery {
    pub target_type: Option<ActorKind>,
    pub offset: u64,
    pub limit: u32,
}

/// Distinct internal actor IDs, ordered by case-sensitive UTF-8 byte order.
/// total counts filtered peers before pagination, including for empty pages.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FriendIdsPage {
    pub items: Vec<String>,
    pub total: u64,
}

#[async_trait]
pub trait EdgeGrantRepoPort: Send + Sync {
    /// Active approved edges `from -> to` in `env` (friend + non-friend).
    async fn list_active_grants(&self, from: &str, to: &str, env: &str) -> Vec<EdgeGrant>;

    /// Is `from` authorized to reach `to` in `env`? True iff at least one
    /// active approved edge `from → to` exists (friend OR non-friend: rules,
    /// non-default profile, etc.). This is the admission-edge superset of
    /// [`Self::has_friend_edge`] — it admits via ANY active edge, not just the
    /// default-profile friend edge (D12).
    async fn is_authorized(&self, from: &str, to: &str, env: &str) -> bool;

    /// `are_friends(x, y)` = any approved default-profile edge x→y OR y→x (D12).
    async fn has_friend_edge(&self, x: &str, y: &str, env: &str) -> bool;

    /// Friends of `actor` (any direction, default-profile edge) — actor ids only.
    async fn list_friends(&self, actor: &str, env: &str) -> Vec<String>;

    /// Store-owned filtering, deduplication, counting and pagination of approved
    /// active-default-profile edges in either direction. Human IDs start with
    /// the exact, case-sensitive prefix `human_`; all other IDs are Bots.
    /// Read failures are errors, never successful empty/partial pages.
    async fn list_friends_paginated(
        &self,
        actor: &str,
        env: &str,
        query: FriendListQuery,
    ) -> ServiceResult<FriendIdsPage>;

    async fn insert_grant(&self, grant: EdgeGrant) -> ServiceResult<u64>;

    async fn revoke_grant(&self, edge_id: u64, env: &str) -> ServiceResult<()>;

    /// Cached source for friend-edge discrimination: `(bot_id, env) -> default profile_id`.
    async fn get_default_profile_id(&self, bot_id: &str, env: &str) -> Option<u64>;
}
