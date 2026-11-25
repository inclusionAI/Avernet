use super::*;

struct EnabledEventFactory;

impl EventRecordFactoryPort for EnabledEventFactory {
    fn prepare(
        &self,
        event: NewEvent,
    ) -> Result<
        Option<bcs_service_api::port::repo::AppendEventRecord>,
        bcs_service_api::port::EventRecordError,
    > {
        Ok(Some(bcs_service_api::port::repo::AppendEventRecord {
            event,
            recorded_at: "2026-08-20T00:00:00.000Z".to_string(),
            retention_until_ms: u64::MAX,
            env: "test".to_string(),
        }))
    }
}

#[tokio::test]
async fn persistent_group_send_routes_stores_and_returns_legacy_fields() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_persistent_group_send(PersistentGroupSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            sender: "human_1".to_string(),
            content: "@Driver persistent hello".to_string(),
            message_type: GroupMessageType::Bot,
            role: MessageRole::User,
            max_group_messages: 10,
            store_messages: true,
        })
        .await
        .unwrap();

    assert!(!outcome.message_id.is_empty());
    assert_eq!(
        outcome.routed_to,
        vec!["bot-driver".to_string(), "bot-observer".to_string()]
    );
    // The fake router resolves text mentions like the production legacy
    // `route()` does: "@Driver" resolves to the driver bot.
    assert_eq!(outcome.mentions, vec!["bot-driver".to_string()]);
    assert_eq!(
        support.routing.route_calls().await,
        vec![(
            "group-1".to_string(),
            "@Driver persistent hello".to_string(),
            Some("human_1".to_string())
        )]
    );
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::Send, BotDeliveryKind::Inject]
    );
    assert!(support.routing.send_calls().await.is_empty());
    assert_eq!(support.group.message_count("group-1").await.unwrap(), 2);
    let group = support.group.get("group-1").await.unwrap();
    assert_eq!(group.messages.len(), 1);
    assert_eq!(group.messages[0].id, outcome.message_id);
    assert_eq!(group.messages[0].sender, "human_1");
    assert_eq!(group.messages[0].content, "@Driver persistent hello");
}

#[tokio::test]
async fn persistent_group_send_resolves_running_session_and_persists_message_event() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let participants = support
        .group
        .get("group-1")
        .await
        .expect("group")
        .participants;
    let repo = Arc::new(RecordingMessageRepo::default());
    // F4: an absent-mode Human participant cannot send; this
    // exercise of the persist/event path runs as a Present one.
    let participants = participants
        .into_iter()
        .map(|mut participant| {
            if participant.bot_uuid == "human_1" {
                participant.mode = Some(ParticipantMode::Present);
            }
            participant
        })
        .collect();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone())
    .with_session_management(Arc::new(StaticSessionManagement::new(test_session(
        "session-event",
        "group-1",
        participants,
    ))))
    .with_event_record_factory(Arc::new(EnabledEventFactory));

    flow.handle_persistent_group_send(PersistentGroupSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        sender: "human_1".to_string(),
        content: "persist and publish".to_string(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::User,
        max_group_messages: 10,
        store_messages: true,
    })
    .await
    .expect("persistent send");

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].session_id, "session-event");

    let events = repo.events().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].event.event_type, "message.created");
    assert_eq!(
        events[0].event.scope.session_id.as_deref(),
        Some("session-event")
    );
    assert_eq!(events[0].event.stream_key, "session:session-event");
}

#[tokio::test]
async fn persistent_group_send_delivers_to_registered_provider_target_without_ws_connection() {
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
        .handle_persistent_group_send(PersistentGroupSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-provider".to_string(),
            sender: "human_1".to_string(),
            content: "hello provider".to_string(),
            message_type: GroupMessageType::Bot,
            role: MessageRole::User,
            max_group_messages: 10,
            store_messages: false,
        })
        .await
        .unwrap();

    assert_eq!(outcome.routed_to, vec!["bot-provider".to_string()]);
    assert!(support.routing.send_calls().await.is_empty());
    assert!(support.bot_delivery.targets().await[0].is_http_provider());
}

#[tokio::test]
async fn persistent_group_send_in_human_bot_dm_uses_dm_routing() {
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
        .handle_persistent_group_send(PersistentGroupSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            sender: "human_1".to_string(),
            content: "dm hello".to_string(),
            message_type: GroupMessageType::Bot,
            role: MessageRole::User,
            max_group_messages: 10,
            store_messages: true,
        })
        .await
        .unwrap();

    assert_eq!(outcome.routed_to, vec!["bot-driver".to_string()]);
    assert_eq!(
        support.routing.dm_route_calls().await,
        vec![(
            "group-1".to_string(),
            "dm hello".to_string(),
            "human_1".to_string()
        )]
    );
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::Send]
    );
    assert!(support.routing.send_calls().await.is_empty());
}

#[tokio::test]
async fn persistent_group_send_marks_group_inactive_when_legacy_message_cap_is_reached() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let error = flow
        .handle_persistent_group_send(PersistentGroupSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            sender: "human_1".to_string(),
            content: "over cap".to_string(),
            message_type: GroupMessageType::Bot,
            role: MessageRole::User,
            max_group_messages: 1,
            store_messages: true,
        })
        .await
        .unwrap_err();

    assert!(matches!(
        error,
        ServiceError::MessageLimitReached(ref message)
            if message == "Group 'group-1' already has 1 messages (max 1)"
    ));
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Inactive
    );
}
