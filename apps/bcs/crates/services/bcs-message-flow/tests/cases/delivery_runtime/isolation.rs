//! One row's deterministic fault must cost that row, never the scheduler.
use super::*;
use bcs_domain::message_delivery::DeliveryWaitReason;
use bcs_service_api::core::message_delivery::DeliveryScheduleEntry;

/// Every transition of the poisoned row fails deterministically, optionally
/// except the scheduler's quarantine settle.
struct PoisonedRow {
    inner: Arc<ManagedMessageDelivery>,
    poisoned: String,
    allow_quarantine: bool,
    outage: bool,
}

#[async_trait]
impl ManagedMessageDeliveryService for PoisonedRow {
    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<BoundDeliveryContexts, ManagedDeliveryError> { self.inner.bounded_contexts(carrier, limit).await }
    async fn lookup(&self, scope: DeliveryLookup) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.lookup(scope).await }
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, ManagedDeliveryError> { self.inner.queued_bots(after, limit).await }
    async fn active_count(&self, bot: &str) -> Result<u64, ManagedDeliveryError> { self.inner.active_count(bot).await }
    async fn lane_blocked(&self, row: &PersistedMessageDelivery) -> Result<bool, ManagedDeliveryError> { self.inner.lane_blocked(row).await }
    async fn queued_heads(&self, bot: &str, after: &str, limit: usize) -> Result<Vec<DeliveryScheduleEntry>, ManagedDeliveryError> { self.inner.queued_heads(bot, after, limit).await }
    async fn work_batch(&self, kind: DeliveryWorkBatch, now: i64, after: &str, limit: usize) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.work_batch(kind, now, after, limit).await }
    async fn queue_statistics(&self) -> Result<Vec<DeliveryQueueStatistic>, ManagedDeliveryError> { self.inner.queue_statistics().await }
    async fn unfinished(&self) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.unfinished().await }
    async fn update_wait_reasons(&self, waiting: Vec<(String, DeliveryWaitReason)>, now: i64) -> Result<(), ManagedDeliveryError> { self.inner.update_wait_reasons(waiting, now).await }
    async fn accept_run(&self, request: &str, bot: &str, alias: Option<&str>, now: i64) -> Result<Option<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.accept_run(request, bot, alias, now).await }
    async fn admit(&self, cmd: AdmitMessageDeliveries) -> Result<DeliveryAdmissionResult, ManagedDeliveryError> { self.inner.admit(cmd).await }
    async fn snapshot(&self, session: Option<&str>) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.snapshot(session).await }
    async fn recover(&self, now: i64) -> Result<(), ManagedDeliveryError> { self.inner.recover(now).await }
    async fn transition(&self, command: DeliveryTransitionCommand) -> Result<PersistedMessageDelivery, ManagedDeliveryError> {
        let quarantine = command.transport_context_json.as_ref()
            .and_then(|v| v.get("scheduler_quarantined")).is_some();
        if command.delivery_id == self.poisoned && !(quarantine && self.allow_quarantine) {
            return Err(if self.outage {
                MessageDeliveryRepoError::Storage("injected persistent outage".into())
            } else {
                MessageDeliveryRepoError::Invalid("injected deterministic row fault".into())
            }.into());
        }
        self.inner.transition(command).await
    }
}

async fn scenario(allow_quarantine: bool, outage: bool) -> Result<(), Box<dyn std::error::Error>> {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let mut expired = command("poisoned", "poisoned-session");
    expired.expire_at_ms = Some(2);
    let poisoned = service.admit(expired).await?.deliveries.remove(0);
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    let mut worker = runtime(service.clone(), io.clone());
    worker.service = Arc::new(PoisonedRow { inner: service.clone(), poisoned: poisoned.delivery_id.clone(), allow_quarantine, outage });
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    // The healthy row is admitted after the poisoned row has been seen.
    tokio::time::sleep(Duration::from_millis(50)).await;
    service.admit(command("healthy", "healthy-session")).await?;
    wait_submitted(&service, "healthy").await?;
    assert!(!task.is_finished(), "a row fault must never stop the scheduler");
    let row = service.snapshot(Some("poisoned-session")).await?.remove(0);
    if allow_quarantine && !outage {
        assert_eq!(row.state.status, Status::Failed, "never-sent row is settled");
        assert_eq!(row.last_error_code.as_deref(), Some("scheduler_quarantined"));
    } else {
        assert_eq!(row.state.status, Status::Queued, "an unsettleable row, or one behind an outage, is only skipped");
    }
    stop.send(true)?;
    task.await??;
    Ok(())
}

#[tokio::test]
async fn deterministic_row_fault_is_quarantined_and_the_queue_keeps_moving() -> Result<(), Box<dyn std::error::Error>> {
    scenario(true, false).await
}

#[tokio::test]
async fn unsettleable_row_is_skipped_and_the_queue_keeps_moving() -> Result<(), Box<dyn std::error::Error>> {
    scenario(false, false).await
}

#[tokio::test]
async fn persistent_storage_fault_never_settles_a_healthy_row() -> Result<(), Box<dyn std::error::Error>> {
    // Quarantine is allowed, but an outage is not a row fault: the row is
    // retried, then skipped, and keeps its Queued state.
    scenario(true, true).await
}
