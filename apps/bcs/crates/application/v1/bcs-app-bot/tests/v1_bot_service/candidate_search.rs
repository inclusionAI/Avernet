use super::*;

async fn register_search_bot(
    repo: &MemoryBotRepo,
    bot_id: &str,
    owner: &str,
    visibility: &str,
) {
    repo.register_with_owner_and_token(
        bot_id.to_string(),
        BotCapabilities {
            name: Some(bot_id.to_string()),
            summary: Some(format!("summary-{bot_id}")),
            visibility: visibility.to_string(),
            ..Default::default()
        },
        owner,
        &format!("token-{bot_id}"),
    )
    .await
    .expect("register search bot");
}

#[tokio::test]
async fn ownership_denial_precedes_provider_hydration() {
    let env = bcs_config::resolve_env_str();
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(AuthorizationProbeCore {
        record: BotControlPlaneRecord {
            bot_id: "owned".to_string(),
            kind: ActorKind::Bot,
            name: "Owned".to_string(),
            visibility: "public".to_string(),
            status: ActorStatus::Online,
            env: env.clone(),
            created_by: Some("staff-1".to_string()),
            descriptor: BotControlPlaneDescriptor {
                summary: String::new(),
                domains: Vec::new(),
                skills: Vec::new(),
                scopes: Vec::new(),
            },
            agent_code: None,
            task_claim_mode: false,
            task_dream_mode: false,
            created_at: 1,
            updated_at: 1,
            user_visibility: Default::default(),
            friend_ext: Default::default(),
            friend_check_in_strategy: Default::default(),
        },
    });
    let service = BotServiceImpl::new(
        control_plane,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopConnectService),
        Arc::new(RecordingCandidateSearch::empty()),
        BotServiceConfig { env },
    )
    // Row 2: the descriptor patch consults the CURRENT
    // owner edge; the authority store holds no edge for
    // this Bot, so staff-2 is denied at the hook.
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(
        Arc::new(MemoryBotRepo::new()),
    ));

    let error = service
        .update(UpdateBot {
            caller: human_caller("staff-2"),
            bot_id: "owned".to_string(),
            patch: BotPatch {
                name: Some("Nope".to_string()),
                ..Default::default()
            },
        })
        .await
        .expect_err("ownership denial must not hydrate Provider metadata");

    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn search_candidates_reads_approved_edges_for_human_and_managed_bot_perspectives() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    let connect = Arc::new(RecordingConnectService::default());
    connect.add_friend_for("human_staff-1", "human-edge-friend", ActorKind::Bot);
    connect.add_friend_for("human_staff-1", "human-peer", ActorKind::Human);
    connect.add_friend_for("acting", "acting-edge-friend", ActorKind::Bot);
    connect.add_friend_for("acting", "acting-human-peer", ActorKind::Human);
    let candidate_search = Arc::new(
        RecordingCandidateSearch::new(BotCandidateSearchCoreResult {
            hits: vec![
                search_hit(
                    "human-edge-friend",
                    false,
                    Some(0.25),
                    Some("human edge friend"),
                ),
                search_hit(
                    "acting-edge-friend",
                    false,
                    Some(0.75),
                    Some("acting edge friend"),
                ),
                search_hit("bot-stranger", false, None, None),
            ],
            mode: CoreCandidateSearchMode::Semantic,
        })
        .deriving_friend_markers(),
    );
    let env = bcs_config::resolve_env_str();
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(FriendCore::memory()),
        connect.clone(),
        candidate_search.clone(),
        BotServiceConfig { env: env.clone() },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;
    repo.ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human actor");
    register_search_bot(&repo, "acting", "staff-1", "private").await;
    register_search_bot(&repo, "human-edge-friend", "staff-2", "private").await;
    register_search_bot(&repo, "acting-edge-friend", "staff-3", "public").await;
    register_search_bot(&repo, "bot-stranger", "staff-4", "public").await;

    let human = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            query: Some(" 规划 ".to_string()),
        })
        .await
        .expect("Human perspective search");
    let managed = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Collaboration,
            query: Some("规划".to_string()),
        })
        .await
        .expect("managed Bot perspective search");

    let queries = candidate_search
        .queries
        .lock()
        .expect("candidate search queries lock")
        .clone();
    assert_eq!(queries.len(), 2);
    assert_eq!(queries[0].query, "规划");
    assert_eq!(queries[0].acting_actor_id, "human_staff-1");
    assert_eq!(queries[0].env, env);
    assert_eq!(queries[0].viewer_kind, ActorKind::Human);
    assert_eq!(
        queries[0].visibility,
        BotCandidateVisibility::Discovery
    );
    assert_eq!(
        queries[0].friend_ids,
        HashSet::from(["human-edge-friend".to_string()]),
        "the Human perspective reads the Human actor's Bot edges only"
    );
    assert_eq!(queries[0].limit, 20);
    assert_eq!(queries[1].acting_actor_id, "acting");
    assert_eq!(queries[1].env, env);
    assert_eq!(queries[1].viewer_kind, ActorKind::Bot);
    assert_eq!(
        queries[1].visibility,
        BotCandidateVisibility::Collaboration
    );
    assert_eq!(
        queries[1].friend_ids,
        HashSet::from(["acting-edge-friend".to_string()]),
        "the managed Bot perspective reads the Bot actor's distinct edges"
    );

    assert_eq!(human.search_mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        human
            .items
            .iter()
            .map(|item| (item.bot.bot_id.as_str(), item.is_friend))
            .collect::<Vec<_>>(),
        vec![
            ("human-edge-friend", true),
            ("acting-edge-friend", false),
            ("bot-stranger", false)
        ],
        "the Human perspective marks only its own approved edges as friends"
    );
    assert_eq!(human.items[0].score, Some(0.25));
    assert_eq!(
        human.items[0].short_profile.as_deref(),
        Some("human edge friend")
    );
    assert_eq!(
        human.items[0].tags.get("specialty"),
        Some(&serde_json::json!("planning")),
        "search results keep the Core tag projection"
    );
    assert_eq!(human.items[0].bot.kind, BotKind::Bot);
    assert_eq!(human.items[0].bot.name, "human-edge-friend");
    assert_eq!(
        human.items[0].bot.descriptor.summary,
        "summary-human-edge-friend"
    );
    assert_eq!(managed.search_mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        managed
            .items
            .iter()
            .map(|item| (item.bot.bot_id.as_str(), item.is_friend))
            .collect::<Vec<_>>(),
        vec![
            ("human-edge-friend", false),
            ("acting-edge-friend", true),
            ("bot-stranger", false)
        ],
        "the managed Bot perspective marks only its own approved edges as friends"
    );
    assert_eq!(managed.items[1].score, Some(0.75));
    assert_eq!(
        managed.items[1].short_profile.as_deref(),
        Some("acting edge friend")
    );
    assert_eq!(
        connect.actor_queries(),
        vec![
            (
                "human_staff-1".to_string(),
                FriendListQuery {
                    target_type: Some(ActorKind::Bot),
                    offset: 0,
                    limit: 100,
                },
            ),
            (
                "acting".to_string(),
                FriendListQuery {
                    target_type: Some(ActorKind::Bot),
                    offset: 0,
                    limit: 100,
                },
            ),
        ],
        "each perspective performs exactly one bounded read of its own actor's edges"
    );
}

#[tokio::test]
async fn search_candidates_falls_back_to_names_with_an_empty_worker_profile() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    let connect = Arc::new(RecordingConnectService::new(vec![FriendListEntry {
        actor_id: "planning-bot".to_string(),
        name: Some("规划助手".to_string()),
        summary: None,
        is_online: false,
        kind: ActorKind::Bot,
    }]));
    let candidate_search: Arc<dyn BotCandidateSearchCoreService> = Arc::new(
        BotCandidateSearchCore::new(
            registry.clone(),
            Arc::new(NoopFriendCoreService),
            Arc::new(EmptyWorkerProfileCoreService),
            0.0,
        )
        .with_control_plane(control_plane.clone()),
    );
    let env = bcs_config::resolve_env_str();
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(NoopFriendCoreService),
        connect.clone(),
        candidate_search,
        BotServiceConfig { env: env.clone() },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
    repo.ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human actor");
    repo.register_with_owner_and_token(
        "planning-bot".to_string(),
        BotCapabilities {
            name: Some("规划助手".to_string()),
            summary: Some("负责规划".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        "staff-2",
        "token-planning-bot",
    )
    .await
    .expect("register planning bot");

    let result = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            query: Some("规划".to_string()),
        })
        .await
        .expect("Human perspective name fallback");

    assert!(matches!(
        result.search_mode,
        BotCandidateSearchMode::NameFallback
    ));
    assert!(result.items.iter().all(|item| item.score.is_none()));
    assert!(result.items.iter().all(|item| item.short_profile.is_none()));
    assert_eq!(result.items.len(), 1);
    assert_eq!(result.items[0].bot.bot_id, "planning-bot");
    assert!(result.items[0].is_friend);
    assert_eq!(
        connect.queries(),
        vec![FriendListQuery {
            target_type: Some(ActorKind::Bot),
            offset: 0,
            limit: 100,
        }],
        "the fallback reuses the request's single friend read"
    );
}

#[tokio::test]
async fn search_candidates_empty_queries_skip_friend_reads_and_the_core() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    let connect = Arc::new(RecordingConnectService::default());
    let candidate_search = Arc::new(RecordingCandidateSearch::empty());
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(FriendCore::memory()),
        connect.clone(),
        candidate_search.clone(),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
    register_search_bot(&repo, "acting", "staff-1", "private").await;
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;

    for query in [None, Some(String::new()), Some("  \t\n ".to_string())] {
        let result = service
            .search_candidates(SearchBotCandidates {
                caller: human_caller("staff-1"),
                bot_id: "acting".to_string(),
                purpose: BotCandidatePurpose::Discovery,
                query,
            })
            .await
            .expect("empty query search");

        assert!(result.items.is_empty());
        assert_eq!(result.search_mode, BotCandidateSearchMode::EmptyQuery);
    }

    assert!(
        candidate_search
            .queries
            .lock()
            .expect("candidate search queries lock")
            .is_empty(),
        "an empty query must not reach the Core"
    );
    assert!(
        connect.queries().is_empty(),
        "an empty query must not read friends"
    );
}

#[tokio::test]
async fn search_candidates_preserves_name_fallback_order_and_omits_semantic_enrichment() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    let candidate_search = Arc::new(RecordingCandidateSearch::new(
        BotCandidateSearchCoreResult {
            hits: vec![
                search_hit(
                    "fallback-b",
                    false,
                    Some(0.0),
                    Some("must not escape fallback mode"),
                ),
                search_hit(
                    "fallback-a",
                    true,
                    Some(0.0),
                    Some("must not escape fallback mode"),
                ),
            ],
            mode: CoreCandidateSearchMode::NameFallback,
        },
    ));
    let connect = Arc::new(RecordingConnectService::new(vec![FriendListEntry {
        actor_id: "fallback-a".to_string(),
        name: Some("Fallback A".to_string()),
        summary: None,
        is_online: false,
        kind: ActorKind::Bot,
    }]));
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(FriendCore::memory()),
        connect.clone(),
        candidate_search.clone(),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;
    for bot_id in ["acting", "fallback-a", "fallback-b"] {
        register_search_bot(
            &repo,
            bot_id,
            if bot_id == "acting" { "staff-1" } else { "staff-2" },
            if bot_id == "acting" { "private" } else { "public" },
        )
        .await;
    }

    let result = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            query: Some(" fallback ".to_string()),
        })
        .await
        .expect("fallback search");

    assert_eq!(result.search_mode, BotCandidateSearchMode::NameFallback);
    assert_eq!(
        result
            .items
            .iter()
            .map(|item| item.bot.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["fallback-b", "fallback-a"]
    );
    assert!(result.items.iter().all(|item| item.score.is_none()));
    assert!(result.items.iter().all(|item| item.short_profile.is_none()));
    let queries = candidate_search
        .queries
        .lock()
        .expect("candidate search queries lock")
        .clone();
    assert_eq!(queries.len(), 1);
    assert_eq!(queries[0].query, "fallback");
    assert_eq!(queries[0].viewer_kind, ActorKind::Bot);
    assert_eq!(
        queries[0].friend_ids,
        HashSet::from(["fallback-a".to_string()])
    );
    assert_eq!(
        connect.queries(),
        vec![FriendListQuery {
            target_type: Some(ActorKind::Bot),
            offset: 0,
            limit: 100,
        }],
        "name fallback must not trigger a second friend read"
    );
}

#[tokio::test]
async fn search_candidates_maps_core_empty_query_mode_without_enrichment() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    // Hits paired with the `EmptyQuery` Core mode are unreachable from the
    // real Core (it returns before recall). This test pins only the
    // strip-enrichment mapping for that mode, not desired output.
    let candidate_search = Arc::new(RecordingCandidateSearch::new(
        BotCandidateSearchCoreResult {
            hits: vec![search_hit(
                "unexpected-empty-hit",
                false,
                Some(0.9),
                Some("must not escape empty mode"),
            )],
            mode: CoreCandidateSearchMode::EmptyQuery,
        },
    ));
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(FriendCore::memory()),
        Arc::new(NoopConnectService),
        candidate_search,
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;
    for bot_id in ["acting", "unexpected-empty-hit"] {
        register_search_bot(
            &repo,
            bot_id,
            if bot_id == "acting" { "staff-1" } else { "staff-2" },
            if bot_id == "acting" { "private" } else { "public" },
        )
        .await;
    }

    let result = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            query: Some("planning".to_string()),
        })
        .await
        .expect("empty-mode search");

    assert_eq!(result.search_mode, BotCandidateSearchMode::EmptyQuery);
    assert_eq!(result.items.len(), 1);
    assert!(result.items[0].score.is_none());
    assert!(result.items[0].short_profile.is_none());
}

#[tokio::test]
async fn search_candidates_never_projects_human_hits_as_physical_bots() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    let mut human_hit = search_hit("human_staff-2", false, Some(0.5), None);
    human_hit.bot.actor_kind = ActorKind::Human;
    let candidate_search = Arc::new(RecordingCandidateSearch::new(
        BotCandidateSearchCoreResult {
            hits: vec![human_hit],
            mode: CoreCandidateSearchMode::Semantic,
        },
    ));
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(FriendCore::memory()),
        Arc::new(NoopConnectService),
        candidate_search,
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
    register_search_bot(&repo, "acting", "staff-1", "private").await;
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;
    repo.ensure_human_actor("staff-2", "Other Human")
        .await
        .expect("ensure human actor");

    let error = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            query: Some("human".to_string()),
        })
        .await
        .expect_err("Human hit must never be projected as a physical Bot");

    assert_eq!(error.code(), "internal_error");
}

#[tokio::test]
async fn search_candidates_denies_other_perspectives_before_any_friend_read() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    let connect = Arc::new(RecordingConnectService::default());
    let candidate_search = Arc::new(RecordingCandidateSearch::empty());
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(FriendCore::memory()),
        connect.clone(),
        candidate_search.clone(),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
    register_search_bot(&repo, "acting", "staff-1", "private").await;
    register_search_bot(&repo, "unmanaged", "staff-2", "public").await;
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;
    repo.ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human actor");
    repo.ensure_human_actor("staff-2", "Other Human")
        .await
        .expect("ensure other human actor");

    for bot_id in ["human_staff-2", "unmanaged"] {
        let error = service
            .search_candidates(SearchBotCandidates {
                caller: human_caller("staff-1"),
                bot_id: bot_id.to_string(),
                purpose: BotCandidatePurpose::Discovery,
                query: Some("planning".to_string()),
            })
            .await
            .expect_err("non-owned perspective must fail");
        assert_eq!(error.code(), "forbidden");
    }

    assert!(
        candidate_search
            .queries
            .lock()
            .expect("candidate search queries lock")
            .is_empty()
    );
    assert!(
        connect.queries().is_empty(),
        "an unauthorized perspective must not start a friend read"
    );
}

#[tokio::test]
async fn list_and_search_agree_on_friend_markers() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    let connect = Arc::new(RecordingConnectService::new(vec![FriendListEntry {
        actor_id: "bot-friend".to_string(),
        name: Some("Bot Friend".to_string()),
        summary: None,
        is_online: false,
        kind: ActorKind::Bot,
    }]));
    let candidate_search = Arc::new(
        RecordingCandidateSearch::new(BotCandidateSearchCoreResult {
            hits: vec![
                search_hit("bot-friend", false, Some(0.5), None),
                search_hit("bot-stranger", false, Some(0.6), None),
            ],
            mode: CoreCandidateSearchMode::Semantic,
        })
        .deriving_friend_markers(),
    );
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(FriendCore::memory()),
        connect.clone(),
        candidate_search.clone(),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
    for bot_id in ["acting", "bot-friend", "bot-stranger"] {
        register_search_bot(
            &repo,
            bot_id,
            if bot_id == "acting" { "staff-1" } else { "staff-2" },
            if bot_id == "acting" { "private" } else { "public" },
        )
        .await;
    }
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;

    let page = service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("list candidates");
    let searched = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            query: Some("bot".to_string()),
        })
        .await
        .expect("search candidates");

    let listed = page
        .items
        .iter()
        .map(|item| (item.bot.bot_id.clone(), item.is_friend))
        .collect::<BTreeMap<_, _>>();
    let search_items = searched
        .items
        .iter()
        .map(|item| (item.bot.bot_id.clone(), item.is_friend))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(
        listed, search_items,
        "list and search derive is_friend from the same approved edge set"
    );
    assert_eq!(listed.get("bot-friend"), Some(&true));
    assert_eq!(listed.get("bot-stranger"), Some(&false));
    let queries = candidate_search
        .queries
        .lock()
        .expect("candidate search queries lock")
        .clone();
    assert_eq!(queries.len(), 1);
    assert_eq!(
        queries[0].friend_ids,
        HashSet::from(["bot-friend".to_string()])
    );
    assert_eq!(connect.queries().len(), 2, "one friend read per entry point");
}
