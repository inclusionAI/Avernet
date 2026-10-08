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
    /// - All validation happens inside the mutation transaction on the
    ///   serialized Bot boundary (the same lock the Task 2 primitives
    ///   proved): the actor still holds a current owner-or-manager role
    ///   (Gate 0: managers may re-grant/revoke other managers) — a current
    ///   authority check performed outside the transaction adds no
    ///   authority; the subject is a live human of the store's env
    ///   (`AuthorityError::InvalidSubject`); the subject is not the owner
    ///   (`AuthorityError::Conflict` — the owner changes only through the
    ///   transfer flow); the Bot exists (`BotNotFound`), is initialized
    ///   (`OwnershipNotInitialized`) and has a unique approved owner
    ///   (`CorruptAuthority`).
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
}