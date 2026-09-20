use super::*;
use bcs_db_api::DbValue;
use bcs_service_api::StateMachineChatResultStatus as Status;

struct PersistedPublisher { db: Arc<LocalSqliteDbPlugin>, reject: bool }
#[async_trait]
impl StateMachineResultPublisherPort for PersistedPublisher {
    async fn publish_state_machine_result(&self, cmd: StateMachineResultPublishCommand) -> ServiceResult<()> {
        self.db.execute(DbStatement::with_params("INSERT INTO test_publications (payload) VALUES (?)",
            vec![DbValue::from(serde_json::to_string(&cmd).unwrap())])).await.unwrap();
        if self.reject { Err(ServiceError::InternalError("saved publisher failure".into())) } else { Ok(()) }
    }
}

async fn publication_child(path: &str, case: &str, prepare: bool) {
    let db = Arc::new(LocalSqliteDbPlugin::new_file(path).unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("CREATE TABLE IF NOT EXISTS test_publications (payload TEXT NOT NULL)")).await.unwrap();
    let store = Arc::new(MySqlCollaborationStore::sqlite(db.clone(), "test".into()));
    let runs = Arc::new(FaultyRuns::new(store.clone()));
    let sessions = Arc::new(SessionManagementServiceImpl::new(Arc::new(MySqlSessionStore::sqlite(db.clone(), "test".into())), Arc::new(MemoryGroupRepo::new())));
    let group = Arc::new(GroupStore::new()); let mut original = test_group();
    original.label = Some(if prepare { "original Group" } else { "changed Group" }.into());
    group.upsert(original).await.unwrap();
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(store.clone(), store.clone(), runs.clone(), store.clone(), group,
        sessions.clone(), delivery.clone(), noop_judge()).with_loop_execution()
        .with_message_repo(Arc::new(MySqlMessageStore::sqlite(db.clone(), "test".into())))
        .with_result_publisher(Arc::new(PersistedPublisher { db: db.clone(), reject: case == "failed" }));
    if prepare {
        let session = sessions.create_or_reactivate(CreateOrReactivateCommand {
            group_id: "group-1".into(), session_id: None,
            params: NewSessionParams { session_kind: SessionKind::Chat, participants: test_group().participants, ..Default::default() },
        }).await.unwrap().session;
        let mut cmd = command(single_node_yaml(), false); cmd.session_id = Some(session.id); cmd.caller_id = Some("driver-bot".into());
        let run = runtime.start_state_machine_run(cmd).await.unwrap().view.run;
        match case {
            "pending" => &runs.fail_publication_claim,
            "unknown" => &runs.fail_publication_finish,
            "failed" => &runs.fail_run_failure,
            _ => &runs.fail_finalize,
        }.store(true, Ordering::SeqCst);
        assert!(send_terminal(&runtime, &delivery, 0, &run.session_id, "跨进程原始结果").await.is_err());
        db.execute(DbStatement::new("DELETE FROM bcs_collaboration_definitions WHERE env = 'test'")).await.unwrap();
    } else {
        let run = store.list_running_runs(None, 10).await.unwrap().remove(0);
        let before = store.get_chat_result(&run.run_id).await.unwrap().unwrap();
        let page = runtime.recover_state_machine_progression(None, 10).await.unwrap();
        assert!(page.failures.is_empty(), "{:?}", page.failures);
        if case == "unknown" {
            assert_eq!(before.status, Status::Delivering);
            assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
            store.expire_chat_result(&run.run_id, before.payload.deadline_ms).await.unwrap();
        }
        let page = runtime.recover_state_machine_progression(None, 10).await.unwrap();
        assert!(page.failures.is_empty(), "{:?}", page.failures);
        let status = if matches!(case, "unknown" | "failed") { StateMachineRunStatus::Failed } else { StateMachineRunStatus::Completed };
        assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, status);
        assert_eq!(sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Running);
        let calls = db.query(DbStatement::new("SELECT payload FROM test_publications")).await.unwrap();
        assert_eq!(calls.len(), 1, "publisher must not be replayed after restart");
        let payload: StateMachineResultPublishCommand = serde_json::from_str(&bcs_db_api::db_get_column::<String>(&calls[0], "payload").unwrap()).unwrap();
        assert_eq!(payload, before.payload.command);
        assert_eq!(payload.content, "跨进程原始结果");
        assert!(delivery.commands.lock().await.is_empty());
    }
}

#[tokio::test]
async fn sqlite_process_restart_recovers_chat_publication_windows() {
    if let Ok(path) = std::env::var("BCS_CHAT_RESULT_TEST_DB") {
        publication_child(&path, &std::env::var("BCS_CHAT_RESULT_TEST_CASE").unwrap(),
            std::env::var("BCS_CHAT_RESULT_TEST_PHASE").unwrap() == "prepare").await;
        return;
    }
    let directory = TempDatabaseDirectory(std::env::temp_dir().join(format!("bcs-chat-publication-{}", uuid::Uuid::new_v4())));
    for case in ["pending", "delivered", "unknown", "failed"] {
        for phase in ["prepare", "recover"] {
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "fixed_loop_tests::recovery_tests::sqlite_restart_tests::publication_tests::sqlite_process_restart_recovers_chat_publication_windows", "--nocapture"])
                .env("BCS_CHAT_RESULT_TEST_DB", directory.0.join(format!("{case}.sqlite")))
                .env("BCS_CHAT_RESULT_TEST_CASE", case).env("BCS_CHAT_RESULT_TEST_PHASE", phase).output().unwrap();
            assert!(output.status.success(), "{case}/{phase}: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        }
    }
}
