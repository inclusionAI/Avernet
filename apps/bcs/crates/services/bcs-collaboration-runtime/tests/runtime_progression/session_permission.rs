use super::{
    Arc,
    AuthenticatedHumanCaller,
    BTreeMap,
    CollaborationRuntime,
    CollaborationRuntimeError,
    CollaborationRuntimeService,
    CreateOrReactivateCommand,
    GroupCoreService,
    GroupStore,
    GroupStrategy,
    HumanRunAccessCommand,
    MemoryCollaborationStore,
    MemoryMessageRepo,
    MessageViewScope,
    NewSessionParams,
    Participant,
    ParticipantRole,
    RecordingDelivery,
    RuntimeParticipantBinding,
    SessionKind,
    SessionManagementService,
    SessionStateMachinePermissionCommand,
    StartSessionStateMachineRunCommand,
    StartStateMachineRunCommand,
    StateMachineNodeStatus,
    StateMachineRunRepoPort,
    StateMachineRunStatus,
    Value,
    human_input_yaml,
    json,
    noop_judge,
    one_shot_authoring_yaml,
    session_collaboration_group,
    state_machine_test_group,
    test_sessions
};

#[tokio::test]
async fn current_session_permission_is_owned_by_chat_and_manager_group_driver() {
    let group_store = Arc::new(GroupStore::new());
    let chat_group = session_collaboration_group(GroupStrategy::Chat);
    group_store
        .upsert(chat_group.clone())
        .await
        .expect("seed chat group");
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: chat_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: chat_group.participants.clone(),
                ..Default::default()
            },
        })
        .await
        .expect("seed chat session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group_store.clone(),
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let chat_owner = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id.clone(),
            caller_bot_id: "driver-bot".to_string(),
        })
        .await
        .expect("query chat owner permission");
    assert!(chat_owner.allowed);
    assert_eq!(chat_owner.reason_code, "allowed");
    assert_eq!(chat_owner.group_strategy, "chat");
    assert_eq!(chat_owner.group_owner_bot_id, "driver-bot");

    let chat_member = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id.clone(),
            caller_bot_id: "worker-bot".to_string(),
        })
        .await
        .expect("query chat member permission");
    assert!(!chat_member.allowed);
    assert_eq!(chat_member.reason_code, "caller_not_group_owner");
    let bypass = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session.id.clone(),
            caller_bot_id: "worker-bot".to_string(),
            definition_yaml: one_shot_authoring_yaml(),
            participant_bindings: BTreeMap::from([(
                "writer".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["worker-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            opening_message: None,
            input: Value::Null,
            judge_available: false,
        })
        .await
        .expect_err("run start must re-check permission server-side");
    assert!(matches!(
        bypass,
        CollaborationRuntimeError::Forbidden(message)
            if message.contains("caller_not_group_owner")
    ));

    let manager_group = session_collaboration_group(GroupStrategy::ManagerWorker);
    group_store
        .upsert(manager_group)
        .await
        .expect("switch to manager-worker group");
    let manager = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id.clone(),
            caller_bot_id: "driver-bot".to_string(),
        })
        .await
        .expect("query manager permission");
    assert!(manager.allowed);
    assert_eq!(manager.group_strategy, "manager_worker");

    let state_machine_group = session_collaboration_group(GroupStrategy::StateMachine);
    group_store
        .upsert(state_machine_group)
        .await
        .expect("switch to state-machine group");
    let unsupported = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id,
            caller_bot_id: "driver-bot".to_string(),
        })
        .await
        .expect("query unsupported strategy");
    assert!(!unsupported.allowed);
    assert_eq!(unsupported.reason_code, "unsupported_group_strategy");
}

#[tokio::test]
async fn legacy_chat_session_allows_one_shot_run_for_participant_human_view() {
    let group_store = Arc::new(GroupStore::new());
    let group = session_collaboration_group(GroupStrategy::Chat);
    group_store
        .upsert(group.clone())
        .await
        .expect("seed chat group");
    let mut participants = group.participants.clone();
    let mut human = Participant::human("human_player", ParticipantRole::Observer);
    human.message_view_scope = MessageViewScope::Participant;
    participants.push(human);
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants,
                message_visibility_version: 0,
                ..Default::default()
            },
        })
        .await
        .expect("seed legacy chat session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group_store,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    let started = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session.id.clone(),
            caller_bot_id: "driver-bot".to_string(),
            definition_yaml: one_shot_authoring_yaml(),
            participant_bindings: BTreeMap::from([(
                "writer".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["worker-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            opening_message: None,
            input: Value::Null,
            judge_available: false,
        })
        .await
        .expect("message scope must not block a one-shot state-machine run");

    assert_eq!(started.view.run.session_id, session.id);
    assert!(
        StateMachineRunRepoPort::get_run_by_session_id(&*store, &session.id)
            .await
            .expect("query started run")
            .is_some(),
    );
}

#[tokio::test]
async fn human_run_owner_can_cancel_through_human_access_api() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "cancel it"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start Human run");

    let cancelled = runtime
        .cancel_state_machine_run_for_human(
            HumanRunAccessCommand {
                run_id: started.view.run.run_id,
                caller_actor_id: "human_1001".to_string(),
            },
            Some("user cancelled".to_string()),
        )
        .await
        .expect("run owner cancels Human run");

    assert_eq!(cancelled.run.status, StateMachineRunStatus::Aborted);
    assert_eq!(cancelled.nodes[0].status, StateMachineNodeStatus::Running);
}
