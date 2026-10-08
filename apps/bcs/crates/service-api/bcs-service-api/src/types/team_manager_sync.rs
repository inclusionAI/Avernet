//! Transport-neutral team manager synchronization commands
//! (spec §5.4, plan Tasks 7/13).

use serde::{Deserialize, Serialize};

/// The operation a verified team-manager service asks the
/// platform to run (spec §5.4).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TeamManagerOperation {
    /// Replace the target team's manager snapshot with the
    /// given set (empty set is legal).
    Sync,
    /// Atomically stop the old team's source and enable the
    /// new team with the given snapshot.
    Move { new_team_id: String },
}

/// A platform service whose team-manager credential has been
/// verified (plan Task 13).
///
/// Constructed only by the trusted credential verifier (or a
/// test fixture in test-support); business commands carry this
/// verified result. Authorization NEVER keys off a raw service
/// id string in a body. The credential itself never enters
/// audit rows, business logs, or persisted commands.
///
/// Scope fields are allow-lists: `None` means "unrestricted
/// within `env`", `Some` lists the exact allowed values. The
/// operation scope constrains which [`TeamManagerOperation`]
/// values this credential may run (e.g. a sync-only service
/// cannot issue moves).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VerifiedTeamManagerService {
    /// Verified platform service id (the value recorded in
    /// the `service` audit actor).
    pub service_id: String,
    /// Binding environment of the credential.
    pub env: String,
    /// Bot ids this credential may synchronize; `None` =
    /// unrestricted within `env`.
    pub allowed_bots: Option<Vec<String>>,
    /// URL teams this credential may manage; `None` =
    /// unrestricted within `env`.
    pub allowed_teams: Option<Vec<String>>,
    /// Operations this credential may run; `None` =
    /// unrestricted.
    pub allowed_operations: Option<Vec<TeamManagerOperation>>,
}
