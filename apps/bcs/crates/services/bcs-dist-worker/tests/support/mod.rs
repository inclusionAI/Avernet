#![allow(dead_code)]
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::{coordination::CoordinationStore, layout::KeyLayout};
use bcs_dist_worker::{WorkerConfig, WorkerRuntime};
use bcs_service_api::{
    port::{
        dist_runtime::{DistRuntimeControlPort, DistWorkerPeerPort},
        repo::distributed::{common::*, inbox::*, ownership::*, session::*},
    },
    types::dist_runtime::*,
};
use bcs_store_api::document::DocumentStorePlugin;
use bcs_test_support::store_model::Model;
use std::{
    collections::{BTreeMap, VecDeque},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::Duration,
};
#[derive(Default)]
pub struct FeedData {
    pub watched: Mutex<Vec<Sid>>,
    pub pending: Mutex<Vec<Sid>>,
    pub batches: Mutex<VecDeque<FeedBatch>>,
    pub reads: Mutex<Vec<(ReadLimits, Duration)>>,
    pub unwatch_unavailable: AtomicBool,
    pub open_feeds: AtomicUsize,
}
struct Feed(Arc<FeedData>);
impl Drop for Feed {
    fn drop(&mut self) {
        self.0.open_feeds.fetch_sub(1, Ordering::SeqCst);
    }
}
#[async_trait]
impl ShardFeedPort for Feed {
    async fn watch(&mut self, sid: Sid, _after: Option<InboxPos>) -> Result<(), StoreError> {
        self.0.watched.lock().unwrap().push(sid);
        Ok(())
    }
    async fn unwatch(&mut self, sid: &Sid) -> Result<(), StoreError> {
        if self.0.unwatch_unavailable.load(Ordering::SeqCst) {
            return Err(StoreError::NotApplied {
                code: "unwatch_unavailable".into(),
            });
        }
        self.0.watched.lock().unwrap().retain(|s| s != sid);
        Ok(())
    }
    async fn next(
        &mut self,
        limits: ReadLimits,
        timeout: Duration,
    ) -> Result<FeedBatch, StoreError> {
        self.0.reads.lock().unwrap().push((limits, timeout));
        Ok(self
            .0
            .batches
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or(FeedBatch {
                failures: vec![],
                sessions: vec![],
                hints: vec![],
            }))
    }
}
pub struct Inbox(pub Arc<FeedData>);
#[async_trait]
impl InboxConsumerPort for Inbox {
    async fn open(&self, _shard: ShardId) -> Result<Box<dyn ShardFeedPort>, StoreError> {
        self.0.open_feeds.fetch_add(1, Ordering::SeqCst);
        Ok(Box::new(Feed(self.0.clone())))
    }
    async fn has_unconsumed(
        &self,
        _sid: &Sid,
        _after: Option<&InboxPos>,
    ) -> Result<bool, StoreError> {
        Ok(false)
    }
    async fn confirm_committed(
        &self,
        _delivery: &DeliveryReceipt,
        _proof: &CommitReceipt,
    ) -> Result<(), StoreError> {
        Ok(())
    }
    async fn trim_committed(&self, _proof: &CommitReceipt) -> Result<(), StoreError> {
        Ok(())
    }
    async fn enumerate_pending(
        &self,
        shard: ShardId,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError> {
        let pending = self.0.pending.lock().unwrap();
        let after = page.after.map(|s| s.0);
        let prefix = format!("s{:04}.", shard.0);
        let items = pending
            .iter()
            .filter(|sid| sid.0.starts_with(&prefix))
            .filter(|s| after.as_ref().is_none_or(|a| s.0 > *a))
            .take(page.limit.get() as usize)
            .cloned()
            .collect::<Vec<_>>();
        let next = if items.len() == page.limit.get() as usize {
            items.last().map(|s| PageToken(s.0.clone()))
        } else {
            None
        };
        Ok(Page { items, next })
    }
}
#[derive(Default)]
pub struct Scheduling {
    pub observed: Mutex<Vec<SessionOwnership>>,
    pub unknown: AtomicBool,
    pub fenced: AtomicBool,
}
#[async_trait]
impl InboxSchedulingPort for Scheduling {
    async fn observe(&self, owner: &SessionOwnership) -> Result<ScheduleObservation, StoreError> {
        if self.unknown.load(Ordering::SeqCst) {
            return Err(StoreError::OutcomeUnknown {
                operation_id: "observe".into(),
            });
        }
        if self.fenced.load(Ordering::SeqCst) {
            return Ok(ScheduleObservation::Fenced);
        }
        self.observed.lock().unwrap().push(owner.clone());
        Ok(ScheduleObservation::Observed(ScheduleToken {
            sid: owner.sid.clone(),
            owner_epoch: owner.owner.epoch,
            opaque: "observed".into(),
        }))
    }
    async fn settle(&self, _request: SettleSession) -> Result<SettleOutcome, StoreError> {
        Ok(SettleOutcome::Applied)
    }
    async fn due(
        &self,
        _shard: ShardId,
        _now: TimestampMillis,
        _page: PageRequest,
    ) -> Result<Page<Sid>, StoreError> {
        Ok(Page {
            items: vec![],
            next: None,
        })
    }
}
pub struct Sessions {
    inner: Arc<CoordinationStore>,
    pub released: Mutex<Vec<SessionOwnership>>,
    pub release_unavailable: AtomicBool,
    pub transfer_delay: Mutex<Duration>,
    pub spec_unavailable: AtomicBool,
    pub failed_acquires: Mutex<BTreeMap<Sid, bool>>,
    pub acquire_calls: Mutex<Vec<Sid>>,
    pub failed_transfers: Mutex<Vec<Sid>>,
    pub hold_transfer: AtomicBool,
    pub transfer_entered: tokio::sync::Notify,
    pub transfer_release: tokio::sync::Notify,
    pub transfer_calls: Mutex<Vec<(Sid, tokio::time::Instant)>>,
    pub release_calls: Mutex<Vec<(Sid, tokio::time::Instant)>>,
    pub failed_releases: Mutex<Vec<Sid>>,
}
#[async_trait]
impl SessionStorePort for Sessions {
    fn local_session_capacity(&self) -> Option<usize> {
        self.inner.local_session_capacity()
    }
    async fn release_local(&self, owner: &SessionOwnership) -> Result<(), StoreError> {
        self.release_calls
            .lock()
            .unwrap()
            .push((owner.sid.clone(), tokio::time::Instant::now()));
        if self.release_unavailable.load(Ordering::SeqCst)
            || self.failed_releases.lock().unwrap().contains(&owner.sid)
        {
            return Err(StoreError::NotApplied {
                code: "release_unavailable".into(),
            });
        }
        self.inner.release_local(owner).await?;
        self.released.lock().unwrap().push(owner.clone());
        Ok(())
    }
    async fn get_spec(&self, sid: &Sid) -> Result<Option<SessionSpec>, StoreError> {
        if self.spec_unavailable.load(Ordering::SeqCst) {
            return Err(StoreError::NotApplied {
                code: "spec_unavailable".into(),
            });
        }
        self.inner.get_spec(sid).await
    }
    async fn acquire(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
        initial: Checkpoint,
    ) -> Result<AcquireOutcome, StoreError> {
        self.acquire_calls.lock().unwrap().push(sid.clone());
        if let Some(unknown) = self.failed_acquires.lock().unwrap().get(sid).copied() {
            return Err(if unknown {
                StoreError::OutcomeUnknown {
                    operation_id: "acquire".into(),
                }
            } else {
                StoreError::NotApplied {
                    code: "test_failure".into(),
                }
            });
        }
        self.inner.acquire(sid, claim, initial).await
    }
    async fn handoff_session(
        &self,
        source: &SessionOwnership,
        target: &ShardClaim,
        id: &RequestId,
    ) -> Result<SessionTransferOutcome, StoreError> {
        self.transfer_calls
            .lock()
            .unwrap()
            .push((source.sid.clone(), tokio::time::Instant::now()));
        if self.hold_transfer.swap(false, Ordering::SeqCst) {
            self.transfer_entered.notify_one();
            self.transfer_release.notified().await;
            return Err(StoreError::OutcomeUnknown {
                operation_id: "delayed_transfer".into(),
            });
        }
        if self.failed_transfers.lock().unwrap().contains(&source.sid) {
            return Err(StoreError::OutcomeUnknown {
                operation_id: "transfer".into(),
            });
        }
        let delay = *self.transfer_delay.lock().unwrap();
        tokio::time::sleep(delay).await;
        self.inner.handoff_session(source, target, id).await
    }
    async fn load(&self, sid: &Sid) -> Result<Option<CheckpointEnvelope>, StoreError> {
        self.inner.load(sid).await
    }
    async fn commit(&self, request: CommitRequest) -> Result<CommitOutcome, StoreError> {
        self.inner.commit(request).await
    }
    async fn resolve_commit(
        &self,
        owner: &SessionOwnership,
        id: &CommitId,
        digest: &Digest,
    ) -> Result<CommitResolution, StoreError> {
        self.inner.resolve_commit(owner, id, digest).await
    }
    async fn record_recovery_attempt(
        &self,
        owner: &SessionOwnership,
        expected: CommitSeq,
        attempt: RequestId,
    ) -> Result<RecoveryUpdate, StoreError> {
        self.inner
            .record_recovery_attempt(owner, expected, attempt)
            .await
    }
}
#[derive(Default)]
pub struct Peers {
    pub workers: Mutex<BTreeMap<String, Arc<WorkerRuntime>>>,
    pub lose_reply: AtomicBool,
    pub rejected_nodes: Mutex<Vec<String>>,
    pub unknown_nodes: Mutex<Vec<String>>,
    pub requests: Mutex<Vec<HandoffRequest>>,
    pub request_times: Mutex<Vec<(HandoffRequest, tokio::time::Instant)>>,
    pub unknown_shards: Mutex<Vec<ShardId>>,
}
#[async_trait]
impl DistWorkerPeerPort for Peers {
    async fn handoff(
        &self,
        target: &MemberRecord,
        request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        self.requests.lock().unwrap().push(request.clone());
        self.request_times
            .lock()
            .unwrap()
            .push((request.clone(), tokio::time::Instant::now()));
        if self
            .unknown_nodes
            .lock()
            .unwrap()
            .contains(&target.instance.node)
            || self
                .unknown_shards
                .lock()
                .unwrap()
                .contains(&request.from.shard)
        {
            return Err(RuntimeError {
                code: "reply_unknown".into(),
            });
        }
        if self
            .rejected_nodes
            .lock()
            .unwrap()
            .contains(&target.instance.node)
        {
            return Ok(HandoffOutcome::Rejected);
        }
        let worker = self
            .workers
            .lock()
            .unwrap()
            .get(&target.instance.node)
            .cloned()
            .ok_or_else(|| RuntimeError {
                code: "peer_unavailable".into(),
            })?;
        let result = worker.accept_handoff(request).await?;
        if self.lose_reply.swap(false, Ordering::SeqCst) {
            return Err(RuntimeError {
                code: "lost_reply".into(),
            });
        }
        Ok(result)
    }
    async fn source_done(
        &self,
        target: &MemberRecord,
        source: Instance,
        claim: ShardClaim,
        request_id: RequestId,
    ) -> Result<bool, RuntimeError> {
        let worker = self
            .workers
            .lock()
            .unwrap()
            .get(&target.instance.node)
            .cloned()
            .ok_or_else(|| RuntimeError {
                code: "peer_unavailable".into(),
            })?;
        worker.handoff_done(source, claim, request_id).await
    }
}
pub struct World {
    pub model: Arc<Model>,
    pub store: Arc<CoordinationStore>,
    pub sessions: Arc<Sessions>,
    pub data: Arc<FeedData>,
    pub scheduling: Arc<Scheduling>,
    pub peers: Arc<Peers>,
}
impl World {
    pub fn new() -> Self {
        let model = Arc::new(Model::default());
        let store = Arc::new(
            CoordinationStore::new(
                model.clone(),
                model.clone(),
                model.clone(),
                KeyLayout::new("test", "runtime").unwrap(),
            )
            .unwrap(),
        );
        Self {
            model,
            sessions: Arc::new(Sessions {
                inner: store.clone(),
                released: Mutex::default(),
                release_unavailable: AtomicBool::new(false),
                transfer_delay: Mutex::default(),
                spec_unavailable: AtomicBool::default(),
                failed_acquires: Mutex::default(),
                acquire_calls: Mutex::default(),
                failed_transfers: Mutex::default(),
                hold_transfer: AtomicBool::default(),
                transfer_entered: tokio::sync::Notify::new(),
                transfer_release: tokio::sync::Notify::new(),
                transfer_calls: Mutex::default(),
                release_calls: Mutex::default(),
                failed_releases: Mutex::default(),
            }),
            store,
            data: Arc::default(),
            scheduling: Arc::default(),
            peers: Arc::default(),
        }
    }
    pub async fn worker(&self, name: &str) -> Arc<WorkerRuntime> {
        self.worker_with_capacity(name, 10_000).await
    }
    pub async fn worker_with_capacity(&self, name: &str, capacity: usize) -> Arc<WorkerRuntime> {
        let config = WorkerConfig {
            shards: vec![ShardId(42)],
            max_active_sessions: capacity,
            ..WorkerConfig::default()
        };
        self.worker_with_config(name, config).await
    }
    pub async fn worker_with_config(&self, name: &str, config: WorkerConfig) -> Arc<WorkerRuntime> {
        let runtime = WorkerRuntime::start(
            name.into(),
            format!("http://{name}.example.test:8080"),
            config,
            self.store.clone(),
            self.store.clone(),
            self.sessions.clone(),
            Arc::new(Inbox(self.data.clone())),
            self.scheduling.clone(),
            self.peers.clone(),
            Arc::new(|_| Ok(initial())),
        )
        .await
        .unwrap();
        self.peers
            .workers
            .lock()
            .unwrap()
            .insert(name.into(), runtime.clone());
        runtime
    }
    pub fn instance(&self, name: &str) -> Instance {
        self.peers.workers.lock().unwrap()[name].instance().clone()
    }
    pub async fn pending(&self, sid: &Sid) {
        let spec = SessionSpec {
            schema_version: 2,
            sid: sid.clone(),
            shard: KeyLayout::shard_of(sid).unwrap(),
            scope: TaskScope {
                tenant: TenantId("t".into()),
                principal: PrincipalId("p".into()),
            },
            target: SessionTarget::Group { group: GroupId("g".into()) },
            mode_envelope: ModeConfigEnvelope {
                mode: CollaborationMode::StateMachine,
                mode_version: 1,
                revision: "1".into(),
                digest: Digest("d".into()),
                payload: Payload::Inline(serde_json::json!({})),
            },
            authorized_agents: vec![],
            limits: SessionLimits {
                max_queued_tasks: 4,
                max_task_steps: 32,
                task_timeout_millis: 10_000,
                max_inline_bytes: 1024,
            },
            digest: Digest("spec".into()),
            created_at: TimestampMillis(0),
        };
        let key = KeyLayout::new("test", "runtime")
            .unwrap()
            .spec(sid)
            .unwrap();
        self.model
            .create_document(&key, &serde_json::to_value(spec).unwrap())
            .await
            .unwrap();
        self.data.pending.lock().unwrap().push(sid.clone());
    }
    pub async fn claim(&self, name: &str) -> ShardClaim {
        match self
            .store
            .claim(ClaimShard {
                shard: ShardId(42),
                me: self.instance(name),
                expected_epoch: None,
                request_id: RequestId(format!("initial-{name}")),
            })
            .await
            .unwrap()
        {
            LeaseClaim::Claimed(c) => c,
            x => panic!("{x:?}"),
        }
    }
}
pub fn instance(name: &str) -> Instance {
    Instance {
        node: name.into(),
        incarnation: format!("{name}-1"),
    }
}
pub fn sid() -> Sid {
    Sid("s0042.session".into())
}
pub fn initial() -> Checkpoint {
    Checkpoint {
        format_version: 2,
        commit_seq: CommitSeq(0),
        consumed: None,
        tasks: Default::default(),
        downstream_generation: 0,
        pending_cancel_did: None,
        runner_state: None,
        dispatch_seq: Default::default(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: Default::default(),
        last_event_id: None,
        outbox: None,
    }
}
pub async fn warm(workers: &[Arc<WorkerRuntime>]) {
    for _ in 0..3 {
        tokio::time::advance(Duration::from_secs(1)).await;
        for w in workers {
            w.tick().await.unwrap();
        }
    }
}
