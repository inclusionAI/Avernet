//! Trusted ownership sync write command (2026-10-29 design §5.1).
//!
//! One bot-row-locked transaction: swap the bot's owner edge to
//! the requested live Human; the previous owner is FULLY removed
//! (owner edge revoked, any manager edge revoked — unlike the
//! transfer-accept demotion, 设计 §2); the target's manager edge
//! is stripped (owner 不占 manager 名额); `ownership_version`
//! CAS-increments exactly once; a VALID pending transfer closes
//! as `invalidated` with the fixed system marker and the frozen
//! `ownership_sync` reason; one `ownership_sync` audit row (plus
//! one `transfer_invalidate` row when a pending closed) commits
//! with the data. Missing bots are tolerated no-ops; version-0
//! hard-rejects; corrupt owner state is the integrity error.

use std::time::Duration;

use tracing::warn;

use bcs_db_api::{
    DbStatement, DbTransactionOptions, DbTransactionParam,
    DbTransactionStep, DbValue,
};
use bcs_service_api::types::bot_authority::{
    human_user_id_of_actor, AuthorityActorKind, OwnershipChange,
    OwnershipSync,
};
use bcs_service_api::ServiceResult;

use super::audit::{
    ACTION_OWNERSHIP_SYNC, ACTION_TRANSFER_INVALIDATE, CLOSING_SYSTEM_ACTOR,
    REASON_OWNERSHIP_SYNC,
};
use super::managers::SyncBotState;
use super::transfers::transfer_audit_state;
use super::transfers_support::{
    gmt_modified_now, lock_suffix, target_id_eq, TransferAuditDraft, TransferTxBuilder,
};
use super::DbBotAuthorityStore;

const TX_DEADLINE: Duration = Duration::from_secs(10);
const MAX_RETRIES: u32 = 3;

/// Frozen audit state JSON for the swap row (key order pinned by
/// the contract harness): the owner actor + the version it held.
fn ownership_audit_state(owner_actor: &str, version: u64) -> String {
    format!("{{\"owner\":\"{owner_actor}\",\"version\":{version}}}")
}

impl DbBotAuthorityStore {
    /// Trusted ownership sync (2026-10-29 design §5.1).
    pub(super) async fn sync_ownership_impl(
        &self,
        command: OwnershipSync,
    ) -> ServiceResult<OwnershipChange> {
        // sync 是可信应用专用命令; Humans use the transfer commands.
        if command.mutation.actor.kind() != AuthorityActorKind::Service {
            return Err(super::operator_forbidden_error(
                "ownership sync is trusted-service only; Human operators use the ownership transfer commands"
                    .to_string(),
            ));
        }
        let op = "authority_sync_ownership";
        let bot_id = command.bot_id.as_str();
        // 目标 actor 形态（Repo 层输入已是 Core 组装的 actor id；
        // 形状/存续校验推迟到幂等判定之后——设计 §5.1 步骤 3→4）。
        let target = command.owner_user_id.clone();
        // Lenient target classification (设计 §2): a missing or
        // tombstoned bot is an unchanged no-op with a warn log
        // (no audit, no state change); a version-0 live bot
        // still hard-rejects.
        match self.sync_bot_state(bot_id).await? {
            SyncBotState::Missing => {
                warn!(
                    operation = op,
                    env = %self.env(),
                    bot_id = bot_id,
                    mutation_id = %command.mutation.mutation_id,
                    "ownership sync target bot is missing; returning unchanged"
                );
                return Ok(OwnershipChange::unchanged(bot_id));
            }
            SyncBotState::Uninitialized => {
                return Err(super::not_initialized_error(bot_id));
            }
            SyncBotState::Ready => {}
        }

        // Per-attempt diagnostics so the budget-exhausted error
        // names WHY each try conflicted (no secrets / caller
        // data; the underlying DbError only).
        let mut conflicts: Vec<String> = Vec::new();
        for attempt in 0..=MAX_RETRIES {
            // Preparation reads (re-verified by in-tx guards).
            let owner = self.pre_read_owner(bot_id).await?;
            // A legit retirement/tombstone can commit between the
            // lenient classification and this attempt: the SAME
            // live-row lens now reads `None` — re-enter the
            // lenient leg (设计 §2: deleted/missing bot → no-op),
            // never a panic in a write command.
            let Some((_, version)) = self.bot_ownership_state(bot_id).await? else {
                warn!(
                    operation = op,
                    env = %self.env(),
                    bot_id = bot_id,
                    mutation_id = %command.mutation.mutation_id,
                    "ownership sync target bot was retired or tombstoned \
                     before the transaction; returning unchanged"
                );
                return Ok(OwnershipChange::unchanged(bot_id));
            };
            // 幂等判定先于一切目标校验（设计 §5.1 步骤 3）：
            // 重放当前 owner 永不写状态，也不依赖目标的存活性。
            if target == owner {
                // Corrupt non-`human_` owner edge: the pinned
                // integrity error (owner shape, same message lens
                // as the read paths), never a write-command panic.
                let owner_user = human_user_id_of_actor(&owner).ok_or_else(|| {
                    super::owner_integrity_error(format!(
                        "owner edge subject '{owner}' is not a Human actor id"
                    ))
                })?;
                return Ok(OwnershipChange::unchanged_with_owner(bot_id, &owner_user));
            }
            // 目标两阶段判定（设计 §5.1 步骤 4；只在真实切换
            // 可能发生时执行）：
            // 4a/4b 合并为 Repo 层的同一 human_unknown 出口——
            // actor 形态须剥离回非空无空白的 user id，且其
            // Human 行须在事务内复验存活。
            let Some(target_user) = human_user_id_of_actor(&target)
                .filter(|user| !user.is_empty() && user.trim() == *user
                    && !user.chars().any(char::is_whitespace))
            else {
                return Err(super::human_unknown_error(format!(
                    "ownership sync target '{target}' is not a valid Human user id"
                )));
            };
            if target == bot_id || self.live_human_count(&target).await? != 1 {
                return Err(super::human_unknown_error(format!(
                    "ownership sync target '{target}' is not a live Human actor"
                )));
            }
            // Edge-state classification (same four predicates as
            // the accept annex, transfers.rs:1866-1924).
            let (t_owner_approved, t_owner_revoked, t_owner_other) =
                self.pre_read_edge_state(bot_id, &target, "owner").await?;
            if t_owner_approved > 0 || t_owner_other > 0 {
                return Err(super::owner_integrity_error(format!(
                    "bot {bot_id} target '{target}' carries an unexpected \
                     owner edge state"
                )));
            }
            let restore_target_owner = t_owner_revoked > 0;
            let (t_mgr_approved, _t_mgr_revoked, t_mgr_other) =
                self.pre_read_edge_state(bot_id, &target, "manager").await?;
            if t_mgr_approved > 1 || t_mgr_other > 0 {
                return Err(super::owner_integrity_error(format!(
                    "bot {bot_id} target '{target}' carries an unexpected \
                     manager edge state"
                )));
            }
            let (o_mgr_approved, _o_mgr_revoked, o_mgr_other) =
                self.pre_read_edge_state(bot_id, &owner, "manager").await?;
            if o_mgr_other > 0 {
                return Err(super::owner_integrity_error(format!(
                    "bot {bot_id} old owner '{owner}' carries an unexpected \
                     manager edge shape"
                )));
            }
            // The bot's single valid pending row (unique
            // constraint; pre-read identity re-guarded in-tx).
            let pending = self.pre_read_bot_pending(bot_id).await?;

            let steps = self.build_ownership_sync_tx(
                &command, bot_id, &owner, version, &target,
                restore_target_owner, t_mgr_approved, o_mgr_approved,
                pending.as_ref(),
            );
            match self
                .db
                .transaction_with_options(
                    steps,
                    DbTransactionOptions::with_deadline(TX_DEADLINE),
                )
                .await
            {
                Ok(_) => {
                    // Corrupt non-`human_` owner shape: the swap
                    // already committed, so surface the pinned
                    // integrity code — a post-commit panic would be
                    // an undefined-outcome hole.
                    let Some(old_user) = human_user_id_of_actor(&owner) else {
                        return Err(super::owner_integrity_error(format!(
                            "owner edge subject '{owner}' is not a Human actor id"
                        )));
                    };
                    return Ok(OwnershipChange {
                        bot_id: bot_id.to_string(),
                        owner_user_id: Some(target_user),
                        previous_owner_user_id: Some(old_user),
                        changed: true,
                    });
                }
                Err(err) if super::managers::is_retryable(&err) => {
                    conflicts.push(format!("attempt {attempt} ownership_sync_tx: {err}"));
                    continue;
                }
                Err(err) => return Err(super::map_tx_error(op, err)),
            }
        }
        Err(super::authority_db_error(
            op,
            bcs_db_api::DbError::Backend(format!(
                "ownership sync kept conflicting; retry budget \
                 exhausted; conflicts: [{conflicts:?}]"
            )),
        ))
    }

    /// Pre-read the bot's single valid pending row
    /// (`expires_at > now` — an already-expired pending keeps
    /// its read-time expired projection, mirroring retirement;
    /// identity re-verified by the in-tx guards).
    /// A pending EXPIRING between this pre-read and the in-tx
    /// guard stays stored-`pending` (the sync only closes a
    /// VALID pending); the failed guard sends the attempt back
    /// here, and the expired pending is lazily expiry-classed
    /// by the later transfer reads — the closure semantics
    /// self-heal.
    /// Returns `(target, from_owner, expected_version)`.
    async fn pre_read_bot_pending(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<(String, String, u64)>> {
        let rows = self
            .query(
                "authority_ownership_sync_pending",
                DbStatement::with_params(
                    format!(
                        "SELECT target_owner_actor_id, from_owner_actor_id, \
                          expected_ownership_version FROM bot_ownership_transfers \
                         WHERE env = ? AND bot_id = ? AND status = 'pending' \
                           AND expires_at > {}",
                        self.db_now_utc()
                    )
                    .as_str(),
                    vec![self.env_param(), DbValue::from(bot_id)],
                ),
            )
            .await?;
        match rows.len() {
            0 => Ok(None),
            1 => {
                let row = &rows[0];
                Ok(Some((
                    crate::required_string(row, "target_owner_actor_id")?,
                    crate::required_string(row, "from_owner_actor_id")?,
                    crate::required_u64(row, "expected_ownership_version")?,
                )))
            }
            count => Err(super::owner_integrity_error(format!(
                "bot {bot_id} has {count} valid pending transfers"
            ))),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn build_ownership_sync_tx(
        &self,
        command: &OwnershipSync,
        bot_id: &str,
        owner: &str,
        version: u64,
        target: &str,
        restore_target_owner: bool,
        target_mgr_approved: u64,
        old_mgr_approved: u64,
        pending: Option<&(String, String, u64)>,
    ) -> Vec<DbTransactionStep> {
        let mut builder = TransferTxBuilder::new(self, bot_id, true);
        let lock = lock_suffix(self);
        let now = self.db_now_utc();
        let modified = gmt_modified_now(self);
        // ABA CAS: pre-read owner/version must hold in-tx
        // (same eq-guard pair as transfers.rs:1745-1752).
        builder.push_eq_guard(vec![
            DbTransactionParam::value(DbValue::from(owner)),
            builder.owner_ref(),
        ]);
        builder.push_eq_guard(vec![
            DbTransactionParam::value(DbValue::from(version)),
            builder.version_ref(),
        ]);
        // Target Human survival under the row lock
        // (transfers.rs:1866-1905 verbatim shape).
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM bcs_bots \
                 WHERE {} AND actor_kind = 'human' AND env = ? \
                   AND COALESCE(is_deleted, 0) = 0{lock}",
                self.bot_id_eq(),
            ),
            vec![
                DbTransactionParam::value(DbValue::from(target)),
                DbTransactionParam::value(self.env_param()),
            ],
            1,
        );
        // ---- edge-state guards (transfers.rs:1926-2014 pattern) ----
        if restore_target_owner {
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM edge_grants \
                     WHERE env = ? AND {} AND grant_kind = 'owner' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'revoked' AND {}{lock}",
                    self.to_id_eq(), self.from_id_eq(),
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
                     WHERE env = ? AND {} AND grant_kind = 'owner' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND {}{lock}",
                    self.to_id_eq(), self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
                0,
            );
        }
        if target_mgr_approved == 1 {
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM edge_grants \
                     WHERE env = ? AND {} AND grant_kind = 'manager' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'approved' AND {}{lock}",
                    self.to_id_eq(), self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
                1,
            );
        }
        if old_mgr_approved == 1 {
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM edge_grants \
                     WHERE env = ? AND {} AND grant_kind = 'manager' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'approved' AND {}{lock}",
                    self.to_id_eq(), self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(owner)),
                ],
                1,
            );
        }
        // Pending identity re-guard (mirror run_close_tx's
        // closure guard at transfers.rs:1651-1665 — the SAME
        // valid pending must still be there).
        if let Some((pending_target, _from_owner, _expected_version)) = pending {
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                     WHERE env = ? AND bot_id = ? AND {target_pred} \
                       AND status = 'pending' AND expires_at > {now}{lock}",
                    target_pred = target_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(pending_target.as_str())),
                ],
                1,
            );
            // (no separate identity eq-guard needed: the unique
            // pending-per-bot constraint plus the target-bound
            // count guard above pin the row's identity; the
            // from_owner/expected_version values flow into the
            // audit states below.)
        } else {
            // No VALID pending at pre-read: NONE may appear before
            // commit. A transfer initiation that commits between
            // the pre-read and this transaction touches neither
            // owner nor version, so the guards above would not
            // catch it — this zero-live-pending guard fails that
            // attempt instead, and the retry re-reads the pending
            // and closes it in the SAME transaction as the swap
            // (设计 §2 closure mandate; request-path zero-pending
            // guard precedent, transfers.rs:1298-1312, and
            // retirement's pre-read-size pin, bcs-bot-store
            // retirement.rs:186-204).
            builder.push_count_guard(
                format!(
                    "SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                     WHERE env = ? AND bot_id = ? AND status = 'pending' \
                       AND expires_at > {now}{lock}",
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                ],
                0,
            );
        }
        // ---- ordered writes ----
        // 1. Old owner edge revoked (frees approved-owner
        //    uniqueness). NO manager restore for the old owner —
        //    ownership sync removes it FULLY (设计 §2, unlike accept).
        builder.add_checked_write(
            format!(
                "UPDATE edge_grants \
                 SET status = 'revoked', gmt_modified = {modified} \
                 WHERE env = ? AND {} AND grant_kind = 'owner' \
                   AND grant_ref_id = 0 AND rules IS NULL \
                   AND status = 'approved' AND {}",
                self.to_id_eq(), self.from_id_eq(),
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
                builder.owner_ref(),
            ],
        );
        // 2. Target owner edge created/restored (transfers.rs:2037-2074).
        if restore_target_owner {
            builder.add_checked_write(
                format!(
                    "UPDATE edge_grants \
                     SET status = 'approved', gmt_modified = {modified} \
                     WHERE env = ? AND {} AND grant_kind = 'owner' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'revoked' AND {}",
                    self.to_id_eq(), self.from_id_eq(),
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
        // 3. Version +1 CAS (transfers.rs:2078-2096 verbatim shape).
        builder.add_checked_write(
            format!(
                "UPDATE bcs_bots \
                 SET ownership_version = ownership_version + 1, \
                     gmt_modified = {modified} \
                 WHERE {} AND env = ? AND ownership_version = ?",
                self.bot_id_eq(),
            ),
            vec![
                builder.bot_ref(),
                DbTransactionParam::value(self.env_param()),
                builder.version_ref(),
            ],
        );
        // 4. The new owner's manager edge is stripped.
        if target_mgr_approved == 1 {
            builder.add_checked_write(
                format!(
                    "UPDATE edge_grants \
                     SET status = 'revoked', gmt_modified = {modified} \
                     WHERE env = ? AND {} AND grant_kind = 'manager' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'approved' AND {}",
                    self.to_id_eq(), self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(target)),
                ],
            );
        }
        // 5. The old owner's manager edge goes too (full removal).
        if old_mgr_approved == 1 {
            builder.add_checked_write(
                format!(
                    "UPDATE edge_grants \
                     SET status = 'revoked', gmt_modified = {modified} \
                     WHERE env = ? AND {} AND grant_kind = 'manager' \
                       AND grant_ref_id = 0 AND rules IS NULL \
                       AND status = 'approved' AND {}",
                    self.to_id_eq(), self.from_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(owner)),
                ],
            );
        }
        // 6. Valid pending closes as invalidated (system marker),
        //    mirroring run_close_tx's UPDATE (transfers.rs:1673-1695)
        //    with the frozen ownership_sync reason.
        if let Some((pending_target, _from_owner, _expected_version)) = pending {
            builder.add_checked_write(
                format!(
                    "UPDATE bot_ownership_transfers \
                     SET status = 'invalidated', decided_at = {now}, \
                         decided_by_kind = 'system', \
                         decided_by_id = '{CLOSING_SYSTEM_ACTOR}', \
                         reason = '{REASON_OWNERSHIP_SYNC}', \
                         updated_at = {now}, gmt_modified = {modified} \
                     WHERE env = ? AND bot_id = ? AND {} \
                       AND status = 'pending'",
                    target_id_eq(),
                ),
                vec![
                    DbTransactionParam::value(self.env_param()),
                    builder.bot_ref(),
                    DbTransactionParam::value(DbValue::from(pending_target.as_str())),
                ],
            );
        }
        // 7. Audits (same mutation; swap row seq 0, closure
        //    row seq 1 — deterministic across retries).
        self.push_transfer_audit(
            &mut builder,
            bot_id,
            &command.mutation.mutation_id,
            TransferAuditDraft {
                sequence: 0,
                action: ACTION_OWNERSHIP_SYNC,
                actor_kind: command.mutation.actor.kind().as_str().to_string(),
                actor_id: command.mutation.actor.actor_id(),
                subject: target.to_string(),
                before_state: Some(ownership_audit_state(owner, version)),
                after_state: Some(ownership_audit_state(target, version + 1)),
            },
        );
        if let Some((pending_target, from_owner, expected_version)) = pending {
            self.push_transfer_audit(
                &mut builder,
                bot_id,
                &command.mutation.mutation_id,
                TransferAuditDraft {
                    sequence: 1,
                    action: ACTION_TRANSFER_INVALIDATE,
                    actor_kind: AuthorityActorKind::System.as_str().to_string(),
                    actor_id: CLOSING_SYSTEM_ACTOR.to_string(),
                    subject: pending_target.clone(),
                    before_state: Some(transfer_audit_state(
                        "pending", from_owner, *expected_version,
                    )),
                    after_state: Some(transfer_audit_state(
                        "invalidated", from_owner, *expected_version,
                    )),
                },
            );
        }
        // 8. Post-state guard: the bot still owns exactly one
        //    approved owner edge — the target's
        //    (transfers.rs:2166-2188 verbatim shape).
        builder.push_count_guard(
            format!(
                "SELECT COUNT(*) AS c FROM edge_grants \
                 WHERE env = ? AND {} AND grant_kind = 'owner' \
                   AND grant_ref_id = 0 AND rules IS NULL \
                   AND status = 'approved'",
                self.to_id_eq(),
            ),
            vec![
                DbTransactionParam::value(self.env_param()),
                builder.bot_ref(),
            ],
            1,
        );
        builder.finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use bcs_db_api::DbSqlFlavor;
    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_service_api::types::bot_authority::{
        AuthorityActor, AuthorityMutation,
    };

    #[test]
    fn frozen_spellings() {
        assert_eq!(ACTION_OWNERSHIP_SYNC, "ownership_sync");
        assert_eq!(REASON_OWNERSHIP_SYNC, "ownership_sync");
        assert_eq!(
            ownership_audit_state("human_a", 3),
            "{\"owner\":\"human_a\",\"version\":3}"
        );
    }

    /// Test-only store for the tx builder: the builder is a pure
    /// step assembler (no statement executes), so a bare
    /// in-memory plugin instance suffices.
    fn sqlite_store_for_builder() -> DbBotAuthorityStore {
        DbBotAuthorityStore::new(
            std::sync::Arc::new(
                LocalSqliteDbPlugin::new().expect("local sqlite"),
            ),
            DbSqlFlavor::Sqlite,
            "env-builder".to_string(),
        )
    }

    fn service_command() -> OwnershipSync {
        OwnershipSync {
            bot_id: "bot_builder".to_string(),
            owner_user_id: "human_next".to_string(),
            mutation: AuthorityMutation {
                mutation_id: "mutation-builder".to_string(),
                actor: AuthorityActor::Service { app_id: 7 },
            },
        }
    }

    /// The builder's pending-table guard query steps (writes and
    /// audits are `ExecuteChecked` steps and never match).
    fn pending_guard_steps(steps: &[DbTransactionStep]) -> Vec<&DbStatement> {
        steps
            .iter()
            .filter_map(|step| match step {
                DbTransactionStep::Query(statement)
                    if statement
                        .sql()
                        .contains("FROM bot_ownership_transfers") =>
                {
                    Some(statement)
                }
                _ => None,
            })
            .collect()
    }

    /// Critical-1 pin: the None leg (no valid pending at
    /// pre-read) must still guard the transfer table — zero
    /// live pendings may exist at commit, so a concurrent
    /// transfer initiation (owner/version untouched by it)
    /// cannot survive under the swapped ownership.
    #[test]
    fn pending_none_leg_pins_the_zero_live_pending_guard() {
        let store = sqlite_store_for_builder();
        let steps = store.build_ownership_sync_tx(
            &service_command(), "bot_builder", "human_own", 1,
            "human_next", false, 0, 0, None,
        );
        let guards = pending_guard_steps(&steps);
        assert_eq!(guards.len(), 1, "exactly the zero-count guard");
        let guard = guards[0];
        let sql = guard.sql();
        assert!(sql.contains("status = 'pending'"), "{sql}");
        assert!(sql.contains("expires_at > "), "{sql}");
        assert!(sql.contains("WHERE f.c = ?"), "count-guard envelope: {sql}");
        assert!(
            !sql.contains("target_owner_actor_id"),
            "the zero guard binds no target: {sql}"
        );
        assert_eq!(guard.params().len(), 3);
        assert_eq!(guard.params()[0], DbValue::from("env-builder"));
        assert_eq!(guard.params()[1], DbValue::Null, "bot id stays a tx-bound reference");
        assert_eq!(guard.params()[2], DbValue::from(0u64), "zero live pendings");
    }

    /// Contrast pin: the Some leg keeps the target-bound
    /// identity guard (count 1) the contract harness exercises.
    #[test]
    fn pending_some_leg_pins_the_target_bound_guard() {
        let store = sqlite_store_for_builder();
        let pending = ("human_t1".to_string(), "human_own".to_string(), 1u64);
        let steps = store.build_ownership_sync_tx(
            &service_command(), "bot_builder", "human_own", 1,
            "human_next", false, 0, 0, Some(&pending),
        );
        let guards = pending_guard_steps(&steps);
        assert_eq!(guards.len(), 1, "exactly the identity guard");
        let guard = guards[0];
        assert!(
            guard.sql().contains("target_owner_actor_id = ?"),
            "{}", guard.sql()
        );
        assert_eq!(guard.params().len(), 4);
        assert_eq!(guard.params()[2], DbValue::from("human_t1"));
        assert_eq!(
            guard.params()[3], DbValue::from(1u64),
            "exactly the pre-read pending"
        );
    }
}
