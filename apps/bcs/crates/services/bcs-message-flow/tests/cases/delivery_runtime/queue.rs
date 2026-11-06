use super::*;

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
    policy.flow_enabled.system = true;
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
    policy.flow_enabled.system = false;
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
    policy.flow_enabled.system = true;
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
async fn policy_less_bots_get_a_no_policy_wait_marker() -> Result<(), Box<dyn std::error::Error>> {
    // §15.98 visibility: a bot outside the runtime policy map used to be
    // invisible — never scanned, never claimed, no wait_reason. The rows
    // now carry NoPolicy so the stall is observable on the row itself.
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    let mut scheduler = runtime(service.clone(), io.clone());
    // The harness runtime only knows "bot"; this admission targets a bot
    // the policy map has no profile for.
    scheduler.policy = None;
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(scheduler.run(shutdown));
    let mut orphan = command("orphan", "orphan-session");
    orphan.targets[0].target_bot_id = "unknown-bot".into();
    service.admit(orphan).await?;

    let marked = tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let rows = service.snapshot(None).await?;
            if let Some(row) = rows.iter().find(|r| r.source_message_id == "orphan") {
                if row.wait_reason == Some(bcs_domain::message_delivery::DeliveryWaitReason::NoPolicy) {
                    return Ok::<(), Box<dyn std::error::Error>>(());
                }
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await??;
    drop(marked);
    assert!(service.snapshot(None).await?.iter().find(|r| r.source_message_id == "orphan").unwrap().wait_reason
        == Some(bcs_domain::message_delivery::DeliveryWaitReason::NoPolicy));
    stop.send(true)?;
    task.await??;
    Ok(())
}
