//! WebSocket Integration Tests for BCS.
//!
//! These tests verify the core routing and protocol logic
//! without requiring a running server.
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_ws_test
//! ```

use bcs_service_api::{
    DeliveryType, Group, GroupStatus, GroupStrategy, Participant, ParticipantRole, Workspace,
    RoutingCoreService,
};
use bcs_routing::MessageRouter;
use bcs_protocol::GroupContext;

// ============================================================================
// Bot Lifecycle Tests (Protocol Layer)
// ============================================================================

/// Test bot.connect frame structure
#[test]
fn test_bot_connect_frame_structure() {
    let connect_frame = serde_json::json!({
        "type": "req",
        "id": "1",
        "method": "bot.connect",
        "params": {
            "token": ""
        }
    });

    assert_eq!(connect_frame["type"], "req");
    assert_eq!(connect_frame["method"], "bot.connect");
    assert_eq!(connect_frame["params"]["token"], "");
}

/// Test bot.connect response structure
#[test]
fn test_bot_connect_response_structure() {
    let response = serde_json::json!({
        "type": "res",
        "id": "1",
        "ok": true,
        "payload": {
            "is_new": true,
            "bot_id": "bot-xxx",
            "token": "token-yyy"
        }
    });

    assert!(response["ok"].as_bool().unwrap());
    assert!(response["payload"]["is_new"].as_bool().unwrap());
}

/// Test onboard.request frame structure
#[test]
fn test_onboard_request_frame() {
    let frame = serde_json::json!({
        "type": "req",
        "id": "onboard-1",
        "method": "onboard.request",
        "params": {
            "bot_id": "bot-xxx",
            "token": "token-yyy"
        }
    });

    assert_eq!(frame["method"], "onboard.request");
}

/// Test onboard.response frame structure
#[test]
fn test_onboard_response_frame() {
    let frame = serde_json::json!({
        "type": "res",
        "id": "onboard-1",
        "ok": true,
        "payload": {
            "name": "张三",
            "summary": "开发助手",
            "skills": ["code_review", "deployment"],
            "domains": ["development"],
            "scopes": ["production"]
        }
    });

    assert!(frame["ok"].as_bool().unwrap());
    let skills = frame["payload"]["skills"].as_array().unwrap();
    assert_eq!(skills.len(), 2);
}

// ============================================================================
// Group Chat Routing Tests
// ============================================================================

/// Test routing to all participants with correct delivery types
#[tokio::test]
async fn test_group_chat_routing_no_mention() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-group-001".to_string(),
        label: Some("Test Group".to_string()),
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
                bot_uuid: "expert".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            },
        ],
        messages: vec![],
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

    // No @mention → driver gets Send, expert gets Inject
    let decision = router.route(&session, "Hello everyone", None).await;

    assert_eq!(decision.targets.len(), 2);
    for target in &decision.targets {
        if target.bot_uuid == "driver" {
            assert_eq!(target.delivery_type, DeliveryType::Send);
        } else {
            assert_eq!(target.delivery_type, DeliveryType::Inject);
        }
    }
}

/// Test routing with @mention
#[tokio::test]
async fn test_group_chat_routing_with_mention() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-group-002".to_string(),
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
                bot_uuid: "expert".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            },
        ],
        messages: vec![],
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

    // @mention → mentioned gets Send, others get Inject
    let decision = router.route(&session, "@expert please help", None).await;

    assert_eq!(decision.mentions, vec!["expert"]);
    for target in &decision.targets {
        if target.bot_uuid == "expert" {
            assert_eq!(target.delivery_type, DeliveryType::Send);
        } else {
            assert_eq!(target.delivery_type, DeliveryType::Inject);
        }
    }
}

/// Test sender exclusion in routing
#[tokio::test]
async fn test_group_chat_sender_exclusion() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-group-003".to_string(),
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
                bot_uuid: "expert".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            },
        ],
        messages: vec![],
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

    // Bot sender → sender excluded from targets
    let decision = router.route(&session, "Update from driver", Some("driver")).await;

    assert_eq!(decision.targets.len(), 1);
    assert_eq!(decision.targets[0].bot_uuid, "expert");
}

// ============================================================================
// Real Person Participation Tests
// ============================================================================

/// Test real person sending message to group
#[tokio::test]
async fn test_real_person_sends_to_group() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-group-004".to_string(),
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
        messages: vec![],
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

    // Real person "user_zhangsan" sends message (not a participant bot_id)
    let decision = router.route(&session, "Please help with database", Some("user_zhangsan")).await;

    // All bots should receive (sender is not a participant)
    assert_eq!(decision.targets.len(), 2);

    let bot_ids: Vec<_> = decision.targets.iter().map(|t| t.bot_uuid.as_str()).collect();
    assert!(bot_ids.contains(&"zhangsan"));
    assert!(bot_ids.contains(&"dba"));
}

// ============================================================================
// GroupContext Tests
// ============================================================================

/// Test GroupContext for bot response decision
#[test]
fn test_group_context_for_bot_decision() {
    // Bot is mentioned → should respond
    let ctx = GroupContext {
        session_id: "grp-001".to_string(),
        participants: vec!["driver".to_string(), "expert".to_string()],
        originator: "driver".to_string(),
        from: "user".to_string(),
        you_are_mentioned: true,
        is_sender: false,
        mentions: vec!["expert".to_string()],
        message: "@expert please help".to_string(),
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

    assert!(ctx.you_are_mentioned);
    assert!(!ctx.is_sender);
    // Bot should respond because it's mentioned
}

/// Test GroupContext for originator response decision
#[test]
fn test_group_context_for_originator_decision() {
    // Bot is originator, no @mention → should respond
    let ctx = GroupContext {
        session_id: "grp-002".to_string(),
        participants: vec!["driver".to_string(), "consultant".to_string()],
        originator: "driver".to_string(),
        from: "user".to_string(),
        you_are_mentioned: false,
        is_sender: false,
        mentions: vec![],
        message: "Status update please".to_string(),
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

    assert_eq!(ctx.originator, "driver");
    assert!(!ctx.you_are_mentioned);
    assert!(!ctx.is_sender);
    // If this is for "driver", it should respond as originator
}

// ============================================================================
// chat.send vs chat.inject Frame Tests
// ============================================================================

/// Test chat.send frame structure
#[test]
fn test_chat_send_frame_structure() {
    let frame = serde_json::json!({
        "type": "req",
        "id": "chat-001",
        "method": "chat.send",
        "params": {
            "sessionKey": "group:abc123",
            "bcsGroupId": "grp-001",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello"}],
                "timestamp": 1234567890
            },
            "channel": {
                "source": "webui",
                "userId": "user_001"
            },
            "sessionContext": {
                "sessionId": "grp-001",
                "participants": ["driver", "consultant"],
                "originator": "driver",
                "from": "user",
                "youAreMentioned": true,
                "isSender": false,
                "mentions": ["consultant"],
                "message": "Hello @consultant"
            }
        }
    });

    assert_eq!(frame["method"], "chat.send");
    assert!(frame["params"]["sessionContext"]["youAreMentioned"].as_bool().unwrap());
}

/// Test chat.inject event frame structure
#[test]
fn test_chat_inject_frame_structure() {
    let frame = serde_json::json!({
        "type": "event",
        "event": "chat.inject",
        "payload": {
            "sessionKey": "group:abc123",
            "bcsGroupId": "grp-001",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello"}],
                "timestamp": 1234567890
            },
            "channel": {
                "source": "api"
            },
            "sessionContext": {
                "sessionId": "grp-001",
                "participants": ["driver", "consultant"],
                "originator": "driver",
                "from": "user",
                "youAreMentioned": false,
                "isSender": false,
                "mentions": ["consultant"],
                "message": "Hello @consultant"
            }
        }
    });

    assert_eq!(frame["type"], "event");
    assert_eq!(frame["event"], "chat.inject");
    assert!(!frame["payload"]["sessionContext"]["youAreMentioned"].as_bool().unwrap());
}

// ============================================================================
// Edge Cases
// ============================================================================

/// Test @ALL mention routing
#[tokio::test]
async fn test_all_mention_routing() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-group-005".to_string(),
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
                bot_uuid: "expert1".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            },
            Participant {
                bot_uuid: "expert2".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            },
        ],
        messages: vec![],
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

    // @ALL → everyone gets Send
    let decision = router.route(&session, "@ALL please all respond", None).await;

    assert_eq!(decision.targets.len(), 3);
    for target in &decision.targets {
        assert_eq!(target.delivery_type, DeliveryType::Send);
    }
}

/// Test invalid @mention ignored
#[tokio::test]
async fn test_invalid_mention_ignored() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-group-006".to_string(),
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
        ],
        messages: vec![],
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

    // @unknown is not a participant
    let decision = router.route(&session, "@unknown hello", None).await;

    // Mention ignored, broadcast to driver
    assert!(decision.mentions.is_empty());
    assert_eq!(decision.targets.len(), 1);
    assert_eq!(decision.targets[0].delivery_type, DeliveryType::Send); // Driver gets Send
}

/// Test empty group (single participant)
#[tokio::test]
async fn test_single_participant_group() {
    let router = MessageRouter::new();

    let session = Group {
        id: "test-group-007".to_string(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "solo".to_string(),
        originator: Some("solo".to_string()),
        routing_policy: None,
        participants: vec![
            Participant {
                bot_uuid: "solo".to_string(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: bcs_service_api::ActorKind::default(),
                mode: None,
            },
        ],
        messages: vec![],
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

    // Message from the only participant → no targets (self excluded)
    let decision = router.route(&session, "Hello", Some("solo")).await;
    assert!(decision.targets.is_empty());

    // Message from external user → single target
    let decision = router.route(&session, "Hello", Some("user")).await;
    assert_eq!(decision.targets.len(), 1);
}