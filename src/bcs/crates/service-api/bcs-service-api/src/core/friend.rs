use async_trait::async_trait;

use super::ServiceResult;

pub use bcs_domain::{FriendRequest, FriendRequestDirection, FriendRequestStatus, Friendship};

// ============================================================================
// Friend Service Traits
// ============================================================================

/// Service for bot friendship management.
///
/// Manages the symmetric friendship relationship between bots.
/// A single record is stored per friendship pair (bot_a < bot_b by lexicographic order);
/// `list_friends` returns both directions.
#[async_trait]
pub trait FriendCoreService: Send + Sync {
    /// List all friends of a bot (returns bot_uuid list only).
    ///
    /// The HTTP handler enriches results with name/summary/online status
    /// by querying BotRegistryCoreService.
    async fn list_friends(&self, bot_id: &str) -> Vec<String>;

    /// Check if two bots are friends.
    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> bool;

    /// Check if two bots are friends without hiding persistence failures.
    ///
    /// The compatibility default preserves existing implementations. Services
    /// backed by fallible persistence should override this method.
    async fn try_are_friends(&self, bot_a: &str, bot_b: &str) -> ServiceResult<bool> {
        Ok(self.are_friends(bot_a, bot_b).await)
    }

    /// Check if all bots in the list are friends of the given bot.
    ///
    /// Returns `ServiceError::NotFriends` with non-friend bot_uuids on failure.
    /// Note: visibility check (public bots bypass friendship) is delegated to the caller.
    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()>;

    /// Insert a friendship record (called when a friend request is accepted).
    ///
    /// Stores a single record with bot_a < bot_b by lexicographic order.
    /// Idempotent: inserting an existing friendship returns Ok.
    async fn add_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<()>;

    /// Remove all friendships for a bot (called when visibility changes to private).
    ///
    /// Deletes all records where left_bot or right_bot matches bot_id.
    /// Returns the number of removed friendships.
    /// Idempotent: calling on a bot with no friends returns Ok(0).
    async fn remove_all_friendships(&self, bot_id: &str) -> ServiceResult<usize>;

    /// List friendships of `bot_id` as symmetric [`Friendship`] projections.
    ///
    /// Ordered by `created_at` descending (`friend_bot_uuid` ascending
    /// tie-breaker), with `offset`/`limit` pagination and a total count.
    /// Default returns an empty page; `FriendCore` overrides to delegate to
    /// [`FriendRepoPort::list_friendships_paginated`].
    async fn list_friendships_paginated(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<(Vec<Friendship>, u64)> {
        let _ = (bot_id, offset, limit);
        Ok((Vec::new(), 0))
    }

    /// Remove a single friendship pair symmetrically and idempotently.
    ///
    /// Returns `true` when a row was removed, `false` when the pair did not
    /// exist. Default returns `Ok(false)`; `FriendCore` overrides to delegate
    /// to [`FriendRepoPort::remove_friendship`] and clean up the relation
    /// graph (mirroring `remove_all_friendships`' best-effort edge cleanup).
    async fn remove_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<bool> {
        let _ = (bot_a, bot_b);
        Ok(false)
    }
}

/// Service for friend request workflow (request → accept/reject).
///
/// Business validations are performed in this service layer, NOT in the HTTP handler.
/// The handler layer is responsible for HTTP-level input parsing, caller resolution,
/// and translating ServiceError to HTTP status codes.
#[async_trait]
pub trait FriendRequestCoreService: Send + Sync {
    /// Create a friend request from one bot to another.
    ///
    /// Business validations:
    /// - `CannotAddSelf`: from_bot == to_bot (AC-6)
    /// - Already friends: returns idempotent Ok with a synthetic FriendRequest (AC-5)
    /// - `PendingRequestExists`: pending request from A→B already exists (AC-4)
    /// - `BotNotFound`: target bot not registered in BCS
    ///
    /// Does NOT check B→A direction pending requests (allows mutual requests, AC-20).
    async fn create_request(&self, from_bot: &str, to_bot: &str) -> ServiceResult<FriendRequest>;

    /// Accept a friend request by ID.
    ///
    /// Creates the friendship record and updates request status to accepted.
    /// Also auto-accepts the reverse pending request (B→A) if it exists (AC-20).
    /// Idempotent: accepting an already-accepted request returns Ok (AC-21).
    /// Error: accepting a rejected request returns `CannotAcceptRejected` (AC-21).
    async fn accept_request(&self, request_id: &str) -> ServiceResult<()>;

    /// Reject a friend request by ID.
    ///
    /// Updates request status to rejected.
    /// Idempotent: rejecting an already-rejected request returns Ok (AC-21).
    /// Error: rejecting an accepted request returns `CannotRejectAccepted` (AC-21).
    async fn reject_request(&self, request_id: &str) -> ServiceResult<()>;

    /// Get a single friend request by ID.
    ///
    /// Returns `FriendRequestNotFound` if the request does not exist.
    async fn get_request(&self, request_id: &str) -> ServiceResult<FriendRequest>;

    /// List friend requests related to a bot.
    ///
    /// - `direction`: filter by received/sent/all (default: received)
    /// - `status_filter`: optional filter by status; None returns all statuses (AC-9)
    async fn list_requests(
        &self,
        bot_id: &str,
        direction: FriendRequestDirection,
        status_filter: Option<FriendRequestStatus>,
    ) -> Vec<FriendRequest>;

    /// List friend requests related to a bot without hiding persistence
    /// failures.
    ///
    /// The compatibility default preserves existing implementations. Services
    /// backed by fallible persistence should override this method so DB
    /// failures surface to the V1 facade as `ApplicationError::Internal`
    /// (HTTP 500) instead of being masked as an empty 200 page.
    async fn try_list_requests(
        &self,
        bot_id: &str,
        direction: FriendRequestDirection,
        status_filter: Option<FriendRequestStatus>,
    ) -> ServiceResult<Vec<FriendRequest>> {
        Ok(self.list_requests(bot_id, direction, status_filter).await)
    }

    /// Cancel all pending friend requests related to a bot
    /// (called when visibility changes to private).
    ///
    /// Deletes pending requests where from_bot or to_bot matches bot_id.
    /// Only affects pending requests; accepted/rejected history is preserved.
    /// Returns the number of cancelled requests.
    /// Idempotent: calling on a bot with no pending requests returns Ok(0).
    async fn cancel_pending_requests(&self, bot_id: &str) -> ServiceResult<usize>;
}
