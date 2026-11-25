use super::*;

#[tokio::test]
async fn create_group_with_human_consultant_chat() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let result = service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
                participant("human_alice", Some("consultant")),
            ],
        ))
        .await
        .expect("human consultant in chat group should succeed");
    assert_eq!(result.participants.len(), 3);

    let human = result
        .participants
        .iter()
        .find(|p| p.bot_uuid == "human_alice")
        .expect("human participant");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.mode, Some(ParticipantMode::Present));
    assert_eq!(human.role, "consultant");
}

#[tokio::test]
async fn create_group_with_human_worker_manager_worker() {
    let fixture = Fixture::new()
        .with_bot("mgr", "Manager", "public", Some("alice"))
        .with_bot("worker-bot", "Worker", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("human_alice"),
        "mgr",
        vec![
            participant("mgr", Some("manager")),
            participant("worker-bot", Some("worker")),
            participant("human_alice", Some("worker")),
        ],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);

    let result = service
        .create_group(cmd)
        .await
        .expect("human worker in manager_worker group should succeed");
    assert_eq!(result.participants.len(), 3);

    let human = result
        .participants
        .iter()
        .find(|p| p.bot_uuid == "human_alice")
        .expect("human participant");
    assert_eq!(human.mode, Some(ParticipantMode::Present));
    assert_eq!(human.role, "worker");
}

#[tokio::test]
async fn create_group_rejects_human_as_driver() {
    let fixture = Fixture::new()
        .with_bot("bot-a", "Bot A", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let result = service
        .create_group(create_cmd(
            Some("bot-a"),
            "bot-a",
            vec![
                participant("bot-a", Some("consultant")),
                participant("human_alice", Some("driver")),
            ],
        ))
        .await;
    let err = result.expect_err("human driver should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(ref msg) if msg.contains("consultant or observer")), "got: {:?}", err);
}

#[tokio::test]
async fn create_group_rejects_human_as_manager() {
    let fixture = Fixture::new()
        .with_bot("bot-a", "Bot A", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("bot-a"),
        "bot-a",
        vec![
            participant("bot-a", Some("worker")),
            participant("human_alice", Some("manager")),
        ],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);

    let err = service
        .create_group(cmd)
        .await
        .expect_err("human manager should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(ref msg) if msg.contains("worker or observer")), "got: {:?}", err);
}

#[tokio::test]
async fn create_group_rejects_all_human_group() {
    let fixture = Fixture::new()
        .with_human("human_alice", "Alice")
        .with_human("human_bob", "Bob");
    let service = fixture.service_with_limits(5, 10, 10);

    let err = service
        .create_group(create_cmd(
            Some("human_alice"),
            "human_alice",
            vec![
                participant("human_alice", Some("driver")),
                participant("human_bob", Some("consultant")),
            ],
        ))
        .await
        .expect_err("all-human group should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(ref msg) if msg.contains("at least one bot")), "got: {:?}", err);
}

#[tokio::test]
async fn create_group_human_owner_not_participant_ok() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let result = service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
            ],
        ))
        .await
        .expect("human owner creating group without being participant should succeed");
    assert_eq!(result.participants.len(), 2);
    assert!(result.participants.iter().all(|p| p.actor_kind == ActorKind::Bot));
}

#[tokio::test]
async fn create_group_rejects_human_driver_bot_id() {
    // When caller == driver_bot_id, authorize_driver passes (self-auth).
    // Then validate_human_constraints catches the human_ prefix.
    let fixture = Fixture::new()
        .with_bot("bot-a", "Bot A", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let result = service
        .create_group(create_cmd(
            Some("human_alice"),
            "human_alice",
            vec![
                participant("human_alice", Some("driver")),
                participant("bot-a", Some("consultant")),
            ],
        ))
        .await;
    let err = result.expect_err("human as driver_bot_id should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(ref msg) if msg.contains("must be a bot")), "got: {:?}", err);
}

#[tokio::test]
async fn add_member_human_consultant_ok() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_bob", "Bob");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "human_bob".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("adding human consultant should succeed");
    assert_eq!(result.member.actor_kind, ActorKind::Human);
    assert_eq!(result.member.mode, Some(ParticipantMode::Present));
    assert_eq!(result.member.role, "consultant");
}

#[tokio::test]
async fn add_member_human_worker_in_manager_worker_ok() {
    let fixture = Fixture::new()
        .with_bot("mgr", "Manager", "public", Some("alice"))
        .with_human("human_bob", "Bob");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("mgr"),
        "mgr",
        vec![participant("mgr", Some("manager"))],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);
    service.create_group(cmd).await.expect("create mw group");

    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("mgr".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "human_bob".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("adding human worker to mw group should succeed");
    assert_eq!(result.member.actor_kind, ActorKind::Human);
    assert_eq!(result.member.mode, Some(ParticipantMode::Present));
    assert_eq!(result.member.role, "worker");
}

#[tokio::test]
async fn originator_human_can_delete_group() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let result = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect("human originator should be able to delete group");
    assert!(result.deleted);
    assert!(fixture.group.get("group-under-test").await.is_none());
}

#[tokio::test]
async fn originator_human_can_add_member() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("human_alice".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "helper".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("human originator should be able to add member");
    assert_eq!(result.member.bot_uuid, "helper");
}

#[tokio::test]
async fn originator_human_can_update_label() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let result = service
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
            label: Some("updated by originator".to_string()),
        })
        .await
        .expect("human originator should be able to update label");
    assert_eq!(result.label, Some("updated by originator".to_string()));
}

#[tokio::test]
async fn non_coordinator_cannot_delete_group() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("stranger", "Stranger", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let err = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "stranger".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect_err("stranger should not be able to delete group");
    assert!(matches!(err, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn originator_bot_self_driver_can_manage() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    // driver can add member (originator == driver, both work)
    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "helper".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("driver should still be able to add member");
    assert_eq!(result.member.bot_uuid, "helper");

    // driver can delete group
    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect("driver should be able to delete group");
    assert!(deleted.deleted);
}

#[tokio::test]
async fn originator_and_driver_different_both_can_manage() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    // human originator (different from driver) can update label
    service
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
            label: Some("originator updated".to_string()),
        })
        .await
        .expect("human originator should be able to update label");

    // driver can also update label (both are coordinators)
    service
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
            label: Some("driver updated".to_string()),
        })
        .await
        .expect("driver should also be able to update label");

    // driver can delete
    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect("driver should be able to delete group");
    assert!(deleted.deleted);
}
