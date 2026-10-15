use bcs_collaboration_runtime::fixed_loop::{compile_authoring_definition, execution_plan_content_hash};
use bcs_collaboration_runtime::definition::node_loop_path;
use bcs_collaboration_runtime::fixed_loop::NESTED_FIXED_LOOP_COMPILER_VERSION;
use bcs_collaboration_runtime::snapshot::{load_state_machine_snapshot, snapshot_execution_plan};
use bcs_config_api::FixedLoopLimits;
use bcs_service_api::port::repo::StateMachineRunSnapshot;

fn fixture() -> StateMachineRunSnapshot {
    let definition = serde_yaml::from_str(include_str!("fixtures/fixed_loop.yaml")).unwrap();
    let compiled = compile_authoring_definition(definition, &FixedLoopLimits::default()).unwrap();
    StateMachineRunSnapshot {
        definition: compiled.definition,
        execution_plan: Some(snapshot_execution_plan(compiled.plan.unwrap()).unwrap()),
        resolved_participant_bindings: None,
    }
}

fn nested_fixture() -> StateMachineRunSnapshot {
    let mut definition: bcs_domain::CollaborationDefinition = serde_yaml::from_str(
        include_str!("fixtures/nested_fixed_loop.yaml"),
    ).unwrap();
    definition.id = "nested-snapshot".into();
    definition.version = 3;
    let compiled = compile_authoring_definition(definition, &FixedLoopLimits::default()).unwrap();
    StateMachineRunSnapshot {
        definition: compiled.definition,
        execution_plan: Some(snapshot_execution_plan(compiled.plan.unwrap()).unwrap()),
        resolved_participant_bindings: None,
    }
}

fn rehash(snapshot: &mut StateMachineRunSnapshot) {
    let stored = snapshot.execution_plan.as_mut().unwrap();
    stored.content_hash = execution_plan_content_hash(&stored.plan).unwrap();
}

#[test]
fn snapshot_loader_uses_persisted_identity_without_current_id_generation() {
    let mut snapshot = fixture();
    let stored = snapshot.execution_plan.as_mut().unwrap();
    let old = stored.plan.node_metadata.values().find(|meta| meta.is_loop_result && meta.iteration == Some(1))
        .unwrap().execution_node_id.clone();
    let historical = "ln-historical-result-identity".to_string();
    let node = stored.plan.state_machine.nodes.remove(&old).unwrap();
    stored.plan.state_machine.nodes.insert(historical.clone(), node);
    for node in stored.plan.state_machine.nodes.values_mut() {
        for transition in node.transitions.values_mut() {
            for target in &mut transition.targets {
                if target == &old { *target = historical.clone(); }
            }
        }
    }
    let mut meta = stored.plan.node_metadata.remove(&old).unwrap();
    meta.execution_node_id = historical.clone();
    stored.plan.node_metadata.insert(historical.clone(), meta);
    for meta in stored.plan.node_metadata.values_mut() {
        if meta.previous_result_node_id.as_ref() == Some(&old) {
            meta.previous_result_node_id = Some(historical.clone());
        }
    }
    for edge in &mut stored.plan.edge_metadata {
        if edge.source_execution_node_id == old { edge.source_execution_node_id = historical.clone(); }
        if edge.target_execution_node_id == old { edge.target_execution_node_id = historical.clone(); }
    }
    rehash(&mut snapshot);
    let expected_hash = snapshot.execution_plan.as_ref().unwrap().content_hash.clone();
    let loaded = load_state_machine_snapshot(snapshot).unwrap();
    let plan = loaded.plan.unwrap();
    assert!(plan.state_machine.nodes.contains_key(&historical));
    assert_eq!(execution_plan_content_hash(&plan).unwrap(), expected_hash);
    // The loader has no current compiler/limits/definition repository argument.
    assert!(!plan.state_machine.nodes.contains_key(&old));
}

#[test]
fn snapshot_loader_rejects_missing_plan_bad_hash_and_unknown_compiler() {
    for mutation in 0..5 {
        let mut snapshot = fixture();
        match mutation {
            0 => snapshot.execution_plan = None,
            1 => snapshot.execution_plan.as_mut().unwrap().content_hash = "0".repeat(64),
            2 => snapshot.execution_plan.as_mut().unwrap().compiler_version.clear(),
            3 => {
                let stored = snapshot.execution_plan.as_mut().unwrap();
                stored.compiler_version = "future-compiler/v99".into();
                stored.plan.compiler_version = stored.compiler_version.clone();
                rehash(&mut snapshot);
            }
            _ => snapshot.execution_plan.as_mut().unwrap().plan.compiler_version = "different".into(),
        }
        let error = load_state_machine_snapshot(snapshot).err().expect("must reject damaged snapshot");
        assert!(error.to_string().contains("corrupt state-machine snapshot"));
    }
}

#[test]
fn snapshot_loader_rejects_incomplete_or_inconsistent_mapping_even_with_matching_hash() {
    for mutation in 0..7 {
        let mut snapshot = fixture();
        let plan = &mut snapshot.execution_plan.as_mut().unwrap().plan;
        match mutation {
            0 => { plan.node_metadata.pop_first(); }
            1 => { plan.edge_metadata.pop(); }
            2 => plan.node_metadata.values_mut().find(|meta| meta.iteration == Some(2) && meta.is_loop_entry)
                .unwrap().previous_result_node_id = None,
            3 => plan.node_metadata.values_mut().find(|meta| meta.loop_id.is_some()).unwrap().iteration = Some(0),
            4 => plan.edge_metadata.iter_mut().find(|edge| edge.loop_route.is_some()).unwrap().loop_route = None,
            5 => {
                let edge = plan.edge_metadata[0].clone();
                plan.edge_metadata.push(edge);
            }
            _ => {
                let edge = &mut plan.edge_metadata[0];
                let old_target = edge.target_execution_node_id.clone();
                edge.target_execution_node_id = "missing".into();
                let node = plan.state_machine.nodes.get_mut(&edge.source_execution_node_id).unwrap();
                for target in &mut node.transitions.get_mut(&edge.outcome).unwrap().targets {
                    if target == &old_target { *target = "missing".into(); }
                }
            }
        }
        rehash(&mut snapshot);
        assert!(load_state_machine_snapshot(snapshot).is_err(), "mutation {mutation}");
    }
}

#[test]
fn legacy_v1_snapshot_load_keeps_original_graph_and_omits_plan() {
    let compiled = load_state_machine_snapshot(fixture()).unwrap();
    let snapshot = StateMachineRunSnapshot {
        definition: compiled.execution.definition,
        execution_plan: None,
        resolved_participant_bindings: None,
    };
    let expected = serde_json::to_value(&snapshot.definition).unwrap();
    let loaded = load_state_machine_snapshot(snapshot).unwrap();
    assert!(loaded.plan.is_none());
    assert_eq!(serde_json::to_value(loaded.definition).unwrap(), expected);
}

#[test]
fn loop_path_accessor_normalizes_v1_and_validates_v2() {
    let snapshot = fixture();
    let mut plan = snapshot.execution_plan.unwrap().plan;
    let legacy = plan.node_metadata.values().find(|meta| meta.loop_id.is_some()).unwrap().clone();
    assert_eq!(node_loop_path(&plan, &legacy).unwrap(), vec![bcs_domain::StateMachineLoopFrame {
        loop_id: legacy.loop_id.clone().unwrap(),
        iteration: legacy.iteration.unwrap(),
        max_iterations: legacy.max_iterations.unwrap(),
    }]);
    let ordinary = plan.node_metadata.values().find(|meta| meta.loop_id.is_none()).unwrap();
    assert!(node_loop_path(&plan, ordinary).unwrap().is_empty());

    plan.compiler_version = NESTED_FIXED_LOOP_COMPILER_VERSION.into();
    assert!(node_loop_path(&plan, &legacy).is_err());
    let mut nested = legacy.clone();
    nested.loop_path = Some(vec![
        bcs_domain::StateMachineLoopFrame { loop_id: "outer".into(), iteration: 2, max_iterations: 2 },
        bcs_domain::StateMachineLoopFrame {
            loop_id: nested.loop_id.clone().unwrap(),
            iteration: nested.iteration.unwrap(),
            max_iterations: nested.max_iterations.unwrap(),
        },
    ]);
    assert_eq!(node_loop_path(&plan, &nested).unwrap(), nested.loop_path.clone().unwrap());
    nested.loop_path.as_mut().unwrap().last_mut().unwrap().iteration += 1;
    assert!(node_loop_path(&plan, &nested).is_err());
    nested.loop_path.as_mut().unwrap().last_mut().unwrap().iteration = 0;
    assert!(node_loop_path(&plan, &nested).is_err());
}

#[test]
fn compiler_v2_snapshot_round_trips_and_rejects_rehashed_path_corruption() {
    let loaded = load_state_machine_snapshot(nested_fixture()).unwrap();
    let plan = loaded.plan.unwrap();
    assert_eq!(plan.state_machine.nodes.len(), 17);
    assert!(plan.node_metadata.values().any(|meta|
        meta.loop_path.as_ref().is_some_and(|path| path.len() == 2)));

    for mutation in 0..5 {
        let mut snapshot = nested_fixture();
        let plan = &mut snapshot.execution_plan.as_mut().unwrap().plan;
        match mutation {
            0 => plan.node_metadata.values_mut().find(|meta|
                meta.loop_path.as_ref().is_some_and(|path| path.len() == 2))
                .unwrap().loop_path.as_mut().unwrap()[0].iteration = 3,
            1 => plan.node_metadata.values_mut().find(|meta|
                meta.loop_path.as_ref().is_some_and(|path| path.len() == 2))
                .unwrap().loop_path = None,
            2 => {
                let wrong = plan.node_metadata.values().find(|candidate|
                    candidate.loop_path.as_ref().is_some_and(|path|
                        path.len() == 2 && path[0].iteration == 2)
                        && candidate.is_loop_result).unwrap().execution_node_id.clone();
                let meta = plan.node_metadata.values_mut().find(|meta|
                    meta.loop_path.as_ref().is_some_and(|path|
                        path.len() == 2 && path[0].iteration == 1)
                        && meta.is_loop_entry && meta.iteration == Some(2))
                    .unwrap();
                meta.previous_result_node_id = Some(wrong);
            }
            3 => { plan.edge_metadata.pop(); }
            _ => snapshot.definition.requires.as_mut().unwrap().server_features
                .retain(|feature| feature != "state_machine.loop.nesting"),
        }
        rehash(&mut snapshot);
        assert!(load_state_machine_snapshot(snapshot).is_err(), "mutation {mutation}");
    }
}
