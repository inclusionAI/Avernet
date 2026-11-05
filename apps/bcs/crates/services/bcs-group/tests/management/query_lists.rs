use super::*;

#[tokio::test]
async fn list_groups_orders_by_updated_at_desc_before_pagination() {
    let fixture = Fixture::new();
    let service = fixture.service_with_limits(5, 10, 10);

    for (group_id, updated_at) in [
        ("oldest-group", 10),
        ("newest-group", 30),
        ("middle-group", 20),
    ] {
        let mut group = Group::new(
            group_id,
            "driver",
            vec![bcs_service_api::Participant::bot(
                "driver",
                bcs_service_api::ParticipantRole::Driver,
            )],
        );
        group.updated_at = updated_at;
        fixture.group.upsert(group).await.unwrap();
    }

    let first_page = service
        .list_groups(GroupListCommand {
            group_kind: None,
            offset: 0,
            limit: 2,
            visibility: None,
            label: None,
        })
        .await
        .unwrap();
    assert_eq!(first_page.total, 3);
    assert_eq!(first_page.items[0].group_id, "newest-group");
    assert_eq!(first_page.items[1].group_id, "middle-group");

    let second_page = service
        .list_groups(GroupListCommand {
            group_kind: None,
            offset: 2,
            limit: 1,
            visibility: None,
            label: None,
        })
        .await
        .unwrap();
    assert_eq!(second_page.items[0].group_id, "oldest-group");
}

#[tokio::test]
async fn list_bot_groups_filters_absent_participant_groups() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut present = Group::new(
        "present-group",
        "driver",
        vec![
            bcs_service_api::Participant::bot(
                "driver",
                bcs_service_api::ParticipantRole::Driver,
            ),
            bcs_service_api::Participant::human(
                "human_alice",
                bcs_service_api::ParticipantRole::Observer,
            ),
        ],
    );
    present
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("human participant")
        .mode = Some(bcs_service_api::ParticipantMode::Present);
    fixture.group.upsert(present).await.unwrap();

    fixture
        .group
        .upsert(Group::new(
            "absent-group",
            "driver",
            vec![
                bcs_service_api::Participant::bot(
                    "driver",
                    bcs_service_api::ParticipantRole::Driver,
                ),
                bcs_service_api::Participant::human(
                    "human_alice",
                    bcs_service_api::ParticipantRole::Observer,
                ),
            ],
        ))
        .await
        .unwrap();

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "human_alice".to_string(),
            group_kind: None,
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();

    assert_eq!(bot_groups.total, 1);
    assert_eq!(bot_groups.items[0].group_id, "present-group");
}

#[tokio::test]
async fn list_bot_groups_orders_by_updated_at_desc_after_filtering() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    for (group_id, updated_at) in [("older-present", 10), ("newer-present", 30)] {
        let mut group = Group::new(
            group_id,
            "driver",
            vec![
                bcs_service_api::Participant::bot(
                    "driver",
                    bcs_service_api::ParticipantRole::Driver,
                ),
                bcs_service_api::Participant::human(
                    "human_alice",
                    bcs_service_api::ParticipantRole::Observer,
                ),
            ],
        );
        group.updated_at = updated_at;
        group
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == "human_alice")
            .expect("human participant")
            .mode = Some(bcs_service_api::ParticipantMode::Present);
        fixture.group.upsert(group).await.unwrap();
    }

    let mut absent = Group::new(
        "newest-absent",
        "driver",
        vec![
            bcs_service_api::Participant::bot(
                "driver",
                bcs_service_api::ParticipantRole::Driver,
            ),
            bcs_service_api::Participant::human(
                "human_alice",
                bcs_service_api::ParticipantRole::Observer,
            ),
        ],
    );
    absent.updated_at = 40;
    fixture.group.upsert(absent).await.unwrap();

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "human_alice".to_string(),
            group_kind: None,
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();

    assert_eq!(bot_groups.total, 2);
    assert_eq!(bot_groups.items[0].group_id, "newer-present");
    assert_eq!(bot_groups.items[1].group_id, "older-present");
}

#[tokio::test]
async fn list_bot_groups_filters_query_by_label_only() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    for (group_id, label, updated_at) in [
        ("label-match-newer", "05231022测试主子", 40),
        ("label-match-older", "Demo Worker测试05232205", 30),
        ("id-contains-05", "plain label", 50),
        ("miss", "1213123213", 20),
    ] {
        let mut group = Group::new(
            group_id,
            "driver",
            vec![
                bcs_service_api::Participant::bot(
                    "driver",
                    bcs_service_api::ParticipantRole::Driver,
                ),
                bcs_service_api::Participant::human(
                    "human_alice",
                    bcs_service_api::ParticipantRole::Observer,
                ),
            ],
        );
        group.label = Some(label.to_string());
        group.updated_at = updated_at;
        group
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == "human_alice")
            .expect("human participant")
            .mode = Some(bcs_service_api::ParticipantMode::Present);
        fixture.group.upsert(group).await.unwrap();
    }

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "human_alice".to_string(),
            group_kind: None,
            q: Some("05".to_string()),
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();

    assert_eq!(bot_groups.total, 2);
    assert_eq!(bot_groups.items[0].group_id, "label-match-newer");
    assert_eq!(bot_groups.items[1].group_id, "label-match-older");
}

#[tokio::test]
async fn list_bot_groups_returns_empty_when_actor_only_has_absent_groups() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    for group_id in ["absent-group-a", "absent-group-b"] {
        fixture
            .group
            .upsert(Group::new(
                group_id,
                "driver",
                vec![
                    bcs_service_api::Participant::bot(
                        "driver",
                        bcs_service_api::ParticipantRole::Driver,
                    ),
                    bcs_service_api::Participant::human(
                        "human_alice",
                        bcs_service_api::ParticipantRole::Observer,
                    ),
                ],
            ))
            .await
            .unwrap();
    }

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "human_alice".to_string(),
            group_kind: None,
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();

    assert_eq!(bot_groups.total, 0);
    assert!(bot_groups.items.is_empty());
}

#[tokio::test]
async fn list_bot_groups_keeps_bot_groups_with_absent_human_members() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    fixture
        .group
        .upsert(Group::new(
            "bot-auto-group",
            "driver",
            vec![
                bcs_service_api::Participant::bot(
                    "driver",
                    bcs_service_api::ParticipantRole::Driver,
                ),
                bcs_service_api::Participant::bot(
                    "helper",
                    bcs_service_api::ParticipantRole::Consultant,
                ),
                bcs_service_api::Participant::human(
                    "human_alice",
                    bcs_service_api::ParticipantRole::Observer,
                ),
            ],
        ))
        .await
        .unwrap();

    let mut muted = Group::new(
        "bot-muted-group",
        "driver",
        vec![
            bcs_service_api::Participant::bot(
                "driver",
                bcs_service_api::ParticipantRole::Driver,
            ),
            bcs_service_api::Participant::bot(
                "helper",
                bcs_service_api::ParticipantRole::Consultant,
            ),
            bcs_service_api::Participant::human(
                "human_alice",
                bcs_service_api::ParticipantRole::Observer,
            ),
        ],
    );
    muted
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "helper")
        .expect("helper participant")
        .mode = Some(bcs_service_api::ParticipantMode::Muted);
    fixture.group.upsert(muted).await.unwrap();

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "helper".to_string(),
            group_kind: None,
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();
    let ids = bot_groups
        .items
        .iter()
        .map(|item| item.group_id.as_str())
        .collect::<HashSet<_>>();

    assert_eq!(bot_groups.total, 2);
    assert_eq!(ids, HashSet::from(["bot-auto-group", "bot-muted-group"]));
}
