//! In-memory team manager synchronization (plan Task 7, spec
//! §5.4/§6/§1.3) — the strict twin of the SQL store's `team_sync.rs`.
//!
//! ONE critical section (the in-memory equivalent of the SQL store's ONE
//! Bot-bounded transaction) runs validation → edge reconcile → audit →
//! binding upsert → DURABLE receipt against the SAME authority state
//! boundary the direct lane shares
//! (`bots` + `deleted_bot_ids` + `authority`):
//! - the verified service's scopes are RE-validated fail-closed (env,
//!   Bot, every touched team, the operation) — never a raw client actor;
//! - the snapshot is normalized identically (deduplicated,
//!   case-sensitively sorted; blank members rejected; the Gate 0
//!   [`TEAM_SYNC_MAX_SNAPSHOT`] bound enforced pre-transaction);
//! - the same-key DURABLE idempotency receipt is consulted from the
//!   `bot_manager_sync_operations` mirror: identical canonical payload →
//!   the ORIGINAL receipt returns without recomputation (no audit rows);
//!   different payload → `Conflict`;
//! - every snapshot Human must be live and same-env (`InvalidSubject`),
//!   and the Bot's owner may not appear in the snapshot (`Conflict`);
//! - `added = desired − current`, `removed = current − desired` of THIS
//!   team only (a Move stops the old team entirely and writes the full
//!   snapshot at the new team, replacing any previous one); restorations
//!   reuse revoked rows under the SAME edge id, fresh rows are inserted
//!   — never INSERT-IGNORE semantics;
//! - only actually-changed edges append `bot_manager_changes` rows
//!   through the shared audit primitive, recording the TRUE operator
//!   (the Service actor) and ONE operation id shared with the receipt;
//! - the team binding mirror upserts `active` (even for an EMPTY
//!   snapshot) or `stopped` (a Move's old team) with the operation id.
//!
//! Lock discipline: `deleted_bot_ids` (read) → `bots` (write) →
//! `authority` (write), the same ordering family the lifecycle and
//! manager-mutation lanes use, so no lane can deadlock against this one.

use std::collections::BTreeSet;

use bcs_service_api::port::repo::bot_authority::{human_actor_id, user_id_from_actor};
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::team_manager_sync::{
    canonical_team_sync_payload, normalize_team_manager_snapshot, validate_team_sync_command,
    TeamManagerOperation, TeamManagerSync, TeamSyncReceipt,
};
use bcs_service_api::types::{
    AuditActor, ActorKind, UNINITIALIZED_OWNERSHIP_VERSION, ROLE_GRANT_REF_ID,
};
use bcs_service_api::{ServiceError, ServiceResult};

use super::memory_authority::{corrupt, MemoryAuthorityState, MemoryRoleEdgeRow};
use super::{MemoryBotRepo, resolve_env};

/// The team-sync engine behind `MemoryBotRepo`'s `sync_team` port method.
pub(super) async fn memory_sync_team(
    repo: &MemoryBotRepo,
    command: TeamManagerSync,
) -> ServiceResult<TeamSyncReceipt> {
    // One-shot test lever: the whole critical section fails before any
    // partial state appears (same lever as the direct mutation lane).
    if repo.take_authority_write_failure().await {
        return Err(ServiceError::InternalError(
            "test-injected authority write failure".into(),
        ));
    }

    // -- pre-critical-section pure validation (no state accesses) -------
    validate_team_sync_command(&command)?;
    let env = resolve_env();
    command.service.authorize_sync(&env, &command)?;
    let desired = normalize_team_manager_snapshot(&command.manager_user_ids)
        .map_err(ServiceError::Authority)?;
    let canonical_users: Vec<String> = desired.iter().cloned().collect();
    let payload =
        canonical_team_sync_payload(&command.team_id, &command.operation, &canonical_users)?;

    // Short deleted-set guard BEFORE any long-held lock (the lifecycle
    // lane's deleted→bots ordering).
    if repo.deleted_bot_ids.read().await.contains(&command.bot_id) {
        return Err(ServiceError::BotNotFound(command.bot_id.clone()));
    }

    // -- ONE critical section ------------------------------------------
    let deleted_ids = repo.deleted_bot_ids.read().await;
    let bots = repo.bots.write().await;
    let mut authority = repo.authority.write().await;

    // Bot invariant: live row, initialized version, exactly one approved
    // owner edge (the same branches as the SQL store).
    if !bots.contains_key(&command.bot_id) {
        return Err(ServiceError::BotNotFound(command.bot_id.clone()));
    }
    let ownership_version = authority
        .ownership_versions
        .get(&command.bot_id)
        .copied()
        .unwrap_or(UNINITIALIZED_OWNERSHIP_VERSION);
    if ownership_version == UNINITIALIZED_OWNERSHIP_VERSION {
        return Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
            bot_id: command.bot_id.clone(),
            env: env.clone(),
        }));
    }
    let owner_rows: Vec<MemoryRoleEdgeRow> = authority
        .role_rows
        .iter()
        .filter(|row| {
            row.env == env
                && row.to_id == command.bot_id
                && row.grant_kind == "owner"
                && row.status == "approved"
        })
        .cloned()
        .collect();
    if owner_rows.len() != 1 {
        return Err(corrupt(
            &command.bot_id,
            &env,
            format!(
                "initialized bot has {} approved owner edges (team sync denied)",
                owner_rows.len()
            ),
        ));
    }
    let Some(owner_user_id) = user_id_from_actor(&owner_rows[0].from_id) else {
        return Err(corrupt(
            &command.bot_id,
            &env,
            format!(
                "owner edge from non-human actor id '{}'",
                owner_rows[0].from_id
            ),
        ));
    };

    // Durable idempotency: replay the ORIGINAL receipt (identical
    // canonical payload) or refuse the diverged request — never recompute.
    if let Some(record) = authority
        .sync_operations
        .iter()
        .find(|record| {
            record.env == env
                && record.service_id == command.service.service_id
                && record.bot_id == command.bot_id
                && record.team_id == command.team_id
                && record.idempotency_key == command.idempotency_key
        })
    {
        if record.payload != payload {
            return Err(ServiceError::Authority(AuthorityError::Conflict(format!(
                "team sync key '{}' on bot '{}' team '{}' was already used \
                 with a different payload/operation",
                command.idempotency_key, command.bot_id, command.team_id
            ))));
        }
        return Ok(record.receipt.clone());
    }

    // The owner never derives authority from a team source.
    if desired.contains(owner_user_id) {
        return Err(ServiceError::Authority(AuthorityError::Conflict(format!(
            "user '{owner_user_id}' is the owner of bot '{}'; owner authority \
             never derives from a team manager source",
            command.bot_id
        ))));
    }

    // -- Snapshot human validation (live, same env, not soft-deleted) ----
    let mut invalid: Vec<String> = Vec::new();
    for user_id in &desired {
        let actor_id = human_actor_id(user_id);
        let live = match bots.get(&actor_id) {
            Some(row) => {
                row.actor_kind == ActorKind::Human && row.env.as_deref() == Some(env.as_str())
            }
            None => false,
        };
        if !live || deleted_ids.contains(&actor_id) {
            invalid.push(user_id.clone());
        }
    }
    if !invalid.is_empty() {
        return Err(ServiceError::Authority(AuthorityError::InvalidSubject(format!(
            "team manager snapshot of bot '{}' names non-live/unknown humans: {}",
            command.bot_id,
            invalid.join(", ")
        ))));
    }

    // -- Lanes: current sources, classification, reconcile --------------
    let old_members = current_team_members(&authority, &env, &command.bot_id, &command.team_id);
    let (target_team, target_members) = match &command.operation {
        TeamManagerOperation::Sync => (command.team_id.clone(), old_members.clone()),
        TeamManagerOperation::Move { new_team_id } => (
            new_team_id.clone(),
            current_team_members(&authority, &env, &command.bot_id, new_team_id),
        ),
    };
    let revived = revoked_team_members(
        &authority,
        &env,
        &command.bot_id,
        &target_team,
        &canonical_users,
    );

    let operation_id = uuid::Uuid::new_v4().to_string();
    let actor = AuditActor::Service {
        service_id: command.service.service_id.clone(),
    };
    let mut granted_count: u64 = 0;
    let mut revoked_count: u64 = 0;

    // A Move stops the old team FIRST: every approved old edge flips.
    if matches!(command.operation, TeamManagerOperation::Move { .. }) {
        let stopped_users: Vec<String> = old_members.iter().cloned().collect();
        let stopped = revoke_team_members(
            &mut authority,
            &env,
            &command.bot_id,
            &command.team_id,
            &stopped_users,
        );
        for (user_id, edge_id) in stopped {
            authority.append_manager_audit(
                &operation_id,
                &user_id,
                edge_id,
                "team",
                &command.team_id,
                "revoke",
                &actor,
            );
            revoked_count += 1;
        }
        upsert_binding(
            &mut authority,
            &env,
            &command.bot_id,
            &command.team_id,
            "stopped",
            &operation_id,
        );
    }

    // The reconciled target lane: `removed = current − desired`,
    // `added = desired − current` split into restored vs fresh rows.
    let removed: Vec<String> = target_members
        .iter()
        .filter(|user_id| !desired.contains(*user_id))
        .cloned()
        .collect();
    let added: Vec<String> = desired
        .iter()
        .filter(|user_id| !target_members.contains(*user_id))
        .cloned()
        .collect();
    let (restored_users, fresh_users): (Vec<String>, Vec<String>) =
        added.into_iter().partition(|user_id| revived.contains(user_id));

    let revoked_rows = revoke_team_members(&mut authority, &env, &command.bot_id, &target_team, &removed);
    for (user_id, edge_id) in revoked_rows {
        authority.append_manager_audit(
            &operation_id,
            &user_id,
            edge_id,
            "team",
            &target_team,
            "revoke",
            &actor,
        );
        revoked_count += 1;
    }

    let restored_rows = restore_team_members(
        &mut authority,
        &env,
        &command.bot_id,
        &target_team,
        &restored_users,
    );
    let fresh_rows = insert_fresh_team_members(
        &mut authority,
        &env,
        &command.bot_id,
        &target_team,
        &fresh_users,
    );
    for (user_id, edge_id) in restored_rows.iter().chain(fresh_rows.iter()) {
        authority.append_manager_audit(
            &operation_id,
            user_id,
            *edge_id,
            "team",
            &target_team,
            "grant",
            &actor,
        );
        granted_count += 1;
    }

    // The binding of the reconciled team stays/becomes `active` — even
    // for an EMPTY snapshot, an active binding with no members is legal.
    upsert_binding(
        &mut authority,
        &env,
        &command.bot_id,
        &target_team,
        "active",
        &operation_id,
    );

    // -- The durable receipt (committed with everything above) ----------
    let receipt = TeamSyncReceipt {
        operation_id: operation_id.clone(),
        bot_id: command.bot_id.clone(),
        team_id: command.team_id.clone(),
        operation: command.operation.clone(),
        granted_count,
        revoked_count,
    };
    authority.sync_operations.push(MemoryTeamSyncOperationRecord {
        env,
        service_id: command.service.service_id.clone(),
        bot_id: command.bot_id.clone(),
        team_id: command.team_id.clone(),
        payload,
        idempotency_key: command.idempotency_key.clone(),
        receipt: receipt.clone(),
    });
    Ok(receipt)
}

/// The current approved members of ONE team's manager source.
fn current_team_members(
    state: &MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    team_id: &str,
) -> BTreeSet<String> {
    let mut members = BTreeSet::new();
    for row in &state.role_rows {
        if row.env == env
            && row.to_id == bot_id
            && row.grant_kind == "manager"
            && row.management_source_kind == "team"
            && row.management_source_id == team_id
            && row.status == "approved"
        {
            if let Some(user_id) = user_id_from_actor(&row.from_id) {
                members.insert(user_id.to_string());
            }
        }
    }
    members
}

/// Which snapshot users already hold a REVOKED row of the reconciled
/// team's slot (restored-under-same-id vs fresh-insert classification —
/// the mirror of the SQL store's classification read).
fn revoked_team_members(
    state: &MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    team_id: &str,
    users: &[String],
) -> BTreeSet<String> {
    let mut revived = BTreeSet::new();
    for row in &state.role_rows {
        if row.env != env
            || row.to_id != bot_id
            || row.grant_kind != "manager"
            || row.management_source_kind != "team"
            || row.management_source_id != team_id
            || row.status != "revoked"
        {
            continue;
        }
        if let Some(user_id) = user_id_from_actor(&row.from_id) {
            if users.iter().any(|candidate| candidate == user_id) {
                revived.insert(user_id.to_string());
            }
        }
    }
    revived
}

/// Revoke the given members' approved rows of ONE team lane; returns the
/// `(user_id, edge_id)` pairs actually flipped (only real changes).
fn revoke_team_members(
    state: &mut MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    team_id: &str,
    users: &[String],
) -> Vec<(String, i64)> {
    if users.is_empty() {
        return Vec::new();
    }
    let targets: Vec<String> = users.iter().map(|user_id| human_actor_id(user_id)).collect();
    let mut flipped = Vec::new();
    for row in state.role_rows.iter_mut() {
        if row.env == env
            && row.to_id == bot_id
            && row.grant_kind == "manager"
            && row.management_source_kind == "team"
            && row.management_source_id == team_id
            && row.status == "approved"
            && targets.contains(&row.from_id)
        {
            row.status = "revoked".to_string();
            let user_id = user_id_from_actor(&row.from_id)
                .unwrap_or_default()
                .to_string();
            flipped.push((user_id, row.id));
        }
    }
    flipped
}

/// RESTORE the members' previously revoked rows of ONE team lane under
/// the SAME edge ids — never a second slot, never INSERT-IGNORE.
fn restore_team_members(
    state: &mut MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    team_id: &str,
    users: &[String],
) -> Vec<(String, i64)> {
    if users.is_empty() {
        return Vec::new();
    }
    let targets: Vec<String> = users.iter().map(|user_id| human_actor_id(user_id)).collect();
    let mut restored = Vec::new();
    for row in state.role_rows.iter_mut() {
        if row.env == env
            && row.to_id == bot_id
            && row.grant_kind == "manager"
            && row.management_source_kind == "team"
            && row.management_source_id == team_id
            && row.status == "revoked"
            && targets.contains(&row.from_id)
        {
            row.status = "approved".to_string();
            let user_id = user_id_from_actor(&row.from_id)
                .unwrap_or_default()
                .to_string();
            restored.push((user_id, row.id));
        }
    }
    restored
}

/// Insert fresh approved team-slot rows for members holding no row in
/// ANY status. The classification guarantees this precondition; the
/// check stays as the defensive unique-slot stand-in of the SQL lane
/// (a taken slot is skipped, never duplicated).
fn insert_fresh_team_members(
    state: &mut MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    team_id: &str,
    users: &[String],
) -> Vec<(String, i64)> {
    let mut inserted = Vec::new();
    for user_id in users {
        let from_id = human_actor_id(user_id);
        let slot_taken = state.role_rows.iter().any(|row| {
            row.env == env
                && row.to_id == bot_id
                && row.grant_kind == "manager"
                && row.management_source_kind == "team"
                && row.management_source_id == team_id
                && row.from_id == from_id
        });
        if slot_taken {
            continue;
        }
        let edge_id = state.allocate_edge_id();
        state.role_rows.push(MemoryRoleEdgeRow {
            id: edge_id,
            env: env.to_string(),
            from_id,
            to_id: bot_id.to_string(),
            grant_kind: "manager".to_string(),
            grant_ref_id: ROLE_GRANT_REF_ID as i64,
            rules: None,
            status: "approved".to_string(),
            management_source_kind: "team".to_string(),
            management_source_id: team_id.to_string(),
        });
        inserted.push((user_id.clone(), edge_id));
    }
    inserted
}

/// The `bot_team_manager_sources` mirror upsert: `active` (even for an
/// EMPTY snapshot — the binding may represent an active team with no
/// members) or `stopped` (a Move's old team), with the operation id.
fn upsert_binding(
    state: &mut MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    team_id: &str,
    status: &str,
    operation_id: &str,
) {
    state.team_source_bindings.insert(
        (env.to_string(), bot_id.to_string(), team_id.to_string()),
        MemoryTeamSourceBinding {
            status: status.to_string(),
            last_operation_id: operation_id.to_string(),
        },
    );
}
/// One `bot_manager_sync_operations` record projection (plan Task 7):
/// the durable idempotency scope plus the canonical payload and the
/// persisted minimal receipt.
#[derive(Debug, Clone)]
pub(crate) struct MemoryTeamSyncOperationRecord {
    pub(crate) env: String,
    pub(crate) service_id: String,
    pub(crate) bot_id: String,
    pub(crate) team_id: String,
    pub(crate) payload: String,
    pub(crate) idempotency_key: String,
    pub(crate) receipt: TeamSyncReceipt,
}

/// One `bot_team_manager_sources` row projection (plan Task 7).
#[derive(Debug, Clone)]
pub(crate) struct MemoryTeamSourceBinding {
    pub(crate) status: String,
    pub(crate) last_operation_id: String,
}

// ---------------------------------------------------------------------------
// Test-only projection levers (moved beside the team-sync engine)
// ---------------------------------------------------------------------------

impl MemoryBotRepo {
    /// Test-only projection of the Bot's `bot_team_manager_sources` mirror
    /// (plan Task 7): `(team_id, status, last_operation_id)` rows of the
    /// current env, sorted team_id ASC — the binding-state lever of the
    /// team-sync conformance.
    pub async fn authority_team_bindings(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Vec<(String, String, String)>> {
        let env = resolve_env();
        let authority = self.authority.read().await;
        let mut rows: Vec<(String, String, String)> = authority
            .team_source_bindings
            .iter()
            .filter(|((binding_env, binding_bot, _), _)| {
                binding_env == &env && binding_bot == bot_id
            })
            .map(|((_, _, team_id), binding)| {
                (
                    team_id.clone(),
                    binding.status.clone(),
                    binding.last_operation_id.clone(),
                )
            })
            .collect();
        rows.sort();
        Ok(rows)
    }

    /// Test-only projection of the Bot's committed
    /// `bot_manager_sync_operations` mirror row count (plan Task 7:
    /// durable idempotency receipts — replays never append a second row,
    /// even completely no-difference syncs persist exactly one).
    pub async fn authority_sync_operation_count(&self, bot_id: &str) -> ServiceResult<u64> {
        let env = resolve_env();
        let authority = self.authority.read().await;
        Ok(authority
            .sync_operations
            .iter()
            .filter(|record| record.env == env && record.bot_id == bot_id)
            .count() as u64)
    }
}
