//! A managed-delivery wrapper that pauses the CALLER between the
//! durable admission commit and the rest of the send — the exact
//! scheduling window a real queue worker may exploit (recheck R6):
//! the row is runnable the moment `admit` commits, before the
//! sender's own bookkeeping would run. Doubles only pause; every
//! storage and lifecycle decision stays with the REAL inner service.

use std::sync::Arc;

use bcs_domain::message_delivery::{DeliveryWaitReason, PersistedMessageDelivery};
use bcs_service_api::core::message_delivery::DeliveryScheduleEntry;
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, BoundDeliveryContexts, DeliveryAdmissionResult,
    DeliveryQueueStatistic, DeliveryWorkBatch,
};
use bcs_service_api::{
    DeliveryTransitionCommand, ManagedDeliveryError, ManagedMessageDeliveryService,
};

pub struct PausedDelivery {
    pub inner: Arc<dyn ManagedMessageDeliveryService>,
    /// Fires once after the durable admission committed, BEFORE the
    /// caller is allowed to continue.
    pub committed: tokio::sync::Notify,
    /// The test lets the caller resume through this gate.
    pub resume: tokio::sync::Notify,
}

impl PausedDelivery {
    pub fn new(inner: Arc<dyn ManagedMessageDeliveryService>) -> Self {
        Self {
            inner,
            committed: tokio::sync::Notify::new(),
            resume: tokio::sync::Notify::new(),
        }
    }
}

#[async_trait::async_trait]
impl ManagedMessageDeliveryService for PausedDelivery {
    async fn bounded_contexts(
        &self,
        carrier: &str,
        limit: usize,
    ) -> Result<BoundDeliveryContexts, ManagedDeliveryError> {
        self.inner.bounded_contexts(carrier, limit).await
    }
    async fn lookup(
        &self,
        scope: bcs_service_api::port::repo::message_delivery::DeliveryLookup,
    ) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        self.inner.lookup(scope).await
    }
    async fn queued_bots(
        &self,
        after: &str,
        limit: usize,
    ) -> Result<Vec<String>, ManagedDeliveryError> {
        self.inner.queued_bots(after, limit).await
    }
    async fn active_count(&self, bot: &str) -> Result<u64, ManagedDeliveryError> {
        self.inner.active_count(bot).await
    }
    async fn lane_blocked(
        &self,
        row: &PersistedMessageDelivery,
    ) -> Result<bool, ManagedDeliveryError> {
        self.inner.lane_blocked(row).await
    }
    async fn queued_heads(
        &self,
        bot: &str,
        after: &str,
        limit: usize,
    ) -> Result<Vec<DeliveryScheduleEntry>, ManagedDeliveryError> {
        self.inner.queued_heads(bot, after, limit).await
    }
    async fn work_batch(
        &self,
        kind: DeliveryWorkBatch,
        now: i64,
        after: &str,
        limit: usize,
    ) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        self.inner.work_batch(kind, now, after, limit).await
    }
    async fn queue_statistics(
        &self,
    ) -> Result<Vec<DeliveryQueueStatistic>, ManagedDeliveryError> {
        self.inner.queue_statistics().await
    }
    async fn unfinished(&self) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        self.inner.unfinished().await
    }
    async fn update_wait_reasons(
        &self,
        waiting: Vec<(String, DeliveryWaitReason)>,
        now: i64,
    ) -> Result<(), ManagedDeliveryError> {
        self.inner.update_wait_reasons(waiting, now).await
    }
    async fn accept_run(
        &self,
        request: &str,
        bot: &str,
        alias: Option<&str>,
        now: i64,
    ) -> Result<Option<PersistedMessageDelivery>, ManagedDeliveryError> {
        self.inner.accept_run(request, bot, alias, now).await
    }
    async fn admit(
        &self,
        cmd: AdmitMessageDeliveries,
    ) -> Result<DeliveryAdmissionResult, ManagedDeliveryError> {
        let result = self.inner.admit(cmd).await?;
        // The row is runnable NOW. Hold the caller so the test can
        // drive a worker transition before the sender continues.
        self.committed.notify_one();
        self.resume.notified().await;
        Ok(result)
    }
    async fn snapshot(
        &self,
        session: Option<&str>,
    ) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        self.inner.snapshot(session).await
    }
    async fn recover(&self, now: i64) -> Result<(), ManagedDeliveryError> {
        self.inner.recover(now).await
    }
    async fn transition(
        &self,
        command: DeliveryTransitionCommand,
    ) -> Result<PersistedMessageDelivery, ManagedDeliveryError> {
        self.inner.transition(command).await
    }
}
