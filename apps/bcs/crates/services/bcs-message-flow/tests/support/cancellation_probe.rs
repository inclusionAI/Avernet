//! Controls lookup timing/failures only; real service decisions and commits stay intact.
use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
use bcs_domain::message_delivery::{PersistedMessageDelivery, DeliveryWaitReason};
use bcs_service_api::{ManagedMessageDeliveryService, ManagedDeliveryError, DeliveryTransitionCommand};
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::core::message_delivery::DeliveryScheduleEntry;

pub struct CancellationProbe {
    inner: Arc<dyn ManagedMessageDeliveryService>,
    pub lookup_calls: AtomicUsize,
    pub initial_snapshots: Option<tokio::sync::Barrier>,
    pub fail_refresh: bool,
}
impl CancellationProbe {
    pub fn new(inner: Arc<dyn ManagedMessageDeliveryService>) -> Self {
        Self { inner, lookup_calls: AtomicUsize::new(0), initial_snapshots: None, fail_refresh: false }
    }
}

#[async_trait::async_trait]
impl ManagedMessageDeliveryService for CancellationProbe {
    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<BoundDeliveryContexts, ManagedDeliveryError> { self.inner.bounded_contexts(carrier, limit).await }
    async fn lookup(&self, scope: DeliveryLookup) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        let call = self.lookup_calls.fetch_add(1, Ordering::SeqCst);
        if self.fail_refresh && call > 0 {
            return Err(MessageDeliveryRepoError::Storage("injected confirmation read failure".into()).into());
        }
        let rows = self.inner.lookup(scope).await?;
        if call < 2 {
            if let Some(barrier) = &self.initial_snapshots {
                // Both requests hold the initial queued snapshot before either can transition.
                barrier.wait().await;
            }
        }
        Ok(rows)
    }
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, ManagedDeliveryError> { self.inner.queued_bots(after, limit).await }
    async fn active_count(&self, bot: &str) -> Result<u64, ManagedDeliveryError> { self.inner.active_count(bot).await }
    async fn lane_blocked(&self, row: &PersistedMessageDelivery) -> Result<bool, ManagedDeliveryError> { self.inner.lane_blocked(row).await }
    async fn queued_heads(&self, bot: &str, after: &str, limit: usize) -> Result<Vec<DeliveryScheduleEntry>, ManagedDeliveryError> { self.inner.queued_heads(bot, after, limit).await }
    async fn work_batch(&self, kind: DeliveryWorkBatch, now: i64, after: &str, limit: usize) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.work_batch(kind, now, after, limit).await }
    async fn queue_statistics(&self) -> Result<Vec<DeliveryQueueStatistic>, ManagedDeliveryError> { self.inner.queue_statistics().await }
    async fn unfinished(&self) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.unfinished().await }
    async fn update_wait_reasons(&self, waiting: Vec<(String, DeliveryWaitReason)>, now: i64) -> Result<(), ManagedDeliveryError> { self.inner.update_wait_reasons(waiting, now).await }
    async fn accept_run(&self, request: &str, bot: &str, alias: Option<&str>, now: i64) -> Result<Option<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.accept_run(request, bot, alias, now).await }
    async fn admit(&self, command: AdmitMessageDeliveries) -> Result<DeliveryAdmissionResult, ManagedDeliveryError> { self.inner.admit(command).await }
    async fn snapshot(&self, session: Option<&str>) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> { self.inner.snapshot(session).await }
    async fn recover(&self, now: i64) -> Result<(), ManagedDeliveryError> { self.inner.recover(now).await }
    async fn transition(&self, command: DeliveryTransitionCommand) -> Result<PersistedMessageDelivery, ManagedDeliveryError> { self.inner.transition(command).await }
}
