use super::{
    Arc,
    BcsMessageFlow,
    BlockingInterceptor,
    BotDeliveryKind,
    FrontendDeliveryTarget,
    GroupCoreService,
    GroupStrategy,
    MessageFlowService,
    Participant,
    ParticipantMode,
    ParticipantRole,
    RecordingSessionManagement,
    RecordingSystemMessage,
    ServiceError,
    SessionKind,
    SystemMessageEvent,
    TaskDispatchCommand,
    TaskMessageCommand,
    Value,
    json,
    request_params,
    support,
    test_session
};

#[tokio::test]
async fn manager_worker_task_dispatch_authorizes_manager_role_not_driver_bot_field() {
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
            payload: json!({"message": "do work"}),
        })
        .await
        .expect("manager role should be allowed to dispatch tasks");

    assert_eq!(dispatch.status, "dispatched");
    assert_eq!(dispatch.bot_deliveries[0].target_bot_id, "bot-worker");
}

#[tokio::test]
async fn manager_worker_unknown_target_emits_public_group_notice() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    let mut manager = Participant::bot("bot-manager", ParticipantRole::Manager);
    manager.bot_name = Some("Manager".to_string());
    let mut worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    worker.bot_name = Some("Worker".to_string());
    group.participants = vec![manager, worker];
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

    let error = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "Wrong Worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .expect_err("unknown worker must reject task dispatch");

    assert!(matches!(error, ServiceError::BotNotFound(bot) if bot == "Wrong Worker"));
    assert!(support.bot_delivery.kinds().await.is_empty());
    let notifications = system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].session_id, "group-1:abcdef12");
    match &notifications[0].event {
        SystemMessageEvent::UserNotification { message, .. } => {
            assert!(message.contains("未找到 worker \"Wrong Worker\""));
            assert!(message.contains("Worker (bot-worker)"));
            assert!(message.contains("任务未派发"));
        }
        other => panic!("expected UserNotification, got {other:?}"),
    }
}

#[tokio::test]
async fn task_dispatch_rejects_muted_target_without_delivery() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    let mut worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    worker.mode = Some(ParticipantMode::Muted);
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        worker,
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let err = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .expect_err("muted target should not receive task dispatch");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "target bot is muted");
        }
        other => panic!("expected InvalidOperation for muted target, got {other:?}"),
    }
    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn task_dispatch_rejects_target_muted_in_session_participants() {
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

    let mut session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let mut muted_worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    muted_worker.mode = Some(ParticipantMode::Muted);
    session.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        muted_worker,
    ];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management);

    let err = flow
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
        .expect_err("session-muted target should not receive task dispatch");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "target bot is muted");
        }
        other => panic!("expected InvalidOperation for muted target, got {other:?}"),
    }
    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn task_dispatch_uses_legacy_wire_group_id_to_load_session_participants() {
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

    let mut session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let mut muted_worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    muted_worker.mode = Some(ParticipantMode::Muted);
    session.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        muted_worker,
    ];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management);

    let err = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1:abcdef12".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .expect_err("legacy wire group id should resolve session participants");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "target bot is muted");
        }
        other => panic!("expected InvalidOperation for muted target, got {other:?}"),
    }
    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn task_message_from_worker_is_forwarded_to_manager_session() {
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

    let mut session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    session.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management);

    let outcome = flow
        .handle_task_message(TaskMessageCommand {
            worker_bot_id: "bot-worker".to_string(),
            group_id: "group-1".to_string(),
            payload: json!({
                "message": "blocked on missing schema",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .expect("worker should be able to send task-scoped message to manager");

    assert_eq!(outcome.status, "sent");
    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(outcome.bot_deliveries[0].target_bot_id, "bot-manager");
    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(
        outcome.frontend_deliveries[0].target,
        FrontendDeliveryTarget::Session {
            session_id: "group-1:abcdef12".to_string(),
        }
    );
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskMessage]);

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.first().expect("expected task message frame"));
    assert_eq!(params["session_key"], "group-1:abcdef12");
    assert_eq!(params["bcs_group_id"], "group-1:abcdef12");
    assert_eq!(params["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(params["channel"]["user_id"], "Worker");
    assert_eq!(params["channel"]["actor_id"], "bot-worker");
    assert_eq!(params["channel"]["actor_name"], "Worker");
    assert_eq!(params["channel"]["thread_id"], "group-1");
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
    assert_eq!(params["session_context"]["recipient_role"], "manager");
    assert_eq!(params["session_context"]["from_bot_id"], "bot-worker");
    let text = params["message"]["content"][0]["text"].as_str().unwrap();
    assert!(text.contains("blocked on missing schema"));

    let frontend_commands = support.frontend_delivery.commands().await;
    assert_eq!(frontend_commands.len(), 1);
    assert_eq!(
        frontend_commands[0].target,
        FrontendDeliveryTarget::Session {
            session_id: "group-1:abcdef12".to_string(),
        }
    );
    let event: Value = serde_json::from_str(&frontend_commands[0].event_json).unwrap();
    assert_eq!(event["event"], "chat");
    assert_eq!(event["group_id"], "group-1");
    assert_eq!(event["bot_uuid"], "bot-worker");
    assert_eq!(event["bot_name"], "Worker");
    assert_eq!(event["payload"]["session_key"], "group-1:abcdef12");
    assert_eq!(event["payload"]["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(event["payload"]["message"]["role"], "assistant");
    assert_eq!(event["payload"]["message"]["from"], "bot-worker");
    assert_eq!(event["payload"]["message"]["from_name"], "Worker");
    assert_eq!(
        event["payload"]["message"]["content"][0]["text"],
        "blocked on missing schema"
    );
}

#[tokio::test]
async fn task_message_requires_session_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
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

    let err = flow
        .handle_task_message(TaskMessageCommand {
            worker_bot_id: "bot-worker".to_string(),
            group_id: "group-1".to_string(),
            payload: json!({"message": "progress"}),
        })
        .await
        .expect_err("task.message must be scoped to a session");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "task.message requires bcs_session_id");
        }
        other => panic!("expected InvalidOperation for missing session id, got {other:?}"),
    }
    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn task_dispatch_blocking_interceptor_prevents_bot_delivery() {
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
    )
    .with_interceptor(BlockingInterceptor);

    let result = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await;

    match result {
        Err(ServiceError::Forbidden(message)) => {
            assert_eq!(message, "task dispatch blocked by test");
        }
        other => panic!("expected blocked Forbidden, got {other:?}"),
    }
    // Bot must NOT receive the dispatch frame.
    assert!(support.bot_delivery.frames().await.is_empty());
}
