//! V1 participant avatars and lightweight list summaries (bot-avatar plan Task 6).
//!
//! Design §5.1–§5.3 (`docs/specs/2026-11-02-bot-avatar-collaboration-repair/design.md`):
//! - List summaries gain a `participants` array whose `name` is the stored
//!   membership snapshot verbatim (null allowed, never backfilled) and whose
//!   `avatar_url` comes from ONE batched display-profile read per result page.
//! - Group detail participants keep all control fields and gain `avatar_url`
//!   from one batched read for that Group; DM `peer_actor` gains the same
//!   avatar field.
//! - Human participants always resolve `avatar_url: null`; tombstoned Bots
//!   keep their stored face; missing identities resolve null, and avatar read
//!   failures surface as service errors instead of an all-null page.

use super::*;
use bcs_service_api::application::v1::{AddGroupParticipant, Page, UpdateGroupParticipant};
use bcs_service_api::types::bot_profile::{AvatarChange, ProfileWrite};
use bcs_service_api::{GroupKind, ParticipantMode};
use serde_json::{Value, json};

/// A `BotCore` whose stored-profile records (the avatar writes below) live in
/// a test-scoped temp directory instead of the crate working directory.
fn avatar_backed_core() -> (tempfile::TempDir, Arc<BotCore>) {
    let dir = tempfile::tempdir().expect("temp bots base dir");
    let bots = Arc::new(BotCore::with_base_dir(dir.path().to_path_buf()));
    (dir, bots)
}

/// Seed one Bot's avatar through the Task 4/5 write contract.
async fn seed_avatar(bots: &BotCore, bot_id: &str, url: &str) {
    assert_eq!(
        bots.update_avatar_existing(bot_id, AvatarChange::Set(url.to_string()))
            .await
            .expect("seed avatar"),
        ProfileWrite::Updated
    );
}

/// The lightweight list-summary row shape (design §5.2): identity plus the
/// two nullable display fields, and none of the detail-only control fields.
fn assert_participant_shape(value: &Value) {
    let row = &value["participants"][0];
    assert!(row.get("actor_id").is_some());
    assert!(row.get("actor_kind").is_some());
    assert!(row.get("name").is_some());
    assert!(row.get("avatar_url").is_some());
    assert!(row.get("role").is_none());
    assert!(row.get("mode").is_none());
    assert!(row.get("message_view_scope").is_none());
    assert!(row.get("tags").is_none());
}

/// A list-summary row's `name` is the stored membership snapshot verbatim —
/// `snapshot` (as null when the member row has no name), never the registry's
/// current name.
fn assert_group_list_name_snapshot(value: &Value, actor: &str, snapshot: Option<&str>) {
    let row = value["participants"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["actor_id"] == actor)
        .unwrap();
    let expected = snapshot
        .map(Value::from)
        .unwrap_or(Value::Null);
    assert_eq!(row["name"], expected);
}

fn find_summary_json<'a>(
    page: &'a Page<GroupSummary>,
    group_id: &str,
) -> (&'a GroupSummary, Value) {
    let summary = page
        .items
        .iter()
        .find(|item| match item {
            GroupSummary::Normal(summary) => summary.group_id == group_id,
            GroupSummary::DirectMessage(summary) => summary.group_id == group_id,
        })
        .expect("summary for group");
    let value = serde_json::to_value(summary).expect("serialize summary");
    (summary, value)
}

fn participant_row<'a>(value: &'a Value, actor: &str) -> &'a Value {
    value["participants"]
        .as_array()
        .expect("participants array")
        .iter()
        .find(|item| item["actor_id"] == actor)
        .expect("participant row")
}

fn list_command(viewer: &str) -> ListGroups {
    ListGroups {
        caller: bot_principal(viewer),
        view_bot_id: Some(viewer.into()),
        offset: 0,
        limit: 50,
        q: None,
        visibility: None,
        membership: MembershipFilter::All,
        kind: GroupKindFilter::All,
        strategy: None,
    }
}

/// Register a public Bot whose REGISTRY name differs from every membership
/// snapshot, so a snapshot/backfill mix-up cannot silently pass.
async fn add_public_bot_named_current_profile(fixture: &Fixture, bot_uuid: &str) {
    let capabilities = BotCapabilities {
        name: Some("current-name".to_string()),
        visibility: "public".into(),
        ..Default::default()
    };
    fixture
        .bots
        .register(bot_uuid.to_string(), capabilities)
        .await
        .expect("register bot");
    fixture
        .bots
        .save_created_by(bot_uuid, bot_uuid, true)
        .await
        .expect("assign test Bot owner");
    bcs_test_support::grant_owner_edge(&fixture.authority_repo, bot_uuid, bot_uuid).await;
}

// ---------------------------------------------------------------------------
// Avatar follows the CURRENT Bot profile (list, detail, DM, clear-to-null)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn group_avatars_follow_current_profile() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["viewer", "avatar-driver", "avatar-peer"] {
        fixture.add_public_bot(bot).await;
    }
    // A Human participant keeps `avatar_url: null` by contract.
    fixture
        .groups
        .upsert(normal_group(
            "avatar-group",
            "avatar-driver",
            vec![
                Participant::bot("avatar-driver", ParticipantRole::Driver),
                Participant::bot("avatar-peer", ParticipantRole::Consultant),
                Participant::bot("viewer", ParticipantRole::Consultant),
                Participant::human("human_colleague", ParticipantRole::Observer),
            ],
            GroupStrategy::Chat,
            30,
        ))
        .await
        .expect("store group");

    seed_avatar(&bots, "avatar-driver", "https://cdn.example.com/driver.png").await;
    seed_avatar(&bots, "avatar-peer", "https://cdn.example.com/peer.png").await;

    // The DM is created by a canonical Human Actor, so its members are
    // `human_staff-viewer` and the Bot peer.
    let dm = fixture
        .service
        .create(CreateGroup {
            caller: human_principal_with_profile("staff-viewer", "staff-viewer", None, None),
            group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                name: Some("Viewer and Peer".into()),
                context: None,
                target_actor_id: "avatar-peer".into(),
            }),
        })
        .await
        .expect("create DM group");
    let GroupDetail::DirectMessage(dm_detail) = dm else {
        panic!("expected the created DM detail");
    };
    let dm_group_id = dm_detail.group_id.clone();
    let dm_value = serde_json::to_value(&dm_detail).expect("serialize DM detail");
    for actor in ["human_staff-viewer", "avatar-peer"] {
        assert!(
            dm_value["participants"]
                .as_array()
                .expect("DM detail participants")
                .iter()
                .any(|row| row["actor_id"] == actor),
            "DM detail keeps the {actor} participant"
        );
    }
    assert_eq!(
        participant_row(&dm_value, "avatar-peer")["avatar_url"],
        json!("https://cdn.example.com/peer.png")
    );

    let page = fixture
        .service
        .list_groups(list_command("viewer"))
        .await
        .expect("list groups");
    assert_eq!(page.total, 1);

    let (normal_summary, normal_value) = find_summary_json(&page, "avatar-group");
    let GroupSummary::Normal(_summary) = normal_summary else {
        panic!("expected a normal summary");
    };
    assert_participant_shape(&normal_value);
    assert_eq!(
        normal_value["participants"].as_array().expect("rows").len(),
        4
    );
    assert_eq!(
        participant_row(&normal_value, "avatar-driver")["avatar_url"],
        json!("https://cdn.example.com/driver.png")
    );
    assert_eq!(
        participant_row(&normal_value, "avatar-peer")["avatar_url"],
        json!("https://cdn.example.com/peer.png")
    );
    assert_eq!(
        participant_row(&normal_value, "viewer")["avatar_url"],
        json!(null)
    );
    assert_eq!(
        participant_row(&normal_value, "human_colleague")["actor_kind"],
        json!("human")
    );
    assert_eq!(
        participant_row(&normal_value, "human_colleague")["avatar_url"],
        json!(null)
    );

    // The DM summary is only visible to the Human view Actor that owns it:
    // the same list contract, viewed as the direct Human participant.
    let dm_page = fixture
        .service
        .list_groups(ListGroups {
            caller: human_principal_with_profile("staff-viewer", "staff-viewer", None, None),
            view_bot_id: None,
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list groups as the DM Human");
    let GroupSummary::DirectMessage(dm_summary) =
        dm_page
            .items
            .iter()
            .find(|item| matches!(item, GroupSummary::DirectMessage(_)))
            .expect("DM summary in page")
    else {
        unreachable!()
    };
    let dm_list_value = serde_json::to_value(dm_summary).expect("serialize DM summary");
    assert_participant_shape(&dm_list_value);
    assert_eq!(
        dm_list_value["participants"].as_array().expect("rows").len(),
        2
    );
    assert_eq!(
        participant_row(&dm_list_value, "avatar-peer")["avatar_url"],
        json!("https://cdn.example.com/peer.png")
    );
    assert_eq!(dm_summary.peer_actor.as_ref().expect("peer actor").actor_id, "avatar-peer");
    assert_eq!(
        dm_list_value["peer_actor"]["avatar_url"],
        json!("https://cdn.example.com/peer.png")
    );

    let dm_detail = fixture
        .service
        .get(GetGroup {
            caller: human_principal_with_profile("staff-viewer", "staff-viewer", None, None),
            group_id: dm_group_id.clone(),
        })
        .await
        .expect("read DM detail");
    let dm_detail_value = serde_json::to_value(&dm_detail).expect("serialize DM detail");
    assert_eq!(
        participant_row(&dm_detail_value, "human_staff-viewer")["avatar_url"],
        json!(null)
    );
    assert_eq!(
        participant_row(&dm_detail_value, "avatar-peer")["avatar_url"],
        json!("https://cdn.example.com/peer.png")
    );

    let detail = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("viewer"),
            group_id: "avatar-group".into(),
        })
        .await
        .expect("read group detail");
    let detail_value = serde_json::to_value(&detail).expect("serialize detail");
    // Detail keeps the full control fields and gains the avatar column.
    let detail_driver = detail_value["participants"]
        .as_array()
        .expect("detail participants")
        .iter()
        .find(|row| row["actor_id"] == "avatar-driver")
        .expect("driver row");
    assert_eq!(detail_driver["avatar_url"], json!("https://cdn.example.com/driver.png"));
    assert!(detail_driver.get("message_view_scope").is_some());
    assert!(detail_driver.get("role").is_some());
    assert_eq!(
        participant_row(&detail_value, "avatar-peer")["avatar_url"],
        json!("https://cdn.example.com/peer.png")
    );
    let viewer_detail_row = participant_row(&detail_value, "viewer");
    assert_eq!(viewer_detail_row["avatar_url"], json!(null));
    // The key is PRESENT even when null-valued: `avatar_url` always
    // serializes (design §5.1 explicit-null contract), so a future
    // `skip_serializing_if` regression cannot hide behind a missing key.
    assert!(viewer_detail_row.get("avatar_url").is_some());

    // Clearing the avatar through the store write contract flips the V1
    // output to null on the SAME groups — no re-creation involved.
    assert_eq!(
        bots.update_avatar_existing("avatar-peer", AvatarChange::Clear)
            .await
            .expect("clear avatar"),
        ProfileWrite::Updated
    );
    let detail = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("viewer"),
            group_id: "avatar-group".into(),
        })
        .await
        .expect("re-read group detail");
    let detail_value = serde_json::to_value(&detail).expect("serialize detail");
    assert_eq!(
        participant_row(&detail_value, "avatar-peer")["avatar_url"],
        json!(null)
    );
    assert_eq!(
        participant_row(&detail_value, "avatar-driver")["avatar_url"],
        json!("https://cdn.example.com/driver.png")
    );

    let page = fixture
        .service
        .list_groups(list_command("viewer"))
        .await
        .expect("re-list groups");
    let (_normal_summary, normal_value) = find_summary_json(&page, "avatar-group");
    assert_eq!(
        participant_row(&normal_value, "avatar-peer")["avatar_url"],
        json!(null)
    );

    // The cleared avatar must vanish from the DM surfaces too, without
    // re-creating the DM.
    let dm_page = fixture
        .service
        .list_groups(ListGroups {
            caller: human_principal_with_profile("staff-viewer", "staff-viewer", None, None),
            view_bot_id: None,
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("re-list groups as the DM Human");
    let GroupSummary::DirectMessage(dm_summary) = dm_page
        .items
        .iter()
        .find(|item| matches!(item, GroupSummary::DirectMessage(_)))
        .expect("DM summary on re-list")
    else {
        unreachable!()
    };
    let dm_list_value = serde_json::to_value(dm_summary).expect("serialize DM summary");
    assert_eq!(dm_list_value["peer_actor"]["avatar_url"], json!(null));
    let dm_detail = fixture
        .service
        .get(GetGroup {
            caller: human_principal_with_profile("staff-viewer", "staff-viewer", None, None),
            group_id: dm_group_id,
        })
        .await
        .expect("re-read DM detail");
    let dm_detail_value = serde_json::to_value(&dm_detail).expect("serialize DM detail");
    assert_eq!(
        participant_row(&dm_detail_value, "avatar-peer")["avatar_url"],
        json!(null)
    );
}

// ---------------------------------------------------------------------------
// List names are membership snapshots, not registry current names
// ---------------------------------------------------------------------------

#[tokio::test]
async fn group_list_names_are_snapshots() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["viewer", "snap-driver", "snap-peer"] {
        add_public_bot_named_current_profile(&fixture, bot).await;
    }
    seed_avatar(&bots, "snap-driver", "https://cdn.example.com/snap-driver.png").await;
    seed_avatar(&bots, "snap-peer", "https://cdn.example.com/snap-peer.png").await;
    seed_avatar(&bots, "viewer", "https://cdn.example.com/viewer.png").await;

    let mut snap_group = normal_group(
        "snap-group",
        "snap-driver",
        vec![
            Participant::bot("snap-driver", ParticipantRole::Driver),
            Participant::bot("snap-peer", ParticipantRole::Consultant),
            Participant::bot("viewer", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        20,
    );
    snap_group.participants[0].bot_name = Some("snapshot-name".to_string());
    snap_group.participants[2].bot_name = Some("viewer-snapshot".to_string());

    let mut snap_nofill = normal_group(
        "snap-nofill",
        "snap-driver",
        vec![
            Participant::bot("snap-driver", ParticipantRole::Driver),
            Participant::bot("viewer", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        10,
    );
    snap_nofill.participants[1].bot_name = Some("viewer-snapshot".to_string());

    let mut snap_dm = normal_group(
        "snap-dm",
        "snap-peer",
        vec![
            Participant::bot("viewer", ParticipantRole::Driver),
            Participant::bot("snap-peer", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        5,
    );
    snap_dm.group_kind = GroupKind::Dm;
    snap_dm.visibility = "public".into();

    let mut snap_public = snap_group.clone();
    snap_public.id = "snap-public".to_string();
    snap_public.visibility = "public".into();

    for group in [snap_group, snap_nofill, snap_dm, snap_public] {
        fixture.groups.upsert(group).await.expect("store group");
    }

    let page = fixture
        .service
        .list_groups(list_command("viewer"))
        .await
        .expect("list groups");
    assert_eq!(page.total, 4);

    let (_, snap_value) = find_summary_json(&page, "snap-group");
    assert_participant_shape(&snap_value);
    // Snapshots win over the registry's always-different current name, and a
    // missing snapshot stays null even though an avatar IS available.
    assert_group_list_name_snapshot(&snap_value, "snap-driver", Some("snapshot-name"));
    assert_group_list_name_snapshot(&snap_value, "snap-peer", None);
    assert_group_list_name_snapshot(&snap_value, "viewer", Some("viewer-snapshot"));
    assert_eq!(
        participant_row(&snap_value, "snap-peer")["avatar_url"],
        json!("https://cdn.example.com/snap-peer.png")
    );

    let (_, nofill_value) = find_summary_json(&page, "snap-nofill");
    // Existing driver-name backfill keeps working on the legacy field while
    // the new array still reports the raw snapshot (null).
    let GroupSummary::Normal(nofill) = page
        .items
        .iter()
        .find(|item| matches!(item, GroupSummary::Normal(s) if s.group_id == "snap-nofill"))
        .expect("nofill summary")
    else {
        unreachable!()
    };
    assert_eq!(nofill.driver_bot_name.as_deref(), Some("current-name"));
    assert_group_list_name_snapshot(&nofill_value, "snap-driver", None);

    let (dm_summary, dm_value) = find_summary_json(&page, "snap-dm");
    let GroupSummary::DirectMessage(dm) = dm_summary else {
        panic!("expected the DM summary");
    };
    assert_participant_shape(&dm_value);
    // The DM member rows snapshot; the legacy peer display field keeps its
    // registry backfill behavior.
    assert_group_list_name_snapshot(&dm_value, "viewer", None);
    assert_group_list_name_snapshot(&dm_value, "snap-peer", None);
    assert_eq!(dm.peer_actor.as_ref().expect("peer actor").name.as_deref(), Some("current-name"));
    assert_eq!(
        participant_row(&dm_value, "snap-peer")["avatar_url"],
        json!("https://cdn.example.com/snap-peer.png")
    );

    // The public plaza uses the same summary shape and the same visibility
    // boundary: the summary rows are exactly the Group's own member rows.
    let public_page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 10,
            q: None,
            strategy: None,
        })
        .await
        .expect("list public groups");
    let (_, public_value) = find_summary_json(&public_page, "snap-public");
    assert_participant_shape(&public_value);
    assert_group_list_name_snapshot(&public_value, "snap-driver", Some("snapshot-name"));
    assert_group_list_name_snapshot(&public_value, "snap-peer", None);
    assert_group_list_name_snapshot(&public_value, "viewer", Some("viewer-snapshot"));
    assert_eq!(
        participant_row(&public_value, "snap-driver")["avatar_url"],
        json!("https://cdn.example.com/snap-driver.png")
    );
    // Nothing outside the Group's own member list ever appears.
    let public_rows = public_value["participants"].as_array().expect("public rows");
    assert_eq!(public_rows.len(), 3);
    let mut public_ids: Vec<&str> = public_rows
        .iter()
        .map(|row| row["actor_id"].as_str().expect("actor id"))
        .collect();
    public_ids.sort();
    assert_eq!(public_ids, ["snap-driver", "snap-peer", "viewer"]);
}

// ---------------------------------------------------------------------------
// Session-only visible Groups share the same avatar summary
// ---------------------------------------------------------------------------

#[tokio::test]
async fn session_only_group_summaries_carry_avatars() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["viewer", "session-driver"] {
        fixture.add_public_bot(bot).await;
    }
    seed_avatar(&bots, "session-driver", "https://cdn.example.com/session.png").await;
    fixture
        .groups
        .upsert(normal_group(
            "session-only-group",
            "session-driver",
            vec![Participant::bot("session-driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            10,
        ))
        .await
        .expect("store session-only group");
    fixture
        .sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "session-only-group".into(),
            session_id: None,
            params: NewSessionParams {
                participants: vec![Participant::bot("viewer", ParticipantRole::Consultant)],
                ..Default::default()
            },
        })
        .await
        .expect("create session");

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("viewer"),
            view_bot_id: Some("viewer".into()),
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::SessionOnly,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list session-only groups");
    assert_eq!(page.total, 1);
    let (summary, value) = find_summary_json(&page, "session-only-group");
    let GroupSummary::Normal(summary) = summary else {
        panic!("expected a normal summary");
    };
    assert_eq!(summary.membership, Membership::SessionOnly);
    assert_participant_shape(&value);
    assert_eq!(
        participant_row(&value, "session-driver")["avatar_url"],
        json!("https://cdn.example.com/session.png")
    );
}

// ---------------------------------------------------------------------------
// Tombstoned, missing and Human rows (design §5.3 display semantics)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn tombstoned_and_missing_participants_keep_list_avatar_rows() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["viewer", "tomb-driver", "tomb-peer"] {
        fixture.add_public_bot(bot).await;
    }
    seed_avatar(&bots, "tomb-peer", "https://cdn.example.com/tomb.png").await;
    assert!(bots.soft_delete("tomb-peer").await);

    let mut group = normal_group(
        "tomb-group",
        "tomb-driver",
        vec![
            Participant::bot("tomb-driver", ParticipantRole::Driver),
            Participant::bot("tomb-peer", ParticipantRole::Consultant),
            Participant::bot("ghost-participant", ParticipantRole::Consultant),
            Participant::human("human_colleague", ParticipantRole::Observer),
            Participant::bot("viewer", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        10,
    );
    group.participants[1].bot_name = Some("tomb-snapshot".to_string());
    fixture.groups.upsert(group).await.expect("store group");

    let page = fixture
        .service
        .list_groups(list_command("viewer"))
        .await
        .expect("list groups");
    let (_summary, value) = find_summary_json(&page, "tomb-group");
    assert_participant_shape(&value);
    assert_eq!(
        value["participants"].as_array().expect("rows").len(),
        5
    );
    // A retained member of a soft-deleted Bot keeps its stored face and its
    // membership snapshot; an unknown identity resolves a null row; a Human
    // participant stays null.
    let tomb_row = participant_row(&value, "tomb-peer");
    assert_eq!(tomb_row["name"], json!("tomb-snapshot"));
    assert_eq!(tomb_row["avatar_url"], json!("https://cdn.example.com/tomb.png"));
    let ghost_row = participant_row(&value, "ghost-participant");
    assert_eq!(ghost_row["name"], json!(null));
    assert_eq!(ghost_row["avatar_url"], json!(null));
    // Null-valued keys still serialize (explicit-null contract).
    assert!(ghost_row.get("avatar_url").is_some());
    assert!(ghost_row.get("name").is_some());
    assert_eq!(
        participant_row(&value, "human_colleague")["avatar_url"],
        json!(null)
    );

    let detail = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("viewer"),
            group_id: "tomb-group".into(),
        })
        .await
        .expect("read detail");
    let detail_value = serde_json::to_value(&detail).expect("serialize detail");
    assert_eq!(
        participant_row(&detail_value, "tomb-peer")["avatar_url"],
        json!("https://cdn.example.com/tomb.png")
    );
}

// ---------------------------------------------------------------------------
// Shared create/update/participant response models
// ---------------------------------------------------------------------------

#[tokio::test]
async fn shared_participant_models_carry_avatars() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["participant-driver", "participant-a", "participant-b"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .bots
        .ensure_human_actor("staff-manager", "staff-manager")
        .await
        .expect("register Human actor");
    for (bot, url) in [
        ("participant-driver", "https://cdn.example.com/driver.png"),
        ("participant-b", "https://cdn.example.com/added.png"),
    ] {
        seed_avatar(&bots, bot, url).await;
    }

    // create (normal Group): the created detail fills the shared Participant
    // model with the stored avatar instead of leaving nulls.
    let created = fixture
        .service
        .create(CreateGroup {
            caller: human_principal_with_profile("staff-manager", "staff-manager", None, None),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("Shared models".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "participant-driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "participant-a".into(),
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
        .expect("create group");
    let GroupDetail::Collaboration(created) = created else {
        panic!("expected a collaboration detail");
    };
    let group_id = created.group_id.clone();
    let created_value = serde_json::to_value(&created).expect("serialize created detail");
    assert_eq!(
        participant_row(&created_value, "participant-driver")["avatar_url"],
        json!("https://cdn.example.com/driver.png")
    );

    // update (same shared detail model, same avatar source).
    let updated = fixture
        .service
        .update(UpdateGroup {
            caller: human_principal_with_profile("staff-manager", "staff-manager", None, None),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("Shared models renamed".into()),
                ..Default::default()
            },
        })
        .await
        .expect("update group");
    let GroupDetail::Collaboration(updated) = updated else {
        panic!("expected a collaboration detail");
    };
    let updated_value = serde_json::to_value(&updated).expect("serialize updated detail");
    assert_eq!(
        participant_row(&updated_value, "participant-driver")["avatar_url"],
        json!("https://cdn.example.com/driver.png")
    );

    // add / update participant through the shared single-Participant model.
    let added = fixture
        .service
        .add_participant(AddGroupParticipant {
            caller: human_principal_with_profile("staff-manager", "staff-manager", None, None),
            group_id: group_id.clone(),
            actor_id: "participant-b".into(),
            message_view_scope: None,
        })
        .await
        .expect("add participant");
    let added_value = serde_json::to_value(&added).expect("serialize added participant");
    assert_eq!(added_value["actor_id"], json!("participant-b"));
    assert_eq!(added_value["avatar_url"], json!("https://cdn.example.com/added.png"));

    let updated_participant = fixture
        .service
        .update_participant(UpdateGroupParticipant {
            caller: human_principal_with_profile("staff-manager", "staff-manager", None, None),
            group_id: group_id.clone(),
            actor_id: "participant-b".into(),
            mode: Some(ParticipantMode::Muted),
            message_view_scope: None,
        })
        .await
        .expect("update participant");
    let updated_value =
        serde_json::to_value(&updated_participant).expect("serialize updated participant");
    assert_eq!(
        updated_value["avatar_url"],
        json!("https://cdn.example.com/added.png")
    );
}
