//! Plan Task 7: team sync/move aggregate transaction with persistent
//! idempotency — the test binary entry. The shared drivers and the
//! Memory/SQLite conformance suite live in `team_manager_sync_common`
//! (loaded via `#[path]`); the individual tests are declared here.
//!
//! SQLite-specific proofs (real DB locking, one-transaction atomic
//! assembly, raw row inspection): the deterministic TOCTOU drift
//! revalidation, the same-key concurrency (one committed operation + an
//! idempotent replay), the statement budget of a full 1,000-Human
//! snapshot (batched per chunk, never per subject), and the cross-env
//! member rejection. MySQL live conformance (two instances, same key +
//! move concurrency) is `#[ignore]`d on `BCS_TEST_MYSQL_URL` like the
//! bootstrap authority tests: this dev environment has no MySQL server
//! reachable, so live MySQL behavior is UNVERIFIED here; CI runs these
//! contracts against its MySQL service.

#[path = "team_manager_sync_common/mod.rs"]
mod common;

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::ServiceError;
use bcs_service_api::types::error::AuthorityError;

use common::{
    memory_harness, sqlite_harness, sync_cmd, team_manager_sync_contract_tests, verified_service,
};

#[tokio::test]
async fn sqlite_team_manager_sync_contract() {
    let (h, _) = sqlite_harness().await;
    team_manager_sync_contract_tests(&h).await;
}

#[tokio::test]
async fn memory_team_manager_sync_contract() {
    let h = memory_harness().await;
    team_manager_sync_contract_tests(&h).await;
}

// ---------------------------------------------------------------------------
// SQLite-specific proofs (raw DB + the SyncDb observation levers)
// ---------------------------------------------------------------------------

/// Deterministic TOCTOU drift: the snapshot's human dies BETWEEN the
/// validated read and the write transaction. The changing statement's
/// in-transaction subject guard yields zero rows, the pin rolls the
/// WHOLE attempt back, and the retry re-validates and surfaces
/// InvalidSubject — no edge, no audit row, no receipt survives.
#[tokio::test]
async fn sqlite_drifted_subject_rolls_back_and_revalidates() {
    let (h, db) = sqlite_harness().await;
    h.driver.seed_owned("bot-drift", "dro").await;
    h.driver.seed_humans(&["dro", "d1"]).await;
    db.arm_drift("UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = 'human_d1'");
    match h
        .repo
        .sync_team(sync_cmd(
            verified_service(&h.driver.env()),
            "bot-drift",
            "team-d",
            &["d1"],
            "drift-1",
        ))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::InvalidSubject(_))) => {}
        other => panic!(
            "a member dead at commit time must surface InvalidSubject, got {:?}",
            other.map(|r| r.granted_count)
        ),
    }
    assert_eq!(h.repo.role("d1", "bot-drift").await.unwrap(), None);
    assert_eq!(h.driver.audit_count().await, 0, "no audit rows for the aborted grant");
    assert_eq!(
        h.driver.sync_receipt_row_count("bot-drift").await,
        0,
        "no durable receipt may survive the roll back"
    );
}

/// Same-key concurrency on one real SQLite store: exactly ONE operation
/// commits and the concurrent duplicate wipes into the idempotent
/// replay — both callers return the SAME receipt, one receipt row, one
/// audit row for the one granted edge.
#[tokio::test]
async fn sqlite_concurrent_same_key_yields_one_committed_operation() {
    let (h, _) = sqlite_harness().await;
    h.driver.seed_owned("bot-race", "ro").await;
    h.driver.seed_humans(&["ro", "rr"]).await;

    let a = h.repo.clone();
    let b = h.repo.clone();
    let command = |key: &'static str| {
        sync_cmd(
            verified_service(&h.driver.env()),
            "bot-race",
            "team-race",
            &["rr"],
            key,
        )
    };
    let (ra, rb) = tokio::join!(
        async { a.sync_team(command("race-1")).await },
        async { b.sync_team(command("race-1")).await },
    );
    let one = ra.expect("the winner commits");
    let two = rb.expect("the loser replays the winner's receipt");
    assert_eq!(one, two, "both callers observe the same committed receipt");
    assert_eq!(one.granted_count, 1);
    assert_eq!(h.driver.sync_receipt_row_count("bot-race").await, 1);
    assert_eq!(h.driver.audit_count().await, 1, "exactly one audited grant");
    assert_eq!(
        h.repo.role("rr", "bot-race").await.unwrap(),
        Some(bcs_domain::BotAccessRelation::Manager)
    );
}

/// The statement budget of a full 1,000-Human snapshot: the commit runs
/// as exactly TWO transactions (one validated read + ONE all-or-nothing
/// write) and ZERO per-row statements — batch chunked SQL, never N+1,
/// and the atomic snapshot is never assembled from multiple
/// transactions.
#[tokio::test]
async fn sqlite_bulk_snapshot_uses_bounded_statement_budget() {
    let (h, db) = sqlite_harness().await;
    h.driver.seed_owned("bot-budget", "bo").await;
    let thousand: Vec<String> = (0..1_000).map(|i| format!("bg-{i:03}")).collect();
    h.driver
        .seed_humans(&thousand.iter().map(|s| s.as_str()).collect::<Vec<_>>())
        .await;

    let tx_before = db.transaction_count();
    let q_before = db.query_count();
    let receipt = h
        .repo
        .sync_team(sync_cmd(
            verified_service(&h.driver.env()),
            "bot-budget",
            "team-budget",
            &thousand.iter().map(|s| s.as_str()).collect::<Vec<_>>(),
            "budget-1",
        ))
        .await
        .unwrap();
    assert_eq!(receipt.granted_count, 1_000);
    assert_eq!(receipt.revoked_count, 0);
    let tx_used = db.transaction_count() - tx_before;
    let q_used = db.query_count() - q_before;
    assert_eq!(
        tx_used, 2,
        "one validated read transaction + ONE aggregate write transaction"
    );
    assert_eq!(
        q_used, 0,
        "no per-chunk standalone queries and no per-subject N+1"
    );
    // Every one of the 1,000 granted edges is audited through the batched
    // INSERT SELECT chunks.
    assert_eq!(h.driver.audit_count().await, 1_000);
    assert_eq!(h.driver.sync_receipt_row_count("bot-budget").await, 1);
}

/// REMOVING a member who has since died is a cleanup, not a drift
/// violation: an empty snapshot (or any snapshot without the dead
/// member) revokes their edge and converges — the desired snapshot's
/// members are what require liveness validation, not the members being
/// taken away.
#[tokio::test]
async fn sqlite_removing_a_dead_member_cleans_up_instead_of_failing() {
    let (h, db) = sqlite_harness().await;
    h.driver.seed_owned("bot-dead", "do").await;
    h.driver.seed_humans(&["do", "dspy"]).await;
    h.driver.seed_manager_source("bot-dead", "dspy", "team", "team-dd").await;
    db.inner
        .execute(DbStatement::new(
            "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = 'human_dspy'",
        ))
        .await
        .expect("soft-delete the seeded member");
    let receipt = h
        .repo
        .sync_team(sync_cmd(
            verified_service(&h.driver.env()),
            "bot-dead",
            "team-dd",
            &[],
            "dd-1",
        ))
        .await
        .expect("removal of a dead member must converge");
    assert_eq!(receipt.granted_count, 0);
    assert_eq!(receipt.revoked_count, 1);
    assert_eq!(h.repo.role("dspy", "bot-dead").await.unwrap(), None);
    assert_eq!(h.driver.sync_receipt_row_count("bot-dead").await, 1);
}

/// A snapshot member materialized in ANOTHER env is not a live same-env
/// human: InvalidSubject, nothing applied.
#[tokio::test]
async fn sqlite_cross_env_member_rejected() {
    let (h, db) = sqlite_harness().await;
    h.driver.seed_owned("bot-x", "xo").await;
    db.inner
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) \
             VALUES (?, ?, ?, 'human', 'online')",
            vec![
                DbValue::from("human_xenv-user"),
                DbValue::from("xenv-user"),
                DbValue::from("other-env"),
            ],
        ))
        .await
        .expect("seed the cross-env human row");
    match h
        .repo
        .sync_team(sync_cmd(
            verified_service(&h.driver.env()),
            "bot-x",
            "team-x",
            &["xenv-user"],
            "xenv-1",
        ))
        .await
    {
        Err(ServiceError::Authority(AuthorityError::InvalidSubject(_))) => {}
        other => panic!(
            "a cross-env member must be InvalidSubject, got {:?}",
            other.map(|r| r.granted_count)
        ),
    }
    assert_eq!(h.repo.role("xenv-user", "bot-x").await.unwrap(), None);
    assert_eq!(h.driver.sync_receipt_row_count("bot-x").await, 0);
}

/// Raw-row proof of the restore semantic: a revoked team slot row is
/// RESTORED under the SAME row id by a later sync — never a second slot.
#[tokio::test]
async fn sqlite_revoked_team_row_restores_same_edge_id() {
    let (h, db) = sqlite_harness().await;
    h.driver.seed_owned("bot-rid", "ro").await;
    h.driver.seed_humans(&["ro", "r1"]).await;
    h.driver.seed_manager_source("bot-rid", "r1", "team", "team-rid").await;

    let read_edge_ids = || {
        let sql = "SELECT id FROM edge_grants \
             WHERE env = 'local' AND to_id = 'bot-rid' AND from_id = 'human_r1' \
               AND grant_kind = 'manager' AND management_source_kind = 'team' \
               AND management_source_id = 'team-rid' ORDER BY id";
        sql.to_string()
    };
    let ids_before = {
        let rows = db
            .inner
            .query(DbStatement::new(read_edge_ids()))
            .await
            .expect("read seeded edge ids");
        rows.iter().map(|row| row.get_i64("id").ok().flatten().unwrap_or(0)).collect::<Vec<i64>>()
    };
    assert_eq!(ids_before.len(), 1);

    // Revoke via an empty sync, then re-add: same row id restored.
    h.repo
        .sync_team(sync_cmd(
            verified_service(&h.driver.env()),
            "bot-rid",
            "team-rid",
            &[],
            "rid-1",
        ))
        .await
        .unwrap();
    let readd = h
        .repo
        .sync_team(sync_cmd(
            verified_service(&h.driver.env()),
            "bot-rid",
            "team-rid",
            &["r1"],
            "rid-2",
        ))
        .await
        .unwrap();
    assert_eq!(readd.granted_count, 1, "the revoked row was restored");
    let ids_after = {
        let rows = db
            .inner
            .query(DbStatement::new(read_edge_ids()))
            .await
            .expect("read restored edge ids");
        rows.iter().map(|row| row.get_i64("id").ok().flatten().unwrap_or(0)).collect::<Vec<i64>>()
    };
    assert_eq!(ids_after, ids_before, "restore reuses the SAME row id");
    assert_eq!(ids_after.len(), 1, "never a second slot");
}

/// The audit rows record the TRUE operator: the Service actor derived
/// from the verified credential — never a client actor or the subject —
/// and one operation_id groups the whole committed operation.
#[tokio::test]
async fn sqlite_audit_records_service_actor_and_operation_id() {
    let (h, db) = sqlite_harness().await;
    h.driver.seed_owned("bot-av", "ao").await;
    h.driver.seed_humans(&["ao", "av1", "av2"]).await;
    h.driver.seed_manager_source("bot-av", "av2", "team", "team-av").await;
    let receipt = h
        .repo
        .sync_team(sync_cmd(
            verified_service(&h.driver.env()),
            "bot-av",
            "team-av",
            &["av1"],
            "av-1",
        ))
        .await
        .unwrap();
    assert_eq!(receipt.granted_count, 1);
    assert_eq!(receipt.revoked_count, 1);

    let rows = db
        .inner
        .query(DbStatement::with_params(
            "SELECT actor_kind, actor_id, action, subject_user_id, operation_id, \
               management_source_kind, management_source_id \
             FROM bot_manager_changes WHERE env = ? AND bot_id = ? ORDER BY id",
            vec![
                DbValue::from("local"),
                DbValue::from("bot-av"),
            ],
        ))
        .await
        .expect("read audit rows");
    assert_eq!(rows.len(), 2, "one revoke + one grant");
    for row in &rows {
        assert_eq!(
            row.get_string("actor_kind").ok().flatten(),
            Some("service".to_string()),
            "team-sync audit rows record the service actor kind"
        );
        assert_eq!(
            row.get_string("actor_id").ok().flatten(),
            Some("team-sync-svc".to_string()),
            "the audit records the verified service id, never the subject"
        );
        assert_eq!(
            row.get_string("operation_id").ok().flatten(),
            Some(receipt.operation_id.clone()),
            "one operation_id groups the whole committed operation and matches receipt"
        );
        assert_eq!(
            row.get_string("management_source_kind").ok().flatten(),
            Some("team".to_string())
        );
        assert_eq!(
            row.get_string("management_source_id").ok().flatten(),
            Some("team-av".to_string())
        );
    }
    let subjects: Vec<String> = rows
        .iter()
        .map(|row| row.get_string("subject_user_id").ok().flatten().unwrap_or_default())
        .collect();
    assert_eq!(
        subjects,
        vec!["av2".to_string(), "av1".to_string()],
        "subject ids decode from the shared human_<uid> actor shape"
    );
    let actions: Vec<String> = rows
        .iter()
        .map(|row| row.get_string("action").ok().flatten().unwrap_or_default())
        .collect();
    assert_eq!(actions, vec!["revoke".to_string(), "grant".to_string()]);
}

// ---------------------------------------------------------------------------
// MySQL live conformance (ignored: requires BCS_TEST_MYSQL_URL)
// ---------------------------------------------------------------------------

/// Applies the full MySQL migration chain (the exact schema production
/// startup builds; same pattern as the bootstrap authority MySQL tests).
async fn apply_full_mysql_chain(db: &dyn DbPlugin) {
    const MYSQL_CHAIN: &[&str] = &[
        include_str!("../../../../migrations/mysql/001_init_schema.sql"),
        include_str!("../../../../migrations/mysql/002_add_owner_bot_id.sql"),
        include_str!("../../../../migrations/mysql/003_add_organizations.sql"),
        include_str!("../../../../migrations/mysql/004_add_session_collection.sql"),
        include_str!("../../../../migrations/mysql/005_add_session_collection_timestamp.sql"),
        include_str!("../../../../migrations/mysql/006_session_files.sql"),
        include_str!("../../../../migrations/mysql/007_add_human_input_runtime.sql"),
        include_str!("../../../../migrations/mysql/008_human_input_im_requests.sql"),
        include_str!("../../../../migrations/mysql/009_eventing.sql"),
        include_str!("../../../../migrations/mysql/010_group_opening_message.sql"),
        include_str!("../../../../migrations/mysql/011_group_participant_tags.sql"),
        include_str!("../../../../migrations/mysql/012_expand_session_ids.sql"),
        include_str!("../../../../migrations/mysql/013_add_bot_task_modes.sql"),
        include_str!("../../../../migrations/mysql/014_edge_permission.sql"),
        include_str!("../../../../migrations/mysql/015_add_bot_internal_attributes.sql"),
        include_str!("../../../../migrations/mysql/016_session_callback_lease_and_chat_runs.sql"),
        include_str!("../../../../migrations/mysql/017_state_machine_rerun_lineage.sql"),
        include_str!("../../../../migrations/mysql/018_one_shot_opening_message_override.sql"),
        include_str!("../../../../migrations/mysql/019_invite_code.sql"),
        include_str!("../../../../migrations/mysql/020_human_participant_message_visibility.sql"),
        include_str!("../../../../migrations/mysql/021_message_deliveries.sql"),
        include_str!("../../../../migrations/mysql/022_message_delivery_policy.sql"),
        include_str!("../../../../migrations/mysql/023_delivery_worker_queries.sql"),
        include_str!("../../../../migrations/mysql/024_delivery_context_selection.sql"),
        include_str!("../../../../migrations/mysql/025_delivery_pending_abort.sql"),
        include_str!("../../../../migrations/mysql/026_run_reply_segments.sql"),
        include_str!("../../../../migrations/mysql/027_provider_bot_webhook.sql"),
        include_str!("../../../../migrations/mysql/028_fixed_loop_runtime.sql"),
        include_str!("../../../../migrations/mysql/029_bot_provider_storage.sql"),
        include_str!("../../../../migrations/mysql/030_group_human_mention_notify_mode.sql"),
        include_str!("../../../../migrations/mysql/033_bot_authority.sql"),
    ];
    for file in MYSQL_CHAIN {
        let body: String = file
            .lines()
            .filter(|line| !line.trim_start().starts_with("--"))
            .collect::<Vec<_>>()
            .join("\n");
        for statement in body.split(';').map(str::trim).filter(|s| !s.is_empty()) {
            db.execute(DbStatement::new(statement))
                .await
                .unwrap_or_else(|err| panic!("apply mysql chain statement: {err}"));
        }
    }
}

async fn mysql_plugin() -> (Arc<bcs_db_mysql::MysqlDbPlugin>, String) {
    let url = std::env::var("BCS_TEST_MYSQL_URL")
        .unwrap_or_else(|_| "BCS_TEST_MYSQL_URL must be set for the ignored MySQL contract".into());
    let opts = mysql_async::Opts::from_url(&url).expect("valid BCS_TEST_MYSQL_URL");
    let database = opts
        .db_name()
        .expect("BCS_TEST_MYSQL_URL includes a database name")
        .to_string();
    let config = bcs_config_api::MysqlDbConfig::new()
        .with_database(&database)
        .with_connection(bcs_config_api::mysql::MysqlConnectionConfig {
            connection_type: "direct".to_string(),
            host: Some(opts.ip_or_hostname().to_string()),
            port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string),
            password: opts.pass().map(str::to_string),
            extra: BTreeMap::new(),
        })
        .with_statement_protocol(bcs_config_api::StatementProtocol::Text);
    let mut config = config;
    // A single pooled connection keeps row locks and session state stable
    // for the entire concurrent contract.
    config.pool_size = 1;
    config.min_pool_size = 1;
    let manager = bcs_db_mysql::MysqlDbManager::new(config)
        .await
        .expect("open MySQL contract datasource");
    let plugin = Arc::new(bcs_db_mysql::MysqlDbPlugin::new(manager, database.clone()));
    apply_full_mysql_chain(plugin.as_ref()).await;
    (plugin, database)
}

/// MySQL live conformance, two instances, same key: TWO store instances
/// racing one idempotency key with the SAME payload commit exactly one
/// operation; both callers return the same receipt (the loser replays),
/// exactly one durable receipt row and one audit row exist.
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service (no MySQL server is reachable in this dev environment — live MySQL behavior UNVERIFIED here)"]
async fn mysql_two_instances_same_key_single_committed_operation() {
    let (plugin, env_database) = mysql_plugin().await;
    let store_a = DbBotAuthorityStore::mysql(plugin.clone(), "local".to_string());
    let store_b = DbBotAuthorityStore::mysql(plugin.clone(), "local".to_string());
    let repo_a: Arc<dyn BotAuthorityRepoPort> = Arc::new(store_a);
    let repo_b: Arc<dyn BotAuthorityRepoPort> = Arc::new(store_b);

    for statement in [
        "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) \
         VALUES ('bot-mysql-sync', 'Sync Bot', 'local', 1)",
        "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) \
         VALUES ('human_ms-a', 'a', 'local', 'human', 'online'), \
                ('human_ms-m1', 'm1', 'local', 'human', 'online')",
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
         VALUES ('local', 'human_ms-a', 'bot-mysql-sync', 'owner', 0, NULL, 'approved', \
         'same_as_from', NULL, 'owner', 'owner')",
    ] {
        plugin
            .execute(DbStatement::new(statement))
            .await
            .expect("seed mysql fixture");
    }

    let command = || {
        sync_cmd(
            verified_service("local"),
            "bot-mysql-sync",
            "team-mysql",
            &["ms-m1"],
            "mysql-key-1",
        )
    };
    let (ra, rb) = tokio::join!(
        async { repo_a.sync_team(command()).await },
        async { repo_b.sync_team(command()).await },
    );
    let one = ra.expect("the winner commits");
    let two = rb.expect("the loser replays the winner's receipt");
    assert_eq!(one, two);
    assert_eq!(one.granted_count, 1);
    // One durable receipt row.
    let rows = plugin
        .query(DbStatement::new(
            "SELECT COUNT(*) AS n FROM bot_manager_sync_operations \
             WHERE env = 'local' AND bot_id = 'bot-mysql-sync'",
        ))
        .await
        .expect("count receipts");
    assert_eq!(row_count(&rows), 1);
    // Exactly one audited grant.
    let rows = plugin
        .query(DbStatement::new(
            "SELECT COUNT(*) AS n FROM bot_manager_changes \
             WHERE env = 'local' AND bot_id = 'bot-mysql-sync'",
        ))
        .await
        .expect("count audit rows");
    assert_eq!(row_count(&rows), 1);
    let _ = env_database;
}

/// MySQL live conformance, two instances, one move racing: TOCTOU-proof
/// aggregate move — one committed move, the same receipt for both
/// callers, the old team stopped with its edges revoked, the new team
/// holding the snapshot exactly once.
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service (no MySQL server is reachable in this dev environment — live MySQL behavior UNVERIFIED here)"]
async fn mysql_two_instances_concurrent_move_single_committed_operation() {
    let (plugin, _db) = mysql_plugin().await;
    let repo_a: Arc<dyn BotAuthorityRepoPort> = Arc::new(DbBotAuthorityStore::mysql(
        plugin.clone(),
        "local".to_string(),
    ));
    let repo_b: Arc<dyn BotAuthorityRepoPort> = Arc::new(DbBotAuthorityStore::mysql(
        plugin.clone(),
        "local".to_string(),
    ));

    for statement in [
        "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) \
         VALUES ('bot-mysql-move', 'Move Bot', 'local', 1)",
        "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) \
         VALUES ('human_mv-a', 'a', 'local', 'human', 'online'), \
                ('human_mv-m1', 'm1', 'local', 'human', 'online')",
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
         VALUES ('local', 'human_mv-a', 'bot-mysql-move', 'owner', 0, NULL, 'approved', \
         'same_as_from', NULL, 'owner', 'owner')",
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
         VALUES ('local', 'human_mv-m1', 'bot-mysql-move', 'manager', 0, NULL, 'approved', \
         'same_as_from', NULL, 'team', 'team-mysql-old')",
        "INSERT INTO bot_team_manager_sources (env, bot_id, team_id, status) \
         VALUES ('local', 'bot-mysql-move', 'team-mysql-old', 'active')",
    ] {
        plugin
            .execute(DbStatement::new(statement))
            .await
            .expect("seed mysql move fixture");
    }

    let command = || {
        common::move_cmd(
            verified_service("local"),
            "bot-mysql-move",
            "team-mysql-old",
            "team-mysql-new",
            &["mv-m1"],
            "mysql-move-key-1",
        )
    };
    let (ra, rb) = tokio::join!(
        async { repo_a.sync_team(command()).await },
        async { repo_b.sync_team(command()).await },
    );
    let one = ra.expect("the winner commits the move");
    let two = rb.expect("the loser replays the move's receipt");
    assert_eq!(one, two);
    assert_eq!(one.granted_count, 1, "mv-m1 granted at the new team");
    assert_eq!(one.revoked_count, 1, "m1's old-team edge revoked");

    // The new team holds the snapshot exactly once; the old team holds
    // zero approved edges and its binding is stopped.
    let rows = plugin
        .query(DbStatement::new(
            "SELECT COUNT(*) AS n FROM edge_grants \
             WHERE env = 'local' AND to_id = 'bot-mysql-move' AND grant_kind = 'manager' \
               AND management_source_kind = 'team' AND management_source_id = 'team-mysql-new' \
               AND status = 'approved'",
        ))
        .await
        .expect("count new-team edges");
    assert_eq!(row_count(&rows), 1);
    let rows = plugin
        .query(DbStatement::new(
            "SELECT COUNT(*) AS n FROM edge_grants \
             WHERE env = 'local' AND to_id = 'bot-mysql-move' AND grant_kind = 'manager' \
               AND management_source_kind = 'team' AND management_source_id = 'team-mysql-old' \
               AND status = 'approved'",
        ))
        .await
        .expect("count old-team edges");
    assert_eq!(row_count(&rows), 0);
    let rows = plugin
        .query(DbStatement::new(
            "SELECT team_id, status FROM bot_team_manager_sources \
             WHERE env = 'local' AND bot_id = 'bot-mysql-move' ORDER BY team_id",
        ))
        .await
        .expect("read bindings");
    let bindings: Vec<(String, String)> = rows
        .iter()
        .map(|row| {
            (
                row.get_string("team_id").ok().flatten().unwrap_or_default(),
                row.get_string("status").ok().flatten().unwrap_or_default(),
            )
        })
        .collect();
    assert_eq!(
        bindings,
        vec![
            ("team-mysql-new".to_string(), "active".to_string()),
            ("team-mysql-old".to_string(), "stopped".to_string()),
        ]
    );
}

fn row_count(rows: &[bcs_db_api::DbRow]) -> u64 {
    rows.first()
        .and_then(|row| row.get_i64("n").ok().flatten())
        .unwrap_or(0)
        .max(0) as u64
}