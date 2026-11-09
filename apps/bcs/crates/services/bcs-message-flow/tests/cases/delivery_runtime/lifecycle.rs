use super::*;

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
    service.transition(DeliveryTransitionCommand { caller_dispatch_commit: None, delivery_id: row.delivery_id,
        expected_state_version: row.state.state_version, event: Event::CancelRequested,
        now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None, reply: None,
        transport_context_json: None, deadline_at_ms: Some(chrono::Utc::now().timestamp_millis() + 5000), caller_cancel: None, caller_terminal: None }).await?;
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
#[tokio::test]
async fn transport_not_sent_is_terminal_unless_explicitly_retryable() -> Result<(), Box<dyn std::error::Error>> {
    struct NotSentIo { retryable: bool, nested: bool, calls: std::sync::atomic::AtomicUsize }
    #[async_trait]
    impl BotDeliveryPort for NotSentIo {
        async fn is_available(&self, _: &BotDeliveryTarget) -> bool { true }
        async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
            self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            let error = ServiceError::DeliveryNotSent { code: "preflight_rejected", retryable: self.retryable };
            if self.nested { Ok(BotDeliveryResult { target_bot_id: cmd.target_bot_id().into(), delivered: false, error: Some(error) }) } else { Err(error) }
        }
    }
    for retryable in [false, true] {
        for nested in [false, true] {
            let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())).with_retry_backoff(1));
            service.admit(command("not-sent", "a")).await?;
            let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
            let mut worker = runtime(service.clone(), io);
            let transport = Arc::new(NotSentIo { retryable, nested, calls: Default::default() });
            worker.transport = transport.clone();
            worker.config.max_safe_retries = 1;
            let (stop, shutdown) = tokio::sync::watch::channel(false);
            let task = tokio::spawn(worker.run(shutdown));
            let failed = wait_status(&service, "not-sent", Status::Failed).await?;
            assert!(!failed.state.may_have_been_sent);
            assert_eq!(failed.attempt_no, if retryable { 2 } else { 1 });
            assert_eq!(transport.calls.load(std::sync::atomic::Ordering::SeqCst), failed.attempt_no as usize);
            assert_eq!(service.active_count("bot").await?, 0);
            stop.send(true)?; task.await??;
        }
    }
    Ok(())
}
#[tokio::test]
async fn explicit_delivery_rejection_is_terminal_releases_lane_and_is_not_retried(
) -> Result<(), Box<dyn std::error::Error>> {
    struct RejectThenAccept {
        calls: std::sync::atomic::AtomicUsize,
    }
    #[async_trait]
    impl BotDeliveryPort for RejectThenAccept {
        async fn is_available(&self, _: &BotDeliveryTarget) -> bool {
            true
        }

        async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
            let call = self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            if call == 0 {
                return Ok(BotDeliveryResult {
                    target_bot_id: cmd.target_bot_id().into(),
                    delivered: false,
                    error: Some(ServiceError::InternalError("provider returned HTTP 500".into())),
                });
            }
            Ok(BotDeliveryResult {
                target_bot_id: cmd.target_bot_id().into(),
                delivered: true,
                error: None,
            })
        }
    }

    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    service.admit(command("rejected", "same-lane")).await?;
    service.admit(command("successor", "same-lane")).await?;
    let preparation = Arc::new(RecordingIo {
        service: service.clone(),
        sent: Default::default(),
        aborts: Default::default(),
        fail_send: false,
        fail_registration: false,
    });
    let transport = Arc::new(RejectThenAccept { calls: Default::default() });
    let mut worker = runtime(service.clone(), preparation);
    worker.transport = transport.clone();
    worker.config.max_safe_retries = 5;
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));

    let failed = wait_status(&service, "rejected", Status::Failed).await?;
    assert!(failed.state.may_have_been_sent);
    assert_eq!(failed.attempt_no, 1);
    assert_eq!(failed.last_error_code, None);
    let unchanged = service.transition(event(&failed, Event::Completed)).await?;
    assert_eq!(unchanged.state.status, Status::Failed);
    wait_status(&service, "successor", Status::Dispatching).await?;
    assert_eq!(transport.calls.load(std::sync::atomic::Ordering::SeqCst), 2);

    stop.send(true)?;
    task.await??;
    Ok(())
}
#[tokio::test]
async fn startup_recovery_grace_bridges_reconnect_but_does_not_queue_offline_bots(
) -> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    service.admit(command("reconnect", "lane")).await?;
    let io = Arc::new(RecordingIo {
        service: service.clone(),
        sent: Default::default(),
        aborts: Default::default(),
        fail_send: false,
        fail_registration: false,
    });
    let available = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let mut worker = runtime(service.clone(), io.clone());
    worker.preparation = Arc::new(AvailabilityPreparation {
        inner: io.clone(),
        available: available.clone(),
    });
    worker.config.startup_recovery_grace = Duration::from_millis(200);
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    tokio::time::timeout(Duration::from_secs(1), async {
        loop {
            let row = service.snapshot(None).await.unwrap().remove(0);
            if row.wait_reason
                == Some(bcs_domain::message_delivery::DeliveryWaitReason::BotOffline)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await?;
    available.store(true, std::sync::atomic::Ordering::SeqCst);
    wait_status(&service, "reconnect", Status::Dispatching).await?;
    assert_eq!(io.sent.lock().await.as_slice(), ["reconnect"]);
    stop.send(true)?;
    task.await??;

    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    service.admit(command("offline", "lane")).await?;
    let io = Arc::new(RecordingIo {
        service: service.clone(),
        sent: Default::default(),
        aborts: Default::default(),
        fail_send: false,
        fail_registration: false,
    });
    let mut worker = runtime(service.clone(), io.clone());
    worker.preparation = Arc::new(AvailabilityPreparation {
        inner: io.clone(),
        available: Arc::new(std::sync::atomic::AtomicBool::new(false)),
    });
    worker.config.startup_recovery_grace = Duration::from_millis(10);
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    let failed = wait_status(&service, "offline", Status::Failed).await?;
    assert!(!failed.state.may_have_been_sent);
    assert_eq!(failed.attempt_no, 0);
    assert!(io.sent.lock().await.is_empty());
    stop.send(true)?;
    task.await??;
    Ok(())
}
#[tokio::test]
async fn websocket_disconnect_before_send_is_terminal_and_not_retried(
) -> Result<(), Box<dyn std::error::Error>> {
    struct DisconnectedTransport(std::sync::atomic::AtomicUsize);

    #[async_trait]
    impl BotDeliveryPort for DisconnectedTransport {
        async fn is_available(&self, _: &BotDeliveryTarget) -> bool {
            true
        }

        async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
            self.0.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Ok(BotDeliveryResult {
                target_bot_id: cmd.target_bot_id().into(),
                delivered: false,
                error: Some(ServiceError::BotNotConnected(cmd.target_bot_id().into())),
            })
        }
    }

    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    service.admit(command("disconnected", "lane")).await?;
    let preparation = Arc::new(RecordingIo {
        service: service.clone(),
        sent: Default::default(),
        aborts: Default::default(),
        fail_send: false,
        fail_registration: false,
    });
    let transport = Arc::new(DisconnectedTransport(Default::default()));
    let mut worker = runtime(service.clone(), preparation);
    worker.transport = transport.clone();
    worker.config.max_safe_retries = 5;
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    let failed = wait_status(&service, "disconnected", Status::Failed).await?;
    assert!(failed.state.may_have_been_sent);
    assert_eq!(failed.attempt_no, 1);
    assert_eq!(failed.last_error_code, None);
    assert_eq!(transport.0.load(std::sync::atomic::Ordering::SeqCst), 1);
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
#[tokio::test]
async fn uncertain_send_expires_after_a_fresh_queue_ttl_and_unblocks_its_lane()
-> Result<(), Box<dyn std::error::Error>> {
    use bcs_config_api::message_delivery::{BotDeliveryMode, DeliveryPolicyRecord};
    let repo = Arc::new(MemoryMessageRepo::new());
    let mut record = DeliveryPolicyRecord::default();
    record.policy.flow_enabled.group = true;
    record.policy.defaults.mode = BotDeliveryMode::Enforce;
    record.policy.queue_ttl_ms = Some(50);
    let policy = Arc::new(bcs_message_flow::delivery_policy::LiveDeliveryPolicy::new(repo.clone(), record));
    let service = Arc::new(ManagedMessageDelivery::new(repo).with_policy(policy));
    let now = chrono::Utc::now().timestamp_millis();
    let mut first = command("unknown-first", "lane");
    first.now_ms = now;
    first.message.created_at = now as u64;
    service.admit(first).await?;
    let io = Arc::new(RecordingIo {
        service: service.clone(), sent: Default::default(), aborts: Default::default(),
        fail_send: true, fail_registration: false,
    });
    let mut worker = runtime(service.clone(), io.clone());
    worker.config.expiry_tick = Duration::from_millis(5);
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    wait_status(&service, "unknown-first", Status::Unknown).await?;
    tokio::time::sleep(Duration::from_millis(20)).await;
    let now = chrono::Utc::now().timestamp_millis();
    let mut second = command("unknown-second", "lane");
    second.now_ms = now;
    second.message.created_at = now as u64;
    service.admit(second).await?;
    let first = wait_status(&service, "unknown-first", Status::Expired).await?;
    assert!(first.state.may_have_been_sent);
    assert_eq!(first.last_error_code.as_deref(), Some("unknown_ttl_expired"));
    let second = wait_status(&service, "unknown-second", Status::Expired).await?;
    assert!(second.state.may_have_been_sent);
    assert_eq!(*io.sent.lock().await, vec!["unknown-first", "unknown-second"]);
    stop.send(true)?;
    task.await??;
    Ok(())
}
#[tokio::test]
async fn caller_rows_drop_the_queue_ttl_and_never_expire_into_lane_release() -> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let now = chrono::Utc::now().timestamp_millis();

    // StartSend clears the queue TTL only for caller rows; the ordinary
    // row beside it keeps its expiry.
    service.admit(caller_command("caller-ttl", Some(now + 1_000))).await?;
    let mut plain_admission = command("plain-exp", "session3");
    plain_admission.expire_at_ms = Some(now + 1_000);
    service.admit(plain_admission).await?;

    let caller_row = service
        .snapshot(None)
        .await?
        .into_iter()
        .find(|row| row.source_message_id == "caller-ttl")
        .unwrap();
    assert!(caller_row.expire_at_ms.is_some(), "precondition: admitted with a queue TTL");
    let started = service.transition(caller_transition(&caller_row, Event::StartSend)).await?;
    assert_eq!(started.expire_at_ms, None, "caller StartSend clears the queue TTL in the same CAS");
    assert!(started.run_deadline_at_ms.is_some(), "the run deadline survives");

    let plain_row = service
        .snapshot(None)
        .await?
        .into_iter()
        .find(|row| row.source_message_id == "plain-exp")
        .unwrap();
    let plain_started = service.transition(caller_transition(&plain_row, Event::StartSend)).await?;
    assert_eq!(plain_started.expire_at_ms, Some(now + 1_000), "ordinary rows keep the queue TTL");

    // An uncertain caller attempt can never be expired into a lane release:
    // the CAS refuses QueueExpired regardless of the caller.
    let uncertain = service
        .transition(DeliveryTransitionCommand {
            caller_dispatch_commit: None,
            delivery_id: started.delivery_id.clone(),
            expected_state_version: started.state.state_version,
            event: Event::TransportUnknown,
            now_ms: chrono::Utc::now().timestamp_millis(),
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: None,
            deadline_at_ms: None,
            caller_cancel: None,
            caller_terminal: None,
        })
        .await?;
    assert_eq!(uncertain.state.status, Status::Unknown);
    assert_eq!(uncertain.expire_at_ms, None, "entering Unknown does not re-arm the caller TTL");
    let error = service
        .transition(caller_transition(&uncertain, Event::QueueExpired))
        .await
        .expect_err("maybe-sent caller rows never expire into stopped");
    assert!(matches!(error, ManagedDeliveryError::Conflict));
    let intact = service
        .snapshot(None)
        .await?
        .into_iter()
        .find(|row| row.delivery_id == uncertain.delivery_id)
        .unwrap();
    assert_eq!(intact.state.status, Status::Unknown, "no stop without evidence");

    // A queued caller row that never started still expires normally.
    service.admit(caller_command("caller-queued", Some(now - 1))).await?;
    let queued = service
        .snapshot(None)
        .await?
        .into_iter()
        .find(|row| row.source_message_id == "caller-queued")
        .unwrap();
    let expired = service.transition(caller_transition(&queued, Event::QueueExpired)).await?;
    assert_eq!(expired.state.status, Status::Expired);
    Ok(())
}
