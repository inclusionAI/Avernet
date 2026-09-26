use super::*;
use bcs_domain::message_delivery::{PersistedMessageDelivery, MessageDeliveryStatus as Status};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use std::sync::atomic::Ordering::SeqCst;

#[derive(Default)]
pub(super) struct Pause {
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}

impl Pause {
    pub(super) async fn pause(&self) {
        self.entered.notify_one();
        tokio::time::timeout(Duration::from_secs(5), self.release.notified()).await.unwrap();
    }

    async fn entered(&self) {
        tokio::time::timeout(Duration::from_secs(5), self.entered.notified()).await.unwrap();
    }
}

fn transition(row: &PersistedMessageDelivery, event: Event) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand {
        delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
        event, now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None,
        reply: None, transport_context_json: None, deadline_at_ms: None,
    }
}

async fn start(h: &Harness, run: &str) -> PersistedMessageDelivery {
    let row = h.row(run).await;
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&h.flow), deliveries: h.service.clone() };
    let mut prepared = preparer.prepare(&row).await.unwrap();
    prepared.transport_context_json["policy_version"] = json!(h.policy.snapshot.read().await.version);
    let mut command = transition(&row, Event::StartSend);
    command.transport_context_json = Some(prepared.transport_context_json);
    command.deadline_at_ms = Some(i64::MAX);
    let started = h.service.transition(command).await.unwrap();
    if let bcs_protocol::BcsFrame::Request(frame) = &mut prepared.command.frame {
        frame.id = started.request_id.clone().unwrap();
    }
    preparer.before_send(&started, &prepared.command).await.unwrap();
    started
}

pub(super) async fn assert_context_cleaned(h: &Harness, row: &PersistedMessageDelivery) {
    let contexts = h.flow.bot_run_context.as_ref().unwrap();
    let run = row.run_id.as_deref().unwrap();
    assert!(contexts.get_context(run).await.unwrap().terminal);
    assert!(contexts.find_active_run(run).await.unwrap().is_none());
    assert!(contexts.get_context(row.request_id.as_deref().unwrap()).await.is_none(), "request alias must be removed");
    assert!(contexts.list_active_runs(&BotRunScope {
        group_id: row.group_id.clone(), session_id: row.session_id.clone(), bot_id: row.target_bot_id.clone(),
    }).await.unwrap().iter().all(|active| active.canonical_run_id != run));
}

#[tokio::test]
async fn independent_instances_retry_sequence_cas_without_duplicate_admission() {
    let first = Harness::new(true).await;
    let db = first.db.as_ref().unwrap();
    let second = Harness::with_db(true, Some(db.clone())).await;
    *db.sequence_barrier.lock().unwrap() = Some((2, Arc::new(tokio::sync::Barrier::new(2))));
    let (one, two) = tokio::join!(first.submit("replica-one", "shared-cas"), second.submit("replica-two", "shared-cas"));
    assert_eq!(one.unwrap().status, "pending");
    assert_eq!(two.unwrap().status, "pending");
    assert_eq!(db.admission_conflicts.load(SeqCst), 1, "must exercise a real rolled-back sequence CAS");
    assert_eq!(db.admission_attempts.load(SeqCst), 3);
    let mut seqs = Vec::new();
    for run in ["replica-one", "replica-two"] {
        let row = first.row(run).await;
        seqs.push(row.source_session_seq);
        let record = first.direct.run_store().get(run).await.unwrap();
        let message = first.messages.get_message_by_id("shared-cas", &row.source_message_id).await.unwrap().unwrap();
        assert_eq!(record.source_message_id.as_deref(), Some(row.source_message_id.as_str()));
        assert_eq!(message.client_msg_id, Some(format!("direct-a2a:{run}")));
        assert_eq!(message.run_id, run);
    }
    seqs.sort();
    assert_eq!(seqs, vec![1, 2]);
    for table in ["bcs_chat_runs", "bcs_messages", "bcs_message_deliveries"] {
        let rows = db.query(DbStatement::new(format!("SELECT COUNT(*) AS n FROM {table}"))).await.unwrap();
        assert_eq!(bcs_db_api::db_get_column::<i64>(&rows[0], "n").unwrap(), 2, "{table}");
    }
    assert!(first.support.bot_delivery.frames().await.is_empty());
    assert!(second.support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn admission_conflict_retries_are_bounded_and_leave_no_partial_message() {
    let h = Harness::new(true).await;
    let db = h.db.as_ref().unwrap();
    db.fail_admissions.store(true, SeqCst);
    assert!(h.submit("contended", "contended-session").await.is_err());
    assert_eq!(db.admission_attempts.load(SeqCst), 4);
    assert!(h.service.snapshot(None).await.unwrap().is_empty());
    assert_eq!(h.sessions.session_registration("contended-session").await.unwrap().unwrap().current_msg_seq, Some(0));
    assert!(h.support.bot_delivery.frames().await.is_empty());
    let rows = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM bcs_messages")).await.unwrap();
    assert_eq!(bcs_db_api::db_get_column::<i64>(&rows[0], "n").unwrap(), 0);
    // The existing orphan recovery policy still owns a failed admission.
    assert_eq!(h.direct.run_store().get("contended").await.unwrap().state, ChatRunState::Pending);
}

#[tokio::test]
async fn terminal_retries_stale_version_after_response_checkpoint_without_losing_body() {
    for (state, expected) in [(ChatEventState::Final, Status::Completed), (ChatEventState::Error, Status::Failed), (ChatEventState::Aborted, Status::Cancelled)] {
        let h = Harness::new(true).await;
        h.submit("fast-final", "fast-session").await.unwrap();
        let started = start(&h, "fast-final").await;
        let gate = Arc::new(Pause::default());
        *h.db.as_ref().unwrap().checkpoint_gate.lock().unwrap() = Some(gate.clone());
        let flow = h.flow.clone();
        let event = BotEventCommand {
            bot_id: "bot-observer".into(), run_id: "fast-final".into(), group_id: String::new(),
            bcs_session_id: Some("fast-session".into()), event_type: "chat".into(), state,
            event_payload: json!({"message":{"role":"assistant","content":[{"type":"text","text":"saved answer"}]}}),
        };
        let duplicate = event.clone();
        let terminal = tokio::spawn(async move { flow.handle_bot_event(event).await });
        gate.entered().await;
        // Scheduler completes transport while the response checkpoint waits.
        let submitted = h.service.transition(transition(&started, Event::Submitted)).await.unwrap();
        assert!(submitted.state.state_version > started.state.state_version);
        gate.release.notify_one();
        terminal.await.unwrap().unwrap();
        h.flow.handle_bot_event(duplicate).await.unwrap();
        assert_eq!(h.row("fast-final").await.state.status, expected);
        let record = h.direct.run_store().get("fast-final").await.unwrap();
        assert!(record.state.is_terminal());
        if expected == Status::Completed { assert_eq!(record.accumulated_content, "saved answer"); }
        assert_context_cleaned(&h, &started).await;
    }
}

#[tokio::test]
async fn cancel_retries_stale_version_and_retains_context_until_confirmed_stop() {
    let h = Harness::new(true).await;
    h.submit("cancel-race", "cancel-race-session").await.unwrap();
    let started = start(&h, "cancel-race").await;
    let gate = Arc::new(Pause::default());
    *h.db.as_ref().unwrap().delivery_read_gate.lock().unwrap() = Some(gate.clone());
    let direct = h.direct.clone();
    let cancel = tokio::spawn(async move { A2aChatService::cancel_run(direct.as_ref(), caller(), "cancel-race").await });
    gate.entered().await;
    h.service.transition(transition(&started, Event::Submitted)).await.unwrap();
    gate.release.notify_one();
    cancel.await.unwrap().unwrap();
    let cancelling = h.row("cancel-race").await;
    assert_eq!(cancelling.state.status, Status::Cancelling);
    let contexts = h.flow.bot_run_context.as_ref().unwrap();
    assert!(contexts.find_active_run("cancel-race").await.unwrap().is_some());
    h.service.transition(transition(&cancelling, Event::Aborted)).await.unwrap();
    // Recovery must clean context even without a direct_event callback.
    A2aChatService::cleanup_expired(h.direct.as_ref(), chrono::Utc::now().timestamp_millis() as u64, 60_000).await.unwrap();
    assert_context_cleaned(&h, &started).await;
    assert_eq!(h.status("cancel-race").await["state"], "cancelled");
}

#[tokio::test]
async fn reconciliation_cleans_expired_active_entries_and_partial_terminal_cleanup() {
    for partial in [false, true] {
        let h = Harness::new(true).await;
        h.submit("cleanup", "cleanup-session").await.unwrap();
        let started = start(&h, "cleanup").await;
        let contexts = h.flow.bot_run_context.as_ref().unwrap();
        let mut active = contexts.find_active_run("cleanup").await.unwrap().unwrap();
        active.deadline_ms = 0;
        contexts.register_active_run(active).await.unwrap();
        if partial { contexts.mark_terminal("cleanup").await; }
        h.service.transition(transition(&started, Event::Completed)).await.unwrap();
        assert_eq!(h.status("cleanup").await["state"], "completed");
        assert_context_cleaned(&h, &started).await;
        // Terminal projections must remain safe to reconcile again.
        assert_eq!(h.status("cleanup").await["state"], "completed");
    }
}
