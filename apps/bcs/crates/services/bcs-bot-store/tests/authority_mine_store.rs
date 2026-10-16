//! `list_controllable` store conformance
//! (bot-authority plan Task 4.5, spec §8.0 point 1):
//! the SQL store (SQLite-backed `PersistentBotRepo`)
//! and the memory projection run the SAME
//! `bcs-test-support` candidate harness, so the mine
//! union semantics cannot drift between the two
//! stores.
//!
//! MySQL note: the SQL statements share the dialect
//! switches with the registration composites
//! (byte-exact `CAST(x AS BINARY)` predicates);
//! MySQL execution stays CI-gated the same way
//! (plan §6; no new ignored tests are fabricated).

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::types::bot_authority::BotAccessRelation;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotRepoPort,
};
use bcs_test_support::contract::bot_mine_contract::{
    BotControllableContractFixture, run_bot_controllable_contract,
};

fn resolve_env() -> String {
    bcs_config::resolve_env_str()
}

// ---------------------------------------------------------------------------
// The SQL (SQLite) fixture: only the two tables this
// contract reads (bcs_bots + edge_grants), the same
// shapes the migration chain creates.
// ---------------------------------------------------------------------------

const SCHEMA: &[&str] = &[
    "CREATE TABLE bcs_bots (\
        bot_uuid TEXT NOT NULL, \
        env TEXT NOT NULL, \
        name TEXT NOT NULL DEFAULT '', \
        bot_info TEXT, \
        session_token TEXT, \
        created_by TEXT, \
        visibility TEXT NOT NULL DEFAULT 'protected', \
        status TEXT NOT NULL DEFAULT 'online', \
        actor_kind TEXT NOT NULL DEFAULT 'bot', \
        is_deleted INTEGER NOT NULL DEFAULT 0, \
        ownership_version INTEGER NOT NULL DEFAULT 0, \
        agent_code TEXT, \
        connection_mode TEXT, \
        provider_id TEXT, \
        provider_bot_ref TEXT, \
        webhook_url TEXT, \
        registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
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
];

struct SqlControllableFixture {
    repo: Arc<PersistentBotRepo>,
    db: Arc<LocalSqliteDbPlugin>,
}

impl SqlControllableFixture {
    async fn new() -> Self {
        let db = Arc::new(LocalSqliteDbPlugin::new().expect("open sqlite"));
        for statement in SCHEMA {
            db.execute(DbStatement::new(*statement))
                .await
                .expect("schema");
        }
        let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
            db.clone(),
            DbSqlFlavor::Sqlite,
        ));
        Self { repo, db }
    }

    async fn execute(&self, sql: &str, params: Vec<DbValue>) {
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .expect("execute seed statement");
    }

    async fn execute_with(
        &self,
        sql: &str,
        kind: BotAccessRelation,
    ) {
        let _ = (sql, kind);
    }
}

#[async_trait]
impl BotControllableContractFixture for SqlControllableFixture {
    fn control_plane_repo(&self) -> Arc<dyn BotControlPlaneRepoPort> {
        self.repo.clone()
    }

    async fn seed_bot(
        &self,
        env: &str,
        bot_id: &str,
        name: &str,
        status: ActorStatus,
    ) {
        let status = match status {
            ActorStatus::Online => "online",
            ActorStatus::Hidden => "hidden",
        };
        self.execute(
            "INSERT INTO bcs_bots (bot_uuid, env, name, actor_kind, status) \
             VALUES (?, ?, ?, 'bot', ?)",
            vec![
                DbValue::from(bot_id),
                DbValue::from(env),
                DbValue::from(name),
                DbValue::from(status),
            ],
        )
        .await;
    }

    async fn delete_bot(&self, env: &str, bot_id: &str) {
        self.execute(
            "UPDATE bcs_bots SET is_deleted = 1 \
             WHERE bot_uuid = ? AND env = ?",
            vec![DbValue::from(bot_id), DbValue::from(env)],
        )
        .await;
    }

    async fn grant_edge(
        &self,
        env: &str,
        bot_id: &str,
        user_id: &str,
        kind: BotAccessRelation,
    ) {
        let kind = match kind {
            BotAccessRelation::Owner => "owner",
            BotAccessRelation::Manager => "manager",
        };
        self.execute(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, status) \
             VALUES (?, ?, ?, ?, 0, 'approved')",
            vec![
                DbValue::from(env),
                DbValue::from(format!("human_{user_id}")),
                DbValue::from(bot_id),
                DbValue::from(kind),
            ],
        )
        .await;
    }

    async fn revoke_edge(
        &self,
        env: &str,
        bot_id: &str,
        user_id: &str,
        kind: BotAccessRelation,
    ) {
        let kind = match kind {
            BotAccessRelation::Owner => "owner",
            BotAccessRelation::Manager => "manager",
        };
        self.execute(
            "UPDATE edge_grants SET status = 'revoked' \
             WHERE env = ? AND to_id = ? AND from_id = ? AND grant_kind = ?",
            vec![
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(format!("human_{user_id}")),
                DbValue::from(kind),
            ],
        )
        .await;
    }

    async fn seed_human_self(&self, env: &str, user_id: &str) {
        self.execute(
            "INSERT INTO bcs_bots (bot_uuid, env, name, actor_kind, status) \
             VALUES (?, ?, ?, 'human', 'online')",
            vec![
                DbValue::from(format!("human_{user_id}")),
                DbValue::from(env),
                DbValue::from(user_id),
            ],
        )
        .await;
    }
}

// ---------------------------------------------------------------------------
// The memory fixture: the SAME `MemoryBotRepo` shared state.
// ---------------------------------------------------------------------------

struct MemoryControllableFixture {
    repo: Arc<MemoryBotRepo>,
}

#[async_trait]
impl BotControllableContractFixture for MemoryControllableFixture {
    fn control_plane_repo(&self) -> Arc<dyn BotControlPlaneRepoPort> {
        self.repo.clone()
    }

    async fn seed_bot(
        &self,
        _env: &str,
        bot_id: &str,
        name: &str,
        status: ActorStatus,
    ) {
        // Memory rows bind the process env at
        // registration; the harness seeds candidate
        // scenarios in that env and pins env-exactness
        // through edge envs.
        let _ = _env;
        self.repo
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    ..Default::default()
                },
            )
            .await
            .expect("register fixture bot");
        self.repo
            .update_actor_status(bot_id, status)
            .await
            .expect("fixture status");
    }

    async fn delete_bot(&self, _env: &str, bot_id: &str) {
        let deleted = self.repo.soft_delete(bot_id).await;
        let _ = deleted;
    }

    async fn grant_edge(
        &self,
        env: &str,
        bot_id: &str,
        user_id: &str,
        kind: BotAccessRelation,
    ) {
        use bcs_bot_store::memory_authority::{
            MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus,
        };
        let edge_kind = match kind {
            BotAccessRelation::Owner => MemoryAuthorityEdgeKind::Owner,
            BotAccessRelation::Manager => MemoryAuthorityEdgeKind::Manager,
        };
        self.repo
            .authority_state()
            .write()
            .await
            .edges
            .insert(
                (
                    env.to_string(),
                    bot_id.to_string(),
                    format!("human_{user_id}"),
                    edge_kind,
                ),
                (MemoryAuthorityEdgeStatus::Approved, 0),
            );
    }

    async fn revoke_edge(
        &self,
        env: &str,
        bot_id: &str,
        user_id: &str,
        kind: BotAccessRelation,
    ) {
        use bcs_bot_store::memory_authority::{
            MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus,
        };
        let edge_kind = match kind {
            BotAccessRelation::Owner => MemoryAuthorityEdgeKind::Owner,
            BotAccessRelation::Manager => MemoryAuthorityEdgeKind::Manager,
        };
        self.repo
            .authority_state()
            .write()
            .await
            .edges
            .insert(
                (
                    env.to_string(),
                    bot_id.to_string(),
                    format!("human_{user_id}"),
                    edge_kind,
                ),
                (MemoryAuthorityEdgeStatus::Revoked, 0),
            );
    }

    async fn seed_human_self(&self, _env: &str, user_id: &str) {
        self.repo
            .ensure_human_actor(user_id, user_id)
            .await
            .expect("fixture human actor");
    }
}

#[tokio::test]
async fn sql_store_controllable_contract() {
    let env = resolve_env();
    let other = format!("{env}-other");
    let fixture = SqlControllableFixture::new().await;
    run_bot_controllable_contract(&fixture, &env, &other).await;
}

#[tokio::test]
async fn memory_store_controllable_contract() {
    let env = resolve_env();
    let other = format!("{env}-other");
    let fixture = MemoryControllableFixture {
        repo: Arc::new(MemoryBotRepo::new()),
    };
    run_bot_controllable_contract(&fixture, &env, &other).await;
}
