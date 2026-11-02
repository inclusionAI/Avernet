use std::collections::BTreeSet;

use bcs_config_api::FixedLoopLimits;
use bcs_domain::{
    CollaborationDefinition, CollaborationRuntimeDefinition, CompiledArtifactProjection,
    StateMachineExecutionPlan, StateMachineGraphMode, StateMachineLoopRouteKind,
};
use bcs_collaboration_runtime::fixed_loop::{
    compile_authoring_definition, execution_plan_content_hash, FIXED_LOOP_COMPILER_VERSION,
    NESTED_FIXED_LOOP_COMPILER_VERSION,
};
use bcs_collaboration_runtime::validation::validate_authoring_definition_yaml_with_limits;
use bcs_collaboration_runtime::{validate_authoring_definition_yaml, validate_definition};
use bcs_service_api::ValidateCollaborationDefinitionYamlCommand;
use serde_json::{json, Value};

const YAML: &str = include_str!("fixtures/fixed_loop.yaml");
const NESTED_YAML: &str = include_str!("fixtures/nested_fixed_loop.yaml");
const THREE_LEVEL_HUMAN_YAML: &str = include_str!("fixtures/three_level_human_loop.yaml");

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
    let cli = yaml_block(cli.split_once("## Fixed Loop\n").unwrap().1);
    for yaml in [full.to_string(), serde_yaml::to_string(&fixed).unwrap(), cli.to_string()] {
        let result = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand { definition_yaml: yaml, judge_available: true });
        assert!(result.valid, "{:?}", result.errors);
    }
}

#[test]
fn cli_nested_fixed_loop_example_compiles_with_scoped_iterations_and_limits() {
    let cli = include_str!("../../../tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md");
    let yaml = yaml_block(cli.split_once("### Nested fixed Loop example\n").unwrap().1);
    let command = || ValidateCollaborationDefinitionYamlCommand {
        definition_yaml: yaml.into(), judge_available: false,
    };
    let result = validate_authoring_definition_yaml(command());
    assert!(result.valid, "{:?}", result.errors);
    assert!(result.warnings.iter().any(|warning| warning.code == "VALIDATION_ONLY_FEATURE"));
    let graph = result.graph.unwrap();
    assert_eq!(graph.nodes.len(), 11);
    assert_eq!(graph.loops.keys().map(String::as_str).collect::<Vec<_>>(), vec!["outer", "outer/inner"]);
    let compiled = compile_authoring_definition(result.definition.unwrap(), &FixedLoopLimits::default()).unwrap();
    let plan = compiled.plan.unwrap();
    assert_eq!(plan.compiler_version, NESTED_FIXED_LOOP_COMPILER_VERSION);
    assert!(plan.state_machine.nodes["publish"].final_output);
    let entries: Vec<_> = plan.node_metadata.values()
        .filter(|meta| meta.definition_node_id == "refine").collect();
    assert_eq!(entries.len(), 6);
    for entry in entries {
        let path = entry.loop_path.as_ref().unwrap();
        assert_eq!(path.len(), 2);
        if path[1].iteration == 1 {
            assert!(entry.previous_result_node_id.is_none());
        } else {
            let previous = &plan.node_metadata[entry.previous_result_node_id.as_ref().unwrap()];
            let previous_path = previous.loop_path.as_ref().unwrap();
            assert_eq!(previous_path[0], path[0]);
            assert_eq!(previous_path[1].iteration, path[1].iteration - 1);
        }
    }
    let limited = validate_authoring_definition_yaml_with_limits(command(),
        &FixedLoopLimits { max_fixed_loop_depth: 1, ..Default::default() });
    assert!(!limited.valid);
    assert!(limited.errors.iter().any(|error|
        error.path.ends_with("outer.loop.nodes.inner.loop") && error.message.contains("max_fixed_loop_depth")));
}

fn fixture() -> Value {
    let mut definition: CollaborationDefinition = serde_yaml::from_str(YAML).unwrap();
    definition.id = "fixed-loop-fixture".into();
    definition.version = 7;
    serde_json::to_value(definition).unwrap()
}

fn nested_fixture() -> Value {
    let mut definition: CollaborationDefinition = serde_yaml::from_str(NESTED_YAML).unwrap();
    definition.id = "nested-loop-fixture".into();
    definition.version = 11;
    serde_json::to_value(definition).unwrap()
}

#[test]
fn nested_leaf_dag_errors_preserve_the_complete_authoring_path() {
    for assignee in [None, Some(json!({"type": "bot_binding", "binding": "missing"}))] {
        let mut value = nested_fixture();
        let leaf = value["runtime"]["state_machine"]["nodes"]["outer"]["loop"]["nodes"]
            ["inner"]["loop"]["nodes"]["review"].as_object_mut().unwrap();
        match assignee {
            Some(assignee) => { leaf.insert("assignee".into(), assignee); }
            None => { leaf.remove("assignee"); }
        }
        let definition = serde_json::from_value(value).unwrap();
        let error = compile_authoring_definition(definition, &FixedLoopLimits::default()).err().unwrap();
        assert_eq!(error.path, "$.runtime.state_machine.nodes.outer.loop.nodes.inner.loop.nodes.review");
        assert!(error.message.contains("assignee"), "{error:?}");
    }
    let mut value: Value = serde_yaml::from_str(THREE_LEVEL_HUMAN_YAML).unwrap();
    value["runtime"]["state_machine"]["nodes"]["outer"]["loop"]["nodes"]["middle"]
        ["loop"]["nodes"]["inner"]["loop"]["nodes"]["inner_review"]
        .as_object_mut().unwrap().remove("assignee");
    let result = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
        definition_yaml: serde_yaml::to_string(&value).unwrap(), judge_available: true,
    });
    assert!(!result.valid);
    assert!(result.errors.iter().any(|error| error.path ==
        "$.runtime.state_machine.nodes.outer.loop.nodes.middle.loop.nodes.inner.loop.nodes.inner_review"), "{:?}", result.errors);
}

#[test]
fn nested_example_expands_to_v2_with_complete_instance_paths() {
    let definition: CollaborationDefinition = serde_json::from_value(nested_fixture()).unwrap();
    let compiled = compile_authoring_definition(definition, &FixedLoopLimits::default()).unwrap();
    assert!(compiled.definition.requires.as_ref().unwrap().server_features.iter()
        .any(|feature| feature == "state_machine.loop.nesting"));
    let plan = compiled.plan.unwrap();
    assert_eq!(plan.compiler_version, NESTED_FIXED_LOOP_COMPILER_VERSION);
    assert_eq!(plan.state_machine.nodes.len(), 17);
    assert_eq!(plan.state_machine.nodes.values().filter(|node| node.final_output).count(), 1);
    assert!(plan.state_machine.nodes["publish"].final_output);
    assert_eq!(plan.node_metadata.values().filter(|meta|
        meta.loop_path.as_ref().is_some_and(|path| path.len() == 1)).count(), 4);
    assert_eq!(plan.node_metadata.values().filter(|meta|
        meta.loop_path.as_ref().is_some_and(|path| path.len() == 2)).count(), 12);
    assert!(plan.node_metadata["publish"].loop_path.is_none());

    for outer_iteration in 1..=2 {
        for inner_iteration in 1..=3 {
            let draft = plan.node_metadata.values().find(|meta| {
                meta.definition_node_id == "draft" && meta.loop_path.as_ref().is_some_and(|path| {
                    path[0].loop_id == "outer" && path[0].iteration == outer_iteration
                        && path[1].loop_id == "inner" && path[1].iteration == inner_iteration
                })
            }).unwrap();
            if inner_iteration == 1 {
                assert!(draft.previous_result_node_id.is_none());
            } else {
                let previous = &plan.node_metadata[draft.previous_result_node_id.as_ref().unwrap()];
                let path = previous.loop_path.as_ref().unwrap();
                assert_eq!(previous.definition_node_id, "review");
                assert_eq!(path[0].iteration, outer_iteration);
                assert_eq!(path[1].iteration, inner_iteration - 1);
            }
        }
    }
}

#[test]
fn nested_preview_uses_full_definition_paths_for_loop_descriptors() {
    let outcome = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
        definition_yaml: NESTED_YAML.into(), judge_available: true,
    });
    assert!(outcome.valid, "{:?}", outcome.errors);
    assert!(outcome.warnings.iter().any(|warning|
        warning.code == "VALIDATION_ONLY_FEATURE" && warning.message.contains("loop.nesting")));
    let graph = outcome.graph.unwrap();
    assert_eq!(graph.nodes.len(), 17);
    assert_eq!(graph.loops.keys().map(String::as_str).collect::<Vec<_>>(), vec!["outer", "outer/inner"]);
    assert_eq!(graph.loops["outer"].body_node_ids, vec!["assess", "inner", "prepare"]);
    assert_eq!(graph.loops["outer/inner"].body_node_ids, vec!["draft", "review"]);
}

#[test]
fn nested_depth_limit_rejects_before_expansion() {
    let definition = serde_json::from_value(nested_fixture()).unwrap();
    let limits = FixedLoopLimits { max_fixed_loop_depth: 1, ..Default::default() };
    let error = compile_authoring_definition(definition, &limits).err().unwrap();
    assert_eq!(error.code, "INVALID_DEFINITION");
    assert!(error.path.ends_with("outer.loop.nodes.inner.loop"), "{}", error.path);
    assert!(error.message.contains("max_fixed_loop_depth"));
}

#[test]
fn compiler_version_follows_actual_depth_and_nested_cost_is_checked_before_expansion() {
    let mut single = fixture();
    single["requires"]["server_features"] = json!(["state_machine.loop.nesting"]);
    assert_eq!(compile(single).compiler_version, FIXED_LOOP_COMPILER_VERSION);

    let nested = serde_json::from_value(nested_fixture()).unwrap();
    let limits = FixedLoopLimits { max_compiled_state_machine_nodes: 16, ..Default::default() };
    let error = compile_authoring_definition(nested, &limits).err().unwrap();
    assert!(error.message.contains("max_compiled_state_machine_nodes"));

    let nested: CollaborationDefinition = serde_json::from_value(nested_fixture()).unwrap();
    let bytes = serde_json::to_vec(&compile(nested_fixture())).unwrap().len();
    assert!(compile_authoring_definition(nested.clone(), &FixedLoopLimits {
        max_compiled_state_machine_bytes: bytes, ..Default::default()
    }).is_ok());
    let error = compile_authoring_definition(nested, &FixedLoopLimits {
        max_compiled_state_machine_bytes: bytes - 1, ..Default::default()
    }).err().unwrap();
    assert!(error.message.contains("max_compiled_state_machine_bytes"));

    let mut overflow: CollaborationDefinition = serde_yaml::from_str(THREE_LEVEL_HUMAN_YAML).unwrap();
    let CollaborationRuntimeDefinition::StateMachine(machine) = &mut overflow.runtime else { panic!() };
    let outer = machine.nodes.get_mut("outer").unwrap().loop_definition.as_mut().unwrap();
    outer.max_iterations = u32::MAX;
    let middle = outer.nodes.get_mut("middle").unwrap().loop_definition.as_mut().unwrap();
    middle.max_iterations = u32::MAX;
    middle.nodes.get_mut("inner").unwrap().loop_definition.as_mut().unwrap().max_iterations = u32::MAX;
    let error = compile_authoring_definition(overflow, &FixedLoopLimits {
        max_fixed_loop_iterations: u32::MAX,
        max_fixed_loop_body_nodes: usize::MAX,
        max_compiled_state_machine_nodes: usize::MAX,
        max_compiled_state_machine_bytes: usize::MAX,
        ..Default::default()
    }).err().unwrap();
    assert!(error.message.contains("overflow"));
}

#[test]
fn nested_capacity_boundaries_cover_near_maximum_nodes_and_plan_bytes() {
    let mut near_nodes: CollaborationDefinition = serde_json::from_value(nested_fixture()).unwrap();
    let CollaborationRuntimeDefinition::StateMachine(machine) = &mut near_nodes.runtime else { panic!() };
    let outer = machine.nodes.get_mut("outer").unwrap().loop_definition.as_mut().unwrap();
    outer.max_iterations = 31;
    outer.nodes.get_mut("inner").unwrap().loop_definition.as_mut().unwrap().max_iterations = 32;
    let started = std::time::Instant::now();
    let compiled = compile_authoring_definition(near_nodes, &FixedLoopLimits {
        max_compiled_state_machine_bytes: 16 * 1024 * 1024,
        ..Default::default()
    }).unwrap().plan.unwrap();
    let node_elapsed = started.elapsed();
    assert_eq!(compiled.state_machine.nodes.len(), 2047);
    let serialized_bytes = serde_json::to_vec(&compiled).unwrap().len();
    eprintln!("nested capacity: nodes=2047 plan_bytes={serialized_bytes} compile={node_elapsed:?}");

    let base: CollaborationDefinition = serde_json::from_value(nested_fixture()).unwrap();
    let plan_limit = FixedLoopLimits::default().max_compiled_state_machine_bytes;
    let plan_bytes = |padding: usize| {
        let mut definition = base.clone();
        let CollaborationRuntimeDefinition::StateMachine(machine) = &mut definition.runtime else { panic!() };
        machine.nodes.get_mut("outer").unwrap().loop_definition.as_mut().unwrap()
            .nodes.get_mut("inner").unwrap().loop_definition.as_mut().unwrap()
            .nodes.get_mut("draft").unwrap().instruction = Some("x".repeat(padding));
        let plan = compile_authoring_definition(definition, &FixedLoopLimits {
            max_compiled_state_machine_bytes: usize::MAX,
            ..Default::default()
        }).unwrap().plan.unwrap();
        (serde_json::to_vec(&plan).unwrap().len(), plan)
    };
    let mut low = 0;
    let mut high = plan_limit;
    while low + 1 < high {
        let middle = low + (high - low) / 2;
        if plan_bytes(middle).0 <= plan_limit { low = middle; } else { high = middle; }
    }
    let (bytes, plan) = plan_bytes(low);
    assert!(bytes <= plan_limit && plan_limit - bytes < 64, "bytes={bytes}, limit={plan_limit}");
    let definition = serde_json::from_value::<CollaborationDefinition>(nested_fixture()).unwrap();
    let mut near_bytes = definition;
    let CollaborationRuntimeDefinition::StateMachine(machine) = &mut near_bytes.runtime else { panic!() };
    machine.nodes.get_mut("outer").unwrap().loop_definition.as_mut().unwrap()
        .nodes.get_mut("inner").unwrap().loop_definition.as_mut().unwrap()
        .nodes.get_mut("draft").unwrap().instruction = Some("x".repeat(low));
    let started = std::time::Instant::now();
    assert!(compile_authoring_definition(near_bytes.clone(), &FixedLoopLimits::default()).is_ok());
    let byte_elapsed = started.elapsed();
    let CollaborationRuntimeDefinition::StateMachine(machine) = &mut near_bytes.runtime else { panic!() };
    machine.nodes.get_mut("outer").unwrap().loop_definition.as_mut().unwrap()
        .nodes.get_mut("inner").unwrap().loop_definition.as_mut().unwrap()
        .nodes.get_mut("draft").unwrap().instruction = Some("x".repeat(high));
    assert!(compile_authoring_definition(near_bytes, &FixedLoopLimits::default()).is_err());
    eprintln!("nested capacity: nodes={} plan_bytes={bytes} compile={byte_elapsed:?}",
        plan.state_machine.nodes.len());
}

#[test]
fn three_level_human_fixture_compiles_with_complete_paths_and_checked_cost() {
    let validation = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
        definition_yaml: THREE_LEVEL_HUMAN_YAML.into(), judge_available: true,
    });
    assert!(validation.valid, "{:?}", validation.errors);
    let mut definition: CollaborationDefinition = serde_yaml::from_str(THREE_LEVEL_HUMAN_YAML).unwrap();
    definition.id = "three-level-human".into();
    definition.version = 3;
    let compiled = compile_authoring_definition(definition, &FixedLoopLimits::default()).unwrap();
    let plan = compiled.plan.unwrap();
    assert_eq!(plan.compiler_version, NESTED_FIXED_LOOP_COMPILER_VERSION);
    assert_eq!(plan.state_machine.nodes.len(), 21);
    let deepest = plan.node_metadata.values().filter(|meta|
        meta.loop_path.as_ref().is_some_and(|path| path.len() == 3)).collect::<Vec<_>>();
    assert_eq!(deepest.len(), 8);
    assert!(deepest.iter().all(|meta| meta.definition_node_id == "inner_review"));
    assert!(deepest.iter().filter(|meta| meta.iteration == Some(1))
        .all(|meta| meta.previous_result_node_id.is_none()));

    let definition: CollaborationDefinition = serde_yaml::from_str(THREE_LEVEL_HUMAN_YAML).unwrap();
    assert!(compile_authoring_definition(definition.clone(), &FixedLoopLimits {
        max_fixed_loop_depth: 3, ..Default::default()
    }).is_ok());
    let error = compile_authoring_definition(definition, &FixedLoopLimits {
        max_fixed_loop_depth: 2, ..Default::default()
    }).err().unwrap();
    assert!(error.path.ends_with("outer.loop.nodes.middle.loop.nodes.inner.loop"));
}

#[test]
fn parent_child_and_parallel_sibling_loops_with_repeated_local_ids_are_distinct() {
    let mut same_name = nested_fixture();
    let outer_body = same_name["runtime"]["state_machine"]["nodes"]["outer"]["loop"]
        .as_object_mut().unwrap();
    let child = outer_body["nodes"].as_object_mut().unwrap().remove("inner").unwrap();
    outer_body["nodes"]["outer"] = child;
    outer_body["nodes"]["prepare"]["transitions"]["complete"]["targets"] = json!(["outer"]);
    let plan = compile(same_name);
    assert!(plan.node_metadata.values().any(|metadata| metadata.loop_path.as_ref().is_some_and(|path|
        path.iter().map(|frame| frame.loop_id.as_str()).collect::<Vec<_>>() == ["outer", "outer"]
    )));

    let mut parallel = nested_fixture();
    let sibling = parallel["runtime"]["state_machine"]["nodes"]["outer"].clone();
    parallel["runtime"]["state_machine"]["nodes"]["outer_b"] = sibling;
    parallel["runtime"]["state_machine"]["nodes"]["start"] = json!({
        "kind": "bot_task",
        "display_name": "Start both reviews",
        "assignee": {"type": "bot_binding", "binding": "worker"},
        "instruction": "Start both reviews.",
        "transitions": {"complete": {"targets": ["outer", "outer_b"]}}
    });
    let plan = compile(parallel);
    assert_eq!(plan.state_machine.nodes.len(), 34);
    let paths = plan.node_metadata.values().filter_map(|metadata| metadata.loop_path.as_ref())
        .map(|path| path.iter().map(|frame| frame.loop_id.as_str()).collect::<Vec<_>>())
        .collect::<BTreeSet<_>>();
    assert!(paths.contains(&vec!["outer", "inner"]));
    assert!(paths.contains(&vec!["outer_b", "inner"]));
    assert_eq!(plan.node_metadata.len(), plan.node_metadata.keys().collect::<BTreeSet<_>>().len());
}

#[test]
fn nested_ordinary_transition_cannot_escape_its_scope() {
    let mut value = nested_fixture();
    value["runtime"]["state_machine"]["nodes"]["outer"]["loop"]["nodes"]["inner"]
        ["loop"]["nodes"]["draft"]["transitions"]["complete"]["targets"] = json!(["assess"]);
    let error = compile_authoring_definition(
        serde_json::from_value(value).unwrap(), &FixedLoopLimits::default(),
    ).err().unwrap();
    assert!(error.path.ends_with("outer.loop.nodes.inner.loop.nodes.draft.transitions.complete.targets"));
    assert!(error.message.contains("same body"));
}

#[test]
fn serial_and_parallel_child_loops_resolve_entries_and_shared_join_in_parent_scope() {
    let with_second_child = || {
        let mut value = nested_fixture();
        let child = value["runtime"]["state_machine"]["nodes"]["outer"]["loop"]["nodes"]["inner"].clone();
        value["runtime"]["state_machine"]["nodes"]["outer"]["loop"]["nodes"]["inner2"] = child;
        value
    };

    let mut serial = with_second_child();
    for outcome in ["approved", "exhausted"] {
        serial["runtime"]["state_machine"]["nodes"]["outer"]["loop"]["nodes"]["inner"]
            ["transitions"][outcome]["targets"] = json!(["inner2"]);
    }
    let serial = compile(serial);
    assert_eq!(serial.state_machine.nodes.len(), 29);
    assert!(serial.edge_metadata.iter().any(|edge| {
        let source = &serial.node_metadata[&edge.source_execution_node_id];
        let target = &serial.node_metadata[&edge.target_execution_node_id];
        source.definition_node_id == "review"
            && source.loop_path.as_ref().is_some_and(|path| path[0].iteration == 1 && path[1].loop_id == "inner")
            && target.is_loop_entry
            && target.loop_path.as_ref().is_some_and(|path| path[0].iteration == 1 && path[1].loop_id == "inner2" && path[1].iteration == 1)
    }));

    let mut parallel = with_second_child();
    parallel["runtime"]["state_machine"]["nodes"]["outer"]["loop"]["nodes"]["prepare"]
        ["transitions"]["complete"]["targets"] = json!(["inner", "inner2"]);
    let parallel = compile(parallel);
    assert_eq!(parallel.state_machine.nodes.len(), 29);
    let assess = parallel.node_metadata.values().find(|metadata| {
        metadata.definition_node_id == "assess"
            && metadata.loop_path.as_ref().is_some_and(|path| path.len() == 1 && path[0].iteration == 1)
    }).unwrap();
    let incoming_children = parallel.edge_metadata.iter()
        .filter(|edge| edge.target_execution_node_id == assess.execution_node_id)
        .filter_map(|edge| parallel.node_metadata[&edge.source_execution_node_id].loop_path.as_ref())
        .map(|path| path[1].loop_id.as_str())
        .collect::<BTreeSet<_>>();
    assert_eq!(incoming_children, BTreeSet::from(["inner", "inner2"]));
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

#[test]
fn seeded_research_writing_loops_have_independent_bodies_and_explicit_exit_routes() {
    for yaml in [
        include_str!("../../../../seeds/collaboration-templates/zh-CN/research-writing-loops.yaml"),
        include_str!("../../../../seeds/collaboration-templates/en-US/research-writing-loops.yaml"),
    ] {
        let validation = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
            definition_yaml: yaml.into(), judge_available: true,
        });
        assert!(validation.valid, "{:?}", validation.errors);
        let definition: CollaborationDefinition = serde_yaml::from_str(yaml).unwrap();
        let plan = compile(serde_json::to_value(definition).unwrap());
        assert_eq!(plan.state_machine.nodes.len(), 16);
        assert_eq!(plan.node_metadata.values().filter(|node| node.is_loop_entry && node.iteration == Some(1)).count(), 2);
        let writing_entry = plan.node_metadata.values().find(|node|
            node.loop_id.as_deref() == Some("writing_loop") && node.is_loop_entry && node.iteration == Some(1)).unwrap();
        assert!(writing_entry.previous_result_node_id.is_none());
        for (loop_id, approved_target, exhausted_target) in [
            ("research_loop", writing_entry.execution_node_id.as_str(), "research_gaps"),
            ("writing_loop", "polish", "rewrite"),
        ] {
            for iteration in 1..=3 {
                let result = plan.node_metadata.values().find(|node| node.loop_id.as_deref() == Some(loop_id)
                    && node.definition_node_id == "review" && node.iteration == Some(iteration)).unwrap();
                let approved = plan.edge_metadata.iter().find(|edge|
                    edge.source_execution_node_id == result.execution_node_id && edge.outcome == "approved").unwrap();
                assert_eq!(approved.target_execution_node_id, approved_target);
                assert_eq!(approved.loop_route.as_ref().unwrap().kind, StateMachineLoopRouteKind::Break);
                let revise = plan.edge_metadata.iter().find(|edge|
                    edge.source_execution_node_id == result.execution_node_id && edge.outcome == "revise").unwrap();
                if iteration < 3 {
                    let next = &plan.node_metadata[&revise.target_execution_node_id];
                    assert_eq!(next.loop_id.as_deref(), Some(loop_id));
                    assert_eq!(next.iteration, Some(iteration + 1));
                    assert_eq!(next.previous_result_node_id.as_ref(), Some(&result.execution_node_id));
                } else {
                    assert_eq!(revise.target_execution_node_id, exhausted_target);
                    assert_eq!(revise.loop_route.as_ref().unwrap().kind, StateMachineLoopRouteKind::Exhausted);
                }
            }
        }
    }
}

#[test]
fn seeded_nested_review_loops_compile_with_scoped_parent_and_child_instances() {
    for yaml in [
        include_str!("../../../../seeds/collaboration-templates/zh-CN/nested-review-loops.yaml"),
        include_str!("../../../../seeds/collaboration-templates/en-US/nested-review-loops.yaml"),
    ] {
        let validation = validate_authoring_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
            definition_yaml: yaml.into(), judge_available: true,
        });
        assert!(validation.valid, "{:?}", validation.errors);
        let graph = validation.graph.unwrap();
        assert_eq!(graph.loops.keys().map(String::as_str).collect::<Vec<_>>(),
            vec!["delivery_cycles", "delivery_cycles/review_rounds"]);

        let definition: CollaborationDefinition = serde_yaml::from_str(yaml).unwrap();
        assert!(definition.uses_judge());
        let plan = compile(serde_json::to_value(definition).unwrap());
        assert_eq!(plan.compiler_version, NESTED_FIXED_LOOP_COMPILER_VERSION);
        assert_eq!(plan.state_machine.nodes.len(), 17);
        assert_eq!(plan.state_machine.nodes.values().filter(|node| node.final_output).count(), 1);
        assert!(plan.state_machine.nodes["finalize"].final_output);
        assert_eq!(plan.node_metadata.values().filter(|node|
            node.loop_path.as_ref().is_some_and(|path| path.len() == 1)).count(), 4);
        assert_eq!(plan.node_metadata.values().filter(|node|
            node.loop_path.as_ref().is_some_and(|path| path.len() == 2)).count(), 12);
        assert!(plan.node_metadata["finalize"].loop_path.is_none());
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
fn reserved_ids_child_loop_as_entry_and_missing_final_are_rejected() {
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
