use bcs_config_api::message_delivery::{BotDeliveryMode, DeliveryPolicy, DeliveryPolicyRecord};
use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{HumanActor, CallerContext};
use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort;
use std::sync::{Arc, atomic::Ordering};

fn admin() -> CallerContext {
    CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() })
}

#[tokio::test]
async fn legacy_group_policy_is_migrated_durably_and_only_once() {
    let repo = SlowPolicyRepo::default();
    let mut legacy = DeliveryPolicyRecord::default();
    legacy.version = 1;
    legacy.policy.flow_enabled.group = true;
    legacy.policy.defaults.mode = BotDeliveryMode::Enforce;
    repo.seed_legacy(0, legacy.clone()).await;
    repo.release.notify_one();
    let upgraded = LiveDeliveryPolicy::load_compatible(&repo).await.unwrap();
    assert_eq!(upgraded.version, 2);
    assert!(upgraded.policy.manages_system("bot"));
    assert_eq!(upgraded.updated_by, "system:group-system-policy-migration");
    assert_eq!(repo.load_policy().await.unwrap(), upgraded);
    assert_eq!(LiveDeliveryPolicy::load_compatible(&repo).await.unwrap(), upgraded);
}

#[tokio::test]
async fn takeover_and_reconciliation_migrate_legacy_commits() {
    let repo = Arc::new(SlowPolicyRepo::default());
    let live = LiveDeliveryPolicy::new(repo.clone(), Default::default());
    let mut legacy = DeliveryPolicyRecord::default();
    legacy.version = 1;
    legacy.policy.flow_enabled.group = true;
    repo.seed_legacy(0, legacy.clone()).await;
    repo.release.notify_one();
    live.refresh_for_takeover().await.unwrap();
    assert_eq!(live.snapshot.read().await.version, 2);
    assert!(live.snapshot.read().await.policy.flow_enabled.system);
    legacy.version = 3;
    legacy.policy.flow_enabled.group = false;
    legacy.policy.flow_enabled.system = true;
    repo.seed_legacy(2, legacy).await;
    repo.release.notify_one();
    live.reconcile_durable_version().await.unwrap();
    assert_eq!(live.snapshot.read().await.version, 4);
    assert!(!live.snapshot.read().await.policy.flow_enabled.system);
    assert_eq!(*live.snapshot.read().await, repo.load_policy().await.unwrap());
}

#[tokio::test]
async fn management_rejects_mismatched_switches_without_persisting() {
    let repo = Arc::new(MemoryMessageRepo::new());
    let live = LiveDeliveryPolicy::new(repo.clone(), Default::default());
    live.scheduler_available.store(true, Ordering::SeqCst);
    for group in [false, true] {
        let mut policy = DeliveryPolicy::default();
        policy.flow_enabled.group = group;
        policy.flow_enabled.system = !group;
        let error = live.replace(admin(), 0, policy).await.unwrap_err();
        assert!(error.to_string().contains("queue_group_system_switch_mismatch"));
        assert_eq!(repo.load_policy().await.unwrap().version, 0);
    }
}

#[tokio::test]
async fn migration_conflict_reloads_latest_operator_policy_without_overwriting_it() {
    let repo = Arc::new(SlowPolicyRepo::default());
    let mut legacy = DeliveryPolicyRecord::default();
    legacy.version = 1;
    legacy.policy.flow_enabled.group = true;
    repo.seed_legacy(0, legacy).await;
    let migrating_repo = repo.clone();
    let migration = tokio::spawn(async move { LiveDeliveryPolicy::load_compatible(migrating_repo.as_ref()).await });
    repo.started.notified().await;
    let mut operator = DeliveryPolicyRecord::default();
    operator.version = 2;
    operator.policy.pause_dispatch = true;
    operator.updated_by = "human_operator".into();
    repo.inner.replace_policy(1, operator.clone()).await.unwrap();
    repo.release.notify_one();
    assert_eq!(migration.await.unwrap().unwrap(), operator);
    assert_eq!(repo.inner.load_policy().await.unwrap(), operator);
}

#[tokio::test]
async fn takeover_reloads_policy_changed_by_previous_master() {
    let repo = Arc::new(MemoryMessageRepo::new());
    let live = LiveDeliveryPolicy::new(repo.clone(), Default::default());
    let mut stored = DeliveryPolicyRecord::default();
    stored.version = 1;
    stored.policy.pause_dispatch = true;
    repo.replace_policy(0, stored.clone()).await.unwrap();
    assert_eq!(live.snapshot.read().await.version, 0);
    live.refresh_for_takeover().await.unwrap();
    assert_eq!(*live.snapshot.read().await, stored);
}


#[derive(Default)]
struct SlowPolicyRepo {
    inner: MemoryMessageRepo,
    legacy: tokio::sync::RwLock<Option<DeliveryPolicyRecord>>,
    fail_read: std::sync::atomic::AtomicBool,
    started: tokio::sync::Notify,
    release: tokio::sync::Notify,
}

impl SlowPolicyRepo {
    // Model old durable bytes at the read boundary without weakening current
    // repository validation. The real inner version still participates in CAS.
    async fn seed_legacy(&self, expected: u64, legacy: DeliveryPolicyRecord) {
        let mut valid = legacy.clone();
        valid.policy.flow_enabled.system = valid.policy.flow_enabled.group;
        self.inner.replace_policy(expected, valid).await.unwrap();
        *self.legacy.write().await = Some(legacy);
    }
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
        if self.fail_read.load(Ordering::SeqCst) {
            return Err(bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Storage("unavailable".into()));
        }
        let stored = self.inner.load_policy().await?;
        if let Some(legacy) = self.legacy.read().await.as_ref() {
            if legacy.version == stored.version { return Ok(legacy.clone()); }
        }
        Ok(stored)
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
    let repo = Arc::new(SlowPolicyRepo::default());
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
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
async fn failed_takeover_read_does_not_publish_a_default_policy() {
    let repo = Arc::new(SlowPolicyRepo { fail_read: true.into(), ..Default::default() });
    let mut initial = DeliveryPolicyRecord::default();
    initial.version = 7;
    initial.policy.pause_dispatch = true;
    let live = LiveDeliveryPolicy::new(repo.clone(), initial.clone());
    assert!(live.refresh_for_takeover().await.is_err());
    assert_eq!(*live.snapshot.read().await, initial);
    repo.fail_read.store(false, Ordering::SeqCst);
    live.refresh_for_takeover().await.unwrap();
    assert_eq!(live.snapshot.read().await.version, 0);
}

#[tokio::test]
async fn reconciliation_observes_previous_masters_late_commit() {
    let repo = Arc::new(SlowPolicyRepo::default());
    let old = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
    let next = LiveDeliveryPolicy::new(repo.clone(), Default::default());
    let writing = old.clone();
    let update = tokio::spawn(async move {
        let mut policy = DeliveryPolicy::default();
        policy.pause_dispatch = true;
        writing.replace(admin(), 0, policy).await
    });
    repo.started.notified().await;
    next.refresh_for_takeover().await.unwrap();
    assert_eq!(next.snapshot.read().await.version, 0);
    repo.release.notify_one();
    let committed = update.await.unwrap().unwrap();
    assert!(!next.snapshot.read().await.policy.pause_dispatch);
    repo.fail_read.store(true, Ordering::SeqCst);
    assert!(next.reconcile_durable_version().await.is_err());
    assert_eq!(next.snapshot.read().await.version, 0);
    repo.fail_read.store(false, Ordering::SeqCst);
    next.reconcile_durable_version().await.unwrap();
    assert_eq!(*next.snapshot.read().await, committed);
    next.reconcile_durable_version().await.unwrap();
    assert_eq!(*next.snapshot.read().await, committed);
}

#[tokio::test]
async fn policy_management_auth_cas_and_live_publication() {
    let repo = Arc::new(MemoryMessageRepo::new());
    let live = LiveDeliveryPolicy::new(repo.clone(), DeliveryPolicyRecord::default());
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
    policy.flow_enabled.system = true;
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
async fn unready_flows_are_rejected_but_group_activation_is_allowed() {
    let live = LiveDeliveryPolicy::new(Arc::new(MemoryMessageRepo::new()), Default::default());
    live.scheduler_available.store(true, Ordering::SeqCst);
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.state_machine = true;
    assert!(live.replace(admin(), 0, policy).await.is_err());
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true;
    policy.flow_enabled.system = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    assert!(live.replace(admin(), 0, policy).await.is_ok());
}
