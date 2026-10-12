//! Shared internals of the `manager_mutation` test binary (plan Task 4):
//! the per-driver seeding levers, the deterministic audit/drift plugin
//! wrapper, and the shared Memory/SQLite conformance suite. Kept in a
//! subdirectory module (`mod.rs`) so cargo does not auto-discover it as a
//! second integration-test binary; `manager_mutation.rs` pulls it in with
//! `#[path]` and declares the individual #[tokio::test]s.

//! Plan Task 4: manual manager mutations, source scoping, real-operator audit.
//!
//! Shared RED/GREEN suite (brief step 1) runs identically against BOTH
//! production repo drivers:
//! - SQLite: [`DbBotAuthorityStore`] over the FULL migration chain
//!   (`run_sqlite_migrations`, the exact schema production startup builds);
//! - Memory: `bcs_bot_store::MemoryBotRepo` sharing the bot lifecycle state.
//!
//! Cases mandated by the brief:
//! - the RED snapshot verbatim (grant→changed, idempotent grant→not changed,
//!   revoke→changed + remaining_team_sources==["team-a"], role stays
//!   Manager), with B's team-a source seeded through FORMAL source semantics
//!   (raw Task 2 schema INSERT / driver lever) BEFORE the direct grant;
//! - team-only subject DELETE → changed=false (team sources stay);
//! - last-source self-revocation → next operation Forbidden;
//! - owner target → AuthorityError::Conflict;
//! - revoked row restores under the SAME edge id (no INSERT IGNORE);
//! - audit records only actual changes;
//! - manager mutual revocation / duplicate grant lock order (real SQLite);
//! - audit/commit injected failure rolls back EVERYTHING (incl. the business
//!   edge change);
//! - large source sets: batched SQL, bounded statements, never scans all bots.

#[path = "../../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{BotAccessRelation, ManagementSource};
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::{
    AuditActor, BotManagerSummary, ManagerMutation,
};
use bcs_service_api::ServiceError;
use tempfile::TempDir;

pub const ENV: &str = "local";

pub fn human(user_id: &str) -> AuditActor {
    AuditActor::Human {
        user_id: user_id.to_string(),
    }
}

pub fn grant_direct(user_id: &str) -> ManagerMutation {
    ManagerMutation::GrantDirect {
        user_id: user_id.to_string(),
    }
}

pub fn revoke_non_team(user_id: &str) -> ManagerMutation {
    ManagerMutation::RevokeNonTeam {
        user_id: user_id.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Harness (per-driver seeding + levers; production work goes through `repo`)
// ---------------------------------------------------------------------------

#[async_trait]
pub trait ManagerMutationDriver: Send + Sync {
    /// One legal Bot (live, version 1) + its approved owner edge, atomically
    /// (Task 2 schema). Test-only stand-in for the Task 5 initialization.
    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str);
    /// Real Human materialization (`human_<user_id>` actor row).
    async fn seed_human(&self, user_id: &str);
    /// Formal-source manager seeding through the Task 2 schema / authority
    /// row shape — NEVER a friend edge.
    async fn seed_manager_source(&self, bot_id: &str, user_id: &str, kind: &str, id: &str);
    async fn seed_uninitialized_bot(&self, bot_id: &str);
    async fn break_owner_edge(&self, bot_id: &str);
    /// Arm a one-shot failure of the next manager-mutation WRITE (audit).
    fn arm_mutation_write_failure(&self);
    async fn audit_count(&self) -> u64;
    /// Edge ids of the subject's direct/manual manager rows (any status).
    async fn direct_manager_edge_ids(&self, bot_id: &str, user_id: &str) -> Vec<i64>;
}

pub struct Harness {
    pub repo: Arc<dyn BotAuthorityRepoPort>,
    pub driver: Arc<dyn ManagerMutationDriver>,
}

impl Harness {
    pub(crate) async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) {
        self.driver.seed_owned(bot_id, owner_user_id).await;
    }

    pub(crate) async fn seed_human(&self, user_id: &str) {
        self.driver.seed_human(user_id).await;
    }

    pub(crate) async fn seed_manager_source(&self, bot_id: &str, user_id: &str, kind: &str, id: &str) {
        self.driver.seed_manager_source(bot_id, user_id, kind, id).await;
    }
}

// ---------------------------------------------------------------------------
// SQLite driver over the full migration chain
// ---------------------------------------------------------------------------

/// DbPlugin wrapper: an armed flag fails the next transaction that WRITES
/// `bot_manager_changes` (the audit step of a mutation) — seeding and
/// read/lock transactions pass through untouched. Also counts transactions
/// and standalone queries so the bulk case can pin the statement budget.
pub struct MutationDb {
    pub inner: Arc<dyn DbPlugin>,
    pub armed: AtomicBool,
    /// Deterministic TOCTOU drift: applied on the raw connection the
    /// moment the mutation's WRITE transaction (one whose steps change
    /// edge_grants) arrives — after the validated read, before the write
    /// transaction executes.
    pub drift: Mutex<Option<String>>,
    transactions: AtomicUsize,
    standalone_queries: AtomicUsize,
}

impl MutationDb {
    fn steps_write_manager_changes(steps: &[DbTransactionStep]) -> bool {
        let touches = |statement: &DbStatement| statement.sql().contains("bot_manager_changes");
        steps.iter().any(|step| match step {
            DbTransactionStep::Query(statement) => touches(statement),
            DbTransactionStep::Execute(statement) => touches(statement),
            DbTransactionStep::ExecuteChecked { statement, .. } => touches(statement),
        })
    }

    /// Whether a transaction's steps CHANGE edge_grants rows (the mutation
    /// write transaction; read/lock/audit-blocked validation does not).
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
impl DbPlugin for MutationDb {
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
        // The armed drift (if any) fires exactly when the mutation's write
        // transaction arrives — AFTER its validated read committed, and NOT
        // on the (earlier) read-only validation transaction. The mutex guard
        // is scoped to the take so it never lives across an await.
        if Self::steps_change_edge_grants(&steps) {
            let drift_sql = { self.drift.lock().unwrap().take() };
            if let Some(drift_sql) = drift_sql {
                self.inner
                    .execute(DbStatement::new(drift_sql))
                    .await
                    .expect("apply the armed drift statement");
            }
        }
        // Consume the armed flag ONLY for transactions that actually write
        // the audit table — validation reads and seeding pass untouched.
        if self.armed.load(Ordering::SeqCst) && Self::steps_write_manager_changes(&steps) {
            self.armed.store(false, Ordering::SeqCst);
            return Err(bcs_db_api::DbError::Backend(
                "test-injected manager audit write failure".into(),
            ));
        }
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

pub struct SqliteDriver {
    db: Arc<MutationDb>,
    env: String,
}

#[async_trait]
impl ManagerMutationDriver for SqliteDriver {
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

    async fn seed_human(&self, user_id: &str) {
        let actor_id = format!("human_{user_id}");
        let existing = self
            .db
            .inner
            .query(DbStatement::with_params(
                "SELECT 1 AS one FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
                vec![
                    DbValue::from(actor_id.clone()),
                    DbValue::from(self.env.clone()),
                ],
            ))
            .await
            .expect("seed_human lookup");
        if !existing.is_empty() {
            return;
        }
        self.db
            .inner
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) VALUES (?, ?, ?, 'human', 'online')",
                vec![
                    DbValue::from(actor_id),
                    DbValue::from(user_id),
                    DbValue::from(self.env.clone()),
                ],
            ))
            .await
            .expect("seed_human");
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

    async fn break_owner_edge(&self, bot_id: &str) {
        self.db
            .inner
            .execute(DbStatement::with_params(
                "UPDATE edge_grants SET status = 'revoked' WHERE env = ? AND to_id = ? AND grant_kind = 'owner'",
                vec![DbValue::from(self.env.clone()), DbValue::from(bot_id)],
            ))
            .await
            .expect("break_owner_edge");
    }

    fn arm_mutation_write_failure(&self) {
        self.db.armed.store(true, Ordering::SeqCst);
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

    async fn direct_manager_edge_ids(&self, bot_id: &str, user_id: &str) -> Vec<i64> {
        let rows = self
            .db
            .inner
            .query(DbStatement::with_params(
                "SELECT id FROM edge_grants \
                 WHERE env = ? AND to_id = ? AND from_id = ? AND grant_kind = 'manager' \
                   AND management_source_kind = 'direct' AND management_source_id = 'manual' \
                 ORDER BY id",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(bot_id),
                    DbValue::from(format!("human_{user_id}")),
                ],
            ))
            .await
            .expect("direct_manager_edge_ids");
        rows.iter()
            .map(|row| row.get_i64("id").ok().flatten().expect("id column"))
            .collect()
    }
}

pub async fn sqlite_harness() -> (Harness, Arc<MutationDb>) {
    let raw = LocalSqliteDbPlugin::new().expect("open local sqlite");
    migrations::run_sqlite_migrations(&raw)
        .await
        .expect("apply the full sqlite migration chain");
    let db = Arc::new(MutationDb {
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

pub struct MemoryDriver {
    repo: Arc<MemoryBotRepo>,
    _dir: TempDir,
}

#[async_trait]
impl ManagerMutationDriver for MemoryDriver {
    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) {
        self.repo
            .seed_authority_owned(bot_id, owner_user_id)
            .await
            .expect("seed_owned");
    }

    async fn seed_human(&self, user_id: &str) {
        self.repo
            .ensure_human_actor(user_id, user_id)
            .await
            .expect("seed_human");
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

    async fn break_owner_edge(&self, bot_id: &str) {
        self.repo
            .break_authority_owner_edge(bot_id)
            .await
            .expect("break_owner_edge");
    }

    fn arm_mutation_write_failure(&self) {
        self.repo.arm_authority_write_failure();
    }

    async fn audit_count(&self) -> u64 {
        self.repo
            .authority_audit_count()
            .await
            .expect("audit_count")
    }

    async fn direct_manager_edge_ids(&self, bot_id: &str, user_id: &str) -> Vec<i64> {
        self.repo
            .authority_direct_manager_edge_ids(bot_id, user_id)
            .await
            .expect("direct_manager_edge_ids")
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
// Shared suites: identical for every driver
// ---------------------------------------------------------------------------

/// Brief RED snapshot (step 1), verbatim semantics. B's team-a source is
/// established through FORMAL source semantics BEFORE the direct grant.
async fn red_snapshot_grant_idempotent_revoke_keeps_team(h: &Harness) {
    let repo = h.repo.clone();
    let actor = human("a");
    let grant = grant_direct("b");
    assert!(repo.mutate_manager(actor.clone(), "bot-a", grant.clone()).await.unwrap().changed);
    assert!(!repo.mutate_manager(actor.clone(), "bot-a", grant).await.unwrap().changed);
    let removed = repo
        .mutate_manager(actor, "bot-a", revoke_non_team("b"))
        .await
        .unwrap();
    assert!(removed.changed);
    assert_eq!(removed.remaining_team_sources, vec!["team-a"]);
    assert_eq!(
        repo.role("b", "bot-a").await.unwrap(),
        Some(BotAccessRelation::Manager)
    );
    // Repeating the revoke changes nothing more (audit stays consistent).
    let repeat = repo.mutate_manager(human("a"), "bot-a", revoke_non_team("b")).await.unwrap();
    assert!(!repeat.changed);
    assert_eq!(repeat.remaining_team_sources, vec!["team-a"]);
}

async fn owner_and_validation_errors(h: &Harness) {
    let repo = h.repo.clone();
    // Owner target: Conflict (owner changes only through the transfer flow).
    for mutation in [grant_direct("a"), revoke_non_team("a")] {
        match repo.mutate_manager(human("a"), "bot-a", mutation).await {
            Err(ServiceError::Authority(AuthorityError::Conflict(_))) => {}
            other => panic!("owner target must Conflict, got {:?}", other.map(|r| r.changed)),
        }
    }
    // Unauthorized actor: Forbidden even when the mutation would be a
    // no-change idempotent repeat (spec §6: no bypassing auth via idempotency).
    for mutation in [grant_direct("b"), revoke_non_team("b")] {
        match repo.mutate_manager(human("stranger"), "bot-a", mutation).await {
            Err(ServiceError::Authority(AuthorityError::Forbidden(_))) => {}
            other => panic!("unauthorized actor must be Forbidden, got {:?}", other.map(|r| r.changed)),
        }
    }
    // Non-Human actors fail closed BEFORE any from_id matching: a crafted
    // service id shaped like `human_<uid>` must never alias the owner's
    // role-edge subject (structural, both drivers).
    for mutation in [grant_direct("b"), revoke_non_team("b")] {
        match repo
            .mutate_manager(
                AuditActor::Service {
                    service_id: "human_a".into(),
                },
                "bot-a",
                mutation,
            )
            .await
        {
            Err(ServiceError::Authority(AuthorityError::Forbidden(_))) => {}
            other => panic!("Service actor must be Forbidden, got {:?}", other.map(|r| r.changed)),
        }
    }
    // Unresolvable target: InvalidSubject (application later maps to 404
    // after the actor was already authorized).
    for mutation in [grant_direct("ghost-user"), revoke_non_team("ghost-user")] {
        match repo.mutate_manager(human("a"), "bot-a", mutation).await {
            Err(ServiceError::Authority(AuthorityError::InvalidSubject(_))) => {}
            other => panic!("unknown target human must be InvalidSubject, got {:?}", other.map(|r| r.changed)),
        }
    }
    // Unknown bot / uninitialized / corrupt: fail closed before any write.
    assert!(matches!(
        repo.mutate_manager(human("a"), "ghost-bot", grant_direct("b")).await,
        Err(ServiceError::BotNotFound(_))
    ));
    h.driver.seed_uninitialized_bot("bot-zero").await;
    assert!(matches!(
        repo.mutate_manager(human("a"), "bot-zero", grant_direct("b")).await,
        Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. }))
    ));
    assert!(matches!(
        repo.list_managers("bot-zero", 0, 20).await,
        Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized { .. }))
    ));
    h.driver.seed_owned("bot-broken", "brk").await;
    h.driver.seed_human("brk2").await;
    h.driver.break_owner_edge("bot-broken").await;
    assert!(matches!(
        repo.mutate_manager(human("brk"), "bot-broken", grant_direct("brk2")).await,
        Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
    ));
}

async fn team_only_revoke_is_no_change(h: &Harness) {
    let repo = h.repo.clone();
    let audit_before = h.driver.audit_count().await;
    let removed = repo
        .mutate_manager(human("a"), "bot-a", revoke_non_team("teammate"))
        .await
        .unwrap();
    assert!(!removed.changed, "team-only subject DELETE stays a no-change");
    assert_eq!(removed.remaining_team_sources, vec!["team-a"]);
    assert_eq!(
        repo.role("teammate", "bot-a").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "team sources survive the direct-API revoke"
    );
    assert_eq!(
        h.driver.audit_count().await,
        audit_before,
        "no state change → no audit row"
    );
}

async fn last_source_self_revocation_then_forbidden(h: &Harness) {
    let repo = h.repo.clone();
    let removed = repo
        .mutate_manager(human("selfrev"), "bot-a", revoke_non_team("selfrev"))
        .await
        .unwrap();
    assert!(removed.changed);
    assert!(removed.remaining_team_sources.is_empty());
    assert_eq!(repo.role("selfrev", "bot-a").await.unwrap(), None);
    match repo.mutate_manager(human("selfrev"), "bot-a", grant_direct("b")).await {
        Err(ServiceError::Authority(AuthorityError::Forbidden(_))) => {}
        other => panic!("exhausted manager must be Forbidden on the next op, got {:?}", other.map(|r| r.changed)),
    }
}

async fn revoked_row_restores_same_edge_id(h: &Harness) {
    let repo = h.repo.clone();
    let granted = repo
        .mutate_manager(human("a"), "bot-a", grant_direct("restoree"))
        .await
        .unwrap();
    assert!(granted.changed);
    let granted_ids = h.driver.direct_manager_edge_ids("bot-a", "restoree").await;
    assert_eq!(granted_ids.len(), 1, "fresh grant creates exactly one direct row");
    // Revoke then re-grant: the revoked row is RESTORED under the same id,
    // never INSERT IGNORE-duplicated.
    assert!(repo
        .mutate_manager(human("a"), "bot-a", revoke_non_team("restoree"))
        .await
        .unwrap()
        .changed);
    let revoked_ids = h.driver.direct_manager_edge_ids("bot-a", "restoree").await;
    assert_eq!(revoked_ids, granted_ids, "the revoked row is retained, same id");
    assert!(repo
        .mutate_manager(human("a"), "bot-a", grant_direct("restoree"))
        .await
        .unwrap()
        .changed);
    let restored = h.driver.direct_manager_edge_ids("bot-a", "restoree").await;
    assert_eq!(restored, granted_ids, "revoked rows restore under the SAME source row id");
    assert_eq!(restored.len(), 1, "never two direct/manual slots for one subject");
}

async fn audit_records_only_actual_changes(h: &Harness) {
    let repo = h.repo.clone();
    let audit_before = h.driver.audit_count().await;

    // First grant changes → exactly one audit row.
    assert!(repo.mutate_manager(human("a"), "bot-a", grant_direct("auditee")).await.unwrap().changed);
    assert_eq!(h.driver.audit_count().await, audit_before + 1);

    // Repeated grant: no change → no new audit row.
    assert!(!repo.mutate_manager(human("a"), "bot-a", grant_direct("auditee")).await.unwrap().changed);
    assert_eq!(h.driver.audit_count().await, audit_before + 1);

    // Revoke of a subject holding TWO non-team sources (direct + an
    // ownership_transfer edge): one atomic revoke, TWO audit rows.
    let removed = repo
        .mutate_manager(human("a"), "bot-a", revoke_non_team("auditee"))
        .await
        .unwrap();
    assert!(removed.changed);
    assert_eq!(h.driver.audit_count().await, audit_before + 3);

    // Repeated revoke (team sources remain): no change → no audit row.
    let repeat = repo
        .mutate_manager(human("a"), "bot-a", revoke_non_team("auditee"))
        .await
        .unwrap();
    assert!(!repeat.changed);
    assert_eq!(h.driver.audit_count().await, audit_before + 3);
}

async fn list_managers_paging_and_owner_field(h: &Harness) {
    let repo = h.repo.clone();
    // Dedicated bot: controlled manager book for the page assertions.
    h.seed_owned("bot-list", "lo").await;
    h.seed_manager_source("bot-list", "lo", "direct", "manual").await;
    h.seed_manager_source("bot-list", "m2", "ownership_transfer", "ot-list").await;
    h.seed_manager_source("bot-list", "m1", "team", "tv-b").await;
    h.seed_manager_source("bot-list", "m1", "team", "tv-a").await;

    let list = repo.list_managers("bot-list", 0, 20).await.unwrap();
    assert_eq!(list.owner_user_id, "lo");
    assert!(
        list.managers.iter().all(|m| m.user_id != "lo"),
        "owner never mixes into the manager page, even with a stray manager edge"
    );
    assert_eq!(
        list.managers,
        vec![
            BotManagerSummary {
                user_id: "m1".into(),
                sources: vec![
                    ManagementSource::Team("tv-a".into()),
                    ManagementSource::Team("tv-b".into()),
                ],
            },
            BotManagerSummary {
                user_id: "m2".into(),
                sources: vec![ManagementSource::OwnershipTransfer("ot-list".into())],
            },
        ],
        "manager page is deduped per user, sorted user_id ASC, sources decoded and canonically ordered"
    );

    let page = repo.list_managers("bot-list", 0, 1).await.unwrap();
    assert_eq!(page.managers.len(), 1);
    assert_eq!(page.managers[0].user_id, "m1");
    let page = repo.list_managers("bot-list", 1, 1).await.unwrap();
    assert_eq!(page.managers.len(), 1);
    assert_eq!(page.managers[0].user_id, "m2");
    let beyond = repo.list_managers("bot-list", 2, 1).await.unwrap();
    assert!(beyond.managers.is_empty(), "beyond-the-end page is empty, not an error");
    let zero = repo.list_managers("bot-list", 0, 0).await.unwrap();
    assert!(zero.managers.is_empty(), "limit 0 → empty page");

    assert!(matches!(
        repo.list_managers("ghost-bot", 0, 20).await,
        Err(ServiceError::BotNotFound(_))
    ));
}

async fn audit_write_failure_rolls_back_everything(h: &Harness) {
    let repo = h.repo.clone();
    let audit_before = h.driver.audit_count().await;
    h.driver.arm_mutation_write_failure();
    let outcome = repo
        .mutate_manager(human("a"), "bot-a", grant_direct("rollbackee"))
        .await;
    assert!(outcome.is_err(), "the injected audit/commit failure must surface");
    assert_eq!(
        repo.role("rollbackee", "bot-a").await.unwrap(),
        None,
        "the same-transaction business edge change must roll back with the audit"
    );
    assert_eq!(
        h.driver.audit_count().await,
        audit_before,
        "no audit residue may survive the failed mutation"
    );
    // Revoke path too: the business revoke rolls back with the audit write.
    h.driver.arm_mutation_write_failure();
    let outcome = repo
        .mutate_manager(human("a"), "bot-a", revoke_non_team("many-sources"))
        .await;
    assert!(outcome.is_err());
    assert_eq!(
        repo.role("many-sources", "bot-a").await.unwrap(),
        Some(BotAccessRelation::Manager),
        "the revoked business edges must be intact after the audit failure"
    );
    assert_eq!(h.driver.audit_count().await, audit_before);
}

/// The full shared suite; run for every driver.
pub async fn manager_mutation_contract_tests(h: &Harness) {
    // Fixture (shared): owner a; formal-source managers, all seeded BEFORE
    // the mutations under test.
    h.seed_owned("bot-a", "a").await;
    for user in ["b", "selfrev", "restoree", "auditee", "rollbackee", "teammate", "many-sources"] {
        h.seed_human(user).await;
    }
    h.seed_manager_source("bot-a", "b", "team", "team-a").await;
    h.seed_manager_source("bot-a", "teammate", "team", "team-a").await;
    h.seed_manager_source("bot-a", "auditee", "team", "team-a").await;
    h.seed_manager_source("bot-a", "auditee", "ownership_transfer", "ot-1").await;
    h.seed_manager_source("bot-a", "many-sources", "direct", "manual").await;
    h.seed_manager_source("bot-a", "many-sources", "ownership_transfer", "ot-1").await;
    // selfrev's live direct edge before it self-revokes.
    h.seed_manager_source("bot-a", "selfrev", "direct", "manual").await;
    assert_eq!(h.driver.audit_count().await, 0, "levers never write audit rows");

    red_snapshot_grant_idempotent_revoke_keeps_team(h).await;
    owner_and_validation_errors(h).await;
    team_only_revoke_is_no_change(h).await;
    last_source_self_revocation_then_forbidden(h).await;
    revoked_row_restores_same_edge_id(h).await;
    audit_records_only_actual_changes(h).await;
    list_managers_paging_and_owner_field(h).await;
    audit_write_failure_rolls_back_everything(h).await;
}
