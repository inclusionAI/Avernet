//! Task 2: bot authority schema (migration) real-SQL tests.
//!
//! Every assertion in this file is backed by a real INSERT execution or a
//! real COUNT query against a real SQLite database built by the FULL existing
//! migration chain (`bcs::migrations::run_sqlite_migrations`), never by mock
//! booleans (plan Task 2, brief 2026-10-08 step 1). Schema/contract:
//! `docs/superpowers/specs/2026-09-18-bot-manage-permission-design.md`
//! §5 (role/source encoding), §5.2 (transfer fields), §5.3
//! (uniqueness/indexes), §12.5 (action-audit table).
//!
//! The MySQL companion lives in `bot_authority_mysql.rs` as an `#[ignore]`d
//! test that follows the `BCS_TEST_MYSQL_URL` pattern of
//! `crates/services/bcs-event-store/tests/conformance_event_store_mysql.rs`.
//! No MySQL server is reachable in this dev environment, so live MySQL
//! behavior (generated slots, FOR UPDATE locking, CHECK enforcement) is
//! UNVERIFIED here; the ignored test exists so CI can run it.

use bcs_db_api::{DbError, DbPlugin, DbStatement, DbValue, db_get_column};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::edge_permission::GrantKind;
use bcs_domain::{
    DIRECT_SOURCE_ID, DIRECT_SOURCE_KIND, NON_ROLE_SOURCE_ID, NON_ROLE_SOURCE_KIND,
    OWNER_SOURCE_ID, OWNER_SOURCE_KIND, ROLE_GRANT_REF_ID, TerminalReason, UNINITIALIZED_OWNERSHIP_VERSION,
    INITIALIZED_OWNERSHIP_VERSION,
};

const ENV: &str = "local";
const BOT_A: &str = "bot-a";

/// Fresh SQLite database with the FULL migration chain applied.
async fn migrated_db() -> LocalSqliteDbPlugin {
    let db = LocalSqliteDbPlugin::new().expect("open local sqlite");
    bcs::migrations::run_sqlite_migrations(&db)
        .await
        .expect("apply the full sqlite migration chain");
    db
}

/// Real COUNT query (the only way counts are obtained in this suite).
async fn count(db: &dyn DbPlugin, sql: &str, params: Vec<DbValue>) -> i64 {
    let rows = db
        .query(DbStatement::with_params(sql, params))
        .await
        .unwrap_or_else(|error| panic!("count query failed: {error}\n{sql}"));
    let n = rows
        .first()
        .unwrap_or_else(|| panic!("count query returned no rows: {sql}"));
    db_get_column::<i64>(n, "n").expect("count column n")
}

/// INSERT helper for `bcs_bot_action_audits` rows used by the operator-shape
/// tests below.
async fn insert_audit_row(
    db: &dyn DbPlugin,
    audit_id: &str,
    step_key: &str,
    operator_kind: &str,
    operator_id: &str,
    user: Option<&str>,
    phase: &str,
) -> Result<bcs_db_api::DbExecuteResult, DbError> {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bot_action_audits (audit_id, env, operation_id, step_key, \
         operator_kind, operator_id, operator_user_id, effective_actor_id, resource_kind, \
         resource_id, action, phase) \
         VALUES (?, 'local', 'op-1', ?, ?, ?, ?, 'bot-x', 'message', 'msg-1', 'send', ?)",
        vec![
            DbValue::from(audit_id),
            DbValue::from(step_key),
            DbValue::from(operator_kind),
            DbValue::from(operator_id),
            user.map(DbValue::from).unwrap_or(DbValue::Null),
            DbValue::from(phase),
        ],
    ))
    .await
}

async fn table_exists(db: &dyn DbPlugin, table: &str) -> bool {
    count(db,
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = ?",
        vec![DbValue::from(table)],
    )
    .await == 1
}

async fn index_exists(db: &dyn DbPlugin, index: &str) -> bool {
    count(db,
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'index' AND name = ?",
        vec![DbValue::from(index)],
    )
    .await == 1
}

/// Insert an edge_grants row with explicit kind/sources. Values come from the
/// shared `bcs_domain` constants so no fresh literals drift from the encoding.
#[expect(clippy::too_many_arguments)]
async fn insert_edge(
    db: &dyn DbPlugin,
    env: &str,
    from_id: &str,
    to_id: &str,
    grant_kind: &str,
    grant_ref_id: i64,
    status: &str,
    source_kind: &str,
    source_id: &str,
    rules: Option<&str>,
) -> Result<(), DbError> {
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, \
         management_source_id) VALUES (?, ?, ?, ?, ?, ?, ?, 'same_as_from', NULL, ?, ?)",
        vec![
            DbValue::from(env),
            DbValue::from(from_id),
            DbValue::from(to_id),
            DbValue::from(grant_kind),
            DbValue::from(grant_ref_id),
            rules.map(DbValue::from).unwrap_or(DbValue::Null),
            DbValue::from(status),
            DbValue::from(source_kind),
            DbValue::from(source_id),
        ],
    ))
    .await
    .map(|_| ())
}

/// The `owner` grant_kind encoding coincides with the fixed owner source kind
/// (single shared label); asserted once in the constants test below.
async fn insert_owner_edge(db: &dyn DbPlugin, from_user: &str) -> Result<(), DbError> {
    insert_edge(db,
        ENV,
        from_user,
        BOT_A,
        OWNER_SOURCE_KIND,
        ROLE_GRANT_REF_ID as i64,
        "approved",
        OWNER_SOURCE_KIND,
        OWNER_SOURCE_ID,
        None,
    )
    .await
}

/// Insert one bot row so reference columns point at a real Bot.
async fn insert_bot(db: &dyn DbPlugin, bot_uuid: &str, name: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, env) VALUES (?, ?, ?)",
        vec![
            DbValue::from(bot_uuid),
            DbValue::from(name),
            DbValue::from(ENV),
        ],
    ))
    .await
    .expect("seed bcs_bots row");
}

async fn insert_transfer(
    db: &dyn DbPlugin,
    transfer_id: &str,
    from_user: &str,
    to_user: &str,
    expected_owner_version: i64,
    client_request_id: &str,
) -> Result<(), DbError> {
    db.execute(DbStatement::with_params(
        "INSERT INTO bot_ownership_transfers (transfer_id, env, bot_id, from_user_id, to_user_id, \
         expected_owner_version, client_request_id, status, expires_at, bot_name_snapshot) \
         VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '2030-01-01 00:00:00', 'snapshot-name')",
        vec![
            DbValue::from(transfer_id),
            DbValue::from(ENV),
            DbValue::from(BOT_A),
            DbValue::from(from_user),
            DbValue::from(to_user),
            DbValue::from(expected_owner_version),
            DbValue::from(client_request_id),
        ],
    ))
    .await
    .map(|_| ())
}

/// The brief's step-1 core scenario with the exact variable shape it requires:
/// real INSERT results and real COUNT queries, one person keeping two runtime
/// refs and multiple manager sources, and post-failure counts proving that no
/// rejected write was half-persisted.
#[tokio::test]
async fn second_owner_second_pending_and_multi_source_shapes() {
    let db = migrated_db().await;
    insert_bot(&db, BOT_A, "Bot A").await;

    // First approved owner slot is taken…
    let insert_first_owner = insert_owner_edge(&db, "human_user-a").await;
    // …a second APPROVED owner for the same Bot in the same env must fail
    // (approved owner slot uniqueness, §5.3).
    let insert_second_active_owner = insert_owner_edge(&db, "human_user-b").await;
    // The runtime-ref dimension of the unique key is preserved: the same
    // actor pair keeps several non-role edges with distinct refs. One
    // runtime ref is already in place…
    insert_edge(&db,
        ENV,
        "human_user-a",
        BOT_A,
        "rules",
        101,
        "approved",
        NON_ROLE_SOURCE_KIND,
        NON_ROLE_SOURCE_ID,
        None,
    )
    .await
    .expect("seed first runtime ref edge");
    // …and a second one must still be retainable alongside it.
    let insert_second_runtime_ref = insert_edge(&db,
        ENV,
        "human_user-a",
        BOT_A,
        "rules",
        102,
        "approved",
        NON_ROLE_SOURCE_KIND,
        NON_ROLE_SOURCE_ID,
        None,
    )
    .await;
    // Same Human, same Bot: a manager role may accumulate several sources.
    insert_edge(&db,
        ENV,
        "human_user-a",
        BOT_A,
        "manager",
        ROLE_GRANT_REF_ID as i64,
        "approved",
        DIRECT_SOURCE_KIND,
        DIRECT_SOURCE_ID,
        None,
    )
    .await
    .expect("seed direct/manual manager edge");
    let insert_second_team_source = insert_edge(&db,
        ENV,
        "human_user-a",
        BOT_A,
        "manager",
        ROLE_GRANT_REF_ID as i64,
        "approved",
        "team",
        "team-1",
        None,
    )
    .await;

    // Pending transfer slot: the first insert commits, the second must fail
    // against the DB constraint — including "not-yet-materialized expired"
    // rows (§9.1), because the physical status is still pending.
    let insert_first_pending =
        insert_transfer(&db, "transfer-1", "human_user-a", "human_user-b", 1, "cr-1").await;
    let insert_second_pending =
        insert_transfer(&db, "transfer-2", "human_user-a", "human_user-c", 1, "cr-2").await;

    // Real COUNT queries after the failures: nothing was half-persisted by
    // the rejected second-owner/second-pending inserts.
    let owner_count_after_rollback = count(&db,
        &format!(
            "SELECT COUNT(*) AS n FROM edge_grants WHERE env = ? AND to_id = ? \
             AND grant_kind = '{}' AND status = 'approved'",
            OWNER_SOURCE_KIND
        ),
        vec![DbValue::from(ENV), DbValue::from(BOT_A)],
    )
    .await;
    let pending_count_after_rollback = count(&db,
        "SELECT COUNT(*) AS n FROM bot_ownership_transfers WHERE env = ? AND bot_id = ? AND status = 'pending'",
        vec![DbValue::from(ENV), DbValue::from(BOT_A)],
    )
    .await;

    assert!(insert_first_owner.is_ok());
    assert!(insert_second_active_owner.is_err());
    assert!(insert_second_runtime_ref.is_ok());
    assert!(insert_second_team_source.is_ok());
    assert!(insert_first_pending.is_ok());
    assert!(insert_second_pending.is_err());
    assert_eq!(owner_count_after_rollback, 1);
    assert_eq!(pending_count_after_rollback, 1);
}

/// Schema is built complete in one shot: all authority tables plus edge and
/// Bot columns exist, the unified unique key replaced the old one, and the
/// migration is registered.
#[tokio::test]
async fn complete_authority_schema_and_migration_registration() {
    let db = migrated_db().await;

    for table in [
        "bot_ownership_transfers",
        "bot_manager_changes",
        "bcs_bot_action_audits",
        "bot_manager_sync_operations",
        "bot_team_manager_sources",
        "bot_ownership_initializations",
    ] {
        assert!(
            table_exists(&db, table).await,
            "authority table {table} must be created by the migration"
        );
    }
    // Version 32 recorded as applied under the shared name.
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM bcs_schema_migrations \
             WHERE version = 32 AND name = 'bot_authority' AND dialect = 'sqlite'",
            vec![],
        )
        .await,
        1
    );
    // bot_ownership_transfers carries the full §5.2 field set.
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM pragma_table_info('bot_ownership_transfers') \
             WHERE name IN ('transfer_id','env','bot_id','from_user_id','to_user_id', \
             'expected_owner_version','client_request_id','status','expires_at', \
             'decision_actor_kind','decided_by','decided_at','result_owner_version', \
             'terminal_reason','bot_name_snapshot')",
            vec![],
        )
        .await,
        15
    );
    // The unified edge key (source columns included, runtime-ref dimension
    // preserved) replaced the old unique index; the approved-owner slot exists.
    assert!(index_exists(&db, "uk_edge_from_to_env_kind_ref_source").await);
    assert!(index_exists(&db, "uk_edge_active_owner_slot").await);
    assert!(!index_exists(&db, "uk_edge_from_to_env_ref").await);
    // The pending transfer slot exists as the SQLite partial unique index.
    assert!(index_exists(&db, "uk_bot_transfer_pending_slot").await);
    // Idempotency key of pending creation, §5.3.
    assert!(index_exists(&db, "uk_bot_transfer_client_request").await);
    // Uninitialized ownership_version is a NOT NULL column defaulting to the
    // shared constant 0 for every row, historical ones included.
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM pragma_table_info('bcs_bots') \
             WHERE name = 'ownership_version' AND \"notnull\" = 1 AND dflt_value = '0'",
            vec![],
        )
        .await,
        1
    );
}

/// Human-field completeness (operator_user_id required for human, legal NULL
/// for Bot-only), operation/step uniqueness, and two phases of one operation
/// both representable (§12.5: admitted and completed are different steps).
#[tokio::test]
async fn action_audit_operator_shape_and_step_uniqueness() {
    let db = migrated_db().await;

    // Human audit without operator_user_id must be rejected by the schema.
    let human_missing_user_id =
        insert_audit_row(&db, "a-1", "send/message/admitted", "human", "user-a", None, "admitted").await;
    assert!(
        human_missing_user_id.is_err(),
        "human operator needs operator_user_id"
    );
    // Non-human operator carrying a forged Human id must also fail closed.
    let bot_with_user_id = insert_audit_row(&db, "a-2", "send/message/admitted", "bot", "bot-x", Some("user-a"), "admitted").await;
    assert!(
        bot_with_user_id.is_err(),
        "Bot-only audit must keep operator_user_id NULL"
    );
    // Bot-only with NULL operator_user_id is the legal branch.
    let bot_only = insert_audit_row(&db, "a-3", "send/message/admitted", "bot", "bot-x", None, "admitted").await;
    assert!(bot_only.is_ok(), "Bot-only NULL operator_user_id must be legal");
    // Admitted and completed must both be representable as different steps of
    // ONE operation — one operation is never limited to a single audit row.
    let completed_step = insert_audit_row(&db, "a-4", "send/message/completed", "bot", "bot-x", None, "completed").await;
    assert!(completed_step.is_ok());
    // The same (env, operation_id, step_key) slot cannot be blindly
    // double-written: legitimate retries compare against the persisted row
    // first (Task 1 `content_conflicts`), and a blind insert must fail.
    let duplicate_slot = insert_audit_row(&db, "a-5", "send/message/completed", "bot", "bot-x", None, "completed").await;
    assert!(duplicate_slot.is_err());
    // Unknown phase values are rejected by the controlled vocabulary.
    let unknown_phase = insert_audit_row(&db, "a-6", "send/message/done", "bot", "bot-x", None, "done").await;
    assert!(unknown_phase.is_err());
    // operator_user_id assignments did not leave residue behind the rejects.
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM bcs_bot_action_audits WHERE env = 'local' AND operation_id = 'op-1'",
            vec![],
        )
        .await,
        2
    );
}

/// Role/source shape enforcement (§5.1) exercised from real INSERTs, both
/// valid and invalid encodings.
#[tokio::test]
async fn role_source_shape_enforced_by_schema() {
    let db = migrated_db().await;
    insert_bot(&db, BOT_A, "Bot A").await;

    // Owner edge with a non-placeholder ref or inline rules is an invalid shape.
    assert!(
        insert_edge(&db, ENV, "human_user-a", BOT_A, OWNER_SOURCE_KIND, 5, "approved",
            OWNER_SOURCE_KIND, OWNER_SOURCE_ID, None)
            .await
            .is_err(),
        "owner edges must use the fixed grant_ref_id placeholder"
    );
    assert!(
        insert_edge(&db, ENV, "human_user-a", BOT_A, OWNER_SOURCE_KIND, ROLE_GRANT_REF_ID as i64,
            "approved", OWNER_SOURCE_KIND, OWNER_SOURCE_ID, Some("{}"))
            .await
            .is_err(),
        "owner edits must not carry inline rules"
    );
    // Manager edges cannot smuggle the owner's fixed source encoding or made
    // up direct ids; team ids must be non-empty.
    assert!(
        insert_edge(&db, ENV, "human_user-a", BOT_A, "manager", ROLE_GRANT_REF_ID as i64,
            "approved", OWNER_SOURCE_KIND, OWNER_SOURCE_ID, None)
            .await
            .is_err(),
        "manager edges must use a manager source encoding"
    );
    assert!(
        insert_edge(&db, ENV, "human_user-a", BOT_A, "manager", ROLE_GRANT_REF_ID as i64,
            "approved", DIRECT_SOURCE_KIND, "whatever", None)
            .await
            .is_err(),
        "direct source id is fixed to manual"
    );
    assert!(
        insert_edge(&db, ENV, "human_user-a", BOT_A, "manager", ROLE_GRANT_REF_ID as i64,
            "approved", "team", "", None)
            .await
            .is_err(),
        "team source ids must be non-empty"
    );
    // Non-role edges must use the fixed none/none encoding — no fresh literals.
    assert!(
        insert_edge(&db, ENV, "human_user-a", BOT_A, "permission_profile", 7, "approved",
            DIRECT_SOURCE_KIND, DIRECT_SOURCE_ID, None)
            .await
            .is_err(),
        "non-role edges must keep the fixed none/none source encoding"
    );
    // The legal direct/manual manager shape passes.
    assert!(
        insert_edge(&db, ENV, "human_user-a", BOT_A, "manager", ROLE_GRANT_REF_ID as i64,
            "approved", DIRECT_SOURCE_KIND, DIRECT_SOURCE_ID, None)
            .await
            .is_ok()
    );
}

/// Task 1's follow-up contract: a role-kind edge INSERT performed exactly the
/// way the pre-Task-2 store writes role edges (a statement WITHOUT the new
/// source columns) must be rejected by the database, never silently persisted
/// as a half role edge whose sources would only decode fail-closed later.
#[tokio::test]
async fn pre_task2_style_role_edge_insert_is_rejected_not_half_persisted() {
    let db = migrated_db().await;
    insert_bot(&db, BOT_A, "Bot A").await;

    for grant_kind in ["manager", "owner"] {
        let legacy_insert = db
            .execute(DbStatement::with_params(
                // This is insert_grant's SQL shape from before the authority
                // columns: it names every column except the sources.
                "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                 status, originator_policy_type, originator_policy_data) \
                 VALUES (?, 'human_user-b', ?, ?, 0, NULL, 'approved', 'same_as_from', NULL)",
                vec![
                    DbValue::from(ENV),
                    DbValue::from(BOT_A),
                    DbValue::from(grant_kind),
                ],
            ))
            .await;
        assert!(
            legacy_insert.is_err(),
            "legacy role EDGE INSERT without source columns must be rejected (kind {grant_kind})"
        );
    }
    // Zero role rows exist while the rejects happened.
    assert_eq!(
        count(&db,
            &format!(
                "SELECT COUNT(*) AS n FROM edge_grants WHERE grant_kind IN ('{}', 'manager')",
                OWNER_SOURCE_KIND
            ),
            vec![],
        )
        .await,
        0
    );
}

/// Case-sensitive identity semantics (§5.3): case-distinct team sources are
/// distinct managers, and case-distinct User IDs stay distinct senders for
/// the transfer idempotency key. MySQL byte-exact comparisons are covered by
/// the ignored MySQL test.
#[tokio::test]
async fn case_sensitive_ids_stay_distinct() {
    let db = migrated_db().await;
    insert_bot(&db, BOT_A, "Bot A").await;

    insert_owner_edge(&db, "human_user-a")
        .await
        .expect("seed owner edge");
    insert_edge(&db, ENV, "human_user-a", BOT_A, "manager", ROLE_GRANT_REF_ID as i64,
        "approved", "team", "Team-X", None).await.expect("team source Team-X");
    let case_distinct_team = insert_edge(&db, ENV, "human_user-a", BOT_A, "manager",
        ROLE_GRANT_REF_ID as i64, "approved", "team", "team-x", None).await;
    assert!(
        case_distinct_team.is_ok(),
        "team ids are case-sensitive identities"
    );
    // The same team id spelled identically is the same identity: duplicate.
    let same_team = insert_edge(&db, ENV, "human_user-a", BOT_A, "manager",
        ROLE_GRANT_REF_ID as i64, "approved", "team", "team-x", None).await;
    assert!(same_team.is_err());

    // Case-distinct User IDs are distinct Humans for the idempotency scope.
    insert_transfer(&db, "transfer-1", "human_user-a", "human_user-b", 1, "cr-1")
        .await
        .expect("first transfer");
    // Release the pending slot (terminal invalidated) and prove that the
    // SAME client_request_id from a case-distinct sender is a NEW row, not a
    // collision of merged identities.
    db.execute(DbStatement::new(
        "UPDATE bot_ownership_transfers SET status = 'invalidated', \
         decision_actor_kind = 'system', decided_by = 'system', \
         decided_at = CURRENT_TIMESTAMP, terminal_reason = 'owner_changed' \
         WHERE transfer_id = 'transfer-1'",
    ))
    .await
    .expect("invalidate first transfer");
    let case_distinct_sender =
        insert_transfer(&db, "transfer-2", "human_USER-a", "human_user-b", 1, "cr-1").await;
    assert!(
        case_distinct_sender.is_ok(),
        "human_user-a and human_USER-a are different senders"
    );
    assert_eq!(
        count(&db,
            "SELECT COUNT(*) AS n FROM bot_ownership_transfers \
             WHERE from_user_id IN ('human_user-a','human_USER-a')",
            vec![],
        )
        .await,
        2
    );
}

/// Grants keep revoked rows (§5.1): restoring a revoked manager edge must
/// reuse the SAME row id; while the row exists, an equivalent insert — even
/// in approved status — is blocked by the unified unique key. Restoring an
/// owner edge can never produce a second approved owner slot.
#[tokio::test]
async fn revoked_role_row_restores_in_place() {
    let db = migrated_db().await;
    insert_bot(&db, BOT_A, "Bot A").await;

    insert_owner_edge(&db, "human_user-a")
        .await
        .expect("seed owner edge");
    insert_edge(&db, ENV, "human_user-b", BOT_A, "manager", ROLE_GRANT_REF_ID as i64,
        "approved", DIRECT_SOURCE_KIND, DIRECT_SOURCE_ID, None)
        .await
        .expect("seed manager edge");

    // Revoke the manager edge — the row itself must survive.
    db.execute(DbStatement::new(
        "UPDATE edge_grants SET status = 'revoked' \
         WHERE from_id = 'human_user-b' AND grant_kind = 'manager'",
    ))
    .await
    .expect("revoke manager edge");
    let before = db
        .query(DbStatement::new(
            "SELECT id, status FROM edge_grants WHERE from_id = 'human_user-b' AND grant_kind = 'manager'",
        ))
        .await
        .expect("revoked row kept");
    let id_before = db_get_column::<i64>(&before[0], "id").expect("row id");

    // A new INSERT of the same edge — approved, same sources — cannot squeeze
    // next to the revoked row: re-grant must RESTORE the existing row.
    let duplicate_insert = insert_edge(&db, ENV, "human_user-b", BOT_A, "manager",
        ROLE_GRANT_REF_ID as i64, "approved",
        DIRECT_SOURCE_KIND, DIRECT_SOURCE_ID, None).await;
    assert!(
        duplicate_insert.is_err(),
        "revoked row still occupies the unique slot"
    );
    assert!(
        db.execute(DbStatement::new(
            "UPDATE edge_grants SET status = 'approved' \
             WHERE from_id = 'human_user-b' AND grant_kind = 'manager'",
        ))
        .await
        .is_ok(),
        "re-grant restores the kept row back to approved"
    );
    let after = db
        .query(DbStatement::new(
            "SELECT id, status FROM edge_grants WHERE from_id = 'human_user-b' AND grant_kind = 'manager'",
        ))
        .await
        .expect("read restored row");
    assert_eq!(db_get_column::<i64>(&after[0], "id").expect("row id"), id_before);
    assert_eq!(db_get_column::<String>(&after[0], "status").expect("status"), "approved");

    // Owner zombie-restore: the owner role moves to user-c (the historical
    // user-a row is revoked); restoring user-a's row as approved must fail
    // the approved-owner slot — a second approved owner cannot arise by
    // restoring historical rows.
    db.execute(DbStatement::new(
        "UPDATE edge_grants SET status = 'revoked' \
         WHERE from_id = 'human_user-a' AND grant_kind = 'owner'",
    ))
    .await
    .expect("revoke historical owner row");
    insert_owner_edge(&db, "human_user-c")
        .await
        .expect("owner role moves to user-c");
    let zombie_restore = db
        .execute(DbStatement::new(
            "UPDATE edge_grants SET status = 'approved' \
             WHERE from_id = 'human_user-a' AND grant_kind = 'owner'",
        ))
        .await;
    assert!(
        zombie_restore.is_err(),
        "restoring a second approved owner must violate the owner slot"
    );
    assert_eq!(
        count(&db,
            &format!(
                "SELECT COUNT(*) AS n FROM edge_grants WHERE grant_kind = '{}' AND status = 'approved'",
                OWNER_SOURCE_KIND
            ),
            vec![],
        )
        .await,
        1
    );
}

/// A legacy edge_grants table (pre-authority shape with the OLD unique key)
/// rebuilt by the migration must keep every existing column, index and row ID
/// of friends and runtime refs, adopting the shared none/none encoding for
/// backfilled rows — and must enforce role shapes with old-writer inserts.
#[tokio::test]
async fn legacy_edge_table_rebuild_preserves_friend_and_ref_rows() {
    let db = LocalSqliteDbPlugin::new().expect("open local sqlite");
    // The v13-era shape, exactly as the old runner created it.
    db.execute(DbStatement::new(
        "CREATE TABLE edge_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT NOT NULL, \
         from_id TEXT NOT NULL, to_id TEXT NOT NULL, grant_kind TEXT NOT NULL, \
         grant_ref_id INTEGER NOT NULL, rules TEXT, status TEXT NOT NULL DEFAULT 'approved', \
         originator_policy_type TEXT NOT NULL DEFAULT 'any', originator_policy_data TEXT, \
         gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .expect("create legacy edge_grants");
    db.execute(DbStatement::new(
        "CREATE TABLE permission_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL, \
         env TEXT NOT NULL, name TEXT NOT NULL DEFAULT 'default', description TEXT, rules_template TEXT NOT NULL, \
         revision INTEGER NOT NULL DEFAULT 1, digest TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0, \
         status TEXT NOT NULL DEFAULT 'active', created_by TEXT NOT NULL, updated_by TEXT, \
         gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .expect("create legacy permission_profiles");
    db.execute(DbStatement::new(
        "INSERT INTO permission_profiles (bot_id, env, name, rules_template, digest, is_default, created_by) \
         VALUES ('bot-a', 'local', 'default', '{}', 'digest-1', 1, 'system')",
    ))
    .await
    .expect("seed default profile");
    let profile_rows = db
        .query(DbStatement::new("SELECT id FROM permission_profiles"))
        .await
        .expect("profile id");
    let profile_ref = db_get_column::<i64>(&profile_rows[0], "id").expect("profile id");

    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX uk_edge_from_to_env_ref ON edge_grants(from_id, to_id, env, grant_ref_id)",
    ))
    .await
    .expect("create legacy unique index");

    // Friend edge (default profile ref) + an inline rules ref edge + a
    // revoked historical edge — the rows the rebuild must not lose.
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type) \
         VALUES ('local', 'bot-b', 'bot-a', 'permission_profile', ?, NULL, 'approved', 'any')",
        vec![DbValue::from(profile_ref)],
    ))
    .await
    .expect("seed friend edge");
    db.execute(DbStatement::new(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type) \
         VALUES ('local', 'bot-b', 'bot-a', 'rules', 55, '{\"domain\":[\"ops\"]}', 'approved', 'any')",
    ))
    .await
    .expect("seed rules ref edge");
    db.execute(DbStatement::new(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type) \
         VALUES ('local', 'bot-c', 'bot-a', 'rules', 66, NULL, 'revoked', 'any')",
    ))
    .await
    .expect("seed revoked rules edge");
    let pre_ids = db
        .query(DbStatement::new("SELECT id FROM edge_grants ORDER BY id"))
        .await
        .expect("legacy ids");
    let pre_ids: Vec<i64> = pre_ids
        .iter()
        .map(|r| db_get_column::<i64>(r, "id").expect("id"))
        .collect();

    bcs::migrations::run_sqlite_migrations(&db)
        .await
        .expect("apply the full chain with the authority migration");

    // All rows survive with the SAME ids.
    let post = db
        .query(DbStatement::new(
            "SELECT id, grant_kind, grant_ref_id, rules, status FROM edge_grants ORDER BY id",
        ))
        .await
        .expect("rows after migration");
    let post_ids: Vec<i64> = post
        .iter()
        .map(|r| db_get_column::<i64>(r, "id").expect("id"))
        .collect();
    assert_eq!(post_ids, pre_ids, "edge table rebuild must keep original IDs");
    assert_eq!(post.len(), 3);
    let friend = post
        .iter()
        .find(|r| db_get_column::<i64>(r, "grant_ref_id").expect("ref") == profile_ref)
        .expect("friend ref row survived");
    assert_eq!(
        db_get_column::<String>(friend, "grant_kind").expect("kind"),
        "permission_profile"
    );
    let rules = post
        .iter()
        .find(|r| db_get_column::<i64>(r, "grant_ref_id").expect("ref") == 55)
        .expect("inline rules row survived");
    assert_eq!(
        db_get_column::<String>(rules, "rules").expect("rules"),
        "{\"domain\":[\"ops\"]}"
    );
    // Imported rows are non-role edges and adopted the one shared encoding
    // used for both backfill and all new writes.
    let sources = db
        .query(DbStatement::new(
            "SELECT DISTINCT management_source_kind AS kind, management_source_id AS src FROM edge_grants",
        ))
        .await
        .expect("source encodings after backfill");
    assert_eq!(sources.len(), 1);
    assert_eq!(
        db_get_column::<String>(&sources[0], "kind").expect("kind"),
        NON_ROLE_SOURCE_KIND
    );
    assert_eq!(
        db_get_column::<String>(&sources[0], "src").expect("src"),
        NON_ROLE_SOURCE_ID
    );
    // The rebuilt table rejects an old-writer role insert as well.
    let legacy_role_insert = db
        .execute(DbStatement::new(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type) \
             VALUES ('local', 'human_user-z', 'bot-a', 'manager', 0, NULL, 'approved', 'same_as_from')",
        ))
        .await;
    assert!(legacy_role_insert.is_err());
    // The old unique index was replaced, the new unified key is present.
    assert!(index_exists(&db, "uk_edge_from_to_env_kind_ref_source").await);
    assert!(!index_exists(&db, "uk_edge_from_to_env_ref").await);
}

/// Legacy role rows cannot exist in a healthy deployment (no pre-Task-2
/// writer produces them), and corrupt ones must BLOCK the migration loudly,
/// never silently half-persist under a guessed source encoding.
#[tokio::test]
async fn corrupt_legacy_role_row_blocks_the_migration() {
    let db = LocalSqliteDbPlugin::new().expect("open local sqlite");
    db.execute(DbStatement::new(
        "CREATE TABLE edge_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT NOT NULL, \
         from_id TEXT NOT NULL, to_id TEXT NOT NULL, grant_kind TEXT NOT NULL, \
         grant_ref_id INTEGER NOT NULL, rules TEXT, status TEXT NOT NULL DEFAULT 'approved', \
         originator_policy_type TEXT NOT NULL DEFAULT 'any', originator_policy_data TEXT, \
         gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .expect("create legacy edge_grants");
    db.execute(DbStatement::new(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type) \
         VALUES ('local', 'human_user-x', 'bot-a', 'manager', 0, NULL, 'approved', 'same_as_from')",
    ))
    .await
    .expect("seed corrupt legacy role row");

    let migrated = bcs::migrations::run_sqlite_migrations(&db).await;
    assert!(
        migrated.is_err(),
        "unknown-provenance role rows must fail closed, not adopt a guessed encoding"
    );
}

/// The delivery/run operation_id context columns (§12.5 admitted snapshots)
/// are added without backfilling fabricated values: historical rows keep
/// NULL semantics, and fresh delegation commands can persist their context.
#[tokio::test]
async fn legacy_delivery_rows_gain_operation_context_without_backfill() {
    let db = LocalSqliteDbPlugin::new().expect("open local sqlite");
    // The post-031 (pre-032) shapes of the persistent command tables with
    // real historical rows.
    db.execute(DbStatement::new(
        include_str!("../../../../migrations/sqlite/022_message_deliveries.sql"),
    ))
    .await
    .expect("create legacy bcs_message_deliveries");
    db.execute(DbStatement::new(
        "INSERT INTO bcs_message_deliveries (delivery_id, env, source_message_id, target_bot_id, \
         session_id, group_id, source_session_seq, flow_kind, kind, status, state_version, \
         may_have_been_sent, available_at_ms, created_at_ms, updated_at_ms, attempt_no, \
         semantic_projection_json) \
         VALUES ('delivery-1', 'local', 'msg-1', 'bot-b', 'session-1', 'group-1', 1, 'direct', \
         'chat', 'pending', 1, 0, 1000, 1000, 1000, 0, '{}')",
    ))
    .await
    .expect("legacy delivery row");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_chat_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, \
         gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
         gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, env TEXT NOT NULL, \
         run_id TEXT NOT NULL, bot_uuid TEXT NOT NULL, from_bot_id TEXT NOT NULL, \
         session_key TEXT NOT NULL, state TEXT NOT NULL, accumulated_content TEXT, \
         error_message TEXT, original_request TEXT, completed_at_ms INTEGER, \
         expires_at_ms INTEGER NOT NULL, version INTEGER NOT NULL, content_truncated INTEGER NOT NULL DEFAULT 0, \
         client TEXT, response_mode TEXT NOT NULL, completion_policy TEXT NOT NULL, delivery_ack_at_ms INTEGER)",
    ))
    .await
    .expect("create legacy bcs_chat_runs");
    db.execute(DbStatement::new(
        "INSERT INTO bcs_chat_runs (env, run_id, bot_uuid, from_bot_id, session_key, state, \
         expires_at_ms, version, response_mode, completion_policy) \
         VALUES ('local', 'run-1', 'bot-b', 'bot-c', 'session-key', 'running', 2000, 1, 'streaming', 'stop')",
    ))
    .await
    .expect("legacy chat run row");

    bcs::migrations::run_sqlite_migrations(&db)
        .await
        .expect("apply the full chain with the authority migration");

    // Historical row keeps operation_id NULL — no fabricated delegation
    // context for old commands, and no fake Human actors.
    let deliveries = db
        .query(DbStatement::new(
            "SELECT operation_id FROM bcs_message_deliveries WHERE delivery_id = 'delivery-1'",
        ))
        .await
        .expect("deliveries after migration");
    assert_eq!(deliveries[0].get("operation_id"), Some(&DbValue::Null));
    let runs = db
        .query(DbStatement::new(
            "SELECT operation_id FROM bcs_chat_runs WHERE run_id = 'run-1'",
        ))
        .await
        .expect("runs after migration");
    assert_eq!(runs[0].get("operation_id"), Some(&DbValue::Null));
    // Fresh delegation commands can persist their operation context.
    assert!(
        db.execute(DbStatement::new(
            "INSERT INTO bcs_chat_runs (env, run_id, bot_uuid, from_bot_id, session_key, state, \
             expires_at_ms, version, response_mode, completion_policy, operation_id) \
             VALUES ('local', 'run-2', 'bot-b', 'bot-c', 'session-key', 'running', 2000, 1, \
             'streaming', 'stop', 'op-run-2')",
        ))
        .await
        .is_ok()
    );
}

/// Both dialect files must implement the SAME controlled source encoding and
/// agree with the single shared `bcs_domain` constants used by the storage
/// implementations — the SQL literals are locked to those constants by this
/// test, and neither file invents extra constants such as `runtime`/`legacy`
/// (spec §5.1 fixed-encoding requirement). The owner role's edge-kind label
/// is the same shared string as the fixed owner source kind, so the backfill
/// never maintains two "owner" literals.
#[test]
fn sql_files_share_the_domain_source_encoding_constants() {
    let sqlite = include_str!("../../../../migrations/sqlite/032_bot_authority.sql");
    let mysql = include_str!("../../../../migrations/mysql/031_bot_authority.sql");
    for sql in [sqlite, mysql] {
        for (kind, id) in [
            (OWNER_SOURCE_KIND, OWNER_SOURCE_ID),
            (NON_ROLE_SOURCE_KIND, NON_ROLE_SOURCE_ID),
            (DIRECT_SOURCE_KIND, DIRECT_SOURCE_ID),
        ] {
            assert!(
                sql.contains(&format!("'{kind}'")),
                "migration file must encode the shared source kind literal {kind:?}"
            );
            assert!(
                sql.contains(&format!("'{id}'")),
                "migration file must encode the shared source id literal {id:?}"
            );
        }
        assert!(sql.contains("'team'"), "team source kind must be encoded");
        assert!(
            sql.contains("'ownership_transfer'"),
            "transfer source kind must be encoded"
        );
        for stray in ["'runtime'", "'legacy'", "'system_default'", "'unknown_source'"] {
            assert!(!sql.contains(stray), "no fresh source constants allowed: {stray}");
        }
        // Terminal reasons are the §5.2 fixed machine vocabulary.
        for reason in [
            serde_json::to_string(&TerminalReason::BotDeleted).unwrap(),
            serde_json::to_string(&TerminalReason::ActorUnavailable).unwrap(),
            serde_json::to_string(&TerminalReason::OwnerChanged).unwrap(),
        ] {
            let literal = reason.trim_matches('"');
            assert!(
                sql.contains(&format!("'{literal}'")),
                "migration file must encode the terminal reason {literal}"
            );
        }
    }
    // Single encoding source for the owner edge kind and owner source kind.
    assert_eq!(
        serde_json::to_value(GrantKind::Owner).unwrap(),
        serde_json::json!(OWNER_SOURCE_KIND)
    );
    assert_eq!(
        serde_json::to_value(GrantKind::Manager).unwrap(),
        serde_json::json!("manager")
    );
    assert_eq!(UNINITIALIZED_OWNERSHIP_VERSION, 0);
    assert_eq!(INITIALIZED_OWNERSHIP_VERSION, 1);
}
