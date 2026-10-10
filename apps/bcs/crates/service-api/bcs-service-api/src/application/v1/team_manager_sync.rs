//! `TeamManagerSyncService` — the trusted team-manager synchronization
//! application contract (plan Task 13, spec §5.4/§6.1).
//!
//! The trusted platform's normal synchronization entry is
//! `PUT /api/v1/bots/{bot_id}/manager-sources/teams/{team_id}` (or the
//! equivalent internal route contract): ONE complete snapshot for one URL
//! team, executed as a single Bot-serialized transaction by the Core with
//! durable idempotency (spec §5.4, plan Task 7).
//!
//! Trust boundary (Gate 0 record, spec §1.3):
//! - the delivery adapter extracts the raw service credential and calls
//!   [`Self::verify_service_credential`]; verification happens INSIDE the
//!   application boundary through the injected
//!   [`crate::port::TeamManagerCredentialVerifierPort`] port — never in the
//!   adapter's own protocol definitions;
//! - only the verification RESULT — the
//!   [`crate::types::VerifiedTeamManagerService`] with service id, env,
//!   and the verified Bot/team/operation allow scopes — flows onward into
//!   the business command; the raw credential never enters audit rows,
//!   business logs, or any persisted command;
//! - [`Self::sync`]/[`Self::repair_*`] re-validate the command's scopes
//!   against the verified service fail-closed (application-level
//!   check first; the store re-proves it inside the transaction);
//! - no Human/App/Bot Principal is involved and the lane is never
//!   anonymous: a missing credential is the transport's 401, and an
//!   unverifiable credential or out-of-scope command is this layer's
//!   403 `invalid_manager_sync_source`.
//!
//! The single-member repairs (`POST`/`DELETE .../members`) are INTERNAL
//! OPERATIONS-repair entries (spec §6.1): same source semantics, same
//! audit, same durable idempotency, same credential lane — they derive
//! one full snapshot reconcile (current team set ± the single user) and
//! are never the platform's normal sync entry.

use async_trait::async_trait;

use crate::types::team_manager_sync::{
    TeamManagerOperation, TeamSyncReceipt, VerifiedTeamManagerService,
};

use super::ApplicationError;

// Delivery adapters may import ONLY `bcs_service_api::application::…`
// (the boundary contract), so the shared transport-neutral team-sync
// vocabulary flows through this application module for them.
pub use crate::types::team_manager_sync::{
    TeamManagerOperation as TeamManagerOperationKind, TeamSyncReceipt as TeamSyncReceiptValue,
    VerifiedTeamManagerService as VerifiedTeamService,
};

/// One snapshot command under a VERIFIED platform service (spec §6.1).
///
/// `manager_user_ids` is the RAW snapshot payload: an empty list is
/// legal (validated full revoke of this team's source), while a missing
/// or null field is rejected at the transport boundary and never decoded
/// into an empty snapshot here; duplicates normalize into a set inside
/// the store's transaction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TeamManagerSyncCommand {
    /// The verified platform service performing the operation; the audit
    /// actor derives from it, never from a body value.
    pub service: VerifiedTeamManagerService,
    /// The Bot whose team manager source is reconciled.
    pub bot_id: String,
    /// The synchronized team for `Sync`; the now-stopped team for `Move`.
    pub team_id: String,
    /// `Sync` (replace the team's snapshot) or `Move { new_team_id }`.
    pub operation: TeamManagerOperation,
    /// The raw, complete desired manager snapshot (may be empty).
    pub manager_user_ids: Vec<String>,
    /// Durable idempotency key, unique within (env, service, bot, team).
    pub idempotency_key: String,
}

/// One single-member repair command (spec §6.1 internal repair lane).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TeamManagerMemberRepair {
    /// The verified platform service performing the repair.
    pub service: VerifiedTeamManagerService,
    /// The Bot whose team manager source is repaired.
    pub bot_id: String,
    /// The team whose manager source is repaired.
    pub team_id: String,
    /// The single trusted User ID to add/remove from the team source.
    pub user_id: String,
    /// Durable idempotency key for the derived reconcile.
    pub idempotency_key: String,
}

/// Trusted team-manager synchronization use cases (spec §5.4/§6.1).
#[async_trait]
pub trait TeamManagerSyncService: Send + Sync {
    /// Verify ONE platform service credential and yield the verified
    /// service identity + scopes. A blank/empty credential is
    /// [`ApplicationError::Unauthenticated`] (indistinguishable from a
    /// missing header); an unverifiable credential is the 403
    /// `invalid_manager_sync_source` fixed code. The raw credential is
    /// never returned, recorded, or logged by this layer.
    async fn verify_service_credential(
        &self,
        credential: &str,
    ) -> Result<VerifiedTeamManagerService, ApplicationError>;

    /// Execute ONE synchronized snapshot command (sync or move). Scopes
    /// are re-validated here before the Core's serialized transaction —
    /// the store remains the fail-closed authority and re-proves them.
    async fn sync(
        &self,
        command: TeamManagerSyncCommand,
    ) -> Result<TeamSyncReceipt, ApplicationError>;

    /// Internal repair: ensure ONE user's `team/{team_id}` manager edge
    /// exists (derived snapshot reconcile, same lane semantics).
    async fn repair_add_team_member(
        &self,
        command: TeamManagerMemberRepair,
    ) -> Result<TeamSyncReceipt, ApplicationError>;

    /// Internal repair: remove ONE user's `team/{team_id}` manager edge
    /// (derived snapshot reconcile, same lane semantics).
    async fn repair_remove_team_member(
        &self,
        command: TeamManagerMemberRepair,
    ) -> Result<TeamSyncReceipt, ApplicationError>;
}