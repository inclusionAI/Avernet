//! Fault-injection and replay coverage for a StateMachine Run that fails while
//! a player Bot owns the current turn (issue #2610).
//!
//! The happy-path recovery stories live in `failure_recovery.rs` and the
//! terminal-notification restart windows in `terminal_im_recovery.rs`. This
//! module only adds the adversarial paths that a "notify the manager, then
//! retry" implementation is expected to get right:
//!
//! | test | #2610 acceptance criterion |
//! | --- | --- |
//! | `heartbeat_loss_fails_run_and_stops_the_turn` | a player Bot that dies without any message still moves the Run to `failed` |
//! | `restart_between_failed_cas_and_notification_delivers_once` | the failed result is durable and idempotent across a process restart |
//! | `replayed_failure_event_is_consumed_without_a_second_effect` | duplicated / reordered failure events are processed once |
//! | `late_artifact_after_failure_cannot_advance_or_write_chat` | a late artifact for a terminal Run is inert |
//! | `user_aborted_run_is_never_retried` | `aborted` is terminal: no automatic retry of the manager |
//!
//! The harness is the shared `recovery.rs` fixture: `FaultyRuns` injects write
//! failures at exact checkpoints, `install_runtime` rebuilds the runtime to
//! simulate a process restart, and no production code is modified.
//!
//! `DeliveryChannel` is the notification double. It freezes one real recipient
//! in `prepare_state_machine_terminal` and records every
//! `deliver_terminal_notification` the runtime actually performs. The shared
//! `RecordingSessionChannelOutbound::terminal_events` recorder only observes
//! `finish_state_machine_terminal`, which is the post-completion cleanup
//! callback, so it cannot prove that a manager notification was sent.

use super::*;
use std::sync::atomic::Ordering;
use bcs_service_api::{
    CancelStateMachineRunCommand, StateMachineTerminalEvent,
    StateMachineTerminalImDelivery, StateMachineTerminalNotification,
    StateMachineTerminalStatus,
};

/// Outbound fake whose `prepare` returns a non-empty recipient plan, so a
/// delivery is an external `deliver_terminal_notification` call and not the
/// terminal cleanup callback.
#[derive(Default)]
struct DeliveryChannel {
    prepares: AtomicUsize,
    finished: Mutex<Vec<StateMachineTerminalEvent>>,
    delivered: Mutex<Vec<(StateMachineTerminalEvent, StateMachineTerminalNotification)>>,
}

#[async_trait]
impl SessionChannelOutboundPort for DeliveryChannel {
    async fn publish_human_input_ready(
        &self,
        _: HumanInputReadyEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        Ok(SessionChannelDeliveryOutcome::Delivered)
    }

    async fn prepare_state_machine_terminal(
        &self,
        event: &StateMachineTerminalEvent,
    ) -> ServiceResult<Vec<StateMachineTerminalNotification>> {
        self.prepares.fetch_add(1, Ordering::SeqCst);
        Ok(vec![StateMachineTerminalNotification {
            binding_id: "binding".into(),
            channel_type: "test-im".into(),
            account_ref: "account".into(),
            im_conversation_id: format!("conversation-{}", event.run_id),
            im_conversation_type: "2".into(),
            im_user_id: None,
            request_id: format!("request-{}", event.run_id),
            node_id: "node".into(),
            text: format!("terminal notification {:?}", event.status),
        }])
    }

    async fn validate_terminal_notification(
        &self,
        _: &StateMachineTerminalNotification,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn deliver_terminal_notification(
        &self,
        event: &StateMachineTerminalEvent,
        notification: &StateMachineTerminalNotification,
    ) -> ServiceResult<Option<String>> {
        let reference = format!("message-{}", notification.request_id);
        self.delivered
            .lock()
            .await
            .push((event.clone(), notification.clone()));
        Ok(Some(reference))
    }

    async fn finish_state_machine_terminal(
        &self,
        event: &StateMachineTerminalEvent,
    ) -> ServiceResult<()> {
        self.finished.lock().await.push(event.clone());
        Ok(())
    }
}

/// Message repository that records every append so a test can prove that a
/// late event added no chat/history row. It stays readable and writable, so a
/// positive control can show the recorder is live.
#[derive(Default)]
struct RecordingMessages {
    inner: MemoryMessageRepo,
    writes: StdMutex<Vec<String>>,
}

#[async_trait]
impl MessageRepoPort for RecordingMessages {
    async fn append_message(
        &self,
        message: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError> {
        self.writes.lock().unwrap().push(message.message_type.clone());
        self.inner.append_message(message).await
    }

    async fn append_message_with_id(
        &self,
        id: String,
        message: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError> {
        self.writes.lock().unwrap().push(message.message_type.clone());
        self.inner.append_message_with_id(id, message).await
    }

    async fn query_messages(&self, query: MessageQuery) -> Result<MessagePage, MessageRepoError> {
        self.inner.query_messages(query).await
    }

    async fn get_message_by_id(
        &self,
        session_id: &str,
        message_id: &str,
    ) -> Result<Option<PersistedMessage>, MessageRepoError> {
        self.inner.get_message_by_id(session_id, message_id).await
    }

    async fn get_current_seq(&self, session_id: &str) -> Result<i64, MessageRepoError> {
        self.inner.get_current_seq(session_id).await
    }
}

/// Delivers an error terminal event for the Bot delivery at `index`, the shape
/// a player Bot produces when its process is killed mid-turn (the channel
/// reports the loss, no assistant message ever arrives).
async fn lost_heartbeat(
    h: &Harness,
    index: usize,
    run: &StateMachineRun,
) -> Result<bcs_service_api::HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
    let delivery_id = h.delivery.commands.lock().await[index].run_id.clone();
    h.runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".into(),
            run_id: delivery_id.clone(),
            event_type: "chat.event".into(),
            event_payload: json!({
                "run_id": delivery_id,
                "state": "error",
                "error": "player process exited without a final message",
                // The channel carries the reason as the only text block; no
                // assistant artifact ever arrives for the lost turn.
                "message": {"content": [{
                    "type": "text",
                    "text": "player process exited without a final message",
                }]},
            }),
            state: ChatEventState::Error,
            bcs_session_id: Some(run.session_id.clone()),
        })
        .await
}

async fn recover_terminal_im(h: &Harness) {
    let page = h
        .runtime
        .recover_state_machine_terminal_im(None, 32)
        .await
        .unwrap();
    assert!(page.failures.is_empty(), "{:?}", page.failures);
}

async fn failed_deliveries(channel: &DeliveryChannel, run: &StateMachineRun) -> usize {
    channel
        .delivered
        .lock()
        .await
        .iter()
        .filter(|(event, _)| {
            event.run_id == run.run_id && event.status == StateMachineTerminalStatus::Failed
        })
        .count()
}

#[tokio::test]
async fn heartbeat_loss_fails_run_and_stops_the_turn() {
    let (mut h, _runs) = harness(&[]).await;
    let channel = Arc::new(DeliveryChannel::default());
    h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
    // One attempt: an exhausted (unrecoverable) failure must fail the Run
    // instead of scheduling a retry.
    let started = h.start(loop_yaml(1, false, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;

    let outcome = lost_heartbeat(&h, 0, run).await.unwrap();

    let view = outcome.view.unwrap();
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(
        view.run.error.as_deref(),
        Some("player process exited without a final message")
    );
    assert!(view.run.completed_at.is_some());

    // The dead turn is closed, not left running for the next scanner sweep.
    let node = h
        .store
        .get_node_run(&run.run_id, &iteration_id(&plan, 1))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(node.status, StateMachineNodeStatus::Failed);
    assert_eq!(node.error.as_deref(), Some("player process exited without a final message"));

    // Exactly one Bot turn was dispatched for the whole Run, and the manager is
    // informed by one real `deliver_terminal_notification` call.
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(
        failed_deliveries(&channel, run).await,
        1,
        "exactly one failed notification is really sent: {:?}",
        channel.delivered.lock().await
    );
    let saved = h.store.get_terminal_im(&run.run_id).await.unwrap().unwrap();
    assert_eq!(saved.payload.notifications.len(), 1);

    // A failed Run must not be revived by the progression scanner.
    recover(&h).await;
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(failed_deliveries(&channel, run).await, 1);
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Failed
    );
}

#[tokio::test]
async fn restart_between_failed_cas_and_notification_delivers_once() {
    let (mut h, runs) = harness(&[]).await;
    let channel = Arc::new(DeliveryChannel::default());
    h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
    let started = h.start(loop_yaml(1, false, false, 1), false).await;
    let run = &started.view.run;

    // The status CAS to `failed` succeeds, then the process dies before the
    // terminal notification checkpoint is saved. `fail_im_save` sits exactly
    // between those two writes.
    runs.fail_im_save.store(true, Ordering::SeqCst);
    assert!(lost_heartbeat(&h, 0, run).await.is_err());
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Failed,
        "the failed terminal state is committed before the crash"
    );
    assert_eq!(
        h.sessions.get(&run.session_id).await.unwrap().unwrap().status,
        SessionStatus::Running,
        "the session has not been reconciled yet"
    );
    assert!(h.store.get_terminal_im(&run.run_id).await.unwrap().is_none());
    assert_eq!(
        failed_deliveries(&channel, run).await,
        0,
        "the crashed process died before any external send"
    );
    assert!(channel.finished.lock().await.is_empty());

    // Simulated restart: a brand new runtime over the same durable stores,
    // still using the same outbound fake so sends remain observable.
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    install_runtime(&mut h, runs, &[]).await;
    h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
    let sessions = h
        .runtime
        .recover_state_machine_sessions(None, 32)
        .await
        .unwrap();
    assert!(sessions.failures.is_empty(), "{:?}", sessions.failures);
    recover_terminal_im(&h).await;
    let delivered = failed_deliveries(&channel, run).await;
    assert_eq!(delivered, 1, "the failed result is really sent after restart");
    assert_eq!(
        channel.finished.lock().await.len(),
        1,
        "the cleanup callback runs once and is not the delivery itself"
    );
    let prepares = channel.prepares.load(Ordering::SeqCst);

    // The dedupe key is the run id: replaying the whole recovery cycle after a
    // second restart must not send the same result again, and must not even
    // reprepare the frozen recipient plan.
    let restarted = Arc::new(FaultyRuns::new(h.store.clone()));
    install_runtime(&mut h, restarted, &[]).await;
    h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
    let sessions = h
        .runtime
        .recover_state_machine_sessions(None, 32)
        .await
        .unwrap();
    assert!(sessions.failures.is_empty(), "{:?}", sessions.failures);
    recover_terminal_im(&h).await;
    assert_eq!(
        failed_deliveries(&channel, run).await,
        delivered,
        "the same run failure is never sent twice"
    );
    assert_eq!(
        channel.prepares.load(Ordering::SeqCst),
        prepares,
        "a durable terminal plan is never rebuilt on restart"
    );
    assert_eq!(channel.finished.lock().await.len(), 1);
    let saved = h.store.get_terminal_im(&run.run_id).await.unwrap().unwrap();
    assert_eq!(saved.payload.event.status, StateMachineTerminalStatus::Failed);
    assert!(matches!(
        saved.progress.deliveries[0],
        StateMachineTerminalImDelivery::Delivered { .. }
    ));
    assert_eq!(
        h.sessions.get(&run.session_id).await.unwrap().unwrap().status,
        SessionStatus::Completed
    );
    assert_eq!(h.delivery.commands.lock().await.len(), 1);

    // A replayed Bot event for the already terminal turn is a no-op too.
    let outcome = lost_heartbeat(&h, 0, run).await.unwrap();
    assert!(outcome.consumed);
    assert_eq!(failed_deliveries(&channel, run).await, delivered);
}

#[tokio::test]
async fn replayed_failure_event_is_consumed_without_a_second_effect() {
    let (mut h, _runs) = harness(&[]).await;
    let channel = Arc::new(DeliveryChannel::default());
    h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
    let started = h.start(loop_yaml(1, false, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;

    lost_heartbeat(&h, 0, run).await.unwrap();
    let node_after_first = h
        .store
        .get_node_run(&run.run_id, &iteration_id(&plan, 1))
        .await
        .unwrap()
        .unwrap();
    let delivered = failed_deliveries(&channel, run).await;
    assert_eq!(delivered, 1, "the first failure is really sent once");

    // Reorder / duplicate: the same failure event arrives a second time.
    let replay = lost_heartbeat(&h, 0, run).await.unwrap();
    assert!(replay.consumed, "a replay is acknowledged so the sender can drop it");
    assert_eq!(
        replay.view.as_ref().unwrap().run.status,
        StateMachineRunStatus::Failed
    );
    assert_eq!(
        failed_deliveries(&channel, run).await,
        delivered,
        "a replayed event cannot send the manager notification twice"
    );

    let node_after_replay = h
        .store
        .get_node_run(&run.run_id, &iteration_id(&plan, 1))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        serde_json::to_value(&node_after_first).unwrap(),
        serde_json::to_value(&node_after_replay).unwrap(),
        "a replayed event cannot rewrite the failed attempt"
    );
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(
        h.sessions.get(&run.session_id).await.unwrap().unwrap().status,
        SessionStatus::Completed
    );
}

#[tokio::test]
async fn late_artifact_after_failure_cannot_advance_or_write_chat() {
    let (mut h, _runs) = harness(&[]).await;
    let channel = Arc::new(DeliveryChannel::default());
    let messages = Arc::new(RecordingMessages::default());
    // History persistence is on and the message repository is observable, so a
    // late event that reached the acceptance path would leave a row behind.
    h.runtime = h
        .runtime
        .with_session_channel_outbound(channel.clone())
        .with_message_repo(messages.clone())
        .with_history_persistence(true)
        .with_history_cutoff_timestamp(u64::MAX);
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    let failed_node = iteration_id(&plan, 1);
    let next_node = iteration_id(&plan, 2);

    lost_heartbeat(&h, 0, run).await.unwrap();
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Failed
    );
    let deliveries = h.delivery.commands.lock().await.len();
    let delivered = failed_deliveries(&channel, run).await;
    let writes = messages.writes.lock().unwrap().len();
    let next_before = h
        .store
        .get_node_run(&run.run_id, &next_node)
        .await
        .unwrap()
        .unwrap();

    // A late artifact produced by the killed Bot arrives inside a real `Final`
    // event addressed to the original delivery, which is the route the runtime
    // exposes to a transport that replays a completed turn.
    let delivery_id = h.delivery.commands.lock().await[0].run_id.clone();
    let late = h
        .runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".into(),
            run_id: delivery_id.clone(),
            event_type: "chat.event".into(),
            event_payload: json!({
                "run_id": delivery_id,
                "state": "final",
                "message": {"content": [{"type": "text", "text": "late artifact"}]},
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(run.session_id.clone()),
        })
        .await
        .unwrap();
    assert!(
        late.consumed,
        "the delivery id is still correlated, so the event reaches the terminal guard"
    );
    assert_eq!(
        late.view.as_ref().unwrap().run.status,
        StateMachineRunStatus::Failed
    );

    // Store-level CAS: the same late artifact must not move the terminal Run.
    let recorded = h
        .store
        .record_node_artifact_if_running(&run.run_id, &failed_node, 0, "late artifact".into())
        .await
        .unwrap();
    let completed = h
        .store
        .complete_node_attempt(
            &run.run_id,
            &failed_node,
            0,
            "late outcome".into(),
            "late artifact".into(),
            None,
            bcs_protocol::now_ms(),
        )
        .await
        .unwrap();
    assert!(!recorded, "a terminal Run rejects a late artifact");
    assert!(!completed, "a terminal Run rejects a late completion");

    // The late artifact neither advances the phase nor writes chat.
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Failed
    );
    let failed_after = h
        .store
        .get_node_run(&run.run_id, &failed_node)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(failed_after.status, StateMachineNodeStatus::Failed);
    assert_eq!(failed_after.artifact_text.as_deref(), None);
    let next_after = h
        .store
        .get_node_run(&run.run_id, &next_node)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        serde_json::to_value(&next_before).unwrap(),
        serde_json::to_value(&next_after).unwrap(),
        "the next phase is untouched"
    );
    let late_writes = messages.writes.lock().unwrap()[writes..].to_vec();
    assert!(
        late_writes.is_empty(),
        "a late Final event must not append chat history: {late_writes:?}"
    );
    assert_eq!(h.delivery.commands.lock().await.len(), deliveries);
    assert_eq!(failed_deliveries(&channel, run).await, delivered);
    recover(&h).await;
    assert_eq!(
        h.delivery.commands.lock().await.len(),
        deliveries,
        "recovery does not resume a failed Run because of a late artifact"
    );

    // Positive control: the recorder really observes writes, so the empty slice
    // above is evidence rather than an unwired repository.
    let witness = h.start(single_node_yaml(), false).await;
    let witness_outcome = terminal(&h, 1, &witness.view.run, "witness output").await.unwrap();
    assert!(witness_outcome.consumed);
    assert!(
        messages.writes.lock().unwrap().len() > writes,
        "the message repository is observable: {:?}",
        messages.writes.lock().unwrap()
    );
}

#[tokio::test]
async fn user_aborted_run_is_never_retried() {
    let (mut h, _runs) = harness(&[]).await;
    let channel = Arc::new(DeliveryChannel::default());
    h.runtime = h.runtime.with_session_channel_outbound(channel.clone());
    let started = h.start(loop_yaml(2, false, false, 3), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    let node_id = iteration_id(&plan, 1);

    h.runtime
        .cancel_state_machine_run(CancelStateMachineRunCommand {
            run_id: run.run_id.clone(),
            reason: Some("user aborted".into()),
        })
        .await
        .unwrap();
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Aborted
    );
    let deliveries = h.delivery.commands.lock().await.len();
    let delivered = failed_deliveries(&channel, run).await;
    assert_eq!(delivered, 0, "an abort is not a failed result notification");

    // A user abort is not a failure: the exhausted-turn event for the aborted
    // Run must not schedule the manager retry (`--retry`).
    let replay = lost_heartbeat(&h, 0, run).await.unwrap();
    assert!(replay.consumed);
    assert_eq!(
        replay.view.as_ref().unwrap().run.status,
        StateMachineRunStatus::Aborted
    );
    let node = h
        .store
        .get_node_run(&run.run_id, &node_id)
        .await
        .unwrap()
        .unwrap();
    assert_ne!(
        node.status,
        StateMachineNodeStatus::RetryScheduled,
        "an aborted Run must not schedule a retry"
    );
    assert!(h
        .store
        .get_node_attempt_failure(&run.run_id, &node_id, 0)
        .await
        .unwrap()
        .is_none());

    // Neither the bot-hosted retry nor any recovery sweep re-dispatches a turn.
    recover(&h).await;
    recover(&h).await;
    recover_terminal_im(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), deliveries);
    assert_eq!(failed_deliveries(&channel, run).await, delivered);
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Aborted
    );
}
