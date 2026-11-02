//! MySQL conformance gate for the Bot Authority strict
//! reads, manager/sync writes, transfer state machine and
//! audit scenarios (bot-authority plan Task 8 / §6).
//!
//! This is the CI-gated MySQL entry: it runs the SAME
//! shared contract harness as the SQLite suites
//! (`authority_reads.rs` / `authority_managers.rs` /
//! `authority_transfers.rs`), but against
//! [`DbBotAuthorityStore`] with the MySQL dialect over the
//! REAL full MySQL migration chain. Locally there is no
//! MySQL server, so this entry is `#[ignore]`d and the
//! SQLite suites prove the scenario parity on SQLite
//! (plan §6).
//!
//! Database requirements (plan §6): `BCS_TEST_MYSQL_URL`
//! points at an ISOLATED disposable EMPTY MySQL database;
//! a missing URL fails the entry immediately and the URL
//! is never logged. The fixture DB is the migrated chain
//! itself (no local fixture DDL), so scenario SQL mirrors
//! the production schema — including the generated-column
//! owner/pending unique keys.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_db_api::{DbExecuteResult, DbPlugin, DbRow, DbStatement, DbValue};
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    BotAccessRelation, human_user_id_of_actor,
};
use bcs_test_support::contract::bot_authority_contract::{
    BotAuthorityContractFixture, HarnessAuditRow, HarnessTransferDecision,
    run_bot_authority_contract_with_scenarios,
};
use bcs_test_support::contract::bot_transfer_contract::{
    BotTransferContractFixture, HarnessTransferSeed,
    run_bot_transfer_contract_with_scenarios,
};
#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;

use mysql_chain::{MysqlConformanceDb, report_mysql_gate_scenarios};

// edge_grants.env is VARCHAR(16): both harness envs fit.
const ENV: &str = "auth-edge";
const OTHER_ENV: &str = "auth-alt";

/// Fixture over the migrated MySQL schema. Raw seeding
/// SQL mirrors the SQLite fixture of
/// `authority_transfers.rs` in MySQL dialect.
struct MysqlAuthorityFixture {
    db: Arc<dyn DbPlugin>,
    store: Arc<DbBotAuthorityStore>,
}

#[async_trait]
impl BotAuthorityContractFixture for MysqlAuthorityFixture {
    fn authority_repo(&self) -> Arc<dyn BotAuthorityRepoPort> {
        self.store.clone()
    }

    async fn seed_bot(&self, env: &str, bot_id: &str, ownership_version: u64) {
        execute(
            self.db.as_ref(),
            "INSERT INTO bcs_bots (bot_uuid, env, name, ownership_version) \
             VALUES (?, ?, ?, ?) \
             ON DUPLICATE KEY UPDATE \
             ownership_version = VALUES(ownership_version), is_deleted = 0",
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
        let keys = vec![
            DbValue::from(actor_id),
            DbValue::from(bot_id),
            DbValue::from(env),
            DbValue::from(kind),
        ];
        // Branch on key EXISTENCE, never on
        // `affected_rows`: MySQL (default client flags,
        // no CLIENT_FOUND_ROWS) reports only rows whose
        // VALUES changed, so re-granting an already
        // approved edge — or one whose gmt_modified is
        // unchanged within the same second — reports 0
        // and a naive UPDATE-then-INSERT falls through
        // to a duplicate-key 1062 (SQLite counts
        // processed rows and diverges). No INSERT
        // IGNORE: it would swallow other real errors.
        let existing = query(
            self.db.as_ref(),
            "SELECT 1 AS existing FROM edge_grants \
             WHERE from_id = ? AND to_id = ? AND env = ? \
               AND grant_kind = ? AND grant_ref_id = 0",
            keys.clone(),
        )
        .await;
        if existing.is_empty() {
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
        } else {
            execute(
                self.db.as_ref(),
                "UPDATE edge_grants SET status = 'approved', gmt_modified = CURRENT_TIMESTAMP \
                 WHERE from_id = ? AND to_id = ? AND env = ? AND grant_kind = ? AND grant_ref_id = 0",
                keys,
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
        // The MySQL owner-uniqueness key rides the virtual
        // `authority_owner_log` generated column
        // (migrations/mysql/033). Drop it so the corrupt
        // second approved-owner row can be inserted —
        // idempotently, unlike SQLite's DROP INDEX IF EXISTS.
        let rows = query(
            self.db.as_ref(),
            "SELECT COUNT(*) AS has_index \
             FROM information_schema.statistics \
             WHERE table_schema = DATABASE() \
               AND table_name = 'edge_grants' \
               AND index_name = 'uk_edge_approved_owner'",
            vec![],
        )
        .await;
        let has_index: i64 = rows[0]
            .get("has_index")
            .expect("count column")
            .as_i64()
            .expect("index count");
        if has_index > 0 {
            execute(
                self.db.as_ref(),
                "ALTER TABLE edge_grants DROP INDEX uk_edge_approved_owner",
                vec![],
            )
            .await;
        }
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
        // actor row (spec §3.1): env-bound, live, seeded in
        // the same shape the production `ensure_human_actor`
        // path creates. The session token must stay unique
        // (`uk_bots_session_token`), so it carries the env.
        // A re-seed RESTORES a previously tombstoned row
        // (MySQL has no upsert-on-conflict here without
        // touching the token column, so the restore is a
        // plain follow-up UPDATE).
        execute(
            self.db.as_ref(),
            "INSERT IGNORE INTO bcs_bots \
             (bot_uuid, actor_kind, name, session_token, created_by, \
              visibility, status, env) \
             VALUES (?, 'human', ?, ?, ?, 'protected', 'online', ?)",
            vec![
                DbValue::from(format!("human_{user_id}")),
                DbValue::from(user_id),
                DbValue::from(format!("fixture-token-{env}-{user_id}")),
                DbValue::from(user_id),
                DbValue::from(env),
            ],
        )
        .await;
        execute(
            self.db.as_ref(),
            "UPDATE bcs_bots SET is_deleted = 0 \
             WHERE bot_uuid = ? AND env = ?",
            vec![
                DbValue::from(format!("human_{user_id}")),
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
                DbValue::from(sequence as i64),
            ],
        )
        .await;
    }

    async fn audit_rows(&self, env: &str) -> Vec<HarnessAuditRow> {
        let rows = query(
            self.db.as_ref(),
            "SELECT mutation_id, sequence, action, actor_kind, actor_id, subject_id, \
                    before_state, after_state \
             FROM bot_authority_changes WHERE env = ? ORDER BY sequence, id",
            vec![DbValue::from(env)],
        )
        .await;
        rows.iter()
            .map(|row| HarnessAuditRow {
                mutation_id: row
                    .get_string("mutation_id")
                    .expect("mutation_id")
                    .unwrap(),
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

    async fn ownership_version(&self, env: &str, bot_id: &str) -> u64 {
        let rows = query(
            self.db.as_ref(),
            "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![DbValue::from(bot_id), DbValue::from(env)],
        )
        .await;
        rows.first()
            .map(|row| {
                row.get_i64("ownership_version")
                    .expect("ownership_version")
                    .unwrap_or(0) as u64
            })
            .unwrap_or(0)
    }

    async fn seed_pending_transfer(
        &self,
        env: &str,
        bot_id: &str,
        from_owner_actor: &str,
        target_actor: &str,
        expected_version: u64,
    ) {
        let requested = mysql_offset_expr(0);
        let expires = mysql_offset_expr(7 * 24 * 3600);
        execute(
            self.db.as_ref(),
            &format!(
                "INSERT INTO bot_ownership_transfers \
                 (env, bot_id, target_owner_actor_id, from_owner_actor_id, \
                  expected_ownership_version, status, requested_at, expires_at, \
                  updated_at) \
                 VALUES (?, ?, ?, ?, ?, 'pending', {requested}, {expires}, {requested})"
            ),
            vec![
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(target_actor),
                DbValue::from(from_owner_actor),
                DbValue::from(expected_version),
            ],
        )
        .await;
    }

    async fn transfer_decision_state(
        &self,
        env: &str,
        bot_id: &str,
        target_actor: &str,
    ) -> Option<HarnessTransferDecision> {
        let rows = query(
            self.db.as_ref(),
            "SELECT status, decided_by_kind, decided_by_id, reason \
             FROM bot_ownership_transfers \
             WHERE env = ? AND bot_id = ? AND target_owner_actor_id = ? LIMIT 1",
            vec![
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(target_actor),
            ],
        )
        .await;
        rows.first()
            .map(|row| HarnessTransferDecision {
                status: row.get_string("status").expect("status").unwrap(),
                decided_by_kind: row.get_string("decided_by_kind").expect("decided_by_kind"),
                decided_by_id: row.get_string("decided_by_id").expect("decided_by_id"),
                reason: row.get_string("reason").expect("reason"),
            })
    }
}

#[async_trait]
impl BotTransferContractFixture for MysqlAuthorityFixture {
    async fn seed_transfer(&self, env: &str, row: HarnessTransferSeed) {
        // DB time domain matches the store's MySQL flavor
        // (`CURRENT_TIMESTAMP`, migrations/mysql/033).
        let requested = mysql_offset_expr(row.requested_at_offset_s);
        let expires = mysql_offset_expr(row.expires_at_offset_s);
        let decided_at = match row.decided_at_offset_s {
            Some(offset) => mysql_offset_expr(offset),
            None => "NULL".to_string(),
        };
        let mut decision = Vec::with_capacity(7);
        decision.push(DbValue::from(row.from_owner_actor_id.as_str()));
        decision.push(DbValue::from(row.expected_ownership_version));
        decision.push(DbValue::from(row.status.as_str()));
        decision.push(optional_text(row.decided_by_kind.as_deref()));
        decision.push(optional_text(row.decided_by_id.as_deref()));
        decision.push(optional_text(row.reason.as_deref()));
        decision.push(match row.result_ownership_version {
            Some(value) => DbValue::from(value),
            None => DbValue::Null,
        });

        let keys = vec![
            DbValue::from(env),
            DbValue::from(row.bot_id.as_str()),
            DbValue::from(row.target_owner_actor_id.as_str()),
        ];

        let db = self.db.as_ref();
        let mut update_params = decision.clone();
        update_params.extend(keys.clone());
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
            update_params,
        )
        .await;
        if updated.affected_rows == 0 {
            let mut insert_params = keys;
            insert_params.extend(decision);
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
                insert_params,
            )
            .await;
        }
    }
}

fn optional_text(value: Option<&str>) -> DbValue {
    match value {
        Some(value) => DbValue::from(value),
        None => DbValue::Null,
    }
}

/// MySQL relative-time expression, second-precision,
/// aligned with the store's `CURRENT_TIMESTAMP` flavor.
fn mysql_offset_expr(offset_s: i64) -> String {
    if offset_s == 0 {
        "CURRENT_TIMESTAMP".to_string()
    } else if offset_s > 0 {
        format!("DATE_ADD(CURRENT_TIMESTAMP, INTERVAL {offset_s} SECOND)")
    } else {
        format!(
            "DATE_SUB(CURRENT_TIMESTAMP, INTERVAL {} SECOND)",
            -offset_s
        )
    }
}

fn grant_kind_str(kind: BotAccessRelation) -> &'static str {
    match kind {
        BotAccessRelation::Owner => "owner",
        BotAccessRelation::Manager => "manager",
    }
}

async fn execute(db: &dyn DbPlugin, sql: &str, params: Vec<DbValue>) -> DbExecuteResult {
    db.execute(DbStatement::with_params(sql, params))
        .await
        .expect("fixture execute")
}

async fn query(db: &dyn DbPlugin, sql: &str, params: Vec<DbValue>) -> Vec<DbRow> {
    db.query(DbStatement::with_params(sql, params))
        .await
        .expect("fixture query")
}

/// Plan §6 MySQL gate 1: the authority harness
/// (strict reads + manager/sync + audit) and the transfer
/// state-machine harness over the migrated MySQL chain.
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs the plan §6 MySQL gate"]
async fn authority_mysql_conformance() {
    let database = MysqlConformanceDb::open_empty().await;
    let applied = database.apply_full_migration_chain().await;
    assert!(
        !applied.is_empty(),
        "full MySQL migration chain applied ({} versions)",
        applied.len()
    );

    let fixture = MysqlAuthorityFixture {
        db: database.plugin.clone(),
        store: Arc::new(DbBotAuthorityStore::mysql(
            database.plugin.clone(),
            ENV.to_string(),
        )),
    };

    let mut scenarios: Vec<&'static str> = Vec::new();
    run_bot_authority_contract_with_scenarios(
        &fixture,
        ENV,
        OTHER_ENV,
        |scenario| scenarios.push(scenario),
    )
    .await;
    let authority_scenarios = scenarios.len();
    run_bot_transfer_contract_with_scenarios(&fixture, ENV, |scenario| {
        scenarios.push(scenario)
    })
    .await;

    // Scenario inventory is reported for the CI gate: the
    // aggregate is not an empty shell — it ran the full
    // harness scenario list and must keep it complete.
    assert_eq!(authority_scenarios, 39, "authority harness scenarios");
    assert_eq!(
        scenarios.len() - authority_scenarios,
        24,
        "transfer harness scenarios"
    );
    report_mysql_gate_scenarios("edge-authority", &scenarios);

    database.close().await;
}
