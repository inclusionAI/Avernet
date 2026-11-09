//! Shared deployment scope/ports, distinct Worker and Connector incarnations.
use crate::{RuntimeConfig,StorageSelection};
use bcn_runtime_app::{DefaultGateway,DefaultWorker,DefaultConnector,RuntimePorts};
use bcn_runtime_api::{port::*,core::SessionRunner,application::*};
use bcn_runtime_model::*;
use bcn_runtime_storage_api::ServerClock;
use bcn_runtime_store::{RuntimeStore,LocalResultStore,LivenessAdapter};
use bcn_session_runner::{DirectA2aRunner,StructuredGroupRunner};
use bcn_storage_memory::MemoryAtomicStore;
use async_trait::async_trait;
use std::{sync::Arc,time::{SystemTime,UNIX_EPOCH}};
pub struct Components {
    pub catalog: Arc<dyn AgentCatalog>,
    pub policies: Vec<Arc<dyn AccessPolicy>>,
    pub judge: Arc<dyn JudgeExecutor>,
    pub connectors: Vec<(FrozenTargetBinding,Arc<dyn AgentConnector>)>,
}
pub struct Runtime {
    pub gateway: Arc<DefaultGateway>,
    pub worker: Arc<DefaultWorker>,
    pub connector: Arc<DefaultConnector>,
}
impl Runtime {
    pub async fn run(&self,shutdown:CancellationReceiver)->RuntimeResult<()> {
        tokio::try_join!(self.worker.run(shutdown.clone()),self.connector.run(shutdown))?;
        Ok(())
    }
}
pub struct SystemClock;
impl Clock for SystemClock { fn now_ms(&self)->u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis().min(u64::MAX as u128) as u64 } }
impl ServerClock for SystemClock { fn now_ms(&self)->u64 { Clock::now_ms(self) } }
/// Explicitly selected single-process memory store. Never called for production.
pub fn memory_ports(config:&RuntimeConfig)->RuntimeResult<RuntimePorts> {
    config.validate()?;
    if config.storage!=StorageSelection::Memory || config.profile==RuntimeProfile::Production { return Err(RuntimeError::InvalidInput("memory requires explicit local/test selection".into())); }
    let atomic=Arc::new(MemoryAtomicStore::new(config.profile,Arc::new(SystemClock)).map_err(|e|RuntimeError::Storage(e.to_string()))?);
    let repo=Arc::new(RuntimeStore::new(atomic));
    let results=Arc::new(LocalResultStore::new((*repo).clone(),repo.clone(),config.profile)?);
    Ok(RuntimePorts{sessions:repo.clone(),ownership:repo.clone(),admissions:repo.clone(),inbox:repo.clone(),commands:repo.clone(),dispatches:repo.clone(),events:repo.clone(),queries:repo.clone(),results,liveness:Arc::new(LivenessAdapter::new(repo))})
}
struct CatalogGate { inner:Arc<dyn AgentCatalog>,scope:Scope,allowlist:Vec<ExecutionTarget> }
#[async_trait]
impl AgentCatalog for CatalogGate {
    async fn resolve(&self,caller:&CallerContext,target:&ExecutionTarget,session:&SessionId)->RuntimeResult<SessionSpec> {
        if caller.scope!=self.scope || session.scope!=self.scope || !self.allowlist.contains(target) { return Err(RuntimeError::AccessDenied); }
        let mut spec=self.inner.resolve(caller,target,session).await?;
        if spec.kind==RunnerKind::StructuredGroup {
            let definition=spec.definition.as_ref().ok_or_else(||RuntimeError::InvalidInput("group definition required".into()))?;
            spec.live_output_nodes=Some(definition.nodes.iter().filter(|(_,node)|node.final_output).map(|(id,_)|id.clone()).collect());
        }
        let defaults=RuntimeDefaults::default();
        if spec.policy.max_pending_tasks!=defaults.session_pending_tasks || spec.policy.max_graph_nodes!=defaults.graph_nodes || spec.policy.max_agents!=defaults.graph_agents {
            return Err(RuntimeError::InvalidInput("catalog overrides fixed MVP limits".into()));
        }
        Ok(spec)
    }
}
pub fn build(config:RuntimeConfig,components:Components,ports:Option<RuntimePorts>)->RuntimeResult<Runtime> {
    build_with_observer(config, components, ports, None)
}
/// Optional bounded diagnostics, selected only at the composition root.
pub fn build_with_observer(config:RuntimeConfig,components:Components,ports:Option<RuntimePorts>,observer:Option<Arc<dyn RuntimeObserverPort>>)->RuntimeResult<Runtime> {
    config.validate()?;
    let ports=match(config.storage,ports){
        (StorageSelection::Memory,None)=>memory_ports(&config)?,
        (StorageSelection::External,Some(ports))=>ports,
        _=>return Err(RuntimeError::InvalidInput("storage selection and supplied ports disagree".into())),
    };
    let clock=Arc::new(SystemClock);
    let runners:Vec<Arc<dyn SessionRunner>>=vec![Arc::new(DirectA2aRunner),Arc::new(StructuredGroupRunner)];
    let catalog=Arc::new(CatalogGate{inner:components.catalog,scope:config.scope.clone(),allowlist:config.allowlist});
    let identity=|role:&str|NodeIdentity{scope:config.scope.clone(),node_id:role.into(),incarnation:uuid::Uuid::new_v4().to_string()};
    let mut worker=DefaultWorker::new(identity("worker"),ports.clone(),runners.clone(),components.judge,clock.clone())?;
    if let Some(observer)=observer {worker=worker.with_observer(observer);}
    Ok(Runtime{
        gateway:Arc::new(DefaultGateway::new(ports.clone(),runners.clone(),catalog,components.policies,clock.clone(),config.enabled)?),
        worker:Arc::new(worker),
        connector:Arc::new(DefaultConnector::new(identity("connector"),ports,components.connectors,clock)?),
    })
}

/// Assemble repositories once over shared external storage. Each PROCESS supplies
/// its own dedicated liveness pool; ResultStore receives the admission query port.
pub fn external_ports(
    atomic:Arc<dyn bcn_runtime_storage_api::AtomicStore>,
    liveness:Arc<dyn bcn_runtime_storage_api::NodeLivenessPort>,
    results:impl FnOnce(Arc<dyn repo::TaskQueryStore>)->Arc<dyn repo::ResultStore>,
)->RuntimePorts {
    let repo=Arc::new(RuntimeStore::new(atomic));
    let results=results(repo.clone());
    RuntimePorts{sessions:repo.clone(),ownership:repo.clone(),admissions:repo.clone(),inbox:repo.clone(),commands:repo.clone(),dispatches:repo.clone(),events:repo.clone(),queries:repo,results,liveness:Arc::new(LivenessAdapter::new(liveness))}
}
