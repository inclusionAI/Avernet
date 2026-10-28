use super::{
    Arc,
    BcsFrame,
    BcsMessageFlow,
    BotDeliveryKind,
    BotEventCommand,
    ChatEventState,
    GroupCoreService,
    MessageFlowService,
    RecordingSystemMessage,
    ServiceError,
    SystemMessageEvent,
    TaskDispatchCommand,
    TaskLedgerStatus,
    TaskRunAliasRegistration,
    assert_ledger_notification,
    json,
    request_params,
    support
};

#[tokio::test]
async fn task_bot_final_event_is_forwarded_to_driver_and_task_replied() {
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
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    assert_eq!(
        flow.task_store.pending_targets("group-1", None).await,
        vec!["Observer".to_string()]
    );
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Dispatched
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
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
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(
        flow.task_store.resolve_task_id(&dispatch.task_id).await,
        Some(dispatch.task_id.clone())
    );
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Replied
    );
    assert!(flow.task_store.pending_targets("group-1", None).await.is_empty());
    let ledger = flow.task_store.ledger_summary("group-1", None).await;
    assert_eq!(ledger.pending, Vec::<String>::new());
    assert_eq!(ledger.replied, vec!["Observer".to_string()]);
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::TaskDispatch, BotDeliveryKind::TaskResult]
    );
    assert!(support
        .bot_delivery
        .frames()
        .await
        .into_iter()
        .any(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send" && req.id == dispatch.task_id)));
}

#[tokio::test]
async fn duplicate_terminal_task_bot_event_is_ignored_after_reply() {
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
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    let first = flow
        .handle_bot_event(BotEventCommand {
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
            bcs_session_id: None,
        })
        .await
        .unwrap();
    let duplicate = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "duplicate"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(first.bot_deliveries.len(), 1);
    assert!(duplicate.bot_deliveries.is_empty());
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::TaskDispatch, BotDeliveryKind::TaskResult]
    );
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Replied
    );
}

#[tokio::test]
async fn terminal_task_bot_event_from_non_target_bot_is_ignored() {
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
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-driver".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "manager follow-up"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert!(outcome.bot_deliveries.is_empty());
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Dispatched
    );
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
}

#[tokio::test]
async fn task_run_alias_requires_target_bot_and_dispatched_task() {
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
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    let manager_alias = flow
        .register_task_run_alias(&dispatch.task_id, "manager-run", "bot-driver")
        .await
        .unwrap();
    assert_eq!(manager_alias, TaskRunAliasRegistration::Rejected);
    assert_eq!(flow.task_store.resolve_task_id("manager-run").await, None);

    let worker_alias = flow
        .register_task_run_alias(&dispatch.task_id, "worker-run", "bot-observer")
        .await
        .unwrap();
    assert_eq!(worker_alias, TaskRunAliasRegistration::Registered);
    assert_eq!(
        flow.task_store.resolve_task_id("worker-run").await,
        Some(dispatch.task_id.clone())
    );

    flow.task_store.mark_replied(&dispatch.task_id).await;
    let late_alias = flow
        .register_task_run_alias(&dispatch.task_id, "late-worker-run", "bot-observer")
        .await
        .unwrap();
    assert_eq!(late_alias, TaskRunAliasRegistration::Rejected);
    assert_eq!(flow.task_store.resolve_task_id("late-worker-run").await, None);
}

#[tokio::test]
async fn channel_source_message_can_be_rebound_to_accepted_run_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );
    flow.message_tracker
        .cache_channel_source_message_id("source-run", "source-message")
        .await;

    let rebound = flow
        .rebind_channel_source_message("source-run", "accepted-run")
        .await
        .unwrap();

    assert!(rebound);
    assert!(
        flow.message_tracker
            .channel_source_message_id("source-run")
            .await
            .is_none()
    );
    assert_eq!(
        flow.message_tracker
            .channel_source_message_id("accepted-run")
            .await
            .as_deref(),
        Some("source-message")
    );
}

#[tokio::test]
async fn task_ledger_notifications_are_sent_to_driver_after_dispatch_and_reply() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let system_message = Arc::new(RecordingSystemMessage::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_system_message(system_message.clone());

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work", "bcs_session_id": "group-1:abcdef12"}),
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

    let notifications = system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 2);
    assert_eq!(notifications[0].group_id, "group-1");
    assert_eq!(notifications[0].session_id, "group-1:abcdef12");
    assert_eq!(notifications[0].participants.len(), 3);
    assert_ledger_notification(
        &notifications[0].event,
        "待回复: Observer",
        "已回复: -",
    );
    assert_ledger_notification(
        &notifications[1].event,
        "待回复: -",
        "已回复: Observer",
    );
}

#[tokio::test]
async fn dispatch_transport_error_remains_pending_until_confirmed() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    support.bot_delivery.fail_for("bot-observer").await;
    let system_message = Arc::new(RecordingSystemMessage::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_system_message(system_message.clone());

    let Err(err) = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
    else {
        panic!("dispatch should return the delivery failure");
    };

    assert!(matches!(err, ServiceError::BotNotConnected(bot) if bot == "bot-observer"));
    let ledger = flow.task_store.ledger_summary("group-1", None).await;
    assert_eq!(ledger.pending, vec!["Observer".to_string()]);
    assert!(ledger.failed.is_empty());
    assert!(ledger.replied.is_empty());
    assert!(ledger.timed_out.is_empty());

    let notifications = system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    match &notifications[0].event {
        SystemMessageEvent::UserNotification { message, .. } => {
            assert!(message.contains("待回复: Observer"));
            assert!(message.contains("失败: -"));
            assert!(message.contains("超时: -"));
        }
        other => panic!("expected UserNotification, got {other:?}"),
    }
}

#[tokio::test]
async fn non_terminal_bot_event_does_not_mark_replied() {
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
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "working"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Dispatched
    );
    assert_eq!(
        flow.task_store.pending_targets("group-1", None).await,
        vec!["Observer".to_string()]
    );
    let ledger = flow.task_store.ledger_summary("group-1", None).await;
    assert_eq!(ledger.pending, vec!["Observer".to_string()]);
    assert!(ledger.replied.is_empty());
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
}

#[tokio::test]
async fn task_dispatch_uses_manager_session_for_worker_context() {
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
        })
        .await
        .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.first().expect("expected task dispatch frame"));
    assert_eq!(params["session_key"], "group-1:abcdef12");
    assert_eq!(params["bcs_group_id"], "group-1:abcdef12");
    assert_eq!(params["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(params["channel"]["user_id"], "bot-driver");
    assert_eq!(params["channel"]["actor_id"], "bot-driver");
    assert_eq!(params["channel"]["actor_name"], "Driver");
    assert_eq!(params["channel"]["thread_id"], "group-1");
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
    assert_eq!(params["session_context"]["recipient_role"], "worker");

    let entry = flow.task_store.get(&dispatch.task_id).await.unwrap();
    assert_eq!(entry.group_id, "group-1");
    assert_eq!(entry.session_id.as_deref(), Some("group-1:abcdef12"));
}
