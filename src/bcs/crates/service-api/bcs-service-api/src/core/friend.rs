use async_trait::async_trait;

use super::ServiceResult;

pub use bcs_domain::{FriendRequest, FriendRequestDirection, FriendRequestStatus};

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

    /// Cancel all pending friend requests related to a bot
    /// (called when visibility changes to private).
    ///
    /// Deletes pending requests where from_bot or to_bot matches bot_id.
    /// Only affects pending requests; accepted/rejected history is preserved.
    /// Returns the number of cancelled requests.
    /// Idempotent: calling on a bot with no pending requests returns Ok(0).
    async fn cancel_pending_requests(&self, bot_id: &str) -> ServiceResult<usize>;
}
