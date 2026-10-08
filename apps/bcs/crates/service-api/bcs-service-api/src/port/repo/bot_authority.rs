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
use bcs_domain::{BotAccessRelation, OwnershipState};

use crate::core::error::ServiceResult;

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
}