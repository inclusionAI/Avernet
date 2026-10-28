use super::{
    Arc,
    AuthenticatedHumanCaller,
    BcsFrame,
    CollaborationDefinition,
    CollaborationRuntime,
    CollaborationRuntimeDefinition,
    CollaborationRuntimeError,
    CollaborationRuntimeService,
    Duration,
    GroupCoreService,
    GroupStore,
    HumanResponseSource,
    MemoryCollaborationStore,
    MemoryMessageRepo,
    RecordingDelivery,
    RejectingDelivery,
    RespondHumanNodeCommand,
    SessionManagementService,
    StartStateMachineRunCommand,
    StateMachineDefinitionRepoPort,
    StateMachineNodeStatus,
    StateMachineRunRepoPort,
    StateMachineRunStatus,
    StateMachineTransition,
    Value,
    human_input_yaml,
    json,
    noop_judge,
    single_node_yaml,
    test_group,
    test_sessions
};

#[tokio::test]
async fn human_input_timeout_ignores_global_retries_and_rejects_late_response() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );
    let definition_yaml = human_input_yaml()
        .replace(
            "    nodes:\n",
            "    defaults:\n      max_attempts: 3\n    nodes:\n",
        )
        .replace("60000", "1");
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(definition_yaml),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start Human timeout run");
    tokio::time::sleep(Duration::from_millis(5)).await;

    let processed = runtime
        .process_expired_node_timeouts(10, 0)
        .await
        .expect("scan Human timeout");
    assert_eq!(processed, 1);
    let view = runtime
        .get_state_machine_run(&started.view.run.run_id)
        .await
        .expect("get timed out run")
        .expect("timed out run");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(view.nodes[0].status, StateMachineNodeStatus::Failed);
    assert_eq!(view.nodes[0].attempt, 0);
    assert_eq!(view.nodes[0].max_attempts, 1);
    assert!(delivery.commands.lock().await.is_empty());

    let late = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "批准".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("late Human response must be rejected");
    assert!(matches!(late, CollaborationRuntimeError::Conflict(_)));
}

#[tokio::test]
async fn timeout_scanner_aborts_run_when_group_is_missing() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group.clone(),
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(
                single_node_yaml()
                    .replace("node_timeout_ms: 60000", "node_timeout_ms: 1000")
                    .replace("max_attempts: 3", "max_attempts: 1"),
            ),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "group will be deleted"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    group.delete("group-1").await.expect("delete group");
    let deadline = started.view.nodes.iter()
        .find_map(|node| node.timeout_deadline_ms)
        .expect("saved deadline");
    tokio::time::sleep(Duration::from_millis(
        deadline.saturating_sub(bcs_protocol::now_ms()) + 5,
    )).await;

    let processed = runtime
        .process_expired_node_timeouts(10, 0)
        .await
        .expect("scan missing-group timeout");

    assert_eq!(processed, 1);
    let view = runtime
        .get_state_machine_run(&started.view.run.run_id)
        .await
        .expect("get aborted run")
        .expect("aborted run");
    assert_eq!(view.run.status, StateMachineRunStatus::Aborted);
    assert_eq!(view.run.error.as_deref(), Some("group_not_found"));
}

#[tokio::test]
async fn timeout_scanner_aborts_run_when_session_is_missing() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions.clone(),
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(
                single_node_yaml()
                    .replace("node_timeout_ms: 60000", "node_timeout_ms: 1000")
                    .replace("max_attempts: 3", "max_attempts: 1"),
            ),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "session will be deleted"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    sessions
        .delete(&started.view.run.session_id)
        .await
        .expect("delete session");
    let deadline = started.view.nodes.iter()
        .find_map(|node| node.timeout_deadline_ms)
        .expect("saved deadline");
    tokio::time::sleep(Duration::from_millis(
        deadline.saturating_sub(bcs_protocol::now_ms()) + 5,
    )).await;

    let processed = runtime
        .process_expired_node_timeouts(10, 0)
        .await
        .expect("scan missing-session timeout");

    assert_eq!(processed, 1);
    let view = runtime
        .get_state_machine_run(&started.view.run.run_id)
        .await
        .expect("get aborted run")
        .expect("aborted run");
    assert_eq!(view.run.status, StateMachineRunStatus::Aborted);
    assert_eq!(view.run.error.as_deref(), Some("session_not_found"));
}

#[tokio::test]
async fn timeout_scanner_skips_invalid_candidate_and_processes_later_run() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    let valid_yaml = single_node_yaml()
        .replace("id: single_node", "id: valid_timeout")
        .replace("node_timeout_ms: 60000", "node_timeout_ms: 1000")
        .replace("max_attempts: 3", "max_attempts: 1");
    let valid = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(valid_yaml),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "valid timeout"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start valid run");

    let mut invalid_definition: CollaborationDefinition = serde_yaml::from_str(
        &single_node_yaml().replace("id: single_node", "id: invalid_snapshot"),
    )
    .expect("parse invalid-definition fixture base");
    let CollaborationRuntimeDefinition::StateMachine(state_machine) =
        &mut invalid_definition.runtime
    else {
        panic!("fixture must be a state machine");
    };
    state_machine
        .nodes
        .get_mut("answer")
        .expect("answer node")
        .transitions
        .insert(
            "complete".to_string(),
            StateMachineTransition {
                display_name: None,
                targets: vec!["answer".to_string()],
                guard: None,
            },
        );
    StateMachineDefinitionRepoPort::upsert(&*store, invalid_definition)
        .await
        .expect("seed invalid historical definition");

    let mut poison_run = valid.view.run.clone();
    poison_run.run_id = "invalid-timeout-candidate".to_string();
    poison_run.definition_id = "invalid_snapshot".to_string();
    poison_run.created_at = 1;
    poison_run.updated_at = 1;
    let mut poison_node =
        StateMachineRunRepoPort::get_node_run(&*store, &valid.view.run.run_id, "answer")
            .await
            .expect("read valid node")
            .expect("valid node");
    poison_node.run_id = poison_run.run_id.clone();
    poison_node.timeout_deadline_ms = Some(1);
    StateMachineRunRepoPort::create_run(&*store, poison_run.clone(), vec![poison_node])
        .await
        .expect("seed invalid timeout candidate");
    let deadline = valid.view.nodes.iter().find_map(|node| node.timeout_deadline_ms).expect("saved deadline");
    tokio::time::sleep(Duration::from_millis(deadline.saturating_sub(bcs_protocol::now_ms()) + 5)).await;

    let processed = runtime
        .process_expired_node_timeouts(10, 0)
        .await
        .expect("scan timeout candidates");

    assert_eq!(processed, 1);
    let poison = StateMachineRunRepoPort::get_run(&*store, &poison_run.run_id)
        .await
        .expect("read invalid run")
        .expect("invalid run");
    assert_eq!(poison.status, StateMachineRunStatus::Running);
    assert!(runtime.process_expired_node_timeouts(10, 0).await
        .expect_err("remaining poison candidate must trigger scanner backoff")
        .to_string().contains("timeout recovery made no progress"));
    let valid = StateMachineRunRepoPort::get_run(&*store, &valid.view.run.run_id)
        .await
        .expect("read valid run")
        .expect("valid run");
    assert_eq!(valid.status, StateMachineRunStatus::Failed);
}

#[tokio::test]
async fn state_machine_explicit_node_timeout_overrides_provider_chat_default() {
    const CONFIGURED_TIMEOUT_MS: u64 = 7_200_000;

    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        test_sessions(),
        delivery.clone(),
        noop_judge(),
    )
    .with_provider_chat_run_timeout_ms(CONFIGURED_TIMEOUT_MS);

    runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "stream this"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");

    let commands = delivery.commands.lock().await;
    let BcsFrame::Request(request) = &commands[0].frame else {
        panic!("state-machine bot delivery must use a request frame");
    };
    let timeout_ms = request
        .params
        .as_ref()
        .and_then(|params| params.get("timeout_ms"))
        .and_then(Value::as_u64)
        .expect("explicit node timeout must reach Provider chat.send");
    assert!(timeout_ms > 0);
    assert!(timeout_ms <= 60_000);
    assert_ne!(timeout_ms, CONFIGURED_TIMEOUT_MS);
}

#[tokio::test]
async fn start_run_fails_and_marks_node_failed_when_delivery_returns_not_delivered() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RejectingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    let result = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "review this"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await;

    let error = result.err().expect("delivery rejection should fail start");
    assert!(
        error
            .to_string()
            .contains("state-machine node delivery failed")
    );
    let commands = delivery.commands.lock().await;
    let delivery_id = commands[0].run_id.clone();
    drop(commands);
    let run_id = delivery_id
        .strip_prefix("smnode-")
        .and_then(|value| value.strip_suffix("-answer-0"))
        .expect("state-machine delivery id should include run id");
    let view = runtime
        .get_state_machine_run(run_id)
        .await
        .expect("get failed run")
        .expect("failed run should be persisted");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(view.nodes[0].status, StateMachineNodeStatus::Failed);
    assert!(
        view.nodes[0]
            .error
            .as_ref()
            .expect("node error")
            .contains("not connected")
    );
}
