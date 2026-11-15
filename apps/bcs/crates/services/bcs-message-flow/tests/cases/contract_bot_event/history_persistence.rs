use super::{
    Arc,
    BcsMessageFlow,
    BotEventCommand,
    ChatEventState,
    FrontendDeliveryTarget,
    GroupCoreService,
    GroupStrategy,
    MessageAudience,
    MessageFlowService,
    Participant,
    ParticipantRole,
    RecordingMessageRepo,
    RecordingSessionManagement,
    SessionKind,
    Value,
    agent_tool_result_payload,
    json,
    support,
    test_session
};

#[tokio::test]
async fn bot_final_chat_persists_worker_owner_and_public_manager_owner_for_manager_worker() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
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
        bot_id: "bot-worker".to_string(),
        run_id: "run-worker-final".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "worker result"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].sender_id, "bot-worker");
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(
        appended[0].audience,
        Some(MessageAudience::directed(["bot-worker"]).unwrap())
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: "run-manager-final".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "manager result"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 2);
    assert_eq!(appended[1].sender_id, "bot-manager");
    assert_eq!(appended[1].message_type, "chat");
    assert_eq!(appended[1].owner_bot_id, None);
    assert_eq!(appended[1].audience, Some(MessageAudience::Public));

    let chat_support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let chat_repo = Arc::new(RecordingMessageRepo::default());
    let chat_flow = BcsMessageFlow::new(
        chat_support.group.clone(),
        chat_support.routing.clone(),
        chat_support.registry.clone(),
        chat_support.bot_delivery.clone(),
        chat_support.frontend_delivery.clone(),
    )
    .with_message_repo(chat_repo.clone());

    chat_flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-chat-final".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "chat result"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    let chat_appended = chat_repo.appended().await;
    assert_eq!(chat_appended.len(), 1);
    assert_eq!(chat_appended[0].message_type, "chat");
    assert_eq!(chat_appended[0].owner_bot_id, None);
}

#[tokio::test]
async fn bot_self_output_uses_session_worker_role_for_private_owner() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-session-worker", "Session Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![Participant::bot("bot-manager", ParticipantRole::Manager)];
    support.group.upsert(group).await.unwrap();

    let mut session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    session.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-session-worker", ParticipantRole::Worker),
    ];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management)
    .with_message_repo(repo.clone());

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-session-worker".to_string(),
        run_id: "run-session-worker-final".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "session worker result"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-session-worker".to_string(),
        run_id: "run-session-worker-tool".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(
            Some("exec"),
            "tool-session-worker-1",
            "session worker tool output",
            false,
        ),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 2);
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-session-worker"));
    assert_eq!(appended[1].message_type, "tool_call");
    assert_eq!(appended[1].owner_bot_id.as_deref(), Some("bot-session-worker"));
}

#[tokio::test]
async fn agent_tool_result_persists_worker_owner_and_public_manager_owner_for_manager_worker() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
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
        bot_id: "bot-worker".to_string(),
        run_id: "run-worker-tool".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(
            Some("exec"),
            "tool-worker-1",
            "worker tool output",
            false,
        ),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].sender_id, "bot-worker");
    assert_eq!(appended[0].message_type, "tool_call");
    assert_eq!(appended[0].content["tool_call_id"], "tool-worker-1");
    assert_eq!(appended[0].run_id, "run-worker-tool");
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: "run-manager-tool".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(
            Some("exec"),
            "tool-manager-1",
            "manager tool output",
            false,
        ),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 2);
    assert_eq!(appended[1].sender_id, "bot-manager");
    assert_eq!(appended[1].message_type, "tool_call");
    assert_eq!(appended[1].owner_bot_id, None);
    assert_eq!(appended[1].audience, Some(MessageAudience::FullOnly));

    let chat_support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let chat_repo = Arc::new(RecordingMessageRepo::default());
    let chat_flow = BcsMessageFlow::new(
        chat_support.group.clone(),
        chat_support.routing.clone(),
        chat_support.registry.clone(),
        chat_support.bot_delivery.clone(),
        chat_support.frontend_delivery.clone(),
    )
    .with_message_repo(chat_repo.clone());

    chat_flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-chat-tool".to_string(),
            group_id: "group-1".to_string(),
            event_type: "agent".to_string(),
            event_payload: agent_tool_result_payload(
                Some("exec"),
                "tool-chat-1",
                "chat tool output",
                false,
            ),
            state: ChatEventState::Delta,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();

    let chat_appended = chat_repo.appended().await;
    assert_eq!(chat_appended.len(), 1);
    assert_eq!(chat_appended[0].message_type, "tool_call");
    assert_eq!(chat_appended[0].owner_bot_id, None);
}

#[tokio::test]
async fn agent_tool_result_clears_cached_start_after_successful_persist() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
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
        bot_id: "bot-observer".to_string(),
        run_id: "run-tool-name".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "tool",
            "data": {
                "phase": "start",
                "toolCallId": "tool-backfill",
                "name": "Bash",
                "args": { "command": "mcporter list --json" },
            },
        }),
        state: ChatEventState::ToolCallStart,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-tool-name".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(Some("Bash"), "tool-backfill", "command echo", false),
        state: ChatEventState::ToolCallEnd,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-tool-name".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(None, "tool-backfill", "actual command output", false),
        state: ChatEventState::ToolCallEnd,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 2);
    assert_eq!(appended[0].content["name"], "Bash");
    assert_eq!(appended[1].content["name"], "");
    assert_eq!(appended[1].content["args"], Value::Null);
    assert_eq!(
        appended[1].content["result"]["content"][0]["text"],
        "actual command output"
    );
}

#[tokio::test]
async fn agent_tool_result_retains_cached_start_when_persistence_fails() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
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
        bot_id: "bot-observer".to_string(),
        run_id: "run-tool-retry".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "tool",
            "data": {
                "phase": "start",
                "toolCallId": "tool-retry",
                "name": "Bash",
                "args": { "command": "mcporter list --json" },
            },
        }),
        state: ChatEventState::ToolCallStart,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    repo.set_fail_appends(true);
    let failed = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-tool-retry".to_string(),
            group_id: "group-1".to_string(),
            event_type: "agent".to_string(),
            event_payload: agent_tool_result_payload(None, "tool-retry", "failed output", false),
            state: ChatEventState::ToolCallEnd,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await;
    assert!(failed.is_err());

    repo.set_fail_appends(false);
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-tool-retry".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(None, "tool-retry", "actual output", false),
        state: ChatEventState::ToolCallEnd,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].content["name"], "Bash");
    assert_eq!(
        appended[0].content["args"]["command"],
        "mcporter list --json"
    );
}

/// A run that streams chat deltas and then terminates with `error` (never a
/// `final`) must still flush its open chat segment to history. Regression: the
/// terminal error/abort path skipped the flush (only `final` flushed), so the
/// buffered partial reply was silently dropped.
#[tokio::test]
async fn bot_error_terminal_flushes_buffered_chat_segment() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    // Two streaming chat deltas accumulate in the run's open segment. These only
    // buffer in memory — nothing is persisted yet.
    for delta in ["部分回复", "，还没说完"] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-err".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({ "state": "delta", "delta_text": delta }),
            state: ChatEventState::Delta,
            bcs_session_id: Some("group-1:error-session".into()),
        })
        .await
        .unwrap();
    }
    assert!(
        repo.appended().await.is_empty(),
        "streaming deltas must only buffer, not persist"
    );

    // The run dies with an error before any `final` frame.
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-err".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "error", "errorMessage": "engine crashed" }),
        state: ChatEventState::Error,
        bcs_session_id: Some("group-1:error-session".into()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        2,
        "error terminal must flush the partial reply and persist the error"
    );
    assert_eq!(appended[1].message_type, "chat_error");
    assert_eq!(appended[1].content, json!("engine crashed"));
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].run_id, "run-err");
    assert_eq!(
        appended[0].content,
        json!("部分回复，还没说完"),
        "the flushed content must be the accumulated segment text"
    );
}

#[tokio::test]
async fn direct_chat_segment_boundaries_do_not_persist_group_history() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());
    let session_id = "bcs-cli:caller-bot:abcdef12";

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "direct-run".to_string(),
        group_id: String::new(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "delta", "delta_text": "临时直聊回复" }),
        state: ChatEventState::Delta,
        bcs_session_id: Some(session_id.to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "direct-run".to_string(),
        group_id: String::new(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": { "stream": "thinking", "delta": "思考中" },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some(session_id.to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "direct-run".to_string(),
        group_id: String::new(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{ "type": "text", "text": "直聊完成" }],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some(session_id.to_string()),
    })
    .await
    .unwrap();

    assert!(
        repo.appended().await.is_empty(),
        "Direct A2A events must never be written to Group message history"
    );
    let frontend_commands = support.frontend_delivery.commands().await;
    assert_eq!(frontend_commands.len(), 3);
    assert!(frontend_commands.iter().all(|command| {
        command.target
            == FrontendDeliveryTarget::Session {
                session_id: session_id.to_string(),
            }
    }));
}
