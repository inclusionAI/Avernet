use super::encoding::encoded_size;
use bcs_dist_model::*;
use bcs_service_api::{
    core::{dist_projection::*, dist_runner::*},
    port::repo::distributed::session::*,
};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, PartialEq, Eq)]
pub(super) enum DecisionFailure {
    OutputQuota,
    Invalid(&'static str),
    UnsafeState(&'static str),
}
pub(super) struct DecisionContext<'a> {
    pub spec: &'a SessionSpec,
    pub task: &'a TaskProgress,
    pub display: &'a DisplaySnapshot,
    pub dispatches: &'a BTreeMap<Did, DispatchSpec>,
    pub deadline_history: &'a BTreeMap<DeadlineId, Deadline>,
    pub registered_deadlines: &'a [Deadline],
}
pub(super) struct ValidatedDecision {
    pub next_state: RunnerState,
    pub display: DisplaySnapshot,
    pub usage: OutputUsage,
    pub events: Vec<OutputBody>,
    pub effects: Vec<EffectKind>,
    pub finish: Option<(TerminalStatus, Payload)>,
    pub deadlines: Vec<Deadline>,
    pub status: Option<(TaskStatus, Vec<Interaction>)>,
}

/// Build only local values. No state, input coverage or effect becomes visible
/// until every action and the complete display candidate has passed validation.
pub(super) fn validate_decision(
    context: DecisionContext<'_>,
    decision: Decision,
    projection: &dyn OutputProjectionCoreService,
) -> Result<ValidatedDecision, DecisionFailure> {
    let limits = &context.task.output_limits;
    projection
        .validate_limits(limits)
        .map_err(projection_error)?;
    if context.task.phase != TaskPhase::Running || context.task.finalization.is_some() {
        return Err(DecisionFailure::UnsafeState("runner_task_phase"));
    }
    if decision.actions.len() > limits.max_actions as usize {
        return Err(DecisionFailure::OutputQuota);
    }
    if encoded_size(&decision.next_state, limits.max_runner_state_bytes as usize).is_none() {
        return Err(DecisionFailure::UnsafeState("runner_state_bound"));
    }
    if decision.next_deadlines.len() > limits.max_actions as usize {
        return Err(DecisionFailure::Invalid("deadline_count"));
    }
    let mut changes = Vec::new();
    let mut events = Vec::new();
    let mut effects = Vec::new();
    let mut status_change = None;
    let mut finish = None;
    let mut created = BTreeSet::new();
    for action in decision.actions {
        if finish.is_some() {
            return Err(DecisionFailure::Invalid("action_after_finish"));
        }
        match action {
            ActionIntent::Show {
                tid,
                node,
                artifact_id,
                class,
                update,
            } => {
                task_identity(&context, &tid)?;
                changes.push(ArtifactChange {
                    node: node.clone(),
                    artifact_id: artifact_id.clone(),
                    class,
                    update: update.clone(),
                });
                events.push(OutputBody::Artifact {
                    node,
                    artifact_id,
                    class,
                    update,
                });
            }
            ActionIntent::SetStatus {
                tid,
                status,
                interactions,
            } => {
                task_identity(&context, &tid)?;
                if !matches!(
                    status,
                    TaskStatus::Working | TaskStatus::InputRequired | TaskStatus::AuthRequired
                ) {
                    return Err(DecisionFailure::Invalid("terminal_set_status"));
                }
                if status_change.is_some() {
                    return Err(DecisionFailure::Invalid("multiple_status_actions"));
                }
                if interactions.len() > 64 || encoded_size(&interactions, 64 * 1024).is_none() {
                    return Err(DecisionFailure::Invalid("interaction_bound"));
                }
                let mut ids = BTreeSet::new();
                for interaction in &interactions {
                    identity(&interaction.id.0, limits)?;
                    if !ids.insert(&interaction.id) {
                        return Err(DecisionFailure::Invalid("interaction_identity"));
                    }
                    dispatch(&context, &interaction.did, &created)?;
                }
                events.push(OutputBody::Status {
                    status,
                    interactions: interactions.clone(),
                });
                status_change = Some((status, interactions));
            }
            ActionIntent::StartDispatch(spec) => {
                task_identity(&context, &spec.tid)?;
                identity(&spec.did.0, limits)?;
                if spec.schema_version != 1
                    || spec.sid != context.spec.sid
                    || !spec.did.0.starts_with(&format!("{}.", context.task.tid.0))
                    || spec.attempt == 0
                    || !created.insert(spec.did.clone())
                    || context.dispatches.contains_key(&spec.did)
                {
                    return Err(DecisionFailure::Invalid("dispatch_identity"));
                }
                if context.dispatches.len() + created.len() > limits.max_dispatches as usize {
                    return Err(DecisionFailure::Invalid("dispatch_count"));
                }
                if !context.spec.participants.iter().any(|binding| {
                    binding.node == spec.node
                        && binding.target == spec.target
                        && binding.pool == spec.pool
                        && binding.capabilities == spec.capabilities
                }) {
                    return Err(DecisionFailure::Invalid("dispatch_binding"));
                }
                payload(&spec.request, limits)?;
                effects.push(EffectKind::CreateDispatch { spec });
            }
            ActionIntent::CancelDispatch { did, intent } => {
                dispatch(&context, &did, &created)?;
                identity(&intent.request_id.0, limits)?;
                if let Some(reason) = &intent.reason {
                    identity(reason, limits)?;
                }
                if encoded_size(&intent, 4096).is_none() {
                    return Err(DecisionFailure::Invalid("cancel_bound"));
                }
                effects.push(EffectKind::RequestCancel { did, intent });
            }
            ActionIntent::ContinueDispatch { did, input } => {
                dispatch(&context, &did, &created)?;
                identity(&input.input_id.0, limits)?;
                identity(&input.business_digest.0, limits)?;
                if input.seq.0 == 0 {
                    return Err(DecisionFailure::Invalid("dispatch_input_sequence"));
                }
                payload(&input.payload, limits)?;
                effects.push(EffectKind::PushInput { did, input });
            }
            ActionIntent::FinishTask {
                tid,
                status,
                result,
            } => {
                task_identity(&context, &tid)?;
                payload(&result, limits).map_err(|_| DecisionFailure::OutputQuota)?;
                finish = Some((status, result));
            }
        }
    }
    let mut deadline_ids = BTreeSet::new();
    for deadline in &decision.next_deadlines {
        identity(&deadline.id.0, limits)?;
        if context
            .deadline_history
            .get(&deadline.id)
            .is_some_and(|prior| prior != deadline)
            || !deadline_ids.insert(&deadline.id)
            || context
                .registered_deadlines
                .iter()
                .any(|prior| prior.id == deadline.id && prior != deadline)
        {
            return Err(DecisionFailure::Invalid("deadline_identity_conflict"));
        }
        match &deadline.kind {
            DeadlineKind::Task { tid } if *tid == context.task.tid => {}
            DeadlineKind::Dispatch { tid, did } if *tid == context.task.tid => {
                dispatch(&context, did, &created)?;
            }
            _ => return Err(DecisionFailure::Invalid("deadline_scope")),
        }
    }
    let new_occurrences = decision
        .next_deadlines
        .iter()
        .filter(|d| !context.deadline_history.contains_key(&d.id))
        .count();
    if context
        .deadline_history
        .len()
        .saturating_add(new_occurrences)
        > 256
    {
        return Err(DecisionFailure::Invalid("deadline_history_capacity"));
    }
    let output = projection
        .project_candidate(context.display, context.task.output_usage, &changes, limits)
        .map_err(projection_error)?;
    Ok(ValidatedDecision {
        next_state: decision.next_state,
        display: output.snapshot,
        usage: output.usage,
        events,
        effects,
        finish,
        deadlines: decision.next_deadlines,
        status: status_change,
    })
}

fn task_identity(context: &DecisionContext<'_>, tid: &Tid) -> Result<(), DecisionFailure> {
    if *tid != context.task.tid {
        return Err(DecisionFailure::Invalid("action_task_scope"));
    }
    Ok(())
}
fn identity(value: &str, limits: &OutputLimits) -> Result<(), DecisionFailure> {
    if value.is_empty() || encoded_size(&value, limits.max_identity_bytes as usize).is_none() {
        return Err(DecisionFailure::Invalid("action_identity"));
    }
    Ok(())
}
fn dispatch(
    context: &DecisionContext<'_>,
    did: &Did,
    created: &BTreeSet<Did>,
) -> Result<(), DecisionFailure> {
    if !created.contains(did)
        && !context
            .dispatches
            .get(did)
            .is_some_and(|spec| spec.tid == context.task.tid && spec.sid == context.spec.sid)
    {
        return Err(DecisionFailure::Invalid("action_dispatch_scope"));
    }
    Ok(())
}
fn payload(payload: &Payload, limits: &OutputLimits) -> Result<(), DecisionFailure> {
    let valid = match payload {
        Payload::Inline(value) => encoded_size(value, limits.max_snapshot_bytes as usize).is_some(),
        Payload::Ref(reference) => {
            encoded_size(reference, limits.max_reference_metadata_bytes as usize).is_some()
        }
    };
    if !valid {
        return Err(DecisionFailure::Invalid("action_payload_bound"));
    }
    Ok(())
}
fn projection_error(error: ProjectionError) -> DecisionFailure {
    match error {
        ProjectionError::OutputQuotaExceeded { .. } => DecisionFailure::OutputQuota,
        ProjectionError::InvalidArtifact { code } => DecisionFailure::Invalid(code),
        ProjectionError::UnresolvedReference => DecisionFailure::Invalid("unresolved_output"),
        _ => DecisionFailure::UnsafeState("persisted_output_invalid"),
    }
}
