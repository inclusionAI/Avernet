//! Transport-neutral bot authority commands (plan Task 1).
//!
//! Role/source vocabulary itself is pure domain: `bcs_domain`
//! (`BotAccessRelation`, `ManagementSource`, `AuditActor`,
//! `ManagerMutation`, `ManagerMutationResult`, `OwnershipState`). This
//! module carries the cross-boundary command envelopes that the
//! application/Core/Repo layers share for role lifecycle entrances.
//! Presence/absence of a role is `Option<BotAccessRelation>`;
//! corrupted or uninitialized authority is an error
//! (`ServiceError::Authority`), never an implicit role.

use bcs_domain::{AuditActor, ManagementSource, UNINITIALIZED_OWNERSHIP_VERSION};
use serde::{Deserialize, Serialize};

/// First-ownership initialization for a not-yet-owned Bot
/// (spec §13.3, plan Task 5).
///
/// Only a trusted registration context may supply this; the initial
/// owner can never come from an arbitrary request body. Runtime-only
/// Bot connects with no Human do NOT carry an initialization and stay
/// at version 0 (uninitialized).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnershipInitialization {
    /// Trusted first owner User ID.
    pub owner_user_id: String,
    /// Typed role-lifecycle operator (Human on registration, System on
    /// governed repair/migration); service ids are never recorded as
    /// Human user ids.
    pub actor: AuditActor,
    /// Operation id grouping the initialization audit row.
    pub operation_id: String,
}

impl OwnershipInitialization {
    /// A not-yet-initialized Bot reports this ownership version.
    pub fn uninitialized_version() -> u64 {
        UNINITIALIZED_OWNERSHIP_VERSION
    }
}

/// One deduplicated manager entry of a [`BotManagerList`] page (spec §6):
/// the subject's bare User ID plus every management source it currently
/// holds on the Bot, decoded strictly and canonically ordered (by the stored
/// (kind, id) pair — `direct` < `ownership_transfer` < `team`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotManagerSummary {
    /// The trusted User ID of the manager (never the `human_<uid>` actor id).
    pub user_id: String,
    /// All live (approved) management sources of this user on the Bot.
    pub sources: Vec<ManagementSource>,
}

/// Result of `list_managers` (spec §6): the single owner in its own
/// read-only field — the owner never mixes into the manager page — plus one
/// user-deduplicated, user_id-ASC sorted page of explicit managers.
///
/// Pagination is positional: `offset` skips users, `limit == 0` yields an
/// empty page, and a page reaching beyond the last manager is empty rather
/// than an error. The application layer clamps `limit` into 1..100 per the
/// API contract; the store only requires the positional semantics.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotManagerList {
    /// The single effective owner User ID of the initialized Bot.
    pub owner_user_id: String,
    /// One page of explicit (non-owner) managers, sorted user_id ASC.
    pub managers: Vec<BotManagerSummary>,
}
