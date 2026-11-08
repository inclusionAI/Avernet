use super::*;

#[tokio::test]
async fn update_rejects_delivery_policy_for_non_chat_strategy() {
    let fixture = Fixture::new().await;
    for bot in ["manager", "worker"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .groups
        .upsert(normal_group(
            "manager-worker",
            "manager",
            vec![
                Participant::bot("manager", ParticipantRole::Manager),
                Participant::bot("worker", ParticipantRole::Worker),
            ],
            GroupStrategy::ManagerWorker,
            1,
        ))
        .await
        .expect("store ManagerWorker Group");

    let result = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("manager"),
            group_id: "manager-worker".into(),
            patch: GroupPatch {
                delivery_policy: Some(GroupDeliveryPolicy {
                    bot_final_delivery: BotFinalDelivery::InjectObservers,
                }),
                ..Default::default()
            },
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_request"
    ));
}
#[tokio::test]
async fn update_opening_message_preserves_patch_states_and_strategy_scopes() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("StateMachine".into()),
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
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create StateMachine Group");
    let group_id = runtime
        .configured
        .lock()
        .expect("runtime lock")
        .as_ref()
        .expect("configured runtime")
        .group_id
        .clone();

    let configured = OpeningMessage::Text("Run {{bcs.run_id}}".to_string());
    fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                opening_message: Some(Some(configured.clone())),
                ..Default::default()
            },
        })
        .await
        .expect("configure opening message");
    assert_eq!(
        fixture
            .groups
            .get(&group_id)
            .await
            .expect("stored StateMachine Group")
            .opening_message,
        Some(configured.clone())
    );

    fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("Renamed".into()),
                ..Default::default()
            },
        })
        .await
        .expect("omit opening-message patch");
    assert_eq!(
        fixture
            .groups
            .get(&group_id)
            .await
            .expect("stored StateMachine Group")
            .opening_message,
        Some(configured)
    );

    fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                opening_message: Some(None),
                ..Default::default()
            },
        })
        .await
        .expect("clear opening message");
    assert_eq!(
        fixture
            .groups
            .get(&group_id)
            .await
            .expect("stored StateMachine Group")
            .opening_message,
        None
    );

    let mut chat = normal_group(
        "chat",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
        GroupStrategy::Chat,
        1,
    );
    chat.opening_message = None;
    fixture.groups.upsert(chat).await.expect("store Chat Group");
    fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: "chat".into(),
            patch: GroupPatch {
                opening_message: Some(Some(OpeningMessage::Text("hello".into()))),
                ..Default::default()
            },
        })
        .await
        .expect("Chat Group accepts session-scoped opening message");
    assert_eq!(
        fixture
            .groups
            .get("chat")
            .await
            .expect("stored Chat Group")
            .opening_message,
        Some(OpeningMessage::Text("hello".into()))
    );

    let error = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: "chat".into(),
            patch: GroupPatch {
                opening_message: Some(Some(OpeningMessage::Text(
                    "Run {{bcs.run_id}}".into(),
                ))),
                ..Default::default()
            },
        })
        .await
        .expect_err("Chat Group must reject run-scoped variables");
    assert!(matches!(
        error,
        ApplicationError::InvalidInput { code, .. } if code == "invalid_opening_message"
    ));
}
#[tokio::test]
async fn create_rejects_duplicate_participant_actor_ids() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![
                    CreateParticipant {
                        actor_id: "helper".into(),
                        role: ParticipantRole::Consultant,
                        tags: Vec::new(),
                        message_view_scope: None,
                    },
                    CreateParticipant {
                        actor_id: "helper".into(),
                        role: ParticipantRole::Observer,
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
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_participant"
    ));
}
#[tokio::test]
async fn create_rejects_roles_that_do_not_match_the_strategy_lead() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "manager", "worker"] {
        fixture.add_public_bot(bot).await;
    }

    let invalid_chat = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "manager".into(),
                    role: ParticipantRole::Manager,
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
        invalid_chat,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_participant"
    ));

    let invalid_manager_worker = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![
                    CreateParticipant {
                        actor_id: "manager".into(),
                        role: ParticipantRole::Manager,
                        tags: Vec::new(),
                        message_view_scope: None,
                    },
                    CreateParticipant {
                        actor_id: "worker".into(),
                        role: ParticipantRole::Worker,
                        tags: Vec::new(),
                        message_view_scope: None,
                    },
                ],
                collaboration: CollaborationConfiguration::ManagerWorker(Default::default()),
            }),
        })
        .await;
    assert!(matches!(
        invalid_manager_worker,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_participant"
    ));
}
#[tokio::test]
async fn human_principal_creates_legacy_actor_with_current_display_name_priority() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("bot-b").await;

    for (staff_no, username, display_name, full_name, expected_name) in [
        (
            "staff-display",
            "alice-login",
            Some("Alice Display"),
            Some("Alice Full"),
            "Alice Display",
        ),
        (
            "staff-full",
            "bob-login",
            None,
            Some("Bob Full"),
            "Bob Full",
        ),
        ("staff-login", "carol-login", None, None, "carol-login"),
    ] {
        let detail = fixture
            .service
            .create(CreateGroup {
                caller: human_principal_with_profile(staff_no, username, display_name, full_name),
                group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                    name: Some("Human and B".into()),
                    context: None,
                    target_actor_id: "bot-b".into(),
                }),
            })
            .await
            .expect("canonical Human actor creates DM");

        let GroupDetail::DirectMessage(detail) = detail else {
            panic!("expected DM detail");
        };
        let actor_id = format!("human_{staff_no}");
        assert!(
            detail
                .participants
                .iter()
                .any(|participant| participant.actor_id == actor_id)
        );
        let human = fixture
            .bots
            .get(&actor_id)
            .await
            .expect("V1 must materialize the legacy Human Actor");
        assert_eq!(human.capabilities.name.as_deref(), Some(expected_name));
        assert_eq!(human.created_by.as_deref(), Some(staff_no));
    }
}
#[tokio::test]
async fn human_principal_preserves_existing_legacy_actor_display_name() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("bot-b").await;
    fixture
        .bots
        .ensure_human_actor("staff-1", "Original Name")
        .await
        .expect("register existing Human actor");

    fixture
        .service
        .create(CreateGroup {
            caller: human_principal_with_profile(
                "staff-1",
                "alice-login",
                Some("Changed Name"),
                None,
            ),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: Some("Human and B".into()),
                context: None,
                target_actor_id: "bot-b".into(),
            }),
        })
        .await
        .expect("existing Human actor creates DM");

    let human = fixture
        .bots
        .get("human_staff-1")
        .await
        .expect("existing Human actor remains registered");
    assert_eq!(human.capabilities.name.as_deref(), Some("Original Name"));
}
#[tokio::test]
async fn dm_create_outcome_reports_when_the_existing_pair_is_reused() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("bot-a").await;
    fixture.add_public_bot("bot-b").await;
    let command = || CreateGroup {
        caller: bot_principal("bot-a"),
        group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
            name: Some("A and B".into()),
            context: Some("original context".into()),
            target_actor_id: "bot-b".into(),
        }),
    };

    let first = fixture
        .service
        .create_with_outcome(command())
        .await
        .expect("create first DM");
    let reused = fixture
        .service
        .create_with_outcome(command())
        .await
        .expect("reuse existing DM");

    assert!(first.created);
    assert!(!reused.created);
    let GroupDetail::DirectMessage(first) = first.group else {
        panic!("expected first DM detail");
    };
    let GroupDetail::DirectMessage(reused) = reused.group else {
        panic!("expected reused DM detail");
    };
    assert_eq!(reused.group_id, first.group_id);
}
#[tokio::test]
async fn client_caused_group_errors_map_to_documented_4xx_classes() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_bot_owned_by("protected", "driver", "protected").await;

    let public_with_protected = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Public,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "protected".into(),
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
        public_with_protected,
        Err(ApplicationError::Conflict { code, .. }) if code == "non_public_participant"
    ));
}
#[tokio::test]
async fn human_caller_does_not_require_a_same_named_bot() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("target").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("missing-caller"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "target".into(),
            }),
        })
        .await;

    assert!(result.is_ok());
}
#[tokio::test]
async fn deleting_dm_maps_the_legacy_rejection_to_contract_conflict() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("peer").await;
    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: None,
                context: None,
                target_actor_id: "peer".into(),
            }),
        })
        .await
        .expect("create DM");
    let GroupDetail::DirectMessage(detail) = detail else {
        panic!("expected DM detail");
    };

    let result = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: detail.group_id,
            acting_bot_id: None,
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Conflict { code, .. }) if code == "conflict"
    ));
}
#[tokio::test]
async fn session_only_nonmember_dm_summary_omits_peer_actor() {
    let fixture = Fixture::new().await;
    for bot in ["bot-a", "bot-b", "target"] {
        fixture.add_public_bot(bot).await;
    }
    let (group, _) = fixture
        .groups
        .create_or_reuse_actor_dm_group(
            "dm-1",
            bcs_service_api::DmActorSpec {
                actor_id: "bot-a".into(),
                actor_kind: bcs_service_api::ActorKind::Bot,
                display_name: Some("bot-a".into()),
            },
            bcs_service_api::DmActorSpec {
                actor_id: "bot-b".into(),
                actor_kind: bcs_service_api::ActorKind::Bot,
                display_name: Some("bot-b".into()),
            },
            "bot-a",
            "bot-a",
            None,
            None,
        )
        .await
        .expect("create DM");
    fixture
        .sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id,
            session_id: None,
            params: NewSessionParams {
                participants: vec![Participant::bot("target", ParticipantRole::Consultant)],
                ..Default::default()
            },
        })
        .await
        .expect("create session");

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: None,
            membership: MembershipFilter::SessionOnly,
            kind: GroupKindFilter::Dm,
            strategy: None,
        })
        .await
        .expect("list related DMs");
    let GroupSummary::DirectMessage(summary) = &page.items[0] else {
        panic!("expected DM summary");
    };
    assert!(summary.peer_actor.is_none());
}
