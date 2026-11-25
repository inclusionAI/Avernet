use async_trait::async_trait;
use bcs_db_api::*;
use bcs_edge_permission::agent_invoke::{AgentInvokeApplicationService, AgentInvokePolicy};
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeCommandStore;
use bcs_service_api::application::agent_invoke::{
    AgentInvocationService, AgentInvokeGrantManagementService,
};
use bcs_service_api::types::agent_invoke::*;
use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
use std::sync::{
    Arc,
    atomic::{AtomicU8, AtomicUsize, Ordering},
};
#[path = "support/agent_invoke_sql_driver.rs"]
mod driver;
use driver::Driver;
fn pair() -> AgentInvokePair {
    AgentInvokePair {
        from_bot_id: "bot-a".into(),
        to_bot_id: "bot-b".into(),
    }
}
fn cmd(d: &Driver, actor: &str, key: &str, change: AgentInvokeChange) -> AgentInvokeCommand {
    AgentInvokeCommand {
        principal: AgentInvokePrincipal::Human(actor.into()),
        authenticated_scope: d.scope.clone(),
        pair: pair(),
        change,
        idempotency_key: key.into(),
        reason: "SQL atomicity test".into(),
    }
}
async fn setup(d: &Driver) {
    for b in ["bot-a", "bot-b", "bot-c"] {
        d.register(b).await;
    }
    d.role("human_source", "bot-a", Some(AgentInvokeRole::Owner))
        .await;
    for b in ["bot-b", "bot-c"] {
        d.role("human_target", b, Some(AgentInvokeRole::Owner))
            .await;
    }
}
async fn count(d: &Driver, table: &str) -> u64 {
    let rows =
        d.db.query(DbStatement::new(format!(
            "SELECT COUNT(*) AS n FROM {table}"
        )))
        .await
        .unwrap();
    db_get_column(&rows[0], "n").unwrap()
}
struct ObservedDb {
    inner: Arc<dyn DbPlugin>,
    fault: AtomicU8,
    writes: AtomicUsize,
    statements: AtomicUsize,
    gate: AtomicU8,
    entered: tokio::sync::Notify,
    resume: tokio::sync::Notify,
}
impl ObservedDb {
    fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            fault: AtomicU8::new(0),
            writes: AtomicUsize::new(0),
            statements: AtomicUsize::new(0),
            gate: AtomicU8::new(0),
            entered: tokio::sync::Notify::new(),
            resume: tokio::sync::Notify::new(),
        }
    }
}
#[async_trait]
impl DbPlugin for ObservedDb {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> {
        self.statements.fetch_add(1, Ordering::SeqCst);
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
        mut s: Vec<DbTransactionStep>,
        o: DbTransactionOptions,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.statements.fetch_add(s.len(), Ordering::SeqCst);
        let command = s.iter().any(|x| match x {
            DbTransactionStep::ExecuteChecked { statement, .. } => statement
                .sql()
                .starts_with("INSERT INTO agent_invoke_receipts"),
            _ => false,
        });
        if !command {
            return self.inner.transaction_with_options(s, o).await;
        }
        assert!(s.len() <= 32, "bounded, fixed command plan");
        for step in &s {
            let statement = match step {
                DbTransactionStep::ExecuteChecked { statement, .. }
                | DbTransactionStep::Query(statement)
                | DbTransactionStep::Execute(statement) => statement,
            };
            assert!(
                statement.params().len() <= 512,
                "bounded statement parameters below SQLite limit"
            );
        }
        self.writes.fetch_add(1, Ordering::SeqCst);
        if self.gate.swap(0, Ordering::SeqCst) == 1 {
            self.entered.notify_one();
            self.resume.notified().await;
        }
        let fault = self.fault.swap(0, Ordering::SeqCst);
        if fault == 1 {
            for step in &mut s {
                if let DbTransactionStep::ExecuteChecked { statement, .. } = step {
                    if statement
                        .sql()
                        .starts_with("INSERT INTO agent_invoke_audit")
                    {
                        *statement = DbStatement::new(
                            "INSERT INTO deliberately_missing_audit_table DEFAULT VALUES",
                        );
                        break;
                    }
                }
            }
        }
        let result = self.inner.transaction_with_options(s, o).await?;
        if fault == 2 {
            return Err(DbError::Backend(
                "injected lost commit acknowledgement".into(),
            ));
        }
        Ok(result)
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}
fn app(d: &Driver, db: Arc<ObservedDb>) -> AgentInvokeApplicationService {
    let one = std::num::NonZeroU32::MIN;
    let store = Arc::new(
        bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore::new(
            db,
            DbSqlFlavor::Sqlite,
            d.scope.clone(),
            AgentInvokeLimits {
                max_out_degree: one,
                max_in_degree: one,
                max_pending_out: one,
                max_pending_in: one,
            },
            std::num::NonZeroU64::new(60_000).unwrap(),
        )
        .unwrap(),
    );
    let policy = Arc::new(AgentInvokePolicy);
    let commands = Arc::new(DbAgentInvokeCommandStore::new(
        store.clone(),
        policy.clone(),
    ));
    AgentInvokeApplicationService::new(d.scope.clone(), policy, store, commands).unwrap()
}
#[tokio::test]
async fn audit_failure_rolls_back_edge_request_receipt_scope_and_pending() {
    for change in [
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
        AgentInvokeChange::Request,
    ] {
        let d = Driver::new().await;
        setup(&d).await;
        let db = Arc::new(ObservedDb::new(d.db.clone()));
        let app = app(&d, db.clone());
        let old = d.store.scope_state().await.unwrap();
        let c = cmd(
            &d,
            if matches!(change, AgentInvokeChange::Request) {
                "human_source"
            } else {
                "human_target"
            },
            "failed-first",
            change,
        );
        db.fault.store(1, Ordering::SeqCst);
        assert_eq!(
            app.change(c.clone()).await,
            Err(AgentInvokeError::CommitUnknown)
        );
        assert_eq!(d.store.scope_state().await.unwrap(), old);
        assert_eq!(count(&d, "agent_invoke_receipts").await, 0);
        assert_eq!(count(&d, "agent_invoke_grants").await, 0);
        assert_eq!(count(&d, "agent_invoke_requests").await, 0);
        assert_eq!(count(&d, "agent_invoke_pending_pairs").await, 0);
        assert_eq!(d.audit_count().await, 0);
        let recovered = app.change(c).await.unwrap();
        assert!(!recovered.replayed);
        assert_eq!(count(&d, "agent_invoke_receipts").await, 1);
        assert_eq!(d.audit_count().await, 1);
    }
}
#[tokio::test]
async fn lost_commit_ack_recovers_immutable_receipt_without_duplicate_write() {
    let d = Driver::new().await;
    setup(&d).await;
    let db = Arc::new(ObservedDb::new(d.db.clone()));
    let app = app(&d, db.clone());
    let c = cmd(
        &d,
        "human_target",
        "unknown-commit",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    db.fault.store(2, Ordering::SeqCst);
    assert_eq!(
        app.change(c.clone()).await,
        Err(AgentInvokeError::CommitUnknown)
    );
    assert_eq!(db.writes.load(Ordering::SeqCst), 1);
    assert_eq!(d.audit_count().await, 1);
    let recovered = app.change(c).await.unwrap();
    assert!(recovered.replayed);
    assert_eq!(db.writes.load(Ordering::SeqCst), 1);
    assert_eq!(d.audit_count().await, 1);
}
#[tokio::test]
async fn role_loss_after_precheck_is_fenced_inside_the_write() {
    let d = Arc::new(Driver::new().await);
    setup(&d).await;
    let db = Arc::new(ObservedDb::new(d.db.clone()));
    db.gate.store(1, Ordering::SeqCst);
    let app = app(&d, db.clone());
    let c = cmd(
        &d,
        "human_target",
        "role-race",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    let task = tokio::spawn(async move { app.change(c).await });
    db.entered.notified().await;
    d.role("human_target", "bot-b", None).await;
    db.resume.notify_one();
    assert_eq!(task.await.unwrap(), Err(AgentInvokeError::Conflict));
    assert_eq!(count(&d, "agent_invoke_grants").await, 0);
    assert_eq!(count(&d, "agent_invoke_receipts").await, 0);
    assert_eq!(d.audit_count().await, 0);
}
#[tokio::test]
async fn same_human_record_retirement_restoration_does_not_revive_pending_request() {
    let d = Driver::new().await;
    setup(&d).await;
    let requested = d
        .service
        .change(cmd(
            &d,
            "human_source",
            "request",
            AgentInvokeChange::Request,
        ))
        .await
        .unwrap()
        .original_request
        .unwrap();
    d.sql("UPDATE bcs_bots SET is_deleted=1,agent_invoke_identity_revision=agent_invoke_identity_revision+1 WHERE bot_uuid='human_source'",vec![]).await;
    d.sql("UPDATE bcs_bots SET is_deleted=0,agent_invoke_identity_revision=agent_invoke_identity_revision+1 WHERE bot_uuid='human_source'",vec![]).await;
    let c = cmd(
        &d,
        "human_target",
        "approve-after-human-aba",
        AgentInvokeChange::Approve {
            request_id: requested.request_id,
            expected_version: requested.version,
        },
    );
    assert_eq!(d.service.change(c).await, Err(AgentInvokeError::Conflict));
    assert_eq!(count(&d, "agent_invoke_grants").await, 0);
}
#[tokio::test]
async fn governance_loss_restoration_cannot_approve_an_old_request() {
    let d = Driver::new().await;
    setup(&d).await;
    let principal = AgentInvokePrincipal::Governance("request-governor".into());
    d.delegate(
        &principal,
        &pair(),
        0,
        vec![AgentInvokeAction::Request],
        "trusted fixture delegation",
    )
    .await
    .unwrap();
    let mut c = cmd(
        &d,
        "human_source",
        "governance-request",
        AgentInvokeChange::Request,
    );
    c.principal = principal.clone();
    let requested = d.service.change(c).await.unwrap().original_request.unwrap();
    d.delegate(&principal, &pair(), 1, vec![], "loss")
        .await
        .unwrap();
    d.delegate(
        &principal,
        &pair(),
        2,
        vec![AgentInvokeAction::Request],
        "restoration",
    )
    .await
    .unwrap();
    assert_eq!(
        d.service
            .change(cmd(
                &d,
                "human_target",
                "governance-stale-approve",
                AgentInvokeChange::Approve {
                    request_id: requested.request_id,
                    expected_version: requested.version
                }
            ))
            .await,
        Err(AgentInvokeError::Conflict)
    );
}
#[tokio::test]
async fn file_restart_replays_committed_action_and_runtime_permission() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("lifecycle.db");
    let original;
    let command;
    {
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new_file(&path).unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        let d = Driver::new_on(db, DbSqlFlavor::Sqlite, "restart").await;
        setup(&d).await;
        command = cmd(
            &d,
            "human_target",
            "durable-restart",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        );
        original = d.service.change(command.clone()).await.unwrap();
    }
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new_file(&path).unwrap());
    let one = std::num::NonZeroU32::MIN;
    let store = Arc::new(
        bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore::new(
            db,
            DbSqlFlavor::Sqlite,
            command.authenticated_scope.clone(),
            AgentInvokeLimits {
                max_out_degree: one,
                max_in_degree: one,
                max_pending_out: one,
                max_pending_in: one,
            },
            std::num::NonZeroU64::new(60_000).unwrap(),
        )
        .unwrap(),
    );
    store.initialize_scope().await.unwrap();
    let policy = Arc::new(AgentInvokePolicy);
    let commands = Arc::new(DbAgentInvokeCommandStore::new(
        store.clone(),
        policy.clone(),
    ));
    let service = AgentInvokeApplicationService::new(
        command.authenticated_scope.clone(),
        policy,
        store,
        commands,
    )
    .unwrap();
    let recovered = service.change(command.clone()).await.unwrap();
    assert!(recovered.replayed);
    assert_eq!(recovered.action_id, original.action_id);
    assert_eq!(recovered.original_grant, original.original_grant);
    let permit = service
        .authorize_invocation(AgentInvocationInput {
            principal: AgentInvokePrincipal::Agent("bot-a".into()),
            authenticated_scope: command.authenticated_scope,
            pair: pair(),
            originator: "bot-a".into(),
            operation: AgentInvokeOperation::DirectInvocation,
        })
        .await
        .unwrap();
    assert_eq!(
        permit.ref_id,
        original.original_grant.unwrap().edge_id.get()
    );
}
#[tokio::test]
async fn invocation_reads_remain_one_query_and_management_is_bounded() {
    let d = Driver::new().await;
    setup(&d).await;
    let db = Arc::new(ObservedDb::new(d.db.clone()));
    let app = app(&d, db.clone());
    app.change(cmd(
        &d,
        "human_target",
        "bounded-grant",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    ))
    .await
    .unwrap();
    let statements = db.statements.load(Ordering::SeqCst);
    assert!(
        statements <= 48,
        "bounded ordinary management plan, got {statements}"
    );
    db.statements.store(0, Ordering::SeqCst);
    app.authorize_invocation(AgentInvocationInput {
        principal: AgentInvokePrincipal::Agent("bot-a".into()),
        authenticated_scope: d.scope.clone(),
        pair: pair(),
        originator: "bot-a".into(),
        operation: AgentInvokeOperation::DirectInvocation,
    })
    .await
    .unwrap();
    assert_eq!(db.statements.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn registry_change_requires_fresh_generation_and_explicit_reapproval() {
    use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;
    let d = Driver::new().await;
    setup(&d).await;
    let granted = d
        .service
        .change(cmd(
            &d,
            "human_target",
            "before-rebind",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        ))
        .await
        .unwrap()
        .original_grant
        .unwrap();
    d.sql("UPDATE bcs_bots SET agent_invoke_identity_revision=agent_invoke_identity_revision+1 WHERE bot_uuid='bot-a'",vec![]).await;
    let input = AgentInvocationInput {
        principal: AgentInvokePrincipal::Agent("bot-a".into()),
        authenticated_scope: d.scope.clone(),
        pair: pair(),
        originator: "bot-a".into(),
        operation: AgentInvokeOperation::DirectInvocation,
    };
    assert_eq!(
        d.service.authorize_invocation(input.clone()).await,
        Err(AgentInvokeError::Unavailable)
    );
    let old = d.store.read_fact(&d.scope, "bot-a").await.unwrap().unwrap();
    let mut p = AgentInvokeFactPublication {
        state: old.state,
        expected_revision: old.revision.get(),
        valid_until_ms: d.store.database_time_ms().await.unwrap() + 30_000,
    };
    assert_eq!(
        d.store.publish_fact(&d.scope, p.clone()).await,
        Err(AgentInvokeError::Conflict)
    );
    p.state.binding_generation =
        std::num::NonZeroU64::new(p.state.binding_generation.get() + 1).unwrap();
    d.store.publish_fact(&d.scope, p).await.unwrap();
    assert!(d.service.authorize_invocation(input.clone()).await.is_err());
    let rows =
        d.db.query(DbStatement::new("SELECT version FROM agent_invoke_grants"))
            .await
            .unwrap();
    let version: u64 = db_get_column(&rows[0], "version").unwrap();
    assert!(version > granted.version.get());
    let renewed = d
        .service
        .change(cmd(
            &d,
            "human_target",
            "after-rebind",
            AgentInvokeChange::Grant {
                expected_version: version,
            },
        ))
        .await
        .unwrap()
        .original_grant
        .unwrap();
    assert_eq!(renewed.edge_id, granted.edge_id);
    assert_eq!(
        d.service
            .authorize_invocation(input)
            .await
            .unwrap()
            .revision,
        Some(renewed.version.get())
    );
}
#[tokio::test]
async fn governance_permission_is_explicit_scoped_and_rechecked_on_replay() {
    let d = Driver::new().await;
    setup(&d).await;
    let principal = AgentInvokePrincipal::Governance("grant-governor".into());
    d.delegate(
        &principal,
        &pair(),
        0,
        vec![AgentInvokeAction::Grant],
        "trusted grant delegation",
    )
    .await
    .unwrap();
    let mut c = cmd(
        &d,
        "human_target",
        "governance-grant",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    c.principal = principal.clone();
    let mut wrong = c.clone();
    wrong.pair.to_bot_id = "bot-c".into();
    assert_eq!(
        d.service.change(wrong).await,
        Err(AgentInvokeError::Forbidden)
    );
    let mut agent = c.clone();
    agent.principal = AgentInvokePrincipal::Agent("grant-governor".into());
    assert_eq!(
        d.service.change(agent).await,
        Err(AgentInvokeError::Forbidden)
    );
    let original = d.service.change(c.clone()).await.unwrap();
    assert_eq!(
        d.delegate(&principal, &pair(), 0, vec![], "stale publisher")
            .await,
        Err(AgentInvokeError::Conflict)
    );
    d.delegate(&principal, &pair(), 1, vec![], "delegation lost")
        .await
        .unwrap();
    assert_eq!(
        d.service.change(c.clone()).await,
        Err(AgentInvokeError::Forbidden)
    );
    d.delegate(
        &principal,
        &pair(),
        2,
        vec![AgentInvokeAction::Grant],
        "delegation restored",
    )
    .await
    .unwrap();
    let recovered = d.service.change(c).await.unwrap();
    assert!(recovered.replayed);
    assert_eq!(recovered.action_id, original.action_id);
    assert_eq!(count(&d, "agent_invoke_receipts").await, 1);
}
