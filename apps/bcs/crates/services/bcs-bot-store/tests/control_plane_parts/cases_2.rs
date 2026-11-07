use super::*;

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
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        synchronized_db,
        DbSqlFlavor::Sqlite,
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
            bot_uuids: None,
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

    let (exact_bots, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            bot_uuids: Some(vec![
                "search-match-memory".to_string(),
                "missing-memory".to_string(),
            ]),
            name: None,
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: None,
            tc_bot: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("exact bot uuid filter");
    assert_eq!(total, 1);
    assert_eq!(exact_bots[0].bot.bot_id, "search-match-memory");

    let (empty_visibility, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-memory-search".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            bot_uuids: None,
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
            bot_uuids: None,
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
            bot_uuids: None,
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
            bot_uuids: None,
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
            bot_uuids: None,
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
            bot_uuids: None,
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
            bot_uuids: None,
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
