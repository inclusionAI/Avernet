//! Unit fixtures for the in-memory Group repository.
//!
//! Copied verbatim from the single-file `memory.rs` fixtures.
use super::*;
use bcs_domain::{GroupMessageType, MessageRole, ParticipantRole};

#[test]
fn group_builder_uses_canonical_generated_id() {
    let group = GroupBuilder::new("driver").build();

    assert!(group.id.starts_with("bcs_grp_"));
    assert_eq!(group.id.chars().count(), 40);
}

#[tokio::test]
async fn test_group_store_crud() {
    let store = MemoryGroupRepo::new();

    let session = GroupBuilder::new("driver")
        .id("test-group")
        .participant(Participant {
            bot_uuid: "driver".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .build();

    // Create
    store.upsert(session.clone()).await.unwrap();

    // Read
    let retrieved = store.get("test-group").await;
    assert!(retrieved.is_some());
    assert_eq!(retrieved.unwrap().id, "test-group");

    // Add message
    let msg = GroupMessage {
        id: "msg-1".to_string(),
        timestamp: 0,
        sender: "user".to_string(),
        content: "Hello".to_string(),
        message_type: GroupMessageType::Bot,
        bot_name: None,
        role: MessageRole::User,
        history_meta: None,
        metadata: None,
        run_id: String::new(),
        attachments: None,
    };
    store.add_message("test-group", msg).await.unwrap();

    let updated = store.get("test-group").await.unwrap();
    assert_eq!(updated.messages.len(), 1);

    // Delete
    let deleted = store.delete("test-group").await.unwrap();
    assert!(deleted.is_some());

    let not_found = store.get("test-group").await;
    assert!(not_found.is_none());
}

#[tokio::test]
async fn test_group_builder() {
    let session = GroupBuilder::new("driver")
        .id("custom-id")
        .label("Test Session")
        .participant(Participant {
            bot_uuid: "driver".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .build();

    assert_eq!(session.id, "custom-id");
    assert_eq!(session.label, Some("Test Session".to_string()));
    assert_eq!(session.driver_bot, "driver");
    assert_eq!(session.participants.len(), 1);
}

#[tokio::test]
async fn test_add_message_to_nonexistent_group() {
    let store = MemoryGroupRepo::new();

    let msg = GroupMessage {
        id: "msg-1".to_string(),
        timestamp: 0,
        sender: "user".to_string(),
        content: "Hello".to_string(),
        message_type: GroupMessageType::Bot,
        bot_name: None,
        role: MessageRole::User,
        history_meta: None,
        metadata: None,
        run_id: String::new(),
        attachments: None,
    };

    let result = store.add_message("nonexistent", msg).await;
    assert!(result.is_err());

    match result {
        Err(ServiceError::GroupNotFound(id)) => assert_eq!(id, "nonexistent"),
        _ => panic!("Expected GroupNotFound error"),
    }
}

#[tokio::test]
async fn test_add_participant_duplicate_ignored() {
    let store = MemoryGroupRepo::new();

    let group = GroupBuilder::new("driver")
        .id("test-group")
        .participant(Participant {
            bot_uuid: "driver".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .build();

    store.upsert(group).await.unwrap();

    // Add same participant again
    let duplicate = Participant {
        bot_uuid: "driver".to_string(),
        bot_name: None,
        kind: None,
        role: ParticipantRole::Consultant, // Different role
        actor_kind: bcs_service_api::ActorKind::default(),
        mode: None,
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
    };
    store
        .add_participant("test-group", duplicate)
        .await
        .unwrap();

    let retrieved = store.get("test-group").await.unwrap();
    assert_eq!(retrieved.participants.len(), 1); // Still only 1
    assert_eq!(retrieved.participants[0].role, ParticipantRole::Driver); // Original role preserved
}

#[tokio::test]
async fn test_add_participant_to_nonexistent_group() {
    let store = MemoryGroupRepo::new();

    let participant = Participant {
        bot_uuid: "bot".to_string(),
        bot_name: None,
        kind: None,
        role: ParticipantRole::Consultant,
        actor_kind: bcs_service_api::ActorKind::default(),
        mode: None,
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
    };

    let result = store.add_participant("nonexistent", participant).await;
    assert!(result.is_err());
}

#[tokio::test]
async fn test_update_workspace() {
    let store = MemoryGroupRepo::new();

    let group = GroupBuilder::new("driver").id("test-group").build();

    store.upsert(group).await.unwrap();

    let workspace = Workspace {
        tasks: vec![bcs_service_api::Task {
            id: "task-1".to_string(),
            description: "Test task".to_string(),
            assigned_to: Some("driver".to_string()),
            status: bcs_service_api::TaskStatus::InProgress,
        }],
        decisions: vec!["Decision 1".to_string()],
        notes: vec![],
        audit_log: vec![],
    };

    store
        .update_workspace("test-group", workspace.clone())
        .await
        .unwrap();

    let retrieved = store.get("test-group").await.unwrap();
    assert_eq!(retrieved.workspace.tasks.len(), 1);
    assert_eq!(retrieved.workspace.tasks[0].id, "task-1");
    assert_eq!(retrieved.workspace.decisions.len(), 1);
}

#[tokio::test]
async fn test_update_workspace_nonexistent_group() {
    let store = MemoryGroupRepo::new();

    let workspace = Workspace::default();
    let result = store.update_workspace("nonexistent", workspace).await;
    assert!(result.is_err());
}

#[tokio::test]
async fn test_update_label() {
    let store = MemoryGroupRepo::new();

    let session = GroupBuilder::new("driver").id("test-group").build();

    store.upsert(session).await.unwrap();

    store
        .update_label("test-group", Some("New Label".to_string()))
        .await
        .unwrap();

    let retrieved = store.get("test-group").await.unwrap();
    assert_eq!(retrieved.label, Some("New Label".to_string()));

    // Clear label
    store.update_label("test-group", None).await.unwrap();
    let retrieved = store.get("test-group").await.unwrap();
    assert!(retrieved.label.is_none());
}

#[tokio::test]
async fn test_update_label_nonexistent_group() {
    let store = MemoryGroupRepo::new();
    let result = store
        .update_label("nonexistent", Some("Label".to_string()))
        .await;
    assert!(result.is_err());
}

#[tokio::test]
async fn test_list_groups() {
    let store = MemoryGroupRepo::new();

    let session1 = GroupBuilder::new("driver1").id("session-1").build();
    let session2 = GroupBuilder::new("driver2").id("group-2").build();

    store.upsert(session1).await.unwrap();
    store.upsert(session2).await.unwrap();

    let sessions = store.list().await;
    assert_eq!(sessions.len(), 2);

    // Delete one
    assert!(store.delete("session-1").await.unwrap().is_some());

    let sessions = store.list().await;
    assert_eq!(sessions.len(), 1);
    assert_eq!(sessions[0].id, "group-2");
}

#[tokio::test]
async fn test_group_timestamp_updates_on_message() {
    let store = MemoryGroupRepo::new();

    let session = GroupBuilder::new("driver").id("test-group").build();
    let original_updated_at = session.updated_at;
    store.upsert(session).await.unwrap();

    // Small delay to ensure timestamp difference
    tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;

    let msg = GroupMessage {
        id: "msg-1".to_string(),
        timestamp: 0,
        sender: "user".to_string(),
        content: "Hello".to_string(),
        message_type: GroupMessageType::Bot,
        bot_name: None,
        role: MessageRole::User,
        history_meta: None,
        metadata: None,
        run_id: String::new(),
        attachments: None,
    };
    store.add_message("test-group", msg).await.unwrap();

    let retrieved = store.get("test-group").await.unwrap();
    assert!(retrieved.updated_at > original_updated_at);
}

// ========================================================================
// Additional tests for BCS.md features
// ========================================================================

#[tokio::test]
async fn test_update_group_status() {
    let store = MemoryGroupRepo::new();

    let session = GroupBuilder::new("driver").id("test-group").build();

    store.upsert(session).await.unwrap();

    // Update to completed status
    store
        .update_status("test-group", GroupStatus::Completed)
        .await
        .unwrap();

    let retrieved = store.get("test-group").await.unwrap();
    assert_eq!(retrieved.status, GroupStatus::Completed);

    // Update to closed status
    store
        .update_status("test-group", GroupStatus::Closed)
        .await
        .unwrap();
    let retrieved = store.get("test-group").await.unwrap();
    assert_eq!(retrieved.status, GroupStatus::Closed);
}

#[tokio::test]
async fn test_update_status_nonexistent_group() {
    let store = MemoryGroupRepo::new();
    let result = store
        .update_status("nonexistent", GroupStatus::Completed)
        .await;
    assert!(result.is_err());
}

#[tokio::test]
async fn test_group_originator_defaults_to_driver() {
    let session = GroupBuilder::new("driver-bot")
        .id("test-group")
        .participant(Participant {
            bot_uuid: "driver-bot".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .build();

    // When originator is not set, it should default to driver_bot
    assert_eq!(session.originator(), "driver-bot");
    assert!(session.originator.is_none()); // The field itself is None
}

#[tokio::test]
async fn test_group_originator_can_be_set_explicitly() {
    let session = GroupBuilder::new("driver-bot")
        .id("test-group")
        .originator("initiator-bot")
        .participant(Participant {
            bot_uuid: "driver-bot".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .participant(Participant {
            bot_uuid: "initiator-bot".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .build();

    assert_eq!(session.originator(), "initiator-bot");
    assert_eq!(session.originator, Some("initiator-bot".to_string()));
}

#[tokio::test]
async fn test_group_multicast_message_to_all_participants() {
    // Test G1 scenario: broadcast to all participants
    let store = MemoryGroupRepo::new();

    let session = GroupBuilder::new("driver")
        .id("test-group")
        .participant(Participant {
            bot_uuid: "driver".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .participant(Participant {
            bot_uuid: "dba".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .participant(Participant {
            bot_uuid: "security".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .build();

    store.upsert(session).await.unwrap();

    // Add a group message (broadcast style, no @mention)
    let msg = GroupMessage {
        id: "msg-1".to_string(),
        timestamp: 0,
        sender: "user".to_string(),
        content: "团队帮我评估一下这个方案".to_string(),
        message_type: GroupMessageType::Bot,
        bot_name: None,
        role: MessageRole::User,
        history_meta: None,
        metadata: None,
        run_id: String::new(),
        attachments: None,
    };
    store.add_message("test-group", msg).await.unwrap();

    let retrieved = store.get("test-group").await.unwrap();
    assert_eq!(retrieved.messages.len(), 1);
    assert_eq!(retrieved.participants.len(), 3);
}

#[tokio::test]
async fn test_group_add_multiple_messages_transcript() {
    let store = MemoryGroupRepo::new();

    let session = GroupBuilder::new("driver")
        .id("test-group")
        .participant(Participant {
            bot_uuid: "driver".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .participant(Participant {
            bot_uuid: "dba".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .build();

    store.upsert(session).await.unwrap();

    let messages = vec![
        GroupMessage {
            id: "msg-1".to_string(),
            timestamp: 100,
            sender: "user".to_string(),
            content: "帮我排查数据库死锁".to_string(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::User,
            history_meta: None,
            metadata: None,
            run_id: String::new(),
            attachments: None,
        },
        GroupMessage {
            id: "msg-2".to_string(),
            timestamp: 200,
            sender: "driver".to_string(),
            content: "@dba 请分析死锁根因".to_string(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::Assistant,
            history_meta: None,
            metadata: None,
            run_id: String::new(),
            attachments: None,
        },
        GroupMessage {
            id: "msg-3".to_string(),
            timestamp: 300,
            sender: "dba".to_string(),
            content: "分析结果：加锁顺序不一致...".to_string(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::Assistant,
            history_meta: None,
            metadata: None,
            run_id: String::new(),
            attachments: None,
        },
    ];

    for msg in messages {
        store.add_message("test-group", msg).await.unwrap();
    }

    let retrieved = store.get("test-group").await.unwrap();
    assert_eq!(retrieved.messages.len(), 3);
    assert_eq!(retrieved.messages[0].sender, "user");
    assert_eq!(retrieved.messages[1].sender, "driver");
    assert_eq!(retrieved.messages[2].sender, "dba");
}

#[tokio::test]
async fn test_group_upsert_updates_existing() {
    let store = MemoryGroupRepo::new();

    let session1 = GroupBuilder::new("driver")
        .id("test-group")
        .label("Initial Label")
        .build();
    store.upsert(session1).await.unwrap();

    let session2 = GroupBuilder::new("driver")
        .id("test-group")
        .label("Updated Label")
        .participant(Participant {
            bot_uuid: "new-participant".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .build();
    store.upsert(session2).await.unwrap();

    let retrieved = store.get("test-group").await.unwrap();
    assert_eq!(retrieved.label, Some("Updated Label".to_string()));
}

#[tokio::test]
async fn mutable_patch_preserves_unrelated_routing_fields() {
    let store = MemoryGroupRepo::new();
    let mut group = GroupBuilder::new("driver").id("test-group").build();
    group.routing_policy = Some(bcs_service_api::RoutingPolicy {
        mode: bcs_service_api::RoutingMode::Structured,
        default_bot_final_delivery: bcs_service_api::DefaultDelivery::SendToDriver,
        sender_routes: HashMap::from([("worker".to_string(), vec!["driver".to_string()])]),
    });
    store.upsert(group).await.unwrap();

    store
        .patch_mutable_fields(
            "test-group",
            GroupMutableFieldsPatch {
                label: Some("Renamed".to_string()),
                default_bot_final_delivery: Some(
                    bcs_service_api::DefaultDelivery::InjectObservers,
                ),
                ..Default::default()
            },
        )
        .await
        .unwrap();

    let stored = store.get("test-group").await.unwrap();
    let routing = stored.routing_policy.unwrap();
    assert_eq!(stored.label.as_deref(), Some("Renamed"));
    assert_eq!(routing.mode, bcs_service_api::RoutingMode::Structured);
    assert_eq!(
        routing.sender_routes.get("worker"),
        Some(&vec!["driver".to_string()])
    );
    assert_eq!(
        routing.default_bot_final_delivery,
        bcs_service_api::DefaultDelivery::InjectObservers
    );
}

#[tokio::test]
async fn test_group_long_running_project() {
    let session = GroupBuilder::new("pm-bot")
        .id("project-group")
        .label("项目运行群")
        .originator("pm-bot")
        .participant(Participant {
            bot_uuid: "pm-bot".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .participant(Participant {
            bot_uuid: "dev-bot".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .participant(Participant {
            bot_uuid: "qa-bot".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: bcs_service_api::ActorKind::default(),
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        })
        .build();

    assert_eq!(session.participants.len(), 3);
    assert_eq!(session.originator(), "pm-bot");
}

#[tokio::test]
async fn test_group_workspace_persistence() {
    let store = MemoryGroupRepo::new();

    let session = GroupBuilder::new("driver").id("test-group").build();
    store.upsert(session).await.unwrap();

    let workspace = Workspace {
        decisions: vec![
            "决定使用方案A".to_string(),
            "安全审查由安全Bot负责".to_string(),
        ],
        tasks: vec![
            bcs_service_api::Task {
                id: "task-1".to_string(),
                description: "数据库死锁排查".to_string(),
                assigned_to: Some("dba".to_string()),
                status: bcs_service_api::TaskStatus::Completed,
            },
            bcs_service_api::Task {
                id: "task-2".to_string(),
                description: "安全审核".to_string(),
                assigned_to: Some("security".to_string()),
                status: bcs_service_api::TaskStatus::InProgress,
            },
        ],
        notes: vec!["需要注意性能影响".to_string()],
        audit_log: vec![bcs_service_api::AuditEntry {
            timestamp: 1234567890,
            action: "task_completed".to_string(),
            actor: "dba".to_string(),
            details: "Marked task-1 as completed".to_string(),
        }],
    };

    store
        .update_workspace("test-group", workspace)
        .await
        .unwrap();

    let retrieved = store.get("test-group").await.unwrap();
    assert_eq!(retrieved.workspace.decisions.len(), 2);
    assert_eq!(retrieved.workspace.tasks.len(), 2);
    assert_eq!(retrieved.workspace.audit_log.len(), 1);
    assert_eq!(
        retrieved.workspace.tasks[0].status,
        bcs_service_api::TaskStatus::Completed
    );
}
