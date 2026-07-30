use async_trait::async_trait;

use crate::types::{FriendRequest, FriendRequestDirection, FriendRequestStatus, Friendship, ServiceResult};

/// Repository contract for friendship persistence implementations.
///
/// This is intentionally independent from `FriendCoreService`: repositories own
/// storage and row/domain mapping, while the core service owns friendship
/// behavior, validation, and relation-graph side effects.
#[async_trait]
pub trait FriendRepoPort: Send + Sync {
    async fn list_friends(&self, bot_id: &str) -> ServiceResult<Vec<String>>;
    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> ServiceResult<bool>;
    async fn add_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<()>;
    async fn remove_all_friendships(&self, bot_id: &str) -> ServiceResult<usize>;

    /// List friendships of `bot_id` as symmetric [`Friendship`] projections.
    ///
    /// Returns rows where `bot_id` is either side of the pair, projected as
    /// `Friendship { bot_uuid: bot_id, friend_bot_uuid: <peer>, created_at }`,
    /// ordered by `created_at` descending with `friend_bot_uuid` ascending as
    /// the tie-breaker. `offset`/`limit` paginate the page; the second tuple
    /// element is the total matching count (before pagination).
    ///
    /// Default returns an empty page so noop/test implementations remain valid.
    async fn list_friendships_paginated(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<(Vec<Friendship>, u64)> {
        let _ = (bot_id, offset, limit);
        Ok((Vec::new(), 0))
    }

    /// Remove the single friendship pair `(bot_a, bot_b)` symmetrically.
    ///
    /// Idempotent: returns `true` when a row was removed, `false` when the
    /// pair did not exist (no-op). Implementations that store the pair
    /// bidirectionally must remove both directions.
    ///
    /// Default returns `Ok(false)` so noop/test implementations remain valid.
    async fn remove_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<bool> {
        let _ = (bot_a, bot_b);
        Ok(false)
    }
}

/// Repository contract for friend-request persistence implementations.
///
/// Business rules such as visibility checks, Human↔Human rejection, duplicate
/// semantics, auto-accept, and friendship creation live in
/// `FriendRequestCoreService` implementations.
#[async_trait]
pub trait FriendRequestRepoPort: Send + Sync {
    async fn find_pending_request(
        &self,
        from_bot: &str,
        to_bot: &str,
    ) -> ServiceResult<Option<FriendRequest>>;
    async fn insert_pending_request_if_absent(
        &self,
        request: FriendRequest,
    ) -> ServiceResult<Option<FriendRequest>>;
    async fn insert_request(&self, request: FriendRequest) -> ServiceResult<()>;
    async fn update_request_status(
        &self,
        request_id: &str,
        status: FriendRequestStatus,
    ) -> ServiceResult<()>;
    async fn accept_reverse_pending_requests(
        &self,
        from_bot: &str,
        to_bot: &str,
    ) -> ServiceResult<usize>;
    async fn get_request(&self, request_id: &str) -> ServiceResult<FriendRequest>;
    async fn list_requests(
        &self,
        bot_id: &str,
        direction: FriendRequestDirection,
        status_filter: Option<FriendRequestStatus>,
    ) -> Vec<FriendRequest>;
    async fn delete_pending_requests_for_bot(&self, bot_id: &str) -> ServiceResult<usize>;
    /// Insert an accepted request record if no accepted request for the same
    /// (from_bot, to_bot) pair already exists. Returns the existing or newly
    /// inserted record.
    async fn insert_accepted_request_if_absent(
        &self,
        request: FriendRequest,
    ) -> ServiceResult<FriendRequest>;
}
