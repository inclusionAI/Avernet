use super::{
    ActorKind,
    Arc,
    BTreeMap,
    ChatEventState,
    CollaborationRuntime,
    CollaborationRuntimeService,
    CreateOrReactivateCommand,
    CreateStateMachineRerun,
    CreateStateMachineRerunOutcome,
    FailingResultPublisher,
    GroupCoreService,
    GroupStore,
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
    RerunStateMachineCommand,
    RuntimeParticipantBinding,
    STATE_MACHINE_PANEL_MESSAGE_TYPE,
    SessionKind,
    SessionManagementService,
    SessionStatus,
    StartSessionStateMachineRunCommand,
    StateMachineNodeStatus,
    StateMachineRunRepoPort,
    StateMachineRunStatus,
    Value,
    json,
    noop_judge,
    one_shot_authoring_yaml,
    test_group,
    test_sessions
};

#[tokio::test]
async fn one_shot_result_publication_failure_marks_run_failed_and_allows_rerun() {
    let group_store = Arc::new(GroupStore::new());
    let mut group = test_group();
    group.label = Some("Original Group".to_string());
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
    let message_repo = Arc::new(MemoryMessageRepo::new());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group_store.clone(),
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    )
    .with_message_repo(message_repo.clone())
    .with_frontend_delivery(frontend_delivery.clone())
    .with_result_publisher(Arc::new(FailingResultPublisher));
    let opening_message: OpeningMessage = serde_json::from_value(json!({
        "type": "panel",
        "component": "custom.OneShotRunView",
        "params": {
            "groupName": "{{bcs.group_name}}",
            "runId": "{{bcs.run_id}}",
            "sessionId": "{{bcs.session_id}}"
        },
        "tab": {
            "id": "one-shot-{{bcs.run_id}}",
            "title": "One Shot",
            "closable": true
        }
    }))
    .expect("valid one-shot opening panel");

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
            opening_message: Some(opening_message.clone()),
            input: json!({"question": "resolve this"}),
            judge_available: false,
            caller_association: None,
        })
        .await
        .expect("start one-shot run");
    let persisted_source = StateMachineRunRepoPort::get_run(&*store, &started.view.run.run_id)
        .await
        .expect("load persisted source run")
        .expect("persisted source run");
    assert_eq!(
        persisted_source.opening_message_override.as_ref(),
        Some(&opening_message)
    );
    assert!(
        serde_json::to_value(&started.view)
            .expect("serialize public run view")
            .pointer("/run/opening_message_override")
            .is_none(),
        "the raw one-shot override must not be exposed in the public Run view"
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
        .expect("terminal event returns failed run");

    let view = completed.view.expect("failed run view");
    assert_eq!(view.run.run_id, started.view.run.run_id);
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert!(
        view.run
            .error
            .as_deref()
            .is_some_and(|error| error.contains("result publication failed"))
    );
    assert_eq!(
        sessions
            .get(&session.id)
            .await
            .expect("read chat session")
            .expect("chat session")
            .status,
        SessionStatus::Running
    );

    // Simulate a pre-lineage Run. The migration deliberately leaves its
    // root_run_id NULL, so rerun must treat the source Run itself as the root.
    let mut legacy_source = view.run.clone();
    legacy_source.root_run_id = None;
    store
        .create_run(legacy_source, view.nodes.clone())
        .await
        .expect("preserve legacy NULL root before rerun");

    let rerun = runtime
        .rerun_state_machine_run(RerunStateMachineCommand {
            source_run_id: started.view.run.run_id.clone(),
            authenticated_human: None,
        })
        .await
        .expect("rerun failed one-shot run");
    assert!(rerun.created);
    assert_ne!(rerun.view.run.run_id, started.view.run.run_id);
    assert_eq!(
        rerun.view.run.root_run_id.as_deref(),
        Some(started.view.run.run_id.as_str())
    );
    assert_eq!(
        rerun.view.run.rerun_of.as_deref(),
        Some(started.view.run.run_id.as_str())
    );
    assert_eq!(rerun.view.run.input, started.view.run.input);
    assert_eq!(
        rerun.view.run.opening_message_override,
        started.view.run.opening_message_override
    );
    assert_eq!(rerun.view.nodes.len(), 1);
    assert_eq!(rerun.view.nodes[0].attempt, 0);
    assert_eq!(rerun.view.nodes[0].status, StateMachineNodeStatus::Running);
    assert_eq!(
        delivery
            .commands
            .lock()
            .await
            .last()
            .map(|command| command.target_bot_id()),
        Some("worker-bot")
    );
    let history = runtime
        .get_state_machine_session_history(&session.id, 20, None)
        .await
        .expect("history after rerun")
        .expect("state-machine history after rerun");
    let panel_run_ids = history
        .messages
        .iter()
        .filter_map(|message| {
            message
                .metadata
                .as_ref()
                .and_then(|metadata| metadata.pointer("/state_machine/event"))
                .and_then(Value::as_str)
                .filter(|event| *event == "panel")
                .and_then(|_| {
                    message
                        .metadata
                        .as_ref()
                        .and_then(|metadata| metadata.pointer("/state_machine/run_id"))
                        .and_then(Value::as_str)
                })
        })
        .collect::<Vec<_>>();
    assert_eq!(panel_run_ids.len(), 2);
    assert!(panel_run_ids.contains(&started.view.run.run_id.as_str()));
    assert!(panel_run_ids.contains(&rerun.view.run.run_id.as_str()));
    let panel_history = message_repo
        .query_messages(MessageQuery {
            group_id: group.id.clone(),
            session_id: session.id.clone(),
            cursor: None,
            limit: 20,
            keyword: None,
            sender_id: None,
            message_type: Some(STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string()),
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
            human_view: None,
        })
        .await
        .expect("query custom panel history");
    let panel_text_for = |run_id: &str| {
        panel_history
            .messages
            .iter()
            .find(|message| message.run_id == run_id)
            .and_then(|message| message.content["text"].as_str())
            .expect("persisted panel text")
    };
    let source_panel = panel_text_for(&started.view.run.run_id);
    assert!(source_panel.contains("custom.OneShotRunView"));
    assert!(source_panel.contains(&started.view.run.run_id));
    let rerun_panel = panel_text_for(&rerun.view.run.run_id);
    assert!(rerun_panel.contains("custom.OneShotRunView"));
    assert!(rerun_panel.contains("Original Group"));
    assert!(rerun_panel.contains(&rerun.view.run.run_id));
    assert!(!rerun_panel.contains(&started.view.run.run_id));

    group_store
        .update_label(&group.id, Some("Renamed Group".to_string()))
        .await
        .expect("rename group after rerun panel persistence");

    let repeated = runtime
        .rerun_state_machine_run(RerunStateMachineCommand {
            source_run_id: started.view.run.run_id.clone(),
            authenticated_human: None,
        })
        .await
        .expect("repeat failed rerun request");
    assert!(!repeated.created);
    assert_eq!(repeated.view.run.run_id, rerun.view.run.run_id);
    assert_eq!(delivery.commands.lock().await.len(), 2);
    let frontend_commands = frontend_delivery.commands.lock().await;
    let repeated_event: Value = serde_json::from_str(
        &frontend_commands
            .last()
            .expect("repeated rerun frontend event")
            .event_json,
    )
    .expect("decode repeated rerun frontend event");
    assert_eq!(
        repeated_event["payload"]["content"].as_str(),
        Some(rerun_panel),
        "an idempotent rerun must publish the already persisted opening message"
    );
    assert!(
        !repeated_event["payload"]["content"]
            .as_str()
            .unwrap()
            .contains("Renamed Group")
    );
    drop(frontend_commands);

    let rerun_delivery_run_id = delivery.commands.lock().await[1].run_id.clone();
    let failed_rerun = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "worker-bot".to_string(),
            run_id: rerun_delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": rerun_delivery_run_id,
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "rerun result"}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(session.id.clone()),
        })
        .await
        .expect("rerun terminal event returns failed run")
        .view
        .expect("failed rerun view");
    assert_eq!(failed_rerun.run.status, StateMachineRunStatus::Failed);

    let materialized_at = bcs_protocol::now_ms();
    let mut materialized = failed_rerun.run.clone();
    materialized.run_id = "sm-materialized-before-start".to_string();
    materialized.rerun_of = Some(failed_rerun.run.run_id.clone());
    materialized.status = StateMachineRunStatus::Pending;
    materialized.output = None;
    materialized.error = None;
    materialized.created_at = materialized_at;
    materialized.updated_at = materialized_at;
    materialized.completed_at = None;
    let mut materialized_nodes = failed_rerun.nodes.clone();
    for node in &mut materialized_nodes {
        node.run_id = materialized.run_id.clone();
        node.status = StateMachineNodeStatus::Pending;
        node.attempt = 0;
        node.timeout_deadline_ms = None;
        node.outcome = None;
        node.responded_by = None;
        node.delivery_request_id = None;
        node.bot_delivery_run_id = None;
        node.artifact_text = None;
        node.error = None;
        node.started_at = None;
        node.completed_at = None;
    }
    assert!(matches!(
        store
            .create_rerun_if_session_idle(CreateStateMachineRerun {
                source_run_id: failed_rerun.run.run_id.clone(),
                run: materialized.clone(),
                nodes: materialized_nodes,
                reactivate_service_session: false,
            })
            .await
            .expect("materialize rerun before simulated crash"),
        CreateStateMachineRerunOutcome::Created
    ));

    store.save_run_opening(bcs_service_api::StateMachineOpeningPayload {
        run_id: materialized.run_id.clone(), group_id: materialized.group_id.clone(), session_id: materialized.session_id.clone(),
        client_msg_id: format!("{}:000-panel", materialized.run_id), content: "original rendered opening before crash".into(),
        component: None, created_at_ms: materialized.created_at,
    }).await.expect("persist original rendered opening before simulated crash");

    let recovered = runtime
        .rerun_state_machine_run(RerunStateMachineCommand {
            source_run_id: failed_rerun.run.run_id,
            authenticated_human: None,
        })
        .await
        .expect("retry resumes materialized rerun");
    assert!(!recovered.created);
    assert_eq!(recovered.view.run.run_id, materialized.run_id);
    assert_eq!(recovered.view.run.status, StateMachineRunStatus::Running);
    assert_eq!(
        recovered.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );
    assert_eq!(delivery.commands.lock().await.len(), 3);
    let recovered_history = runtime
        .get_state_machine_session_history(&session.id, 20, None)
        .await
        .expect("history after recovering materialized rerun")
        .expect("state-machine history after recovery");
    assert!(recovered_history.messages.iter().any(|message| {
        message
            .metadata
            .as_ref()
            .and_then(|metadata| metadata.pointer("/state_machine/run_id"))
            .and_then(Value::as_str)
            == Some(materialized.run_id.as_str())
    }));
}
