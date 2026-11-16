//! Protocol-independent application orchestration. Wiring belongs to bootstrap/tests.
use bcn_runtime_api::{
    core::SessionRunner,
    port::{repo::*, *},
};
use bcn_runtime_model::*;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::sync::Arc;
mod connector;
mod gateway;
mod projection;
mod recovery;
mod results;
mod timers;
mod worker;
pub use connector::DefaultConnector;
pub use gateway::DefaultGateway;
pub use worker::DefaultWorker;
/// Injected service contracts only; no selection of a backend or transport occurs here.
#[derive(Clone)]
pub struct RuntimePorts {
    pub sessions: Arc<dyn SessionStore>,
    pub ownership: Arc<dyn OwnershipPort>,
    pub admissions: Arc<dyn AdmissionStore>,
    pub inbox: Arc<dyn InboxPort>,
    pub commands: Arc<dyn CommandBus>,
    pub dispatches: Arc<dyn DispatchStore>,
    pub events: Arc<dyn TaskEventStore>,
    pub queries: Arc<dyn TaskQueryStore>,
    pub results: Arc<dyn ResultStore>,
    pub liveness: Arc<dyn RuntimeLiveness>,
}
#[derive(Debug, Default)]
pub struct TickReport {
    pub progressed: u32,
    pub errors: Vec<RuntimeError>,
}
pub const MAX_CONTENT_BYTES: usize = 65536;
pub const MAX_SPEC_BYTES: usize = 32768;
pub(crate) fn hash<T: Serialize>(purpose: &str, value: &T) -> RuntimeResult<String> {
    let bytes = serde_json::to_vec(&(purpose, value))
        .map_err(|e| RuntimeError::InvalidInput(e.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}
pub(crate) fn size<T: Serialize>(value: &T) -> RuntimeResult<usize> {
    Ok(serde_json::to_vec(value)
        .map_err(|e| RuntimeError::InvalidInput(e.to_string()))?
        .len())
}
pub(crate) fn runner(
    runners: &[Arc<dyn SessionRunner>],
    kind: RunnerKind,
) -> RuntimeResult<&dyn SessionRunner> {
    runners
        .iter()
        .find(|r| r.kind() == kind)
        .map(|r| r.as_ref())
        .ok_or_else(|| RuntimeError::Unsupported("runner not registered".into()))
}
pub(crate) fn target_scope(target: &ExecutionTarget) -> &Scope {
    match target {
        ExecutionTarget::Agent { agent_id } => &agent_id.scope,
        ExecutionTarget::Group { group_id } => &group_id.scope,
    }
}
pub(crate) fn validate_node(node: &NodeIdentity) -> RuntimeResult<()> {
    node.scope
        .validate()
        .map_err(|e| RuntimeError::InvalidInput(e.to_string()))?;
    if node.node_id.is_empty() || node.incarnation.is_empty() {
        return Err(RuntimeError::InvalidInput(
            "node incarnation required".into(),
        ));
    }
    Ok(())
}
