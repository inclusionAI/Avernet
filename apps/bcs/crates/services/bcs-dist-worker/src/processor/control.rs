//! Isolation closes admission before bounded independent terminal transitions.
use super::{engine::*, lifecycle::*, transitions::*};
use crate::runtime::{err, store_error};
use bcs_dist_model::*;
use bcs_service_api::{
    port::repo::distributed::{admission::*, control::*, session::*},
    types::dist_runtime::RuntimeError,
};

impl WorkerProcessor {
    pub(super) fn guard(control: &ControlSnapshot) -> ControlGuard {
        ControlGuard {
            owner: control.owner.clone(),
            expected_head: control.head,
            expected_revision: control.control.revision,
        }
    }
    pub(super) fn ordinary_purpose(&self, slot: &Slot) -> Result<CommitPurpose, RuntimeError> {
        let control = slot
            .control
            .as_ref()
            .ok_or_else(|| err("control_missing"))?;
        match &control.control.mode {
            ControlMode::Normal => Ok(CommitPurpose::Ordinary {
                control_revision: control.control.revision,
            }),
            _ => Err(err("session_control_paused")),
        }
    }
    pub(super) fn purpose_allowed(slot: &Slot, purpose: &CommitPurpose) -> bool {
        slot.control
            .as_ref()
            .is_some_and(|c| match (&c.control.mode, purpose) {
                (ControlMode::Normal, CommitPurpose::Ordinary { control_revision }) => {
                    c.control.revision == *control_revision
                }
                (
                    ControlMode::Quarantined { isolation },
                    CommitPurpose::Isolation { operation_id },
                ) => isolation.operation_id == *operation_id,
                _ => false,
            })
    }
    pub(super) fn discard_control_fenced(&self, slot: &mut Slot) -> Result<(), RuntimeError> {
        // Call only after observing a monotonic control fence at the unchanged
        // business head. An old delayed write can never pass that fence again.
        self.runtime
            .control_fenced_round(&slot.acquired.ownership)?;
        slot.prepared = None;
        slot.pending = None;
        slot.pending_since = None;
        slot.evaluation = None;
        slot.round_started = false;
        Ok(())
    }
    pub(super) async fn refresh_control(&self, slot: &mut Slot) -> Result<(), RuntimeError> {
        self.load_current_control(slot).await?;
        if slot
            .control
            .as_ref()
            .is_some_and(|c| matches!(c.control.mode, ControlMode::Blocked { .. }))
        {
            return Ok(());
        }
        let digest = bcs_dist_encoding::digest_json(
            &serde_json::to_value(&slot.acquired.envelope.checkpoint)
                .map_err(|_| err("checkpoint_encoding"))?,
        );
        if slot
            .control
            .as_ref()
            .is_none_or(|c| c.checkpoint_digest != digest)
        {
            return Err(err("checkpoint_content_changed"));
        }
        Ok(())
    }
    pub(super) async fn load_current_control(&self, slot: &mut Slot) -> Result<(), RuntimeError> {
        let control = self
            .io(self
                .ports
                .control
                .load_control(&slot.acquired.ownership.sid))
            .await?
            .ok_or_else(|| err("control_missing"))?;
        if control.owner != slot.acquired.ownership {
            self.runtime.forget_fenced(&slot.acquired.ownership).await?;
            return Err(err("control_fenced"));
        }
        if control.head != slot.acquired.envelope.checkpoint.commit_seq {
            return Err(err("control_head_conflict"));
        }
        slot.control = Some(control);
        Ok(())
    }
    pub(super) fn reconcile_control_fence(&self, slot: &mut Slot) -> Result<(), RuntimeError> {
        let control = slot
            .control
            .as_ref()
            .ok_or_else(|| err("control_missing"))?;
        if slot
            .risk_begin
            .as_ref()
            .is_some_and(|(attempt, _, revision)| {
                control.control.revision > *revision
                    && control
                        .control
                        .active_attempt
                        .as_ref()
                        .is_none_or(|a| a.identity.id != attempt.id)
            })
        {
            slot.risk_begin = None;
        }
        if matches!(control.control.mode, ControlMode::Normal) {
            return Ok(());
        }
        let mut discarded = slot.evaluation.take().is_some();
        if slot
            .prepared
            .as_ref()
            .is_some_and(|p| !Self::purpose_allowed(slot, &p.purpose))
        {
            slot.prepared = None;
            discarded = true;
        }
        if slot.risk_outcome.as_ref().is_some_and(|(attempt, _)| {
            control
                .control
                .active_attempt
                .as_ref()
                .is_none_or(|a| a.identity.id != attempt.id)
        }) {
            slot.risk_outcome = None;
        }
        if !matches!(control.control.mode, ControlMode::Quarantined { .. }) {
            slot.risk_begin = None;
        }
        if discarded && slot.round_started && slot.pending.is_none() {
            self.runtime
                .control_fenced_round(&slot.acquired.ownership)?;
            slot.round_started = false;
        }
        Ok(())
    }
    pub(super) async fn accept_control(
        &self,
        slot: &mut Slot,
        outcome: ControlOutcome,
    ) -> Result<(), RuntimeError> {
        match outcome {
            ControlOutcome::Applied(control) | ControlOutcome::AlreadyApplied(control) => {
                slot.control = Some(control);
                Ok(())
            }
            ControlOutcome::Fenced => {
                self.runtime.forget_fenced(&slot.acquired.ownership).await?;
                Err(err("control_fenced"))
            }
            ControlOutcome::HeadConflict => Err(err("control_head_conflict")),
            ControlOutcome::RevisionConflict => Err(err("control_revision_conflict")),
            ControlOutcome::ContentConflict => Err(err("control_content_conflict")),
            ControlOutcome::NotReady => Err(err("control_not_ready")),
        }
    }
    /// Returns true when ordinary execution is paused. Each visit advances at
    /// most one closure phase or creates one immutable finalization candidate.
    pub(super) async fn drive_control(&self, slot: &mut Slot) -> Result<bool, RuntimeError> {
        let control = slot.control.clone().ok_or_else(|| err("control_missing"))?;
        let (isolation, blocked) = match &control.control.mode {
            ControlMode::Normal => return Ok(false),
            ControlMode::Quarantined { isolation } => (isolation.clone(), false),
            ControlMode::Blocked { isolation } => (isolation.clone(), true),
            ControlMode::Recovering { .. } => return Ok(true),
        };
        if isolation.closure.is_none() {
            let gate = self
                .io(self.ports.admission.gate_state(&control.owner))
                .await?;
            match self
                .io(self.ports.admission.close_gate(
                    Self::guard(&control),
                    gate.generation,
                    isolation.operation_id,
                ))
                .await?
            {
                GateCloseOutcome::Closed(closure) => {
                    let outcome = self
                        .io(self
                            .ports
                            .control
                            .record_closure(Self::guard(&control), closure))
                        .await?;
                    self.accept_control(slot, outcome).await?;
                }
                GateCloseOutcome::Fenced => return Err(err("control_fenced")),
                GateCloseOutcome::Conflict => return Err(err("closure_conflict")),
            }
            return Ok(true);
        }
        if blocked || isolation.completion.is_some() {
            // The gate and evidence are durable. Keep remote obligations there,
            // not in a resident slot that can prevent healthy Session admission.
            // No commit is unknown here: pending requests are resolved before
            // entering drive_control, and the control fence rejects old work.
            if slot.pending.is_some() || slot.prepared.is_some() {
                return Err(err("isolation_park_pending_commit"));
            }
            self.runtime.park_isolated(&control.owner).await?;
            return Ok(true);
        }
        let before = &slot.acquired.envelope.checkpoint;
        let spec = slot
            .spec
            .as_ref()
            .ok_or_else(|| err("session_spec_missing"))?;
        let now = (self.ports.now)();
        let candidate = if let Some(task) = before
            .tasks
            .iter()
            .find(|t| matches!(t.phase, TaskPhase::AwaitingResult { .. }))
        {
            let metadata = self.metadata(spec, &task.tid).await?;
            Some(
                archive_confirmed(
                    before,
                    &metadata,
                    now,
                    slot.completed
                        .as_ref()
                        .ok_or_else(|| err("archive_unconfirmed"))?,
                )
                .map_err(store_error)?,
            )
        } else if before.tasks.iter().any(|t| t.phase == TaskPhase::Done) {
            Some(retire_published(before, slot.completed.as_ref()).map_err(store_error)?)
        } else if let Some(task) = before.tasks.front() {
            let metadata = self.metadata(spec, &task.tid).await?;
            let status = if task.phase == TaskPhase::Running {
                TerminalStatus::Failed
            } else {
                TerminalStatus::Rejected
            };
            Some(
                finalize_independently(
                    before,
                    &metadata,
                    status,
                    "SessionQuarantined",
                    now,
                    slot.completed.as_ref(),
                )
                .map_err(store_error)?,
            )
        } else {
            let closure = isolation
                .closure
                .as_ref()
                .ok_or_else(|| err("closure_missing"))?;
            let work = self
                .io(self
                    .ports
                    .admission
                    .advance_closure(&control.owner, closure))
                .await?;
            if let Some(certificate) = work.certificate {
                let outcome = self
                    .io(self
                        .ports
                        .control
                        .record_closure_complete(Self::guard(&control), certificate))
                    .await?;
                self.accept_control(slot, outcome).await?;
                return Ok(true);
            }
            if let Some(obligation) = work.obligation {
                let input = obligation.request.input;
                let metadata = self.metadata(spec, &input.tid).await?;
                let mut base = before.clone();
                base.tasks.push_back(TaskProgress {
                    tid: input.tid,
                    phase: TaskPhase::Queued,
                    input: match input.body {
                        InputBody::User { payload } => payload,
                        _ => Payload::Inline(serde_json::Value::Null),
                    },
                    steps: 0,
                    result_ref: None,
                    output_limits: metadata.output_limits,
                    output_usage: metadata.output_usage,
                    publication: SnapshotProgress::default(),
                    finalization: None,
                });
                Some(
                    finalize_independently(
                        &base,
                        &metadata,
                        TerminalStatus::Rejected,
                        "SessionQuarantined",
                        now,
                        slot.completed.as_ref(),
                    )
                    .map_err(store_error)?,
                )
            } else {
                None
            }
        };
        if let Some(candidate) = candidate {
            if !slot.round_started {
                self.runtime.begin_round(&control.owner.sid)?;
                slot.round_started = true;
            }
            slot.prepared = Some(Prepared {
                candidate,
                finalizing: true,
                id: CommitId(uuid::Uuid::new_v4().to_string()),
                purpose: CommitPurpose::Isolation {
                    operation_id: isolation.operation_id,
                },
            });
        }
        Ok(true)
    }
}
