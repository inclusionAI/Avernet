//! Ownership-transfer write paths (`change_transfer`)
//! and their lazy-closure conflict semantics
//! (bot-authority plan Task 3, spec §6).
//!
//! ## Transaction shape (Task-0 sketch, feasibility
//! doc §2; lock order Bot → transfer row → Human,
//! spec §7 — this path never holds a binding)
//!
//! Every command classifies from a pre-read
//! (parameter preparation only), then runs ONE
//! `DbPlugin::transaction_with_options` call whose
//! guards re-verify that classification under the
//! Bot row lock (`FOR UPDATE` on MySQL). Guard
//! failure rolls the transaction back deterministically
//! and the caller retries from a fresh pre-read
//! (bounded; same retryability rules as the manager
//! paths — `DbError::Timeout` is never retried).
//! Guard rows are consumed by a trailing step
//! (`SELECT ? AS t0, ...`), so a failed guard fails
//! the transaction at binding resolution (Task-0 s1c).
//!
//! Outcomes follow plan §3: [`TransferOutcome::Applied`]
//! for real state changes (Created/Updated/Unchanged)
//! and [`TransferOutcome::Conflict`] for *committed*
//! business conflicts (`transfer_pending`,
//! `owner_changed`, `transfer_expired`,
//! `transfer_state_conflict`). A `Conflict` is never
//! an `Err`: expiry/invalidation lazily close the
//! dead pending inside the SAME transaction (audit +
//! closure commit first, spec §6.4). The initiation
//! applies the same rule to OTHER-target pendings
//! (spec §6.2): a LIVE other-target pending (fresh
//! AND matching the current round) conflicts
//! (`transfer_pending`); a DEAD one (expired by DB
//! time, or owner/version mismatched) is closed in
//! the SAME transaction the new-target row is
//! created in — the per-bot pending unique key
//! (`uk_transfer_pending`) is held by the dead
//! row's still-`pending` status, so re-initiation
//! to a different target would otherwise hit the
//! key until the retry budget expired. The no-write
//! conflict outcomes (`transfer_state_conflict`,
//! stored expired/invalidated, absent rows) still
//! pass a guard transaction so a stale
//! classification can never answer instead of a
//! fresher state — a stale pre-read retries and
//! re-classifies.
//!
//! ## State machine (spec §6.4)
//!
//! | effective current state | action | outcome |
//! | --- | --- | --- |
//! | pending (valid, snapshot matches) | decision | terminal Applied |
//! | terminal accepted/rejected/cancelled | SAME decision, eligible caller | Unchanged, no writes |
//! | terminal accepted/rejected/cancelled | different decision | Conflict{transfer_state_conflict}, no writes |
//! | stored expired / invalidated | any decision | Conflict{transfer_expired / owner_changed}, no writes |
//! | pending past expiry | any decision | close expired (audited) → Conflict{transfer_expired} |
//! | pending, owner/version mismatched | any decision | close invalidated (audited) → Conflict{owner_changed} |
//! | no row / terminal | current owner re-initiates | pending round (Created) |
//! | LIVE other-target pending | re-initiate to another target | Conflict{transfer_pending}, no writes |
//! | DEAD other-target pending | re-initiate to another target | close it in the SAME tx (audited) → pending round (Created) |
//!
//! Authorization precedence: actor-kind and caller
//! identity gates run BEFORE any state handling, so
//! a denied caller never triggers a lazy closure
//! (no writes without authorization).
//!
//! Audits (spec §4.4): every real change writes one
//! `transfer_*` audit row; pure GETs and no-write
//! conflicts audit nothing; system closures use the
//! fixed System identity + fixed reason. The audit
//! row records the full round (from_owner + version
//! before/after the change) as JSON state strings;
//! `subject_id` is the target owner actor id.

use std::time::Duration;

use bcs_db_api::{
    DbStatement, DbTransactionOptions, DbTransactionParam, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityActorKind, AuthorityConflictCode,
    AuthorityMutation, TransferAction, TransferChange, TransferCommand,
    TransferKey, TransferOutcome, TransferStatus, TransferView,
    human_actor_id, human_user_id_of_actor,
};
use bcs_service_api::{ServiceError, ServiceResult};

use super::audit::{
    ACTION_TRANSFER_ACCEPT, ACTION_TRANSFER_CANCEL, ACTION_TRANSFER_REFRESH,
    ACTION_TRANSFER_REQUEST, ACTION_TRANSFER_REJECT, CLOSING_SYSTEM_ACTOR,
};
use super::transfers_support::*;
use super::{
    DbBotAuthorityStore, human_unknown_error, map_tx_error,
    operator_forbidden_error,
};
use bcs_service_api::port::repo::BotAuthorityRepoPort as _;

/// Whole-transaction deadline (same bound as the
/// manager paths; feasibility doc §5.1).
const TX_DEADLINE: Duration = Duration::from_secs(10);

/// Deterministic-rollback retries (guard churn between
/// pre-read and transaction). `DbError::Timeout` is
/// never retried.
const MAX_RETRIES: u32 = 3;

/// The transfer-row columns the write paths select in
/// pre-reads (spec §4.3).
const TRANSFER_PREREAD_COLUMNS: &str =
    "bot_id, target_owner_actor_id, from_owner_actor_id, \
     expected_ownership_version, status, requested_at, expires_at, \
     updated_at, decided_at, decided_by_kind, decided_by_id, reason, \
     result_ownership_version, bot_name_snapshot";

/// The audit state string shares its exact spelling
/// with the transfer contract harness so neither
/// store can drift.
pub(crate) fn transfer_audit_state(
    status: &str,
    from_owner_actor_id: &str,
    version: u64,
) -> String {
    format!(
        "{{\"status\":\"{status}\",\"from_owner\":\"{from_owner_actor_id}\",\"version\":{version}}}"
    )
}

impl DbBotAuthorityStore {
    /// Entry point dispatched from the port
    /// (`reads.rs`). Static authorization/identity
    /// gates run first; then the request or decision
    /// flow.
    pub(super) async fn change_transfer_impl(
        &self,
        command: TransferCommand,
    ) -> ServiceResult<TransferOutcome> {
        let TransferCommand {
            key,
            action,
            mutation,
        } = command;
        // The key target must be a Human actor shape.
        human_user_id_of_actor(&key.target_owner_actor_id)
            .ok_or_else(|| {
                human_unknown_error(format!(
                    "transfer target '{}' is not a Human actor id",
                    key.target_owner_actor_id
                ))
            })?;
        // Actor-kind / target-identity policy per action
        // (spec §6.1 dual admission). Provider/System
        // actors never drive transfer commands directly.
        let operator = match (&mutation.actor, action) {
            (
                AuthorityActor::Human { user_id },
                TransferAction::RequestOrRefresh,
            ) => Operator {
                kind: AuthorityActorKind::Human,
                actor_id: human_actor_id(user_id),
            },
            (
                AuthorityActor::Human { user_id },
                TransferAction::Accept,
            )
            | (
                AuthorityActor::Human { user_id },
                TransferAction::Reject,
            ) => {
                if human_actor_id(user_id) != key.target_owner_actor_id {
                    return Err(operator_forbidden_error(format!(
                        "transfer decision by '{}' does not match \
                         the target '{}'",
                        human_actor_id(user_id),
                        key.target_owner_actor_id
                    )));
                }
                Operator {
                    kind: AuthorityActorKind::Human,
                    actor_id: human_actor_id(user_id),
                }
            }
            (
                AuthorityActor::Service { .. },
                TransferAction::Accept,
            )
            | (
                AuthorityActor::Service { .. },
                TransferAction::Reject,
            ) => Operator {
                kind: AuthorityActorKind::Service,
                actor_id: mutation.actor.actor_id(),
            },
            (AuthorityActor::Human { user_id }, TransferAction::Cancel) => Operator {
                kind: AuthorityActorKind::Human,
                actor_id: human_actor_id(user_id),
            },
            _ => {
                return Err(operator_forbidden_error(format!(
                    "{:?} actors may not perform transfer {:?} on bot {}",
                    mutation.actor.kind(),
                    action,
                    key.bot_id
                )));
            }
        };
        // The target must be a valid surviving Human for
        // any transfer command (spec §6.2/§6.3 存续
        // Human; the SQL target-registry guard re-checks
        // inside transactions that write). This check runs
        // FIRST: when operator and target are the same
        // departed Human the pinned code stays input-level
        // `bot_authority_human_unknown`.
        self.require_existing_human(&key.target_owner_actor_id)
            .await?;
        // A HUMAN operator must be a live Human as well: the
        // residual edge of a tombstoned operator grants no
        // initiation/cancellation/decision (spec §3.1, the
        // same survival gate the read paths apply; the write
        // transactions re-verify it under the row lock).
        if operator.kind == AuthorityActorKind::Human {
            self.require_operator_human(&operator.actor_id).await?;
        }
        match action {
            TransferAction::RequestOrRefresh => {
                self.request_or_refresh_transfer(&key, &operator, &mutation)
                    .await
            }
            TransferAction::Accept
            | TransferAction::Reject
            | TransferAction::Cancel => {
                self.decide_transfer(&key, action, &operator, &mutation)
                    .await
            }
        }
    }

    /// COUNT of the actor's live Human rows (0 or 1) in the
    /// Human ACTOR registry (identical to the manager paths'
    /// Human guard): the `bcs_bots` row must be a live Human in
    /// the bound env (`actor_kind = 'human'`, not tombstoned).
    pub(super) async fn live_human_count(&self, actor_id: &str) -> ServiceResult<u64> {
        let user_id = human_user_id_of_actor(actor_id)
            .expect("human actor shape validated upstream");
        let rows = self
            .query(
                "authority_transfer_human_check",
                DbStatement::with_params(
                    format!(
                        "SELECT COUNT(*) AS c FROM bcs_bots \
                         WHERE {} \
                           AND actor_kind = 'human' \
                           AND env = ? \
                           AND COALESCE(is_deleted, 0) = 0",
                        self.bot_id_eq()
                    )
                    .as_str(),
                    vec![
                        DbValue::from(human_actor_id(&user_id).as_str()),
                        self.env_param(),
                    ],
                ),
            )
            .await?;
        rows.first()
            .map(|row| crate::required_u64(row, "c"))
            .transpose()
            .map(|count| count.unwrap_or(0))
    }

    /// The target's Human ACTOR registry check (spec §6.2/§6.3
    /// 存续 Human — an input-level error: the named target is
    /// unknown/gone).
    async fn require_existing_human(&self, actor_id: &str) -> ServiceResult<()> {
        if self.live_human_count(actor_id).await? != 1 {
            return Err(human_unknown_error(format!(
                "transfer target '{actor_id}' is not a valid Human"
            )));
        }
        Ok(())
    }

    /// The HUMAN operator's survival gate (spec §3.1): the
    /// operator's own actor row must be live as well — a
    /// tombstoned operator holding a residual ownership edge
    /// and a valid credential drives no transfer command
    /// (Forbidden — the caller's identity lapsed, the command
    /// input itself is well-formed).
    async fn require_operator_human(&self, actor_id: &str) -> ServiceResult<()> {
        if self.live_human_count(actor_id).await? != 1 {
            return Err(operator_forbidden_error(format!(
                "transfer operator '{actor_id}' is not a live Human actor"
            )));
        }
        Ok(())
    }

    // ============ shared pre-reads ============

    /// Pre-read the bot row state: single approved
    /// owner + version; strict semantics (corruption
    /// errors, deleted/uninitialized deny).
    async fn transfer_bot_state(
        &self,
        bot_id: &str,
    ) -> ServiceResult<(String, u64)> {
        match self.bot_ownership_state(bot_id).await? {
            Some((_, version)) if version > 0 => {
                let owners = self.approved_owner_rows(bot_id).await?;
                match owners.len() {
                    1 => {
                        Ok((owners.into_iter().next().expect("one row"), version))
                    }
                    0 => Err(super::owner_integrity_error(format!(
                        "bot {bot_id} has no approved owner edge"
                    ))),
                    count => Err(super::owner_integrity_error(format!(
                        "bot {bot_id} has {count} approved owner edges"
                    ))),
                }
            }
            Some(_) => Err(super::not_initialized_error(bot_id)),
            None => Err(ServiceError::BotNotFound(bot_id.to_string())),
        }
    }

    /// Pre-read the one target transfer row.
    async fn pre_read_row(
        &self,
        bot_id: &str,
        target: &str,
        now_ms: u64,
    ) -> ServiceResult<Option<StoredRow>> {
        let rows = self
            .query(
                "authority_transfer_row_preread",
                DbStatement::with_params(
                    format!(
                        "SELECT {TRANSFER_PREREAD_COLUMNS} FROM \
                         bot_ownership_transfers \
                         WHERE env = ? AND bot_id = ? AND {} LIMIT 1",
                        target_id_eq()
                    )
                    .as_str(),
                    vec![
                        self.env_param(),
                        DbValue::from(bot_id),
                        DbValue::from(target),
                    ],
                ),
            )
            .await?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        Ok(Some(parse_stored_row(&row, now_ms)?))
    }

    /// Pre-read ALL other-target pending rows, any
    /// freshness: the `transfer_pending` conflict
    /// source is a LIVE one (fresh at the captured
    /// now AND matching the current round); a DEAD
    /// one (past expiry, or owner/version no longer
    /// matching) is classified for the in-transaction
    /// closure (spec §6.2). At most one pending per
    /// Bot exists per `uk_transfer_pending`; the
    /// captured now decides freshness (Task-1
    /// single-time lens).
    async fn pre_read_other_pendings(
        &self,
        bot_id: &str,
        target: &str,
        now_ms: u64,
    ) -> ServiceResult<Vec<OtherPendingRow>> {
        let rows = self
            .query(
                "authority_transfer_other_pendings",
                DbStatement::with_params(
                    format!(
                        "SELECT {TRANSFER_PREREAD_COLUMNS} FROM \
                         bot_ownership_transfers \
                         WHERE env = ? AND bot_id = ? \
                           AND status = 'pending' AND {} \
                         ORDER BY target_owner_actor_id",
                        target_id_neq()
                    )
                    .as_str(),
                    vec![
                        self.env_param(),
                        DbValue::from(bot_id),
                        DbValue::from(target),
                    ],
                ),
            )
            .await?;
        let mut pendings = Vec::with_capacity(rows.len());
        for row in rows {
            let target = crate::required_string(
                &row,
                "target_owner_actor_id",
            )?;
            let stored = parse_stored_row(&row, now_ms)?;
            pendings.push(OtherPendingRow {
                row,
                target,
                stored,
            });
        }
        Ok(pendings)
    }

    /// Pre-read owner/manager-edge row states of one
    /// actor for the accept edge writes:
    /// (approved_count, revoked_count, other_count).
    pub(super) async fn pre_read_edge_state(
        &self,
        bot_id: &str,
        actor_id: &str,
        kind: &'static str,
    ) -> ServiceResult<(u64, u64, u64)> {
        let rows = self
            .query(
                "authority_transfer_edge_state",
                DbStatement::with_params(
                    format!(
                        "SELECT status, COUNT(*) AS c FROM edge_grants \
                         WHERE env = ? AND {} AND grant_kind = '{kind}' \
                           AND grant_ref_id = 0 AND rules IS NULL \
                           AND {} GROUP BY status",
                        self.to_id_eq(),
                        self.from_id_eq()
                    )
                    .as_str(),
                    vec![
                        self.env_param(),
                        DbValue::from(bot_id),
                        DbValue::from(actor_id),
                    ],
                ),
            )
            .await?;
        let mut approved = 0u64;
        let mut revoked = 0u64;
        let mut other = 0u64;
        for row in &rows {
            let count = crate::required_u64(row, "c")?;
            match crate::required_string(row, "status")?.as_str() {
                "approved" => approved = count,
                "revoked" => revoked = count,
                _ => other += count,
            }
        }
        Ok((approved, revoked, other))
    }

    // ============ transaction plumbing ============

    async fn run_tx(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> Result<Vec<DbTransactionStepResult>, bcs_db_api::DbError> {
        self.db
            .transaction_with_options(
                steps,
                DbTransactionOptions::with_deadline(TX_DEADLINE),
            )
            .await
    }

    /// Read-after-write snapshot INSIDE the write transaction,
    /// appended as the LAST step so it surfaces as the last
    /// result: the response to an Applied command must carry
    /// the state THIS command committed. A record keyed by
    /// (bot, target) is reused by later rounds, so a
    /// post-commit re-read could pick up the NEXT round's
    /// pending state that landed between commit and response
    /// assembly (review finding P2-3).
    fn transfer_snapshot_step(
        &self,
        bot_id: &str,
        target: &str,
    ) -> DbTransactionStep {
        DbTransactionStep::Query(DbStatement::with_transaction_params(
            format!(
                "SELECT {columns} FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND target_owner_actor_id = ? LIMIT 1",
                columns = super::reads::TRANSFER_COLUMNS,
            )
            .as_str(),
            vec![
                DbTransactionParam::value(self.env_param()),
                DbTransactionParam::value(DbValue::from(bot_id)),
                DbTransactionParam::value(DbValue::from(target)),
            ],
        ))
    }

    /// Project the in-transaction snapshot (the last step
    /// result) with the SAME effective-status lens as the
    /// strict read — one DB-time captured for the response
    /// projection (Task-1 lens shape).
    async fn transfer_snapshot_view(
        &self,
        results: &[DbTransactionStepResult],
    ) -> ServiceResult<TransferView> {
        let rows = match results.last() {
            Some(DbTransactionStepResult::Rows(rows)) => rows,
            other => {
                return Err(ServiceError::InternalError(format!(
                    "transfer snapshot step returned {other:?}"
                )));
            }
        };
        let row = rows.first().ok_or_else(|| {
            ServiceError::InternalError(
                "transfer row vanished within its own write transaction"
                    .to_string(),
            )
        })?;
        let now = self.db_now_ms().await?;
        self.transfer_view_from_row(row, now).await
    }

    /// Fresh post-commit view (the Task-1 lens).
    async fn trade_view(
        &self,
        key: &TransferKey,
    ) -> ServiceResult<TransferView> {
        self.transfer(key).await?.ok_or_else(|| {
            ServiceError::InternalError(
                "transfer row vanished after commit".to_string(),
            )
        })
    }

    fn now_plus_7d(&self) -> &'static str {
        expires_now_plus_seven_days(self)
    }

    /// In-transaction operator-survival guard (HUMAN
    /// operators only — a trusted-Service decision carries no
    /// Human row): the operator's live Human actor row must
    /// still exist under the row lock (spec §3.1, mirroring
    /// the manager-edit guard). Its failure is retryable; the
    /// pre-transaction gate then maps the deletion to
    /// Forbidden deterministically.
    fn push_operator_alive_guard(
        &self,
        builder: &mut TransferTxBuilder,
        operator: &Operator,
    ) {
        if operator.kind != AuthorityActorKind::Human {
            return;
        }
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bcs_bots \
                 WHERE {} \
                   AND actor_kind = 'human' \
                   AND env = ? \
                   AND COALESCE(is_deleted, 0) = 0{}",
                self.bot_id_eq(),
                lock_suffix(self)
            ),
            vec![
                DbTransactionParam::value(DbValue::from(
                    operator.actor_id.as_str(),
                )),
                DbTransactionParam::value(self.env_param()),
            ],
            1,
        );
    }

    /// One transfer audit write of this mutation
    /// (before/after NULL preserved exactly).
    pub(super) fn push_transfer_audit(
        &self,
        builder: &mut TransferTxBuilder,
        bot_id: &str,
        mutation_id: &str,
        draft: TransferAuditDraft,
    ) {
        let before_value = match draft.before_state {
            Some(state) => DbValue::from(state),
            None => DbValue::Null,
        };
        let after_value = match draft.after_state {
            Some(state) => DbValue::from(state),
            None => DbValue::Null,
        };
        builder.add_checked_write(
            "INSERT INTO bot_authority_changes \
             (env, bot_id, mutation_id, sequence, action, actor_kind, \
              actor_id, subject_id, before_state, after_state) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                .to_string(),
            vec![
                DbTransactionParam::value(self.env_param()),
                DbTransactionParam::value(DbValue::from(bot_id)),
                DbTransactionParam::value(DbValue::from(mutation_id)),
                DbTransactionParam::value(DbValue::from(draft.sequence)),
                DbTransactionParam::value(DbValue::from(draft.action)),
                DbTransactionParam::value(DbValue::from(
                    draft.actor_kind.as_str(),
                )),
                DbTransactionParam::value(DbValue::from(
                    draft.actor_id.as_str(),
                )),
                DbTransactionParam::value(DbValue::from(
                    draft.subject.as_str(),
                )),
                DbTransactionParam::value(before_value),
                DbTransactionParam::value(after_value),
            ],
        );
    }

    // ============ initiation (§6.2) ============

    async fn request_or_refresh_transfer(
        &self,
        key: &TransferKey,
        operator: &Operator,
        mutation: &AuthorityMutation,
    ) -> ServiceResult<TransferOutcome> {
        let op = "authority_transfer_request";
        let bot_id = key.bot_id.clone();
        let target = key.target_owner_actor_id.clone();
        // Per-attempt diagnostics so the budget-exhausted
        // error names WHY each try conflicted (no secrets /
        // caller data; the underlying DbError only).
        let mut conflicts: Vec<String> = Vec::new();
        for attempt in 0..=MAX_RETRIES {
            let (owner, version) =
                self.transfer_bot_state(&bot_id).await?;
            if !operator.is_human() {
                return Err(operator_forbidden_error(
                    "transfer initiation is a Human (current-owner) command"
                        .to_string(),
                ));
            }
            if operator.actor_id != owner {
                return Err(operator_forbidden_error(format!(
                    "transfer initiation requires the current owner; \
                     '{}' is not '{}'",
                    operator.actor_id, owner
                )));
            }
            if target == owner {
                return Err(transfer_state_denial(format!(
                    "transfer target '{target}' is the current owner of \
                     bot '{bot_id}'"
                )));
            }
            let now = self.db_now_ms().await?;
            let row = self.pre_read_row(&bot_id, &target, now).await?;
            match classify_request(owner.as_str(), version, row.as_ref()) {
                RequestExecution::Refresh => {
                    let mut tx = self.build_refresh_tx(
                        &bot_id,
                        &target,
                        operator,
                        &owner,
                        version,
                        mutation,
                    );
                    // In-transaction snapshot LAST: the response
                    // carries THIS refresh's renewed state, not
                    // a record a racing round already reused.
                    tx.push(self.transfer_snapshot_step(&bot_id, &target));
                    match self.run_tx(tx).await {
                        Ok(results) => {
                            return Ok(TransferOutcome::Applied {
                                transfer: self
                                    .transfer_snapshot_view(&results)
                                    .await?,
                                change: TransferChange::Updated,
                            });
                        }
                        Err(err) if is_retryable(&err) => {
                            conflicts.push(format!(
                                "attempt {attempt} refresh_tx: {err}"
                            ));
                            continue;
                        }
                        Err(err) => return Err(map_tx_error(op, err)),
                    }
                }
                RequestExecution::Create { over_row, closure } => {
                    // Every other-target pending classifies
                    // (spec §6.2): a LIVE one (fresh AND
                    // matching the current round) blocks the
                    // initiation, verified under a guard
                    // transaction so a stale classification
                    // never answers for a fresher state. A
                    // DEAD one (expired by DB time, or
                    // owner/version mismatched) is closed in
                    // the SAME transaction the new-target row
                    // is created in — the per-bot pending
                    // unique key (`uk_transfer_pending`) is
                    // held by the dead row's still-`pending`
                    // status, so only the in-tx closure frees
                    // the slot. (Without this, re-initiating
                    // to a DIFFERENT target after an expiring
                    // pending kept hitting the unique key and
                    // exhausted the retry budget.)
                    let mut live = None;
                    let mut other_closures: Vec<OtherPendingClosure> =
                        Vec::new();
                    for other in self
                        .pre_read_other_pendings(
                            &bot_id,
                            &target,
                            now,
                        )
                        .await?
                    {
                        match other.stored.effective_status {
                            TransferStatus::Pending
                                if other.stored.from_owner
                                    == owner
                                    && other.stored
                                        .expected_version
                                        == version =>
                            {
                                if live.is_none() {
                                    live = Some(other);
                                }
                            }
                            TransferStatus::Pending => {
                                // Fresh by time, but the round
                                // no longer matches: the same
                                // ABA rule the same-target
                                // classification applies.
                                other_closures.push(
                                    OtherPendingClosure {
                                        target: other.target,
                                        kind:
                                            ClosureKind::Invalidate,
                                        from_owner: other
                                            .stored
                                            .from_owner,
                                        expected_version: other
                                            .stored
                                            .expected_version,
                                    },
                                );
                            }
                            TransferStatus::Expired => {
                                // Stored pending past the
                                // captured now (the same kind
                                // precedence the same-target
                                // closure uses).
                                other_closures.push(
                                    OtherPendingClosure {
                                        target: other.target,
                                        kind: ClosureKind::Expire,
                                        from_owner: other
                                            .stored
                                            .from_owner,
                                        expected_version: other
                                            .stored
                                            .expected_version,
                                    },
                                );
                            }
                            // The pre-read only returns
                            // stored-pending rows.
                            _ => {
                                return Err(
                                    ServiceError::InternalError(
                                        "other-target pending \
                                         pre-read lost its status \
                                         (classification bug)"
                                            .to_string(),
                                    ),
                                );
                            }
                        }
                    }
                    if let Some(other) = live {
                        match self
                            .run_other_pending_guard_tx(
                                &bot_id,
                                &target,
                                operator,
                                &owner,
                            )
                            .await
                        {
                            Ok(()) => {
                                let view = self
                                    .transfer_view_from_row(
                                        &other.row, now,
                                    )
                                    .await?;
                                return Ok(TransferOutcome::Conflict {
                                    transfer: view,
                                    code: AuthorityConflictCode::TransferPending,
                                });
                            }
                            Err(err) if is_retryable(&err) => {
                                conflicts.push(format!(
                                    "attempt {attempt} pending_guard_tx: {err}"
                                ));
                                continue;
                            }
                            Err(err) => return Err(map_tx_error(op, err)),
                        }
                    }
                    let mut tx = self.build_create_tx(
                        &bot_id,
                        &target,
                        operator,
                        &owner,
                        version,
                        over_row,
                        closure,
                        row.as_ref(),
                        &other_closures,
                        mutation,
                    )?;
                    // In-transaction snapshot LAST: the response
                    // carries THIS initiation's pending row.
                    tx.push(self.transfer_snapshot_step(&bot_id, &target));
                    match self.run_tx(tx).await {
                        Ok(results) => {
                            return Ok(TransferOutcome::Applied {
                                transfer: self
                                    .transfer_snapshot_view(&results)
                                    .await?,
                                change: TransferChange::Created,
                            });
                        }
                        Err(err) if is_retryable(&err) => {
                            conflicts.push(format!(
                                "attempt {attempt} create_tx: {err}"
                            ));
                            continue;
                        }
                        Err(err) => return Err(map_tx_error(op, err)),
                    }
                }
            }
        }
        Err(super::authority_db_error(
            op,
            bcs_db_api::DbError::Backend(format!(
                "transfer request kept conflicting; retry budget \
                 exhausted; conflicts: [{conflicts:?}]"
            )),
        ))
    }

    /// Guards verifying (a) the operator is still the
    /// in-transaction owner and (b) the other-target
    /// LIVE pending (fresh AND matching the current
    /// round) still exists, then commits a no-write
    /// transaction.
    async fn run_other_pending_guard_tx(
        &self,
        bot_id: &str,
        target: &str,
        operator: &Operator,
        owner: &str,
    ) -> Result<(), bcs_db_api::DbError> {
        let mut builder = TransferTxBuilder::new(self, bot_id, true);
        builder.push_eq_guard(vec![
            DbTransactionParam::value(DbValue::from(owner)),
            builder.owner_ref(),
            DbTransactionParam::value(DbValue::from(
                operator.actor_id.as_str(),
            )),
            DbTransactionParam::value(DbValue::from(owner)),
        ]);
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND status = 'pending' \
                   AND expires_at > {} AND {target_neq} \
                   AND from_owner_actor_id = ? \
                   AND expected_ownership_version = ?",
                self.db_now_utc(),
                target_neq = target_id_neq(),
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                DbTransactionParam::value(DbValue::from(target)),
                builder.owner_ref(),
                builder.version_ref(),
            ],
            1,
        );
        self.run_tx(builder.finish()).await.map(|_| ())
    }

    /// Refresh of a valid same-snapshot pending
    /// (spec §6.2: 复用该行，刷新 requested_at 和七天
    /// 期限，不创建第二条).
    fn build_refresh_tx(
        &self,
        bot_id: &str,
        target: &str,
        operator: &Operator,
        owner: &str,
        version: u64,
        mutation: &AuthorityMutation,
    ) -> Vec<DbTransactionStep> {
        let lock = lock_suffix(self);
        let mut builder = TransferTxBuilder::new(self, bot_id, true);
        builder.push_eq_guard(vec![
            DbTransactionParam::value(DbValue::from(owner)),
            builder.owner_ref(),
            DbTransactionParam::value(DbValue::from(
                operator.actor_id.as_str(),
            )),
            DbTransactionParam::value(DbValue::from(owner)),
        ]);
        builder.push_eq_guard(vec![
            DbTransactionParam::value(DbValue::from(version)),
            builder.version_ref(),
        ]);
        self.push_operator_alive_guard(&mut builder, operator);
        // The pending row must still exist, still be
        // pending, fresh, and still carry THIS round's
        // owner/version (ref-bound to the in-tx values).
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND {target} \
                   AND status = 'pending' \
                   AND from_owner_actor_id = ? \
                   AND expected_ownership_version = ? \
                   AND expires_at > {now}{lock}",
                target = target_id_eq(),
                now = self.db_now_utc(),
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                DbTransactionParam::value(DbValue::from(target)),
                builder.owner_ref(),
                builder.version_ref(),
            ],
            1,
        );
        // The refresh is idempotent BY DESIGN: a re-request
        // within the same wall-clock second rewrites the
        // identical values, and MySQL (the connection does
        // not set CLIENT_FOUND_ROWS — see
        // bcs-session-store repo_collect precedent) then
        // reports CHANGED rows 0, which would turn a row
        // checked write into a permanent ConditionFailed
        // retry loop. The count guard above already locked
        // and verified exactly this one pending row, so the
        // renewal is an UNconditional write, not a
        // row-count-checked one (the decision writes below
        // are real status transitions and stay checked).
        builder.steps.push(DbTransactionStep::Execute(
            DbStatement::with_transaction_params(
                format!(
                    "UPDATE bot_ownership_transfers \
                     SET requested_at = {now}, expires_at = {plus7}, \
                         updated_at = {now}, gmt_modified = {modified} \
                     WHERE env = ? AND bot_id = ? AND {target} \
                       AND status = 'pending'",
                    now = self.db_now_utc(),
                    plus7 = self.now_plus_7d(),
                    modified = gmt_modified_now(self),
                    target = target_id_eq(),
                )
                .as_str(),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
            ),
        ));
        self.push_transfer_audit(
            &mut builder,
            bot_id,
            &mutation.mutation_id,
            TransferAuditDraft {
                sequence: 0,
                action: ACTION_TRANSFER_REFRESH,
                actor_kind: operator.kind.as_str().to_string(),
                actor_id: operator.actor_id.clone(),
                subject: target.to_string(),
                before_state: None,
                after_state: Some(transfer_audit_state(
                    "pending", owner, version,
                )),
            },
        );
        builder.finish()
    }

    /// Typed per-row guard for the create path's
    /// non-closure branches: the classified pre-read
    /// state, key-exact. Fresh insert: the row must be
    /// absent. Terminal re-use: the row still carries
    /// its exact stored terminal status. (Closure
    /// re-initiations guard through
    /// [`Self::push_pending_closure`] instead — the
    /// row must still BE the classified DEAD pending.)
    /// A concurrent recreation fails the guard
    /// (deterministic local rollback → bounded retry
    /// recompute; `Timeout` never retried), so a pending
    /// that a racing command already renewed can never
    /// be closed as stale and audited as such.
    #[allow(clippy::too_many_arguments)]
    fn create_row_state_guard(
        &self,
        builder: &TransferTxBuilder,
        target: &str,
        over_row: bool,
        stored_status: Option<&str>,
        lock: &'static str,
    ) -> ServiceResult<(String, Vec<DbTransactionParam>)> {
        let base = |status_clause: String| {
            (
                format!(
                    "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                     WHERE env = ? AND bot_id = ? AND {target} \
                       AND {status_clause}{lock}",
                    target = target_id_eq(),
                    lock = lock,
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
            )
        };
        if !over_row {
            // Fresh insert: the row must be absent.
            return Ok(base(
                "status IN ('pending', 'accepted', \
                              'rejected', 'cancelled', 'expired', \
                              'invalidated')"
                    .to_string(),
            ));
        }
        Ok(base(format!(
            "status = '{}'",
            stored_status.ok_or_else(|| {
                ServiceError::InternalError(
                    "terminal re-use plan lost the row \
                     snapshot (classification bug)"
                        .to_string(),
                )
            })?
        )))
    }

    /// One lazy pending closure inside the create
    /// transaction (spec §6.2 过期或 owner/version 已
    /// 不匹配的 pending：先在本事务中关闭，再执行本次
    /// 发起): the typed dead-row guard (the row must
    /// still BE the classified dead pending — a
    /// concurrently renewed live pending fails the
    /// guard and the caller recomputes; a live pending
    /// is never closed as stale and audited as such),
    /// the closure write (status → terminal, fixed
    /// System decision) and the closure audit (spec
    /// §4.4: system actor + closure reason). Shared by
    /// the same-target closure and the other-target
    /// closures (`other_closures`), so both carry the
    /// exact same predicates and audit shapes.
    #[allow(clippy::too_many_arguments)]
    fn push_pending_closure(
        &self,
        builder: &mut TransferTxBuilder,
        bot_id: &str,
        target: &str,
        kind: ClosureKind,
        from_owner: &str,
        expected_version: u64,
        sequence: u64,
        mutation_id: &str,
    ) {
        let lock = lock_suffix(self);
        let dead_predicate = match kind {
            ClosureKind::Expire => format!(
                "expires_at <= {}",
                self.db_now_utc()
            ),
            ClosureKind::Invalidate =>
                "(from_owner_actor_id <> ? OR                      expected_ownership_version <> ?)".to_string(),
        };
        let mut guard_params = vec![
            DbTransactionParam::value(self.env_param()),
            builder.bot_ref(),
            DbTransactionParam::value(DbValue::from(target)),
        ];
        if kind == ClosureKind::Invalidate {
            guard_params.push(builder.owner_ref());
            guard_params.push(builder.version_ref());
        }
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND {target_eq} \
                   AND status = 'pending' \
                   AND {dead_predicate}{lock}",
                target_eq = target_id_eq(),
                lock = lock,
            ),
            guard_params,
            1,
        );
        // Lazy closure first; the audit keeps the
        // closure reason (spec §4.4).
        builder.add_checked_write(
            format!(
                "UPDATE bot_ownership_transfers \
                 SET status = '{closed}', decided_at = {now}, \
                     decided_by_kind = 'system', \
                     decided_by_id = '{system_actor}', \
                     reason = '{reason}', updated_at = {now}, \
                     gmt_modified = {modified} \
                 WHERE env = ? AND bot_id = ? AND {target_eq} \
                   AND status = 'pending'",
                closed = kind.status(),
                now = self.db_now_utc(),
                system_actor = CLOSING_SYSTEM_ACTOR,
                reason = kind.reason(),
                modified = gmt_modified_now(self),
                target_eq = target_id_eq(),
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                DbTransactionParam::value(DbValue::from(target)),
            ],
        );
        self.push_transfer_audit(
            builder,
            bot_id,
            mutation_id,
            TransferAuditDraft {
                sequence,
                action: kind.audit_action(),
                actor_kind: AuthorityActorKind::System.as_str().to_string(),
                actor_id: CLOSING_SYSTEM_ACTOR.to_string(),
                subject: target.to_string(),
                before_state: Some(transfer_audit_state(
                    "pending",
                    from_owner,
                    expected_version,
                )),
                after_state: Some(transfer_audit_state(
                    kind.status(),
                    from_owner,
                    expected_version,
                )),
            },
        );
    }

    /// Creation (fresh insert or terminal-row re-use)
    /// including the typed per-row state guard (see
    /// [`Self::create_row_state_guard`] — the review
    /// fix: a row concurrently recreated between the
    /// pre-read and the transaction fails the guard and
    /// the caller recomputes from a fresh pre-read, so
    /// a live pending is never closed as stale and
    /// audited as such), the in-transaction closures —
    /// the dead same-target pending and any DEAD
    /// other-target pending (`other_closures`; spec
    /// §6.2, freeing the per-bot pending slot before
    /// the new-row insert) — and the post-closure
    /// "no other live pending" re-verification.
    #[allow(clippy::too_many_arguments)]
    fn build_create_tx(
        &self,
        bot_id: &str,
        target: &str,
        operator: &Operator,
        owner: &str,
        version: u64,
        over_row: bool,
        closure: Option<ClosureKind>,
        row: Option<&StoredRow>,
        other_closures: &[OtherPendingClosure],
        mutation: &AuthorityMutation,
    ) -> ServiceResult<Vec<DbTransactionStep>> {
        let lock = lock_suffix(self);
        let mut builder = TransferTxBuilder::new(self, bot_id, true);
        builder.push_eq_guard(vec![
            DbTransactionParam::value(DbValue::from(owner)),
            builder.owner_ref(),
            DbTransactionParam::value(DbValue::from(
                operator.actor_id.as_str(),
            )),
            DbTransactionParam::value(DbValue::from(owner)),
        ]);
        builder.push_eq_guard(vec![
            DbTransactionParam::value(DbValue::from(version)),
            builder.version_ref(),
        ]);
        self.push_operator_alive_guard(&mut builder, operator);
        // Target must be a valid surviving Human: its ACTOR
        // row is live in `bcs_bots` under the row lock (the
        // Bot row is locked first, spec §7 order).
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bcs_bots \
                 WHERE {} \
                   AND actor_kind = 'human' \
                   AND env = ? \
                   AND COALESCE(is_deleted, 0) = 0{}",
                self.bot_id_eq(),
                lock_suffix(self),
            ),
            vec![
                DbTransactionParam::value(DbValue::from(
                    human_actor_id(
                        &human_user_id_of_actor(target)
                            .expect("target shape validated"),
                    )
                    .as_str(),
                )),
                DbTransactionParam::value(self.env_param()),
            ],
            1,
        );
        // The target row carries EXACTLY the classified
        // pre-read state before any reset/closure write
        // (typed per-row status predicate): a row
        // concurrently recreated between pre-read and
        // transaction fails this guard and the caller
        // recomputes from a fresh pre-read — a live
        // pending is never closed as stale (review fix:
        // no false system closure in the audit trail).
        // Closure branches guard through
        // `push_pending_closure` instead.
        if closure.is_none() {
            let (guard_sql, guard_params) = self
                .create_row_state_guard(
                    &builder,
                    target,
                    over_row,
                    row.map(|row| row.status.as_str()),
                    lock,
                )?;
            let expected_rows = if over_row { 1 } else { 0 };
            builder.push_count_guard(
                guard_sql,
                guard_params,
                expected_rows,
            );
        }
        let mut sequence = 0u64;
        if let Some(kind) = closure {
            let row = row.expect("closure plan needs the row snapshot");
            self.push_pending_closure(
                &mut builder,
                bot_id,
                target,
                kind,
                &row.from_owner,
                row.expected_version,
                sequence,
                &mutation.mutation_id,
            );
            sequence += 1;
        }
        // The DEAD other-target pendings close in the
        // same transaction, deterministically by target
        // (the pre-read ordered them; spec §6.2).
        for other in other_closures {
            self.push_pending_closure(
                &mut builder,
                bot_id,
                &other.target,
                other.kind,
                &other.from_owner,
                other.expected_version,
                sequence,
                &mutation.mutation_id,
            );
            sequence += 1;
        }
        // No other-target LIVE pending exists (spec
        // §6.2; `uk_transfer_pending` re-verified AFTER
        // the in-transaction closures, so a dead pending
        // this transaction just closed no longer counts
        // — that guard order is the fix for the
        // different-target re-initiation after an
        // expiry).
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND status = 'pending' \
                   AND expires_at > {} AND {}",
                self.db_now_utc(),
                target_id_neq()
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                DbTransactionParam::value(DbValue::from(target)),
            ],
            0,
        );
        if over_row {
            builder.add_checked_write(
                format!(
                    "UPDATE bot_ownership_transfers \
                     SET from_owner_actor_id = ?, \
                         expected_ownership_version = ?, \
                         status = 'pending', requested_at = {now}, \
                         expires_at = {plus7}, updated_at = {now}, \
                         decided_at = NULL, decided_by_kind = NULL, \
                         decided_by_id = NULL, reason = NULL, \
                         result_ownership_version = NULL, \
                         bot_name_snapshot = ?, gmt_modified = {modified} \
                     WHERE env = ? AND bot_id = ? AND {target}",
                    now = self.db_now_utc(),
                    plus7 = self.now_plus_7d(),
                    modified = gmt_modified_now(self),
                    target = target_id_eq(),
                ),
                vec![
                    builder.owner_ref(),
                    builder.version_ref(),
                    builder.name_ref(),
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
            );
        } else {
            builder.add_checked_write(
                format!(
                    "INSERT INTO bot_ownership_transfers \
                     (env, bot_id, target_owner_actor_id, \
                      from_owner_actor_id, expected_ownership_version, \
                      status, requested_at, expires_at, updated_at, \
                      bot_name_snapshot) \
                     VALUES (?, ?, ?, ?, ?, 'pending', {now}, {plus7}, \
                             {now}, ?)",
                    now = self.db_now_utc(),
                    plus7 = self.now_plus_7d(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                    builder.owner_ref(),
                    builder.version_ref(),
                    builder.name_ref(),
                ],
            );
        }
        self.push_transfer_audit(
            &mut builder,
            bot_id,
            &mutation.mutation_id,
            TransferAuditDraft {
                sequence,
                action: ACTION_TRANSFER_REQUEST,
                actor_kind: operator.kind.as_str().to_string(),
                actor_id: operator.actor_id.clone(),
                subject: target.to_string(),
                before_state: None,
                after_state: Some(transfer_audit_state(
                    "pending", owner, version,
                )),
            },
        );
        Ok(builder.finish())
    }

    // ============ decisions (§6.3/§6.4) ============

    async fn decide_transfer(
        &self,
        key: &TransferKey,
        action: TransferAction,
        operator: &Operator,
        mutation: &AuthorityMutation,
    ) -> ServiceResult<TransferOutcome> {
        let op = "authority_transfer_decide";
        let bot_id = key.bot_id.clone();
        let target = key.target_owner_actor_id.clone();
        // Per-attempt diagnostics so the budget-exhausted
        // error names WHY each try conflicted (no secrets /
        // caller data; the underlying DbError only).
        let mut conflicts: Vec<String> = Vec::new();
        for attempt in 0..=MAX_RETRIES {
            let (owner, version) =
                self.transfer_bot_state(&bot_id).await?;
            let now = self.db_now_ms().await?;
            let row = self.pre_read_row(&bot_id, &target, now).await?;
            let plan = classify_decision(
                action,
                operator,
                owner.as_str(),
                version,
                row.as_ref(),
            );
            match plan {
                DecisionPlan::Forbidden => {
                    return Err(operator_forbidden_error(format!(
                        "transfer {:?} by '{}' is not the row initiator",
                        action, operator.actor_id
                    )));
                }
                DecisionPlan::Miss => {
                    match self.run_absence_guard_tx(&bot_id, &target).await {
                        Ok(()) => {
                            return Err(transfer_state_denial(format!(
                                "no transfer row exists for bot \
                                 '{bot_id}' and target '{target}'"
                            )));
                        }
                        Err(err) if is_retryable(&err) => {
                            conflicts.push(format!(
                                "attempt {attempt} miss_guard_tx: {err}"
                            ));
                            continue;
                        }
                        Err(err) => return Err(map_tx_error(op, err)),
                    }
                }
                DecisionPlan::RepeatSame => {
                    let stored = row
                        .as_ref()
                        .map(|row| row.status.clone())
                        .expect("repeat plan needs the row");
                    match self
                        .run_stored_status_guard_tx(
                            &bot_id,
                            &target,
                            &stored,
                        )
                        .await
                    {
                        Ok(results) => {
                            return Ok(TransferOutcome::Applied {
                                transfer: self
                                    .transfer_snapshot_view(&results)
                                    .await?,
                                change: TransferChange::Unchanged,
                            });
                        }
                        Err(err) if is_retryable(&err) => {
                            conflicts.push(format!(
                                "attempt {attempt} repeat_guard_tx: {err}"
                            ));
                            continue;
                        }
                        Err(err) => return Err(map_tx_error(op, err)),
                    }
                }
                DecisionPlan::ConflictOnTerminal(code) => {
                    let view = self.trade_view(key).await?;
                    let stored = row
                        .as_ref()
                        .map(|row| row.status.clone())
                        .expect("terminal conflict needs the row");
                    match self
                        .run_stored_status_guard_tx(
                            &bot_id,
                            &target,
                            &stored,
                        )
                        .await
                    {
                        Ok(_) => {
                            return Ok(TransferOutcome::Conflict {
                                transfer: view,
                                code,
                            });
                        }
                        Err(err) if is_retryable(&err) => {
                            conflicts.push(format!(
                                "attempt {attempt} terminal_guard_tx: {err}"
                            ));
                            continue;
                        }
                        Err(err) => return Err(map_tx_error(op, err)),
                    }
                }
                DecisionPlan::CloseAndConflict(kind) => {
                    let row = row.as_ref().expect("closure plan");
                    match self
                        .run_close_tx(
                            &bot_id,
                            &target,
                            kind,
                            &row.from_owner,
                            row.expected_version,
                            mutation,
                        )
                        .await
                    {
                        Ok(()) => {
                            return Ok(TransferOutcome::Conflict {
                                transfer: self.trade_view(key).await?,
                                code: kind.code(),
                            });
                        }
                        Err(err) if is_retryable(&err) => {
                            conflicts.push(format!(
                                "attempt {attempt} close_tx: {err}"
                            ));
                            continue;
                        }
                        Err(err) => return Err(map_tx_error(op, err)),
                    }
                }
                DecisionPlan::Apply => {
                    let row = row.as_ref().expect("apply plan");
                    let mut tx = self
                        .build_decision_tx(
                            &bot_id,
                            &target,
                            action,
                            operator,
                            mutation,
                            &owner,
                            version,
                            &row.from_owner,
                            row.expected_version,
                        )
                        .await?;
                    // In-transaction snapshot LAST: an Approved
                    // decision's response carries THIS round's
                    // terminal state, even when a same-target
                    // next round commits before the response is
                    // assembled (review finding P2-3).
                    tx.push(self.transfer_snapshot_step(&bot_id, &target));
                    match self.run_tx(tx).await {
                        Ok(results) => {
                            return Ok(TransferOutcome::Applied {
                                transfer: self
                                    .transfer_snapshot_view(&results)
                                    .await?,
                                change: TransferChange::Updated,
                            });
                        }
                        Err(err) if is_retryable(&err) => {
                            conflicts.push(format!(
                                "attempt {attempt} decision_tx: {err}"
                            ));
                            continue;
                        }
                        Err(err) => return Err(map_tx_error(op, err)),
                    }
                }
            }
        }
        Err(super::authority_db_error(
            op,
            bcs_db_api::DbError::Backend(format!(
                "transfer decision kept conflicting; retry budget \
                 exhausted; conflicts: [{conflicts:?}]"
            )),
        ))
    }

    /// Absence guard transaction: re-verification only
    /// (a freshly created row must not be answered by a
    /// stale "no row" classification).
    async fn run_absence_guard_tx(
        &self,
        bot_id: &str,
        target: &str,
    ) -> Result<(), bcs_db_api::DbError> {
        let mut builder = TransferTxBuilder::new(self, bot_id, false);
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND {target}",
                target = target_id_eq()
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                DbTransactionParam::value(DbValue::from(target)),
            ],
            0,
        );
        self.run_tx(builder.finish()).await.map(|_| ())
    }

    /// Stored-terminal guard transaction: the row must
    /// still carry the classified stored status.
    async fn run_stored_status_guard_tx(
        &self,
        bot_id: &str,
        target: &str,
        stored_status: &str,
    ) -> Result<Vec<DbTransactionStepResult>, bcs_db_api::DbError> {
        let mut builder = TransferTxBuilder::new(self, bot_id, false);
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND {target} \
                   AND status = '{stored_status}'",
                target = target_id_eq()
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                DbTransactionParam::value(DbValue::from(target)),
            ],
            1,
        );
        // In-transaction snapshot as the LAST step (after the
        // guard-binding step): a terminal-repeat response must
        // carry the state the guard just re-verified, not a
        // record a racing next round already reclaimed.
        let mut steps = builder.finish();
        steps.push(self.transfer_snapshot_step(bot_id, target));
        self.run_tx(steps).await
    }

    /// Lazy-closure transaction: audit + state commit
    /// BEFORE the conflict is returned (spec §6.4).
    async fn run_close_tx(
        &self,
        bot_id: &str,
        target: &str,
        kind: ClosureKind,
        from_owner: &str,
        expected_version: u64,
        mutation: &AuthorityMutation,
    ) -> Result<(), bcs_db_api::DbError> {
        let mut builder = TransferTxBuilder::new(self, bot_id, true);
        // Same typed discipline as the create path: the
        // pending must still be the classified DEAD one
        // (a concurrently renewed pending fails the guard
        // and the caller recomputes — a live pending is
        // never closed as stale).
        let closure_guard_sql = match kind {
            ClosureKind::Expire => format!(
                "status = 'pending' AND expires_at <= {}",
                self.db_now_utc()
            ),
            ClosureKind::Invalidate =>
                "status = 'pending' AND                  (from_owner_actor_id <> ? OR                   expected_ownership_version <> ?)".to_string(),
        };
        let mut closure_guard_params = vec![
            DbTransactionParam::value(self.env_param()),
            builder.bot_ref(),
            DbTransactionParam::value(DbValue::from(target)),
        ];
        if kind == ClosureKind::Invalidate {
            closure_guard_params.push(builder.owner_ref());
            closure_guard_params.push(builder.version_ref());
        }
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND {target} \
                   AND {closure_guard_sql}{lock}",
                target = target_id_eq(),
                lock = lock_suffix(self),
            ),
            closure_guard_params,
            1,
        );
        builder.add_checked_write(
            format!(
                "UPDATE bot_ownership_transfers \
                 SET status = '{closed}', decided_at = {now}, \
                     decided_by_kind = 'system', \
                     decided_by_id = '{system_actor}', \
                     reason = '{reason}', updated_at = {now}, \
                     gmt_modified = {modified} \
                 WHERE env = ? AND bot_id = ? AND {target} \
                   AND status = 'pending'",
                closed = kind.status(),
                now = self.db_now_utc(),
                system_actor = CLOSING_SYSTEM_ACTOR,
                reason = kind.reason(),
                modified = gmt_modified_now(self),
                target = target_id_eq(),
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                DbTransactionParam::value(DbValue::from(target)),
            ],
        );
        self.push_transfer_audit(
            &mut builder,
            bot_id,
            &mutation.mutation_id,
            TransferAuditDraft {
                sequence: 0,
                action: kind.audit_action(),
                actor_kind: AuthorityActorKind::System.as_str().to_string(),
                actor_id: CLOSING_SYSTEM_ACTOR.to_string(),
                subject: target.to_string(),
                before_state: Some(transfer_audit_state(
                    "pending",
                    from_owner,
                    expected_version,
                )),
                after_state: Some(transfer_audit_state(
                    kind.status(),
                    from_owner,
                    expected_version,
                )),
            },
        );
        self.run_tx(builder.finish()).await.map(|_| ())
    }

    /// The decision transaction: accept applies the
    /// spec §6.3 sequence (old owner edge revoked,
    /// target owner edge created/restored, version
    /// incremented, new owner's manager edge removed,
    /// old owner added as a normal manager, row
    /// accepted, audit); reject/cancel only record the
    /// terminal state. Other managers are untouched.
    #[allow(clippy::too_many_arguments)]
    async fn build_decision_tx(
        &self,
        bot_id: &str,
        target: &str,
        action: TransferAction,
        operator: &Operator,
        mutation: &AuthorityMutation,
        owner: &str,
        version: u64,
        from_owner: &str,
        expected_version: u64,
    ) -> ServiceResult<Vec<DbTransactionStep>> {
        let mut builder = TransferTxBuilder::new(self, bot_id, true);
        // ABA CAS: the round snapshot must equal the
        // in-transaction owner/version (already
        // classified; re-verified under the lock).
        builder.push_eq_guard(vec![
            DbTransactionParam::value(DbValue::from(owner)),
            builder.owner_ref(),
        ]);
        builder.push_eq_guard(vec![
            DbTransactionParam::value(DbValue::from(version)),
            builder.version_ref(),
        ]);
        self.push_operator_alive_guard(&mut builder, operator);
        // The pending row must still be fresh and carry
        // THIS round (ref-bound to the in-tx values).
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND {target} \
                   AND status = 'pending' \
                   AND from_owner_actor_id = ? \
                   AND expected_ownership_version = ? \
                   AND expires_at > {now}{lock}",
                target = target_id_eq(),
                now = self.db_now_utc(),
                lock = lock_suffix(self),
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                DbTransactionParam::value(DbValue::from(target)),
                builder.owner_ref(),
                builder.version_ref(),
            ],
            1,
        );
        if action == TransferAction::Accept {
            self.build_accept_writes(
                &mut builder,
                bot_id,
                target,
                operator,
                mutation,
                owner,
                version,
                from_owner,
                expected_version,
            )
            .await?;
        } else {
            let (status, audit_action) = match action {
                TransferAction::Reject => {
                    ("rejected", ACTION_TRANSFER_REJECT)
                }
                _ => ("cancelled", ACTION_TRANSFER_CANCEL),
            };
            builder.add_checked_write(
                format!(
                    "UPDATE bot_ownership_transfers \
                     SET status = '{status}', decided_at = {now}, \
                         decided_by_kind = ?, decided_by_id = ?, \
                         reason = NULL, updated_at = {now}, \
                         gmt_modified = {modified} \
                     WHERE env = ? AND bot_id = ? AND {target} \
                       AND status = 'pending'",
                    now = self.db_now_utc(),
                    modified = gmt_modified_now(self),
                    target = target_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(DbValue::from(
                        operator.kind.as_str(),
                    )),
                    DbTransactionParam::value(DbValue::from(
                        operator.actor_id.as_str(),
                    )),
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
            );
            self.push_transfer_audit(
                &mut builder,
                bot_id,
                &mutation.mutation_id,
                TransferAuditDraft {
                    sequence: 0,
                    action: audit_action,
                    actor_kind: operator.kind.as_str().to_string(),
                    actor_id: operator.actor_id.clone(),
                    subject: target.to_string(),
                    before_state: Some(transfer_audit_state(
                        "pending",
                        from_owner,
                        expected_version,
                    )),
                    after_state: Some(transfer_audit_state(
                        status,
                        from_owner,
                        expected_version,
                    )),
                },
            );
        }
        Ok(builder.finish())
    }

    /// The accept write annex (spec §6.3): edge-state
    /// classification happens from fresh reads and is
    /// re-verified by in-transaction guards; writes are
    /// ordered so the approved-owner uniqueness is never
    /// violated mid-transaction (revoke before
    /// restore/insert).
    #[allow(clippy::too_many_arguments)]
    async fn build_accept_writes(
        &self,
        builder: &mut TransferTxBuilder,
        bot_id: &str,
        target: &str,
        operator: &Operator,
        mutation: &AuthorityMutation,
        owner: &str,
        version: u64,
        from_owner: &str,
        expected_version: u64,
    ) -> ServiceResult<()> {
        let lock = lock_suffix(self);
        let _ = (owner, version);
        let now = self.db_now_utc();
        let modified = gmt_modified_now(self);
        // Target Human survival at decision time
        // (spec §6.3 check the Bot/Human survival): the
        // ACTOR row is re-verified live under the row lock
        // (Bot row first, spec §7 order).
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bcs_bots \
                 WHERE {} \
                   AND actor_kind = 'human' \
                   AND env = ? \
                   AND COALESCE(is_deleted, 0) = 0{}",
                self.bot_id_eq(),
                lock_suffix(self),
            ),
            vec![
                DbTransactionParam::value(DbValue::from(
                    human_actor_id(
                        &human_user_id_of_actor(target)
                            .expect("target shape validated"),
                    )
                    .as_str(),
                )),
                DbTransactionParam::value(self.env_param()),
            ],
            1,
        );
        // Edge-state classification (parameter
        // preparation; guards re-verify under the lock).
        let (owner_approved, owner_revoked, owner_other) =
            self.pre_read_edge_state(bot_id, target, "owner").await?;
        if owner_approved > 0 || owner_other > 0 {
            return Err(super::owner_integrity_error(format!(
                "bot {bot_id} target '{target}' carries an unexpected \
                 owner edge state"
            )));
        }
        let restore_target_owner = owner_revoked > 0;
        let (old_mgr_approved, old_mgr_revoked, old_mgr_other) =
            self.pre_read_edge_state(bot_id, from_owner, "manager")
                .await?;
        if old_mgr_other > 0 {
            return Err(super::owner_integrity_error(format!(
                "bot {bot_id} old owner '{from_owner}' carries an \
                 unexpected manager edge shape"
            )));
        }
        let (new_mgr_approved, _new_mgr_revoked, _new_mgr_other) =
            self.pre_read_edge_state(bot_id, target, "manager").await?;
        if new_mgr_approved > 1 {
            return Err(super::owner_integrity_error(format!(
                "bot {bot_id} target '{target}' carries {new_mgr_approved} \
                 approved manager edges"
            )));
        }

        // Guards pinning the classified edge states.
        if restore_target_owner {
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM edge_grants \
                     WHERE env = ? AND {to} AND grant_kind = 'owner' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'revoked' AND {from_}{lock}",
                    to = self.to_id_eq(),
                    from_ = self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
                1,
            );
        } else {
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM edge_grants \
                     WHERE env = ? AND {to} AND grant_kind = 'owner' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND {from_}{lock}",
                    to = self.to_id_eq(),
                    from_ = self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
                0,
            );
        }
        if old_mgr_revoked > 0 {
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM edge_grants \
                     WHERE env = ? AND {to} AND grant_kind = 'manager' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'revoked' AND {from_}{lock}",
                    to = self.to_id_eq(),
                    from_ = self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(from_owner)),
                ],
                1,
            );
        } else if old_mgr_approved == 0 {
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM edge_grants \
                     WHERE env = ? AND {to} AND grant_kind = 'manager' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND {from_}{lock}",
                    to = self.to_id_eq(),
                    from_ = self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(from_owner)),
                ],
                0,
            );
        }
        if new_mgr_approved == 1 {
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM edge_grants \
                     WHERE env = ? AND {to} AND grant_kind = 'manager' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'approved' AND {from_}{lock}",
                    to = self.to_id_eq(),
                    from_ = self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
                1,
            );
        }

        // ---- ordered writes ----
        // 1. Old owner edge revoked (frees the
        //    approved-owner uniqueness).
        builder.add_checked_write(
            format!(
                "UPDATE edge_grants \
                 SET status = 'revoked', gmt_modified = {modified} \
                 WHERE env = ? AND {to} AND grant_kind = 'owner' \
                   AND grant_ref_id = 0 AND rules IS NULL \
                   AND status = 'approved' AND {from_}",
                modified = modified,
                to = self.to_id_eq(),
                from_ = self.from_id_eq(),
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                builder.owner_ref(),
            ],
        );
        // 2. Target owner edge created/restored.
        if restore_target_owner {
            builder.add_checked_write(
                format!(
                    "UPDATE edge_grants \
                     SET status = 'approved', gmt_modified = {modified} \
                     WHERE env = ? AND {to} AND grant_kind = 'owner' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'revoked' AND {from_}",
                    modified = modified,
                    to = self.to_id_eq(),
                    from_ = self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
            );
        } else {
            builder.add_checked_write(
                "INSERT INTO edge_grants \
                 (env, from_id, to_id, grant_kind, grant_ref_id, \
                  rules, status, originator_policy_type) \
                 VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', \
                         'same_as_from')"
                    .to_string(),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    DbTransactionParam::value(DbValue::from(target)),
                    builder.bot_ref(),
                ],
            );
        }
        // 3. Version +1 (conditional CAS; genuinely
        //    changing).
        builder.add_checked_write(
            format!(
                "UPDATE bcs_bots \
                 SET ownership_version = ownership_version + 1, \
                     gmt_modified = {modified} \
                 WHERE {bot_pred} AND env = ? \
                   AND ownership_version = ?",
                modified = modified,
                bot_pred = self.bot_id_eq(),
            ),
            vec![
                builder.bot_ref(),
                DbTransactionParam::value(self.env_param()),
                builder.version_ref(),
            ],
        );
        // 4. The new owner's manager edge is removed
        //    (spec §6.3 step 3).
        if new_mgr_approved == 1 {
            builder.add_checked_write(
                format!(
                    "UPDATE edge_grants \
                     SET status = 'revoked', gmt_modified = {modified} \
                     WHERE env = ? AND {to} AND grant_kind = 'manager' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'approved' AND {from_}",
                    modified = modified,
                    to = self.to_id_eq(),
                    from_ = self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
            );
        }
        // 5. The old owner becomes a normal manager.
        if old_mgr_revoked > 0 {
            builder.add_checked_write(
                format!(
                    "UPDATE edge_grants \
                     SET status = 'approved', gmt_modified = {modified} \
                     WHERE env = ? AND {to} AND grant_kind = 'manager' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'revoked' AND {from_}",
                    modified = modified,
                    to = self.to_id_eq(),
                    from_ = self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(from_owner)),
                ],
            );
        } else if old_mgr_approved == 0 {
            builder.add_checked_write(
                "INSERT INTO edge_grants \
                 (env, from_id, to_id, grant_kind, grant_ref_id, \
                  rules, status, originator_policy_type) \
                 VALUES (?, ?, ?, 'manager', 0, NULL, 'approved', \
                         'same_as_from')"
                    .to_string(),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    DbTransactionParam::value(DbValue::from(from_owner)),
                    builder.bot_ref(),
                ],
            );
        }
        // 6. Row → accepted with the decision and the
        //    result version.
        builder.add_checked_write(
            format!(
                "UPDATE bot_ownership_transfers \
                 SET status = 'accepted', decided_at = {now}, \
                     decided_by_kind = ?, decided_by_id = ?, \
                     reason = NULL, result_ownership_version = ?, \
                     updated_at = {now}, gmt_modified = {modified} \
                 WHERE env = ? AND bot_id = ? AND {target_pred} \
                   AND status = 'pending'",
                now = now,
                modified = modified,
                target_pred = target_id_eq(),
            ),
            vec![
                DbTransactionParam::value(DbValue::from(
                    operator.kind.as_str(),
                )),
                DbTransactionParam::value(DbValue::from(
                    operator.actor_id.as_str(),
                )),
                DbTransactionParam::value(DbValue::from(version + 1)),
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                DbTransactionParam::value(DbValue::from(target)),
            ],
        );
        // Post-state guard: the bot still owns exactly
        // one approved owner edge (the target's).
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM edge_grants \
                 WHERE env = ? AND {to} AND grant_kind = 'owner' \
                   AND grant_ref_id = 0 AND rules IS NULL \
                   AND status = 'approved'",
                to = self.to_id_eq(),
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
            ],
            1,
        );
        // Audit: full round (pending → accepted with the
        // new owner + the incremented version).
        self.push_transfer_audit(
            builder,
            bot_id,
            &mutation.mutation_id,
            TransferAuditDraft {
                sequence: 0,
                action: ACTION_TRANSFER_ACCEPT,
                actor_kind: operator.kind.as_str().to_string(),
                actor_id: operator.actor_id.clone(),
                subject: target.to_string(),
                before_state: Some(transfer_audit_state(
                    "pending",
                    from_owner,
                    expected_version,
                )),
                after_state: Some(transfer_audit_state(
                    "accepted", target, version + 1,
                )),
            },
        );
        Ok(())
    }
}

