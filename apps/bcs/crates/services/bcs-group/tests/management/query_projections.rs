use super::*;

#[tokio::test]
async fn query_methods_list_detail_and_bot_groups() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);
    let created = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
            ],
        ))
        .await
        .unwrap();
    let listed = service
        .list_groups(GroupListCommand {
            group_kind: Some(Default::default()),
            offset: 0,
            limit: 10,
            visibility: None,
            label: None,
        })
        .await
        .unwrap();
    assert_eq!(listed.total, 1);
    assert_eq!(listed.items[0].group_id, created.group_id);
    assert_eq!(listed.items[0].participant_count, 2);

    let detail = service
        .get_group(GroupDetailCommand {
            group_id: created.group_id.clone(),
        })
        .await
        .unwrap();
    assert_eq!(detail.driver_bot_id, "driver");
    assert_eq!(detail.participants.len(), 2);

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "helper".to_string(),
            group_kind: Some(Default::default()),
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();
    assert_eq!(bot_groups.total, 1);
    assert_eq!(bot_groups.items[0].driver_bot_id, "driver");

}

// Step 1: legacy Group application projections expose the persisted
// HumanMentionNotifyMode on every Group surface — after create, after a
// legacy update_label (any update path), after a get detail reload, and on
// every list entry. The default is `All`; legacy updates that don't touch
// the notify mode leave it intact.
#[tokio::test]
async fn group_application_projections_expose_human_mention_notify_mode() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);
    let created = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
            ],
        ))
        .await
        .expect("create group");
    let group_id = created.group_id.clone();
    assert_eq!(
        created.human_mention_notify_mode,
        HumanMentionNotifyMode::All,
        "GroupDetailResult after create must expose the default notify mode"
    );

    // Legacy updates that do not touch the notify mode must preserve it.
    let updated = service
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: "driver".to_string(),
            group_id: group_id.clone(),
            label: Some("Renamed".to_string()),
        })
        .await
        .expect("update label");
    assert_eq!(updated.label.as_deref(), Some("Renamed"));
    assert_eq!(
        updated.human_mention_notify_mode,
        HumanMentionNotifyMode::All,
        "update_label projection must keep the unchanged notify mode"
    );

    let detail = service
        .get_group(GroupDetailCommand {
            group_id: group_id.clone(),
        })
        .await
        .expect("get group");
    assert_eq!(
        detail.human_mention_notify_mode,
        HumanMentionNotifyMode::All,
        "GroupDetailResult after reload must carry the stored notify mode"
    );

    let listed = service
        .list_groups(GroupListCommand {
            group_kind: Some(Default::default()),
            offset: 0,
            limit: 10,
            visibility: None,
            label: None,
        })
        .await
        .expect("list groups");
    assert_eq!(listed.total, 1);
    assert_eq!(
        listed.items[0].human_mention_notify_mode,
        HumanMentionNotifyMode::All,
        "GroupListEntry must expose the stored notify mode"
    );

    // Ensure the stored Group itself carries the field for downstream paths
    // (e.g. the message-flow gate) — proves the projection is backed by the
    // stored Group, not defaulted at projection time.
    let stored = fixture
        .group
        .get(&group_id)
        .await
        .expect("stored group");
    assert_eq!(
        stored.human_mention_notify_mode,
        HumanMentionNotifyMode::All
    );
}

// Step 1 Dm projection: the stored notify mode stays visible on a Dm Group
// projection even though Dm delivery semantics don't trigger external
// human-mention notifications — the field is visible-but-inactive for Dm
// Groups.
#[tokio::test]
async fn dm_projection_exposes_human_mention_notify_mode_for_visibility_only() {
    let fixture = Fixture::new().with_human("human_alice", "Alice").with_bot(
        "assistant",
        "Assistant",
        "public",
        Some("alice"),
    );
    let service = fixture.service_with_limits(5, 10, 10);

    let created = service
        .create_dm(DmCreateCommand {
            group_id: Some("dm-notify-mode".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: None,
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: Some("help".to_string()),
            context: Some("dm context".to_string()),
            provisioning: false,
        })
        .await
        .expect("owner human should create Human-Bot DM");

    assert_eq!(created.group.group_kind, GroupKind::Dm);
    assert_eq!(
        created.group.human_mention_notify_mode,
        HumanMentionNotifyMode::All,
        "DM Group projection must surface the stored notify mode even though \
         Dm human-mention delivery remains disabled"
    );

    let stored = fixture
        .group
        .get("dm-notify-mode")
        .await
        .expect("stored DM group");
    assert_eq!(stored.group_kind, GroupKind::Dm);
    assert_eq!(
        stored.human_mention_notify_mode,
        HumanMentionNotifyMode::All
    );
}
