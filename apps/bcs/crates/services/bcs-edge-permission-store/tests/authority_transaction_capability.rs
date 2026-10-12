//! Task 2: authority transaction expressibility against the REAL DbPlugin.
//!
//! Spec `docs/superpowers/specs/2026-09-18-bot-manage-permission-design.md`
//! §10 (transactions/concurrency) and §12.5 (same-store audit atomicity);
//! brief 2026-10-08 step 3. The proofs required by the plan:
//!
//! 1. the minimal plugin call shape
//!    `[Query(lock), ExecuteChecked{changing CAS, expected 1}, Execute(audit)]`
//!    must commit real business + audit writes in ONE transaction
//!    (`manager_revoke_transaction_commits_cas_audit_atomically`);
//! 2. identical conditions inside conditional SQL, with the no-change branch
//!    using stop-on-no-rows "committed prefix" semantics — a committed
//!   invalidation is a normal domain result, not an ExecuteChecked failure;
//!    a genuinely invalid pending stays untouched and the flow continues to
//!    the accept branch (`owner_changed_invalidation_commits_prefix`);
//! 3. the §10.2 owner_changed invalidation is persisted by the same committed
//!    prefix pattern and releases the pending slot
//!    (`invalidated_transfer_releases_pending_slot`);
//! 4. query-result binding: the FOR UPDATE read feeds the CAS guard of the
//!    next step inside the same transaction
//!    (`cas_bump_binds_locked_read_into_where_guard`);
//! 5. a real write failure in the audit step rolls back the SAME-transaction
//!    business write (`audit_failure_rolls_back_business_write`);
//! 6. a genuinely failing conditional (ExecuteChecked mismatch) rolls back
//!    earlier writes too (`execute_checked_mismatch_rollbacks_earlier_write`).
//!
//! The database is a real SQLite built from the FULL migration chain, so the
//! pending/owner slot constraints are the ones the migration created. The
//! SQLite plugin reserves the write lock (IMMEDIATE) for any transaction
//! containing writes, which is the dialect's path for serializing per-Bot
//! writes; MySQL additionally uses `SELECT ... FOR UPDATE` (covered by the
//! ignored MySQL conformance in crate `bcs`).

use bcs_db_api::{
    DbError, DbPlugin, DbStatement, DbTransactionParam, DbTransactionStep, DbTransactionStepResult,
    DbValue, db_get_column,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{DIRECT_SOURCE_ID, DIRECT_SOURCE_KIND, OWNER_SOURCE_ID, OWNER_SOURCE_KIND};

// The full versioned migration runner, pulled in the same way as the
// message-store conformance tests so the authority schema is exactly what
// production startup builds (full chain, not an ad-hoc DDL subset).
#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

const ENV: &str = "local";
const BOT_X: &str = "bot-x";
const OWNER_A: &str = "human_user-a";
const OWNER_B: &str = "human_user-b";

async fn migrated_db() -> LocalSqliteDbPlugin {
    let db = LocalSqliteDbPlugin::new().expect("open local sqlite");
    migrations::run_sqlite_migrations(&db)
        .await
        .expect("apply the full sqlite migration chain");
    db
}

/// SQLite dialect of the §10 per-Bot serialization boundary: the plugin
/// reserves the write lock (IMMEDIATE) for transactions that contain writes,
/// so this read is the in-lock current read of the precise Bot row.
fn lock_bot_statement(bot_id: &str, env: &str) -> DbStatement {
    DbStatement::with_params(
        "SELECT bot_uuid, name, ownership_version FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
        vec![DbValue::from(bot_id), DbValue::from(env)],
    )
}

async fn seed_bot_with_owner(db: &dyn DbPlugin, ownership_version: i64) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) VALUES (?, 'Bot X', ?, ?)",
        vec![
            DbValue::from(BOT_X),
            DbValue::from(ENV),
            DbValue::from(ownership_version),
        ],
    ))
    .await
    .expect("seed bot row");
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, \
         management_source_id) VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL, ?, ?)",
        vec![
            DbValue::from(ENV),
            DbValue::from(OWNER_A),
            DbValue::from(BOT_X),
            DbValue::from(OWNER_SOURCE_KIND),
            DbValue::from(OWNER_SOURCE_ID),
        ],
    ))
    .await
    .expect("seed owner edge");
}

async fn seed_direct_manager_edge(db: &dyn DbPlugin, manager_user: &str) -> i64 {
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, \
         management_source_id) VALUES (?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from', NULL, ?, ?)",
        vec![
            DbValue::from(ENV),
            DbValue::from(manager_user),
            DbValue::from(BOT_X),
            DbValue::from(DIRECT_SOURCE_KIND),
            DbValue::from(DIRECT_SOURCE_ID),
        ],
    ))
    .await
    .expect("seed manager edge");
    let rows = db
        .query(DbStatement::with_params(
            "SELECT id FROM edge_grants WHERE from_id = ? AND to_id = ? AND grant_kind = 'manager'",
            vec![DbValue::from(manager_user), DbValue::from(BOT_X)],
        ))
        .await
        .expect("manager edge id");
    db_get_column::<i64>(&rows[0], "id").expect("manager edge id")
}

async fn count(db: &dyn DbPlugin, sql: &str, params: Vec<DbValue>) -> i64 {
    let rows = db
        .query(DbStatement::with_params(sql, params))
        .await
        .unwrap_or_else(|error| panic!("count query failed: {error}\n{sql}"));
    db_get_column::<i64>(&rows[0], "n").expect("count column n")
}

async fn edge_status(db: &dyn DbPlugin, edge_id: i64) -> String {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT status FROM edge_grants WHERE id = ?",
            vec![DbValue::from(edge_id)],
        ))
        .await
        .expect("edge status");
    db_get_column::<String>(&rows[0], "status").expect("edge status")
}

/// The brief's minimal plugin call shape commits business + audit writes in
/// one transaction: lock the precise Bot row (SQLite keeps the write lock via
/// the IMMEDIATE transaction the local plugin opens for write transactions),
/// revoke the manager edge with an ExecuteChecked CAS write, and append the
/// manager-change audit in the SAME transaction.
#[tokio::test]
async fn manager_revoke_transaction_commits_cas_audit_atomically() {
    let db = migrated_db().await;
    seed_bot_with_owner(&db, 1).await;
    let edge_id = seed_direct_manager_edge(&db, OWNER_B).await;

    let lock_bot_statement = lock_bot_statement(BOT_X, ENV);
    let changing_cas_statement = DbStatement::with_params(
        // Genuinely changing CAS write (spec §10: only rows still approved
        // can be revoked), so affected-row counts agree across dialects.
        "UPDATE edge_grants SET status = 'revoked' WHERE id = ? AND status = 'approved'",
        vec![DbValue::from(edge_id)],
    );
    let audit_statement = DbStatement::with_params(
        "INSERT INTO bot_manager_changes (audit_id, env, bot_id, subject_user_id, edge_id, \
         management_source_kind, management_source_id, action, actor_kind, actor_id, \
         operation_id, decided_at) \
         VALUES ('audit-revoke-1', ?, ?, ?, ?, ?, ?, 'revoke', 'human', ?, 'op-revoke-1', CURRENT_TIMESTAMP)",
        vec![
            DbValue::from(ENV),
            DbValue::from(BOT_X),
            DbValue::from(OWNER_B),
            DbValue::from(edge_id),
            DbValue::from(DIRECT_SOURCE_KIND),
            DbValue::from(DIRECT_SOURCE_ID),
            DbValue::from(OWNER_A),
        ],
    );
    let steps = vec![
        DbTransactionStep::Query(lock_bot_statement),
        DbTransactionStep::ExecuteChecked { statement: changing_cas_statement, expected_affected_rows: 1 },
        DbTransactionStep::Execute(audit_statement),
    ];
    let committed_steps = db.transaction(steps).await.expect("revoke transaction commits");

    assert_eq!(committed_steps.len(), 3);
    let DbTransactionStepResult::Rows(locked) = &committed_steps[0] else {
        panic!("first step is the locking query");
    };
    assert_eq!(locked.len(), 1, "the precise bot row was locked in-transaction");
    assert_eq!(edge_status(&db, edge_id).await, "revoked");
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM bot_manager_changes WHERE operation_id = 'op-revoke-1'",
            vec![],
        )
        .await,
        1,
        "the audit row persisted in the same transaction"
    );
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM bot_manager_changes WHERE idempotency_key IS NOT NULL",
            vec![],
        )
        .await,
        0,
        "non-team audits leave the platform idempotency_key empty, never fabricated"
    );
}

/// Repeating the same no-change revoke must be a normal committed domain
/// result: the stop-on-no-rows path commits the executed prefix and returns
/// control to the caller WITHOUT an ExecuteChecked failure and WITHOUT a new
/// audit row — "只对实际状态变化追加审计" (§5.4).
#[tokio::test]
async fn repeated_revoke_returns_committed_prefix_without_new_audit() {
    let db = migrated_db().await;
    seed_bot_with_owner(&db, 1).await;
    let edge_id = seed_direct_manager_edge(&db, OWNER_B).await;

    // First revoke commits with its audit.
    db.transaction(vec![
        DbTransactionStep::Query(lock_bot_statement(BOT_X, ENV)),
        DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE edge_grants SET status = 'revoked' WHERE id = ? AND status = 'approved'",
                vec![DbValue::from(edge_id)],
            ),
            expected_affected_rows: 1,
        },
        DbTransactionStep::Execute(DbStatement::with_params(
            "INSERT INTO bot_manager_changes (audit_id, env, bot_id, subject_user_id, edge_id, \
             management_source_kind, management_source_id, action, actor_kind, actor_id, \
             operation_id, decided_at) \
             VALUES ('audit-revoke-first', ?, ?, ?, ?, 'direct', 'manual', 'revoke', 'human', ?, \
             'op-repeat-1', CURRENT_TIMESTAMP)",
            vec![
                DbValue::from(ENV),
                DbValue::from(BOT_X),
                DbValue::from(OWNER_B),
                DbValue::from(edge_id),
                DbValue::from(OWNER_A),
            ],
        )),
    ])
    .await
    .expect("first revoke commits");

    // Second attempt: the conditional update is a plain Execute with
    // stop-on-no-rows, so zero affected rows COMMITS the prefix (nothing new
    // to invalidate, no new audit step) instead of rolling back.
    let audit_count_before = count(&db,
        "SELECT COUNT(*) AS n FROM bot_manager_changes WHERE operation_id = 'op-repeat-1'",
        vec![],
    )
    .await;
    let results = db
        .transaction(vec![
            DbTransactionStep::Query(lock_bot_statement(BOT_X, ENV)),
            DbTransactionStep::Execute(
                DbStatement::with_params(
                    "UPDATE edge_grants SET status = 'revoked' WHERE id = ? AND status = 'approved'",
                    vec![DbValue::from(edge_id)],
                )
                .with_transaction_stop_on_no_rows(),
            ),
        ])
        .await
        .expect("the no-change repeat must be a committed domain result, not an error");
    // The executed prefix committed and the never-executed suffix was cut.
    assert_eq!(results.len(), 2);
    let DbTransactionStepResult::Executed(second_update) = &results[1] else {
        panic!("second step is the conditional write");
    };
    assert_eq!(second_update.affected_rows, 0, "nothing was still approved");
    assert_eq!(edge_status(&db, edge_id).await, "revoked");
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM bot_manager_changes WHERE operation_id = 'op-repeat-1'",
            vec![],
        )
        .await,
        audit_count_before,
        "no-change repeats must not append a duplicate audit row"
    );
}

/// The §10.2 commit-invalidated-first protocol: when the conditional
/// invalidation SQL re-checking ALL policy conditions (pending, not expired,
/// expected version) does not match — i.e. the request is still valid — the
/// stop-on-no-rows prefix commits without touching the transfer and the flow
/// proceeds to the accept branch; when the version snapshot has drifted, the
/// invalidation is persisted as a committed domain result with terminal
/// owner_changed, no edge is modified, and no version is bumped.
#[tokio::test]
async fn owner_changed_invalidation_uses_committed_prefix_in_both_branches() {
    let db = migrated_db().await;
    seed_bot_with_owner(&db, bcs_domain::INITIALIZED_OWNERSHIP_VERSION as i64 + 1).await;
    // Seed: owner edge for user-a, a pending transfer whose snapshot matches
    // the current ownership_version (expected_owner_version = 2).
    db.execute(DbStatement::with_params(
        "INSERT INTO bot_ownership_transfers (transfer_id, env, bot_id, from_user_id, to_user_id, \
         expected_owner_version, client_request_id, status, expires_at, bot_name_snapshot) \
         VALUES ('transfer-1', ?, ?, 'human_user-a', 'human_user-b', 2, 'cr-1', 'pending', \
         '2030-01-01 00:00:00', 'Bot X')",
        vec![DbValue::from(ENV), DbValue::from(BOT_X)],
    ))
    .await
    .expect("seed pending transfer");

    let results = db
        .transaction(vec![
            DbTransactionStep::Query(lock_bot_statement(BOT_X, ENV)),
            DbTransactionStep::Execute(
                DbStatement::with_transaction_params(
                    "UPDATE bot_ownership_transfers SET status = 'invalidated', \
                     terminal_reason = 'owner_changed', decision_actor_kind = 'system', \
                     decided_by = 'bcs-ownership-system', decided_at = CURRENT_TIMESTAMP \
                     WHERE transfer_id = ? AND env = ? AND status = 'pending' \
                       AND expected_owner_version <> ?",
                    vec![
                        DbTransactionParam::value("transfer-1"),
                        DbTransactionParam::value(ENV),
                        DbTransactionParam::query_result(0, 0, "ownership_version"),
                    ],
                )
                .with_transaction_stop_on_no_rows(),
            ),
        ])
        .await
        .expect("expected version matches, so the still-valid request is left pending");
    assert_eq!(results.len(), 2, "conditional write matched zero rows; prefix committed");
    let still = db
        .query(DbStatement::with_params(
            "SELECT status, terminal_reason FROM bot_ownership_transfers WHERE transfer_id = ?",
            vec![DbValue::from("transfer-1")],
        ))
        .await
        .expect("transfer state");
    assert_eq!(
        db_get_column::<String>(&still[0], "status").expect("status"),
        "pending",
        "an unmatched invalidation branch must not mutate the transfer"
    );

    // Simulate A→B→A style drift: the transfer's snapshot no longer matches
    // the current ownership_version. The same step set now invalidates the
    // pending in-transaction and commits it as a normal domain result — no
    // ExecuteChecked failure, no rolled-back invalidation.
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET ownership_version = ownership_version + 1 WHERE bot_uuid = ? AND env = ?",
        vec![DbValue::from(BOT_X), DbValue::from(ENV)],
    ))
    .await
    .expect("drift the version");
    let results = db
        .transaction(vec![
            DbTransactionStep::Query(lock_bot_statement(BOT_X, ENV)),
            DbTransactionStep::Execute(
                DbStatement::with_transaction_params(
                    "UPDATE bot_ownership_transfers SET status = 'invalidated', \
                     terminal_reason = 'owner_changed', decision_actor_kind = 'system', \
                     decided_by = 'bcs-ownership-system', decided_at = CURRENT_TIMESTAMP \
                     WHERE transfer_id = ? AND env = ? AND status = 'pending' \
                       AND expected_owner_version <> ?",
                    vec![
                        DbTransactionParam::value("transfer-1"),
                        DbTransactionParam::value(ENV),
                        DbTransactionParam::query_result(0, 0, "ownership_version"),
                    ],
                )
                // A matched row CONTINUES the transaction (any suffix steps
                // with the same operation would run); a miss commits prefix.
                .with_transaction_stop_on_no_rows(),
            ),
        ])
        .await
        .expect("invalidation must persist as a committed domain result");
    assert_eq!(results.len(), 2);
    let DbTransactionStepResult::Executed(invalidated) = &results[1] else {
        panic!("second step is the conditional write");
    };
    assert_eq!(invalidated.affected_rows, 1);
    let initialized = db
        .query(DbStatement::with_params(
            "SELECT status, terminal_reason, result_owner_version FROM bot_ownership_transfers WHERE transfer_id = ?",
            vec![DbValue::from("transfer-1")],
        ))
        .await
        .expect("invalidated row");
    assert_eq!(
        db_get_column::<String>(&initialized[0], "status").expect("status"),
        "invalidated"
    );
    assert_eq!(
        db_get_column::<String>(&initialized[0], "terminal_reason").expect("terminal_reason"),
        "owner_changed"
    );
    assert_eq!(
        initialized[0].get("result_owner_version"),
        Some(&DbValue::Null),
        "invalidation keeps result_owner_version empty (§10.2)"
    );
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM edge_grants WHERE grant_kind = 'owner' AND status = 'approved'",
            vec![],
        )
        .await,
        1,
        "no edge was modified by the invalidation branch"
    );
    assert_eq!(
        db.query(DbStatement::with_params(
            "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ?",
            vec![DbValue::from(BOT_X)],
        ))
        .await
        .map(|rows| db_get_column::<i64>(&rows[0], "ownership_version").expect("version"))
        .expect("version read"),
        3,
        "the invalidation branch never bumps ownership_version"
    );
}

/// After a committed invalidation the pending slot is actually released: a
/// new transfer for the same Bot can be created in a follow-up transaction
/// (§10.2 — the slot free is what makes the invalidated, not the expired,
/// terminal semantics observable).
#[tokio::test]
async fn invalidated_transfer_releases_pending_slot() {
    let db = migrated_db().await;
    seed_bot_with_owner(&db, 1).await;
    db.execute(DbStatement::with_params(
        "INSERT INTO bot_ownership_transfers (transfer_id, env, bot_id, from_user_id, to_user_id, \
         expected_owner_version, client_request_id, status, expires_at, bot_name_snapshot) \
         VALUES ('transfer-1', ?, ?, 'human_user-a', 'human_user-b', 1, 'cr-1', 'pending', \
         '2030-01-01 00:00:00', 'Bot X')",
        vec![DbValue::from(ENV), DbValue::from(BOT_X)],
    ))
    .await
    .expect("seed pending transfer");

    // Invalidation committed as a normal domain result, under the write lock.
    db.transaction(vec![
        DbTransactionStep::Query(lock_bot_statement(BOT_X, ENV)),
        DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE bot_ownership_transfers SET status = 'invalidated', \
                 terminal_reason = 'owner_changed', decision_actor_kind = 'system', \
                 decided_by = 'bcs-ownership-system', decided_at = CURRENT_TIMESTAMP \
                 WHERE transfer_id = 'transfer-1' AND env = ? AND status = 'pending'",
                vec![DbValue::from(ENV)],
            ),
            expected_affected_rows: 1,
        },
    ])
    .await
    .expect("commit the invalidation");

    // The new pending (new client_request_id + new version snapshot) fits
    // the slot once the invalidated terminal state is committed.
    let new_pending = db
        .execute(DbStatement::with_params(
            "INSERT INTO bot_ownership_transfers (transfer_id, env, bot_id, from_user_id, to_user_id, \
             expected_owner_version, client_request_id, status, expires_at, bot_name_snapshot) \
             VALUES ('transfer-2', ?, ?, 'human_user-a', 'human_user-c', 1, 'cr-2', 'pending', \
             '2031-01-01 00:00:00', 'Bot X')",
            vec![DbValue::from(ENV), DbValue::from(BOT_X)],
        ))
        .await;
    assert!(
        new_pending.is_ok(),
        "an invalidated terminal row must free the pending slot for the next legitimate request"
    );
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM bot_ownership_transfers WHERE status = 'pending'",
            vec![],
        )
        .await,
        1
    );
}

/// Query-result binding proof: the in-lock read of the current
/// ownership_version feeds the CAS guard of the bump write inside the SAME
/// transaction (§10.2 step 5), including the audit append of the same
/// operation — here a bot-control business audit per §12.5.
#[tokio::test]
async fn cas_bump_binds_locked_read_into_where_guard() {
    let db = migrated_db().await;
    seed_bot_with_owner(&db, bcs_domain::UNINITIALIZED_OWNERSHIP_VERSION as i64).await;

    let committed_steps = db
        .transaction(vec![
            DbTransactionStep::Query(lock_bot_statement(BOT_X, ENV)),
            DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_transaction_params(
                    "UPDATE bcs_bots SET ownership_version = ownership_version + 1 \
                     WHERE bot_uuid = ? AND env = ? AND ownership_version = ?",
                    vec![
                        DbTransactionParam::value(BOT_X),
                        DbTransactionParam::value(ENV),
                        DbTransactionParam::query_result(0, 0, "ownership_version"),
                    ],
                ),
                expected_affected_rows: 1,
            },
            DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO bcs_bot_action_audits (audit_id, env, operation_id, step_key, \
                 operator_kind, operator_id, operator_user_id, effective_actor_id, resource_kind, \
                 resource_id, action, phase) \
                 VALUES ('audit-cas-1', ?, 'op-bump-1', 'update/bot/applied', 'human', 'user-a', \
                 'user-a', ?, 'bot', ?, 'update', 'applied')",
                vec![
                    DbValue::from(ENV),
                    DbValue::from(BOT_X),
                    DbValue::from(BOT_X),
                ],
            )),
        ])
        .await
        .expect("locked read + CAS bump + audit commit in one transaction");
    assert_eq!(committed_steps.len(), 3);
    assert_eq!(
        db.query(DbStatement::with_params(
            "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ?",
            vec![DbValue::from(BOT_X)],
        ))
        .await
        .map(|rows| db_get_column::<i64>(&rows[0], "ownership_version").expect("version"))
        .expect("version read"),
        bcs_domain::INITIALIZED_OWNERSHIP_VERSION as i64
    );
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM bcs_bot_action_audits WHERE operation_id = 'op-bump-1'",
            vec![],
        )
        .await,
        1
    );
}

/// A genuinely broken audit write (duplicate operation/step slot) must roll
/// back the SAME-transaction business write: neither the UPDATE nor the
/// audit row survives (§12.5 + §5.4: 写失败禁止 best-effort 持久化后报成功).
#[tokio::test]
async fn audit_failure_rolls_back_business_write() {
    let db = migrated_db().await;
    seed_bot_with_owner(&db, 1).await;
    // Pre-existing audit row occupying the (env, operation, step) slot: the
    // second insert in the transaction below must fail on the unique slot.
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bot_action_audits (audit_id, env, operation_id, step_key, \
         operator_kind, operator_id, operator_user_id, effective_actor_id, resource_kind, \
         resource_id, action, phase) \
         VALUES ('audit-conflict-seed', ?, 'op-conflict', 'update/bot/applied', 'human', \
         'user-a', 'user-a', ?, 'bot', ?, 'update', 'applied')",
        vec![
            DbValue::from(ENV),
            DbValue::from(BOT_X),
            DbValue::from(BOT_X),
        ],
    ))
    .await
    .expect("seed conflicting audit slot");

    let outcome = db
        .transaction(vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_bots SET name = 'renamed-by-bot' WHERE bot_uuid = ? AND env = ?",
                vec![DbValue::from(BOT_X), DbValue::from(ENV)],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO bcs_bot_action_audits (audit_id, env, operation_id, step_key, \
                 operator_kind, operator_id, operator_user_id, effective_actor_id, resource_kind, \
                 resource_id, action, phase) \
                 VALUES ('audit-conflict-2', ?, 'op-conflict', 'update/bot/applied', 'human', \
                 'user-a', 'user-a', ?, 'bot', ?, 'update', 'applied')",
                vec![
                    DbValue::from(ENV),
                    DbValue::from(BOT_X),
                    DbValue::from(BOT_X),
                ],
            )),
        ])
        .await;
    assert!(outcome.is_err(), "the duplicate audit slot must fail the commit");
    // The business write was rolled back…
    let name = db
        .query(DbStatement::with_params(
            "SELECT name FROM bcs_bots WHERE bot_uuid = ?",
            vec![DbValue::from(BOT_X)],
        ))
        .await
        .expect("name after rollback");
    assert_eq!(
        db_get_column::<String>(&name[0], "name").expect("name"),
        "Bot X",
        "the same-transaction business UPDATE must roll back with the audit"
    );
    // …and no audit residue was left behind.
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM bcs_bot_action_audits WHERE operation_id = 'op-conflict'",
            vec![],
        )
        .await,
        1
    );
}

/// ExecuteChecked mismatch is a genuine failure: earlier writes in the same
/// transaction roll back and the error surfaces (never report a committed
/// invalidation through this path — that is what the committed-prefix
/// pattern above exists to avoid).
#[tokio::test]
async fn execute_checked_mismatch_rolls_back_earlier_write() {
    let db = migrated_db().await;
    seed_bot_with_owner(&db, 1).await;

    let outcome = db
        .transaction(vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_bots SET name = 'should-not-survive' WHERE bot_uuid = ? AND env = ?",
                vec![DbValue::from(BOT_X), DbValue::from(ENV)],
            )),
            DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_params(
                    // Version already bumped to 1 in the seed: the guard
                    // matches zero rows, so expecting 1 is a REAL mismatch.
                    "UPDATE bcs_bots SET ownership_version = ownership_version + 1 \
                     WHERE bot_uuid = ? AND env = ? AND ownership_version = 42",
                    vec![DbValue::from(BOT_X), DbValue::from(ENV)],
                ),
                expected_affected_rows: 1,
            },
        ])
        .await;
    assert!(matches!(outcome, Err(DbError::ConditionFailed { .. })));
    let name = db
        .query(DbStatement::with_params(
            "SELECT name, ownership_version FROM bcs_bots WHERE bot_uuid = ?",
            vec![DbValue::from(BOT_X)],
        ))
        .await
        .expect("name after rollback");
    assert_eq!(
        db_get_column::<String>(&name[0], "name").expect("name"),
        "Bot X",
        "an ExecuteChecked mismatch must roll back the earlier business write"
    );
    assert_eq!(
        db_get_column::<i64>(&name[0], "ownership_version").expect("version"),
        1
    );
}