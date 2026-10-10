//! Team manager source synchronization (plan Task 7, spec §5.4/§6/§1.3).
//!
//! `sync_team` is the ONE-TRANSACTION lane that writes `team/*` manager
//! edges. The direct API (`mutate_manager`) never touches team rows;
//! this lane never touches `direct`/`ownership_transfer` rows: one
//! command replaces exactly ONE team's source and leaves every other
//! source of every other subject intact (a direct-only manager survives
//! an empty team sync untouched, and other teams' members stay).
//!
//! One command → ONE of two reconciles:
//! - `Sync`: `added = desired − current`, `removed = current − desired`
//!   for THIS team (`desired`/`current` are the validated same-Bot/
//!   same-team sets, never the global manager set);
//! - `Move`: atomically STOP the old team (binding `stopped`, ALL of its
//!   approved edges revoked) and write the complete snapshot at the new
//!   team — an existing new-team snapshot is REPLACED (never merged into
//!   "some other team"); replaying the move's key (or any older key)
//!   never resurrects the stopped team.
//!
//! Protocol — the Task 4 optimistic-revalidate shape, applied to the
//! aggregate lane:
//! 1. a pure command/credential validation runs BEFORE any transaction:
//!    structural identity fields, scope re-check against the verified
//!    service (env/Bot/team/operation — never a raw client actor),
//!    snapshot normalization (deduplicated, case-sensitively sorted
//!    `BTreeSet`; blank members rejected; the Gate 0
//!    [`TEAM_SYNC_MAX_SNAPSHOT`] bound — spec §1.3), and the canonical
//!    durable payload;
//! 2. a validated read transaction (one fast-path snapshot over the
//!    target Bot's rows) discovers a SAME-KEY receipt — same payload →
//!    the ORIGINAL receipt returns WITHOUT recompute (no writes, no
//!    audit rows); different payload → `Conflict` — or plans the chunked
//!    writes: Human liveness validation, revoked-vs-fresh classification
//!    of the added set, the removed set, the exclusive owner slot, and
//!    the current sources of the affected team(s);
//! 3. ONE write transaction on the serialized Bot boundary performs
//!    lock → validated writes → audit → binding → DURABLE receipt, all
//!    committing TOGETHER. The brief's lock-then-re-read rule holds
//!    INSIDE that transaction: the pinned changing statements re-derive
//!    the current source rows under the lock (row conditions + live/
//!    not-owner subject guards), and the guarded receipt INSERT
//!    re-checks the idempotency-key slot (`NOT EXISTS` over the unique
//!    scope) plus the live-initialized-Bot/owner-slot guards, so any
//!    drift rolls the WHOLE attempt back (`ExecuteChecked` pins),
//!    re-validates and surfaces the branch the new state demands
//!    (bounded retries; a same-key racer resolves as the racer's
//!    original receipt or its conflicting payload).
//!
//! Statement budget (no N+1, spec §13.5): per chunk of at most 100
//! Humans (60 for guarded fresh-INSERT arms, keeping every statement's
//! parameter count far below the SQLite bind ceiling) there is at most
//! ONE revoke UPDATE, ONE restore UPDATE, ONE fresh multi-INSERT, ONE
//! grant audit INSERT-SELECT and ONE revoke audit INSERT-SELECT — the
//! count grows with ceil(n/chunk), never with n, never per subject.
//! All chunks stay inside the SAME Bot transaction; the atomic snapshot
//! is never assembled from multiple transactions.
//!
//! Audit: only actually-changed edges append `bot_manager_changes` rows
//! through the Task 4 shared primitives (columns + `audit_id` ==
//! `<operation_id>-<edge_id>`, batched INSERT-SELECT with the same row
//! conditions as the changing statements), recording the TRUE operator:
//! the Service actor derived from the verified credential.

use std::collections::BTreeSet;

use bcs_db_api::{DbError, DbRow, DbTransactionStep, DbTransactionStepResult};
use bcs_service_api::port::repo::bot_authority::{human_actor_id, user_id_from_actor};
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::team_manager_sync::{
    canonical_team_sync_payload, decode_team_sync_receipt, encode_team_sync_receipt,
    normalize_team_manager_snapshot, validate_team_sync_command, TeamManagerOperation,
    TeamManagerSync, TeamSyncReceipt, VerifiedTeamManagerService, TEAM_SYNC_MAX_SNAPSHOT,
};
use bcs_service_api::types::AuditActor;
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

use crate::common::service_db_error;

/// IN-list chunk: subject validation / classification / revoke / restore /
/// audit statements bind at most `chunk + 6` parameters.
const TEAM_SYNC_LIST_CHUNK: usize = 100;
/// Fresh-INSERT chunk: each guarded union arm binds 13 parameters, so a
/// statement binds at most `60 * 13 = 780` parameters (safely below the
/// SQLite bind ceiling).
pub(super) const TEAM_SYNC_INSERT_CHUNK: usize = 60;
/// Optimistic-write attempts before surfacing a retryable conflict.
const MAX_SYNC_ATTEMPTS: usize = 3;

/// Assert the Gate 0 snapshot bound stays at the documented value; the
/// chunk math below is written against 1,000 (spec §1.3).
const _: () = assert!(TEAM_SYNC_MAX_SNAPSHOT == 1_000);

enum SyncAttempt {
    /// The committed (or replayed) receipt.
    Done(TeamSyncReceipt),
    /// The optimistic window drifted; the caller re-validates.
    Drift(DbError),
    /// A business or infrastructure branch — surface it unchanged.
    Service(ServiceError),
}

impl From<ServiceError> for SyncAttempt {
    fn from(err: ServiceError) -> Self {
        SyncAttempt::Service(err)
    }
}

impl super::reads::DbBotAuthorityStore {
    // ------------------------------------------------------------------
    // Entry + attempt loop
    // ------------------------------------------------------------------

    pub(super) async fn sync_team_inner(
        &self,
        command: TeamManagerSync,
    ) -> ServiceResult<TeamSyncReceipt> {
        // -- pre-transaction pure validation (fast-fail, no DB access) ----
        validate_team_sync_command(&command)?;
        command.service.authorize_sync(&self.env, &command)?;
        let desired = normalize_team_manager_snapshot(&command.manager_user_ids)
            .map_err(ServiceError::Authority)?;
        let canonical_users: Vec<String> = desired.iter().cloned().collect();
        let payload =
            canonical_team_sync_payload(&command.team_id, &command.operation, &canonical_users)?;
        let prepared = PreparedSync {
            service: command.service.clone(),
            bot_id: command.bot_id.clone(),
            team_id: command.team_id.clone(),
            operation: command.operation.clone(),
            desired,
            idempotency_key: command.idempotency_key.clone(),
            payload,
            actor: command.service.audit_actor(),
        };

        let mut last_drift = None;
        for _ in 0..MAX_SYNC_ATTEMPTS {
            match self.sync_team_attempt(&prepared).await {
                SyncAttempt::Done(receipt) => return Ok(receipt),
                SyncAttempt::Drift(err) => {
                    warn!(
                        bot_id = %prepared.bot_id,
                        "team manager sync lost its optimistic window, re-validating: {err}"
                    );
                    last_drift = Some(err);
                }
                SyncAttempt::Service(err) => return Err(err),
            }
        }
        Err(ServiceError::Authority(AuthorityError::Conflict(format!(
            "concurrent team manager sync on bot '{}' team '{}'; retry: {}",
            prepared.bot_id,
            prepared.team_id,
            last_drift
                .map(|err| err.to_string())
                .unwrap_or_else(|| "optimistic window lost repeatedly".to_string())
        ))))
    }

    async fn sync_team_attempt(&self, prepared: &PreparedSync) -> SyncAttempt {
        match self.sync_team_validate(prepared).await {
            Err(err) => SyncAttempt::Service(err),
            Ok(ValidateOutcome::Replay(receipt)) => SyncAttempt::Done(receipt),
            Ok(ValidateOutcome::Fresh(plan)) => self.sync_team_commit(prepared, plan).await,
        }
    }

    // ------------------------------------------------------------------
    // Phase A: validated read transaction
    // ------------------------------------------------------------------

    /// The validated read transaction: Bot invariant, the same-key
    /// idempotency slot, the exclusive owner slot and the current
    /// sources of every affected team, plus the chunked Human-liveness
    /// validation and revoked classification of the snapshot.
    async fn sync_team_validate(&self, prepared: &PreparedSync) -> ServiceResult<ValidateOutcome> {
        let reconciled_team = prepared
            .operation
            .reconciled_team(&prepared.team_id)
            .to_string();

        let mut read_steps = vec![
            DbTransactionStep::Query(self.sync_read_aggregate_statement(&prepared.bot_id)),
            DbTransactionStep::Query(self.sync_receipt_statement(prepared)),
            DbTransactionStep::Query(self.sync_owner_slot_statement(&prepared.bot_id)),
            DbTransactionStep::Query(
                self.sync_current_members_statement(&prepared.bot_id, &prepared.team_id),
            ),
        ];
        if let TeamManagerOperation::Move { .. } = &prepared.operation {
            read_steps.push(DbTransactionStep::Query(
                self.sync_current_members_statement(&prepared.bot_id, &reconciled_team),
            ));
        }
        let snapshot: Vec<String> = prepared.desired.iter().cloned().collect();
        for chunk in snapshot.chunks(TEAM_SYNC_LIST_CHUNK) {
            read_steps.push(DbTransactionStep::Query(
                self.sync_live_humans_chunk_statement(chunk),
            ));
        }
        for chunk in snapshot.chunks(TEAM_SYNC_LIST_CHUNK) {
            read_steps.push(DbTransactionStep::Query(
                self.sync_revoked_members_statement(&prepared.bot_id, &reconciled_team, chunk),
            ));
        }

        let outcome = self
            .db
            .transaction(read_steps)
            .await
            .map_err(|err| service_db_error("authority_sync_read", err))?;

        // -- Bot invariant (live, initialized, unique approved owner) ------
        let aggregate_rows = step_rows(&outcome, 0, "the aggregate query")?;
        let Some(aggregate) = aggregate_rows.first().cloned() else {
            return Err(ServiceError::BotNotFound(prepared.bot_id.clone()));
        };
        let ownership_version = sync_row_u64(&aggregate, "ownership_version")?;
        if ownership_version == bcs_domain::UNINITIALIZED_OWNERSHIP_VERSION {
            return Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
                bot_id: prepared.bot_id.clone(),
                env: self.env.clone(),
            }));
        }
        let owner_edge_count = sync_row_u64(&aggregate, "owner_edge_count")?;
        if owner_edge_count != 1 {
            return Err(self.corrupt(
                &prepared.bot_id,
                format!(
                    "initialized bot has {owner_edge_count} approved owner edges \
                     (team sync denied)"
                ),
            ));
        }

        // -- Idempotency slot: replay the original receipt (same canonical
        //    payload) or refuse the diverged one — never recompute.
        let receipt_rows = step_rows(&outcome, 1, "the idempotency receipt query")?;
        if let Some(receipt_row) = receipt_rows.first() {
            let stored_payload = required_text(receipt_row, "payload")?;
            if stored_payload != prepared.payload {
                return Err(ServiceError::Authority(AuthorityError::Conflict(format!(
                    "team sync key '{}' on bot '{}' team '{}' was already used \
                     with a different payload/operation",
                    prepared.idempotency_key, prepared.bot_id, prepared.team_id
                ))));
            }
            let stored_result = required_text(receipt_row, "result")?;
            return decode_team_sync_receipt(&stored_result, &prepared.bot_id, &self.env)
                .map(ValidateOutcome::Replay);
        }

        // -- Owner slot vs the snapshot -----------------------------------
        let owner_rows = step_rows(&outcome, 2, "the owner-slot query")?;
        if owner_rows.len() != 1 {
            return Err(self.corrupt(
                &prepared.bot_id,
                format!(
                    "initialized bot has {} approved owner edges in the owner-slot read",
                    owner_rows.len()
                ),
            ));
        }
        let owner_from_id = required_text(&owner_rows[0], "from_id")?;
        let Some(owner_user_id) = user_id_from_actor(&owner_from_id) else {
            return Err(self.corrupt(
                &prepared.bot_id,
                format!("owner edge from non-human actor id '{owner_from_id}'"),
            ));
        };
        if prepared.desired.contains(owner_user_id) {
            return Err(ServiceError::Authority(AuthorityError::Conflict(format!(
                "user '{owner_user_id}' is the owner of bot '{}'; owner authority \
                 never derives from a team manager source",
                prepared.bot_id
            ))));
        }

        // -- Current sources of the affected team(s) ------------------------
        let mut next = 3;
        let old_team_members =
            self.decode_members(prepared, &outcome, next, "old team sources")?;
        next += 1;
        let new_team_members = if matches!(prepared.operation, TeamManagerOperation::Move { .. }) {
            let members =
                self.decode_members(prepared, &outcome, next, "move-target team sources")?;
            next += 1;
            members
        } else {
            BTreeSet::new()
        };

        // -- Chunked liveness validation of the whole snapshot --------------
        for chunk in snapshot.chunks(TEAM_SYNC_LIST_CHUNK) {
            let lived: Vec<String> = step_rows(&outcome, next, "the human-liveness query")?
                .iter()
                .filter_map(|row| row.get_string("bot_uuid").ok().flatten())
                .collect();
            let missing: Vec<String> = chunk
                .iter()
                .filter(|user_id| {
                    !lived
                        .iter()
                        .any(|lived_id| lived_id == &human_actor_id(user_id))
                })
                .cloned()
                .collect();
            if !missing.is_empty() {
                return Err(ServiceError::Authority(AuthorityError::InvalidSubject(format!(
                    "team manager snapshot of bot '{}' names non-live/unknown humans: {}",
                    prepared.bot_id,
                    missing.join(", ")
                ))));
            }
            next += 1;
        }

        // -- Revoked-vs-fresh classification of the added set ---------------
        let mut revived: BTreeSet<String> = BTreeSet::new();
        for _ in snapshot.chunks(TEAM_SYNC_LIST_CHUNK) {
            for from_id in step_rows(&outcome, next, "the revoked-classification query")?
                .iter()
                .filter_map(|row| row.get_string("from_id").ok().flatten())
            {
                if let Some(user_id) = user_id_from_actor(&from_id) {
                    revived.insert(user_id.to_string());
                }
            }
            next += 1;
        }

        // -- The validated reconcile plan -----------------------------------
        match &prepared.operation {
            TeamManagerOperation::Sync => {
                let lane = reconcile_lane(
                    &prepared.desired,
                    old_team_members,
                    &revived,
                );
                Ok(ValidateOutcome::Fresh(SyncPlan {
                    stop_team: None,
                    target: (prepared.team_id.clone(), lane),
                }))
            }
            TeamManagerOperation::Move { new_team_id } => {
                let stopped: Vec<String> =
                    old_team_members.iter().cloned().collect();
                let lane = reconcile_lane(&prepared.desired, new_team_members, &revived);
                Ok(ValidateOutcome::Fresh(SyncPlan {
                    stop_team: Some((prepared.team_id.clone(), stopped)),
                    target: (new_team_id.clone(), lane),
                }))
            }
        }
    }

    /// Strictly decode one current-source row set into user ids.
    fn decode_members(
        &self,
        prepared: &PreparedSync,
        results: &[DbTransactionStepResult],
        index: usize,
        context: &'static str,
    ) -> ServiceResult<BTreeSet<String>> {
        let mut members = BTreeSet::new();
        for row in step_rows(results, index, context)? {
            let from_id = required_text(row, "from_id")?;
            match user_id_from_actor(&from_id) {
                Some(user_id) => {
                    members.insert(user_id.to_string());
                }
                None => {
                    return Err(self.corrupt(
                        &prepared.bot_id,
                        format!("team manager edge from non-human actor id '{from_id}'"),
                    ))
                }
            }
        }
        Ok(members)
    }

    // ------------------------------------------------------------------
    // Phase B: the all-or-nothing write transaction
    // ------------------------------------------------------------------

    /// The write transaction: locked Bot boundary → guarded chunked
    /// changes → audits → binding upserts → the guarded durable receipt.
    async fn sync_team_commit(&self, prepared: &PreparedSync, plan: SyncPlan) -> SyncAttempt {
        let operation_id = uuid::Uuid::new_v4().to_string();
        let mut granted_total: u64 = 0;
        let mut revoked_total: u64 = 0;

        let mut steps = vec![DbTransactionStep::Query(
            self.bot_lock_statement(&prepared.bot_id),
        )];

        // Move: stop the old team FIRST (binding row, every approved edge).
        if let Some((stop_team, stopped_users)) = &plan.stop_team {
            for chunk in stopped_users.chunks(TEAM_SYNC_LIST_CHUNK) {
                steps.push(DbTransactionStep::ExecuteChecked {
                    statement: self.sync_lane_revoke_statement(
                        &prepared.bot_id,
                        stop_team,
                        chunk,
                    ),
                    expected_affected_rows: chunk.len() as u64,
                });
                steps.push(DbTransactionStep::ExecuteChecked {
                    statement: self.sync_lane_audit_statement(
                        "revoke",
                        &prepared.bot_id,
                        stop_team,
                        chunk,
                        &prepared.actor,
                        &operation_id,
                        &prepared.idempotency_key,
                    ),
                    expected_affected_rows: chunk.len() as u64,
                });
                revoked_total += chunk.len() as u64;
            }
        }

        // The reconciled target team: revokes before restores/inserts, and
        // one grant-audit pass over the union of restored+fresh rows.
        let (target_team, lane) = &plan.target;
        for chunk in lane.removed.chunks(TEAM_SYNC_LIST_CHUNK) {
            steps.push(DbTransactionStep::ExecuteChecked {
                statement: self.sync_lane_revoke_statement(&prepared.bot_id, target_team, chunk),
                expected_affected_rows: chunk.len() as u64,
            });
            steps.push(DbTransactionStep::ExecuteChecked {
                statement: self.sync_lane_audit_statement(
                    "revoke",
                    &prepared.bot_id,
                    target_team,
                    chunk,
                    &prepared.actor,
                    &operation_id,
                    &prepared.idempotency_key,
                ),
                expected_affected_rows: chunk.len() as u64,
            });
            revoked_total += chunk.len() as u64;
        }
        for chunk in lane.restored.chunks(TEAM_SYNC_LIST_CHUNK) {
            steps.push(DbTransactionStep::ExecuteChecked {
                statement: self.sync_lane_restore_statement(&prepared.bot_id, target_team, chunk),
                expected_affected_rows: chunk.len() as u64,
            });
        }
        for chunk in lane.fresh.chunks(TEAM_SYNC_INSERT_CHUNK) {
            steps.push(DbTransactionStep::ExecuteChecked {
                statement: self
                    .sync_lane_fresh_insert_statement(&prepared.bot_id, target_team, chunk),
                expected_affected_rows: chunk.len() as u64,
            });
        }
        let granted_list = lane.granted_list();
        for chunk in granted_list.chunks(TEAM_SYNC_LIST_CHUNK) {
            steps.push(DbTransactionStep::ExecuteChecked {
                statement: self.sync_lane_audit_statement(
                    "grant",
                    &prepared.bot_id,
                    target_team,
                    chunk,
                    &prepared.actor,
                    &operation_id,
                    &prepared.idempotency_key,
                ),
                expected_affected_rows: chunk.len() as u64,
            });
            granted_total += chunk.len() as u64;
        }

        // Bindings: the move stops its old team binding and activates the
        // new one; a sync (re)activates its own team — even for an EMPTY
        // snapshot, an active binding with no members is a legal state.
        if let Some((stop_team, _)) = &plan.stop_team {
            steps.push(DbTransactionStep::Execute(
                self.sync_binding_upsert_statement(&prepared.bot_id, stop_team, &operation_id, false),
            ));
        }
        steps.push(DbTransactionStep::Execute(
            self.sync_binding_upsert_statement(&prepared.bot_id, target_team, &operation_id, true),
        ));

        // The durable receipt last, guarded by the same locking window.
        let receipt = TeamSyncReceipt {
            operation_id: operation_id.clone(),
            bot_id: prepared.bot_id.clone(),
            team_id: prepared.team_id.clone(),
            operation: prepared.operation.clone(),
            granted_count: granted_total,
            revoked_count: revoked_total,
        };
        let result_text = match encode_team_sync_receipt(&receipt) {
            Ok(text) => text,
            Err(err) => return SyncAttempt::Service(err),
        };
        steps.push(DbTransactionStep::ExecuteChecked {
            statement: self.sync_receipt_insert_statement(prepared, &operation_id, &result_text),
            expected_affected_rows: 1,
        });

        match self.db.transaction(steps).await {
            Ok(_) => SyncAttempt::Done(receipt),
            Err(err) => self.map_sync_write_error(err),
        }
    }

    fn map_sync_write_error(&self, err: DbError) -> SyncAttempt {
        if err.is_duplicate_key() {
            // A same-key racer committed first: re-validate; the retry
            // replays their receipt or surfaces the conflicting payload.
            return SyncAttempt::Drift(err);
        }
        match err {
            // A pinned statement's affected rows no longer match the plan:
            // the validated window drifted — roll back and re-validate.
            DbError::ConditionFailed { expected, actual } => {
                SyncAttempt::Drift(DbError::ConditionFailed { expected, actual })
            }
            other => SyncAttempt::Service(service_db_error("authority_sync_write", other)),
        }
    }

}

// ---------------------------------------------------------------------------
// Pure plan shapes
// ---------------------------------------------------------------------------

/// The command inputs after pre-transaction validation, shared with the
/// `team_sync_sql` statement builders.
pub(super) struct PreparedSync {
    pub(super) service: VerifiedTeamManagerService,
    pub(super) bot_id: String,
    pub(super) team_id: String,
    pub(super) operation: TeamManagerOperation,
    pub(super) desired: BTreeSet<String>,
    pub(super) idempotency_key: String,
    pub(super) payload: String,
    pub(super) actor: AuditActor,
}

enum ValidateOutcome {
    Replay(TeamSyncReceipt),
    Fresh(SyncPlan),
}

/// The validated plan of one committed operation: the move's stopped
/// team (with every member to revoke) plus the fully-reconciled target
/// team lane.
struct SyncPlan {
    /// `(team_id, every approved member to revoke)`; `None` for `Sync`.
    stop_team: Option<(String, Vec<String>)>,
    /// The reconciled team (the sync team, or the move's new team).
    target: (String, UserLane),
}

/// The validated, chunkable change lists of one lane.
struct UserLane {
    /// approved members this operation revokes
    removed: Vec<String>,
    /// revoked rows restored under the same row id
    restored: Vec<String>,
    /// fresh rows inserted
    fresh: Vec<String>,
}

impl UserLane {
    /// The whole granted set of the lane (restored + fresh) in canonical
    /// user order — one audit pass covers both row classes after their
    /// post-state (approved) matches.
    fn granted_list(&self) -> Vec<String> {
        let mut granted: BTreeSet<String> = self.restored.iter().cloned().collect();
        granted.extend(self.fresh.iter().cloned());
        granted.into_iter().collect()
    }
}

/// Brief step 3, verbatim semantics: `added = desired − current` (split
/// into restored vs fresh by the classification read) and
/// `removed = current − desired`. Inputs are already the validated
/// same-Bot/same-team sets — never the global manager set.
fn reconcile_lane(
    desired: &BTreeSet<String>,
    current: BTreeSet<String>,
    revived: &BTreeSet<String>,
) -> UserLane {
    let removed: Vec<String> = current.difference(desired).cloned().collect();
    let added: Vec<String> = desired.difference(&current).cloned().collect();
    let (restored, fresh) = added
        .into_iter()
        .partition(|user_id| revived.contains(user_id));
    UserLane {
        removed,
        restored,
        fresh,
    }
}

/// Borrow one Query step's rows from a transaction result, positionally
/// and strictly: a mis-shaped or missing step is an internal error,
/// never silently skipped.
fn step_rows<'a>(
    results: &'a [DbTransactionStepResult],
    index: usize,
    context: &'static str,
) -> ServiceResult<&'a [DbRow]> {
    match results.get(index) {
        Some(DbTransactionStepResult::Rows(rows)) => Ok(rows),
        _ => Err(ServiceError::InternalError(format!(
            "authority_sync_read: expected the {context} query at step {index}"
        ))),
    }
}

fn sync_row_u64(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    row.get_i64(column)
        .ok()
        .flatten()
        .map(|value| value.max(0) as u64)
        .ok_or_else(|| {
            ServiceError::InternalError(format!(
                "authority sync aggregate column '{column}' missing or NULL"
            ))
        })
}

fn required_text(row: &DbRow, column: &'static str) -> ServiceResult<String> {
    match row.get_string(column) {
        Ok(Some(text)) => Ok(text),
        _ => Err(ServiceError::InternalError(format!(
            "authority sync row column '{column}' missing or NULL"
        ))),
    }
}