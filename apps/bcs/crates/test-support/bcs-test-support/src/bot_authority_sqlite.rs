//! Shared SQLite authority/manager/transfer contract fixture using the
//! real `DbBotAuthorityStore`. Store-specific regressions and the R25
//! conformance entries share this schema and seeding implementation.
//! Transaction-decorated fixtures keep their own fault/race behavior;
//! MySQL conformance retains its full migration-chain fixture.

#![allow(clippy::expect_used, reason = "test fixture")]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_db_api::{DbExecuteResult, DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    BotAccessRelation, human_user_id_of_actor,
};
use crate::contract::bot_transfer_contract::{BotTransferContractFixture, HarnessTransferSeed};
use crate::contract::bot_authority_contract::{
    BotAuthorityContractFixture, HarnessAuditRow,
};

/// One scenario DB + store + fixture. Fresh instance per
/// scenario so corrupt-state cases cannot contaminate others.
pub struct SqlAuthorityFixture {
    pub db: Arc<dyn DbPlugin>,
    pub store: Arc<DbBotAuthorityStore>,
}

pub async fn new_fixture(env: &str) -> SqlAuthorityFixture {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));
    new_fixture_with_db(env, db).await
}

/// Initialize the same schema on a test-owned SQLite decorator (e.g. a
/// deterministic transaction gate). The decorator remains active for the store.
pub async fn new_fixture_with_db(env: &str, db: Arc<dyn DbPlugin>) -> SqlAuthorityFixture {
    for statement in SCHEMA_STATEMENTS {
        db.execute(DbStatement::new(*statement)).await.expect("authority fixture schema");
    }
    let store = Arc::new(DbBotAuthorityStore::new(
        db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
        env.to_string(),
    ));
    SqlAuthorityFixture { db, store }
}

pub async fn execute(db: &dyn DbPlugin, sql: &str, params: Vec<DbValue>) -> DbExecuteResult {
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
        // Human actor row (spec §3.1).
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
                before_state: row.get_string("before_state").expect("before"),
                after_state: row.get_string("after_state").expect("after"),
            })
            .collect()
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
}

fn grant_kind_str(kind: BotAccessRelation) -> &'static str {
    match kind {
        BotAccessRelation::Owner => "owner",
        BotAccessRelation::Manager => "manager",
    }
}

/// Post-migration SQLite schema for the authority store.
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

/// SQLite relative-time expression (fixture-only; tests run
/// SQLite locally, plan §6).
fn sqlite_offset_expr(offset_s: i64) -> String {
    if offset_s == 0 {
        "CURRENT_TIMESTAMP".to_string()
    } else if offset_s > 0 {
        format!("datetime('now', '+{offset_s} seconds')")
    } else {
        format!("datetime('now', '-{} seconds')", -offset_s)
    }
}

#[async_trait]
impl BotTransferContractFixture for SqlAuthorityFixture {
    async fn seed_transfer(&self, env: &str, row: HarnessTransferSeed) {
        let requested = sqlite_offset_expr(row.requested_at_offset_s);
        let expires = sqlite_offset_expr(row.expires_at_offset_s);
        let decided_at = match row.decided_at_offset_s {
            Some(offset) => sqlite_offset_expr(offset),
            None => "NULL".to_string(),
        };
        let db = self.db.as_ref();
        let updated = execute(
            db,
            &format!(
                "UPDATE bot_ownership_transfers SET \
                 from_owner_actor_id = ?, expected_ownership_version = ?, \
                 status = ?, requested_at = {requested}, expires_at = {expires}, \
                 updated_at = {requested}, decided_at = {decided_at}, \
                 decided_by_kind = ?, decided_by_id = ?, reason = ?, \
                 result_ownership_version = ?, bot_name_snapshot = NULL \
                 WHERE env = ? AND bot_id = ? AND target_owner_actor_id = ?"
            ),
            vec![
                DbValue::from(row.from_owner_actor_id.as_str()),
                DbValue::from(row.expected_ownership_version),
                DbValue::from(row.status.as_str()),
                match row.decided_by_kind {
                    Some(ref value) => DbValue::from(value.as_str()),
                    None => DbValue::Null,
                },
                match row.decided_by_id {
                    Some(ref value) => DbValue::from(value.as_str()),
                    None => DbValue::Null,
                },
                match row.reason {
                    Some(ref value) => DbValue::from(value.as_str()),
                    None => DbValue::Null,
                },
                match row.result_ownership_version {
                    Some(value) => DbValue::from(value),
                    None => DbValue::Null,
                },
                DbValue::from(env),
                DbValue::from(row.bot_id.as_str()),
                DbValue::from(row.target_owner_actor_id.as_str()),
            ],
        )
        .await;
        if updated.affected_rows == 0 {
            execute(
                db,
                &format!(
                    "INSERT INTO bot_ownership_transfers \
                     (env, bot_id, target_owner_actor_id, from_owner_actor_id, \
                      expected_ownership_version, status, requested_at, expires_at, \
                      updated_at, decided_at, decided_by_kind, decided_by_id, reason, \
                      result_ownership_version, bot_name_snapshot) \
                     VALUES (?, ?, ?, ?, ?, ?, {requested}, {expires}, {requested}, \
                             {decided_at}, ?, ?, ?, ?, NULL)"
                ),
                vec![
                    DbValue::from(env),
                    DbValue::from(row.bot_id.as_str()),
                    DbValue::from(row.target_owner_actor_id.as_str()),
                    DbValue::from(row.from_owner_actor_id.as_str()),
                    DbValue::from(row.expected_ownership_version),
                    DbValue::from(row.status.as_str()),
                    match row.decided_by_kind {
                        Some(ref value) => DbValue::from(value.as_str()),
                        None => DbValue::Null,
                    },
                    match row.decided_by_id {
                        Some(ref value) => DbValue::from(value.as_str()),
                        None => DbValue::Null,
                    },
                    match row.reason {
                        Some(ref value) => DbValue::from(value.as_str()),
                        None => DbValue::Null,
                    },
                    match row.result_ownership_version {
                        Some(value) => DbValue::from(value),
                        None => DbValue::Null,
                    },
                ],
            )
            .await;
        }
    }
}
