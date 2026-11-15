use super::*;

#[tokio::test]
async fn web_send_resets_message_count_routes_and_delivers() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "hello".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            channel_sender_identity: None,
            sender_conn_id: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert_eq!(support.group.message_count("group-1").await.unwrap(), 0);
    assert_eq!(outcome.status, "started");
    assert!(
        outcome
            .bot_deliveries
            .iter()
            .any(|delivery| delivery.delivered)
    );
    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert!(
        support
            .bot_delivery
            .kinds()
            .await
            .contains(&BotDeliveryKind::Send)
    );
    assert_eq!(
        support.routing.route_calls().await,
        vec![("group-1".to_string(), "hello".to_string(), None)]
    );
}

#[tokio::test]
async fn web_send_explicit_mentions_do_not_inject_manager_worker_workers() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.driver_bot = "bot-driver".to_string();
    group.participants = vec![
        Participant {
            bot_uuid: "bot-driver".to_string(),
            bot_name: Some("Manager".to_string()),
            kind: None,
            role: ParticipantRole::Manager,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    },
        Participant {
            bot_uuid: "bot-observer".to_string(),
            bot_name: Some("Worker".to_string()),
            kind: None,
            role: ParticipantRole::Worker,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    },
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
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: Some("group-1:s1".to_string()),
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "@Manager ask worker later".to_string(),
            mentions: vec!["bot-driver".to_string()],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            channel_sender_identity: None,
            sender_conn_id: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    let delivered_bots: Vec<String> = outcome
        .bot_deliveries
        .iter()
        .map(|delivery| delivery.target_bot_id.clone())
        .collect();
    assert_eq!(delivered_bots, vec!["bot-driver".to_string()]);
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::Send]
    );
}

#[tokio::test]
async fn web_send_in_human_bot_dm_uses_dm_routing_and_keeps_frontend_echo() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    make_human_bot_dm(&support).await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "@bot-observer hello".to_string(),
            mentions: vec!["bot-driver".to_string()],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            channel_sender_identity: None,
            sender_conn_id: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(outcome.delivered_count, 1);
    assert!(support.routing.route_calls().await.is_empty());
    assert_eq!(
        support.routing.dm_route_calls().await,
        vec![(
            "group-1".to_string(),
            "@bot-observer hello".to_string(),
            "human_1".to_string()
        )]
    );
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::Send]
    );
}

#[tokio::test]
async fn web_send_blocking_interceptor_prevents_bot_delivery() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_interceptor(BlockingInterceptor);

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "hello".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            channel_sender_identity: None,
            sender_conn_id: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert!(support.bot_delivery.frames().await.is_empty());
    assert_eq!(outcome.delivered_count, 0);
    assert_eq!(outcome.failed_count, 2);
    assert!(
        outcome
            .delivery_results
            .iter()
            .all(|result| result.error.as_deref() == Some("blocked by test"))
    );
}

#[tokio::test]
async fn web_send_inject_delivery_uses_event_frame() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "observe this".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let inject_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.inject"))
        .expect("inject request frame");
    let BcsFrame::Request(req) = inject_frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], "group-1");
    assert_eq!(params["deliver"], false);
    assert!(
        frames
            .iter()
            .all(|frame| !matches!(frame, BcsFrame::Event(event) if event.event == "chat.inject"))
    );
}

#[tokio::test]
async fn web_send_delivers_to_private_group_targets() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .set_visibility("bot-observer", "private")
        .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "private targets remain in collaboration".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            channel_sender_identity: None,
            sender_conn_id: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert_eq!(outcome.delivered_count, 2);
    assert_eq!(outcome.failed_count, 0);
    assert!(
        outcome
            .delivery_results
            .iter()
            .any(|result| result.bot_uuid == "bot-observer" && result.success)
    );
    assert_eq!(support.bot_delivery.frames().await.len(), 2);
    assert!(
        support
            .bot_delivery
            .kinds()
            .await
            .contains(&BotDeliveryKind::Inject)
    );
}

#[tokio::test]
async fn web_send_partial_delivery_failure_is_represented_in_outcome() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.bot_delivery.fail_for("bot-observer").await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "partial".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            channel_sender_identity: None,
            sender_conn_id: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert_eq!(outcome.delivered_count, 1);
    assert_eq!(outcome.failed_count, 1);
    assert!(
        outcome
            .delivery_results
            .iter()
            .any(|result| result.bot_uuid == "bot-observer" && !result.success)
    );
}
