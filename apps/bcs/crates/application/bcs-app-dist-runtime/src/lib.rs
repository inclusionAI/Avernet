//! Worker control application boundary. The delivery layer must authenticate
//! and bind an exact node/incarnation before constructing the source argument.
use async_trait::async_trait;
use bcs_dist_model::{Instance, RequestId, ShardId};
use bcs_service_api::{
    application::dist_runtime::DistWorkerRuntimeService,
    port::{
        dist_runtime::DistRuntimeControlPort,
        repo::distributed::ownership::{HandoffOutcome, HandoffRequest, ShardClaim},
    },
    types::dist_runtime::*,
};
use std::{sync::Arc, time::Duration};
pub struct WorkerRuntimeApplication {
    control: Arc<dyn DistRuntimeControlPort>,
}
impl WorkerRuntimeApplication {
    pub fn new(control: Arc<dyn DistRuntimeControlPort>) -> Self {
        Self { control }
    }
}
#[async_trait]
impl DistWorkerRuntimeService for WorkerRuntimeApplication {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        self.control.snapshot().await
    }
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        self.control.drain(grace).await
    }
    async fn accept_handoff(
        &self,
        source: Instance,
        request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        if source != request.from.owner.instance {
            return Err(RuntimeError {
                code: "peer_instance_mismatch".into(),
            });
        }
        let snapshot = self.control.snapshot().await?;
        if request.target != snapshot.instance {
            return Err(RuntimeError {
                code: "target_incarnation_mismatch".into(),
            });
        }
        self.control.accept_handoff(request).await
    }
    async fn handoff_done(
        &self,
        source: Instance,
        target: ShardClaim,
        id: RequestId,
    ) -> Result<bool, RuntimeError> {
        let snapshot = self.control.snapshot().await?;
        if target.owner.instance != snapshot.instance {
            return Err(RuntimeError {
                code: "target_incarnation_mismatch".into(),
            });
        }
        self.control.handoff_done(source, target, id).await
    }
    async fn wake(&self, _source: Instance, shard: ShardId) -> Result<(), RuntimeError> {
        self.control.wake(shard).await
    }
}
