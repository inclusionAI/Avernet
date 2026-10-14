//! Ordinary-Bot atomic retirement on [`PersistentBotRepo`]
//! (bot-authority spec §7.1).
//!
//! ONE `transaction_with_options` commits: the Bot
//! tombstone, the owner/manager edge revocation, the
//! valid-pending transfer closure (fixed system marker
//! and reason) and the `retire` audit — with the branch
//! (here: a live non-Provider Bot) re-verified under the
//! row lock. A Provider-managed Bot is refused: it must
//! go through the dedicated Provider retirement command
//! and is never chained into an ordinary one.
//!
//! The idempotent re-execution of an already-tombstoned
//! Bot returns `Ok(false)` WITHOUT a second retire audit
//! (the revocation/closure steps still run and are
//! no-ops), so a retry after a failed post-commit cleanup
//! never duplicates permission audit history. Deleted IDs
//! never resurrect through this command.

use bcs_db_api::{
    DbError, DbSqlFlavor, DbStatement, DbTransactionParam,
    DbTransactionStep, DbValue as Value,
};
use bcs_edge_permission_store::lifecycle;
use bcs_service_api::types::bot_authority::{
    AuthorityActorKind, AuthorityMutation,
};
use bcs_service_api::{ServiceError, ServiceResult};

use super::PersistentBotRepo;
use crate::ownership;
use crate::retirement as compose;

impl PersistentBotRepo {
    pub(super) async fn retire_bot_with_authority(
        &self,
        bot_id: &str,
        mutation: &AuthorityMutation,
    ) -> ServiceResult<bool> {
        let env = bcs_config::resolve_env_str();
        ownership::validate_mutation(mutation)?;
        if bot_id.trim().is_empty() {
            return Err(ServiceError::InvalidOperation {
                message: "bot_id must not be blank".into(),
                request_id: None,
            });
        }

        for _ in 0..compose::RETIRE_MAX_RETRIES {
            // Pre-read is parameter preparation only (spec §7).
            let rows = self
                .db_query(
                    "SELECT is_deleted, provider_id, ownership_version \
                     FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
                    vec![Value::from(bot_id), Value::from(env.as_str())],
                )
                .await
                .map_err(|_| compose::retire_failed())?;
            let (pre_tombstoned, provider_managed, authority_initialized) =
                match rows.as_slice() {
                    [row] => {
                        let is_deleted: i64 =
                            bcs_db_api::db_get_column(
                                row,
                                "is_deleted",
                            )
                            .map_err(|_| compose::retire_failed())?;
                        let provider_id: Option<String> = row
                            .get_string("provider_id")
                            .map_err(|_| compose::retire_failed())?;
                        let ownership_version: i64 =
                            bcs_db_api::db_get_column(
                                row,
                                "ownership_version",
                            )
                            .map_err(|_| compose::retire_failed())?;
                        (
                            is_deleted != 0,
                            provider_id.is_some(),
                            ownership_version > 0,
                        )
                    }
                    [] => {
                        return Err(ServiceError::BotNotFound(
                            bot_id.to_string(),
                        ))
                    }
                    _ => return Err(compose::retire_failed()),
                };
            if provider_managed {
                // Two distinct retirement contracts; never
                // chain an ordinary one behind a committed
                // Provider delete (spec §7.1).
                return Err(ServiceError::Conflict(
                    "provider-managed bot must be retired \
                     through the Provider retirement command"
                        .into(),
                ));
            }
            let pendings = compose::read_pending_transfers(
                self.db.as_ref(),
                &env,
                bot_id,
            )
            .await
            .map_err(compose::map_retire_tx_error)?;

            let changed = !pre_tombstoned;
            // 事务内复验 the operator's authority — only for an
            // AUTHORITY-INITIALIZED Bot (version > 0): a runtime
            // version-0 Bot carries no owner/manager register, so
            // the ordinary lifecycle delete keeps its legacy
            // contract. This PRE-READ only short-circuits an
            // already-lost edge to a stable 403 instead of
            // burning the retry budget on the in-transaction
            // guard below; the already-tombstoned retry keeps its
            // idempotent no-op outcome (no operator demand).
            if changed && authority_initialized {
                match mutation.actor.kind() {
                    AuthorityActorKind::Human => {
                        if !self
                            .operator_edge_present(&env, bot_id, mutation)
                            .await?
                        {
                            return Err(ServiceError::Forbidden(
                                format!(
                                    "operator '{}' holds no current \
                                     owner/manager edge on '{bot_id}'",
                                    mutation.actor.actor_id()
                                ),
                            ));
                        }
                    }
                    _ => {
                        return Err(ServiceError::Forbidden(
                            "ordinary retirement requires a Human operator"
                                .to_string(),
                        ));
                    }
                }
            }
            // In-transaction statement composition only — no
            // execution branches; all guard rows are consumed
            // by the final binding statement.
            let mut steps: Vec<DbTransactionStep> = Vec::new();
            let mut guard_indexes: Vec<(usize, &'static str)> =
                Vec::new();
            // MySQL takes REAL row locks on the Bot row and
            // the guard rows below (Task-3 style lock
            // suffix); SQLite is single-writer, so the
            // suffix carries no semantics there.
            let lock_suffix = match self.flavor {
                DbSqlFlavor::Mysql => " FOR UPDATE",
                DbSqlFlavor::Sqlite => "",
            };

            // 0: Bot row lock — the serialization point of
            // every authority command on this Bot (spec §7).
            steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!(
                        "SELECT 1 AS ok FROM bcs_bots \
                         WHERE bot_uuid = ? AND env = ?{lock_suffix}"
                    )
                    .as_str(),
                    vec![
                        DbTransactionParam::value(Value::from(bot_id)),
                        DbTransactionParam::value(Value::from(
                            env.as_str(),
                        )),
                    ],
                ),
            ));
            guard_indexes
                .push((steps.len() - 1, compose::GUARD_COLUMN));

            // 1: the Bot row is still in the pre-read state
            // under the lock and is NOT provider-managed.
            steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!(
                        "SELECT t.c AS ok FROM \
                         (SELECT COUNT(*) AS c FROM bcs_bots \
                          WHERE bot_uuid = ? AND env = ? \
                          AND is_deleted = ? AND provider_id IS NULL) t \
                          WHERE t.c = 1{lock_suffix}"
                    )
                    .as_str(),
                    vec![
                        DbTransactionParam::value(Value::from(bot_id)),
                        DbTransactionParam::value(Value::from(
                            env.as_str(),
                        )),
                        DbTransactionParam::value(Value::from(
                            pre_tombstoned as i64,
                        )),
                    ],
                ),
            ));
            guard_indexes
                .push((steps.len() - 1, compose::GUARD_COLUMN));

            // 2 (transition only, and only on an
            // authority-initialized Bot): the operator STILL
            // holds a live owner/manager edge — the
            // in-transaction twin of the pre-read above
            // (spec §7: 事务前读取只准备参数，权限必须在事务
            // 内再次验证). A lost edge rolls the whole attempt
            // back through the final binding statement, never
            // a tombstone.
            if changed && authority_initialized {
                steps.push(DbTransactionStep::Query(
                    DbStatement::with_transaction_params(
                        self.operator_edge_guard_sql(&lock_suffix)
                            .as_str(),
                        vec![
                            DbTransactionParam::value(Value::from(
                                env.as_str(),
                            )),
                            DbTransactionParam::value(Value::from(bot_id)),
                            DbTransactionParam::value(Value::from(
                                mutation.actor.actor_id(),
                            )),
                        ],
                    ),
                ));
                guard_indexes
                    .push((steps.len() - 1, compose::GUARD_COLUMN));
            }

            // 3: the tombstone. Only the transition writes;
            // a retry takes the changed = false path above.
            if changed {
                steps.push(DbTransactionStep::ExecuteChecked {
                    statement: lifecycle::tombstone_bot_statement(
                        &env, bot_id,
                    ),
                    expected_affected_rows: 1,
                });
            }

            // 4: owner/manager revocation + pending closure
            // (spec §3.2.7) with the fixed system marker.
            steps.push(DbTransactionStep::Execute(
                lifecycle::revoke_authority_edges_statement(
                    &env, bot_id,
                ),
            ));
            compose::push_pending_closure_steps(
                &mut steps,
                self.flavor,
                &mut guard_indexes,
                &env,
                bot_id,
                &mutation.mutation_id,
                &pendings,
            );

            // 5: the one `retire` audit row — only the
            // transition writes it (no duplicate audit on a
            // retry).
            if changed {
                compose::push_retire_audit_steps(
                    &mut steps,
                    &mut guard_indexes,
                    &env,
                    bot_id,
                    mutation,
                );
            }

            steps.push(compose::final_guard_consume(&guard_indexes));

            match self
                .db
                .transaction_with_options(
                    steps,
                    ownership::transaction_options(),
                )
                .await
            {
                Ok(_) => {
                    // Post-commit: clear the runtime/token
                    // caches AFTER the durable tombstone. The
                    // db part of soft_delete is deliberately
                    // NOT re-run — the tombstone already
                    // committed in the ONE transaction.
                    clear_retired_runtime_caches(self, bot_id).await;
                    return Ok(changed);
                }
                Err(error)
                    if compose::deterministic_rollback(&error) =>
                {
                    // A racing decision/pending change rolled
                    // the transaction; a fresh pre-read
                    // re-classifies.
                    continue;
                }
                Err(DbError::Timeout) => {
                    return Err(compose::retire_uncertain())
                }
                Err(_) => return Err(compose::retire_failed()),
            }
        }
        Err(ServiceError::InternalError(
            "bot retirement kept racing; retry budget exhausted"
                .into(),
        ))
    }

    /// Pre-read: the plain-Text existence of the Human
    /// operator's CURRENT approved owner/manager edge on this
    /// Bot (`edge_grants` subject id = `human_<user id>`,
    /// byte-exact). The transaction re-verifies the same fact
    /// under the row lock — this read only classifies a
    /// stable loss into a 403 without burning retries.
    async fn operator_edge_present(
        &self,
        env: &str,
        bot_id: &str,
        mutation: &AuthorityMutation,
    ) -> ServiceResult<bool> {
        let rows = self
            .db_query(
                &format!(
                    "SELECT COUNT(*) AS c FROM edge_grants WHERE {}",
                    self.operator_edge_presence_predicate()
                ),
                vec![
                    Value::from(env),
                    Value::from(bot_id),
                    Value::from(mutation.actor.actor_id()),
                ],
            )
            .await
            .map_err(|_| compose::retire_failed())?;
        let count = rows
            .first()
            .map(|row| bcs_db_api::db_get_column::<i64>(row, "c"))
            .transpose()
            .map_err(|_| compose::retire_failed())?
            .unwrap_or(0);
        Ok(count > 0)
    }

    /// Shared predicate for the operator-edge fact
    /// (management-edge unified shape from spec §4.1:
    /// `owner|manager`, `grant_ref_id = 0`, `rules` NULL,
    /// `approved` only; in-transaction ids byte-exact).
    fn operator_edge_predicate(&self) -> String {
        let from_eq = match self.flavor {
            DbSqlFlavor::Mysql => "CAST(from_id AS BINARY) = ?",
            DbSqlFlavor::Sqlite => "from_id = ?",
        };
        format!(
            "env = ? AND to_id = ? AND {from_eq} \
             AND grant_kind IN ('owner', 'manager') \
             AND grant_ref_id = 0 AND rules IS NULL \
             AND status = 'approved'"
        )
    }

    /// The PRE-READ presence predicate: deliberately a plain
    /// presence probe (any case posture of the binding
    /// over-matches at worst, and the in-transaction
    /// byte-exact guard resolves the true fact). Never
    /// tighten this one to the cast form — an optimistic
    /// presence pre-read only classifies stable loss into a
    /// 403; it is not the authorization gate itself.
    fn operator_edge_presence_predicate(&self) -> String {
        "env = ? AND to_id = ? AND from_id = ? \
         AND grant_kind IN ('owner', 'manager') \
         AND grant_ref_id = 0 AND rules IS NULL \
         AND status = 'approved'"
            .to_string()
    }

    /// The in-transactionCOUNT guard over the same fact
    /// (`COUNT(*) = 1` row consumed by the final binding
    /// statement; `FOR UPDATE` under MySQL like the sibling
    /// guards so a racing revoke cannot pass the RR snapshot).
    fn operator_edge_guard_sql(&self, lock_suffix: &str) -> String {
        format!(
            "SELECT t.c AS ok FROM \
               (SELECT COUNT(*) AS c FROM edge_grants WHERE {}) t \
             WHERE t.c = 1{lock_suffix}",
            self.operator_edge_predicate()
        )
    }
}

/// The process-memory half of the runtime cache clearance
/// (the same state `BotRepoPort::soft_delete` clears; the
/// database tombstone already committed above).
async fn clear_retired_runtime_caches(
    repo: &PersistentBotRepo,
    bot_id: &str,
) {
    repo.bots.write().await.remove(bot_id);
    repo.token_to_bot
        .write()
        .await
        .retain(|_, value| value != bot_id);
}
