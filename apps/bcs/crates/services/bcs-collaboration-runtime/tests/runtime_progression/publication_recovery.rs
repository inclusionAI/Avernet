use super::*;
use bcs_service_api::{StateMachineChatResultStatus as Status, CancelStateMachineRunCommand};

async fn start_chat(h: &Harness, yaml: String) -> StateMachineRun {
    let session = h.sessions.create_or_reactivate(CreateOrReactivateCommand {
        group_id: "group-1".into(), session_id: None,
        params: NewSessionParams { session_kind: SessionKind::Chat, participants: test_group().participants, ..Default::default() },
    }).await.unwrap().session;
    let mut start = command(yaml, false); start.session_id = Some(session.id); start.caller_id = Some("driver-bot".into());
    h.runtime.start_state_machine_run(start).await.unwrap().view.run
}

#[tokio::test]
async fn chat_publication_write_failures_before_send_recover_original_output() {
    for stage in ["save", "claim", "marker"] {
        let (mut h, runs) = harness(&[]).await;
        let publisher = Arc::new(RecordingResultPublisher::default());
        h.runtime = h.runtime.with_result_publisher(publisher.clone());
        let run = start_chat(&h, loop_yaml(1, false, false, 1)).await;
        terminal(&h, 0, &run, "iteration output").await.unwrap();
        match stage {
            "save" => &runs.fail_publication_save, "claim" => &runs.fail_publication_claim,
            _ => &runs.fail_publication_marker,
        }.store(true, Ordering::SeqCst);
        assert!(terminal(&h, 1, &run, "original final result").await.is_err());
        assert!(publisher.commands.lock().await.is_empty());
        assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
        h.definitions.hide_definitions.store(true, Ordering::SeqCst);
        install_runtime(&mut h, runs, &[]).await;
        h.runtime = h.runtime.with_result_publisher(publisher.clone());
        recover(&h).await; recover(&h).await;
        assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Completed);
        let commands = publisher.commands.lock().await;
        assert_eq!(commands.len(), 1); assert_eq!(commands[0].content, "original final result");
        assert_eq!(commands[0].session_id, run.session_id); assert_eq!(commands[0].sender_bot_id, "driver-bot");
        assert_eq!(h.store.get_chat_result(&run.run_id).await.unwrap().unwrap().payload.command, commands[0]);
        assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Running);
        assert_eq!(h.delivery.commands.lock().await.len(), 2);
    }
}

#[tokio::test]
async fn chat_publication_lost_ack_does_not_resend_and_fails_at_original_deadline() {
    let (mut h, runs) = harness(&[]).await;
    let publisher = Arc::new(RecordingResultPublisher::default());
    h.runtime = h.runtime.with_result_publisher(publisher.clone());
    let run = start_chat(&h, single_node_yaml()).await;
    runs.fail_publication_finish.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &run, "may already be visible").await.is_err());
    let saved = h.store.get_chat_result(&run.run_id).await.unwrap().unwrap();
    assert_eq!(saved.status, Status::Delivering); assert!(saved.lease_owner.is_none());
    install_runtime(&mut h, runs, &[]).await;
    h.runtime = h.runtime.with_result_publisher(publisher.clone());
    recover(&h).await;
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
    assert!(h.store.expire_chat_result(&run.run_id, saved.payload.deadline_ms).await.unwrap());
    recover(&h).await; recover(&h).await;
    let failed = h.store.get_run(&run.run_id).await.unwrap().unwrap();
    assert_eq!(failed.status, StateMachineRunStatus::Failed);
    assert!(failed.error.unwrap().contains("delivery may be unknown"));
    assert_eq!(publisher.commands.lock().await.len(), 1);
    assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Running);
}

#[tokio::test]
async fn chat_publication_saved_failure_recovers_without_calling_publisher_again() {
    let (mut h, runs) = harness(&[]).await;
    h.runtime = h.runtime.with_result_publisher(Arc::new(FailingResultPublisher));
    let run = start_chat(&h, single_node_yaml()).await;
    runs.fail_run_failure.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &run, "original result").await.is_err());
    assert_eq!(h.store.get_chat_result(&run.run_id).await.unwrap().unwrap().status, Status::Failed);
    let publisher = Arc::new(RecordingResultPublisher::default());
    install_runtime(&mut h, runs, &[]).await;
    h.runtime = h.runtime.with_result_publisher(publisher.clone());
    recover(&h).await;
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Failed);
    assert!(publisher.commands.lock().await.is_empty());
}

struct PausedPublisher { entered: Notify, proceed: Notify, calls: AtomicUsize }
#[async_trait]
impl StateMachineResultPublisherPort for PausedPublisher {
    async fn publish_state_machine_result(&self, _: StateMachineResultPublishCommand) -> ServiceResult<()> {
        self.calls.fetch_add(1, Ordering::SeqCst); self.entered.notify_one();
        self.proceed.notified().await; Ok(())
    }
}

#[tokio::test]
async fn chat_publication_concurrent_recovery_and_cancel_fence_late_ack() {
    let (mut h, _) = harness(&[]).await;
    let publisher = Arc::new(PausedPublisher { entered: Notify::new(), proceed: Notify::new(), calls: AtomicUsize::new(0) });
    h.runtime = h.runtime.with_result_publisher(publisher.clone());
    let run = start_chat(&h, single_node_yaml()).await;
    let foreground = terminal(&h, 0, &run, "cancel race"); tokio::pin!(foreground);
    tokio::select! { result = &mut foreground => panic!("unexpected completion: {result:?}"), () = publisher.entered.notified() => {} }
    recover(&h).await;
    assert_eq!(publisher.calls.load(Ordering::SeqCst), 1);
    h.runtime.cancel_state_machine_run(CancelStateMachineRunCommand { run_id: run.run_id.clone(), reason: None }).await.unwrap();
    publisher.proceed.notify_one(); foreground.await.unwrap();
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Aborted);
    assert_ne!(h.store.get_chat_result(&run.run_id).await.unwrap().unwrap().status, Status::Delivered);
    recover(&h).await;
    assert_eq!(publisher.calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn chat_publication_two_recoverers_send_pending_result_once() {
    let (mut h, runs) = harness(&[]).await;
    let publisher = Arc::new(RecordingResultPublisher::default());
    h.runtime = h.runtime.with_result_publisher(publisher.clone());
    let run = start_chat(&h, single_node_yaml()).await;
    runs.fail_publication_claim.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &run, "single publication").await.is_err());
    let (a, b) = tokio::join!(h.runtime.recover_state_machine_progression(None, 32), h.runtime.recover_state_machine_progression(None, 32));
    assert!(a.unwrap().failures.is_empty()); assert!(b.unwrap().failures.is_empty());
    assert_eq!(publisher.commands.lock().await.len(), 1);
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn aborted_terminal_result_survives_terminal_cleanup_recovery() {
    let mut h = Harness::new(&[]).await;
    let started = start_chat(&h, single_node_yaml()).await;
    let mut run = started.clone();
    run.run_id = "aborted-cleanup-recovery".into();
    run.status = StateMachineRunStatus::Aborted;
    run.created_by = Some("driver-bot".into());
    run.output = None;
    run.error = Some("user cancellation".into());
    run.completed_at = Some(100);
    run.created_at = 1;
    run.updated_at = 100;
    StateMachineRunRepoPort::create_run(&*h.store, run.clone(), Vec::new())
        .await
        .unwrap();
    let sender_bot_id = run.created_by.clone().expect("run creator");
    let created_at_ms = bcs_protocol::now_ms();
    let terminal_result = bcs_service_api::StateMachineRunResultPayload::aborted(
        run.run_id.clone(),
        run.group_id.clone(),
        run.session_id.clone(),
        "single_node".into(),
        sender_bot_id.clone(),
        bcs_service_api::StateMachineTerminalReason::UserAborted,
        Vec::new(),
        created_at_ms,
    );
    let payload = bcs_service_api::StateMachineChatResultPayload {
        command: StateMachineResultPublishCommand {
            run_id: run.run_id.clone(),
            group_id: run.group_id.clone(),
            session_id: run.session_id.clone(),
            sender_bot_id,
            content: terminal_result.render_manager_message(),
            terminal_result: Some(terminal_result),
            created_at_ms,
        },
        deadline_ms: created_at_ms + 90_000,
    };
    assert!(h.store.save_chat_result(payload.clone()).await.unwrap());
    let publisher = Arc::new(RecordingResultPublisher::default());
    h.runtime = h.runtime.with_result_publisher(publisher.clone());

    h.runtime
        .cleanup_state_machine_terminal_work(None, 32)
        .await
        .unwrap();

    let saved = h.store.get_chat_result(&run.run_id).await.unwrap().unwrap();
    assert_eq!(saved.status, Status::Delivered);
    assert_eq!(saved.payload, payload);
    assert_eq!(publisher.commands.lock().await.len(), 1);
    assert!(publisher.commands.lock().await[0].terminal_result.is_some());
}

#[tokio::test]
async fn chat_disconnect_persists_structured_terminal_result_once() {
    let h = Harness::new(&[]).await;
    let run = start_chat(&h, single_node_yaml()).await;

    assert!(h.runtime.handle_bot_disconnect("driver-bot").await.unwrap() >= 1);
    let checkpoint = h.store.get_chat_result(&run.run_id).await.unwrap().expect("terminal result checkpoint");
    let terminal_result = checkpoint.payload.command.terminal_result.as_ref().expect("structured terminal result");
    assert_eq!(terminal_result.status, bcs_service_api::StateMachineRunTerminalStatus::Failed);
    assert_eq!(terminal_result.reason, bcs_service_api::StateMachineTerminalReason::NodeFailed);
    assert!(!checkpoint.payload.command.content.contains("disconnected"));

    assert_eq!(h.runtime.handle_bot_disconnect("driver-bot").await.unwrap(), 0);
    assert_eq!(h.store.get_chat_result(&run.run_id).await.unwrap().unwrap().payload, checkpoint.payload);
}
