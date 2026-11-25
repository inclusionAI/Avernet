use super::{
    ActorKind,
    Arc,
    BTreeMap,
    ChatEventState,
    CollaborationRuntime,
    CollaborationRuntimeError,
    CollaborationRuntimeService,
    CreateOrReactivateCommand,
    GroupCoreService,
    GroupRuntimeBindingRepoPort,
    GroupStore,
    GroupStrategy,
    HandleSessionHumanInputCommand,
    HandleSessionHumanInputOutcome,
    HumanResponseSource,
    MemoryCollaborationStore,
    MemoryMessageRepo,
    MessageOwnerFilter,
    MessageQuery,
    MessageRepoPort,
    MessageViewScope,
    NewSessionParams,
    OpeningMessage,
    Participant,
    ParticipantMode,
    ParticipantRole,
    RecordingDelivery,
    RecordingFrontendDelivery,
    RecordingResultPublisher,
    RerunStateMachineCommand,
    RuntimeParticipantBinding,
    STATE_MACHINE_PANEL_MESSAGE_TYPE,
    SessionKind,
    SessionManagementService,
    SessionStateMachinePermissionCommand,
    SessionStatus,
    StartSessionStateMachineRunCommand,
    StateMachineDefinitionRepoPort,
    StateMachineNodeStatus,
    StateMachineRunRepoPort,
    StateMachineRunStatus,
    Value,
    json,
    noop_judge,
    one_shot_authoring_yaml,
    one_shot_human_input_authoring_yaml,
    session_collaboration_group,
    test_group,
    test_sessions
};

#[tokio::test]
async fn one_shot_session_run_uses_transient_bindings_keeps_chat_open_and_publishes_as_initiator() {
    let group_store = Arc::new(GroupStore::new());
    let mut group = test_group();
    group.opening_message = Some(OpeningMessage::Text(
        "Chat session opening {{bcs.session_id}}".to_string(),
    ));
    group_store.upsert(group.clone()).await.expect("seed group");
    let mut session_participants = group.participants.clone();
    session_participants.push(Participant {
        bot_uuid: "worker-bot".to_string(),
        bot_name: Some("Worker".to_string()),
        kind: None,
        role: ParticipantRole::Consultant,
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::Auto),
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
    });
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: session_participants,
                ..Default::default()
            },
        })
        .await
        .expect("seed chat session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let result_publisher = Arc::new(RecordingResultPublisher::default());
    let message_repo = Arc::new(MemoryMessageRepo::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group_store,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    )
    .with_frontend_delivery(frontend_delivery.clone())
    .with_message_repo(message_repo.clone())
    .with_result_publisher(result_publisher.clone());

    let invalid_opening_message = runtime
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
            opening_message: Some(OpeningMessage::Text("Invalid {{bcs.unknown}}".to_string())),
            input: Value::Null,
            judge_available: false,
            caller_association: None,
        })
        .await
        .expect_err("invalid one-shot opening message must be rejected");
    assert!(matches!(
        invalid_opening_message,
        CollaborationRuntimeError::InvalidRequest(message)
            if message.contains("invalid_opening_message")
    ));
    assert!(
        StateMachineRunRepoPort::get_run_by_session_id(&*store, &session.id)
            .await
            .expect("query run after invalid opening message")
            .is_none()
    );
    assert!(delivery.commands.lock().await.is_empty());

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
            input: json!({"question": "resolve this"}),
            judge_available: false,
            caller_association: None,
        })
        .await
        .expect("start one-shot run");

    assert_eq!(started.view.run.session_id, session.id);
    assert_eq!(started.view.run.created_by.as_deref(), Some("driver-bot"));
    assert_eq!(
        started.view.nodes[0].assignee_bot_id.as_deref(),
        Some("worker-bot")
    );
    assert_eq!(
        delivery.commands.lock().await[0].target_bot_id(),
        "worker-bot"
    );
    assert!(
        GroupRuntimeBindingRepoPort::get(&*store, "group-1")
            .await
            .expect("read persisted group binding")
            .is_none(),
        "one-shot role bindings must not mutate group runtime configuration"
    );
    assert!(
        StateMachineDefinitionRepoPort::get(
            &*store,
            &started.view.run.definition_id,
            started.view.run.definition_version,
        )
        .await
        .expect("read global definition store")
        .is_none(),
        "one-shot inline YAML must not create a reusable global definition"
    );
    let run_snapshot =
        StateMachineDefinitionRepoPort::get_run_snapshot(&*store, &started.view.run.run_id)
            .await
            .expect("read one-shot run snapshot")
            .expect("one-shot run snapshot");
    assert_eq!(run_snapshot.definition.id, started.view.run.definition_id);

    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 1);
    let panel_event: Value =
        serde_json::from_str(&frontend_commands[0].event_json).expect("panel event json");
    let panel_text = panel_event["payload"]["message"]["content"][0]["text"]
        .as_str()
        .expect("panel AixUI text");
    assert!(panel_text.contains("<AixUI"));
    assert!(panel_text.contains("bcsPanel.StateMachineRunView"));
    assert!(!panel_text.contains("Chat session opening"));
    drop(frontend_commands);

    let panel_history = message_repo
        .query_messages(MessageQuery {
            group_id: group.id.clone(),
            session_id: session.id.clone(),
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
        .expect("query persisted panel history");
    assert_eq!(panel_history.messages.len(), 1);
    let persisted_panel = &panel_history.messages[0];
    assert_eq!(
        persisted_panel.client_msg_id.as_deref(),
        Some(format!("{}:000-panel", started.view.run.run_id).as_str())
    );
    assert_eq!(
        persisted_panel.content["metadata"]["state_machine"]["event"].as_str(),
        Some("panel")
    );
    assert_eq!(
        persisted_panel.content["metadata"]["state_machine"]["run_id"].as_str(),
        Some(started.view.run.run_id.as_str())
    );
    assert_eq!(persisted_panel.content["text"].as_str(), Some(panel_text));

    let while_running = runtime
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: session.id.clone(),
            caller_bot_id: "driver-bot".to_string(),
        })
        .await
        .expect("query permission while run is active");
    assert!(!while_running.allowed);
    assert_eq!(while_running.reason_code, "state_machine_run_active");
    assert_eq!(
        while_running.active_run_id.as_deref(),
        Some(started.view.run.run_id.as_str())
    );

    let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
    let completed = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "worker-bot".to_string(),
            run_id: delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": delivery_run_id,
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "resolved result"}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(session.id.clone()),
        })
        .await
        .expect("complete one-shot run");

    let view = completed.view.expect("completed run view");
    assert_eq!(view.run.status, StateMachineRunStatus::Completed);
    assert_eq!(view.run.output.as_deref(), Some("resolved result"));
    let publish_commands = result_publisher.commands.lock().await;
    assert_eq!(publish_commands.len(), 1);
    assert_eq!(publish_commands[0].run_id, view.run.run_id);
    assert_eq!(publish_commands[0].group_id, "group-1");
    assert_eq!(publish_commands[0].session_id, session.id);
    assert_eq!(publish_commands[0].sender_bot_id, "driver-bot");
    assert_eq!(publish_commands[0].content, "resolved result");
    drop(publish_commands);

    let preserved_session = sessions
        .get(&session.id)
        .await
        .expect("read chat session")
        .expect("chat session exists");
    assert_eq!(preserved_session.status, SessionStatus::Running);

    let error = runtime
        .rerun_state_machine_run(RerunStateMachineCommand {
            source_run_id: started.view.run.run_id.clone(),
            authenticated_human: None,
                    })
        .await
        .expect_err("completed one-shot run must not be rerunnable");
    assert!(matches!(
        error,
        CollaborationRuntimeError::Conflict(message)
            if message.contains("must be failed before rerun")
    ));
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn manager_worker_one_shot_human_input_consumes_the_assigned_human_chat_reply() {
    let group_store = Arc::new(GroupStore::new());
    let group = session_collaboration_group(GroupStrategy::ManagerWorker);
    group_store
        .upsert(group.clone())
        .await
        .expect("seed manager-worker group");
    let mut participants = group.participants.clone();
    let mut human = Participant::human("human_1001", ParticipantRole::Observer);
    human.mode = Some(ParticipantMode::Present);
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
                message_visibility_version: 1,
                ..Default::default()
            },
        })
        .await
        .expect("seed manager-worker session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group_store,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    )
    .with_message_repo(Arc::new(MemoryMessageRepo::new()))
    .with_result_publisher(Arc::new(RecordingResultPublisher::default()));

    let started = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session.id.clone(),
            caller_bot_id: "driver-bot".to_string(),
            definition_yaml: one_shot_human_input_authoring_yaml(),
            participant_bindings: BTreeMap::new(),
            opening_message: None,
            input: Value::Null,
            judge_available: false,
            caller_association: None,
        })
        .await
        .expect("start one-shot HumanInput run");
    assert_eq!(
        started.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );

    let outcome = runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: group.id,
            session_id: Some(session.id),
            caller_actor_id: "human_1001".to_string(),
            content: "确认".to_string(),
            source: HumanResponseSource::Http,
            caller_ingress: None,
        })
        .await
        .expect("route manager-worker chat reply to one-shot HumanInput");
    let HandleSessionHumanInputOutcome::Consumed { response } = outcome else {
        panic!("assigned Human reply must be consumed by the one-shot run");
    };
    assert_eq!(response.node.status, StateMachineNodeStatus::Completed);
    assert_eq!(response.node.artifact_text.as_deref(), Some("确认"));
    assert_eq!(response.node.responded_by.as_deref(), Some("human_1001"));
    assert_eq!(response.run.status, StateMachineRunStatus::Completed);
}
