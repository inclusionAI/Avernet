//! Atomic ownership retirement/deletion boundary (plan Task 5).
//!
//! [`retire_bot_lifecycle`] retires one Bot in a SINGLE transaction that
//! takes the same first lock every authority flow takes (the Bot's
//! `bcs_bots` row):
//!
//! 1. the soft-delete UPDATE itself — its write lock serializes against
//!    manager mutations and the later transfer-acceptance lane; the plain
//!    Execute carries `stop_on_no_rows` so a repeatedly retired or missing
//!    Bot commits the empty prefix and reports `Ok(false)` (leader-wins
//!    semantics, no error branch for a race that someone else already won);
//! 2. withdrawal of EVERY authority role edge of the Bot (owner and
//!    manager, across all management sources);
//! 3. termination of the Bot's PENDING ownership transfers
//!    (`status = 'invalidated'`, `terminal_reason = 'bot_deleted'`, the
//!    decision columns the frozen schema CHECK requires), so a later
//!    acceptance can never resurrect the retired Bot;
//! 4. the lifecycle audit row (`bcs_bot_action_audits`,
//!    `delete/bot/applied`) in the same commit — its failure rolls the
//!    whole retirement back.
//!
//! [`delete_human_actor`] deletes a Human actor row only when the Human is
//! no LIVE Bot owner: it locks the Human's own row, then every related Bot
//! in stable `bot_uuid` order, re-proves the live-owner guard inside the
//! write transaction (a concurrent acceptance that granted an owner edge
//! rolls the deletion back), withdraws every edge the Human holds (no
//! orphan edges for grants racing the deletion) and appends the same-commit
//! lifecycle audit. Human rows never retire through the Bot lane.
//!
//! The memory twins run inside the exact `MemoryBotRepo` critical sections
//! shared with registration and the Task 3/4 authority state, under the
//! deletion lane's `deleted → bots → authority` lock order.

use bcs_db_api::{DbError, DbSqlFlavor, DbStatement, DbTransactionStep, DbTransactionStepResult};
use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionKind, BotActionResourceKind, BotActionAuditRecord,
    BotOperationContext,
};
use bcs_service_api::{ActorKind, ServiceError, ServiceResult};

use super::ownership_initialization::sanitized;
use super::{MemoryBotRepo, PersistentBotRepo, Value, resolve_env};

/// Optimistic-write attempts before surfacing a retryable conflict: each
/// attempt re-validates AND re-pins its held-edge expectation to the
/// freshly re-read count, so a grant or revocation landing between the
/// read and an attempt genuinely settles into its business branch long
/// before this budget is spent (a drifted count is absorbed by the next
/// attempt, not replayed against the stale expectation until exhaustion).
const MAX_DELETION_ATTEMPTS: usize = 3;

/// Lifecycle audit record for one committed deletion (the store fills
/// resource/action/phase from the controlled vocabulary; operator columns
/// project ONLY from the typed operation context).
fn deletion_audit(
    operation: &BotOperationContext,
    resource_id: &str,
) -> BotActionAuditRecord {
    BotActionAuditRecord::new(
        uuid::Uuid::new_v4().to_string(),
        resolve_env(),
        operation.operation_id.clone(),
        operation.actor.clone(),
        BotActionResourceKind::Bot,
        resource_id.to_string(),
        BotActionKind::Delete,
        BotActionAuditPhase::Applied,
        None,
    )
}

/// The lifecycle audit INSERT of one deletion (controlled vocabulary only;
/// operator columns project from the typed actor, no request-body fields).
fn deletion_audit_statement(
    env: &str,
    operation: &BotOperationContext,
    resource_id: &str,
) -> DbStatement {
    let record = deletion_audit(operation, resource_id);
    DbStatement::with_params(
        "INSERT INTO bcs_bot_action_audits \
           (audit_id, env, operation_id, step_key, operator_kind, operator_id, \
            operator_user_id, effective_actor_id, resource_kind, resource_id, \
            action, phase, reason_code) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'bot', ?, 'delete', 'applied', NULL)",
        vec![
            Value::from(record.audit_id),
            Value::from(env),
            Value::from(record.operation_id),
            Value::from(record.step_key),
            Value::from(record.operator.operator_kind()),
            Value::from(record.operator.operator_id()),
            record.operator.operator_user_id().map(Value::from).unwrap_or(Value::Null),
            Value::from(record.operator.effective_actor_id()),
            Value::from(resource_id),
        ],
    )
}

fn now_sql(flavor: &DbSqlFlavor) -> &'static str {
    flavor.now()
}

/// The authority-withdrawal steps that must follow a Bot's tombstone in the
/// SAME commit (plan Task 5 deletion boundary; consumed by the bot
/// retirement lane AND the Provider tombstone lane after the Task 17
/// orphan-edge carry): revoke every approved role edge of the Bot,
/// terminate its PENDING transfers with the frozen decision columns, and
/// append the `delete/bot/applied` lifecycle audit row — any step failure
/// rolls the tombstone itself back.
pub(crate) fn retirement_withdrawal_steps(
    env: &str,
    flavor: &DbSqlFlavor,
    bot_id: &str,
    operation: &BotOperationContext,
) -> Vec<DbTransactionStep> {
    vec![
        // Withdraw EVERY authority role edge of the retired Bot.
        DbTransactionStep::Execute(DbStatement::with_params(
            format!(
                "UPDATE edge_grants SET status = 'revoked', gmt_modified = {now} \
                 WHERE env = ? AND to_id = ? AND status = 'approved' \
                   AND grant_kind IN ('owner', 'manager')",
                now = now_sql(flavor)
            ),
            vec![Value::from(env), Value::from(bot_id)],
        )),
        // Terminate the Bot's PENDING transfers: an acceptance racing
        // this commit loses its pending slot and can never resurrect
        // the Bot. Already-decided rows are history and stay untouched.
        DbTransactionStep::Execute(DbStatement::with_params(
            format!(
                "UPDATE bot_ownership_transfers \
                 SET status = 'invalidated', terminal_reason = 'bot_deleted', \
                     decision_actor_kind = 'system', decided_by = ?, decided_at = {now}, \
                     gmt_modified = {now} \
                 WHERE env = ? AND bot_id = ? AND status = 'pending'",
                now = now_sql(flavor)
            ),
            vec![
                Value::from(operation.actor.operator_id()),
                Value::from(env),
                Value::from(bot_id),
            ],
        )),
        // The lifecycle audit row commits with the retirement or not at
        // all: a failed audit rolls the tombstone back with it.
        DbTransactionStep::ExecuteChecked {
            statement: deletion_audit_statement(env, operation, bot_id),
            expected_affected_rows: 1,
        },
    ]
}

impl PersistentBotRepo {
    /// One-transaction Bot retirement (see the module header). Returns
    /// `Ok(true)` when this call retired the Bot, `Ok(false)` for a missing,
    /// already-retired or Human row.
    pub(super) async fn retire_bot_lifecycle_impl(
        &self,
        bot_id: &str,
        operation: &BotOperationContext,
    ) -> ServiceResult<bool> {
        let env = resolve_env();
        // The soft-delete leads the transaction: its row write lock is
        // the same first lock every authority flow takes, and
        // stop-on-no-rows turns a lost race or a missing row into the
        // empty-prefix commit (Ok(false)), never an error.
        let mut steps = vec![DbTransactionStep::Execute(
            DbStatement::with_params(
                "UPDATE bcs_bots SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP \
                 WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 \
                   AND COALESCE(actor_kind, 'bot') <> 'human'",
                vec![Value::from(bot_id), Value::from(env.as_str())],
            )
            .with_transaction_stop_on_no_rows(),
        )];
        steps.extend(retirement_withdrawal_steps(
            env.as_str(),
            &self.flavor,
            bot_id,
            operation,
        ));
        let results = self
            .db
            .transaction(steps)
            .await
            .map_err(|_| sanitized("bot retirement commit failed"))?;
        if matches!(
            results.first(),
            Some(DbTransactionStepResult::Executed(result)) if result.affected_rows == 0
        ) {
            // Someone else won the retirement, or the row was missing or a
            // Human actor row (Human rows go through delete_human_actor).
            return Ok(false);
        }
        // The same critical-no-more cleanup the plain soft delete performs:
        // drop the process-local registration/token state.
        self.bots.write().await.remove(bot_id);
        self.token_to_bot.write().await.retain(|_, value| value != bot_id);
        Ok(true)
    }

    /// One-transaction Human actor deletion (see the module header). The
    /// Human's own row locks first, related Bots lock in stable id order,
    /// and every changing statement re-proves the guards inside the write
    /// transaction, so a concurrently granted owner edge — or a manager
    /// grant racing this deletion — can never survive as an orphan edge.
    pub(super) async fn delete_human_actor_impl(
        &self,
        staff_no: &str,
        operation: &BotOperationContext,
    ) -> ServiceResult<bool> {
        let human_id = human_actor_id(staff_no);
        let env = resolve_env();
        // Validated read (fast path branches, never authority sources): the
        // Human's own availability, its live owned Bots and its held-role
        // count.
        let snapshot = self
            .db
            .query(DbStatement::with_params(
                "SELECT COALESCE(h.is_deleted, 0) AS human_deleted, \
                        (SELECT COUNT(*) FROM edge_grants o JOIN bcs_bots b \
                           ON b.bot_uuid = o.to_id AND b.env = o.env \
                          WHERE o.env = h.env AND o.from_id = h.bot_uuid \
                            AND o.grant_kind = 'owner' AND o.status = 'approved' \
                            AND COALESCE(b.is_deleted, 0) = 0) AS live_owned_bots, \
                        (SELECT COUNT(*) FROM edge_grants e \
                          WHERE e.env = h.env AND e.from_id = h.bot_uuid \
                            AND e.status = 'approved' \
                            AND e.grant_kind IN ('owner', 'manager')) AS held_role_edges \
                 FROM bcs_bots h \
                 WHERE h.bot_uuid = ? AND h.env = ?",
                vec![Value::from(human_id.as_str()), Value::from(env.as_str())],
            ))
            .await
            .map_err(|_| sanitized("human deletion read failed"))?;
        let Some(row) = snapshot.into_iter().next() else {
            return Ok(false);
        };
        if row.get_i64("human_deleted").ok().flatten().unwrap_or(0) == 1 {
            return Ok(false);
        }
        let live_owned = row
            .get_i64("live_owned_bots")
            .ok()
            .flatten()
            .unwrap_or(0);
        if live_owned > 0 {
            return Err(ServiceError::Authority(AuthorityError::Forbidden(format!(
                "user '{staff_no}' is the live owner of {live_owned} Bot(s); delete them or \
                 transfer their ownership before deleting the Human actor"
            ))));
        }
        let held_role_edges = row
            .get_i64("held_role_edges")
            .ok()
            .flatten()
            .unwrap_or(0) as u64;

        // The held-edge expectation is an optimistic pin, RE-FRESHED after
        // every drift (from the re-validation read), not a value captured
        // once at the pre-read: a grant or revocation landing between the
        // read and an attempt must converge on the next attempt instead of
        // failing the same expected-count check until the budget is spent.
        let mut expected_held_role_edges = held_role_edges;
        let mut last_conflict = None;
        for _ in 0..MAX_DELETION_ATTEMPTS {
            match self
                .delete_human_once(&human_id, operation, expected_held_role_edges, &env)
                .await
            {
                Ok(deleted) => return Ok(deleted),
                Err(HumanDeletionAttempt::Service(error)) => return Err(error),
                Err(HumanDeletionAttempt::Drift(error)) => {
                    last_conflict = Some(error);
                    // Re-validate against the new state: a newly live owned
                    // Bot turns the retry into Forbidden; a vanished Human
                    // into Ok(false); a still-consistent state retries with
                    // a FRESHLY re-read held-edge count.
                    match self.delete_human_revalidate(&human_id, staff_no).await {
                        Ok(Recheck::Retry(fresh_held_role_edges)) => {
                            expected_held_role_edges = fresh_held_role_edges;
                            continue;
                        }
                        Ok(Recheck::DeletedBySomeoneElse) => return Ok(false),
                        Ok(Recheck::LiveOwner) => {
                            return Err(ServiceError::Authority(AuthorityError::Forbidden(
                                format!(
                                    "user '{staff_no}' became the live owner of a Bot while \
                                     its deletion was in flight; the deletion was rolled back"
                                ),
                            )))
                        }
                        Err(error) => return Err(error),
                    }
                }
            }
        }
        Err(ServiceError::Authority(AuthorityError::Conflict(format!(
            "concurrent changes raced the deletion of human actor '{}'; retry: {:?}",
            human_id,
            last_conflict.map(|error| error.to_string()).unwrap_or_default()
        ))))
    }

    /// One lock → guarded-write attempt of the Human deletion.
    async fn delete_human_once(
        &self,
        human_id: &str,
        operation: &BotOperationContext,
        held_role_edges: u64,
        env: &str,
    ) -> Result<bool, HumanDeletionAttempt> {
        let lock_suffix = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };
        let results = self
            .db
            .transaction(vec![
                // 1. The Human's own availability, locked first.
                DbTransactionStep::Query(DbStatement::with_params(
                    format!(
                        "SELECT bot_uuid FROM bcs_bots \
                         WHERE bot_uuid = ? AND env = ? AND actor_kind = 'human' \
                           AND COALESCE(is_deleted, 0) = 0{lock_suffix}"
                    ),
                    vec![Value::from(human_id), Value::from(env)],
                )),
                // 2. Every related live Bot, locked in stable id order: a
                //    grant or acceptance on those rows serializes behind
                //    this commit (and vice versa).
                DbTransactionStep::Query(DbStatement::with_params(
                    format!(
                        "SELECT b.bot_uuid FROM bcs_bots b \
                         JOIN edge_grants e ON e.to_id = b.bot_uuid AND e.env = b.env \
                         WHERE e.env = ? AND e.from_id = ? AND e.status = 'approved' \
                           AND COALESCE(b.is_deleted, 0) = 0 \
                         ORDER BY b.bot_uuid{lock_suffix}"
                    ),
                    vec![Value::from(env), Value::from(human_id)],
                )),
                // 3. The soft delete of the Human row itself.
                DbTransactionStep::ExecuteChecked {
                    statement: DbStatement::with_params(
                        "UPDATE bcs_bots SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP \
                         WHERE bot_uuid = ? AND env = ? AND actor_kind = 'human' \
                           AND COALESCE(is_deleted, 0) = 0",
                        vec![Value::from(human_id), Value::from(env)],
                    ),
                    expected_affected_rows: 1,
                },
                // 4. Withdraw every edge the Human holds — guarded by the
                //    live-owner check re-proved against CURRENT rows inside
                //    the write transaction (the derived-table shape keeps
                //    MySQL's same-table update restriction satisfied). A
                //    drift in the held count rolls the whole deletion back
                //    for re-validation.
                DbTransactionStep::ExecuteChecked {
                    statement: DbStatement::with_params(
                        format!(
                            "UPDATE edge_grants SET status = 'revoked', gmt_modified = {now} \
                             WHERE env = ? AND from_id = ? AND status = 'approved' \
                               AND grant_kind IN ('owner', 'manager') \
                               AND NOT EXISTS (SELECT 1 FROM ( \
                                     SELECT 1 FROM edge_grants o JOIN bcs_bots b \
                                       ON b.bot_uuid = o.to_id AND b.env = o.env \
                                     WHERE o.env = ? AND o.from_id = ? \
                                       AND o.grant_kind = 'owner' AND o.status = 'approved' \
                                       AND COALESCE(b.is_deleted, 0) = 0) live_owner_guard)",
                            now = now_sql(&self.flavor)
                        ),
                        vec![
                            Value::from(env),
                            Value::from(human_id),
                            Value::from(env),
                            Value::from(human_id),
                        ],
                    ),
                    expected_affected_rows: held_role_edges,
                },
                // 5. The lifecycle audit row commits with the deletion.
                DbTransactionStep::ExecuteChecked {
                    statement: deletion_audit_statement(env, operation, human_id),
                    expected_affected_rows: 1,
                },
            ])
            .await
            .map_err(|error| match error {
                DbError::ConditionFailed { expected, actual } => {
                    HumanDeletionAttempt::Drift(DbError::ConditionFailed { expected, actual })
                }
                other => HumanDeletionAttempt::Service(sanitized_storage(other)),
            })?;
        if matches!(
            results.get(2),
            Some(DbTransactionStepResult::Executed(result)) if result.affected_rows == 0
        ) {
            return Ok(false);
        }
        // The same process-local cleanup the soft-delete lane performs.
        self.bots.write().await.remove(human_id);
        self.token_to_bot.write().await.retain(|_, value| value != human_id);
        Ok(true)
    }

    /// Re-read the branches after a drifted attempt. The `Retry` branch
    /// returns the FRESH held-role-edge count so the next attempt pins to
    /// the current state.
    async fn delete_human_revalidate(
        &self,
        human_id: &str,
        staff_no: &str,
    ) -> Result<Recheck, ServiceError> {
        let env = resolve_env();
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT COALESCE(h.is_deleted, 0) AS human_deleted, \
                        (SELECT COUNT(*) FROM edge_grants o JOIN bcs_bots b \
                           ON b.bot_uuid = o.to_id AND b.env = o.env \
                          WHERE o.env = h.env AND o.from_id = h.bot_uuid \
                            AND o.grant_kind = 'owner' AND o.status = 'approved' \
                            AND COALESCE(b.is_deleted, 0) = 0) AS live_owned_bots, \
                        (SELECT COUNT(*) FROM edge_grants e \
                          WHERE e.env = h.env AND e.from_id = h.bot_uuid \
                            AND e.status = 'approved' \
                            AND e.grant_kind IN ('owner', 'manager')) AS held_role_edges \
                 FROM bcs_bots h \
                 WHERE h.bot_uuid = ? AND h.env = ?",
                vec![Value::from(human_id), Value::from(env.as_str())],
            ))
            .await
            .map_err(|_| sanitized("human deletion re-validation failed"))?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(Recheck::DeletedBySomeoneElse);
        };
        if row.get_i64("human_deleted").ok().flatten().unwrap_or(0) == 1 {
            return Ok(Recheck::DeletedBySomeoneElse);
        }
        if row.get_i64("live_owned_bots").ok().flatten().unwrap_or(0) > 0 {
            return Ok(Recheck::LiveOwner);
        }
        let held_role_edges = row
            .get_i64("held_role_edges")
            .ok()
            .flatten()
            .unwrap_or(0) as u64;
        let _ = staff_no;
        Ok(Recheck::Retry(held_role_edges))
    }
}

enum Recheck {
    /// The state changed but remains deletable: one more guarded attempt,
    /// re-pinned to the freshly re-read held-edge count (the stale-count
    /// defect made every retry fail the same check instead of converging).
    Retry(u64),
    /// Another writer deleted the Human (or it vanished): report false.
    DeletedBySomeoneElse,
    /// The Human became a live owner: Forbidden, deletion rolled back.
    LiveOwner,
}

enum HumanDeletionAttempt {
    /// Drift inside the optimistic window: re-validate, never error.
    Drift(DbError),
    /// A business or infrastructure branch: surface unchanged.
    Service(ServiceError),
}

fn sanitized_storage(error: DbError) -> ServiceError {
    let _ = error;
    sanitized("human actor deletion commit failed")
}

impl MemoryBotRepo {
    /// Memory twin of [`PersistentBotRepo::retire_bot_lifecycle_impl`]:
    /// one critical section (deleted → bots → authority → token/binding
    /// maps) withdraws every role edge, invalidates the pending transfers,
    /// tombstones the registration and appends the lifecycle audit.
    pub(crate) async fn retire_bot_lifecycle_impl(
        &self,
        bot_id: &str,
        operation: &BotOperationContext,
    ) -> ServiceResult<bool> {
        if self.take_authority_write_failure().await {
            return Err(sanitized("test-injected authority write failure"));
        }
        let env = resolve_env();
        let mut deleted_ids = self.deleted_bot_ids.write().await;
        if deleted_ids.contains(bot_id) {
            return Ok(false);
        }
        let mut bots = self.bots.write().await;
        let live_row = bots.get(bot_id).map(|row| row.actor_kind.clone());
        let registered = live_row.is_some() || self.bot_info_path(bot_id).exists();
        if !registered {
            return Ok(false);
        }
        if live_row == Some(ActorKind::Human) {
            // Human rows retire only through the Human deletion lane, which
            // carries the live-owner protection.
            return Ok(false);
        }
        let mut authority = self.authority.write().await;
        for row in authority.role_rows.iter_mut() {
            if row.env == env
                && row.to_id == bot_id
                && row.status == "approved"
                && (row.grant_kind == "owner" || row.grant_kind == "manager")
            {
                row.status = "revoked".to_string();
            }
        }
        for transfer in authority.transfer_rows.iter_mut() {
            if transfer.env == env && transfer.bot_id == bot_id && transfer.status == "pending" {
                transfer.status = "invalidated".to_string();
                transfer.terminal_reason = Some("bot_deleted".to_string());
                transfer.decision_actor_kind = Some("system".to_string());
                transfer.decided_by = Some(operation.actor.operator_id().to_string());
                transfer.decided_at = Some(crate::memory::transfer::now_db_text());
                transfer.gmt_modified = crate::memory::transfer::now_db_text();
            }
        }
        authority
            .action_audit_records
            .push(deletion_audit(operation, bot_id));
        deleted_ids.insert(bot_id.to_string());
        bots.remove(bot_id);
        self.token_to_bot.write().await.retain(|_, value| value != bot_id);
        self.binding_channel_index
            .write()
            .await
            .retain(|_, value| value != bot_id);
        Ok(true)
    }

    /// Memory twin of [`PersistentBotRepo::delete_human_actor_impl`]: the
    /// single shared critical section IS the stable-ID serialization, and
    /// the live-owner guard + full edge withdrawal run inside it, so a
    /// grant racing the deletion can never leave an orphan edge.
    pub(crate) async fn delete_human_actor_impl(
        &self,
        staff_no: &str,
        operation: &BotOperationContext,
    ) -> ServiceResult<bool> {
        if self.take_authority_write_failure().await {
            return Err(sanitized("test-injected authority write failure"));
        }
        let env = resolve_env();
        let human_id = human_actor_id(staff_no);
        let mut deleted_ids = self.deleted_bot_ids.write().await;
        if deleted_ids.contains(&human_id) {
            return Ok(false);
        }
        let mut bots = self.bots.write().await;
        if !bots.contains_key(&human_id) {
            return Ok(false);
        }
        let mut authority = self.authority.write().await;
        let owns_live_bot = authority.role_rows.iter().any(|row| {
            row.env == env
                && row.from_id == human_id
                && row.grant_kind == "owner"
                && row.status == "approved"
                && bots.contains_key(&row.to_id)
        });
        if owns_live_bot {
            return Err(ServiceError::Authority(AuthorityError::Forbidden(format!(
                "user '{staff_no}' is the live owner of one or more Bots; delete them or \
                 transfer their ownership before deleting the Human actor"
            ))));
        }
        for row in authority.role_rows.iter_mut() {
            if row.env == env
                && row.from_id == human_id
                && row.status == "approved"
                && (row.grant_kind == "owner" || row.grant_kind == "manager")
            {
                row.status = "revoked".to_string();
            }
        }
        authority
            .action_audit_records
            .push(deletion_audit(operation, &human_id));
        deleted_ids.insert(human_id.clone());
        bots.remove(&human_id);
        self.token_to_bot.write().await.retain(|_, value| value != &human_id);
        self.binding_channel_index
            .write()
            .await
            .retain(|_, value| value != &human_id);
        Ok(true)
    }
}