//! MySQL live conformance for the ownership transfer lanes (plan Task 8).
//!
//! Ignored by default: requires `BCS_TEST_MYSQL_URL` and follows
//! `tests/bot_authority_mysql.rs`. No MySQL server is reachable in this
//! dev environment (port closed, no client), so the cross-CONNECTION
//! unique-key races — the pending slot (uk_bot_transfer_pending_slot), the
//! durable idempotency key (uk_bot_transfer_client_request) and the
//! all-or-nothing acceptance against two simultaneous deciders — are
//! UNVERIFIED here. CI runs these tests against its MySQL service. The
//! SQLite proofs live in
//! `crates/services/bcs-edge-permission-store/tests/ownership_transfer.rs`.

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_domain::{TransferAction, TransferStatus};
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult,
};

/// The full MySQL chain, shared with the Task 2 conformance (same files,
/// same order — the transfer schema is exactly what production startup
/// builds).
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
        include_str!("../../../../migrations/mysql/031_bot_authority.sql"),
    ];
    for script in MYSQL_CHAIN {
        db.execute(DbStatement::new(*script)).await.expect(script);
    }
}

/// Open a SECOND independent plugin (its own pool — a second "instance"/
/// connection family) against the same test database, so the races below
/// exercise real cross-connection unique-key behavior, never a process
/// local lock.
async fn open_mysql_plugin(url: &str) -> bcs_db_mysql::MysqlDbPlugin {
    let opts = mysql_async::Opts::from_url(url).expect("valid BCS_TEST_MYSQL_URL");
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
    let manager = bcs_db_mysql::MysqlDbManager::new(config)
        .await
        .expect("open the second MySQL test datasource");
    bcs_db_mysql::MysqlDbPlugin::new(manager, database)
}

fn create_command(actor: &str, bot: &str, to: &str, expected: u64, key: &str) -> CreateOwnershipTransfer {
    CreateOwnershipTransfer {
        actor_user_id: actor.to_string(),
        bot_id: bot.to_string(),
        to_user_id: to.to_string(),
        expected_owner_version: expected,
        client_request_id: key.to_string(),
    }
}

async fn seed_fixture(db: &dyn DbPlugin, bot: &str) {
    for statement in [
        format!(
            "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) \
             VALUES ('{bot}', '{bot}', 'local', 1)"
        ),
        "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) VALUES \
             ('human_a', 'a', 'local', 'human', 'online'), \
             ('human_b', 'b', 'local', 'human', 'online'), \
             ('human_c', 'c', 'local', 'human', 'online')".to_string(),
        format!(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
             status, originator_policy_type, originator_policy_data, management_source_kind, \
             management_source_id) \
             VALUES ('local', 'human_a', '{bot}', 'owner', 0, NULL, 'approved', 'same_as_from', \
             NULL, 'owner', 'owner')"
        ),
    ] {
        db.execute(DbStatement::new(statement))
            .await
            .expect("seed_fixture statement");
    }
}

/// OT04's cross-connection case: two instances racing the pending slot
/// (different idempotency keys) — the database's
/// `uk_bot_transfer_pending_slot` is the final defense, so exactly one
/// pending commits and the loser surfaces the documented conflict branch.
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_pending_slot_race_across_two_instances() {
    let url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL transfer contract");
    let second = open_mysql_plugin(&url).await;
    let first = open_mysql_plugin(&url).await;
    apply_full_mysql_chain(&first).await;
    let bot = "bot-mysql-slot";
    seed_fixture(&first, bot).await;

    let first = Arc::new(first);
    let left: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::mysql(first.clone(), "local".to_string()));
    let right: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::mysql(Arc::new(second), "local".to_string()));
    let (ra, rb) = tokio::join!(
        left.create_transfer(create_command("a", bot, "b", 1, "slot-key-left")),
        right.create_transfer(create_command("a", bot, "c", 1, "slot-key-right")),
    );
    let winners = [ra.is_ok(), rb.is_ok()].iter().filter(|x| **x).count();
    assert_eq!(
        winners, 1,
        "exactly one racing create wins the pending slot; got {ra:?} and {rb:?}"
    );
    if let Ok(won) = &ra {
        assert!(won.created);
    } else if let Ok(won) = &rb {
        assert!(won.created);
    }
    let loser = if ra.is_ok() { &rb } else { &ra };
    assert!(
        matches!(
            loser,
            Err(bcs_service_api::ServiceError::Authority(
                bcs_service_api::types::error::AuthorityError::Conflict(_)
            ))
        ),
        "the slot loser surfaces the documented conflict, got {loser:?}"
    );
    let rows = &first
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS n FROM bot_ownership_transfers WHERE env = 'local' AND status = 'pending'",
            vec![],
        ))
        .await
        .expect("pending count")[0];
    assert_eq!(rows.get_i64("n").ok().flatten().unwrap_or(0), 1);
}

/// OT05's cross-connection case: the durable idempotency key makes the
/// same-key same-payload create converge — one side created=true, the
/// other replays the SAME receipt with created=false (no memory locks, no
/// double execution).
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_idempotency_race_across_two_instances() {
    let url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL transfer contract");
    let second = open_mysql_plugin(&url).await;
    let first = open_mysql_plugin(&url).await;
    apply_full_mysql_chain(&first).await;
    let bot = "bot-mysql-idem";
    seed_fixture(&first, bot).await;

    let first = Arc::new(first);
    let left: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::mysql(first.clone(), "local".to_string()));
    let right: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::mysql(Arc::new(second), "local".to_string()));
    let command = create_command("a", bot, "b", 1, "idem-key-shared");
    let (ra, rb) = tokio::join!(
        left.create_transfer(command.clone()),
        right.create_transfer(command)
    );
    let a: CreateTransferResult = ra.expect("the first instance must succeed");
    let b: CreateTransferResult = rb.expect("the replaying instance must succeed");
    assert_eq!(
        a.created as u8 + b.created as u8,
        1,
        "exactly one instance creates; the other replays the durable receipt"
    );
    assert_eq!(a.receipt, b.receipt, "same key + same payload converge on one receipt");
}

/// OT06/OT07's cross-connection case: two instances accepting the SAME
/// pending concurrently — the guarded acceptance commits exactly once
/// (one version bump, one owner edge) and BOTH callers return the same
/// committed receipt (the response-loss retry semantics hold across
/// instances).
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_accept_race_across_two_instances() {
    let url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL transfer contract");
    let second = open_mysql_plugin(&url).await;
    let first = open_mysql_plugin(&url).await;
    apply_full_mysql_chain(&first).await;
    let bot = "bot-mysql-accept";
    seed_fixture(&first, bot).await;

    let first = Arc::new(first);
    let left: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::mysql(first.clone(), "local".to_string()));
    let right: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::mysql(Arc::new(second), "local".to_string()));
    let created = left
        .create_transfer(create_command("a", bot, "b", 1, "accept-key"))
        .await
        .expect("seed the pending");
    let id = created.receipt.transfer_id.clone();

    let (ra, rb) = tokio::join!(
        left.decide_transfer("b", &id, TransferAction::Accept),
        right.decide_transfer("b", &id, TransferAction::Accept),
    );
    for outcome in [&ra, &rb] {
        match outcome {
            Ok(CommittedTransferOutcome::Receipt(receipt)) => {
                assert_eq!(receipt.status, TransferStatus::Accepted);
                assert_eq!(receipt.result_owner_version, Some(2));
            }
            other => panic!("both racers must observe the committed acceptance, got {other:?}"),
        }
    }
    assert_eq!(ra.unwrap(), rb.unwrap(), "both instances return the same receipt");
    let rows = &first
        .query(DbStatement::with_params(
            "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ? AND env = 'local'",
            vec![DbValue::from(bot)],
        ))
        .await
        .expect("version read")[0];
    assert_eq!(
        rows.get_i64("ownership_version").ok().flatten().unwrap_or(0),
        2,
        "the version bumps exactly once across both racing instances (OT13)"
    );
    let ownership = left.ownership(bot).await.expect("read ownership");
    assert_eq!(ownership.owner_user_id, "b");
    assert_eq!(ownership.ownership_version, 2);
}