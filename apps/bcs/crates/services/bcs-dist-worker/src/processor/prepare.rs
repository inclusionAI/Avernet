use super::{decision::*, engine::*, lifecycle::*, transitions::*};
use crate::runtime::{err, store_error};
use bcs_dist_model::*;
use bcs_service_api::{
    core::dist_runner::*,
    port::repo::distributed::{control::*, session::*},
    types::dist_runtime::RuntimeError,
};
use std::collections::BTreeMap;

pub(super) struct FrozenDecision {
    attempt: AttemptIdentity,
    base: Checkpoint,
    metadata: TaskSnapshot,
    decision: Decision,
    now: TimestampMillis,
    dispatches: BTreeMap<Did, DispatchSpec>,
    display: DisplaySnapshot,
}
impl WorkerProcessor {
    pub(super) async fn prepare(
        &self,
        slot: &mut Slot,
    ) -> Result<Option<(Checkpoint, bool)>, RuntimeError> {
        if slot.evaluation.is_some() {
            return self.finish_evaluation(slot).await.map(Some);
        }
        let before = &slot.acquired.envelope.checkpoint;
        let spec = slot
            .spec
            .as_ref()
            .ok_or_else(|| err("session_spec_missing"))?;
        let now = self.frozen_now(slot);
        if let Some(task) = before
            .tasks
            .iter()
            .find(|t| matches!(t.phase, TaskPhase::AwaitingResult { .. }))
        {
            let metadata = self.metadata(spec, &task.tid).await?;
            let completed = slot
                .completed
                .as_ref()
                .ok_or_else(|| err("archive_unconfirmed"))?;
            return archive_confirmed(before, &metadata, now, completed)
                .map(|c| Some((c, true)))
                .map_err(store_error);
        }
        if before.tasks.iter().any(|t| t.phase == TaskPhase::Done) {
            return retire_published(before, slot.completed.as_ref())
                .map(|c| Some((c, false)))
                .map_err(store_error);
        }
        // A terminal event already in this delivery confirms the remote dispatch,
        // even if its CancelGrace deadline has become due on the same visit.
        let confirmed_cancel = slot.input.as_ref().and_then(|batch| match batch.entries.as_slice() {
            [InputEnvelope { body: InputBody::Agent { event }, .. }]
                if before.pending_cancel_did.as_ref() == Some(&event.did)
                    && matches!(event.kind, AgentEventKind::Final | AgentEventKind::Error) =>
            {
                Some(&event.did)
            }
            _ => None,
        });
        if let Some(deadline) = before
            .deadlines
            .iter()
            .filter(|d| d.at <= now)
            .filter(|d| !matches!(&d.kind, DeadlineKind::CancelGrace { did } if confirmed_cancel == Some(did)))
            .min_by_key(|d| d.at)
            .cloned()
        {
            let tid = match &deadline.kind {
                DeadlineKind::CancelGrace { did } => {
                    let mut candidate = next_checkpoint(before, slot.completed.as_ref()).map_err(store_error)?;
                    if candidate.pending_cancel_did.as_ref() == Some(did) {
                        candidate.downstream_generation = candidate.downstream_generation.checked_add(1)
                            .ok_or_else(|| err("downstream_generation_exhausted"))?;
                        candidate.pending_cancel_did = None;
                    }
                    candidate.deadlines.retain(|d| d.id != deadline.id);
                    return Ok(Some((candidate, false)));
                }
                DeadlineKind::Task { tid }
                | DeadlineKind::Snapshot { tid }
                | DeadlineKind::Dispatch { tid, .. } => tid,
            };
            if before
                .tasks
                .iter()
                .any(|t| t.tid == *tid && t.phase == TaskPhase::Running)
            {
                let metadata = self.metadata(spec, tid).await?;
                if deadline.id == task_timeout_id(tid) {
                    return finalize_independently(
                        before,
                        &metadata,
                        TerminalStatus::Failed,
                        "TaskDeadlineExceeded",
                        now,
                        slot.completed.as_ref(),
                    )
                    .map(|c| Some((c, true)))
                    .map_err(store_error);
                }
                let mut base = before.clone();
                if !matches!(deadline.kind, DeadlineKind::Snapshot { .. }) {
                    base.deadline_history
                        .insert(deadline.id.clone(), deadline.clone());
                }
                base.deadlines.retain(|d| d.id != deadline.id);
                if matches!(deadline.kind, DeadlineKind::Snapshot { .. }) {
                    let display = self.display(before, &metadata).await?;
                    return flush_snapshot(
                        before,
                        &metadata,
                        display,
                        now,
                        slot.completed.as_ref(),
                    )
                    .map(|c| Some((c, false)))
                    .map_err(store_error);
                }
                self.evaluate(
                    slot,
                    base,
                    metadata,
                    vec![RunnerInput::Timeout(deadline)],
                    now,
                    false,
                )
                .await?;
                return self.finish_evaluation(slot).await.map(Some);
            }
            let mut candidate =
                next_checkpoint(before, slot.completed.as_ref()).map_err(store_error)?;
            candidate.deadlines.retain(|d| d.id != deadline.id);
            return Ok(Some((candidate, false)));
        }
        if before.pending_cancel_did.is_none()
            && !before.tasks.iter().any(|t| t.phase == TaskPhase::Running)
            && let Some(task) = before.tasks.iter().find(|t| t.phase == TaskPhase::Queued)
        {
            let metadata = self.metadata(spec, &task.tid).await?;
            let payload = self
                .references
                .resolve(&task.input, PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES)
                .await
                .map_err(store_error)?;
            let tid = task.tid.clone();
            let mut base = before.clone();
            let task = base
                .tasks
                .iter_mut()
                .find(|t| t.tid == tid)
                .ok_or_else(|| err("queued_task_missing"))?;
            task.phase = TaskPhase::Running;
            let Some(at) = i64::try_from(spec.limits.task_timeout_millis)
                .ok()
                .and_then(|duration| now.0.checked_add(duration))
            else {
                return finalize_independently(
                    before,
                    &metadata,
                    TerminalStatus::Failed,
                    "TaskDeadlineOutOfRange",
                    now,
                    slot.completed.as_ref(),
                )
                .map(|candidate| Some((candidate, true)))
                .map_err(store_error);
            };
            base.deadlines.push(Deadline {
                id: task_timeout_id(&tid),
                at: TimestampMillis(at),
                kind: DeadlineKind::Task { tid: tid.clone() },
            });
            self.evaluate(
                slot,
                base,
                metadata,
                vec![RunnerInput::Start {
                    tid,
                    payload: Payload::Inline(payload),
                }],
                now,
                true,
            )
            .await?;
            return self.finish_evaluation(slot).await.map(Some);
        }
        let Some(batch) = &slot.input else {
            return Ok(None);
        };
        if batch.entries.len() > 1 {
            return Err(err("processor_input_batch_bound"));
        }
        let mut base = before.clone();
        base.consumed = Some(batch.read_through.clone());
        let Some(input) = batch.entries.first() else {
            return next_checkpoint(&base, slot.completed.as_ref())
                .map(|c| Some((c, false)))
                .map_err(store_error);
        };
        if input.sid != spec.sid || input.schema_version != 1 {
            return Err(err("processor_input_scope"));
        }
        if let InputBody::User { payload } = &input.body {
            if !base.tasks.iter().any(|t| t.tid == input.tid) {
                let metadata = self.metadata(spec, &input.tid).await?;
                if matches!(
                    metadata.content.status,
                    TaskStatus::Completed
                        | TaskStatus::Failed
                        | TaskStatus::Canceled
                        | TaskStatus::Rejected
                ) {
                    return next_checkpoint(&base, slot.completed.as_ref())
                        .map(|c| Some((c, false)))
                        .map_err(store_error);
                }
                let maximum =
                    spec.limits
                        .max_queued_tasks
                        .min(metadata.output_limits.max_queued_tasks) as usize;
                let full = base
                    .tasks
                    .iter()
                    .filter(|t| t.phase == TaskPhase::Queued)
                    .count()
                    >= maximum;
                base.tasks.push_back(TaskProgress {
                    steps: 0,
                    tid: input.tid.clone(),
                    phase: TaskPhase::Queued,
                    input: payload.clone(),
                    result_ref: None,
                    output_limits: metadata.output_limits,
                    output_usage: metadata.output_usage,
                    publication: SnapshotProgress::default(),
                    finalization: None,
                });
                if full {
                    return finalize_independently(
                        &base,
                        &metadata,
                        TerminalStatus::Rejected,
                        "QueueCapacityExceeded",
                        now,
                        slot.completed.as_ref(),
                    )
                    .map(|c| Some((c, true)))
                    .map_err(store_error);
                }
            }
            return next_checkpoint(&base, slot.completed.as_ref())
                .map(|c| Some((c, false)))
                .map_err(store_error);
        }
        if let Some(confirmed) = confirmed_cancel {
            base.pending_cancel_did = None;
            base.deadlines.retain(|d| !matches!(&d.kind, DeadlineKind::CancelGrace { did } if did == confirmed));
            return next_checkpoint(&base, slot.completed.as_ref()).map(|c| Some((c, false))).map_err(store_error);
        }
        let Some(task) = base.tasks.iter().find(|t| t.tid == input.tid) else {
            return next_checkpoint(&base, slot.completed.as_ref())
                .map(|c| Some((c, false)))
                .map_err(store_error);
        };
        let metadata = self.metadata(spec, &input.tid).await?;
        if matches!(input.body, InputBody::Cancel { .. }) {
            return finalize_independently(
                &base,
                &metadata,
                TerminalStatus::Canceled,
                "CanceledByUser",
                now,
                slot.completed.as_ref(),
            )
            .map(|c| Some((c, true)))
            .map_err(store_error);
        }
        if task.phase != TaskPhase::Running {
            return next_checkpoint(&base, slot.completed.as_ref())
                .map(|c| Some((c, false)))
                .map_err(store_error);
        }
        let runner_input = match &input.body {
            InputBody::HumanReply {
                interaction,
                responder,
                payload,
            } => {
                let content = base
                    .view_drafts
                    .get(&input.tid)
                    .unwrap_or(&metadata.content);
                if !content.interactions.iter().any(|i| i.id == *interaction) {
                    return next_checkpoint(&base, slot.completed.as_ref())
                        .map(|c| Some((c, false)))
                        .map_err(store_error);
                }
                RunnerInput::HumanReply {
                    tid: input.tid.clone(),
                    interaction: interaction.clone(),
                    responder: responder.clone(),
                    payload: Payload::Inline(
                        self.references
                            .resolve(payload, PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES)
                            .await
                            .map_err(store_error)?,
                    ),
                }
            }
            InputBody::Agent { event } => {
                let critical = matches!(
                    event.kind,
                    AgentEventKind::InputRequired { .. }
                        | AgentEventKind::AuthRequired { .. }
                        | AgentEventKind::Final
                        | AgentEventKind::Error
                );
                let Some(sequence) = base.dispatch_seq.get_mut(&event.did) else {
                    return next_checkpoint(&base, slot.completed.as_ref())
                        .map(|c| Some((c, false)))
                        .map_err(store_error);
                };
                if sequence.tid != input.tid {
                    return Err(err("dispatch_task_identity"));
                }
                if !critical
                    && (event.holder_epoch < sequence.holder_epoch
                        || (event.holder_epoch == sequence.holder_epoch
                            && event.seq <= sequence.sequence))
                {
                    return next_checkpoint(&base, slot.completed.as_ref())
                        .map(|c| Some((c, false)))
                        .map_err(store_error);
                }
                if event.holder_epoch > sequence.holder_epoch
                    || (event.holder_epoch == sequence.holder_epoch
                        && event.seq > sequence.sequence)
                {
                    sequence.holder_epoch = event.holder_epoch;
                    sequence.sequence = event.seq;
                }
                if matches!(event.kind, AgentEventKind::Final | AgentEventKind::Error) {
                    sequence.terminal_seen = true;
                }
                if matches!(event.kind, AgentEventKind::Thinking) {
                    return self
                        .thinking(slot, &base, &metadata, event)
                        .await
                        .map(|c| Some((c, false)));
                }
                let mut event = event.clone();
                event.payload = Payload::Inline(
                    self.references
                        .resolve(&event.payload, PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES)
                        .await
                        .map_err(store_error)?,
                );
                RunnerInput::Agent {
                    tid: input.tid.clone(),
                    event,
                }
            }
            _ => return Err(err("processor_input_kind")),
        };
        self.evaluate(slot, base, metadata, vec![runner_input], now, false)
            .await?;
        self.finish_evaluation(slot).await.map(Some)
    }
    pub(super) async fn metadata(
        &self,
        spec: &SessionSpec,
        tid: &Tid,
    ) -> Result<TaskSnapshot, RuntimeError> {
        let metadata = self
            .io(self.ports.tasks.load_snapshot(&spec.scope, tid))
            .await?
            .ok_or_else(|| err("task_snapshot_missing"))?;
        if metadata.sid != spec.sid || metadata.tid != *tid || metadata.scope != spec.scope {
            return Err(err("task_snapshot_identity"));
        }
        self.ports
            .projection
            .validate_task_limits(&metadata, &metadata.output_limits)
            .map_err(|_| err("frozen_task_limits"))?;
        self.ports
            .projection
            .validate_session_limits(spec, &metadata.output_limits)
            .map_err(|_| err("session_capacity_profile"))?;
        Ok(metadata)
    }
    async fn display(
        &self,
        base: &Checkpoint,
        metadata: &TaskSnapshot,
    ) -> Result<DisplaySnapshot, RuntimeError> {
        let content = base
            .view_drafts
            .get(&metadata.tid)
            .unwrap_or(&metadata.content);
        let value = self
            .references
            .resolve_output(&content.snapshot)
            .await
            .map_err(store_error)?;
        serde_json::from_value(value).map_err(|_| err("display_format"))
    }
    async fn evaluate(
        &self,
        slot: &mut Slot,
        mut base: Checkpoint,
        metadata: TaskSnapshot,
        inputs: Vec<RunnerInput>,
        now: TimestampMillis,
        initialize: bool,
    ) -> Result<(), RuntimeError> {
        let spec = slot
            .spec
            .clone()
            .ok_or_else(|| err("session_spec_missing"))?;
        let mut dispatches = BTreeMap::new();
        for (did, _) in base
            .dispatch_seq
            .iter()
            .filter(|(_, sequence)| sequence.tid == metadata.tid)
        {
            if dispatches.len() >= metadata.output_limits.max_dispatches as usize {
                return Err(err("dispatch_set_bound"));
            }
            if let Some(cached) = slot.dispatch_specs.get(did) {
                dispatches.insert(did.clone(), cached.clone());
                continue;
            }
            let record = self
                .io(self.ports.dispatch.get(did))
                .await?
                .ok_or_else(|| err("dispatch_missing"))?;
            if record.view.spec.tid != metadata.tid || record.view.spec.sid != spec.sid {
                return Err(err("dispatch_task_identity"));
            }
            // Immutable definitions survive yielding. At most one missing
            // Dispatch read per visit, even with the maximum active set.
            slot.dispatch_specs.insert(did.clone(), record.view.spec);
            return Err(err("processor_preparing"));
        }
        let display = self.display(&base, &metadata).await?;
        let task = base
            .tasks
            .iter_mut()
            .find(|t| t.tid == metadata.tid)
            .ok_or_else(|| err("runner_task_missing"))?;
        if task.steps >= spec.limits.max_task_steps {
            let candidate = finalize_independently(
                &base,
                &metadata,
                TerminalStatus::Failed,
                "TaskStepLimitExceeded",
                now,
                slot.completed.as_ref(),
            )
            .map_err(store_error)?;
            slot.prepared = Some(Prepared {
                candidate,
                finalizing: true,
                id: CommitId(uuid::Uuid::new_v4().to_string()),
                purpose: self.ordinary_purpose(slot)?,
            });
            return Ok(());
        }
        task.steps = task
            .steps
            .checked_add(1)
            .ok_or_else(|| err("task_steps_exhausted"))?;
        let attempt = self.begin_evaluation(slot, &base, &inputs, &spec, now, initialize).await?;
        let config = if let Some(config) = &slot.mode_config {
            config.clone()
        } else {
            // Inline Direct A2A configurations require no directory or database lookup.
            let raw = self.references.resolve(&spec.mode_envelope.payload, PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES)
                .await.map_err(store_error)?;
            match self.ports.runner.validate_session(&spec, &raw) {
                Ok(config) => {
                    slot.mode_config = Some(config.clone());
                    config
                }
                Err(error) => {
                    self.reject_risk(slot, attempt, &runner_error(error).code, false);
                    return Err(err("risk_outcome_pending"));
                }
            }
        };
        let context = EvaluationContext {
            spec: &spec,
            config: config.as_ref(),
            downstream_generation: base.downstream_generation,
            tid: &metadata.tid,
            now,
        };
        let state = if initialize {
            self.ports.runner.init(&context).await
        } else {
            base.runner_state
                .as_ref()
                .filter(|s| s.tid == metadata.tid)
                .map(|s| s.state.clone())
                .ok_or_else(|| RunnerError::CorruptState {
                    code: "runner_state_missing".into(),
                })
        };
        let result = match state {
            Ok(state) => self.ports.runner.reduce(&context, &state, &inputs).await,
            Err(error) => Err(error),
        };
        let decision = match result {
            Ok(decision) => decision,
            Err(error) => {
                let code = runner_error(error).code;
                self.reject_risk(slot, attempt, &code, false);
                return Err(err("risk_outcome_pending"));
            }
        };
        // Freeze before any asynchronous reference validation/Blob externalization.
        slot.evaluation = Some(FrozenDecision {
            attempt,
            base,
            metadata,
            decision,
            now,
            dispatches,
            display,
        });
        Ok(())
    }
    async fn finish_evaluation(&self, slot: &mut Slot) -> Result<(Checkpoint, bool), RuntimeError> {
        if let Some(prepared) = slot.prepared.take() {
            return Ok((prepared.candidate, prepared.finalizing));
        }
        let frozen = slot
            .evaluation
            .as_mut()
            .ok_or_else(|| err("evaluation_missing"))?;
        if frozen.decision.actions.len() <= frozen.metadata.output_limits.max_actions as usize {
            for action in &mut frozen.decision.actions {
                let payload = match action {
                    ActionIntent::Show { update, .. } => match update {
                        ArtifactUpdate::Upsert { content } => Some(content),
                        ArtifactUpdate::AppendText { delta } => Some(delta),
                    },
                    ActionIntent::StartDispatch(spec) => Some(&mut spec.request),
                    ActionIntent::ContinueDispatch { input, .. } => Some(&mut input.payload),
                    ActionIntent::FinishTask { result, .. } => Some(result),
                    _ => None,
                };
                if let Some(payload) = payload
                    && matches!(payload, Payload::Ref(_))
                {
                    *payload = Payload::Inline(
                        self.references
                            .resolve_output(payload)
                            .await
                            .map_err(store_error)?,
                    );
                    // Persist each resolved action in the frozen local Decision;
                    // a slow later reference cannot restart earlier reads/Runner.
                    return Err(err("processor_preparing"));
                }
            }
        }
        let decision = frozen.decision.clone();
        let task = frozen
            .base
            .tasks
            .iter()
            .find(|t| t.tid == frozen.metadata.tid)
            .ok_or_else(|| err("active_task_missing"))?;
        let validation = validate_decision(
            DecisionContext {
                spec: slot
                    .spec
                    .as_ref()
                    .ok_or_else(|| err("session_spec_missing"))?,
                task,
                display: &frozen.display,
                dispatches: &frozen.dispatches,
                registered_deadlines: &frozen.base.deadlines,
                deadline_history: &frozen.base.deadline_history,
            },
            decision,
            self.ports.projection.as_ref(),
        );
        let (candidate, finalizing) = match validation {
            Ok(decision) => {
                let finalizing = decision.finish.is_some();
                (
                    apply_decision(
                        &frozen.base,
                        &frozen.metadata,
                        decision,
                        frozen.now,
                        slot.completed.as_ref(),
                    )
                    .map_err(store_error)?,
                    finalizing,
                )
            }
            Err(DecisionFailure::OutputQuota) => (
                finalize_independently(
                    &frozen.base,
                    &frozen.metadata,
                    TerminalStatus::Failed,
                    "OutputQuotaExceeded",
                    frozen.now,
                    slot.completed.as_ref(),
                )
                .map_err(store_error)?,
                true,
            ),
            Err(DecisionFailure::Invalid(code)) => {
                let attempt = frozen.attempt.clone();
                // The acquired checkpoint and its completed effects remain the
                // authority. Never finalize the rejected speculative state.
                let safe = finalize_independently(
                    &slot.acquired.envelope.checkpoint,
                    &frozen.metadata,
                    TerminalStatus::Rejected,
                    "SessionQuarantined",
                    frozen.now,
                    slot.completed.as_ref(),
                )
                .is_ok();
                self.reject_risk(slot, attempt, code, safe);
                return Err(err("risk_outcome_pending"));
            }
            Err(DecisionFailure::UnsafeState(code)) => {
                let attempt = frozen.attempt.clone();
                self.reject_risk(slot, attempt, code, false);
                return Err(err("risk_outcome_pending"));
            }
        };
        let attempt = frozen.attempt.clone();
        let digest = bcs_dist_encoding::digest_json(
            &serde_json::to_value(&frozen.decision).map_err(|_| err("decision_encoding"))?,
        );
        slot.risk_outcome = Some((
            attempt,
            AttemptOutcome::Succeeded {
                decision_digest: digest,
            },
        ));
        self.finish_risk(slot).await?;
        // Historical success is evidence, not permission for new business.
        self.ordinary_purpose(slot)?;
        slot.evaluation = None;
        Ok((candidate, finalizing))
    }
}
fn runner_error(error: RunnerError) -> RuntimeError {
    err(match error {
        RunnerError::CorruptState { .. } => "runner_corrupt_state",
        RunnerError::UnsupportedFormat { .. } => "runner_unsupported_format",
        RunnerError::InvalidWorkflow { .. } => "runner_invalid_workflow",
    })
}
