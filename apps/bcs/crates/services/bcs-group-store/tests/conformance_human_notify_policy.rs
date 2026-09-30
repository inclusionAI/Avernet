//! Group human-mention notify persistence conformance.
//!
//! The Memory repo factory returns a clone of the same shared `Arc`; SQL
//! stores build a NEW store over the same database on every factory call so
//! every read is a cold read that proves persistence, not cache-only tooling
//! reads.

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{Participant, ParticipantRole};
use bcs_group_store::{GroupBuilder, MemoryGroupRepo, MySqlGroupStore};
use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::{
    DefaultDelivery, GroupKind, GroupMutableFieldsPatch, HumanMentionNotifyMode, RoutingMode,
    RoutingPolicy,
};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

const ENV: &str = "notify-contract";

fn sqlite_factory(db: &Arc<dyn DbPlugin>) -> impl Fn() -> Arc<dyn GroupRepoPort> {
    let db = Arc::clone(db);
    move || Arc::new(MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string()))
}

async fn migrated_sqlite_db() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    db
}

#[tokio::test]
async fn memory_group_repo_passes_group_human_notify_contract() {
    let repo = Arc::new(MemoryGroupRepo::new());
    let reader = Arc::clone(&repo) as Arc<dyn GroupRepoPort>;
    bcs_test_support::contract::group_human_notify::group_human_notify_contract(
        repo.as_ref(),
        move || Arc::clone(&reader),
    )
    .await;
}

#[tokio::test]
async fn sqlite_group_store_passes_group_human_notify_contract() {
    let db = migrated_sqlite_db().await;
    let factory = sqlite_factory(&db);
    bcs_test_support::contract::group_human_notify::group_human_notify_contract(
        factory().as_ref(),
        factory,
    )
    .await;
}

// 1. Build a Group with DriverBotOnly, upsert it, and assert a cold reader
//    (separate Store instance) returns DriverBotOnly.
#[tokio::test]
async fn driver_bot_only_mode_round_trips_to_a_cold_sqlite_reader() {
    let db = migrated_sqlite_db().await;
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut group = GroupBuilder::new("driver").id("notify-driver-only").build();
    group.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    writer.upsert(group).await.expect("upsert");

    let cold = MySqlGroupStore::sqlite(db, ENV.to_string());
    let loaded = cold
        .try_get("notify-driver-only")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(
        loaded.human_mention_notify_mode,
        HumanMentionNotifyMode::DriverBotOnly
    );
}

// 2. Patch to None through a separate reader and assert the other mutable
//    fields are unchanged.
#[tokio::test]
async fn mode_patch_leaves_other_mutable_fields_unchanged() {
    let db = migrated_sqlite_db().await;
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut group = GroupBuilder::new("driver").id("notify-isolation").build();
    group.label = Some("ops".to_string());
    group.visibility = "public".to_string();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: Default::default(),
    });
    writer.upsert(group).await.expect("upsert");

    let patcher = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    patcher
        .patch_mutable_fields(
            "notify-isolation",
            GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            },
        )
        .await
        .expect("patch mode");

    let cold = MySqlGroupStore::sqlite(db, ENV.to_string());
    let loaded = cold
        .try_get("notify-isolation")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(loaded.human_mention_notify_mode, HumanMentionNotifyMode::None);
    assert_eq!(loaded.label.as_deref(), Some("ops"));
    assert_eq!(loaded.visibility, "public");
    assert_eq!(
        loaded.routing_policy.as_ref().map(|policy| policy.mode),
        Some(RoutingMode::Structured)
    );
    assert_eq!(
        loaded
            .routing_policy
            .as_ref()
            .map(|policy| policy.default_bot_final_delivery),
        Some(DefaultDelivery::SendToDriver)
    );
}

// 3. `GroupMutableFieldsPatch::default()` is fully omitted; only the explicit
//    new field changes the stored mode.
#[tokio::test]
async fn default_patch_is_omitted_while_the_new_field_overrides() {
    let db = migrated_sqlite_db().await;
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut group = GroupBuilder::new("driver").id("notify-omitted").build();
    group.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    writer.upsert(group).await.expect("upsert");

    writer
        .patch_mutable_fields("notify-omitted", GroupMutableFieldsPatch::default())
        .await
        .expect("omitted patch");
    let after_omitted = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    assert_eq!(
        after_omitted
            .try_get("notify-omitted")
            .await
            .expect("cold read")
            .expect("group")
            .human_mention_notify_mode,
        HumanMentionNotifyMode::DriverBotOnly
    );

    let patcher = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    patcher
        .patch_mutable_fields(
            "notify-omitted",
            GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            },
        )
        .await
        .expect("explicit patch");
    let after_explicit = MySqlGroupStore::sqlite(db, ENV.to_string());
    assert_eq!(
        after_explicit
            .try_get("notify-omitted")
            .await
            .expect("cold read")
            .expect("group")
            .human_mention_notify_mode,
        HumanMentionNotifyMode::None
    );
}

// 4. A warm stale cache must not answer or freshen the scoped policy read:
//    after a second Store commits a new mode/driver, the first Store's policy
//    read returns the new mode/driver while its ordinary Group cache is left
//    untouched (neither consulted nor populated by the policy read).
#[tokio::test]
async fn policy_read_bypasses_a_warm_stale_group_cache() {
    let db = migrated_sqlite_db().await;
    let store_a = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut group = GroupBuilder::new("driver").id("notify-stale-cache").build();
    group.human_mention_notify_mode = HumanMentionNotifyMode::All;
    store_a.upsert(group).await.expect("upsert");
    store_a
        .try_get("notify-stale-cache")
        .await
        .expect("warm Store A cache with the old row")
        .expect("group");

    let store_b = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut replacement = GroupBuilder::new("replacement-driver")
        .id("notify-stale-cache")
        .build();
    replacement.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    replacement.service_group_uuid = Some("fresh-row".to_string());
    store_b.upsert(replacement).await.expect("commit new row");

    // The policy read must observe the committed row through a scoped SQL read,
    // not through Store A's warm cache.
    let policy = store_a
        .read_human_notify_policy("notify-stale-cache")
        .await
        .expect("policy read")
        .expect("policy");
    assert_eq!(policy.mode, HumanMentionNotifyMode::DriverBotOnly);
    assert_eq!(policy.driver_bot_id, "replacement-driver");

    // The policy read must not have populated or refreshed the ordinary
    // Group cache either: it still holds the stale row, proving the policy
    // read never touched it.
    let cached = store_a
        .try_get("notify-stale-cache")
        .await
        .expect("cached read")
        .expect("group");
    assert_eq!(
        cached.human_mention_notify_mode,
        HumanMentionNotifyMode::All,
        "policy read must not freshen the ordinary Group cache"
    );
    assert_eq!(cached.driver_bot, "driver");
    assert_eq!(cached.service_group_uuid.as_deref(), None);
}

// Policy reads are env-scoped: a Group visible in one environment is
// invisible to a Store bound to another environment.
#[tokio::test]
async fn policy_read_is_environment_scoped() {
    let db = migrated_sqlite_db().await;
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut group = GroupBuilder::new("driver").id("notify-env-scoped").build();
    group.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    writer.upsert(group).await.expect("upsert");

    let other_env = MySqlGroupStore::sqlite(Arc::clone(&db), "notify-other-env".to_string());
    assert_eq!(
        other_env
            .read_human_notify_policy("notify-env-scoped")
            .await
            .expect("other-env policy read"),
        None
    );
    let same_env = MySqlGroupStore::sqlite(db, ENV.to_string());
    assert_eq!(
        same_env
            .read_human_notify_policy("notify-env-scoped")
            .await
            .expect("same-env policy read")
            .expect("policy")
            .mode,
        HumanMentionNotifyMode::DriverBotOnly
    );
}

// Every Group SELECT projection must carry the column, not only the
// single-group load path.
#[tokio::test]
async fn every_group_projection_returns_the_persisted_mode() {
    let db = migrated_sqlite_db().await;
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut group = GroupBuilder::new("driver").id("notify-projections").build();
    group.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    group
        .participants
        .push(Participant::bot("driver", ParticipantRole::Driver));
    writer.upsert(group).await.expect("upsert");

    // A second Store proves each read comes from SQL, not the writer's cache.
    let reader = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    for loaded in [
        reader.list().await,
        reader.list_paginated(0, 10).await,
        reader.list_paginated_by_kind(Some(GroupKind::Normal), 0, 10).await,
        reader
            .list_paginated_filtered(0, 10, None, None, None)
            .await,
    ] {
        let loaded = loaded
            .into_iter()
            .find(|group| group.id == "notify-projections")
            .expect("projection row");
        assert_eq!(
            loaded.human_mention_notify_mode,
            HumanMentionNotifyMode::DriverBotOnly,
            "projection lost the persisted mode"
        );
    }
    let projection_rows = [
        ("find_by_participant", reader.find_by_participant("driver").await),
        (
            "find_by_participant_filtered",
            reader
                .find_by_participant_filtered("driver", None, None)
                .await,
        ),
        (
            "find_by_participant_paginated",
            reader
                .find_by_participant_paginated("driver", 0, 10)
                .await,
        ),
        ("list_paginated", reader.list_paginated(0, 10).await),
        ("list", reader.list().await),
        (
            "list_paginated_by_kind",
            reader
                .list_paginated_by_kind(Some(GroupKind::Normal), 0, 10)
                .await,
        ),
        (
            "list_paginated_filtered",
            reader.list_paginated_filtered(0, 10, None, None, None).await,
        ),
    ];
    for (projection, groups) in projection_rows {
        let loaded = groups
            .into_iter()
            .find(|group| group.id == "notify-projections")
            .unwrap_or_else(|| panic!("{projection} lost the group row"));
        assert_eq!(
            loaded.human_mention_notify_mode,
            HumanMentionNotifyMode::DriverBotOnly,
            "{projection} lost the persisted mode"
        );
    }
    assert_eq!(reader.count().await, 1);
}

// The DM race-safe INSERT must persist the mode with its Group-level default.
#[tokio::test]
async fn dm_insert_persists_the_default_mode() {
    let db = migrated_sqlite_db().await;
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut dm_group = GroupBuilder::new("bot-a").id("notify-dm").build();
    dm_group.group_kind = GroupKind::Dm;
    dm_group.dm_pair_key = Some("notify-dm-pair".to_string());
    dm_group
        .participants
        .push(Participant::bot(
            "bot-a",
            ParticipantRole::Driver,
        ));
    dm_group.participants.push(Participant::bot(
        "bot-b",
        ParticipantRole::Consultant,
    ));
    assert!(writer.insert_dm_group_if_absent(dm_group).await.expect("dm insert"));

    let reader = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut racer = GroupBuilder::new("bot-b").id("notify-dm-race").build();
    racer.group_kind = GroupKind::Dm;
    racer.dm_pair_key = Some("notify-dm-pair".to_string());
    assert_eq!(
        reader
            .insert_dm_group_if_absent(racer)
            .await
            .expect("dm race insert is refused"),
        false,
        "pair key race must be refused"
    );
    let cold = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let loaded = cold
        .try_get("notify-dm")
        .await
        .expect("cold read")
        .expect("dm group");
    assert_eq!(
        loaded.human_mention_notify_mode,
        HumanMentionNotifyMode::All,
        "DM insert of a default Group keeps the All default"
    );
}

// 7. An invalid persisted `human_mention_notify_mode` is treated asymmetrically:
//    the infallible bulk list/find projections (`human_mention_notify_mode_from_row`)
//    log + normalize to `All` in memory only, while the fallible single-group
//    load (`try_get`) and the scoped policy read (`read_human_notify_policy`)
//    return `Err`. Mirrors the `message_view_scope_from_row` convention; the
//    DB row is never rewritten. The asymmetry is spec §7.2-deliberate but was
//    previously unpinned.
#[tokio::test]
async fn bulk_projection_normalizes_invalid_persisted_mode_while_fallible_reads_fail() {
    let db = migrated_sqlite_db().await;
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
    let mut group = GroupBuilder::new("driver").id("notify-invalid-mode").build();
    group.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    group
        .participants
        .push(Participant::bot("driver", ParticipantRole::Driver));
    writer.upsert(group).await.expect("upsert");

    // Out-of-band: corrupt the persisted mode so the bulk decoder hits its
    // unknown-value branch. Same direct-SQL idiom as
    // `conformance_group_repo::sqlite_group_opening_message_rejects_invalid_persisted_json`.
    db.execute(DbStatement::with_params(
        "UPDATE bcs_groups SET human_mention_notify_mode = ? WHERE group_id = ? AND env = ?",
        vec![
            DbValue::from("weird"),
            DbValue::from("notify-invalid-mode"),
            DbValue::from(ENV),
        ],
    ))
    .await
    .expect("corrupt persisted human_mention_notify_mode");

    // A cold reader proves each read comes from SQL, not the writer's cache.
    let reader = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());

    // Bulk list/find projections are infallible: they MUST normalize the
    // unknown value to `All` in memory (and never rewrite the DB row).
    for loaded in [
        reader.list().await,
        reader.list_paginated(0, 10).await,
        reader
            .list_paginated_by_kind(Some(GroupKind::Normal), 0, 10)
            .await,
        reader.list_paginated_filtered(0, 10, None, None, None).await,
    ] {
        let loaded = loaded
            .into_iter()
            .find(|group| group.id == "notify-invalid-mode")
            .expect("bulk projection row");
        assert_eq!(
            loaded.human_mention_notify_mode,
            HumanMentionNotifyMode::All,
            "bulk projection must normalize invalid persisted mode to All"
        );
    }
    let projection_rows = [
        ("find_by_participant", reader.find_by_participant("driver").await),
        (
            "find_by_participant_filtered",
            reader
                .find_by_participant_filtered("driver", None, None)
                .await,
        ),
        (
            "find_by_participant_paginated",
            reader
                .find_by_participant_paginated("driver", 0, 10)
                .await,
        ),
    ];
    for (projection, groups) in projection_rows {
        let loaded = groups
            .into_iter()
            .find(|group| group.id == "notify-invalid-mode")
            .unwrap_or_else(|| panic!("{projection} lost the group row"));
        assert_eq!(
            loaded.human_mention_notify_mode,
            HumanMentionNotifyMode::All,
            "{projection} must normalize invalid persisted mode to All"
        );
    }

    // The fallible single-group load returns Err rather than silently normalizing.
    let single_err = reader
        .try_get("notify-invalid-mode")
        .await
        .expect_err("invalid persisted mode must surface as Err on the fallible single-group load");
    assert!(
        single_err
            .to_string()
            .contains("unknown human_mention_notify_mode 'weird'"),
        "single-load error must name the unknown persisted mode: {single_err}"
    );

    // The scoped policy read is also fallible and returns Err (it does not
    // consult the ordinary Group cache, so the cold read also surfaces the
    // corrupted row directly).
    let policy_err = reader
        .read_human_notify_policy("notify-invalid-mode")
        .await
        .expect_err("invalid persisted mode must surface as Err on the policy read");
    assert!(
        policy_err
            .to_string()
            .contains("unknown human_mention_notify_mode 'weird'"),
        "policy-read error must name the unknown persisted mode: {policy_err}"
    );
}
