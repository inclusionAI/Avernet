//! A real registry hidden/restore ABA between approve preflight and commit.
use async_trait::async_trait;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::*;
use bcs_service_api::application::agent_invoke::AgentInvokeGrantManagementService;
use bcs_service_api::types::agent_invoke::*;
use bcs_service_api::{ActorStatus, BotRepoPort};
use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};
use tokio::sync::Notify;
#[path = "support/agent_invoke_sql_driver.rs"]
mod driver;
struct CommitGate {
    inner: Arc<dyn DbPlugin>,
    armed: AtomicBool,
    registry_calls: AtomicUsize,
    registry_steps: AtomicUsize,
    entered: Notify,
    resume: Notify,
}
#[async_trait]
impl DbPlugin for CommitGate {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> {
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
        if matches!(s.first(),Some(DbTransactionStep::Execute(statement)) if statement.sql().starts_with("UPDATE bcs_bots SET agent_invoke_identity_revision=agent_invoke_identity_revision"))
        {
            assert_eq!(
                s.len(),
                3,
                "one registry mutation has a fixed point-only plan"
            );
            self.registry_calls.fetch_add(1, Ordering::SeqCst);
            self.registry_steps.fetch_add(s.len(), Ordering::SeqCst);
            for step in &s {
                let statement = match step {
                    DbTransactionStep::Query(s)
                    | DbTransactionStep::Execute(s)
                    | DbTransactionStep::ExecuteChecked { statement: s, .. } => s,
                };
                assert!(statement.params().len() <= 64);
            }
        }
        self.inner.transaction_with_options(s, o).await
    }
    async fn transaction_with_outcome(
        &self,
        s: Vec<DbTransactionStep>,
        o: DbTransactionOptions,
    ) -> DbTransactionResult<Vec<DbTransactionStepResult>> {
        let command=s.iter().any(|step|matches!(step,DbTransactionStep::ExecuteChecked {statement,..} if statement.sql().starts_with("INSERT INTO agent_invoke_receipts")));
        if command && self.armed.swap(false, Ordering::SeqCst) {
            self.entered.notify_one();
            self.resume.notified().await;
        }
        self.inner.transaction_with_outcome(s, o).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}
fn command(
    d: &driver::Driver,
    key: &str,
    actor: &str,
    change: AgentInvokeChange,
) -> AgentInvokeCommand {
    AgentInvokeCommand {
        principal: AgentInvokePrincipal::Human(actor.into()),
        authenticated_scope: d.scope.clone(),
        pair: AgentInvokePair {
            from_bot_id: "race-a".into(),
            to_bot_id: "race-b".into(),
        },
        change,
        idempotency_key: key.into(),
        reason: "real registry writer race".into(),
    }
}
#[tokio::test]
async fn approve_cannot_commit_after_registry_hidden_restore_acknowledgement() {
    tokio::time::timeout(std::time::Duration::from_secs(10), async {
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        let gate = Arc::new(CommitGate {
            inner: db.clone(),
            armed: AtomicBool::new(false),
            registry_calls: AtomicUsize::new(0),
            registry_steps: AtomicUsize::new(0),
            entered: Notify::new(),
            resume: Notify::new(),
        });
        let d = Arc::new(
            driver::Driver::new_on(
                gate.clone(),
                DbSqlFlavor::Sqlite,
                &bcs_config::resolve_env_str(),
            )
            .await,
        );
        for bot in ["race-a", "race-b"] {
            d.register(bot).await;
        }
        d.role("human_source", "race-a", Some(AgentInvokeRole::Owner))
            .await;
        d.role("human_target", "race-b", Some(AgentInvokeRole::Owner))
            .await;
        let pending = d
            .service
            .change(command(
                &d,
                "race-request",
                "human_source",
                AgentInvokeChange::Request,
            ))
            .await
            .unwrap()
            .current_request
            .unwrap();
        let before = d.audit_count().await;
        gate.armed.store(true, Ordering::SeqCst);
        let worker = d.clone();
        let id = pending.request_id.clone();
        let approval = tokio::spawn(async move {
            worker
                .service
                .change(command(
                    &worker,
                    "race-approve",
                    "human_target",
                    AgentInvokeChange::Approve {
                        request_id: id,
                        expected_version: pending.version,
                    },
                ))
                .await
        });
        gate.entered.notified().await;
        let repo = PersistentBotRepo::with_sql_flavor(gate.clone(), DbSqlFlavor::Sqlite);
        repo.update_actor_status("race-a", ActorStatus::Hidden)
            .await
            .unwrap();
        repo.update_actor_status("race-a", ActorStatus::Online)
            .await
            .unwrap();
        assert_eq!(gate.registry_calls.load(Ordering::SeqCst), 2);
        assert_eq!(gate.registry_steps.load(Ordering::SeqCst), 6);
        gate.resume.notify_one();
        assert_eq!(approval.await.unwrap(), Err(AgentInvokeError::Conflict));
        let rows = db
            .query(DbStatement::with_params(
                "SELECT status FROM permission_requests WHERE request_id=?",
                vec![pending.request_id.into()],
            ))
            .await
            .unwrap();
        assert_eq!(
            rows[0].get_string("status").unwrap().as_deref(),
            Some("pending")
        );
        assert_eq!(
            d.audit_count().await,
            before,
            "rejected approval has no audit prefix"
        );
        assert!(
            db.query(DbStatement::new("SELECT * FROM agent_invoke_grants"))
                .await
                .unwrap()
                .is_empty()
        );
        let receipts = db
            .query(DbStatement::new(
                "SELECT COUNT(*) AS n FROM agent_invoke_receipts",
            ))
            .await
            .unwrap();
        assert_eq!(
            db_get_column::<u64>(&receipts[0], "n").unwrap(),
            1,
            "failed approve cannot consume its key"
        );
    })
    .await
    .expect("Notify-driven race must finish without sleeps");
}
