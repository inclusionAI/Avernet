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
