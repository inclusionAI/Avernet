//! Deterministic SQL-read races and dependency failure propagation.
use async_trait::async_trait;
use bcs_db_api::*;
use bcs_edge_permission::agent_invoke::{AgentInvokeApplicationService, AgentInvokePolicy};
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
use bcs_service_api::application::agent_invoke::AgentInvocationService;
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;
use bcs_service_api::types::agent_invoke::*;
use std::num::NonZeroU32;
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};
#[path = "support/agent_invoke_snapshots.rs"]
mod fixture;
use fixture::*;

struct GateDb {
    inner: Arc<dyn DbPlugin>,
    match_sql: &'static str,
    armed: AtomicBool,
    entered: tokio::sync::Notify,
    resume: tokio::sync::Notify,
    fail: AtomicBool,
    query_count: AtomicUsize,
    transaction_steps: AtomicUsize,
    transaction_rows: AtomicUsize,
}
impl GateDb {
    fn new(inner: Arc<dyn DbPlugin>, match_sql: &'static str, armed: bool) -> Self {
        Self {
            inner,
            match_sql,
            armed: AtomicBool::new(armed),
            entered: tokio::sync::Notify::new(),
            resume: tokio::sync::Notify::new(),
            fail: AtomicBool::new(false),
            query_count: AtomicUsize::new(0),
            transaction_steps: AtomicUsize::new(0),
            transaction_rows: AtomicUsize::new(0),
        }
    }
}
#[async_trait]
impl DbPlugin for GateDb {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> {
        self.query_count.fetch_add(1, Ordering::SeqCst);
        if s.sql().contains(self.match_sql) && self.armed.swap(false, Ordering::SeqCst) {
            self.entered.notify_one();
            self.resume.notified().await;
        }
        if self.fail.load(Ordering::SeqCst) {
            return Err(DbError::Backend(
                "injected private provider/SQL details".into(),
            ));
        }
        self.inner.query(s).await
    }
    async fn execute(&self, s: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(s).await
    }
    async fn transaction(
        &self,
        s: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(s).await
    }
    async fn transaction_with_options(
        &self,
        s: Vec<DbTransactionStep>,
        o: DbTransactionOptions,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.transaction_steps.fetch_add(s.len(), Ordering::SeqCst);
        if s.len() == 6 {
            let lock_id = |step: &DbTransactionStep| match step {
                DbTransactionStep::Query(statement) => {
                    assert!(!statement.sql().contains(" IN ("));
                    match &statement.params()[2] {
                        DbValue::String(id) => id.clone(),
                        _ => panic!("ordered Bot identity parameter"),
                    }
                }
                _ => panic!("read-only lock step"),
            };
            assert!(
                lock_id(&s[1]) < lock_id(&s[2]),
                "physical point-lock acquisition follows Rust byte order"
            );
        }

        if self.fail.load(Ordering::SeqCst) {
            return Err(DbError::Backend(
                "injected snapshot dependency details".into(),
            ));
        }
        let result = self.inner.transaction_with_options(s, o).await?;
        self.transaction_rows.fetch_add(
            result
                .iter()
                .map(|r| match r {
                    DbTransactionStepResult::Rows(rows) => rows.len(),
                    _ => 0,
                })
                .sum::<usize>(),
            Ordering::SeqCst,
        );
        Ok(result)
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}
fn observed_store(db: Arc<dyn DbPlugin>) -> Arc<DbAgentInvokeStore> {
    let n = NonZeroU32::new(2).unwrap();
    Arc::new(
        DbAgentInvokeStore::new(
            db,
            DbSqlFlavor::Sqlite,
            scope(),
            AgentInvokeLimits {
                max_out_degree: n,
                max_in_degree: n,
                max_pending_out: n,
                max_pending_in: n,
            },
            nz(60_000),
        )
        .unwrap(),
    )
}

#[tokio::test]
async fn qualification_committed_before_single_read_is_observed_without_cache() {
    let f = Fixture::new().await;
    f.grant().await;
    let db = Arc::new(GateDb::new(
        f.db.clone(),
        "FROM agent_invoke_scopes s",
        true,
    ));
    let app = Arc::new(
        AgentInvokeApplicationService::new(
            scope(),
            Arc::new(AgentInvokePolicy),
            observed_store(db.clone()),
            Arc::new(NoCommands),
        )
        .unwrap(),
    );
    let task = tokio::spawn({
        let app = app.clone();
        async move { app.authorize_invocation(input()).await }
    });
    db.entered.notified().await;
    let old = f.store.read_fact(&scope(), "bot-a").await.unwrap().unwrap();
    let mut state = old.state;
    state.eligible = false;
    state.eligibility_generation = nz(2);
    f.store
        .publish_fact(
            &scope(),
            AgentInvokeFactPublication {
                state,
                expected_revision: old.revision.get(),
                valid_until_ms: f.store.database_time_ms().await.unwrap() + 30_000,
            },
        )
        .await
        .unwrap();
    db.resume.notify_one();
    assert_eq!(task.await.unwrap(), Err(AgentInvokeError::Forbidden));
    assert_eq!(db.query_count.load(Ordering::SeqCst), 1);
    assert_eq!(
        app.authorize_invocation(input()).await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(
        db.query_count.load(Ordering::SeqCst),
        2,
        "every operation reads current persisted state"
    );
}

#[tokio::test]
async fn governance_publication_between_locked_read_and_delegation_cannot_mix_snapshots() {
    let f = Fixture::new().await;
    let db = Arc::new(GateDb::new(
        f.db.clone(),
        "FROM agent_invoke_governance",
        true,
    ));
    let store = observed_store(db.clone());
    let mut c = command(
        "human_target",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    c.principal = AgentInvokePrincipal::Governance("governor".into());
    let task = tokio::spawn(async move { store.management_snapshot(&scope(), &c).await });
    db.entered.notified().await;
    // Scope fencing is the trusted publisher contract, not an HTTP write API.
    use sha2::{Digest, Sha256};
    let hash: String = Sha256::digest(
        serde_json::json!(["governance", "governor"])
            .to_string()
            .as_bytes(),
    )
    .iter()
    .map(|b| format!("{b:02x}"))
    .collect();
    f.db.transaction(vec![DbTransactionStep::Execute(DbStatement::new("UPDATE agent_invoke_scopes SET revision=revision+1")),
        DbTransactionStep::Execute(DbStatement::with_params("INSERT INTO agent_invoke_governance (scope_id,principal_hash,principal_id,from_id,to_id,revision,actions_json,updated_at_ms) SELECT id,?,'governor','bot-a','bot-b',1,'[\"grant\"]',1 FROM agent_invoke_scopes",vec![hash.into()]))]).await.unwrap();
    db.resume.notify_one();
    assert_eq!(task.await.unwrap(), Err(AgentInvokeError::Conflict));
}

#[tokio::test]
async fn real_statement_counts_are_constant_and_read_failures_are_unavailable() {
    let f = Fixture::new().await;
    f.grant().await;
    let db = Arc::new(GateDb::new(f.db, "no-gate", false));
    let store = observed_store(db.clone());
    store.runtime_snapshot(&scope(), &pair()).await.unwrap();
    assert_eq!(db.query_count.load(Ordering::SeqCst), 1);
    let c = command(
        "human_target",
        AgentInvokeChange::Grant {
            expected_version: 1,
        },
    );
    store.management_snapshot(&scope(), &c).await.unwrap();
    assert_eq!(db.transaction_steps.load(Ordering::SeqCst), 6);
    assert_eq!(
        db.transaction_rows.load(Ordering::SeqCst),
        5,
        "scope + 2 Bot locks + pair + authority; absent request returns no row"
    );
    assert_eq!(
        db.query_count.load(Ordering::SeqCst),
        1,
        "ordinary Human management adds no standalone read"
    );
    let mut reversed = c.clone();
    reversed.principal = AgentInvokePrincipal::Human("human_target".into());
    reversed.pair = AgentInvokePair {
        from_bot_id: "bot-b".into(),
        to_bot_id: "bot-a".into(),
    };
    reversed.change = AgentInvokeChange::Request;
    assert_eq!(
        store
            .management_snapshot(&scope(), &reversed)
            .await
            .unwrap()
            .source_role,
        Some(AgentInvokeRole::Owner)
    );
    db.fail.store(true, Ordering::SeqCst);
    assert_eq!(
        store.runtime_snapshot(&scope(), &pair()).await,
        Err(AgentInvokeError::Unavailable)
    );
    assert_eq!(
        store.management_snapshot(&scope(), &c).await,
        Err(AgentInvokeError::Unavailable)
    );
}
