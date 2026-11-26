//! A corrupt body cannot stop raw owner sealing and durable Blocked diagnostics.
use super::engine::*;
use crate::runtime::{AcquisitionStage, err};
use bcs_dist_encoding::{digest_fields, hex_of};
use bcs_dist_model::*;
use bcs_service_api::{
    port::repo::distributed::{admission::*, control::*, inbox::ScheduleObservation},
    types::dist_runtime::RuntimeError,
};
impl WorkerProcessor {
    pub(super) async fn diagnose_acquisition(&self) -> Result<(), RuntimeError> {
        let failed = {
            let mut state = self.runtime.state()?;
            if !self.runtime.ready_locked(&state) {
                return Ok(());
            }
            let after = state.acquisition_cursor.as_ref();
            let failed = state
                .acquisitions
                .iter()
                .filter(|(sid, _)| after.is_none_or(|after| *sid > after))
                .chain(
                    state
                        .acquisitions
                        .iter()
                        .filter(|(sid, _)| after.is_some_and(|after| *sid <= after)),
                )
                .find_map(|(sid, a)| match &a.stage {
                    AcquisitionStage::Diagnostic { code }
                        if state.shards.get(&a.claim.shard) == Some(&a.claim) =>
                    {
                        Some((sid.clone(), a.claim.clone(), code.clone()))
                    }
                    _ => None,
                });
            if let Some((sid, _, _)) = &failed {
                state.acquisition_cursor = Some(sid.clone());
            }
            failed
        };
        let Some((sid, claim, code)) = failed else {
            return Ok(());
        };
        let Some(_scan_visit) = self.runtime.begin_session_scan()? else {
            return Ok(());
        };
        let mut control = match self
            .io(self.ports.control.acquire_control(&sid, &claim))
            .await?
        {
            ControlAcquireOutcome::Acquired(control) => control,
            ControlAcquireOutcome::Fenced => {
                self.runtime.state()?.acquisitions.remove(&sid);
                return Ok(());
            }
            ControlAcquireOutcome::Deferred { .. } => return Ok(()),
            ControlAcquireOutcome::Missing => return Err(err("corrupt_checkpoint_missing")),
        };
        if !matches!(control.control.mode, ControlMode::Blocked { .. }) {
            let hash = digest_fields(&[
                b"raw-block:v1",
                sid.0.as_bytes(),
                &control.head.0.to_be_bytes(),
                control.checkpoint_digest.0.as_bytes(),
                code.as_bytes(),
            ]);
            let attempt = AttemptIdentity {
                id: RequestId(format!("raw-{}", hex_of(&hash))),
                head: control.head,
                state_digest: control.checkpoint_digest.clone(),
                input_digest: Digest("none".into()),
                context_digest: Digest(format!("raw:{code}")),
                frozen_now: TimestampMillis(0),
            };
            let outcome = AttemptOutcome::Uninterpretable {
                code,
                isolation_id: RequestId(format!("raw-block-{}", hex_of(&hash))),
            };
            control = match self
                .io(self
                    .ports
                    .control
                    .isolate(Self::guard(&control), attempt, outcome))
                .await?
            {
                ControlOutcome::Applied(c) | ControlOutcome::AlreadyApplied(c) => c,
                _ => return Err(err("raw_isolation_conflict")),
            };
        }
        // Header-only scheduling observe installs exactly this new owner fence.
        if !matches!(
            self.io(self.runtime.scheduling.observe(&control.owner))
                .await?,
            ScheduleObservation::Observed(_)
        ) {
            return Err(err("schedule_fenced"));
        }
        let ControlMode::Blocked { isolation } = &control.control.mode else {
            return Err(err("raw_isolation_conflict"));
        };
        if isolation.closure.is_none() {
            let gate = self
                .io(self.ports.admission.gate_state(&control.owner))
                .await?;
            let GateCloseOutcome::Closed(closure) = self
                .io(self.ports.admission.close_gate(
                    Self::guard(&control),
                    gate.generation,
                    isolation.operation_id.clone(),
                ))
                .await?
            else {
                return Err(err("closure_conflict"));
            };
            if !matches!(
                self.io(self
                    .ports
                    .control
                    .record_closure(Self::guard(&control), closure))
                    .await?,
                ControlOutcome::Applied(_) | ControlOutcome::AlreadyApplied(_)
            ) {
                return Err(err("closure_conflict"));
            }
        }
        self.io(self.runtime.sessions.release_local(&control.owner))
            .await?;
        let mut state = self.runtime.state()?;
        state.acquisitions.remove(&sid);
        // This is only a bounded local retry hint. Durable discovery still owns
        // the damaged Session and a later successor can inspect the same proof.
        if state.retries.len() < self.runtime.config.max_active_sessions {
            state.retries.insert(
                sid,
                (
                    claim,
                    tokio::time::Instant::now() + self.runtime.config.check_interval,
                ),
            );
        }
        Ok(())
    }
}
