use super::*;

async fn profile_store() -> DbPermissionProfileStore {
    let db = LocalSqliteDbPlugin::new().expect("local sqlite");
    db.execute(DbStatement::new(
        "CREATE TABLE permission_profiles (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            bot_id VARCHAR(128) NOT NULL, \
            env VARCHAR(32) NOT NULL, \
            name VARCHAR(128) NOT NULL DEFAULT 'default', \
            description VARCHAR(512), \
            rules_template TEXT NOT NULL, \
            revision INTEGER NOT NULL DEFAULT 1, \
            digest VARCHAR(128) NOT NULL, \
            is_default INTEGER NOT NULL DEFAULT 0, \
            status VARCHAR(16) NOT NULL DEFAULT 'active', \
            created_by VARCHAR(128) NOT NULL, \
            updated_by VARCHAR(128), \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            UNIQUE (bot_id, env, is_default, status))",
    ))
    .await
    .expect("create permission_profiles");
    DbPermissionProfileStore::sqlite(Arc::new(db))
}

#[tokio::test]
async fn ensure_default_profile_idempotent() {
    let store = profile_store().await;
    // First call seeds the default profile.
    let profile_id = store
        .ensure_default_profile("bot_x", "dev")
        .await
        .expect("seed 1");
    // Second call must be a no-op (D12 rule 2): no overwrite, no revision bump.
    let profile_id_2 = store
        .ensure_default_profile("bot_x", "dev")
        .await
        .expect("seed 2");
    assert_eq!(profile_id, profile_id_2);

    let profile = store
        .get_active_default("bot_x", "dev")
        .await
        .expect("default exists");
    assert_eq!(profile.permission_profile_id, profile_id);
    assert!(profile.is_default);
    assert_eq!(profile.status, ProfileStatus::Active);
    assert_eq!(profile.created_by, "system");
    // revision stays 1 (idempotent, not bumped).
    assert_eq!(profile.revision, 1);
    // rules_template is the wildcard-allow seed.
    assert_eq!(
        profile.rules_template,
        serde_json::from_str::<serde_json::Value>(WILDCARD_ALLOW).unwrap()
    );
    assert_eq!(
        profile.digest,
        sha256_hex(WILDCARD_ALLOW),
        "digest must match sha256(rules_template)"
    );
}

#[tokio::test]
async fn upsert_revision_keeps_profile_id_bumps_revision() {
    let store = profile_store().await;
    store
        .ensure_default_profile("bot_y", "prod")
        .await
        .expect("seed");
    let seeded = store
        .get_active_default("bot_y", "prod")
        .await
        .expect("seeded default");
    assert_eq!(seeded.revision, 1);
    let seeded_profile_id = seeded.permission_profile_id.clone();

    // Bump to revision 2 with new rules + new digest. profile_id unchanged.
    let new_rules =
        serde_json::from_str::<serde_json::Value>(
            r#"[{"tool":"read","specifier":"*","effect":"allow"}]"#,
        )
        .unwrap();
    let new_digest = sha256_hex(&new_rules.to_string());
    let updated = PermissionProfile {
        permission_profile_id: seeded_profile_id.clone(),
        bot_id: seeded.bot_id.clone(),
        env: seeded.env.clone(),
        name: seeded.name.clone(),
        description: seeded.description.clone(),
        rules_template: new_rules.clone(),
        revision: 2,
        digest: new_digest.clone(),
        is_default: seeded.is_default,
        status: seeded.status,
        created_by: seeded.created_by.clone(),
        updated_by: Some("admin".to_string()),
    };
    store
        .upsert_revision(updated)
        .await
        .expect("upsert revision");

    let after = store
        .get_active_default("bot_y", "prod")
        .await
        .expect("updated default");
    // D12 rule 2: profile_id is unchanged.
    assert_eq!(after.permission_profile_id, seeded_profile_id);
    // revision + rules_template + digest bumped.
    assert_eq!(after.revision, 2);
    assert_eq!(after.rules_template, new_rules);
    assert_eq!(after.digest, new_digest);
    assert_eq!(after.updated_by.as_deref(), Some("admin"));
    // is_default/status/bot_id/env/name/created_by untouched.
    assert!(after.is_default);
    assert_eq!(after.status, ProfileStatus::Active);
    assert_eq!(after.bot_id, "bot_y");
    assert_eq!(after.env, "prod");
    assert_eq!(after.created_by, "system");
}

// ---- DbPermissionRequestStore (T9) ----

/// Request store backed by a fresh LocalSqliteDbPlugin with the full
/// `permission_requests` schema (mirrors 014_edge_permission.sql).
async fn request_store() -> DbPermissionRequestStore {
    let db = LocalSqliteDbPlugin::new().expect("local sqlite");
    db.execute(DbStatement::new(
        "CREATE TABLE permission_requests (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            request_id VARCHAR(64) NOT NULL, \
            edge_id INTEGER, \
            env VARCHAR(32) NOT NULL, \
            from_id VARCHAR(128) NOT NULL, \
            to_id VARCHAR(128) NOT NULL, \
            request_kind VARCHAR(32) NOT NULL, \
            requested_ref_id INTEGER, \
            requested_rules TEXT, \
            message TEXT, \
            status VARCHAR(16) NOT NULL DEFAULT 'pending', \
            decision_reason TEXT, \
            created_by VARCHAR(128) NOT NULL, \
            decided_by VARCHAR(128), \
            decided_at TEXT, \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .expect("create permission_requests");
    DbPermissionRequestStore::sqlite(Arc::new(db))
}

static REQUEST_ID_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

fn next_test_request_id() -> String {
    format!(
        "test_{}",
        REQUEST_ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
    )
}

fn sample_request(env: &str) -> PermissionRequest {
    PermissionRequest {
        request_id: next_test_request_id(),
        edge_id: None,
        env: env.to_string(),
        from_id: "human_a".to_string(),
        to_id: "bot_b".to_string(),
        request_kind: RequestKind::Connect,
        requested_ref_id: None,
        requested_rules: None,
        message: Some("hi".to_string()),
        status: RequestStatus::Pending,
        decision_reason: None,
        created_by: "human_a".to_string(),
        decided_by: None,
        decided_at: None,
        // DB-managed on insert; the store reads back gmt_create/gmt_modified.
        created_at: 0,
        updated_at: 0,
    }
}

#[tokio::test]
async fn request_insert_and_get() {
    let store = request_store().await;
    let req = sample_request("dev");
    let request_id = req.request_id.clone();
    store.insert(req).await.expect("insert");
    let got = store.get(&request_id, "dev").await.expect("found");
    assert_eq!(got.request_id, request_id);
    assert_eq!(got.status, RequestStatus::Pending);
    assert!(got.edge_id.is_none(), "pending → no edge_id");
    assert_eq!(got.request_kind, RequestKind::Connect);
    assert!(store.get("missing", "dev").await.is_none(), "missing → None");
}

#[tokio::test]
async fn request_list_inbox_all_and_status_filter() {
    let store = request_store().await;
    let r1 = sample_request("dev");
    let r1_id = r1.request_id.clone();
    store.insert(r1).await.expect("insert r1");
    let r2 = sample_request("dev");
    let r2_id = r2.request_id.clone();
    store.insert(r2).await.expect("insert r2");
    // decide r2 → approved
    store
        .decide(&r2_id, "dev", RequestStatus::Approved, "85020", Some("ok"))
        .await
        .expect("decide");
    let all = store.list_inbox("bot_b", "dev", None).await.expect("request listing");
    assert_eq!(all.len(), 2, "both visible without filter");
    let pending = store
        .list_inbox("bot_b", "dev", Some(RequestStatus::Pending))
        .await.expect("request listing");
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].request_id, r1_id);
    let approved = store
        .list_inbox("bot_b", "dev", Some(RequestStatus::Approved))
        .await.expect("request listing");
    assert_eq!(approved.len(), 1);
    assert_eq!(approved[0].request_id, r2_id);
    assert_eq!(approved[0].decided_by.as_deref(), Some("85020"));
    assert!(
        approved[0].decided_at.is_some(),
        "decided_at set to decision time"
    );
}

#[tokio::test]
async fn permission_request_roundtrip_carries_created_and_updated_at() {
    let store = request_store().await;
    let mut req = sample_request("dev");
    req.request_id = format!("ts-{}", next_test_request_id());
    let request_id = req.request_id.clone();
    store.insert(req).await.expect("insert");

    let got = store.get(&request_id, "dev").await.expect("found");
    assert!(got.created_at > 0, "created_at comes from DB gmt_create");
    assert!(got.updated_at > 0, "updated_at comes from DB gmt_modified");
    // DB CURRENT_TIMESTAMP has second resolution, so creation and first
    // modification may land in the same second; creation never exceeds it.
    assert!(
        got.created_at <= got.updated_at,
        "created_at {} must not exceed updated_at {}",
        got.created_at,
        got.updated_at
    );
    let first_updated_at = got.updated_at;

    store
        .decide(&request_id, "dev", RequestStatus::Approved, "decider", None)
        .await
        .expect("decide");
    let decided = store
        .get(&request_id, "dev")
        .await
        .expect("found after decide");
    assert_eq!(decided.status, RequestStatus::Approved);
    assert!(
        decided.updated_at >= first_updated_at,
        "updated_at {} must not regress from {}",
        decided.updated_at,
        first_updated_at
    );
    assert!(decided.decided_at.is_some(), "decide sets decided_at");
}

#[tokio::test]
async fn request_backfill_edge_id() {
    let store = request_store().await;
    let req = sample_request("dev");
    let request_id = req.request_id.clone();
    store.insert(req).await.expect("insert");
    store
        .backfill_edge_id(&request_id, "dev", 1001)
        .await
        .expect("backfill");
    let got = store.get(&request_id, "dev").await.expect("found");
    assert_eq!(got.edge_id, Some(1001));
}

#[tokio::test]
async fn find_request_matches_every_predicate_and_returns_none_on_miss() {
    let store = request_store().await;
    // Exact match (from/to/env/kind/status/created_by all equal).
    let exact = sample_request("dev");
    let exact_id = exact.request_id.clone();
    store.insert(exact).await.expect("insert exact");
    // Same pair + kind + status, but authored by someone else (the
    // Bot↔Bot reverse-row shape) must NOT match.
    let mut foreign = sample_request("dev");
    foreign.created_by = "bot_b".to_string();
    store.insert(foreign).await.expect("insert foreign-authored");
    // Different status / env rows stay isolated.
    let mut approved = sample_request("dev");
    approved.status = RequestStatus::Approved;
    store.insert(approved).await.expect("insert approved");
    store.insert(sample_request("pre")).await.expect("insert other env");

    let found = store
        .find_request(
            "human_a",
            "bot_b",
            "dev",
            RequestKind::Connect,
            RequestStatus::Pending,
            "human_a",
        )
        .await
        .expect("the exact lookup hits");
    assert_eq!(found.request_id, exact_id);
    assert_eq!(found.created_by, "human_a");
    // Positive control: the pre-env row is findable in its own env.
    assert!(
        store
            .find_request(
                "human_a",
                "bot_b",
                "pre",
                RequestKind::Connect,
                RequestStatus::Pending,
                "human_a",
            )
            .await
            .is_some(),
        "env scoping is per-row, not global"
    );

    for (from_id, to_id, env, status, created_by) in [
        ("human_a", "bot_c", "dev", RequestStatus::Pending, "human_a"), // no row for this target
        ("human_a", "bot_b", "dev", RequestStatus::Rejected, "human_a"), // no rejected row
        ("human_a", "bot_b", "pre", RequestStatus::Pending, "nobody"), // pre row author differs
        ("human_a", "bot_b", "dev", RequestStatus::Pending, "nobody"), // other author misses
    ] {
        assert!(
            store
                .find_request(from_id, to_id, env, RequestKind::Connect, status, created_by)
                .await
                .is_none(),
            "find_request must not match ({from_id}, {to_id}, {env}, {status:?}, {created_by})"
        );
    }
}

#[tokio::test]
async fn find_request_reads_at_most_one_bounded_row() {
    let seed = request_store().await;
    // A fat outbox: the duplicate guard must not materialize it.
    for _ in 0..10 {
        seed.insert(sample_request("dev")).await.expect("insert");
    }
    let db = Arc::new(RecordingFriendDb {
        inner: seed.db,
        reads: std::sync::Mutex::new(Vec::new()),
    });
    let store = DbPermissionRequestStore::sqlite(db.clone());
    let found = store
        .find_request(
            "human_a",
            "bot_b",
            "dev",
            RequestKind::Connect,
            RequestStatus::Pending,
            "human_a",
        )
        .await;
    assert!(found.is_some(), "a matching pending row exists");
    let reads = db.reads.lock().unwrap();
    assert_eq!(reads.len(), 1, "exactly one bounded statement");
    assert_eq!(reads[0].1, 1, "at most one row crosses the DB boundary");
    assert!(reads[0].0.sql().contains("LIMIT 1"));
}

// ---- DbBotActorConfigStore (T12) ----

/// Bot-config store backed by a fresh LocalSqliteDbPlugin with a minimal
/// `bcs_bots` schema (the decision cols + the soft-delete flag the read
/// filters on). Mirrors the bot-store decision fields plus `bot_info`
/// for the internal friend-gating attributes.
async fn bot_config_store() -> DbBotActorConfigStore {
    let db = LocalSqliteDbPlugin::new().expect("local sqlite");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (\
            bot_uuid TEXT NOT NULL, \
            env TEXT NOT NULL, \
            name TEXT NOT NULL DEFAULT '', \
            visibility TEXT NOT NULL DEFAULT 'public', \
            user_visibility TEXT NOT NULL DEFAULT 'protected', \
            friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL', \
            bot_info TEXT DEFAULT NULL, \
            friend_ext TEXT DEFAULT NULL, \
            status TEXT NOT NULL DEFAULT 'online', \
            created_by TEXT, \
            is_deleted INTEGER NOT NULL DEFAULT 0, \
            PRIMARY KEY (bot_uuid, env))",
    ))
    .await
    .expect("create bcs_bots");
    DbBotActorConfigStore::sqlite(Arc::new(db))
}

/// Seed a `bcs_bots` row.
async fn seed_bot(
    store: &DbBotActorConfigStore,
    bot_uuid: &str,
    env: &str,
    visibility: &str,
    user_visibility: &str,
    friend_check_in_strategy: &str,
    status: &str,
    created_by: Option<&str>,
) {
    seed_bot_with_friend_ext(
        store,
        bot_uuid,
        env,
        visibility,
        user_visibility,
        friend_check_in_strategy,
        status,
        created_by,
        serde_json::Map::new(),
    )
    .await;
}

async fn seed_bot_with_friend_ext(
    store: &DbBotActorConfigStore,
    bot_uuid: &str,
    env: &str,
    visibility: &str,
    user_visibility: &str,
    friend_check_in_strategy: &str,
    status: &str,
    created_by: Option<&str>,
    friend_ext: serde_json::Map<String, serde_json::Value>,
) {
    let friend_ext_json = serde_json::to_string(&friend_ext).expect("friend_ext json");
    let bot_info = serde_json::json!({
        "friend_check_in_strategy": friend_check_in_strategy,
        "friend_ext": friend_ext,
    });
    store
        .execute(
            "seed_bot",
            DbStatement::with_params(
                "INSERT INTO bcs_bots \
                 (bot_uuid, env, name, visibility, user_visibility, friend_check_in_strategy, bot_info, friend_ext, status, created_by) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                vec![
                    DbValue::from(bot_uuid),
                    DbValue::from(env),
                    DbValue::from(bot_uuid),
                    DbValue::from(visibility),
                    DbValue::from(user_visibility),
                    DbValue::from(friend_check_in_strategy),
                    DbValue::from(serde_json::to_string(&bot_info).expect("bot_info json")),
                    DbValue::from(friend_ext_json),
                    DbValue::from(status),
                    match created_by {
                        Some(v) => DbValue::from(v),
                        None => DbValue::Null,
                    },
                ],
            ),
        )
        .await
        .expect("seed bot");
}

#[tokio::test]
async fn bot_actor_config_get_roundtrip() {
    let store = bot_config_store().await;
    // Public, human-addable, auto-approval bot owned by user 85020.
    seed_bot(
        &store,
        "20260421_x:85020",
        "dev",
        "public",
        "protected",
        "APPROVAL",
        "online",
        Some("85020"),
    )
    .await;
    let cfg = store
        .get("20260421_x:85020", "dev")
        .await
        .expect("bot exists");
    assert_eq!(cfg.bot_id, "20260421_x:85020");
    assert_eq!(cfg.env, "dev");
    assert_eq!(cfg.name, "20260421_x:85020");
    assert_eq!(cfg.visibility, "public");
    assert_eq!(cfg.user_visibility, "protected");
    assert_eq!(cfg.friend_check_in_strategy, "APPROVAL");
    assert!(cfg.friend_ext.is_empty());
    assert_eq!(cfg.status, "online");
    assert_eq!(cfg.created_by.as_deref(), Some("85020"));
}
#[tokio::test]
async fn bot_actor_config_reads_friend_strategy_from_column() {
    let store = bot_config_store().await;
    store
        .execute(
            "seed_bot_column_strategy",
            DbStatement::with_params(
                "INSERT INTO bcs_bots \
                 (bot_uuid, env, name, visibility, user_visibility, friend_check_in_strategy, bot_info, friend_ext, status, created_by) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                vec![
                    DbValue::from("column-strategy-bot"),
                    DbValue::from("dev"),
                    DbValue::from("column-strategy-bot"),
                    DbValue::from("protected"),
                    DbValue::from("public"),
                    DbValue::from("OPEN"),
                    DbValue::from(r#"{"friend_ext": {"scope": "bot_info"}}"#),
                    DbValue::from(r#"{"scope": "column"}"#),
                    DbValue::from("online"),
                    DbValue::Null,
                ],
            ),
        )
        .await
        .expect("seed bot with column strategy");

    let cfg = store
        .get("column-strategy-bot", "dev")
        .await
        .expect("bot exists");
    assert_eq!(cfg.friend_check_in_strategy, "OPEN");
    assert_eq!(cfg.friend_ext["scope"], "column");
}

#[test]
fn bot_actor_config_accepts_utf8_bytes_for_json_text_columns() {
    let mut row = std::collections::BTreeMap::new();
    row.insert("bot_uuid".to_string(), DbValue::String("bytes-json-bot".to_string()));
    row.insert("env".to_string(), DbValue::String("prod".to_string()));
    row.insert("name".to_string(), DbValue::String("bytes-json-bot".to_string()));
    row.insert("visibility".to_string(), DbValue::String("protected".to_string()));
    row.insert("user_visibility".to_string(), DbValue::String("public".to_string()));
    row.insert("friend_check_in_strategy".to_string(), DbValue::String("OPEN".to_string()));
    row.insert(
        "bot_info".to_string(),
        DbValue::Bytes(br#"{"friend_ext":{"scope":"bot_info"}}"#.to_vec()),
    );
    row.insert(
        "friend_ext".to_string(),
        DbValue::Bytes(br#"{"scope":"column"}"#.to_vec()),
    );
    row.insert("status".to_string(), DbValue::String("online".to_string()));
    row.insert("created_by".to_string(), DbValue::String("owner-1".to_string()));

    let cfg = row_to_bot_actor_config(&DbRow::new(row)).expect("bytes JSON columns parse");

    assert_eq!(cfg.bot_id, "bytes-json-bot");
    assert_eq!(cfg.env, "prod");
    assert_eq!(cfg.friend_ext["scope"], "column");
    assert_eq!(cfg.created_by.as_deref(), Some("owner-1"));
}

#[tokio::test]
async fn bot_actor_config_missing_returns_none_and_legacy_owner() {
    let store = bot_config_store().await;
    // Missing bot -> None (non-fallible).
    assert!(store.get("nope", "dev").await.is_none());
    // Different env -> None (PK is bot_uuid + env).
    seed_bot(&store, "bot_b", "prod", "protected", "private", "DEPT_FREE", "hidden", None).await;
    assert!(store.get("bot_b", "dev").await.is_none());
    // Same env row reads back; user_visibility comes from its column,
    // friend_check_in_strategy comes from its dedicated column, and
    // friend_ext now comes from its dedicated column with bot_info fallback.
    let cfg = store.get("bot_b", "prod").await.expect("bot exists in prod");
    assert_eq!(cfg.user_visibility, "private");
    assert_eq!(cfg.friend_check_in_strategy, "DEPT_FREE");
    assert!(cfg.friend_ext.is_empty());
    assert_eq!(cfg.status, "hidden");
    assert!(cfg.created_by.is_none(), "legacy bot has no created_by");
}

#[tokio::test]
async fn request_lists_propagate_sql_and_row_decoding_failures() {
    let store = request_store().await;
    store.insert(sample_request("dev")).await.unwrap();
    store.db.execute(DbStatement::new(
        "UPDATE permission_requests SET gmt_create = 'invalid-timestamp'"
    )).await.unwrap();
    for status in [None, Some(RequestStatus::Pending)] {
        assert!(store.list_inbox("bot_b", "dev", status).await.is_err());
        assert!(store.list_sent("human_a", "dev", status).await.is_err());
    }
    store.db.execute(DbStatement::new("DROP TABLE permission_requests")).await.unwrap();
    for status in [None, Some(RequestStatus::Pending)] {
        assert!(store.list_inbox("bot_b", "dev", status).await.is_err());
        assert!(store.list_sent("human_a", "dev", status).await.is_err());
    }
}
