use super::*;

#[tokio::test]
async fn human_originator_can_create_with_owned_protected_participant() {
    // Originator-anchored: a Human originator reaches a protected participant
    // via ownership (`created_by` matches the caller). The driver is public, so
    // the participant ownership gate is the binding check.
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_bot_owned_by("helper", "staff-1", "protected").await;

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: human_principal_with_profile("staff-1", "alice-login", Some("Alice"), None),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("Protected collaboration".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![
                    CreateParticipant {
                        actor_id: "human_staff-1".into(),
                        role: ParticipantRole::Observer,
                        tags: Vec::new(),
                        message_view_scope: None,
                    },
                    CreateParticipant {
                        actor_id: "helper".into(),
                        role: ParticipantRole::Consultant,
                        tags: Vec::new(),
                        message_view_scope: None,
                    },
                ],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await
        .expect("human originator reaches owned protected participant");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.originator_actor_id, "human_staff-1");
    let human = fixture
        .bots
        .get("human_staff-1")
        .await
        .expect("V1 normal Group creation must materialize the Human participant");
    assert_eq!(human.capabilities.name.as_deref(), Some("Alice"));
}
#[tokio::test]
async fn human_originator_rejects_non_owned_protected_participant() {
    // A protected participant the human originator does not own is not
    // reachable (human↔bot friendship is not consulted), so creation is
    // rejected. The driver is public, isolating the participant gate.
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_bot_owned_by("helper", "someone-else", "protected").await;

    let err = fixture
        .service
        .create(CreateGroup {
            caller: human_principal_with_profile("staff-1", "alice-login", Some("Alice"), None),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await
        .expect_err("protected participant not owned by human originator");
    assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
}
#[tokio::test]
async fn dm_create_uses_target_actor_id_and_projects_two_symmetric_participants() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("bot-a").await;
    fixture.add_public_bot("bot-b").await;

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("bot-a"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: Some("A and B".into()),
                context: None,
                target_actor_id: "bot-b".into(),
            }),
        })
        .await
        .expect("create DM");

    let GroupDetail::DirectMessage(detail) = detail else {
        panic!("expected DM detail");
    };
    assert_eq!(detail.participants.len(), 2);
    assert!(
        detail
            .participants
            .iter()
            .any(|p| p.actor_id == "human_bot-a")
    );
    assert!(detail.participants.iter().any(|p| p.actor_id == "bot-b"));
    let json = serde_json::to_value(&detail).expect("serialize DM detail");
    assert!(json.get("driver_bot_uuid").is_none());
    assert!(json.get("strategy").is_none());
    assert!(json.get("dm_pair_key").is_none());
}
#[tokio::test]
async fn dm_kind_rejects_a_strategy_filter() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("target").await;

    let result = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::Dm,
            strategy: Some(V1GroupStrategy::Chat),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_request"
    ));
}
#[tokio::test]
async fn explicit_view_requires_the_target_bot_to_exist_and_be_owned() {
    let fixture = Fixture::new().await;

    let result = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("missing"),
            view_bot_id: Some("missing".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::Normal,
            strategy: None,
        })
        .await;

    assert!(matches!(result, Err(ApplicationError::Forbidden(_))));
}
#[tokio::test]
async fn explicit_view_propagates_registry_database_failure() {
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::new(Arc::new(FailingDb)),
    )));
    let fixture = Fixture::new_with_bots(bots).await;

    let result = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("stored-bot"),
            view_bot_id: Some("stored-bot".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::Normal,
            strategy: None,
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("bot database unavailable")
    ));
}
#[tokio::test]
async fn create_group_propagates_quota_lookup_database_failure() {
    let fixture = Fixture::new_with_failing_group_store().await;
    fixture.add_public_bot("driver").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                driver_bot_uuid: "driver".into(),
                name: Some("quota lookup failure".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                participants: Vec::new(),
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("find Groups for participant")
    ));
}
#[tokio::test]
async fn create_group_propagates_non_driver_registry_database_failure() {
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::new(Arc::new(DriverThenFailingDb::default())),
    )));
    let fixture = Fixture::new_with_bots(bots).await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                driver_bot_uuid: "driver".into(),
                name: Some("registry failure".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("participant registry unavailable")
    ));
}
#[tokio::test]
async fn bot_dm_propagates_caller_registry_failure_after_target_validation() {
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::new(Arc::new(DriverThenFailingDb::default())),
    )));
    let fixture = Fixture::new_with_bots(bots).await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("caller"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "target".into(),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("participant registry unavailable")
    ));
}
#[tokio::test]
async fn bot_dm_propagates_friendship_failure_after_initial_validation() {
    let friends = Arc::new(FriendCore::with_repo(Arc::new(
        FirstFriendCheckThenFailingRepo::default(),
    )));
    let fixture = Fixture::new_with_friends(friends).await;
    fixture.add_public_bot("caller").await;
    fixture.add_protected_bot("target").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("caller"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "target".into(),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("friend store unavailable after validation")
    ));
}
#[tokio::test]
async fn create_rejects_non_bot_driver_and_dm_target_with_declared_code() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("requester").await;
    fixture
        .bots
        .ensure_human_actor("staff-1", "Alice")
        .await
        .expect("register Human actor");

    let driver = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("requester"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "human_staff-1".into(),
                participants: Vec::new(),
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await;
    assert!(matches!(
        driver,
        Err(ApplicationError::InvalidInput { code, message })
            if code == "invalid_participant"
                && message.contains("driver_bot_uuid")
    ));

    let target = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("requester"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "human_staff-1".into(),
            }),
        })
        .await;
    assert!(matches!(
        target,
        Err(ApplicationError::InvalidInput { code, message })
            if code == "invalid_participant"
                && message.contains("target_actor_id")
    ));
}
