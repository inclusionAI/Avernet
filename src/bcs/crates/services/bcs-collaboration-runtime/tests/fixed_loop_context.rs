use bcs_collaboration_runtime::fixed_loop::compile_authoring_definition;
use bcs_collaboration_runtime::loop_context::{build_loop_context, projects_upstream_artifact, render_loop_context};
use bcs_config_api::FixedLoopLimits;
use bcs_domain::{StateMachineExecutionPlan, StateMachineNodeRun, StateMachineNodeStatus};
use serde_json::json;

fn plan() -> StateMachineExecutionPlan {
    compile_authoring_definition(
        serde_yaml::from_str(include_str!("fixtures/fixed_loop.yaml")).unwrap(),
        &FixedLoopLimits::default(),
    ).unwrap().plan.unwrap()
}

fn entry(plan: &StateMachineExecutionPlan, iteration: u32) -> String {
    plan.node_metadata.values().find(|meta| meta.is_loop_entry && meta.iteration == Some(iteration))
        .unwrap().execution_node_id.clone()
}

fn previous(plan: &StateMachineExecutionPlan, iteration: u32) -> StateMachineNodeRun {
    let meta = &plan.node_metadata[&entry(plan, iteration)];
    serde_json::from_value(json!({
        "run_id": "run-1", "node_id": meta.previous_result_node_id,
        "status": "completed", "attempt": 2, "outcome": "continue",
        "artifact_text": "previous result\nwith another line", "completed_at": 1234
    })).unwrap()
}

#[test]
fn entry_context_has_stable_prompt_shape_and_uses_only_designated_result() {
    let plan = plan();
    let first = build_loop_context(&plan, "run-1", &entry(&plan, 1), None).unwrap().unwrap();
    assert_eq!(render_loop_context(&first), "[Loop Context]\nloop_id: rounds\niteration: 1\nmax_iterations: 3\n\n[Previous Iteration Result]\n(none - this is the first iteration)");
    for iteration in 2..=3 {
        let source = previous(&plan, iteration);
        let context = build_loop_context(&plan, "run-1", &entry(&plan, iteration), Some(&source)).unwrap().unwrap();
        assert_eq!(context.previous_result.as_ref().unwrap().execution_node_id, source.node_id);
        assert_eq!(render_loop_context(&context), format!("[Loop Context]\nloop_id: rounds\niteration: {iteration}\nmax_iterations: 3\n\n[Previous Iteration Result]\niteration: {}\nresult_node_id: decision\noutcome: continue\ncompleted_at: 1234\noutput:\nprevious result\nwith another line", iteration - 1));
    }
    for meta in plan.node_metadata.values().filter(|meta| !meta.is_loop_entry) {
        assert!(build_loop_context(&plan, "run-1", &meta.execution_node_id, None).unwrap().is_none());
    }
}

#[test]
fn invalid_previous_result_is_rejected_instead_of_producing_partial_context() {
    let plan = plan();
    let second = entry(&plan, 2);
    assert!(build_loop_context(&plan, "run-1", &second, None).is_err());
    for mutation in 0..9 {
        let mut source = previous(&plan, 2);
        match mutation {
            0 => source.run_id = "other-run".into(),
            1 => source.node_id = entry(&plan, 1),
            2 => source.status = StateMachineNodeStatus::Running,
            3 => source.status = StateMachineNodeStatus::Skipped,
            4 => source.outcome = None,
            5 => source.outcome = Some("  ".into()),
            6 => source.outcome = Some("completed".into()),
            7 => source.artifact_text = None,
            _ => source.completed_at = None,
        }
        assert!(build_loop_context(&plan, "run-1", &second, Some(&source)).is_err(), "mutation {mutation}");
    }
    let source = previous(&plan, 3);
    assert!(build_loop_context(&plan, "run-1", &second, Some(&source)).is_err());
    assert!(build_loop_context(&plan, "run-1", &entry(&plan, 1), Some(&source)).is_err());
    let mut source = previous(&plan, 2);
    source.artifact_text = Some(String::new());
    assert!(build_loop_context(&plan, "run-1", &second, Some(&source)).is_ok(), "an explicitly persisted empty artifact is valid");
}

#[test]
fn projection_filters_control_only_and_unselected_edges_without_changing_v1() {
    let plan = plan();
    let mut source = previous(&plan, 2);
    assert!(!projects_upstream_artifact(Some(&plan), &entry(&plan, 2), &source));
    assert!(!projects_upstream_artifact(Some(&plan), "publish", &source));
    source.outcome = Some("completed".into());
    assert!(projects_upstream_artifact(Some(&plan), "publish", &source));
    assert!(!projects_upstream_artifact(Some(&plan), "manual_finish", &source));
    source.status = StateMachineNodeStatus::Skipped;
    assert!(!projects_upstream_artifact(Some(&plan), "publish", &source));
    assert!(projects_upstream_artifact(None, "publish", &source));
}
