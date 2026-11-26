//! Characterization tests for the shared Group row projection and participant
//! aggregation. These drive the original entry points so they must stay green
//! before and after the projection extraction.

use super::*;
use bcs_service_api::GroupKind;

fn row(entries: Vec<(&str, Value)>) -> DbRow {
    DbRow::new(
        entries
            .into_iter()
            .map(|(column, value)| (column.to_string(), value))
            .collect(),
    )
}

fn joined_row_fields(
    id: &str,
    bot: &str,
    role: &str,
    opening_message_json: Value,
    human_mention_notify_mode: Value,
) -> DbRow {
    row(vec![
        ("group_id", Value::from(id)),
        ("status", Value::from("active")),
        ("driver_bot", Value::from("driver")),
        ("opening_message_json", opening_message_json),
        ("human_mention_notify_mode", human_mention_notify_mode),
        ("bot_uuid", Value::from(bot)),
        ("role", Value::from(role)),
    ])
}

fn joined_row(id: &str, bot: &str, role: &str) -> DbRow {
    joined_row_fields(id, bot, role, Value::Null, Value::Null)
}

fn joined_row_at(id: &str, updated_ts: i64) -> DbRow {
    row(vec![
        ("group_id", Value::from(id)),
        ("status", Value::from("active")),
        ("driver_bot", Value::from("driver")),
        ("opening_message_json", Value::Null),
        ("human_mention_notify_mode", Value::Null),
        ("created_ts", Value::from(updated_ts)),
        ("updated_ts", Value::from(updated_ts)),
        ("bot_uuid", Value::from("bot")),
        ("role", Value::from("driver")),
    ])
}

fn group_without_member(id: &str) -> DbRow {
    row(vec![
        ("group_id", Value::from(id)),
        ("status", Value::from("active")),
        ("driver_bot", Value::from("driver")),
        ("opening_message_json", Value::Null),
        ("human_mention_notify_mode", Value::Null),
        ("bot_uuid", Value::Null),
        ("role", Value::Null),
    ])
}

/// The participant-scoped find SELECT aliases the group and participant
/// columns (`g_*` / `p_*`).
fn aliased_find_row() -> DbRow {
    row(vec![
        ("group_id", Value::from("dm-group")),
        ("status", Value::from("active")),
        ("driver_bot", Value::from("driver")),
        ("opening_message_json", Value::Null),
        ("human_mention_notify_mode", Value::Null),
        ("g_group_kind", Value::from("dm")),
        ("g_dm_pair_key", Value::from("a|b")),
        ("p_bot_uuid", Value::from("human-1")),
        ("p_role", Value::from("consultant")),
        ("p_actor_kind", Value::from("human")),
        ("p_mode", Value::from("present")),
        ("p_tags_json", Value::from("[\"tenant-a\"]")),
        ("p_message_view_scope", Value::from("participant")),
    ])
}

fn strict_group_row() -> DbRow {
    row(vec![
        ("group_id", Value::from("group-1")),
        ("status", Value::from("active")),
        ("driver_bot", Value::from("driver")),
        ("opening_message_json", Value::Null),
        ("human_mention_notify_mode", Value::Null),
    ])
}

fn participant_row() -> DbRow {
    row(vec![
        ("bot_uuid", Value::from("driver")),
        ("role", Value::from("driver")),
        ("actor_kind", Value::from("bot")),
        ("mode", Value::from("auto")),
        ("tags_json", Value::Null),
        ("message_view_scope", Value::from("full")),
    ])
}

fn store_with_rows(rows: Vec<DbRow>) -> (Arc<RecordingDbPlugin>, MySqlGroupStore) {
    let db = Arc::new(RecordingDbPlugin::with_query_rows(rows));
    let store = MySqlGroupStore::new(db.clone(), "local".to_string());
    (db, store)
}

fn query_count(db: &Arc<RecordingDbPlugin>) -> usize {
    db.query_sqls.lock().expect("query sqls").len()
}

#[tokio::test]
async fn joined_rows_keep_first_role_and_encounter_order() {
    let row = |bot: &str, role: &str| {
        DbRow::new(BTreeMap::from([
            ("group_id".into(), Value::from("g")),
            ("driver_bot".into(), Value::from("b")),
            ("opening_message_json".into(), Value::Null),
            ("human_mention_notify_mode".into(), Value::Null),
            ("bot_uuid".into(), Value::from(bot)),
            ("role".into(), Value::from(role)),
        ]))
    };
    let db = RecordingDbPlugin::with_query_rows(vec![
        row("b", "driver"),
        row("a", "worker"),
        row("b", "observer"),
    ]);
    let store = MySqlGroupStore::new(Arc::new(db), "local".into());
    let groups = store.load_all_groups_from_mysql().await;
    assert_eq!(groups.len(), 1);
    let members: Vec<_> = groups[0]
        .participants
        .iter()
        .map(|p| (p.bot_uuid.as_str(), p.role))
        .collect();
    assert_eq!(
        members,
        [
            ("b", ParticipantRole::Driver),
            ("a", ParticipantRole::Worker)
        ]
    );
}

#[tokio::test]
async fn shared_members_stay_in_every_group_and_left_join_groups_survive() {
    let (db, store) = store_with_rows(vec![
        joined_row("g1", "shared-bot", "worker"),
        group_without_member("g2"),
        joined_row("g3", "shared-bot", "worker"),
    ]);

    let groups = store.load_all_groups_from_mysql().await;

    assert_eq!(query_count(&db), 1);
    assert_eq!(
        groups.iter().map(|group| group.id.as_str()).collect::<Vec<_>>(),
        ["g1", "g2", "g3"]
    );
    assert_eq!(groups[0].participants.len(), 1);
    assert_eq!(groups[0].participants[0].bot_uuid, "shared-bot");
    assert!(
        groups[1].participants.is_empty(),
        "a LEFT JOIN row without a member must still return the group"
    );
    assert_eq!(groups[2].participants.len(), 1);
    assert_eq!(groups[2].participants[0].bot_uuid, "shared-bot");
}

#[tokio::test]
async fn non_paginated_find_alias_projection_decodes_dm_identity_and_member_columns() {
    let assert_alias_projection = |groups: Vec<Group>| {
        assert_eq!(groups.len(), 1);
        let group = &groups[0];
        assert_eq!(group.group_kind, GroupKind::Dm);
        assert_eq!(group.dm_pair_key.as_deref(), Some("a|b"));
        assert_eq!(group.participants.len(), 1);
        let member = &group.participants[0];
        assert_eq!(member.bot_uuid, "human-1");
        assert_eq!(member.role, ParticipantRole::Consultant);
        assert_eq!(member.actor_kind, ActorKind::Human);
        assert_eq!(member.mode, Some(ParticipantMode::Present));
        assert_eq!(member.tags, vec!["tenant-a".to_string()]);
        assert_eq!(member.message_view_scope, MessageViewScope::Participant);
    };

    let (db, store) = store_with_rows(vec![aliased_find_row()]);
    assert_alias_projection(store.find_by_participant_sql("human-1").await);
    assert_eq!(query_count(&db), 1);

    let (db, store) = store_with_rows(vec![aliased_find_row()]);
    assert_alias_projection(
        store
            .find_by_participant_filtered_sql("human-1", None, None)
            .await,
    );
    assert_eq!(query_count(&db), 1);
}

#[tokio::test]
async fn corrupt_opening_message_on_a_later_row_discards_the_whole_batch() {
    let rows = vec![
        joined_row_fields("g", "bot", "driver", Value::Null, Value::Null),
        joined_row_fields(
            "g",
            "other",
            "worker",
            Value::from("not-json"),
            Value::Null,
        ),
    ];

    let (_, store) = store_with_rows(rows.clone());
    assert!(store.load_all_groups_from_mysql().await.is_empty());

    let (_, store) = store_with_rows(rows.clone());
    assert!(store.list_paginated_sql(0, 10).await.is_empty());

    let (_, store) = store_with_rows(rows);
    assert!(store.find_by_participant_sql("bot").await.is_empty());
}

#[tokio::test]
async fn strict_single_group_read_propagates_driver_decode_errors() {
    let malformed_group = row(vec![
        ("group_id", Value::from("group-1")),
        ("driver_bot", Value::from(7_i64)),
        ("opening_message_json", Value::Null),
    ]);
    let db = Arc::new(RecordingDbPlugin {
        query_results: StdMutex::new(VecDeque::from([vec![malformed_group]])),
        ..Default::default()
    });
    let store = MySqlGroupStore::new(db.clone(), "local".to_string());

    let error = store
        .try_get("group-1")
        .await
        .expect_err("a non-string driver must fail the strict read");

    assert!(error.to_string().contains("driver_bot"), "{error}");
    assert_eq!(query_count(&db), 1);
}

#[tokio::test]
async fn strict_single_group_read_propagates_invalid_notify_modes() {
    let group = row(vec![
        ("group_id", Value::from("group-1")),
        ("status", Value::from("active")),
        ("driver_bot", Value::from("driver")),
        ("opening_message_json", Value::Null),
        ("human_mention_notify_mode", Value::from("weird")),
    ]);
    let db = Arc::new(RecordingDbPlugin {
        query_results: StdMutex::new(VecDeque::from([vec![group], vec![participant_row()]])),
        ..Default::default()
    });
    let store = MySqlGroupStore::new(db.clone(), "local".to_string());

    let error = store
        .try_get("group-1")
        .await
        .expect_err("an unknown notify mode must fail the strict read");

    assert!(
        error.to_string().contains("human_mention_notify_mode"),
        "{error}"
    );
    assert_eq!(query_count(&db), 2);
}

#[tokio::test]
async fn list_projection_normalizes_invalid_notify_mode_in_memory() {
    let (_, store) = store_with_rows(vec![joined_row_fields(
        "g",
        "bot",
        "driver",
        Value::Null,
        Value::from("weird"),
    )]);

    let groups = store.load_all_groups_from_mysql().await;

    assert_eq!(groups.len(), 1);
    assert_eq!(
        groups[0].human_mention_notify_mode,
        HumanMentionNotifyMode::All
    );
    assert_eq!(groups[0].participants.len(), 1);
}

#[tokio::test]
async fn empty_results_and_equal_update_times_order_groups_by_id() {
    let (_, store) = store_with_rows(Vec::new());
    assert!(store.load_all_groups_from_mysql().await.is_empty());
    assert!(store.list_paginated_sql(0, 10).await.is_empty());
    assert!(store.find_by_participant_sql("bot").await.is_empty());
    assert!(store
        .find_by_participant_paginated_sql("bot", 0, 10)
        .await
        .is_empty());

    let (_, store) = store_with_rows(vec![
        joined_row_at("g-b", 100),
        joined_row_at("g-a", 100),
        joined_row_at("g-c", 200),
    ]);
    let groups = store.load_all_groups_from_mysql().await;
    assert_eq!(
        groups.iter().map(|group| group.id.as_str()).collect::<Vec<_>>(),
        ["g-c", "g-a", "g-b"]
    );
}

#[tokio::test]
async fn filtered_list_and_paginated_participant_find_each_issue_one_query() {
    let (db, store) = store_with_rows(vec![joined_row("g", "bot", "driver")]);
    store.list_paginated_filtered_sql(0, 10, None, None, None).await;
    assert_eq!(query_count(&db), 1);

    let (db, store) = store_with_rows(vec![joined_row("g", "bot", "driver")]);
    store.find_by_participant_paginated_sql("bot", 0, 10).await;
    assert_eq!(query_count(&db), 1);
}

#[tokio::test]
async fn strict_read_uses_one_query_per_collection_and_skips_members_when_missing() {
    let db = Arc::new(RecordingDbPlugin {
        query_results: StdMutex::new(VecDeque::from([
            vec![strict_group_row()],
            vec![participant_row()],
        ])),
        ..Default::default()
    });
    let store = MySqlGroupStore::new(db.clone(), "local".to_string());

    let group = store
        .try_get("group-1")
        .await
        .expect("strict read")
        .expect("group");
    assert_eq!(group.participants.len(), 1);
    assert_eq!(query_count(&db), 2);
    let sqls = db.query_sqls.lock().expect("query sqls").clone();
    assert!(sqls[0].contains("FROM bcs_groups"), "{}", sqls[0]);
    assert!(
        sqls[1].contains("FROM bcs_group_participants"),
        "{}",
        sqls[1]
    );

    let (db, store) = store_with_rows(Vec::new());
    assert!(store.try_get("missing").await.expect("strict read").is_none());
    assert_eq!(query_count(&db), 1);
}

#[tokio::test]
async fn large_and_cross_group_members_deduplicate_in_first_seen_order() {
    let mut rows = Vec::new();
    for index in 0..32 {
        let bot = format!("bot-{index:02}");
        let role = if index % 2 == 0 { "worker" } else { "observer" };
        rows.push(joined_row("big", &bot, role));
        // The duplicate row must not overwrite the first-seen role.
        rows.push(joined_row("big", &bot, "driver"));
        // The same member also belongs to another group, whose own set is
        // independent from this group's set.
        rows.push(joined_row("other", &bot, "manager"));
    }

    let (db, store) = store_with_rows(rows);
    let groups = store.load_all_groups_from_mysql().await;

    assert_eq!(query_count(&db), 1);
    assert_eq!(groups.len(), 2);
    let big = groups.iter().find(|group| group.id == "big").expect("big");
    assert_eq!(big.participants.len(), 32);
    assert_eq!(big.participants[0].role, ParticipantRole::Worker);
    assert_eq!(big.participants[1].role, ParticipantRole::Observer);
    let other = groups
        .iter()
        .find(|group| group.id == "other")
        .expect("other");
    assert_eq!(other.participants.len(), 32);
    assert!(other
        .participants
        .iter()
        .all(|participant| participant.role == ParticipantRole::Manager));
}

fn query_params(db: &Arc<RecordingDbPlugin>) -> Vec<Vec<Value>> {
    db.query_params.lock().expect("query params").clone()
}

fn env_param() -> Value {
    Value::from("local")
}

fn count_row(cnt: i64) -> DbRow {
    row(vec![("cnt", Value::from(cnt))])
}

#[tokio::test]
async fn count_entries_bind_only_the_env_and_issue_one_query() {
    let (db, store) = store_with_rows(Vec::new());
    assert_eq!(store.count().await, 0);
    assert_eq!(query_count(&db), 1);
    assert_eq!(query_params(&db), vec![vec![env_param()]]);

    let (db, store) = store_with_rows(Vec::new());
    assert_eq!(store.count_by_kind(None).await, 0);
    assert_eq!(query_count(&db), 1);
    assert_eq!(query_params(&db), vec![vec![env_param()]]);

    let (db, store) = store_with_rows(vec![count_row(2)]);
    assert_eq!(store.count_by_kind(Some(GroupKind::Dm)).await, 2);
    assert_eq!(query_count(&db), 1);
    assert_eq!(
        query_params(&db),
        vec![vec![env_param(), Value::from("dm")]]
    );
    let sqls = db.query_sqls.lock().expect("query sqls").clone();
    assert!(
        sqls[0].contains("WHERE env = ? AND group_kind = ?"),
        "unexpected kind count: {}",
        sqls[0]
    );
}

#[tokio::test]
async fn count_by_kind_keeps_the_zero_fallback_for_a_failed_query() {
    let db = Arc::new(RecordingDbPlugin::failing_queries());
    let store = MySqlGroupStore::new(db.clone(), "local".to_string());

    assert_eq!(store.count_by_kind(Some(GroupKind::Dm)).await, 0);
    assert_eq!(query_count(&db), 1);
    assert_eq!(
        query_params(&db),
        vec![vec![env_param(), Value::from("dm")]]
    );
}

#[tokio::test]
async fn paginated_lists_bind_env_limit_offset_and_optional_kind() {
    let (db, store) = store_with_rows(vec![joined_row("g", "bot", "driver")]);
    store.list_paginated_sql(5, 10).await;
    assert_eq!(query_count(&db), 1);
    assert_eq!(
        query_params(&db),
        vec![vec![
            env_param(),
            Value::from(10_i64),
            Value::from(5_i64),
            env_param(),
        ]]
    );
    let plain_sql = db.query_sqls.lock().expect("query sqls")[0].clone();

    let (db, store) = store_with_rows(vec![joined_row("g", "bot", "driver")]);
    store.list_paginated_by_kind_sql(None, 5, 10).await;
    assert_eq!(query_count(&db), 1);
    assert_eq!(
        query_params(&db),
        vec![vec![
            env_param(),
            Value::from(10_i64),
            Value::from(5_i64),
            env_param(),
        ]]
    );
    let no_kind_sql = db.query_sqls.lock().expect("query sqls")[0].clone();
    assert_eq!(
        plain_sql, no_kind_sql,
        "the unfiltered entries must share one pagination statement"
    );

    let (db, store) = store_with_rows(vec![joined_row("g", "bot", "driver")]);
    store
        .list_paginated_by_kind_sql(Some(GroupKind::Dm), 5, 10)
        .await;
    assert_eq!(query_count(&db), 1);
    assert_eq!(
        query_params(&db),
        vec![vec![
            env_param(),
            Value::from("dm"),
            Value::from(10_i64),
            Value::from(5_i64),
            env_param(),
        ]]
    );
    let kind_sql = db.query_sqls.lock().expect("query sqls")[0].clone();
    assert_eq!(
        kind_sql.matches("AND group_kind = ?").count(),
        1,
        "the kind fragment is only in the inner WHERE: {kind_sql}"
    );
    assert!(
        kind_sql.contains("WHERE env = ? AND group_kind = ? LIMIT ? OFFSET ?) gs"),
        "the kind filter must precede the inner LIMIT: {kind_sql}"
    );
    assert!(
        kind_sql.contains(
            "LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ?"
        ),
        "the outer participant JOIN literal must stay unchanged: {kind_sql}"
    );
}

// Minimal schema, deliberate; the full schema is exercised via
// run_sqlite_migrations elsewhere.
const SQLITE_GROUPS_DDL: &str = "CREATE TABLE bcs_groups ( \
     group_id TEXT PRIMARY KEY, \
     env TEXT NOT NULL, \
     label TEXT, \
     status TEXT NOT NULL, \
     driver_bot TEXT, \
     originator TEXT, \
     routing_policy_json TEXT, \
     context TEXT, \
     opening_message_json TEXT, \
     service_group_uuid TEXT, \
     service_mode TEXT, \
     version INTEGER, \
     record_status TEXT, \
     gmt_create TEXT, \
     gmt_modified TEXT, \
     group_kind TEXT, \
     dm_pair_key TEXT, \
     group_strategy TEXT, \
     visibility TEXT, \
     human_mention_notify_mode TEXT \
 )";

const SQLITE_PARTICIPANTS_DDL: &str = "CREATE TABLE bcs_group_participants ( \
     group_id TEXT NOT NULL, \
     env TEXT NOT NULL, \
     bot_uuid TEXT NOT NULL, \
     role TEXT, \
     actor_kind TEXT, \
     mode TEXT, \
     tags_json TEXT, \
     message_view_scope TEXT, \
     PRIMARY KEY (group_id, env, bot_uuid) \
 )";

async fn seed_sqlite_group(db: &dyn DbPlugin, id: &str, kind: &str, updated: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_groups (group_id, env, status, driver_bot, opening_message_json, \
         gmt_create, gmt_modified, group_kind, visibility, human_mention_notify_mode) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            Value::from(id),
            env_param(),
            Value::from("active"),
            Value::from("driver"),
            Value::Null,
            Value::from(updated),
            Value::from(updated),
            Value::from(kind),
            Value::from("private"),
            Value::from("all"),
        ],
    ))
    .await
    .expect("seed sqlite group");
}

async fn seed_sqlite_participant(db: &dyn DbPlugin, group_id: &str, bot_uuid: &str, role: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_group_participants \
         (group_id, env, bot_uuid, role, actor_kind, mode, tags_json, message_view_scope) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            Value::from(group_id),
            env_param(),
            Value::from(bot_uuid),
            Value::from(role),
            Value::from("bot"),
            Value::from("auto"),
            Value::Null,
            Value::from("full"),
        ],
    ))
    .await
    .expect("seed sqlite participant");
}

async fn sqlite_mixed_store() -> MySqlGroupStore {
    let db: Arc<dyn DbPlugin> = Arc::new(
        bcs_db_local::LocalSqliteDbPlugin::new().expect("sqlite db"),
    );
    db.execute(DbStatement::new(SQLITE_GROUPS_DDL))
        .await
        .expect("create groups table");
    db.execute(DbStatement::new(SQLITE_PARTICIPANTS_DDL))
        .await
        .expect("create participants table");

    seed_sqlite_group(db.as_ref(), "normal-a", "normal", "2026-11-23 10:00:00").await;
    seed_sqlite_participant(db.as_ref(), "normal-a", "a1", "driver").await;
    seed_sqlite_participant(db.as_ref(), "normal-a", "a2", "worker").await;
    seed_sqlite_group(db.as_ref(), "dm-a", "dm", "2026-11-23 11:00:00").await;
    seed_sqlite_participant(db.as_ref(), "dm-a", "d1", "driver").await;
    seed_sqlite_participant(db.as_ref(), "dm-a", "d2", "consultant").await;
    seed_sqlite_group(db.as_ref(), "normal-b", "normal", "2026-11-23 12:00:00").await;
    seed_sqlite_participant(db.as_ref(), "normal-b", "b1", "driver").await;
    seed_sqlite_group(db.as_ref(), "dm-b", "dm", "2026-11-23 13:00:00").await;
    seed_sqlite_participant(db.as_ref(), "dm-b", "e1", "driver").await;

    MySqlGroupStore::sqlite(db, "local".to_string())
}

#[tokio::test]
async fn sqlite_mixed_groups_page_and_count_by_kind_without_changing_selection() {
    let store = sqlite_mixed_store().await;

    assert_eq!(store.count().await, 4);
    assert_eq!(store.count_by_kind(None).await, 4);
    assert_eq!(store.count_by_kind(Some(GroupKind::Dm)).await, 2);
    assert_eq!(store.count_by_kind(Some(GroupKind::Normal)).await, 2);

    // The inner query has no ORDER BY, so only the page size and membership
    // are contracted, not which dm group comes first.
    let first_page = store
        .list_paginated_by_kind_sql(Some(GroupKind::Dm), 0, 1)
        .await;
    assert_eq!(first_page.len(), 1);
    assert_eq!(first_page[0].group_kind, GroupKind::Dm);

    let dm_groups = store
        .list_paginated_by_kind_sql(Some(GroupKind::Dm), 0, 10)
        .await;
    let mut dm_ids: Vec<_> = dm_groups.iter().map(|group| group.id.as_str()).collect();
    dm_ids.sort_unstable();
    assert_eq!(dm_ids, ["dm-a", "dm-b"]);
    assert!(dm_groups
        .iter()
        .all(|group| group.group_kind == GroupKind::Dm));

    let dm_a = dm_groups
        .iter()
        .find(|group| group.id == "dm-a")
        .expect("dm-a page member");
    let mut dm_members: Vec<_> = dm_a
        .participants
        .iter()
        .map(|participant| (participant.bot_uuid.as_str(), participant.role))
        .collect();
    dm_members.sort_unstable_by_key(|(bot_uuid, _)| *bot_uuid);
    assert_eq!(
        dm_members,
        [
            ("d1", ParticipantRole::Driver),
            ("d2", ParticipantRole::Consultant),
        ]
    );

    assert!(
        store
            .list_paginated_by_kind_sql(Some(GroupKind::Dm), 10, 10)
            .await
            .is_empty(),
        "an offset past the matching total must yield an empty page"
    );

    // The inner query has no ORDER BY, so pin the page size and kind only.
    let filtered_offset_page = store
        .list_paginated_by_kind_sql(Some(GroupKind::Dm), 1, 1)
        .await;
    assert_eq!(filtered_offset_page.len(), 1);
    assert_eq!(filtered_offset_page[0].group_kind, GroupKind::Dm);

    assert!(
        store
            .list_paginated_by_kind_sql(Some(GroupKind::Dm), 0, 0)
            .await
            .is_empty(),
        "a zero limit must yield an empty page"
    );

    let unfiltered = store.list_paginated_sql(0, 10).await;
    let mut all_ids: Vec<_> = unfiltered.iter().map(|group| group.id.as_str()).collect();
    all_ids.sort_unstable();
    assert_eq!(all_ids, ["dm-a", "dm-b", "normal-a", "normal-b"]);
    assert_eq!(store.list_paginated_sql(0, 2).await.len(), 2);
    assert_eq!(
        store.list_paginated_by_kind_sql(None, 0, 10).await.len(),
        unfiltered.len()
    );
}

#[tokio::test]
async fn bulk_projection_keeps_the_tolerant_driver_fallback() {
    let (_, store) = store_with_rows(vec![row(vec![
        ("group_id", Value::from("g")),
        ("status", Value::from("active")),
        ("driver_bot", Value::from(7_i64)),
        ("opening_message_json", Value::Null),
        ("human_mention_notify_mode", Value::Null),
        ("bot_uuid", Value::from("bot")),
        ("role", Value::from("driver")),
    ])]);

    let groups = store.load_all_groups_from_mysql().await;

    assert_eq!(groups.len(), 1);
    assert_eq!(
        groups[0].driver_bot, "",
        "the tolerant JOIN projector must never turn a list read into an error"
    );
}
