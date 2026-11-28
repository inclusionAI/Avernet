#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "real-provider processor fixtures"
)]
mod blob;
mod control;
mod control_runners;
mod feed;
pub use blob::FaultBlob;
pub use control::FaultControl;
#[allow(
    unused_imports,
    reason = "shared fixture runners used by separate integration targets"
)]
pub use control_runners::*;
mod output;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::{
    archive::DbArchiveStore, coordination::CoordinationStore, dispatch::*, inbox::*,
    layout::KeyLayout, task_feed::*,
};
use bcs_dist_worker::*;
use bcs_service_api::{
    core::dist_runner::*,
    port::{
        dist_agent::*,
        repo::distributed::{common::*, inbox::*, ownership::*, session::*, task_feed::*},
    },
    types::dist_runtime::RuntimeError,
};
use bcs_test_support::store_model::Model;
pub use feed::FaultConsumer;
pub use output::FaultPublisher;
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicI64, AtomicUsize, Ordering},
    },
    time::Duration,
};

pub struct ClockAt(pub AtomicI64);
impl Clock for ClockAt {
    fn now(&self) -> TimestampMillis {
        TimestampMillis(self.0.load(Ordering::SeqCst))
    }
}
pub struct Directory;
#[async_trait]
impl DistSessionDirectoryPort for Directory {
    async fn authorize(&self, _: &TaskScope, _: &SessionTarget) -> Result<(), DirectoryError> {
        Ok(())
    }
    async fn resolve(
        &self,
        _: &TaskScope,
        _: &SessionTarget,
    ) -> Result<FrozenSessionProposal, DirectoryError> {
        Err(DirectoryError::NotFound)
    }

}
#[derive(Default)]
pub struct FinishingRunner {
    pub calls: AtomicUsize,
    pub text: usize,
}
#[async_trait::async_trait]
impl DistRunnerCoreService for FinishingRunner {
    fn validate_session(
        &self, _: &SessionSpec, _: &serde_json::Value,
    ) -> Result<Arc<dyn std::any::Any + Send + Sync>, RunnerError> {
        Ok(Arc::new(()))
    }
    async fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        Ok(RunnerState {
            format_version: 1,
            value: serde_json::Value::Null,
        })
    }
    async fn reduce(
        &self,
        _: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let RunnerInput::Start { tid, .. } = &inputs[0] else {
            panic!("unexpected test input")
        };
        Ok(Decision {
            next_state: state.clone(),
            next_deadlines: vec![],
            actions: vec![
                ActionIntent::Show {
                    tid: tid.clone(),
                    source: CorrelationKey("n".into()),
                    artifact_id: ArtifactId("answer".into()),
                    class: OutputClass::Output,
                    update: ArtifactUpdate::Upsert {
                        content: Payload::Inline(serde_json::json!("x".repeat(self.text))),
                    },
                },
                ActionIntent::FinishTask {
                    tid: tid.clone(),
                    status: TerminalStatus::Completed,
                    result: Payload::Inline(serde_json::json!({"finished":true})),
                },
            ],
        })
    }
}
pub struct FaultSessions {
    pub inner: Arc<CoordinationStore>,
    pub capacity: std::sync::Mutex<Option<SessionCheckpointCapacity>>,
    pub lose_commit: AtomicBool,
    pub fail_release: AtomicBool,
    pub pause_release: AtomicBool,
    pub release_entered: tokio::sync::Notify,
    pub release_resume: tokio::sync::Notify,
    pub reject_session: std::sync::Mutex<Option<Sid>>,
    pub release_calls: AtomicUsize,
    pub pause_commit: AtomicBool,
    pub entered: tokio::sync::Notify,
    pub release: tokio::sync::Notify,
    pub lost: std::sync::Mutex<Vec<CommitRequest>>,
}
#[async_trait]
impl SessionStorePort for FaultSessions {
    fn checkpoint_capacity(&self) -> Option<SessionCheckpointCapacity> {
        *self.capacity.lock().unwrap()
    }

    fn local_session_capacity(&self) -> Option<usize> {
        self.inner.local_session_capacity()
    }
    async fn release_local(&self, o: &SessionOwnership) -> Result<(), StoreError> {
        self.release_calls.fetch_add(1, Ordering::SeqCst);
        if self.fail_release.swap(false, Ordering::SeqCst) {
            return Err(StoreError::NotApplied {
                code: "injected_release".into(),
            });
        }
        self.inner.release_local(o).await?;
        if self.pause_release.swap(false, Ordering::SeqCst) {
            self.release_entered.notify_one();
            self.release_resume.notified().await;
        }
        Ok(())
    }
    async fn get_spec(&self, s: &Sid) -> Result<Option<SessionSpec>, StoreError> {
        self.inner.get_spec(s).await
    }
    async fn acquire(
        &self,
        s: &Sid,
        c: &ShardClaim,
        i: Checkpoint,
    ) -> Result<AcquireOutcome, StoreError> {
        self.inner.acquire(s, c, i).await
    }
    async fn handoff_session(
        &self,
        s: &SessionOwnership,
        t: &ShardClaim,
        r: &RequestId,
    ) -> Result<SessionTransferOutcome, StoreError> {
        self.inner.handoff_session(s, t, r).await
    }
    async fn load(&self, s: &Sid) -> Result<Option<CheckpointEnvelope>, StoreError> {
        self.inner.load(s).await
    }
    async fn commit(&self, r: CommitRequest) -> Result<CommitOutcome, StoreError> {
        if self.reject_session.lock().unwrap().as_ref() == Some(&r.owner.sid) {
            return Err(StoreError::NotApplied {
                code: "injected_session_unavailable".into(),
            });
        }
        let result = self.inner.commit(r.clone()).await?;
        if self.pause_commit.swap(false, Ordering::SeqCst) {
            self.entered.notify_one();
            self.release.notified().await;
        }
        if self.lose_commit.swap(false, Ordering::SeqCst) {
            self.lost.lock().unwrap().push(r.clone());
            Err(StoreError::OutcomeUnknown {
                operation_id: r.commit_id.0,
            })
        } else {
            Ok(result)
        }
    }
    async fn resolve_commit(
        &self,
        o: &SessionOwnership,
        i: &CommitId,
        d: &Digest,
    ) -> Result<CommitResolution, StoreError> {
        self.inner.resolve_commit(o, i, d).await
    }
    async fn record_recovery_attempt(
        &self,
        o: &SessionOwnership,
        s: CommitSeq,
        i: RequestId,
    ) -> Result<RecoveryUpdate, StoreError> {
        self.inner.record_recovery_attempt(o, s, i).await
    }
}
pub struct Fixture {
    pub model: Arc<Model>,
    pub scan_budget: Arc<bcs_dist_store::budget::SharedScanBudget>,
    pub coordination: Arc<CoordinationStore>,
    pub control: Arc<FaultControl>,
    pub sessions: Arc<FaultSessions>,
    pub inbox: Arc<StoreInboxProducer>,
    pub consumer: Arc<FaultConsumer>,
    pub archive: Arc<DbArchiveStore>,
    pub blobs: Arc<FaultBlob>,
    pub feed: Arc<StoreTaskFeed>,
    pub publisher: Arc<FaultPublisher>,
    pub dispatch: Arc<StoreDispatch>,
    pub queue: bcs_dist_connector_store::StoreDispatchQueue,
    pub runtime: Arc<WorkerRuntime>,
    pub processor: WorkerProcessor,
    pub clock: Arc<ClockAt>,
    pub layout: KeyLayout,
}
impl Fixture {
    pub async fn new(runner: Arc<dyn DistRunnerCoreService>) -> Self {
        Self::with_reader_limits(runner, 64, 128, 256).await
    }
    pub async fn with_reader_limits(
        runner: Arc<dyn DistRunnerCoreService>,
        readers: usize,
        watches: usize,
        receipts: usize,
    ) -> Self {
        Self::with_shards(runner, readers, watches, receipts, vec![ShardId(1)]).await
    }
    pub async fn with_shards(
        runner: Arc<dyn DistRunnerCoreService>,
        readers: usize,
        watches: usize,
        receipts: usize,
        shards: Vec<ShardId>,
    ) -> Self {
        let model = Arc::new(Model::default());
        let scan_budget = Arc::new(
            bcs_dist_store::budget::SharedScanBudget::new(
                bcs_dist_store::budget::ScanBudgetConfig::default(),
            )
            .unwrap(),
        );
        let layout = KeyLayout::new("processor", "test").unwrap();
        let clock = Arc::new(ClockAt(AtomicI64::new(1000)));
        let coordination = Arc::new(
            CoordinationStore::new(model.clone(), model.clone(), model.clone(), layout.clone())
                .unwrap(),
        );
        let control = Arc::new(FaultControl {
            inner: coordination.clone(),
            isolate_after_runner_success: AtomicBool::new(false),
            isolate_after_begin: AtomicBool::new(false),
            block_after_replay_begin: AtomicBool::new(false),
        });
        let sessions = Arc::new(FaultSessions {
            inner: coordination.clone(),
            capacity: std::sync::Mutex::new(coordination.checkpoint_capacity()),
            lose_commit: AtomicBool::new(false),
            fail_release: AtomicBool::new(false),
            pause_release: AtomicBool::new(false),
            release_entered: tokio::sync::Notify::new(),
            release_resume: tokio::sync::Notify::new(),
            reject_session: std::sync::Mutex::new(None),
            release_calls: AtomicUsize::new(0),
            pause_commit: AtomicBool::new(false),
            entered: tokio::sync::Notify::new(),
            release: tokio::sync::Notify::new(),
            lost: Default::default(),
        });
        let mut config = InboxProducerConfig::new(BackendId("model".into()));
        config.max_readers = readers;
        config.max_watches = watches;
        config.max_receipts = receipts;
        let inbox = Arc::new(
            StoreInboxProducer::new(
                scan_budget.clone(),
                Arc::new(OutputProjectionService),
                model.clone(),
                model.clone(),
                model.clone(),
                model.clone(),
                layout.clone(),
                clock.clone(),
                config,
            )
            .unwrap(),
        );
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        let archive = Arc::new(DbArchiveStore::new(db, model.clone(), layout.clone()).unwrap());
        let blobs = Arc::new(FaultBlob {
            inner: archive.clone(),
            fail_read: AtomicBool::new(false),
        });
        let feed = Arc::new(
            StoreTaskFeed::new(
                TaskFeedStores {
                    documents: model.clone(),
                    streams: model.clone(),
                    index: model.clone(),
                    blobs: archive.clone(),
                    results: archive.clone(),
                },
                Arc::new(OutputProjectionService),
                layout.clone(),
                TaskFeedConfig::default(),
            )
            .unwrap(),
        );
        let publisher = Arc::new(FaultPublisher {
            inner: feed.clone(),
            lose_append: AtomicBool::new(false),
            append_calls: AtomicUsize::new(0),
            fail_session: Default::default(),
            append_requests: Default::default(),
            pause_failure: AtomicBool::new(false),
            failure_entered: tokio::sync::Notify::new(),
            failure_resume: tokio::sync::Notify::new(),
        });
        let queue = bcs_dist_connector_store::StoreDispatchQueue::new(
            model.clone(),
            model.clone(),
            model.clone(),
            coordination.clone(),
            layout.clone(),
            clock.clone(),
            bcs_dist_connector_store::DispatchBoardConfig::new(
                BackendId(layout.namespace_identity()),
                DispatchConfig::default().command_streams,
            ),
        )
        .unwrap();
        let dispatch = Arc::new(
            StoreDispatch::new(
                DispatchStores {
                    scan_budget: scan_budget.clone(),
                    documents: model.clone(),
                    streams: model.clone(),
                    index: model.clone(),
                    membership: coordination.clone(),
                },
                layout.clone(),
                DispatchConfig::default(),
            )
            .unwrap(),
        );
        let consumer = Arc::new(FaultConsumer {
            inner: inbox.clone(),
            fail: Arc::new(AtomicBool::new(false)),
            opens: Arc::new(AtomicUsize::new(0)),
            failed_watch: Arc::new(std::sync::Mutex::new(None)),
            mixed_failure: Arc::new(std::sync::Mutex::new(None)),
        });
        let runtime = WorkerRuntime::start(
            "worker".into(),
            "http://worker.example.test".into(),
            WorkerConfig {
                shards,
                max_active_sessions: watches.min(16),
                page_size: 128,
                ..WorkerConfig::default()
            },
            coordination.clone(),
            coordination.clone(),
            sessions.clone(),
            consumer.clone(),
            inbox.clone(),
            Arc::new(crate::support::Peers::default()),
            Arc::new(|_| Ok(crate::support::initial())),
        )
        .await
        .unwrap();
        let processor = WorkerProcessor::new(
            runtime.clone(),
            ProcessorPorts {
                scan_budget: scan_budget.clone(),
                control: control.clone(),
                admission: Arc::new(StoreAdmissionControl::new((*inbox).clone(), feed.clone())),
                runner,
                projection: Arc::new(OutputProjectionService),
                tasks: feed.clone(),
                output: publisher.clone(),
                blobs: blobs.clone(),
                results: archive.clone(),
                dispatch: dispatch.clone(),
                now: {
                    let c = clock.clone();
                    Arc::new(move || c.now())
                },
            },
            ProcessorConfig::default(),
        )
        .unwrap();
        Self {
            model,
            scan_budget,
            coordination,
            control,
            sessions,
            inbox,
            consumer,
            archive,
            blobs,
            feed,
            publisher,
            queue,
            dispatch,
            runtime,
            processor,
            clock,
            layout,
        }
    }
    pub async fn admit(&self, name: &str) -> Tid {
        self.admit_for(spec(), name).await
    }
    pub async fn admit_for(&self, definition: SessionSpec, name: &str) -> Tid {
        let mut metadata = metadata();
        metadata.sid = definition.sid.clone();
        metadata.scope = definition.scope.clone();
        metadata.tid = Tid(format!("{}.{}", definition.sid.0, name));
        let tid = metadata.tid.clone();
        self.inbox
            .admit(AdmitRequest {
                scope: definition.scope.clone(),
                input: InputEnvelope {
                    schema_version: 1,
                    sid: definition.sid.clone(),
                    tid: tid.clone(),
                    input_id: InputId(name.into()),
                    business_digest: Digest(String::new()),
                    body: InputBody::User {
                        payload: Payload::Inline(serde_json::json!("start")),
                    },
                },
                create_session: Some(definition),
                create_task: Some(metadata),
            })
            .await
            .unwrap();
        tid
    }
    pub async fn warm(&self) {
        for _ in 0..4 {
            tokio::time::sleep(Duration::from_secs(1)).await;
            self.runtime.tick().await.unwrap();
        }
    }
    pub async fn step(&self) -> Result<(), RuntimeError> {
        self.runtime.tick().await?;
        self.processor.tick().await
    }
    pub async fn checkpoint(&self) -> Checkpoint {
        self.coordination
            .load(&spec().sid)
            .await
            .unwrap()
            .unwrap()
            .checkpoint
    }
    pub async fn snapshot(&self, tid: &Tid) -> TaskSnapshot {
        self.feed
            .load_snapshot(&spec().scope, tid)
            .await
            .unwrap()
            .unwrap()
    }
}
pub fn spec() -> SessionSpec {
    SessionSpec {
        schema_version: 1,
        sid: Sid("s0001.session".into()),
        shard: ShardId(1),
        scope: TaskScope {
            tenant: TenantId("t".into()),
            principal: PrincipalId("p".into()),
        },
        target: SessionTarget::Group { group: GroupId("g".into()) },
        mode_envelope: ModeConfigEnvelope {
            mode: CollaborationMode::StateMachine,
            mode_version: 1,
            revision: "1".into(),
            digest: Digest("wf-digest".into()),
            payload: Payload::Inline(serde_json::json!({})),
        },
        authorized_agents: vec![],
        limits: SessionLimits {
            max_queued_tasks: 32,
            max_task_steps: 100,
            task_timeout_millis: 60000,
            max_inline_bytes: 65536,
        },
        digest: Digest("spec".into()),
        created_at: TimestampMillis(0),
    }
}
pub fn metadata() -> TaskSnapshot {
    TaskSnapshot {
        schema_version: 1,
        scope: spec().scope,
        sid: spec().sid,
        tid: Tid("s0001.session.task".into()),
        content: TaskContent {
            status: TaskStatus::Submitted,
            interactions: vec![],
            snapshot: Payload::Inline(serde_json::json!({"format_version":1,"units":[]})),
            result_ref: None,
        },
        output_limits: PHASE_ONE_OUTPUT_LIMITS,
        output_usage: OutputUsage::default(),
        snapshot_event_id: None,
        source_commit: CommitSeq(0),
        created_at: TimestampMillis(0),
        updated_at: TimestampMillis(0),
    }
}

impl Fixture {
    pub async fn replacement(
        &self,
        runner: Arc<dyn DistRunnerCoreService>,
    ) -> (Arc<WorkerRuntime>, WorkerProcessor) {
        let runtime = WorkerRuntime::start(
            "replacement".into(),
            "http://replacement.example.test".into(),
            WorkerConfig {
                shards: vec![ShardId(1)],
                max_active_sessions: 16,
                page_size: 128,
                ..WorkerConfig::default()
            },
            self.coordination.clone(),
            self.coordination.clone(),
            self.sessions.clone(),
            self.inbox.clone(),
            self.inbox.clone(),
            Arc::new(crate::support::Peers::default()),
            Arc::new(|_| Ok(crate::support::initial())),
        )
        .await
        .unwrap();
        let processor = WorkerProcessor::new(
            runtime.clone(),
            ProcessorPorts {
                scan_budget: self.scan_budget.clone(),
                control: self.control.clone(),
                admission: Arc::new(StoreAdmissionControl::new(
                    (*self.inbox).clone(),
                    self.feed.clone(),
                )),
                runner,
                projection: Arc::new(OutputProjectionService),
                tasks: self.feed.clone(),
                output: self.publisher.clone(),
                blobs: self.blobs.clone(),
                results: self.archive.clone(),
                dispatch: self.dispatch.clone(),
                now: {
                    let c = self.clock.clone();
                    Arc::new(move || c.now())
                },
            },
            ProcessorConfig::default(),
        )
        .unwrap();
        (runtime, processor)
    }
}
