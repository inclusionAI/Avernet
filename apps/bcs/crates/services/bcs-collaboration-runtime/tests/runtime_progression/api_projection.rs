use super::*;
use bcs_domain::{StateMachineGraphMode, StateMachineLoopRouteKind};

pub(super) async fn read_only_runtime(h: &Harness) -> CollaborationRuntime {
    let groups = Arc::new(GroupStore::new()); groups.upsert(test_group()).await.unwrap();
    // History remains readable with execution disabled and stricter current limits.
    test_runtime!(h.definitions.clone(), h.store.clone(), h.store.clone(), h.store.clone(),
        groups, h.sessions.clone(), h.delivery.clone(), noop_judge())
        .with_fixed_loop_limits(bcs_config_api::FixedLoopLimits { max_fixed_loop_iterations: 1, ..Default::default() })
}

#[tokio::test]
async fn start_get_node_and_graph_share_complete_execution_metadata_and_real_routes() {
    let h = Harness::new(&["again", "again", "again"]).await;
    let started = h.start(loop_yaml(3, true, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    let metadata = started.view.node_execution_metadata.as_ref().unwrap();
    assert_eq!(metadata.len(), 3);
    for iteration in 1..=3 {
        let id = iteration_id(&plan, iteration);
        assert_eq!(serde_json::to_value(&metadata[&id]).unwrap(), json!({
            "definition_node_id": "work", "loop_id": "rounds", "iteration": iteration, "max_iterations": 3,
        }));
    }
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    let reader = read_only_runtime(&h).await;
    let queried = reader.get_state_machine_run(&run.run_id).await.unwrap().unwrap();
    assert_eq!(queried.node_execution_metadata, started.view.node_execution_metadata);
    let graph = reader.get_state_machine_run_graph(&run.run_id).await.unwrap().unwrap();
    let descriptor = &graph.loops["rounds"];
    assert_eq!(descriptor.display_name, "Rounds");
    assert_eq!(descriptor.max_iterations, 3);
    assert_eq!(descriptor.entry_node_id, "work");
    assert_eq!(descriptor.result_node_id, "work");
    assert_eq!(descriptor.body_node_ids, vec!["work"]);
    assert_eq!(descriptor.continue_outcomes, vec!["again"]);
    assert_eq!(descriptor.break_outcomes, vec!["done"]);
    assert_eq!(descriptor.exhausted_outcome, "exhausted");
    assert_eq!(graph.definition.graph_mode, StateMachineGraphMode::Hierarchical);
    assert_eq!(graph.definition.execution_graph_mode, Some(StateMachineGraphMode::Acyclic));
    assert_eq!(graph.definition.execution_plan_compiler_version.as_deref(), Some("bcs.fixed-loop.compiler/v1"));
    assert_eq!(graph.nodes.len(), started.view.nodes.len());
    assert_eq!(graph.definition.initial_nodes, vec![iteration_id(&plan, 1)]);
    assert!(!graph.nodes.iter().any(|node| node.node_id == "rounds"));
    for node in &graph.nodes {
        let single = reader.get_state_machine_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap();
        assert_eq!(node.execution.as_ref(), metadata.get(&node.node_id));
        assert_eq!(single.execution, node.execution);
        assert_eq!(single.node.node_id, node.node_id);
    }
    for edge in &graph.edges {
        match metadata.get(&edge.source) {
            Some(meta) => {
                let route = edge.loop_route.as_ref().unwrap();
                if edge.outcome == "done" {
                    assert_eq!(route.kind, StateMachineLoopRouteKind::Break);
                    assert_eq!(route.logical_outcome, "done");
                } else if meta.iteration == 3 {
                    assert_eq!(route.kind, StateMachineLoopRouteKind::Exhausted);
                    assert_eq!(route.logical_outcome, "exhausted");
                    assert_eq!(edge.outcome, "again");
                } else {
                    assert_eq!(route.kind, StateMachineLoopRouteKind::Continue);
                    assert_eq!(route.logical_outcome, "again");
                }
            }
            None => assert!(edge.loop_route.is_none()),
        }
    }
    // Actual outcomes remain unchanged when the final continuation exhausts the Loop.
    for index in 0..3 { h.finish(index, run, &format!("result-{index}")).await; }
    let last = reader.get_state_machine_node_run(&run.run_id, &iteration_id(&plan, 3)).await.unwrap().unwrap();
    assert_eq!(last.node.outcome.as_deref(), Some("again"));
    assert_eq!(last.execution.as_ref().unwrap().iteration, 3);
    assert_eq!(reader.get_state_machine_run(&run.run_id).await.unwrap().unwrap().node_execution_metadata, started.view.node_execution_metadata);
}

#[tokio::test]
async fn historical_projection_uses_saved_opaque_ids_without_current_definition_or_compiler() {
    let h = Harness::new(&[]).await;
    let started = h.start(loop_yaml(3, true, false, 1), false).await;
    let source = h.store.get_run_snapshot(&started.view.run.run_id).await.unwrap().unwrap();
    let mut plan = source.execution_plan.unwrap().plan;
    let rename: BTreeMap<_, _> = plan.node_metadata.iter().filter_map(|(id, meta)|
        meta.iteration.map(|iteration| (id.clone(), format!("historical-work-{iteration}")))).collect();
    let renamed = |id: &str| rename.get(id).cloned().unwrap_or_else(|| id.into());
    plan.state_machine.nodes = plan.state_machine.nodes.into_iter().map(|(id, mut node)| {
        for edge in node.transitions.values_mut() { edge.targets = edge.targets.iter().map(|id| renamed(id)).collect(); }
        (renamed(&id), node)
    }).collect();
    plan.node_metadata = plan.node_metadata.into_iter().map(|(id, mut meta)| {
        meta.execution_node_id = renamed(&meta.execution_node_id);
        meta.previous_result_node_id = meta.previous_result_node_id.as_deref().map(renamed);
        (renamed(&id), meta)
    }).collect();
    for edge in &mut plan.edge_metadata {
        edge.source_execution_node_id = renamed(&edge.source_execution_node_id);
        edge.target_execution_node_id = renamed(&edge.target_execution_node_id);
    }
    let mut run = started.view.run; run.run_id = "historical-api-run".into();
    let nodes = started.view.nodes.into_iter().map(|mut node| {
        node.run_id = run.run_id.clone(); node.node_id = renamed(&node.node_id); node
    }).collect();
    h.store.create_run(run.clone(), nodes).await.unwrap();
    let saved_plan = bcs_collaboration_runtime::snapshot::snapshot_execution_plan(plan).unwrap();
    h.store.save_run_snapshot(&run, run.group_version, &source.definition, source.resolved_participant_bindings.as_ref(), Some(&saved_plan)).await.unwrap();
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    let reader = read_only_runtime(&h).await;
    let view = reader.get_state_machine_run(&run.run_id).await.unwrap().unwrap();
    let fixture: Value = serde_json::from_str(include_str!("../../../../../tests/fixtures/fixed_loop_api.json")).unwrap();
    assert_eq!(serde_json::to_value(view.node_execution_metadata).unwrap(), fixture["run"]["node_execution_metadata"]);
    let node = reader.get_state_machine_node_run(&run.run_id, "historical-work-3").await.unwrap().unwrap();
    assert_eq!(serde_json::to_value(node.execution).unwrap(), fixture["node"]["execution"]);
    assert!(reader.get_state_machine_node_run(&run.run_id, "work").await.unwrap().is_none());
    let graph = reader.get_state_machine_run_graph(&run.run_id).await.unwrap().unwrap();
    assert_eq!(serde_json::to_value(&graph.loops).unwrap(), fixture["graph"]["loops"]);
    assert_eq!(graph.definition.initial_nodes, vec!["historical-work-1"]);
    assert!(graph.edges.iter().any(|edge| edge.source == "historical-work-3" && edge.outcome == "again"
        && edge.loop_route.as_ref().is_some_and(|route| route.kind == StateMachineLoopRouteKind::Exhausted)));
}

#[tokio::test]
async fn corrupted_plan_is_rejected_by_all_execution_metadata_queries() {
    let h = Harness::new(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let id = started.view.node_execution_metadata.as_ref().unwrap().keys().next().unwrap();
    h.definitions.corrupt_read.store(true, Ordering::SeqCst);
    assert!(h.runtime.get_state_machine_run(&started.view.run.run_id).await.is_err());
    assert!(h.runtime.get_state_machine_node_run(&started.view.run.run_id, id).await.is_err());
    assert!(h.runtime.get_state_machine_run_graph(&started.view.run.run_id).await.is_err());
}

#[tokio::test]
async fn first_and_later_pending_human_context_matches_node_and_run_metadata() {
    let h = Harness::new(&[]).await;
    let started = h.start(loop_yaml(2, false, true, 1), true).await;
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    let reader = read_only_runtime(&h).await;
    for iteration in 1..=2 {
        let pending = reader.list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(), caller_actor_id: "human_1001".into(),
        }).await.unwrap().remove(0);
        let context = pending.loop_context.unwrap();
        let execution = &started.view.node_execution_metadata.as_ref().unwrap()[&pending.node_id];
        assert_eq!(context.iteration, iteration); assert_eq!(context.iteration, execution.iteration);
        assert_eq!(context.loop_id, execution.loop_id); assert_eq!(context.max_iterations, execution.max_iterations);
        if iteration == 1 { assert!(context.previous_result.is_none()); }
        else { assert_eq!(context.previous_result.unwrap().output, "saved human result"); }
        let node = reader.get_state_machine_node_run(&started.view.run.run_id, &pending.node_id).await.unwrap().unwrap();
        assert_eq!(node.execution.as_ref(), Some(execution));
        if iteration == 1 {
            h.runtime.respond_human_node(RespondHumanNodeCommand { run_id: started.view.run.run_id.clone(), node_id: pending.node_id,
                caller_actor_id: "human_1001".into(), content: "saved human result".into(), source: HumanResponseSource::Http }).await.unwrap();
        }
    }
}

#[tokio::test]
async fn v1_projections_omit_all_loop_fields_and_keep_legacy_queries_available() {
    let h = Harness::new(&[]).await;
    let mut definition: Value = serde_yaml::from_str(&loop_yaml(2, false, false, 1)).unwrap();
    let machine = &mut definition["runtime"]["state_machine"];
    machine["version"] = json!(1); machine["graph_mode"] = json!("acyclic");
    machine["nodes"].as_object_mut().unwrap().remove("rounds");
    let started = h.start(serde_yaml::to_string(&definition).unwrap(), false).await;
    let run_id = &started.view.run.run_id;
    assert!(serde_json::to_value(&started.view).unwrap().get("node_execution_metadata").is_none());
    let node = h.runtime.get_state_machine_node_run(run_id, "publish").await.unwrap().unwrap();
    assert!(serde_json::to_value(node).unwrap().get("execution").is_none());
    let graph = serde_json::to_value(h.runtime.get_state_machine_run_graph(run_id).await.unwrap().unwrap()).unwrap();
    assert_eq!(graph["definition"]["graph_mode"], "acyclic");
    assert!(graph["definition"].get("execution_graph_mode").is_none());
    assert!(graph["definition"].get("execution_plan_compiler_version").is_none());
    assert!(graph["nodes"][0].get("execution").is_none());
    // Old rows without snapshots can still be read without inventing metadata.
    let mut legacy = started.view.run; legacy.run_id = "pre-snapshot-run".into();
    let mut nodes = started.view.nodes;
    for node in &mut nodes { node.run_id = legacy.run_id.clone(); }
    h.store.create_run(legacy.clone(), nodes).await.unwrap();
    assert!(h.runtime.get_state_machine_run(&legacy.run_id).await.unwrap().unwrap().node_execution_metadata.is_none());
    assert!(h.runtime.get_state_machine_node_run(&legacy.run_id, "publish").await.unwrap().unwrap().execution.is_none());
}
