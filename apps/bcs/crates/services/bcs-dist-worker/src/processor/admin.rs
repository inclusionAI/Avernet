//! Conditional recovery is only entered by an authorized application request.
use super::engine::WorkerProcessor;
use crate::runtime::err;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    port::{
        dist_runtime::DistWorkerIsolationControlPort,
        repo::distributed::{admission::*, control::*},
    },
    types::dist_runtime::RuntimeError,
};
#[async_trait]
impl DistWorkerIsolationControlPort for WorkerProcessor {
    async fn isolation(
        &self,
        scope: &TaskScope,
        sid: &Sid,
    ) -> Result<Option<ControlSnapshot>, RuntimeError> {
        let Some(spec) = self.io(self.runtime.sessions.get_spec(sid)).await? else {
            return Ok(None);
        };
        if spec.scope != *scope || spec.sid != *sid {
            return Ok(None);
        }
        self.io(self.ports.control.load_control(sid)).await
    }
    async fn resume(
        &self,
        scope: &TaskScope,
        request: RecoveryAuthorization,
    ) -> Result<ControlOutcome, RuntimeError> {
        let sid = &request.guard.owner.sid;
        if self.isolation(scope, sid).await?.is_none() {
            return Err(err("session_control_scope"));
        }
        {
            let state = self.runtime.state()?;
            if !self.runtime.ready_locked(&state)
                || request.guard.owner.owner.instance != self.runtime.instance
                || state
                    .shards
                    .get(&request.guard.owner.shard)
                    .is_none_or(|claim| claim.owner != request.guard.owner.owner)
            {
                return Ok(ControlOutcome::Fenced);
            }
        }
        let outcome = self
            .io(self.ports.control.begin_recovery(request.clone()))
            .await?;
        let control = match outcome {
            ControlOutcome::Applied(c) | ControlOutcome::AlreadyApplied(c) => c,
            other => return Ok(other),
        };
        if matches!(control.control.mode, ControlMode::Normal) {
            return Ok(ControlOutcome::AlreadyApplied(control));
        }
        let guard = Self::guard(&control);
        let receipt = match self
            .io(self
                .ports
                .admission
                .reopen_gate(guard.clone(), request.request_id))
            .await?
        {
            GateReopenOutcome::Opened(receipt) | GateReopenOutcome::AlreadyOpened(receipt) => {
                receipt
            }
            GateReopenOutcome::Fenced => return Ok(ControlOutcome::Fenced),
            GateReopenOutcome::Conflict => return Ok(ControlOutcome::ContentConflict),
            GateReopenOutcome::NotReady => return Ok(ControlOutcome::NotReady),
        };
        self.io(self.ports.control.confirm_recovery(guard, receipt))
            .await
    }
}
