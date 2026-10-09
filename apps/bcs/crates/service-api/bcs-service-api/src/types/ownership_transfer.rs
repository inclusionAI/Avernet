//! Transport-neutral ownership transfer records (spec §5.2, plan
//! Task 8).
//!
//! Field semantics follow spec §5.2 exactly. The persisted states
//! (`TransferStatus`) and the machine reasons (`TerminalReason`) are
//! domain types; this module holds the receipt record and the committed
//! outcome domain result of a `decide` call.

use bcs_domain::{AuditActor, TerminalReason, TransferStatus};
use serde::{Deserialize, Serialize};

/// Create command for one ownership transfer (plan Task 8, spec §10.1).
///
/// `actor_user_id` is the authenticated Human initiating the request. Under
/// the naming lock of the plan, every id is a `String` and the version is a
/// bare `u64` (the transport layer validates/normalizes before the store).
/// The store treats `actor_user_id` as the intended `from_user_id` and
/// re-proves INSIDE its Bot write transaction that the actor is still the
/// current owner at the current `ownership_version`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CreateOwnershipTransfer {
    /// The authenticated Human initiating the transfer (must be the Bot's
    /// current owner when a NEW pending is created; idempotent replays of
    /// the same key do not re-require current ownership).
    pub actor_user_id: String,
    /// The exact resource scope target (env comes from the store binding).
    pub bot_id: String,
    /// The designated recipient Human; must differ from the actor and be a
    /// live Human of the same env.
    pub to_user_id: String,
    /// The `ownership_version` snapshot the initiator read before creating;
    /// a stale value rejects the request without persisting anything
    /// (409/`ownership_changed` semantics live at the application layer).
    pub expected_owner_version: u64,
    /// Caller-generated idempotency key (UUID). Together with the store's
    /// env/bot/actor binding it forms the durable
    /// `(env, bot_id, from_user_id, client_request_id)` unique key (spec
    /// §5.3): same key + same payload replays the ORIGINAL receipt with
    /// `created = false`; same key + different payload is a conflict.
    pub client_request_id: String,
}

/// Result of [`CreateOwnershipTransfer`] (plan Task 8).
///
/// `created == true` only for the FIRST committed creation of the
/// idempotency key; a same-payload replay returns the ORIGINAL committed
/// receipt with `created == false` (the application layer maps the flag to
/// HTTP 201/200 — that mapping is NOT the store's concern).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CreateTransferResult {
    /// The persisted receipt as it exists after this call committed (the
    /// original row for replays, the fresh row for first-time creates).
    pub receipt: OwnershipTransfer,
    /// Whether THIS call created the receipt. Idempotent replays (`false`)
    /// never re-execute a finished request and never create a second
    /// pending.
    pub created: bool,
}

/// Which side of the transfer relationship a listing asks for (spec §11.1
/// `direction` query parameter).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TransferListDirection {
    /// Transfers addressed TO the viewer (`to_user_id` == viewer) — the
    /// recipient inbox.
    Received,
    /// Transfers initiated BY the viewer (`from_user_id` == viewer) — the
    /// sender outbox.
    Sent,
}

/// Listing command for the transfer inbox/outbox (plan Task 8, spec §11.1).
///
/// Read-only by contract (no materialization, no side effects): a
/// physically-`pending` row whose deadline already lapsed projects as
/// `expired` (the system decider and `decided_at = expires_at` are
/// projected, never persisted by a read), and status filtering applies the
/// SAME effective-status rule inside the database before paging.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ListOwnershipTransfers {
    /// The authenticated Human whose inbox/outbox is read; never a
    /// free-text user id from the request body.
    pub viewer_user_id: String,
    /// See [`TransferListDirection`].
    pub direction: TransferListDirection,
    /// Optional effective-status filter (`None` = all states). The store
    /// applies the expiry-aware effective-status rule in SQL.
    pub status: Option<TransferStatus>,
    /// Page offset (>= 0), positions after the unified ordering
    /// `gmt_create DESC, transfer_id ASC`.
    pub offset: u64,
    /// Page size; the store clamps to the transport ceiling (1..=100
    /// after transport validation, `0` yields an empty page).
    pub limit: u64,
}

/// One page of the viewer's transfer inbox/outbox (plan Task 8).
///
/// `total` and `items` come from the SAME database read snapshot so they can
/// never disagree across an expiry boundary crossed while paging.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipTransferPage {
    /// The decoded receipts of this page, ordered
    /// `gmt_create DESC, transfer_id ASC`.
    pub items: Vec<OwnershipTransfer>,
    /// The total row count matching the same filter under the same
    /// snapshot.
    pub total: u64,
}

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
