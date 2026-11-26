use super::workload::{Directory, Runner, initial};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::{
    archive::DbArchiveStore, budget::*, coordination::CoordinationStore, dispatch::*, inbox::*,
    layout::KeyLayout, task_feed::*,
};
use bcs_dist_worker::{supervisor::*, *};
use bcs_service_api::{
    port::{
        dist_runtime::*,
        repo::distributed::{admission::*, ownership::*},
    },
    types::dist_runtime::*,
};
use bcs_test_support::store_model::Model;
use std::{
    collections::BTreeMap,
    path::Path,
    sync::{Arc, Mutex, Weak},
    time::Duration,
};
#[derive(Default)]
struct Peers(Mutex<BTreeMap<String, Weak<WorkerRuntime>>>);
#[async_trait]
impl DistWorkerPeerPort for Peers {
    async fn handoff(
        &self,
        target: &MemberRecord,
        request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        let runtime = self
            .0
            .lock()
            .unwrap()
            .get(&target.instance.incarnation)
            .and_then(Weak::upgrade)
            .ok_or(RuntimeError {
                code: "peer_offline".into(),
            })?;
        bcs_service_api::application::dist_runtime::DistWorkerRuntimeService::accept_handoff(
            &bcs_app_dist_runtime::WorkerRuntimeApplication::new(runtime),
            request.from.owner.instance.clone(),
            request,
        )
        .await
    }
    async fn source_done(
        &self,
        target: &MemberRecord,
        source: Instance,
        claim: ShardClaim,
        id: RequestId,
    ) -> Result<bool, RuntimeError> {
        let runtime = self
            .0
            .lock()
            .unwrap()
            .get(&target.instance.incarnation)
            .and_then(Weak::upgrade)
            .ok_or(RuntimeError {
                code: "peer_offline".into(),
            })?;
        bcs_service_api::application::dist_runtime::DistWorkerRuntimeService::handoff_done(
            &bcs_app_dist_runtime::WorkerRuntimeApplication::new(runtime),
            source,
            claim,
            id,
        )
        .await
    }
}
pub struct Worker {
    pub runtime: Arc<WorkerRuntime>,
    pub processor: Arc<WorkerProcessor>,
    pub stopped: bool,
}
pub struct Host {
    pub model: Arc<Model>,
    pub layout: KeyLayout,
    pub inbox: Arc<StoreInboxProducer>,
    pub feed: Arc<StoreTaskFeed>,
    pub archive: Arc<DbArchiveStore>,
    pub coordination: Arc<CoordinationStore>,
    pub dispatch: Arc<StoreDispatch>,
    pub queue: bcs_dist_connector_store::StoreDispatchQueue,
    pub runner: Arc<Runner>,
    pub workers: Vec<Worker>,
    pub supervisor: DispatchSupervisor,
    budget: Arc<SharedScanBudget>,
    peers: Arc<Peers>,
}
impl Host {
    pub async fn new(path: &Path) -> Self {
        let model = Arc::new(Model::default());
        let layout = KeyLayout::new("singlebox", "worker-mock").unwrap();
        let budget = Arc::new(SharedScanBudget::new(ScanBudgetConfig::default()).unwrap());
        let coordination = Arc::new(
            CoordinationStore::new(model.clone(), model.clone(), model.clone(), layout.clone())
                .unwrap(),
        );
        let inbox = Arc::new(
            StoreInboxProducer::new(
                budget.clone(),
                Arc::new(OutputProjectionService),
                model.clone(),
                model.clone(),
                model.clone(),
                model.clone(),
                layout.clone(),
                Arc::new(SystemClock),
                InboxProducerConfig::new(BackendId("singlebox-memory-mock".into())),
            )
            .unwrap(),
        );
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new_file(path).unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        let archive = Arc::new(DbArchiveStore::new(db, model.clone(), layout.clone()).unwrap());
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
        let queue = bcs_dist_connector_store::StoreDispatchQueue::new(
            model.clone(),
            model.clone(),
            model.clone(),
            coordination.clone(),
            layout.clone(),
            Arc::new(SystemClock),
            bcs_dist_connector_store::DispatchBoardConfig::new(
                BackendId(layout.namespace_identity()),
                DispatchConfig::default().command_streams,
            ),
        )
        .unwrap();
        let dispatch = Arc::new(
            StoreDispatch::new(
                DispatchStores {
                    scan_budget: budget.clone(),
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
        let supervisor = DispatchSupervisor::new(
            SupervisorPorts {
                dispatch: dispatch.clone(),
                sessions: coordination.clone(),
                inbox: inbox.clone(),
                membership: coordination.clone(),
                scan_budget: budget.clone(),
                now: Arc::new(|| SystemClock.now()),
            },
            SupervisorConfig {
                shards: vec![ShardId(1)],
                ..Default::default()
            },
        )
        .unwrap();
        let mut host = Self {
            model,
            layout,
            inbox,
            feed,
            archive,
            coordination,
            queue,
            dispatch,
            runner: Arc::new(Runner::default()),
            workers: vec![],
            supervisor,
            budget,
            peers: Arc::new(Peers::default()),
        };
        host.add_worker("worker-a").await;
        host
    }
    pub async fn add_worker(&mut self, name: &str) {
        let coordination = Arc::new(
            CoordinationStore::new(
                self.model.clone(),
                self.model.clone(),
                self.model.clone(),
                self.layout.clone(),
            )
            .unwrap(),
        );
        let runtime = WorkerRuntime::start(
            name.into(),
            format!("http://{name}.example.test"),
            WorkerConfig {
                shards: vec![ShardId(1)],
                heartbeat_interval: Duration::from_millis(50),
                lease_ttl: Duration::from_secs(1),
                request_timeout: Duration::from_millis(200),
                exit_margin: Duration::from_millis(5),
                check_interval: Duration::from_millis(5),
                candidate_backoff: Duration::from_millis(5),
                max_active_sessions: 8,
                max_acquires_per_tick: 4,
                max_shards_per_tick: 1,
                ..Default::default()
            },
            coordination.clone(),
            coordination.clone(),
            coordination.clone(),
            self.inbox.clone(),
            self.inbox.clone(),
            self.peers.clone(),
            Arc::new(|_| Ok(initial())),
        )
        .await
        .unwrap();
        self.peers.0.lock().unwrap().insert(
            runtime.instance().incarnation.clone(),
            Arc::downgrade(&runtime),
        );
        let processor = WorkerProcessor::new(
            runtime.clone(),
            ProcessorPorts {
                scan_budget: self.budget.clone(),
                control: coordination,
                admission: Arc::new(StoreAdmissionControl::new(
                    (*self.inbox).clone(),
                    self.feed.clone(),
                )),
                runner: self.runner.clone(),
                projection: Arc::new(OutputProjectionService),
                directory: Arc::new(Directory),
                tasks: self.feed.clone(),
                output: self.feed.clone(),
                blobs: self.archive.clone(),
                results: self.archive.clone(),
                dispatch: self.dispatch.clone(),
                rounds: None,
                archives: None,
                now: Arc::new(|| SystemClock.now()),
            },
            ProcessorConfig::default(),
        )
        .unwrap();
        self.workers.push(Worker {
            runtime,
            processor: Arc::new(processor),
            stopped: false,
        });
    }
    pub async fn tick(&mut self, count: u64) -> anyhow::Result<Vec<String>> {
        let mut processor_errors = vec![];
        for _ in 0..count.min(500) {
            self.model.advance(Duration::from_millis(2));
            for worker in &mut self.workers {
                if worker.stopped {
                    continue;
                }
                worker.runtime.heartbeat().await?;
                worker.runtime.tick().await?;
                if let Err(error) = worker.processor.tick().await {
                    processor_errors.push(error.code);
                }
            }
            self.inbox.repair(ShardId(1)).await?;
            self.inbox.discover(ShardId(1)).await?;
            let report = self.supervisor.tick().await;
            anyhow::ensure!(report.failures == 0, "supervisor failed: {report:?}");
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
        Ok(processor_errors)
    }
}

impl Host {
    /// Drive real graceful handoff while the destination keeps serving ticks.
    /// Both Workers share this process's mock; no Store restart is involved.
    pub async fn handoff_cycle(&mut self) -> anyhow::Result<serde_json::Value> {
        use bcs_service_api::application::dist_runtime::DistWorkerRuntimeService;
        use std::sync::atomic::{AtomicBool, Ordering};
        let source_index = self.workers.len() - 1;
        let source = self.workers[source_index].runtime.clone();
        self.add_worker("worker-handoff-target").await;
        let target = self.workers.last().unwrap().runtime.clone();
        let processor = self.workers.last().unwrap().processor.clone();
        let ready_until = tokio::time::Instant::now() + Duration::from_secs(5);
        while !DistRuntimeControlPort::snapshot(target.as_ref())
            .await?
            .ready
        {
            anyhow::ensure!(
                tokio::time::Instant::now() < ready_until,
                "handoff target did not become ready"
            );
            let errors = self.tick(5).await?;
            anyhow::ensure!(
                errors.is_empty(),
                "processor failed before handoff: {errors:?}"
            );
        }
        let before = self.coordination.current(ShardId(1)).await?.unwrap();
        let finished = AtomicBool::new(false);
        let application = bcs_app_dist_runtime::WorkerRuntimeApplication::new(source.clone());
        let (drained, driven) = tokio::join!(
            async {
                let result = application.drain(Duration::from_secs(2)).await;
                finished.store(true, Ordering::SeqCst);
                result
            },
            async {
                let mut errors = vec![];
                while !finished.load(Ordering::SeqCst) {
                    if let Err(error) = target.heartbeat().await {
                        errors.push(error.code);
                    }
                    if let Err(error) = target.tick().await {
                        errors.push(error.code);
                    }
                    if let Err(error) = processor.tick().await {
                        errors.push(error.code);
                    }
                    tokio::time::sleep(Duration::from_millis(2)).await;
                }
                errors
            },
        );
        self.workers[source_index].stopped = true;
        anyhow::ensure!(driven.is_empty(), "handoff destination failed: {driven:?}");
        let after = self.coordination.current(ShardId(1)).await?.unwrap();
        Ok(
            serde_json::json!({"outcome":format!("{:?}",drained?),"previous_owner":before.claim.owner.instance,"owner":after.claim.owner.instance,"target":target.instance(),"handoff_recorded":after.handoff.is_some()}),
        )
    }
}
