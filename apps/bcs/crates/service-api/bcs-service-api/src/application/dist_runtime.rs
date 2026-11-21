pub use crate::port::repo::distributed::ownership::{HandoffOutcome, HandoffRequest, ShardClaim};
pub use crate::types::dist_runtime::{DrainOutcome, RuntimeError, RuntimeSnapshot};
use async_trait::async_trait;
use bcs_dist_model::{Instance, RequestId, ShardId};
use std::time::Duration;

/// No business submission API; inputs arrive through SessionInbox.
#[async_trait]
pub trait DistWorkerRuntimeService: Send + Sync {
    /// The host binds the source via instance authentication or a channel
    /// restricted to mutually trusted Workers. Do not derive it from the JSON
    /// request body. The application checks it against the precise source claim.
    async fn accept_handoff(
        &self,
        _authenticated_source: Instance,
        _request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        Err(RuntimeError {
            code: "worker_handoff_unsupported".into(),
        })
    }
    async fn handoff_done(
        &self,
        _authenticated_source: Instance,
        _target: ShardClaim,
        _request_id: RequestId,
    ) -> Result<bool, RuntimeError> {
        Err(RuntimeError {
            code: "worker_handoff_unsupported".into(),
        })
    }
    async fn wake(
        &self,
        _authenticated_source: Instance,
        _shard: ShardId,
    ) -> Result<(), RuntimeError> {
        Err(RuntimeError {
            code: "worker_wake_unsupported".into(),
        })
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

/// Administrative isolation query and conditional recovery; no business inputs.
/// The first scope is authenticated actor identity, not a request body field.
#[async_trait]
pub trait DistWorkerIsolationService: Send + Sync {
    async fn isolation(
        &self,
        authenticated_actor: bcs_dist_model::TaskScope,
        target: bcs_dist_model::TaskScope,
        sid: bcs_dist_model::Sid,
    ) -> Result<Option<crate::port::repo::distributed::control::ControlSnapshot>, RuntimeError>;
    async fn resume(
        &self,
        authenticated_actor: bcs_dist_model::TaskScope,
        target: bcs_dist_model::TaskScope,
        request: crate::port::repo::distributed::control::RecoveryAuthorization,
    ) -> Result<crate::port::repo::distributed::control::ControlOutcome, RuntimeError>;
}
