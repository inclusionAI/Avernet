//! Engine control consumed by the application admin facade.
use crate::port::repo::distributed::ownership::{
    HandoffOutcome, HandoffRequest, MemberRecord, ShardClaim,
};
use crate::types::dist_runtime::{DrainOutcome, RuntimeError, RuntimeSnapshot};
use async_trait::async_trait;
use bcs_dist_model::{Instance, RequestId, ShardId};
use std::time::Duration;

/// The engine implements this port without importing application-layer contracts.
#[async_trait]
pub trait DistRuntimeControlPort: Send + Sync {
    /// Called only after the application has checked the caller's bound instance.
    async fn accept_handoff(
        &self,
        _request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        Err(RuntimeError {
            code: "worker_handoff_unsupported".into(),
        })
    }
    async fn handoff_done(
        &self,
        _source: Instance,
        _target: ShardClaim,
        _request_id: RequestId,
    ) -> Result<bool, RuntimeError> {
        Err(RuntimeError {
            code: "worker_handoff_unsupported".into(),
        })
    }
    /// Hint only. Durable shard/pending scans must recover a lost notification.
    async fn wake(&self, _shard: ShardId) -> Result<(), RuntimeError> {
        Err(RuntimeError {
            code: "worker_wake_unsupported".into(),
        })
    }
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError>;
    /// Stop acquiring new work; retain recoverable obligations if grace expires.
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError>;
}

/// Replaceable outbound peer transport. Credentials and HTTP details stay outside
/// the engine. Implementations bind the caller's exact instance using peer
/// authentication or a deployment channel restricted to mutually trusted Workers.
/// Target addresses come from current member records, never arbitrary user input.
#[async_trait]
pub trait DistWorkerPeerPort: Send + Sync {
    async fn handoff(
        &self,
        target: &MemberRecord,
        request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError>;
    async fn source_done(
        &self,
        target: &MemberRecord,
        source: Instance,
        claim: ShardClaim,
        request_id: RequestId,
    ) -> Result<bool, RuntimeError>;
}

/// Mandatory host policy. Actor identity is supplied by authentication, never
/// copied from request JSON; target scope is separately authorized on every call.
#[async_trait]
pub trait DistWorkerControlAuthorizerPort: Send + Sync {
    async fn authorize(
        &self,
        actor: &bcs_dist_model::TaskScope,
        target: &bcs_dist_model::TaskScope,
        action: crate::types::dist_runtime::WorkerControlAction,
    ) -> Result<bool, RuntimeError>;
}
/// Engine operations behind the application authorization boundary.
#[async_trait]
pub trait DistWorkerIsolationControlPort: Send + Sync {
    async fn isolation(
        &self,
        scope: &bcs_dist_model::TaskScope,
        sid: &bcs_dist_model::Sid,
    ) -> Result<Option<crate::port::repo::distributed::control::ControlSnapshot>, RuntimeError>;
    async fn resume(
        &self,
        scope: &bcs_dist_model::TaskScope,
        request: crate::port::repo::distributed::control::RecoveryAuthorization,
    ) -> Result<crate::port::repo::distributed::control::ControlOutcome, RuntimeError>;
}
