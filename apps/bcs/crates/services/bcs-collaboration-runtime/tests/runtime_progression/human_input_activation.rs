use super::{
    Arc,
    AuthenticatedHumanCaller,
    CollaborationRuntime,
    CollaborationRuntimeError,
    CollaborationRuntimeService,
    CreateOrReactivateCommand,
    GroupCoreService,
    GroupStore,
    HumanResponseSource,
    ListPendingHumanNodesCommand,
    MemoryCollaborationStore,
    MemoryGroupRepo,
    MemoryMessageRepo,
    MemorySessionRepo,
    NewSessionParams,
    Participant,
    ParticipantMode,
    ParticipantRole,
    RecordingDelivery,
    RecordingSessionChannelOutbound,
    RespondHumanNodeCommand,
    SessionKind,
    SessionManagementService,
    SessionManagementServiceImpl,
    StartStateMachineRunCommand,
    StateMachineNodeStatus,
    StateMachineRunStatus,
    frontend_human_input_yaml,
    human_input_yaml,
    json,
    noop_judge,
    state_machine_test_group,
    test_sessions
};

#[tokio::test]
async fn human_input_without_authenticated_or_present_human_is_invalid_request() {
    let group = Arc::new(GroupStore::new());
    group
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
        store,
        group.clone(),
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    );

    let error = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("human_untrusted".to_string()),
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect_err("HumanInput must require a Present Human session participant");

    assert!(matches!(
        error,
        CollaborationRuntimeError::InvalidRequest(message)
            if message.contains("Present Human session participant")
    ));
    assert!(delivery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn human_input_can_start_without_authenticated_human_when_session_has_present_human() {
    let group = Arc::new(GroupStore::new());
    let seeded_group = state_machine_test_group();
    group
        .upsert(seeded_group.clone())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let mut reviewer = Participant::human("human_1001", ParticipantRole::Observer);
    reviewer.mode = Some(ParticipantMode::Present);
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: seeded_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: vec![reviewer],
                ..Default::default()
            },
        })
        .await
        .expect("seed session")
        .session;
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

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: seeded_group.id,
            session_id: Some(session.id),
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("bot_driver".to_string()),
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("existing Present Human should allow service invocation");

    assert_eq!(
        started.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );
}

#[tokio::test]
async fn authenticated_human_is_added_or_restored_before_human_input_starts() {
    let group = Arc::new(GroupStore::new());
    let seeded_group = state_machine_test_group();
    group
        .upsert(seeded_group.clone())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: seeded_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: seeded_group.participants.clone(),
                ..Default::default()
            },
        })
        .await
        .expect("seed session")
        .session;
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

    runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: seeded_group.id.clone(),
            session_id: Some(session.id.clone()),
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("bot_driver".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
            caller_association: None,
        })
        .await
        .expect("authenticated Human should be materialized into session");

    let updated = sessions
        .get(&session.id)
        .await
        .expect("read session")
        .expect("session exists");
    let reviewer = updated
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_1001")
        .expect("Human added");
    assert!(reviewer.is_human());
    assert_eq!(reviewer.role, ParticipantRole::Observer);
    assert_eq!(reviewer.effective_mode(), ParticipantMode::Present);
    assert_eq!(reviewer.bot_name.as_deref(), Some("Reviewer"));

    let mut returning_reviewer = Participant::human("human_2002", ParticipantRole::Observer);
    returning_reviewer.mode = Some(ParticipantMode::Absent);
    let returning_session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: seeded_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: vec![returning_reviewer],
                ..Default::default()
            },
        })
        .await
        .expect("seed session with absent Human")
        .session;

    runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: seeded_group.id,
            session_id: Some(returning_session.id.clone()),
            definition_yaml: Some(
                human_input_yaml()
                    .replace("human_input_single", "returning_human_input")
                    .replace("human_1001", "human_2002"),
            ),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("bot_driver".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_2002".to_string(),
                display_name: Some("Returning Reviewer".to_string()),
            }),
            caller_association: None,
        })
        .await
        .expect("authenticated Human should be restored to Present");

    let updated = sessions
        .get(&returning_session.id)
        .await
        .expect("read returning session")
        .expect("returning session exists");
    let reviewer = updated
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_2002")
        .expect("returning Human retained");
    assert_eq!(reviewer.effective_mode(), ParticipantMode::Present);
}

#[tokio::test]
async fn frontend_human_input_skips_im_delivery_and_accepts_present_human() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let session_repo = Arc::new(MemorySessionRepo::new());
    let sessions = Arc::new(SessionManagementServiceImpl::new(session_repo.clone(), Arc::new(MemoryGroupRepo::new())));
    let store = Arc::new(MemoryCollaborationStore::new().with_session_repo(session_repo));
    let channel_outbound = Arc::new(RecordingSessionChannelOutbound::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    )
    .with_session_channel_outbound(channel_outbound.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(frontend_human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "review in frontend"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
            caller_association: None,
        })
        .await
        .expect("start frontend HumanInput run");

    assert_eq!(
        started.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );
    assert!(channel_outbound.events.lock().await.is_empty());

    let pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(),
            caller_actor_id: "human_1001".to_string(),
        })
        .await
        .expect("frontend Human lists pending node");
    assert_eq!(pending.len(), 1);

    let completed = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "approved in frontend".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect("Present Human responds from frontend");

    assert_eq!(completed.node.status, StateMachineNodeStatus::Completed);
    assert_eq!(completed.run.status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn im_human_input_rejects_a_different_present_human() {
    let group = Arc::new(GroupStore::new());
    let seeded_group = state_machine_test_group();
    group
        .upsert(seeded_group.clone())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let mut assigned = Participant::human("human_1001", ParticipantRole::Observer);
    assigned.mode = Some(ParticipantMode::Present);
    let mut other = Participant::human("human_2002", ParticipantRole::Observer);
    other.mode = Some(ParticipantMode::Present);
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: seeded_group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: vec![assigned, other],
                ..Default::default()
            },
        })
        .await
        .expect("seed session")
        .session;
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
            group_id: seeded_group.id,
            session_id: Some(session.id),
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "assigned review"}),
            caller_id: Some("bot_driver".to_string()),
            authenticated_human: None, caller_association: None,
        })
        .await
        .expect("start assigned IM HumanInput run");

    let pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(),
            caller_actor_id: "human_2002".to_string(),
        })
        .await
        .expect("other Present Human may inspect the run");
    assert!(pending.is_empty());

    let error = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "review".to_string(),
            caller_actor_id: "human_2002".to_string(),
            content: "attempted response".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("IM HumanInput must reject a non-assignee");
    assert!(matches!(error, CollaborationRuntimeError::Forbidden(_)));
}
