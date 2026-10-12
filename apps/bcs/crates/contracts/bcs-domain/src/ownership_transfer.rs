//! Ownership transfer lifecycle states (spec §9/§10).
//!
//! Pure domain states only. The transfer receipt record (spec §5.2
//! fields) is a transport-neutral service contract and lives in
//! `bcs_service_api::types::ownership_transfer`.

use serde::{Deserialize, Serialize};

/// A confirmation action on a pending ownership transfer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TransferAction {
    /// The designated recipient accepts ownership.
    Accept,
    /// The designated recipient rejects the transfer.
    Reject,
    /// The initiating owner cancels their own pending transfer.
    Cancel,
}

/// Persisted status of an ownership transfer row (six states, spec §5.2).
///
/// Terminal states are immutable; a repeated decide on an
/// already-terminal row re-derives the committed outcome from the row's
/// persisted `terminal_reason`, never by overwriting history.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TransferStatus {
    /// Created, awaiting the recipient's decision.
    Pending,
    /// The recipient accepted; ownership changed in the same transaction.
    Accepted,
    /// The recipient rejected the transfer.
    Rejected,
    /// The initiating owner cancelled.
    Cancelled,
    /// The pending window lapsed while still pending.
    Expired,
    /// The record was invalidated before decision (owner/version
    /// changed or the Bot's authority state no longer matches).
    Invalidated,
}

/// Fixed machine reason recorded on a non-success terminal transfer row
/// or on an invalidated row (spec §5.2 `terminal_reason`).
///
/// Storage never persists arbitrary error text or credentials here.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TerminalReason {
    /// The Bot was deleted before the transfer could be decided.
    BotDeleted,
    /// A required party (owner or recipient) is no longer available.
    ActorUnavailable,
    /// The owner/`ownership_version` no longer matched the transfer's
    /// precondition; the pending was committed as invalidated
    /// (`owner_changed`) before the miss was reported.
    OwnerChanged,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// An invalidated owner_changed row must stay readable as
    /// TerminalReason::OwnerChanged so later retries map back to the
    /// same committed outcome (OT12).
    #[test]
    fn invalidated_owner_changed_round_trips() {
        let reason = TerminalReason::OwnerChanged;
        let text = serde_json::to_string(&reason).unwrap();
        assert_eq!(text, "\"owner_changed\"");
        let back: TerminalReason = serde_json::from_str(&text).unwrap();
        assert_eq!(back, TerminalReason::OwnerChanged);
    }
}
