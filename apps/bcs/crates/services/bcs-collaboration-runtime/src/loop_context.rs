//! Shared server-owned Loop context for Bot prompts and HumanInput views/events.

use bcs_domain::{
    CompiledArtifactProjection, LoopContext, PreviousLoopResult, StateMachineExecutionPlan,
    StateMachineNodeRun, StateMachineNodeStatus,
};
use bcs_service_api::{CollaborationRuntimeError, ServiceError};

pub fn build_loop_context(
    plan: &StateMachineExecutionPlan,
    run_id: &str,
    node_id: &str,
    previous: Option<&StateMachineNodeRun>,
) -> Result<Option<LoopContext>, CollaborationRuntimeError> {
    let meta = plan.node_metadata.get(node_id).ok_or_else(|| invalid("missing node mapping"))?;
    if !meta.is_loop_entry {
        return Ok(None);
    }
    let loop_id = meta.loop_id.clone().ok_or_else(|| invalid("missing Loop ID"))?;
    let iteration = meta.iteration.ok_or_else(|| invalid("missing iteration"))?;
    let max_iterations = meta.max_iterations.ok_or_else(|| invalid("missing max iterations"))?;
    if iteration == 0 || iteration > max_iterations {
        return Err(invalid("invalid iteration"));
    }
    let previous_result = if iteration == 1 {
        if meta.previous_result_node_id.is_some() || previous.is_some() {
            return Err(invalid("first iteration has a previous result"));
        }
        None
    } else {
        let expected_id = meta.previous_result_node_id.as_ref().ok_or_else(|| invalid("missing previous result mapping"))?;
        let source = previous.ok_or_else(|| invalid("missing previous result Node Run"))?;
        let source_meta = plan.node_metadata.get(expected_id).ok_or_else(|| invalid("missing result node mapping"))?;
        if source.run_id != run_id || &source.node_id != expected_id || source.status != StateMachineNodeStatus::Completed
            || !source_meta.is_loop_result || source_meta.loop_id.as_ref() != Some(&loop_id)
            || source_meta.iteration != Some(iteration - 1)
        {
            return Err(invalid("previous result identity or completion mismatch"));
        }
        let outcome = source.outcome.as_ref().filter(|outcome| !outcome.trim().is_empty())
            .ok_or_else(|| invalid("previous result has no outcome"))?;
        if !plan.edge_metadata.iter().any(|edge| {
            &edge.source_execution_node_id == expected_id && edge.target_execution_node_id == node_id
                && &edge.outcome == outcome && edge.artifact_projection == CompiledArtifactProjection::ControlOnly
        }) {
            return Err(invalid("previous result did not select this iteration"));
        }
        Some(PreviousLoopResult {
            iteration: iteration - 1,
            result_node_id: source_meta.definition_node_id.clone(),
            execution_node_id: expected_id.clone(),
            outcome: outcome.clone(),
            output: source.artifact_text.clone().ok_or_else(|| invalid("previous result has no artifact"))?,
            completed_at: source.completed_at.ok_or_else(|| invalid("previous result has no completion time"))?,
        })
    };
    Ok(Some(LoopContext { loop_id, iteration, max_iterations, previous_result }))
}

pub fn render_loop_context(context: &LoopContext) -> String {
    let previous = match &context.previous_result {
        Some(previous) => format!(
            "iteration: {}\nresult_node_id: {}\noutcome: {}\ncompleted_at: {}\noutput:\n{}",
            previous.iteration, previous.result_node_id, previous.outcome, previous.completed_at, previous.output
        ),
        None => "(none - this is the first iteration)".into(),
    };
    format!(
        "[Loop Context]\nloop_id: {}\niteration: {}\nmax_iterations: {}\n\n[Previous Iteration Result]\n{}",
        context.loop_id, context.iteration, context.max_iterations, previous
    )
}

pub fn projects_upstream_artifact(
    plan: Option<&StateMachineExecutionPlan>,
    target: &str,
    source: &StateMachineNodeRun,
) -> bool {
    let Some(plan) = plan else { return true; };
    source.status == StateMachineNodeStatus::Completed && plan.edge_metadata.iter().any(|edge| {
        edge.source_execution_node_id == source.node_id && edge.target_execution_node_id == target
            && source.outcome.as_ref() == Some(&edge.outcome)
            && edge.artifact_projection == CompiledArtifactProjection::Artifact
    })
}

fn invalid(message: &str) -> CollaborationRuntimeError {
    CollaborationRuntimeError::Internal(ServiceError::InternalError(format!("invalid Loop context: {message}")))
}
