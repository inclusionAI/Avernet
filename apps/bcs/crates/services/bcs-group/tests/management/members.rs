use super::*;

use bcs_service_api::port::{ParticipantViewBindingPort, ParticipantViewScopeChangeLease};

#[derive(Default)]
struct RecordingParticipantViewBindings {
    begun: Mutex<Vec<(String, String)>>,
    finished: Mutex<Vec<(String, String)>>,
}

#[async_trait]
impl ParticipantViewBindingPort for RecordingParticipantViewBindings {
    async fn begin_scope_change(
        &self,
        scope_id: &str,
        human_actor_id: &str,
    ) -> ServiceResult<ParticipantViewScopeChangeLease> {
        self.begun
            .lock()
            .await
            .push((scope_id.to_string(), human_actor_id.to_string()));
        Ok(ParticipantViewScopeChangeLease {
            scope_id: scope_id.to_string(),
            human_actor_id: human_actor_id.to_string(),
            lease_id: 1,
        })
    }

    async fn finish_scope_change(
        &self,
        lease: ParticipantViewScopeChangeLease,
    ) -> ServiceResult<()> {
        self.finished
            .lock()
            .await
            .push((lease.scope_id, lease.human_actor_id));
        Ok(())
    }
}

#[tokio::test]
async fn add_member_allows_provider_downlink_bot_in_manager_worker_group() {
    let fixture = Fixture::new()
        .with_bot("manager", "Manager", "public", Some("alice"))
        .with_bot("worker", "Worker", "public", None)
        .with_provider_downlink_bot("provider-worker", "Provider Worker", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("manager"),
        "manager",
        vec![
            participant("manager", Some("manager")),
            participant("worker", Some("worker")),
        ],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);
    let group = service
        .create_group(cmd)
        .await
        .expect("regular manager_worker group should be created");

    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("manager".to_string()),
            human_actor_id: None,
            group_id: group.group_id,
            bot_id: "provider-worker".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("provider downlink bot can be added to manager_worker group");
    assert_eq!(result.member.bot_uuid, "provider-worker");
}

#[tokio::test]
async fn add_member_assigns_worker_in_manager_worker_group() {
    let fixture = Fixture::new()
        .with_bot("manager", "Manager", "public", Some("alice"))
        .with_bot("existing-worker", "Existing Worker", "public", None)
        .with_bot("new-worker", "New Worker", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("manager"),
        "manager",
        vec![
            participant("manager", Some("manager")),
            participant("existing-worker", Some("worker")),
        ],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);
    let group = service
        .create_group(cmd)
        .await
        .expect("manager_worker group should be created");

    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("manager".to_string()),
            human_actor_id: None,
            group_id: group.group_id,
            bot_id: "new-worker".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("member should be added with the strategy-owned role");

    assert_eq!(result.member.role, "worker");
}

#[tokio::test]
async fn add_member_authorizes_coordinator_and_checks_reachability() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("other-bot", "Other", "public", None)
        .with_bot("friend", "Friend", "protected", None)
        .with_bot("private-friend", "Private Friend", "private", None)
        .with_bot("stranger", "Stranger", "protected", None)
        .with_friendship("driver", "friend")
        .with_friendship("driver", "private-friend");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let non_coordinator = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("other-bot".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "friend".to_string(),
            message_view_scope: None,
        })
        .await;
    // Row 14 cutover (bot-authority plan Task 6.3):
    // acting THROUGH a Bot the Human does not
    // control is rejected at the control leg, before
    // the coordinator check that used to catch it via
    // the legacy `created_by = None` auto-claim.
    assert!(matches!(
        non_coordinator,
        Err(GroupUseCaseError::Forbidden(message))
            if message.contains("Not authorized as bot 'other-bot'")
    ));

    let protected_stranger = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "stranger".to_string(),
            message_view_scope: None,
        })
        .await;
    assert!(matches!(
        protected_stranger,
        Err(GroupUseCaseError::Forbidden(_))
    ));

    let private_friend = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "private-friend".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("private friend target is reachable");
    assert_eq!(private_friend.member.bot_uuid, "private-friend");

    let wrong_human_owner = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_impostor".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "friend".to_string(),
            message_view_scope: None,
        })
        .await;
    assert!(matches!(
        wrong_human_owner,
        Err(GroupUseCaseError::Forbidden(message))
            if message.contains("Not authorized as bot")
    ));

    let added = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "friend".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("friend target is reachable");
    assert_eq!(added.group_id, "group-under-test");
    assert_eq!(added.member.bot_uuid, "friend");
    assert_eq!(added.member.role, "consultant");

    let stored = fixture.group.get("group-under-test").await.unwrap();
    assert!(
        stored
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "friend")
    );
}

#[tokio::test]
async fn add_member_writes_subscription_edge_with_driver_identity_for_public_targets() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("originator", "Originator", "public", Some("alice"))
        .with_bot("public-helper", "Public Helper", "public", None)
        .with_bot("protected-helper", "Protected Helper", "protected", None)
        .with_friendship("driver", "protected-helper");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let mut group = fixture.group.get("group-under-test").await.unwrap();
    group.originator = Some("originator".to_string());
    fixture.group.upsert(group).await.unwrap();

    service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("originator".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "public-helper".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("originator can add public helper");
    service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("originator".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "protected-helper".to_string(),
            message_view_scope: None,
        })
        .await
        .expect("originator can add protected friend of driver");

    let driver_edge = fixture
        .relation
        .get_edge("driver", "public-helper", "dev")
        .await
        .unwrap();
    assert!(
        driver_edge.is_some(),
        "add-member should subscribe the driver to public targets"
    );

    let originator_edge = fixture
        .relation
        .get_edge("originator", "public-helper", "dev")
        .await
        .unwrap();
    assert!(
        originator_edge.is_none(),
        "add-member should not attribute subscription edges to the caller"
    );

    let protected_edge = fixture
        .relation
        .get_edge("driver", "protected-helper", "dev")
        .await
        .unwrap();
    assert!(
        protected_edge.is_none(),
        "protected visibility should not create subscription edges"
    );
}

#[tokio::test]
async fn participant_mode_update_authorizes_self_or_creator_and_inserts_human() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("member", "Member", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    fixture
        .relation
        .insert_creator("human_alice", "member", "dev");
    let participant_view_bindings = Arc::new(RecordingParticipantViewBindings::default());
    let service = fixture
        .service_with_limits(5, 10, 10)
        .with_participant_view_bindings(participant_view_bindings.clone());
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("member", None),
            ],
        ))
        .await
        .unwrap();

    let member_update = service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "member".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "member".to_string(),
            mode: bcs_service_api::ParticipantMode::Muted,
            message_view_scope: None,
        })
        .await
        .unwrap();
    assert_eq!(member_update.mode, bcs_service_api::ParticipantMode::Muted);

    let human_update = service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "human_alice".to_string(),
            mode: bcs_service_api::ParticipantMode::Present,
            message_view_scope: Some(MessageViewScope::Participant),
        })
        .await
        .unwrap();
    assert_eq!(human_update.actor_id, "human_alice");
    assert_eq!(
        human_update.message_view_scope,
        MessageViewScope::Participant,
    );

    let stored = fixture.group.get("group-under-test").await.unwrap();
    let human = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("human participant should be inserted on first mode update");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.mode, Some(bcs_service_api::ParticipantMode::Present));
    assert_eq!(human.message_view_scope, MessageViewScope::Participant);
    assert!(human.tags.is_empty());

    let managed_scope = service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "human_alice".to_string(),
            mode: bcs_service_api::ParticipantMode::Absent,
            message_view_scope: Some(MessageViewScope::Full),
        })
        .await
        .expect("group coordinator may update a Human participant scope");
    assert_eq!(managed_scope.mode, bcs_service_api::ParticipantMode::Present);
    assert_eq!(managed_scope.message_view_scope, MessageViewScope::Full);

    let full_view = service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "human_alice".to_string(),
            mode: bcs_service_api::ParticipantMode::Present,
            message_view_scope: Some(MessageViewScope::Full),
        })
        .await
        .unwrap();
    assert_eq!(full_view.message_view_scope, MessageViewScope::Full);
    assert_eq!(
        *participant_view_bindings.begun.lock().await,
        vec![(
            "group-under-test".to_string(),
            "human_alice".to_string(),
        )]
    );
    assert_eq!(
        *participant_view_bindings.finished.lock().await,
        vec![(
            "group-under-test".to_string(),
            "human_alice".to_string(),
        )]
    );

    let forbidden = service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "member".to_string(),
            mode: bcs_service_api::ParticipantMode::Auto,
            message_view_scope: None,
        })
        .await
        .expect_err("non-self non-creator caller is forbidden");
    assert!(matches!(forbidden, GroupUseCaseError::Forbidden(_)));
}

// ---------------------------------------------------------------------------
// Leave-group / remove-member permission tests
// ---------------------------------------------------------------------------

#[tokio::test]
async fn bot_can_self_leave_group() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("bot_b".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_b".to_string(),
    }).await;
    assert!(result.is_ok(), "bot should be able to self-leave: {:?}", result.err());
}

#[tokio::test]
async fn human_owner_can_remove_their_bot_from_group() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", Some("alice"));
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("human_alice".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_b".to_string(),
    }).await;
    assert!(result.is_ok(), "owner should be able to remove their bot: {:?}", result.err());
}

#[tokio::test]
async fn driver_cannot_self_leave_group() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("bot_a".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_a".to_string(),
    }).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().to_string().contains("driver/coordinator"));
}

#[tokio::test]
async fn non_owner_cannot_remove_others_bot_from_group() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", Some("bob"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("human_alice".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_b".to_string(),
    }).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().to_string().contains("not authorized"));
}

#[tokio::test]
async fn coordinator_can_still_kick_members() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("bot_a".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_b".to_string(),
    }).await;
    assert!(result.is_ok(), "coordinator should still be able to kick: {:?}", result.err());
}
