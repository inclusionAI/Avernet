//! Plan T9 composition root: the FSM-Group-as-A2A-Agent joint roles.
//!
//! Delivery scope (plan `apps/bcs/specs/2026-11-20-fsm-group-a2a-discussion` T9
//! + the 2026-11-25 split): configuration seams, acceptance procedures and
//! functional assembly only. The external persistent coordination provider
//! (P), real cross-process fault matrices, real MySQL acceptance and capacity
//! certification stay RECORDED AS BLOCKED — never satisfied here, never
//! substituted by same-process evidence.
//!
//! Invariants enforced by this module:
//!
//! - **Startup failure, not degradation.** `mount_production` fails the
//!   process startup when the joint assembly is enabled but the persistent
//!   provider (P) is unavailable. No in-memory substitution may ever run on
//!   the production path.
//! - **Opt-in only.** `[group_a2a] enabled` defaults to false: no route
//!   mounts and no existing route changes while the surface is closed.
//! - **Composition-root discipline.** Real group directory, grant policy,
//!   downstream agent transport and worker peer transport are injected
//!   here; handlers only ever see application services, never repos.
//!
//! Lifecycle note (recorded): the assembly itself spawns nothing. The host
//! drives the joint heartbeats through [`GroupA2aRoles::tick`] (and starts/
//! stops the connector's `run` loop), so crash vectors stop drives
//! deterministically; a production P assembly owns the native loops.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use bcs_dist_model::{BackendId, Instance, PoolId, ShardId, TimestampMillis};
use bcs_service_api::port::repo::distributed::group_round::RoundBudgetProfile;
use bcs_service_api::port::repo::distributed::result::ArchiveBudget;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::archive::DbArchiveStore;
use bcs_dist_store::coordination::CoordinationStore;
use bcs_dist_store::dispatch::{DispatchConfig, DispatchStores, StoreDispatch};
use bcs_dist_store::group_admission::{GroupAdmissionConfig, StoreGroupRoundAdmission};
use bcs_dist_store::group_artifact::{GroupArtifactStore, JsonProjectionValidator};
use bcs_dist_store::inbox::{Clock, InboxProducerConfig, StoreInboxProducer, SystemClock};
use bcs_dist_store::layout::KeyLayout;
use bcs_dist_store::task_feed::{StoreTaskFeed, TaskFeedConfig, TaskFeedStores};
use bcs_dist_store::budget::{ScanBudgetConfig, SharedScanBudget};
use bcs_service_api::port::dist_agent::DistAgentClientPort;
use bcs_service_api::port::repo::distributed::admission::AdmissionMaintenancePort;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::ownership::{ShardClaim, ShardLeasePort};
use bcs_store_api::{
    document::DocumentStorePlugin, json::JsonStorePlugin, kv::KvStorePlugin,
    lease::LeaseStorePlugin, stream::StreamStorePlugin, zset::ZSetStorePlugin,
};

use crate::config::GroupA2aConfig;

/// Startup-grade assembly errors. `Startup` variants are operator-facing
/// failures: process start must not continue with the surface half-alive.
#[derive(Debug, thiserror::Error)]
pub enum GroupA2aWiringError {
    #[error("group A2A surface is not mounted: {0}")]
    NotMounted(&'static str),
    #[error("group A2A startup failure: {0}")]
    Startup(&'static str),
    #[error("group A2A assembly failed: {code}")]
    Assembly { code: String },
}

/// The persistent-provider seam (P): every durable role of the joint
/// assembly reads and writes through these plugin ports. The OSS tree ships
/// no certified persistent implementation of the coordination plane yet —
/// P development and certification are out of scope for this delivery —
/// so a production deployment that enables the surface fails here at
/// startup instead of degrading to an in-memory role.
pub struct GroupA2aProvider {
    pub kv: Arc<dyn KvStorePlugin>,
    pub documents: Arc<dyn DocumentStorePlugin>,
    /// The dispatch plane's document view (`DocumentStorePlugin` plus the
    /// JSON store capability the dispatch directory requires).
    pub dispatch_documents: Arc<dyn bcs_dist_store::dispatch::DispatchDocuments>,
    pub zset: Arc<dyn ZSetStorePlugin>,
    pub streams: Arc<dyn StreamStorePlugin>,
    pub leases: Arc<dyn LeaseStorePlugin>,
    /// The queue-plane JSON store (command-stream hints of the dispatch
    /// board). Same concrete provider object as `documents`.
    pub queue_json: Arc<dyn JsonStorePlugin>,
}

impl GroupA2aProvider {
    /// P-seam of the production path. When a certified provider lands it is
    /// registered here (and nowhere else); until then enabling the surface
    /// keeps failing the startup on purpose.
    pub fn resolve_production() -> Result<Self, GroupA2aWiringError> {
        Err(GroupA2aWiringError::Startup(
            "persistent coordination provider (P) unavailable: no certified \
             implementation is registered in this build",
        ))
    }
}

/// Host-supplied roles the composition root injects (never exposed to
/// handlers): the real group directory, the D0 host-registered grant
/// policy, the downstream A2A agent transport, and the Worker peer
/// transport of cross-worker handoff.
pub struct GroupA2aHost {
    pub directory: Arc<dyn bcs_service_api::port::dist_agent::DistGroupDirectoryPort>,
    pub policy: Arc<dyn bcs_service_api::application::dist_gateway::GroupGrantPolicy>,
    pub downstream: Arc<dyn DistAgentClientPort>,
    /// Worker peer transport of `DistWorkerPeerPort` handoffs. Same-process
    /// wiring registers the in-process peer map; a production cross-process
    /// transport is part of the blocked(P) delivery.
    pub peers: Arc<dyn bcs_service_api::port::dist_runtime::DistWorkerPeerPort>,
}

/// Real, same-process handles of the joint roles for acceptance vectors.
/// Every field is a production service assembled over the injected
/// provider ports; crash/takeover vectors operate on these roles.
pub struct GroupA2aRoles {
    pub coordination: Arc<CoordinationStore>,
    /// The shared scan budget every durable scanning role co-owns: joint
    /// rebuilds (workers, supervisors) must register on this identity.
    pub scan_budget: Arc<SharedScanBudget>,
    pub inbox: Arc<StoreInboxProducer>,
    pub feed: Arc<StoreTaskFeed>,
    pub dispatch: Arc<StoreDispatch>,
    pub queue: Arc<bcs_dist_connector_store::StoreDispatchQueue>,
    pub archive: Arc<DbArchiveStore>,
    pub node_history: Arc<bcs_dist_store::node_history::DbNodeHistoryStore>,
    pub artifacts: Arc<GroupArtifactStore>,
    /// The strict licensed admit/get seam, bound to the shard lease and
    /// rebound on rotation (crash takeovers). All call sites use the
    /// `GroupRoundRepo` surface.
    pub rounds: Arc<dyn GroupRoundRepo>,
    /// The concrete binding behind `rounds` (held for the leaser registry
    /// a host extends when it spawns successor Workers).
    pub rounds_binding: Arc<RebindingRoundAdmission>,
    /// The mounted strict-round admission seam of the Gateway
    /// (`with_rounds`) plus the licensed admit entry the group surface uses.
    pub gateway_admission: Arc<bcs_dist_worker::WorkerTaskAdmission>,
    pub runtime: Arc<bcs_dist_worker::WorkerRuntime>,
    pub processor: Arc<bcs_dist_worker::WorkerProcessor>,
    /// `None` while the Supervisor is disabled — the crash-recovery control
    /// group the acceptance vectors require to fail. The pass state is
    /// owned (the supervisor's visit cycle is sequential by design), so
    /// the host heartbeat locks the joint drive.
    pub supervisor: Option<Arc<tokio::sync::Mutex<bcs_dist_worker::supervisor::DispatchSupervisor>>>,
    pub connector: bcs_dist_connector::ConnectorEngine,
    /// The shared reader of the listing and task-read surfaces: the
    /// signed-cursor store (production `cursor_secret` plumbing).
    pub reader: Arc<bcs_dist_gateway_store::StoreTaskReader>,
    pub gateway: Arc<bcs_app_dist_gateway::DistGateway>,
    pub directory: Arc<dyn bcs_service_api::port::dist_agent::DistGroupDirectoryPort>,
    pub policy: Arc<dyn bcs_service_api::application::dist_gateway::GroupGrantPolicy>,
    pub downstream: Arc<dyn DistAgentClientPort>,
    pub shards: Vec<ShardId>,
    /// The coordination-plane layout: session-spec keys (`layout.spec(sid)`)
    /// are where a published group's durable session document is seeded.
    pub layout: KeyLayout,
}

impl std::fmt::Debug for GroupA2aRoles {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("GroupA2aRoles")
    }
}

/// The mounted joint assembly: roles plus the REST/SSE router of the
/// published FSM-group surface (T8 wire contract).
pub struct GroupA2aAssembly {
    pub roles: GroupA2aRoles,
    pub router: axum::Router,
}

impl std::fmt::Debug for GroupA2aAssembly {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("GroupA2aAssembly")
    }
}

/// Production entry used by `server_run`: returns the mounted assembly only
/// when the surface is explicitly enabled; `Ok(None)` while closed means no
/// route mounts and no existing route changes. Enabled-but-unavailable is a
/// startup failure, never a degraded runtime.
pub async fn mount_production(
    config: &GroupA2aConfig,
) -> Result<Option<GroupA2aAssembly>, GroupA2aWiringError> {
    if !config.enabled {
        return Ok(None);
    }
    // P is a startup-grade requirement of the production assembly: the
    // failure below (and nothing else) is the correct behavior until a
    // certified persistent provider is registered.
    let _provider = GroupA2aProvider::resolve_production()?;
    // Reachable only after a certified provider registers itself (this
    // build has none): the production assembly of the joint roles is part
    // of that same P delivery, never a runtime substitute.
    Err(GroupA2aWiringError::Startup(
        "persistent coordination provider resolved but the production joint \
         assembly is not wired to it in this build",
    ))
}

fn assembly_err(e: impl std::fmt::Display) -> GroupA2aWiringError {
    GroupA2aWiringError::Assembly { code: e.to_string() }
}

/// Same-process opt-in assembly (the reusable acceptance harness of the
/// companion target). Builds the real joint roles over injected provider
/// ports with the SQLite archive provider; the same-process result is
/// functional evidence only and never replaces the blocked(P) evidence.
///
/// Seams the assembly mounts (plan T9 lineage):
/// - the signed-cursor listing (`cursor_secret` plumbing, fail-closed
///   while unset) of the T8 public surface, over the shared reader;
/// - the strict `with_rounds` licensed admit/get on the Gateway admission
///   and the Worker's round license consumption;
/// - the Dispatch Supervisor behind `[group_a2a] supervisor_enabled`
///   (`None` while disabled);
/// - the verification-lane backoff ceiling from the config (T16 residual:
///   `verification_backoff_max_ms`);
/// - the group Card discovery over the same directory/policy pair;
/// - the bounded dispatch-directory reclaim over the shared claim.
pub async fn assemble_inprocess(
    config: &GroupA2aConfig,
    provider: GroupA2aProvider,
    host: GroupA2aHost,
    archive_dir: PathBuf,
) -> Result<GroupA2aAssembly, GroupA2aWiringError> {
    let namespace = config.namespace.as_str();
    let layout = KeyLayout::new("bcs", namespace).map_err(assembly_err)?;
    let scan_budget = Arc::new(
        SharedScanBudget::new(ScanBudgetConfig::default()).map_err(assembly_err)?,
    );
    let coordination = Arc::new(
        CoordinationStore::new(
            provider.documents.clone(),
            provider.leases.clone(),
            provider.zset.clone(),
            layout.clone(),
        )
        .map_err(assembly_err)?,
    );
    let inbox_clock: Arc<dyn Clock> = Arc::new(SystemClock);
    let backend = BackendId(namespace.to_string());
    let inbox = Arc::new(
        StoreInboxProducer::new(
            scan_budget.clone(),
            Arc::new(OutputProjectionService),
            provider.kv.clone(),
            provider.documents.clone(),
            provider.zset.clone(),
            provider.streams.clone(),
            layout.clone(),
            inbox_clock.clone(),
            InboxProducerConfig::new(backend.clone()),
        )
        .map_err(assembly_err)?,
    );
    // Result/Blob durability: the existing SQLite provider + migration
    // chain, one file per store role.
    let archive_db = sqlite_at(&archive_dir, "archive.sqlite").await?;
    let node_history = Arc::new(bcs_dist_store::node_history::DbNodeHistoryStore::new(archive_db.clone())
        .map_err(assembly_err)?);
    let archive = Arc::new(
        DbArchiveStore::new(archive_db, provider.documents.clone(), layout.clone())
            .map_err(assembly_err)?,
    );
    let artifacts_db = sqlite_at(&archive_dir, "artifacts.sqlite").await?;
    let artifacts = Arc::new(
        GroupArtifactStore::new(
            artifacts_db,
            provider.documents.clone(),
            layout.clone(),
            Arc::new(JsonProjectionValidator),
            ArchiveBudget { version: 1, profile: RoundBudgetProfile::v1() },
        )
        .map_err(assembly_err)?,
    );
    let feed = Arc::new(
        StoreTaskFeed::new(
            TaskFeedStores {
                documents: provider.documents.clone(),
                streams: provider.streams.clone(),
                index: provider.zset.clone(),
                blobs: archive.clone(),
                results: archive.clone(),
            },
            Arc::new(OutputProjectionService),
            layout.clone(),
            TaskFeedConfig::default(),
        )
        .map_err(assembly_err)?,
    );
    let dispatch = Arc::new(
        StoreDispatch::new(
            DispatchStores {
                scan_budget: scan_budget.clone(),
                documents: provider.dispatch_documents.clone(),
                streams: provider.streams.clone(),
                index: provider.zset.clone(),
                membership: coordination.clone(),
            },
            layout.clone(),
            DispatchConfig::default(),
        )
        .map_err(assembly_err)?,
    );
    let queue = Arc::new(bcs_dist_connector_store::StoreDispatchQueue::new(
        provider.queue_json.clone(),
        provider.zset.clone(),
        provider.streams.clone(),
        coordination.clone(),
        layout.clone(),
        inbox_clock.clone(),
        bcs_dist_connector_store::DispatchBoardConfig::new(
            backend.clone(),
            DispatchConfig::default().command_streams,
        ),
    )
    .map_err(assembly_err)?,
    );
    // Shared reader of the gateway: the legacy TaskReaderPort and the T8
    // GroupListingPort over one store, with the production cursor-secret
    // plumbing (fail-closed while unset — the store's own gate).
    let mut reader_config = bcs_dist_gateway_store::GatewayStoreConfig::new(backend);
    reader_config.cursor_secret =
        config.cursor_secret.as_ref().map(|secret| secret.as_bytes().to_vec());
    let reader = Arc::new(
        bcs_dist_gateway_store::StoreTaskReader::with_group_archives(
            provider.documents.clone(),
            provider.zset.clone(),
            provider.streams.clone(),
            archive.clone(),
            layout.clone(),
            reader_config,
            (artifacts.clone(), feed.clone()),
        )
        .map_err(assembly_err)?,
    );
    // The Worker: real runtime (heartbeat, lease, peer handoff) plus the
    // real processor over the strict-round spine.
    let shards = config
        .shards
        .iter()
        .map(|id| ShardId(*id))
        .collect::<Vec<_>>();
    // Single-shard scope: the strict round admission binding follows ONE
    // shard record (config `shards[0]`); a multi-shard `[group_a2a]` needs
    // one assembly per shard — the host mounts those separately (P-time).
    if shards.len() != 1 {
        return Err(GroupA2aWiringError::Startup(
            "group A2A assembly currently serves exactly one shard: `[group_a2a].shards` must have one id",
        ));
    }
    let runtime = bcs_dist_worker::WorkerRuntime::start(
        format!("{namespace}-worker"),
        format!("http://{namespace}-worker.example.invalid"),
        bcs_dist_worker::WorkerConfig {
            shards: shards.clone(),
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
        },
        coordination.clone(),
        coordination.clone(),
        coordination.clone(),
        inbox.clone(),
        inbox.clone(),
        host.peers.clone(),
        Arc::new(|_| Ok(empty_checkpoint())),
    )
    .await
    .map_err(|e| assembly_err(e.code))?;
    // The shard claim the strict round admission shares: a real
    // leader-of-shard claim owned by the runtime above. The strict
    // admission must share that claim or every CAS would fence.
    let claim = shard_claim(&coordination, &runtime, &shards[0]).await?;
    // The claim-rebinding wrapper (plan T9): the underlying admission pins
    // its construction claim, so lease rotation (a successor Worker's fresh
    // epoch after a crash) fences it. The wrapper rebuilds the binding
    // against the current shard record; durable licenses never move.
    let rebinding_build = {
        let coordination = coordination.clone();
        let inbox = inbox.clone();
        let documents = provider.documents.clone();
        let layout = layout.clone();
        let inbox_clock = inbox_clock.clone();
        let artifacts = artifacts.clone();
        let node_history = node_history.clone();
        let archive = archive.clone();
        move |claim: ShardClaim| {
            StoreGroupRoundAdmission::with_artifacts(
                coordination.clone(),
                inbox.clone(),
                documents.clone(),
                claim,
                layout.clone(),
                GroupAdmissionConfig::default(),
                inbox_clock.clone(),
                artifacts.clone(),
            )
            .map(|rounds| rounds.with_node_history(node_history.clone(), archive.clone()))
            .map_err(|e| GroupA2aWiringError::Assembly { code: e.to_string() })
        }
    };
    let rebinding_first = rebinding_build(claim).map_err(assembly_err)?;
    let rounds_binding = Arc::new(RebindingRoundAdmission {
        coordination: coordination.clone(),
        shard: shards[0],
        leasers: std::sync::Mutex::new(std::collections::BTreeSet::from_iter(
            std::iter::once(runtime.instance().incarnation.clone()),
        )),
        state: tokio::sync::RwLock::new(Arc::new(rebinding_first)),
        rebuild: Box::new(rebinding_build),
    });
    let rounds: Arc<dyn GroupRoundRepo> = rounds_binding.clone();
    // The strict seam (plan T9 wiring item b): the Gateway admission and
    // the Worker processor both consume the same licensed round repo.
    let gateway_admission = Arc::new(bcs_dist_worker::WorkerTaskAdmission::with_rounds(
        inbox.clone(),
        rounds.clone(),
        coordination.clone(),
        Arc::new(now_ms),
    ).with_node_history(node_history.clone(), archive.clone()).map_err(assembly_err)?);
    let processor = Arc::new(
        bcs_dist_worker::WorkerProcessor::new(
            runtime.clone(),
            bcs_dist_worker::ProcessorPorts {
                scan_budget: scan_budget.clone(),
                control: coordination.clone(),
                admission: Arc::new(bcs_dist_store::inbox::StoreAdmissionControl::new(
                    (*inbox).clone(),
                    feed.clone(),
                )),
                // The real strict-FSM reducer (plan T5): decisions, not a
                // script.
                runner: Arc::new(bcs_dist_runner_fsm::FsmReducerRunner::d0()),
                projection: Arc::new(OutputProjectionService),
                directory: host.directory.clone(),
                tasks: feed.clone(),
                output: feed.clone(),
                blobs: archive.clone(),
                results: archive.clone(),
                dispatch: dispatch.clone(),
                rounds: Some(rounds.clone()),
                archives: Some(artifacts.clone()),
                now: Arc::new(now_ms),
            },
            bcs_dist_worker::ProcessorConfig::default(),
        )
        .map_err(assembly_err)?
        .with_node_history(node_history.clone()),
    );
    let supervisor = if config.supervisor_enabled {
        Some(Arc::new(tokio::sync::Mutex::new(
            bcs_dist_worker::supervisor::DispatchSupervisor::new(
                bcs_dist_worker::supervisor::SupervisorPorts {
                    dispatch: dispatch.clone(),
                    sessions: coordination.clone(),
                    inbox: inbox.clone(),
                    membership: coordination.clone(),
                    scan_budget: scan_budget.clone(),
                    now: Arc::new(now_ms),
                },
                    bcs_dist_worker::supervisor::SupervisorConfig {
                        shards: shards.clone(),
                        ..bcs_dist_worker::supervisor::SupervisorConfig::default()
                    },
                )
                .map_err(assembly_err)?,
            ),
        ))
    } else {
        None
    };
    // The Connector: real engine over the queue, the inbox, the injected
    // downstream transport and the verification-lane backoff ceiling from
    // the config (T16 residual seam: wired here, bounded by validation).
    let connector_instance = Instance {
        node: format!("{namespace}-connector"),
        incarnation: format!("{namespace}-connector-1"),
    };
    let mut connector_config =
        bcs_dist_connector::config::ConnectorConfig::new(
            connector_instance.clone(),
            PoolId(namespace.to_string()),
        );
    connector_config.verification_backoff_max =
        Duration::from_millis(config.verification_backoff_max_ms);
    // Membership registrations must be reachable to be accepted: an
    // http(s) control address is a hard requirement of the lease ladder.
    connector_config.control_address =
        format!("http://{namespace}-connector.example.invalid");
    let connector = bcs_dist_connector::ConnectorEngine::new(
        connector_config,
        bcs_dist_connector::config::ConnectorPorts {
            queue: queue.clone(),
            inbox: inbox.clone(),
            agent: host.downstream.clone(),
            membership: coordination.clone(),
            blobs: archive.clone(),
            node_history: Some(node_history.clone()),
            rules: Arc::new(bcs_dist_connector::rules::DistConnectorRules),
        },
    )
    .map_err(|e| assembly_err(e.code))?;
    // The public application surface (T8): the DistGateway service family
    // plus the read-only Card discovery, over the host's directory and D0
    // policy.
    let core = bcs_dist_gateway::DistGatewayCore::new(
        gateway_admission.clone(),
        Arc::new(bcs_dist_worker::WorkerOutputReader::new(
            reader.clone(),
            archive.clone(),
            Arc::new(bcs_dist_a2a_output::A2aOutputAdapter),
        )),
        coordination.clone(),
    );
    // The gateway's derived session shard value is the assembly's single
    // mounted shard: the group entrance pins its session onto it (the strict
    // round admission binds exactly this shard's license), and the legacy
    // spread modulo collapses onto the same one-shard world (plan T9: one
    // assembly per shard).
    let mut gateway_config = bcs_app_dist_gateway::DistGatewayConfig::new(
        shards[0].0,
    );
    gateway_config.grants_recheck = Duration::from_secs(1);
    let gateway = Arc::new(
        bcs_app_dist_gateway::DistGateway::new(
            Arc::new(core),
            host.directory.clone(),
            host.policy.clone(),
            reader.clone(),
            Arc::new(bcs_app_dist_gateway::SystemClock),
            gateway_config,
        )
        .map_err(assembly_err)?,
    );
    let discovery = Arc::new(bcs_app_dist_gateway::GroupDiscovery::new(
        host.directory.clone(),
        host.policy.clone(),
        bcs_app_dist_gateway::GroupDiscoveryConfig::default(),
    ));
    let http_config =
        bcs_dist_gateway_http::GatewayHttpConfig::new(config.public_url.clone());
    let router = bcs_dist_gateway_http::router(gateway.clone(), discovery, http_config);
    Ok(GroupA2aAssembly {
        roles: GroupA2aRoles {
            coordination,
            scan_budget,
            inbox,
            feed,
            dispatch,
            queue,
            archive,
            node_history,
            artifacts,
            rounds,
            rounds_binding,
            gateway_admission,
            runtime,
            processor,
            supervisor,
            connector,
            reader,
            gateway,
            directory: host.directory,
            policy: host.policy,
            downstream: host.downstream,
            shards,
            layout,
        },
        router,
    })
}

fn now_ms() -> TimestampMillis {
    SystemClock.now()
}

/// The joint admission binding that follows shard-lease rotation
/// (plan T9: "owner 交接后接续扫描"). `StoreGroupRoundAdmission` pins the
/// shard claim of its construction and the checkpoint acquire fences
/// against the CURRENT holder, so when the lease ladder rotates — a
/// successor Worker claims with a fresh epoch after a crash — the
/// pinned binding fences (`strict_session_fenced`). This wrapper owns
/// the binding: it rebuilds the underlying admission against the
/// current shard record and retries the operation once. All durable
/// state (request rows, licenses, checkpoints, cold proofs) is shared
/// and untouched — only the in-process binding moves.
pub struct RebindingRoundAdmission {
    coordination: Arc<CoordinationStore>,
    /// The one shard this binding follows: `[group_a2a.shards[0]]` (the
    /// assembly validates the single-shard scope up front, so this is
    /// captured once and never guessed).
    shard: ShardId,
    /// Worker incarnations of THIS host that may hold the shard lease. A
    /// record held by anyone else fences the admit out — this host never
    /// rides a foreign epoch.
    leasers: std::sync::Mutex<std::collections::BTreeSet<String>>,
    state: tokio::sync::RwLock<Arc<StoreGroupRoundAdmission>>,
    rebuild:
        Box<dyn Fn(ShardClaim) -> Result<StoreGroupRoundAdmission, GroupA2aWiringError> + Send + Sync>,
}



impl RebindingRoundAdmission {
    /// Registers one more Worker incarnation of this host as a legal shard
    /// lessee (a successor spawning after a crash of the previous holder).
    pub fn register_leaser(&self, instance: &bcs_dist_model::Instance) {
        self.leasers
            .lock()
            .expect("rebind leasers")
            .insert(instance.incarnation.clone());
    }
    async fn rebind(&self) -> Result<(), StoreError> {
        let mut state = self.state.write().await;
        let record = self
            .coordination
            .current(self.shard)
            .await
            .map_err(|e| StoreError::NotApplied { code: e.to_string() })?
            .ok_or_else(|| StoreError::NotApplied { code: "shard_unclaimed".into() })?;
        // Holder verification: the binding only follows a lease this host
        // actually holds. A claim by an unregistered holder (a foreign
        // process or a misconfigured duplicate) fences the admit out — the
        // caller sees the original `strict_session_fenced` error and
        // never writes round state under someone else's epoch.
        let ours = self
            .leasers
            .lock()
            .expect("rebind leasers")
            .contains(&record.claim.owner.instance.incarnation);
        if !ours {
            return Err(StoreError::NotApplied {
                code: "strict_session_fenced".into(),
            });
        }
        let rebuilt = (self.rebuild)(record.claim)
            .map_err(|e| StoreError::NotApplied { code: e.to_string() })?;
        *state = Arc::new(rebuilt);
        Ok(())
    }
}

use bcs_dist_model::{Admission, CommitSeq, RoundKey, TerminalProof};
use bcs_service_api::port::repo::distributed::group_round::{
    GroupRequestIdentity, GroupRequestRecord, GroupRoundRepo, GroupRoundView, GroupStartRequest,
};



fn is_fenced(error: &StoreError) -> bool {
    matches!(
        error,
        StoreError::NotApplied { code } if code == "strict_session_fenced"
    )
}


#[async_trait::async_trait]
impl GroupRoundRepo for RebindingRoundAdmission {
    async fn lookup(&self, identity: &GroupRequestIdentity<'_>) -> Result<GroupRequestRecord, StoreError> {
        let outer = self.state.read().await.clone();
        match outer.lookup(identity).await {
            Err(error) if is_fenced(&error) => {
                self.rebind().await?;
                self.state.read().await.clone().lookup(identity).await
            }
            other => other,
        }
    }
    async fn admit(&self, request: &GroupStartRequest) -> Result<Admission, StoreError> {
        let outer = self.state.read().await.clone();
        match outer.admit(request).await {
            Err(error) if is_fenced(&error) => {
                self.rebind().await?;
                self.state.read().await.clone().admit(request).await
            }
            other => other,
        }
    }
    async fn get(&self, key: &RoundKey) -> Result<Option<GroupRoundView>, StoreError> {
        let outer = self.state.read().await.clone();
        match outer.get(key).await {
            Err(error) if is_fenced(&error) => {
                self.rebind().await?;
                self.state.read().await.clone().get(key).await
            }
            other => other,
        }
    }
    async fn commit_terminal(
        &self,
        round: &RoundKey,
        proof: &TerminalProof,
    ) -> Result<CommitSeq, StoreError> {
        let outer = self.state.read().await.clone();
        match outer.commit_terminal(round, proof).await {
            Err(error) if is_fenced(&error) => {
                self.rebind().await?;
                self.state.read().await.clone().commit_terminal(round, proof).await
            }
            other => other,
        }
    }
}

/// One heartbeat's report: the supervisor pass, the reclaim batch bound,
/// and the worker tick's error codes. Failures surface here instead of
/// aborting the drive loop.
#[derive(Debug, Default)]
pub struct GroupTickReport {
    pub supervisor: Option<bcs_dist_worker::supervisor::SupervisorReport>,
    pub reclaimed: usize,
    pub worker: Vec<String>,
}

impl GroupA2aRoles {
    /// One bounded host heartbeat of the SHARED joint machinery: inbox
    /// repair/discovery per shard, one restricted dispatch-directory
    /// reclaim batch per shard, and the Dispatch Supervisor pass over the
    /// shards. Worker heartbeat/runtime/processor ticks belong to the live
    /// Worker incarnations (this assembly's original plus any successors
    /// the host registers); the host drives those alongside this tick.
    /// The assembly spawns nothing — the host owns pacing so crash
    /// vectors can stop drives deterministically. The dead-Connector
    /// wake needs no host rescue here: the supervisor's own directory
    /// scan re-hints every open (held) dispatch each pass, and a live
    /// successor Connector takes the call once membership answers Dead
    /// (the delayed.rs contract note on the dev stream).
    pub async fn joint_tick(&self) -> GroupTickReport {
        let mut report = GroupTickReport::default();
        for shard in &self.shards {
            let _ = self.inbox.repair(*shard).await;
            let _ = self.inbox.discover(*shard).await;
            // The bounded reclaim batch of ended dispatch entries (T7's
            // stable-cursor reclaim): one restricted batch per shard tick,
            // driven only while the shard record exists. Retention of
            // cut-snapshot documents stays RECORD-ONLY (policy unsettled).
            match self.dispatch.reclaim_dispatch_directory(*shard).await {
                Ok(count) => report.reclaimed += count,
                Err(_) => report.worker.push("reclaim_unavailable".to_string()),
            }
        }
        if let Some(supervisor) = &self.supervisor {
            let mut supervisor = supervisor.lock().await;
            report.supervisor = Some(supervisor.tick().await);
        }
        report
    }

    /// Registers a successor Worker incarnation of this host: it becomes a
    /// legal shard lessee whose claim the round binding will follow.
    pub fn register_worker_leaser(&self, instance: &bcs_dist_model::Instance) {
        self.rounds_binding.register_leaser(instance);
    }
}

fn empty_checkpoint() -> bcs_service_api::port::repo::distributed::session::Checkpoint {
    bcs_service_api::port::repo::distributed::session::Checkpoint {
        format_version: 1,
        commit_seq: bcs_dist_model::CommitSeq(0),
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

async fn sqlite_at(
    dir: &PathBuf,
    file: &str,
) -> Result<Arc<dyn bcs_db_api::DbPlugin>, GroupA2aWiringError> {
    let path = dir.join(file);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| assembly_err(format!("archive_dir_create:{e}")))?;
    }
    let db = Arc::new(
        bcs_db_local::LocalSqliteDbPlugin::new_file(&path)
            .map_err(|e| assembly_err(format!("sqlite_open:{e}")))?,
    );
    bcs_db_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .map_err(|e| assembly_err(format!("sqlite_migrate:{e}")))?;
    Ok(db)
}

/// The strict round admission must share the runtime's own shard claim or
/// every CAS would fence. Settling the lease ladder is real wall-clock
/// time, so the poll sleeps in bounded real intervals.
async fn shard_claim(
    coordination: &CoordinationStore,
    runtime: &bcs_dist_worker::WorkerRuntime,
    shard: &ShardId,
) -> Result<bcs_service_api::port::repo::distributed::ownership::ShardClaim, GroupA2aWiringError> {
    for _ in 0..400 {
        if let Some(record) = coordination.current(*shard).await.map_err(assembly_err)? {
            return Ok(record.claim);
        }
        runtime
            .tick()
            .await
            .map_err(|e| assembly_err(format!("worker_tick:{e}")))?;
        std::thread::sleep(Duration::from_millis(50));
    }
    Err(GroupA2aWiringError::Startup(
        "worker runtime did not claim its shard; the strict round admission \
         cannot share a claim that does not exist",
    ))
}
