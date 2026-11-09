use super::{
    Arc,
    BcsMessageFlow,
    BlockingInterceptor,
    BotEventCommand,
    ChatEventState,
    GroupCoreService,
    MessageFlowService,
    ParticipantMode,
    RoutingMode,
    RoutingPolicy,
    json,
    support
};

async fn group_with_present_human(support: &support::FlowTestSupport) {
    let mut group = support.group.get("group-1").await.unwrap();
    for participant in &mut group.participants {
        if participant.bot_uuid == "human_1" {
            participant.mode = Some(ParticipantMode::Present);
        }
    }
    support.group.upsert(group).await.unwrap();
}

fn bot_event_with_text(message_text: &str) -> BotEventCommand {
    BotEventCommand {
        bot_id: "bot-driver".to_string(),
        run_id: "run-1".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1",
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": message_text}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: None,
    }
}

#[tokio::test]
async fn bot_event_legacy_mention_notifies_mentioned_human() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    group_with_present_human(&support).await;
    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    flow.handle_bot_event(bot_event_with_text("@Human One 请确认")).await.unwrap();

    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1, "bot text @human must notify");
    assert_eq!(notifications[0].mentioned[0].actor_id, "human_1");
    assert_eq!(notifications[0].sender_actor_id, "bot-driver");
}

#[tokio::test]
async fn bot_event_structured_routing_does_not_notify() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    group_with_present_human(&support).await;
    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        ..Default::default()
    });
    support.group.upsert(group).await.unwrap();

    flow.handle_bot_event(bot_event_with_text("@Human One 请确认")).await.unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(
        notifications.is_empty(),
        "non-mention routing paths must not notify"
    );
}

#[tokio::test]
async fn bot_event_sender_routes_do_not_notify() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    group_with_present_human(&support).await;
    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        sender_routes: std::collections::HashMap::from([(
            "bot-driver".to_string(),
            vec!["bot-observer".to_string()],
        )]),
        ..Default::default()
    });
    support.group.upsert(group).await.unwrap();

    flow.handle_bot_event(bot_event_with_text("@Human One 请确认")).await.unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(
        notifications.is_empty(),
        "sender-routes path does not parse text mentions and must not notify"
    );
}

#[tokio::test]
async fn bot_event_structured_compat_mentions_do_not_notify() {
    // Structured routing copies the resolved *responder selection* into
    // `decision.mentions` for backward compatibility; that field may name
    // humans without any text @-mention and must not trigger notifications.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    group_with_present_human(&support).await;
    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        ..Default::default()
    });
    support.group.upsert(group).await.unwrap();

    let mut cmd = bot_event_with_text("请确认");
    cmd.event_payload["routing"] = json!({
        "responders": [{"type": "bot", "value": "human_1"}],
        "reason": "contract test: compat mention names a human",
    });
    flow.handle_bot_event(cmd).await.unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(
        notifications.is_empty(),
        "structured routing compat mentions must not notify"
    );
}

#[tokio::test]
async fn bot_event_policy_blocked_message_does_not_notify() {
    // The notification must go through the same outbound policy as bot
    // deliveries: when the chain blocks the content, humans must not receive
    // it out-of-band either.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    group_with_present_human(&support).await;
    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_interceptor(BlockingInterceptor)
    .with_human_mention_notify(recorder.clone());

    flow.handle_bot_event(bot_event_with_text("@Human One 请确认")).await.unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(
        notifications.is_empty(),
        "policy-blocked content must not reach humans via notifications"
    );
}
