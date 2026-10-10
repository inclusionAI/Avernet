//! Shared internals of the `team_manager_sync` test binary (plan Task 7):
//! the per-driver seeding levers, the deterministic failure/drift plugin
//! wrapper, and the shared Memory/SQLite conformance suite kept in a
//! subdirectory module (`mod.rs`) so cargo does not auto-discover it as a
//! second integration-test binary (`team_manager_sync.rs` pulls it in
//! with `#[path]` and declares the individual `#[tokio::test]`s).
//!
//! Cases mandated by the brief, run identically against BOTH production
//! store drivers:
//! - the RED snapshot verbatim: an empty snapshot revokes ONLY this team's
//!   members, replays return the original receipt without audit growth,
//!   and a direct-only manager keeps its Manager role;
//! - a completely no-difference sync still persists its durable receipt;
//! - same key with a changed operation/payload → Conflict;
//! - a move replays without resurrecting the old team, and replaying the
//!   OLD sync's key never re-applies its recorded effect;
//! - team intersection / other-team union: revoking from one team leaves
//!   the other teams' and direct sources intact;
//! - an ordinary member absent from the new snapshot loses authority;
//!   the owner edge coexists with team edges and empty team edges;
//! - the 1,000/1,001 snapshot boundary (Gate 0, spec §1.3);
//! - an injected audit/receipt write failure rolls back EVERYTHING;
//! - credential scope re-validation fails closed; bot validation branches;
//!   unknown/blank/cross-env snapshot members are rejected.

#[path = "../../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::types::team_manager_sync::{
    TeamManagerOperation, TeamManagerSync, VerifiedTeamManagerService,
};
use tempfile::TempDir;

pub const ENV: &str = "local";

// ---------------------------------------------------------------------------
// Command fixtures (the trusted-verifier stand-in of plan Task 13)
// ---------------------------------------------------------------------------

pub fn verified_service(env: &str) -> VerifiedTeamManagerService {
    VerifiedTeamManagerService {
        service_id: "team-sync-svc".to_string(),
        env: env.to_string(),
        allowed_bots: None,
        allowed_teams: None,
        allowed_operations: None,
    }
}

pub fn restricted_service(env: &str) -> VerifiedTeamManagerService {
    VerifiedTeamManagerService {
        service_id: "restricted-svc".to_string(),
        env: env.to_string(),
        allowed_bots: Some(vec!["bot-b".to_string()]),
        allowed_teams: Some(vec!["allowed-team".to_string()]),
        allowed_operations: Some(vec![TeamManagerOperation::Sync]),
    }
}

pub fn sync_cmd(
    service: VerifiedTeamManagerService,
    bot_id: &str,
    team_id: &str,
    users: &[&str],
    key: &str,
) -> TeamManagerSync {
    TeamManagerSync {
        service,
        bot_id: bot_id.to_string(),
        team_id: team_id.to_string(),
        operation: TeamManagerOperation::Sync,
        manager_user_ids: users.iter().map(|user| user.to_string()).collect(),
        idempotency_key: key.to_string(),
    }
}

pub fn move_cmd(
    service: VerifiedTeamManagerService,
    bot_id: &str,
    old_team: &str,
    new_team: &str,
    users: &[&str],
    key: &str,
) -> TeamManagerSync {
    TeamManagerSync {
        service,
        bot_id: bot_id.to_string(),
        team_id: old_team.to_string(),
        operation: TeamManagerOperation::Move {
            new_team_id: new_team.to_string(),
        },
        manager_user_ids: users.iter().map(|user| user.to_string()).collect(),
        idempotency_key: key.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Harness (per-driver seeding + levers; production work goes through `repo`)
// ---------------------------------------------------------------------------

#[async_trait]
pub trait TeamSyncDriver: Send + Sync {
    /// The env this driver's authority rows are bound to (the SQLite
    /// store binds "local"; the Memory repo resolves the process env).
    fn env(&self) -> String;
    /// One legal Bot (live, version 1) + its approved owner edge (Task 2
    /// schema / same critical section as the lifecycle).
    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str);
    /// Bulk live-Human materialization (`human_<user_id>` actor rows).
    async fn seed_humans(&self, users: &[&str]);
    /// Formal-source manager seeding — NEVER a friend edge.
    async fn seed_manager_source(&self, bot_id: &str, user_id: &str, kind: &str, id: &str);
    async fn seed_uninitialized_bot(&self, bot_id: &str);
    /// Arm a one-shot failure of the next team-sync WRITE (the audit /
    /// durable-receipt step of the one Bot transaction / the memory
    /// critical section): the failure must surface with NO partial state.
    fn arm_sync_write_failure(&self);
    async fn audit_count(&self) -> u64;
    /// Persisted `bot_manager_sync_operations` rows of this Bot (durable —
    /// not memory-only — idempotency).
    async fn sync_receipt_row_count(&self, bot_id: &str) -> u64;
    /// The Bot's `bot_team_manager_sources` binding rows as
    /// `(team_id, status, last_operation_id)`, team_id ASC.
    async fn team_bindings(&self, bot_id: &str) -> Vec<(String, String, String)>;
}

pub struct Harness {
    pub repo: Arc<dyn BotAuthorityRepoPort>,
    pub driver: Arc<dyn TeamSyncDriver>,
}

impl Harness {
    #[allow(dead_code)]
    pub fn env(&self) -> String {
        self.driver.env()
    }
}

// ---------------------------------------------------------------------------
// SQLite driver over the full migration chain
// ---------------------------------------------------------------------------

/// DbPlugin wrapper: an armed flag fails the NEXT transaction that
/// writes the team-sync commit surface (`bot_manager_changes` audit or
/// the `bot_manager_sync_operations` durable receipt) — seeding and
/// read-only transactions pass untouched. Also counts transactions and
/// standalone queries so the bulk case can pin the statement budget, and
/// can arm a deterministic TOCTOU drift applied on the raw connection
/// the moment the WRITE transaction arrives (after the validated read).
pub struct SyncDb {
    pub inner: Arc<dyn DbPlugin>,
    armed: AtomicBool,
    drift: Mutex<Option<String>>,
    transactions: AtomicUsize,
    standalone_queries: AtomicUsize,
}

impl SyncDb {
    fn steps_write_commit_surface(steps: &[DbTransactionStep]) -> bool {
        let touches = |statement: &DbStatement| {
            statement.sql().contains("bot_manager_changes")
                || statement.sql().contains("bot_manager_sync_operations")
        };
        steps.iter().any(|step| match step {
            DbTransactionStep::Query(statement) => touches(statement),
            DbTransactionStep::Execute(statement) => touches(statement),
            DbTransactionStep::ExecuteChecked { statement, .. } => touches(statement),
        })
    }

    fn steps_change_edge_grants(steps: &[DbTransactionStep]) -> bool {
        let changes = |statement: &DbStatement| {
            statement.sql().contains("INSERT INTO edge_grants")
                || statement.sql().contains("UPDATE edge_grants")
        };
        steps.iter().any(|step| match step {
            DbTransactionStep::Query(_) => false,
            DbTransactionStep::Execute(statement) => changes(statement),
            DbTransactionStep::ExecuteChecked { statement, .. } => changes(statement),
        })
    }

    pub fn arm_drift(&self, sql: &str) {
        *self.drift.lock().unwrap() = Some(sql.to_string());
    }

    pub fn transaction_count(&self) -> usize {
        self.transactions.load(Ordering::SeqCst)
    }

    pub fn query_count(&self) -> usize {
        self.standalone_queries.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl DbPlugin for SyncDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.standalone_queries.fetch_add(1, Ordering::SeqCst);
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.transactions.fetch_add(1, Ordering::SeqCst);
        if Self::steps_change_edge_grants(&steps) {
            let drift_sql = { self.drift.lock().unwrap().take() };
            if let Some(drift_sql) = drift_sql {
                self.inner
                    .execute(DbStatement::new(drift_sql))
                    .await
                    .expect("apply the armed drift statement");
            }
        }
        if self.armed.load(Ordering::SeqCst) && Self::steps_write_commit_surface(&steps) {
            self.armed.store(false, Ordering::SeqCst);
            return Err(bcs_db_api::DbError::Backend(
                "test-injected team-sync commit write failure".into(),
            ));
        }
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

struct SqliteDriver {
    db: Arc<SyncDb>,
    env: String,
}

#[async_trait]
impl TeamSyncDriver for SqliteDriver {
    fn env(&self) -> String {
        self.env.clone()
    }

    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) {
        self.db
            .inner
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) VALUES (?, ?, ?, 1)",
                    vec![
                        DbValue::from(bot_id),
                        DbValue::from(bot_id),
                        DbValue::from(self.env.clone()),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
                     originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
                     VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL, 'owner', 'owner')",
                    vec![
                        DbValue::from(self.env.clone()),
                        DbValue::from(format!("human_{owner_user_id}")),
                        DbValue::from(bot_id),
                    ],
                )),
            ])
            .await
            .expect("seed_owned");
    }

    async fn seed_humans(&self, users: &[&str]) {
        // Parameter-bounded and idempotent: look up what exists, insert
        // only the missing rows (multi-VALUES, 100 rows per statement).
        for chunk in users.chunks(100) {
            if chunk.is_empty() {
                continue;
            }
            let mut lookup_conjunction = String::new();
            let mut lookup_params = vec![DbValue::from(self.env.clone())];
            for (index, user) in chunk.iter().enumerate() {
                if index > 0 {
                    lookup_conjunction.push_str(", ");
                }
                lookup_conjunction.push('?');
                lookup_params.push(DbValue::from(format!("human_{user}")));
            }
            let existing = self
                .db
                .inner
                .query(DbStatement::with_params(
                    &format!(
                        "SELECT bot_uuid FROM bcs_bots WHERE env = ? \
                         AND bot_uuid IN ({lookup_conjunction})"
                    ),
                    lookup_params,
                ))
                .await
                .expect("seed_humans lookup");
            let present: Vec<String> = existing
                .iter()
                .filter_map(|row| row.get_string("bot_uuid").ok().flatten())
                .collect();
            let missing: Vec<&str> = chunk
                .iter()
                .filter(|user| !present.iter().any(|row| row == &format!("human_{user}")))
                .copied()
                .collect();
            if missing.is_empty() {
                continue;
            }
            let mut sql = String::from(
                "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) VALUES ",
            );
            let mut params = Vec::with_capacity(missing.len() * 3);
            for (index, user) in missing.iter().enumerate() {
                if index > 0 {
                    sql.push_str(", ");
                }
                sql.push_str("(?, ?, ?, 'human', 'online')");
                params.push(DbValue::from(format!("human_{user}")));
                params.push(DbValue::from(*user));
                params.push(DbValue::from(self.env.clone()));
            }
            self.db
                .inner
                .execute(DbStatement::with_params(&sql, params))
                .await
                .expect("seed_humans");
        }
    }

    async fn seed_manager_source(&self, bot_id: &str, user_id: &str, kind: &str, id: &str) {
        self.db
            .inner
            .execute(DbStatement::with_params(
                "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
                 originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
                 VALUES (?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from', NULL, ?, ?)",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(format!("human_{user_id}")),
                    DbValue::from(bot_id),
                    DbValue::from(kind),
                    DbValue::from(id),
                ],
            ))
            .await
            .expect("seed_manager_source");
    }

    async fn seed_uninitialized_bot(&self, bot_id: &str) {
        self.db
            .inner
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, name, env) VALUES (?, ?, ?)",
                vec![
                    DbValue::from(bot_id),
                    DbValue::from(bot_id),
                    DbValue::from(self.env.clone()),
                ],
            ))
            .await
            .expect("seed_uninitialized_bot");
    }

    fn arm_sync_write_failure(&self) {
        self.db
            .armed
            .store(true, Ordering::SeqCst);
    }

    async fn audit_count(&self) -> u64 {
        let rows = self
            .db
            .inner
            .query(DbStatement::new("SELECT COUNT(*) AS n FROM bot_manager_changes"))
            .await
            .expect("audit_count");
        rows.first()
            .and_then(|row| row.get_i64("n").ok().flatten())
            .unwrap_or(0)
            .max(0) as u64
    }

    async fn sync_receipt_row_count(&self, bot_id: &str) -> u64 {
        let rows = self
            .db
            .inner
            .query(DbStatement::with_params(
                "SELECT COUNT(*) AS n FROM bot_manager_sync_operations \
                 WHERE env = ? AND bot_id = ?",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(bot_id),
                ],
            ))
            .await
            .expect("sync_receipt_row_count");
        rows.first()
            .and_then(|row| row.get_i64("n").ok().flatten())
            .unwrap_or(0)
            .max(0) as u64
    }

    async fn team_bindings(&self, bot_id: &str) -> Vec<(String, String, String)> {
        let rows = self
            .db
            .inner
            .query(DbStatement::with_params(
                "SELECT team_id, status, last_operation_id FROM bot_team_manager_sources \
                 WHERE env = ? AND bot_id = ? ORDER BY team_id",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(bot_id),
                ],
            ))
            .await
            .expect("team_bindings");
        rows.iter()
            .map(|row| {
                (
                    row.get_string("team_id").ok().flatten().unwrap_or_default(),
                    row.get_string("status").ok().flatten().unwrap_or_default(),
                    row.get_string("last_operation_id")
                        .ok()
                        .flatten()
                        .unwrap_or_default(),
                )
            })
            .collect()
    }
}

pub async fn sqlite_harness() -> (Harness, Arc<SyncDb>) {
    let raw = LocalSqliteDbPlugin::new().expect("open local sqlite");
    migrations::run_sqlite_migrations(&raw)
        .await
        .expect("apply the full sqlite migration chain");
    let db = Arc::new(SyncDb {
        inner: Arc::new(raw),
        armed: AtomicBool::new(false),
        drift: Mutex::new(None),
        transactions: AtomicUsize::new(0),
        standalone_queries: AtomicUsize::new(0),
    });
    let driver = SqliteDriver {
        db: db.clone(),
        env: ENV.to_string(),
    };
    let repo: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::sqlite(db.clone(), ENV.to_string()));
    (
        Harness {
            repo,
            driver: Arc::new(driver),
        },
        db,
    )
}

// ---------------------------------------------------------------------------
// Memory driver over the shared MemoryBotRepo state boundary
// ---------------------------------------------------------------------------

struct MemoryDriver {
    repo: Arc<MemoryBotRepo>,
    _dir: TempDir,
}

#[async_trait]
impl TeamSyncDriver for MemoryDriver {
    fn env(&self) -> String {
        bcs_config::resolve_env_str()
    }

    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) {
        self.repo
            .seed_authority_owned(bot_id, owner_user_id)
            .await
            .expect("seed_owned");
    }

    async fn seed_humans(&self, users: &[&str]) {
        for user in users {
            self.repo
                .ensure_human_actor(user, user)
                .await
                .expect("seed_humans");
        }
    }

    async fn seed_manager_source(&self, bot_id: &str, user_id: &str, kind: &str, id: &str) {
        self.repo
            .seed_authority_manager_source(bot_id, user_id, kind, id)
            .await
            .expect("seed_manager_source");
    }

    async fn seed_uninitialized_bot(&self, bot_id: &str) {
        self.repo
            .seed_authority_uninitialized_bot(bot_id)
            .await
            .expect("seed_uninitialized_bot");
    }

    fn arm_sync_write_failure(&self) {
        self.repo.arm_authority_write_failure();
    }

    async fn audit_count(&self) -> u64 {
        self.repo
            .authority_audit_count()
            .await
            .expect("audit_count")
    }

    async fn sync_receipt_row_count(&self, bot_id: &str) -> u64 {
        self.repo
            .authority_sync_operation_count(bot_id)
            .await
            .expect("sync_receipt_row_count")
    }

    async fn team_bindings(&self, bot_id: &str) -> Vec<(String, String, String)> {
        self.repo
            .authority_team_bindings(bot_id)
            .await
            .expect("team_bindings")
    }
}

pub async fn memory_harness() -> Harness {
    let dir = TempDir::new().expect("bots base dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf()));
    Harness {
        repo: repo.clone(),
        driver: Arc::new(MemoryDriver {
            repo,
            _dir: dir,
        }),
    }
}

// ---------------------------------------------------------------------------

mod suite;

pub use suite::team_manager_sync_contract_tests;
