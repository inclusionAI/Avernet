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
use bcs_service_api::types::bot_authority::AuthorityMutation;
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
                    "SELECT is_deleted, provider_id FROM bcs_bots \
                     WHERE bot_uuid = ? AND env = ?",
                    vec![Value::from(bot_id), Value::from(env.as_str())],
                )
                .await
                .map_err(|_| compose::retire_failed())?;
            let (pre_tombstoned, provider_managed) =
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
                        (is_deleted != 0, provider_id.is_some())
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

            // 2: the tombstone. Only the transition writes;
            // a retry takes the changed = false path above.
            if changed {
                steps.push(DbTransactionStep::ExecuteChecked {
                    statement: lifecycle::tombstone_bot_statement(
                        &env, bot_id,
                    ),
                    expected_affected_rows: 1,
                });
            }

            // 3: owner/manager revocation + pending closure
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

            // 4: the one `retire` audit row — only the
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
