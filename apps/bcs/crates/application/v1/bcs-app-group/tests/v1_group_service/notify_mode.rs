use super::*;

#[tokio::test]
async fn update_round_trips_human_mention_notify_mode_for_normal_groups() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("caller-bot").await;
    fixture.add_public_bot("driver").await;
    let mut command = collaboration_create_command();
    command.caller = authenticated_bot_principal("caller-bot", "owner-1");
    if let CreateGroupSpec::Collaboration(create) = &mut command.group {
        create.participants.push(CreateParticipant {
            actor_id: "caller-bot".into(),
            role: ParticipantRole::Consultant,
            tags: Vec::new(),
            message_view_scope: None,
        });
    }
    let created = fixture
        .service
        .create(command)
        .await
        .expect("create Group");
    let group_id = detail_group_id(&created).to_string();

    assert_eq!(
        detail_notify_mode(&created),
        bcs_service_api::HumanMentionNotifyMode::All
    );

    let updated = fixture
        .service
        .update(UpdateGroup {
            caller: authenticated_bot_principal("caller-bot", "owner-1"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                human_mention_notify_mode: Some(
                    bcs_service_api::HumanMentionNotifyMode::DriverBotOnly,
                ),
                ..Default::default()
            },
        })
        .await
        .expect("update notify mode");
    assert_eq!(
        detail_notify_mode(&updated),
        bcs_service_api::HumanMentionNotifyMode::DriverBotOnly
    );

    let reloaded = fixture
        .service
        .get(GetGroup {
            caller: authenticated_bot_principal("caller-bot", "owner-1"),
            group_id: group_id.clone(),
        })
        .await
        .expect("reload Group");
    assert_eq!(
        detail_notify_mode(&reloaded),
        bcs_service_api::HumanMentionNotifyMode::DriverBotOnly
    );

    let listed = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("caller-bot"),
            view_bot_id: Some("caller-bot".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list Groups");
    assert_eq!(listed.items.len(), 1);
    assert_eq!(
        summary_notify_mode(&listed.items[0]),
        bcs_service_api::HumanMentionNotifyMode::DriverBotOnly
    );
}
#[tokio::test]
async fn update_round_trips_human_mention_notify_mode_for_dm_groups() {
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
    let group_id = detail_group_id(&detail).to_string();

    let updated = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("bot-a"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                human_mention_notify_mode: Some(
                    bcs_service_api::HumanMentionNotifyMode::DriverBotOnly,
                ),
                ..Default::default()
            },
        })
        .await
        .expect("update DM notify mode");
    let GroupDetail::DirectMessage(updated) = updated else {
        panic!("expected DM detail");
    };
    assert_eq!(
        updated.human_mention_notify_mode,
        bcs_service_api::HumanMentionNotifyMode::DriverBotOnly
    );

    let reloaded = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("bot-a"),
            group_id,
        })
        .await
        .expect("reload DM");
    let GroupDetail::DirectMessage(reloaded) = reloaded else {
        panic!("expected DM detail");
    };
    assert_eq!(
        reloaded.human_mention_notify_mode,
        bcs_service_api::HumanMentionNotifyMode::DriverBotOnly
    );
}
// Step 3: patching only `human_mention_notify_mode` must leave every other
// mutable field byte-identical. The Group is seeded with a non-default label,
// context, visibility, and routing policy.
#[tokio::test]
async fn update_notify_mode_patch_preserves_other_mutable_fields() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }
    let mut sender_routes = HashMap::new();
    sender_routes.insert("driver".to_string(), vec!["helper".to_string()]);
    let mut group = normal_group(
        "notify-isolation",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("helper", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        1,
    );
    group.label = Some("ops".to_string());
    group.context = Some("incident-room".to_string());
    group.visibility = "private".to_string();
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
            group_id: "notify-isolation".into(),
            patch: GroupPatch {
                human_mention_notify_mode: Some(
                    bcs_service_api::HumanMentionNotifyMode::None,
                ),
                ..Default::default()
            },
        })
        .await
        .expect("notify-mode-only patch");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.version, original_version + 1);
    assert_eq!(
        detail.human_mention_notify_mode,
        bcs_service_api::HumanMentionNotifyMode::None
    );
    // Old clients' mutable fields must remain identical to the seeded values.
    assert_eq!(detail.name.as_deref(), Some("ops"));
    assert_eq!(detail.context.as_deref(), Some("incident-room"));
    assert_eq!(detail.visibility, GroupVisibility::Private);
    let CollaborationConfiguration::Chat(chat) = &detail.collaboration else {
        panic!("expected chat collaboration on the patched Group");
    };
    assert_eq!(
        chat.delivery_policy.bot_final_delivery,
        BotFinalDelivery::SendToDriver
    );

    let stored = fixture
        .groups
        .get("notify-isolation")
        .await
        .expect("stored group");
    assert_eq!(stored.version, original_version + 1);
    assert_eq!(
        stored.human_mention_notify_mode,
        bcs_service_api::HumanMentionNotifyMode::None
    );
    assert_eq!(stored.label.as_deref(), Some("ops"));
    assert_eq!(stored.context.as_deref(), Some("incident-room"));
    assert_eq!(stored.visibility, "private");
    let stored_policy = stored.routing_policy.expect("stored routing policy");
    assert_eq!(stored_policy.mode, RoutingMode::Structured);
    assert_eq!(
        stored_policy.default_bot_final_delivery,
        DefaultDelivery::SendToDriver
    );
    assert_eq!(stored_policy.sender_routes, sender_routes);
}
// Step 3 inverse: patching a normal field must not reset the notify mode.The
// Group is seeded with a non-default mode; a `name`-only patch leaves it in place.
#[tokio::test]
async fn update_other_field_patch_preserves_notify_mode() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }
    let mut group = normal_group(
        "notify-inverse",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("helper", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        1,
    );
    group.human_mention_notify_mode =
        bcs_service_api::HumanMentionNotifyMode::DriverBotOnly;
    let original_version = group.version;
    fixture.groups.upsert(group).await.expect("store group");

    let detail = fixture
        .service
        .update(UpdateGroup {
            caller: bot_principal("driver"),
            group_id: "notify-inverse".into(),
            patch: GroupPatch {
                name: Some("Renamed".into()),
                ..Default::default()
            },
        })
        .await
        .expect("name-only patch");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.version, original_version + 1);
    assert_eq!(detail.name.as_deref(), Some("Renamed"));
    assert_eq!(
        detail.human_mention_notify_mode,
        bcs_service_api::HumanMentionNotifyMode::DriverBotOnly,
        "name patch must not reset the notify mode"
    );

    let stored = fixture
        .groups
        .get("notify-inverse")
        .await
        .expect("stored group");
    assert_eq!(stored.label.as_deref(), Some("Renamed"));
    assert_eq!(
        stored.human_mention_notify_mode,
        bcs_service_api::HumanMentionNotifyMode::DriverBotOnly
    );
}
