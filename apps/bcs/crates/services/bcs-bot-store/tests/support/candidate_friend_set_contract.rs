//! Shared candidate viewer-visibility and friend-set store contract.
//!
//! One function drives every real store implementation through the same
//! semantics: a candidate page is filtered by the requested viewer dimension
//! BEFORE the total is counted and before `offset`/`limit`, and the friend set
//! arrives as one bound JSON array instead of a per-friend `UNION ALL` chain.
//!
//! It is mounted by the bot-store conformance root (Memory + real SQLite with
//! the complete migration chain) and by the bcs-admin full MySQL migration
//! chain gate, so this file must stay free of fixture dependencies and take
//! only the repository ports plus an environment and a unique prefix.

use std::collections::HashSet;

use bcs_service_api::{
    ActorKind, BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility,
    BotCapabilities, BotControlPlanePatch, BotControlPlaneRepoPort, BotRepoPort,
    BotSearchCandidateQuery, BotSearchFriendshipFilter, UserVisibility,
};

async fn register_bot(
    repo: &dyn BotControlPlaneRepoPort,
    bots: &dyn BotRepoPort,
    env: &str,
    bot_id: &str,
    name: &str,
    summary: &str,
    visibility: &str,
    user_visibility: UserVisibility,
) {
    assert!(
        bots.create_registration_if_absent(
            bot_id.to_string(),
            BotCapabilities {
                name: Some(name.to_string()),
                summary: Some(summary.to_string()),
                visibility: visibility.to_string(),
                ..Default::default()
            },
            "candidate-set-owner",
            &format!("candidate-set-token-{bot_id}"),
        )
        .await
        .expect("candidate contract registration"),
        "{bot_id} must be freshly registered",
    );
    let patched = repo
        .patch_control_plane(
            bot_id,
            env,
            BotControlPlanePatch {
                user_visibility: Some(user_visibility),
                ..Default::default()
            },
        )
        .await
        .expect("candidate contract patch")
        .expect("candidate contract target exists");
    assert_eq!(patched.bot_id, bot_id);
}

fn list_query(
    acting: &str,
    env: &str,
    name_prefix: &str,
    purpose: BotCandidateVisibility,
    viewer: ActorKind,
    friend_ids: &HashSet<String>,
    offset: u64,
    limit: u64,
) -> BotCandidateReadQuery {
    BotCandidateReadQuery {
        acting_bot_id: acting.to_string(),
        env: env.to_string(),
        visibility: purpose,
        viewer_kind: viewer,
        friend_ids: friend_ids.clone(),
        // Contracts share a migrated MySQL database with other fixtures.
        name: Some(name_prefix.to_string()),
        offset,
        limit,
    }
}

fn search_query(
    acting: &str,
    env: &str,
    purpose: BotCandidateVisibility,
    viewer: ActorKind,
    friend_ids: &HashSet<String>,
    q: &str,
    name_only: bool,
    offset: u64,
    limit: u64,
) -> BotSearchCandidateQuery {
    BotSearchCandidateQuery {
        acting_bot_id: acting.to_string(),
        env: env.to_string(),
        visibility: purpose,
        viewer_kind: Some(viewer),
        friend_ids: friend_ids.clone(),
        bot_uuids: None,
        name: None,
        q: Some(q.to_string()),
        visibility_filter: None,
        user_visibility: None,
        status: None,
        friendship: Some(BotSearchFriendshipFilter::All),
        tc_bot: None,
        name_only,
        offset,
        limit,
    }
}

async fn list_page(
    repo: &dyn BotControlPlaneRepoPort,
    query: BotCandidateReadQuery,
) -> (Vec<BotCandidateReadRecord>, u64) {
    repo.list_control_plane_candidates(query)
        .await
        .expect("candidate list query")
}

async fn search_page(
    repo: &dyn BotControlPlaneRepoPort,
    query: BotSearchCandidateQuery,
) -> (Vec<BotCandidateReadRecord>, u64) {
    repo.search_control_plane_candidates(query)
        .await
        .expect("candidate search query")
}

fn record_ids(records: &[BotCandidateReadRecord]) -> Vec<String> {
    records
        .iter()
        .map(|record| record.bot.bot_id.clone())
        .collect()
}

async fn walk_list(
    repo: &dyn BotControlPlaneRepoPort,
    acting: &str,
    env: &str,
    prefix: &str,
    friend_ids: &HashSet<String>,
    expected_total: u64,
) -> Vec<String> {
    let mut collected = Vec::new();
    let mut offset = 0_u64;
    loop {
        let (page, total) = list_page(
            repo,
            list_query(
                acting,
                env,
                prefix,
                BotCandidateVisibility::Collaboration,
                ActorKind::Bot,
                friend_ids,
                offset,
                100,
            ),
        )
        .await;
        assert_eq!(total, expected_total, "list total at offset {offset}");
        assert!(page.iter().all(|record| record.is_friend));
        if page.is_empty() {
            return collected;
        }
        offset += page.len() as u64;
        collected.extend(record_ids(&page));
    }
}

async fn walk_search(
    repo: &dyn BotControlPlaneRepoPort,
    acting: &str,
    env: &str,
    friend_ids: &HashSet<String>,
    expected_total: u64,
    q: &str,
) -> Vec<String> {
    let mut collected = Vec::new();
    let mut offset = 0_u64;
    loop {
        let (page, total) = search_page(
            repo,
            search_query(
                acting,
                env,
                BotCandidateVisibility::Collaboration,
                ActorKind::Bot,
                friend_ids,
                q,
                true,
                offset,
                100,
            ),
        )
        .await;
        assert_eq!(total, expected_total, "search total at offset {offset}");
        assert!(page.iter().all(|record| record.is_friend));
        if page.is_empty() {
            return collected;
        }
        offset += page.len() as u64;
        collected.extend(record_ids(&page));
    }
}

async fn verify_capacity(
    repo: &dyn BotControlPlaneRepoPort,
    acting: &str,
    env: &str,
    count: usize,
    friends: &HashSet<String>,
    marker: &str,
    stranger: &str,
    prefix: &str,
) {
    let expected_total = count as u64;

    // Discovery must not reveal the private friend set to either viewer.
    for viewer in [ActorKind::Bot, ActorKind::Human] {
        let (page, total) = list_page(
            repo,
            list_query(acting, env, prefix, BotCandidateVisibility::Discovery, viewer, friends, 0, 100),
        )
        .await;
        assert_eq!(total, 0, "{prefix}/{count}: discovery {viewer:?} total");
        assert!(page.is_empty(), "{prefix}/{count}: discovery {viewer:?} page");
    }

    // Post-filter total with a one-row page, then the next row.
    let (first, total) = list_page(
        repo,
        list_query(acting, env, prefix, BotCandidateVisibility::Collaboration, ActorKind::Bot, friends, 0, 1),
    )
    .await;
    assert_eq!(total, expected_total, "{prefix}/{count}: list total");
    assert_eq!(first.len(), usize::from(count > 0), "{prefix}/{count}: list page 1");
    assert!(first.iter().all(|record| record.is_friend));
    if count > 1 {
        let (second, total) = list_page(
            repo,
            list_query(
                acting,
                env,
                prefix,
                BotCandidateVisibility::Collaboration,
                ActorKind::Bot,
                friends,
                1,
                1,
            ),
        )
        .await;
        assert_eq!(total, expected_total);
        assert_eq!(second.len(), 1);
        assert_ne!(first[0].bot.bot_id, second[0].bot.bot_id);
    }

    // Name fallback is filtered in the store before its page/total, and the
    // 100-row page walk proves stable contiguous ordering across offsets.
    let name_needle = format!("{prefix} capacity");
    let (search_first, total) = search_page(
        repo,
        search_query(
            acting,
            env,
            BotCandidateVisibility::Collaboration,
            ActorKind::Bot,
            friends,
            &name_needle,
            true,
            0,
            1,
        ),
    )
    .await;
    assert_eq!(total, expected_total, "{prefix}/{count}: search total");
    assert_eq!(
        search_first.len(),
        usize::from(count > 0),
        "{prefix}/{count}: search page 1"
    );

    let listed = walk_list(repo, acting, env, prefix, friends, expected_total).await;
    let searched = walk_search(repo, acting, env, friends, expected_total, &name_needle).await;
    assert_eq!(listed.len(), count, "{prefix}/{count}: list page walk");
    assert_eq!(searched.len(), count, "{prefix}/{count}: search page walk");
    assert_eq!(
        listed.iter().collect::<HashSet<_>>().len(),
        count,
        "{prefix}/{count}: list pages must not duplicate identities"
    );
    assert_eq!(
        searched.iter().collect::<HashSet<_>>().len(),
        count,
        "{prefix}/{count}: search pages must not duplicate identities"
    );
    assert!(!listed.iter().any(|id| id == stranger));
    assert!(!searched.iter().any(|id| id == stranger));

    // The reference page uses the same ordering as the paged walk, so the
    // offset pages neither skip nor reorder identities.
    let (reference, total) = list_page(
        repo,
        list_query(
            acting,
            env,
            prefix,
            BotCandidateVisibility::Collaboration,
            ActorKind::Bot,
            friends,
            0,
            count.max(1) as u64,
        ),
    )
    .await;
    assert_eq!(total, expected_total);
    assert_eq!(record_ids(&reference), listed, "{prefix}/{count}: list order");

    // `name_only = false` still matches summaries; the dedicated v1 name
    // fallback must only match Bot names.
    let (summary_matches, total) = search_page(
        repo,
        search_query(
            acting,
            env,
            BotCandidateVisibility::Collaboration,
            ActorKind::Bot,
            friends,
            marker,
            false,
            0,
            100,
        ),
    )
    .await;
    assert_eq!(total, expected_total, "{prefix}/{count}: summary search total");
    assert_eq!(summary_matches.len(), count.min(100) as usize);
    if count > 0 {
        let (name_only_marker, total) = search_page(
            repo,
            search_query(
                acting,
                env,
                BotCandidateVisibility::Collaboration,
                ActorKind::Bot,
                friends,
                marker,
                true,
                0,
                100,
            ),
        )
        .await;
        assert_eq!(total, 0, "{prefix}/{count}: name_only must ignore summaries");
        assert!(name_only_marker.is_empty());
    }
}

async fn verify_binding_edges(
    repo: &dyn BotControlPlaneRepoPort,
    bots: &dyn BotRepoPort,
    env: &str,
    prefix: &str,
    acting: &str,
    longest_bot_id_len: usize,
) {
    let quoted = format!("{prefix}-quote'id\\backslash");
    let longest = format!(
        "{prefix}-{}",
        "x".repeat(longest_bot_id_len - prefix.len() - 1)
    );
    assert_eq!(longest.len(), longest_bot_id_len);
    for bot_id in [&quoted, &longest] {
        register_bot(
            repo,
            bots,
            env,
            bot_id,
            bot_id,
            &format!("{prefix}-binding-marker"),
            "private",
            UserVisibility::Private,
        )
        .await;
    }
    let friends = HashSet::from([quoted.clone(), longest.clone()]);
    let (page, total) = list_page(
        repo,
        list_query(
            acting,
            env,
            prefix,
            BotCandidateVisibility::Collaboration,
            ActorKind::Bot,
            &friends,
            0,
            10,
        ),
    )
    .await;
    assert_eq!(total, 2, "{prefix}: quoted/backslash/longest friend binding");
    let mut returned = record_ids(&page);
    returned.sort();
    let mut expected = vec![quoted, longest];
    expected.sort();
    assert_eq!(returned, expected);
    assert!(page.iter().all(|record| record.is_friend));

    let (searched, total) = search_page(
        repo,
        search_query(
            acting,
            env,
            BotCandidateVisibility::Collaboration,
            ActorKind::Bot,
            &friends,
            &format!("{prefix}-binding-marker"),
            false,
            0,
            10,
        ),
    )
    .await;
    assert_eq!(total, 2);
    assert_eq!(searched.len(), 2);
}

/// The shared viewer-visibility + friend-set contract. See
/// `tests/conformance_bot_control_plane_repo.rs` for the store mounts.
pub async fn verify(
    repo: &dyn BotControlPlaneRepoPort,
    bots: &dyn BotRepoPort,
    env: &str,
    prefix: &str,
    longest_bot_id_len: usize,
) {
    let acting = format!("{prefix}-acting");
    register_bot(
        repo,
        bots,
        env,
        &acting,
        &format!("{prefix} acting"),
        "acting summary",
        "private",
        UserVisibility::Private,
    )
    .await;

    let marker = format!("{prefix}-caption-marker");
    let mut ids = Vec::with_capacity(1_000);
    for index in 0..1_000 {
        let bot_id = format!("{prefix}-bot-{index:04}");
        register_bot(
            repo,
            bots,
            env,
            &bot_id,
            &format!("{prefix} capacity {index:04}"),
            &marker,
            "private",
            UserVisibility::Private,
        )
        .await;
        ids.push(bot_id);
    }
    let stranger = format!("{prefix}-stranger");
    register_bot(
        repo,
        bots,
        env,
        &stranger,
        &format!("{prefix} stranger"),
        &marker,
        "private",
        UserVisibility::Private,
    )
    .await;

    for count in [0_usize, 100, 500, 501, 1_000] {
        let friends = ids[..count].iter().cloned().collect::<HashSet<_>>();
        verify_capacity(repo, &acting, env, count, &friends, &marker, &stranger, prefix)
            .await;
    }

    verify_binding_edges(repo, bots, env, prefix, &acting, longest_bot_id_len).await;
}

/// Cross-viewer discovery/collaboration visibility pinned before paging.
pub async fn verify_viewer_visibility(
    repo: &dyn BotControlPlaneRepoPort,
    bots: &dyn BotRepoPort,
    env: &str,
    prefix: &str,
) {
    let marker = format!("{prefix}-caption-marker");
    let acting = format!("{prefix}-acting");
    let human = format!("human_{prefix}-staff-1");
    let human_only = format!("{prefix}-human-only-a");
    let bot_only = format!("{prefix}-bot-only-b");
    let both_protected = format!("{prefix}-both-protected-c");
    let private_friend = format!("{prefix}-private-friend-d");
    let summary_only = format!("{prefix}-summary-only");

    let register = |bot_id: &str, name: &str, visibility: &str, user: UserVisibility| {
        let bots = bots;
        let repo = repo;
        let marker = marker.clone();
        let bot_id = bot_id.to_string();
        let name = name.to_string();
        let visibility = visibility.to_string();
        async move {
            assert!(
                bots.create_registration_if_absent(
                    bot_id.clone(),
                    BotCapabilities {
                        name: Some(name),
                        summary: Some(marker),
                        visibility,
                        ..Default::default()
                    },
                    "staff-1",
                    &format!("token-{bot_id}"),
                )
                .await
                .expect("register candidate"),
                "{bot_id} must be freshly registered",
            );
            repo.patch_control_plane(
                &bot_id,
                env,
                BotControlPlanePatch {
                    user_visibility: Some(user),
                    ..Default::default()
                },
            )
            .await
            .expect("patch candidate user visibility")
            .expect("candidate exists");
        }
    };

    register(&acting, "Candidate Acting", "private", UserVisibility::Private).await;
    register(&human_only, "Candidate Human Only", "private", UserVisibility::Public).await;
    register(&bot_only, "Candidate Bot Only", "public", UserVisibility::Private).await;
    register(
        &both_protected,
        "Candidate Both Protected",
        "protected",
        UserVisibility::Protected,
    )
    .await;
    register(
        &private_friend,
        "Candidate Private Friend",
        "private",
        UserVisibility::Private,
    )
    .await;
    register(
        &summary_only,
        "Unrelated Display Name",
        "private",
        UserVisibility::Private,
    )
    .await;

    let friends = HashSet::from([private_friend.clone()]);
    let candidate_query = |viewer: ActorKind, purpose: BotCandidateVisibility, offset, limit| {
        BotCandidateReadQuery {
            acting_bot_id: if viewer == ActorKind::Human {
                human.clone()
            } else {
                acting.clone()
            },
            env: env.to_string(),
            visibility: purpose,
            viewer_kind: viewer,
            friend_ids: friends.clone(),
            name: None,
            offset,
            limit,
        }
    };
    let search_query = |viewer: ActorKind, purpose: BotCandidateVisibility, q: &str, name_only| {
        BotSearchCandidateQuery {
            acting_bot_id: if viewer == ActorKind::Human {
                human.clone()
            } else {
                acting.clone()
            },
            env: env.to_string(),
            visibility: purpose,
            viewer_kind: Some(viewer),
            friend_ids: friends.clone(),
            bot_uuids: None,
            name: None,
            q: Some(q.to_string()),
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: Some(BotSearchFriendshipFilter::All),
            tc_bot: None,
            name_only,
            offset: 0,
            limit: 20,
        }
    };
    let id_set = |records: &[BotCandidateReadRecord]| {
        records
            .iter()
            .map(|record| record.bot.bot_id.clone())
            .collect::<HashSet<_>>()
    };

    // Discovery, Human perspective: `user_visibility IN (public, protected)`.
    let (human_page, human_total) = repo
        .list_control_plane_candidates(candidate_query(
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            0,
            1,
        ))
        .await
        .expect("Human discovery candidates");
    assert_eq!(human_total, 2, "{prefix}: Human discovery total before paging");
    assert_eq!(human_page.len(), 1);
    assert!(
        human_page
            .iter()
            .all(|row| row.bot.user_visibility != UserVisibility::Private),
        "{prefix}: Human discovery must never expose private user visibility",
    );
    let (human_next, human_total) = repo
        .list_control_plane_candidates(candidate_query(
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            1,
            1,
        ))
        .await
        .expect("Human discovery next page");
    assert_eq!(human_total, 2);
    assert_eq!(human_next.len(), 1);
    let mut human_ids = id_set(&human_page);
    human_ids.extend(id_set(&human_next));
    assert_eq!(
        human_ids,
        HashSet::from([human_only.clone(), both_protected.clone()]),
        "{prefix}: Human discovery matches the user dimension only",
    );

    // Discovery, Bot perspective: `visibility IN (public, protected)`.
    let (bot_page, bot_total) = repo
        .list_control_plane_candidates(candidate_query(
            ActorKind::Bot,
            BotCandidateVisibility::Discovery,
            0,
            1,
        ))
        .await
        .expect("Bot discovery candidates");
    assert_eq!(bot_total, 2, "{prefix}: Bot discovery total before paging");
    assert_eq!(bot_page.len(), 1);
    let (bot_next, bot_total) = repo
        .list_control_plane_candidates(candidate_query(
            ActorKind::Bot,
            BotCandidateVisibility::Discovery,
            1,
            1,
        ))
        .await
        .expect("Bot discovery next page");
    assert_eq!(bot_total, 2);
    let mut bot_ids = id_set(&bot_page);
    bot_ids.extend(id_set(&bot_next));
    assert_eq!(
        bot_ids,
        HashSet::from([bot_only.clone(), both_protected.clone()]),
        "{prefix}: Bot discovery matches the Bot dimension only",
    );

    // Collaboration, Human perspective: public user visibility OR approved
    // friend. Collaboration, Bot perspective: public Bot visibility OR the
    // same approved friend.
    let (human_collaboration, total) = repo
        .list_control_plane_candidates(candidate_query(
            ActorKind::Human,
            BotCandidateVisibility::Collaboration,
            0,
            20,
        ))
        .await
        .expect("Human collaboration candidates");
    assert_eq!(total, 2, "{prefix}: Human collaboration total");
    assert_eq!(
        id_set(&human_collaboration),
        HashSet::from([human_only.clone(), private_friend.clone()]),
    );
    assert!(human_collaboration
        .iter()
        .find(|row| row.bot.bot_id == private_friend)
        .is_some_and(|row| row.is_friend));

    let (bot_collaboration, total) = repo
        .list_control_plane_candidates(candidate_query(
            ActorKind::Bot,
            BotCandidateVisibility::Collaboration,
            0,
            20,
        ))
        .await
        .expect("Bot collaboration candidates");
    assert_eq!(total, 2, "{prefix}: Bot collaboration total");
    assert_eq!(
        id_set(&bot_collaboration),
        HashSet::from([bot_only.clone(), private_friend.clone()]),
    );
    assert!(bot_collaboration
        .iter()
        .find(|row| row.bot.bot_id == private_friend)
        .is_some_and(|row| row.is_friend));

    // The candidate search path uses the same viewer dimension and filters
    // before its own page/total.
    let (human_search, total) = repo
        .search_control_plane_candidates(search_query(
            ActorKind::Human,
            BotCandidateVisibility::Discovery,
            &marker,
            false,
        ))
        .await
        .expect("Human discovery search");
    assert_eq!(total, 2);
    assert_eq!(
        id_set(&human_search),
        HashSet::from([human_only.clone(), both_protected.clone()]),
    );

    let (bot_search, total) = repo
        .search_control_plane_candidates(search_query(
            ActorKind::Bot,
            BotCandidateVisibility::Discovery,
            &marker,
            false,
        ))
        .await
        .expect("Bot discovery search");
    assert_eq!(total, 2);
    assert_eq!(
        id_set(&bot_search),
        HashSet::from([bot_only.clone(), both_protected.clone()]),
    );

    let (human_collaboration_search, total) = repo
        .search_control_plane_candidates(search_query(
            ActorKind::Human,
            BotCandidateVisibility::Collaboration,
            &marker,
            false,
        ))
        .await
        .expect("Human collaboration search");
    assert_eq!(total, 2);
    assert_eq!(
        id_set(&human_collaboration_search),
        HashSet::from([human_only.clone(), private_friend.clone()]),
    );

    let (bot_collaboration_search, total) = repo
        .search_control_plane_candidates(search_query(
            ActorKind::Bot,
            BotCandidateVisibility::Collaboration,
            &marker,
            false,
        ))
        .await
        .expect("Bot collaboration search");
    assert_eq!(total, 2);
    assert_eq!(
        id_set(&bot_collaboration_search),
        HashSet::from([bot_only.clone(), private_friend.clone()]),
    );

    // The v1 name fallback must match only the Bot display name. `summary_only`
    // shares the summary marker but has an unrelated name, so a name-only
    // search for the summary marker returns nothing while the legacy search
    // (which also matches summaries) finds it.
    let summary_friends = HashSet::from([private_friend.clone(), summary_only.clone()]);
    let (legacy_summary, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            friend_ids: summary_friends.clone(),
            ..search_query(
                ActorKind::Bot,
                BotCandidateVisibility::Collaboration,
                &marker,
                false,
            )
        })
        .await
        .expect("legacy summary search");
    assert_eq!(total, 3);
    assert!(id_set(&legacy_summary).contains(&summary_only));

    let (name_only, total) = repo
        .search_control_plane_candidates(BotSearchCandidateQuery {
            friend_ids: summary_friends.clone(),
            ..search_query(
                ActorKind::Bot,
                BotCandidateVisibility::Collaboration,
                &marker,
                true,
            )
        })
        .await
        .expect("name-only search");
    assert_eq!(total, 0, "{prefix}: name-only search must ignore summaries");
    assert!(name_only.is_empty());

    // `viewer_kind: None` is the legacy control-plane caller and must be
    // exactly the Bot perspective. Lock both the visibility dimension and the
    // legacy `name_only: false` text matching (summaries and Bot IDs).
    for needle in [marker.clone(), bot_only.clone()] {
        let bot_view = repo
            .search_control_plane_candidates(BotSearchCandidateQuery {
                friend_ids: summary_friends.clone(),
                ..search_query(
                    ActorKind::Bot,
                    BotCandidateVisibility::Collaboration,
                    &needle,
                    false,
                )
            })
            .await
            .expect("Bot-view legacy-compatible search");
        let legacy_view = repo
            .search_control_plane_candidates(BotSearchCandidateQuery {
                viewer_kind: None,
                friend_ids: summary_friends.clone(),
                ..search_query(
                    ActorKind::Bot,
                    BotCandidateVisibility::Collaboration,
                    &needle,
                    false,
                )
            })
            .await
            .expect("legacy None-viewer search");
        assert_eq!(
            bot_view.1, legacy_view.1,
            "{prefix}: None viewer must match the Bot total for {needle}",
        );
        assert_eq!(
            record_ids(&bot_view.0),
            record_ids(&legacy_view.0),
            "{prefix}: None viewer must match the Bot items for {needle}",
        );
        assert!(
            legacy_view.1 > 0,
            "{prefix}: legacy text matching must stay enabled for {needle}",
        );
    }
}
