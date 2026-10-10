//! Rule 25 driver: strict bot-authority reads (plan Task 3).
//!
//! Runs the shared authority conformance suites against BOTH drivers:
//! - SQLite: [`DbBotAuthorityStore`] over the FULL migration chain
//!   (`run_sqlite_migrations`, the exact schema production startup builds);
//! - Memory: `bcs_bot_store::MemoryBotRepo` sharing the bot lifecycle state;
//! and the production Core (`bcs_edge_permission::authority`) over both.
//!
//! Besides the shared snapshots, this file owns the driver-specific
//! strictness proofs that only the SQLite state can express: the batch
//! single-statement (no per-pair N+1) SQL count, env isolation, actor
//! deletion failing closed through the Core, a real DB read failure
//! staying `Err` (never an empty vec), and manager-edge readability
//! (strict source decode, owner priority, revoked rows stop reading).

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{BotAccessRelation, OWNER_SOURCE_ID, OWNER_SOURCE_KIND};
use bcs_edge_permission::authority::BotAuthorityCoreServiceImpl;
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::{BotAuthorityCoreService, ServiceError, ServiceResult};
use bcs_test_support::contract::repo::{
    AuthorityHarnessDriver, AuthorityRepoHarness, bot_authority_core_service_contract_tests,
    bot_authority_repo_port_contract_tests,
};
use tempfile::TempDir;

/// Env bound into the SQLite driver's authority repo (authority queries are
/// env-isolated; the env is bound to the store instance, never to a request).
const ENV: &str = "local";

// ---------------------------------------------------------------------------
// SQLite plumbing
// ---------------------------------------------------------------------------

/// Pass-through DbPlugin wrapper whose NEXT WRITE fails exactly once when
/// armed; reads keep reporting the truth. Backs the harness's
/// `fail_next_write` contract: the failure must surface and must never
/// fabricate state or audit rows.
struct FailingWritesDb {
    inner: Arc<dyn DbPlugin>,
    fail_next_write: AtomicBool,
}

impl FailingWritesDb {
    fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            fail_next_write: AtomicBool::new(false),
        }
    }

    fn arm_write_failure(&self) {
        self.fail_next_write.store(true, Ordering::SeqCst);
    }

    fn take_armed(&self) -> bool {
        self.fail_next_write.swap(false, Ordering::SeqCst)
    }
}

#[async_trait]
impl DbPlugin for FailingWritesDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        if self.take_armed() {
            return Err(DbError::Backend("test-injected authority write failure".into()));
        }
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        if self.take_armed() {
            return Err(DbError::Backend("test-injected authority write failure".into()));
        }
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

/// Counting wrapper: records how many SELECT statements crossed the plugin.
struct CountingDb {
    inner: Arc<dyn DbPlugin>,
    query_count: AtomicUsize,
}

impl CountingDb {
    fn count(&self) -> usize {
        self.query_count.load(Ordering::SeqCst)
    }

    fn reset(&self) {
        self.query_count.store(0, Ordering::SeqCst);
    }
}

#[async_trait]
impl DbPlugin for CountingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.query_count.fetch_add(1, Ordering::SeqCst);
        self.inner.query(statement).await
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

/// SQLite driver: seeding follows the Task 2 schema (bcs_bots row + approved
/// owner edge written in ONE transaction); corruption levers are plain SQL.
struct SqliteAuthorityDriver {
    db: Arc<FailingWritesDb>,
    env: String,
}

impl SqliteAuthorityDriver {
    fn internal(&self, operation: &'static str, err: DbError) -> ServiceError {
        ServiceError::InternalError(format!("authority driver {}: {}", operation, err))
    }
}

#[async_trait]
impl AuthorityHarnessDriver for SqliteAuthorityDriver {
    /// Atomically write one legal Bot (version 1) + its approved owner edge
    /// per the Task 2 schema — the test-only stand-in for the Task 5
    /// production initialization contract; never a production claim entry.
    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) -> ServiceResult<()> {
        self.db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) \
                     VALUES (?, ?, ?, 1)",
                    vec![
                        DbValue::from(bot_id),
                        DbValue::from(bot_id),
                        DbValue::from(self.env.clone()),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, \
                     grant_ref_id, rules, status, originator_policy_type, \
                     originator_policy_data, management_source_kind, management_source_id) \
                     VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL, ?, ?)",
                    vec![
                        DbValue::from(self.env.clone()),
                        DbValue::from(human_actor_id(owner_user_id)),
                        DbValue::from(bot_id),
                        DbValue::from(OWNER_SOURCE_KIND),
                        DbValue::from(OWNER_SOURCE_ID),
                    ],
                )),
            ])
            .await
            .map_err(|err| self.internal("seed_owned", err))?;
        Ok(())
    }

    /// Real Human materialization: the `bcs_bots` `human_<user_id>` actor
    /// row (idempotent), mirroring the production ensure_human_actor shape.
    async fn seed_human(&self, user_id: &str) -> ServiceResult<()> {
        let actor_id = human_actor_id(user_id);
        let existing = self
            .db
            .query(DbStatement::with_params(
                "SELECT 1 AS one FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
                vec![
                    DbValue::from(actor_id.as_str()),
                    DbValue::from(self.env.clone()),
                ],
            ))
            .await
            .map_err(|err| self.internal("seed_human", err))?;
        if !existing.is_empty() {
            return Ok(());
        }
        self.db
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) \
                 VALUES (?, ?, ?, 'human', 'online')",
                vec![
                    DbValue::from(actor_id),
                    DbValue::from(user_id),
                    DbValue::from(self.env.clone()),
                ],
            ))
            .await
            .map_err(|err| self.internal("seed_human", err))?;
        Ok(())
    }

    fn fail_next_write(&self) {
        self.db.arm_write_failure();
    }

    async fn audit_count(&self) -> ServiceResult<u64> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT COUNT(*) AS n FROM bot_manager_changes",
                vec![],
            ))
            .await
            .map_err(|err| self.internal("audit_count", err))?;
        let value = rows
            .first()
            .and_then(|row| row.get_i64("n").ok().flatten())
            .unwrap_or(0);
        Ok(value.max(0) as u64)
    }

    async fn seed_uninitialized_bot(&self, bot_id: &str) -> ServiceResult<()> {
        // ownership_version defaults to 0 (uninitialized) in the Task 2
        // schema; historical rows default the same way.
        self.db
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, name, env) VALUES (?, ?, ?)",
                vec![
                    DbValue::from(bot_id),
                    DbValue::from(bot_id),
                    DbValue::from(self.env.clone()),
                ],
            ))
            .await
            .map_err(|err| self.internal("seed_uninitialized_bot", err))?;
        Ok(())
    }

    async fn break_owner_edge(&self, bot_id: &str) -> ServiceResult<()> {
        self.db
            .execute(DbStatement::with_params(
                "UPDATE edge_grants SET status = 'revoked' \
                 WHERE env = ? AND to_id = ? AND grant_kind = 'owner'",
                vec![DbValue::from(self.env.clone()), DbValue::from(bot_id)],
            ))
            .await
            .map_err(|err| self.internal("break_owner_edge", err))?;
        Ok(())
    }
}

/// The full-migration-chain SQLite harness + the raw (wrapped) db handle,
/// for driver-injected corruption like silent updates and cross-env rows.
async fn sqlite_harness() -> (AuthorityRepoHarness, Arc<FailingWritesDb>) {
    let raw = LocalSqliteDbPlugin::new().expect("open local sqlite");
    migrations::run_sqlite_migrations(&raw)
        .await
        .expect("apply the full sqlite migration chain");
    let db = Arc::new(FailingWritesDb::new(Arc::new(raw)));
    let driver = SqliteAuthorityDriver {
        db: db.clone(),
        env: ENV.to_string(),
    };
    let repo: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::sqlite(db.clone(), ENV.to_string()));
    (AuthorityRepoHarness::new(repo, Arc::new(driver)), db)
}

// ---------------------------------------------------------------------------
// Memory driver
// ---------------------------------------------------------------------------

/// In-memory driver: `MemoryBotRepo` shares the bot lifecycle state with the
/// authority rows (same locks / same state boundary); seeding goes through
/// the store's test-only authority levers.
struct MemoryAuthorityDriver {
    repo: Arc<MemoryBotRepo>,
    /// Keeps the bots base dir alive for the test's process lifetime.
    _dir: TempDir,
}

#[async_trait]
impl AuthorityHarnessDriver for MemoryAuthorityDriver {
    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) -> ServiceResult<()> {
        self.repo.seed_authority_owned(bot_id, owner_user_id).await
    }

    async fn seed_human(&self, user_id: &str) -> ServiceResult<()> {
        self.repo.ensure_human_actor(user_id, user_id).await?;
        Ok(())
    }

    fn fail_next_write(&self) {
        self.repo.arm_authority_write_failure();
    }

    async fn audit_count(&self) -> ServiceResult<u64> {
        self.repo.authority_audit_count().await
    }

    async fn seed_uninitialized_bot(&self, bot_id: &str) -> ServiceResult<()> {
        self.repo.seed_authority_uninitialized_bot(bot_id).await
    }

    async fn break_owner_edge(&self, bot_id: &str) -> ServiceResult<()> {
        self.repo.break_authority_owner_edge(bot_id).await
    }
}

async fn memory_harness() -> AuthorityRepoHarness {
    let dir = TempDir::new().expect("bots base dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf()));
    let driver = MemoryAuthorityDriver {
        repo: repo.clone(),
        _dir: dir,
    };
    AuthorityRepoHarness::new(repo, Arc::new(driver))
}

// ---------------------------------------------------------------------------
// Shared suites, both drivers
// ---------------------------------------------------------------------------

#[tokio::test]
async fn sqlite_authority_repo_port_contract() {
    let (h, _) = sqlite_harness().await;
    bot_authority_repo_port_contract_tests(&h).await;
}

#[tokio::test]
async fn sqlite_authority_core_service_contract() {
    let (h, _) = sqlite_harness().await;
    let core = BotAuthorityCoreServiceImpl::new(h.repo.clone());
    bot_authority_core_service_contract_tests(&core, &h).await;
}

#[tokio::test]
async fn memory_authority_repo_port_contract() {
    let h = memory_harness().await;
    bot_authority_repo_port_contract_tests(&h).await;
}

#[tokio::test]
async fn memory_authority_core_service_contract() {
    let h = memory_harness().await;
    let core = BotAuthorityCoreServiceImpl::new(h.repo.clone());
    bot_authority_core_service_contract_tests(&core, &h).await;
}

// ---------------------------------------------------------------------------
// SQLite-specific strictness proofs
// ---------------------------------------------------------------------------

/// The batch read must be a SINGLE statement correlated by full
/// (user_id, bot_id) pairs — never two independent IN sets (an
/// authorization cartesian product) nor a per-pair N+1 lookup.
#[tokio::test]
async fn sqlite_authority_batch_read_is_one_statement_no_pair_n1() {
    let raw = LocalSqliteDbPlugin::new().expect("open local sqlite");
    migrations::run_sqlite_migrations(&raw)
        .await
        .expect("apply sqlite migration chain");
    let counting = Arc::new(CountingDb {
        inner: Arc::new(raw),
        query_count: AtomicUsize::new(0),
    });
    let db = Arc::new(FailingWritesDb::new(counting.clone()));
    let driver = SqliteAuthorityDriver {
        db: db.clone(),
        env: ENV.to_string(),
    };
    let repo: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::sqlite(db, ENV.to_string()));
    let h = AuthorityRepoHarness::new(repo, Arc::new(driver));

    // One owner (a) and 29 subjects holding no role, all on one bot.
    h.seed_owned("bot-batch", "a").await.unwrap();
    let mut pairs = vec![("a".to_string(), "bot-batch".to_string())];
    for i in 0..29 {
        pairs.push((format!("user-{i}"), "bot-batch".to_string()));
    }

    counting.reset();
    let roles = h.repo.roles_for(&pairs).await.unwrap();
    assert_eq!(roles.len(), 30, "position-aligned results for every pair");
    assert_eq!(roles[0], Some(BotAccessRelation::Owner));
    assert!(roles[1..].iter().all(|role| role.is_none()));
    assert_eq!(
        counting.count(),
        1,
        "one batched statement for the whole batch: no per-pair N+1, no cartesian IN pair"
    );
}

/// Authority queries are bound to the store's env: a fully legal owner pair
/// in ANOTHER env is invisible, and a bot living only there does not exist
/// for this store instance.
#[tokio::test]
async fn sqlite_authority_reads_are_env_bound() {
    let (h, db) = sqlite_harness().await;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) VALUES (?, 'Bot X', ?, 1)",
        vec![DbValue::from("bot-x"), DbValue::from("other-env")],
    ))
    .await
    .expect("seed other-env bot");
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
         VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL, ?, ?)",
        vec![
            DbValue::from("other-env"),
            DbValue::from(human_actor_id("u1")),
            DbValue::from("bot-x"),
            DbValue::from(OWNER_SOURCE_KIND),
            DbValue::from(OWNER_SOURCE_ID),
        ],
    ))
    .await
    .expect("seed other-env owner edge");

    assert_eq!(
        h.repo.role("u1", "bot-x").await.unwrap(),
        None,
        "a cross-env role edge must be invisible"
    );
    assert!(
        matches!(h.repo.ownership("bot-x").await, Err(ServiceError::BotNotFound(_))),
        "a bot living only in another env is not present in this env"
    );
}

/// A soft-deleted Bot fails closed through the Core (existence/liveness is
/// validated before any role answer); its stale edges never answer.
#[tokio::test]
async fn sqlite_authority_deleted_actor_fails_closed_via_core() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-del", "d").await.unwrap();
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = ? AND env = ?",
        vec![DbValue::from("bot-del"), DbValue::from(ENV)],
    ))
    .await
    .expect("soft-delete the bot actor");

    let core = BotAuthorityCoreServiceImpl::new(h.repo.clone());
    assert!(
        matches!(core.ownership("bot-del").await, Err(ServiceError::BotNotFound(_))),
        "a soft-deleted Bot has no authority surface left"
    );
    assert!(
        matches!(core.role("d", "bot-del").await, Err(ServiceError::BotNotFound(_))),
        "the Core must validate the Bot BEFORE answering any role question"
    );
}

/// A real DB read failure must surface as `Err`, never as a successful
/// empty batch (fail closed: a query failure is never an empty success).
#[tokio::test]
async fn sqlite_authority_read_failure_stays_err_not_empty_vec() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-a", "a").await.unwrap();
    db.execute(DbStatement::new("DROP TABLE edge_grants"))
        .await
        .expect("break the edge table");

    let outcome = h
        .repo
        .roles_for(&[("a".into(), "bot-a".into()), ("b".into(), "bot-a".into())])
        .await;
    assert!(
        matches!(outcome, Err(ServiceError::InternalError(_))),
        "roles_for under a DB failure must stay Err, got {:?}",
        outcome.map(|roles| roles.len())
    );
    assert!(matches!(
        h.repo.role("a", "bot-a").await,
        Err(ServiceError::InternalError(_))
    ));
    // ownership() reads bcs_bots first; with edge_grants gone the owner
    // read still fails closed instead of reporting a corrupt/empty state.
    assert!(matches!(
        h.repo.ownership("bot-a").await,
        Err(ServiceError::InternalError(_))
    ));
}

/// Role rows are readable through the strict codec: direct/team manager
/// edges decode, owner takes priority, and revoked rows stop reading
/// without any error.
#[tokio::test]
async fn sqlite_authority_manager_rows_read_strictly() {
    let (h, db) = sqlite_harness().await;
    h.seed_owned("bot-m", "m").await.unwrap();

    let insert_manager = |from: &str, kind: &str, id: &str| {
        DbStatement::with_params(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
             originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
             VALUES (?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from', NULL, ?, ?)",
            vec![
                DbValue::from(ENV),
                DbValue::from(human_actor_id(from)),
                DbValue::from("bot-m"),
                DbValue::from(kind),
                DbValue::from(id),
            ],
        )
    };

    db.execute(insert_manager("m2", "direct", "manual"))
        .await
        .expect("direct manager edge");
    assert_eq!(
        h.repo.role("m2", "bot-m").await.unwrap(),
        Some(BotAccessRelation::Manager)
    );

    // The same user may hold several manager sources; they stay one Manager.
    db.execute(insert_manager("m2", "team", "team-a"))
        .await
        .expect("team manager edge");
    assert_eq!(
        h.repo.role("m2", "bot-m").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "any approved manager source grants the manager relation"
    );

    // Owner + manager edges on one subject: owner wins.
    db.execute(insert_manager("m", "direct", "manual"))
        .await
        .expect("manager edge for the owner");
    assert_eq!(
        h.repo.role("m", "bot-m").await.unwrap(),
        Some(BotAccessRelation::Owner),
        "owner takes priority over manager sources"
    );

    // Revoked manager rows are retained but stop reading.
    db.execute(DbStatement::with_params(
        "UPDATE edge_grants SET status = 'revoked' \
         WHERE env = ? AND to_id = ? AND grant_kind = 'manager'",
        vec![DbValue::from(ENV), DbValue::from("bot-m")],
    ))
    .await
    .expect("revoke manager edges");
    assert_eq!(h.repo.role("m2", "bot-m").await.unwrap(), None);
    assert_eq!(
        h.repo.role("m", "bot-m").await.unwrap(),
        Some(BotAccessRelation::Owner)
    );
}