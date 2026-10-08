use super::*;
use bcs_service_api::port::{EventRecorderPort, EventRecordError, EventRecordErrorCategory, EventRecordResult, NewEvent};
use bcs_service_api::{FrontendDeliveryPort, FrontendDeliveryCommand, FrontendDeliveryResult, ServiceResult};
use std::sync::atomic::{AtomicBool, AtomicUsize};

#[derive(Default)]
struct FailingRecorder {
    fail: AtomicBool,
    attempts: AtomicUsize,
    events: tokio::sync::Mutex<std::collections::BTreeMap<String, NewEvent>>,
}

#[async_trait::async_trait]
impl EventRecorderPort for FailingRecorder {
    async fn record(&self, event: NewEvent) -> Result<EventRecordResult, EventRecordError> {
        if event.event_type == "task.completed" {
            self.attempts.fetch_add(1, Ordering::SeqCst);
            if self.fail.load(Ordering::SeqCst) {
                return Err(EventRecordError { category:EventRecordErrorCategory::Storage, message:"injected completed event failure".into() });
            }
        }
        let key = format!("{}:{}:{}", event.producer, event.producer_key, event.event_type);
        self.events.lock().await.entry(key).or_insert(event);
        Ok(EventRecordResult::Disabled)
    }
}

#[derive(Default)]
struct CleanupFrontend { unregistered: tokio::sync::Mutex<Vec<String>>, fail: AtomicBool }
#[async_trait::async_trait]
impl FrontendDeliveryPort for CleanupFrontend {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        if self.fail.load(Ordering::SeqCst) { return Err(bcs_service_api::ServiceError::InternalError("injected frontend failure".into())); }
        Ok(FrontendDeliveryResult { target:cmd.target, delivered:1 })
    }
    async fn unregister_run(&self, id: &str) -> ServiceResult<()> {
        self.unregistered.lock().await.push(id.into()); Ok(())
    }
}

#[tokio::test]
async fn failed_completion_event_still_cleans_run_and_retries_from_durable_result() {
    let mut f = Fixture::new().await;
    let recorder = Arc::new(FailingRecorder::default());
    let frontend = Arc::new(CleanupFrontend::default());
    f.flow = Fixture::configured_flow(&f.support, &f.service, &f.repo, &f.live, |mut flow| {
        flow.frontend_delivery = frontend.clone();
        flow.with_event_recorder(recorder.clone())
    }).await;
    let (task, row) = f.dispatch().await;
    let row = f.start(&row).await;
    let run = row.run_id.as_ref().unwrap();
    assert!(f.flow.bot_run_context.as_ref().unwrap().find_active_run(run).await.unwrap().is_some());
    recorder.fail.store(true, Ordering::SeqCst);
    assert!(f.flow.handle_bot_event(final_event(&row)).await.unwrap_err().to_string().contains("injected completed"));
    assert_eq!(f.rows().await.iter().find(|r| r.delivery_id == row.delivery_id).unwrap().state.status, Status::Completed);
    assert!(frontend.unregistered.lock().await.contains(run));
    assert!(f.flow.bot_run_context.as_ref().unwrap().find_active_run(run).await.unwrap().is_none());
    // A retry must still report the unresolved write, even though delivery is terminal.
    assert!(f.flow.handle_bot_event(final_event(&row)).await.is_err());
    recorder.fail.store(false, Ordering::SeqCst);
    // Losing all process-local state must not lose the pending event projection.
    f.flow = Fixture::configured_flow(&f.support, &f.service, &f.repo, &f.live, |mut flow| {
        flow.frontend_delivery = frontend.clone(); flow.with_event_recorder(recorder.clone())
    }).await;
    let mut retry = final_event(&row);
    retry.event_payload = json!({"message":{"content":"DIFFERENT_RETRY_BODY"}});
    f.flow.handle_bot_event(retry.clone()).await.unwrap();
    f.flow.handle_bot_event(retry).await.unwrap();
    assert_eq!(f.rows().await.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").count(), 1);
    let events = recorder.events.lock().await;
    let completed: Vec<_> = events.values().filter(|e| e.event_type == "task.completed").collect();
    assert_eq!(completed.len(), 1);
    assert_eq!(completed[0].data["result"]["text"], "WORKER_RESULT");
    assert_eq!(completed[0].producer_key, format!("task.completed:{task}"));
    assert_eq!(recorder.attempts.load(Ordering::SeqCst), 3);
}

#[tokio::test]
async fn frontend_failure_after_commit_does_not_skip_context_cleanup() {
    let mut f = Fixture::new().await;
    let frontend = Arc::new(CleanupFrontend::default());
    f.flow = Fixture::configured_flow(&f.support, &f.service, &f.repo, &f.live, |mut flow| {
        flow.frontend_delivery = frontend.clone(); flow
    }).await;
    let (_, row) = f.dispatch().await;
    let row = f.start(&row).await;
    frontend.fail.store(true, Ordering::SeqCst);
    assert!(f.flow.handle_bot_event(final_event(&row)).await.is_err());
    assert!(frontend.unregistered.lock().await.contains(row.run_id.as_ref().unwrap()));
    assert!(f.flow.bot_run_context.as_ref().unwrap().find_active_run(row.run_id.as_ref().unwrap()).await.unwrap().is_none());
    frontend.fail.store(false, Ordering::SeqCst);
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
}

#[tokio::test]
async fn queued_task_send_uses_session_tags_for_dispatch_message_and_result() {
    let mut f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone();
    policy.defaults.min_send_interval_ms = 0;
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let mut group = f.support.group.get("group-1").await.unwrap();
    for p in &mut group.participants { p.tags = vec![format!("session-{}", p.bot_uuid)]; }
    let session = sessions::test_session(SESSION, "group-1", group.participants);
    f.flow = Fixture::configured_flow(&f.support, &f.service, &f.repo, &f.live, |flow|
        flow.with_session_management(Arc::new(sessions::StaticSessionManagement::new(session)))).await;
    for bot in ["bot-driver", "bot-observer"] {
        f.support.registry.set_delivery_target(bot, support::FakeRegistryService::provider_target(bot)).await;
    }
    let (_, row) = f.dispatch().await;
    let row = f.start(&row).await;
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    let result = f.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let result = f.start(&result).await;
    // Release this lane without creating a subsequent group relay in this tag-only test.
    f.transition(&result, Event::Completed).await;
    f.flow.handle_task_message(TaskMessageCommand { worker_bot_id:"bot-observer".into(), group_id:"group-1".into(),
        payload:json!({"bcs_session_id":SESSION,"message":"progress"}) }).await.unwrap();
    let message = f.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["leg"] == "message").unwrap();
    f.start(&message).await;
    let frames = f.support.bot_delivery.frames().await;
    for (frame, bot) in frames.iter().zip(["bot-observer", "bot-driver", "bot-driver"]) {
        assert_eq!(serde_json::to_value(frame).unwrap()["params"]["tags"], json!([format!("session-{bot}")]));
    }
    assert_eq!(frames.len(), 3);
}

#[tokio::test]
async fn ordinary_group_system_and_relay_sends_keep_provider_session_tags() {
    for source in ["group", "system", "relay"] {
        let mut f = Fixture::new().await;
        let mut group = f.support.group.get("group-1").await.unwrap();
        group.group_strategy = GroupStrategy::Chat;
        for p in &mut group.participants {
            p.role = if p.bot_uuid == "bot-driver" { ParticipantRole::Driver } else { ParticipantRole::Observer };
            p.tags = vec!["group-route".into()];
        }
        f.support.group.upsert(group.clone()).await.unwrap();
        let mut participants = group.participants.clone();
        for p in &mut participants { p.tags = vec![format!("session-{}", p.bot_uuid)]; }
        let session = sessions::test_session(SESSION, "group-1", participants.clone());
        f.flow = Fixture::configured_flow(&f.support, &f.service, &f.repo, &f.live, |flow|
            flow.with_session_management(Arc::new(sessions::StaticSessionManagement::new(session)))).await;
        f.support.registry.set_delivery_target("bot-observer", support::FakeRegistryService::provider_target("bot-observer")).await;
        match source {
            "group" => {
                f.flow.handle_web_send(bcs_service_api::WebSendCommand {
                    caller:CallerContext::Human(HumanActor { actor_id:"human_1".into(), staff_no:"1".into() }),
                    group_id:"group-1".into(), session_id:Some(SESSION.into()), from_actor_id:"human_1".into(),
                    from_name:None, message:"@Observer check".into(), mentions:vec!["bot-observer".into()], attachments:None,
                    thinking:None, idempotency_key:None, source_im_message_id:None, channel_sender_identity:None,
                    sender_conn_id:None, provider_bypass_headers:vec![],
                }).await.unwrap();
            }
            "system" => {
                f.flow.system_queue_port().admit(&group, SESSION, &participants, SystemMessageEventKind::SessionContext,
                    &[SystemGroupMessage { recipients:vec!["bot-observer".into()], message:"SYSTEM_BODY".into(),
                        delivery_type:DeliveryType::Send, persist:PersistMode::PerRecipient }]).await.unwrap();
            }
            _ => {
                f.flow.handle_bot_event(BotEventCommand { bot_id:"bot-driver".into(), run_id:"legacy-relay-run".into(),
                    group_id:"group-1".into(), bcs_session_id:Some(SESSION.into()), state:ChatEventState::Final,
                    event_type:"chat".into(), event_payload:json!({"message":{"content":"@Observer check"}}) }).await.unwrap();
            }
        }
        let row = f.rows().await.into_iter().find(|r| r.target_bot_id == "bot-observer" && r.state.kind == DeliveryType::Send).unwrap();
        assert_eq!(row.semantic_projection_json["target_tags"], json!(["session-bot-observer"]), "{source}");
        f.start(&row).await;
        let frames = f.support.bot_delivery.frames().await;
        assert_eq!(serde_json::to_value(frames.last().unwrap()).unwrap()["params"]["tags"], json!(["session-bot-observer"]), "{source}");
    }
}

#[tokio::test]
async fn task_terminal_session_read_failure_does_not_commit_empty_route_tags() {
    let mut f = Fixture::new().await;
    let (_, row) = f.dispatch().await;
    let row = f.start(&row).await;
    let participants = f.support.group.get("group-1").await.unwrap().participants;
    f.flow = Fixture::configured_flow(&f.support, &f.service, &f.repo, &f.live, |flow|
        flow.with_session_management(Arc::new(sessions::StaticSessionManagement::new(
            sessions::test_session(SESSION, "group-1", participants)).with_get_failure()))).await;
    assert!(f.flow.handle_bot_event(final_event(&row)).await.unwrap_err().to_string().contains("session read failed"));
    let rows = f.rows().await;
    assert_eq!(rows.iter().find(|r| r.delivery_id == row.delivery_id).unwrap().state.status, Status::Dispatching);
    assert!(!rows.iter().any(|r| r.semantic_projection_json["task"]["leg"] == "result"));
}

#[tokio::test]
async fn late_final_after_cancel_does_not_republish_or_change_the_committed_outcome() {
    let f = Fixture::new().await;
    let (_, row) = f.dispatch().await;
    let row = f.start(&row).await;
    let mut aborted = final_event(&row);
    aborted.state = ChatEventState::Aborted;
    f.flow.handle_bot_event(aborted).await.unwrap();
    let restarted = Fixture::make_flow(&f.support, &f.service, &f.repo, &f.live).await;
    let before = f.support.frontend_delivery.events().await.len();
    restarted.handle_bot_event(final_event(&row)).await.unwrap();
    assert_eq!(f.support.frontend_delivery.events().await.len(), before);
    let rows = f.rows().await;
    assert_eq!(rows.iter().find(|r| r.delivery_id == row.delivery_id).unwrap().state.status, Status::Cancelled);
    assert_eq!(rows.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").count(), 1);
}
