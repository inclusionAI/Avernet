//! `OwnershipTransferService` — the Human-only ownership-transfer and
//! ownership-query use cases (plan Task 14, spec §9-§11).
//!
//! Transport-independent use cases behind
//! `/openapi/v1/collaboration/bots/{bot_id}/ownership`,
//! `/bots/{bot_id}/ownership-transfers` and `/ownership-transfers*`:
//! - the caller is ALWAYS the Gateway-verified Human inside
//!   [`AuthenticatedCaller`] — Bot/App/AccessKey-only principals are
//!   rejected (spec §11.2: they can never stand in for a User);
//! - `get_ownership` projects the current `owner_user_id` and
//!   `ownership_version` for a caller holding a current owner/manager
//!   role (the same read family as the manager list, spec §11.1);
//! - `create_transfer` is the owner-only initiate use case: the
//!   application validates shapes, requires the current owner, and
//!   delegates the ONE-Bot-transaction contract (idempotent replay,
//!   stale-version rejection without any persisted row, pending-slot
//!   hygiene, recipient liveness) to the Core;
//! - `accept`/`reject`/`cancel` map the STORE's committed domain
//!   results ([`crate::types::ownership_transfer::CommittedTransferOutcome`])
//!   precisely (spec §11.2): `Receipt` is the 200 historical receipt,
//!   `OwnerChanged` is 409 `ownership_changed` (already committed as
//!   `invalidated(owner_changed)` — the application NEVER rolls it
//!   back), `Expired` is 409 `ownership_transfer_expired`, and
//!   `Invalidated` re-reads the PERSISTED row's `terminal_reason`
//!   through the Core before choosing between 409 `ownership_changed`
//!   (an `invalidated(owner_changed)` retry keeps the SAME code, OT12)
//!   and 409 `ownership_transfer_not_pending` — the outcome enum alone
//!   is not sufficient (plan Task 14 Step 3);
//! - resource concealment (a non-party 404 that does not reveal whether
//!   the transfer id exists) and the both-parties read eligibility live
//!   in the application lane: `get_transfer`/`list_transfers` only ever
//!   query as the authenticated Human, and non-parties share the
//!   concealment 404 with missing ids.
//!
//! Receipts are HISTORY records: they never name a `current_owner`
//! (the live owner goes through the ownership query) and a pending
//! receipt carries only the confirmation-scope projection — no summary,
//! prompt, files, credentials or manager list (spec §11.1).

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, AuthenticatedCaller};

// Transport-boundary re-exports: the HTTP adapter's import-boundary
// contract requires every `use bcs_service_api::` line to end in
// `application::`, so the enum vocabulary the wire DTOs match on is
// reachable through this module (Task 13's team-lane precedent).
pub use crate::types::TransferListDirection;
pub use bcs_domain::{TerminalReason, TransferStatus};

/// Current ownership of one Bot (spec §11.1 `data` of the ownership
/// query): the single effective owner User ID and the
/// optimistic-concurrency version.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotOwnership {
    pub bot_id: String,
    /// The one effective owner User ID (read-only projection).
    pub owner_user_id: String,
    /// 0 = uninitialized; 1 = first initialization; +1 per accepted
    /// transfer (the version a create request must snapshot).
    pub ownership_version: u64,
}

/// The application-level receipt projection of one ownership transfer
/// (spec §11.1): exactly the confirmation-scope fields both parties may
/// read. Deliberately NO `current_owner` field, NO env and NO
/// `client_request_id` (receipts are history; the live owner goes
/// through the ownership query).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotOwnershipTransferReceipt {
    pub transfer_id: String,
    pub bot_id: String,
    /// Initiating owner; immutable after creation.
    pub from_user_id: String,
    /// Designated recipient; immutable after creation.
    pub to_user_id: String,
    /// Effective-status projection: a physically-`pending` row whose
    /// deadline lapsed projects `expired` without any write (spec §9.1).
    pub status: TransferStatus,
    /// The `ownership_version` snapshot taken at initiation.
    pub expected_owner_version: u64,
    /// The committed version of THIS acceptance; only set on `accepted`
    /// and never rewritten by later transfers.
    pub result_owner_version: Option<u64>,
    /// Fixed deadline (epoch milliseconds).
    pub expires_at: u64,
    /// Human user id or fixed system/service marker of the terminal
    /// decision; `None` while pending.
    pub decided_by: Option<String>,
    /// Terminal decision time (epoch ms); `None` while pending.
    pub decided_at: Option<u64>,
    /// Fixed machine reason of terminal/invalidated rows (`None` for
    /// plain decisions); drives the accept-retry code mapping (OT12).
    pub terminal_reason: Option<TerminalReason>,
    /// Minimal name snapshot for recipient confirmation; never summary,
    /// prompt, config or credentials.
    pub bot_name_snapshot: String,
    /// Database-generated creation time (epoch ms).
    pub gmt_create: u64,
    /// Database-generated last-modified time (epoch ms).
    pub gmt_modified: u64,
}

impl From<crate::types::ownership_transfer::OwnershipTransfer> for BotOwnershipTransferReceipt {
    fn from(record: crate::types::ownership_transfer::OwnershipTransfer) -> Self {
        Self {
            transfer_id: record.transfer_id,
            bot_id: record.bot_id,
            from_user_id: record.from_user_id,
            to_user_id: record.to_user_id,
            status: record.status,
            expected_owner_version: record.expected_owner_version,
            result_owner_version: record.result_owner_version,
            expires_at: record.expires_at,
            decided_by: record.decision_actor.map(|actor| actor.actor_id().to_string()),
            decided_at: record.decided_at,
            terminal_reason: record.terminal_reason,
            bot_name_snapshot: record.bot_name_snapshot,
            gmt_create: record.gmt_create,
            gmt_modified: record.gmt_modified,
        }
    }
}

/// Read the current ownership of one Bot (spec §11.1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GetBotOwnership {
    /// The Gateway-verified Human reading the ownership state.
    pub caller: AuthenticatedCaller,
    pub bot_id: String,
}

/// Initiate one ownership transfer (spec §11.1/§10.1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateBotOwnershipTransfer {
    /// The Gateway-verified Human initiating the transfer; must be the
    /// Bot's current owner when a NEW pending is created (an idempotent
    /// replay of the same key does not re-require current ownership).
    pub caller: AuthenticatedCaller,
    pub bot_id: String,
    /// The designated recipient Human; must differ from the caller and
    /// be a live Human of the same environment.
    pub to_user_id: String,
    /// The `ownership_version` snapshot the initiator read before
    /// creating; a stale value rejects the request without persisting
    /// anything (409 `ownership_changed`).
    pub expected_owner_version: u64,
    /// Caller-generated idempotency key (UUID).
    pub client_request_id: String,
}

/// Result of [`CreateBotOwnershipTransfer`]: the committed receipt and
/// whether THIS call created it. A same-payload replay returns the
/// ORIGINAL historical receipt with `created = false` (HTTP 200; the
/// first creation is HTTP 201).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotOwnershipTransferCreation {
    pub receipt: BotOwnershipTransferReceipt,
    pub created: bool,
}

/// Read the viewer's transfer inbox/outbox page (spec §11.1). Read-only
/// by contract: expiry is projected, statuses are filtered effectively
/// under the same read snapshot, and nothing is materialized.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ListBotOwnershipTransfers {
    /// The Gateway-verified Human whose inbox/outbox is read; never a
    /// request-body identity.
    pub caller: AuthenticatedCaller,
    pub direction: TransferListDirection,
    /// Optional effective-status filter (`None` = all states).
    pub status: Option<TransferStatus>,
    pub offset: u64,
    pub limit: u64,
}

/// One page of the viewer's transfer inbox/outbox.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotOwnershipTransferPage {
    pub items: Vec<BotOwnershipTransferReceipt>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

/// Read one transfer receipt as one of its two recorded parties (spec
/// §11.1: unrelated callers share the concealment 404 with missing ids
/// and can never enumerate transfer records by id).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GetBotOwnershipTransfer {
    /// The Gateway-verified Human reading the receipt.
    pub caller: AuthenticatedCaller,
    pub transfer_id: String,
}

/// Decide one transfer: accept (recipient), reject (recipient) or
/// cancel (initiating owner), or observe its already-committed outcome
/// on a response-loss retry (spec §10.2/§10.3).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecideBotOwnershipTransfer {
    /// The Gateway-verified Human deciding the transfer.
    pub caller: AuthenticatedCaller,
    pub transfer_id: String,
}

/// Human-only ownership view/transfer use cases (spec §9-§11, plan
/// Task 14). Authorization and concealment live HERE; the committed
/// state transitions and Repo atomicity live in the Core (plan Task 8
/// contracts, unchanged).
#[async_trait]
pub trait OwnershipTransferService: Send + Sync {
    /// The current owner and version of one Bot (caller must hold a
    /// current owner-or-manager role on it).
    async fn get_ownership(
        &self,
        query: GetBotOwnership,
    ) -> Result<BotOwnership, ApplicationError>;

    /// Initiate a transfer. `Ok` carries the committed (or replayed)
    /// historical receipt and the `created` flag for the 201/200 split;
    /// every rejected precondition maps to its fixed §11.2 code and a
    /// stale version rejects WITHOUT any persisted row.
    async fn create_transfer(
        &self,
        command: CreateBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferCreation, ApplicationError>;

    /// The viewer's inbox/outbox page (party-scoped only, effective
    /// statuses, one snapshot for count and page).
    async fn list_transfers(
        &self,
        query: ListBotOwnershipTransfers,
    ) -> Result<BotOwnershipTransferPage, ApplicationError>;

    /// One receipt for a recorded party; unrelated callers get the
    /// concealment 404.
    async fn get_transfer(
        &self,
        query: GetBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError>;

    /// The recipient accepts (spec §10.2): the all-or-nothing owner
    /// switch, or the historical accepted receipt on retry — the live
    /// owner it produced never changes that receipt.
    async fn accept_transfer(
        &self,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError>;

    /// The recipient rejects; same-action retries return the original
    /// terminal receipt.
    async fn reject_transfer(
        &self,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError>;

    /// The initiating owner cancels (spec §9: only while still the
    /// current owner); same-action retries return the original receipt.
    async fn cancel_transfer(
        &self,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError>;
}