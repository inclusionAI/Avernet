use super::{
    Arc,
    BcsMessageFlow,
    ChannelRenderHint,
    Duration,
    MessageVisibilityDomain,
    RecordingChannelService,
    json,
    support,
    timeout
};

#[tokio::test]
async fn queued_im_reaction_targets_source_message_and_does_not_replay_on_restart() {
    use bcs_domain::{DeliveryType, NewMessage, SenderType, message_delivery::DeliveryFlowKind};
    use bcs_service_api::{ManagedMessageDeliveryService, DeliveryTransitionCommand};
    use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
    let fixture = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
    let flow = Arc::new(BcsMessageFlow::new(fixture.group, fixture.routing, fixture.registry, fixture.bot_delivery, fixture.frontend_delivery)
        .with_message_repo(repo).with_managed_deliveries(service.clone()));
    let channel = Arc::new(RecordingChannelService::default());
    assert!(flow.channel_slot().set(channel.clone()).is_ok());
    let (shutdown, receiver) = tokio::sync::watch::channel(false);
    let notifications = tokio::spawn(bcs_message_flow::delivery_notifications::run(Arc::downgrade(&flow), service.subscribe(), receiver));
    let now = chrono::Utc::now().timestamp_millis();
    service.admit(AdmitMessageDeliveries {
        display_message: None,
        message_id: "im-young".into(), flow_kind: DeliveryFlowKind::Group, now_ms: now - 3_000, expire_at_ms: None, event: None,
        targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: "bot-driver".into(), kind: DeliveryType::Send, max_queued: 10, semantic_projection_json: json!({"version":1}), run_id: None }],
        message: NewMessage { visibility_domain: MessageVisibilityDomain::Chat, audience: None, group_id: "group-1".into(), session_id: "group-1:im-original".into(), sender_id: "human_1".into(), sender_type: SenderType::Human,
            message_type: "chat".into(), content: json!({"text":"young","source_im_message_id":"im-young-source"}), client_msg_id: None, owner_bot_id: None, created_at: (now - 3_000) as u64, run_id: String::new() },
    }).await.unwrap();
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert!(channel.outbound().await.is_empty(), "queued hints wait ten seconds");
    let admitted = service.admit(AdmitMessageDeliveries {
        display_message: None,
        message_id: "im-queue".into(), flow_kind: DeliveryFlowKind::Group, now_ms: now - 11_000, expire_at_ms: None, event: None,
        targets: [("bot-driver", DeliveryType::Send), ("bot-observer", DeliveryType::Send), ("context-only", DeliveryType::Inject)].into_iter()
            .map(|(bot, kind)| DeliveryAdmissionTarget { rejection: None, target_bot_id: bot.into(), kind, max_queued: 10, semantic_projection_json: json!({"version":1}), run_id: None,}).collect(),
        message: NewMessage { visibility_domain: MessageVisibilityDomain::Chat, audience: None, group_id: "group-1".into(), session_id: "group-1:im-original".into(), sender_id: "human_1".into(), sender_type: SenderType::Human,
            message_type: "chat".into(), content: json!({"text":"hello","source_im_message_id":"im-original"}), client_msg_id: None, owner_bot_id: None, created_at: (now - 11_000) as u64, run_id: String::new() },
    }).await.unwrap();
    timeout(Duration::from_secs(2), async {
        while channel.outbound().await.is_empty() { tokio::time::sleep(Duration::from_millis(10)).await; }
    }).await.unwrap();
    let messages = channel.outbound().await;
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0].source_im_message_id.as_deref(), Some("im-original"));
    assert_eq!(messages[0].bcs_session_id, "group-1:im-original");
    assert_eq!(messages[0].text, None);
    assert_eq!(messages[0].render_hint, ChannelRenderHint::IgnoreByDefault);
    assert_eq!(messages[0].raw_payload["type"], "message.delivery.reaction");
    assert_eq!(messages[0].raw_payload["state"], "queued");
    assert_eq!(messages[0].raw_payload["message_id"], "im-queue");
    let failed_row = admitted.deliveries.iter().find(|row| row.target_bot_id == "bot-observer").unwrap();
    let running = service.transition(DeliveryTransitionCommand { delivery_id: failed_row.delivery_id.clone(), expected_state_version: failed_row.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
        caller_cancel: None,
        caller_dispatch_commit: None,
        caller_terminal: None,
        now_ms: now, request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    let failed = service.transition(DeliveryTransitionCommand { delivery_id: running.delivery_id.clone(), expected_state_version: running.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::Failed,
        caller_cancel: None,
        caller_dispatch_commit: None,
        caller_terminal: None,
        now_ms: now, request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    assert_eq!(failed.last_error_code.as_deref(), Some("bot_terminal_error"));
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert_eq!(channel.outbound().await.len(), 1, "a terminal chat error has its own safe channel feedback and must not also emit a generic delivery hint");
    let rejected = service.admit(AdmitMessageDeliveries {
        display_message: None,
        message_id: "im-rejected".into(), flow_kind: DeliveryFlowKind::Group, now_ms: now, expire_at_ms: None, event: None,
        targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: "bot-observer".into(), kind: DeliveryType::Send, max_queued: 10, semantic_projection_json: json!({"version":1}), run_id: None,}],
        message: NewMessage { visibility_domain: MessageVisibilityDomain::Chat, audience: None, group_id: "group-1".into(), session_id: "group-1:im-original".into(), sender_id: "human_1".into(), sender_type: SenderType::Human,
            message_type: "chat".into(), content: json!({"text":"reject","source_im_message_id":"im-rejected-source"}), client_msg_id: None, owner_bot_id: None, created_at: now as u64, run_id: String::new() },
    }).await.unwrap().deliveries.remove(0);
    let rejected = service.transition(DeliveryTransitionCommand { delivery_id: rejected.delivery_id.clone(), expected_state_version: rejected.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
        caller_cancel: None,
        caller_dispatch_commit: None,
        caller_terminal: None,
        now_ms: now, request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    service.transition(DeliveryTransitionCommand { delivery_id: rejected.delivery_id.clone(), expected_state_version: rejected.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::TransportRejected,
        caller_cancel: None,
        caller_dispatch_commit: None,
        caller_terminal: None,
        now_ms: now, request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    timeout(Duration::from_secs(2), async {
        while channel.outbound().await.len() < 2 { tokio::time::sleep(Duration::from_millis(10)).await; }
    }).await.unwrap();
    let rejected_hint = channel.outbound().await;
    assert_eq!(rejected_hint[1].source_im_message_id.as_deref(), Some("im-rejected-source"));
    assert!(rejected_hint[1].text.as_ref().unwrap().contains("bot-observer：处理失败"));
    let row = admitted.deliveries.iter().find(|row| row.target_bot_id == "bot-driver").unwrap();
    service.transition(DeliveryTransitionCommand { delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelRequested,
        caller_cancel: None,
        caller_dispatch_commit: None,
        caller_terminal: None,
        now_ms: now, request_id: None, actor_id: Some("human_1".into()), reply: None, transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    timeout(Duration::from_secs(2), async {
        while channel.outbound().await.len() < 4 { tokio::time::sleep(Duration::from_millis(10)).await; }
    }).await.unwrap();
    let messages = channel.outbound().await;
    assert_eq!(messages[2].raw_payload["type"], "message.delivery.reaction");
    assert_eq!(messages[2].raw_payload["state"], "clear");
    assert_eq!(messages[2].source_im_message_id.as_deref(), Some("im-original"));
    assert!(messages[3].text.as_ref().unwrap().contains("已取消"));
    shutdown.send(true).unwrap();
    notifications.await.unwrap();
    let (shutdown, receiver) = tokio::sync::watch::channel(false);
    let restarted = tokio::spawn(bcs_message_flow::delivery_notifications::run(Arc::downgrade(&flow), service.subscribe(), receiver));
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert_eq!(channel.outbound().await.len(), 4, "notifications are not replayed from durable state");
    shutdown.send(true).unwrap();
    restarted.await.unwrap();
}

#[tokio::test]
async fn expired_im_reaction_waits_until_no_target_remains_queued() {
    use bcs_domain::{DeliveryType, NewMessage, SenderType, message_delivery::DeliveryFlowKind};
    use bcs_service_api::{DeliveryTransitionCommand, ManagedMessageDeliveryService};
    use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
    let fixture = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
    let flow = Arc::new(BcsMessageFlow::new(fixture.group, fixture.routing, fixture.registry, fixture.bot_delivery, fixture.frontend_delivery)
        .with_message_repo(repo).with_managed_deliveries(service.clone()));
    let channel = Arc::new(RecordingChannelService::default());
    assert!(flow.channel_slot().set(channel.clone()).is_ok());
    let (shutdown, receiver) = tokio::sync::watch::channel(false);
    let notifications = tokio::spawn(bcs_message_flow::delivery_notifications::run(Arc::downgrade(&flow), service.subscribe(), receiver));
    let now = chrono::Utc::now().timestamp_millis();
    let admitted = service.admit(AdmitMessageDeliveries {
        display_message: None,
        message_id: "im-expiring".into(), flow_kind: DeliveryFlowKind::Group, now_ms: now - 11_000, expire_at_ms: Some(now + 1_000), event: None,
        targets: ["bot-driver", "bot-observer"].into_iter().map(|bot| DeliveryAdmissionTarget { rejection: None, target_bot_id: bot.into(), kind: DeliveryType::Send, max_queued: 10, semantic_projection_json: json!({"version":1}), run_id: None }).collect(),
        message: NewMessage { visibility_domain: MessageVisibilityDomain::Chat, audience: None, group_id: "group-1".into(), session_id: "group-1:im-expiring".into(), sender_id: "human_1".into(), sender_type: SenderType::Human,
            message_type: "chat".into(), content: json!({"text":"later","source_im_message_id":"im-expiring-source"}), client_msg_id: None, owner_bot_id: None, created_at: (now - 11_000) as u64, run_id: String::new() },
    }).await.unwrap();
    timeout(Duration::from_secs(2), async {
        while channel.outbound().await.is_empty() { tokio::time::sleep(Duration::from_millis(10)).await; }
    }).await.unwrap();
    let queued = &channel.outbound().await[0];
    assert_eq!(queued.source_im_message_id.as_deref(), Some("im-expiring-source"));
    assert_eq!(queued.raw_payload["state"], "queued");
    let row = admitted.deliveries.iter().find(|row| row.target_bot_id == "bot-driver").unwrap();
    service.transition(DeliveryTransitionCommand {
        delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::QueueExpired,
        caller_cancel: None,
        caller_dispatch_commit: None,
        caller_terminal: None,
        now_ms: now + 1_000, request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert_eq!(channel.outbound().await.len(), 1, "a queued target keeps the actionable queue reaction visible");
    let row = admitted.deliveries.iter().find(|row| row.target_bot_id == "bot-observer").unwrap();
    service.transition(DeliveryTransitionCommand {
        delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::QueueExpired,
        caller_cancel: None,
        caller_dispatch_commit: None,
        caller_terminal: None,
        now_ms: now + 1_000, request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    timeout(Duration::from_secs(2), async {
        while channel.outbound().await.len() < 2 { tokio::time::sleep(Duration::from_millis(10)).await; }
    }).await.unwrap();
    let messages = channel.outbound().await;
    assert_eq!(messages.len(), 2);
    assert_eq!(messages[1].source_im_message_id.as_deref(), Some("im-expiring-source"));
    assert_eq!(messages[1].text, None);
    assert_eq!(messages[1].raw_payload["type"], "message.delivery.reaction");
    assert_eq!(messages[1].raw_payload["state"], "expired");
    shutdown.send(true).unwrap();
    notifications.await.unwrap();
}

#[tokio::test(start_paused = true)]
async fn reaction_delivery_is_bounded_to_one_provider_call_per_tick() {
    use bcs_domain::{DeliveryType, NewMessage, SenderType, message_delivery::DeliveryFlowKind};
    use bcs_service_api::ManagedMessageDeliveryService;
    use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
    let fixture = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
    let flow = Arc::new(BcsMessageFlow::new(fixture.group, fixture.routing, fixture.registry, fixture.bot_delivery, fixture.frontend_delivery)
        .with_message_repo(repo).with_managed_deliveries(service.clone()));
    let channel = Arc::new(RecordingChannelService::default());
    assert!(flow.channel_slot().set(channel.clone()).is_ok());
    let (shutdown, shutdown_receiver) = tokio::sync::watch::channel(false);
    let changes = service.subscribe();
    let now = chrono::Utc::now().timestamp_millis();
    for index in 1..=2 {
        service.admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: format!("im-bounded-{index}"), flow_kind: DeliveryFlowKind::Group, now_ms: now - 11_000, expire_at_ms: None, event: None,
            targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: "bot-driver".into(), kind: DeliveryType::Send, max_queued: 10, semantic_projection_json: json!({"version":1}), run_id: None }],
            message: NewMessage { visibility_domain: MessageVisibilityDomain::Chat, audience: None, group_id: "group-1".into(), session_id: format!("group-1:im-bounded-{index}"), sender_id: "human_1".into(), sender_type: SenderType::Human,
                message_type: "chat".into(), content: json!({"text":"later","source_im_message_id":format!("im-bounded-source-{index}")}), client_msg_id: None, owner_bot_id: None, created_at: (now - 11_000) as u64, run_id: String::new() },
        }).await.unwrap();
    }
    let notifications = tokio::spawn(bcs_message_flow::delivery_notifications::run(Arc::downgrade(&flow), changes, shutdown_receiver));
    for _ in 0..20 { tokio::task::yield_now().await; }
    assert_eq!(channel.outbound().await.len(), 1, "one tick performs at most one provider delivery");
    tokio::time::advance(Duration::from_millis(100)).await;
    for _ in 0..20 { tokio::task::yield_now().await; }
    assert_eq!(channel.outbound().await.len(), 2, "the next tick continues pending reaction work");
    shutdown.send(true).unwrap();
    notifications.await.unwrap();
}
