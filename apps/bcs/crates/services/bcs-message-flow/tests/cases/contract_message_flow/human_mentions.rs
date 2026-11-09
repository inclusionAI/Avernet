use super::*;

fn web_send_human_mentions(mention_actor_ids: Vec<String>) -> WebSendCommand {
    WebSendCommand {
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
        message: "hello".to_string(),
        mentions: mention_actor_ids,
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    }
}

async fn set_human_1_present(support: &support::FlowTestSupport) {
    let mut group = support.group.get("group-1").await.unwrap();
    let human = group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "human_1")
        .unwrap();
    human.mode = Some(ParticipantMode::Present);
    support.group.upsert(group).await.unwrap();
}

async fn add_present_human_2(support: &support::FlowTestSupport) {
    let mut group = support.group.get("group-1").await.unwrap();
    group.participants.push(Participant {
        bot_uuid: "human_2".to_string(),
        bot_name: Some("Human Two".to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: ActorKind::Human,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    });
    support.group.upsert(group).await.unwrap();
}

#[tokio::test]
async fn web_send_notifies_explicitly_mentioned_human() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.participants.push(Participant {
        bot_uuid: "human_2".to_string(),
        bot_name: Some("Human Two".to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: ActorKind::Human,
        mode: None,
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    });
    support.group.upsert(group).await.unwrap();

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    flow.handle_web_send(web_send_human_mentions(vec!["human_2".to_string()]))
        .await
        .unwrap();

    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1, "mentioned human must be notified");
    assert_eq!(notifications[0].mentioned.len(), 1);
    assert_eq!(notifications[0].mentioned[0].actor_id, "human_2");
    assert_eq!(notifications[0].mentioned[0].display_name, "Human Two");
    assert_eq!(notifications[0].sender_actor_id, "human_1");
    assert_eq!(notifications[0].group_id, "group-1");
    assert_eq!(notifications[0].session_id, "group-1:s1");
}

#[tokio::test]
async fn web_send_skips_notification_for_self_mention() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    flow.handle_web_send(web_send_human_mentions(vec!["human_1".to_string()]))
        .await
        .unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(notifications.is_empty(), "self mention must not notify");
}

#[tokio::test]
async fn web_send_skips_notification_for_bot_only_mentions() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    flow.handle_web_send(web_send_human_mentions(vec!["bot-driver".to_string()]))
        .await
        .unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(notifications.is_empty(), "bot-only mentions must not notify");
}

#[tokio::test]
async fn web_send_skips_notification_when_port_unavailable() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.participants.push(Participant {
        bot_uuid: "human_2".to_string(),
        bot_name: Some("Human Two".to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: ActorKind::Human,
        mode: None,
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    });
    support.group.upsert(group).await.unwrap();

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(false));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    flow.handle_web_send(web_send_human_mentions(vec!["human_2".to_string()]))
        .await
        .unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(
        notifications.is_empty(),
        "unavailable port must block a would-be notification"
    );
}

#[tokio::test]
async fn web_send_text_mention_notifies_resolved_human() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    add_present_human_2(&support).await;

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    let mut cmd = web_send_human_mentions(Vec::new());
    cmd.message = "@Human Two 你怎么看".to_string();
    flow.handle_web_send(cmd).await.unwrap();

    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1, "text-mentioned human must be notified");
    assert_eq!(notifications[0].mentioned.len(), 1);
    assert_eq!(notifications[0].mentioned[0].actor_id, "human_2");
    assert_eq!(notifications[0].mentioned[0].display_name, "Human Two");
}

#[tokio::test]
async fn web_send_at_all_alone_does_not_notify() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    set_human_1_present(&support).await;

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    let mut cmd = web_send_human_mentions(Vec::new());
    cmd.message = "@所有人 通知一下".to_string();
    flow.handle_web_send(cmd).await.unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(notifications.is_empty(), "@all alone must not notify humans");
}

#[tokio::test]
async fn web_send_at_all_with_explicit_human_notifies_that_human() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    add_present_human_2(&support).await;

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    let mut cmd = web_send_human_mentions(Vec::new());
    cmd.message = "@所有人 @Human Two 请回复".to_string();
    flow.handle_web_send(cmd).await.unwrap();

    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1, "explicit human next to @all must be notified");
    assert_eq!(notifications[0].mentioned.len(), 1);
    assert_eq!(notifications[0].mentioned[0].actor_id, "human_2");
}

#[tokio::test]
async fn group_callback_never_triggers_human_notification() {
    // Security boundary: the callback route carries no caller identity, so its
    // request-controlled `mentions` must never reach the notification backend
    // (an unauthenticated caller could otherwise DM arbitrary participants).
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.participants.push(Participant {
        bot_uuid: "human_2".to_string(),
        bot_name: Some("Human Two".to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: ActorKind::Human,
        mode: None,
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    });
    support.group.upsert(group).await.unwrap();

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    flow.handle_group_callback(GroupCallbackCommand {
        group_id: "group-1".to_string(),
        message: "system callback".to_string(),
        mentions: vec!["human_2".to_string()],
        metadata: Some(json!({"source": "contract"})),
        store_message: false,
    })
    .await
    .unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(
        notifications.is_empty(),
        "unauthenticated callbacks must not trigger human notifications"
    );
}

#[tokio::test]
async fn web_send_dm_group_does_not_notify_mentioned_human() {
    // DM groups carry no @-mention semantics, so even an explicit mention
    // must not notify.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    make_human_bot_dm(&support).await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.participants.push(Participant {
        bot_uuid: "human_2".to_string(),
        bot_name: Some("Human Two".to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: ActorKind::Human,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    });
    support.group.upsert(group).await.unwrap();

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    flow.handle_web_send(web_send_human_mentions(vec!["human_2".to_string()]))
        .await
        .unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(
        notifications.is_empty(),
        "DM groups must not trigger human notifications"
    );
}

#[tokio::test]
async fn web_send_policy_blocked_message_does_not_notify_humans() {
    // The notification must go through the same outbound policy as bot
    // deliveries: when the chain blocks the content, humans must not receive
    // it out-of-band either.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    add_present_human_2(&support).await;

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

    flow.handle_web_send(web_send_human_mentions(vec!["human_2".to_string()]))
        .await
        .unwrap();

    let notifications = recorder.wait_for_none().await;
    assert!(
        notifications.is_empty(),
        "policy-blocked content must not reach humans via notifications"
    );
}

#[tokio::test]
async fn persistent_group_send_text_mention_notifies_human() {
    // `POST /groups/{id}/messages` resolves text @-mentions through the same
    // router and must notify mentioned humans like the workbench send path.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    add_present_human_2(&support).await;

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    flow.handle_persistent_group_send(PersistentGroupSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        sender: "human_1".to_string(),
        content: "@Human Two 请查收".to_string(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::User,
        max_group_messages: 10,
        store_messages: true,
    })
    .await
    .unwrap();

    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1, "persistent send must notify");
    assert_eq!(notifications[0].mentioned.len(), 1);
    assert_eq!(notifications[0].mentioned[0].actor_id, "human_2");
    assert_eq!(notifications[0].mentioned[0].display_name, "Human Two");
    assert_eq!(notifications[0].sender_actor_id, "human_1");
    assert_eq!(notifications[0].message_text, "Human Two 请查收");
}
