//! Friend / friend-request pure domain types.

use serde::{Deserialize, Serialize};

/// Status of a friend request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum FriendRequestStatus {
    Pending,
    Accepted,
    Rejected,
}

/// Direction filter for listing friend requests.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum FriendRequestDirection {
    /// Requests received by the bot (default).
    Received,
    /// Requests sent by the bot.
    Sent,
    /// All requests (both sent and received).
    All,
}

impl Default for FriendRequestDirection {
    fn default() -> Self {
        Self::Received
    }
}

/// Symmetric projection of a persisted friendship edge.
///
/// `bot_uuid` is the bot whose friendship list was queried; `friend_bot_uuid`
/// is the peer on the other side of the pair. Repositories return this view
/// (rather than the raw `(left_bot, right_bot)` storage record) so callers do
/// not need to know the storage normalization convention. `created_at` is the
/// friendship establishment time in epoch milliseconds.
///
/// This is the domain type reused by `FriendRepoPort`, `FriendCoreService`,
/// and the V1 friendship facade. It intentionally mirrors the V1 wire
/// projection (`application::v1::friendship::Friendship`) field-for-field.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Friendship {
    /// Bot whose friendship list was queried.
    pub bot_uuid: String,
    /// The peer bot on the other side of the friendship pair.
    pub friend_bot_uuid: String,
    /// Epoch milliseconds when the friendship was established.
    pub created_at: u64,
}

/// A friend request record.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FriendRequest {
    /// Unique request ID (UUID).
    pub id: String,
    /// Bot UUID of the request sender.
    pub from_bot: String,
    /// Bot UUID of the request receiver.
    pub to_bot: String,
    /// Current status of the request.
    pub status: FriendRequestStatus,
    /// Timestamp when the request was created (epoch millis).
    pub created_at: u64,
    /// Timestamp when the request was last updated (epoch millis).
    pub updated_at: u64,
}
