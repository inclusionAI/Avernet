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

#[tokio::test]
async fn transport_evidence_outlives_the_bounded_row_retries() -> Result<(), Box<dyn std::error::Error>> {
    // More consecutive storage failures than the row retry budget (3): the
    // send and abort outcomes are one-shot evidence and must still persist.
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let faults = Arc::new(failing_delivery::FailingDelivery::new(service.clone()));
    faults.arm("submitted", 4);
    faults.arm("aborted", 4);
    service.admit(command("evidence", "session")).await?;
    let io = Arc::new(RecordingIo { service: service.clone(), sent: Default::default(), aborts: Default::default(), fail_send: false, fail_registration: false });
    let mut worker = runtime(service.clone(), io.clone());
    worker.service = faults.clone();
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    let row = tokio::time::timeout(Duration::from_secs(10), wait_submitted(&service, "evidence")).await??;
    service.transition(DeliveryTransitionCommand { caller_dispatch_commit: None, delivery_id: row.delivery_id,
        expected_state_version: row.state.state_version, event: Event::CancelRequested,
        now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None, reply: None,
        transport_context_json: None, deadline_at_ms: Some(chrono::Utc::now().timestamp_millis() + 10_000),
        caller_cancel: None, caller_terminal: None }).await?;
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            let row = service.snapshot(None).await.unwrap().remove(0);
            if row.state.status == Status::Cancelled { break; }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }).await?;
    assert_eq!(faults.remaining(), 0);
    assert_eq!(*io.sent.lock().await, vec!["evidence"], "never resent");
    assert_eq!(*io.aborts.lock().await, vec!["evidence"], "the confirmed abort was not lost");
    assert!(!task.is_finished());
    stop.send(true)?;
    task.await??;
    Ok(())
}

/// Wait reasons are advisory: an undecodable queued head must not fail the
/// whole update (it used to end every master epoch), and healthy rows in the
/// same batch still get their reason.
#[tokio::test]
async fn unreadable_rows_do_not_fail_wait_reason_updates() -> Result<(), Box<dyn std::error::Error>> {
    use bcs_db_api::{DbPlugin, DbStatement};
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new()?);
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('healthy-session', 'group', 'dev', '[]')")).await?;
    let service = ManagedMessageDelivery::new(Arc::new(bcs_message_store::MySqlMessageStore::sqlite(db.clone(), "dev".into())));
    let healthy = service.admit(command("healthy", "healthy-session")).await?.deliveries.remove(0);
    db.execute(DbStatement::new("INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json) VALUES ('corrupt','dev','m','bot-a','doomed','g',1,'not-a-flow-kind','send','queued',1,0,1,1,1,0,'{}')")).await?;
    service.update_wait_reasons(vec![("corrupt".into(), DeliveryWaitReason::BotOffline), (healthy.delivery_id.clone(), DeliveryWaitReason::BotOffline)], 5).await?;
    let row = service.snapshot(Some("healthy-session")).await?.remove(0);
    assert_eq!(row.wait_reason, Some(DeliveryWaitReason::BotOffline));
    Ok(())
}
