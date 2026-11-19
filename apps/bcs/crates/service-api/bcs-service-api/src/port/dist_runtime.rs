//! Engine control consumed by the application admin facade.
use crate::types::dist_runtime::{DrainOutcome, RuntimeError, RuntimeSnapshot};
use async_trait::async_trait;
use bcs_dist_model::{Instance,RequestId,ShardId};
use crate::port::repo::distributed::ownership::{HandoffRequest,HandoffOutcome,MemberRecord,ShardClaim};
use std::time::Duration;

/// The engine implements this port without importing application-layer contracts.
#[async_trait]
pub trait DistRuntimeControlPort: Send + Sync {
    /// Called only after the application has bound authenticated peer identity.
    async fn accept_handoff(&self, _request: HandoffRequest) -> Result<HandoffOutcome, RuntimeError> {
        Err(RuntimeError { code: "worker_handoff_unsupported".into() })
    }
    async fn handoff_done(&self, _source: Instance, _target: ShardClaim, _request_id: RequestId) -> Result<bool, RuntimeError> {
        Err(RuntimeError { code: "worker_handoff_unsupported".into() })
    }
    /// Hint only. Durable shard/pending scans must recover a lost notification.
    async fn wake(&self, _shard: ShardId) -> Result<(), RuntimeError> {
        Err(RuntimeError { code: "worker_wake_unsupported".into() })
    }
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError>;
    /// Stop acquiring new work; retain recoverable obligations if grace expires.
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError>;
}


/// Replaceable outbound peer transport. Credentials and HTTP details stay outside
/// the engine. Implementations authenticate and bind the caller's exact instance;
/// target addresses come from current member records, never arbitrary user input.
#[async_trait]
pub trait DistWorkerPeerPort: Send + Sync {
    async fn handoff(&self, target: &MemberRecord, request: HandoffRequest) -> Result<HandoffOutcome, RuntimeError>;
    async fn source_done(&self, target: &MemberRecord, source: Instance, claim: ShardClaim, request_id: RequestId) -> Result<bool, RuntimeError>;
}
