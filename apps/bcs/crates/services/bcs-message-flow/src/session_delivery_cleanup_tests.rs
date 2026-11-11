use super::*;
use crate::managed_delivery::ManagedMessageDelivery;
use bcs_domain::message_delivery::{DeliveryFlowKind, DeliveryWaitReason};
use bcs_domain::{NewMessage, SenderType};
use bcs_service_api::core::message_delivery::DeliveryScheduleEntry;
use bcs_service_api::port::repo::message_delivery::*;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

/// Loses the version race for the first `races` cancels, as if the
/// scheduler committed a transition between the lane lookup and the cancel.
struct Racing { inner: Arc<ManagedMessageDelivery>, races: AtomicUsize }

#[async_trait::async_trait]
impl ManagedMessageDeliveryService for Racing {
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
        if self.races.load(Ordering::SeqCst) > 0 {
            self.races.fetch_sub(1, Ordering::SeqCst);
            return Err(ManagedDeliveryError::Conflict);
        }
        self.inner.transition(command).await
    }
}

async fn queued(races: usize) -> (Racing, PersistedMessageDelivery) {
    queued_for("bot", races).await
}

async fn queued_for(bot: &str, races: usize) -> (Racing, PersistedMessageDelivery) {
    let inner = Arc::new(ManagedMessageDelivery::new(Arc::new(bcs_message_store::MemoryMessageRepo::new())));
    let row = inner.admit(AdmitMessageDeliveries {
        display_message: None, message_id: "m".into(), flow_kind: DeliveryFlowKind::Group, now_ms: 1,
        expire_at_ms: None, event: None,
        message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None,
            group_id: "g".into(), session_id: "s".into(), sender_id: "human".into(), sender_type: SenderType::Human,
            message_type: "chat".into(), content: serde_json::json!({"text": "m"}), client_msg_id: Some("m".into()),
            owner_bot_id: None, created_at: 1, run_id: String::new() },
        targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: bot.into(), kind: DeliveryType::Send,
            max_queued: 10, semantic_projection_json: serde_json::json!({"version": 1}), run_id: None }],
    }).await.unwrap().deliveries.remove(0);
    (Racing { inner, races: AtomicUsize::new(races) }, row)
}

async fn status(service: &Racing, row: &PersistedMessageDelivery) -> Status {
    service.lookup(DeliveryLookup::Id(row.delivery_id.clone())).await.unwrap().remove(0).state.status
}

#[tokio::test]
async fn a_lost_version_race_is_reloaded_and_retried() {
    let (service, row) = queued(2).await;
    cancel_session(&service, "s", "session_deleted").await.unwrap();
    assert_eq!(status(&service, &row).await, Status::Cancelled);
}

#[tokio::test]
async fn a_row_that_stays_active_fails_the_cleanup() {
    let (service, row) = queued(CANCEL_ATTEMPTS).await;
    let error = cancel_session(&service, "s", "session_deleted").await.unwrap_err();
    assert!(error.to_string().contains("1 delivery cancellation(s) failed"), "{error}");
    assert_eq!(status(&service, &row).await, Status::Queued, "the Session must not be deleted over a live row");
}

#[tokio::test]
async fn lanes_of_bots_no_longer_in_the_session_are_cancelled_too() {
    // The cleanup is keyed by Session, not by the current participant list:
    // a Bot removed while its delivery was pending is still covered.
    let (service, row) = queued_for("removed-bot", 0).await;
    cancel_session(&service, "s", "session_deleted").await.unwrap();
    assert_eq!(status(&service, &row).await, Status::Cancelled);
    assert_eq!(service.lookup(DeliveryLookup::SessionUnfinished { session: "s".into(), after: None, limit: 10 }).await.unwrap().len(), 0);
}
