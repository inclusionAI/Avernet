pub use crate::types::dist_runtime::{DrainOutcome, RuntimeError, RuntimeSnapshot};
use async_trait::async_trait;
use bcs_dist_model::{Instance,RequestId,ShardId};
pub use crate::port::repo::distributed::ownership::{HandoffRequest,HandoffOutcome,ShardClaim};
use std::time::Duration;

/// No business submission API; inputs arrive through SessionInbox.
#[async_trait]
pub trait DistWorkerRuntimeService: Send + Sync {
    /// authenticated_source must come from service authentication, not the JSON
    /// request body. The application checks it against the precise source claim.
    async fn accept_handoff(&self, _authenticated_source: Instance, _request: HandoffRequest) -> Result<HandoffOutcome, RuntimeError> {
        Err(RuntimeError { code: "worker_handoff_unsupported".into() })
    }
    async fn handoff_done(&self, _authenticated_source: Instance, _target: ShardClaim, _request_id: RequestId) -> Result<bool, RuntimeError> {
        Err(RuntimeError { code: "worker_handoff_unsupported".into() })
    }
    async fn wake(&self, _authenticated_source: Instance, _shard: ShardId) -> Result<(), RuntimeError> {
        Err(RuntimeError { code: "worker_wake_unsupported".into() })
    }
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError>;
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError>;
}

/// No business submission API; calls arrive through DispatchBoard.
#[async_trait]
pub trait DistConnectorRuntimeService: Send + Sync {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError>;
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError>;
}
