use super::{
    Arc,
    BcsMessageFlow,
    ChatResponseMode,
    GroupCoreService,
    GroupStatus,
    GroupStrategy,
    MessageFlowService,
    Participant,
    ParticipantRole,
    RecordingSessionManagement,
    ServiceError,
    SessionKind,
    TASK_TTL_MS,
    TaskCompleteCommand,
    TaskDispatchCommand,
    json,
    new_task_entry,
    support,
    test_session
};

#[tokio::test]
async fn task_complete_updates_group_status_without_legacy_service_group_callback() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    group.service_group_uuid = Some("sg-1".to_string());
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert_eq!(outcome.status, "completed");
    assert!(!outcome.callback_requested);
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Completed
    );
}

#[tokio::test]
async fn ws_task_complete_blocked_returns_err_when_pending() {
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

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-driver".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-observer".to_string(),
        target_bot_name: None,
        payload: json!({"message": "do work"}),
    })
    .await
    .unwrap();

    let err = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .expect_err("legacy task.complete should fail while workers are pending");

    match err {
        ServiceError::InvalidOperation { message, request_id } => {
            assert!(message.contains("pending"));
            assert!(message.contains("Observer"));
            assert_eq!(request_id, Some("group-1".to_string()));
        }
        other => panic!("expected invalid operation, got {other:?}"),
    }
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
}

#[tokio::test]
async fn echo_task_complete_blocked_returns_ok_guarded_when_pending() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management.clone());

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-driver".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-observer".to_string(),
        target_bot_name: None,
        payload: json!({"message": "do work"}),
    })
    .await
    .unwrap();

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: true,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert!(outcome.blocked);
    assert_eq!(outcome.pending, vec!["Observer".to_string()]);
    assert!(outcome.completed_session.is_none());
    assert!(!outcome.callback_requested);
    assert!(outcome.frontend_deliveries.is_empty());
    assert!(session_management.completed.read().await.is_empty());
    assert!(support.frontend_delivery.events().await.is_empty());
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
}

#[tokio::test]
async fn task_complete_allowed_when_no_pending() {
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
    flow.task_store.mark_replied(&dispatch.task_id).await;

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert!(!outcome.blocked);
    assert!(outcome.pending.is_empty());
    assert_eq!(outcome.status, "completed");
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Completed
    );
}

#[tokio::test]
async fn task_complete_allowed_when_dispatched_task_timed_out() {
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
    flow.task_store
        .register(new_task_entry(
            "stale-task".to_string(),
            "group-1".to_string(),
            None,
            "bot-driver".to_string(),
            "bot-observer".to_string(),
            Some("Observer".to_string()),
            0,
            ChatResponseMode::AfterLastToolCall,
        ))
        .await;
    let summary = flow
        .task_store
        .ledger_summary_at("group-1", None, TASK_TTL_MS + 1)
        .await;
    assert!(summary.pending.is_empty());
    assert_eq!(summary.timed_out, vec!["Observer".to_string()]);

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert!(!outcome.blocked);
    assert!(outcome.pending.is_empty());
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Completed
    );
}

#[tokio::test]
async fn task_complete_pending_in_other_session_does_not_block() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    let participants = group.participants.clone();
    support.group.upsert(group).await.unwrap();
    let mut pending_session =
        test_session("group-1:pending", "group-1", SessionKind::ServiceInvocation);
    pending_session.participants = participants.clone();
    let mut completing_session =
        test_session("group-1:complete", "group-1", SessionKind::ServiceInvocation);
    completing_session.participants = participants;
    let session_management = Arc::new(RecordingSessionManagement::new(vec![
        pending_session,
        completing_session,
    ]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management.clone());

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-driver".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-observer".to_string(),
        target_bot_name: None,
        payload: json!({
            "bcs_session_id": "group-1:pending",
            "message": "do work"
        }),
    })
    .await
    .unwrap();

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "bcs_session_id": "group-1:complete",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert!(!outcome.blocked);
    assert!(outcome.pending.is_empty());
    assert_eq!(
        outcome.completed_session.as_ref().map(|session| session.id.as_str()),
        Some("group-1:complete")
    );
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
    assert_eq!(
        flow.task_store.pending_targets("group-1", Some("group-1:pending")).await,
        vec!["Observer".to_string()]
    );
}

#[tokio::test]
async fn task_complete_with_session_id_completes_service_session_without_closing_group() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management.clone());

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "bcs_session_id": "group-1:abcdef12",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert_eq!(outcome.status, "completed");
    assert!(outcome.callback_requested);
    assert_eq!(
        outcome.completed_session.as_ref().map(|session| session.id.as_str()),
        Some("group-1:abcdef12")
    );
    let completed = session_management.completed.read().await;
    assert_eq!(completed.len(), 1);
    assert_eq!(completed[0].0, "group-1:abcdef12");
    assert_eq!(completed[0].1, Some(json!("done")));
    assert_eq!(completed[0].2, None);
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
}

#[tokio::test]
async fn task_complete_with_legacy_session_group_id_completes_session_without_closing_group() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management.clone());

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1:abcdef12".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1:abcdef12",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert_eq!(outcome.status, "completed");
    assert!(outcome.callback_requested);
    assert_eq!(
        outcome.completed_session.as_ref().map(|session| session.id.as_str()),
        Some("group-1:abcdef12")
    );
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
}

#[tokio::test]
async fn manager_worker_task_complete_authorizes_manager_role_not_driver_bot_field() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![Participant::bot("bot-manager", ParticipantRole::Manager)];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_task_complete(TaskCompleteCommand {
        bot_id: "bot-manager".to_string(),
        task_id: "group-1".to_string(),
        via_echo: false,
        payload: json!({
            "group_id": "group-1",
            "status": "completed"
        }),
    })
    .await
    .expect("manager role should be allowed to complete task groups");

    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Completed
    );
}
