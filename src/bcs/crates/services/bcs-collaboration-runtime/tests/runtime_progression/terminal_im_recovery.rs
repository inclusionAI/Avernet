use super::*;
use bcs_service_api::{StateMachineTerminalEvent, StateMachineTerminalNotification, StateMachineTerminalImStatus as Status,
    StateMachineTerminalImDelivery as Delivery};

#[derive(Default)]
struct TerminalChannel {
    calls: Mutex<Vec<StateMachineTerminalNotification>>, prepares: AtomicUsize,
    unavailable: AtomicBool, pause: AtomicBool, entered: Notify, resume: Notify,
}
#[async_trait]
impl SessionChannelOutboundPort for TerminalChannel {
    async fn publish_human_input_ready(&self, _: HumanInputReadyEvent) -> ServiceResult<SessionChannelDeliveryOutcome> { Ok(SessionChannelDeliveryOutcome::Delivered) }
    async fn prepare_state_machine_terminal(&self, _: &StateMachineTerminalEvent) -> ServiceResult<Vec<StateMachineTerminalNotification>> {
        self.prepares.fetch_add(1, Ordering::SeqCst);
        Ok((0..2).map(|index| notification(index, "原终态通知")).collect())
    }
    async fn validate_terminal_notification(&self, n: &StateMachineTerminalNotification) -> ServiceResult<()> {
        if n.im_conversation_id == "conversation-1" && self.unavailable.load(Ordering::SeqCst) { return Err(injected()); }
        Ok(())
    }
    async fn deliver_terminal_notification(&self, _: &StateMachineTerminalEvent, n: &StateMachineTerminalNotification) -> ServiceResult<Option<String>> {
        self.calls.lock().await.push(n.clone());
        if self.pause.load(Ordering::SeqCst) { self.entered.notify_one(); self.resume.notified().await; }
        Ok(Some(format!("message-{}", n.im_conversation_id)))
    }
}
fn notification(index: usize, text: &str) -> StateMachineTerminalNotification {
    StateMachineTerminalNotification { binding_id: "binding".into(), channel_type: "test-im".into(), account_ref: "account".into(),
        im_conversation_id: format!("conversation-{index}"), im_conversation_type: "2".into(), im_user_id: None,
        request_id: format!("request-{index}"), node_id: format!("node-{index}"), text: text.into() }
}
async fn recover_im(h: &Harness) {
    let page = h.runtime.recover_state_machine_terminal_im(None, 32).await.unwrap();
    assert!(page.failures.is_empty(), "{:?}", page.failures);
}

#[tokio::test]
async fn terminal_im_recovers_persistence_windows_without_changing_terminal_run() {
    for stage in ["save", "claim", "marker", "ack"] {
        let (mut h, runs) = harness(&[]).await;
        let channel = Arc::new(TerminalChannel::default()); h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
        let run = h.start(single_node_yaml(), false).await.view.run;
        match stage { "save" => &runs.fail_im_save, "claim" => &runs.fail_im_claim, "marker" => &runs.fail_im_marker, _ => &runs.fail_im_ack }.store(true, Ordering::SeqCst);
        assert!(terminal(&h, 0, &run, "original final output").await.is_err());
        assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Completed);
        assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, if stage == "save" { SessionStatus::Running } else { SessionStatus::Completed });
        h.definitions.hide_definitions.store(true, Ordering::SeqCst);
        install_runtime(&mut h, runs, &[]).await; h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
        let page = h.runtime.recover_state_machine_sessions(None, 32).await.unwrap(); assert!(page.failures.is_empty(), "{:?}", page.failures);
        recover_im(&h).await; recover_im(&h).await;
        let saved = h.store.get_terminal_im(&run.run_id).await.unwrap().unwrap();
        assert_eq!(saved.status, if stage == "ack" { Status::Failed } else { Status::Delivered });
        assert_eq!(saved.payload.event.output.as_deref(), Some("original final output"));
        assert_eq!(channel.calls.lock().await.len(), 2); // An unacknowledged target is never resent.
        assert!(channel.calls.lock().await.iter().all(|n| n.text == "原终态通知"));
        assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Completed);
        assert_eq!(h.delivery.commands.lock().await.len(), 1);
    }
}

#[tokio::test]
async fn terminal_im_retries_only_unsent_recipient_from_frozen_plan() {
    let (mut h, _) = harness(&[]).await; let channel = Arc::new(TerminalChannel::default());
    channel.unavailable.store(true, Ordering::SeqCst); h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
    let run = h.start(single_node_yaml(), false).await.view.run;
    terminal(&h, 0, &run, "result").await.unwrap();
    let saved = h.store.get_terminal_im(&run.run_id).await.unwrap().unwrap();
    assert_eq!(saved.status, Status::Pending);
    assert!(matches!(saved.progress.deliveries[0], Delivery::Delivered { .. }));
    assert!(matches!(saved.progress.deliveries[1], Delivery::Pending { .. }));
    assert_eq!(channel.calls.lock().await.len(), 1);
    channel.unavailable.store(false, Ordering::SeqCst);
    tokio::time::sleep(Duration::from_millis(1_010)).await;
    recover_im(&h).await; recover_im(&h).await;
    assert_eq!(channel.calls.lock().await.len(), 2); assert_eq!(channel.prepares.load(Ordering::SeqCst), 1);
    assert_eq!(h.store.get_terminal_im(&run.run_id).await.unwrap().unwrap().status, Status::Delivered);
}

#[tokio::test]
async fn terminal_im_concurrent_recovery_and_new_activation_reject_old_ack() {
    let (mut h, _) = harness(&[]).await; let channel = Arc::new(TerminalChannel::default());
    channel.pause.store(true, Ordering::SeqCst); h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
    let run = h.start(single_node_yaml(), false).await.view.run;
    let foreground = terminal(&h, 0, &run, "result"); tokio::pin!(foreground);
    tokio::select! { r = &mut foreground => panic!("unexpected completion: {r:?}"), () = channel.entered.notified() => {} }
    recover_im(&h).await; assert_eq!(channel.calls.lock().await.len(), 1);
    h.sessions.update_callback_status(&run.session_id, "not_applicable").await.unwrap();
    h.sessions.create_or_reactivate(CreateOrReactivateCommand { group_id: run.group_id.clone(), session_id: Some(run.session_id.clone()),
        params: NewSessionParams { session_kind: SessionKind::ServiceInvocation, ..Default::default() } }).await.unwrap();
    channel.resume.notify_one(); foreground.await.unwrap();
    recover_im(&h).await;
    assert_eq!(h.store.get_terminal_im(&run.run_id).await.unwrap().unwrap().status, Status::Superseded);
    assert_eq!(channel.calls.lock().await.len(), 1);
    assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Running);
}
