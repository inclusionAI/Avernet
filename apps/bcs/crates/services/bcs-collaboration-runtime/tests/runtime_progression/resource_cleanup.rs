use super::{
    Arc,
    CollaborationRuntime,
    CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand,
    GroupCoreService,
    GroupDeleteCommand,
    GroupManagement,
    GroupManagementService,
    GroupManagementWithRuntimeCleanup,
    GroupRuntimeBindingRepoPort,
    GroupStore,
    MemoryCollaborationStore,
    MemoryMessageRepo,
    NoopBotRegistryCoreService,
    NoopFriendCoreService,
    RecordingDelivery,
    SessionManagementService,
    SessionManagementWithRuntimeCleanup,
    StartStateMachineRunCommand,
    StateMachineRunRepoPort,
    StateMachineRunStatus,
    json,
    noop_judge,
    single_node_yaml,
    test_group,
    test_sessions
};

#[tokio::test]
async fn deleting_session_aborts_all_active_state_machine_runs() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = Arc::new(test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group.clone(),
        sessions.clone(),
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    ));
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "delete the session"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    let mut second_run = started.view.run.clone();
    second_run.run_id = "session-cleanup-second-run".to_string();
    second_run.created_at = second_run.created_at.saturating_add(1);
    second_run.updated_at = second_run.created_at;
    StateMachineRunRepoPort::create_run(&*store, second_run.clone(), Vec::new())
        .await
        .expect("seed second active run for the session");
    let service = SessionManagementWithRuntimeCleanup::new(sessions.clone(), runtime);

    let deleted = service
        .delete(&started.view.run.session_id)
        .await
        .expect("delete session and cancel runs");

    assert!(deleted);
    assert!(
        sessions
            .get(&started.view.run.session_id)
            .await
            .expect("read deleted session")
            .is_none()
    );
    for run_id in [&started.view.run.run_id, &second_run.run_id] {
        let run = StateMachineRunRepoPort::get_run(&*store, run_id)
            .await
            .expect("read aborted run")
            .expect("aborted run remains for audit");
        assert_eq!(run.status, StateMachineRunStatus::Aborted);
        assert_eq!(run.error.as_deref(), Some("session_deleted"));
    }
}

#[tokio::test]
async fn deleting_group_aborts_active_state_machine_runs() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = Arc::new(test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group.clone(),
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    ));
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "delete the group"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    let group_management = Arc::new(GroupManagement::with_defaults(
        group,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
    ));
    let service = GroupManagementWithRuntimeCleanup::new(group_management, runtime);

    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver-bot".to_string(),
            group_id: "group-1".to_string(),
        })
        .await
        .expect("delete group and runtime state");

    assert!(deleted.deleted);
    let run = StateMachineRunRepoPort::get_run(&*store, &started.view.run.run_id)
        .await
        .expect("read aborted run")
        .expect("aborted run remains for audit");
    assert_eq!(run.status, StateMachineRunStatus::Aborted);
    assert_eq!(run.error.as_deref(), Some("group_deleted"));
}

#[tokio::test]
async fn retrying_deleted_group_cleanup_aborts_orphaned_active_runs() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = Arc::new(test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group.clone(),
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    ));
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "recover orphaned run"}),
            caller_id: None,
            authenticated_human: None,
        })
        .await
        .expect("start run");
    group
        .delete("group-1")
        .await
        .expect("simulate partial delete");
    let group_management = Arc::new(GroupManagement::with_defaults(
        group,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
    ));
    let service = GroupManagementWithRuntimeCleanup::new(group_management, runtime);

    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver-bot".to_string(),
            group_id: "group-1".to_string(),
        })
        .await
        .expect("retry group runtime cleanup");

    assert!(!deleted.deleted);
    let run = StateMachineRunRepoPort::get_run(&*store, &started.view.run.run_id)
        .await
        .expect("read recovered run")
        .expect("run remains for audit");
    assert_eq!(run.status, StateMachineRunStatus::Aborted);
    assert_eq!(run.error.as_deref(), Some("group_deleted"));
}

#[tokio::test]
async fn group_runtime_cleanup_aborts_runs_and_removes_sessions_and_binding() {
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
        sessions.clone(),
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: Default::default(),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "review this"}),
            caller_id: Some("driver-bot".to_string()),
            authenticated_human: None,
        })
        .await
        .expect("start run");
    let mut second_run = started.view.run.clone();
    second_run.run_id = "group-cleanup-second-run".to_string();
    second_run.created_at = second_run.created_at.saturating_add(1);
    second_run.updated_at = second_run.created_at;
    StateMachineRunRepoPort::create_run(&*store, second_run.clone(), Vec::new())
        .await
        .expect("seed second active run for the same session");

    runtime
        .cancel_group_runs("group-1", "group_deleted")
        .await
        .expect("cancel active group runs");
    let aborted = StateMachineRunRepoPort::get_run(&*store, &started.view.run.run_id)
        .await
        .expect("read cancelled run")
        .expect("cancelled run remains for audit");
    assert_eq!(aborted.status, StateMachineRunStatus::Aborted);
    let second_aborted = StateMachineRunRepoPort::get_run(&*store, &second_run.run_id)
        .await
        .expect("read second cancelled run")
        .expect("second cancelled run remains for audit");
    assert_eq!(second_aborted.status, StateMachineRunStatus::Aborted);

    runtime
        .delete_group_runtime_state("group-1")
        .await
        .expect("delete runtime state");
    assert!(
        sessions
            .get(&started.view.run.session_id)
            .await
            .expect("read deleted session")
            .is_none()
    );
    assert!(
        GroupRuntimeBindingRepoPort::get(&*store, "group-1")
            .await
            .expect("read deleted binding")
            .is_none()
    );
    runtime
        .delete_group_runtime_state("group-1")
        .await
        .expect("runtime state deletion is idempotent");
}
