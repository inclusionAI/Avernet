//! The assembled same-process CompanionWorld: host drives, worker
//! incarnations, connector legs. (Shared items come from `super::*`.)
use super::*;
// ---------------------------------------------------------------------------
// The assembled companion world.
// ---------------------------------------------------------------------------

/// One live Worker incarnation over the world's durable joints; crash
/// vectors stop a handle, takeover vectors add one.
pub struct WorkerHandle {
    pub runtime: Arc<bcs_dist_worker::WorkerRuntime>,
    pub processor: Arc<bcs_dist_worker::WorkerProcessor>,
    pub stopped: bool,
}

pub struct CompanionWorld {
    pub assembly: GroupA2aAssembly,
    pub model: Arc<Model>,
    pub journal: Arc<DownstreamJournal>,
    pub peers: Arc<Peers>,
    pub workers: Mutex<Vec<WorkerHandle>>,
    /// The injected host directory: the publication seed resolves the
    /// group through the SAME seam the assembly mounts (different
    /// scenarios may publish a different frozen participant/workflow).
    /// Retain the SQLite fixture for the lifetime of its running roles.
    archive_dir: tempfile::TempDir,
    directory: std::sync::Mutex<Arc<dyn DistGroupDirectoryPort>>,
}

impl CompanionWorld {
    /// The quarantine-prone assembly: the fast-stop-deadline catalog
    /// plus the silent-remote journal plan — the world whose natural
    /// machinery produces a real `DispatchRecord.quarantined`.
    pub async fn assemble_quarantine_prone() -> Arc<Self> {
        let journal = DownstreamJournal::new();
        journal.set_plan(JournalPlan::SilentRemote);
        Self::assemble_with_parts(enabled_config(), journal, Arc::new(FastDeadlineCatalog)).await
    }

    /// Full real assembly over the in-process Model store ports, a temp
    /// SQLite archive pair, the two-node catalog, the D0 policy, the
    /// downstream journal and the in-process peers. The session document
    /// of the group is seeded through the provider's own document port —
    /// the durable write a publication service owns in a production world.
    pub async fn assemble(config: GroupA2aConfig) -> Arc<Self> {
        Self::assemble_with_parts(config, DownstreamJournal::new(), Arc::new(Catalog)).await
    }

    pub async fn assemble_with_downstream(
        directory: Arc<dyn DistGroupDirectoryPort>,
        downstream: Arc<dyn DistAgentClientPort>,
    ) -> Arc<Self> {
        Self::assemble_with_host(enabled_config(), DownstreamJournal::new(), directory, downstream).await
    }

    async fn assemble_with_parts(
        config: GroupA2aConfig,
        journal: Arc<DownstreamJournal>,
        directory: Arc<dyn DistGroupDirectoryPort>,
    ) -> Arc<Self> {
        Self::assemble_with_host(config, journal.clone(), directory, journal).await
    }

    async fn assemble_with_host(
        config: GroupA2aConfig,
        journal: Arc<DownstreamJournal>,
        directory: Arc<dyn DistGroupDirectoryPort>,
        downstream: Arc<dyn DistAgentClientPort>,
    ) -> Arc<Self> {
        let model = Arc::new(Model::default());
        let peers = Arc::new(Peers::default());
        let provider = GroupA2aProvider {
            kv: model.clone(),
            documents: model.clone(),
            dispatch_documents: model.clone(),
            queue_json: model.clone(),
            zset: model.clone(),
            streams: model.clone(),
            leases: model.clone(),
        };
        let host = GroupA2aHost {
            directory: directory.clone(),
            policy: Arc::new(AllowPolicy),
            downstream,
            peers: peers.clone(),
        };
        let archive = tempfile::tempdir().expect("temp archive dir");
        let assembly = bcs::server::group_a2a_wiring::assemble_inprocess(
            &config,
            provider,
            host,
            archive.path().to_path_buf(),
        )
        .await
        .expect("same-process joint assembly must mount over the real roles");
        peers.register(&assembly.roles.runtime);
        let world = Arc::new(Self {
            assembly,
            model,
            journal,
            peers,
            workers: Mutex::new(vec![]),
            archive_dir: archive,
            directory: std::sync::Mutex::new(directory.clone()),
        });
        // The wired Worker pair is the first live incarnation of the drive.
        {
            let roles = &world.assembly.roles;
            world.workers.lock().unwrap().push(WorkerHandle {
                runtime: roles.runtime.clone(),
                processor: roles.processor.clone(),
                stopped: false,
            });
        }
        Arc::clone(&world).seed_session().await;
        world
    }

    pub fn archive_path(&self) -> std::path::PathBuf {
        self.archive_dir.path().join("archive.sqlite")
    }

    /// The durable publication write of the group's strict session: the
    /// immutable SessionSpec document at `layout.spec(sid)`, participants
    /// and workflow of the frozen catalog definition.
    async fn seed_session(self: Arc<Self>) {
        let directory = self.directory.lock().unwrap().clone();
        let frozen = directory
            .resolve(&scope(), &group_id())
            .await
            .expect("catalog resolves the published group");
        let spec = SessionSpec {
            schema_version: 1,
            sid: sid(),
            shard: bcs_dist_store::layout::KeyLayout::shard_of(&sid()).expect("sid shard"),
            scope: scope(),
            kind: SessionKind::FsmGroup,
            group: group_id(),
            workflow: frozen.workflow,
            participants: frozen.participants,
            limits: frozen.limits,
            digest: Digest("companion-spec".into()),
            created_at: TimestampMillis(0),
        };
        let key = world_layout(&self).spec(&sid()).expect("spec key");
        self.model
            .create_document(&key, &serde_json::to_value(&spec).unwrap())
            .await
            .expect("seed session document");
    }

    /// A strict round request of the published group (the production entry
    /// the mounted `with_rounds` seam serves): stable request id, explicit
    /// generation, the seeded workflow ref and an empty history cut.
    pub fn round(&self, message: &str, generation: u64) -> GroupStartRequest {
        let empty_history = json!({"entries": []});
        AcceptedRound {
            input: RoundInput {
                schema_version: RoundSchemaVersion::V1,
                source: GroupRoundSource::GroupA2a,
                key: RoundKey {
                    scope: scope(),
                    group: group_id(),
                    sid: sid(),
                    tid: Tid(format!("{}.{}", sid().0, message)),
                    generation: generation.try_into().unwrap(),
                },
                ordered_parts: vec![RoundPart::Text {
                    text: message.into(),
                }],
                workflow_ref: crate::group_a2a_companion_support::catalog::workflow_ref(),
                history_cut: BlobRef {
                    key: "companion-history".into(),
                    digest: bcs_dist_encoding::digest_json(&empty_history),
                },
                // Absolute wall-clock deadline: the round must live inside a
                // real window (install freezes rounds whose window elapsed).
                execution_deadline: TimestampMillis(now_ms().0 + 240_000),
            },
            request_id: RequestId(format!("request-{message}")),
            request_digest: Digest(format!("digest-{message}")),
            normalization_ref: BlobRef {
                key: "companion-norm".into(),
                digest: Digest("companion-norm-digest".into()),
            },
            return_configuration: json!({"mode":"text"}),
        }
    }

    /// The live strict admit/get seam of the assembled surface.
    pub async fn admit_round(&self, request: &GroupStartRequest) -> Admission {
        self.assembly
            .roles
            .gateway_admission
            .admit_group_round(&scope(), request)
            .await
            .expect("licensed admit must answer")
    }

    /// The licensed authority view of one round key (`get` of the seam).
    pub async fn round_view(&self, key: &RoundKey) -> Option<GroupRoundView> {
        self.assembly
            .roles
            .rounds
            .get(key)
            .await
            .expect("licensed get must answer")
    }

    /// Drives n bounded host heartbeats of every live Worker incarnation
    /// (runtime tick + processor tick per handle), then the shared joint
    /// maintenance: inbox repair/discover, the supervisor pass (when
    /// enabled) and the bounded reclaim batch. The returned report
    /// AGGREGATES every joint tick of the whole drive — a notify that
    /// lands in an early iteration is never lost to a later empty pass.
    pub async fn drive(&self, n: usize) -> bcs::server::group_a2a_wiring::GroupTickReport {
        let mut total = bcs::server::group_a2a_wiring::GroupTickReport::default();
        let mut supervisor_seen = false;
        for _ in 0..n {
            // The in-process Model is the test-only lease provider: its
            // lease/membership expiries read the model's own clock, so the
            // host drive advances it exactly like the Singlebox worker story
            // does. Production reads real clocks on the (P) provider.
            self.model.advance(Duration::from_millis(10));
            for worker in self.workers.lock().unwrap().iter_mut() {
                if worker.stopped {
                    continue;
                }
                let _ = worker.runtime.heartbeat().await;
                let _ = worker.runtime.tick().await;
                let _ = worker.processor.tick().await;
            }
            // The shared joint heartbeat (inbox repair/discovery, the
            // bounded reclaim batch, the Dispatch Supervisor pass, and the
            // D3 held-index rescue that re-arms dead-holder dispatches for
            // the SUPERVISOR scan to discover and notify).
            let step = self.assembly.roles.joint_tick().await;
            total.reclaimed += step.reclaimed;
            total.worker.extend(step.worker);
            if let Some(pass) = step.supervisor {
                supervisor_seen = true;
                let merged = total.supervisor.get_or_insert_with(Default::default);
                merged.discovered += pass.discovered;
                merged.visited += pass.visited;
                merged.notified += pass.notified;
                merged.routed += pass.routed;
                merged.returned += pass.returned;
                merged.settled += pass.settled;
                merged.deferred += pass.deferred;
                merged.failures += pass.failures;
            }
            tokio::time::sleep(Duration::from_millis(5)).await
        }
        if !supervisor_seen {
            total.supervisor = None;
        }
        total
    }

    /// Starts the real connector run loop of the assembly (one
    /// incarnation), returning its task handle so the crash vectors can
    /// stop it.
    pub fn start_connector(&self) -> tokio::task::JoinHandle<()> {
        let engine = self.assembly.roles.connector.clone();
        tokio::spawn(async move {
            let _ = engine.run().await;
        })
    }

    /// Starts the successor Connector incarnation on the same durable
    /// joints after the predecessor's in-process crash: the predecessor's
    /// run loop is aborted and its membership goes unanswered, so the
    /// SUPERVISOR's own open-dispatch scan finds the held call, Dead
    /// liveness answers, and the wake is entirely the supervisor's — no
    /// host rescue seam in between (the delayed.rs contract note).
    pub async fn successor_after_connector_death(
        self: &Arc<Self>,
        name: &str,
    ) -> (tokio::task::JoinHandle<()>, bcs_dist_connector::ConnectorEngine) {
        self.connect_successor(name)
    }

    /// Starts a successor Connector incarnation on the same durable joints
    /// (queue, inbox, membership, archive) with the same downstream journal:
    /// the successor inherits everything except the dead incarnation's
    /// identity.
    pub fn connect_successor(
        self: &Arc<Self>,
        name: &str,
    ) -> (tokio::task::JoinHandle<()>, bcs_dist_connector::ConnectorEngine) {
        self.connect_with_config(name, |_| {})
    }

    pub fn connect_with_config(
        self: &Arc<Self>,
        name: &str,
        configure: impl FnOnce(&mut bcs_dist_connector::config::ConnectorConfig),
    ) -> (tokio::task::JoinHandle<()>, bcs_dist_connector::ConnectorEngine) {
        let roles = &self.assembly.roles;
        let mut config = bcs_dist_connector::config::ConnectorConfig::new(
            Instance {
                node: format!("{name}-connector"),
                incarnation: format!("{name}-connector-1"),
            },
            PoolId(POOL.into()),
        );
        config.pool = PoolId(POOL.into());
        // Membership registrations must be reachable to be accepted.
        config.control_address = format!("http://{name}-connector.example.invalid");
        configure(&mut config);
        let engine = bcs_dist_connector::ConnectorEngine::new(
            config,
            bcs_dist_connector::config::ConnectorPorts {
                queue: roles.queue.clone(),
                inbox: roles.inbox.clone(),
                agent: roles.downstream.clone(),
                membership: roles.coordination.clone(),
                blobs: roles.archive.clone(),
                node_history: Some(roles.node_history.clone()),
                rules: Arc::new(bcs_dist_connector::rules::DistConnectorRules),
            },
        )
        .expect("successor connector engine");
        let handle = tokio::spawn({
            let engine = engine.clone();
            async move {
                let _ = engine.run().await;
            }
        });
        (handle, engine)
    }

    /// Registers a FOREIGN Worker incarnation: a real runtime on the same
    /// joints that takes the shard lease but is NOT registered with the
    /// round binding's leaser registry — the same-process stand-in for a
    /// rogue/duplicate host process claiming the shard.
    pub async fn add_foreign_worker(&self, name: &str) {
        let roles = &self.assembly.roles;
        let runtime = bcs_dist_worker::WorkerRuntime::start(
            name.into(),
            format!("http://{name}.example.invalid"),
            worker_config(roles.shards.clone()),
            roles.coordination.clone(),
            roles.coordination.clone(),
            roles.coordination.clone(),
            roles.inbox.clone(),
            roles.inbox.clone(),
            self.peers.clone(),
            Arc::new(|_| Ok(empty_checkpoint())),
        )
        .await
        .expect("foreign worker runtime starts");
        self.peers.register(&runtime);
        let processor = Arc::new(
            bcs_dist_worker::WorkerProcessor::new(
                runtime.clone(),
                bcs_dist_worker::ProcessorPorts {
                    scan_budget: roles.scan_budget.clone(),
                    control: roles.coordination.clone(),
                    admission: Arc::new(bcs_dist_store::inbox::StoreAdmissionControl::new(
                        (*roles.inbox).clone(),
                        roles.feed.clone(),
                    )),
                    runner: Arc::new(bcs_dist_runner_fsm::FsmReducerRunner::d0()),
                    projection: Arc::new(bcs_dist_projection::OutputProjectionService),
                    directory: roles.directory.clone(),
                    tasks: roles.feed.clone(),
                    output: roles.feed.clone(),
                    blobs: roles.archive.clone(),
                    results: roles.archive.clone(),
                    dispatch: roles.dispatch.clone(),
                    rounds: Some(Arc::clone(&roles.rounds) as Arc<dyn bcs_service_api::port::repo::distributed::group_round::GroupRoundRepo>),
                    archives: Some(roles.artifacts.clone()),
                    now: Arc::new(now_ms),
                },
                bcs_dist_worker::ProcessorConfig::default(),
            )
            .expect("foreign processor assembles")
            .with_node_history(roles.node_history.clone()),
        );
        // Deliberately NO register_worker_leaser: this claims the shard
        // as an unregistered holder.
        self.workers.lock().unwrap().push(WorkerHandle {
            runtime,
            processor,
            stopped: false,
        });
    }

    /// Registers a second real Worker incarnation (graceful handoff peer)
    /// on the same durable joints, with its own real processor over the
    /// same strict-round spine.
    pub async fn add_worker(&self, name: &str) {
        let roles = &self.assembly.roles;
        let runtime = bcs_dist_worker::WorkerRuntime::start(
            name.into(),
            format!("http://{name}.example.invalid"),
            worker_config(roles.shards.clone()),
            roles.coordination.clone(),
            roles.coordination.clone(),
            roles.coordination.clone(),
            roles.inbox.clone(),
            roles.inbox.clone(),
            self.peers.clone(),
            Arc::new(|_| Ok(empty_checkpoint())),
        )
        .await
        .expect("second worker runtime starts");
        self.peers.register(&runtime);
        let processor = Arc::new(
            bcs_dist_worker::WorkerProcessor::new(
                runtime.clone(),
                bcs_dist_worker::ProcessorPorts {
                    scan_budget: roles.scan_budget.clone(),
                    control: roles.coordination.clone(),
                    admission: Arc::new(bcs_dist_store::inbox::StoreAdmissionControl::new(
                        (*roles.inbox).clone(),
                        roles.feed.clone(),
                    )),
                    runner: Arc::new(bcs_dist_runner_fsm::FsmReducerRunner::d0()),
                    projection: Arc::new(bcs_dist_projection::OutputProjectionService),
                    directory: roles.directory.clone(),
                    tasks: roles.feed.clone(),
                    output: roles.feed.clone(),
                    blobs: roles.archive.clone(),
                    results: roles.archive.clone(),
                    dispatch: roles.dispatch.clone(),
                    rounds: Some(roles.rounds.clone()),
                    archives: Some(roles.artifacts.clone()),
                    now: Arc::new(now_ms),
                },
                bcs_dist_worker::ProcessorConfig::default(),
            )
            .expect("second processor assembles")
            .with_node_history(roles.node_history.clone()),
        );
        // The successor is a legal lessee of this host's shard: the
        // round admission binding follows its claim after the takeover.
        self.assembly.roles.register_worker_leaser(runtime.instance());
        self.workers.lock().unwrap().push(WorkerHandle {
            runtime,
            processor,
            stopped: false,
        });
    }
}

fn world_layout(world: &CompanionWorld) -> &bcs_dist_store::layout::KeyLayout {
    &world.assembly.roles.layout
}

fn worker_config(shards: Vec<ShardId>) -> bcs_dist_worker::WorkerConfig {
    bcs_dist_worker::WorkerConfig {
        shards,
        heartbeat_interval: Duration::from_millis(50),
        lease_ttl: Duration::from_secs(1),
        request_timeout: Duration::from_millis(200),
        exit_margin: Duration::from_millis(5),
        check_interval: Duration::from_millis(5),
        candidate_backoff: Duration::from_millis(5),
        max_active_sessions: 8,
        max_acquires_per_tick: 4,
        max_shards_per_tick: 1,
        page_size: 128,
        ..bcs_dist_worker::WorkerConfig::default()
    }
}

fn empty_checkpoint() -> bcs_service_api::port::repo::distributed::session::Checkpoint {
    bcs_service_api::port::repo::distributed::session::Checkpoint {
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


