use super::*;

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

    bot_control_plane_repo_port_contract_tests(
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

    bot_control_plane_repo_port_contract_tests(
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
    let repo = PersistentBotRepo::with_sql_flavor(
        Arc::new(UnchangedUpdateDb),
        DbSqlFlavor::Mysql,
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
