//! Authorization is mandatory before either query or conditional recovery.
use async_trait::async_trait;
use bcs_dist_model::{Sid, TaskScope};
use bcs_service_api::{
    application::dist_runtime::DistWorkerIsolationService,
    port::{
        dist_runtime::{DistWorkerControlAuthorizerPort, DistWorkerIsolationControlPort},
        repo::distributed::control::*,
    },
    types::dist_runtime::*,
};
use std::sync::Arc;
pub struct WorkerIsolationApplication {
    control: Arc<dyn DistWorkerIsolationControlPort>,
    authorizer: Arc<dyn DistWorkerControlAuthorizerPort>,
}
impl WorkerIsolationApplication {
    pub fn new(
        control: Arc<dyn DistWorkerIsolationControlPort>,
        authorizer: Arc<dyn DistWorkerControlAuthorizerPort>,
    ) -> Self {
        Self {
            control,
            authorizer,
        }
    }
    async fn authorize(
        &self,
        actor: &TaskScope,
        target: &TaskScope,
        action: WorkerControlAction,
    ) -> Result<(), RuntimeError> {
        if self.authorizer.authorize(actor, target, action).await? {
            Ok(())
        } else {
            Err(RuntimeError {
                code: "worker_control_forbidden".into(),
            })
        }
    }
}
#[async_trait]
impl DistWorkerIsolationService for WorkerIsolationApplication {
    async fn isolation(
        &self,
        actor: TaskScope,
        target: TaskScope,
        sid: Sid,
    ) -> Result<Option<ControlSnapshot>, RuntimeError> {
        self.authorize(&actor, &target, WorkerControlAction::Inspect)
            .await?;
        self.control.isolation(&target, &sid).await
    }
    async fn resume(
        &self,
        actor: TaskScope,
        target: TaskScope,
        request: RecoveryAuthorization,
    ) -> Result<ControlOutcome, RuntimeError> {
        self.authorize(&actor, &target, WorkerControlAction::Resume)
            .await?;
        self.control.resume(&target, request).await
    }
}
