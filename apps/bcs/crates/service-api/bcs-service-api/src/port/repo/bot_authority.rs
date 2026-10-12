//! `BotAuthorityRepoPort` — strict persistence port for bot authority reads
//! (plan Task 3, spec §5/§12.4).
//!
//! Authority = the explicit owner/manager roles carried by dedicated
//! `edge_grants` rows (`GrantKind::Owner`/`GrantKind::Manager` with their
//! non-null management-source columns). This port owns the strict READS;
//! role lifecycle mutations (manager changes, ownership initialization and
//! transfer) are later tasks with their own contracts.
//!
//! Strictness baseline (binding for every implementation):
//! - `env` is bound to the repo instance (the composition root knows which
//!   environment the process serves). It is never a request-body input.
//! - `roles_for` results are position-aligned with the input pairs; an
//!   absent active role is `None`; any corruption, illegal shape, or query
//!   failure is `Err` — a missing row is never treated as allowed, and no
//!   method may default to an empty-success.
//! - `ownership` yields a typed snapshot only for a live, initialized Bot:
//!   version 0 is [`AuthorityError::OwnershipNotInitialized`], version > 0
//!   with a missing-non-unique owner edge is
//!   [`AuthorityError::CorruptAuthority`], and a missing/soft-deleted Bot
//!   is [`ServiceError::BotNotFound`]. Corruption checks live here so every
//!   read fails closed at the lowest level; the Core only composes.
//!
//! Subject identity encoding: role edges persist the Human ACTOR id
//! (`human_<user_id>`, the D11 id-by-prefix convention used by
//! `ensure_human_actor`) in `edge_grants.from_id`, while these methods
//! speak the bare `user_id` of the trusted User. [`human_actor_id`] /
//! [`user_id_from_actor`] keep that mapping in one place for every store.

use async_trait::async_trait;
use bcs_domain::{
    AuditActor, BotAccessRelation, ManagerMutation, ManagerMutationResult, OwnershipState,
    TransferAction,
};

use crate::core::error::ServiceResult;
use crate::types::BotManagerList;
use crate::types::ownership_transfer::{
    CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult, ListOwnershipTransfers,
    OwnershipTransfer, OwnershipTransferPage,
};
use crate::types::team_manager_sync::{TeamManagerSync, TeamSyncReceipt};

/// The Human actor id carrying a principal's `user_id`
/// (`human_<user_id>`, D11 id-by-prefix).
pub fn human_actor_id(user_id: &str) -> String {
    format!("human_{}", user_id)
}

/// The principal `user_id` behind a Human actor id, or `None` when the edge
/// `from_id` is not in the Human actor id shape (a corrupt role row).
pub fn user_id_from_actor(actor_id: &str) -> Option<&str> {
    actor_id.strip_prefix("human_")
}

/// Persistence port for strict bot authority reads (spec §5, §12.4).
///
/// Implementations own the row decoding and MUST fail closed on any
/// illegal shape: role rows decode through `bcs_domain::decode_role_source`
/// and never serde-default a corrupt source into a valid role.
#[async_trait]
pub trait BotAuthorityRepoPort: Send + Sync {
    /// The strictly validated ownership snapshot of one Bot.
    ///
    /// `Err(BotNotFound)` for a missing or soft-deleted Bot;
    /// `Err(OwnershipNotInitialized)` for `ownership_version = 0`;
    /// `Err(CorruptAuthority)` when an initialized Bot has no or multiple
    /// approved owner edges (the schema's unique slot makes the latter
    /// structurally impossible in SQL; the read still checks, and the
    /// in-memory model enforces the same rule).
    async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState>;

    /// The active role one User holds on one Bot.
    ///
    /// `Ok(None)` when no active role exists; owner takes priority over
    /// manager sources. This is a pure strict lookup: it does not validate
    /// the Bot's ownership invariant — the Core composes
    /// [`Self::ownership`] before answering authorization questions.
    /// A matching role row that fails strict decode is
    /// `Err(CorruptAuthority)`, never a silently skipped row.
    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>>;

    /// Positional batch lookup of [`Self::role`] over full
    /// (user_id, bot_id) pairs.
    ///
    /// Implementations MUST correlate by the complete pair (never two
    /// independent IN sets — that produces an authorization cartesian
    /// product) and MUST NOT issue per-pair queries (no N+1). A query
    /// failure or any corrupted matching row fails the whole batch.
    async fn roles_for(
        &self,
        pairs: &[(String, String)],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>>;

    /// Idempotent manual manager mutation (spec §5.4/§6, plan Task 4).
    ///
    /// ONE-TRANSACTION semantics, binding for every implementation:
    /// - The authoritative validation happens INSIDE the mutation
    ///   transaction on the serialized Bot boundary (the same lock the
    ///   Task 2 primitives proved), expressed as conditions of the
    ///   changing statements themselves (so pre-transaction reads are
    ///   fast paths, never authority sources): the actor still holds a
    ///   current owner-or-manager role (Gate 0: managers may
    ///   re-grant/revoke other managers); the Bot is live and initialized
    ///   with its unique approved owner slot (`BotNotFound` /
    ///   `OwnershipNotInitialized` / `CorruptAuthority`); the subject is a
    ///   live human of the store's env (`AuthorityError::InvalidSubject`);
    ///   the subject is not the owner (`AuthorityError::Conflict` — the
    ///   owner changes only through the transfer flow). Only `Human`
    ///   actors may mutate managers; `Service`/`System` actors are
    ///   rejected fail-closed and must never be able to alias a
    ///   `human_<uid>` role-edge subject.
    /// - `GrantDirect` is idempotent: an already-approved direct edge is
    ///   `changed=false`; a previously revoked row RESTORES under the same
    ///   row id (never INSERT-IGNORE + revoked semantics); a fresh row is
    ///   inserted.
    /// - `RevokeNonTeam` revokes ONLY `direct` and `ownership_transfer`
    ///   sources (`team/*` rows must stay exactly as they are) and yields
    ///   the still-live team source ids in `remaining_team_sources` from
    ///   the same transaction's state.
    /// - Audit rows (`bot_manager_changes`) append ONLY for actual state
    ///   changes; one `operation_id` groups every edge changed by one
    ///   call, the audit records the TRUE operator (`AuditActor` verbatim,
    ///   never the subject), and business edge writes, audit writes and
    ///   the commit succeed or roll back TOGETHER — no best-effort
    ///   persistence after a failure.
    /// - Large source sets are handled with batched SQL (one INSERT SELECT
    ///   audit + one bulk UPDATE per revoke, parameter counts independent
    ///   of the source count), bounded lock waiting, and no full-table
    ///   scans over all Bots.
    ///
    /// This port never writes `team/*` sources: `ManagerMutation` has no
    /// team variant, and team synchronization owns its own lane.
    async fn mutate_manager(
        &self,
        actor: AuditActor,
        bot_id: &str,
        mutation: ManagerMutation,
    ) -> ServiceResult<ManagerMutationResult>;

    /// The manager list of one initialized Bot (spec §6): the single
    /// owner in the separate `owner_user_id` field (an owner with a stray
    /// manager edge never mixes into the page) plus one user-deduplicated
    /// page of explicit managers sorted user_id ASC with their strictly
    /// decoded, canonically ordered management sources.
    ///
    /// `offset` skips users positionally; `limit == 0` yields an empty
    /// page; a page beyond the end is empty, never an error. The same
    /// Bot validation branches as [`Self::mutate_manager`] apply before
    /// any rows are read.
    async fn list_managers(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<BotManagerList>;

    /// Atomically reconcile ONE URL team's manager source (spec §5.4/§6,
    /// plan Task 7). `team/*` manager edges are written ONLY through this
    /// lane — the direct manager API never touches them, and this lane
    /// never touches `direct`/`ownership_transfer` sources: a sync
    /// replaces exactly ONE team's source and leaves every other source
    /// of every other subject intact.
    ///
    /// The command carries a [`VerifiedTeamManagerService`] credential —
    /// never a raw client actor. The application layer (Task 13's trusted
    /// verifier) validated the credential first; the STORE re-validates
    /// the scopes fail-closed as the authority boundary: credential env
    /// == the store's bound env, Bot/team/operation inside the
    /// credential's allow-lists (`AuthorityError::Forbidden`), structural
    /// identity defects (`ServiceError::InvalidOperation`).
    ///
    /// Normalization: `manager_user_ids` becomes the canonical
    /// deduplicated, case-sensitively ordered set. An EMPTY snapshot is
    /// legal and means a validated full revoke of this team's source
    /// (the team binding itself may stay `active`); blank member ids are
    /// rejected (`InvalidSubject`) — a missing/null field is rejected at
    /// the transport boundary and NEVER defaults to an empty snapshot.
    /// The deduplicated snapshot above
    /// `TEAM_SYNC_MAX_SNAPSHOT` (= 1,000; Gate 0, spec §1.3) is rejected
    /// BEFORE any transaction. Every listed Human must be a live,
    /// same-env human (`InvalidSubject`); the Bot's owner may not appear
    /// in the snapshot (`Conflict` — owner authority never derives from
    /// team sources); the Bot must be live, initialized and own its
    /// unique approved owner slot (`BotNotFound` /
    /// `OwnershipNotInitialized` / `CorruptAuthority`).
    ///
    /// Durable idempotency (`bot_manager_sync_operations`, keyed by
    /// `(env, service, bot, team, idempotency_key)` — NOT memory-only):
    /// - same key + identical canonical payload → the ORIGINAL receipt is
    ///   returned WITHOUT recomputation (no edge change, no audit row);
    /// - same key + different payload/operation → [`AuthorityError::Conflict`];
    /// - even a completely no-difference sync persists its receipt.
    ///
    /// Reconcile semantics on the locked Bot boundary (ONE transaction:
    /// lock → re-read idempotency receipt + current sources under the
    /// lock → validated writes → audit → binding → receipt, all commit
    /// together or not at all; a validated pre-read is a fast path whose
    /// drift re-validates through bounded retries, exactly like
    /// [`Self::mutate_manager`]):
    /// - `Sync`: `added = desired − current`, `removed = current −
    ///   desired` for THIS team; grants restore previously revoked rows
    ///   under the SAME row id (never INSERT-IGNORE), revokes only
    ///   actually-changed edges;
    /// - `Move`: atomically STOP the old team (binding `stopped`, ALL its
    ///   approved edges revoked) and write the complete snapshot at the
    ///   new team — an existing new-team snapshot is REPLACED by the new
    ///   complete one, never merged; replaying the move's key (or an old
    ///   sync's key) never resurrects the stopped team.
    ///
    /// Audit: every actually-changed edge appends one
    /// `bot_manager_changes` row through the shared audit primitives,
    /// recording the TRUE operator (the service actor derived from the
    /// verified credential) and ONE `operation_id` shared with the
    /// receipt. Large snapshots run as chunked SQL (at most 100 Humans
    /// per in-chunk statement, at most 60 guarded INSERT rows per
    /// statement) inside the SAME Bot transaction — statements are
    /// batched per chunk, never per subject (no N+1), and the atomic
    /// snapshot is never assembled from multiple transactions.
    async fn sync_team(&self, command: TeamManagerSync) -> ServiceResult<TeamSyncReceipt>;

    /// Atomically create one pending ownership transfer (plan Task 8, spec
    /// §10.1). ONE Bot-serialized transaction, binding for every
    /// implementation:
    ///
    /// Idempotency (durable key `(env, bot_id, from_user_id,
    /// client_request_id)`, spec §5.3):
    /// - same key + same payload (`to_user_id` + `expected_owner_version`)
    ///   → the ORIGINAL committed receipt returns with `created = false`,
    ///   WITHOUT re-requiring current ownership and without re-executing
    ///   anything (the branch works for terminal historical receipts too);
    /// - same key + different payload →
    ///   [`AuthorityError::TransferConflict`] with the typed
    ///   [`TransferConflict::IdempotencyBody`](crate::types::error::TransferConflict)
    ///   branch (409 `idempotency_conflict` at the application layer).
    ///
    /// Validation for a FRESH pending (all re-proved inside the write
    /// transaction as conditions of the changing statement): live physical
    /// Bot (`BotNotFound`), initialized ownership with the unique approved
    /// owner slot ([`AuthorityError::OwnershipNotInitialized`] /
    /// [`AuthorityError::CorruptAuthority`]), actor is the CURRENT owner
    /// ([`AuthorityError::Forbidden`]), recipient is a live same-env Human
    /// that is not the actor ([`AuthorityError::InvalidSubject`]), and
    /// `expected_owner_version` equals the current
    /// `ownership_version`. A stale version (with everything else legal)
    /// rejects as the TYPED
    /// [`TransferConflict::VersionSnapshotStale`](crate::types::error::TransferConflict)
    /// WITHOUT persisting any row or cleaning anything (409
    /// `ownership_changed`; the application layer branches on the
    /// TYPE, never on a message string).
    ///
    /// Slot hygiene, inside the SAME transaction and BEFORE the insert:
    /// the Bot's time-lapsed pendings are materialized `expired` and its
    /// owner/version-mismatched pendings are materialized
    /// `invalidated(owner_changed)` (the §10.2 committed-invalidation
    /// statement; system decider), releasing the unique pending slot.
    /// After cleanup, a still-VALID pending of another key rejects with the
    /// TYPED [`TransferConflict::PendingSlot`](crate::types::error::TransferConflict)
    /// (409 `ownership_transfer_pending`).
    ///
    /// The insert fixes `expires_at` as database create-time + 7 days
    /// (inside the transaction, database clock). The statement count is
    /// bounded and index-driven (env/bot, idempotency key, pending slot);
    /// never a scan over other Bots' history.
    async fn create_transfer(
        &self,
        command: CreateOwnershipTransfer,
    ) -> ServiceResult<CreateTransferResult>;

    /// Atomically decide (accept/reject/cancel) one transfer, or observe
    /// its already-committed outcome (plan Task 8, spec §10.2/§10.3).
    ///
    /// Visibility first: `actor_user_id` must be a recorded party of the
    /// transfer — a third party gets
    /// [`AuthorityError::OwnershipTransferNotFound`] (concealment, 404). A
    /// party whose role forbids this action (e.g. the initiator accepting;
    /// the recipient cancelling) gets [`AuthorityError::Forbidden`].
    ///
    /// Terminal-row retries NEVER rewrite history — they re-derive the
    /// committed outcome from the persisted row (spec §10.3, response-loss
    /// retries):
    /// - `accepted` + accept-by same actor → the original
    ///   [`CommittedTransferOutcome::Receipt`] (even when ownership has
    ///   since moved on to a third party);
    /// - `rejected`/`cancelled` + the same action → the original receipt;
    ///   any incompatible action on a decided row → the TYPED
    ///   [`TransferConflict::NotPending`](crate::types::error::TransferConflict)
    ///   (409 `ownership_transfer_not_pending`);
    /// - `expired` → [`CommittedTransferOutcome::Expired`] (already a
    ///   committed domain result);
    /// - `invalidated` re-derives from `terminal_reason`:
    ///   `owner_changed` → [`CommittedTransferOutcome::OwnerChanged`]
    ///   (never degraded to a generic Invalidated), other reasons
    ///   (`bot_deleted`, `actor_unavailable`) →
    ///   [`CommittedTransferOutcome::Invalidated`] — no resurrection of
    ///   retired Bots, no edge writes.
    ///
    /// A still-pending row runs the ONE Bot-serialized decision flow,
    /// expiry judged by the DATABASE clock at the conditional transition:
    /// - deadline lapsed → the row is materialized `expired` (system
    ///   decider, `decided_at = expires_at`) and
    ///   [`CommittedTransferOutcome::Expired`] returns as the committed
    ///   domain result;
    /// - owner/`ownership_version` no longer matches the transfer's
    ///   snapshot (with authority data intact) → the row is materialized
    ///   `invalidated(owner_changed)` in the SAME transaction and
    ///   [`CommittedTransferOutcome::OwnerChanged`] returns as a NORMAL
    ///   domain result (the invalidation must not be rolled back); no
    ///   edge changes, no version bump, the pending slot is released;
    /// - accept: revoke the old owner edge, restore/insert the recipient's
    ///   owner edge, grant the old owner the
    ///   `ownership_transfer/<transfer_id>` manager source, revoke ONLY
    ///   the recipient's non-team (`direct`/`ownership_transfer`) manager
    ///   sources (`team/*` stays governed by team sync), CAS the
    ///   `ownership_version` (+1) and save `accepted` with the committed
    ///   `result_owner_version` — ALL in one all-or-nothing transaction;
    ///   genuinely failing/committing DB errors roll back everything;
    /// - reject/cancel: only the row's own guarded transition to the
    ///   terminal state; cancel re-proves the initiator is still the
    ///   current owner ([`AuthorityError::Forbidden`] otherwise).
    ///
    /// These lanes never write `bot_manager_changes` (spec §5.2: the
    /// committed transfer receipt IS the audit of the role change). All
    /// concurrent decides on one pending serialize on the Bot boundary;
    /// losers observe the winner's committed outcome on re-validation.
    async fn decide_transfer(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        action: TransferAction,
    ) -> ServiceResult<CommittedTransferOutcome>;

    /// Read ONE transfer receipt for a recorded party (plan Task 8, spec
    /// §11.1/§18 OT18). Read-only: no materialization writes, no
    /// permission grants; managers that are not one of the two parties
    /// cannot enumerate (concealed through
    /// [`AuthorityError::OwnershipTransferNotFound`]). The returned
    /// receipt carries the persisted `terminal_reason` (mapped HTTP
    /// semantics depend on it) and PROJECTS effective expiry: a physically
    /// `pending` row whose deadline lapsed reads as `expired` with the
    /// fixed system decider and `decided_at = expires_at`.
    async fn get_transfer(
        &self,
        viewer_user_id: &str,
        transfer_id: &str,
    ) -> ServiceResult<OwnershipTransfer>;

    /// List the viewer's transfer inbox/outbox page (plan Task 8, spec
    /// §11.1). Read-only. Spec rules, binding for every implementation:
    /// - only rows where the `direction` side is the viewer (`to_user_id`
    ///   received / `from_user_id` sent); never a third party's rows;
    /// - the effective-status filter runs IN the database with the same
    ///   expiry-aware rule [`Self::get_transfer`] projects (a time-lapsed
    ///   pending matches `expired` and stops matching `pending`), never
    ///   filter-after-paging;
    /// - `total` and the returned page come from ONE snapshot;
    /// - ordering `gmt_create DESC, transfer_id ASC`, `offset` positions
    ///   after the ordering, `limit == 0` yields an empty page and the
    ///   store clamps the limit to its documented ceiling;
    /// - receipts carry the persisted `terminal_reason`;
    /// - a constant, index-driven statement count (env+user+status
    ///   receiver/sender indexes of spec §5.3): no per-row reads, no
    ///   full-table history scan, regardless of the row count.
    async fn list_transfers(
        &self,
        query: ListOwnershipTransfers,
    ) -> ServiceResult<OwnershipTransferPage>;
}
