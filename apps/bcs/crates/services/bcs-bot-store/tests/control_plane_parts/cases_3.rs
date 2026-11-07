use super::*;

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
    assert_eq!(match_name.0[0].bot.bot_id, "search-match-persistent");

    let (exact_bots, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            bot_uuids: Some(vec![
                "search-match-persistent".to_string(),
                "missing-persistent".to_string(),
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
    assert_eq!(exact_bots[0].bot.bot_id, "search-match-persistent");

    let (empty_visibility, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
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
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
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
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
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
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["friend-persistent".to_string()]),
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
    assert_eq!(friends[0].bot.bot_id, "friend-persistent");
    assert!(friends[0].is_friend);

    let (non_friends, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["friend-persistent".to_string()]),
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
    assert!(non_friends.iter().all(|row| row.bot.bot_id != "friend-persistent"));

    let (tc_only, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
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
    assert_eq!(tc_only[0].bot.bot_id, "tc-persistent:300");

    let (non_tc_only, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            acting_bot_id: "acting-persistent-search".to_string(),
            env: "dev".to_string(),
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
    assert!(non_tc_only.iter().all(|row| row.bot.bot_id != "tc-persistent:300"));
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
