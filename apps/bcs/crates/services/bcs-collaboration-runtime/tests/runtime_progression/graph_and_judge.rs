use super::{
    Arc,
    BTreeMap,
    CollaborationEventRepoPort,
    CollaborationRuntime,
    CollaborationRuntimeService,
    GroupCoreService,
    GroupStore,
    MemoryCollaborationStore,
    MemoryMessageRepo,
    RecordingDelivery,
    RecordingEventFactory,
    RecordingFrontendDelivery,
    RecordingJudge,
    StartStateMachineRunCommand,
    StateMachineNodeStatus,
    StateMachineRunStatus,
    Value,
    complete_with_text,
    join_yaml,
    json,
    judge_branch_yaml,
    judge_shared_merge_yaml,
    judge_timeout_yaml,
    noop_judge,
    test_group,
    test_sessions
};

#[tokio::test]
async fn graph_view_returns_snapshot_edges_and_node_status() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery,
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(join_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "graph"}),
            caller_id: None,
            authenticated_human: None, caller_association: None,
        })
        .await
        .expect("start run");

    let graph = runtime
        .get_state_machine_run_graph(&started.view.run.run_id)
        .await
        .expect("get graph")
        .expect("graph");

    assert_eq!(graph.definition.id, "join_graph");
    assert_eq!(graph.definition.initial_nodes, vec!["start"]);
    let mut edges = graph
        .edges
        .iter()
        .map(|edge| {
            (
                edge.source.as_str(),
                edge.outcome.as_str(),
                edge.target.as_str(),
            )
        })
        .collect::<Vec<_>>();
    edges.sort();
    assert_eq!(
        edges,
        vec![
            ("branch_b", "complete", "join"),
            ("branch_c", "complete", "join"),
            ("start", "complete", "branch_b"),
            ("start", "complete", "branch_c"),
        ]
    );
    let by_id = graph
        .nodes
        .iter()
        .map(|node| (node.node_id.as_str(), node.status))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(by_id["start"], Some(StateMachineNodeStatus::Running));
    assert_eq!(by_id["branch_b"], Some(StateMachineNodeStatus::Pending));
    assert_eq!(by_id["join"], Some(StateMachineNodeStatus::Pending));
}

#[tokio::test]
async fn complete_transitions_support_fan_out_and_implicit_all_join() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let event_factory = Arc::new(RecordingEventFactory::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    )
    .with_event_record_factory(event_factory.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(join_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "join"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run");

    assert_eq!(delivery.commands.lock().await.len(), 1);
    let first_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(&runtime, &first_run_id, &started.view.run.session_id, "a").await;
    assert_eq!(delivery.commands.lock().await.len(), 3);

    let branch_b_run_id = delivery.commands.lock().await[1].run_id.clone();
    let branch_c_run_id = delivery.commands.lock().await[2].run_id.clone();
    complete_with_text(
        &runtime,
        &branch_b_run_id,
        &started.view.run.session_id,
        "b",
    )
    .await;
    assert_eq!(
        delivery.commands.lock().await.len(),
        3,
        "join node must wait for the other upstream"
    );

    complete_with_text(
        &runtime,
        &branch_c_run_id,
        &started.view.run.session_id,
        "c",
    )
    .await;
    assert_eq!(delivery.commands.lock().await.len(), 4);

    let join_run_id = delivery.commands.lock().await[3].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &join_run_id,
        &started.view.run.session_id,
        "joined",
    )
    .await;
    let view = handled.view.expect("completed run");
    assert_eq!(view.run.output.as_deref(), Some("joined"));

    let predecessors_by_node = event_factory
        .events
        .lock()
        .expect("event recording lock")
        .iter()
        .filter(|event| event.event_type == "state_machine.node.started")
        .map(|event| {
            (
                event.data["node_id"].as_str().expect("node_id").to_string(),
                serde_json::from_value::<Vec<String>>(event.data["predecessor_node_ids"].clone())
                    .expect("predecessor_node_ids"),
            )
        })
        .collect::<BTreeMap<_, _>>();
    assert_eq!(predecessors_by_node["start"], Vec::<String>::new());
    assert_eq!(predecessors_by_node["branch_b"], ["start"]);
    assert_eq!(predecessors_by_node["branch_c"], ["start"]);
    assert_eq!(predecessors_by_node["join"], ["branch_b", "branch_c"]);
}

#[tokio::test]
async fn judged_node_routes_selected_outcome_and_skips_unselected_branch() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let judge = Arc::new(RecordingJudge::with_outcome("approved"));
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge.clone(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_branch_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run");

    assert_eq!(delivery.commands.lock().await.len(), 1);
    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;
    assert_eq!(delivery.commands.lock().await.len(), 2);
    assert_eq!(
        judge.requests.lock().await[0].allowed_outcomes,
        vec!["approved", "rejected"]
    );
    let judged_view = runtime
        .get_state_machine_run(&started.view.run.run_id)
        .await
        .expect("get judged run")
        .expect("run view");
    assert_eq!(judged_view.judge_outputs.len(), 1);
    assert_eq!(judged_view.judge_outputs[0].node_id, "review");
    assert_eq!(judged_view.judge_outputs[0].attempt, 0);
    assert_eq!(judged_view.judge_outputs[0].decision.outcome, "approved");
    let node_view = runtime
        .get_state_machine_node_run(&started.view.run.run_id, "review")
        .await
        .expect("get node run")
        .expect("node view");
    assert_eq!(node_view.node.node_id, "review");
    assert_eq!(node_view.judge_outputs.len(), 1);
    assert_eq!(node_view.judge_outputs[0].decision.outcome, "approved");

    let publish_run_id = delivery.commands.lock().await[1].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &publish_run_id,
        &started.view.run.session_id,
        "approved final",
    )
    .await;
    let view = handled.view.expect("completed run");

    assert_eq!(view.run.output.as_deref(), Some("approved final"));
    let by_id = view
        .nodes
        .iter()
        .map(|node| (node.node_id.as_str(), node.status))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(by_id["review"], StateMachineNodeStatus::Completed);
    assert_eq!(by_id["publish"], StateMachineNodeStatus::Completed);
    assert_eq!(by_id["revise"], StateMachineNodeStatus::Skipped);
    assert_eq!(by_id["manual_review"], StateMachineNodeStatus::Skipped);
}

#[tokio::test]
async fn judged_node_publishes_bot_output_but_not_judge_message_to_workbench() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let judge = Arc::new(RecordingJudge::with_outcome("approved"));
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge,
    )
    .with_frontend_delivery(frontend_delivery.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_branch_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;

    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 2);
    let panel_event: Value =
        serde_json::from_str(&frontend_commands[0].event_json).expect("panel event json");
    assert_eq!(
        panel_event["payload"]["metadata"]["state_machine"]["event"].as_str(),
        Some("panel")
    );
    let bot_event: Value =
        serde_json::from_str(&frontend_commands[1].event_json).expect("bot event json");
    assert_eq!(bot_event["event"].as_str(), Some("chat"));
    assert_eq!(bot_event["bot_uuid"].as_str(), Some("driver-bot"));
    assert_eq!(
        bot_event["payload"]["message"]["content"][0]["text"].as_str(),
        Some("candidate final answer")
    );
    assert_ne!(
        bot_event["payload"]["metadata"]["state_machine"]["event"].as_str(),
        Some("judge")
    );
}

#[tokio::test]
async fn judged_node_failure_records_runtime_event_and_fails_run() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let judge = Arc::new(RecordingJudge::with_error("judge provider timed out"));
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge,
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_branch_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;

    let view = handled.view.expect("failed run view");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(
        view.run.error.as_deref(),
        Some("judge failed for node review attempt 0: judge provider timed out")
    );
    let review = view
        .nodes
        .iter()
        .find(|node| node.node_id == "review")
        .expect("review node");
    assert_eq!(review.status, StateMachineNodeStatus::Failed);
    assert_eq!(
        review.error.as_deref(),
        Some("judge failed for node review attempt 0: judge provider timed out")
    );
    let failure_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "review",
        "state_machine.judge.failed",
    )
    .await
    .expect("list judge failure events");
    assert_eq!(failure_events.len(), 1);
    assert_eq!(failure_events[0].attempt, Some(0));
    assert_eq!(
        failure_events[0].payload["error"].as_str(),
        Some("judge provider timed out")
    );
    assert_eq!(
        failure_events[0].payload["reason"].as_str(),
        Some("judge_failed")
    );
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn judged_node_timeout_records_runtime_event_and_fails_run() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    // Leave startup enough wall time; the delayed Judge still deterministically exceeds its own timeout.
    let judge = Arc::new(RecordingJudge::with_delayed_outcome("approved", 1500));
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge,
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_timeout_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;

    let view = handled.view.expect("failed run view");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(
        view.run.error.as_deref(),
        Some("judge timed out for node review attempt 0 after 1000ms")
    );
    let review = view
        .nodes
        .iter()
        .find(|node| node.node_id == "review")
        .expect("review node");
    assert_eq!(review.status, StateMachineNodeStatus::Failed);
    assert_eq!(
        review.error.as_deref(),
        Some("judge timed out for node review attempt 0 after 1000ms")
    );
    let failure_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "review",
        "state_machine.judge.failed",
    )
    .await
    .expect("list judge failure events");
    assert_eq!(failure_events.len(), 1);
    assert_eq!(failure_events[0].attempt, Some(0));
    assert_eq!(
        failure_events[0].payload["reason"].as_str(),
        Some("judge_timeout")
    );
    assert_eq!(failure_events[0].payload["timeout_ms"].as_u64(), Some(1000));
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn judged_node_keeps_shared_merge_reachable_from_selected_branch() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        Arc::new(RecordingJudge::with_outcome("approved")),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_shared_merge_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "judge shared merge"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate",
    )
    .await;
    assert_eq!(delivery.commands.lock().await.len(), 2);

    let fast_run_id = delivery.commands.lock().await[1].run_id.clone();
    complete_with_text(&runtime, &fast_run_id, &started.view.run.session_id, "fast").await;
    assert_eq!(delivery.commands.lock().await.len(), 3);

    let merge_run_id = delivery.commands.lock().await[2].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &merge_run_id,
        &started.view.run.session_id,
        "merged",
    )
    .await;
    let view = handled.view.expect("completed run");
    assert_eq!(view.run.output.as_deref(), Some("merged"));
    let by_id = view
        .nodes
        .iter()
        .map(|node| (node.node_id.as_str(), node.status))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(by_id["fast"], StateMachineNodeStatus::Completed);
    assert_eq!(by_id["slow"], StateMachineNodeStatus::Skipped);
    assert_eq!(by_id["merge"], StateMachineNodeStatus::Completed);
}
