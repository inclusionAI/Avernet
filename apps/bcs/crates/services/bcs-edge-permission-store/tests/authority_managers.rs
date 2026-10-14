//! Manager write paths (`edit_manager` / `sync_managers`) against
//! [`DbBotAuthorityStore`] on SQLite (bot-authority plan Task
//! 2.1–2.5), running the SAME shared harness scenarios as the
//! memory projection so results and errors cannot drift
//! (spec §5.1/§5.2, feasibility doc §2 sketch).
//!
//! The fixture DB mirrors `migrations/sqlite/034_bot_authority.sql`;
//! the Human registry the Task-2 write paths validate targets
//! against is the `bcs_bots` actor rows (`actor_kind='human'`,
//! env-bound), so a seeded Human IS a live actor row. MySQL
//! still runs the same store in CI (plan §6); locally there is
//! no MySQL server.
//!
//! The stock-bound (>10,000) rejection is beyond what the shared
//! harness intentionally seeds; the SQLite bulk-seeding scenarios
//! below cover it.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_db_api::{DbExecuteResult, DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, BotAccessRelation, ManagerEdit, ManagerEditAction,
    ManagerSync, human_user_id_of_actor,
};
use bcs_test_support::contract::bot_authority_contract::{
    BotAuthorityContractFixture, HarnessAuditRow, run_bot_authority_contract,
};

const ENV: &str = "authority-env";
const OTHER_ENV: &str = "authority-env-other";

/// One scenario DB + store + fixture. Fresh instance per
/// scenario so corrupt-state cases cannot contaminate others.
struct SqlAuthorityFixture {
    db: Arc<dyn DbPlugin>,
    store: Arc<DbBotAuthorityStore>,
}

async fn new_fixture() -> SqlAuthorityFixture {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));
    for statement in SCHEMA_STATEMENTS {
        db.execute(DbStatement::new(*statement)).await.expect("authority fixture schema");
    }
    let store = Arc::new(DbBotAuthorityStore::new(
        db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
        ENV.to_string(),
    ));
    SqlAuthorityFixture { db, store }
}

async fn execute(db: &dyn DbPlugin, sql: &str, params: Vec<DbValue>) -> DbExecuteResult {
    db.execute(DbStatement::with_params(sql, params)).await.expect("fixture execute")
}

#[async_trait]
impl BotAuthorityContractFixture for SqlAuthorityFixture {
    fn authority_repo(&self) -> Arc<dyn BotAuthorityRepoPort> {
        self.store.clone()
    }

    async fn seed_bot(&self, env: &str, bot_id: &str, ownership_version: u64) {
        execute(
            self.db.as_ref(),
            "INSERT INTO bcs_bots (bot_uuid, env, name, ownership_version) \
             VALUES (?, ?, ?, ?) \
             ON CONFLICT(bot_uuid, env) DO UPDATE \
             SET ownership_version = excluded.ownership_version, is_deleted = 0",
            vec![
                DbValue::from(bot_id),
                DbValue::from(env),
                DbValue::from(format!("name-of-{bot_id}")),
                DbValue::from(ownership_version),
            ],
        )
        .await;
    }

    async fn delete_bot(&self, env: &str, bot_id: &str) {
        execute(
            self.db.as_ref(),
            "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = ? AND env = ?",
            vec![DbValue::from(bot_id), DbValue::from(env)],
        )
        .await;
    }

    async fn grant_edge(&self, env: &str, bot_id: &str, actor_id: &str, kind: BotAccessRelation) {
        // Production-faithful: a granted edge implies its
        // Human actor row (spec §3.1) — the registry the
        // live read paths authorize against.
        if let Some(user) = human_user_id_of_actor(actor_id) {
            self.seed_human(env, &user).await;
        }
        let kind = grant_kind_str(kind);
        let updated = execute(
            self.db.as_ref(),
            "UPDATE edge_grants SET status = 'approved', gmt_modified = CURRENT_TIMESTAMP \
             WHERE from_id = ? AND to_id = ? AND env = ? AND grant_kind = ? AND grant_ref_id = 0",
            vec![
                DbValue::from(actor_id),
                DbValue::from(bot_id),
                DbValue::from(env),
                DbValue::from(kind),
            ],
        )
        .await;
        if updated.affected_rows == 0 {
            execute(
                self.db.as_ref(),
                "INSERT INTO edge_grants \
                 (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
                  originator_policy_type, originator_policy_data) \
                 VALUES (?, ?, ?, ?, 0, NULL, 'approved', 'same_as_from', NULL)",
                vec![
                    DbValue::from(env),
                    DbValue::from(actor_id),
                    DbValue::from(bot_id),
                    DbValue::from(kind),
                ],
            )
            .await;
        }
    }

    async fn revoke_edge(&self, env: &str, bot_id: &str, actor_id: &str, kind: BotAccessRelation) {
        execute(
            self.db.as_ref(),
            "UPDATE edge_grants SET status = 'revoked', gmt_modified = CURRENT_TIMESTAMP \
             WHERE from_id = ? AND to_id = ? AND env = ? AND grant_kind = ? AND grant_ref_id = 0",
            vec![
                DbValue::from(actor_id),
                DbValue::from(bot_id),
                DbValue::from(env),
                DbValue::from(grant_kind_str(kind)),
            ],
        )
        .await;
    }

    async fn seed_runtime_edge(&self, env: &str, bot_id: &str, actor_id: &str) {
        execute(
            self.db.as_ref(),
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
              originator_policy_type, originator_policy_data) \
             VALUES (?, ?, ?, 'permission_profile', 42, NULL, 'approved', 'any', NULL)",
            vec![
                DbValue::from(env),
                DbValue::from(actor_id),
                DbValue::from(bot_id),
            ],
        )
        .await;
    }

    async fn seed_corrupt_duplicate_owner(&self, env: &str, bot_id: &str, actor_id: &str) {
        // Genuine corrupt legacy data cannot exist under the
        // partial unique index: emulate a pre-index database by
        // dropping the constraint, writing the second approved
        // owner, and leaving the constraint dropped.
        self.db
            .execute(DbStatement::new("DROP INDEX IF EXISTS uk_edge_approved_owner"))
            .await
            .expect("drop owner index");
        execute(
            self.db.as_ref(),
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
              originator_policy_type, originator_policy_data) \
             VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL)",
            vec![
                DbValue::from(env),
                DbValue::from(actor_id),
                DbValue::from(bot_id),
            ],
        )
        .await;
    }

    async fn seed_human(&self, env: &str, user_id: &str) {
        // The authority Human registry is the `bcs_bots`
        // actor row (spec §3.1): seed the same row the
        // production `ensure_human_actor` path creates —
        // env-bound, live, `human_<user_id>` keyed.
        execute(
            self.db.as_ref(),
            "INSERT INTO bcs_bots \
             (bot_uuid, actor_kind, name, session_token, created_by, \
              visibility, status, env, is_deleted) \
             VALUES (?, 'human', ?, ?, ?, 'protected', 'online', ?, 0) \
             ON CONFLICT(bot_uuid, env) DO UPDATE SET is_deleted = 0",
            vec![
                DbValue::from(format!("human_{user_id}")),
                DbValue::from(user_id),
                DbValue::from(format!("fixture-token-{env}-{user_id}")),
                DbValue::from(user_id),
                DbValue::from(env),
            ],
        )
        .await;
    }

    async fn remove_human(&self, env: &str, user_id: &str) {
        execute(
            self.db.as_ref(),
            "UPDATE bcs_bots SET is_deleted = 1 \
             WHERE bot_uuid = ? AND env = ?",
            vec![
                DbValue::from(format!("human_{user_id}")),
                DbValue::from(env),
            ],
        )
        .await;
    }

    async fn seed_audit_conflict(&self, env: &str, mutation_id: &str, sequence: u64) {
        execute(
            self.db.as_ref(),
            "INSERT INTO bot_authority_changes \
             (env, bot_id, mutation_id, sequence, action, actor_kind, actor_id, \
              subject_id, before_state, after_state) \
             VALUES (?, 'fixture-seed', ?, ?, 'seed_conflict', 'system', 'system_seed', \
                     'none', NULL, NULL)",
            vec![
                DbValue::from(env),
                DbValue::from(mutation_id),
                DbValue::from(sequence),
            ],
        )
        .await;
    }

    async fn audit_rows(&self, env: &str) -> Vec<HarnessAuditRow> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT mutation_id, sequence, action, actor_kind, actor_id, subject_id, \
                        before_state, after_state \
                 FROM bot_authority_changes WHERE env = ? ORDER BY sequence, id",
                vec![DbValue::from(env)],
            ))
            .await
            .expect("read audit rows");
        rows.iter()
            .map(|row| HarnessAuditRow {
                mutation_id: row.get_string("mutation_id").expect("mutation_id").unwrap(),
                sequence: row.get_i64("sequence").expect("sequence").unwrap() as u64,
                action: row.get_string("action").expect("action").unwrap(),
                actor_kind: row.get_string("actor_kind").expect("actor_kind").unwrap(),
                actor_id: row.get_string("actor_id").expect("actor_id").unwrap(),
                subject_id: row.get_string("subject_id").expect("subject_id").unwrap(),
                before_state: row.get_string("before_state").expect("before_state"),
                after_state: row.get_string("after_state").expect("after_state"),
            })
            .collect()
    }
}

fn grant_kind_str(kind: BotAccessRelation) -> &'static str {
    match kind {
        BotAccessRelation::Owner => "owner",
        BotAccessRelation::Manager => "manager",
    }
}

/// Post-migration SQLite schema for the authority store (the
/// Human registry is the `bcs_bots` actor rows).
const SCHEMA_STATEMENTS: &[&str] = &[
    "CREATE TABLE bcs_bots (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        bot_uuid TEXT NOT NULL, \
        env TEXT NOT NULL, \
        name TEXT NOT NULL DEFAULT '', \
        session_token TEXT, \
        created_by TEXT, \
        visibility TEXT NOT NULL DEFAULT 'protected', \
        status TEXT NOT NULL DEFAULT 'online', \
        actor_kind TEXT NOT NULL DEFAULT 'bot', \
        is_deleted INTEGER NOT NULL DEFAULT 0, \
        ownership_version INTEGER NOT NULL DEFAULT 0, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (bot_uuid, env))",
    "CREATE TABLE edge_grants (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        env TEXT NOT NULL, \
        from_id TEXT NOT NULL, \
        to_id TEXT NOT NULL, \
        grant_kind TEXT NOT NULL, \
        grant_ref_id INTEGER NOT NULL, \
        rules TEXT, \
        status TEXT NOT NULL DEFAULT 'approved', \
        originator_policy_type TEXT NOT NULL DEFAULT 'any', \
        originator_policy_data TEXT, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (from_id, to_id, env, grant_kind, grant_ref_id))",
    "CREATE UNIQUE INDEX uk_edge_approved_owner \
        ON edge_grants(env, to_id) \
        WHERE grant_kind = 'owner' AND status = 'approved'",
    "CREATE TABLE bot_ownership_transfers (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        env TEXT NOT NULL, \
        bot_id TEXT NOT NULL, \
        target_owner_actor_id TEXT NOT NULL, \
        from_owner_actor_id TEXT NOT NULL, \
        expected_ownership_version INTEGER NOT NULL, \
        status TEXT NOT NULL DEFAULT 'pending', \
        requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        expires_at TEXT NOT NULL, \
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        decided_at TEXT, \
        decided_by_kind TEXT, \
        decided_by_id TEXT, \
        reason TEXT, \
        result_ownership_version INTEGER, \
        bot_name_snapshot TEXT, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (env, bot_id, target_owner_actor_id))",
    "CREATE UNIQUE INDEX uk_transfer_pending \
        ON bot_ownership_transfers(env, bot_id) WHERE status = 'pending'",
    "CREATE INDEX idx_transfer_received \
        ON bot_ownership_transfers(env, target_owner_actor_id, status, requested_at)",
    "CREATE INDEX idx_transfer_sent \
        ON bot_ownership_transfers(env, from_owner_actor_id, status, requested_at)",
    "CREATE TABLE bot_authority_changes (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        env TEXT NOT NULL, \
        bot_id TEXT NOT NULL, \
        mutation_id TEXT NOT NULL, \
        sequence INTEGER NOT NULL, \
        action TEXT NOT NULL, \
        actor_kind TEXT NOT NULL, \
        actor_id TEXT NOT NULL, \
        subject_id TEXT NOT NULL, \
        before_state TEXT, \
        after_state TEXT, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (env, mutation_id, sequence))",
    "CREATE INDEX idx_change_bot ON bot_authority_changes(env, bot_id, sequence)",
];

#[tokio::test]
async fn authority_manager_contract_sqlite() {
    let fixture = new_fixture().await;
    run_bot_authority_contract(&fixture, ENV, OTHER_ENV).await;
}

// Real predefined manager set: the mutation actor carries the
// literal human actor id an Application would map.
fn raw_edit(bot: &str, actor: &str, action: ManagerEditAction) -> ManagerEdit {
    ManagerEdit {
        bot_id: bot.to_string(),
        user_id: actor.to_string(),
        action,
        mutation: AuthorityMutation {
            mutation_id: "mutation-raw".to_string(),
            actor: AuthorityActor::Human { user_id: "own".to_string() },
        },
    }
}

/// A mutation actor that is not a `human_` actor id must be
/// rejected as an unknown Human (never inserted raw).
#[tokio::test]
async fn manager_target_must_be_human_actor_shape() {
    let fixture = new_fixture().await;
    fixture.seed_bot(ENV, "bot_shape", 1).await;
    fixture
        .grant_edge(ENV, "bot_shape", "human_own", BotAccessRelation::Owner)
        .await;
    fixture.seed_human(ENV, "own").await;
    let result = fixture
        .authority_repo()
        .edit_manager(raw_edit("bot_shape", "bot_someone", ManagerEditAction::Grant))
        .await;
    assert!(result.is_err(), "non-Human actor target must be rejected");
    assert_eq!(
        fixture
            .authority_repo()
            .role("human_own", "bot_shape")
            .await
            .unwrap(),
        Some(BotAccessRelation::Owner),
        "unchanged owner"
    );
}

/// The stock bound (feasibility §5.2, frozen 10,000): |S| over the
/// bound rejects the whole sync with an explicit error, no
/// truncation.
#[tokio::test]
async fn sync_rejects_stock_over_10000() {
    // >10,000 current managers is only practical to build here via
    // bulk multi-row INSERTs (the shared harness works at 1,001).
    let fixture = new_fixture().await;
    let db = fixture.db.clone();
    fixture.seed_bot(ENV, "bot_huge", 1).await;
    fixture.seed_human(ENV, "own").await;
    // Bulk humans (live `bcs_bots` actor rows).
    for chunk in (0..10_110usize).collect::<Vec<_>>().chunks(100) {
        let mut values = String::new();
        let mut params = Vec::new();
        for (index, id) in chunk.iter().enumerate() {
            if index > 0 {
                values.push_str(",");
            }
            values.push_str("(?, 'human', ?, ?, ?, 'protected', 'online', ?, 0)");
            params.push(DbValue::from(format!("human_stock{id:0>5}")));
            params.push(DbValue::from(format!("stock{id:0>5}")));
            params.push(DbValue::from(format!("fixture-token-stock{id:0>5}")));
            params.push(DbValue::from(format!("stock{id:0>5}")));
            params.push(DbValue::from(ENV));
        }
        execute(
            db.as_ref(),
            &format!(
                "INSERT INTO bcs_bots \
                 (bot_uuid, actor_kind, name, session_token, created_by, \
                  visibility, status, env, is_deleted) \
                 VALUES {values} \
                 ON CONFLICT(bot_uuid, env) DO NOTHING"
            ),
            params,
        )
        .await;
    }
    fixture
        .grant_edge(ENV, "bot_huge", "human_own", BotAccessRelation::Owner)
        .await;
    // Bulk manager edges.
    for chunk in (0..10_101usize).collect::<Vec<_>>().chunks(100) {
        let mut values = String::new();
        let mut params = Vec::new();
        for id in chunk {
            if !values.is_empty() {
                values.push_str(",");
            }
            values.push_str("(?, ?, 'bot_huge', 'manager', 0, NULL, 'approved', 'same_as_from', NULL)");
            params.push(DbValue::from(ENV));
            params.push(DbValue::from(format!("human_stock{id:0>5}")));
        }
        execute(
            db.as_ref(),
            &format!(
                "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                 status, originator_policy_type, originator_policy_data) VALUES {values}"
            ),
            params,
        )
        .await;
    }

    let started = std::time::Instant::now();
    let result = fixture
        .authority_repo()
        .sync_managers(ManagerSync {
            bot_id: "bot_huge".to_string(),
            user_ids: vec![],
            mutation: AuthorityMutation {
                mutation_id: "mutation-huge".to_string(),
                actor: AuthorityActor::Service { app_id: 9 },
            },
        })
        .await;
    assert!(
        result.is_err(),
        "sync must reject a stock beyond the frozen bound, got {result:?} \
         after {:?}",
        started.elapsed()
    );
    assert!(
        result
            .as_ref()
            .unwrap_err()
            .to_string()
            .contains("bot_manager_stock_limit"),
        "the stock bound must surface its pinned code, got {result:?}"
    );
    let page = fixture
        .authority_repo()
        .managers(bcs_service_api::types::bot_authority::ManagerListQuery {
            bot_id: "bot_huge".to_string(),
            ..Default::default()
        })
        .await
        .expect("page after rejection");
    assert_eq!(page.total, 10_101, "no truncation, whole request rejected");
}
