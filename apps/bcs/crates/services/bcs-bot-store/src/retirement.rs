//! Atomic retirement composition (bot-authority spec §7.1).
//!
//! Shared pieces for this crate's TWO retire commands —
//! the ordinary Bot retirement (`PersistentBotRepo`, via
//! Registry Core) and the Provider three-branch retirement
//! (`DbProviderStore`, via Provider Core). The two commands
//! are distinct contracts and are NEVER chained one after
//! the other.
//!
//! Each composite runs ONE `transaction_with_options`:
//! the binding row first (when one exists), then the
//! physical Bot row, then the transfer rows and the
//! closures' audit rows — the unified binding → Bot →
//! transfer → Human lock order (spec §7). Pure SQL step
//! builders live in
//! `bcs-edge-permission-store::authority::lifecycle`; the
//! memory projection mirrors the exact semantics in
//! `memory::retirement`.

use bcs_db_api::{
    DbError, DbSqlFlavor, DbStatement, DbTransactionParam,
    DbTransactionStep, DbValue as Value,
};
use bcs_edge_permission_store::lifecycle;
use bcs_service_api::types::bot_authority::AuthorityMutation;
use bcs_service_api::ServiceError;

/// Same deterministic-rollback retry budget as the
/// registration/manager write paths.
pub(crate) const RETIRE_MAX_RETRIES: u32 = 3;

pub(crate) fn retire_failed() -> ServiceError {
    ServiceError::InternalError(
        "bot retirement persistence failed".into(),
    )
}

/// Deadline overrun: the plugin contract rolls the transaction
/// back, and the caller must treat the outcome as terminal and
/// never auto-retry (spec §5.2 consumer contract).
pub(crate) fn retire_uncertain() -> ServiceError {
    ServiceError::InternalError(format!(
        "{}: the retirement transaction exceeded the deadline; per \
         contract it rolled back and the caller must not auto-retry",
        bcs_service_api::application::v1::bot_authority::
            ERROR_BOT_TX_OUTCOME_UNCERTAIN
    ))
}

pub(crate) fn map_retire_tx_error(err: DbError) -> ServiceError {
    match err {
        DbError::Timeout => retire_uncertain(),
        _ => retire_failed(),
    }
}

/// Deterministic local rollbacks whose whole effect the next
/// iteration's fresh pre-read re-classifies.
/// `InvalidInput` is included deliberately: a guard row
/// that fails under the lock (0 rows) fails the
/// transaction at BINDING resolution (Task-0 s1c) — a
/// stale pre-read pendings set may only re-classify
/// through a bounded retry, it must not surface as a
/// persistence failure.
pub(crate) fn deterministic_rollback(err: &DbError) -> bool {
    matches!(
        err,
        DbError::ConditionFailed { .. }
            | DbError::InvalidInput(_)
            | DbError::Backend(_)
    )
}

/// The owner-suffixed legacy Bot shape — the SAME pure
/// rule the application layer enforces (single Home in
/// service-api `core::provider`). The
/// `allow_unbound_owner_suffixed_bot` flag only relaxes
/// the LOOKUP; this admission rule is re-checked by the
/// store and never granted by the flag alone.
pub(crate) fn is_owner_suffixed_bot_id(bot_id: &str) -> bool {
    bcs_service_api::core::provider::is_owner_suffixed_bot_id(
        bot_id,
    )
}

/// One valid pending transfer snapshotted BEFORE the transaction
/// (parameter preparation only; spec §7). The transaction
/// re-reads every row of this set BY TARGET under the row
/// locks and re-verifies its identity (target/from/version)
/// before it closes it and names it in a closure audit —
/// the pre-read alone is never authoritative for WHOSE row
/// the retirement closes.
pub(crate) struct PendingSnapshot {
    pub target_owner_actor_id: String,
    pub from_owner_actor_id: String,
    pub expected_ownership_version: u64,
}

fn pending_valid_selector() -> &'static str {
    "env = ? AND bot_id = ? AND status = 'pending' \
     AND expires_at > CURRENT_TIMESTAMP"
}

/// Pre-read the valid pendings of the Bot (parameter
/// preparation; the transaction re-reads each row by
/// target under the row locks and re-verifies its
/// snapshot before the closure commits).
pub(crate) async fn read_pending_transfers(
    db: &dyn bcs_db_api::DbPlugin,
    env: &str,
    bot_id: &str,
) -> bcs_db_api::DbResult<Vec<PendingSnapshot>> {
    let rows = db
        .query(DbStatement::with_params(
            &format!(
                "SELECT target_owner_actor_id, from_owner_actor_id, \
                 expected_ownership_version FROM bot_ownership_transfers \
                 WHERE {}",
                pending_valid_selector()
            ),
            vec![Value::from(env), Value::from(bot_id)],
        ))
        .await?;
    let mut pendings = Vec::with_capacity(rows.len());
    for row in &rows {
        pendings.push(PendingSnapshot {
            target_owner_actor_id: bcs_db_api::db_get_column(
                row,
                "target_owner_actor_id",
            )?,
            from_owner_actor_id: bcs_db_api::db_get_column(
                row,
                "from_owner_actor_id",
            )?,
            expected_ownership_version: bcs_db_api::db_get_column(
                row,
                "expected_ownership_version",
            )?,
        });
    }
    // Stable by target: the closure audit sequences must be
    // deterministic across retries.
    pendings.sort_by(|a, b| {
        a.target_owner_actor_id.cmp(&b.target_owner_actor_id)
    });
    Ok(pendings)
}

/// Guard the pending set under the row locks with the
/// transaction's OWN authoritative view of the set, then
/// write the per-row `transfer_invalidate` system audits
/// and the ONE bulk closure (fixed system marker,
/// `bot_retired` reason).
///
/// Identity is read INSIDE the transaction (review
/// Finding #8): for every pre-read pending the
/// transaction re-reads the row BY TARGET under its row
/// lock and (1) requires it to still be a VALID pending
/// and (2) requires the in-transaction identity (the
/// re-read columns) to equal the pre-read snapshot. Both
/// checks bind through guard rows (Task-0 s1c): count
/// drift (a pending decided or re-created between the
/// pre-read and the lock — the "cancel Bob → create
/// Carol" interleave keeps the COUNT at 1 while the
/// identity changes) and per-row identity drift roll the
/// retirement back and the caller retries from a fresh
/// pre-read, so the closure audit NEVER names the
/// identity of a row this transaction did not see.
pub(crate) fn push_pending_closure_steps(
    steps: &mut Vec<DbTransactionStep>,
    flavor: DbSqlFlavor,
    guard_indexes: &mut Vec<(usize, &'static str)>,
    env: &str,
    bot_id: &str,
    mutation_id: &str,
    pendings: &[PendingSnapshot],
) {
    let lock_suffix = match flavor {
        DbSqlFlavor::Mysql => " FOR UPDATE",
        DbSqlFlavor::Sqlite => "",
    };
    // The pending set must still be exactly the pre-read
    // SIZE under the row locks (a concurrent decision
    // changes the count and rolls the retirement back for
    // a fresh pre-read).
    steps.push(DbTransactionStep::Query(
        DbStatement::with_transaction_params(
            &format!(
                "SELECT t.c AS ok FROM \
                 (SELECT COUNT(*) AS c FROM bot_ownership_transfers \
                  WHERE {}) t WHERE t.c = ?{}",
                pending_valid_selector(),
                lock_suffix
            ),
            vec![
                DbTransactionParam::value(Value::from(env)),
                DbTransactionParam::value(Value::from(bot_id)),
                DbTransactionParam::value(Value::from(
                    pendings.len() as u64,
                )),
            ],
        ),
    ));
    guard_indexes.push((steps.len() - 1, GUARD_COLUMN));

    // Row locks AFTER the Bot row (binding → Bot →
    // transfer): per pending, re-read the row BY TARGET
    // inside the transaction — this read, not the
    // pre-read, is the identity the closure audits write.
    for (index, pending) in pendings.iter().enumerate() {
        steps.push(DbTransactionStep::Query(
            DbStatement::with_transaction_params(
                &format!(
                    "SELECT target_owner_actor_id AS closure_target, \
                            from_owner_actor_id AS closure_from, \
                            expected_ownership_version AS closure_version \
                     FROM bot_ownership_transfers \
                     WHERE {} AND target_owner_actor_id = ?{}",
                    pending_valid_selector(),
                    lock_suffix
                ),
                vec![
                    DbTransactionParam::value(Value::from(env)),
                    DbTransactionParam::value(Value::from(bot_id)),
                    DbTransactionParam::value(Value::from(
                        pending.target_owner_actor_id.as_str(),
                    )),
                ],
            ),
        ));
        let read_step = steps.len() - 1;
        // Identity guard: the in-transaction row must equal
        // the pre-read snapshot (target/from/version). A
        // re-read that lost the row fails at the binding of
        // THIS guard (0 rows to resolve); a row whose
        // identity changed fails at this guard's 0-row
        // result — both are deterministic local rollbacks
        // (see `deterministic_rollback`).
        steps.push(DbTransactionStep::Query(
            DbStatement::with_transaction_params(
                "SELECT w.p0 AS ok FROM \
                 (SELECT ? AS p0, ? AS p1, ? AS p2, ? AS p3, \
                         ? AS p4, ? AS p5) w \
                 WHERE w.p0 = w.p1 AND w.p2 = w.p3 \
                   AND w.p4 = w.p5 AND w.p0 IS NOT NULL",
                vec![
                    DbTransactionParam::query_result(
                        read_step,
                        0,
                        "closure_target",
                    ),
                    DbTransactionParam::value(Value::from(
                        pending.target_owner_actor_id.as_str(),
                    )),
                    DbTransactionParam::query_result(
                        read_step,
                        0,
                        "closure_from",
                    ),
                    DbTransactionParam::value(Value::from(
                        pending.from_owner_actor_id.as_str(),
                    )),
                    DbTransactionParam::query_result(
                        read_step,
                        0,
                        "closure_version",
                    ),
                    DbTransactionParam::value(Value::from(
                        pending.expected_ownership_version,
                    )),
                ],
            ),
        ));
        guard_indexes.push((steps.len() - 1, GUARD_COLUMN));

        // Closure audit: the FIXED system decision marker;
        // the real initiator is the separate `retire` audit
        // row (§4.4). The subject comes from the
        // in-transaction re-read; the JSON states carry the
        // pre-read snapshot the identity guard above just
        // proved equal to that re-read.
        steps.push(DbTransactionStep::Execute(
            DbStatement::with_transaction_params(
                "INSERT INTO bot_authority_changes \
                 (env, bot_id, mutation_id, sequence, action, \
                  actor_kind, actor_id, subject_id, before_state, \
                  after_state) \
                 VALUES (?, ?, ?, ?, 'transfer_invalidate', \
                         'system', ?, ?, ?, ?)",
                vec![
                    DbTransactionParam::value(Value::from(env)),
                    DbTransactionParam::value(Value::from(bot_id)),
                    DbTransactionParam::value(Value::from(
                        mutation_id,
                    )),
                    DbTransactionParam::value(Value::from(
                        1 + index as u64,
                    )),
                    DbTransactionParam::value(Value::from(
                        lifecycle::system_transfer_close_actor(),
                    )),
                    DbTransactionParam::query_result(
                        read_step,
                        0,
                        "closure_target",
                    ),
                    DbTransactionParam::value(Value::from(
                        pending_before_state(pending),
                    )),
                    DbTransactionParam::value(Value::from(
                        pending_after_state(pending),
                    )),
                ],
            ),
        ));
    }

    steps.push(DbTransactionStep::Execute(
        lifecycle::close_pending_transfers_statement(env, bot_id),
    ));
}

/// The guarded pre-state JSON of one pending closure
/// audit (mirrors `lifecycle::retirement_closure_states`).
fn pending_before_state(pending: &PendingSnapshot) -> String {
    lifecycle::retirement_closure_states(
        &pending.from_owner_actor_id,
        pending.expected_ownership_version,
    )
    .0
}

/// The guarded after-state JSON of one pending closure
/// audit.
fn pending_after_state(pending: &PendingSnapshot) -> String {
    lifecycle::retirement_closure_states(
        &pending.from_owner_actor_id,
        pending.expected_ownership_version,
    )
    .1
}

/// Write the one `retire` audit row (the real mutation actor;
/// no credentials ever enter) plus its commit guard query.
/// The retired Bot is the audit subject; the audit table's
/// `(env, mutation_id, sequence)` key makes a duplicate
/// mutation id fail the transaction deterministically.
pub(crate) fn push_retire_audit_steps(
    steps: &mut Vec<DbTransactionStep>,
    guard_indexes: &mut Vec<(usize, &'static str)>,
    env: &str,
    bot_id: &str,
    mutation: &AuthorityMutation,
) {
    steps.push(DbTransactionStep::ExecuteChecked {
        statement: lifecycle::retire_audit_statement(
            env,
            bot_id,
            &mutation.mutation_id,
            mutation.actor.kind().as_str(),
            &mutation.actor.actor_id(),
        ),
        expected_affected_rows: 1,
    });
    steps.push(DbTransactionStep::Query(
        lifecycle::initialize_audit_guard_statement(
            env,
            bot_id,
            &mutation.mutation_id,
        ),
    ));
    guard_indexes.push((steps.len() - 1, "sequence"));
}

/// Final guard-consumption statement: every guard row above
/// must bind; one missing row rolls the whole retirement back
/// (Task-0 s1c). Indexes are absolute within the composed
/// transaction.
pub(crate) fn final_guard_consume(
    guard_indexes: &[(usize, &'static str)],
) -> DbTransactionStep {
    let mut sql = String::from("SELECT ");
    let mut params: Vec<DbTransactionParam> = Vec::new();
    for (offset, (index, column)) in guard_indexes.iter().enumerate() {
        if offset > 0 {
            sql.push_str(", ");
        }
        sql.push_str(&format!("? AS t{offset}"));
        params.push(DbTransactionParam::query_result(*index, 0, *column));
    }
    DbTransactionStep::Query(
        DbStatement::with_transaction_params(sql.as_str(), params),
    )
}

/// Marker column name of the shared count guards.
pub(crate) const GUARD_COLUMN: &'static str = "ok";
