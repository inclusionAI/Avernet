use super::*;
use bcs_service_api::{StateMachineLoopMetric, StateMachineLoopOutcome};

impl CollaborationRuntime {
    /// Read only the already validated plan. Observation adds no storage read,
    /// transaction, durable checkpoint, or dependency on today's Definition.
    pub(super) fn observe_loop_node(
        &self, compiled: &CompiledStateMachine, run: &StateMachineRun, node_id: &str,
        attempt: i32, transition: &'static str, outcome: Option<&str>,
    ) {
        let Some(plan) = compiled.execution_plan.as_ref() else { return; };
        let Some(meta) = plan.node_metadata.get(node_id) else { return; };
        let Some(execution) = crate::definition::node_execution_metadata(meta) else { return; };
        log_loop_execution(run, node_id, attempt, &execution, transition, outcome);
        let Some(hook) = self.loop_instrumentation.as_ref() else { return; };
        if transition == "started" && meta.is_loop_entry && attempt == 0 {
            hook.record(StateMachineLoopMetric::IterationStarted);
        }
        if transition == "completed" && meta.is_loop_result {
            if let Some((outcome, route)) = outcome.and_then(|outcome| plan.edge_metadata.iter()
                .find(|edge| edge.source_execution_node_id == node_id && edge.outcome == outcome)
                .and_then(|edge| edge.loop_route.as_ref()).map(|route| (outcome, route.kind))) {
                // One result transition, even when it activates several targets.
                hook.record(StateMachineLoopMetric::IterationCompleted {
                    route, outcome: StateMachineLoopOutcome::from_outcome(outcome),
                });
            }
        }
    }
}

pub(super) fn log_loop_execution(
    run: &StateMachineRun, node_id: &str, attempt: i32,
    execution: &bcs_domain::StateMachineNodeExecutionMetadata,
    transition: &'static str, outcome: Option<&str>,
) {
    info!(target: "bcs_observation", run_id = %run.run_id, session_id = %run.session_id,
        loop_id = %execution.loop_id, loop_iteration = execution.iteration,
        loop_max_iterations = execution.max_iterations,
        definition_node_id = %execution.definition_node_id, execution_node_id = %node_id,
        attempt, selected_outcome = outcome.unwrap_or(""), transition,
        "state_machine: loop node transition");
}
