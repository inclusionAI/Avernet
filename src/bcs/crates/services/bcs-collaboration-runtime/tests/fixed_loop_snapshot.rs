use bcs_collaboration_runtime::fixed_loop::{compile_authoring_definition, execution_plan_content_hash};
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
