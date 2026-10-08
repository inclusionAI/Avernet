//! Transport-neutral ownership transfer records (spec §5.2, plan
//! Task 8).
//!
//! Field semantics follow spec §5.2 exactly. The persisted states
//! (`TransferStatus`) and the machine reasons (`TerminalReason`) are
//! domain types; this module holds the receipt record and the committed
//! outcome domain result of a `decide` call.

use bcs_domain::{AuditActor, TerminalReason, TransferStatus};
use serde::{Deserialize, Serialize};

/// Immutable receipt of one ownership transfer (spec §5.2 fields,
/// verbatim semantics). There is deliberately NO `current_owner`
/// field: receipts describe history; the live owner goes through the
/// ownership query.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipTransfer {
    /// Opaque, immutable business id.
    pub transfer_id: String,
    /// Exact resource scope (env + bot).
    pub env: String,
    /// See [`OwnershipTransfer::env`].
    pub bot_id: String,
    /// Initiating owner; immutable after creation.
    pub from_user_id: String,
    /// Designated recipient; immutable after creation.
    pub to_user_id: String,
    /// `ownership_version` snapshot taken at initiation.
    pub expected_owner_version: u64,
    /// Caller-generated idempotency key of the initiate operation.
    pub client_request_id: String,
    /// Persisted six-state lifecycle status.
    pub status: TransferStatus,
    /// Required, fixed at creation (epoch milliseconds).
    pub expires_at: u64,
    /// Terminal decisions only: Human decision carries the real User
    /// ID, system decisions carry the fixed system marker — system
    /// identifiers are never recorded as User IDs. `None` while
    /// pending.
    pub decision_actor: Option<AuditActor>,
    /// Terminal decision time (epoch ms); `None` while pending.
    pub decided_at: Option<u64>,
    /// The committed ownership version of THIS acceptance; only set on
    /// `accepted` and never rewritten by future transfers.
    pub result_owner_version: Option<u64>,
    /// Optional fixed machine reason on terminal/invalidated rows;
    /// never arbitrary error text or credentials. Application mapping
    /// reads this to distinguish `ownership_changed` retries from
    /// other non-pending outcomes; the status enum alone is not
    /// sufficient.
    pub terminal_reason: Option<TerminalReason>,
    /// Minimal display snapshot for recipient confirmation; contains
    /// no summary, prompt, config or credentials.
    pub bot_name_snapshot: String,
    /// Database-generated creation time (epoch ms).
    pub gmt_create: u64,
    /// Database-generated last-modified time (epoch ms).
    pub gmt_modified: u64,
}

/// Committed domain outcome of `decide_transfer`
/// (spec §10.2, plan Task 14).
///
/// `OwnerChanged`, `Expired` and `Invalidated` are committed domain
/// RESULTS, not database exceptions: the invalidation was already
/// persisted in the same transaction and must not be rolled back.
/// Retries of already-terminal rows re-derive this outcome from the
/// persisted row's `terminal_reason` (an `invalidated` row with
/// `owner_changed` still maps to [`CommittedTransferOutcome::OwnerChanged`]).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum CommittedTransferOutcome {
    /// The action committed normally; the updated historical receipt.
    Receipt(OwnershipTransfer),
    /// Owner/version changed; a pending (or retried invalidated)
    /// transfer was committed as `invalidated(owner_changed)`.
    OwnerChanged,
    /// The pending window had already lapsed.
    Expired,
    /// The record was already invalidated for another reason.
    Invalidated,
}
