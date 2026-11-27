#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "contract assertions"
)]
use bcs_dist_model::*;
use bcs_dist_store::{coordination::CoordinationStore, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{common::*, ownership::*};
use bcs_store_api::{lease::*, zset::*};
use bcs_test_support::store_model::Model;
use std::{num::NonZeroU32, ops::Bound, sync::Arc, time::Duration};

fn instance(name: &str) -> Instance {
    Instance {
        node: name.into(),
        incarnation: format!("{name}-1"),
    }
}
fn layout() -> KeyLayout {
    KeyLayout::new("limits", "worker").unwrap()
}
fn setup() -> (Arc<Model>, CoordinationStore) {
    let model = Arc::new(Model::default());
    let store =
        CoordinationStore::new(model.clone(), model.clone(), model.clone(), layout()).unwrap();
    (model, store)
}
async fn register(store: &CoordinationStore, name: &str) -> MembershipLease {
    store
        .register(
            instance(name),
            RuntimeRole::Worker,
            format!("http://{name}.example.test"),
            Duration::from_secs(3),
        )
        .await
        .unwrap()
}
async fn indexed(model: &Model) -> Vec<ZSetEntry> {
    model
        .range(
            &layout().members("worker").unwrap(),
            &ZSetRange {
                min: Bound::Unbounded,
                max: Bound::Unbounded,
                after: None,
                limit: NonZeroU32::new(200).unwrap(),
            },
        )
        .await
        .unwrap()
}

#[tokio::test]
async fn retiring_removes_discovery_but_preserves_terminal_evidence() {
    let (model, store) = setup();
    let lease = register(&store, "retired").await;
    assert_eq!(indexed(&model).await.len(), 1);
    store.retire(&lease).await.unwrap();
    assert!(indexed(&model).await.is_empty());
    assert_eq!(
        store.liveness(&lease.instance).await.unwrap(),
        Liveness::Dead
    );
    assert_eq!(
        model
            .register_lease(
                &layout().member("instance", &lease.instance).unwrap(),
                b"late",
                Duration::from_secs(3)
            )
            .await
            .unwrap(),
        LeaseRegistration::Terminated
    );
}

#[tokio::test]
async fn discovery_prunes_expired_members_but_preserves_not_yet_registered_entries() {
    let (model, store) = setup();
    register(&store, "expired").await;
    let pending = instance("pending");
    let member = serde_json::to_string(&pending).unwrap();
    model
        .add(
            &layout().members("worker").unwrap(),
            &[ZSetEntry {
                member: member.clone(),
                score: 0.0,
            }],
            ZAddMode::InsertOnly,
        )
        .await
        .unwrap();
    model.advance(Duration::from_secs(3));
    let page = store
        .members(
            RuntimeRole::Worker,
            PageRequest {
                after: None,
                limit: NonZeroU32::new(200).unwrap(),
            },
        )
        .await
        .unwrap();
    assert!(page.items.is_empty());
    assert_eq!(
        indexed(&model).await,
        vec![ZSetEntry { member, score: 0.0 }]
    );
    register(&store, "pending").await;
    let page = store
        .members(
            RuntimeRole::Worker,
            PageRequest {
                after: None,
                limit: NonZeroU32::new(200).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].instance, pending);
}

mod common;
use bcs_service_api::port::repo::distributed::session::*;
use common::Recorder;
fn initial() -> Checkpoint {
    Checkpoint {
        format_version: 1,
        commit_seq: CommitSeq(0),
        consumed: None,
        tasks: Default::default(),
        runner_state: None,
        dispatch_seq: Default::default(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: Default::default(),
        last_event_id: None,
        feed_publications: Vec::new(),
        outbox: None,
    }
}
async fn claim_shard(store: &CoordinationStore, shard: u32) -> ShardClaim {
    match store
        .claim(ClaimShard {
            shard: ShardId(shard),
            me: instance("a"),
            expected_epoch: None,
            request_id: RequestId(format!("claim-{shard}")),
        })
        .await
        .unwrap()
    {
        LeaseClaim::Claimed(claim) => claim,
        other => panic!("{other:?}"),
    }
}
#[tokio::test]
async fn session_handoff_reuses_one_prewrite_owner_read_but_rechecks_after_write() {
    let model = Arc::new(Model::default());
    let docs = Arc::new(Recorder::default());
    let store = CoordinationStore::new(docs.clone(), model.clone(), model, layout()).unwrap();
    register(&store, "a").await;
    register(&store, "b").await;
    let source = claim_shard(&store, 42).await;
    let sid = Sid("s0042.session".into());
    let AcquireOutcome::Acquired(session) = store.acquire(&sid, &source, initial()).await.unwrap()
    else {
        panic!()
    };
    let request = HandoffRequest {
        request_id: RequestId("transfer".into()),
        from: source,
        target: instance("b"),
        grace: Duration::from_secs(3),
    };
    let HandoffOutcome::Accepted(target) = store.handoff(request.clone()).await.unwrap() else {
        panic!()
    };
    docs.clear();
    assert_eq!(
        store
            .handoff_session(&session.ownership, &target.claim, &request.request_id)
            .await
            .unwrap(),
        SessionTransferOutcome::Transferred
    );
    let owner_read = format!("document.read {}", layout().owner(ShardId(42)));
    assert_eq!(
        docs.calls()
            .iter()
            .filter(|call| **call == owner_read)
            .count(),
        2
    );
    let checkpoint_read = format!("document.read {}", layout().ckpt(&sid).unwrap());
    assert_eq!(
        docs.calls()
            .iter()
            .filter(|call| **call == checkpoint_read)
            .count(),
        2
    );
}

#[tokio::test]
async fn configured_cache_capacity_rejects_new_sessions_then_reuses_released_slot() {
    use bcs_dist_store::coordination::CoordinationStoreConfig;
    let model = Arc::new(Model::default());
    let store = CoordinationStore::with_config(
        model.clone(),
        model.clone(),
        model,
        layout(),
        CoordinationStoreConfig {
            max_cached_sessions: 1,
            max_handoff_timers: 2,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(store.local_session_capacity(), Some(1));
    register(&store, "a").await;
    let claim = claim_shard(&store, 42).await;
    let first = Sid("s0042.first".into());
    let second = Sid("s0042.second".into());
    let AcquireOutcome::Acquired(acquired) =
        store.acquire(&first, &claim, initial()).await.unwrap()
    else {
        panic!()
    };
    assert_eq!(
        store.acquire(&second, &claim, initial()).await.unwrap_err(),
        StoreError::NotApplied {
            code: "session_cache_capacity".into()
        }
    );
    assert!(store.load(&second).await.unwrap().is_none());
    store.release_local(&acquired.ownership).await.unwrap();
    assert!(matches!(
        store.acquire(&second, &claim, initial()).await.unwrap(),
        AcquireOutcome::Acquired(_)
    ));
}

#[tokio::test]
async fn handoff_capacity_is_separate_and_reclaimed_after_completion() {
    use bcs_dist_store::coordination::CoordinationStoreConfig;
    let model = Arc::new(Model::default());
    let store = CoordinationStore::with_config(
        model.clone(),
        model.clone(),
        model,
        layout(),
        CoordinationStoreConfig {
            max_cached_sessions: 2,
            max_handoff_timers: 1,
            ..Default::default()
        },
    )
    .unwrap();
    register(&store, "a").await;
    register(&store, "b").await;
    let first = claim_shard(&store, 42).await;
    let second = claim_shard(&store, 43).await;
    let request = |from: ShardClaim| HandoffRequest {
        request_id: RequestId(format!("handoff-{}", from.shard.0)),
        from,
        target: instance("b"),
        grace: Duration::from_secs(3),
    };
    let first = request(first);
    let second = request(second);
    let HandoffOutcome::Accepted(target) = store.handoff(first.clone()).await.unwrap() else {
        panic!()
    };
    assert_eq!(
        store.handoff(second.clone()).await.unwrap_err(),
        StoreError::NotApplied {
            code: "handoff_capacity".into()
        }
    );
    assert!(
        store
            .source_done(&target.claim, &first.request_id)
            .await
            .unwrap()
    );
    // source_done ends protection even before a later cleanup clears the field.
    assert!(matches!(
        store.handoff(second).await.unwrap(),
        HandoffOutcome::Accepted(_)
    ));
    assert!(
        store
            .clear_handoff(&target.claim, &first.request_id)
            .await
            .unwrap()
    );
}

#[test]
fn zero_coordination_limits_are_rejected() {
    use bcs_dist_store::coordination::CoordinationStoreConfig;
    for config in [
        CoordinationStoreConfig {
            max_cached_sessions: 0,
            max_handoff_timers: 1,
            ..Default::default()
        },
        CoordinationStoreConfig {
            max_cached_sessions: 1,
            max_handoff_timers: 0,
            ..Default::default()
        },
    ] {
        let model = Arc::new(Model::default());
        assert!(
            CoordinationStore::with_config(model.clone(), model.clone(), model, layout(), config)
                .is_err()
        );
    }
}

#[tokio::test]
async fn definite_retry_error_keeps_a_previous_unknown_acquisition_reserved() {
    use bcs_dist_store::coordination::CoordinationStoreConfig;
    let model = Arc::new(Model::default());
    let store = CoordinationStore::with_config(
        model.clone(),
        model.clone(),
        model.clone(),
        layout(),
        CoordinationStoreConfig {
            max_cached_sessions: 1,
            max_handoff_timers: 2,
            ..Default::default()
        },
    )
    .unwrap();
    register(&store, "a").await;
    let claim = claim_shard(&store, 42).await;
    let first = Sid("s0042.unknown".into());
    let second = Sid("s0042.second".into());
    model.lose_next_response();
    assert!(matches!(
        store.acquire(&first, &claim, initial()).await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    let mut invalid = initial();
    invalid.format_version = 0;
    assert!(matches!(
        store.acquire(&first, &claim, invalid).await,
        Err(StoreError::Invalid { .. })
    ));
    assert_eq!(
        store.acquire(&second, &claim, initial()).await.unwrap_err(),
        StoreError::NotApplied {
            code: "session_cache_capacity".into()
        }
    );
    let AcquireOutcome::Acquired(acquired) =
        store.acquire(&first, &claim, initial()).await.unwrap()
    else {
        panic!()
    };
    store.release_local(&acquired.ownership).await.unwrap();
    assert!(matches!(
        store.acquire(&second, &claim, initial()).await.unwrap(),
        AcquireOutcome::Acquired(_)
    ));
}

struct DelayedCreate {
    model: Arc<Model>,
    blocked: std::sync::atomic::AtomicBool,
}
impl bcs_store_api::StorePlugin for DelayedCreate {
    fn pool_observation(&self) -> Option<bcs_store_api::StorePoolObservation> {
        self.model.pool_observation()
    }
    fn supports(&self, capability: bcs_store_api::StoreCapability) -> bool {
        self.model.supports(capability)
    }
}
#[async_trait::async_trait]
impl bcs_store_api::document::DocumentStorePlugin for DelayedCreate {
    async fn read_document(
        &self,
        key: &str,
    ) -> bcs_store_api::StoreResult<Option<bcs_store_api::Versioned<serde_json::Value>>> {
        self.model.read_document(key).await
    }
    async fn create_document(
        &self,
        key: &str,
        value: &serde_json::Value,
    ) -> bcs_store_api::StoreResult<bcs_store_api::WriteOutcome> {
        if self.blocked.load(std::sync::atomic::Ordering::SeqCst) {
            std::future::pending::<()>().await;
        }
        self.model.create_document(key, value).await
    }
    async fn replace_document(
        &self,
        key: &str,
        expected: bcs_store_api::Version,
        value: &serde_json::Value,
    ) -> bcs_store_api::StoreResult<bcs_store_api::WriteOutcome> {
        self.model.replace_document(key, expected, value).await
    }
}
#[tokio::test]
async fn cancelled_checkpoint_write_keeps_the_cache_reservation_through_definite_retry_error() {
    use bcs_dist_store::coordination::CoordinationStoreConfig;
    let model = Arc::new(Model::default());
    let docs = Arc::new(DelayedCreate {
        model: model.clone(),
        blocked: false.into(),
    });
    let store = CoordinationStore::with_config(
        docs.clone(),
        model.clone(),
        model,
        layout(),
        CoordinationStoreConfig {
            max_cached_sessions: 1,
            max_handoff_timers: 2,
            ..Default::default()
        },
    )
    .unwrap();
    register(&store, "a").await;
    let claim = claim_shard(&store, 42).await;
    docs.blocked
        .store(true, std::sync::atomic::Ordering::SeqCst);
    let first = Sid("s0042.cancelled".into());
    assert!(
        tokio::time::timeout(
            Duration::from_millis(10),
            store.acquire(&first, &claim, initial())
        )
        .await
        .is_err()
    );
    let mut invalid = initial();
    invalid.format_version = 0;
    assert!(matches!(
        store.acquire(&first, &claim, invalid).await,
        Err(StoreError::Invalid { .. })
    ));
    docs.blocked
        .store(false, std::sync::atomic::Ordering::SeqCst);
    assert_eq!(
        store
            .acquire(&Sid("s0042.second".into()), &claim, initial())
            .await
            .unwrap_err(),
        StoreError::NotApplied {
            code: "session_cache_capacity".into()
        }
    );
    assert!(matches!(
        store.acquire(&first, &claim, initial()).await.unwrap(),
        AcquireOutcome::Acquired(_)
    ));
}

#[tokio::test]
async fn expired_discovery_history_larger_than_one_worker_scan_is_pruned_with_stable_pages() {
    let (model, store) = setup();
    // More than the runtime defaults: 200 entries × 32 pages per cycle.
    for index in 0..6401 {
        register(&store, &format!("a-{index:05}")).await;
    }
    model.advance(Duration::from_secs(3));
    register(&store, "z-live").await;
    let mut after = None;
    for _ in 0..32 {
        let page = store
            .members(
                RuntimeRole::Worker,
                PageRequest {
                    after,
                    limit: NonZeroU32::new(200).unwrap(),
                },
            )
            .await
            .unwrap();
        assert!(page.items.is_empty());
        assert!(page.next.is_some());
        after = page.next;
    }
    assert_eq!(indexed(&model).await.len(), 2);
    let page = store
        .members(
            RuntimeRole::Worker,
            PageRequest {
                after,
                limit: NonZeroU32::new(200).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].instance, instance("z-live"));
    assert_eq!(indexed(&model).await.len(), 1);
}

struct UncertainLease {
    model: Arc<Model>,
    mode: std::sync::atomic::AtomicU8,
}
impl bcs_store_api::StorePlugin for UncertainLease {
    fn pool_observation(&self) -> Option<bcs_store_api::StorePoolObservation> {
        self.model.pool_observation()
    }
    fn supports(&self, capability: bcs_store_api::StoreCapability) -> bool {
        self.model.supports(capability)
    }
}
#[async_trait::async_trait]
impl LeaseStorePlugin for UncertainLease {
    async fn register_lease(
        &self,
        key: &str,
        value: &[u8],
        ttl: Duration,
    ) -> bcs_store_api::StoreResult<LeaseRegistration> {
        self.model.register_lease(key, value, ttl).await
    }
    async fn read_lease(&self, key: &str) -> bcs_store_api::StoreResult<LeaseState> {
        match self.mode.load(std::sync::atomic::Ordering::SeqCst) {
            1 => Err(bcs_store_api::StoreError::NotApplied(
                "injected_read_failure".into(),
            )),
            2 => Ok(LeaseState::Absent),
            _ => self.model.read_lease(key).await,
        }
    }
    async fn update_lease(
        &self,
        key: &str,
        expected: bcs_store_api::Version,
        value: &[u8],
        ttl: Option<Duration>,
    ) -> bcs_store_api::StoreResult<LeaseWrite> {
        self.model.update_lease(key, expected, value, ttl).await
    }
    async fn retire_lease(&self, key: &str) -> bcs_store_api::StoreResult<()> {
        self.model.retire_lease(key).await
    }
}
fn uncertain_setup() -> (Arc<Model>, Arc<UncertainLease>, CoordinationStore) {
    use bcs_dist_store::coordination::CoordinationStoreConfig;
    let model = Arc::new(Model::default());
    let leases = Arc::new(UncertainLease {
        model: model.clone(),
        mode: 0.into(),
    });
    let store = CoordinationStore::with_config(
        model.clone(),
        leases.clone(),
        model.clone(),
        layout(),
        CoordinationStoreConfig {
            max_cached_sessions: 1,
            max_handoff_timers: 2,
            ..Default::default()
        },
    )
    .unwrap();
    (model, leases, store)
}
#[tokio::test]
async fn unknown_lease_after_unknown_acquisition_does_not_fence_or_release_responsibility() {
    let (model, leases, store) = uncertain_setup();
    register(&store, "a").await;
    let claim = claim_shard(&store, 42).await;
    let sid = Sid("s0042.unknown-lease".into());
    model.lose_next_response();
    assert!(matches!(
        store.acquire(&sid, &claim, initial()).await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    for mode in [1, 2] {
        leases.mode.store(mode, std::sync::atomic::Ordering::SeqCst);
        assert!(matches!(
            store.acquire(&sid, &claim, initial()).await,
            Err(StoreError::OutcomeUnknown { .. })
        ));
        assert_eq!(
            store
                .acquire(&Sid("s0042.second".into()), &claim, initial())
                .await
                .unwrap_err(),
            StoreError::NotApplied {
                code: "session_cache_capacity".into()
            }
        );
    }
    leases.mode.store(0, std::sync::atomic::Ordering::SeqCst);
    assert!(matches!(
        store.acquire(&sid, &claim, initial()).await.unwrap(),
        AcquireOutcome::Acquired(_)
    ));
}
#[tokio::test]
async fn uncertain_handoff_target_lease_never_reports_session_fenced_or_source_done() {
    let (_, leases, store) = uncertain_setup();
    register(&store, "a").await;
    register(&store, "b").await;
    let claim = claim_shard(&store, 42).await;
    let sid = Sid("s0042.transfer".into());
    let AcquireOutcome::Acquired(session) = store.acquire(&sid, &claim, initial()).await.unwrap()
    else {
        panic!()
    };
    let request = HandoffRequest {
        request_id: RequestId("lease-transfer".into()),
        from: claim,
        target: instance("b"),
        grace: Duration::from_secs(3),
    };
    let HandoffOutcome::Accepted(target) = store.handoff(request.clone()).await.unwrap() else {
        panic!()
    };
    for mode in [1, 2] {
        leases.mode.store(mode, std::sync::atomic::Ordering::SeqCst);
        assert!(matches!(
            store
                .handoff_session(&session.ownership, &target.claim, &request.request_id)
                .await,
            Err(StoreError::OutcomeUnknown { .. })
        ));
        assert!(matches!(
            store.source_done(&target.claim, &request.request_id).await,
            Err(StoreError::OutcomeUnknown { .. })
        ));
    }
    leases.mode.store(0, std::sync::atomic::Ordering::SeqCst);
    assert_eq!(
        store.load(&sid).await.unwrap().unwrap().owner,
        session.ownership.owner
    );
    assert_eq!(
        store
            .handoff_session(&session.ownership, &target.claim, &request.request_id)
            .await
            .unwrap(),
        SessionTransferOutcome::Transferred
    );
}
#[tokio::test]
async fn uncertain_liveness_does_not_turn_claim_or_handoff_retries_into_definite_rejection() {
    let (_, leases, store) = uncertain_setup();
    register(&store, "a").await;
    register(&store, "b").await;
    let source = claim_shard(&store, 42).await;
    let request = HandoffRequest {
        request_id: RequestId("lease-handoff".into()),
        from: source,
        target: instance("b"),
        grace: Duration::from_secs(3),
    };
    let claim = ClaimShard {
        shard: ShardId(43),
        me: instance("a"),
        expected_epoch: None,
        request_id: RequestId("lease-claim".into()),
    };
    for mode in [1, 2] {
        leases.mode.store(mode, std::sync::atomic::Ordering::SeqCst);
        assert!(matches!(
            store.claim(claim.clone()).await,
            Err(StoreError::OutcomeUnknown { .. })
        ));
        assert!(matches!(
            store.handoff(request.clone()).await,
            Err(StoreError::OutcomeUnknown { .. })
        ));
    }
}

#[tokio::test]
async fn recovery_attempt_bytes_preserve_room_for_control_and_retries() {
    use bcs_store_api::document::DocumentStorePlugin;
    let (model, store) = setup();
    register(&store, "a").await;
    let claim = claim_shard(&store, 42).await;
    let sid = Sid("s0042.capacity".into());
    let AcquireOutcome::Acquired(acquired) = store.acquire(&sid, &claim, initial()).await.unwrap() else { panic!() };
    let request = |n: usize| RequestId(format!("{n:03}{}", "x".repeat(251)));
    for n in 0..31 {
        assert!(matches!(store.record_recovery_attempt(&acquired.ownership,
            CommitSeq(0), request(n)).await.unwrap(), RecoveryUpdate::Recorded { .. }));
    }
    let key = layout().ckpt(&sid).unwrap();
    let before = model.read_document(&key).await.unwrap().unwrap();
    assert_eq!(store.record_recovery_attempt(&acquired.ownership, CommitSeq(0), request(31)).await,
        Err(StoreError::NotApplied { code: "recovery_attempt_byte_budget".into() }));
    assert_eq!(store.record_recovery_attempt(&acquired.ownership, CommitSeq(0), RequestId("x".repeat(255))).await,
        Err(StoreError::Invalid { code: "recovery_attempt_identity".into() }));
    assert_eq!(model.read_document(&key).await.unwrap().unwrap(), before);
    assert!(matches!(store.record_recovery_attempt(&acquired.ownership,
        CommitSeq(0), request(0)).await.unwrap(), RecoveryUpdate::Recorded { .. }));
    assert_eq!(model.read_document(&key).await.unwrap().unwrap(), before);
}
