use super::{
    Arc,
    CollaborationRuntime,
    CollaborationRuntimeService,
    FailingAppendMessageRepo,
    GroupCoreService,
    GroupStore,
    MemoryCollaborationStore,
    MemoryMessageRepo,
    MessageOwnerFilter,
    MessageQuery,
    MessageRepoPort,
    OpeningMessage,
    RecordingDelivery,
    STATE_MACHINE_PANEL_MESSAGE_TYPE,
    SessionManagementService,
    StartStateMachineRunCommand,
    StateMachineRunRepoPort,
    StateMachineRunStatus,
    Value,
    noop_judge,
    single_node_yaml,
    state_machine_test_group,
    test_sessions
};

#[tokio::test]
async fn custom_opening_message_is_rendered_once_and_reused_from_history() {
    let groups = Arc::new(GroupStore::new());
    let mut group = state_machine_test_group();
    group.label = Some("发布检查".to_string());
    group.opening_message = Some(OpeningMessage::Text(
        "群={{bcs.group_name}} session={{bcs.session_name}} group={{bcs.group_id}} session_id={{bcs.session_id}} run={{bcs.run_id}}"
            .to_string(),
    ));
    groups.upsert(group.clone()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let message_repo = Arc::new(MemoryMessageRepo::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        groups.clone(),
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    )
    .with_message_repo(message_repo.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: group.id.clone(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: Value::Null,
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    assert!(
        started.view.run.opening_message_override.is_none(),
        "configured StateMachine Runs must keep the request override unset"
    );
    let expected = format!(
        "群=发布检查 session=Single Node group=group-1 session_id={} run={}",
        started.view.run.session_id, started.view.run.run_id
    );
    let persisted = message_repo
        .query_messages(MessageQuery {
            group_id: group.id.clone(),
            session_id: started.view.run.session_id.clone(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: Some(STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string()),
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
            human_view: None,
        })
        .await
        .expect("query opening message");
    assert_eq!(persisted.messages[0].content["text"], expected);

    group.opening_message = Some(OpeningMessage::Text("后来修改".to_string()));
    groups.upsert(group).await.expect("update group template");
    let history = runtime
        .get_state_machine_session_history(&started.view.run.session_id, 20, None)
        .await
        .expect("history")
        .expect("state-machine history");
    assert_eq!(history.messages[0].content, expected);

    let next = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: Value::Null,
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start next run");
    let next_messages = message_repo
        .query_messages(MessageQuery {
            group_id: "group-1".to_string(),
            session_id: next.view.run.session_id,
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: Some(STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string()),
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
            human_view: None,
        })
        .await
        .expect("query next opening message");
    assert_eq!(next_messages.messages[0].content["text"], "后来修改");
}

#[tokio::test]
async fn opening_message_persistence_failure_fails_run_before_node_dispatch() {
    let groups = Arc::new(GroupStore::new());
    groups
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        groups,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    )
    .with_message_repo(Arc::new(FailingAppendMessageRepo::default()));

    let error = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: Value::Null,
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect_err("opening message persistence failure must abort startup");
    assert!(
        error
            .to_string()
            .contains("panel history persistence failed")
    );
    assert!(delivery.commands.lock().await.is_empty());

    let session = sessions
        .list_by_group("group-1", None, 0, 10, None, None)
        .await
        .expect("list state-machine session")
        .into_iter()
        .next()
        .expect("state-machine session");
    let run = StateMachineRunRepoPort::get_run_by_session_id(&*store, &session.id)
        .await
        .expect("load failed run")
        .expect("failed run");
    assert_eq!(run.status, StateMachineRunStatus::Failed);
}
