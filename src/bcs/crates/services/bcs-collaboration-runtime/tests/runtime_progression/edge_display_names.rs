use super::*;
use bcs_service_api::StateMachineRunGraphView;

#[tokio::test]
async fn graph_and_rerun_keep_saved_display_names_and_exhausted_actual_outcomes() {
    let h = Harness::new(&["again", "again"]).await;
    let mut definition: Value = serde_yaml::from_str(&loop_yaml(2, true, false, 1)).unwrap();
    definition["participants"]["driver"]["display_name"] = json!("主编");
    let nodes = &mut definition["runtime"]["state_machine"]["nodes"];
    nodes["rounds"]["loop"]["continue_display_name"] = json!("继续修订");
    nodes["rounds"]["transitions"]["done"]["display_name"] = json!("评审通过");
    nodes["rounds"]["transitions"]["exhausted"]["display_name"] = json!("转入重写");
    nodes["fallback"]["transitions"]["complete"]["display_name"] = json!("提交汇总");
    let started = h.start(serde_yaml::to_string(&definition).unwrap(), false).await;
    let run = &started.view.run;
    let before = h.store.get_run_snapshot(&run.run_id).await.unwrap().unwrap();
    h.finish(0, run, "first").await;
    h.finish(1, run, "second").await;
    let mut changed = before.definition.clone();
    changed.participants.get_mut("driver").unwrap().display_name = Some("新的角色名称".into());
    if let CollaborationRuntimeDefinition::StateMachine(machine) = &mut changed.runtime {
        machine.nodes.get_mut("rounds").unwrap().loop_definition.as_mut().unwrap().continue_display_name = Some("Changed after start".into());
    }
    changed.version += 1;
    h.definitions.upsert(changed).await.unwrap();
    let graph = h.runtime.get_state_machine_run_graph(&run.run_id).await.unwrap().unwrap();
    assert_names(&graph);
    let exhausted = graph.edges.iter().find(|edge| edge.loop_route.as_ref().is_some_and(|route|
        route.kind == bcs_domain::StateMachineLoopRouteKind::Exhausted)).unwrap();
    let source = graph.nodes.iter().find(|node| node.node_id == exhausted.source).unwrap();
    assert_eq!(source.outcome.as_deref(), Some("again"));
    assert_eq!(source.status, Some(StateMachineNodeStatus::Completed));
    h.finish(2, run, "rewritten").await;
    assert_eq!(h.fail(3, run).await.view.unwrap().run.status, StateMachineRunStatus::Failed);
    wait_for_callback_status(&h.sessions, &run.session_id, "not_applicable").await;
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    let rerun = h.runtime.rerun_state_machine_run(RerunStateMachineCommand {
        source_run_id: run.run_id.clone(), authenticated_human: None,
    }).await.unwrap();
    assert_names(&h.runtime.get_state_machine_run_graph(&rerun.view.run.run_id).await.unwrap().unwrap());
    let after = h.store.get_run_snapshot(&rerun.view.run.run_id).await.unwrap().unwrap();
    assert_eq!(before.execution_plan.unwrap().content_hash, after.execution_plan.unwrap().content_hash);
}

fn assert_names(graph: &StateMachineRunGraphView) {
    for node in &graph.nodes {
        assert_eq!(node.assignee_display_name.as_deref(), Some("主编"));
        assert_eq!(serde_json::to_value(&node.assignee).unwrap()["binding"], "driver");
    }
    assert_eq!(graph.loops["rounds"].continue_display_name.as_deref(), Some("继续修订"));
    for edge in &graph.edges {
        let expected = match edge.loop_route.as_ref().map(|route| route.kind) {
            Some(bcs_domain::StateMachineLoopRouteKind::Continue) => "继续修订",
            Some(bcs_domain::StateMachineLoopRouteKind::Break) => "评审通过",
            Some(bcs_domain::StateMachineLoopRouteKind::Exhausted) => {
                assert_eq!(edge.outcome, "again");
                "转入重写"
            }
            None => "提交汇总",
        };
        assert_eq!(edge.display_name.as_deref(), Some(expected));
    }
}

#[tokio::test]
async fn v1_graph_uses_participant_name_and_omits_missing_or_blank_names() {
    for name in [None, Some(" \t "), Some("  资料研究员  ")] {
        let h = Harness::new(&[]).await;
        let mut definition: Value = serde_yaml::from_str(&join_yaml()).unwrap();
        if let Some(name) = name { definition["participants"]["driver"]["display_name"] = json!(name); }
        let started = h.start(serde_yaml::to_string(&definition).unwrap(), false).await;
        let graph = h.runtime.get_state_machine_run_graph(&started.view.run.run_id).await.unwrap().unwrap();
        let expected = name.map(str::trim).filter(|name| !name.is_empty());
        for node in graph.nodes {
            assert_eq!(node.assignee_display_name.as_deref(), expected);
            assert_eq!(serde_json::to_value(&node).unwrap().get("assignee_display_name").is_some(), expected.is_some());
        }
    }
}

#[tokio::test]
async fn human_actor_does_not_inherit_a_participant_display_name() {
    let h = Harness::new(&[]).await;
    let mut definition: Value = serde_yaml::from_str(&loop_yaml(1, false, true, 1)).unwrap();
    definition["participants"]["driver"]["display_name"] = json!("主编");
    let started = h.start(serde_yaml::to_string(&definition).unwrap(), true).await;
    let graph = h.runtime.get_state_machine_run_graph(&started.view.run.run_id).await.unwrap().unwrap();
    let human = graph.nodes.iter().find(|node| node.kind == bcs_domain::StateMachineNodeKind::HumanInput).unwrap();
    assert!(human.assignee_display_name.is_none());
    assert_eq!(graph.nodes.iter().find(|node| node.node_id == "publish").unwrap().assignee_display_name.as_deref(), Some("主编"));
}
