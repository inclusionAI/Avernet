#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use bcs_test_support::contract::repo::bot_control_plane_repo_port_contract_tests;
#[path = "control_plane_parts/cases_1.rs"]
mod cases_1;
#[path = "control_plane_parts/cases_2.rs"]
mod cases_2;
#[path = "control_plane_parts/cases_3.rs"]
mod cases_3;

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue as Value, db_get_column, db_get_column_opt,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateReadQuery, BotCandidateVisibility, BotCapabilities, BotSearchCandidateQuery, BotSearchFriendshipFilter,
    BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, BotControlPlaneRepoPort, BotRepoPort, BotTaskModesQuery,
    FriendCheckInStrategy, TaskModeMatch, UserVisibility,
};
use bcs_service_api::types::ServiceError;
use tokio::sync::Barrier;


struct SynchronizedSnapshotDb {
    inner: Arc<dyn DbPlugin>,
    snapshot_read_barrier: Barrier,
}

#[async_trait]
impl DbPlugin for SynchronizedSnapshotDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let synchronize_snapshot = statement
            .sql()
            .starts_with("SELECT bot_info FROM bcs_bots");
        let rows = self.inner.query(statement).await?;
        if synchronize_snapshot {
            self.snapshot_read_barrier.wait().await;
        }
        Ok(rows)
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

struct UnchangedUpdateDb;

#[async_trait]
impl DbPlugin for UnchangedUpdateDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        Ok(vec![DbRow::new(BTreeMap::from([
            ("bot_uuid".to_string(), Value::from("unchanged")),
            ("name".to_string(), Value::from("Unchanged")),
            ("bot_info".to_string(), Value::from("{}")),
            ("visibility".to_string(), Value::from("protected")),
            ("status".to_string(), Value::from("online")),
            ("actor_kind".to_string(), Value::from("bot")),
            ("env".to_string(), Value::from("dev")),
            ("created_by".to_string(), Value::from("staff-1")),
            ("agent_code".to_string(), Value::Null),
            ("gmt_create_ms".to_string(), Value::from(1_000_i64)),
            ("gmt_modified_ms".to_string(), Value::from(1_000_i64)),
        ]))])
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        Ok(DbExecuteResult {
            affected_rows: 0,
            last_insert_id: None,
        })
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        Ok(Vec::new())
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

async fn fixture() -> (PersistentBotRepo, Arc<dyn DbPlugin>) {
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_sql_flavor(
        db.clone(),
        DbSqlFlavor::Sqlite,
    );
    (repo, db)
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            agent_card TEXT,
            connection_mode TEXT DEFAULT 'plugin',
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bot_uuid TEXT NOT NULL,
            name TEXT NOT NULL,
            bot_info TEXT,
            session_token TEXT,
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            env TEXT,
            visibility TEXT NOT NULL DEFAULT 'public',
            created_by TEXT,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            status TEXT NOT NULL DEFAULT 'online',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            agent_code TEXT,
            task_claim_mode INTEGER NOT NULL DEFAULT 0,
            task_dream_mode INTEGER NOT NULL DEFAULT 0,
            user_visibility TEXT NOT NULL DEFAULT 'protected',
            friend_ext JSON,
            friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL',
            UNIQUE (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bots table");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_friendships (
            left_bot TEXT NOT NULL,
            right_bot TEXT NOT NULL,
            env TEXT NOT NULL,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (left_bot, right_bot, env)
        )",
    ))
    .await
    .expect("create friendships table");
    db
}

#[allow(clippy::too_many_arguments)]
async fn seed_bot(
    db: &dyn DbPlugin,
    bot_id: &str,
    name: &str,
    kind: &str,
    visibility: &str,
    status: &str,
    created_by: Option<&str>,
    timestamp: &str,
) {
    let bot_info = serde_json::json!({
        "summary": format!("summary-{bot_id}"),
        "domains": ["planning"],
        "skills": [{"name": "plan", "description": "Make a plan"}],
        "scopes": ["workspace"]
    })
    .to_string();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots
         (gmt_create, gmt_modified, bot_uuid, name, bot_info, session_token,
          registered_at, updated_at, env, visibility, created_by, actor_kind,
          status, is_deleted, agent_code)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        vec![
            Value::from(timestamp),
            Value::from(timestamp),
            Value::from(bot_id),
            Value::from(name),
            Value::from(bot_info),
            Value::from(format!("token-{bot_id}")),
            Value::from(timestamp),
            Value::from(timestamp),
            Value::from("dev"),
            Value::from(visibility),
            created_by.map(Value::from).unwrap_or(Value::Null),
            Value::from(kind),
            Value::from(status),
            Value::from(format!("agent-{bot_id}")),
        ],
    ))
    .await
    .expect("seed bot row");
}


/// Sort a Vec<String> in place so roster filter assertions stay order-independent
/// (the repo orders by gmt_create DESC, bot_uuid ASC, which the caller should not re-derive).
fn sorted_ids(mut ids: Vec<String>) -> Vec<String> {
    ids.sort();
    ids
}

/// Extract + sort the bot_ids from a roster query result for order-independent comparison.
fn roster_ids(records: Vec<BotControlPlaneRecord>) -> Vec<String> {
    sorted_ids(records.into_iter().map(|record| record.bot_id).collect())
}


/// Build a `BotTaskModesQuery` with task toggles unset so the visibility /
/// status / user_visibility filters are the only narrowing dimensions. Used by
/// the filter-branch coverage tests below.
fn task_modes_query(
    env: &str,
    visibility: Option<String>,
    status: Option<ActorStatus>,
    user_visibility: Option<UserVisibility>,
) -> BotTaskModesQuery {
    BotTaskModesQuery {
        env: env.to_string(),
        task_claim_mode: None,
        task_dream_mode: None,
        match_mode: TaskModeMatch::Any,
        visibility,
        status,
        user_visibility,
    }
}
