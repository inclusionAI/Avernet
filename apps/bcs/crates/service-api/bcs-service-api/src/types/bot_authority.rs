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

use bcs_domain::{AuditActor, UNINITIALIZED_OWNERSHIP_VERSION};
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
