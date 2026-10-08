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
};

use crate::core::error::ServiceResult;
use crate::types::BotManagerList;
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
}
