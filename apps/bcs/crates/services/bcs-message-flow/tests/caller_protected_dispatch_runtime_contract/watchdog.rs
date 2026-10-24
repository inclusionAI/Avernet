//! Fault injection at the protected send port, exercising the real scheduler.
use super::*;

struct PendingSender {
    entered: Arc<tokio::sync::Notify>,
    dropped: Arc<std::sync::atomic::AtomicBool>,
}

#[async_trait]
impl CallerExecutionSendPort for PendingSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        _command: bcs_service_api::port::SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        struct DropFlag(Arc<std::sync::atomic::AtomicBool>);
        impl Drop for DropFlag {
            fn drop(&mut self) { self.0.store(true, std::sync::atomic::Ordering::SeqCst); }
        }
        let _guard = DropFlag(self.dropped.clone());
        self.entered.notify_one();
        std::future::pending().await
    }
}

async fn hanging_fixture() -> (DeliveryRuntime, Arc<ManagedMessageDelivery>, Arc<tokio::sync::Notify>, Arc<std::sync::atomic::AtomicBool>) {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let entered = Arc::new(tokio::sync::Notify::new());
    let dropped = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let pipeline = Arc::new(CallerDispatchPipeline::new(
        seeded_handoff("caller-ok").await,
        Arc::new(CallerContainerPreparation::new(Arc::new(StubGateway))),
        Arc::new(StubIdentity),
        Arc::new(PendingSender { entered: entered.clone(), dropped: dropped.clone() }),
        600_000,
    ));
    service.admit(admission("caller-ok", builder_projection(true, true, true))).await.unwrap();
    (runtime(service.clone(), Arc::new(RecordingIo::default()), Some(pipeline)), service, entered, dropped)
}

#[tokio::test]
async fn protected_send_timeout_converges_unknown_without_retry() {
    let (mut worker, service, entered, dropped) = hanging_fixture().await;
    worker.config.io_timeout = Duration::from_millis(40);
    let (stop, task) = start(worker);
    tokio::time::timeout(Duration::from_secs(1), entered.notified()).await.unwrap();
    let result = tokio::time::timeout(Duration::from_millis(500), run_until_status(
        &service, "caller-ok", |row| row.state.status == Status::Unknown,
    )).await;
    assert!(dropped.load(std::sync::atomic::Ordering::SeqCst), "the pending send future must be dropped");
    stop.send(true).unwrap();
    task.abort();
    let row = result.expect("protected send must leave Dispatching when its I/O budget expires");
    assert_eq!(row.attempt_no, 1);
    assert!(row.state.may_have_been_sent, "timeout is not proof of non-delivery");
}

#[tokio::test]
async fn hung_protected_send_does_not_block_queue_expiry_or_shutdown() {
    let (mut worker, service, entered, dropped) = hanging_fixture().await;
    worker.config.io_timeout = Duration::from_secs(10);
    let (stop, mut task) = start(worker);
    tokio::time::timeout(Duration::from_secs(1), entered.notified()).await.unwrap();
    let mut queued = admission("expires-behind-caller", builder_projection(true, true, true));
    queued.now_ms = now_ms();
    queued.expire_at_ms = Some(now_ms() + 30);
    service.admit(queued).await.unwrap();
    let expired = tokio::time::timeout(Duration::from_millis(500), run_until_status(
        &service, "expires-behind-caller", |row| row.state.status == Status::Expired,
    )).await;
    stop.send(true).unwrap();
    let stopped = tokio::time::timeout(Duration::from_millis(500), &mut task).await;
    task.abort();
    assert!(expired.is_ok(), "expiry must run while a protected send is pending");
    assert!(stopped.is_ok(), "shutdown must interrupt a pending protected dispatch");
    assert!(dropped.load(std::sync::atomic::Ordering::SeqCst));
}

#[tokio::test]
async fn hung_protected_send_does_not_block_run_deadline() {
    let (mut worker, service, entered, dropped) = hanging_fixture().await;
    worker.config.io_timeout = Duration::from_secs(10);
    worker.config.run_timeout = Duration::from_millis(40);
    let (stop, task) = start(worker);
    tokio::time::timeout(Duration::from_secs(1), entered.notified()).await.unwrap();
    let result = tokio::time::timeout(Duration::from_millis(500), run_until_status(
        &service, "caller-ok", |row| row.state.status == Status::CancelUnknown,
    )).await;
    stop.send(true).unwrap();
    task.abort();
    assert!(result.is_ok(), "run deadline must drive cancellation independently of the send");
    assert!(dropped.load(std::sync::atomic::Ordering::SeqCst), "cancel must stop the local dispatch future");
}

#[tokio::test]
async fn lapsed_run_deadline_converts_uncertain_caller_row_to_cancel_intent() {
    // The lane-freeze class: a dispatch that went Unknown keeps no queue TTL
    // (StartSend cleared it and the transition never re-arms one for caller
    // rows), so its lapsed run deadline is the only durable bound — the
    // control loop must convert it into a durable cancel intent (spec 队列
    // TTL 与执行期限), not leave the lane paused forever waiting for
    // stop/terminal evidence a never-accepted dispatch cannot produce.
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let handoff = seeded_handoff("caller-ok").await;
    let pipeline = caller_pipeline(handoff, CallerDispatchAck::Unknown);
    let mut worker = runtime(service.clone(), Arc::new(RecordingIo::default()), Some(pipeline));
    worker.config.run_timeout = Duration::from_millis(40);
    service.admit(admission("caller-ok", builder_projection(true, true, true))).await.unwrap();
    let (stop, task) = start(worker);
    let uncertain = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Unknown).await;
    assert!(uncertain.run_deadline_at_ms.is_some(), "StartSend commits the run budget on the row");
    let converged = tokio::time::timeout(Duration::from_secs(2), run_until_status(
        &service, "caller-ok",
        |row| matches!(row.state.status, Status::Cancelling | Status::CancelUnknown),
    )).await;
    worker_stop(stop, task).await;
    let row = converged.expect("the lapsed run deadline must convert the uncertain row into a cancel intent");
    assert!(
        row.cancel_requested_at_ms.is_some(),
        "the conversion must leave durable cancellation evidence on the row"
    );
}

#[tokio::test]
async fn timeout_before_start_send_fails_without_possible_send() {
    let (mut worker, service, _, _) = hanging_fixture().await;
    worker.config.io_timeout = Duration::from_millis(40);
    worker.preparation = Arc::new(RecordingIo { hang_caller_target: true, ..Default::default() });
    let (stop, task) = start(worker);
    let result = tokio::time::timeout(Duration::from_millis(500), run_until_status(
        &service, "caller-ok", |row| row.state.status == Status::Failed,
    )).await;
    worker_stop(stop, task).await;
    let row = result.expect("target resolution is covered by the protected I/O budget");
    assert_eq!(row.attempt_no, 0);
    assert!(!row.state.may_have_been_sent);
}

#[tokio::test]
async fn explicit_cancel_interrupts_pending_send() {
    let (mut worker, service, entered, dropped) = hanging_fixture().await;
    worker.config.io_timeout = Duration::from_secs(10);
    let (stop, task) = start(worker);
    tokio::time::timeout(Duration::from_secs(1), entered.notified()).await.unwrap();
    let row = run_until_status(&service, "caller-ok", |r| r.state.status == Status::Dispatching).await;
    service.transition(bcs_service_api::DeliveryTransitionCommand {
        delivery_id: row.delivery_id, expected_state_version: row.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelRequested,
        now_ms: now_ms(), deadline_at_ms: Some(now_ms() + 1_000),
        request_id: None, actor_id: None, reply: None, transport_context_json: None,
        caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None,
    }).await.unwrap();
    let result = tokio::time::timeout(Duration::from_millis(500), run_until_status(
        &service, "caller-ok", |r| r.state.status == Status::CancelUnknown,
    )).await;
    assert!(dropped.load(std::sync::atomic::Ordering::SeqCst));
    worker_stop(stop, task).await;
    assert!(result.is_ok(), "explicit cancellation must not wait for the send timeout");
}
