use std::sync::Arc;
use bcs_chat_run_store::a2a::DbA2aRunStore;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::a2a_run::{A2aContextScope, A2aRunRecord, A2aRunRepoPort, A2aRunState};
use bcs_service_api::types::a2a_bot::{A2aBinding, A2aInterface, A2aUpdateMode};
use bcs_test_support::contract::a2a_bot::a2a_run_repo_port_contract_tests;

async fn initialize(db: &dyn DbPlugin) {
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (bot_uuid TEXT, env TEXT, is_deleted INTEGER DEFAULT 0, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP)")).await.unwrap();
    for sql in bcs_db_api::split_sql_statements(include_str!("../../../../migrations/sqlite/039_bot_a2a.sql")).into_iter() {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
}

fn initial() -> A2aRunRecord {
    A2aRunRecord { run_id: "local-run".into(), scope: A2aContextScope { bot_id: "bot".into(),
        session_id: "session".into(), provider_id: "provider".into(), provider_bot_ref: "remote".into(),
        interface_url: "https://example.org/agent".into(), tenant: None, route_fingerprint:None }, group_id: "group".into(),
        interface: A2aInterface { url: "https://example.org/agent".into(), tenant: None, protocol_version: "1.0".into(),
            binding: A2aBinding::HttpJson, update_mode: A2aUpdateMode::Stream }, credential_kind: "downlink_bcs_to_provider".into(),
        extensions: vec![], message_id: "local-run".into(), request_digest: "test-digest".into(), remote_task_id: None, context_id: None, task_version:None,
        budget:None,pending_input:None,
        state: A2aRunState::Submitting, deadline_ms: 121000, updated_at_ms: 1000, version: 1 }
}
fn accepted(mut record: A2aRunRecord) -> A2aRunRecord {
    record.version = 2;
    record.remote_task_id = Some("remote-task".into()); record.context_id = Some("remote-context".into());
    record.state = A2aRunState::Running;
    record
}

#[tokio::test]
async fn explicit_context_columns_preserve_scope_isolation() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); initialize(db.as_ref()).await;
    let store=DbA2aRunStore::new(db.clone(),"test".into(),DbSqlFlavor::Sqlite);
    assert!(store.create(&initial()).await.unwrap());
    store.compare_and_set(&accepted(initial()),1).await.unwrap().unwrap();
    let rows=db.query(DbStatement::new("SELECT bot_id,session_id,provider_id,provider_bot_ref,interface_url,tenant,route_fingerprint,context_id FROM bcs_a2a_contexts")).await.unwrap();
    assert_eq!(rows[0].get_string("session_id").unwrap().as_deref(),Some("session"));
    assert_eq!(store.context(&initial().scope).await.unwrap().as_deref(),Some("remote-context"));
}

#[tokio::test]
async fn sqlite_remote_run_conformance() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); initialize(db.as_ref()).await;
    let store = DbA2aRunStore::new(db, "test".into(), DbSqlFlavor::Sqlite);
    a2a_run_repo_port_contract_tests(&store, initial()).await;
}

#[tokio::test]
async fn failed_context_write_rolls_back_task_identity_and_version() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); initialize(db.as_ref()).await;
    let store = DbA2aRunStore::new(db.clone(), "test".into(), DbSqlFlavor::Sqlite);
    assert!(store.create(&initial()).await.unwrap());
    db.execute(DbStatement::new("CREATE TRIGGER reject_context BEFORE INSERT ON bcs_a2a_contexts BEGIN SELECT RAISE(ABORT, 'test failure'); END")).await.unwrap();
    assert!(store.compare_and_set(&accepted(initial()), 1).await.is_err());
    assert_eq!(store.get("local-run").await.unwrap(), Some(initial()));
    assert!(store.context(&initial().scope).await.unwrap().is_none());
}

#[tokio::test]
async fn one_remote_task_cannot_be_rebound_to_a_different_local_session() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); initialize(db.as_ref()).await;
    let store = DbA2aRunStore::new(db, "test".into(), DbSqlFlavor::Sqlite);
    let first = initial(); assert!(store.create(&first).await.unwrap());
    assert!(store.compare_and_set(&accepted(first), 1).await.unwrap().is_some());
    let mut second = initial(); second.run_id = "other-run".into(); second.scope.session_id = "other-session".into();
    assert!(store.create(&second).await.unwrap());
    assert!(store.compare_and_set(&accepted(second.clone()), 1).await.is_err());
    assert_eq!(store.get("other-run").await.unwrap(), Some(second.clone()));
    assert!(store.context(&second.scope).await.unwrap().is_none());
}

#[tokio::test]
async fn file_reopen_preserves_context_and_isolates_bot_route_and_environment() {
    let dir = tempfile::tempdir().unwrap(); let path = dir.path().join("correlation.db");
    {
        let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap()); initialize(db.as_ref()).await;
        let store = DbA2aRunStore::new(db, "test".into(), DbSqlFlavor::Sqlite);
        assert!(store.create(&initial()).await.unwrap());
        assert!(store.compare_and_set(&accepted(initial()), 1).await.unwrap().is_some());
    }
    let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
    let store = DbA2aRunStore::new(db.clone(), "test".into(), DbSqlFlavor::Sqlite);
    assert_eq!(store.context(&initial().scope).await.unwrap().as_deref(), Some("remote-context"));
    for scope in [A2aContextScope { bot_id: "other".into(), ..initial().scope },
        A2aContextScope { interface_url: "https://example.org/other".into(), ..initial().scope },
        A2aContextScope { route_fingerprint:Some("different-route".into()), ..initial().scope }] {
        assert!(store.context(&scope).await.unwrap().is_none());
    }
    assert!(DbA2aRunStore::new(db, "other-env".into(), DbSqlFlavor::Sqlite).get("local-run").await.unwrap().is_none());
}

#[tokio::test]
async fn input_budget_and_snapshot_round_trip_across_process_reopen() {
    use bcs_service_api::types::run_budget::{RunBudgetState,RunBudgetPhase};
    let dir=tempfile::tempdir().unwrap();let path=dir.path().join("input.db");
    let mut waiting=accepted(initial());waiting.state=A2aRunState::InputRequired;
    waiting.budget=Some(RunBudgetState {remaining_execution_ms:90_000,total_deadline_ms:1_000_000,input_wait_limit_ms:600_000,
        phase:RunBudgetPhase::Waiting {started_at_ms:30_000,deadline_ms:630_000}});
    waiting.pending_input=Some(serde_json::json!({"request":"opaque-adapter-snapshot","fullSchema":{"retained":true}}));
    {
        let db=Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());initialize(db.as_ref()).await;
        let store=DbA2aRunStore::new(db,"test".into(),DbSqlFlavor::Sqlite);
        assert!(store.create(&initial()).await.unwrap());waiting=store.compare_and_set(&waiting,1).await.unwrap().unwrap();
    }
    let store=DbA2aRunStore::new(Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap()),"test".into(),DbSqlFlavor::Sqlite);
    assert_eq!(store.get("local-run").await.unwrap(),Some(waiting));
}

#[tokio::test]
async fn audit_columns_follow_successful_run_and_context_updates() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); initialize(db.as_ref()).await;
    let store = DbA2aRunStore::new(db.clone(), "test".into(), DbSqlFlavor::Sqlite);
    assert!(store.create(&initial()).await.unwrap());
    assert!(store.compare_and_set(&accepted(initial()), 1).await.unwrap().is_some());
    for table in ["bcs_a2a_run_bindings", "bcs_a2a_tasks", "bcs_a2a_contexts"] {
        let rows = db.query(DbStatement::new(format!("SELECT id, gmt_create, gmt_modified FROM {table}"))).await.unwrap();
        assert!(rows[0].get_i64("id").unwrap().unwrap() > 0);
        assert!(rows[0].get_string("gmt_create").unwrap().is_some());
        assert!(rows[0].get_string("gmt_modified").unwrap().is_some());
        db.execute(DbStatement::new(format!("UPDATE {table} SET gmt_create='2000-01-01 00:00:00', gmt_modified='2000-01-01 00:00:00'"))).await.unwrap();
    }
    let mut next = store.get("local-run").await.unwrap().unwrap(); next.version = 3;
    assert!(store.compare_and_set(&next, 2).await.unwrap().is_some());
    // A stale CAS must leave both the domain record and its audit metadata untouched.
    assert!(store.compare_and_set(&next, 2).await.unwrap().is_none());
    for table in ["bcs_a2a_run_bindings", "bcs_a2a_tasks", "bcs_a2a_contexts"] {
        let rows = db.query(DbStatement::new(format!("SELECT gmt_create, gmt_modified FROM {table}"))).await.unwrap();
        assert_eq!(rows[0].get_string("gmt_create").unwrap().as_deref(), Some("2000-01-01 00:00:00"));
        if table=="bcs_a2a_contexts" {
            assert_eq!(rows[0].get_string("gmt_modified").unwrap().as_deref(),Some("2000-01-01 00:00:00"));
        } else {
            assert_ne!(rows[0].get_string("gmt_modified").unwrap().as_deref(),Some("2000-01-01 00:00:00"));
        }
    }
}

#[tokio::test]
async fn many_runs_share_one_task_with_revision_fencing() {
    let db=Arc::new(LocalSqliteDbPlugin::new().unwrap());initialize(db.as_ref()).await;
    let store=DbA2aRunStore::new(db,"test".into(),DbSqlFlavor::Sqlite);
    bcs_test_support::contract::a2a_bot::a2a_many_run_task_contract_tests(&store,initial()).await;
}

struct CountedDb {
    inner:Arc<LocalSqliteDbPlugin>,
    calls:std::sync::Mutex<Vec<String>>,
}
#[async_trait::async_trait]
impl DbPlugin for CountedDb {
    async fn query(&self, statement:DbStatement)->bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> {
        self.calls.lock().unwrap().push(statement.sql().into());self.inner.query(statement).await
    }
    async fn execute(&self, statement:DbStatement)->bcs_db_api::DbResult<bcs_db_api::DbExecuteResult> {
        self.calls.lock().unwrap().push(statement.sql().into());self.inner.execute(statement).await
    }
    async fn transaction(&self, steps:Vec<bcs_db_api::DbTransactionStep>)->bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> {
        for step in &steps {
            let statement=match step {bcs_db_api::DbTransactionStep::Query(s)|bcs_db_api::DbTransactionStep::Execute(s)=>s,
                bcs_db_api::DbTransactionStep::ExecuteChecked {statement,..}=>statement};
            self.calls.lock().unwrap().push(statement.sql().into());
        }
        self.inner.transaction(steps).await
    }
    async fn health_check(&self)->bcs_db_api::DbResult<bcs_db_api::DbHealth> {self.inner.health_check().await}
}

#[tokio::test]
async fn association_sql_cost_stays_bounded_with_shared_tasks_and_large_input() {
    let inner=Arc::new(LocalSqliteDbPlugin::new().unwrap());initialize(inner.as_ref()).await;
    let db=Arc::new(CountedDb {inner,calls:Default::default()});
    let store=DbA2aRunStore::new(db.clone(),"test".into(),DbSqlFlavor::Sqlite);
    assert!(store.create(&initial()).await.unwrap());
    assert_eq!(db.calls.lock().unwrap().len(),1);db.calls.lock().unwrap().clear();
    let mut next=accepted(initial());next.pending_input=Some(serde_json::json!({"schema":"x".repeat(64*1024)}));
    let mut current=store.compare_and_set(&next,1).await.unwrap().unwrap();
    assert_eq!(db.calls.lock().unwrap().len(),5);db.calls.lock().unwrap().clear();
    // Additional bindings do not cause subsequent writes to fan out over runs.
    for n in 0..16 {
        let mut run=initial();run.run_id=format!("shared-{n}");assert!(store.create(&run).await.unwrap());
        store.compare_and_set(&accepted(run),1).await.unwrap().unwrap();
    }
    current=store.get(&current.run_id).await.unwrap().unwrap();db.calls.lock().unwrap().clear();
    let stale=current.clone();current.version+=1;
    current=store.compare_and_set(&current,2).await.unwrap().unwrap();
    let calls=std::mem::take(&mut *db.calls.lock().unwrap());
    assert_eq!(calls.len(),3);
    assert!(calls.iter().all(|sql|!sql.contains("bcs_a2a_contexts")));
    let mut stale=stale;stale.version+=1;
    for _ in 0..3 {
        db.calls.lock().unwrap().clear();
        assert!(store.compare_and_set(&stale,2).await.unwrap().is_none());
        assert_eq!(db.calls.lock().unwrap().len(),3); // fixed-size transaction, no retry loop
    }
    db.calls.lock().unwrap().clear();assert_eq!(store.active(&current.scope).await.unwrap().len(),2);
    assert_eq!(db.calls.lock().unwrap().len(),1);
    db.calls.lock().unwrap().clear();assert!(store.has_other_active_task_run(&current).await.unwrap());
    assert_eq!(db.calls.lock().unwrap().len(),1);
}
