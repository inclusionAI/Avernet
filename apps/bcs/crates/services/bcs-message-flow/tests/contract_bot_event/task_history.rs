use super::{
    Arc,
    BcsFrame,
    BcsMessageFlow,
    BotDeliveryKind,
    BotEventCommand,
    BotRunContextPort,
    CONFIGURED_TASK_RUN_TIMEOUT_MS,
    ChatEventState,
    ChatResponseMode,
    GroupCoreService,
    MessageAudience,
    MessageFlowService,
    RecordingMessageRepo,
    ServiceError,
    TaskDispatchCommand,
    TaskLedgerStatus,
    TaskMessageCommand,
    Value,
    json,
    manager_worker_flow_with_blocking_repo,
    manager_worker_flow_with_repo,
    register_manager_worker_task,
    request_id,
    support
};

#[tokio::test]
async fn manager_worker_task_dispatch_persists_dispatch_to_worker_history_after_delivery() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;

    let outcome = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .expect("task.dispatch should deliver to worker");

    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    let msg = &appended[0];
    assert_eq!(msg.group_id, "group-1");
    assert_eq!(msg.session_id, "group-1:abcdef12");
    assert_eq!(msg.sender_id, "bot-manager");
    assert_eq!(msg.sender_type, bcs_domain::SenderType::Bot);
    assert_eq!(msg.message_type, "chat");
    assert_eq!(msg.content, json!("do work"));
    assert_eq!(msg.owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(msg.run_id, outcome.task_id);
}

#[tokio::test]
async fn manager_worker_task_dispatch_records_context_before_history_persistence() {
    let (support, repo, run_context, flow) = manager_worker_flow_with_blocking_repo().await;

    let before_dispatch_ms = bcs_protocol::now_ms();
    let handle = tokio::spawn(async move {
        flow.handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
    });

    repo.wait_for_append().await;
    let frames = support.bot_delivery.frames().await;
    let run_id = request_id(frames.first().expect("expected task.dispatch frame"));
    let context_before_append_finished = run_context.get_context(&run_id).await;
    let after_context_ms = bcs_protocol::now_ms();
    repo.release_append();
    let outcome = handle
        .await
        .expect("task.dispatch join should complete")
        .expect("task.dispatch should deliver to worker");

    assert_eq!(outcome.task_id, run_id);
    let context = context_before_append_finished
        .expect("run context should be recorded before history append completes");
    assert_eq!(context.bot_id, "bot-worker");
    assert_eq!(context.group_id, "group-1");
    assert_eq!(context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert!(
        context.deadline_ms
            >= before_dispatch_ms.saturating_add(CONFIGURED_TASK_RUN_TIMEOUT_MS)
    );
    assert!(
        context.deadline_ms
            <= after_context_ms.saturating_add(CONFIGURED_TASK_RUN_TIMEOUT_MS)
    );
    assert_eq!(repo.appended().await.len(), 1);
}

#[tokio::test]
async fn manager_worker_rejected_task_dispatch_persists_error_and_sends_result() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.not_delivered_for("bot-worker").await;

    let err = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .expect_err("not-delivered task.dispatch should fail");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "target bot is not connected");
        }
        other => panic!("expected InvalidOperation for not-delivered dispatch, got {other:?}"),
    }
    let messages = repo.appended().await;
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0].message_type, bcs_domain::CHAT_ERROR_MESSAGE_TYPE);
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch, BotDeliveryKind::TaskResult]);
}

#[tokio::test]
async fn manager_worker_task_dispatch_does_not_persist_when_delivery_errors() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.fail_for("bot-worker").await;

    let err = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .expect_err("delivery error should fail task.dispatch");

    match err {
        ServiceError::BotNotConnected(bot_id) => assert_eq!(bot_id, "bot-worker"),
        other => panic!("expected BotNotConnected for failed dispatch, got {other:?}"),
    }
    assert!(repo.appended().await.is_empty());
}

#[tokio::test]
async fn non_manager_worker_task_dispatch_does_not_persist_owner_tagged_dispatch_history() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
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

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-driver".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-observer".to_string(),
        target_bot_name: None,
        payload: json!({"message": "do work"}),
    })
    .await
    .expect("legacy master_slave dispatch should remain deliverable");

    assert!(repo.appended().await.is_empty());
}

#[tokio::test]
async fn manager_worker_task_message_persists_worker_message_to_manager_history_after_delivery() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;

    flow.handle_task_message(TaskMessageCommand {
        worker_bot_id: "bot-worker".to_string(),
        group_id: "group-1".to_string(),
        payload: json!({
            "message": "blocked on missing schema",
            "bcs_session_id": "group-1:abcdef12",
        }),
    })
    .await
    .expect("task.message should deliver to manager");

    let frames = support.bot_delivery.frames().await;
    let delivery_run_id = match frames.first().expect("expected task.message frame") {
        BcsFrame::Request(req) => req.id.clone(),
        other => panic!("expected request frame for task.message, got {other:?}"),
    };
    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    let msg = &appended[0];
    assert_eq!(msg.group_id, "group-1");
    assert_eq!(msg.session_id, "group-1:abcdef12");
    assert_eq!(msg.sender_id, "bot-worker");
    assert_eq!(msg.sender_type, bcs_domain::SenderType::Bot);
    assert_eq!(msg.message_type, "chat");
    assert_eq!(msg.content, json!("blocked on missing schema"));
    assert_eq!(msg.owner_bot_id, None);
    assert_eq!(msg.audience, Some(MessageAudience::FullOnly));
    assert!(!msg.run_id.is_empty());
    assert_eq!(msg.run_id, delivery_run_id);
    assert_ne!(msg.run_id, "group-1:abcdef12");
}

#[tokio::test]
async fn manager_task_error_storage_retry_does_not_redeliver_result() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    register_manager_worker_task(&flow, "task-error-retry", ChatResponseMode::Full).await;
    let command = BotEventCommand {
        bot_id: "bot-worker".into(), run_id: "task-error-retry".into(), group_id: "group-1".into(),
        bcs_session_id: Some("group-1:abcdef12".into()), event_type: "chat.event".into(),
        state: ChatEventState::Error, event_payload: json!({"errorMessage":"失败"}),
    };
    repo.set_fail_appends(true);
    assert!(flow.handle_bot_event(command.clone()).await.is_err());
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskResult]);
    assert!(support.frontend_delivery.events().await.is_empty());
    repo.set_fail_appends(false);
    flow.handle_bot_event(command.clone()).await.unwrap();
    flow.handle_bot_event(command).await.unwrap();
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskResult]);
    let rows = repo.appended().await;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].message_type, "chat_error");
    assert_eq!(rows[0].run_id, "task-error-retry");
}

#[tokio::test]
async fn bot_error_terminal_persists_display_without_bot_delivery() {
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

    let error = "Concurrent request timed out";
    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-error-only".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": "run-error-only",
                "bcs_group_id": "group-1",
                "state": "error",
                "errorMessage": error,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": error}],
                    "timestamp": 1,
                },
            }),
            state: ChatEventState::Error,
            bcs_session_id: Some("group-1:error-session".into()),
        })
        .await
        .unwrap();

    assert!(
        outcome.bot_deliveries.is_empty(),
        "error terminal must not be relayed to other bots"
    );
    assert!(
        support.bot_delivery.frames().await.is_empty(),
        "error terminal must not broadcast through bot delivery"
    );
    let rows = repo.appended().await;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].message_type, "chat_error");
    assert_eq!(rows[0].content, json!(error));
    assert_eq!(rows[0].client_msg_id.as_deref(), Some("chat-error:run-error-only"));

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 1);
    let frame: Value = serde_json::from_str(&events[0]).unwrap();
    assert_eq!(frame["event"], "chat");
    assert_eq!(frame["payload"]["state"], "error");
    assert_eq!(frame["payload"]["errorMessage"], error);
    assert_eq!(frame["payload"]["message"]["content"][0]["text"], error);
}

#[tokio::test]
async fn manager_worker_task_message_records_context_before_history_persistence() {
    let (support, repo, run_context, flow) = manager_worker_flow_with_blocking_repo().await;

    let handle = tokio::spawn(async move {
        flow.handle_task_message(TaskMessageCommand {
            worker_bot_id: "bot-worker".to_string(),
            group_id: "group-1".to_string(),
            payload: json!({
                "message": "blocked on missing schema",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
    });

    repo.wait_for_append().await;
    let frames = support.bot_delivery.frames().await;
    let run_id = request_id(frames.first().expect("expected task.message frame"));
    let context_before_append_finished = run_context.get_context(&run_id).await;
    repo.release_append();
    handle
        .await
        .expect("task.message join should complete")
        .expect("task.message should deliver to manager");

    let context = context_before_append_finished
        .expect("run context should be recorded before history append completes");
    assert_eq!(context.bot_id, "bot-manager");
    assert_eq!(context.group_id, "group-1");
    assert_eq!(context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert_eq!(repo.appended().await.len(), 1);
}

/// Regression: a TASK run (its run_id resolves to a dispatched task) that streams
/// buffered chat deltas and then terminates with `error` must still flush the
/// buffered segment to history. The terminal flush/cleanup runs BEFORE the task
/// early-return; an earlier ordering placed it after, so task runs took the task
/// branch and returned without flushing, losing the partial reply and leaking the
/// per-run buffer.
#[tokio::test]
async fn task_run_error_terminal_flushes_buffered_chat_segment() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
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

    // Dispatch a task so the run_id below resolves to a task run.
    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    // Stream buffered chat deltas on the task run — memory only, not persisted.
    for delta in ["任务", "进行中"] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({ "state": "delta", "delta_text": delta }),
            state: ChatEventState::Delta,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    }
    assert!(
        repo.appended().await.is_empty(),
        "streaming deltas must only buffer, not persist"
    );

    // The task run dies with an error before any `final` frame.
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "error", "errorMessage": "engine crashed" }),
        state: ChatEventState::Error,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        1,
        "task-run error terminal must still flush the buffered chat segment"
    );
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].run_id, dispatch.task_id);
    assert_eq!(
        appended[0].content,
        json!("任务进行中"),
        "the flushed content must be the accumulated segment text"
    );
}

/// Regression: a TASK run that streams buffered chat deltas and then ends with a
/// normal `final` must flush the accumulated segment to history AND still relay
/// the task result to the driver. The terminal flush runs before the task early
/// return; without it, a successful task run's transcript is lost from history
/// and the per-run buffer leaks (the same early-return class as the error case).
#[tokio::test]
async fn task_run_final_terminal_flushes_buffered_chat_segment() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    // Stream buffered chat deltas on the task run — memory only, not persisted.
    for delta in ["任务", "已完成"] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({ "state": "delta", "delta_text": delta }),
            state: ChatEventState::Delta,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    }
    assert!(
        repo.appended().await.is_empty(),
        "streaming deltas must only buffer, not persist"
    );

    // The task run ends with a normal Final.
    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "任务已完成"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    // History flush happened (delta mode → accumulated buffer text).
    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        1,
        "task-run final must flush the buffered chat segment to history"
    );
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].run_id, dispatch.task_id);
    assert_eq!(appended[0].content, json!("任务已完成"));

    // The task result is still relayed to the driver and the task marked replied.
    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Replied
    );
}

/// Regression: a DUPLICATE task final (after the task is already Replied) must
/// NOT append a second history row. The terminal chat flush is gated by the same
/// status==Dispatched check as task-result delivery, so the second final is
/// rejected before it can flush. (An earlier version flushed before that gate,
/// producing a duplicate history row on retried finals.)
#[tokio::test]
async fn duplicate_task_final_does_not_append_second_history_row() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    let final_cmd = |text: &str| BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": text}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: None,
    };

    flow.handle_bot_event(final_cmd("task done")).await.unwrap();
    // Duplicate/retried final after the task is Replied.
    flow.handle_bot_event(final_cmd("duplicate")).await.unwrap();

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        1,
        "duplicate task final after Replied must not append a second history row"
    );
    assert_eq!(appended[0].content, json!("task done"));
}

/// Regression: a task terminal from a NON-TARGET bot (using the task run_id) must
/// NOT append history. The `target_bot == cmd.bot_id` gate rejects it before the
/// flush, so a misrouted event cannot pollute group history.
#[tokio::test]
async fn task_final_from_non_target_bot_does_not_append_history() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    // A different bot emits a terminal event carrying the task's run_id.
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-intruder".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": "intruder"}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    assert!(
        repo.appended().await.is_empty(),
        "a task terminal from a non-target bot must not append history"
    );
}
