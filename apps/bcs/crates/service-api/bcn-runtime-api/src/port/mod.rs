//! Outbound business ports. Concrete clients/repositories are selected only in bootstrap/tests.
use std::pin::Pin;
use async_trait::async_trait;
use futures::Stream;
use bcn_runtime_model::*;
pub mod repo;

pub type AgentEventStream = Pin<Box<dyn Stream<Item = ConnectorResult<AgentEvent>> + Send + 'static>>;
/// One instance binds one frozen target. Capabilities describe that target, not its protocol.
/// Implementations must reject request/handle target or binding-version mismatches before I/O.
#[async_trait]
pub trait AgentConnector: Send + Sync {
    fn capabilities(&self) -> ConnectorCapabilities;
    async fn send_message(&self, _request: AgentSendRequest) -> ConnectorResult<AgentSendResponse> {
        Err(ConnectorError::Unsupported("send_message".into()))
    }
    async fn send_streaming_message(&self, _request: AgentSendRequest) -> ConnectorResult<AgentEventStream> {
        Err(ConnectorError::Unsupported("send_streaming_message".into()))
    }
    async fn get_task(&self, _handle: ExecutionHandle) -> ConnectorResult<ExecutionSnapshot> {
        Err(ConnectorError::Unsupported("get_task".into()))
    }
    async fn subscribe_to_task(&self, _handle: ExecutionHandle) -> ConnectorResult<AgentEventStream> {
        Err(ConnectorError::Unsupported("subscribe_to_task".into()))
    }
    async fn cancel_task(&self, _request: AgentCancelRequest) -> ConnectorResult<CancelOutcome> {
        Err(ConnectorError::Unsupported("cancel_task".into()))
    }
}
#[async_trait]
pub trait AgentCatalog: Send + Sync {
    async fn resolve(&self, ctx: &CallerContext, target: &ExecutionTarget, session_id: &SessionId) -> RuntimeResult<SessionSpec>;
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AccessAction { Submit, Get, List, Subscribe, Cancel, HumanReply }
/// Cross-cutting access hook registered by bootstrap; every applicable hook must allow.
#[async_trait]
pub trait AccessPolicy: Send + Sync {
    async fn authorize(&self, ctx: &CallerContext, action: AccessAction, target: &ExecutionTarget,
        session_id: Option<&SessionId>, task_id: Option<&TaskId>) -> RuntimeResult<()>;
}
/// Ephemeral secret intentionally has no Serialize or Clone implementation.
pub struct ResolvedCredential(Vec<u8>);
impl ResolvedCredential {
    pub fn new(bytes: Vec<u8>) -> Self { Self(bytes) }
    pub fn expose(&self) -> &[u8] { &self.0 }
}
impl std::fmt::Debug for ResolvedCredential {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.write_str("ResolvedCredential([REDACTED])") }
}
#[async_trait]
pub trait CredentialResolver: Send + Sync {
    async fn resolve(&self, scope: &Scope, reference: &CredentialRef) -> RuntimeResult<ResolvedCredential>;
}
#[async_trait]
pub trait JudgeExecutor: Send + Sync {
    /// The selected branch must be persisted as SessionInput before reduction.
    async fn execute(&self, request: JudgeRequest) -> RuntimeResult<String>;
}
pub trait Clock: Send + Sync { fn now_ms(&self) -> u64; }

/// Business-facing failure discovery, adapted from infrastructure at composition time.
/// Unknown never authorizes takeover; TTL does not revoke ownership.
#[async_trait]
pub trait RuntimeLiveness: Send + Sync {
    async fn register(&self, node: NodeIdentity, ttl_ms: u64) -> RuntimeResult<()>;
    async fn renew(&self, node: &NodeIdentity, ttl_ms: u64) -> RuntimeResult<()>;
    async fn inspect_batch(&self, nodes: &[NodeIdentity]) -> RuntimeResult<Vec<NodeObservation>>;
    async fn mark_draining(&self, node: &NodeIdentity) -> RuntimeResult<()>;
}
