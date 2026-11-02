use super::{
    Arc,
    BotRunContextPort,
    ChatEventState,
    CollaborationEventRepoPort,
    CollaborationRuntime,
    CollaborationRuntimeService,
    FrontendDeliveryTarget,
    GroupCoreService,
    GroupMessageType,
    GroupStore,
    MemoryCollaborationStore,
    MemoryMessageRepo,
    MessageRole,
    RecordingBotRunContext,
    RecordingDelivery,
    RecordingFrontendDelivery,
    SessionManagementService,
    SessionStatus,
    StartStateMachineRunCommand,
    StateMachineDefinitionRepoPort,
    StateMachineNodeStatus,
    StateMachineRunStatus,
    Value,
    assert_inferred_default_requires,
    capture_tracing_logs,
    chat_send_params,
    complete_with_text,
    json,
    noop_judge,
    single_node_yaml,
    test_group,
    test_sessions
};

#[tokio::test]
async fn single_node_run_completes_session_with_bot_final_text() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    )
    .with_frontend_delivery(frontend_delivery.clone());

    let started = runtime
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
            caller_association: None,
        })
        .await
        .expect("start run");

    assert_eq!(started.view.nodes.len(), 1);
    assert_eq!(started.view.nodes[0].node_timeout_ms, Some(60_000));
    assert_eq!(started.view.nodes[0].max_attempts, 3);
    let persisted_definition = StateMachineDefinitionRepoPort::get(&*store, "single_node", 1)
        .await
        .expect("get persisted definition")
        .expect("persisted definition");
    assert_inferred_default_requires(&persisted_definition);
    let snapshot =
        StateMachineDefinitionRepoPort::get_run_snapshot(&*store, &started.view.run.run_id)
            .await
            .expect("get run snapshot")
            .expect("run snapshot");
    assert_inferred_default_requires(&snapshot.definition);
    let command = delivery.commands.lock().await[0].clone();
    let params = chat_send_params(&command);
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 1);
    assert!(matches!(
        frontend_commands[0].target,
        FrontendDeliveryTarget::Session { ref session_id }
            if session_id == &started.view.run.session_id
    ));
    let panel_event: Value =
        serde_json::from_str(&frontend_commands[0].event_json).expect("panel event json");
    assert_eq!(panel_event["event"].as_str(), Some("chat"));
    assert_eq!(panel_event["bot_uuid"].as_str(), Some("bcs_state_machine"));
    assert_eq!(
        panel_event["payload"]["run_id"].as_str(),
        Some(started.view.run.run_id.as_str())
    );
    assert_eq!(panel_event["payload"]["role"].as_str(), Some("assistant"));
    assert_eq!(panel_event["payload"]["message_type"].as_str(), Some("bot"));
    assert_eq!(
        panel_event["payload"]["bot_name"].as_str(),
        Some("BCS State Machine")
    );
    assert_eq!(
        panel_event["payload"]["message"]["role"].as_str(),
        Some("assistant")
    );
    assert_eq!(
        panel_event["payload"]["metadata"]["state_machine"]["event"].as_str(),
        Some("panel")
    );
    let panel_text = panel_event["payload"]["message"]["content"][0]["text"]
        .as_str()
        .expect("panel text");
    assert!(panel_text.contains("<AixUI"));
    assert!(panel_text.contains("type=\"panel\""));
    assert!(panel_text.contains("params='"));
    assert!(panel_text.contains("bcsPanel.StateMachineRunView"));
    assert!(panel_text.contains(&format!("state-machine-run-{}", started.view.run.run_id)));
    assert!(panel_text.contains("State Machine - Single Node"));
    drop(frontend_commands);
    assert_eq!(params.bcs_group_id, "group-1");
    assert_eq!(
        params.bcs_session_id.as_deref(),
        Some(started.view.run.session_id.as_str())
    );
    assert_eq!(
        params.session_context.session_id,
        started.view.run.session_id
    );
    let correlation = runtime
        .lookup_delivery_correlation(&command.run_id)
        .await
        .expect("lookup")
        .expect("correlation");
    assert_eq!(correlation.node_id, "answer");
    let delivery_run_id = command.run_id.clone();

    let delta = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": delivery_run_id.clone(),
                "bcs_group_id": "group-1",
                "state": "delta",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "draft"}]
                }
            }),
            state: ChatEventState::Delta,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("handle delta");
    assert!(delta.consumed);
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 2);
    let delta_event: Value =
        serde_json::from_str(&frontend_commands[1].event_json).expect("delta event json");
    assert_eq!(delta_event["event"].as_str(), Some("chat"));
    assert_eq!(delta_event["bot_uuid"].as_str(), Some("driver-bot"));
    assert_eq!(delta_event["payload"]["state"].as_str(), Some("delta"));
    assert_eq!(
        delta_event["payload"]["message"]["content"][0]["text"].as_str(),
        Some("draft")
    );
    drop(frontend_commands);
    let delta_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "chat.event",
    )
    .await
    .expect("list raw delta events");
    assert!(delta_events.is_empty());
    let bot_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "state_machine.node.bot_event",
    )
    .await
    .expect("list compact bot events");
    assert!(bot_events.is_empty());

    let handled = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": "ignored-by-runtime-test",
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "final answer"}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("handle final");

    assert!(handled.consumed);
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 3);
    assert!(matches!(
        frontend_commands[2].target,
        FrontendDeliveryTarget::Session { ref session_id }
            if session_id == &started.view.run.session_id
    ));
    let bot_event: Value =
        serde_json::from_str(&frontend_commands[2].event_json).expect("bot event json");
    assert_eq!(bot_event["event"].as_str(), Some("chat"));
    assert_eq!(bot_event["bot_uuid"].as_str(), Some("driver-bot"));
    assert_eq!(
        bot_event["payload"]["bcs_session_id"].as_str(),
        Some(started.view.run.session_id.as_str())
    );
    assert_eq!(
        bot_event["payload"]["run_id"].as_str(),
        Some("ignored-by-runtime-test")
    );
    assert_eq!(
        bot_event["payload"]["message"]["content"][0]["text"].as_str(),
        Some("final answer")
    );
    let view = handled.view.expect("run view");
    assert_eq!(view.run.output.as_deref(), Some("final answer"));
    let raw_final_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "chat.event",
    )
    .await
    .expect("list raw final events");
    assert!(raw_final_events.is_empty());
    let bot_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "state_machine.node.bot_event",
    )
    .await
    .expect("list compact bot events");
    assert_eq!(bot_events.len(), 1);
    assert_eq!(bot_events[0].attempt, Some(0));
    assert_eq!(bot_events[0].payload["state"].as_str(), Some("final"));
    assert_eq!(
        bot_events[0].payload["source_event_type"].as_str(),
        Some("chat.event")
    );
    assert_eq!(bot_events[0].payload["text_len"].as_u64(), Some(12));
    assert!(bot_events[0].payload.get("message").is_none());
    let session = sessions
        .get(&started.view.run.session_id)
        .await
        .expect("get session")
        .expect("session");
    assert_eq!(session.output, Some(json!("final answer")));
    let history = runtime
        .get_state_machine_session_history(&started.view.run.session_id, 50, None)
        .await
        .expect("history")
        .expect("state-machine history");
    assert_eq!(history.messages.len(), 2);
    assert_eq!(history.messages[1].sender, "bcs_state_machine");
    assert_eq!(history.messages[1].message_type, GroupMessageType::Bot);
    assert_eq!(history.messages[1].role, MessageRole::Assistant);
    assert_eq!(
        history.messages[1].bot_name.as_deref(),
        Some("BCS State Machine")
    );
    assert!(history.messages[1].content.contains("<AixUI"));
    assert!(history.messages[1].content.contains("type=\"panel\""));
    assert!(history.messages[1].content.contains("params='"));
    assert!(
        history.messages[1]
            .content
            .contains(&format!("\"runId\":\"{}\"", started.view.run.run_id))
    );
    assert_eq!(history.messages[0].sender, "driver-bot");
    assert_eq!(history.messages[0].bot_name.as_deref(), Some("Driver"));
    assert_eq!(history.messages[0].role, MessageRole::Assistant);
    assert_eq!(history.messages[0].message_type, GroupMessageType::Bot);
    assert_eq!(history.messages[0].content, "final answer");
    assert_eq!(
        history.messages[0]
            .metadata
            .as_ref()
            .and_then(|metadata| metadata["state_machine"]["event"].as_str()),
        Some("output")
    );
    let latest = runtime.get_state_machine_session_history(&started.view.run.session_id, 1, None)
        .await.unwrap().unwrap();
    assert_eq!(latest.messages.len(), 1);
    assert_eq!(latest.messages[0].content, "final answer");
    assert_eq!(latest.next_before, Some(latest.messages[0].timestamp));

}

#[tokio::test]
async fn state_machine_bot_delivery_registers_message_flow_run_context() {
    const CONFIGURED_TIMEOUT_MS: u64 = 7_200_000;

    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let run_context = Arc::new(RecordingBotRunContext::default());
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
    .with_provider_chat_run_timeout_ms(CONFIGURED_TIMEOUT_MS)
    .with_bot_run_context(run_context.clone());

    let definition_yaml = single_node_yaml()
        .replace("      node_timeout_ms: 120000\n", "")
        .replace("        node_timeout_ms: 60000\n", "");
    let before_start_ms = bcs_protocol::now_ms();
    runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(definition_yaml),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "stream this"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run");
    let after_start_ms = bcs_protocol::now_ms();

    let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
    let context = run_context
        .get_context(&delivery_run_id)
        .await
        .expect("state-machine delivery run context");
    assert_eq!(context.bot_id, "driver-bot");
    assert_eq!(context.group_id, "group-1");
    let delivery_command = delivery.commands.lock().await[0].clone();
    let delivery_params = chat_send_params(&delivery_command);
    assert_eq!(context.bcs_session_id, delivery_params.bcs_session_id);
    assert!(!context.terminal);
    assert!(context.deadline_ms >= before_start_ms.saturating_add(CONFIGURED_TIMEOUT_MS));
    assert!(context.deadline_ms <= after_start_ms.saturating_add(CONFIGURED_TIMEOUT_MS));
}

#[tokio::test]
async fn message_less_final_fails_attempt_and_schedules_retry() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
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
    .with_frontend_delivery(frontend_delivery.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "empty final should retry"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run");
    let first_delivery_run_id = delivery.commands.lock().await[0].run_id.clone();

    let tool_event = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: first_delivery_run_id.clone(),
            event_type: "agent".to_string(),
            event_payload: json!({
                "run_id": first_delivery_run_id.clone(),
                "stream": "tool",
                "data": {
                    "name": "lookup",
                    "phase": "result",
                    "toolCallId": "tool-1",
                },
            }),
            state: ChatEventState::ToolCallEnd,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("handle tool event");
    assert!(tool_event.consumed);

    let handled = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: first_delivery_run_id,
            event_type: "chat.event".to_string(),
            event_payload: json!({"state": "final"}),
            state: ChatEventState::Final,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("message-less final should enter retry flow");

    assert!(handled.consumed);
    let view = handled.view.expect("run view after retry scheduling");
    assert_eq!(view.run.status, StateMachineRunStatus::Running);
    assert_eq!(view.nodes[0].status, StateMachineNodeStatus::Running);
    assert_eq!(view.nodes[0].attempt, 1);
    assert_eq!(delivery.commands.lock().await.len(), 2);

    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 3);
    let tool_event: Value =
        serde_json::from_str(&frontend_commands[1].event_json).expect("tool event json");
    assert_eq!(tool_event["event"], "agent");
    assert_eq!(tool_event["payload"]["stream"], "tool");
    assert_eq!(tool_event["payload"]["data"]["phase"], "result");
    let final_event: Value =
        serde_json::from_str(&frontend_commands[2].event_json).expect("empty final event json");
    assert_eq!(final_event["event"], "chat");
    assert_eq!(final_event["payload"]["state"], "final");
    assert!(final_event["payload"].get("message").is_none());
    assert!(final_event["payload"].get("stop_reason").is_none());

    let retry_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "state_machine.node.retry_scheduled",
    )
    .await
    .expect("list retry events");
    assert_eq!(retry_events.len(), 1);
    assert_eq!(
        retry_events[0].payload["reason"],
        "bot completed without visible output"
    );
}

#[tokio::test]
async fn state_machine_completion_finishes_service_without_callback() {
    let group = Arc::new(GroupStore::new());
    let seeded_group = test_group();
    group.upsert(seeded_group).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "callback after completion"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run");
    let initial_session = sessions
        .get(&started.view.run.session_id)
        .await
        .expect("get session")
        .expect("session");

    let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &delivery_run_id,
        &started.view.run.session_id,
        "callback payload",
    )
    .await;

    let completed = sessions.get(&started.view.run.session_id).await.unwrap().unwrap();
    assert_eq!(initial_session.status, SessionStatus::Running);
    assert_eq!(completed.status, SessionStatus::Completed);
    assert!(completed.completed_at.is_some());

}

#[tokio::test(flavor = "current_thread")]
async fn state_machine_runtime_logs_run_node_and_terminal_lifecycle() {
    let ((run_id, session_id, delivery_run_id), logs) = capture_tracing_logs(async {
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
            noop_judge(),
        );

        let started = runtime
            .start_state_machine_run(StartStateMachineRunCommand {
                group_id: "group-1".to_string(),
                session_id: None,
                definition_yaml: Some(single_node_yaml()),
                definition: None,
                definition_ref: None,
                participant_bindings: None,
                opening_message_override: None,
                input: json!({"question": "logging"}),
                caller_id: Some("caller-1".to_string()),
                authenticated_human: None,
                caller_association: None,
            })
            .await
            .expect("start run");
        let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
        complete_with_text(
            &runtime,
            &delivery_run_id,
            &started.view.run.session_id,
            "final answer",
        )
        .await;

        (
            started.view.run.run_id,
            started.view.run.session_id,
            delivery_run_id,
        )
    })
    .await;

    for expected in [
        "state_machine: run started",
        "state_machine: node dispatch started",
        "state_machine: node dispatch completed",
        "state_machine: bot terminal event received",
        "state_machine: node completed",
        "state_machine: run completed",
        "group-1",
        "single_node",
        "answer",
        "driver-bot",
        "caller-1",
        "complete",
        "completed",
    ] {
        assert!(
            logs.contains(expected),
            "expected logs to contain {expected:?}; logs:\n{logs}"
        );
    }
    assert!(
        logs.contains(&run_id),
        "expected logs to contain run id {run_id}; logs:\n{logs}"
    );
    assert!(
        logs.contains(&session_id),
        "expected logs to contain session id {session_id}; logs:\n{logs}"
    );
    assert!(
        logs.contains(&delivery_run_id),
        "expected logs to contain delivery run id {delivery_run_id}; logs:\n{logs}"
    );
}
