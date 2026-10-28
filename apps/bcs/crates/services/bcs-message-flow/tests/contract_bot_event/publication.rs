use super::{
    Arc,
    BcsFrame,
    BcsMessageFlow,
    BotEventCommand,
    BotTerminalState,
    ChatEventState,
    ChatResponseMode,
    FrontendDeliveryTarget,
    GroupCoreService,
    GroupStrategy,
    MessageAudience,
    MessageFlowService,
    MessageVisibilityDomain,
    Participant,
    ParticipantRole,
    RecordingBotTerminalObserver,
    RecordingMessageRepo,
    Value,
    json,
    new_task_entry,
    state_payload_name,
    support
};

#[tokio::test]
async fn websocket_chat_events_notify_terminal_observer_only_for_terminal_states() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let observer = Arc::new(RecordingBotTerminalObserver::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_terminal_observer(observer.clone());

    for (run_id, state, payload) in [
        (
            "run-websocket-final",
            ChatEventState::Final,
            json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "websocket result"}],
                },
            }),
        ),
        (
            "run-websocket-error",
            ChatEventState::Error,
            json!({"state": "error", "errorMessage": "websocket failed"}),
        ),
        (
            "run-websocket-aborted",
            ChatEventState::Aborted,
            json!({"state": "aborted", "error_message": "websocket aborted"}),
        ),
        (
            "run-websocket-delta",
            ChatEventState::Delta,
            json!({"state": "delta", "message": {"content": "partial"}}),
        ),
    ] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: run_id.to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: payload,
            state,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    }

    let events = observer.events.lock().await;
    assert_eq!(events.len(), 3);
    assert_eq!(events[0].run_id, "run-websocket-final");
    assert_eq!(events[0].bot_uuid, "bot-observer");
    assert_eq!(events[0].state, BotTerminalState::Final);
    assert_eq!(events[0].text, "websocket result");
    assert_eq!(events[1].state, BotTerminalState::Error);
    assert_eq!(events[1].text, "websocket failed");
    assert_eq!(events[2].state, BotTerminalState::Aborted);
    assert_eq!(events[2].text, "websocket aborted");
}

#[tokio::test]
async fn bot_event_publish_preserves_workbench_event_names() {
    let cases = [
        ("chat.event", ChatEventState::Delta, "chat"),
        ("chat.event", ChatEventState::Final, "chat"),
        ("chat.event", ChatEventState::Error, "chat"),
        ("chat.event", ChatEventState::Aborted, "chat"),
        ("chat.event", ChatEventState::ToolCallStart, "agent"),
        ("chat.event", ChatEventState::ToolCallEnd, "agent"),
        ("chat", ChatEventState::Final, "chat"),
        ("agent", ChatEventState::Delta, "agent"),
    ];

    for (event_type, state, expected_event) in cases {
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let flow = BcsMessageFlow::new(
            support.group.clone(),
            support.routing.clone(),
            support.registry.clone(),
            support.bot_delivery.clone(),
            support.frontend_delivery.clone(),
        );

        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: format!("run-{event_type}-{state:?}"),
            group_id: "group-1".to_string(),
            event_type: event_type.to_string(),
            event_payload: json!({
                "state": state_payload_name(&state),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            }),
            state,
            bcs_session_id: None,
        })
        .await
        .unwrap();

        let events = support.frontend_delivery.events().await;
        let frame: Value = serde_json::from_str(events.first().unwrap()).unwrap();
        assert_eq!(
            frame["event"], expected_event,
            "wire event {event_type} should publish stable workbench event name"
        );
    }
}

#[tokio::test]
async fn bot_event_publish_carries_legacy_run_fallback() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-1".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1",
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "partial"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let commands = support.frontend_delivery.commands().await;
    assert_eq!(commands.len(), 1);
    let fallback = commands[0]
        .run_fallback
        .as_ref()
        .expect("bot event should carry run fallback");
    assert_eq!(fallback.run_id, "run-1");
    assert_eq!(fallback.session_id, "group-1");

    let frame: BcsFrame = serde_json::from_str(&fallback.event_json).unwrap();
    let BcsFrame::Event(event) = frame else {
        panic!("run fallback should use raw event frame");
    };
    assert_eq!(event.event, "chat.event");
    let payload = event.payload.expect("event payload");
    assert_eq!(payload["run_id"], "run-1");
    assert_eq!(payload["bcs_group_id"], "group-1");
    assert_eq!(payload["message"]["content"][0]["text"], "partial");
}

#[tokio::test]
async fn bot_event_with_bcs_session_id_publishes_to_session_target() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-1".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1:abcdef12",
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "partial"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let commands = support.frontend_delivery.commands().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0].target,
        FrontendDeliveryTarget::Session {
            session_id: "group-1:abcdef12".to_string()
        }
    );
    let fallback = commands[0]
        .run_fallback
        .as_ref()
        .expect("bot event should carry run fallback");
    assert_eq!(fallback.session_id, "group-1:abcdef12");
}

#[tokio::test]
async fn manager_worker_bot_events_publish_with_participant_safe_audiences() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: "manager-chat".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "public reply"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: "manager-thinking".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {"stream": "thinking", "delta": "internal reasoning"},
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "unmatched-worker-chat".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "unmatched worker reply"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    flow.task_store
        .register(new_task_entry(
            "task-worker-chat".to_string(),
            "group-1".to_string(),
            Some("group-1:abcdef12".to_string()),
            "bot-manager".to_string(),
            "bot-worker".to_string(),
            None,
            1,
            ChatResponseMode::AfterLastToolCall,
        ))
        .await;
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-worker-chat".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "task worker reply"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let commands = support.frontend_delivery.commands().await;
    assert_eq!(commands.len(), 4);
    assert_eq!(commands[0].visibility_domain, MessageVisibilityDomain::ManagerWorker);
    assert_eq!(commands[0].audience, Some(MessageAudience::Public));
    assert_eq!(commands[1].audience, Some(MessageAudience::FullOnly));
    assert_eq!(commands[2].audience, Some(MessageAudience::FullOnly));
    assert_eq!(
        commands[3].audience,
        Some(MessageAudience::directed(["bot-manager", "bot-worker"]).unwrap())
    );
}

#[tokio::test]
async fn state_machine_group_manager_chat_is_public_in_realtime_and_history() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::StateMachine;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: "manager-announcement".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "round result"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: "manager-announcement".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {"stream": "thinking", "delta": "next round"},
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let commands = support.frontend_delivery.commands().await;
    assert_eq!(commands[0].visibility_domain, MessageVisibilityDomain::StateMachine);
    assert_eq!(commands[0].audience, Some(MessageAudience::Public));
    assert_eq!(commands[1].audience, Some(MessageAudience::FullOnly));

    let messages = repo.appended().await;
    let announcement = messages
        .iter()
        .find(|message| message.message_type == "chat")
        .expect("manager announcement should be persisted");
    assert_eq!(announcement.visibility_domain, MessageVisibilityDomain::StateMachine);
    assert_eq!(announcement.audience, Some(MessageAudience::Public));
}
