use std::{collections::BTreeMap, sync::{Arc, Mutex}};
use bcs_domain::message_delivery::{PersistedMessageDelivery, DeliveryWaitReason};
use bcs_service_api::{ManagedMessageDeliveryService, ManagedDeliveryError, DeliveryTransitionCommand};
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::core::message_delivery::{DeliveryScheduleEntry, DeliveryLifecycleEvent as Event};

pub struct FailingDelivery {
    pub inner: Arc<dyn ManagedMessageDeliveryService>,
    failures: Mutex<BTreeMap<String, usize>>,
    pub failed: tokio::sync::Notify,
}
impl FailingDelivery {
    pub fn new(inner: Arc<dyn ManagedMessageDeliveryService>) -> Self {
        Self { inner, failures: Default::default(), failed: Default::default() }
    }
    pub fn arm(&self, stage: &str, count: usize) { self.failures.lock().unwrap().insert(stage.into(), count); }
    fn check(&self, stage: &str) -> Result<(), ManagedDeliveryError> {
        let mut failures = self.failures.lock().unwrap();
        if let Some(count) = failures.get_mut(stage).filter(|n| **n > 0) {
            *count -= 1;
            self.failed.notify_one();
            return Err(MessageDeliveryRepoError::Storage(format!("injected {stage}")).into());
        }
        Ok(())
    }
    pub fn remaining(&self) -> usize { self.failures.lock().unwrap().values().sum() }
}

#[async_trait::async_trait]
impl ManagedMessageDeliveryService for FailingDelivery {
    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<BoundDeliveryContexts, ManagedDeliveryError> { self.inner.bounded_contexts(carrier, limit).await }
    async fn lookup(&self, scope: DeliveryLookup) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        self.check("lookup")?; self.inner.lookup(scope).await
    }
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, ManagedDeliveryError> { self.check("queued_bots")?; self.inner.queued_bots(after, limit).await }
    async fn active_count(&self, bot: &str) -> Result<u64, ManagedDeliveryError> { self.check("active_count")?; self.inner.active_count(bot).await }
    async fn lane_blocked(&self, row: &PersistedMessageDelivery) -> Result<bool, ManagedDeliveryError> { self.inner.lane_blocked(row).await }
    async fn queued_heads(&self, bot: &str, after: &str, limit: usize) -> Result<Vec<DeliveryScheduleEntry>, ManagedDeliveryError> { self.check("queued_heads")?; self.inner.queued_heads(bot, after, limit).await }
    async fn work_batch(&self, kind: DeliveryWorkBatch, now: i64, after: &str, limit: usize) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.check("scan")?; self.inner.work_batch(kind, now, after, limit).await }
    async fn queue_statistics(&self) -> Result<Vec<DeliveryQueueStatistic>, ManagedDeliveryError> { self.inner.queue_statistics().await }
    async fn unfinished(&self) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.unfinished().await }
    async fn update_wait_reasons(&self, waiting: Vec<(String, DeliveryWaitReason)>, now: i64) -> Result<(), ManagedDeliveryError> { self.check("wait_reason")?; self.inner.update_wait_reasons(waiting, now).await }
    async fn accept_run(&self, request: &str, bot: &str, alias: Option<&str>, now: i64) -> Result<Option<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.accept_run(request, bot, alias, now).await }
    async fn admit(&self, cmd: AdmitMessageDeliveries) -> Result<DeliveryAdmissionResult, ManagedDeliveryError> { self.inner.admit(cmd).await }
    async fn snapshot(&self, session: Option<&str>) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.snapshot(session).await }
    async fn recover(&self, now: i64) -> Result<(), ManagedDeliveryError> { self.check("recover")?; self.inner.recover(now).await }
    async fn transition(&self, command: DeliveryTransitionCommand) -> Result<PersistedMessageDelivery, ManagedDeliveryError> {
        let stage = match command.event {
            Event::StartSend => "send_start", Event::Submitted => "submitted",
            Event::Aborted => "aborted", Event::StartAbort => "abort_start",
            Event::Completed => "completed", Event::Failed => "failed",
            Event::TransportUnknown => "unknown", _ => "transition",
        };
        self.check(stage)?;
        let result = self.inner.transition(command).await?;
        self.check(&format!("{stage}_after_commit"))?;
        Ok(result)
    }
}
