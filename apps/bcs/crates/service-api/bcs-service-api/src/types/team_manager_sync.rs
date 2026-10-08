//! Transport-neutral team manager synchronization commands and receipts
//! (spec §5.4, plan Tasks 7/13).
//!
//! This module is the shared type home ("sharetype") for the team-sync
//! lane: BOTH production store drivers (SQL and Memory) and the core
//! delegation import from here, so the normalization rules, the
//! durable idempotency payload, and the persisted minimal receipt stay
//! byte-identical across drivers.
//!
//! Input limit (Gate 0 — confirmed and written back into spec §1.3): one
//! complete manager snapshot may carry at most [`TEAM_SYNC_MAX_SNAPSHOT`]
//! deduplicated Humans. Anything above is rejected BEFORE any transaction
//! with an `invalid_subject` branch (`AuthorityError::InvalidSubject`),
//! which the application layer maps to 400; chunked SQL (at most 100
//! Humans per statement, inserts at most 60 guarded rows) stays inside
//! ONE Bot-bounded transaction — an atomic snapshot is never assembled
//! from multiple independent transactions.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::types::AuditActor;
use crate::types::error::{AuthorityError, ServiceError, ServiceResult};

/// The Gate 0-confirmed engineering upper bound of one complete team
/// manager snapshot, counted AFTER deduplicated normalization (spec §1.3).
pub const TEAM_SYNC_MAX_SNAPSHOT: usize = 1_000;

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

impl TeamManagerOperation {
    /// The canonical `bot_manager_sync_operations.operation` column text
    /// (both production drivers persist exactly this value).
    pub fn storage_str(&self) -> &'static str {
        match self {
            Self::Sync => "sync",
            Self::Move { .. } => "move",
        }
    }

    /// The team the operation's snapshot is reconciled INTO: the sync team
    /// itself, or the move's new team. The movement's now-stopped team is
    /// the command's `team_id`.
    pub fn reconciled_team<'a>(&'a self, command_team_id: &'a str) -> &'a str {
        match self {
            Self::Sync => command_team_id,
            Self::Move { new_team_id } => new_team_id,
        }
    }
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

impl VerifiedTeamManagerService {
    /// The TRUE operator of a team-sync lane (spec §5.4): the audit actor
    /// derived from the verified credential — never a raw client-supplied
    /// actor, and never a subject Human.
    pub fn audit_actor(&self) -> AuditActor {
        AuditActor::Service {
            service_id: self.service_id.clone(),
        }
    }

    /// Fail-closed scope check of ONE sync command against this verified
    /// credential, as RE-proved by the store (the application layer
    /// validated first; the store is the authority boundary and never
    /// trusts a raw client actor):
    /// - the credential's `env` must match the store's bound env;
    /// - the command's Bot must be inside `allowed_bots` (when restricted);
    /// - every team the command touches — its `team_id` and, for a move,
    ///   the new team — must be inside `allowed_teams` (when restricted);
    /// - the operation itself must be inside `allowed_operations`
    ///   (when restricted).
    pub fn authorize_sync(
        &self,
        store_env: &str,
        command: &TeamManagerSync,
    ) -> Result<(), AuthorityError> {
        let forbidden = |detail: String| Err(AuthorityError::Forbidden(detail));
        if self.env != store_env {
            return forbidden(format!(
                "team-manager credential '{}' is bound to env '{}' and may not \
                 run in env '{store_env}'",
                self.service_id, self.env
            ));
        }
        if let Some(allowed) = &self.allowed_bots {
            if !allowed.iter().any(|bot| bot == &command.bot_id) {
                return forbidden(format!(
                    "team-manager credential '{}' may not synchronize bot '{}'",
                    self.service_id, command.bot_id
                ));
            }
        }
        let mut touched_teams = vec![command.team_id.as_str()];
        if let TeamManagerOperation::Move { new_team_id } = &command.operation {
            touched_teams.push(new_team_id.as_str());
        }
        if let Some(allowed) = &self.allowed_teams {
            for team in touched_teams {
                if !allowed.iter().any(|allowed_team| allowed_team == team) {
                    return forbidden(format!(
                        "team-manager credential '{}' may not manage team '{team}'",
                        self.service_id
                    ));
                }
            }
        }
        if let Some(allowed) = &self.allowed_operations {
            if !allowed.iter().any(|operation| *operation == command.operation) {
                return forbidden(format!(
                    "team-manager credential '{}' may not run this team operation",
                    self.service_id
                ));
            }
        }
        Ok(())
    }
}

/// One team-manager synchronization command, carrying the VERIFIED service
/// credential (never a raw client actor) and the complete desired snapshot.
///
/// `manager_user_ids` is the RAW payload of the command; stores normalize
/// it through [`normalize_team_manager_snapshot`] into the canonical
/// deduplicated, case-sensitively ordered set before any comparison. An
/// empty snapshot IS legal: it means a validated full revoke of this
/// team's manager source (the team binding itself may stay active).
/// A MISSING or null field never decodes into an empty snapshot — the
/// transport layer owns rejecting those, and the store re-verifies every
/// present member.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TeamManagerSync {
    /// The verified platform service performing the operation;
    /// the audit actor derives from it.
    pub service: VerifiedTeamManagerService,
    /// The Bot whose team manager source is reconciled.
    pub bot_id: String,
    /// The team the command's idempotency scope is bound to: the
    /// synchronized team for `Sync`, the now-stopped team for `Move`.
    pub team_id: String,
    /// The operation to run.
    pub operation: TeamManagerOperation,
    /// The raw, complete desired manager snapshot (normalized by the
    /// store; empty is legal, missing/null is rejected at the boundary).
    pub manager_user_ids: Vec<String>,
    /// Durable idempotency key, unique within
    /// `(env, service, bot, team)`; replays with the identical canonical
    /// payload return the original receipt without recomputation.
    pub idempotency_key: String,
}

/// The durable receipt of ONE committed team-sync operation
/// (`bot_manager_sync_operations.result`).
///
/// Same-key replays return the ORIGINAL receipt (including its
/// `operation_id` and counts) without re-executing anything; a same-key
/// request whose canonical payload DIFFERS is a conflict, never a
/// recompute. The receipt exists even when the operation changed nothing.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TeamSyncReceipt {
    /// Groups every audited edge change of the committed operation.
    pub operation_id: String,
    /// The Bot the operation reconciled.
    pub bot_id: String,
    /// The command's team id (the synchronized team; for a move, the
    /// now-stopped team — the snapshot lives at `operation`'s new team).
    pub team_id: String,
    /// The committed operation (a move carries its new team id).
    pub operation: TeamManagerOperation,
    /// Actually-granted manager edges (restored rows under the same id
    /// plus fresh rows).
    pub granted_count: u64,
    /// Actually-revoked manager edges (only actually-changed rows — for a
    /// move this includes the stopped team's revoked snapshot).
    pub revoked_count: u64,
}

/// Structural command validation BEFORE any normalization or transaction:
/// identity fields must be non-empty/non-blank, and a move must target a
/// DIFFERENT team (the Task 2 schema CHECK's shape, validated fail-closed
/// on the store side too).
///
/// The branch is `ServiceError::InvalidOperation` (the 400 family the
/// application layer maps); identity defects are never silently defaulted.
pub fn validate_team_sync_command(command: &TeamManagerSync) -> ServiceResult<()> {
    fn required(field: &'static str, value: &str) -> ServiceResult<()> {
        if value.trim().is_empty() {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "team sync command field '{field}' must be a non-empty, non-blank identity"
                ),
                request_id: None,
            });
        }
        Ok(())
    }
    required("bot_id", &command.bot_id)?;
    required("team_id", &command.team_id)?;
    required("idempotency_key", &command.idempotency_key)?;
    if let TeamManagerOperation::Move { new_team_id } = &command.operation {
        required("new_team_id", new_team_id)?;
        if *new_team_id == command.team_id {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "move target team '{new_team_id}' must differ from the team '{}'",
                    command.team_id
                ),
                request_id: None,
            });
        }
    }
    Ok(())
}

/// Normalize a RAW snapshot into the canonical manager set: exact-identity
/// deduplication, case-SENSITIVE ordering (`BTreeSet<String>`), blank
/// members rejected, and the Gate 0 snapshot bound enforced on the
/// DEDUPLICATED count (spec §1.3) — all BEFORE any transaction.
///
/// Both production drivers share this function so the persisted canonical
/// payload is byte-identical and replays agree across stores.
pub fn normalize_team_manager_snapshot(
    raw: &[String],
) -> Result<BTreeSet<String>, AuthorityError> {
    for user_id in raw {
        if user_id.trim().is_empty() {
            return Err(AuthorityError::InvalidSubject(format!(
                "team manager snapshot entry '{user_id}' is empty or blank; \
                 snapshots must carry explicit user ids (missing/null fields \
                 are rejected at the transport boundary, never defaulted)"
            )));
        }
    }
    let normalized: BTreeSet<String> = raw.iter().cloned().collect();
    if normalized.len() > TEAM_SYNC_MAX_SNAPSHOT {
        return Err(AuthorityError::InvalidSubject(format!(
            "team manager snapshot exceeds the {TEAM_SYNC_MAX_SNAPSHOT}-human \
             engineering limit (Gate 0, spec §1.3): {} humans submitted",
            normalized.len()
        )));
    }
    Ok(normalized)
}

/// The canonical durable payload of one sync command
/// (`bot_manager_sync_operations.payload`): operation, source team, move
/// target (目标), and the normalized manager set — everything that a
/// same-key replay must match byte-for-byte to count as the SAME request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanonicalTeamSyncPayload {
    /// `sync` / `move` (see [`TeamManagerOperation::storage_str`]).
    pub operation: String,
    /// The command's (source) team id.
    pub team_id: String,
    /// The move's new team; `None` for `sync`.
    pub new_team_id: Option<String>,
    /// The canonical (deduplicated, case-sensitively sorted) snapshot.
    pub manager_user_ids: Vec<String>,
}

/// Serialize the canonical payload. Inputs are already validated/
/// normalized plain strings, so serialization cannot fail in practice;
/// an encoding failure still surfaces as an internal error instead of an
/// invented payload (never a defaulted empty snapshot).
pub fn canonical_team_sync_payload(
    team_id: &str,
    operation: &TeamManagerOperation,
    canonical_snapshot: &[String],
) -> ServiceResult<String> {
    let payload = CanonicalTeamSyncPayload {
        operation: operation.storage_str().to_string(),
        team_id: team_id.to_string(),
        new_team_id: match operation {
            TeamManagerOperation::Sync => None,
            TeamManagerOperation::Move { new_team_id } => Some(new_team_id.clone()),
        },
        manager_user_ids: canonical_snapshot.to_vec(),
    };
    serde_json::to_string(&payload).map_err(|err| {
        ServiceError::InternalError(format!("canonical team-sync payload encoding: {err}"))
    })
}

/// The persisted minimal receipt record
/// (`bot_manager_sync_operations.result`) — the durable twin of
/// [`TeamSyncReceipt`], including the move's new team.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PersistedTeamSyncResult {
    /// The committed operation id (groups this operation's audit rows).
    pub operation_id: String,
    /// The operation's Bot.
    pub bot_id: String,
    /// The command's (source) team id.
    pub team_id: String,
    /// The committed operation; a move keeps its new team id.
    pub operation: TeamManagerOperation,
    /// Actually-granted manager edges.
    pub granted_count: u64,
    /// Actually-revoked manager edges.
    pub revoked_count: u64,
}

/// Serialize one committed receipt for durable storage
/// (`result` column). Plain strings and numbers only; an encoding
/// failure still surfaces as an internal error, never a defaulted receipt.
pub fn encode_team_sync_receipt(receipt: &TeamSyncReceipt) -> ServiceResult<String> {
    let persisted = PersistedTeamSyncResult {
        operation_id: receipt.operation_id.clone(),
        bot_id: receipt.bot_id.clone(),
        team_id: receipt.team_id.clone(),
        operation: receipt.operation.clone(),
        granted_count: receipt.granted_count,
        revoked_count: receipt.revoked_count,
    };
    serde_json::to_string(&persisted).map_err(|err| {
        ServiceError::InternalError(format!("persisted team-sync receipt encoding: {err}"))
    })
}

/// Strictly decode one persisted receipt (`result` column) back into a
/// [`TeamSyncReceipt`]. A row that does not decode is corruption of the
/// authority store: fail closed with [`AuthorityError::CorruptAuthority`],
/// never a defaulted receipt.
pub fn decode_team_sync_receipt(
    raw: &str,
    bot_id: &str,
    env: &str,
) -> ServiceResult<TeamSyncReceipt> {
    let decoded: PersistedTeamSyncResult = serde_json::from_str(raw).map_err(|err| {
        ServiceError::Authority(AuthorityError::CorruptAuthority {
            bot_id: bot_id.to_string(),
            env: env.to_string(),
            detail: format!("persisted team-sync receipt does not decode: {err}"),
        })
    })?;
    if decoded.bot_id != bot_id {
        return Err(ServiceError::Authority(AuthorityError::CorruptAuthority {
            bot_id: bot_id.to_string(),
            env: env.to_string(),
            detail: format!(
                "persisted team-sync receipt names bot '{}' inside the row of bot '{bot_id}'",
                decoded.bot_id
            ),
        }));
    }
    Ok(TeamSyncReceipt {
        operation_id: decoded.operation_id,
        bot_id: decoded.bot_id,
        team_id: decoded.team_id,
        operation: decoded.operation,
        granted_count: decoded.granted_count,
        revoked_count: decoded.revoked_count,
    })
}