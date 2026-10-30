use super::{
    Arc,
    BcsMessageFlow,
    BotDeliveryKind,
    BotEventCommand,
    BotRunContextPort,
    ChatEventState,
    ChatResponseMode,
    GroupCoreService,
    GroupStrategy,
    MemoryBotRunContextStore,
    MessageFlowService,
    Participant,
    ParticipantRole,
    ServiceError,
    TaskDispatchCommand,
    TaskLedgerStatus,
    TaskRunAliasRegistration,
    json,
    manager_worker_flow_with_repo,
    register_manager_worker_task,
    request_id,
    request_params,
    support
};

#[tokio::test]
async fn task_result_is_forwarded_back_to_manager_session() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
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
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
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
        bot_id: "bot-observer".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
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
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(params["session_key"], "group-1:abcdef12");
    assert_eq!(params["bcs_group_id"], "group-1:abcdef12");
    assert_eq!(params["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(params["channel"]["user_id"], "Observer");
    assert_eq!(params["channel"]["actor_id"], "bot-observer");
    assert_eq!(params["channel"]["actor_name"], "Observer");
    assert_eq!(params["channel"]["thread_id"], "group-1");
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
}

#[tokio::test]
async fn manager_worker_task_result_preserves_manager_session_context() {
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
        event_type: "chat".to_string(),
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
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(params["session_key"], "group-1:abcdef12");
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
    assert_eq!(params["session_context"]["group_type"], "manager_worker");
    assert_eq!(params["session_context"]["recipient_role"], "manager");
}

#[tokio::test]
async fn manager_worker_task_result_records_independent_manager_run_context() {
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
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

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

    let dispatch_context = run_context
        .get_context(&dispatch.task_id)
        .await
        .expect("task dispatch run context");
    assert_eq!(dispatch_context.bot_id, "bot-worker");
    assert_eq!(dispatch_context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));

    let worker_alias = "worker-provider-run";
    assert_eq!(
        flow.register_task_run_alias(&dispatch.task_id, worker_alias, "bot-worker")
            .await
            .unwrap(),
        TaskRunAliasRegistration::Registered
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: worker_alias.to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
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
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let result_frame = frames.get(1).expect("expected task result frame");
    let params = request_params(result_frame);
    assert_eq!(params["task_id"], dispatch.task_id.as_str());
    let manager_result_run_id = request_id(result_frame);
    assert_ne!(manager_result_run_id, dispatch.task_id);
    assert_ne!(manager_result_run_id, worker_alias);

    let manager_context = run_context
        .get_context(&manager_result_run_id)
        .await
        .expect("manager result run context");
    assert_eq!(manager_context.bot_id, "bot-manager");
    assert_eq!(manager_context.group_id, "group-1");
    assert_eq!(manager_context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert!(!manager_context.terminal);

    let original_context = run_context
        .get_context(&dispatch.task_id)
        .await
        .expect("original worker run context should be retained");
    assert_eq!(original_context.bot_id, "bot-worker");
}

#[tokio::test]
async fn manager_worker_task_final_suffix_snapshot_persists_only_after_tool_result() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;

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
                "content": [{"type": "text", "text": "analysis before tool.\n\nanswer after tool"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::TaskDispatch, BotDeliveryKind::TaskResult]
    );
    let appended = repo.appended().await;
    assert_eq!(appended.len(), 5);
    assert_eq!(appended[0].content, json!("do work"));
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));

    let worker_final = &appended[3];
    assert_eq!(worker_final.group_id, "group-1");
    assert_eq!(worker_final.session_id, "group-1:abcdef12");
    assert_eq!(worker_final.sender_id, "bot-worker");
    assert_eq!(worker_final.sender_type, bcs_domain::SenderType::Bot);
    assert_eq!(worker_final.message_type, "chat");
    assert_eq!(worker_final.content, json!("answer after tool"));
    assert_eq!(worker_final.owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(worker_final.run_id, dispatch.task_id);

    let manager_result = &appended[4];
    assert_eq!(manager_result.group_id, "group-1");
    assert_eq!(manager_result.session_id, "group-1:abcdef12");
    assert_eq!(manager_result.sender_id, "bot-worker");
    assert_eq!(manager_result.sender_type, bcs_domain::SenderType::Bot);
    assert_eq!(manager_result.message_type, "chat");
    assert_eq!(manager_result.content, json!("answer after tool"));
    assert_eq!(manager_result.owner_bot_id, None);
    assert_eq!(manager_result.run_id, dispatch.task_id);
}

#[tokio::test]
async fn duplicate_manager_worker_task_final_does_not_append_duplicate_result_history() {
    let (_support, repo, flow) = manager_worker_flow_with_repo().await;

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

    let final_cmd = |text: &str| BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": text}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    };

    flow.handle_bot_event(final_cmd("task done")).await.unwrap();
    flow.handle_bot_event(final_cmd("duplicate")).await.unwrap();

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        3,
        "dispatch, worker final, and manager result should each persist once"
    );
    assert_eq!(appended[1].content, json!("task done"));
    assert_eq!(appended[1].owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(appended[2].content, json!("task done"));
    assert_eq!(appended[2].owner_bot_id, None);
    assert_eq!(appended[2].run_id, dispatch.task_id);
}

#[tokio::test]
async fn manager_worker_task_final_from_non_target_bot_does_not_append_result_history() {
    let (_support, repo, flow) = manager_worker_flow_with_repo().await;

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
        bot_id: "bot-manager".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": "not worker"}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1, "only the original dispatch should persist");
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));
}

#[tokio::test]
async fn manager_worker_task_result_not_delivered_defers_history_until_retry_success() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.not_delivered_for("bot-manager").await;
    register_manager_worker_task(
        &flow,
        "task-result-not-delivered",
        ChatResponseMode::AfterLastToolCall,
    )
    .await;

    let final_cmd = BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-not-delivered".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": "task done"}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    };

    let first = flow.handle_bot_event(final_cmd.clone()).await.unwrap();

    assert_eq!(first.bot_deliveries.len(), 1);
    assert!(!first.bot_deliveries[0].delivered);
    assert!(
        repo.appended().await.is_empty(),
        "not-delivered manager result must not persist worker final or manager result history"
    );
    assert_eq!(
        flow.task_store
            .get("task-result-not-delivered")
            .await
            .unwrap()
            .status,
        TaskLedgerStatus::Dispatched
    );

    let retry_delivery = Arc::new(support::RecordingBotDelivery::default());
    let retry_flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        retry_delivery,
        support.frontend_delivery.clone(),
    )
    .with_task_store(flow.task_store.clone())
    .with_message_repo(repo.clone());

    let retry = retry_flow.handle_bot_event(final_cmd).await.unwrap();

    assert_eq!(retry.bot_deliveries.len(), 1);
    assert!(retry.bot_deliveries[0].delivered);
    assert_eq!(
        retry_flow
            .task_store
            .get("task-result-not-delivered")
            .await
            .unwrap()
            .status,
        TaskLedgerStatus::Replied
    );
    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        2,
        "retry success should persist exactly worker final and manager result"
    );
    assert_eq!(appended[0].content, json!("task done"));
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(appended[1].content, json!("task done"));
    assert_eq!(appended[1].owner_bot_id, None);
    assert_eq!(appended[1].run_id, "task-result-not-delivered");
}

#[tokio::test]
async fn manager_worker_task_result_retry_uses_only_successful_terminal_text() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.not_delivered_for("bot-manager").await;
    register_manager_worker_task(
        &flow,
        "task-result-retry-text",
        ChatResponseMode::AfterLastToolCall,
    )
    .await;

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-retry-text".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "tool_call_start",
            "tool_call_id": "tool-1",
            "tool_name": "lookup",
        }),
        state: ChatEventState::ToolCallStart,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let terminal_cmd = |text: &str| BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-retry-text".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": text}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    };

    let first = flow.handle_bot_event(terminal_cmd("first")).await.unwrap();

    assert_eq!(first.bot_deliveries.len(), 1);
    assert!(!first.bot_deliveries[0].delivered);
    assert!(
        repo.appended().await.is_empty(),
        "failed first attempt must not persist terminal history"
    );
    assert_eq!(
        flow.task_store
            .get("task-result-retry-text")
            .await
            .unwrap()
            .status,
        TaskLedgerStatus::Dispatched
    );

    let retry_delivery = Arc::new(support::RecordingBotDelivery::default());
    let retry_flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        retry_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_task_store(flow.task_store.clone())
    .with_message_repo(repo.clone());

    let retry = retry_flow
        .handle_bot_event(terminal_cmd("second"))
        .await
        .unwrap();

    assert_eq!(retry.bot_deliveries.len(), 1);
    assert!(retry.bot_deliveries[0].delivered);
    let frames = retry_delivery.frames().await;
    let params = request_params(frames.first().expect("expected retry task result frame"));
    assert_eq!(params["message"]["content"][0]["text"], "[from:Worker] second");
    assert_eq!(params["session_context"]["message"], "second");

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        2,
        "only the successful retry should persist terminal history"
    );
    assert_eq!(appended[0].content, json!("second"));
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(appended[1].content, json!("second"));
    assert_eq!(appended[1].owner_bot_id, None);
    assert_eq!(appended[1].run_id, "task-result-retry-text");
}

#[tokio::test]
async fn manager_worker_task_result_delivery_error_does_not_persist_or_mark_replied() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.fail_for("bot-manager").await;
    register_manager_worker_task(
        &flow,
        "task-result-delivery-error",
        ChatResponseMode::AfterLastToolCall,
    )
    .await;

    let err = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-worker".to_string(),
            run_id: "task-result-delivery-error".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": { "role": "assistant", "content": [{"type": "text", "text": "task done"}] },
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .expect_err("manager delivery error should propagate");

    match err {
        ServiceError::BotNotConnected(bot_id) => assert_eq!(bot_id, "bot-manager"),
        other => panic!("expected BotNotConnected for manager delivery failure, got {other:?}"),
    }
    assert!(
        repo.appended().await.is_empty(),
        "delivery error must not persist worker final or manager result history"
    );
    assert_eq!(
        flow.task_store
            .get("task-result-delivery-error")
            .await
            .unwrap()
            .status,
        TaskLedgerStatus::Dispatched
    );
}
