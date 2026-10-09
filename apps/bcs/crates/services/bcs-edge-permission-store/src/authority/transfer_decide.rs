//! Ownership transfer decision engine (plan Task 8, spec §10.2/§10.3).
//!
//! `decide_transfer` runs the ONE-Bot-serialized decision flow. Terminal
//! rows never rewrite history: the committed outcome is re-derived from
//! the persisted row (receipt / `Expired` / `OwnerChanged` /
//! `Invalidated` by persisted `terminal_reason`), so response-loss
//! retries observe the same committed result — an
//! `invalidated(owner_changed)` retry still returns `OwnerChanged`, never
//! a generic Invalidated (spec §10.2/OT12).
//!
//! A still-pending row decomposes into committed-prefix probes taken in
//! the fixed lock order (Bot row → the guarded transfer row → stable-id
//! Humans), each conditional on the DATABASE clock and the CURRENT rows:
//! 1. expiry probe: a deadline-lapsed pending materializes `expired`
//!    (system decider, `decided_at = expires_at`) as a committed domain
//!    result (§9.1: the boundary decision point is the conditional
//!    transition, never request arrival);
//! 2. owner_changed probe: a pending whose `from_user_id` or
//!    `expected_owner_version` no longer matches the CURRENT authority —
//!    with the authority data proven INTACT first — materializes
//!    `invalidated(owner_changed)` (decider `ownership-validation`,
//!    the brief's exact SQL shape) and returns `Ok(OwnerChanged)` as a
//!    NORMAL domain result that must never be rolled back; no edge
//!    changes, no version bump, the pending slot releases (§10.2);
//! 3. the action's all-or-nothing transaction (§10.2 steps 1–5):
//!    - accept: revoke the previous owner edge, restore/insert the
//!      recipient's owner edge, grant the previous owner the
//!      `ownership_transfer/<transfer_id>` manager source, revoke ONLY
//!      the recipient's non-team (`direct`/`ownership_transfer`) manager
//!      sources (`team/*` stays governed by team sync, OT02), CAS-bump
//!      `ownership_version` and save the accepted receipt with its
//!      committed `result_owner_version` — every changing statement's
//!      WHERE guards re-prove the plan against CURRENT rows, so a genuine
//!      failure rolls the WHOLE transaction back (OT13);
//!    - reject/cancel: only the row's own guarded terminal transition;
//!      cancel re-proves the initiator is still the current owner
//!      (§9: cancellation belongs to the still-current initiating owner).
//!
//! Visibility is decided on the minimal record FIRST: a non-party caller
//! gets the concealment branch (indistinguishable from a missing id,
//! spec §11.2), and a party whose role forbids the action gets
//! `Forbidden` (e.g. the initiator accepting his own transfer).
//!
//! These lanes never write `bot_manager_changes` (spec §5.2: the
//! committed transfer receipt IS the audit of the role change). The
//! statement budget is constant per decide (asserted by the Task 8
//! tests): the minimal-record read, ONE validation read transaction, one
//! lock+conditional per committed-prefix probe, and ONE action
//! transaction of at most seven statements.

use bcs_db_api::{DbError, DbRow, DbSqlFlavor, DbStatement, DbTransactionStep, DbValue};
use bcs_domain::TransferAction;
use bcs_service_api::port::repo::bot_authority::{human_actor_id, user_id_from_actor};
use bcs_service_api::types::error::{AuthorityError, TransferConflict};
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, OwnershipTransfer,
};
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

use super::transfer_query::{
    db_now_expr, decode_transfer_row, from_dual, human_expr, not_visible,
    TRANSFER_ROW_COLUMNS, EXPIRED_SYSTEM_MARKER,
};
use crate::common::{parse_timestamp_epoch_ms, service_db_error};

/// Optimistic-write attempts before surfacing a retryable conflict: each
/// attempt re-validates, so genuine state settles into its business
/// branch long before this budget is spent.
const MAX_DECIDE_ATTEMPTS: usize = 3;

enum DecideAttempt {
    /// A committed (or observed) outcome.
    Done(CommittedTransferOutcome),
    /// The optimistic window drifted; the caller re-validates.
    Drift(DbError),
    /// A business or infrastructure branch — surface it unchanged.
    Service(ServiceError),
}

impl From<ServiceError> for DecideAttempt {
    fn from(err: ServiceError) -> Self {
        DecideAttempt::Service(err)
    }
}

impl super::reads::DbBotAuthorityStore {
    pub(super) async fn decide_transfer_inner(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        action: TransferAction,
    ) -> ServiceResult<CommittedTransferOutcome> {
        if actor_user_id.trim().is_empty() || transfer_id.trim().is_empty() {
            // Blank identities get the concealment branch, never a leak.
            return Err(not_visible(transfer_id));
        }
        let mut last_drift = None;
        for _ in 0..MAX_DECIDE_ATTEMPTS {
            match self.decide_once(actor_user_id, transfer_id, action).await {
                DecideAttempt::Done(outcome) => return Ok(outcome),
                DecideAttempt::Drift(err) => {
                    warn!(
                        transfer_id,
                        "ownership transfer decide lost its optimistic window, \
                         re-validating: {err}"
                    );
                    last_drift = Some(err);
                }
                DecideAttempt::Service(err) => return Err(err),
            }
        }
        Err(ServiceError::Authority(AuthorityError::TransferConflict(
            TransferConflict::Contended {
                resource: transfer_id.to_string(),
                detail: last_drift
                    .map(|err| err.to_string())
                    .unwrap_or_else(|| "optimistic window lost repeatedly".to_string()),
            },
        )))
    }

    /// One validated-read → probe → action attempt.
    async fn decide_once(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        action: TransferAction,
    ) -> DecideAttempt {
        // -- Minimal-record read FIRST (auth + visibility; no Bot data
        //    leaks, and terminal retries never require Bot liveness). ----
        let row = match self.read_minimal_record(transfer_id).await {
            Ok(Some(row)) => row,
            Ok(None) => return DecideAttempt::Service(not_visible(transfer_id)),
            Err(err) => return DecideAttempt::Service(err),
        };
        let bot_id = match row.get_string("bot_id") {
            Ok(Some(value)) => value,
            _ => {
                return DecideAttempt::Service(ServiceError::InternalError(
                    "authority transfer decide: minimal record missing bot_id".to_string(),
                ))
            }
        };
        let from_user_id = row
            .get_string("from_user_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        let to_user_id = row
            .get_string("to_user_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        let status = row
            .get_string("status")
            .ok()
            .flatten()
            .unwrap_or_default();

        // Concealment vs. party-role (spec §11.2): a non-party caller is
        // indistinguishable from a missing id; a party asking for the
        // other side's action gets the Forbidden branch.
        let is_recipient = to_user_id == actor_user_id;
        let is_initiator = from_user_id == actor_user_id;
        if !is_recipient && !is_initiator {
            return DecideAttempt::Service(not_visible(transfer_id));
        }
        let role_allows = match action {
            TransferAction::Accept | TransferAction::Reject => is_recipient,
            TransferAction::Cancel => is_initiator,
        };
        if !role_allows {
            return DecideAttempt::Service(ServiceError::Authority(
                AuthorityError::Forbidden(format!(
                    "user '{actor_user_id}' holds the wrong party role for this transfer \
                     action"
                )),
            ));
        }

        // -- Terminal-row retries: observe the committed outcome, never
        //    rewrite history. The persisted `terminal_reason` keeps the
        //    owner_changed semantics across retries (OT12/OT21). --------
        if status != "pending" {
            return match status.as_str() {
                "accepted" if action == TransferAction::Accept => {
                    match self.receipt_of(&row).await {
                        Ok(receipt) => DecideAttempt::Done(CommittedTransferOutcome::Receipt(receipt)),
                        Err(err) => DecideAttempt::Service(err),
                    }
                }
                "rejected" if action == TransferAction::Reject => {
                    match self.receipt_of(&row).await {
                        Ok(receipt) => DecideAttempt::Done(CommittedTransferOutcome::Receipt(receipt)),
                        Err(err) => DecideAttempt::Service(err),
                    }
                }
                "cancelled" if action == TransferAction::Cancel => {
                    match self.receipt_of(&row).await {
                        Ok(receipt) => DecideAttempt::Done(CommittedTransferOutcome::Receipt(receipt)),
                        Err(err) => DecideAttempt::Service(err),
                    }
                }
                "expired" => DecideAttempt::Done(CommittedTransferOutcome::Expired),
                "invalidated" => match row
                    .get_string("terminal_reason")
                    .ok()
                    .flatten()
                    .as_deref()
                {
                    Some("owner_changed") => {
                        DecideAttempt::Done(CommittedTransferOutcome::OwnerChanged)
                    }
                    Some(_) | None => DecideAttempt::Done(CommittedTransferOutcome::Invalidated),
                },
                _ => DecideAttempt::Service(ServiceError::Authority(
                    AuthorityError::TransferConflict(TransferConflict::NotPending {
                        transfer_id: transfer_id.to_string(),
                        status: status.clone(),
                    }),
                )),
            };
        }

        // -- Pending flow: validate once more, then probe/execute on the
        //    Bot boundary. A retired Bot has no live authority surface;
        //    the pending row survives it only through raw corruption and
        //    the honest branch is BotNotFound (§10.3). ------------------
        let validated = match self.decide_validation(bot_id.as_str(), transfer_id).await {
            Ok(validated) => validated,
            // The typed NotPending marker ONLY means the pending row was
            // decided while we waited: re-validate from the top so the
            // terminal dispatch answers the retry (the incompatible-action
            // branch above produces the same TYPE with the terminal row's
            // status, but never reaches this match on the validation read).
            Err(ServiceError::Authority(AuthorityError::TransferConflict(
                TransferConflict::NotPending { .. },
            ))) => {
                return DecideAttempt::Drift(DbError::ConditionFailed { expected: 1, actual: 0 });
            }
            Err(err) => return DecideAttempt::Service(err),
        };

        // Probe 1: deadline lapsed (DB clock inside the write lock).
        if validated.expires_lapsed {
            return self.expire_probe(transfer_id).await;
        }
        // Probe 2: owner/version snapshot mismatch with intact authority.
        if !validated.from_matches_owner || !validated.version_matches {
            return self.invalidate_probe(bot_id.as_str(), transfer_id).await;
        }

        // Reject/cancel share the owner/version snapshot re-proof inline so
        // a drifted window fails the ExecuteChecked pin, rolls the attempt
        // back and re-validates into the owner_changed invalidation — never
        // a phantom terminal on a snapshot the authority already replaced.
        let snapshot_guard = format!(
            "from_user_id = (SELECT SUBSTR(o.from_id, 7) FROM edge_grants o \
                   WHERE o.env = ? AND o.to_id = ? AND o.grant_kind = 'owner' \
                     AND o.status = 'approved' ORDER BY o.id LIMIT 1) \
             AND expected_owner_version = (SELECT b.ownership_version FROM bcs_bots b \
                   WHERE b.bot_uuid = ? AND b.env = ? AND COALESCE(b.is_deleted, 0) = 0)"
        );
        // The action's all-or-nothing transaction.
        match action {
            TransferAction::Accept => {
                self.accept_transaction(actor_user_id, transfer_id, &bot_id, &row)
                    .await
            }
            TransferAction::Reject => {
                self.terminal_transition(
                    actor_user_id,
                    transfer_id,
                    "rejected",
                    vec![
                        "to_user_id = ?".to_string(),
                        snapshot_guard.clone(),
                    ],
                    vec![
                        DbValue::from(actor_user_id),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(bot_id.as_str()),
                        DbValue::from(bot_id.as_str()),
                        DbValue::from(self.env.as_str()),
                    ],
                )
                .await
            }
            TransferAction::Cancel => {
                // Cancel re-proves the initiator is STILL the current
                // owner (spec §9), as a condition of the changing
                // statement itself under the lock — on top of the shared
                // owner/version snapshot re-proof.
                self.terminal_transition(
                    actor_user_id,
                    transfer_id,
                    "cancelled",
                    vec![
                        "from_user_id = ?".to_string(),
                        snapshot_guard,
                        format!(
                            "EXISTS (SELECT 1 FROM edge_grants co \
                               WHERE co.env = ? AND co.to_id = ? \
                                 AND co.grant_kind = 'owner' AND co.status = 'approved' \
                                 AND co.from_id = {})",
                            human_expr(&self.flavor)
                        ),
                    ],
                    vec![
                        DbValue::from(actor_user_id),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(bot_id.as_str()),
                        DbValue::from(bot_id.as_str()),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(self.env.as_str()),
                        DbValue::from(bot_id.as_str()),
                        DbValue::from(actor_user_id),
                    ],
                )
                .await
            }
        }
    }

    // ------------------------------------------------------------------
    // Probes (committed-prefix on the serialized Bot lock)
    // ------------------------------------------------------------------

    /// Probe 1: the deadline-lapsed pending materializes `expired` in the
    /// Bot-locked transaction (system decider, `decided_at = expires_at`
    /// — §9.1's logical decision time). Zero affected rows commits the
    /// empty prefix and re-validates (state drifted while we waited).
    async fn expire_probe(&self, transfer_id: &str) -> DecideAttempt {
        let now = self.flavor.now();
        let step = DbStatement::with_params(
            &format!(
                "UPDATE bot_ownership_transfers \
                 SET status = 'expired', decision_actor_kind = 'system', \
                     decided_by = '{EXPIRED_SYSTEM_MARKER}', decided_at = expires_at, \
                     gmt_modified = {now} \
                 WHERE env = ? AND transfer_id = ? AND status = 'pending' \
                   AND expires_at <= {now}"
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(transfer_id),
            ],
        )
        .with_transaction_stop_on_no_rows();
        self.run_probe_transaction(transfer_id, step, CommittedTransferOutcome::Expired)
            .await
    }

    /// Probe 2: the owner/version-mismatched pending materializes
    /// `invalidated(owner_changed)` (the brief's SQL shape, decider
    /// `ownership-validation`), the mismatch re-proved as conditions of
    /// the changing statement against CURRENT rows. Zero rows is a drift.
    async fn invalidate_probe(&self, bot_id: &str, transfer_id: &str) -> DecideAttempt {
        let now = self.flavor.now();
        let step = DbStatement::with_params(
            &format!(
                "UPDATE bot_ownership_transfers \
                 SET status = 'invalidated', terminal_reason = 'owner_changed', \
                     decision_actor_kind = 'system', decided_by = 'ownership-validation', \
                     decided_at = {now}, gmt_modified = {now} \
                 WHERE env = ? AND transfer_id = ? AND status = 'pending' \
                   AND expires_at > {now} \
                   AND ( from_user_id <> (SELECT SUBSTR(o.from_id, 7) FROM edge_grants o \
                           WHERE o.env = ? AND o.to_id = ? AND o.grant_kind = 'owner' \
                             AND o.status = 'approved' ORDER BY o.id LIMIT 1) \
                         OR expected_owner_version <> (SELECT b.ownership_version \
                               FROM bcs_bots b WHERE b.bot_uuid = ? AND b.env = ? \
                                 AND COALESCE(b.is_deleted, 0) = 0) )"
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(transfer_id),
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(bot_id),
                DbValue::from(self.env.as_str()),
            ],
        )
        .with_transaction_stop_on_no_rows();
        self.run_probe_transaction(transfer_id, step, CommittedTransferOutcome::OwnerChanged)
            .await
    }

    /// The shared probe transaction: Bot lock first, then the single
    /// conditional write with committed-prefix semantics — a miss commits
    /// nothing and re-validates; a genuine DB failure is an error, never a
    /// fake domain result (§10.2 物化/提交失败则回滚、500).
    async fn run_probe_transaction(
        &self,
        transfer_id: &str,
        step: DbStatement,
        outcome: CommittedTransferOutcome,
    ) -> DecideAttempt {
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Query(self.bot_lock_statement_for(transfer_id)),
                DbTransactionStep::Execute(step),
            ])
            .await;
        match results {
            Ok(steps) => match steps.get(1) {
                Some(bcs_db_api::DbTransactionStepResult::Executed(result))
                    if result.affected_rows >= 1 =>
                {
                    DecideAttempt::Done(outcome)
                }
                // Zero rows after a stop-on-no-rows miss: the executed
                // prefix committed nothing; re-classify from the top.
                _ => DecideAttempt::Drift(DbError::ConditionFailed {
                    expected: 1,
                    actual: 0,
                }),
            },
            // A backend failure inside the probe is a genuine error
            // (rolled back, §10.2 物化/提交失败则回滚, never a fake result).
            Err(other) => DecideAttempt::Service(service_db_error(
                "authority_transfer_probe_write",
                other,
            )),
        }
    }

    // ------------------------------------------------------------------
    // reject / cancel: one guarded terminal transition
    // ------------------------------------------------------------------

    /// The reject/cancel transaction: Bot lock + ONE `ExecuteChecked`
    /// transition whose WHERE guards re-prove the validated predicates
    /// (still pending, deadline intact, the action's party identity —
    /// and for cancel, the initiator still being the current owner).
    async fn terminal_transition(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        terminal_status: &'static str,
        extra_conditions: Vec<String>,
        extra_params: Vec<DbValue>,
    ) -> DecideAttempt {
        let now = self.flavor.now();
        let joined = format!(
            "UPDATE bot_ownership_transfers \
             SET status = '{terminal_status}', decision_actor_kind = 'human', \
                 decided_by = ?, decided_at = {now}, gmt_modified = {now} \
             WHERE env = ? AND transfer_id = ? AND status = 'pending' \
               AND expires_at > {now} AND {extra}",
            extra = extra_conditions.join(" AND ")
        );
        let mut params = vec![
            DbValue::from(actor_user_id),
            DbValue::from(self.env.as_str()),
            DbValue::from(transfer_id),
        ];
        params.extend(extra_params);
        let step = DbStatement::with_params(joined, params);
        match self
            .db
            .transaction(vec![
                DbTransactionStep::Query(self.bot_lock_statement_for(transfer_id)),
                DbTransactionStep::ExecuteChecked {
                    statement: step,
                    expected_affected_rows: 1,
                },
            ])
            .await
        {
            Ok(_) => match self.read_committed_receipt(transfer_id).await {
                Ok(Some(receipt)) => {
                    DecideAttempt::Done(CommittedTransferOutcome::Receipt(receipt))
                }
                Ok(None) => DecideAttempt::Service(ServiceError::InternalError(format!(
                    "ownership transfer decide: committed receipt '{transfer_id}' is unreadable"
                ))),
                Err(err) => DecideAttempt::Service(err),
            },
            Err(err) => match err {
                DbError::ConditionFailed { expected, actual } => DecideAttempt::Drift(
                    DbError::ConditionFailed { expected, actual },
                ),
                other => DecideAttempt::Service(service_db_error(
                    "authority_transfer_decide_write",
                    other,
                )),
            },
        }
    }

    // ------------------------------------------------------------------
    // accept: the all-or-nothing owner-swap transaction (§10.2 steps 3–5)
    // ------------------------------------------------------------------

    async fn accept_transaction(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        bot_id: &str,
        pending_row: &DbRow,
    ) -> DecideAttempt {
        let from_user_id = pending_row
            .get_string("from_user_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        let to_user_id = pending_row
            .get_string("to_user_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        let now = self.flavor.now();
        let env = self.env.as_str();

        // Plan the recipient's owner edge (optimistic fast path; its drift
        // rolls the guarded write back and retries): RESTORE a previously
        // revoked row under the SAME id — never INSERT IGNORE — or FRESH
        // insert when no row exists in any status.
        let recipient_edge = human_actor_id(&to_user_id);
        let recipient_manager_plan = match self
            .db
            .query(DbStatement::with_params(
                "SELECT id FROM edge_grants \
                 WHERE env = ? AND to_id = ? AND from_id = ? AND grant_kind = 'owner' \
                 ORDER BY id LIMIT 1",
                vec![
                    DbValue::from(env),
                    DbValue::from(bot_id),
                    DbValue::from(recipient_edge.as_str()),
                ],
            ))
            .await
        {
            Ok(rows) => rows
                .into_iter()
                .next()
                .and_then(|row| row.get_i64("id").ok().flatten()),
            Err(err) => {
                return DecideAttempt::Service(service_db_error(
                    "authority_transfer_accept_plan",
                    err,
                ))
            }
        };

        // One all-or-nothing transaction (OT13): lock → revoke old owner →
        // restore/insert the recipient's owner edge → the previous owner's
        // transfer-source manager edge → revoke the recipient's non-team
        // manager sources → CAS version → save the accepted receipt.
        let mut steps = vec![DbTransactionStep::Query(self.bot_lock_statement_for(transfer_id))];

        // §10.2 step 3: revoke the previous owner edge.
        steps.push(DbTransactionStep::ExecuteChecked {
            expected_affected_rows: 1,
            statement: DbStatement::with_params(
                &format!(
                    "UPDATE edge_grants SET status = 'revoked', gmt_modified = {now} \
                     WHERE env = ? AND to_id = ? AND grant_kind = 'owner' \
                       AND status = 'approved' AND from_id = {}",
                    human_expr(&self.flavor)
                ),
                vec![
                    DbValue::from(env),
                    DbValue::from(bot_id),
                    DbValue::from(from_user_id.as_str()),
                ],
            ),
        });
        // §10.2 step 3: restore/insert the recipient's owner edge.
        match recipient_manager_plan {
            Some(edge_id) => steps.push(DbTransactionStep::ExecuteChecked {
                expected_affected_rows: 1,
                statement: DbStatement::with_params(
                    &format!(
                        "UPDATE edge_grants SET status = 'approved', gmt_modified = {now} \
                         WHERE id = ? AND env = ? AND to_id = ? AND grant_kind = 'owner' \
                           AND status = 'revoked' AND from_id = {}",
                        human_expr(&self.flavor)
                    ),
                    vec![
                        DbValue::from(edge_id),
                        DbValue::from(env),
                        DbValue::from(bot_id),
                        DbValue::from(to_user_id.as_str()),
                    ],
                ),
            }),
            None => steps.push(DbTransactionStep::ExecuteChecked {
                expected_affected_rows: 1,
                statement: DbStatement::with_params(
                    &format!(
                        "INSERT INTO edge_grants \
                           (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
                            originator_policy_type, originator_policy_data, \
                            management_source_kind, management_source_id) \
                         SELECT ?, {edge}, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL, \
                            'owner', 'owner'{dual} \
                         WHERE NOT EXISTS (SELECT 1 FROM edge_grants ne \
                               WHERE ne.env = ? AND ne.to_id = ? AND ne.from_id = {edge} \
                                 AND ne.grant_kind = 'owner')",
                        edge = human_expr(&self.flavor),
                        dual = from_dual(&self.flavor),
                    ),
                    vec![
                        DbValue::from(env),
                        DbValue::from(to_user_id.as_str()),
                        DbValue::from(bot_id),
                        DbValue::from(env),
                        DbValue::from(bot_id),
                        DbValue::from(to_user_id.as_str()),
                    ],
                ),
            }),
        }
        // §10.2 step 4: the previous owner keeps `ownership_transfer/<id>`
        // manager access on the transferred Bot.
        steps.push(DbTransactionStep::ExecuteChecked {
            expected_affected_rows: 1,
            statement: DbStatement::with_params(
                &format!(
                    "INSERT INTO edge_grants \
                       (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
                        originator_policy_type, originator_policy_data, \
                        management_source_kind, management_source_id) \
                     SELECT ?, {edge}, ?, 'manager', 0, NULL, 'approved', 'same_as_from', NULL, \
                        'ownership_transfer', ?{dual} \
                     WHERE NOT EXISTS (SELECT 1 FROM edge_grants ps \
                           WHERE ps.env = ? AND ps.to_id = ? AND ps.from_id = {edge} \
                             AND ps.grant_kind = 'manager' \
                             AND ps.management_source_kind = 'ownership_transfer' \
                             AND ps.management_source_id = ?)",
                    edge = human_expr(&self.flavor),
                    dual = from_dual(&self.flavor),
                ),
                vec![
                    DbValue::from(env),
                    DbValue::from(from_user_id.as_str()),
                    DbValue::from(bot_id),
                    DbValue::from(transfer_id),
                    DbValue::from(env),
                    DbValue::from(bot_id),
                    DbValue::from(from_user_id.as_str()),
                    DbValue::from(transfer_id),
                ],
            ),
        });
        // §10.2 step 4: revoke ONLY the recipient's non-team manager
        // sources (`team/*` stays governed by team sync — OT02). Any count.
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            &format!(
                "UPDATE edge_grants SET status = 'revoked', gmt_modified = {now} \
                 WHERE env = ? AND to_id = ? AND from_id = {} AND grant_kind = 'manager' \
                   AND status = 'approved' \
                   AND management_source_kind IN ('direct', 'ownership_transfer')",
                human_expr(&self.flavor)
            ),
            vec![
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(to_user_id.as_str()),
            ],
        )));
        // §10.2 step 5: CAS the ownership_version (+1) against the
        // transfer's own pending snapshot.
        steps.push(DbTransactionStep::ExecuteChecked {
            expected_affected_rows: 1,
            statement: DbStatement::with_params(
                "UPDATE bcs_bots SET ownership_version = ownership_version + 1 \
                 WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 \
                   AND ownership_version = (SELECT t.expected_owner_version \
                       FROM bot_ownership_transfers t \
                       WHERE t.env = ? AND t.transfer_id = ? AND t.status = 'pending')",
                vec![
                    DbValue::from(bot_id),
                    DbValue::from(env),
                    DbValue::from(env),
                    DbValue::from(transfer_id),
                ],
            ),
        });
        // §10.2 step 5: save the accepted receipt. `result_owner_version`
        // is the row's own `expected_owner_version + 1` — the CAS above
        // proves that is the committed new version — and the statement's
        // guards re-prove parties, deadline, the new owner edge and the
        // bumped version against CURRENT rows.
        steps.push(DbTransactionStep::ExecuteChecked {
            expected_affected_rows: 1,
            statement: DbStatement::with_params(
                &format!(
                    "UPDATE bot_ownership_transfers \
                     SET status = 'accepted', decision_actor_kind = 'human', decided_by = ?, \
                         decided_at = {now}, result_owner_version = expected_owner_version + 1, \
                         gmt_modified = {now} \
                     WHERE env = ? AND transfer_id = ? AND status = 'pending' \
                       AND from_user_id = ? AND to_user_id = ? \
                       AND expires_at > {now} \
                       AND (SELECT b.ownership_version FROM bcs_bots b \
                             WHERE b.bot_uuid = ? AND b.env = ?) = expected_owner_version + 1 \
                       AND EXISTS (SELECT 1 FROM edge_grants ae \
                             WHERE ae.env = ? AND ae.to_id = ? \
                               AND ae.grant_kind = 'owner' AND ae.status = 'approved' \
                               AND ae.from_id = {})",
                    human_expr(&self.flavor)
                ),
                vec![
                    DbValue::from(actor_user_id),
                    DbValue::from(env),
                    DbValue::from(transfer_id),
                    DbValue::from(from_user_id.as_str()),
                    DbValue::from(to_user_id.as_str()),
                    DbValue::from(bot_id),
                    DbValue::from(env),
                    DbValue::from(env),
                    DbValue::from(bot_id),
                    DbValue::from(to_user_id.as_str()),
                ],
            ),
        });

        match self.db.transaction(steps).await {
            Ok(_) => match self.read_committed_receipt(transfer_id).await {
                Ok(Some(receipt)) => {
                    DecideAttempt::Done(CommittedTransferOutcome::Receipt(receipt))
                }
                Ok(None) => DecideAttempt::Service(ServiceError::InternalError(
                    format!(
                        "ownership transfer accept: committed receipt '{transfer_id}' \
                         is unreadable"
                    ),
                )),
                Err(err) => DecideAttempt::Service(err),
            },
            // A guarded statement drifted: the whole accept rolled back —
            // re-validate into the branch the new state demands.
            Err(DbError::ConditionFailed { expected, actual }) => {
                DecideAttempt::Drift(DbError::ConditionFailed { expected, actual })
            }
            Err(other) => DecideAttempt::Service(service_db_error(
                "authority_transfer_accept_write",
                other,
            )),
        }
    }

    // ------------------------------------------------------------------
    // Shared reads
    // ------------------------------------------------------------------

    /// The minimal-record read (raw columns of the transfer row for the
    /// party/terminal classification; full strict decode happens on the
    /// receipt paths). A DB failure is an error, never a 404-shaped None.
    async fn read_minimal_record(&self, transfer_id: &str) -> ServiceResult<Option<DbRow>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT transfer_id, env, bot_id, from_user_id, to_user_id, status, \
                        terminal_reason, expected_owner_version, expires_at \
                 FROM bot_ownership_transfers \
                 WHERE env = ? AND transfer_id = ? LIMIT 1",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(transfer_id),
                ],
            ))
            .await
            .map_err(|err| service_db_error("authority_transfer_decide_minimal", err))?;
        Ok(rows.into_iter().next())
    }

    /// The pending-flow validation read transaction: live/initialized Bot
    /// with its CURRENT owner edge (strictly human-shaped), the pending
    /// row's snapshot, and the DB-clock expiry classification. Fast-path
    /// branches only; every changing statement re-proves its own guards.
    async fn decide_validation(
        &self,
        bot_id: &str,
        transfer_id: &str,
    ) -> Result<ValidatedPending, ServiceError> {
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Query(DbStatement::with_params(
                    &format!(
                        "SELECT b.ownership_version AS ownership_version, {} \
                         FROM bcs_bots b \
                         WHERE b.bot_uuid = ? AND b.env = ? AND COALESCE(b.is_deleted, 0) = 0",
                        db_now_expr(&self.flavor)
                    ),
                    vec![
                        DbValue::from(bot_id),
                        DbValue::from(self.env.as_str()),
                    ],
                )),
                DbTransactionStep::Query(DbStatement::with_params(
                    "SELECT from_id FROM edge_grants \
                     WHERE env = ? AND to_id = ? AND grant_kind = 'owner' \
                       AND status = 'approved' ORDER BY id",
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(bot_id),
                    ],
                )),
                DbTransactionStep::Query(DbStatement::with_params(
                    &format!(
                        "SELECT from_user_id, to_user_id, expected_owner_version, expires_at, \
                                {} \
                         FROM bot_ownership_transfers \
                         WHERE env = ? AND transfer_id = ? AND status = 'pending' LIMIT 1",
                        db_now_expr(&self.flavor)
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(transfer_id),
                    ],
                )),
            ])
            .await
            .map_err(|err| service_db_error("authority_transfer_decide_read", err))?;
        let bot_rows = match &results[0] {
            bcs_db_api::DbTransactionStepResult::Rows(rows) => rows.clone(),
            _ => {
                return Err(ServiceError::InternalError(
                    "authority transfer decide: expected the bot aggregate".to_string(),
                ))
            }
        };
        let Some(bot_row) = bot_rows.into_iter().next() else {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        };
        let ownership_version = bot_row
            .get_i64("ownership_version")
            .ok()
            .flatten()
            .map(|value| value.max(0) as u64)
            .ok_or_else(|| {
                ServiceError::InternalError(
                    "authority transfer decide: ownership_version missing".to_string(),
                )
            })?;
        if ownership_version == bcs_domain::UNINITIALIZED_OWNERSHIP_VERSION {
            return Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
                bot_id: bot_id.to_string(),
                env: self.env.clone(),
            }));
        }
        let owner_rows = match &results[1] {
            bcs_db_api::DbTransactionStepResult::Rows(rows) => rows.clone(),
            _ => {
                return Err(ServiceError::InternalError(
                    "authority transfer decide: expected the owner-slot query".to_string(),
                ))
            }
        };
        if owner_rows.len() != 1 {
            // 未初始化或损坏的 authority 不伪造失效结论 (OT12): the row
            // stays pending and the caller sees the consistency branch.
            return Err(self.corrupt(
                bot_id,
                format!(
                    "bot has {} approved owner edges while deciding a pending transfer",
                    owner_rows.len()
                ),
            ));
        }
        let owner_from_id = owner_rows[0]
            .get_string("from_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        let Some(owner_user_id) = user_id_from_actor(&owner_from_id) else {
            return Err(self.corrupt(
                bot_id,
                format!("owner edge from non-human actor id '{owner_from_id}'"),
            ));
        };
        let pending_rows = match &results[2] {
            bcs_db_api::DbTransactionStepResult::Rows(rows) => rows.clone(),
            _ => {
                return Err(ServiceError::InternalError(
                    "authority transfer decide: expected the pending-row query".to_string(),
                ))
            }
        };
        let Some(pending) = pending_rows.into_iter().next() else {
            // Decided while we waited (§10.3): surface the typed marker the
            // caller maps to a bounded re-validation from the terminal
            // dispatch. The status field is the marker form; it never
            // escapes to the boundary as a business error.
            return Err(ServiceError::Authority(AuthorityError::TransferConflict(
                TransferConflict::NotPending {
                    transfer_id: transfer_id.to_string(),
                    status: "decided-while-waiting".to_string(),
                },
            )));
        };
        let expected = pending
            .get_i64("expected_owner_version")
            .ok()
            .flatten()
            .map(|value| value.max(0) as u64)
            .unwrap_or(0);
        let from_user_id = pending
            .get_string("from_user_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        let expires_at = pending
            .get_string("expires_at")
            .ok()
            .flatten()
            .unwrap_or_default();
        let db_now = pending
            .get_string("db_now")
            .ok()
            .flatten()
            .or_else(|| bot_row.get_string("db_now").ok().flatten())
            .unwrap_or_default();
        let expires_lapsed = match (
            parse_timestamp_epoch_ms(&expires_at),
            parse_timestamp_epoch_ms(&db_now),
        ) {
            (Some(expires_ms), Some(now_ms)) => now_ms >= expires_ms,
            _ => !expires_at.is_empty() && !db_now.is_empty() && db_now >= expires_at,
        };
        Ok(ValidatedPending {
            expires_lapsed,
            from_matches_owner: from_user_id == owner_user_id,
            version_matches: expected == ownership_version,
        })
    }

    /// The committed receipt re-read (index-driven by the unique transfer
    /// id; `db_now` rides along — terminal rows never re-project).
    async fn read_committed_receipt(
        &self,
        transfer_id: &str,
    ) -> ServiceResult<Option<OwnershipTransfer>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                &format!(
                    "SELECT {TRANSFER_ROW_COLUMNS}, {} FROM bot_ownership_transfers \
                     WHERE env = ? AND transfer_id = ? LIMIT 1",
                    db_now_expr(&self.flavor)
                ),
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(transfer_id),
                ],
            ))
            .await
            .map_err(|err| service_db_error("authority_transfer_receipt", err))?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        decode_transfer_row(&row, &self.env).map(Some)
    }

    /// Strict decode of a terminal row's receipt via the single receipt
    /// read (ONE decode site for every outcome path).
    async fn receipt_of(&self, minimal_row: &DbRow) -> ServiceResult<OwnershipTransfer> {
        let transfer_id = minimal_row
            .get_string("transfer_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        match self.read_committed_receipt(&transfer_id).await {
            Ok(Some(receipt)) => Ok(receipt),
            Ok(None) => Err(not_visible(&transfer_id)),
            Err(err) => Err(err),
        }
    }

    /// The Bot lock of every Decide WRITE transaction, reached THROUGH
    /// the transfer row (lock order: the Bot row first, then the guarded
    /// transfer row — spec §10). The JOIN keeps the DB row of the
    /// serialized boundary identical for both probes and the action.
    fn bot_lock_statement_for(&self, transfer_id: &str) -> DbStatement {
        let suffix = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };
        DbStatement::with_params(
            &format!(
                "SELECT b.bot_uuid, b.env, b.ownership_version FROM bcs_bots b \
                 JOIN bot_ownership_transfers t \
                   ON t.env = b.env AND t.bot_id = b.bot_uuid \
                 WHERE t.env = ? AND t.transfer_id = ? LIMIT 1{suffix}",
                suffix = suffix,
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(transfer_id),
            ],
        )
    }
}

/// The validated fast-path classification of one pending decide.
struct ValidatedPending {
    /// DB clock already past the deadline (probe 1).
    expires_lapsed: bool,
    /// The pending's `from_user_id` is still the CURRENT owner.
    from_matches_owner: bool,
    /// The pending's `expected_owner_version` matches the live version.
    version_matches: bool,
}