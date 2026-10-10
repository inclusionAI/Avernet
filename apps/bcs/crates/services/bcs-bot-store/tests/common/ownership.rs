//! Shared harness for the Task 5 ownership lifecycle tests:
//! schema installation (registry + provider + frozen Task 2 authority
//! chain), real-query row projections, per-transaction-step failure
//! injection, and the ownership-acceptance probe that encodes the later
//! transfer task's locking discipline.

// Both sibling test binaries compile this whole module; each uses a different
// subset, so unused-in-one-binary items are expected (not real dead code).
#![allow(dead_code, unused_imports)]

use std::sync::Mutex as StdMutex;

// Re-exported so both sibling test binaries can `use support::*` without
// duplicating each crate import.
pub use std::sync::Arc;
pub use tokio::sync::Barrier;

pub use async_trait::async_trait;
pub use bcs_bot_store::provider::MemoryBotProviderStore;
pub use bcs_bot_store::{DbProviderStore, MemoryBotRepo, MemoryProviderStore, PersistentBotRepo};
pub use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbResult, DbRow, DbSqlFlavor, DbTransactionStepResult,
};
pub use bcs_db_api::{DbPlugin, DbStatement, DbTransactionStep};
pub use bcs_db_local::LocalSqliteDbPlugin;
pub use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
pub use bcs_service_api::port::repo::BotAuthorityRepoPort;
pub use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
pub use bcs_service_api::types::error::AuthorityError;
pub use bcs_service_api::types::{
    AuditActor, BotOperationActor, BotOperationContext, OwnershipInitialization,
};
pub use bcs_service_api::{BotCapabilities, BotRepoPort, ServiceError};

pub fn caps(name: &str) -> BotCapabilities {
    BotCapabilities {
        name: Some(name.into()),
        summary: Some(format!("summary-{name}")),
        domains: vec![format!("domain-{name}")],
        visibility: "private".into(),
        ..Default::default()
    }
}

pub fn human_init(user: &str) -> OwnershipInitialization {
    OwnershipInitialization {
        owner_user_id: user.into(),
        actor: AuditActor::Human {
            user_id: user.into(),
        },
        operation_id: uuid::Uuid::new_v4().to_string(),
    }
}

pub fn system_init(user: &str) -> OwnershipInitialization {
    OwnershipInitialization {
        owner_user_id: user.into(),
        actor: AuditActor::System {
            name: "ownership-repair".into(),
        },
        operation_id: uuid::Uuid::new_v4().to_string(),
    }
}

pub fn service_init(user: &str) -> OwnershipInitialization {
    OwnershipInitialization {
        owner_user_id: user.into(),
        actor: AuditActor::Service {
            service_id: "team-manager-sync".into(),
        },
        operation_id: uuid::Uuid::new_v4().to_string(),
    }
}

pub fn operation(user: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: uuid::Uuid::new_v4().to_string(),
        actor: BotOperationActor::Human {
            user_id: user.into(),
            effective_actor_id: user.into(),
        },
    }
}

pub fn record(id: &str, mode: BotConnectionMode) -> BotProviderRecord {
    BotProviderRecord {
        bot_uuid: id.into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: id.into(),
        connection_mode: mode,
        webhook_url: None,
        is_deleted: false,
    }
}

// ---------------------------------------------------------------------------
// SQL schemas: registry + provider storage + the frozen Task 2 authority
// chain, so the store code runs against the shipped table shapes.
// ---------------------------------------------------------------------------

pub const SQLITE_AUTHORITY_MIGRATION: &str =
    include_str!("../../../../../migrations/sqlite/034_bot_authority.sql");
pub const SQLITE_PROVIDER_MIGRATION: &str =
    include_str!("../../../../../migrations/sqlite/030_bot_provider_storage.sql");

pub async fn install_sqlite_schema(db: &dyn DbPlugin) {
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            bot_uuid TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL,
            bot_info TEXT, session_token TEXT UNIQUE, created_by TEXT, visibility TEXT,
            status TEXT NOT NULL DEFAULT 'online', actor_kind TEXT NOT NULL DEFAULT 'bot',
            is_deleted INTEGER NOT NULL DEFAULT 0, agent_code TEXT,
            registered_at TEXT, updated_at TEXT,
            ownership_version INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bot_uuid, env))",
    ))
    .await
    .unwrap();
    for statement in SQLITE_PROVIDER_MIGRATION
        .split(';')
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        db.execute(DbStatement::new(statement)).await.unwrap();
    }
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_provider_bot_bindings (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, \
         provider_id TEXT NOT NULL, provider_bot_ref TEXT NOT NULL, webhook_url TEXT, \
         disabled INTEGER NOT NULL DEFAULT 0, gmt_create TEXT DEFAULT CURRENT_TIMESTAMP, \
         gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (env, bot_uuid), \
         UNIQUE (env, provider_id, provider_bot_ref))",
    ))
    .await
    .unwrap();
    for statement in SQLITE_AUTHORITY_MIGRATION
        .split(';')
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        db.execute(DbStatement::new(statement)).await.unwrap();
    }
    db.execute(DbStatement::new(
        "CREATE TABLE IF NOT EXISTS permission_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, \
         bot_id TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL DEFAULT 'default', \
         description TEXT, rules_template TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, \
         digest TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0, \
         status TEXT NOT NULL DEFAULT 'active', created_by TEXT NOT NULL, updated_by TEXT, \
         gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
         gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .unwrap();
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_profile_bot_env_default \
         ON permission_profiles(bot_id, env, is_default) WHERE status = 'active'",
    ))
    .await
    .unwrap();
}

pub async fn sqlite() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    install_sqlite_schema(db.as_ref()).await;
    db
}

pub async fn sqlite_file(path: &std::path::Path) -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new_file(path).unwrap());
    install_sqlite_schema(db.as_ref()).await;
    db
}

pub fn persistent(db: Arc<dyn DbPlugin>) -> PersistentBotRepo {
    PersistentBotRepo::with_sql_flavor(db, DbSqlFlavor::Sqlite)
}

pub async fn scalar(db: &dyn DbPlugin, sql: &str, params: Vec<bcs_db_api::DbValue>) -> i64 {
    let rows = db
        .query(DbStatement::with_params(sql, params))
        .await
        .unwrap();
    rows.first()
        .and_then(|row| row.get_i64("value").ok().flatten())
        .unwrap_or(0)
}

pub async fn ownership_version_of(db: &dyn DbPlugin, bot_id: &str) -> i64 {
    let env = bcs_config::resolve_env_str();
    scalar(
        db,
        "SELECT ownership_version AS value FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
        vec![bot_id.into(), env.as_str().into()],
    )
    .await
}

pub async fn approved_owner_edge_count(db: &dyn DbPlugin, bot_id: &str) -> i64 {
    let env = bcs_config::resolve_env_str();
    scalar(
        db,
        "SELECT COUNT(*) AS value FROM edge_grants \
         WHERE env = ? AND to_id = ? AND grant_kind = 'owner' AND status = 'approved'",
        vec![env.as_str().into(), bot_id.into()],
    )
    .await
}

pub async fn approved_role_edge_count_to(db: &dyn DbPlugin, bot_id: &str) -> i64 {
    let env = bcs_config::resolve_env_str();
    scalar(
        db,
        "SELECT COUNT(*) AS value FROM edge_grants \
         WHERE env = ? AND to_id = ? AND status = 'approved' \
           AND grant_kind IN ('owner', 'manager')",
        vec![env.as_str().into(), bot_id.into()],
    )
    .await
}

pub async fn approved_role_edge_count_from(db: &dyn DbPlugin, actor_id: &str) -> i64 {
    let env = bcs_config::resolve_env_str();
    scalar(
        db,
        "SELECT COUNT(*) AS value FROM edge_grants \
         WHERE env = ? AND from_id = ? AND status = 'approved' \
           AND grant_kind IN ('owner', 'manager')",
        vec![env.into(), actor_id.into()],
    )
    .await
}

pub async fn initialization_count(db: &dyn DbPlugin, bot_id: &str) -> i64 {
    let env = bcs_config::resolve_env_str();
    scalar(
        db,
        "SELECT COUNT(*) AS value FROM bot_ownership_initializations \
         WHERE env = ? AND bot_id = ?",
        vec![env.as_str().into(), bot_id.into()],
    )
    .await
}

pub async fn default_profile_count(db: &dyn DbPlugin, bot_id: &str) -> i64 {
    let env = bcs_config::resolve_env_str();
    scalar(
        db,
        "SELECT COUNT(*) AS value FROM permission_profiles \
         WHERE env = ? AND bot_id = ? AND is_default = 1 AND status = 'active'",
        vec![env.as_str().into(), bot_id.into()],
    )
    .await
}

pub async fn default_profile_id(db: &dyn DbPlugin, bot_id: &str) -> Option<i64> {
    let env = bcs_config::resolve_env_str();
    let rows = db
        .query(DbStatement::with_params(
            "SELECT id AS value FROM permission_profiles \
             WHERE env = ? AND bot_id = ? AND is_default = 1 AND status = 'active'",
            vec![env.as_str().into(), bot_id.into()],
        ))
        .await
        .unwrap();
    rows.first()
        .and_then(|row| row.get_i64("value").ok().flatten())
}

pub async fn pending_transfer_row(db: &dyn DbPlugin, bot_id: &str) -> (String, String, Option<String>) {
    let env = bcs_config::resolve_env_str();
    let rows = db
        .query(DbStatement::with_params(
            "SELECT transfer_id, status, terminal_reason FROM bot_ownership_transfers \
             WHERE env = ? AND bot_id = ? ORDER BY id",
            vec![env.as_str().into(), bot_id.into()],
        ))
        .await
        .unwrap();
    let row = rows.first().expect("one seeded pending transfer");
    (
        row.get_string("transfer_id").unwrap().unwrap(),
        row.get_string("status").unwrap().unwrap(),
        row.get_string("terminal_reason").ok().flatten(),
    )
}

pub async fn seed_pending_transfer(db: &dyn DbPlugin, bot_id: &str, from: &str, to: &str) -> String {
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params(
        "INSERT INTO bot_ownership_transfers \
         (transfer_id, env, bot_id, from_user_id, to_user_id, expected_owner_version, \
          client_request_id, status, expires_at, bot_name_snapshot) \
         VALUES (?, ?, ?, ?, ?, 1, ?, 'pending', CURRENT_TIMESTAMP, 'snapshot')",
        vec![
            uuid::Uuid::new_v4().to_string().into(),
            env.into(),
            bot_id.into(),
            from.into(),
            to.into(),
            uuid::Uuid::new_v4().to_string().into(),
        ],
    ))
    .await
    .unwrap();
    pending_transfer_row(db, bot_id).await.0
}

pub async fn seed_manager_edge(db: &dyn DbPlugin, bot_id: &str, user_id: &str) {
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
         VALUES (?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from', NULL, 'direct', 'manual')",
        vec![
            env.into(),
            format!("human_{user_id}").into(),
            bot_id.into(),
        ],
    ))
    .await
    .unwrap();
}

pub async fn is_deleted(db: &dyn DbPlugin, bot_id: &str) -> bool {
    let env = bcs_config::resolve_env_str();
    scalar(
        db,
        "SELECT COALESCE(is_deleted, 0) AS value FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
        vec![bot_id.into(), env.as_str().into()],
    )
    .await
        == 1
}

// ---------------------------------------------------------------------------
// Failure injection: replace the marked transaction step with a failing
// statement, so the atomicity contract is proven against the real rollback.
// ---------------------------------------------------------------------------

pub const CAS_MARKER: &str = "SET ownership_version = 1";
pub const OWNER_EDGE_MARKER: &str = "INSERT INTO edge_grants";
pub const PROFILE_MARKER: &str = "INTO permission_profiles";
pub const INIT_AUDIT_MARKER: &str = "INTO bot_ownership_initializations";
pub const DELETE_AUDIT_MARKER: &str = "INTO bcs_bot_action_audits";

pub struct InjectedStepDb {
    db: Arc<dyn DbPlugin>,
    armed: StdMutex<Option<&'static str>>,
    /// Test-only: a write that is committed through the underlying handle
    /// (a genuinely separate autocommit) immediately BEFORE the next
    /// transaction whose steps match `target` — the deterministic
    /// between-the-pre-read-and-the-attempt racing writer, no sleeps.
    pending_race: StdMutex<Option<PendingRace>>,
}

pub type PendingRace = (String, DbStatement);

impl InjectedStepDb {
    pub fn new(db: Arc<dyn DbPlugin>) -> Arc<Self> {
        Arc::new(Self {
            db,
            armed: StdMutex::new(None),
            pending_race: StdMutex::new(None),
        })
    }

    pub fn arm(&self, marker: &'static str) {
        *self.armed.lock().unwrap() = Some(marker);
    }

    pub fn disarm(&self) {
        *self.armed.lock().unwrap() = None;
    }

    /// Arm a one-shot racing write: before the next transaction whose SQL
    /// contains `target`, commit `sql`/`params` standalone (autocommit), so
    /// the raced-in state is visible to that transaction's guards but was
    /// never visible to the caller's earlier pre-read.
    pub fn arm_racing_write(&self, target: &str, sql: &str, params: Vec<bcs_db_api::DbValue>) {
        *self.pending_race.lock().unwrap() =
            Some((target.to_string(), DbStatement::with_params(sql, params)));
    }
}

#[async_trait]
impl DbPlugin for InjectedStepDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.db.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.db.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        // The deterministic racing writer: commit the armed statement
        // standalone (a real separate commit) the moment the matching
        // transaction is about to start. The guard is consumed before any
        // await so the async block stays Send.
        let pending_race = self.pending_race.lock().unwrap().take();
        if let Some((target, statement)) = pending_race {
            let matches = steps.iter().any(|step| match step {
                DbTransactionStep::Query(inner) => inner.sql().contains(&target),
                DbTransactionStep::Execute(inner) => inner.sql().contains(&target),
                DbTransactionStep::ExecuteChecked { statement: inner, .. } => {
                    inner.sql().contains(&target)
                }
            });
            if matches {
                self.db.execute(statement).await?;
            }
        }
        let marker = self.armed.lock().unwrap().take();
        let mut steps = steps;
        if let Some(marker) = marker {
            for step in steps.iter_mut() {
                let sql = match step {
                    DbTransactionStep::Query(statement) => statement.sql(),
                    DbTransactionStep::Execute(statement) => statement.sql(),
                    DbTransactionStep::ExecuteChecked { statement, .. } => statement.sql(),
                };
                if sql.contains(marker) {
                    *step = DbTransactionStep::ExecuteChecked {
                        statement: DbStatement::new("SELECT 1 FROM bcs_ownership_failure_injection"),
                        expected_affected_rows: 1,
                    };
                    break;
                }
            }
        }
        self.db.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.db.health_check().await
    }
}
// ---------------------------------------------------------------------------
// Bot authority acceptance probe (the later ownership-transfer task's locked
// shape, re-encoded here): bot lock first, ownership-version CAS, unique
// owner-slot swap, then the pending-slot consume. Proves retirement's
// leader-wins semantics without importing a store that does not exist yet.
// ---------------------------------------------------------------------------

pub async fn probe_accept_ownership(
    db: &dyn DbPlugin,
    bot_id: &str,
    new_owner_user_id: &str,
    transfer_id: &str,
) -> DbResult<()> {
    let env = bcs_config::resolve_env_str();
    db.transaction(vec![
        DbTransactionStep::Query(DbStatement::with_params(
            "SELECT bot_uuid FROM bcs_bots \
             WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0",
            vec![bot_id.into(), env.as_str().into()],
        )),
        DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE bcs_bots SET ownership_version = ownership_version + 1 \
                 WHERE bot_uuid = ? AND env = ? AND ownership_version = 1 \
                   AND COALESCE(is_deleted, 0) = 0",
                vec![bot_id.into(), env.as_str().into()],
            ),
            expected_affected_rows: 1,
        },
        DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE edge_grants SET status = 'revoked' \
                 WHERE env = ? AND to_id = ? AND grant_kind = 'owner' AND status = 'approved'",
                vec![env.as_str().into(), bot_id.into()],
            ),
            expected_affected_rows: 1,
        },
        DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                 status, originator_policy_type, originator_policy_data, \
                 management_source_kind, management_source_id) \
                 VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL, 'owner', 'owner')",
                vec![
                    env.clone().into(),
                    format!("human_{new_owner_user_id}").into(),
                    bot_id.into(),
                ],
            ),
            expected_affected_rows: 1,
        },
        DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE bot_ownership_transfers SET status = 'accepted', \
                 decision_actor_kind = 'human', decided_by = ?, decided_at = CURRENT_TIMESTAMP, \
                 result_owner_version = 2, gmt_modified = CURRENT_TIMESTAMP \
                 WHERE transfer_id = ? AND status = 'pending'",
                vec![new_owner_user_id.into(), transfer_id.into()],
            ),
            expected_affected_rows: 1,
        },
    ])
    .await
    .map(|_| ())
}

pub fn assert_conflict(error: &ServiceError) {
    match error {
        ServiceError::Conflict(_) => {}
        ServiceError::Authority(AuthorityError::Conflict(_)) => {}
        other => panic!("expected a business conflict, got: {other}"),
    }
}

pub fn assert_bot_not_found(error: &ServiceError) {
    match error {
        ServiceError::BotNotFound(_) => {}
        other => panic!("expected BotNotFound, got: {other}"),
    }
}


// ---------------------------------------------------------------------------
// Ignored MySQL live conformance: the same ownership lifecycle contract on
// the MySQL dialect. No MySQL server is reachable in this dev environment
// (port closed, no client), so live MySQL behavior — FOR UPDATE locking,
// INSERT IGNORE, generated unique slots, CHECK enforcement — is UNVERIFIED
// here. CI runs this test against its MySQL service. SQLite (above) is the
// runnable dialect.
// ---------------------------------------------------------------------------

pub const MYSQL_CHAIN: &[&str] = &[
    include_str!("../../../../../migrations/mysql/001_init_schema.sql"),
    include_str!("../../../../../migrations/mysql/002_add_owner_bot_id.sql"),
    include_str!("../../../../../migrations/mysql/003_add_organizations.sql"),
    include_str!("../../../../../migrations/mysql/004_add_session_collection.sql"),
    include_str!("../../../../../migrations/mysql/005_add_session_collection_timestamp.sql"),
    include_str!("../../../../../migrations/mysql/006_session_files.sql"),
    include_str!("../../../../../migrations/mysql/007_add_human_input_runtime.sql"),
    include_str!("../../../../../migrations/mysql/008_human_input_im_requests.sql"),
    include_str!("../../../../../migrations/mysql/009_eventing.sql"),
    include_str!("../../../../../migrations/mysql/010_group_opening_message.sql"),
    include_str!("../../../../../migrations/mysql/011_group_participant_tags.sql"),
    include_str!("../../../../../migrations/mysql/012_expand_session_ids.sql"),
    include_str!("../../../../../migrations/mysql/013_add_bot_task_modes.sql"),
    include_str!("../../../../../migrations/mysql/014_edge_permission.sql"),
    include_str!("../../../../../migrations/mysql/015_add_bot_internal_attributes.sql"),
    include_str!("../../../../../migrations/mysql/016_session_callback_lease_and_chat_runs.sql"),
    include_str!("../../../../../migrations/mysql/017_state_machine_rerun_lineage.sql"),
    include_str!("../../../../../migrations/mysql/018_one_shot_opening_message_override.sql"),
    include_str!("../../../../../migrations/mysql/019_invite_code.sql"),
    include_str!("../../../../../migrations/mysql/020_human_participant_message_visibility.sql"),
    include_str!("../../../../../migrations/mysql/021_message_deliveries.sql"),
    include_str!("../../../../../migrations/mysql/022_message_delivery_policy.sql"),
    include_str!("../../../../../migrations/mysql/023_delivery_worker_queries.sql"),
    include_str!("../../../../../migrations/mysql/024_delivery_context_selection.sql"),
    include_str!("../../../../../migrations/mysql/025_delivery_pending_abort.sql"),
    include_str!("../../../../../migrations/mysql/026_run_reply_segments.sql"),
    include_str!("../../../../../migrations/mysql/027_provider_bot_webhook.sql"),
    include_str!("../../../../../migrations/mysql/028_fixed_loop_runtime.sql"),
    include_str!("../../../../../migrations/mysql/029_bot_provider_storage.sql"),
    include_str!("../../../../../migrations/mysql/030_group_human_mention_notify_mode.sql"),
    include_str!("../../../../../migrations/mysql/033_bot_authority.sql"),
];

/// Open the `BCS_TEST_MYSQL_URL` datasource with a single pooled connection
/// (stable session timezone and DDL order) and apply the full MySQL chain.
pub async fn mysql_url_plugin() -> Arc<dyn DbPlugin> {
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
            extra: std::collections::BTreeMap::new(),
        })
        .with_statement_protocol(bcs_config_api::StatementProtocol::Text);
    let mut config = config;
    config.pool_size = 1;
    config.min_pool_size = 1;
    let manager = bcs_db_mysql::MysqlDbManager::new(config)
        .await
        .expect("open MySQL contract datasource");
    let plugin: Arc<dyn DbPlugin> =
        Arc::new(bcs_db_mysql::MysqlDbPlugin::new(manager, database));
    for file in MYSQL_CHAIN {
        let body: String = file
            .lines()
            .filter(|line| !line.trim_start().starts_with("--"))
            .collect::<Vec<_>>()
            .join("\n");
        for statement in body.split(';').map(str::trim).filter(|s| !s.is_empty()) {
            plugin
                .execute(DbStatement::new(statement))
                .await
                .expect("apply MySQL chain statement");
        }
    }
    plugin
}
