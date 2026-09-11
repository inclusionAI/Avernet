use bcs_config_api::message_delivery::{BotDeliveryMode, DeliveryPolicy, DeliveryPolicyRecord};
use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{HumanActor, CallerContext};
use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort;
use std::sync::{Arc, atomic::Ordering};

fn admin() -> CallerContext {
    CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() })
}


struct SlowPolicyRepo {
    inner: MemoryMessageRepo,
    started: tokio::sync::Notify,
    release: tokio::sync::Notify,
}

#[async_trait::async_trait]
impl MessageDeliveryRepoPort for SlowPolicyRepo {
    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<bcs_service_api::port::repo::message_delivery::BoundDeliveryContexts, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { self.inner.bounded_contexts(carrier, limit).await }
    async fn lane_blocked(&self, row: &bcs_domain::message_delivery::PersistedMessageDelivery) -> Result<bool, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { self.inner.lane_blocked(row).await }
    async fn lookup(&self, q: bcs_service_api::port::repo::message_delivery::DeliveryLookup) -> Result<Vec<bcs_domain::message_delivery::PersistedMessageDelivery>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { self.inner.lookup(q).await }
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { self.inner.queued_bots(after, limit).await }
    async fn active_count(&self, bot: &str) -> Result<u64, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { self.inner.active_count(bot).await }
    async fn queued_heads(&self, bot: &str, after: &str, limit: usize) -> Result<Vec<bcs_service_api::core::message_delivery::DeliveryScheduleEntry>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { self.inner.queued_heads(bot, after, limit).await }
    async fn work_batch(&self, kind: bcs_service_api::port::repo::message_delivery::DeliveryWorkBatch, now: i64, after: &str, limit: usize) -> Result<Vec<bcs_domain::message_delivery::PersistedMessageDelivery>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { self.inner.work_batch(kind, now, after, limit).await }
    async fn queue_statistics(&self) -> Result<Vec<bcs_service_api::port::repo::message_delivery::DeliveryQueueStatistic>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { self.inner.queue_statistics().await }
    async fn load_policy(&self) -> Result<DeliveryPolicyRecord, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        self.inner.load_policy().await
    }
    async fn replace_policy(&self, expected: u64, record: DeliveryPolicyRecord) -> Result<(), bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        self.started.notify_one();
        self.release.notified().await;
        self.inner.replace_policy(expected, record).await
    }
    async fn admit(&self, _: bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries) -> Result<bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { unreachable!() }
    async fn list_deliveries(&self, _: Option<&str>) -> Result<Vec<bcs_domain::message_delivery::PersistedMessageDelivery>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { unreachable!() }
    async fn commit_transition(&self, _: Vec<bcs_service_api::port::repo::message_delivery::DeliveryCompareAndSet>, _: Option<bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries>) -> Result<Option<bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> { unreachable!() }
}

#[tokio::test]
async fn disconnected_management_request_still_finishes_commit_and_publication() {
    let repo = Arc::new(SlowPolicyRepo { inner: MemoryMessageRepo::new(), started: Default::default(), release: Default::default() });
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default(), false));
    let request_live = live.clone();
    let request = tokio::spawn(async move { request_live.replace(admin(), 0, DeliveryPolicy::default()).await });
    tokio::time::timeout(std::time::Duration::from_secs(1), repo.started.notified()).await.unwrap();
    request.abort();
    repo.release.notify_one();
    tokio::time::timeout(std::time::Duration::from_secs(1), async {
        loop { if live.snapshot.read().await.version == 1 { break; } tokio::task::yield_now().await; }
    }).await.unwrap();
    assert_eq!(live.get(admin()).await.unwrap().version, 1);
}

#[tokio::test]
async fn policy_management_auth_cas_and_live_publication() {
    let repo = Arc::new(MemoryMessageRepo::new());
    let live = LiveDeliveryPolicy::new(repo.clone(), DeliveryPolicyRecord::default(), false);
    assert!(live.get(CallerContext::Public).await.is_err());
    assert!(live.replace(CallerContext::Public, 0, DeliveryPolicy::default()).await.is_err());
    for caller in [
        CallerContext::Bot(bcs_service_api::BotActor { bot_uuid: "bot".into() }),
        CallerContext::Admin(bcs_service_api::AdminActor { actor_id: "old-service-admin".into(), scopes: vec!["message_delivery:manage".into()] }),
        CallerContext::Integration(bcs_service_api::IntegrationClient { client_id: "provider".into(), scopes: vec![] }),
    ] {
        assert!(matches!(live.get(caller.clone()).await, Err(bcs_service_api::ServiceError::Forbidden(_))));
        assert!(matches!(live.replace(caller, 0, DeliveryPolicy::default()).await, Err(bcs_service_api::ServiceError::Forbidden(_))));
    }
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    assert!(live.replace(admin(), 0, policy.clone()).await.is_err(), "missing scheduler must not falsely enable");
    assert_eq!(repo.load_policy().await.unwrap().version, 0);
    live.scheduler_available.store(true, Ordering::SeqCst);
    let result = live.replace(admin(), 0, policy.clone()).await.unwrap();
    assert_eq!(result.version, 1);
    assert_eq!(result.updated_by, "human_operator");
    assert!(live.snapshot.read().await.policy.manages_group("future-bot"));
    assert_eq!(repo.load_policy().await.unwrap(), result);
    assert!(live.replace(admin(), 0, policy.clone()).await.is_err());
    policy.defaults.max_running = 0;
    assert!(live.replace(admin(), 1, policy).await.is_err());
    assert_eq!(live.get(admin()).await.unwrap(), result);
    let changed = DeliveryPolicyRecord { version: 2, ..result.clone() };
    repo.replace_policy(1, changed).await.unwrap();
    assert!(live.get(admin()).await.is_err(), "out-of-band DB edits cannot masquerade as applied policy");
    assert!(live.replace(admin(), 1, DeliveryPolicy::default()).await.is_err());
    assert_eq!(live.snapshot.read().await.version, 1, "failed DB CAS must not publish");
}

#[tokio::test]
async fn provider_headers_and_unready_flows_are_rejected() {
    let live = LiveDeliveryPolicy::new(Arc::new(MemoryMessageRepo::new()), Default::default(), true);
    live.scheduler_available.store(true, Ordering::SeqCst);
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.state_machine = true;
    assert!(live.replace(admin(), 0, policy).await.is_err());
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    assert!(live.replace(admin(), 0, policy).await.is_err());
}
