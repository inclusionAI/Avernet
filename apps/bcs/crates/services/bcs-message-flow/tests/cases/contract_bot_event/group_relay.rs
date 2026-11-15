use super::{
    ActorKind,
    Arc,
    BcsFrame,
    BcsMessageFlow,
    BotDeliveryKind,
    BotDeliveryTarget,
    BotEventCommand,
    ChatEventState,
    DefaultDelivery,
    FrontendDeliveryTarget,
    GroupCoreService,
    GroupKind,
    GroupStatus,
    MessageFlowService,
    Participant,
    ParticipantMode,
    ParticipantRole,
    RecordingSessionManagement,
    RecordingSystemMessage,
    RoutingMode,
    RoutingPolicy,
    SessionKind,
    SystemMessageEvent,
    json,
    request_params,
    support,
    test_session
};

#[tokio::test]
async fn bot_final_event_for_synthetic_session_skips_group_relay() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-1".to_string(),
            group_id: "bcs-cli:caller:12345678".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": "run-1",
                "bcs_group_id": "bcs-cli:caller:12345678",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.unregistered_run_ids, vec!["run-1".to_string()]);
    assert!(outcome.bot_deliveries.is_empty());
    assert!(support.bot_delivery.frames().await.is_empty());
    let commands = support.frontend_delivery.commands().await;
    assert_eq!(
        commands[0].target,
        FrontendDeliveryTarget::Group {
            group_id: "bcs-cli:caller:12345678".to_string()
        }
    );
    assert!(commands[0].run_fallback.is_some());
}

#[tokio::test]
async fn bot_final_event_in_human_bot_dm_does_not_self_relay_to_sender_bot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_kind = GroupKind::Dm;
    group.driver_bot = "bot-observer".to_string();
    group.dm_pair_key = Some("bot-observer|human_1".to_string());
    group.participants = vec![
        Participant {
            bot_uuid: "human_1".to_string(),
            bot_name: Some("Human One".to_string()),
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    },
        Participant {
            bot_uuid: "bot-observer".to_string(),
            bot_name: Some("Observer".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    },
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-dm".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
                "routing": {
                    "responders": [{"type": "bot", "value": "bot-observer"}],
                    "reason": "ignored for dm"
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert!(outcome.bot_deliveries.is_empty());
    assert!(support.bot_delivery.frames().await.is_empty());
    assert_eq!(
        support.routing.dm_route_calls().await,
        vec![(
            "group-1".to_string(),
            "done".to_string(),
            "bot-observer".to_string()
        )]
    );
}

#[tokio::test]
async fn bot_final_event_relays_through_bot_delivery_port() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-1".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.unregistered_run_ids, vec!["run-1".to_string()]);
    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(support.group.message_count("group-1").await.unwrap(), 2);
    assert!(support
        .bot_delivery
        .kinds()
        .await
        .contains(&BotDeliveryKind::Send));
    assert!(support
        .bot_delivery
        .frames()
        .await
        .into_iter()
        .any(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send")));
}

#[tokio::test]
async fn failed_provider_bot_event_send_uses_unified_system_message() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .set_delivery_target(
            "bot-driver",
            support::FakeRegistryService::provider_target("bot-driver"),
        )
        .await;
    support.bot_delivery.fail_for("bot-driver").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
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

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-provider-failure".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();

    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert!(!outcome.bot_deliveries[0].delivered);
    let notifications = system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    let SystemMessageEvent::GenericNotification { message, .. } = &notifications[0].event else {
        panic!("expected generic notification");
    };
    assert_eq!(message, "消息投递失败，请稍后重试。");
}

#[tokio::test]
async fn bot_final_event_relay_uses_each_provider_targets_session_tags() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .insert_named_actor("bot-consultant", "Consultant")
        .await;
    support
        .registry
        .set_delivery_target(
            "bot-driver",
            support::FakeRegistryService::provider_target("bot-driver"),
        )
        .await;
    support
        .registry
        .set_delivery_target(
            "bot-consultant",
            support::FakeRegistryService::provider_target("bot-consultant"),
        )
        .await;

    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    let mut stale_consultant =
        Participant::bot("bot-consultant", ParticipantRole::Consultant);
    stale_consultant.tags = vec!["stale-consultant".to_string()];
    group.participants.push(stale_consultant);
    group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "bot-driver")
        .expect("driver participant")
        .tags = vec!["stale-driver".to_string()];
    support.group.upsert(group).await.unwrap();

    let mut driver = Participant::bot("bot-driver", ParticipantRole::Driver);
    driver.tags = vec!["draft".to_string(), "driver-only".to_string()];
    let mut sender = Participant::bot("bot-observer", ParticipantRole::Observer);
    sender.tags = vec!["sender-tag".to_string()];
    let mut consultant = Participant::bot("bot-consultant", ParticipantRole::Consultant);
    consultant.tags = vec!["online".to_string(), "consultant-only".to_string()];
    let mut session = test_session(
        "group-1:abcdef12",
        "group-1",
        SessionKind::ServiceInvocation,
    );
    session.participants = vec![driver, sender, consultant];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management);

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-provider-tags".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done with tags"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 2);
    assert!(support
        .bot_delivery
        .targets()
        .await
        .iter()
        .all(BotDeliveryTarget::is_http_provider));
    let send = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("driver chat.send frame");
    assert_eq!(
        request_params(send)["tags"],
        json!(["draft", "driver-only"])
    );
    let inject = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.inject"))
        .expect("consultant chat.inject frame");
    assert_eq!(
        request_params(inject)["tags"],
        json!(["online", "consultant-only"])
    );
}

#[tokio::test]
async fn bot_final_event_relay_preserves_session_id_for_legacy_target() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-session-relay".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done in session"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.first().expect("expected relay frame"));
    assert_eq!(params["bcs_group_id"], "group-1:abcdef12");
    assert_eq!(params["channel"]["actor_id"], "bot-observer");
    assert_eq!(params["channel"]["actor_name"], "Observer");
    assert!(
        params.get("bcs_session_id").is_none(),
        "v2 targets should receive session id through bcs_group_id"
    );
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
}

#[tokio::test]
async fn bot_final_event_relay_keeps_group_id_for_legacy_default_session_target() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-legacy-default-session-relay".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done in legacy default session"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:00000000".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.first().expect("expected relay frame"));
    assert_eq!(params["bcs_group_id"], "group-1");
    assert!(
        params.get("bcs_session_id").is_none(),
        "v2 targets should not receive bcs_session_id"
    );
    assert_eq!(params["session_context"]["session_id"], "group-1:00000000");
}

#[tokio::test]
async fn private_bot_final_event_relays_without_hidden_prompt() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .set_visibility("bot-observer", "private")
        .await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-private-sender".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "private sender done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(outcome.delivered_count, 1);
    assert_eq!(outcome.failed_count, 0);
    assert!(support
        .frontend_delivery
        .events()
        .await
        .iter()
        .all(|event| !event.contains("隐身") && !event.contains("无法看到消息")));
}

#[tokio::test]
async fn bot_final_event_relays_to_private_group_targets() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.set_visibility("bot-driver", "private").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-private-target".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "private target done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(outcome.delivered_count, 1);
    assert_eq!(outcome.failed_count, 0);
    assert!(outcome
        .delivery_results
        .iter()
        .any(|result| result.bot_uuid == "bot-driver" && result.success));
    assert!(support
        .bot_delivery
        .frames()
        .await
        .into_iter()
        .any(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send")));
}

#[tokio::test]
async fn relay_limit_marks_group_inactive_and_publishes_frontend_event() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_relay_turn_limit(1);

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-1".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "limit"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(support.group.get("group-1").await.unwrap().status, GroupStatus::Inactive);
    assert!(outcome.bot_deliveries.is_empty());
    assert_eq!(outcome.frontend_deliveries.len(), 2);
    assert!(support
        .frontend_delivery
        .events()
        .await
        .iter()
        .any(|event| event.contains("群聊消息数量已达上限")));
}
