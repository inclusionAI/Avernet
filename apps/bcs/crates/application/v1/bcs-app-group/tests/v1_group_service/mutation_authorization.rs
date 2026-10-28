use super::*;

#[tokio::test]
async fn update_preserves_hidden_legacy_routing_fields() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }
    let mut sender_routes = HashMap::new();
    sender_routes.insert("helper".into(), vec!["driver".into()]);
    let mut group = normal_group(
        "group-1",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("helper", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        1,
    );
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: sender_routes.clone(),
    });
    let original_version = group.version;
    fixture.groups.upsert(group).await.expect("store group");

    let detail = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: "group-1".into(),
            patch: GroupPatch {
                name: Some("Renamed".into()),
                context: Some("测试1".into()),
                delivery_policy: Some(GroupDeliveryPolicy {
                    bot_final_delivery: BotFinalDelivery::InjectObservers,
                }),
                ..Default::default()
            },
        })
        .await
        .expect("update");

    let stored = fixture.groups.get("group-1").await.expect("stored group");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.updated_at, stored.updated_at);
    assert_eq!(stored.version, original_version + 1);
    assert_eq!(detail.version, original_version + 1);
    let policy = stored.routing_policy.expect("routing policy");
    assert_eq!(policy.mode, RoutingMode::Structured);
    assert_eq!(policy.sender_routes, sender_routes);
    assert_eq!(
        policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(stored.label.as_deref(), Some("Renamed"));
    assert_eq!(stored.context.as_deref(), Some("测试1"));
    assert_eq!(detail.context.as_deref(), Some("测试1"));
}
#[tokio::test]
async fn get_requires_a_group_relation_and_delete_is_idempotent() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "outsider"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .groups
        .upsert(normal_group(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store group");

    let denied = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("outsider"),
            group_id: "group-1".into(),
        })
        .await;
    assert!(matches!(denied, Err(ApplicationError::Forbidden(_))));

    let first = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect("first delete");
    assert!(first.deleted);

    let second = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect("second delete");
    assert!(!second.deleted);
}
#[tokio::test]
async fn tenant_metadata_does_not_restrict_bot_collaboration() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker"] {
        fixture.add_public_bot(bot).await;
    }

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal_in_tenant("driver", "tenant-b"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("Cross-tenant collaboration".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
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
        .expect("tenant metadata must not block collaboration");

    assert!(matches!(detail, GroupDetail::Collaboration(_)));
}
#[tokio::test]
async fn human_originator_can_update_and_delete_group() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "manager", "worker"] {
        fixture.add_public_bot(bot).await;
    }
    let mut group = normal_group(
        "group-1",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Worker),
            Participant::bot("manager", ParticipantRole::Manager),
            Participant::bot("worker", ParticipantRole::Worker),
        ],
        GroupStrategy::ManagerWorker,
        1,
    );
    group.originator = Some("human_manager".into());
    fixture.groups.upsert(group).await.expect("store group");

    fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("manager"),
            group_id: "group-1".into(),
            patch: GroupPatch {
                name: Some("Managed".into()),
                ..Default::default()
            },
        })
        .await
        .expect("manager may update");

    let deleted = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("manager"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect("manager may delete");
    assert!(deleted.deleted);
}
#[tokio::test]
async fn delete_group_unauthorized_principal_forbidden() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "outsider"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .groups
        .upsert(normal_group(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store group");

    // An unauthorized principal must not receive the idempotent
    // `deleted:false` mask when the group exists; the facade rejects the
    // call with 403 forbidden instead. Idempotent `deleted:false` is reserved
    // for an authorized caller whose target is already absent.
    let err = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("outsider"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect_err("non-manager delete must be forbidden, not idempotent");
    assert!(
        matches!(err, ApplicationError::Forbidden(_)),
        "expected forbidden, got {err:?}"
    );
}
#[tokio::test]
async fn state_machine_patch_failure_does_not_commit_requested_changes() {
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
                name: Some("Before".into()),
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
        .expect("create state-machine group");
    let group_id = runtime
        .configured
        .lock()
        .expect("runtime lock")
        .as_ref()
        .expect("configured runtime")
        .group_id
        .clone();
    *runtime
        .projection_error
        .lock()
        .expect("projection error lock") = Some("runtime unavailable".into());

    let result = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("After".into()),
                ..Default::default()
            },
        })
        .await;
    assert!(matches!(result, Err(ApplicationError::InvalidInput { .. })));
    assert_eq!(
        fixture
            .groups
            .get(&group_id)
            .await
            .expect("stored group")
            .label
            .as_deref(),
        Some("Before")
    );
}
#[tokio::test]
async fn create_gates_driver_against_bot_originator_by_friendship() {
    // Originator-anchored: a Bot originator may drive only a public bot or a
    // bot it is friends with. Here the protected driver is neither, so the
    // driver↔originator gate rejects creation (the driver is no longer exempt
    // from the originator reachability check).
    let fixture = Fixture::new().await;
    fixture.add_public_bot("requester").await;
    fixture.add_protected_bot("driver").await;

    let err = fixture
        .service
        .create(CreateGroup {
            caller: authenticated_bot_principal("requester", "requester"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: None,
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: Vec::new(),
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await
        .expect_err("protected driver not reachable from bot originator");
    assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
}
#[tokio::test]
async fn bot_originator_rejects_non_friend_protected_participant() {
    // A Bot originator reaches a protected participant only via friendship.
    // The public driver is reachable, isolating the participant gate.
    let fixture = Fixture::new().await;
    fixture.add_public_bot("requester").await;
    fixture.add_public_bot("driver").await;
    fixture.add_protected_bot("helper").await;

    let err = fixture
        .service
        .create(CreateGroup {
            caller: authenticated_bot_principal("requester", "requester"),
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
        .expect_err("protected participant not reachable from bot originator");
    assert!(matches!(err, ApplicationError::Forbidden { .. }), "got {err:?}");
}
#[tokio::test]
async fn create_propagates_protected_participant_friendship_lookup_failure() {
    let friends = Arc::new(FriendCore::with_repo(Arc::new(FailingFriendRepo)));
    let fixture = Fixture::new_with_friends(friends).await;
    fixture.add_public_bot("driver").await;
    fixture.add_protected_bot("helper").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: authenticated_bot_principal("driver", "driver"),
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
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::Internal(message))
            if message.contains("friend store unavailable")
    ));
}
