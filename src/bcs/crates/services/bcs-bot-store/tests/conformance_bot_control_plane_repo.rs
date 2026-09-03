#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue as Value, db_get_column, db_get_column_opt,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateReadQuery, BotCandidateVisibility, BotCapabilities, BotSearchCandidateQuery, BotSearchFriendshipFilter,
    BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, BotControlPlaneRepoPort, BotRepoPort, BotTaskModesQuery,
    FriendCheckInStrategy, TaskModeMatch, UserVisibility,
};
use bcs_service_api::types::ServiceError;
use tokio::sync::Barrier;

#[tokio::test]
async fn memory_control_plane_supports_both_kinds_candidates_and_patch_timestamps() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "acting-memory".to_string(),
        BotCapabilities {
            name: Some("Acting Memory".to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        "staff-1",
        "acting-token",
    )
    .await
    .expect("register acting bot");
    repo.register_with_owner_and_token(
        "friend-memory".to_string(),
        BotCapabilities {
            name: Some("Friend Memory".to_string()),
            summary: Some("friend".to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        "staff-2",
        "friend-token",
    )
    .await
    .expect("register friend bot");
    repo.register_with_owner_and_token(
        "default-visibility-memory".to_string(),
        BotCapabilities {
            name: Some("Default Visibility".to_string()),
            ..Default::default()
        },
        "staff-2",
        "default-visibility-token",
    )
    .await
    .expect("register default visibility bot");
    repo.ensure_human_actor("staff-1", "Memory Human")
        .await
        .expect("ensure human");

    bcs_test_support::contract::repo::bot_control_plane_repo_port_contract_tests(
        &repo,
        &env,
        "acting-memory",
    )
    .await;

    let human = repo
        .get_control_plane("human_staff-1", &env)
        .await
        .expect("get human")
        .expect("human exists");
    assert_eq!(human.kind, ActorKind::Human);
    assert_eq!(
        repo.get_control_plane("default-visibility-memory", &env)
            .await
            .expect("get default visibility bot")
            .expect("default visibility bot exists")
            .visibility,
        "protected"
    );

    let (candidates, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting-memory".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Collaboration,
            friend_ids: HashSet::from(["friend-memory".to_string()]),
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("memory candidates");
    assert_eq!(total, 1);
    assert_eq!(candidates[0].bot.bot_id, "friend-memory");
    assert!(candidates[0].is_friend);

    let before = repo
        .get_control_plane("acting-memory", &env)
        .await
        .expect("get acting")
        .expect("acting exists");
    tokio::time::sleep(std::time::Duration::from_millis(2)).await;
    let after = repo
        .patch_control_plane(
            "acting-memory",
            &env,
            BotControlPlanePatch {
                name: Some("Acting Renamed".to_string()),
                descriptor: Some(BotControlPlaneDescriptorPatch {
                    domains: Some(vec!["memory".to_string()]),
                    ..Default::default()
                }),
                ..Default::default()
            },
        )
        .await
        .expect("patch memory")
        .expect("memory bot exists");
    assert_eq!(after.name, "Acting Renamed");
    assert_eq!(after.descriptor.domains, vec!["memory"]);
    assert_eq!(after.created_at, before.created_at);
    assert!(after.updated_at > before.updated_at);
}

#[tokio::test]
async fn memory_control_plane_restores_internal_attributes_from_persisted_capabilities() {
    let temp = tempfile::tempdir().expect("temp dir");
    let env = bcs_config::resolve_env_str();
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    repo.register_with_owner_and_token(
        "memory-attributes".to_string(),
        BotCapabilities {
            name: Some("Memory Attributes".to_string()),
            ..Default::default()
        },
        "staff-1",
        "token-1",
    )
    .await
    .expect("register memory bot");
    repo.patch_control_plane(
        "memory-attributes",
        &env,
        BotControlPlanePatch {
            user_visibility: Some(UserVisibility::Public),
            friend_ext: Some(serde_json::Map::from_iter([(
                "team".to_string(),
                serde_json::json!("platform"),
            )])),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        },
    )
    .await
    .expect("patch memory bot");

    let restored = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    restored
        .register(
            "memory-attributes".to_string(),
            BotCapabilities {
                name: Some("Memory Attributes".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("restore memory bot");
    let record = restored
        .get_control_plane("memory-attributes", &env)
        .await
        .expect("read restored bot")
        .expect("restored bot exists");
    assert_eq!(record.user_visibility, UserVisibility::Public);
    assert_eq!(record.friend_ext["team"], "platform");
    assert_eq!(record.friend_check_in_strategy, FriendCheckInStrategy::Open);
}

#[tokio::test]
async fn memory_control_plane_concurrent_partial_patches_preserve_both_changes() {
    let temp = tempfile::tempdir().expect("temp dir");
    let env = bcs_config::resolve_env_str();
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    repo.register_with_owner_and_token(
        "memory-concurrent-attributes".to_string(),
        BotCapabilities {
            name: Some("Memory Concurrent Attributes".to_string()),
            ..Default::default()
        },
        "staff-1",
        "token-1",
    )
    .await
    .expect("register memory bot");

    let start = Arc::new(Barrier::new(3));
    let visibility_repo = repo.clone();
    let visibility_start = start.clone();
    let visibility_env = env.clone();
    let visibility_patch = tokio::spawn(async move {
        visibility_start.wait().await;
        visibility_repo
            .patch_control_plane(
                "memory-concurrent-attributes",
                &visibility_env,
                BotControlPlanePatch {
                    user_visibility: Some(UserVisibility::Private),
                    ..Default::default()
                },
            )
            .await
    });
    let strategy_repo = repo.clone();
    let strategy_start = start.clone();
    let strategy_env = env.clone();
    let strategy_patch = tokio::spawn(async move {
        strategy_start.wait().await;
        strategy_repo
            .patch_control_plane(
                "memory-concurrent-attributes",
                &strategy_env,
                BotControlPlanePatch {
                    friend_check_in_strategy: Some(FriendCheckInStrategy::DeptFree),
                    ..Default::default()
                },
            )
            .await
    });
    start.wait().await;
    visibility_patch
        .await
        .expect("visibility patch task")
        .expect("visibility patch");
    strategy_patch
        .await
        .expect("strategy patch task")
        .expect("strategy patch");

    let record = repo
        .get_control_plane("memory-concurrent-attributes", &env)
        .await
        .expect("read memory bot")
        .expect("memory bot exists");
    assert_eq!(record.user_visibility, UserVisibility::Private);
    assert_eq!(
        record.friend_check_in_strategy,
        FriendCheckInStrategy::DeptFree
    );
}

#[tokio::test]
async fn persistent_control_plane_reads_project_audit_fields_and_preserve_batch_order() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "bot-a",
        "Alpha",
        "bot",
        "public",
        "online",
        Some("staff-1"),
        "2026-01-02 03:04:05",
    )
    .await;

    bcs_test_support::contract::repo::bot_control_plane_repo_port_contract_tests(
        &repo, "dev", "bot-a",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "human-1",
        "Human",
        "human",
        "protected",
        "hidden",
        Some("staff-1"),
        "2026-01-01 03:04:05",
    )
    .await;

    let bot = repo
        .get_control_plane("bot-a", "dev")
        .await
        .expect("get bot")
        .expect("bot exists");
    assert_eq!(bot.kind, ActorKind::Bot);
    assert_eq!(bot.status, ActorStatus::Online);
    assert_eq!(bot.descriptor.summary, "summary-bot-a");
    assert_eq!(bot.agent_code.as_deref(), Some("agent-bot-a"));
    assert!(bot.created_at > 0);
    assert_eq!(bot.created_at, bot.updated_at);

    let rows = repo
        .get_control_plane_by_ids(
            &[
                "human-1".to_string(),
                "missing".to_string(),
                "bot-a".to_string(),
                "human-1".to_string(),
            ],
            "dev",
        )
        .await
        .expect("batch query");
    assert_eq!(
        rows.iter()
            .map(|row| row.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["human-1", "bot-a"]
    );
}

#[tokio::test]
async fn persistent_control_plane_candidates_apply_purpose_and_stable_ordering() {
    let (repo, db) = fixture().await;
    for (id, name, kind, visibility, created) in [
        ("acting", "Acting", "bot", "private", "2026-01-05 00:00:00"),
        (
            "public-a",
            "Public A",
            "bot",
            "public",
            "2026-01-04 00:00:00",
        ),
        (
            "public-b",
            "Public B",
            "bot",
            "public",
            "2026-01-04 00:00:00",
        ),
        (
            "protected",
            "Protected",
            "bot",
            "protected",
            "2026-01-03 00:00:00",
        ),
        (
            "private-friend",
            "Private Friend",
            "bot",
            "private",
            "2026-01-02 00:00:00",
        ),
        (
            "human-row",
            "Human Row",
            "human",
            "public",
            "2026-01-06 00:00:00",
        ),
    ] {
        seed_bot(
            db.as_ref(),
            id,
            name,
            kind,
            visibility,
            "online",
            Some("staff-1"),
            created,
        )
        .await;
    }
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_friendships (left_bot, right_bot, env) VALUES (?, ?, ?)",
        vec![
            Value::from("acting"),
            Value::from("private-friend"),
            Value::from("dev"),
        ],
    ))
    .await
    .expect("insert friendship");

    let (discovery, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["private-friend".to_string()]),
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("discovery candidates");
    assert_eq!(total, 3);
    assert_eq!(
        discovery
            .iter()
            .map(|row| row.bot.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["public-a", "public-b", "protected"]
    );
    assert!(discovery.iter().all(|row| !row.is_friend));

    let (without_friends, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Collaboration,
            friend_ids: HashSet::new(),
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("collaboration candidates without supplied friends");
    assert_eq!(total, 2);
    assert_eq!(
        without_friends
            .iter()
            .map(|row| row.bot.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["public-a", "public-b"]
    );

    let (collaboration, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Collaboration,
            friend_ids: HashSet::from(["private-friend".to_string()]),
            name: Some("  ".to_string()),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("collaboration candidates");
    assert_eq!(total, 3);
    assert_eq!(
        collaboration
            .iter()
            .map(|row| (row.bot.bot_id.as_str(), row.is_friend))
            .collect::<Vec<_>>(),
        vec![
            ("public-a", false),
            ("public-b", false),
            ("private-friend", true),
        ]
    );
}

#[tokio::test]
async fn persistent_control_plane_name_filters_treat_sql_wildcards_as_literals() {
    let (repo, db) = fixture().await;
    for (id, name, owner) in [
        ("acting", "Acting", "staff-1"),
        ("literal-percent", "100% Real", "staff-1"),
        ("wildcard-match", "100x Real", "staff-1"),
    ] {
        seed_bot(
            db.as_ref(),
            id,
            name,
            "bot",
            "public",
            "online",
            Some(owner),
            "2026-01-01 00:00:00",
        )
        .await;
    }

    let (candidates, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: Some("%".to_string()),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("literal candidate filter");
    assert_eq!(total, 1);
    assert_eq!(candidates[0].bot.bot_id, "literal-percent");

    let owned = repo
        .list_control_plane_by_creator(BotControlPlaneOwnedQuery {
            created_by: "staff-1".to_string(),
            env: "dev".to_string(),
            kind: None,
            name: Some("%".to_string()),
            status: None,
        })
        .await
        .expect("literal owned filter");
    assert_eq!(owned.len(), 1);
    assert_eq!(owned[0].bot_id, "literal-percent");
}

#[tokio::test]
async fn persistent_control_plane_owned_filters_and_patch_replace_descriptor_arrays() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "owned",
        "Owned Planner",
        "bot",
        "public",
        "online",
        Some("staff-1"),
        "2026-01-01 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "other",
        "Other Planner",
        "bot",
        "public",
        "online",
        Some("staff-2"),
        "2026-01-02 00:00:00",
    )
    .await;

    let owned = repo
        .list_control_plane_by_creator(BotControlPlaneOwnedQuery {
            created_by: "staff-1".to_string(),
            env: "dev".to_string(),
            kind: Some(ActorKind::Bot),
            name: Some(" planner ".to_string()),
            status: Some(ActorStatus::Online),
        })
        .await
        .expect("owned rows");
    assert_eq!(owned.len(), 1);
    let before = &owned[0];

    tokio::time::sleep(std::time::Duration::from_millis(1_100)).await;
    let updated = repo
        .patch_control_plane(
            "owned",
            "dev",
            BotControlPlanePatch {
                name: Some("Renamed".to_string()),
                visibility: Some("protected".to_string()),
                status: Some(ActorStatus::Hidden),
                descriptor: Some(BotControlPlaneDescriptorPatch {
                    summary: Some("new summary".to_string()),
                    domains: Some(vec![]),
                    skills: None,
                    scopes: Some(vec!["new-scope".to_string()]),
                }),
                task_claim_mode: None,
                task_dream_mode: None,
                ..Default::default()
            },
        )
        .await
        .expect("patch row")
        .expect("patched row");
    assert_eq!(updated.name, "Renamed");
    assert_eq!(updated.visibility, "protected");
    assert_eq!(updated.status, ActorStatus::Hidden);
    assert_eq!(updated.descriptor.summary, "new summary");
    assert!(updated.descriptor.domains.is_empty());
    assert_eq!(updated.descriptor.skills, before.descriptor.skills);
    assert_eq!(updated.descriptor.scopes, vec!["new-scope"]);
    assert_eq!(updated.agent_code, before.agent_code);
    assert_eq!(updated.created_at, before.created_at);
    assert!(updated.updated_at > before.updated_at);

    let credential = db
        .query(DbStatement::with_params(
            "SELECT session_token FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![Value::from("owned"), Value::from("dev")],
        ))
        .await
        .expect("read credential");
    assert_eq!(
        credential[0].get("session_token").and_then(Value::as_str),
        Some("token-owned")
    );
}

#[tokio::test]
async fn persistent_control_plane_patch_returns_existing_row_when_mysql_changes_nothing() {
    let repo = PersistentBotRepo::with_plugins_flavor_and_cache_key_prefix(
        Arc::new(InMemoryCachePlugin::new()),
        Arc::new(UnchangedUpdateDb),
        DbSqlFlavor::Mysql,
        "test:",
    );

    let updated = repo
        .patch_control_plane(
            "unchanged",
            "dev",
            BotControlPlanePatch {
                name: Some("Unchanged".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("unchanged patch must not fail")
        .expect("an unchanged existing row must not be reported as missing");

    assert_eq!(updated.bot_id, "unchanged");
    assert_eq!(updated.name, "Unchanged");
}

#[tokio::test]
async fn persistent_control_plane_internal_attributes_round_trip_and_clear_friend_ext() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "attributes",
        "Attributes",
        "bot",
        "protected",
        "online",
        Some("staff-1"),
        "2026-01-01 00:00:00",
    )
    .await;

    let legacy = repo
        .get_control_plane("attributes", "dev")
        .await
        .expect("read legacy row")
        .expect("legacy row exists");
    assert_eq!(legacy.user_visibility, UserVisibility::Protected);
    assert!(legacy.friend_ext.is_empty());
    assert_eq!(
        legacy.friend_check_in_strategy,
        FriendCheckInStrategy::Approval
    );

    let updated = repo
        .patch_control_plane(
            "attributes",
            "dev",
            BotControlPlanePatch {
                visibility: Some("private".to_string()),
                user_visibility: Some(UserVisibility::Private),
                friend_ext: Some(serde_json::Map::from_iter([(
                    "scope".to_string(),
                    serde_json::json!("engineering"),
                )])),
                friend_check_in_strategy: Some(FriendCheckInStrategy::DeptFree),
                ..Default::default()
            },
        )
        .await
        .expect("patch internal attributes")
        .expect("patched row exists");
    assert_eq!(updated.visibility, "private");
    assert_eq!(updated.user_visibility, UserVisibility::Private);
    assert_eq!(updated.friend_ext["scope"], "engineering");
    assert_eq!(
        updated.friend_check_in_strategy,
        FriendCheckInStrategy::DeptFree
    );
    let rows = db
        .query(DbStatement::with_params(
            "SELECT visibility, user_visibility, friend_ext, friend_check_in_strategy, \
                    json_extract(bot_info, '$.user_visibility') AS bot_info_user_visibility, \
                    json_extract(bot_info, '$.friend_ext') AS bot_info_friend_ext, \
                    json_extract(bot_info, '$.friend_check_in_strategy') AS bot_info_friend_check_in_strategy \
             FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![Value::from("attributes"), Value::from("dev")],
        ))
        .await
        .expect("read physical attributes");
    let row = rows.first().expect("physical attributes row");
    assert_eq!(
        db_get_column::<String>(row, "visibility").expect("visibility"),
        "private"
    );
    assert_eq!(
        db_get_column::<String>(row, "user_visibility").expect("user visibility"),
        "private"
    );
    assert_eq!(
        db_get_column::<String>(row, "friend_ext").expect("friend extension"),
        r#"{"scope":"engineering"}"#
    );
    assert_eq!(
        db_get_column::<String>(row, "friend_check_in_strategy").expect("strategy"),
        "DEPT_FREE"
    );
    for column in [
        "bot_info_user_visibility",
        "bot_info_friend_ext",
        "bot_info_friend_check_in_strategy",
    ] {
        assert!(
            db_get_column_opt::<String>(row, column)
                .expect("bot info attribute")
                .is_none(),
            "{column} must not be written to bot_info"
        );
    }

    let cleared = repo
        .patch_control_plane(
            "attributes",
            "dev",
            BotControlPlanePatch {
                friend_ext: Some(serde_json::Map::new()),
                ..Default::default()
            },
        )
        .await
        .expect("clear friend extension")
        .expect("cleared row exists");
    assert!(cleared.friend_ext.is_empty());
    assert_eq!(cleared.user_visibility, UserVisibility::Private);
    assert_eq!(
        cleared.friend_check_in_strategy,
        FriendCheckInStrategy::DeptFree
    );
}

#[tokio::test]
async fn persistent_control_plane_rejects_invalid_physical_internal_attributes() {
    let (repo, db) = fixture().await;
    for bot_id in [
        "invalid-user-visibility",
        "invalid-friend-ext",
        "invalid-friend-check-in-strategy",
    ] {
        seed_bot(
            db.as_ref(),
            bot_id,
            "Invalid Attributes",
            "bot",
            "protected",
            "online",
            Some("staff-1"),
            "2026-01-01 00:00:00",
        )
        .await;
    }
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET user_visibility = ? WHERE bot_uuid = ? AND env = ?",
        vec![
            Value::from("invalid"),
            Value::from("invalid-user-visibility"),
            Value::from("dev"),
        ],
    ))
    .await
    .expect("corrupt user visibility");
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET friend_ext = ? WHERE bot_uuid = ? AND env = ?",
        vec![
            Value::from("[]"),
            Value::from("invalid-friend-ext"),
            Value::from("dev"),
        ],
    ))
    .await
    .expect("corrupt friend extension");
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET friend_check_in_strategy = ? WHERE bot_uuid = ? AND env = ?",
        vec![
            Value::from("UNKNOWN"),
            Value::from("invalid-friend-check-in-strategy"),
            Value::from("dev"),
        ],
    ))
    .await
    .expect("corrupt friend check-in strategy");

    for bot_id in [
        "invalid-user-visibility",
        "invalid-friend-ext",
        "invalid-friend-check-in-strategy",
    ] {
        let error = repo
            .get_control_plane(bot_id, "dev")
            .await
            .expect_err("invalid persisted attributes must fail closed");
        assert!(matches!(error, ServiceError::InternalError(_)));
    }
}

#[tokio::test]
async fn persistent_capability_save_preserves_patched_internal_attributes() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "lifecycle-attributes",
        "Lifecycle Attributes",
        "bot",
        "protected",
        "online",
        Some("staff-1"),
        "2026-01-01 00:00:00",
    )
    .await;
    let patched = repo.patch_control_plane(
        "lifecycle-attributes",
        "dev",
        BotControlPlanePatch {
            user_visibility: Some(UserVisibility::Private),
            friend_ext: Some(serde_json::Map::from_iter([(
                "source".to_string(),
                serde_json::json!("control-plane"),
            )])),
            friend_check_in_strategy: Some(FriendCheckInStrategy::DeptFree),
            ..Default::default()
        },
    )
    .await
    .expect("patch internal attributes")
    .expect("patched bot exists");
    assert_eq!(patched.user_visibility, UserVisibility::Private);
    assert_eq!(
        patched.friend_check_in_strategy,
        FriendCheckInStrategy::DeptFree
    );

    repo.save_to_storage(
        "lifecycle-attributes",
        &BotCapabilities {
            name: Some("Lifecycle Attributes Saved".to_string()),
            summary: Some("saved capabilities".to_string()),
            visibility: "protected".to_string(),
            ..Default::default()
        },
    )
    .await
    .expect("save capabilities");

    let record = repo
        .get_control_plane("lifecycle-attributes", "dev")
        .await
        .expect("read lifecycle bot")
        .expect("lifecycle bot exists");
    assert_eq!(record.name, "Lifecycle Attributes Saved");
    assert_eq!(record.user_visibility, UserVisibility::Private);
    assert_eq!(record.friend_ext["source"], "control-plane");
    assert_eq!(
        record.friend_check_in_strategy,
        FriendCheckInStrategy::DeptFree
    );
}

#[tokio::test]
async fn persistent_control_plane_concurrent_partial_patches_preserve_both_changes() {
    let db = sqlite_db().await;
    seed_bot(
        db.as_ref(),
        "persistent-concurrent-attributes",
        "Persistent Concurrent Attributes",
        "bot",
        "protected",
        "online",
        Some("staff-1"),
        "2026-01-01 00:00:00",
    )
    .await;
    let synchronized_db: Arc<dyn DbPlugin> = Arc::new(SynchronizedSnapshotDb {
        inner: db,
        snapshot_read_barrier: Barrier::new(2),
    });
    let repo = Arc::new(PersistentBotRepo::with_plugins_flavor_and_cache_key_prefix(
        Arc::new(InMemoryCachePlugin::new()),
        synchronized_db,
        DbSqlFlavor::Sqlite,
        "test:",
    ));

    let visibility_repo = repo.clone();
    let visibility_patch = tokio::spawn(async move {
        visibility_repo
            .patch_control_plane(
                "persistent-concurrent-attributes",
                "dev",
                BotControlPlanePatch {
                    user_visibility: Some(UserVisibility::Private),
                    ..Default::default()
                },
            )
            .await
    });
    let strategy_repo = repo.clone();
    let strategy_patch = tokio::spawn(async move {
        strategy_repo
            .patch_control_plane(
                "persistent-concurrent-attributes",
                "dev",
                BotControlPlanePatch {
                    friend_check_in_strategy: Some(FriendCheckInStrategy::DeptFree),
                    ..Default::default()
                },
            )
            .await
    });
    visibility_patch
        .await
        .expect("visibility patch task")
        .expect("visibility patch");
    strategy_patch
        .await
        .expect("strategy patch task")
        .expect("strategy patch");

    let record = repo
        .get_control_plane("persistent-concurrent-attributes", "dev")
        .await
        .expect("read persistent bot")
        .expect("persistent bot exists");
    assert_eq!(record.user_visibility, UserVisibility::Private);
    assert_eq!(
        record.friend_check_in_strategy,
        FriendCheckInStrategy::DeptFree
    );
}

struct SynchronizedSnapshotDb {
    inner: Arc<dyn DbPlugin>,
    snapshot_read_barrier: Barrier,
}

#[async_trait]
impl DbPlugin for SynchronizedSnapshotDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let synchronize_snapshot = statement
            .sql()
            .starts_with("SELECT bot_info FROM bcs_bots");
        let rows = self.inner.query(statement).await?;
        if synchronize_snapshot {
            self.snapshot_read_barrier.wait().await;
        }
        Ok(rows)
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

struct UnchangedUpdateDb;

#[async_trait]
impl DbPlugin for UnchangedUpdateDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        Ok(vec![DbRow::new(BTreeMap::from([
            ("bot_uuid".to_string(), Value::from("unchanged")),
            ("name".to_string(), Value::from("Unchanged")),
            ("bot_info".to_string(), Value::from("{}")),
            ("visibility".to_string(), Value::from("protected")),
            ("status".to_string(), Value::from("online")),
            ("actor_kind".to_string(), Value::from("bot")),
            ("env".to_string(), Value::from("dev")),
            ("created_by".to_string(), Value::from("staff-1")),
            ("agent_code".to_string(), Value::Null),
            ("gmt_create_ms".to_string(), Value::from(1_000_i64)),
            ("gmt_modified_ms".to_string(), Value::from(1_000_i64)),
        ]))])
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        Ok(DbExecuteResult {
            affected_rows: 0,
            last_insert_id: None,
        })
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        Ok(Vec::new())
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

async fn fixture() -> (PersistentBotRepo, Arc<dyn DbPlugin>) {
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_plugins_flavor_and_cache_key_prefix(
        Arc::new(InMemoryCachePlugin::new()),
        db.clone(),
        DbSqlFlavor::Sqlite,
        "test:",
    );
    (repo, db)
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bot_uuid TEXT NOT NULL,
            name TEXT NOT NULL,
            bot_info TEXT,
            session_token TEXT,
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            env TEXT,
            visibility TEXT NOT NULL DEFAULT 'public',
            created_by TEXT,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            status TEXT NOT NULL DEFAULT 'online',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            agent_code TEXT,
            task_claim_mode INTEGER NOT NULL DEFAULT 0,
            task_dream_mode INTEGER NOT NULL DEFAULT 0,
            user_visibility TEXT NOT NULL DEFAULT 'protected',
            friend_ext JSON,
            friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL',
            UNIQUE (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bots table");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_friendships (
            left_bot TEXT NOT NULL,
            right_bot TEXT NOT NULL,
            env TEXT NOT NULL,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (left_bot, right_bot, env)
        )",
    ))
    .await
    .expect("create friendships table");
    db
}

#[allow(clippy::too_many_arguments)]
async fn seed_bot(
    db: &dyn DbPlugin,
    bot_id: &str,
    name: &str,
    kind: &str,
    visibility: &str,
    status: &str,
    created_by: Option<&str>,
    timestamp: &str,
) {
    let bot_info = serde_json::json!({
        "summary": format!("summary-{bot_id}"),
        "domains": ["planning"],
        "skills": [{"name": "plan", "description": "Make a plan"}],
        "scopes": ["workspace"]
    })
    .to_string();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots
         (gmt_create, gmt_modified, bot_uuid, name, bot_info, session_token,
          registered_at, updated_at, env, visibility, created_by, actor_kind,
          status, is_deleted, agent_code)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        vec![
            Value::from(timestamp),
            Value::from(timestamp),
            Value::from(bot_id),
            Value::from(name),
            Value::from(bot_info),
            Value::from(format!("token-{bot_id}")),
            Value::from(timestamp),
            Value::from(timestamp),
            Value::from("dev"),
            Value::from(visibility),
            created_by.map(Value::from).unwrap_or(Value::Null),
            Value::from(kind),
            Value::from(status),
            Value::from(format!("agent-{bot_id}")),
        ],
    ))
    .await
    .expect("seed bot row");
}

#[tokio::test]
async fn persistent_control_plane_task_modes_patch_persists_and_reads_back() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "claim-bot",
        "Claim Bot",
        "bot",
        "public",
        "online",
        Some("staff-1"),
        "2026-01-01 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "dream-bot",
        "Dream Bot",
        "bot",
        "public",
        "online",
        Some("staff-2"),
        "2026-01-02 00:00:00",
    )
    .await;

    // Default state: both toggles false on seeded rows.
    let claim = repo
        .get_control_plane("claim-bot", "dev")
        .await
        .expect("get claim")
        .expect("claim row");
    assert!(!claim.task_claim_mode);
    assert!(!claim.task_dream_mode);

    // Patching one toggle leaves the other untouched and bot_info intact.
    let patched = repo
        .patch_control_plane(
            "claim-bot",
            "dev",
            BotControlPlanePatch {
                task_claim_mode: Some(true),
                task_dream_mode: Some(false),
                ..Default::default()
            },
        )
        .await
        .expect("patch claim")
        .expect("claim row");
    assert!(patched.task_claim_mode);
    assert!(!patched.task_dream_mode);
    assert_eq!(patched.descriptor.summary, "summary-claim-bot");

    repo.patch_control_plane(
        "dream-bot",
        "dev",
        BotControlPlanePatch {
            task_claim_mode: Some(false),
            task_dream_mode: Some(true),
            ..Default::default()
        },
    )
    .await
    .expect("patch dream")
    .expect("dream row");

    // Reads back the persisted values independently.
    let claim = repo
        .get_control_plane("claim-bot", "dev")
        .await
        .expect("get claim")
        .expect("claim row");
    assert!(claim.task_claim_mode);
    assert!(!claim.task_dream_mode);

    let dream = repo
        .get_control_plane("dream-bot", "dev")
        .await
        .expect("get dream")
        .expect("dream row");
    assert!(!dream.task_claim_mode);
    assert!(dream.task_dream_mode);
}

/// Sort a Vec<String> in place so roster filter assertions stay order-independent
/// (the repo orders by gmt_create DESC, bot_uuid ASC, which the caller should not re-derive).
fn sorted_ids(mut ids: Vec<String>) -> Vec<String> {
    ids.sort();
    ids
}

/// Extract + sort the bot_ids from a roster query result for order-independent comparison.
fn roster_ids(records: Vec<BotControlPlaneRecord>) -> Vec<String> {
    sorted_ids(records.into_iter().map(|record| record.bot_id).collect())
}

#[tokio::test]
async fn persistent_control_plane_list_by_task_modes_covers_all_match_arms() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "claim-bot",
        "Claim Bot",
        "bot",
        "public",
        "online",
        Some("staff-1"),
        "2026-01-01 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "dream-bot",
        "Dream Bot",
        "bot",
        "public",
        "online",
        Some("staff-2"),
        "2026-01-02 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "both-bot",
        "Both Bot",
        "bot",
        "public",
        "online",
        Some("staff-3"),
        "2026-01-03 00:00:00",
    )
    .await;
    // none-bot keeps the seeded defaults (claim=false, dream=false) to exercise the
    // `false` filter branch and the default-value read-through in record_from_row.
    seed_bot(
        db.as_ref(),
        "none-bot",
        "None Bot",
        "bot",
        "public",
        "online",
        Some("staff-4"),
        "2026-01-04 00:00:00",
    )
    .await;

    // claim-bot: claim=T, dream=F | dream-bot: claim=F, dream=T | both-bot: claim=T, dream=T.
    repo.patch_control_plane(
        "claim-bot",
        "dev",
        BotControlPlanePatch {
            task_claim_mode: Some(true),
            task_dream_mode: Some(false),
            ..Default::default()
        },
    )
    .await
    .expect("patch claim-bot")
    .expect("claim-bot row");
    repo.patch_control_plane(
        "dream-bot",
        "dev",
        BotControlPlanePatch {
            task_claim_mode: Some(false),
            task_dream_mode: Some(true),
            ..Default::default()
        },
    )
    .await
    .expect("patch dream-bot")
    .expect("dream-bot row");
    repo.patch_control_plane(
        "both-bot",
        "dev",
        BotControlPlanePatch {
            task_claim_mode: Some(true),
            task_dream_mode: Some(true),
            ..Default::default()
        },
    )
    .await
    .expect("patch both-bot")
    .expect("both-bot row");

    // (None, None, _) => no filter: all four bots.
    let all = repo
        .list_control_plane_by_task_modes(BotTaskModesQuery {
            env: "dev".to_string(),
            task_claim_mode: None,
            task_dream_mode: None,
            match_mode: TaskModeMatch::Any,
            visibility: None,
            status: None,
            user_visibility: None,
        })
        .await
        .expect("list (none, none)");
    assert_eq!(
        roster_ids(all),
        sorted_ids(vec![
            "claim-bot".to_string(),
            "dream-bot".to_string(),
            "both-bot".to_string(),
            "none-bot".to_string(),
        ]),
    );

    // (Some(true), None, _) => claim ON: claim-bot, both-bot.
    let claim_on = repo
        .list_control_plane_by_task_modes(BotTaskModesQuery {
            env: "dev".to_string(),
            task_claim_mode: Some(true),
            task_dream_mode: None,
            match_mode: TaskModeMatch::Any,
            visibility: None,
            status: None,
            user_visibility: None,
        })
        .await
        .expect("list (claim=true, none)");
    assert_eq!(
        roster_ids(claim_on),
        sorted_ids(vec!["claim-bot".to_string(), "both-bot".to_string()]),
    );

    // (None, Some(true), _) => dream ON: dream-bot, both-bot.
    let dream_on = repo
        .list_control_plane_by_task_modes(BotTaskModesQuery {
            env: "dev".to_string(),
            task_claim_mode: None,
            task_dream_mode: Some(true),
            match_mode: TaskModeMatch::Any,
            visibility: None,
            status: None,
            user_visibility: None,
        })
        .await
        .expect("list (none, dream=true)");
    assert_eq!(
        roster_ids(dream_on),
        sorted_ids(vec!["dream-bot".to_string(), "both-bot".to_string()]),
    );

    // (Some(true), Some(true), All) => both ON: both-bot only.
    let both_all = repo
        .list_control_plane_by_task_modes(BotTaskModesQuery {
            env: "dev".to_string(),
            task_claim_mode: Some(true),
            task_dream_mode: Some(true),
            match_mode: TaskModeMatch::All,
            visibility: None,
            status: None,
            user_visibility: None,
        })
        .await
        .expect("list (both=true, all)");
    assert_eq!(roster_ids(both_all), vec!["both-bot".to_string()]);

    // (Some(true), Some(true), Any) => claim OR dream ON: claim-bot, dream-bot, both-bot.
    let both_any = repo
        .list_control_plane_by_task_modes(BotTaskModesQuery {
            env: "dev".to_string(),
            task_claim_mode: Some(true),
            task_dream_mode: Some(true),
            match_mode: TaskModeMatch::Any,
            visibility: None,
            status: None,
            user_visibility: None,
        })
        .await
        .expect("list (both=true, any)");
    assert_eq!(
        roster_ids(both_any),
        sorted_ids(vec![
            "claim-bot".to_string(),
            "dream-bot".to_string(),
            "both-bot".to_string(),
        ]),
    );

    // (Some(false), None, _) => claim OFF: dream-bot, none-bot. Exercises the `false`
    // SQL-param branch (value 0) and reading a false toggle back through record_from_row.
    let claim_off = repo
        .list_control_plane_by_task_modes(BotTaskModesQuery {
            env: "dev".to_string(),
            task_claim_mode: Some(false),
            task_dream_mode: None,
            match_mode: TaskModeMatch::Any,
            visibility: None,
            status: None,
            user_visibility: None,
        })
        .await
        .expect("list (claim=false, none)");
    assert_eq!(
        roster_ids(claim_off),
        sorted_ids(vec!["dream-bot".to_string(), "none-bot".to_string()]),
    );

    // env scoping: a different env returns no rows (environment isolation).
    let other_env = repo
        .list_control_plane_by_task_modes(BotTaskModesQuery {
            env: "prod".to_string(),
            task_claim_mode: None,
            task_dream_mode: None,
            match_mode: TaskModeMatch::Any,
            visibility: None,
            status: None,
            user_visibility: None,
        })
        .await
        .expect("list prod env");
    assert!(other_env.is_empty());
}

#[tokio::test]
async fn memory_control_plane_search_covers_search_text_friendship_and_tc_filters() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "acting-memory-search".to_string(),
        BotCapabilities {
            name: Some("Acting Memory Search".to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        "staff-1",
        "acting-token",
    )
    .await
    .expect("register acting bot");
    repo.register_with_owner_and_token(
        "search-match-memory".to_string(),
        BotCapabilities {
            name: Some("Needle Match".to_string()),
            summary: Some("memory needle summary".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "staff-2",
        "search-match-token",
    )
    .await
    .expect("register search match bot");
    repo.register_with_owner_and_token(
        "friend-memory".to_string(),
        BotCapabilities {
            name: Some("Friend Memory".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "staff-3",
        "friend-token",
    )
    .await
    .expect("register friend bot");
    repo.register_with_owner_and_token(
        "tc-memory:200".to_string(),
        BotCapabilities {
            name: Some("TC Memory".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "200",
        "tc-token",
    )
    .await
    .expect("register tc bot");
    repo.register_with_owner_and_token(
        "non-tc-memory:200".to_string(),
        BotCapabilities {
            name: Some("Non TC Memory".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "201",
        "non-tc-token",
    )
    .await
    .expect("register non tc bot");
    repo.patch_control_plane(
        "friend-memory",
        &env,
        BotControlPlanePatch {
            user_visibility: Some(UserVisibility::Private),
            ..Default::default()
        },
    )
    .await
    .expect("patch friend visibility")
    .expect("friend bot exists");

    let match_name = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["friend-memory".to_string()]),
            name: Some(" needle ".to_string()),
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: Some(BotSearchFriendshipFilter::All),
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("search by name fallback");
    assert_eq!(match_name.1, 1);
    assert_eq!(match_name.0[0].bot.bot_id, "search-match-memory");

    let (empty_visibility, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: Some(vec![]),
            user_visibility: None,
            status: None,
            friendship: None,
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("empty visibility filter");
    assert!(empty_visibility.is_empty());
    assert_eq!(total, 0);

    let (empty_user_visibility, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: Some(vec![]),
            status: None,
            friendship: None,
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("empty user visibility filter");
    assert!(empty_user_visibility.is_empty());
    assert_eq!(total, 0);

    let (friends_only, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: Some(BotSearchFriendshipFilter::Friends),
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("friends filter without friend ids");
    assert!(friends_only.is_empty());
    assert_eq!(total, 0);

    let (friends, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["friend-memory".to_string()]),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: Some(vec!["private".to_string()]),
            status: None,
            friendship: Some(BotSearchFriendshipFilter::Friends),
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("friends filter with user visibility");
    assert_eq!(total, 1);
    assert_eq!(friends[0].bot.bot_id, "friend-memory");
    assert!(friends[0].is_friend);

    let (non_friends, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["friend-memory".to_string()]),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: Some(BotSearchFriendshipFilter::NonFriends),
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("non friends filter");
    assert_eq!(total, 3);
    assert!(non_friends.iter().all(|row| row.bot.bot_id != "friend-memory"));

    let (tc_only, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: None,
            tc_bot: Some(true),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("tc filter");
    assert_eq!(total, 1);
    assert_eq!(tc_only[0].bot.bot_id, "tc-memory:200");

    let (non_tc_only, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env,
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: None,
            tc_bot: Some(false),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("non tc filter");
    assert_eq!(total, 3);
    assert!(non_tc_only.iter().all(|row| row.bot.bot_id != "tc-memory:200"));
}

#[tokio::test]
async fn persistent_control_plane_search_covers_search_text_friendship_and_tc_filters() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "acting-persistent-search",
        "Acting Persistent Search",
        "bot",
        "private",
        "online",
        Some("staff-1"),
        "2026-01-01 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "search-match-persistent",
        "Needle Match",
        "bot",
        "public",
        "online",
        Some("staff-2"),
        "2026-01-02 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "friend-persistent",
        "Friend Persistent",
        "bot",
        "public",
        "online",
        Some("staff-3"),
        "2026-01-03 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "tc-persistent:300",
        "TC Persistent",
        "bot",
        "public",
        "online",
        Some("300"),
        "2026-01-04 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "non-tc-persistent:300",
        "Non TC Persistent",
        "bot",
        "public",
        "online",
        Some("301"),
        "2026-01-05 00:00:00",
    )
    .await;
    repo.patch_control_plane(
        "friend-persistent",
        "dev",
        BotControlPlanePatch {
            user_visibility: Some(UserVisibility::Private),
            ..Default::default()
        },
    )
    .await
    .expect("patch friend visibility")
    .expect("friend bot exists");

    let match_name = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["friend-persistent".to_string()]),
            name: Some(" needle ".to_string()),
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: Some(BotSearchFriendshipFilter::All),
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("search by name fallback");
    assert_eq!(match_name.1, 1);
    assert_eq!(match_name.0[0].bot.bot_id, "search-match-persistent");

    let (empty_visibility, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: Some(vec![]),
            user_visibility: None,
            status: None,
            friendship: None,
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("empty visibility filter");
    assert!(empty_visibility.is_empty());
    assert_eq!(total, 0);

    let (empty_user_visibility, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: Some(vec![]),
            status: None,
            friendship: None,
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("empty user visibility filter");
    assert!(empty_user_visibility.is_empty());
    assert_eq!(total, 0);

    let (friends_only, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: Some(BotSearchFriendshipFilter::Friends),
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("friends filter without friend ids");
    assert!(friends_only.is_empty());
    assert_eq!(total, 0);

    let (friends, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["friend-persistent".to_string()]),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: Some(vec!["private".to_string()]),
            status: None,
            friendship: Some(BotSearchFriendshipFilter::Friends),
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("friends filter with user visibility");
    assert_eq!(total, 1);
    assert_eq!(friends[0].bot.bot_id, "friend-persistent");
    assert!(friends[0].is_friend);

    let (non_friends, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["friend-persistent".to_string()]),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: Some(BotSearchFriendshipFilter::NonFriends),
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("non friends filter");
    assert_eq!(total, 3);
    assert!(non_friends.iter().all(|row| row.bot.bot_id != "friend-persistent"));

    let (tc_only, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: None,
            tc_bot: Some(true),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("tc filter");
    assert_eq!(total, 1);
    assert_eq!(tc_only[0].bot.bot_id, "tc-persistent:300");

    let (non_tc_only, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: None,
            tc_bot: Some(false),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("non tc filter");
    assert_eq!(total, 3);
    assert!(non_tc_only.iter().all(|row| row.bot.bot_id != "tc-persistent:300"));
}

/// Build a `BotTaskModesQuery` with task toggles unset so the visibility /
/// status / user_visibility filters are the only narrowing dimensions. Used by
/// the filter-branch coverage tests below.
fn task_modes_query(
    env: &str,
    visibility: Option<String>,
    status: Option<ActorStatus>,
    user_visibility: Option<UserVisibility>,
) -> BotTaskModesQuery {
    BotTaskModesQuery {
        env: env.to_string(),
        task_claim_mode: None,
        task_dream_mode: None,
        match_mode: TaskModeMatch::Any,
        visibility,
        status,
        user_visibility,
    }
}

/// Persistent (SQLite) store: the `visibility` / `status` / `user_visibility`
/// SQL WHERE branches added to `list_control_plane_by_task_modes` narrow the
/// roster as expected, including all `ActorStatus` and `UserVisibility` arms.
#[tokio::test]
async fn persistent_control_plane_list_by_task_modes_filters_visibility_status_user_visibility() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "pub-online",
        "Pub Online",
        "bot",
        "public",
        "online",
        Some("staff-1"),
        "2026-01-01 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "prot-hidden",
        "Prot Hidden",
        "bot",
        "protected",
        "hidden",
        Some("staff-2"),
        "2026-01-02 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "priv-online",
        "Priv Online",
        "bot",
        "private",
        "online",
        Some("staff-3"),
        "2026-01-03 00:00:00",
    )
    .await;

    // `user_visibility` is not a seed_bot column; patch each bot to a distinct
    // value so the `AND user_visibility = ?` SQL branch has matching rows.
    repo.patch_control_plane(
        "pub-online",
        "dev",
        BotControlPlanePatch {
            user_visibility: Some(UserVisibility::Public),
            ..Default::default()
        },
    )
    .await
    .expect("patch pub-online user_visibility")
    .expect("pub-online row");
    repo.patch_control_plane(
        "prot-hidden",
        "dev",
        BotControlPlanePatch {
            user_visibility: Some(UserVisibility::Protected),
            ..Default::default()
        },
    )
    .await
    .expect("patch prot-hidden user_visibility")
    .expect("prot-hidden row");
    repo.patch_control_plane(
        "priv-online",
        "dev",
        BotControlPlanePatch {
            user_visibility: Some(UserVisibility::Private),
            ..Default::default()
        },
    )
    .await
    .expect("patch priv-online user_visibility")
    .expect("priv-online row");

    // `visibility` string filter (SQL `AND visibility = ?` branch).
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                Some("public".to_string()),
                None,
                None
            ))
            .await
            .expect("visibility=public")
        ),
        vec!["pub-online".to_string()]
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                Some("protected".to_string()),
                None,
                None
            ))
            .await
            .expect("visibility=protected")
        ),
        vec!["prot-hidden".to_string()]
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                Some("private".to_string()),
                None,
                None
            ))
            .await
            .expect("visibility=private")
        ),
        vec!["priv-online".to_string()]
    );

    // `status` filter (SQL `AND status = ?` Online + Hidden arms).
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                Some(ActorStatus::Online),
                None
            ))
            .await
            .expect("status=online")
        ),
        sorted_ids(vec!["pub-online".to_string(), "priv-online".to_string()])
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                Some(ActorStatus::Hidden),
                None
            ))
            .await
            .expect("status=hidden")
        ),
        vec!["prot-hidden".to_string()]
    );

    // `user_visibility` filter (SQL `AND user_visibility = ?` Public/Protected/Private arms).
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                None,
                Some(UserVisibility::Public)
            ))
            .await
            .expect("user_visibility=public")
        ),
        vec!["pub-online".to_string()]
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                None,
                Some(UserVisibility::Protected)
            ))
            .await
            .expect("user_visibility=protected")
        ),
        vec!["prot-hidden".to_string()]
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                None,
                Some(UserVisibility::Private)
            ))
            .await
            .expect("user_visibility=private")
        ),
        vec!["priv-online".to_string()]
    );

    // Combined `visibility` + `status` filter (both SQL branches active).
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                Some("private".to_string()),
                Some(ActorStatus::Online),
                None
            ))
            .await
            .expect("visibility=private&status=online")
        ),
        vec!["priv-online".to_string()]
    );
}

/// Memory store: the `is_none_or` visibility / status / user_visibility
/// predicates added to `list_control_plane_by_task_modes` narrow the roster
/// identically to the persistent store, covering the in-memory filter path.
#[tokio::test]
async fn memory_control_plane_list_by_task_modes_filters_visibility_status_user_visibility() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());

    repo.register_with_owner_and_token(
        "pub-online".to_string(),
        BotCapabilities {
            name: Some("Pub Online".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "staff-1",
        "token-pub",
    )
    .await
    .expect("register pub-online");
    repo.register_with_owner_and_token(
        "prot-hidden".to_string(),
        BotCapabilities {
            name: Some("Prot Hidden".to_string()),
            visibility: "protected".to_string(),
            ..Default::default()
        },
        "staff-2",
        "token-prot",
    )
    .await
    .expect("register prot-hidden");
    repo.register_with_owner_and_token(
        "priv-online".to_string(),
        BotCapabilities {
            name: Some("Priv Online".to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        "staff-3",
        "token-priv",
    )
    .await
    .expect("register priv-online");

    // Memory-registered bots default to Online status; flip prot-hidden to
    // Hidden, and give each a distinct user_visibility via the patch path.
    repo.patch_control_plane(
        "pub-online",
        "dev",
        BotControlPlanePatch {
            user_visibility: Some(UserVisibility::Public),
            ..Default::default()
        },
    )
    .await
    .expect("patch pub-online")
    .expect("pub-online row");
    repo.patch_control_plane(
        "prot-hidden",
        "dev",
        BotControlPlanePatch {
            status: Some(ActorStatus::Hidden),
            user_visibility: Some(UserVisibility::Protected),
            ..Default::default()
        },
    )
    .await
    .expect("patch prot-hidden")
    .expect("prot-hidden row");
    repo.patch_control_plane(
        "priv-online",
        "dev",
        BotControlPlanePatch {
            user_visibility: Some(UserVisibility::Private),
            ..Default::default()
        },
    )
    .await
    .expect("patch priv-online")
    .expect("priv-online row");

    // `visibility` string predicate.
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                Some("public".to_string()),
                None,
                None
            ))
            .await
            .expect("visibility=public")
        ),
        vec!["pub-online".to_string()]
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                Some("protected".to_string()),
                None,
                None
            ))
            .await
            .expect("visibility=protected")
        ),
        vec!["prot-hidden".to_string()]
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                Some("private".to_string()),
                None,
                None
            ))
            .await
            .expect("visibility=private")
        ),
        vec!["priv-online".to_string()]
    );

    // `status` predicate (Online + Hidden arms).
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                Some(ActorStatus::Online),
                None
            ))
            .await
            .expect("status=online")
        ),
        sorted_ids(vec!["pub-online".to_string(), "priv-online".to_string()])
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                Some(ActorStatus::Hidden),
                None
            ))
            .await
            .expect("status=hidden")
        ),
        vec!["prot-hidden".to_string()]
    );

    // `user_visibility` predicate (Public/Protected/Private arms).
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                None,
                Some(UserVisibility::Public)
            ))
            .await
            .expect("user_visibility=public")
        ),
        vec!["pub-online".to_string()]
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                None,
                Some(UserVisibility::Protected)
            ))
            .await
            .expect("user_visibility=protected")
        ),
        vec!["prot-hidden".to_string()]
    );
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                None,
                None,
                Some(UserVisibility::Private)
            ))
            .await
            .expect("user_visibility=private")
        ),
        vec!["priv-online".to_string()]
    );

    // Combined `visibility` + `status` predicate.
    assert_eq!(
        roster_ids(
            repo.list_control_plane_by_task_modes(task_modes_query(
                "dev",
                Some("private".to_string()),
                Some(ActorStatus::Online),
                None
            ))
            .await
            .expect("visibility=private&status=online")
        ),
        vec!["priv-online".to_string()]
    );
}
