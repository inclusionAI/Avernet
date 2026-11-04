//! V1 Session participant avatars and lightweight list summaries
//! (bot-avatar plan Task 7).
//!
//! Design §5.1–§5.3 (`docs/specs/2026-11-02-bot-avatar-collaboration-repair/design.md`):
//! - Session list summaries gain a `participants` array whose `name` is THIS
//!   Session's own member snapshot verbatim (null allowed, never backfilled
//!   for the list; session-only dynamic members included; a Group member that
//!   has left the Session never appears) and whose `avatar_url` comes from ONE
//!   batched display-profile read per result page.
//! - Session detail participants keep every control field, keep the existing
//!   registry name backfill, and gain `avatar_url` from one batched read for
//!   that Session; Human participants always resolve null, a retained member
//!   of a soft-deleted Bot keeps its stored face, and unknown rows are null.

use super::*;
use bcs_service_api::types::bot_profile::{AvatarChange, ProfileWrite};
use serde_json::{Value, json};

/// A `BotCore` whose stored-profile records (the avatar writes below) live in
/// a test-scoped temp directory instead of the crate working directory.
fn avatar_backed_core() -> (tempfile::TempDir, Arc<BotCore>) {
    let dir = tempfile::tempdir().expect("temp bots base dir");
    let bots = Arc::new(BotCore::with_base_dir(dir.path().to_path_buf()));
    (dir, bots)
}

/// Seed one Bot's avatar through the Task 4/5 existing-only write contract.
async fn seed_avatar(bots: &BotCore, bot_id: &str, url: &str) {
    assert_eq!(
        bots.update_avatar_existing(bot_id, AvatarChange::Set(url.to_string()))
            .await
            .expect("seed avatar"),
        ProfileWrite::Updated
    );
}

/// The lightweight list-summary row shape (design §5.2): identity plus the two
/// nullable display fields, and none of the detail-only control fields.
fn assert_summary_row_shape(value: &Value) {
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

/// A session view row's avatar for a registered Bot that has one.
fn assert_session_avatar(value: &Value, actor: &str, expected: &str) {
    let row = value["participants"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["actor_id"] == actor)
        .unwrap();
    assert_eq!(row["avatar_url"], expected);
}

/// A list-summary row's `name` is THIS Session's member snapshot verbatim —
/// `snapshot` (as null when the member row has no name), never the registry's
/// current name and never the Group's member snapshot.
fn assert_session_list_name_snapshot(value: &Value, actor: &str, snapshot: Option<&str>) {
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

fn participant_row<'a>(value: &'a Value, actor: &str) -> &'a Value {
    value["participants"]
        .as_array()
        .unwrap_or_else(|| panic!("participants array missing for {actor}"))
        .iter()
        .find(|item| item["actor_id"] == actor)
        .unwrap_or_else(|| panic!("participant row for {actor}"))
}

fn session_row_ids(value: &Value) -> Vec<String> {
    value["participants"]
        .as_array()
        .expect("participants array")
        .iter()
        .map(|row| row["actor_id"].as_str().expect("actor id").to_string())
        .collect()
}

fn list_viewing(viewer: &str) -> ListSessions {
    ListSessions {
        caller: bot_principal(viewer),
        group_id: "g1".into(),
        view_bot_id: Some(viewer.into()),
        offset: 0,
        limit: 10,
        status: None,
    }
}

/// Register a public Bot whose REGISTRY name differs from every membership
/// snapshot, so a snapshot/backfill mix-up cannot silently pass.
async fn add_public_bot_named_current_profile(fixture: &Fixture, bot_uuid: &str) {
    fixture
        .bots
        .register(
            bot_uuid.to_string(),
            BotCapabilities {
                name: Some("current-name".to_string()),
                visibility: "public".into(),
                ..Default::default()
            },
        )
        .await
        .expect("register bot");
    fixture
        .bots
        .save_created_by(bot_uuid, bot_uuid, true)
        .await
        .expect("assign test Bot owner");
    bcs_test_support::grant_owner_edge(&fixture.authority_repo, bot_uuid, bot_uuid).await;
}

/// Seed one session directly through the repository so the member snapshots
/// are exactly what the caller provides.
async fn seed_session_with(
    fixture: &Fixture,
    participants: Vec<Participant>,
) -> String {
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants,
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    session.id
}

// ---------------------------------------------------------------------------
// Session views use THIS Session's own roster (list and detail)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn session_views_use_their_own_members_for_avatars() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["driver", "worker-a", "worker-b", "worker-c", "session-dynamic"] {
        fixture.add_bot(bot).await;
    }
    // staff-driver manages the Session through the CURRENT authority edge on
    // the driver Bot (the group originator stays a Bot for this test).
    fixture
        .bots
        .save_created_by("driver", "staff-driver", true)
        .await
        .expect("assign driver owner");
    fixture.grant_owner("driver", "staff-driver").await;
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b", "worker-c"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    seed_avatar(&bots, "driver", "https://cdn.example.com/driver.png").await;
    seed_avatar(&bots, "worker-a", "https://cdn.example.com/worker-a.png").await;
    seed_avatar(&bots, "worker-c", "https://cdn.example.com/group-only.png").await;
    seed_avatar(&bots, "session-dynamic", "https://cdn.example.com/dynamic.png").await;

    // Add the session-only dynamic member (not in the Group roster): the
    // mutation response reuses the shared Participant model and must carry
    // the current avatar.
    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("staff-driver"),
            session_id: session_id.clone(),
            bot_uuid: "session-dynamic".into(),
            message_view_scope: None,
        })
        .await
        .expect("add session-only dynamic member");
    assert_eq!(added.actor_id, "session-dynamic");
    let added_value = serde_json::to_value(&added).expect("serialize added participant");
    assert_eq!(
        added_value["avatar_url"],
        json!("https://cdn.example.com/dynamic.png")
    );

    // worker-c IS a Group member but has LEFT the Session: drive the real
    // add + self-leave so the rosters are genuine.
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("staff-driver"),
            session_id: session_id.clone(),
            bot_uuid: "worker-c".into(),
            message_view_scope: None,
        })
        .await
        .expect("add worker-c before the leave");
    fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            caller: bot_principal("worker-c"),
            session_id: session_id.clone(),
            bot_uuid: "worker-c".into(),
        })
        .await
        .expect("worker-c leaves the Session");
    let group = fixture.groups.get("g1").await.expect("group exists");
    assert!(
        group
            .participants
            .iter()
            .any(|p| p.bot_uuid == "worker-c"),
        "worker-c must still be a Group member after the self-leave"
    );

    let page = SessionService::list(&fixture.service, list_viewing("worker-a"))
        .await
        .expect("list sessions");
    assert_eq!(page.total, 1);
    let value = serde_json::to_value(&page.items[0]).expect("serialize summary");
    assert_summary_row_shape(&value);
    // The rows are exactly the Session's own members in stored order,
    // including the session-only dynamic member.
    assert_eq!(
        session_row_ids(&value),
        ["driver", "worker-a", "worker-b", "human_staff-1", "session-dynamic"]
    );
    assert_session_avatar(&value, "driver", "https://cdn.example.com/driver.png");
    assert_session_avatar(&value, "worker-a", "https://cdn.example.com/worker-a.png");
    assert_session_avatar(
        &value,
        "session-dynamic",
        "https://cdn.example.com/dynamic.png",
    );
    // A registered Bot without an avatar and a Human participant resolve null.
    assert!(participant_row(&value, "worker-b")["avatar_url"].is_null());
    assert!(participant_row(&value, "human_staff-1")["avatar_url"].is_null());
    assert_eq!(
        participant_row(&value, "human_staff-1")["actor_kind"],
        json!("human")
    );
    // The Group-only member that left the Session never appears, even though
    // it still has an avatar in the registry.
    assert!(!value["participants"]
        .as_array()
        .expect("rows")
        .iter()
        .any(|row| row["actor_id"] == "worker-c"));

    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("worker-a"),
            session_id: session_id.clone(),
        })
        .await
        .expect("read session detail");
    let detail_value = serde_json::to_value(&detail).expect("serialize detail");
    // Detail keeps every control field and gains the avatar column.
    let driver_row = participant_row(&detail_value, "driver");
    assert!(driver_row.get("role").is_some());
    assert!(driver_row.get("mode").is_some());
    assert!(driver_row.get("message_view_scope").is_some());
    assert_session_avatar(&detail_value, "driver", "https://cdn.example.com/driver.png");
    assert_session_avatar(
        &detail_value,
        "session-dynamic",
        "https://cdn.example.com/dynamic.png",
    );
    assert!(participant_row(&detail_value, "worker-b")["avatar_url"].is_null());
    assert!(participant_row(&detail_value, "human_staff-1")["avatar_url"].is_null());
    assert!(!detail_value["participants"]
        .as_array()
        .expect("detail rows")
        .iter()
        .any(|row| row["actor_id"] == "worker-c"));
}

// ---------------------------------------------------------------------------
// List names are THIS Session's own snapshots, never registry backfill
// ---------------------------------------------------------------------------

#[tokio::test]
async fn session_list_names_are_snapshots() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["driver", "snapshot-member", "missing-snapshot"] {
        add_public_bot_named_current_profile(&fixture, bot).await;
    }
    // The Group roster carries its own driver snapshot; the Session rows must
    // never fall back to it.
    let mut group = Group::new(
        "g1",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.originator = Some("human_driver".to_string());
    group.label = Some("g1".to_string());
    group.group_strategy = GroupStrategy::Chat;
    group.participants[0].bot_name = Some("group-only-snapshot".to_string());
    fixture
        .groups
        .upsert(group)
        .await
        .expect("store group with its own snapshot");

    let mut snapshot_member = Participant::bot("snapshot-member", ParticipantRole::Consultant);
    snapshot_member.bot_name = Some("session-snapshot".to_string());
    let session_id = seed_session_with(
        &fixture,
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            snapshot_member,
            Participant::bot("missing-snapshot", ParticipantRole::Consultant),
            Participant::human("human_staff-1", ParticipantRole::Observer),
        ],
    )
    .await;

    seed_avatar(&bots, "driver", "https://cdn.example.com/snap-driver.png").await;
    seed_avatar(&bots, "snapshot-member", "https://cdn.example.com/snapshot.png").await;
    seed_avatar(&bots, "missing-snapshot", "https://cdn.example.com/missing.png").await;

    let page = SessionService::list(&fixture.service, list_viewing("driver"))
        .await
        .expect("list sessions");
    assert_eq!(page.total, 1);
    let value = serde_json::to_value(&page.items[0]).expect("serialize summary");
    assert_summary_row_shape(&value);
    // Session snapshots win over the registry's always-different current name
    // and over the Group's member snapshot; a null snapshot stays null.
    assert_session_list_name_snapshot(&value, "snapshot-member", Some("session-snapshot"));
    assert_session_list_name_snapshot(&value, "missing-snapshot", None);
    assert_session_list_name_snapshot(&value, "driver", None);
    assert_session_list_name_snapshot(&value, "human_staff-1", None);
    // A missing name snapshot does not block the avatar column: the two
    // display fields are filled independently.
    assert_session_avatar(&value, "missing-snapshot", "https://cdn.example.com/missing.png");
    assert_session_avatar(&value, "driver", "https://cdn.example.com/snap-driver.png");

    // Detail keeps its PRE-EXISTING registry name backfill (design §5.2:
    // 详情名称仍遵守既有补齐规则), while the avatar column rides along.
    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
        })
        .await
        .expect("read session detail");
    let detail_value = serde_json::to_value(&detail).expect("serialize detail");
    assert_eq!(
        participant_row(&detail_value, "missing-snapshot")["name"],
        json!("current-name")
    );
    assert_eq!(
        participant_row(&detail_value, "driver")["name"],
        json!("current-name")
    );
    // A present snapshot is not re-backfilled (existing behavior).
    assert_eq!(
        participant_row(&detail_value, "snapshot-member")["name"],
        json!("session-snapshot")
    );
    assert_session_avatar(
        &detail_value,
        "missing-snapshot",
        "https://cdn.example.com/missing.png",
    );
}

// ---------------------------------------------------------------------------
// Clearing or replacing a profile updates the SAME Session views
// ---------------------------------------------------------------------------

#[tokio::test]
async fn cleared_or_replaced_avatars_follow_the_profile_on_old_sessions() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["driver", "profile-peer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let session_id = seed_session_with(
        &fixture,
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("profile-peer", ParticipantRole::Consultant),
        ],
    )
    .await;

    seed_avatar(&bots, "driver", "https://cdn.example.com/old.png").await;

    let page = SessionService::list(&fixture.service, list_viewing("driver"))
        .await
        .expect("list sessions");
    let value = serde_json::to_value(&page.items[0]).expect("serialize summary");
    assert_session_avatar(&value, "driver", "https://cdn.example.com/old.png");

    // Replace: the same Session surfaces the new face without re-creation.
    seed_avatar(&bots, "driver", "https://cdn.example.com/new.png").await;
    let page = SessionService::list(&fixture.service, list_viewing("driver"))
        .await
        .expect("re-list sessions");
    let value = serde_json::to_value(&page.items[0]).expect("serialize summary");
    assert_session_avatar(&value, "driver", "https://cdn.example.com/new.png");
    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
        })
        .await
        .expect("re-read detail");
    let detail_value = serde_json::to_value(&detail).expect("serialize detail");
    assert_session_avatar(&detail_value, "driver", "https://cdn.example.com/new.png");

    // Clear to null through the store write contract.
    assert_eq!(
        bots.update_avatar_existing("driver", AvatarChange::Clear)
            .await
            .expect("clear avatar"),
        ProfileWrite::Updated
    );
    let page = SessionService::list(&fixture.service, list_viewing("driver"))
        .await
        .expect("re-list sessions after clear");
    let value = serde_json::to_value(&page.items[0]).expect("serialize summary");
    assert!(participant_row(&value, "driver")["avatar_url"].is_null());
    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
        })
        .await
        .expect("re-read detail after clear");
    let detail_value = serde_json::to_value(&detail).expect("serialize detail");
    assert!(participant_row(&detail_value, "driver")["avatar_url"].is_null());
}

// ---------------------------------------------------------------------------
// Tombstoned, missing, and Human members (design §5.3 display semantics)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn tombstoned_and_missing_members_keep_session_avatar_rows() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["driver", "tomb-peer"] {
        fixture.add_bot(bot).await;
    }
    seed_avatar(&bots, "tomb-peer", "https://cdn.example.com/tomb.png").await;
    assert!(bots.soft_delete("tomb-peer").await);
    fixture.store_group("g1", "driver", None).await;

    let mut tomb_row = Participant::bot("tomb-peer", ParticipantRole::Consultant);
    tomb_row.bot_name = Some("tomb-snapshot".to_string());
    let session_id = seed_session_with(
        &fixture,
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            tomb_row,
            Participant::bot("ghost-participant", ParticipantRole::Consultant),
            Participant::human("human_colleague", ParticipantRole::Observer),
        ],
    )
    .await;

    let page = SessionService::list(&fixture.service, list_viewing("driver"))
        .await
        .expect("list sessions");
    let value = serde_json::to_value(&page.items[0]).expect("serialize summary");
    // A retained member of a soft-deleted Bot keeps its stored face and its
    // Session snapshot; an unknown identity resolves a null row; a Human
    // participant stays null.
    let tomb = participant_row(&value, "tomb-peer");
    assert_eq!(tomb["name"], json!("tomb-snapshot"));
    assert_eq!(tomb["avatar_url"], json!("https://cdn.example.com/tomb.png"));
    let ghost = participant_row(&value, "ghost-participant");
    assert_eq!(ghost["name"], json!(null));
    assert!(ghost["avatar_url"].is_null());
    // The keys are PRESENT even when null-valued: `avatar_url` and `name`
    // always serialize (design §5.1 explicit-null contract), so a future
    // `skip_serializing_if` regression cannot hide behind a missing key.
    assert!(ghost.get("avatar_url").is_some());
    assert!(ghost.get("name").is_some());
    assert!(participant_row(&value, "human_colleague")["avatar_url"].is_null());

    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
        })
        .await
        .expect("read session detail");
    let detail_value = serde_json::to_value(&detail).expect("serialize detail");
    assert_eq!(
        participant_row(&detail_value, "tomb-peer")["avatar_url"],
        json!("https://cdn.example.com/tomb.png")
    );
    assert!(participant_row(&detail_value, "ghost-participant")["avatar_url"].is_null());
    assert!(
        participant_row(&detail_value, "ghost-participant")
            .get("avatar_url")
            .is_some()
    );
}

// ---------------------------------------------------------------------------
// Shared create/update/participant response models
// ---------------------------------------------------------------------------

#[tokio::test]
async fn create_update_and_participant_mutations_reuse_avatar_responses() {
    let (_bots_dir, bots) = avatar_backed_core();
    let fixture = Fixture::new_with_bots(bots.clone()).await;
    for bot in ["driver", "member-a", "member-b"] {
        fixture.add_bot(bot).await;
    }
    // The Human caller "driver" owns the driver Bot and is the Group
    // originator (human_driver), so it may both launch the Session and
    // manage its participants.
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["member-a"],
            "human_driver",
            None,
        )
        .await;
    seed_avatar(&bots, "driver", "https://cdn.example.com/driver.png").await;
    seed_avatar(&bots, "member-a", "https://cdn.example.com/member-a.png").await;
    seed_avatar(&bots, "member-b", "https://cdn.example.com/member-b.png").await;

    // create: the created detail reuses the shared SessionDetail model with
    // the driver's stored avatar.
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("member-a", None)],
        None,
        Some("Avatar session"),
    )
    .await;
    let session_id = outcome.session.session_id.clone();
    let created_value = serde_json::to_value(&outcome.session).expect("serialize created detail");
    assert_session_avatar(&created_value, "driver", "https://cdn.example.com/driver.png");
    // The inherited member-a row carries its avatar too.
    assert_session_avatar(&created_value, "member-a", "https://cdn.example.com/member-a.png");

    // update: the same shared detail model, same avatar source.
    let updated = fixture
        .service
        .update(UpdateSession {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            title: Some("Renamed avatar session".into()),
        })
        .await
        .expect("update session title");
    let updated_value = serde_json::to_value(&updated).expect("serialize updated detail");
    assert_session_avatar(&updated_value, "driver", "https://cdn.example.com/driver.png");

    // add participant through the shared single-Participant model.
    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "member-b".into(),
            message_view_scope: None,
        })
        .await
        .expect("add participant");
    let added_value = serde_json::to_value(&added).expect("serialize added participant");
    assert_eq!(
        added_value["avatar_url"],
        json!("https://cdn.example.com/member-b.png")
    );

    // update participant mode through the same shared model.
    let updated_participant = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "member-b".into(),
            mode: Some(ParticipantMode::Muted),
            message_view_scope: None,
        })
        .await
        .expect("update participant mode");
    let updated_participant_value =
        serde_json::to_value(&updated_participant).expect("serialize updated participant");
    assert_eq!(
        updated_participant_value["avatar_url"],
        json!("https://cdn.example.com/member-b.png")
    );
}
