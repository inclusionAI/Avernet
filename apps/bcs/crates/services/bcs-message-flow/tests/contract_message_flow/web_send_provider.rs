use super::*;

#[derive(Default)]
struct RecordingSystemMessage {
    events: Mutex<Vec<SystemMessageEvent>>,
}

#[async_trait::async_trait]
impl SystemMessageService for RecordingSystemMessage {
    async fn notify(
        &self,
        _group_id: &str,
        event: SystemMessageEvent,
        _session_id: &str,
        _session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.events.lock().await.push(event);
        Ok(1)
    }
}

#[tokio::test]
async fn web_send_delivers_to_registered_provider_target_without_ws_connection() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .insert_named_actor("bot-provider", "Provider")
        .await;
    support
        .registry
        .set_delivery_target(
            "bot-provider",
            support::FakeRegistryService::provider_target("bot-provider"),
        )
        .await;
    let mut provider = Participant::bot("bot-provider", ParticipantRole::Driver);
    provider.bot_name = Some("Provider".to_string());
    let group = Group::new(
        "group-provider",
        "bot-provider",
        vec![
            provider,
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
        ],
    );
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
            group_id: "group-provider".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "hello provider".to_string(),
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
    let targets = support.bot_delivery.targets().await;
    assert_eq!(targets.len(), 1);
    assert!(targets[0].is_http_provider());
}

#[tokio::test]
async fn failed_provider_web_send_uses_unified_system_message() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    install_provider_driver_group(&support, "provider-owner").await;
    support.bot_delivery.fail_for("bot-provider").await;
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
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-provider".to_string(),
            session_id: Some("group-provider:abcdef12".to_string()),
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

    assert_eq!(outcome.failed_count, 1);
    let events = system_message.events.lock().await;
    assert_eq!(events.len(), 1);
    let SystemMessageEvent::GenericNotification { message, .. } = &events[0] else {
        panic!("expected generic notification");
    };
    assert_eq!(message, "消息投递失败，请稍后重试。");
}

#[tokio::test]
async fn deprecated_provider_stream_gray_match_keeps_provider_delivery_target() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    install_provider_driver_group(&support, "gray-user").await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new(vec![
        "gray-user".to_string(),
    ])));

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-provider".to_string(),
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

    assert!(support.bot_delivery.targets().await[0].is_http_provider());
}

#[tokio::test]
async fn deprecated_provider_stream_gray_miss_keeps_provider_delivery_target() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    install_provider_driver_group(&support, "other-user").await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new(vec![
        "gray-user".to_string(),
    ])));

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-provider".to_string(),
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

    assert!(support.bot_delivery.targets().await[0].is_http_provider());
}

#[tokio::test]
async fn deprecated_provider_stream_gray_disabled_keeps_provider_delivery_target() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    install_provider_driver_group(&support, "gray-user").await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new_disabled(vec![
        "gray-user".to_string(),
    ])));

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-provider".to_string(),
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

    assert!(support.bot_delivery.targets().await[0].is_http_provider());
}

#[tokio::test]
async fn deprecated_provider_stream_gray_does_not_change_inject_delivery_target() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "gray-user", true)
        .await
        .unwrap();
    support
        .registry
        .set_delivery_target(
            "bot-observer",
            BotDeliveryTarget::HttpProvider {
                bot_id: "bot-observer".to_string(),
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "bot-observer".to_string(),
                webhook_url: "https://provider.example.com/bcs/webhook".to_string(),
                bcs_to_provider_token: RedactedToken::new("secret-b2p"),
                protocol_version: "2.0".to_string(),
            },
        )
        .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new(vec![
        "gray-user".to_string(),
    ])));

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

    let kinds = support.bot_delivery.kinds().await;
    let targets = support.bot_delivery.targets().await;
    assert_eq!(kinds, vec![BotDeliveryKind::Send, BotDeliveryKind::Inject]);
    assert!(targets[1].is_http_provider());
}

async fn install_provider_driver_group(support: &support::FlowTestSupport, created_by: &str) {
    support
        .registry
        .insert_named_actor("bot-provider", "Provider")
        .await;
    support
        .registry
        .save_created_by("bot-provider", created_by, true)
        .await
        .unwrap();
    support
        .registry
        .set_delivery_target(
            "bot-provider",
            BotDeliveryTarget::HttpProvider {
                bot_id: "bot-provider".to_string(),
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "bot-provider".to_string(),
                webhook_url: "https://provider.example.com/bcs/webhook".to_string(),
                bcs_to_provider_token: RedactedToken::new("secret-b2p"),
                protocol_version: "2.0".to_string(),
            },
        )
        .await;
    let mut provider = Participant::bot("bot-provider", ParticipantRole::Driver);
    provider.bot_name = Some("Provider".to_string());
    support
        .group
        .upsert(Group::new(
            "group-provider",
            "bot-provider",
            vec![
                provider,
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
            ],
        ))
        .await
        .unwrap();
}

#[tokio::test]
async fn provider_send_context_captures_bypass_headers_for_abort_routing() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    install_provider_driver_group(&support, "1").await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-provider".to_string(),
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
            provider_bypass_headers: vec![(
                "x-sandbox-bypass".to_string(),
                "sandbox-route-1".to_string(),
            )],
        })
        .await
        .unwrap();

    let context = run_context
        .find_active_run(&outcome.primary_run_id)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        context.provider_bypass_headers,
        vec![(
            "x-sandbox-bypass".to_string(),
            "sandbox-route-1".to_string(),
        )]
    );
}
