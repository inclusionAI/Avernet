//! Real SQLite migration, CAS, rollback, durable reopen and bounded-query proof.
//! This does not exercise SQL grant lifecycle, ingress identity or dispatch.
use std::num::{NonZeroU32, NonZeroU64};
use std::sync::{Arc, Mutex, atomic::{AtomicBool, AtomicUsize, Ordering}};

use async_trait::async_trait;
use bcs_db_api::*;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;
use bcs_service_api::types::agent_invoke::*;

fn nz(v: u64) -> NonZeroU64 { NonZeroU64::new(v).unwrap() }
fn scope() -> AgentInvokeScope { AgentInvokeScope { namespace: "test-namespace".into(), env: "dev".into() } }
fn limits() -> AgentInvokeLimits {
    AgentInvokeLimits { max_out_degree: NonZeroU32::new(2).unwrap(), max_in_degree: NonZeroU32::new(2).unwrap(),
        max_pending_out: NonZeroU32::new(2).unwrap(), max_pending_in: NonZeroU32::new(2).unwrap() }
}
fn store(db: Arc<dyn DbPlugin>) -> DbAgentInvokeStore {
    DbAgentInvokeStore::new(db, DbSqlFlavor::Sqlite, scope(), limits(), nz(60_000)).unwrap()
}
async fn fixture() -> (Arc<LocalSqliteDbPlugin>, DbAgentInvokeStore) {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let s = store(db.clone()); s.initialize_scope().await.unwrap(); (db, s)
}
async fn publication(s: &DbAgentInvokeStore, bot: &str, expected: u64) -> AgentInvokeFactPublication {
    AgentInvokeFactPublication { state: AgentInvokeBotState { bot_id: bot.into(), binding_generation: nz(1), eligibility_generation: nz(1),
        eligible: true, active: true, hidden: false }, expected_revision: expected, valid_until_ms: s.database_time_ms().await.unwrap() + 30_000 }
}
async fn count(db: &dyn DbPlugin, table: &str) -> i64 {
    let rows = db.query(DbStatement::new(format!("SELECT COUNT(*) AS n FROM {table}"))).await.unwrap();
    db_get_column(&rows[0], "n").unwrap()
}

#[tokio::test]
async fn installation_closed_reopen_and_exclusive_scope() {
    let (db, s) = fixture().await;
    let initial = s.scope_state().await.unwrap();
    assert_eq!(initial.revision, nz(1)); assert!(!initial.writer_enabled && !initial.runtime_enabled);
    assert_eq!(s.initialize_scope().await.unwrap(), initial);
    assert_eq!(s.read_fact(&scope(), "bot").await.unwrap(), None);
    assert_eq!(count(db.as_ref(), "agent_invoke_facts").await, 0);
    let mut other = scope(); other.namespace = "other".into();
    let other = DbAgentInvokeStore::new(db.clone(), DbSqlFlavor::Sqlite, other, limits(), nz(60_000)).unwrap();
    assert_eq!(other.initialize_scope().await, Err(AgentInvokeError::Conflict));
    let changed = DbAgentInvokeStore::new(db, DbSqlFlavor::Sqlite, scope(), limits(), nz(50_000)).unwrap();
    assert_eq!(changed.initialize_scope().await, Err(AgentInvokeError::Conflict));
}

#[tokio::test]
async fn explicit_activation_has_revision_fence_and_never_enables_writer_alone() {
    let (_, s) = fixture().await;
    assert_eq!(s.set_enabled(nz(1), true, false).await, Err(AgentInvokeError::InvalidInput));
    let enabled = s.set_enabled(nz(1), true, true).await.unwrap();
    assert!(enabled.writer_enabled && enabled.runtime_enabled); assert_eq!(enabled.revision, nz(2));
    assert_eq!(s.set_enabled(nz(1), false, false).await, Err(AgentInvokeError::Conflict));
    assert_eq!(s.initialize_scope().await.unwrap(), enabled, "reopening must not overwrite current gates");
    let disabled = s.set_enabled(nz(2), false, false).await.unwrap();
    assert!(!disabled.writer_enabled && !disabled.runtime_enabled);
}

#[tokio::test]
async fn exact_identity_and_scope_never_fold_or_trim() {
    let (db, s) = fixture().await;
    for bot in ["bot-a", "BOT-A", "bot-a "] {
        let p = publication(&s, bot, 0).await;
        let fact = s.publish_fact(&scope(), p).await.unwrap();
        assert_eq!(s.read_fact(&scope(), bot).await.unwrap(), Some(fact));
    }
    assert_eq!(count(db.as_ref(), "agent_invoke_facts").await, 3);
    let mut other = scope(); other.namespace.push('x');
    assert_eq!(s.read_fact(&other, "bot-a").await, Err(AgentInvokeError::NotFound));
    for bot in ["", "  ", "human_owner"] {
        assert_eq!(s.read_fact(&scope(), bot).await, Err(AgentInvokeError::InvalidInput));
    }
}

#[tokio::test]
async fn canonical_constructor_and_finite_lease_validation() {
    let (db, s) = fixture().await;
    for env in ["DEV", "dev ", " dev", "", "déⅴ"] {
        let mut bad = scope(); bad.env = env.into();
        assert!(matches!(DbAgentInvokeStore::new(db.clone(), DbSqlFlavor::Sqlite, bad, limits(), nz(60_000)), Err(AgentInvokeError::InvalidInput)));
    }
    for namespace in ["", " ", " padded", "padded "] {
        let mut bad = scope(); bad.namespace = namespace.into();
        assert!(matches!(DbAgentInvokeStore::new(db.clone(), DbSqlFlavor::Sqlite, bad, limits(), nz(60_000)), Err(AgentInvokeError::InvalidInput)));
    }
    for expires in [0, s.database_time_ms().await.unwrap() - 1, s.database_time_ms().await.unwrap() + 120_000, u64::MAX] {
        let mut p = publication(&s, "bot", 0).await; p.valid_until_ms = expires;
        assert_eq!(s.publish_fact(&scope(), p).await, Err(AgentInvokeError::InvalidInput));
    }
    let mut p = publication(&s, "bot", 0).await; p.state.binding_generation = nz(u64::MAX);
    assert_eq!(s.publish_fact(&scope(), p).await, Err(AgentInvokeError::InvalidInput));
    assert_eq!(count(db.as_ref(), "agent_invoke_audit").await, 0);
}

#[tokio::test]
async fn generation_and_revision_cas_preserve_loss_and_restoration() {
    let (db, s) = fixture().await;
    let p = publication(&s, "bot", 0).await;
    let original = s.publish_fact(&scope(), p.clone()).await.unwrap();
    assert_eq!(s.publish_fact(&scope(), p).await, Err(AgentInvokeError::Conflict));
    let mut p = publication(&s, "bot", 1).await; p.state.eligible = false;
    assert_eq!(s.publish_fact(&scope(), p.clone()).await, Err(AgentInvokeError::Conflict));
    p.state.eligibility_generation = nz(2);
    let lost = s.publish_fact(&scope(), p).await.unwrap(); assert!(!lost.state.eligible);
    let mut p = publication(&s, "bot", 2).await; p.state.eligibility_generation = nz(2);
    assert_eq!(s.publish_fact(&scope(), p.clone()).await, Err(AgentInvokeError::Conflict));
    p.state.eligibility_generation = nz(3); p.state.binding_generation = nz(2);
    let restored = s.publish_fact(&scope(), p).await.unwrap();
    assert!(restored.state.eligible); assert_eq!(restored.revision, nz(3)); assert_ne!(restored.state, original.state);
    for (binding, eligibility) in [(1, 3), (2, 2)] {
        let mut p = publication(&s, "bot", 3).await;
        p.state.binding_generation = nz(binding); p.state.eligibility_generation = nz(eligibility);
        assert_eq!(s.publish_fact(&scope(), p).await, Err(AgentInvokeError::Conflict));
    }
    assert_eq!(count(db.as_ref(), "agent_invoke_audit").await, 3);
    let rows = db.query(DbStatement::new("SELECT before_json, after_json FROM agent_invoke_audit ORDER BY id")).await.unwrap();
    assert_eq!(db_get_column::<String>(&rows[0], "before_json").unwrap(), "null");
    let after: serde_json::Value = serde_json::from_str(&db_get_column::<String>(&rows[2], "after_json").unwrap()).unwrap();
    assert_eq!(after["eligibility_generation"], 3);
}

#[tokio::test]
async fn heartbeat_and_expired_lease_require_distinct_generations() {
    let (db, s) = fixture().await;
    let p = publication(&s, "bot", 0).await; s.publish_fact(&scope(), p).await.unwrap();
    let p = publication(&s, "bot", 1).await; s.publish_fact(&scope(), p).await.unwrap();
    // Deterministic expired persisted fact, no timing sleeps.
    db.execute(DbStatement::new("UPDATE agent_invoke_facts SET valid_until_ms = 1")).await.unwrap();
    assert_eq!(s.read_fact(&scope(), "bot").await.unwrap().unwrap().valid_until_ms, 1, "publisher recovery can inspect expired facts, not authorize them");
    let mut p = publication(&s, "bot", 2).await;
    assert_eq!(s.publish_fact(&scope(), p.clone()).await, Err(AgentInvokeError::Conflict));
    p.state.eligibility_generation = nz(2);
    s.publish_fact(&scope(), p).await.unwrap();
}

#[tokio::test]
async fn audit_failure_rolls_back_scope_fact_and_update() {
    let (db, s) = fixture().await;
    let initial = s.scope_state().await.unwrap();
    db.execute(DbStatement::new("CREATE TRIGGER fail_fact_audit BEFORE INSERT ON agent_invoke_audit BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END")).await.unwrap();
    let p = publication(&s, "bot", 0).await;
    assert_eq!(s.publish_fact(&scope(), p.clone()).await, Err(AgentInvokeError::Unavailable));
    assert_eq!(s.scope_state().await.unwrap(), initial); assert_eq!(s.read_fact(&scope(), "bot").await.unwrap(), None);
    assert_eq!(count(db.as_ref(), "agent_invoke_audit").await, 0);
    db.execute(DbStatement::new("DROP TRIGGER fail_fact_audit")).await.unwrap();
    let original = s.publish_fact(&scope(), p).await.unwrap();
    db.execute(DbStatement::new("CREATE TRIGGER fail_fact_audit BEFORE INSERT ON agent_invoke_audit BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END")).await.unwrap();
    let initial = s.scope_state().await.unwrap();
    let p = publication(&s, "bot", 1).await;
    assert_eq!(s.publish_fact(&scope(), p).await, Err(AgentInvokeError::Unavailable));
    assert_eq!(s.scope_state().await.unwrap(), initial); assert_eq!(s.read_fact(&scope(), "bot").await.unwrap(), Some(original));
    assert_eq!(count(db.as_ref(), "agent_invoke_audit").await, 1);
}

#[tokio::test]
async fn file_backed_restart_preserves_fact_generation_and_gates() {
    let directory = tempfile::tempdir().unwrap(); let path = directory.path().join("invoke.sqlite");
    let original;
    {
        let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
        let s = store(db); s.initialize_scope().await.unwrap();
        let p = publication(&s, "bot", 0).await; original = s.publish_fact(&scope(), p).await.unwrap();
        s.set_enabled(nz(2), true, true).await.unwrap();
    }
    let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let s = store(db); let gates = s.initialize_scope().await.unwrap();
    assert_eq!(gates.revision, nz(3)); assert!(gates.writer_enabled && gates.runtime_enabled);
    assert_eq!(s.read_fact(&scope(), "bot").await.unwrap(), Some(original));
    let p = publication(&s, "bot", 0).await;
    assert_eq!(s.publish_fact(&scope(), p).await, Err(AgentInvokeError::Conflict));
}

// Records actual plugin calls; the barrier forces both current snapshots to
// be read before either transaction, so collision proof does not rely on sleep.
struct ObservedDb {
    inner: Arc<dyn DbPlugin>, reads: AtomicUsize, transactions: AtomicUsize,
    fail_scope_reads: AtomicBool, lose_commit_ack: AtomicBool,
    barrier: Mutex<Option<Arc<tokio::sync::Barrier>>>,
}
impl ObservedDb {
    fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self { inner, reads: AtomicUsize::new(0), transactions: AtomicUsize::new(0), fail_scope_reads: AtomicBool::new(false),
            lose_commit_ack: AtomicBool::new(false), barrier: Mutex::new(None) }
    }
}
#[async_trait]
impl DbPlugin for ObservedDb {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        if s.sql().contains("agent_invoke_scopes") && self.fail_scope_reads.load(Ordering::SeqCst) {
            return Err(DbError::Backend("injected dependency outage".into()));
        }
        let fact = s.sql().contains("FROM agent_invoke_facts")
            || s.sql().contains("JOIN agent_invoke_facts f");
        let rows = self.inner.query(s).await?;
        let barrier = self.barrier.lock().unwrap().clone();
        if let Some(barrier) = barrier { if fact { barrier.wait().await; } }
        Ok(rows)
    }
    async fn execute(&self, s: DbStatement) -> DbResult<DbExecuteResult> { self.inner.execute(s).await }
    async fn transaction(&self, s: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { self.inner.transaction(s).await }
    async fn transaction_with_options(&self, s: Vec<DbTransactionStep>, o: DbTransactionOptions) -> DbResult<Vec<DbTransactionStepResult>> {
        self.transactions.fetch_add(1, Ordering::SeqCst);
        assert!(s.len() <= 12, "bounded publication transaction");
        let result = self.inner.transaction_with_options(s, o).await?;
        if self.lose_commit_ack.load(Ordering::SeqCst) { return Err(DbError::Backend("lost acknowledgement".into())); }
        Ok(result)
    }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}

#[tokio::test]
async fn two_current_snapshots_same_revision_have_exactly_one_winner() {
    let (db, _) = fixture().await; let observed = Arc::new(ObservedDb::new(db.clone()));
    let s = Arc::new(store(observed.clone()));
    let p = publication(&s, "bot", 0).await;
    *observed.barrier.lock().unwrap() = Some(Arc::new(tokio::sync::Barrier::new(2)));
    let domain = scope();
    let (a, b) = tokio::join!(s.publish_fact(&domain, p.clone()), s.publish_fact(&domain, p));
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    assert!(a == Err(AgentInvokeError::Conflict) || b == Err(AgentInvokeError::Conflict));
    assert_eq!(count(db.as_ref(), "agent_invoke_facts").await, 1);
    assert_eq!(count(db.as_ref(), "agent_invoke_audit").await, 1);
    assert_eq!(s.scope_state().await.unwrap().revision, nz(2));
}

#[tokio::test]
async fn reads_are_constant_and_outage_is_not_namespace_conflict() {
    let (db, _) = fixture().await; let observed = Arc::new(ObservedDb::new(db)); let s = store(observed.clone());
    let p = publication(&s, "bot", 0).await; observed.reads.store(0, Ordering::SeqCst);
    s.publish_fact(&scope(), p).await.unwrap();
    assert_eq!(observed.reads.load(Ordering::SeqCst), 2); assert_eq!(observed.transactions.load(Ordering::SeqCst), 1);
    observed.reads.store(0, Ordering::SeqCst); s.read_fact(&scope(), "bot").await.unwrap();
    assert_eq!(observed.reads.load(Ordering::SeqCst), 2);
    observed.fail_scope_reads.store(true, Ordering::SeqCst);
    assert_eq!(s.initialize_scope().await, Err(AgentInvokeError::Unavailable));
    assert_eq!(s.read_fact(&scope(), "bot").await, Err(AgentInvokeError::Unavailable));
}

#[tokio::test]
async fn committed_but_lost_ack_is_not_retried_and_recovery_requires_cas() {
    let (db, _) = fixture().await; let observed = Arc::new(ObservedDb::new(db.clone())); let s = store(observed.clone());
    let p = publication(&s, "bot", 0).await; observed.lose_commit_ack.store(true, Ordering::SeqCst);
    assert_eq!(s.publish_fact(&scope(), p.clone()).await, Err(AgentInvokeError::CommitUnknown));
    assert_eq!(observed.transactions.load(Ordering::SeqCst), 1);
    assert_eq!(count(db.as_ref(), "agent_invoke_audit").await, 1);
    let current = s.read_fact(&scope(), "bot").await.unwrap().unwrap(); assert_eq!(current.revision, nz(1));
    assert_eq!(s.publish_fact(&scope(), p).await, Err(AgentInvokeError::Conflict));
    assert_eq!(observed.transactions.load(Ordering::SeqCst), 1, "no automatic retry or duplicate write");
}

#[tokio::test]
async fn corrupt_fact_is_invalid_not_absent() {
    let (db, s) = fixture().await;
    let p = publication(&s, "bot", 0).await; s.publish_fact(&scope(), p).await.unwrap();
    db.execute(DbStatement::new("PRAGMA ignore_check_constraints = ON")).await.unwrap();
    db.execute(DbStatement::new("UPDATE agent_invoke_facts SET active = 2")).await.unwrap();
    assert_eq!(s.read_fact(&scope(), "bot").await, Err(AgentInvokeError::InvalidState));
    let p = publication(&s, "bot", 1).await;
    assert_eq!(s.publish_fact(&scope(), p).await, Err(AgentInvokeError::InvalidState));
    assert_eq!(count(db.as_ref(), "agent_invoke_audit").await, 1);
}

#[path = "support/agent_invoke_audit_collision.rs"]
mod audit_collision;
#[tokio::test]
async fn portable_dml_only_audit_failure_rolls_back() {
    let (db, store) = fixture().await;
    let p = publication(&store, "bot", 0).await;
    store.publish_fact(&scope(), p).await.unwrap();
    audit_collision::audit_collision_rolls_back(db, &store, &scope(), "bot").await;
}
