//! Reusable composition adapter; the application never imports infrastructure APIs.
use async_trait::async_trait;
use bcn_runtime_api::port::RuntimeLiveness;
use bcn_runtime_model::*;
use bcn_runtime_storage_api::NodeLivenessPort;
use std::sync::Arc;
pub struct LivenessAdapter {
    inner: Arc<dyn NodeLivenessPort>,
}
impl LivenessAdapter {
    pub fn new(inner: Arc<dyn NodeLivenessPort>) -> Self {
        Self { inner }
    }
}
fn error(e: StorageError) -> RuntimeError {
    RuntimeError::Unavailable(e.to_string())
}
#[async_trait]
impl RuntimeLiveness for LivenessAdapter {
    async fn register(&self, node: NodeIdentity, ttl_ms: u64) -> RuntimeResult<()> {
        self.inner.register(node, ttl_ms).await.map_err(error)
    }
    async fn renew(&self, node: &NodeIdentity, ttl_ms: u64) -> RuntimeResult<()> {
        self.inner.renew(node, ttl_ms).await.map_err(error)
    }
    async fn inspect_batch(&self, nodes: &[NodeIdentity]) -> RuntimeResult<Vec<NodeObservation>> {
        self.inner.inspect_batch(nodes).await.map_err(error)
    }
    async fn mark_draining(&self, node: &NodeIdentity) -> RuntimeResult<()> {
        self.inner.mark_draining(node).await.map_err(error)
    }
}
