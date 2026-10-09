//! MySQL live conformance for the bot authority migration (plan Task 2).
//!
//! Ignored by default: requires `BCS_TEST_MYSQL_URL` and follows the pattern of
//! `crates/services/bcs-event-store/tests/conformance_event_store_mysql.rs`.
//! No MySQL server is reachable in this dev environment (port closed, no
//! client), so live MySQL behavior — generated unique slots, the FOR UPDATE
//! row-lock transaction shape, CHECK enforcement, case-sensitive utf8mb4_bin
//! identities — is UNVERIFIED here. CI runs these tests against its MySQL
//! service. The SQLite proofs live in `bot_authority_migration.rs`.

use bcs_db_api::{DbPlugin, DbStatement, DbTransactionParam, DbTransactionStep, DbValue};
use bcs_domain::INITIALIZED_OWNERSHIP_VERSION;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_authority_schema_conformance() {
    use std::collections::BTreeMap;

    let url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL contract");
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
    // A single pooled connection keeps session timezone and DDL order stable.
    let mut config = config;
    config.pool_size = 1;
    config.min_pool_size = 1;
    let manager = bcs_db_mysql::MysqlDbManager::new(config)
        .await
        .expect("open MySQL contract datasource");
    let plugin = std::sync::Arc::new(bcs_db_mysql::MysqlDbPlugin::new(manager.clone(), database));

    apply_full_mysql_chain(plugin.as_ref()).await;

    // --- §5.3 MySQL generated slot: second approved owner must fail. ---
    let seed_first_owner = plugin
        .execute(DbStatement::new(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
             originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
             VALUES ('local', 'human_user-a', 'bot-mysql', 'owner', 0, NULL, 'approved', 'same_as_from', NULL, 'owner', 'owner')",
        ))
        .await;
    assert!(seed_first_owner.is_ok(), "seed first approved owner");
    let second_owner = plugin
        .execute(DbStatement::new(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
             originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
             VALUES ('local', 'human_user-b', 'bot-mysql', 'owner', 0, NULL, 'approved', 'same_as_from', NULL, 'owner', 'owner')",
        ))
        .await;
    assert!(
        second_owner.is_err(),
        "second approved owner must trip uk_edge_active_owner_slot"
    );
    // The old-writer role INSERT (no source columns) must fail closed on
    // MySQL too (CHECK + NOT NULL source columns).
    let legacy_role_insert = plugin
        .execute(DbStatement::new(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type) \
             VALUES ('local', 'human_user-c', 'bot-mysql', 'manager', 0, NULL, 'approved', 'same_as_from')",
        ))
        .await;
    assert!(
        legacy_role_insert.is_err(),
        "role INSERT without source columns must be rejected by the generated slot / CHECK constraints"
    );

    // --- case-sensitive identities on the authority tables (utf8mb4_bin). ---
    for (audit_id, subject, team) in [
        ("audit-a", "human_user-M", "Team-X"),
        ("audit-b", "human_user-m", "team-x"),
    ] {
        let statement = DbStatement::new(format!(
            "INSERT INTO bot_manager_changes (audit_id, env, bot_id, subject_user_id, edge_id, \
             management_source_kind, management_source_id, action, actor_kind, actor_id, \
             operation_id, decided_at) VALUES ('{audit_id}', 'local', 'bot-mysql', '{subject}', 1, \
             'team', '{team}', 'grant', 'human', '{subject}', 'op-{audit_id}', NOW())"
        ));
        let inserted = plugin.execute(statement).await;
        assert!(inserted.is_ok(), "case-distinct identity must not merge: {audit_id}");
    }

    // --- §5.3 pending transfer generated slot + idempotency key. ---
    for (transfer_id, to_user, client_request_id) in
        [("transfer-m-1", "human_user-b", "cr-m-1"), ("transfer-m-2", "human_user-c", "cr-m-2")]
    {
        let statement = DbStatement::new(format!(
            "INSERT INTO bot_ownership_transfers (transfer_id, env, bot_id, from_user_id, to_user_id, \
             expected_owner_version, client_request_id, status, expires_at) \
             VALUES ('{transfer_id}', 'local', 'bot-mysql', 'human_user-a', '{to_user}', 1, \
             '{client_request_id}', 'pending', '2030-01-01 00:00:00')"
        ));
        let inserted = plugin.execute(statement).await;
        if transfer_id == "transfer-m-1" {
            assert!(inserted.is_ok(), "first pending transfer commits");
        } else {
            assert!(
                inserted.is_err(),
                "second pending transfer must trip uk_bot_transfer_pending_slot"
            );
        }
    }

    // --- FOR UPDATE row-lock transaction expressibility on MySQL (brief
    // step 3): lock the precise Bot row, CAS-bump its ownership_version with
    // the locked read, and write the audit step in the SAME transaction. ---
    plugin
        .execute(DbStatement::new(
            "INSERT INTO bcs_bots (bot_uuid, name, env) VALUES ('bot-mysql', 'MySQL Bot', 'local')",
        ))
        .await
        .expect("seed bot row");
    let committed = plugin
        .transaction(vec![
            DbTransactionStep::Query(DbStatement::with_params(
                "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ? AND env = ? FOR UPDATE",
                vec![DbValue::from("bot-mysql"), DbValue::from("local")],
            )),
            DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_transaction_params(
                    "UPDATE bcs_bots SET ownership_version = ownership_version + 1 \
                     WHERE bot_uuid = ? AND env = ? AND ownership_version = ?",
                    vec![
                        DbTransactionParam::value("bot-mysql"),
                        DbTransactionParam::value("local"),
                        // bound from the FOR UPDATE read: decided by the
                        // locked read inside the transaction, not by data
                        // read before acquiring the lock
                        DbTransactionParam::query_result(0, 0, "ownership_version"),
                    ],
                ),
                expected_affected_rows: 1,
            },
            DbTransactionStep::Execute(DbStatement::new(
                "INSERT INTO bcs_bot_action_audits (audit_id, env, operation_id, step_key, \
                 operator_kind, operator_id, operator_user_id, effective_actor_id, resource_kind, \
                 resource_id, action, phase) VALUES ('audit-m-1', 'local', 'op-mysql-lock', \
                 'update/bot/applied', 'human', 'user-a', 'user-a', 'bot-mysql', 'bot', \
                 'bot-mysql', 'update', 'applied')",
            )),
        ])
        .await
        .expect("FOR UPDATE + CAS + audit must commit atomically on MySQL");
    assert_eq!(committed.len(), 3);
    let versions = plugin
        .query(DbStatement::with_params(
            "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ?",
            vec![DbValue::from("bot-mysql")],
        ))
        .await
        .expect("bot version after transaction");
    assert_eq!(
        versions[0].get("ownership_version"),
        Some(&DbValue::I64(INITIALIZED_OWNERSHIP_VERSION as i64)),
        "the CAS must have bumped the version from 0 to 1"
    );
    manager.close().await;
}

/// Plan Task 4 MySQL live conformance: concurrent manager MUTUAL revocation
/// on real MySQL — FOR UPDATE row locking through the production
/// `DbBotAuthorityStore::mysql` mutation must serialize so exactly ONE side
/// revokes and the stale side surfaces 403, with one audit row recording
/// the actual change (§5.4/§10). Ignored like the suite above: no MySQL
/// server exists in this dev environment; CI runs it against its MySQL
/// service, so live MySQL behavior stays UNVERIFIED locally.
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_manager_mutation_concurrent_revocation_lock_order() {
    use bcs_edge_permission_store::DbBotAuthorityStore;
    use bcs_service_api::port::repo::BotAuthorityRepoPort;
    use bcs_service_api::types::{AuditActor, ManagerMutation};
    use std::collections::BTreeMap;

    let url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL contract");
    let opts = mysql_async::Opts::from_url(&url).expect("valid BCS_TEST_MYSQL_URL");
    let database = opts
        .db_name()
        .expect("BCS_TEST_MYSQL_URL includes a database name")
        .to_string();
    let mut config = bcs_config_api::MysqlDbConfig::new()
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
    // Two connections so the two mutations can truly contend for the row.
    config.pool_size = 2;
    config.min_pool_size = 2;
    let manager = bcs_db_mysql::MysqlDbManager::new(config)
        .await
        .expect("open MySQL mutation datasource");
    let plugin: std::sync::Arc<dyn DbPlugin> =
        std::sync::Arc::new(bcs_db_mysql::MysqlDbPlugin::new(manager.clone(), database));
    apply_full_mysql_chain(plugin.as_ref()).await;

    let bot = "bot-mysql-mut";
    for statement in [
        "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) \
         VALUES ('bot-mysql-mut', 'MySQL Mut Bot', 'local', 1)",
        "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) \
         VALUES ('human_user-a', 'a', 'local', 'human', 'online'), \
                ('human_user-b', 'b', 'local', 'human', 'online'), \
         ('human_user-c', 'c', 'local', 'human', 'online')",
    ] {
        plugin
            .execute(DbStatement::new(statement))
            .await
            .expect("seed bot/actors");
    }
    for human in ["human_user-a", "human_user-b", "human_user-c"] {
        let (kind, id) = if human == "human_user-a" { ("owner", "owner") } else { ("direct", "manual") };
        plugin
            .execute(DbStatement::new(format!(
                "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                 status, originator_policy_type, originator_policy_data, \
                 management_source_kind, management_source_id) \
                 VALUES ('local', '{human}', '{bot}', '{}', 0, NULL, 'approved', 'same_as_from', \
                 NULL, '{kind}', '{id}')",
                if human == "human_user-a" { "owner" } else { "manager" }
            )))
            .await
            .expect("seed role edge");
    }

    let repo = DbBotAuthorityStore::mysql(plugin.clone(), "local".to_string());
    let (rm_b, rm_c) = tokio::join!(
        async {
            repo.mutate_manager(
                AuditActor::Human { user_id: "user-b".into() },
                bot,
                ManagerMutation::RevokeNonTeam { user_id: "user-c".into() },
            )
            .await
        },
        async {
            repo.mutate_manager(
                AuditActor::Human { user_id: "user-c".into() },
                bot,
                ManagerMutation::RevokeNonTeam { user_id: "user-b".into() },
            )
            .await
        },
    );
    let changed = [&rm_b, &rm_c]
        .iter()
        .filter(|result| result.as_ref().map(|r| r.changed).unwrap_or(false))
        .count();
    let forbidden = [&rm_b, &rm_c]
        .iter()
        .any(|result| matches!(
            result,
            Err(bcs_service_api::ServiceError::Authority(
                bcs_service_api::types::error::AuthorityError::Forbidden(_)
            ))
        ));
    assert_eq!(changed, 1, "exactly one concurrent revoke may change; got {rm_b:?} / {rm_c:?}");
    assert!(forbidden, "the stale side must surface 403");
    let rows = plugin
        .query(DbStatement::new("SELECT COUNT(*) AS n FROM bot_manager_changes"))
        .await
        .expect("audit count");
    assert_eq!(rows[0].get_i64("n").ok().flatten().expect("n"), 1);
    manager.close().await;
}

/// Apply the full external MySQL chain 001..031 the way the runner/ops would:
/// one file at a time, `--`-comment lines stripped, split on statement
/// boundaries.
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
    for (index, file) in MYSQL_CHAIN.iter().enumerate() {
        let body: String = file
            .lines()
            .filter(|line| !line.trim_start().starts_with("--"))
            .collect::<Vec<_>>()
            .join("\n");
        for statement in body.split(';').map(str::trim).filter(|s| !s.is_empty()) {
            db.execute(DbStatement::new(statement))
                .await
                .unwrap_or_else(|error| panic!("apply mysql chain file {index}: {error}\n{statement}"));
        }
    }
}
