use super::*;
use bcs_db_api::{DbError, DbExecuteResult, DbHealth, DbResult, DbRow, DbTransactionStep, DbTransactionStepResult};
use bcs_service_api::SessionRepoPort;

struct SessionFaultDb {
    inner: Arc<dyn DbPlugin>,
    fail_completion: AtomicBool,
    advance_activation: AtomicBool,
    fail_page: AtomicBool,
}

#[async_trait]
impl DbPlugin for SessionFaultDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if statement.sql().contains("session_id >") && self.fail_page.swap(false, Ordering::SeqCst) {
            return Err(DbError::Backend("injected Session page failure".into()));
        }
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        if statement.sql().starts_with("UPDATE bcs_group_sessions SET status = 'completed'") {
            if self.fail_completion.swap(false, Ordering::SeqCst) {
                return Err(DbError::Backend("injected Session completion failure".into()));
            }
            if self.advance_activation.swap(false, Ordering::SeqCst) {
                // Simulate another actor completing/reactivating after this
                // caller read the old Session, immediately before its CAS.
                self.inner.execute(DbStatement::new("UPDATE bcs_group_sessions SET activation_count = activation_count + 1, status = 'running', output = NULL, error_message = NULL, completed_at = NULL")).await?;
            }
        }
        self.inner.execute(statement).await
    }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}

struct Fixture {
    db: Arc<SessionFaultDb>,
    store: Arc<MySqlCollaborationStore>,
    session_repo: Arc<MySqlSessionStore>,
    sessions: Arc<SessionManagementServiceImpl>,
    runtime: CollaborationRuntime,
    delivery: Arc<RecordingDelivery>,
    channel: Arc<RecordingSessionChannelOutbound>,
}

impl Fixture {
    async fn new(path: Option<&str>) -> Self {
        let inner = Arc::new(match path {
            Some(path) => LocalSqliteDbPlugin::new_file(path).unwrap(),
            None => LocalSqliteDbPlugin::new().unwrap(),
        });
        bootstrap_migrations::run_sqlite_migrations(inner.as_ref()).await.unwrap();
        let db = Arc::new(SessionFaultDb { inner: inner.clone(), fail_completion: AtomicBool::new(false),
            advance_activation: AtomicBool::new(false), fail_page: AtomicBool::new(false) });
        let store = Arc::new(MySqlCollaborationStore::sqlite(inner.clone(), "test".into()));
        let session_repo = Arc::new(MySqlSessionStore::sqlite(db.clone(), "test".into()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(session_repo.clone(), Arc::new(MemoryGroupRepo::new())));
        let group = Arc::new(GroupStore::new());
        group.upsert(test_group()).await.unwrap();
        let delivery = Arc::new(RecordingDelivery::default());
        let channel = Arc::new(RecordingSessionChannelOutbound::default());
        let runtime = CollaborationRuntime::new(store.clone(), store.clone(), store.clone(), store.clone(),
            group, sessions.clone(), delivery.clone(), Arc::new(SequencedJudge::new(Vec::new())))
            .with_message_repo(Arc::new(MySqlMessageStore::sqlite(inner, "test".into())))
            .with_session_channel_outbound(channel.clone()).with_loop_execution();
        Self { db, store, session_repo, sessions, runtime, delivery, channel }
    }

    async fn finish_with_session_write_failure(&self, status: StateMachineRunStatus) -> StateMachineRun {
        let before = self.delivery.commands.lock().await.len();
        let started = self.runtime.start_state_machine_run(command(loop_yaml(1, false, false, 1), false)).await.unwrap();
        let run = started.view.run;
        if status == StateMachineRunStatus::Completed {
            send_terminal(&self.runtime, &self.delivery, before, &run.session_id, "iteration result").await.unwrap();
        }
        self.db.fail_completion.store(true, Ordering::SeqCst);
        let error = match status {
            StateMachineRunStatus::Completed => send_terminal(&self.runtime, &self.delivery, before + 1, &run.session_id, "final result").await.unwrap_err(),
            StateMachineRunStatus::Failed => {
                let id = self.delivery.commands.lock().await[before].run_id.clone();
                self.runtime.handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
                    bot_id: "driver-bot".into(), run_id: id.clone(), event_type: "chat.event".into(),
                    event_payload: json!({"run_id": id, "state": "error", "text": "provider failure"}),
                    state: ChatEventState::Error, bcs_session_id: Some(run.session_id.clone()),
                }).await.unwrap_err()
            }
            StateMachineRunStatus::Aborted => self.runtime.cancel_state_machine_run(bcs_service_api::CancelStateMachineRunCommand {
                run_id: run.run_id.clone(), reason: Some("cancel test".into()),
            }).await.unwrap_err(),
            _ => panic!("expected terminal status"),
        };
        assert!(error.to_string().contains("injected Session completion failure"), "{error}");
        let stored = self.store.get_run(&run.run_id).await.unwrap().unwrap();
        assert_eq!(stored.status, status);
        assert_eq!(self.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Running);
        stored
    }

    async fn assert_recovered(&self, run: &StateMachineRun) {
        let before = self.delivery.commands.lock().await.len();
        let (one, two) = tokio::join!(self.runtime.recover_state_machine_sessions(None, 32), self.runtime.recover_state_machine_sessions(None, 32));
        let one = one.unwrap();
        let two = two.unwrap();
        assert!(one.failures.is_empty(), "{:?}", one.failures);
        assert!(two.failures.is_empty(), "{:?}", two.failures);
        assert_eq!(one.completed + two.completed, 1);
        let session = self.sessions.get(&run.session_id).await.unwrap().unwrap();
        assert_eq!(session.status, SessionStatus::Completed);
        assert_eq!(Some(session.activation_count), run.session_activation_count);
        assert_eq!(session.output, run.output.clone().map(Value::String));
        let expected_error = if run.status == StateMachineRunStatus::Aborted { Some("aborted".into()) } else { run.error.clone() };
        assert_eq!(session.error_message, expected_error);
        assert_eq!(serde_json::to_value(self.store.get_run(&run.run_id).await.unwrap().unwrap()).unwrap(), serde_json::to_value(run).unwrap());
        assert_eq!(self.delivery.commands.lock().await.len(), before);
        assert_eq!(self.runtime.recover_state_machine_sessions(None, 32).await.unwrap().completed, 0);
        assert_eq!(self.channel.terminal_events.lock().await.len(), usize::from(run.status != StateMachineRunStatus::Aborted));
    }
}

#[tokio::test]
async fn terminal_run_recovers_session_write_failure_without_rewriting_run_or_repeating_work() {
    for status in [StateMachineRunStatus::Completed, StateMachineRunStatus::Failed, StateMachineRunStatus::Aborted] {
        let h = Fixture::new(None).await;
        let run = h.finish_with_session_write_failure(status).await;
        assert_eq!(h.runtime.recover_state_machine_progression(None, 32).await.unwrap().scanned, 0);
        h.db.inner.execute(DbStatement::new("DELETE FROM bcs_collaboration_definitions")).await.unwrap();
        h.assert_recovered(&run).await;
    }
}

#[tokio::test]
async fn ordinary_v1_run_uses_the_same_terminal_session_recovery() {
    let h = Fixture::new(None).await;
    let started = h.runtime.start_state_machine_run(command(single_node_yaml(), false)).await.unwrap();
    let run = started.view.run;
    h.db.fail_completion.store(true, Ordering::SeqCst);
    assert!(send_terminal(&h.runtime, &h.delivery, 0, &run.session_id, "v1 final result").await.is_err());
    let saved = h.store.get_run(&run.run_id).await.unwrap().unwrap();
    assert_eq!(saved.status, StateMachineRunStatus::Completed);
    h.assert_recovered(&saved).await;
}

#[tokio::test]
async fn session_recovery_keeps_v2_gate_and_never_infers_missing_activation() {
    let h = Fixture::new(None).await;
    let run = h.finish_with_session_write_failure(StateMachineRunStatus::Completed).await;
    let disabled = CollaborationRuntime::new(h.store.clone(), h.store.clone(), h.store.clone(), h.store.clone(),
        Arc::new(GroupStore::new()), h.sessions.clone(), h.delivery.clone(), Arc::new(SequencedJudge::new(Vec::new())));
    let page = disabled.recover_state_machine_sessions(None, 32).await.unwrap();
    assert_eq!(page.completed, 0);
    assert!(page.failures[0].error.contains("v2 Session recovery is disabled"));
    h.db.inner.execute(DbStatement::new("UPDATE bcs_state_machine_runs SET session_activation_count = NULL")).await.unwrap();
    let chat = h.session_repo.create("group-1", NewSessionParams::default()).await.unwrap();
    let unrelated = h.session_repo.create("group-1", NewSessionParams { session_kind: SessionKind::ServiceInvocation, ..Default::default() }).await.unwrap();
    let page = h.runtime.recover_state_machine_sessions(None, 32).await.unwrap();
    assert_eq!(page.scanned, 2);
    assert_eq!(page.completed, 0);
    assert!(page.failures.is_empty());
    for id in [&run.session_id, &chat.id, &unrelated.id] {
        assert_eq!(h.sessions.get(id).await.unwrap().unwrap().status, SessionStatus::Running);
    }
    assert!(h.channel.terminal_events.lock().await.is_empty());
}

#[tokio::test]
async fn activation_race_and_stale_cancel_cannot_complete_or_notify_a_new_session() {
    let h = Fixture::new(None).await;
    let run = h.finish_with_session_write_failure(StateMachineRunStatus::Completed).await;
    h.db.advance_activation.store(true, Ordering::SeqCst);
    let page = h.runtime.recover_state_machine_sessions(None, 32).await.unwrap();
    assert!(page.failures.is_empty());
    assert_eq!(page.completed, 0);
    assert_eq!(h.runtime.recover_state_machine_sessions(None, 32).await.unwrap().completed, 0);
    h.runtime.cancel_state_machine_run(bcs_service_api::CancelStateMachineRunCommand {
        run_id: run.run_id.clone(), reason: None,
    }).await.unwrap();
    let session = h.sessions.get(&run.session_id).await.unwrap().unwrap();
    assert_eq!(session.status, SessionStatus::Running);
    assert_eq!(Some(session.activation_count - 1), run.session_activation_count);
    assert!(session.output.is_none());
    assert!(session.error_message.is_none());
    assert!(h.channel.terminal_events.lock().await.is_empty());
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn session_recovery_pages_advance_past_corrupt_snapshot_and_propagate_query_errors() {
    let h = Fixture::new(None).await;
    let mut runs = vec![h.finish_with_session_write_failure(StateMachineRunStatus::Failed).await,
        h.finish_with_session_write_failure(StateMachineRunStatus::Completed).await];
    runs.sort_by(|a, b| a.session_id.cmp(&b.session_id));
    h.db.inner.execute(DbStatement::with_params("DELETE FROM bcs_state_machine_definition_snapshots WHERE run_id = ?", vec![bcs_db_api::DbValue::from(runs[0].run_id.as_str())])).await.unwrap();
    h.db.fail_page.store(true, Ordering::SeqCst);
    assert!(h.runtime.recover_state_machine_sessions(None, 32).await.is_err());
    let page = h.runtime.recover_state_machine_sessions(None, 1).await.unwrap();
    assert_eq!(page.failures.len(), 1);
    assert_eq!(page.failures[0].session_id, runs[0].session_id);
    let next = h.runtime.recover_state_machine_sessions(page.next_session_id, 1).await.unwrap();
    assert!(next.failures.is_empty());
    assert_eq!(next.completed, 1);
    let end = h.runtime.recover_state_machine_sessions(next.next_session_id, 1).await.unwrap();
    assert_eq!(end.scanned, 0);
    assert_eq!(end.next_session_id, None);
    assert_eq!(h.sessions.get(&runs[0].session_id).await.unwrap().unwrap().status, SessionStatus::Running);
    assert_eq!(h.runtime.recover_state_machine_sessions(None, 0).await.unwrap().scanned, 0);
}

#[tokio::test]
async fn terminal_session_recovery_survives_process_restart() {
    if let Ok(path) = std::env::var("BCS_LOOP_SESSION_RECOVERY_DB") {
        let h = Fixture::new(Some(&path)).await;
        if std::env::var("BCS_LOOP_SESSION_RECOVERY_PHASE").unwrap() == "prepare" {
            let status: StateMachineRunStatus = serde_json::from_str(&std::env::var("BCS_LOOP_SESSION_RECOVERY_STATUS").unwrap()).unwrap();
            h.finish_with_session_write_failure(status).await;
            h.db.inner.execute(DbStatement::new("DELETE FROM bcs_collaboration_definitions")).await.unwrap();
        } else {
            let session = h.session_repo.list_running_service_after(None, 1).await.unwrap().remove(0);
            let run = h.store.get_run_by_session_id(&session.id).await.unwrap().unwrap();
            h.assert_recovered(&run).await;
        }
        return;
    }
    let dir = TempDatabaseDirectory(std::env::temp_dir().join(format!("bcs-loop-session-recovery-{}", uuid::Uuid::new_v4())));
    for status in [StateMachineRunStatus::Completed, StateMachineRunStatus::Failed, StateMachineRunStatus::Aborted] {
        let name = serde_json::to_string(&status).unwrap();
        for phase in ["prepare", "recover"] {
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "fixed_loop_tests::recovery_tests::sqlite_restart_tests::session_recovery_tests::terminal_session_recovery_survives_process_restart", "--nocapture"])
                .env("BCS_LOOP_SESSION_RECOVERY_DB", dir.0.join(format!("{name}.sqlite")))
                .env("BCS_LOOP_SESSION_RECOVERY_STATUS", &name).env("BCS_LOOP_SESSION_RECOVERY_PHASE", phase)
                .output().unwrap();
            assert!(output.status.success(), "{name}/{phase}: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        }
    }
}
