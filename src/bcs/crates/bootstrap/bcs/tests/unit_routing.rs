//! Integration tests for BCS WebSocket protocol.
//!
//! Tests for the full flow from frontend to bot and back.

use bcs_service_api::{
    Group, GroupStrategy, Participant, ParticipantRole, Workspace,
    RoutingCoreService, GroupStatus, DeliveryType,
};
use bcs_routing::MessageRouter;
use bcs_protocol::GroupContext;

/// Test that no @mention broadcasts to ALL participants (WhatsApp/WeChat mental model)
#[tokio::test]
async fn test_broadcast_to_all_on_no_mention() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "expert".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // No @mention should broadcast to ALL participants
    let decision = router.route(&session, "Hello, can you help me?", None).await;

    assert!(decision.mentions.is_empty()); // No mentions extracted
    assert_eq!(decision.targets.len(), 3); // All three participants

    let bot_ids: Vec<_> = decision.targets.iter().map(|t| t.bot_uuid.as_str()).collect();
    assert!(bot_ids.contains(&"driver"));
    assert!(bot_ids.contains(&"consultant"));
    assert!(bot_ids.contains(&"expert"));
}

/// Test that @mention STILL broadcasts to ALL participants, but extracts mentions
#[tokio::test]
async fn test_mention_broadcasts_to_all_with_mentions_extracted() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // @mention should STILL broadcast to ALL participants (broadcast model)
    // but mentions are extracted for context injection
    let decision = router.route(&session, "@consultant please analyze this", None).await;

    assert_eq!(decision.mentions, vec!["consultant"]); // Mention extracted
    assert_eq!(decision.targets.len(), 2); // ALL participants receive the message

    let bot_ids: Vec<_> = decision.targets.iter().map(|t| t.bot_uuid.as_str()).collect();
    assert!(bot_ids.contains(&"driver"));
    assert!(bot_ids.contains(&"consultant"));
}

/// Test that multiple @mentions broadcast to ALL participants, with all mentions extracted
#[tokio::test]
async fn test_multiple_mentions_broadcast_to_all() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "dba".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "security".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // Multiple @mentions should STILL broadcast to ALL participants
    let decision = router.route(&session, "@dba @security please coordinate", None).await;

    assert_eq!(decision.mentions.len(), 2); // Both mentions extracted
    assert!(decision.mentions.contains(&"dba".to_string()));
    assert!(decision.mentions.contains(&"security".to_string()));
    assert_eq!(decision.targets.len(), 3); // ALL participants

    let bot_ids: Vec<_> = decision.targets.iter().map(|t| t.bot_uuid.as_str()).collect();
    assert!(bot_ids.contains(&"driver"));
    assert!(bot_ids.contains(&"dba"));
    assert!(bot_ids.contains(&"security"));
}

/// Test that invalid @mention is ignored and message broadcasts to all
#[tokio::test]
async fn test_invalid_mention_ignored_broadcasts_to_all() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // @unknown is not a valid participant, so no valid @mention
    // Should broadcast to ALL participants
    let decision = router.route(&session, "@unknown hello", None).await;

    assert!(decision.mentions.is_empty()); // Invalid mention not extracted
    assert_eq!(decision.targets.len(), 2); // Broadcast to both

    let bot_ids: Vec<_> = decision.targets.iter().map(|t| t.bot_uuid.as_str()).collect();
    assert!(bot_ids.contains(&"driver"));
    assert!(bot_ids.contains(&"consultant"));
}

/// Test GroupContext has new fields for bot response decision
#[test]
fn test_group_context_has_response_control_fields() {
    let context = GroupContext {
        session_id: "test-session".to_string(),
        participants: vec!["driver".to_string(), "dba".to_string()],
        originator: "driver".to_string(),
        from: "user".to_string(),
        you_are_mentioned: true,
        is_sender: false,
        mentions: vec!["dba".to_string()],
        message: "Hello @dba".to_string(),
        response_directive: None,
        recipient: None,
        recipient_name: None,
        recipient_role: None,
        delivery_type: None,
        routing_mode: None,
        group_type: None,
        from_bot_id: None,
        from_bot_owner: None,
    };

    // Verify new fields are present
    assert_eq!(context.session_id, "test-session");
    assert_eq!(context.originator, "driver");
    assert_eq!(context.from, "user");
    assert!(context.you_are_mentioned);
    assert!(!context.is_sender);
    assert_eq!(context.mentions, vec!["dba"]);
    assert_eq!(context.message, "Hello @dba");
}

/// Test Group originator defaults to driver_bot
#[test]
fn test_group_session_originator_defaults_to_driver() {
    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: None, // Not set
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // originator() should return driver_bot when not set
    assert_eq!(session.originator(), "driver");
}

/// Test Group originator can be set explicitly
#[test]
fn test_group_session_originator_can_be_set() {
    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("initiator".to_string()), // Explicitly set
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "initiator".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // originator() should return the explicitly set value
    assert_eq!(session.originator(), "initiator");
}

// ============================================================================
// Additional tests for G1-G5 scenarios from BCS.md
// ============================================================================

/// Test G1: Agent mode - @mention routing to specific bot
/// In Agent mode, @mentioned bot receives the message and must respond
#[tokio::test]
async fn test_g1_agent_mode_mention_routes_to_consultant() {
    let router = MessageRouter::new();

    let session = Group {
        id: "g1-session".to_string(),
        label: Some("数据库死锁排查".to_string()),
        status: GroupStatus::Active,
        driver_bot: "zhangsan".to_string(),
        originator: Some("zhangsan".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "zhangsan".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "dba".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // With @mention, message is STILL broadcast to all
    // but the mention is extracted for context injection
    let decision = router.route(&session, "@dba 请分析死锁根因", None).await;

    assert_eq!(decision.mentions, vec!["dba"]);
    assert_eq!(decision.targets.len(), 2); // Broadcast to ALL participants
}

/// Test G2: Fusion mode - all participants receive context
#[tokio::test]
async fn test_g2_fusion_mode_broadcast_to_all() {
    let router = MessageRouter::new();

    let session = Group {
        id: "g2-session".to_string(),
        label: Some("代码与PRD冲突协调".to_string()),
        status: GroupStatus::Active,
        driver_bot: "zhangsan".to_string(),
        originator: Some("zhangsan".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "zhangsan".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "lisi".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "security".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // In Fusion mode, broadcast to all
    let decision = router.route(&session, "这个方案可行吗？", None).await;

    assert!(decision.mentions.is_empty());
    assert_eq!(decision.targets.len(), 3);
}

/// Test G4: Dynamic member management - adding members
#[tokio::test]
async fn test_g4_dynamic_member_addition() {
    let router = MessageRouter::new();

    let mut session = Group {
        id: "g4-session".to_string(),
        label: Some("项目运行群".to_string()),
        status: GroupStatus::Active,
        driver_bot: "pm".to_string(),
        originator: Some("pm".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "pm".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // Initially only PM
    let decision = router.route(&session, "Status update", None).await;
    assert_eq!(decision.targets.len(), 1);

    // Add a new member (simulating G4 dynamic member management)
    session.participants.push(Participant {
        bot_uuid: "dev".to_string(),
        bot_name: None,
        kind: None,
        role: ParticipantRole::Consultant,
            actor_kind: bcs_service_api::ActorKind::default(),
        mode: None,
});

    // Now message should route to both
    let decision = router.route(&session, "@dev please update", None).await;
    assert_eq!(decision.targets.len(), 2);
    assert_eq!(decision.mentions, vec!["dev"]);
}

/// Test G5: Expert consultation - multiple @mentions
#[tokio::test]
async fn test_g5_expert_consultation_multiple_mentions() {
    let router = MessageRouter::new();

    let session = Group {
        id: "g5-session".to_string(),
        label: Some("专家会诊群".to_string()),
        status: GroupStatus::Active,
        driver_bot: "zhangsan".to_string(),
        originator: Some("zhangsan".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "zhangsan".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "security".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "legal".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "dba".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // Multiple experts mentioned, all should receive
    let decision = router.route(&session, "@security @legal @dba 需要各方意见", None).await;

    assert_eq!(decision.mentions.len(), 3);
    assert!(decision.mentions.contains(&"security".to_string()));
    assert!(decision.mentions.contains(&"legal".to_string()));
    assert!(decision.mentions.contains(&"dba".to_string()));
    assert_eq!(decision.targets.len(), 4); // All participants (broadcast model)
}

/// Test GroupContext for G1 scenario's response decision
#[test]
fn test_group_context_g1_scenario() {
    // Simulate DBA receiving message from ZhangSan in G1 session
    let ctx_dba = GroupContext {
        session_id: "g1-session".to_string(),
        participants: vec!["zhangsan".to_string(), "dba".to_string()],
        originator: "zhangsan".to_string(),
        from: "zhangsan".to_string(),
        you_are_mentioned: true,
        is_sender: false,
        mentions: vec!["dba".to_string()],
        message: "@dba 请分析死锁根因".to_string(),
        response_directive: None,
        recipient: None,
        recipient_name: None,
        recipient_role: None,
        delivery_type: None,
        routing_mode: None,
        group_type: None,
        from_bot_id: None,
        from_bot_owner: None,
    };

    // DBA should respond (mentioned = true, not sender)
    assert!(ctx_dba.you_are_mentioned);
    assert!(!ctx_dba.is_sender);
    // DBA should respond because it's mentioned

    // Simulate ZhangSan receiving his own message (echo)
    let ctx_driver = GroupContext {
        session_id: "g1-session".to_string(),
        participants: vec!["zhangsan".to_string(), "dba".to_string()],
        originator: "zhangsan".to_string(),
        from: "zhangsan".to_string(),
        you_are_mentioned: false,
        is_sender: true,
        mentions: vec!["dba".to_string()],
        message: "@dba 请分析死锁根因".to_string(),
        response_directive: None,
        recipient: None,
        recipient_name: None,
        recipient_role: None,
        delivery_type: None,
        routing_mode: None,
        group_type: None,
        from_bot_id: None,
        from_bot_owner: None,
    };

    // ZhangSan should NOT respond to his own message
    assert!(ctx_driver.is_sender);
    assert!(!ctx_driver.you_are_mentioned);
}

/// Test BCS.md P4: Initiator priority protocol
/// For broadcast messages (no @mention), originator should respond
#[test]
fn test_group_context_initiator_protocol() {
    // User sends broadcast message "进度怎么样", originator (张三) should respond
    let ctx_zhangsan = GroupContext {
        session_id: "g1-session".to_string(),
        participants: vec!["zhangsan".to_string(), "dba".to_string()],
        originator: "zhangsan".to_string(),
        from: "user".to_string(),
        you_are_mentioned: false,
        is_sender: false,
        mentions: vec![],
        message: "进度怎么样了？".to_string(),
        response_directive: None,
        recipient: None,
        recipient_name: None,
        recipient_role: None,
        delivery_type: None,
        routing_mode: None,
        group_type: None,
        from_bot_id: None,
        from_bot_owner: None,
    };

    // ZhangSan is originator and not sender, so should respond
    assert_eq!(ctx_zhangsan.originator, "zhangsan");
    assert!(!ctx_zhangsan.is_sender);
    assert!(!ctx_zhangsan.you_are_mentioned);
    // By P4, originator should respond to broadcast messages

    // DBA receives same broadcast
    let ctx_dba = GroupContext {
        session_id: "g1-session".to_string(),
        participants: vec!["zhangsan".to_string(), "dba".to_string()],
        originator: "zhangsan".to_string(),
        from: "user".to_string(),
        you_are_mentioned: false,
        is_sender: false,
        mentions: vec![],
        message: "进度怎么样了？".to_string(),
        response_directive: None,
        recipient: None,
        recipient_name: None,
        recipient_role: None,
        delivery_type: None,
        routing_mode: None,
        group_type: None,
        from_bot_id: None,
        from_bot_owner: None,
    };

    // DBA is not originator, not mentioned, not sender - should keep silent
    assert_ne!(ctx_dba.originator, "dba");
    assert!(!ctx_dba.you_are_mentioned);
    assert!(!ctx_dba.is_sender);
    // By P4, DBA should keep silent (not originator, not mentioned)
}

/// Test message with all participants mentioned (equivalent to @所有人)
#[tokio::test]
async fn test_all_participants_mentioned() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // @driver @consultant is equivalent to @所有人
    let decision = router.route(&session, "@driver @consultant please all respond", None).await;

    assert_eq!(decision.mentions.len(), 2);
    // Still broadcasts to all (broadcast model)
    assert_eq!(decision.targets.len(), 2);
}

/// Test RoutingTarget has is_driver flag
#[tokio::test]
async fn test_routing_target_is_driver_flag() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    let decision = router.route(&session, "Hello", None).await;

    // Check is_driver flag
    for target in &decision.targets {
        assert_eq!(target.is_driver, target.bot_uuid == "driver");
    }
}

/// Test Complex message with mixed content
    /// Note: @mentions anywhere in the message are recognized as routing directives.
    #[tokio::test]
    async fn test_complex_message_with_code_block() {
        let router = MessageRouter::new();

        let session = Group {
            id: "test-session".to_string(),
            label: None,
            status: GroupStatus::Active,
            driver_bot: "driver".to_string(),
            originator: Some("driver".to_string()),
            routing_policy: None,
            participants: vec![
                Participant {
                    bot_uuid: "driver".to_string(),
                    bot_name: None,
                    kind: None,
                    role: ParticipantRole::Driver,
                                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
},
                Participant {
                    bot_uuid: "dba".to_string(),
                    bot_name: None,
                    kind: None,
                    role: ParticipantRole::Consultant,
                                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
},
            ],
            messages: Vec::new(),
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            context: None,
                    group_kind: bcs_service_api::GroupKind::default(),
            dm_pair_key: None,
            group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

        // Message with code block and mention in the middle
        // @dba is recognized as a routing directive (mentions anywhere count)
        let message = r#"请帮我分析这段SQL：
```sql
SELECT * FROM orders WHERE id = 1 FOR UPDATE;
```
@dba 这里的锁会不会有问题？"#;

        let decision = router.route(&session, message, None).await;

        // @dba is recognized as a mention
        assert_eq!(decision.mentions, vec!["dba"]);

        // DBA gets Send (mentioned), Driver gets Inject (not mentioned)
        assert_eq!(decision.targets.len(), 2);
        for target in &decision.targets {
            if target.bot_uuid == "driver" {
                assert_eq!(target.delivery_type, DeliveryType::Inject);
            } else if target.bot_uuid == "dba" {
                assert_eq!(target.delivery_type, DeliveryType::Send);
            }
        }
    }

// ============================================================================
// Tests for DeliveryType (chat.send vs chat.inject differentiation)
// ============================================================================

/// Test P4: No @mention → originator gets DeliveryType::Send, others get Inject
#[tokio::test]
async fn test_no_mention_originator_gets_send_others_inject() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "expert".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // No @mention: originator (driver) should get Send, others get Inject
    let decision = router.route(&session, "Hello, can you help me?", None).await;

    assert!(decision.mentions.is_empty());

    for target in &decision.targets {
        if target.bot_uuid == "driver" {
            assert_eq!(target.delivery_type, DeliveryType::Send, "Originator should get Send");
        } else {
            assert_eq!(target.delivery_type, DeliveryType::Inject, "Non-originator should get Inject");
        }
    }
}

/// Test @mention: mentioned bot gets Send, others get Inject
#[tokio::test]
async fn test_mention_mentioned_gets_send_others_inject() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "dba".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "security".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // @dba mentioned: dba gets Send, others get Inject
    let decision = router.route(&session, "@dba 请分析数据库问题", None).await;

    assert_eq!(decision.mentions, vec!["dba"]);

    for target in &decision.targets {
        if target.bot_uuid == "dba" {
            assert_eq!(target.delivery_type, DeliveryType::Send, "Mentioned bot should get Send");
        } else {
            assert_eq!(target.delivery_type, DeliveryType::Inject, "Non-mentioned should get Inject");
        }
    }
}

/// Test @ALL mention: everyone gets Send
#[tokio::test]
async fn test_all_mention_everyone_gets_send() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // @driver @consultant = @ALL: everyone should get Send
    let decision = router.route(&session, "@driver @consultant 所有人请回应", None).await;

    // All participants mentioned = everyone gets Send
    for target in &decision.targets {
        assert_eq!(target.delivery_type, DeliveryType::Send, "When all mentioned, everyone should get Send");
    }
}

/// Test sender exclusion: sender should NOT receive their own message
#[tokio::test]
async fn test_sender_excluded_from_delivery() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // Message from driver: driver should NOT be in targets
    let decision = router.route(&session, "Hello, I need help", Some("driver")).await;

    let bot_ids: Vec<_> = decision.targets.iter().map(|t| t.bot_uuid.as_str()).collect();

    // Sender should be excluded
    assert!(!bot_ids.contains(&"driver"), "Sender should be excluded from delivery");
    // Only consultant should receive
    assert!(bot_ids.contains(&"consultant"), "Non-sender should receive");
    // Consultant gets Send because it's a broadcast message (no @mention) and consultant is the only non-sender
    // Actually, according to P4, originator (driver) should respond, but driver is sender, so consultant gets Inject
    for target in &decision.targets {
        if target.bot_uuid == "consultant" {
            // Since originator (driver) is the sender, there's no one to Send, so consultant gets Inject
            assert_eq!(target.delivery_type, DeliveryType::Inject);
        }
    }
}

/// Test multiple @mentions: all mentioned get Send, others get Inject
#[tokio::test]
async fn test_multiple_mentions_delivery_type() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "dba".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "security".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "legal".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // @dba @security mentioned: they get Send, driver and legal get Inject
    let decision = router.route(&session, "@dba @security 请分析这个方案", None).await;

    assert_eq!(decision.mentions.len(), 2);

    for target in &decision.targets {
        if target.bot_uuid == "dba" || target.bot_uuid == "security" {
            assert_eq!(target.delivery_type, DeliveryType::Send, "Mentioned bot should get Send");
        } else {
            assert_eq!(target.delivery_type, DeliveryType::Inject, "Non-mentioned should get Inject");
        }
    }
}

// ============================================================================
// Tests for Real Person Participation (Phase 5)
// ============================================================================

/// Test real person (non-bot) sending message: all bots receive the message
/// When sender_bot_id is not a participant, everyone gets the message.
#[tokio::test]
async fn test_real_person_sends_message_all_bots_receive() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "zhangsan".to_string(),
        originator: Some("zhangsan".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "zhangsan".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "dba".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // Real person "user_zhangsan" sends a message (not a bot_id in participants)
    let decision = router.route(&session, "进度怎么样了？", Some("user_zhangsan")).await;

    // All bots should receive the message (sender is not a participant bot)
    assert_eq!(decision.targets.len(), 2, "All bots should receive real person message");

    let bot_ids: Vec<_> = decision.targets.iter().map(|t| t.bot_uuid.as_str()).collect();
    assert!(bot_ids.contains(&"zhangsan"), "Real person's bot should receive the message");
    assert!(bot_ids.contains(&"dba"), "Other bots should receive the message");
}

/// Test real person with @mention: mentioned bot gets Send, others get Inject
#[tokio::test]
async fn test_real_person_sends_with_mention() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "zhangsan".to_string(),
        originator: Some("zhangsan".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "zhangsan".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "dba".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // Real person mentions @dba
    let decision = router.route(&session, "@dba 请帮我分析数据库", Some("user_zhangsan")).await;

    assert_eq!(decision.mentions, vec!["dba"]);
    assert_eq!(decision.targets.len(), 2);

    // dba is mentioned → Send, zhangsan is not mentioned but is driver → Inject
    for target in &decision.targets {
        if target.bot_uuid == "dba" {
            assert_eq!(target.delivery_type, DeliveryType::Send);
        } else {
            // zhangsan is driver but not mentioned, real person sent the message
            // so zhangsan gets Inject (not coordinator for this message)
            assert_eq!(target.delivery_type, DeliveryType::Inject);
        }
    }
}

/// Test None sender (anonymous/user): all bots receive, coordinator gets Send
#[tokio::test]
async fn test_anonymous_sender_broadcasts_to_all() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-session".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "driver".to_string(),
        originator: Some("driver".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "driver".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
            Participant {
                bot_uuid: "consultant".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                            actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
},
        ],
        messages: Vec::new(),
        workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
        created_at: 0,
        updated_at: 0,
        context: None,
            group_kind: bcs_service_api::GroupKind::default(),
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 1,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
};

    // None sender = anonymous/user message
    let decision = router.route(&session, "Hello everyone", None).await;

    assert_eq!(decision.targets.len(), 2);

    // Driver gets Send (originator-first protocol), consultant gets Inject
    for target in &decision.targets {
        if target.bot_uuid == "driver" {
            assert_eq!(target.delivery_type, DeliveryType::Send);
        } else {
            assert_eq!(target.delivery_type, DeliveryType::Inject);
        }
    }
}

/// Test GroupContext for real person message scenario
#[test]
fn test_group_context_real_person_scenario() {
    // Real person "zhangsan" sends message in a group with "zhangsan-bot" and "dba-bot"
    let ctx_for_zhangsan_bot = GroupContext {
        session_id: "grp-001".to_string(),
        participants: vec!["zhangsan-bot".to_string(), "dba-bot".to_string()],
        originator: "zhangsan-bot".to_string(),
        from: "zhangsan".to_string(), // Real person, not a bot
        you_are_mentioned: false,
        is_sender: false, // zhangsan-bot didn't send this
        mentions: vec![],
        message: "请帮我排查数据库问题".to_string(),
        response_directive: None,
        recipient: None,
        recipient_name: None,
        recipient_role: None,
        delivery_type: None,
        routing_mode: None,
        group_type: None,
        from_bot_id: None,
        from_bot_owner: None,
    };

    // zhangsan-bot is the originator and should respond to this broadcast
    assert_eq!(ctx_for_zhangsan_bot.originator, "zhangsan-bot");
    assert!(!ctx_for_zhangsan_bot.is_sender); // Not the sender (real person sent it)
    assert!(!ctx_for_zhangsan_bot.you_are_mentioned); // Not mentioned

    // dba-bot's context
    let ctx_for_dba_bot = GroupContext {
        session_id: "grp-001".to_string(),
        participants: vec!["zhangsan-bot".to_string(), "dba-bot".to_string()],
        originator: "zhangsan-bot".to_string(),
        from: "zhangsan".to_string(),
        you_are_mentioned: false,
        is_sender: false,
        mentions: vec![],
        message: "请帮我排查数据库问题".to_string(),
        response_directive: None,
        recipient: None,
        recipient_name: None,
        recipient_role: None,
        delivery_type: None,
        routing_mode: None,
        group_type: None,
        from_bot_id: None,
        from_bot_owner: None,
    };

    // dba-bot is not originator, not mentioned, not sender → should observe silently
    assert_ne!(ctx_for_dba_bot.originator, "dba-bot");
    assert!(!ctx_for_dba_bot.you_are_mentioned);
    assert!(!ctx_for_dba_bot.is_sender);
}