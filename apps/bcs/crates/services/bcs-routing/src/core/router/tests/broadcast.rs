use super::*;

#[tokio::test]
async fn test_broadcast_to_all_on_no_mention() {
    let router = MessageRouter::new();
    let session = create_test_session();

    let decision = router
        .route(&session, "Hello, can you help me?", None)
        .await;

    assert!(decision.mentions.is_empty());
    assert_eq!(decision.cleaned_message, "Hello, can you help me?");
    assert_eq!(decision.targets.len(), 2);

    let driver_target = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "driver")
        .unwrap();
    let consultant_target = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "consultant")
        .unwrap();
    assert_eq!(driver_target.delivery_type, DeliveryType::Send);
    assert_eq!(consultant_target.delivery_type, DeliveryType::Inject);
}

#[tokio::test]
async fn test_mentioned_bot_gets_send_others_get_inject() {
    let router = MessageRouter::new();
    let session = create_test_session();

    let decision = router
        .route(&session, "@consultant what do you think?", None)
        .await;

    assert_eq!(decision.mentions, vec!["consultant"]);
    assert_eq!(decision.cleaned_message, "consultant what do you think?");
    assert_eq!(decision.targets.len(), 2);

    let driver_target = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "driver")
        .unwrap();
    let consultant_target = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "consultant")
        .unwrap();
    assert_eq!(driver_target.delivery_type, DeliveryType::Inject);
    assert_eq!(consultant_target.delivery_type, DeliveryType::Send);
}

#[tokio::test]
async fn test_all_mention_everyone_gets_send() {
    let router = MessageRouter::new();
    let session = create_test_session();

    let decision = router.route(&session, "@all please respond", None).await;

    assert_eq!(decision.targets.len(), 2);
    // Everyone should get Send
    for target in &decision.targets {
        assert_eq!(target.delivery_type, DeliveryType::Send);
    }
}

#[tokio::test]
async fn test_sender_is_excluded() {
    let router = MessageRouter::new();
    let session = create_test_session();

    // Sender is driver, should be excluded from targets
    let decision = router.route(&session, "Hello", Some("driver")).await;

    assert_eq!(decision.targets.len(), 1); // Only consultant
    assert_eq!(decision.targets[0].bot_uuid, "consultant");

    // No mentions, so consultant (non-driver) gets Inject
    assert_eq!(decision.targets[0].delivery_type, DeliveryType::Inject);
}

#[tokio::test]
async fn test_multiple_mentions() {
    let router = MessageRouter::new();
    let session = create_test_session();

    let decision = router
        .route(&session, "@driver @consultant please coordinate", None)
        .await;

    assert_eq!(decision.mentions.len(), 2);
    assert!(decision.mentions.contains(&"driver".to_string()));
    assert!(decision.mentions.contains(&"consultant".to_string()));
    assert_eq!(
        decision.cleaned_message,
        "driver consultant please coordinate"
    );
    assert_eq!(decision.targets.len(), 2);

    // Both mentioned, both get Send
    for target in &decision.targets {
        assert_eq!(target.delivery_type, DeliveryType::Send);
    }
}

#[tokio::test]
async fn test_repeated_full_display_name_mentions_all_get_send() {
    let router = MessageRouter::new();
    let mut session = create_overlay_test_session();
    session.participants[0].bot_name = Some("alpha-甲".to_string());
    session.participants[1].bot_name = Some("beta (乙)".to_string());
    session.participants[2].bot_name = Some("beta".to_string());
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let decision = router
        .route_with_overlay(
            &session,
            "@alpha-甲 @beta (乙) coordinate; @alpha-甲 and @beta (乙) confirm",
            Some("sender"),
            &overlay,
        )
        .await;

    assert_eq!(
        decision.mentions,
        vec!["driver".to_string(), "bot_x".to_string()]
    );
    assert_eq!(
        decision.cleaned_message,
        "alpha-甲 beta (乙) coordinate; alpha-甲 and beta (乙) confirm"
    );
    assert_eq!(decision.targets.len(), 2);
    for target in &decision.targets {
        assert_eq!(target.delivery_type, DeliveryType::Send);
    }
}

#[tokio::test]
async fn test_chinese_display_name_allows_adjacent_chinese_message_text() {
    let router = MessageRouter::new();
    let mut session = create_test_session();
    session.participants[1].bot_name = Some("圈人部署".to_string());

    let decision = router.route(&session, "@圈人部署通知已输出", None).await;

    assert_eq!(decision.mentions, vec!["consultant".to_string()]);
    let consultant_target = decision
        .targets
        .iter()
        .find(|target| target.bot_uuid == "consultant")
        .unwrap();
    assert_eq!(consultant_target.delivery_type, DeliveryType::Send);
}

#[tokio::test]
async fn test_overlay_route_allows_adjacent_chinese_message_text() {
    let router = MessageRouter::new();
    let mut session = create_overlay_test_session();
    session.participants[1].bot_name = Some("圈人部署".to_string());
    let overlay = vec![
        ov_bot("driver", None, ActorStatus::Online),
        ov_bot("bot_x", None, ActorStatus::Online),
        ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
        ov_bot("sender", None, ActorStatus::Online),
    ];

    let decision = router
        .route_with_overlay(
            &session,
            "@圈人部署通知已输出",
            Some("sender"),
            &overlay,
        )
        .await;

    assert_eq!(decision.mentions, vec!["bot_x".to_string()]);
    let target = decision
        .targets
        .iter()
        .find(|target| target.bot_uuid == "bot_x")
        .unwrap();
    assert_eq!(target.delivery_type, DeliveryType::Send);
}

#[tokio::test]
async fn test_adjacent_chinese_mention_prefers_longest_display_name() {
    let router = MessageRouter::new();
    let mut session = create_test_session();
    session.participants[0].bot_name = Some("圈人部署".to_string());
    session.participants[1].bot_name = Some("圈人部署通知".to_string());

    let decision = router.route(&session, "@圈人部署通知已输出", None).await;

    assert_eq!(decision.mentions, vec!["consultant".to_string()]);
}

#[tokio::test]
async fn test_ascii_display_name_allows_adjacent_chinese_message_text() {
    let router = MessageRouter::new();
    let mut session = create_test_session();
    session.participants[1].bot_name = Some("xxx".to_string());

    let decision = router.route(&session, "@xxx你好", None).await;

    assert_eq!(decision.mentions, vec!["consultant".to_string()]);
    let consultant_target = decision
        .targets
        .iter()
        .find(|target| target.bot_uuid == "consultant")
        .unwrap();
    assert_eq!(consultant_target.delivery_type, DeliveryType::Send);
}

#[tokio::test]
async fn test_ascii_display_name_rejects_adjacent_ascii_text() {
    let router = MessageRouter::new();
    let mut session = create_test_session();
    session.participants[1].bot_name = Some("dev".to_string());

    let decision = router.route(&session, "@developer please respond", None).await;

    assert!(decision.mentions.is_empty());
}

#[tokio::test]
async fn test_invalid_mention_ignored() {
    let router = MessageRouter::new();
    let session = create_test_session();

    let decision = router.route(&session, "@unknown hello", None).await;

    assert!(decision.mentions.is_empty()); // Invalid mention ignored
    assert_eq!(decision.cleaned_message, "unknown hello");
    assert_eq!(decision.targets.len(), 2);

    // No valid mentions, driver gets Send, consultant gets Inject
    let driver_target = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "driver")
        .unwrap();
    let consultant_target = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "consultant")
        .unwrap();
    assert_eq!(driver_target.delivery_type, DeliveryType::Send);
    assert_eq!(consultant_target.delivery_type, DeliveryType::Inject);
}

#[tokio::test]
async fn test_send_to_bot_returns_deprecation_error() {
    let router = MessageRouter::new();
    let session = create_test_session();

    let decision = router.route(&session, "Hello", None).await;
    let result = router
        .send_to_bot(&decision.targets[0], "test", None, Some("test-session"))
        .await;

    assert!(!result.success);
    assert!(result.error.unwrap().contains("deprecated"));
}

#[tokio::test]
async fn test_mid_message_mention_recognized() {
    let router = MessageRouter::new();
    let session = create_test_session();

    // @mention in the middle — should now be recognized as a routing directive
    let decision = router
        .route(&session, "请在你的回复中 @consultant 并请他介绍自己", None)
        .await;

    assert_eq!(decision.mentions, vec!["consultant"]);
    assert_eq!(
        decision.cleaned_message,
        "请在你的回复中 consultant 并请他介绍自己"
    );
    let driver_target = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "driver")
        .unwrap();
    let consultant_target = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "consultant")
        .unwrap();
    assert_eq!(driver_target.delivery_type, DeliveryType::Inject);
    assert_eq!(consultant_target.delivery_type, DeliveryType::Send);
}

#[tokio::test]
async fn test_leading_mention_routes_correctly() {
    let router = MessageRouter::new();
    let session = create_test_session();

    let decision = router.route(&session, "@consultant 请介绍自己", None).await;

    assert_eq!(decision.mentions, vec!["consultant"]);
    assert_eq!(decision.cleaned_message, "consultant 请介绍自己");
    let consultant_target = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "consultant")
        .unwrap();
    assert_eq!(consultant_target.delivery_type, DeliveryType::Send);
}

#[tokio::test]
async fn test_scattered_mentions_in_text() {
    let router = MessageRouter::new();
    let session = create_test_session();

    let decision = router
        .route(&session, "请 @driver 和 @consultant 一起讨论", None)
        .await;

    assert_eq!(decision.mentions.len(), 2);
    assert!(decision.mentions.contains(&"driver".to_string()));
    assert!(decision.mentions.contains(&"consultant".to_string()));
    assert_eq!(decision.cleaned_message, "请 driver 和 consultant 一起讨论");

    for target in &decision.targets {
        assert_eq!(target.delivery_type, DeliveryType::Send);
    }
}

#[tokio::test]
async fn test_chinese_name_mentions() {
    let router = MessageRouter::new();
    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: bcs_service_api::GroupStatus::Active,
        driver_bot: "pmo".to_string(),
        originator: None,
        routing_policy: None,
        human_mention_notify_mode: HumanMentionNotifyMode::default(),
        context: None,
        opening_message: None,
        participants: vec![
            Participant {
                bot_uuid: "pmo".to_string(),
                bot_name: Some("PMO".to_string()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "xiongbing".to_string(),
                bot_name: Some("Developer Bot".to_string()),
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
            Participant {
                bot_uuid: "xiahong".to_string(),
                bot_name: Some("QA Bot".to_string()),
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                tags: Vec::new(),
                message_view_scope: bcs_service_api::application::v1::MessageViewScope::Full,
                mode: None,
            },
        ],
        messages: Vec::new(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    };

    let decision = router
        .route(
            &session,
            "请 @Developer Bot 进行技术评估，@QA Bot 进行质量评估。",
            Some("pmo"),
        )
        .await;

    assert_eq!(decision.mentions.len(), 2);
    assert!(decision.mentions.contains(&"xiongbing".to_string()));
    assert!(decision.mentions.contains(&"xiahong".to_string()));
    assert_eq!(
        decision.cleaned_message,
        "请 Developer Bot 进行技术评估，QA Bot 进行质量评估。"
    );

    // Both mentioned bots get Send
    let xb = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "xiongbing")
        .unwrap();
    let xh = decision
        .targets
        .iter()
        .find(|t| t.bot_uuid == "xiahong")
        .unwrap();
    assert_eq!(xb.delivery_type, DeliveryType::Send);
    assert_eq!(xh.delivery_type, DeliveryType::Send);
}
