use super::*;
use bcs_db_api::DbValue;
use bcs_service_api::{StateMachineTerminalEvent, StateMachineTerminalNotification, StateMachineTerminalImStatus as Status};

struct DurableTerminalChannel { db: Arc<LocalSqliteDbPlugin>, preparing: bool }
#[async_trait]
impl SessionChannelOutboundPort for DurableTerminalChannel {
    async fn publish_human_input_ready(&self, _: HumanInputReadyEvent) -> ServiceResult<SessionChannelDeliveryOutcome> { Ok(SessionChannelDeliveryOutcome::Delivered) }
    async fn prepare_state_machine_terminal(&self, event: &StateMachineTerminalEvent) -> ServiceResult<Vec<StateMachineTerminalNotification>> {
        // If already saved, a restarted process must never consult fresh rendering.
        let text = if self.preparing { "原始 IM 文本" } else { "重新准备的 IM 文本" };
        Ok((0..2).map(|i| StateMachineTerminalNotification { binding_id: "binding".into(), channel_type: "test-im".into(), account_ref: "account".into(),
            im_conversation_id: format!("destination-{i}"), im_conversation_type: "2".into(), im_user_id: None,
            request_id: format!("request-{i}"), node_id: format!("node-{i}"), text: format!("{text}:{}", event.workflow_name) }).collect())
    }
    async fn validate_terminal_notification(&self, _: &StateMachineTerminalNotification) -> ServiceResult<()> { Ok(()) }
    async fn deliver_terminal_notification(&self, _: &StateMachineTerminalEvent, n: &StateMachineTerminalNotification) -> ServiceResult<Option<String>> {
        self.db.execute(DbStatement::with_params("INSERT INTO test_terminal_sends (destination, text) VALUES (?, ?)",
            vec![DbValue::from(n.im_conversation_id.as_str()), DbValue::from(n.text.as_str())])).await.unwrap();
        Ok(Some(format!("remote-{}", n.im_conversation_id)))
    }
}

async fn terminal_child(path: &str, case: &str, prepare: bool) {
    let db = Arc::new(LocalSqliteDbPlugin::new_file(path).unwrap()); bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("CREATE TABLE IF NOT EXISTS test_terminal_sends (destination TEXT NOT NULL, text TEXT NOT NULL)")).await.unwrap();
    let store = Arc::new(MySqlCollaborationStore::sqlite(db.clone(), "test".into()));
    let runs = Arc::new(FaultyRuns::new(store.clone()));
    let sessions = Arc::new(SessionManagementServiceImpl::new(Arc::new(MySqlSessionStore::sqlite(db.clone(), "test".into())), Arc::new(MemoryGroupRepo::new())));
    let group = Arc::new(GroupStore::new()); group.upsert(test_group()).await.unwrap();
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(store.clone(), store.clone(), runs.clone(), store.clone(), group,
        sessions.clone(), delivery.clone(), noop_judge()).with_loop_execution()
        .with_message_repo(Arc::new(MySqlMessageStore::sqlite(db.clone(), "test".into())))
        .with_session_channel_outbound(Arc::new(DurableTerminalChannel { db: db.clone(), preparing: prepare }));
    if prepare {
        let run = runtime.start_state_machine_run(command(single_node_yaml().replace("max_attempts: 3", "max_attempts: 1"), false)).await.unwrap().view.run;
        if case != "delivered" {
            match case { "before_session" => &runs.fail_im_save, "unknown" => &runs.fail_im_ack,
                _ => &runs.fail_im_claim }.store(true, Ordering::SeqCst);
        }
        let result = if case == "failed" {
            let id = delivery.commands.lock().await[0].run_id.clone();
            runtime.handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand { bot_id: "driver-bot".into(), run_id: id.clone(), event_type: "chat.event".into(),
                event_payload: json!({"run_id": id, "state": "error", "error": "original failure"}), state: ChatEventState::Error, bcs_session_id: Some(run.session_id.clone()) }).await
        } else { send_terminal(&runtime, &delivery, 0, &run.session_id, "原始 Run 输出").await };
        assert_eq!(result.is_ok(), case == "delivered");
        if case == "expired" {
            // Move all original timestamp facts together, simulating a restart
            // after the deadline without a slow wall-clock sleep.
            let mut saved = store.get_terminal_im(&run.run_id).await.unwrap().unwrap().payload;
            saved.created_at_ms = 100; saved.deadline_ms = 200;
            let progress = bcs_service_api::StateMachineTerminalImProgress { cleanup_completed: false,
                deliveries: saved.notifications.iter().map(|_| bcs_service_api::StateMachineTerminalImDelivery::Pending { next_attempt_at_ms: 100, last_error: None }).collect() };
            db.execute(DbStatement::with_params("UPDATE bcs_state_machine_runs SET completed_at_ms = 100 WHERE run_id = ?", vec![DbValue::from(run.run_id.as_str())])).await.unwrap();
            db.execute(DbStatement::with_params("UPDATE bcs_collaboration_delivery_checkpoints SET payload_json = ?, progress_json = ?, created_at_ms = 100, deadline_ms = 200 WHERE operation_kind = 'im_terminal'",
                vec![DbValue::from(serde_json::to_string(&saved).unwrap()), DbValue::from(serde_json::to_string(&progress).unwrap())])).await.unwrap();
        }
        db.execute(DbStatement::new("DELETE FROM bcs_collaboration_definitions WHERE env = 'test'")).await.unwrap();
    } else {
        let rows = db.query(DbStatement::new("SELECT run_id FROM bcs_state_machine_runs WHERE env = 'test'")).await.unwrap();
        let id: String = bcs_db_api::db_get_column(&rows[0], "run_id").unwrap();
        let before = store.get_run(&id).await.unwrap().unwrap();
        assert_eq!(before.status, if case == "failed" { StateMachineRunStatus::Failed } else { StateMachineRunStatus::Completed });
        let sessions_page = runtime.recover_state_machine_sessions(None, 10).await.unwrap(); assert!(sessions_page.failures.is_empty(), "{:?}", sessions_page.failures);
        for _ in 0..2 {
            let page = runtime.recover_state_machine_terminal_im(None, 10).await.unwrap(); assert!(page.failures.is_empty(), "{:?}", page.failures);
        }
        let saved = store.get_terminal_im(&id).await.unwrap().unwrap();
        assert_eq!(saved.status, if matches!(case, "unknown" | "expired") { Status::Failed } else { Status::Delivered });
        assert_eq!(store.get_run(&id).await.unwrap().unwrap().status, before.status);
        assert_eq!(store.get_run(&id).await.unwrap().unwrap().output, before.output);
        let calls = db.query(DbStatement::new("SELECT destination, text FROM test_terminal_sends ORDER BY destination")).await.unwrap();
        assert_eq!(calls.len(), if case == "expired" { 0 } else { 2 });
        for (i, call) in calls.iter().enumerate() {
            assert_eq!(bcs_db_api::db_get_column::<String>(call, "destination").unwrap(), saved.payload.notifications[i].im_conversation_id);
            assert_eq!(bcs_db_api::db_get_column::<String>(call, "text").unwrap(), saved.payload.notifications[i].text);
        }
        assert_eq!(sessions.get(&before.session_id).await.unwrap().unwrap().status, SessionStatus::Completed);
        assert!(delivery.commands.lock().await.is_empty());
    }
}

#[tokio::test]
async fn sqlite_process_restart_recovers_terminal_im_windows() {
    if let Ok(path) = std::env::var("BCS_TERMINAL_IM_TEST_DB") {
        terminal_child(&path, &std::env::var("BCS_TERMINAL_IM_TEST_CASE").unwrap(), std::env::var("BCS_TERMINAL_IM_TEST_PHASE").unwrap() == "prepare").await;
        return;
    }
    let directory = TempDatabaseDirectory(std::env::temp_dir().join(format!("bcs-terminal-im-{}", uuid::Uuid::new_v4())));
    for case in ["before_session", "pending", "unknown", "delivered", "failed", "expired"] {
        for phase in ["prepare", "recover"] {
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "fixed_loop_tests::recovery_tests::sqlite_restart_tests::terminal_im_tests::sqlite_process_restart_recovers_terminal_im_windows", "--nocapture"])
                .env("BCS_TERMINAL_IM_TEST_DB", directory.0.join(format!("{case}.sqlite")))
                .env("BCS_TERMINAL_IM_TEST_CASE", case).env("BCS_TERMINAL_IM_TEST_PHASE", phase).output().unwrap();
            assert!(output.status.success(), "{case}/{phase}: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        }
    }
}
