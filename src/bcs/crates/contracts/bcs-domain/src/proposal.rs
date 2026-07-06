//! Group chat proposal pure domain type.

use serde::{Deserialize, Serialize};

/// A pending group chat proposal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupChatProposal {
    /// Unique token for confirmation link.
    pub token: String,
    /// Driver bot ID.
    pub driver_bot: String,
    /// List of participant bot IDs.
    pub participants: Vec<String>,
    /// Reason for creating the group chat.
    pub reason: String,
    /// Proposed by bot ID.
    pub proposed_by: String,
    /// Human-readable introduction of all members.
    pub member_intros: String,
    /// Confirmation URL.
    pub confirm_url: String,
    /// Creation timestamp.
    pub created_at: u64,
}

impl GroupChatProposal {
    /// Proposal expiry time in milliseconds (10 minutes).
    pub const EXPIRY_MS: u64 = 10 * 60 * 1000;

    /// Check if this proposal has expired.
    pub fn is_expired(&self) -> bool {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        now.saturating_sub(self.created_at) > Self::EXPIRY_MS
    }
}
