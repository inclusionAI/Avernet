use super::*;
use bcs_db_api::{DbError, DbResult, DbRow, DbExecuteResult, DbHealth, DbTransactionStep, DbTransactionStepResult};

struct GapDb { inner: Arc<LocalSqliteDbPlugin>, pause: Mutex<Option<&'static str>>, entered: Notify, resume: Notify, fail_session: AtomicBool }
#[async_trait]
impl DbPlugin for GapDb {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> { self.inner.query(s).await }
    async fn execute(&self, s: DbStatement) -> DbResult<DbExecuteResult> {
        let pause = { let mut p = self.pause.lock().await; if p.is_some_and(|needle| s.sql().contains(needle)) { p.take().is_some() } else { false } };
        if pause { self.entered.notify_one(); self.resume.notified().await; }
        if s.sql().starts_with("UPDATE bcs_group_sessions SET status = 'completed'") && self.fail_session.swap(false, Ordering::SeqCst) {
            return Err(DbError::Backend("injected gap Session failure".into()));
        }
        self.inner.execute(s).await
    }
    async fn transaction(&self, s: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { self.inner.transaction(s).await }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}
struct Fixture { db: Arc<GapDb>, store: Arc<MySqlCollaborationStore>, runtime: CollaborationRuntime,
    sessions: Arc<SessionManagementServiceImpl>, delivery: Arc<RecordingDelivery>, channel: Arc<RecordingSessionChannelOutbound> }
impl Fixture {
    async fn new(path: Option<&str>, enabled: bool) -> Self {
        let inner = Arc::new(match path { Some(p) => LocalSqliteDbPlugin::new_file(p).unwrap(), None => LocalSqliteDbPlugin::new().unwrap() });
        bootstrap_migrations::run_sqlite_migrations(inner.as_ref()).await.unwrap();
        let db = Arc::new(GapDb { inner, pause: Mutex::new(None), entered: Notify::new(), resume: Notify::new(), fail_session: AtomicBool::new(false) });
        let store = Arc::new(MySqlCollaborationStore::sqlite(db.clone(), "test".into()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(Arc::new(MySqlSessionStore::sqlite(db.clone(), "test".into())), Arc::new(MemoryGroupRepo::new())));
        let group = Arc::new(GroupStore::new()); group.upsert(test_group()).await.unwrap();
        let delivery = Arc::new(RecordingDelivery::default()); let channel = Arc::new(RecordingSessionChannelOutbound::default());
        let mut runtime = CollaborationRuntime::new(store.clone(), store.clone(), store.clone(), store.clone(), group,
            sessions.clone(), delivery.clone(), noop_judge()).with_message_repo(Arc::new(MySqlMessageStore::sqlite(db.clone(), "test".into())))
            .with_session_channel_outbound(channel.clone());
        if enabled { runtime = runtime.with_experimental_fixed_loop_execution(); }
        Self { db, store, runtime, sessions, delivery, channel }
    }
    async fn original_run(&self) -> StateMachineRun {
        let id: String = bcs_db_api::db_get_column(&self.db.query(DbStatement::new("SELECT run_id FROM bcs_state_machine_runs")).await.unwrap()[0], "run_id").unwrap();
        self.store.get_run(&id).await.unwrap().unwrap()
    }
    async fn interrupt(&self, stage: &'static str, yaml: String) -> StateMachineRun {
        *self.db.pause.lock().await = Some(stage);
        tokio::select! {
            r = self.runtime.start_state_machine_run(command(yaml, false)) => panic!("creator did not pause: {r:?}"),
            () = self.db.entered.notified() => {}
        }
        self.original_run().await
    }
    async fn age(&self, stage: &str) {
        if matches!(stage, "dispatch" | "retry" | "accepted") {
            self.db.execute(DbStatement::new(if stage == "retry" {
                "UPDATE bcs_state_machine_node_runs SET started_at_ms = 100, timeout_deadline_ms = 200, max_attempts = 2 WHERE status = 'running'"
            } else { "UPDATE bcs_state_machine_node_runs SET started_at_ms = 100, timeout_deadline_ms = NULL WHERE status = 'running'" })).await.unwrap();
        } else { self.db.execute(DbStatement::new("UPDATE bcs_state_machine_runs SET created_at_ms = 100")).await.unwrap(); }
    }
}

#[tokio::test]
async fn missing_facts_preserve_live_creator_then_fence_expired_creator() {
    for expire in [false, true] {
        let h = Fixture::new(None, true).await;
        *h.db.pause.lock().await = Some("INSERT INTO bcs_state_machine_definition_snapshots");
        let creator = h.runtime.start_state_machine_run(command(single_node_yaml(), false)); tokio::pin!(creator);
        tokio::select! { r = &mut creator => panic!("creator did not pause: {r:?}"), () = h.db.entered.notified() => {} }
        let run = h.original_run().await;
        let page = h.runtime.recover_state_machine_progression(None, 10).await.unwrap(); assert_eq!(page.failures.len(), 1);
        assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Pending);
        if expire {
            h.age("snapshot").await;
            let page = h.runtime.recover_state_machine_progression(None, 10).await.unwrap(); assert!(page.failures.is_empty(), "{:?}", page.failures);
        }
        h.db.resume.notify_one();
        assert_eq!(creator.await.is_err(), expire);
        assert_eq!(h.delivery.commands.lock().await.len(), usize::from(!expire));
        assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, if expire { SessionStatus::Completed } else { SessionStatus::Running });
    }
}

#[tokio::test]
async fn startup_failure_record_write_failure_rolls_back_run_and_recovers_session_failure() {
    let h = Fixture::new(None, true).await;
    let run = h.interrupt("INSERT INTO bcs_state_machine_definition_snapshots", single_node_yaml()).await; h.age("snapshot").await;
    h.db.execute(DbStatement::new("CREATE TRIGGER reject_startup_failure BEFORE INSERT ON bcs_collaboration_delivery_checkpoints WHEN NEW.operation_kind = 'startup_failure' BEGIN SELECT RAISE(ABORT, 'injected failure record write'); END")).await.unwrap();
    let page = h.runtime.recover_state_machine_progression(None, 10).await.unwrap(); assert_eq!(page.failures.len(), 1);
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Pending);
    assert!(h.store.get_startup_failure(&run.run_id).await.unwrap().is_none());
    h.db.execute(DbStatement::new("DROP TRIGGER reject_startup_failure")).await.unwrap();
    h.db.fail_session.store(true, Ordering::SeqCst);
    let page = h.runtime.recover_state_machine_progression(None, 10).await.unwrap(); assert_eq!(page.failures.len(), 1);
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Failed);
    assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Running);
    let page = h.runtime.recover_state_machine_sessions(None, 10).await.unwrap(); assert!(page.failures.is_empty(), "{:?}", page.failures); assert_eq!(page.completed, 1);
    assert!(h.delivery.commands.lock().await.is_empty()); assert!(h.channel.terminal_events.lock().await.is_empty());
}

#[tokio::test]
async fn foreground_preparation_errors_complete_session_without_waiting_for_grace() {
    for snapshot in [true, false] {
        let h = Fixture::new(None, true).await;
        h.db.execute(DbStatement::new(if snapshot {
            "CREATE TRIGGER reject_preparation BEFORE INSERT ON bcs_state_machine_definition_snapshots BEGIN SELECT RAISE(ABORT, 'injected preparation error'); END"
        } else {
            "CREATE TRIGGER reject_preparation BEFORE INSERT ON bcs_collaboration_delivery_checkpoints WHEN NEW.operation_kind = 'run_opening' BEGIN SELECT RAISE(ABORT, 'injected preparation error'); END"
        })).await.unwrap();
        assert!(h.runtime.start_state_machine_run(command(single_node_yaml(), false)).await.is_err());
        let run = h.original_run().await;
        assert_eq!(run.status, StateMachineRunStatus::Failed);
        assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Completed);
        assert!(h.store.get_startup_failure(&run.run_id).await.unwrap().is_some());
        assert!(h.delivery.commands.lock().await.is_empty());
    }
}

#[tokio::test]
async fn missing_snapshot_failure_never_completes_a_new_activation() {
    let h = Fixture::new(None, true).await;
    let run = h.interrupt("INSERT INTO bcs_state_machine_definition_snapshots", single_node_yaml()).await;
    h.age("snapshot").await;
    h.db.execute(DbStatement::new("UPDATE bcs_group_sessions SET activation_count = activation_count + 1")).await.unwrap();
    let page = h.runtime.recover_state_machine_progression(None, 10).await.unwrap(); assert!(page.failures.is_empty());
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Failed);
    assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Running);
    assert_eq!(h.runtime.recover_state_machine_sessions(None, 10).await.unwrap().completed, 0);
    assert!(h.channel.terminal_events.lock().await.is_empty());
}

async fn child(path: &str, case: &str, prepare: bool) {
    let h = Fixture::new(Some(path), true).await;
    if prepare {
        let stage = match case { "snapshot" | "session" => "INSERT INTO bcs_state_machine_definition_snapshots", "opening" | "v2" => "'run_opening'", _ => "'bot_dispatch'" };
        let yaml = if case == "v2" { loop_yaml(2, false, false, 1) } else { single_node_yaml() };
        h.interrupt(stage, yaml).await;
        h.age(if case == "session" { "snapshot" } else { case }).await;
        if case == "accepted" {
            h.db.execute(DbStatement::new("UPDATE bcs_state_machine_node_runs SET bot_delivery_run_id = 'original-provider-run' WHERE status = 'running'")).await.unwrap();
        }
        if case == "session" {
            h.db.fail_session.store(true, Ordering::SeqCst);
            assert_eq!(h.runtime.recover_state_machine_progression(None, 10).await.unwrap().failures.len(), 1);
        }
        h.db.execute(DbStatement::new("DELETE FROM bcs_collaboration_definitions")).await.unwrap();
    } else {
        let run = h.original_run().await;
        for _ in 0..2 {
            let p = h.runtime.recover_state_machine_progression(None, 10).await.unwrap(); assert!(p.failures.is_empty(), "{:?}", p.failures);
            let p = h.runtime.recover_state_machine_sessions(None, 10).await.unwrap(); assert!(p.failures.is_empty(), "{:?}", p.failures);
        }
        let saved = h.store.get_run(&run.run_id).await.unwrap().unwrap();
        assert_eq!(saved.status, if matches!(case, "retry" | "accepted") { StateMachineRunStatus::Running } else { StateMachineRunStatus::Failed });
        assert_eq!(h.delivery.commands.lock().await.len(), usize::from(case == "retry"));
        if case == "retry" { assert_eq!(h.store.list_node_runs(&run.run_id).await.unwrap()[0].attempt, 1); }
        else if case != "accepted" {
            assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Completed);
            assert!(saved.error.unwrap().contains("missing"));
        }
    }
}

#[tokio::test]
async fn sqlite_process_restart_converges_missing_original_facts() {
    if let Ok(path) = std::env::var("BCS_GAP_TEST_DB") {
        child(&path, &std::env::var("BCS_GAP_TEST_CASE").unwrap(), std::env::var("BCS_GAP_TEST_PHASE").unwrap() == "prepare").await; return;
    }
    let directory = TempDatabaseDirectory(std::env::temp_dir().join(format!("bcs-gap-{}", uuid::Uuid::new_v4())));
    for case in ["snapshot", "opening", "dispatch", "retry", "session", "v2", "accepted"] {
        for phase in ["prepare", "recover"] {
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "fixed_loop_tests::recovery_tests::sqlite_restart_tests::recovery_gap_tests::sqlite_process_restart_converges_missing_original_facts", "--nocapture"])
                .env("BCS_GAP_TEST_DB", directory.0.join(format!("{case}.sqlite"))).env("BCS_GAP_TEST_CASE", case).env("BCS_GAP_TEST_PHASE", phase).output().unwrap();
            assert!(output.status.success(), "{case}/{phase}: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        }
    }
}
