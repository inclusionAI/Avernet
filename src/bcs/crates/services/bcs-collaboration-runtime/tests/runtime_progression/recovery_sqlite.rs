use super::*;
use bcs_collaboration_store::MySqlCollaborationStore;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_message_store::MySqlMessageStore;
use bcs_session_store::MySqlSessionStore;

#[path = "../../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[path = "session_recovery.rs"]
mod session_recovery_tests;

async fn send_terminal(runtime: &CollaborationRuntime, delivery: &RecordingDelivery, index: usize, session: &str, text: &str) -> Result<bcs_service_api::HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
    let id = delivery.commands.lock().await[index].run_id.clone();
    runtime.handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
        bot_id: "driver-bot".into(), run_id: id.clone(), event_type: "chat.event".into(),
        event_payload: json!({"run_id": id, "state": "final", "message": {"content": [{"type": "text", "text": text}]}}),
        state: ChatEventState::Final, bcs_session_id: Some(session.into()),
    }).await
}

async fn child(path: &str, case: &str, prepare: bool) {
    let db = Arc::new(LocalSqliteDbPlugin::new_file(path).unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = Arc::new(MySqlCollaborationStore::sqlite(db.clone(), "test".into()));
    let runs = Arc::new(FaultyRuns::new(store.clone()));
    let sessions = Arc::new(SessionManagementServiceImpl::new(
        Arc::new(MySqlSessionStore::sqlite(db.clone(), "test".into())), Arc::new(MemoryGroupRepo::new()),
    ));
    let group = Arc::new(GroupStore::new());
    let mut group_value = test_group();
    if matches!(case, "opening_pending" | "opening_history") {
        group_value.group_strategy = GroupStrategy::StateMachine;
    }
    group_value.opening_message = Some(OpeningMessage::Text(if prepare { "original process opening {{bcs.run_id}}" } else { "changed process opening" }.into()));
    if matches!(case, "dispatch_pending" | "dispatch_unknown") { group_value.label = Some(if prepare { "original dispatch Group" } else { "changed dispatch Group" }.into()); }
    group.upsert(group_value).await.unwrap();
    let delivery = Arc::new(RecordingDelivery::default());
    let failure_case = matches!(case, "retry" | "failed");
    let decisions = if case == "judge" {
        if prepare { Vec::new() } else { vec![JudgeDecision {
            outcome: "again".into(), reason: "recovered saved input".into(), confidence: 1.0,
            checked_criteria: Vec::new(), retry_instruction: String::new(), raw_response: None,
        }] }
    } else if prepare && case != "finalize" && !failure_case { vec![JudgeDecision {
        outcome: if case == "break" { "done" } else { "again" }.into(), reason: "persisted decision".into(),
        confidence: 1.0, checked_criteria: Vec::new(), retry_instruction: String::new(), raw_response: None,
    }] } else { Vec::new() };
    let runtime = CollaborationRuntime::new(store.clone(), store.clone(), runs.clone(), store.clone(),
        group, sessions.clone(), delivery.clone(), Arc::new(SequencedJudge::new(decisions)))
        .with_message_repo(Arc::new(MySqlMessageStore::sqlite(db.clone(), "test".into())))
        .with_experimental_fixed_loop_execution();
    if prepare && matches!(case, "dispatch_pending" | "dispatch_unknown") {
        if case == "dispatch_pending" { runs.fail_dispatch_claim.store(true, Ordering::SeqCst); }
        else { runs.fail_dispatch_finish.store(true, Ordering::SeqCst); }
        assert!(runtime.start_state_machine_run(command(loop_yaml(3, false, false, 1), false)).await.is_err());
        assert_eq!(delivery.commands.lock().await.len(), if case == "dispatch_pending" { 0 } else { 1 });
        db.execute(DbStatement::new("DELETE FROM bcs_collaboration_definitions WHERE env = 'test'")).await.unwrap();
        return;
    }
    if prepare && matches!(case, "opening_pending" | "opening_history") {
        if case == "opening_pending" { runs.pause_start.store(true, Ordering::SeqCst); }
        else { runs.pause_opening_barrier.store(true, Ordering::SeqCst); }
        tokio::select! {
            result = runtime.start_state_machine_run(command(loop_yaml(3, false, false, 1), false)) => panic!("startup did not pause: {result:?}"),
            () = runs.startup_paused.notified() => {}
        }
        assert!(delivery.commands.lock().await.is_empty());
        db.execute(DbStatement::new("DELETE FROM bcs_collaboration_definitions WHERE env = 'test'")).await.unwrap();
        return;
    }
    if prepare {
        let count = if matches!(case, "exhausted" | "finalize") { 1 } else { 4 };
        let started = runtime.start_state_machine_run(command(loop_yaml(count, case != "finalize" && !failure_case, false, if case == "retry" { 3 } else { 1 }), false)).await.unwrap();
        let run = &started.view.run;
        match case {
            "judge" => runs.fail_judge_claim.store(true, Ordering::SeqCst),
            "retry" => runs.fail_retry.store(true, Ordering::SeqCst),
            "failed" => runs.fail_run_failure.store(true, Ordering::SeqCst),
            "break" => runs.fail_skip_after.store(2, Ordering::SeqCst),
            "finalize" => {
                send_terminal(&runtime, &delivery, 0, &run.session_id, "iteration-result").await.unwrap();
                runs.fail_finalize.store(true, Ordering::SeqCst);
            }
            _ => runs.fail_dispatch.store(true, Ordering::SeqCst),
        }
        let index = if case == "finalize" { 1 } else { 0 };
        if failure_case {
            let id = delivery.commands.lock().await[0].run_id.clone();
            assert!(runtime.handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
                bot_id: "driver-bot".into(), run_id: id.clone(), event_type: "chat.event".into(),
                event_payload: json!({"run_id": id, "state": "error", "message": {"content": [{"type": "text", "text": "durable-source-failure"}]}}),
                state: ChatEventState::Error, bcs_session_id: Some(run.session_id.clone()),
            }).await.is_err());
            let failed = store.list_node_runs(&run.run_id).await.unwrap().into_iter().find(|node| node.status == StateMachineNodeStatus::Failed).unwrap();
            let action = store.get_node_attempt_failure(&run.run_id, &failed.node_id, 0).await.unwrap().unwrap().action;
            assert_eq!(action, Some(if case == "retry" { bcs_service_api::StateMachineFailureAction::Retry } else { bcs_service_api::StateMachineFailureAction::FailRun }));
        } else {
            assert!(send_terminal(&runtime, &delivery, index, &run.session_id, "durable-source-result").await.is_err());
        }
        if case == "judge" {
            let node = store.list_node_runs(&run.run_id).await.unwrap().into_iter().find(|node| node.status == StateMachineNodeStatus::Running).unwrap();
            assert_eq!(node.artifact_text.as_deref(), Some("durable-source-result"));
            store.claim_node_judging(&run.run_id, &node.node_id, 0, "exited-process".into(), 1, 2).await.unwrap().unwrap();
        }
        assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
        // There will be no current Definition in the new process. Only the Run
        // snapshot may supply its execution graph and original IDs.
        db.execute(DbStatement::new("DELETE FROM bcs_collaboration_definitions WHERE env = 'test'")).await.unwrap();
    } else {
        let runtime = runtime.with_fixed_loop_limits(bcs_config_api::FixedLoopLimits { max_fixed_loop_iterations: 1, ..Default::default() });
        let run = if case == "opening_pending" { store.list_pending_runs(None, 1).await.unwrap().remove(0) }
            else { store.list_running_runs(None, 1).await.unwrap().remove(0) };
        let before: Vec<_> = store.list_node_runs(&run.run_id).await.unwrap().into_iter()
            .filter(|node| node.status == StateMachineNodeStatus::Completed).collect();
        let dispatch_before = if matches!(case, "dispatch_pending" | "dispatch_unknown") {
            let node = store.list_node_runs(&run.run_id).await.unwrap().into_iter().find(|node| node.status == StateMachineNodeStatus::Running).unwrap();
            Some(store.get_node_dispatch(&run.run_id, &node.node_id, node.attempt).await.unwrap().unwrap())
        } else { None };
        let page = runtime.recover_state_machine_progression(None, 32).await.unwrap();
        assert!(page.failures.is_empty(), "{:?}", page.failures);
        let again = runtime.recover_state_machine_progression(None, 32).await.unwrap();
        assert!(again.failures.is_empty());
        let commands = delivery.commands.lock().await;
        if let Some(before) = dispatch_before {
            assert_eq!(commands.len(), if case == "dispatch_pending" { 1 } else { 0 });
            if case == "dispatch_pending" { assert_eq!(serde_json::to_value(&commands[0].frame).unwrap(), before.payload.request); }
            let after = store.get_node_dispatch(&run.run_id, &before.payload.node_id, before.payload.attempt).await.unwrap().unwrap();
            assert_eq!(after.payload, before.payload);
            assert_eq!(after.status, if case == "dispatch_pending" { bcs_service_api::StateMachineDispatchStatus::Delivered } else { bcs_service_api::StateMachineDispatchStatus::Delivering });
        } else if matches!(case, "opening_pending" | "opening_history") {
            assert_eq!(commands.len(), 1);
            let opening = store.get_run_opening(&run.run_id).await.unwrap().unwrap();
            assert!(opening.delivered_at_ms.is_some());
            assert!(opening.payload.content.starts_with("original process opening"));
            let messages = MySqlMessageStore::sqlite(db.clone(), "test".into());
            assert_eq!(messages.get_current_seq(&run.session_id).await.unwrap(), 1);
            let saved = messages.get_message_by_id(&run.session_id, &opening.payload.client_msg_id).await.unwrap().unwrap();
            assert_eq!(saved.content["text"], opening.payload.content);
            assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
        } else if case == "failed" {
            assert!(commands.is_empty());
            let after = store.get_run(&run.run_id).await.unwrap().unwrap();
            assert_eq!(after.status, StateMachineRunStatus::Failed);
            assert_eq!(after.error.as_deref(), Some("durable-source-failure"));
            assert_eq!(sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Completed);
        } else if case == "retry" {
            assert_eq!(commands.len(), 1);
            let nodes = store.list_node_runs(&run.run_id).await.unwrap();
            let running = nodes.iter().filter(|node| node.status == StateMachineNodeStatus::Running).collect::<Vec<_>>();
            assert_eq!(running.len(), 1);
            assert_eq!(running[0].attempt, 1);
            let snapshot = store.get_run_snapshot(&run.run_id).await.unwrap().unwrap().execution_plan.unwrap();
            assert_eq!(snapshot.plan.node_metadata[&running[0].node_id].iteration, Some(1));
        } else if case == "finalize" {
            assert!(commands.is_empty());
            assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Completed);
        } else {
            assert_eq!(commands.len(), 1);
            let prompt = chat_send_params(&commands[0]).message.content.iter().filter_map(|block| block.text.as_deref()).collect::<Vec<_>>().join("\n");
            assert!(prompt.contains("durable-source-result"));
            match case {
                "continue" | "judge" => assert!(prompt.contains("iteration: 2")),
                "break" => assert!(prompt.contains("Publish.")),
                "exhausted" => assert!(prompt.contains("Resolve exhausted loop.")),
                _ => panic!("unknown restart case"),
            }
        }
        let after: Vec<_> = store.list_node_runs(&run.run_id).await.unwrap().into_iter()
            .filter(|node| node.status == StateMachineNodeStatus::Completed).collect();
        // Restart reads the original persisted output metadata; it never
        // reconstructs identity using today's compiler or Group configuration.
        let messages = MySqlMessageStore::sqlite(db.clone(), "test".into());
        let snapshot = store.get_run_snapshot(&run.run_id).await.unwrap().unwrap().execution_plan.unwrap();
        for node in &after {
            let saved = event_metadata_tests::persisted_output(&messages, &run, &node.node_id, node.attempt).await;
            assert!(saved.message_id.len() <= 64);
            assert_eq!(saved.content["text"].as_str(), node.artifact_text.as_deref());
            let meta = &snapshot.plan.node_metadata[&node.node_id];
            if let Some(iteration) = meta.iteration {
                assert_eq!(saved.content["metadata"]["state_machine"]["execution"], json!({
                    "definition_node_id": meta.definition_node_id, "loop_id": meta.loop_id,
                    "iteration": iteration, "max_iterations": meta.max_iterations,
                }));
            }
        }
        if case == "judge" {
            assert_eq!(after.len(), before.len() + 1);
            assert_eq!(after[0].attempt, 0);
            assert_eq!(after[0].artifact_text.as_deref(), Some("durable-source-result"));
            assert_eq!(after[0].outcome.as_deref(), Some("again"));
        } else {
            assert_eq!(serde_json::to_value(before).unwrap(), serde_json::to_value(after).unwrap());
        }
    }
}

#[tokio::test]
async fn output_history_fits_mysql_primary_key_and_recovers_legacy_sqlite_ids() {
    let mut h = Harness::new(&["again"]).await;
    let started = h.start(loop_yaml(2, true, false, 1), false).await;
    let run = &started.view.run;
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES (?, ?, 'test', '[]')",
        vec![run.session_id.clone().into(), run.group_id.clone().into()],
    )).await.unwrap();
    // SQLite normally accepts the oversized IDs that strict MySQL rejects.
    db.execute(DbStatement::new("CREATE TRIGGER enforce_mysql_message_id BEFORE INSERT ON bcs_messages \
        WHEN length(CAST(NEW.message_id AS BLOB)) > 64 BEGIN SELECT RAISE(ABORT, 'message_id exceeds 64 bytes'); END"))
        .await.unwrap();
    let messages = Arc::new(MySqlMessageStore::sqlite(db.clone(), "test".into()));
    h.runtime = h.runtime.with_message_repo(messages.clone());
    h.finish(0, run, "first iteration output").await;
    assert_eq!(h.delivery.commands.lock().await.len(), 2, "output persistence must allow the next iteration");
    let first = iteration_id(&h.plan(&run.run_id).await, 1);
    let saved = event_metadata_tests::persisted_output(messages.as_ref(), run, &first, 0).await;
    assert_eq!(saved.message_id.len(), 64);
    let legacy_id = format!("{}:{first}:0:1-output", run.run_id);
    assert!(legacy_id.len() > 64);
    assert_eq!(saved.client_msg_id.as_deref(), Some(legacy_id.as_str()));
    let seq = messages.get_current_seq(&run.session_id).await.unwrap();
    let recovered = h.runtime.recover_state_machine_progression(None, 10).await.unwrap();
    assert!(recovered.failures.is_empty(), "{:?}", recovered.failures);
    assert_eq!(messages.get_current_seq(&run.session_id).await.unwrap(), seq);

    // Model an output persisted by the previous SQLite implementation.
    db.execute(DbStatement::with_params("UPDATE bcs_messages SET message_id = ? WHERE message_id = ?",
        vec![legacy_id.clone().into(), saved.message_id.clone().into()])).await.unwrap();
    let history = h.runtime.get_state_machine_session_history(&run.session_id, 100, None).await.unwrap().unwrap();
    let output = history.messages.iter().find(|message| message.id == legacy_id).expect("original message identity");
    assert_eq!(output.metadata.as_ref(), Some(&saved.content["metadata"]));
    let recovered = h.runtime.recover_state_machine_progression(None, 10).await.unwrap();
    assert!(recovered.failures.is_empty(), "{:?}", recovered.failures);
    assert_eq!(messages.get_current_seq(&run.session_id).await.unwrap(), seq, "recovery must reuse the legacy client key");
    assert!(messages.get_message_by_id(&run.session_id, &saved.message_id).await.unwrap().is_none());
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
}

struct TempDatabaseDirectory(std::path::PathBuf);
impl Drop for TempDatabaseDirectory {
    fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
}

#[tokio::test]
async fn sqlite_process_restart_recovers_all_committed_progression_windows() {
    if let Ok(path) = std::env::var("BCS_LOOP_RECOVERY_TEST_DB") {
        child(&path, &std::env::var("BCS_LOOP_RECOVERY_TEST_CASE").unwrap(),
            std::env::var("BCS_LOOP_RECOVERY_TEST_PHASE").unwrap() == "prepare").await;
        return;
    }
    let directory = TempDatabaseDirectory(std::env::temp_dir().join(format!("bcs-loop-recovery-{}", uuid::Uuid::new_v4())));
    for case in ["continue", "break", "exhausted", "finalize", "retry", "failed", "judge", "opening_pending", "opening_history", "dispatch_pending", "dispatch_unknown"] {
        for phase in ["prepare", "recover"] {
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "fixed_loop_tests::recovery_tests::sqlite_restart_tests::sqlite_process_restart_recovers_all_committed_progression_windows", "--nocapture"])
                .env("BCS_LOOP_RECOVERY_TEST_DB", directory.0.join(format!("{case}.sqlite")))
                .env("BCS_LOOP_RECOVERY_TEST_CASE", case).env("BCS_LOOP_RECOVERY_TEST_PHASE", phase)
                .output().unwrap();
            assert!(output.status.success(), "{case}/{phase}: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        }
    }
}

#[path = "publication_sqlite.rs"]
mod publication_tests;

#[path = "terminal_im_sqlite.rs"]
mod terminal_im_tests;

#[path = "recovery_gap_sqlite.rs"]
mod recovery_gap_tests;

#[path = "terminal_cleanup_sqlite.rs"]
mod terminal_cleanup_tests;

#[path = "human_notification_sqlite.rs"]
mod human_notification_tests;
