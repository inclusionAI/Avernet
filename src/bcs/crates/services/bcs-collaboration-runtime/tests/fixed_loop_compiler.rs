use std::collections::BTreeSet;

use bcs_config_api::FixedLoopLimits;
use bcs_domain::{
    CollaborationDefinition, CollaborationRuntimeDefinition, CompiledArtifactProjection,
    StateMachineExecutionPlan, StateMachineGraphMode, StateMachineLoopRouteKind,
};
use bcs_collaboration_runtime::fixed_loop::{
    compile_authoring_definition, execution_plan_content_hash, FIXED_LOOP_COMPILER_VERSION,
};
use bcs_collaboration_runtime::validation::validate_authoring_definition_yaml_with_limits;
use bcs_collaboration_runtime::{validate_authoring_definition_yaml, validate_definition};
use bcs_service_api::ValidateCollaborationDefinitionYamlCommand;
use serde_json::{json, Value};

const YAML: &str = include_str!("fixtures/fixed_loop.yaml");

fn yaml_block(section: &str) -> &str {
    section.split_once("```yaml\n").unwrap().1.split_once("\n```").unwrap().0
}

#[test]
fn spec_and_cli_fixed_loop_examples_pass_authoring_validation() {
    let spec = include_str!("../../../../docs/superpowers/specs/2026-09-02-bcs-fixed-loop-state-machine-design.md");
    let full = yaml_block(spec.split_once("### 7.3 完整示例").unwrap().1);
    let fragment = yaml_block(spec.split_once("### 7.5 无 Judge 的固定次数示例").unwrap().1);
    let mut fixed: serde_yaml::Value = serde_yaml::from_str(full).unwrap();
    let replacement: serde_yaml::Value = serde_yaml::from_str(fragment).unwrap();
    fixed["runtime"]["state_machine"]["nodes"]["rounds"] = replacement["rounds"].clone();
    let cli = include_str!("../../../tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md");
    let cli = yaml_block(cli.split_once("## Fixed Loop validation preview").unwrap().1);
    for yaml in [full.to_string(), serde_yaml::to_string(&fixed).unwrap(), cli.to_string()] {
        let result = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand { definition_yaml: yaml, judge_available: true });
        assert!(result.valid, "{:?}", result.errors);
    }
}

fn fixture() -> Value {
    let mut definition: CollaborationDefinition = serde_yaml::from_str(YAML).unwrap();
    definition.id = "fixed-loop-fixture".into();
    definition.version = 7;
    serde_json::to_value(definition).unwrap()
}

#[test]
fn seeded_writing_review_loop_routes_approval_to_polish_and_exhaustion_to_rewrite() {
    for yaml in [
        include_str!("../../../../seeds/collaboration-templates/zh-CN/write-review-loop.yaml"),
        include_str!("../../../../seeds/collaboration-templates/en-US/write-review-loop.yaml"),
    ] {
        let validation = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
            definition_yaml: yaml.into(), judge_available: true,
        });
        assert!(validation.valid, "{:?}", validation.errors);
        assert!(validation.warnings.iter().any(|warning| warning.code == "VALIDATION_ONLY_FEATURE"));
        let definition: CollaborationDefinition = serde_yaml::from_str(yaml).unwrap();
        assert!(definition.uses_judge());
        let plan = compile(serde_json::to_value(definition).unwrap());
        assert_eq!(plan.state_machine.nodes.len(), 9);
        assert!(plan.state_machine.nodes["finalize"].final_output);
        assert_eq!(plan.state_machine.nodes.values().filter(|node| node.final_output).count(), 1);
        for source in ["polish", "rewrite"] {
            assert!(plan.edge_metadata.iter().any(|edge| edge.source_execution_node_id == source
                && edge.outcome == "complete" && edge.target_execution_node_id == "finalize"));
        }
        for iteration in 1..=3 {
            let result = result_id(&plan, iteration);
            let approved = plan.edge_metadata.iter().find(|edge| edge.source_execution_node_id == result && edge.outcome == "approved").unwrap();
            assert_eq!(approved.loop_route.as_ref().unwrap().kind, StateMachineLoopRouteKind::Break);
            assert_eq!(approved.target_execution_node_id, "polish");
            assert_eq!(approved.artifact_projection, CompiledArtifactProjection::Artifact);
            let edge = plan.edge_metadata.iter().find(|edge| edge.source_execution_node_id == result && edge.outcome == "revise").unwrap();
            let route = edge.loop_route.as_ref().unwrap();
            if iteration < 3 {
                assert_eq!(route.kind, StateMachineLoopRouteKind::Continue);
                let next = &plan.node_metadata[&edge.target_execution_node_id];
                assert_eq!(next.iteration, Some(iteration + 1));
                assert_eq!(next.previous_result_node_id.as_deref(), Some(result));
            } else {
                assert_eq!(route.kind, StateMachineLoopRouteKind::Exhausted);
                assert_eq!(route.logical_outcome, "exhausted");
                assert_eq!(edge.target_execution_node_id, "rewrite");
                assert_eq!(edge.artifact_projection, CompiledArtifactProjection::Artifact);
            }
        }
    }
}

fn compile(value: Value) -> StateMachineExecutionPlan {
    let definition = serde_json::from_value(value).unwrap();
    compile_authoring_definition(definition, &FixedLoopLimits::default())
        .unwrap_or_else(|error| panic!("{}: {}", error.path, error.message)).plan.unwrap()
}

fn body(value: &mut Value) -> &mut Value {
    &mut value["runtime"]["state_machine"]["nodes"]["rounds"]["loop"]
}

fn result_id(plan: &StateMachineExecutionPlan, iteration: u32) -> &str {
    plan.node_metadata.values().find(|meta| meta.is_loop_result && meta.iteration == Some(iteration))
        .unwrap().execution_node_id.as_str()
}

#[test]
fn unrolls_routes_without_rewriting_real_outcomes() {
    let plan = compile(fixture());
    assert_eq!(plan.compiler_version, FIXED_LOOP_COMPILER_VERSION);
    assert_eq!(plan.state_machine.graph_mode, StateMachineGraphMode::Acyclic);
    assert_eq!(plan.state_machine.nodes.len(), 9);
    assert!(!plan.state_machine.nodes.contains_key("rounds"));
    for iteration in 1..=3 {
        let result = result_id(&plan, iteration);
        let edges: Vec<_> = plan.edge_metadata.iter().filter(|edge| edge.source_execution_node_id == result).collect();
        assert_eq!(edges.len(), 2);
        let broken = edges.iter().find(|edge| edge.outcome == "completed").unwrap();
        assert_eq!(broken.target_execution_node_id, "publish");
        assert_eq!(broken.loop_route.as_ref().unwrap().kind, StateMachineLoopRouteKind::Break);
        assert_eq!(broken.artifact_projection, CompiledArtifactProjection::Artifact);
        let continued = edges.iter().find(|edge| edge.outcome == "continue").unwrap();
        let route = continued.loop_route.as_ref().unwrap();
        if iteration < 3 {
            assert_eq!(route.kind, StateMachineLoopRouteKind::Continue);
            assert_eq!(route.logical_outcome, "continue");
            assert_eq!(continued.artifact_projection, CompiledArtifactProjection::ControlOnly);
            let entry = &plan.node_metadata[&continued.target_execution_node_id];
            assert_eq!(entry.iteration, Some(iteration + 1));
            assert_eq!(entry.previous_result_node_id.as_deref(), Some(result));
        } else {
            assert_eq!(route.kind, StateMachineLoopRouteKind::Exhausted);
            assert_eq!(route.logical_outcome, "exhausted");
            assert_eq!(continued.target_execution_node_id, "manual_finish");
            assert_eq!(continued.artifact_projection, CompiledArtifactProjection::Artifact);
        }
    }
    for edge in &plan.edge_metadata {
        let source = &plan.node_metadata[&edge.source_execution_node_id];
        assert_eq!(edge.loop_route.is_some(), source.is_loop_result);
    }
}

#[test]
fn no_judge_and_empty_break_executes_to_exhausted_including_one_iteration() {
    for iterations in [1, 3] {
        let mut value = fixture();
        body(&mut value)["continue_outcomes"] = json!(["complete"]);
        body(&mut value)["break_outcomes"] = json!([]);
        body(&mut value)["max_iterations"] = json!(iterations);
        body(&mut value)["nodes"]["decision"].as_object_mut().unwrap().remove("judge");
        value["runtime"]["state_machine"]["nodes"]["rounds"]["transitions"].as_object_mut().unwrap().remove("completed");
        let plan = compile(value);
        let last = result_id(&plan, iterations);
        let last_edge = plan.edge_metadata.iter().find(|edge| edge.source_execution_node_id == last).unwrap();
        assert_eq!(last_edge.outcome, "complete");
        assert_eq!(last_edge.target_execution_node_id, "manual_finish");
        assert_eq!(last_edge.loop_route.as_ref().unwrap().kind, StateMachineLoopRouteKind::Exhausted);
        assert!(!plan.edge_metadata.iter().any(|edge| edge.loop_route.as_ref().is_some_and(|route| route.kind == StateMachineLoopRouteKind::Break)));
        assert_eq!(plan.node_metadata.values().filter(|meta| meta.is_loop_entry && meta.previous_result_node_id.is_none()).count(), 1);
    }
}

#[test]
fn multiple_continue_and_break_outcomes_keep_independent_routes() {
    let mut value = fixture();
    body(&mut value)["continue_outcomes"] = json!(["continue", "retry_discussion"]);
    body(&mut value)["break_outcomes"] = json!(["completed", "accepted"]);
    body(&mut value)["nodes"]["decision"]["judge"]["outcomes"] = json!(["continue", "retry_discussion", "completed", "accepted"]);
    value["runtime"]["state_machine"]["nodes"]["rounds"]["transitions"]["accepted"] = json!({"targets": ["publish"]});
    let plan = compile(value);
    let last = result_id(&plan, 3);
    let exhausted: Vec<_> = plan.edge_metadata.iter().filter(|edge| edge.source_execution_node_id == last
        && edge.loop_route.as_ref().is_some_and(|route| route.kind == StateMachineLoopRouteKind::Exhausted)).collect();
    assert_eq!(exhausted.iter().map(|edge| edge.outcome.as_str()).collect::<BTreeSet<_>>(), BTreeSet::from(["continue", "retry_discussion"]));
    assert!(exhausted.iter().all(|edge| edge.loop_route.as_ref().unwrap().logical_outcome == "exhausted"));
}

#[test]
fn judge_with_only_continue_outcomes_is_valid() {
    let mut value = fixture();
    body(&mut value)["continue_outcomes"] = json!(["continue", "completed"]);
    body(&mut value)["break_outcomes"] = json!([]);
    value["runtime"]["state_machine"]["nodes"]["rounds"]["transitions"].as_object_mut().unwrap().remove("completed");
    assert_eq!(compile(value).state_machine.nodes.len(), 9);
}

#[test]
fn body_fan_out_and_join_are_preserved_in_each_iteration() {
    let mut value = fixture();
    let extra = body(&mut value)["nodes"]["discussion"].clone();
    body(&mut value)["nodes"]["other"] = extra;
    body(&mut value)["nodes"]["discussion"]["transitions"]["complete"]["targets"] = json!(["other", "decision"]);
    let plan = compile(value);
    assert_eq!(plan.state_machine.nodes.len(), 12);
    for iteration in 1..=3 {
        let result = result_id(&plan, iteration);
        let predecessors: Vec<_> = plan.edge_metadata.iter().filter(|edge| edge.target_execution_node_id == result).collect();
        assert_eq!(predecessors.len(), 2);
        assert!(predecessors.iter().all(|edge| plan.node_metadata[&edge.source_execution_node_id].iteration == Some(iteration)));
    }
}

#[test]
fn sequential_loops_with_repeated_body_names_have_distinct_ids() {
    let mut value = fixture();
    let nodes = &mut value["runtime"]["state_machine"]["nodes"];
    nodes["second"] = nodes["rounds"].clone();
    nodes["rounds"]["transitions"]["completed"]["targets"] = json!(["second"]);
    nodes["rounds"]["transitions"]["exhausted"]["targets"] = json!(["second"]);
    let plan = compile(value);
    assert_eq!(plan.state_machine.nodes.len(), 15);
    assert_eq!(plan.node_metadata.values().filter(|meta| meta.loop_id.as_deref() == Some("second")).count(), 6);
    assert_eq!(plan.node_metadata.values().filter(|meta| meta.is_loop_entry && meta.iteration == Some(1)).count(), 2);
}

#[test]
fn compiler_is_byte_stable_and_snapshot_round_trip_preserves_metadata() {
    let plan = compile(fixture());
    let json = serde_json::to_vec(&plan).unwrap();
    assert_eq!(json, serde_json::to_vec(&compile(fixture())).unwrap());
    let loaded: StateMachineExecutionPlan = serde_json::from_slice(&json).unwrap();
    assert_eq!(execution_plan_content_hash(&plan).unwrap(), execution_plan_content_hash(&loaded).unwrap());
    assert_eq!(plan.node_metadata, loaded.node_metadata);
    assert_eq!(plan.edge_metadata, loaded.edge_metadata);
    let hash = execution_plan_content_hash(&plan).unwrap();
    assert_eq!(hash, "d1856c877549bfe194061fb13a62abfa9e1d97b3814846ba27dac27d07224fee");
    assert!(plan.node_metadata.values().filter(|meta| meta.loop_id.is_some()).all(|meta| meta.execution_node_id.starts_with("ln-") && meta.execution_node_id.len() == 35));
    let mut changed = fixture();
    changed["version"] = json!(8);
    assert_ne!(result_id(&plan, 1), result_id(&compile(changed), 1));
}

#[test]
fn rejects_invalid_loop_structure_and_outcomes_with_authoring_paths() {
    let cases = [
        ("/runtime/state_machine/nodes/rounds/loop/continue_outcomes", json!([])),
        ("/runtime/state_machine/nodes/rounds/loop/continue_outcomes", json!(["continue", "continue"])),
        ("/runtime/state_machine/nodes/rounds/loop/break_outcomes", json!(["continue"])),
        ("/runtime/state_machine/nodes/rounds/loop/exhausted_outcome", json!("continue")),
        ("/runtime/state_machine/nodes/rounds/loop/entry_node", json!("missing")),
        ("/runtime/state_machine/nodes/rounds/loop/result_node", json!("missing")),
        ("/runtime/state_machine/nodes/rounds/loop/nodes", json!({})),
        ("/runtime/state_machine/nodes/rounds/loop/nodes/discussion/transitions", json!({})),
        ("/runtime/state_machine/nodes/rounds/loop/nodes/discussion/transitions/complete/targets", json!(["publish"])),
        ("/runtime/state_machine/nodes/rounds/loop/nodes/discussion/transitions/complete/targets", json!(["discussion"])),
        ("/runtime/state_machine/nodes/rounds/loop/nodes/decision/final_output", json!(true)),
        ("/runtime/state_machine/nodes/rounds/loop/nodes/decision/transitions", json!({"continue": {"targets": ["discussion"]}})),
        ("/runtime/state_machine/nodes/rounds/transitions/exhausted/targets", json!([])),
        ("/runtime/state_machine/nodes/rounds/transitions/completed/targets", json!([])),
        ("/runtime/state_machine/nodes/rounds/transitions/exhausted/targets", json!(["unknown"])),
        ("/runtime/state_machine/nodes/rounds/loop/max_iterations", json!(0)),
        ("/runtime/state_machine/nodes/rounds/loop/max_iterations", json!(33)),
        ("/runtime/state_machine/nodes/rounds/display_name", json!(" ")),
        ("/runtime/state_machine/nodes/prepare/transitions/complete/targets", json!(["decision"])),
    ];
    for (pointer, bad) in cases {
        let mut value = fixture();
        *value.pointer_mut(pointer).unwrap() = bad;
        let definition = serde_json::from_value(value).unwrap();
        let error = compile_authoring_definition(definition, &FixedLoopLimits::default()).err().unwrap_or_else(|| panic!("accepted {pointer}"));
        assert_eq!(error.code, "INVALID_DEFINITION", "{pointer}");
        assert!(error.path.starts_with("$.runtime.state_machine"), "{pointer}: {:?}", error);
    }
}

#[test]
fn rejects_empty_continue_even_for_single_iteration_complete_break() {
    let mut value = fixture();
    body(&mut value)["max_iterations"] = json!(1);
    body(&mut value)["continue_outcomes"] = json!([]);
    body(&mut value)["break_outcomes"] = json!(["complete"]);
    body(&mut value)["nodes"]["decision"].as_object_mut().unwrap().remove("judge");
    let error = compile_authoring_definition(serde_json::from_value(value).unwrap(), &FixedLoopLimits::default()).err().unwrap();
    assert!(error.path.ends_with("loop.continue_outcomes"));
}

#[test]
fn node_and_serialized_plan_limits_are_enforced_at_boundaries() {
    let definition = serde_json::from_value(fixture()).unwrap();
    let plan = compile(fixture());
    let bytes = serde_json::to_vec(&plan).unwrap().len();
    let mut limits = FixedLoopLimits { max_compiled_state_machine_nodes: 9, max_compiled_state_machine_bytes: bytes, ..FixedLoopLimits::default() };
    assert!(compile_authoring_definition(definition, &limits).is_ok());
    for (nodes, byte_limit, body_limit) in [(8, bytes, 64), (9, bytes - 1, 64), (9, bytes, 1)] {
        limits.max_compiled_state_machine_nodes = nodes;
        limits.max_compiled_state_machine_bytes = byte_limit;
        limits.max_fixed_loop_body_nodes = body_limit;
        assert!(compile_authoring_definition(serde_json::from_value(fixture()).unwrap(), &limits).is_err());
    }
}

#[test]
fn authoring_preview_exposes_metadata_but_execution_is_still_disabled() {
    let validated = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand { definition_yaml: YAML.into(), judge_available: true });
    assert!(validated.valid, "{:?}", validated.errors);
    assert_eq!(validated.warnings[0].code, "VALIDATION_ONLY_FEATURE");
    let graph = validated.graph.unwrap();
    let descriptor = &graph.loops["rounds"];
    assert_eq!(descriptor.max_iterations, 3);
    assert_eq!(descriptor.entry_node_id, "discussion");
    assert_eq!(descriptor.result_node_id, "decision");
    assert_eq!(descriptor.body_node_ids, vec!["decision", "discussion"]);

    assert_eq!(graph.graph_mode, StateMachineGraphMode::Hierarchical);
    assert_eq!(graph.execution_graph_mode, Some(StateMachineGraphMode::Acyclic));
    assert_eq!(graph.nodes.iter().filter(|node| node.execution.is_some()).count(), 6);
    assert!(graph.edges.iter().any(|edge| edge.outcome == "continue" && edge.loop_route.as_ref().is_some_and(|route| route.kind == StateMachineLoopRouteKind::Exhausted)));
    let authoring = validated.definition.unwrap();
    assert!(authoring.requires.as_ref().unwrap().server_features.iter().any(|feature| feature == "state_machine.loop.previous_result"));
    let CollaborationRuntimeDefinition::StateMachine(machine) = &authoring.runtime else { panic!() };
    assert_eq!(machine.version, 2);
    assert!(machine.nodes.contains_key("rounds"));
    assert!(validate_definition(authoring).is_err(), "S1 must not enable runtime creation");
}

#[test]
fn deployment_limits_and_body_judge_capability_are_applied_to_preview() {
    let command = || ValidateCollaborationDefinitionYamlCommand { definition_yaml: YAML.into(), judge_available: true };
    let limits = FixedLoopLimits { max_fixed_loop_iterations: 2, ..FixedLoopLimits::default() };
    assert!(!validate_authoring_definition_yaml_with_limits(command(), &limits).valid);
    let mut unavailable = command();
    unavailable.judge_available = false;
    let validation = validate_authoring_definition_yaml(unavailable);
    assert!(!validation.valid);
    assert_eq!(validation.errors[0].code, "UNAVAILABLE_FEATURE");
}

#[test]
fn authoring_strict_keys_and_required_fields_recurse_into_body() {
    for yaml in [
        YAML.replace("mode: fixed", "mode: fixed\n          mode_typo: true"),
        YAML.replace("criteria: [Whether the discussion has converged]", "criteria: [Whether the discussion has converged]\n                typo: true"),
        YAML.replace("          continue_outcomes: [continue]\n", ""),
        YAML.replace("          break_outcomes: [completed]\n", ""),
        YAML.replace("kind: loop\n        display_name", "kind: loop\n        final_output: false\n        display_name"),
        YAML.replace("continue_outcomes: [continue]", "continue_outcomes: []"),
    ] {
        let validation = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand { definition_yaml: yaml, judge_available: true });
        assert!(!validation.valid);
        assert!(validation.errors[0].path.starts_with("$.runtime.state_machine.nodes.rounds"));
    }
}

#[test]
fn regenerating_loop_authoring_yaml_does_not_add_forbidden_default_fields() {
    let mut authoring = fixture();
    let root = authoring.as_object_mut().unwrap();
    for key in ["id", "api_version", "version", "extensions"] { root.remove(key); }
    let yaml = serde_yaml::to_string(&authoring).unwrap();
    let result = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand { definition_yaml: yaml, judge_available: true });
    assert!(result.valid, "{:?}", result.errors);
}

#[test]
fn human_entry_maps_previous_result_without_changing_assignee_rules() {
    let mut value = fixture();
    value["participants"]["speaker"]["required"] = json!(false);
    let entry = &mut body(&mut value)["nodes"]["discussion"];
    entry["kind"] = json!("human_input");
    entry["node_timeout_ms"] = json!(600000);
    entry.as_object_mut().unwrap().remove("assignee");
    let plan = compile(value.clone());
    for meta in plan.node_metadata.values().filter(|meta| meta.is_loop_entry) {
        assert_eq!(plan.state_machine.nodes[&meta.execution_node_id].kind, bcs_domain::StateMachineNodeKind::HumanInput);
        match meta.iteration.unwrap() {
            1 => assert!(meta.previous_result_node_id.is_none()),
            iteration => assert_eq!(meta.previous_result_node_id.as_deref(), Some(result_id(&plan, iteration - 1))),
        }
    }
    body(&mut value)["nodes"]["discussion"].as_object_mut().unwrap().remove("node_timeout_ms");
    let error = compile_authoring_definition(serde_json::from_value(value).unwrap(), &FixedLoopLimits::default()).err().unwrap();
    assert!(error.path.ends_with("loop.nodes.discussion"));
}

#[test]
fn missing_exhausted_targets_report_the_transition_path() {
    for missing in ["targets", "exhausted"] {
        let mut value = fixture();
        let transitions = &mut value["runtime"]["state_machine"]["nodes"]["rounds"]["transitions"];
        if missing == "targets" {
            transitions["exhausted"].as_object_mut().unwrap().remove("targets");
        } else {
            transitions.as_object_mut().unwrap().remove("exhausted");
        }
        let error = compile_authoring_definition(serde_json::from_value(value).unwrap(), &FixedLoopLimits::default()).err().unwrap();
        assert_eq!(error.path, "$.runtime.state_machine.nodes.rounds.transitions.exhausted.targets");
    }
}

#[test]
fn unsupported_body_capabilities_and_overlong_outer_ids_are_rejected() {
    let mut bad_feature = fixture();
    bad_feature["requires"] = json!({"server_features": ["state_machine.guarded_transitions"]});
    let mut long_id = fixture();
    let node = long_id["runtime"]["state_machine"]["nodes"].as_object_mut().unwrap().remove("prepare").unwrap();
    long_id["runtime"]["state_machine"]["nodes"]["a".repeat(129)] = node;
    for value in [bad_feature, long_id] {
        assert!(compile_authoring_definition(serde_json::from_value(value).unwrap(), &FixedLoopLimits::default()).is_err());
    }
}

#[test]
fn reserved_ids_nested_loops_and_missing_final_are_rejected() {
    let mut reserved = fixture();
    let node = reserved["runtime"]["state_machine"]["nodes"].as_object_mut().unwrap().remove("prepare").unwrap();
    reserved["runtime"]["state_machine"]["nodes"]["ln-reserved"] = node;
    let mut nested = fixture();
    body(&mut nested)["nodes"]["discussion"] = fixture()["runtime"]["state_machine"]["nodes"]["rounds"].clone();
    let mut no_final = fixture();
    no_final["runtime"]["state_machine"]["nodes"]["publish"]["final_output"] = json!(false);
    for value in [reserved, nested, no_final] {
        assert!(compile_authoring_definition(serde_json::from_value(value).unwrap(), &FixedLoopLimits::default()).is_err());
    }
}
