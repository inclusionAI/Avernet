use async_trait::async_trait;
use bcs_domain::message_delivery::{
    DeliveryFlowKind, MessageDeliveryStatus as Status, PersistedMessageDelivery,
};
use bcs_domain::{BotDeliveryTarget, DeliveryType, NewMessage, SenderType};
use bcs_message_flow::delivery_runtime::{
    DeliveryRuntime, DeliveryRuntimeConfig, DeliveryRuntimePolicy,
};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult, ServiceError, ServiceResult,
};
use bcs_service_api::{
    DeliveryTransitionCommand, ManagedDeliveryError, ManagedDeliveryPreparationService,
    ManagedMessageDeliveryService, PreparedManagedDelivery,
};
use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

#[path = "support/failing_delivery.rs"]
mod failing_delivery;

#[tokio::test]
async fn storage_outages_retry_without_stopping_or_repeating_send_and_abort() -> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let faults = Arc::new(failing_delivery::FailingDelivery::new(service.clone()));
    for stage in ["recover", "scan", "queued_bots", "active_count", "queued_heads", "wait_reason", "lookup", "send_start", "submitted", "submitted_after_commit", "abort_start", "aborted"] {
        faults.arm(stage, 1);
    }
    service.admit(command("retry-db", "session")).await?;
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    let mut worker = runtime(service.clone(), io.clone()); worker.service = faults.clone();
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    let row = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let row = service.snapshot(None).await.unwrap().remove(0);
            if row.submitted_at_ms.is_some() { break row; }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }).await?;
    // Wait for the injected post-commit failure to be reconciled before abort.
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert!(!task.is_finished());
    service.transition(DeliveryTransitionCommand { delivery_id: row.delivery_id,
        expected_state_version: row.state.state_version, event: Event::CancelRequested,
        now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None, reply: None,
        transport_context_json: None, deadline_at_ms: Some(chrono::Utc::now().timestamp_millis() + 5000) }).await?;
    wait_status(&service, "retry-db", Status::Cancelled).await?;
    assert!(!task.is_finished());
    assert_eq!(*io.sent.lock().await, vec!["retry-db"]);
    assert_eq!(*io.aborts.lock().await, vec!["retry-db"]);
    assert_eq!(faults.remaining(), 0);
    stop.send(true)?; task.await??;
    Ok(())
}

#[tokio::test]
async fn shutdown_interrupts_storage_backoff() -> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let faults = Arc::new(failing_delivery::FailingDelivery::new(service.clone()));
    faults.arm("scan", usize::MAX);
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    let mut worker = runtime(service, io.clone()); worker.service = faults.clone();
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    tokio::time::timeout(Duration::from_secs(1), faults.failed.notified()).await?;
    assert!(!task.is_finished());
    stop.send(true)?;
    let _ = tokio::time::timeout(Duration::from_secs(1), task).await??;
    assert!(io.sent.lock().await.is_empty());
    Ok(())
}

#[tokio::test]
async fn ambiguous_send_start_commit_never_replays_transport() -> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let faults = Arc::new(failing_delivery::FailingDelivery::new(service.clone()));
    faults.arm("send_start_after_commit", 1);
    service.admit(command("one", "one")).await?;
    service.admit(command("two", "two")).await?;
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    let mut worker = runtime(service.clone(), io.clone()); worker.service = faults.clone();
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let rows = service.snapshot(None).await.unwrap();
            if rows.iter().any(|r| r.submitted_at_ms.is_some()) { break; }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }).await?;
    let rows = service.snapshot(None).await?;
    assert!(rows.iter().all(|r| r.state.status == Status::Dispatching && r.attempt_no == 1));
    assert_eq!(rows.iter().filter(|r| r.submitted_at_ms.is_some()).count(), 1);
    assert_eq!(io.sent.lock().await.len(), 1);
    assert!(!task.is_finished());
    assert_eq!(faults.remaining(), 0);
    stop.send(true)?; task.await??;
    Ok(())
}

struct RecordingIo {
    service: Arc<ManagedMessageDelivery>,
    sent: tokio::sync::Mutex<Vec<String>>,
    aborts: tokio::sync::Mutex<Vec<String>>,
    fail_send: bool,
    fail_registration: bool,
}

#[async_trait]
impl ManagedDeliveryPreparationService for RecordingIo {
    async fn is_available(&self, _: &str) -> bool {
        true
    }
    async fn prepare(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<PreparedManagedDelivery> {
        Ok(PreparedManagedDelivery {
            command: BotDeliveryCommand {
                target: BotDeliveryTarget::WebSocket {
                    bot_id: row.target_bot_id.clone(),
                },
                run_id: row
                    .run_id
                    .clone()
                    .ok_or_else(|| ServiceError::InternalError("missing run".into()))?,
                frame: BcsFrame::Request(RequestFrame::new("prepared", "chat.send", None)),
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: Default::default(),
                provider_bypass_headers: Vec::new(),
            },
            transport_context_json: serde_json::json!({"version":1, "kind":"websocket"}),
        })
    }
    async fn before_send(
        &self,
        row: &PersistedMessageDelivery,
        _: &BotDeliveryCommand,
    ) -> ServiceResult<()> {
        assert_eq!(row.state.status, Status::Dispatching);
        assert!(row.request_id.is_some());
        assert!(row.transport_context_json.is_some());
        if self.fail_registration {
            return Err(ServiceError::InternalError(
                "registration failed before I/O".into(),
            ));
        }
        Ok(())
    }
    async fn prepare_abort(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<BotAbortDeliveryCommand> {
        Ok(BotAbortDeliveryCommand {
            target: BotDeliveryTarget::WebSocket {
                bot_id: row.target_bot_id.clone(),
            },
            command_id: "prepared".into(),
            group_id: row.group_id.clone(),
            session_id: row.session_id.clone(),
            run_id: row.run_id.clone(),
            provider_bypass_headers: Vec::new(),
            timeout_ms: 1000,
        })
    }
}

#[async_trait]
impl BotDeliveryPort for RecordingIo {
    async fn is_available(&self, _: &BotDeliveryTarget) -> bool {
        true
    }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let rows = self
            .service
            .snapshot(None)
            .await
            .map_err(|e| ServiceError::InternalError(e.to_string()))?;
        let row = rows
            .iter()
            .find(|d| d.run_id.as_deref() == Some(cmd.run_id.as_str()))
            .ok_or_else(|| ServiceError::InternalError("I/O before persistence".into()))?;
        assert!(row.state.may_have_been_sent);
        if let BcsFrame::Request(frame) = &cmd.frame {
            assert_eq!(Some(&frame.id), row.request_id.as_ref());
            assert_ne!(&frame.id, &cmd.run_id);
        } else {
            panic!("send must use a request frame");
        }
        self.sent.lock().await.push(row.source_message_id.clone());
        if self.fail_send {
            return Err(ServiceError::InternalError(
                "ambiguous transport error".into(),
            ));
        }
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().into(),
            delivered: true,
            error: None,
        })
    }
    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        let rows = self
            .service
            .snapshot(None)
            .await
            .map_err(|e| ServiceError::InternalError(e.to_string()))?;
        let row = rows
            .iter()
            .find(|d| d.run_id == cmd.run_id)
            .ok_or_else(|| ServiceError::InternalError("run missing".into()))?;
        assert_eq!(row.abort_request_id.as_ref(), Some(&cmd.command_id));
        assert!(row.abort_started_at_ms.is_some());
        self.aborts.lock().await.push(row.source_message_id.clone());
        Ok(BotAbortDeliveryResult {
            target_bot_id: cmd.target_bot_id().into(),
            aborted_run_ids: cmd.run_id.into_iter().collect(),
        })
    }
}

fn command(id: &str, session: &str) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries {
        display_message: None,
        message_id: id.into(),
        flow_kind: DeliveryFlowKind::Group,
        now_ms: 1,
        expire_at_ms: None,
        event: None,
        message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None,
            group_id: "group".into(),
            session_id: session.into(),
            sender_id: "human".into(),
            sender_type: SenderType::Human,
            message_type: "chat".into(),
            content: serde_json::json!({"text":id}),
            client_msg_id: Some(id.into()),
            owner_bot_id: None,
            created_at: 1,
            run_id: String::new(),
        },
        targets: vec![DeliveryAdmissionTarget { rejection: None,
            target_bot_id: "bot".into(),
            kind: DeliveryType::Send,
            max_queued: 10,
            semantic_projection_json: serde_json::json!({"version":1}),
        }],
    }
}

fn runtime(service: Arc<ManagedMessageDelivery>, io: Arc<RecordingIo>) -> DeliveryRuntime {
    DeliveryRuntime {
        policy: None,
        service,
        preparation: io.clone(),
        transport: io,
        config: DeliveryRuntimeConfig {
            max_safe_retries: 0,
            pause_dispatch: false,
            bots: BTreeMap::from([(
                "bot".into(),
                DeliveryRuntimePolicy {
                    max_running: 2,
                    min_send_interval_ms: 0,
                },
            )]),
            tick: Duration::from_millis(1),
            io_timeout: Duration::from_secs(1),
            run_timeout: Duration::from_secs(30),
            cancel_timeout: Duration::from_secs(1),
            max_tasks: 2,
            max_abort_tasks: 1,
        },
    }
}

#[tokio::test]
async fn bot_cursor_wraps_without_spending_an_empty_tick() -> Result<(), Box<dyn std::error::Error>> {
    #[derive(Default)]
    struct Ticks(std::sync::Mutex<(usize, Vec<usize>)>);
    impl bcs_service_api::application::message_delivery::DeliveryInstrumentation for Ticks {
        fn operation(&self, name: &'static str, _: f64, _: usize, _: bool) {
            if name == "tick" { self.0.lock().unwrap().0 += 1; }
        }
        fn event(&self, name: &'static str, _: &PersistedMessageDelivery) {
            if name == "started" { let mut ticks = self.0.lock().unwrap(); let n = ticks.0; ticks.1.push(n); }
        }
    }
    let ticks = Arc::new(Ticks::default());
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())).with_instrumentation(ticks.clone()));
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    service.admit(command("wrap-one", "one")).await?;
    service.admit(command("wrap-two", "two")).await?;
    let mut worker = runtime(service, io.clone());
    worker.config.tick = Duration::from_millis(200);
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    tokio::time::timeout(Duration::from_secs(3), async {
        while io.sent.lock().await.len() < 2 { tokio::time::sleep(Duration::from_millis(5)).await; }
    }).await?;
    stop.send(true)?; task.await??;
    assert_eq!(ticks.0.lock().unwrap().1, vec![1, 2]);
    Ok(())
}

#[tokio::test]
async fn bot_pages_advance_past_blocked_bot_and_session_pages_do_not_starve() -> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    let mut worker = runtime(service.clone(), io.clone());
    worker.config.max_tasks = 32;
    worker.config.bots.clear();
    for n in 0..70 {
        let bot = format!("bot-{n:03}");
        worker.config.bots.insert(bot.clone(), DeliveryRuntimePolicy { max_running: 1, min_send_interval_ms: 0 });
        let mut c = command(&format!("msg-{n:03}"), &format!("session-{n:03}")); c.targets[0].target_bot_id = bot;
        let row = service.admit(c.clone()).await?.deliveries.remove(0);
        if n == 0 {
            service.transition(event(&row, Event::StartSend)).await?;
            c.message_id = "blocked-successor".into(); c.message.client_msg_id = Some(c.message_id.clone());
            service.admit(c).await?;
        }
    }
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    tokio::time::timeout(Duration::from_secs(5), async {
        while io.sent.lock().await.len() < 69 { tokio::time::sleep(Duration::from_millis(2)).await; }
    }).await?;
    assert!(!io.sent.lock().await.contains(&"blocked-successor".into()));
    stop.send(true)?; task.await??;

    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    for n in 0..20 { let mut c = command(&format!("page-{n:03}"), &format!("s-{n:03}")); c.targets[0].max_queued = 64; service.admit(c).await?; }
    let mut worker = runtime(service.clone(), io.clone()); worker.config.bots.get_mut("bot").unwrap().max_running = 1;
    let (stop, shutdown) = tokio::sync::watch::channel(false); let task = tokio::spawn(worker.run(shutdown));
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let rows = service.snapshot(None).await.unwrap();
            let active: Vec<_> = rows.iter().filter(|d| matches!(d.state.status, Status::Dispatching | Status::Running)).collect();
            assert!(active.len() <= 1);
            for row in active { if io.sent.lock().await.contains(&row.source_message_id) { let _ = service.transition(event(row, Event::Completed)).await; } }
            if io.sent.lock().await.len() == 20 { break; }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    }).await?;
    stop.send(true)?; task.await??;
    Ok(())
}

#[tokio::test]
async fn live_policy_enables_empty_scheduler_recalculates_interval_and_drains_off()
-> Result<(), Box<dyn std::error::Error>> {
    use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryMode};
    use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
    use bcs_service_api::{HumanActor, CallerContext};
    let admin = || CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() });
    let repo = Arc::new(MemoryMessageRepo::new());
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
    live.scheduler_available.store(true, std::sync::atomic::Ordering::SeqCst);
    let service = Arc::new(ManagedMessageDelivery::new(repo).with_policy(live.clone()));
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    let mut scheduler = runtime(service.clone(), io.clone());
    scheduler.config.bots.clear();
    scheduler.policy = Some(live.clone());
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(scheduler.run(shutdown));
    assert!(service.admit(command("disabled", "a")).await.is_err());
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    policy.defaults.min_send_interval_ms = 0;
    policy.defaults.max_running = 1;
    policy.queue_ttl_ms = Some(10000);
    live.replace(admin(), 0, policy.clone()).await?;
    let mut first = command("live-first", "a");
    first.now_ms = chrono::Utc::now().timestamp_millis();
    service.admit(first).await?;
    wait_status(&service, "live-first", Status::Dispatching).await?;
    let mut second = command("live-second", "b");
    second.now_ms = chrono::Utc::now().timestamp_millis();
    let queued = service.admit(second).await?.deliveries.remove(0);
    policy.defaults.max_queued = 1;
    policy.defaults.min_send_interval_ms = 1000;
    policy.queue_ttl_ms = Some(20000);
    live.replace(admin(), 1, policy.clone()).await?;
    let rows = service.snapshot(None).await?;
    assert_eq!(rows.iter().find(|r| r.delivery_id == queued.delivery_id).unwrap().expire_at_ms, queued.expire_at_ms);
    let row = rows.iter().find(|r| r.source_message_id == "live-first").unwrap();
    service.transition(event(row, Event::Completed)).await?;
    tokio::time::sleep(Duration::from_millis(25)).await;
    assert_eq!(io.sent.lock().await.as_slice(), ["live-first"]);
    // Lowering the interval must recompute from the same last-start, not retain
    // the previous 1000 ms future deadline or reset rate state.
    policy.defaults.min_send_interval_ms = 0;
    policy.flow_enabled.group = false;
    live.replace(admin(), 2, policy).await?;
    assert!(service.admit(command("new-off", "c")).await.is_err());
    wait_status(&service, "live-second", Status::Dispatching).await?;
    tokio::time::timeout(Duration::from_secs(1), async {
        loop { if io.sent.lock().await.len() == 2 { break; } tokio::task::yield_now().await; }
    }).await?;
    assert!(io.aborts.lock().await.is_empty());
    stop.send(true)?;
    task.await??;
    Ok(())
}

#[tokio::test]
async fn dispatch_pause_keeps_status_and_queued_cancellation_live()
-> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(
        MemoryMessageRepo::new(),
    )));
    service.admit(command("paused", "a")).await?;
    let io = Arc::new(RecordingIo {
        service: service.clone(),
        sent: Default::default(),
        aborts: Default::default(),
        fail_send: false,
        fail_registration: false,
    });
    let mut scheduler = runtime(service.clone(), io.clone());
    scheduler.config.pause_dispatch = true;
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(scheduler.run(shutdown));
    let row = tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            let row = service.snapshot(None).await.unwrap().remove(0);
            if row.wait_reason == Some(bcs_domain::message_delivery::DeliveryWaitReason::Paused) {
                break row;
            }
            tokio::task::yield_now().await;
        }
    })
    .await?;
    assert!(io.sent.lock().await.is_empty());
    assert_eq!(
        service
            .transition(event(&row, Event::CancelRequested))
            .await?
            .state
            .status,
        Status::Cancelled
    );
    assert!(io.aborts.lock().await.is_empty());
    stop.send(true)?;
    task.await??;
    Ok(())
}

async fn wait_status(
    service: &ManagedMessageDelivery,
    source: &str,
    status: Status,
) -> Result<PersistedMessageDelivery, Box<dyn std::error::Error>> {
    Ok(tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if let Some(row) = service
                .snapshot(None)
                .await?
                .into_iter()
                .find(|d| d.source_message_id == source && d.state.status == status)
            {
                return Ok::<_, ManagedDeliveryError>(row);
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await??)
}

#[tokio::test]
async fn lowering_live_limits_preserves_existing_work_and_only_blocks_new_capacity()
-> Result<(), Box<dyn std::error::Error>> {
    use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryMode};
    use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
    use bcs_service_api::{HumanActor, CallerContext};
    let admin = || CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() });
    let repo = Arc::new(MemoryMessageRepo::new());
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
    live.scheduler_available.store(true, std::sync::atomic::Ordering::SeqCst);
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    policy.defaults.max_running = 2;
    policy.defaults.min_send_interval_ms = 0;
    live.replace(admin(), 0, policy.clone()).await?;
    let service = Arc::new(ManagedMessageDelivery::new(repo).with_policy(live.clone()));
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    let mut scheduler = runtime(service.clone(), io.clone());
    scheduler.policy = Some(live.clone());
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(scheduler.run(shutdown));
    service.admit(command("one", "a")).await?;
    service.admit(command("two", "b")).await?;
    wait_status(&service, "one", Status::Dispatching).await?;
    wait_status(&service, "two", Status::Dispatching).await?;
    service.admit(command("three", "c")).await?;
    policy.defaults.max_running = 1;
    policy.defaults.max_queued = 1;
    live.replace(admin(), 1, policy).await?;
    assert_eq!(service.admit(command("four", "d")).await?.deliveries[0].state.status, Status::RejectedCapacity);
    let rows = service.snapshot(None).await?;
    let one = rows.iter().find(|r| r.source_message_id == "one").unwrap();
    service.transition(event(one, Event::Completed)).await?;
    tokio::time::sleep(Duration::from_millis(15)).await;
    assert_eq!(service.snapshot(None).await?.iter().find(|r| r.source_message_id == "three").unwrap().state.status, Status::Queued);
    let rows = service.snapshot(None).await?;
    service.transition(event(rows.iter().find(|r| r.source_message_id == "two").unwrap(), Event::Completed)).await?;
    wait_status(&service, "three", Status::Dispatching).await?;
    assert!(io.aborts.lock().await.is_empty());
    stop.send(true)?;
    task.await??;
    Ok(())
}

#[tokio::test]
async fn pre_io_failures_have_a_durable_backoff_and_bounded_retry_budget()
-> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(
        ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())).with_retry_backoff(20),
    );
    let original = service
        .admit(command("retry", "a"))
        .await?
        .deliveries
        .remove(0);
    let io = Arc::new(RecordingIo {
        service: service.clone(),
        sent: Default::default(),
        aborts: Default::default(),
        fail_send: false,
        fail_registration: true,
    });
    let mut scheduler = runtime(service.clone(), io.clone());
    scheduler.config.max_safe_retries = 2;
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(scheduler.run(shutdown));
    let failed = wait_status(&service, "retry", Status::Failed).await?;
    assert_eq!(failed.attempt_no, 3);
    assert_eq!(failed.run_id, original.run_id);
    assert!(failed.updated_at_ms >= original.created_at_ms + 40);
    assert!(io.sent.lock().await.is_empty());
    stop.send(true)?;
    task.await??;
    Ok(())
}

fn event(row: &PersistedMessageDelivery, event: Event) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand {
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        event,
        now_ms: chrono::Utc::now().timestamp_millis(),
        request_id: None,
        actor_id: Some("human".into()),
        reply: None,
        transport_context_json: None,
        deadline_at_ms: None,
    }
}

async fn wait_submitted(
    service: &ManagedMessageDelivery,
    source: &str,
) -> Result<PersistedMessageDelivery, Box<dyn std::error::Error>> {
    Ok(tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if let Some(row) = service
                .snapshot(None)
                .await?
                .into_iter()
                .find(|d| d.source_message_id == source && d.submitted_at_ms.is_some())
            {
                return Ok::<_, ManagedDeliveryError>(row);
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await??)
}

#[tokio::test]
async fn fifo_waits_for_terminal_and_shutdown_does_not_requeue_sends()
-> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(
        MemoryMessageRepo::new(),
    )));
    service.admit(command("first", "a")).await?;
    service.admit(command("second", "a")).await?;
    service.admit(command("parallel", "b")).await?;
    let io = Arc::new(RecordingIo {
        service: service.clone(),
        sent: Default::default(),
        aborts: Default::default(),
        fail_send: false,
        fail_registration: false,
    });
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(runtime(service.clone(), io.clone()).run(shutdown));
    tokio::time::timeout(Duration::from_secs(3), async {
        while io.sent.lock().await.len() < 2 {
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await?;
    assert!(!io.sent.lock().await.contains(&"second".into()));
    // Wait for submission metadata so this deliberate test update isn't racing it.
    let first = wait_submitted(&service, "first").await?;
    let running = service.transition(event(&first, Event::Accepted)).await?;
    tokio::time::sleep(Duration::from_millis(10)).await;
    assert!(!io.sent.lock().await.contains(&"second".into()));
    service
        .transition(event(&running, Event::Completed))
        .await?;
    wait_status(&service, "second", Status::Dispatching).await?;
    stop.send(true)?;
    task.await??;
    wait_status(&service, "second", Status::Unknown).await?;
    assert_eq!(
        io.sent
            .lock()
            .await
            .iter()
            .filter(|id| id.as_str() == "first")
            .count(),
        1
    );
    Ok(())
}

#[tokio::test]
async fn active_abort_has_durable_identity_and_queued_cancel_never_reaches_bot()
-> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(
        MemoryMessageRepo::new(),
    )));
    service.admit(command("first", "a")).await?;
    let queued = service
        .admit(command("cancelled-queued", "a"))
        .await?
        .deliveries
        .remove(0);
    service
        .transition(event(&queued, Event::CancelRequested))
        .await?;
    let io = Arc::new(RecordingIo {
        service: service.clone(),
        sent: Default::default(),
        aborts: Default::default(),
        fail_send: false,
        fail_registration: false,
    });
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(runtime(service.clone(), io.clone()).run(shutdown));
    let first = wait_submitted(&service, "first").await?;
    service
        .transition(event(&first, Event::CancelRequested))
        .await?;
    wait_status(&service, "first", Status::Cancelled).await?;
    stop.send(true)?;
    task.await??;
    assert_eq!(*io.sent.lock().await, vec!["first"]);
    assert_eq!(*io.aborts.lock().await, vec!["first"]);
    Ok(())
}

#[tokio::test]
async fn uncertain_send_is_not_retried_after_scheduler_restart()
-> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(
        MemoryMessageRepo::new(),
    )));
    service.admit(command("first", "a")).await?;
    service.admit(command("second", "a")).await?;
    let io = Arc::new(RecordingIo {
        service: service.clone(),
        sent: Default::default(),
        aborts: Default::default(),
        fail_send: true,
        fail_registration: false,
    });
    for _ in 0..2 {
        let (stop, shutdown) = tokio::sync::watch::channel(false);
        let task = tokio::spawn(runtime(service.clone(), io.clone()).run(shutdown));
        wait_status(&service, "first", Status::Unknown).await?;
        tokio::time::sleep(Duration::from_millis(20)).await;
        stop.send(true)?;
        task.await??;
    }
    assert_eq!(*io.sent.lock().await, vec!["first"]);
    Ok(())
}
