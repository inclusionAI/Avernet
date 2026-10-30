use super::{
    Arc,
    BcsMessageFlow,
    BotDeliveryKind,
    BotEventCommand,
    ChatEventState,
    ChatResponseMode,
    GroupCoreService,
    GroupStrategy,
    MessageFlowService,
    Participant,
    ParticipantRole,
    RecordingSessionManagement,
    SessionKind,
    TaskDispatchCommand,
    TaskMessageCommand,
    assert_frame_tags,
    configure_provider_v2_target,
    json,
    manager_worker_flow_with_provider_stream,
    manager_worker_flow_with_repo,
    register_manager_worker_task,
    request_params,
    state_payload_name,
    support,
    test_session
};

#[tokio::test]
async fn manager_worker_task_final_delta_mode_flushes_only_open_worker_segment() {
    let (_support, repo, flow) = manager_worker_flow_with_repo().await;
    register_manager_worker_task(&flow, "task-result-delta-full", ChatResponseMode::Full).await;

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-delta-full".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "delta", "delta_text": "intro before tool. " }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-delta-full".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "run_id": "task-result-delta-full",
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "lookup",
                "phase": "result",
                "toolCallId": "tool-1",
                "result": { "content": [{"type": "text", "text": "tool output"}] },
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-delta-full".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "delta", "delta_text": "answer after tool" }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-delta-full".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "intro before tool. answer after tool"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    let worker_chat: Vec<_> = appended
        .iter()
        .filter(|msg| {
            msg.message_type == "chat" && msg.owner_bot_id.as_deref() == Some("bot-worker")
        })
        .map(|msg| msg.content.clone())
        .collect();
    assert_eq!(
        worker_chat,
        vec![json!("intro before tool. "), json!("answer after tool")],
        "worker final history must not duplicate the already-flushed delta segment"
    );
    let manager_result = appended
        .iter()
        .find(|msg| {
            msg.sender_id == "bot-worker"
                && msg.message_type == "chat"
                && msg.owner_bot_id.is_none()
        })
        .expect("manager result history");
    assert_eq!(
        manager_result.content,
        json!("intro before tool. answer after tool")
    );
}

#[tokio::test]
async fn manager_worker_sse_task_response_appends_raw_deltas_across_thinking_boundary() {
    let (_support, repo, flow) = manager_worker_flow_with_repo().await;
    register_manager_worker_task(
        &flow,
        "task-response-delta-thinking",
        ChatResponseMode::AfterLastToolCall,
    )
    .await;

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-response-delta-thinking".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "delta", "delta_text": "A" }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-response-delta-thinking".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": { "delta": "checking" },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    for delta in ["B1", "B2"] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-worker".to_string(),
            run_id: "task-response-delta-thinking".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({ "state": "delta", "delta_text": delta }),
            state: ChatEventState::Delta,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();
    }

    assert_eq!(
        flow.task_store
            .get("task-response-delta-thinking")
            .await
            .unwrap()
            .response_content,
        "AB1B2",
        "TaskStore must append raw SSE deltas instead of synthesized segment snapshots"
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-response-delta-thinking".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "final" }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    let manager_result = appended
        .iter()
        .find(|msg| {
            msg.sender_id == "bot-worker"
                && msg.message_type == "chat"
                && msg.owner_bot_id.is_none()
        })
        .expect("manager result history");
    assert_eq!(manager_result.content, json!("AB1B2"));
}

#[tokio::test]
async fn manager_worker_task_result_defaults_to_text_after_last_tool_call() {
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
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
            caller_delegation: None,
        })
        .await
        .unwrap();

    for (state, text) in [
        (ChatEventState::Delta, Some("analysis before tool. ")),
        (ChatEventState::ToolCallStart, None),
        (
            ChatEventState::Delta,
            Some("analysis before tool. tool detail. "),
        ),
        (ChatEventState::ToolCallEnd, None),
        (
            ChatEventState::Final,
            Some("analysis before tool. tool detail. answer after tool"),
        ),
    ] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-worker".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: match text {
                Some(text) => json!({
                    "state": state_payload_name(&state),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                    },
                }),
                None => json!({
                    "state": state_payload_name(&state),
                    "tool_call_id": "tool-1",
                    "tool_name": "lookup",
                }),
            },
            state,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();
    }

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(
        params["message"]["content"][0]["text"],
        "[from:Worker] answer after tool"
    );
    assert_eq!(params["session_context"]["message"], "answer after tool");
}

#[tokio::test]
async fn manager_worker_task_result_uses_agent_tool_boundary_by_default() {
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
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
            caller_delegation: None,
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool. "}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "run_id": dispatch.task_id.clone(),
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "lookup",
                "phase": "result",
                "toolCallId": "tool-1",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "answer after tool"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool. answer after tool"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(
        params["message"]["content"][0]["text"],
        "[from:Worker] answer after tool"
    );
    assert_eq!(params["session_context"]["message"], "answer after tool");
}

#[tokio::test]
async fn manager_worker_task_result_uses_final_when_agent_tool_has_no_followup_text() {
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
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
            caller_delegation: None,
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool. "}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "run_id": dispatch.task_id.clone(),
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "lookup",
                "phase": "result",
                "toolCallId": "tool-1",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool."}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(
        params["message"]["content"][0]["text"],
        "[from:Worker] analysis before tool."
    );
    assert_eq!(params["session_context"]["message"], "analysis before tool.");
}

#[tokio::test]
async fn manager_worker_task_dispatch_uses_sse_for_eligible_provider_worker() {
    let (support, repo, _) = manager_worker_flow_with_repo().await;
    configure_provider_v2_target(&support, "bot-worker", "gray-user").await;
    let flow = manager_worker_flow_with_provider_stream(&support, repo);

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-manager".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-worker".to_string(),
        target_bot_name: None,
        payload: json!({
            "message": "do work",
            "bcs_session_id": "group-1:abcdef12",
        }),
        caller_delegation: None,
    })
    .await
    .expect("task.dispatch should deliver to provider worker");

    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
    assert!(support.bot_delivery.targets().await[0].is_http_provider());
    assert_frame_tags(&support.bot_delivery.frames().await[0], &["online"]);
}

#[tokio::test]
async fn manager_worker_task_message_uses_sse_for_eligible_provider_manager() {
    let (support, repo, _) = manager_worker_flow_with_repo().await;
    configure_provider_v2_target(&support, "bot-manager", "gray-user").await;
    let flow = manager_worker_flow_with_provider_stream(&support, repo);

    flow.handle_task_message(TaskMessageCommand {
        worker_bot_id: "bot-worker".to_string(),
        group_id: "group-1".to_string(),
        payload: json!({
            "message": "progress update",
            "bcs_session_id": "group-1:abcdef12",
        }),
    })
    .await
    .expect("task.message should deliver to provider manager");

    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskMessage]);
    assert!(support.bot_delivery.targets().await[0].is_http_provider());
    assert_frame_tags(&support.bot_delivery.frames().await[0], &["draft"]);
}

#[tokio::test]
async fn manager_worker_task_result_uses_sse_for_eligible_provider_manager() {
    let (support, repo, _) = manager_worker_flow_with_repo().await;
    configure_provider_v2_target(&support, "bot-manager", "gray-user").await;
    let flow = manager_worker_flow_with_provider_stream(&support, repo);

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
            caller_delegation: None,
        })
        .await
        .expect("task.dispatch should deliver to worker");

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id,
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "task done"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .expect("worker final should deliver task result to provider manager");

    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::TaskDispatch, BotDeliveryKind::TaskResult]
    );
    assert!(support.bot_delivery.targets().await[1].is_http_provider());
    assert_frame_tags(&support.bot_delivery.frames().await[1], &["draft"]);
}

#[tokio::test]
async fn manager_worker_provider_task_flow_uses_session_participant_tags() {
    let (support, _, _) = manager_worker_flow_with_repo().await;
    configure_provider_v2_target(&support, "bot-manager", "manager-owner").await;
    configure_provider_v2_target(&support, "bot-worker", "worker-owner").await;

    let mut manager = Participant::bot("bot-manager", ParticipantRole::Manager);
    manager.tags = vec!["draft".to_string()];
    let mut worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    worker.tags = vec!["online".to_string()];
    let mut session = test_session("group-1:abcdef12", "group-1", SessionKind::Chat);
    session.participants = vec![manager, worker];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management);

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
            caller_delegation: None,
        })
        .await
        .expect("task dispatch should use worker session tags");

    flow.handle_task_message(TaskMessageCommand {
        worker_bot_id: "bot-worker".to_string(),
        group_id: "group-1".to_string(),
        payload: json!({
            "message": "progress update",
            "bcs_session_id": "group-1:abcdef12",
        }),
    })
    .await
    .expect("task message should use manager session tags");

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id,
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "task done"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .expect("worker final should use manager session tags");

    let frames = support.bot_delivery.frames().await;
    assert_frame_tags(&frames[0], &["online"]);
    assert_frame_tags(&frames[1], &["draft"]);
    assert_frame_tags(&frames[2], &["draft"]);
}
