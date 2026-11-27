//! Distributed Direct A2A composition for hosts providing the shared stores.
//! Mount the Gateway router behind the host's authenticated GatewayCaller layer.
use crate::dist_agent_registry::RegisteredAgentResolver;
use bcs_a2a_http::{dist_agent::A2aDistAgentClient, grpc_mesh::GrpcMeshConfig};
use bcs_app_dist_gateway::{DistGateway, DistGatewayConfig};
use bcs_direct_a2a_runner::DirectA2aRunner;
use bcs_dist_connector::{
    ConnectorConfig, ConnectorEngine, ConnectorPorts, rules::DistConnectorRules,
};
use bcs_dist_connector_store::StoreDispatchQueue;
use bcs_dist_gateway::DistGatewayCore;
use bcs_dist_gateway_http::GatewayHttpConfig;
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::{
    archive::DbArchiveStore,
    budget::SharedScanBudget,
    coordination::CoordinationStore,
    dispatch::StoreDispatch,
    inbox::{Clock, StoreAdmissionControl, StoreInboxProducer, SystemClock},
    task_feed::StoreTaskFeed,
};
use bcs_dist_worker::supervisor::{DispatchSupervisor, SupervisorConfig, SupervisorPorts};
use bcs_dist_worker::{
    ProcessorConfig, ProcessorPorts, RunnerRegistry, WorkerConfig, WorkerOutputReader,
    WorkerProcessor, WorkerRuntime, WorkerTaskAdmission,
};
use bcs_message_flow::a2a_chat::DirectSessionDirectory;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{
    BotRegistryCoreService,
    application::dist_gateway::GatewayError,
    port::dist_agent::DistSessionAuthorizerPort,
    port::dist_runtime::DistWorkerPeerPort,
    port::repo::distributed::{budget::ScanBudgetPort, session::Checkpoint},
    types::dist_runtime::RuntimeError,
};
use std::sync::Arc;

/// Construct these providers over the same cluster/namespace and shared SQL archive.
pub struct DirectA2aStores {
    pub coordination: Arc<CoordinationStore>,
    pub inbox: Arc<StoreInboxProducer>,
    pub feed: Arc<StoreTaskFeed>,
    pub archive: Arc<DbArchiveStore>,
}

impl DirectA2aStores {
    /// The Direct directory checks targets and builds configuration. The host
    /// must supply its authorization interface; no policy is installed here.
    pub fn gateway(
        &self,
        authorizer: Arc<dyn DistSessionAuthorizerPort>,
        tenant: TenantId,
        pool: PoolId,
        limits: SessionLimits,
        config: DistGatewayConfig,
        http: GatewayHttpConfig,
    ) -> Result<axum::Router, GatewayError> {
        let admission =
            WorkerTaskAdmission::new(self.inbox.clone(), Arc::new(|| SystemClock.now()));
        let output = WorkerOutputReader::new(
            self.feed.clone(),
            self.archive.clone(),
            Arc::new(bcs_dist_a2a_output::A2aOutputAdapter),
        );
        let core = DistGatewayCore::new(
            Arc::new(admission),
            Arc::new(output),
            self.coordination.clone(),
        );
        let app = DistGateway::new(
            Arc::new(core),
            Arc::new(DirectSessionDirectory::new(authorizer, tenant, pool, limits)),
            Arc::new(bcs_app_dist_gateway::SystemClock),
            config,
        )?;
        Ok(bcs_dist_gateway_http::router(Arc::new(app), http))
    }

    pub async fn worker(
        &self,
        node: String,
        control_address: String,
        config: WorkerConfig,
        processor: ProcessorConfig,
        make_peers: impl FnOnce(&Instance) -> Result<Arc<dyn DistWorkerPeerPort>, RuntimeError>,
        budget: Arc<SharedScanBudget>,
        dispatch: Arc<StoreDispatch>,
    ) -> Result<(Arc<WorkerRuntime>, WorkerProcessor, DispatchSupervisor), RuntimeError> {
        let supervisor = DispatchSupervisor::new(
            SupervisorPorts {
                dispatch: dispatch.clone(),
                sessions: self.coordination.clone(),
                inbox: self.inbox.clone(),
                membership: self.coordination.clone(),
                scan_budget: budget.clone(),
                now: Arc::new(|| SystemClock.now()),
            },
            SupervisorConfig {
                shards: config.shards.clone(),
                concurrency: budget.profile().io_slots,
                ..Default::default()
            },
        )
        .map_err(|e| RuntimeError {
            code: e.to_string(),
        })?;
        let runtime = WorkerRuntime::start_with_peer_factory(
            node,
            control_address,
            config,
            self.coordination.clone(),
            self.coordination.clone(),
            self.coordination.clone(),
            self.inbox.clone(),
            self.inbox.clone(),
            make_peers,
            Arc::new(|_| Ok(Checkpoint::empty())),
        )
        .await?;
        let ports = ProcessorPorts {
            runner: Arc::new(RunnerRegistry::new(DirectA2aRunner)),
            projection: Arc::new(OutputProjectionService),
            control: self.coordination.clone(),
            scan_budget: budget,
            admission: Arc::new(StoreAdmissionControl::new(
                (*self.inbox).clone(),
                self.feed.clone(),
            )),
            tasks: self.feed.clone(),
            output: self.feed.clone(),
            blobs: self.archive.clone(),
            results: self.archive.clone(),
            dispatch,
            now: Arc::new(|| SystemClock.now()),
        };
        let processor = WorkerProcessor::new(runtime.clone(), ports, processor)?;
        Ok((runtime, processor, supervisor))
    }

    pub fn connector(
        &self,
        config: ConnectorConfig,
        queue: Arc<StoreDispatchQueue>,
        registry: Arc<dyn BotRegistryCoreService>,
        guard: OutboundUrlGuard,
        grpc: Option<GrpcMeshConfig>,
    ) -> Result<ConnectorEngine, RuntimeError> {
        let client = A2aDistAgentClient::new(
            Arc::new(RegisteredAgentResolver::new(registry)),
            guard,
            grpc,
        );
        ConnectorEngine::new(
            config,
            ConnectorPorts {
                queue,
                agent: Arc::new(client),
                membership: self.coordination.clone(),
                inbox: self.inbox.clone(),
                blobs: self.archive.clone(),
                rules: Arc::new(DistConnectorRules),
            },
        )
    }
}
