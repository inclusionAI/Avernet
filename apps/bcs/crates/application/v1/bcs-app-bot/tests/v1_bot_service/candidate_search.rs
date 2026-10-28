use super::*;

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
async fn search_candidates_calls_core_once_and_preserves_ranked_enrichment() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers.clone(),
    ));
    let friends = Arc::new(FriendCore::memory());
    let candidate_search = Arc::new(RecordingCandidateSearch::new(
        BotCandidateSearchCoreResult {
            hits: vec![
                search_hit("recommended-b", true, Some(0.0), Some("best match")),
                search_hit("recommended-a", false, Some(0.7), Some("second match")),
            ],
            mode: CoreCandidateSearchMode::Semantic,
        },
    ));
    let env = bcs_config::resolve_env_str();
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        friends,
        Arc::new(NoopConnectService),
        candidate_search.clone(),
        BotServiceConfig { env },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(
        repo.clone(),
    ));

    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;
    for bot_id in ["acting", "recommended-a", "recommended-b"] {
        repo.register_with_owner_and_token(
            bot_id.to_string(),
            BotCapabilities {
                name: Some(bot_id.to_string()),
                summary: Some(format!("summary-{bot_id}")),
                visibility: if bot_id == "acting" {
                    "private".to_string()
                } else {
                    "public".to_string()
                },
                ..Default::default()
            },
            if bot_id == "acting" {
                "staff-1"
            } else {
                "staff-2"
            },
            &format!("token-{bot_id}"),
        )
        .await
        .expect("register search bot");
    }
    repo.register_streaming_connection("recommended-b".to_string())
        .await
        .expect("connect recommended bot");
    providers
        .insert_provider(ProviderRecord {
            provider_id: "provider-search".to_string(),
            slug: None,
            name: "Search Provider".to_string(),
            config: "{}".to_string(),
            created_by: "staff-2".to_string(),
            owners: "[]".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("insert search provider");
    providers
        .insert_binding(ProviderBotBinding {
            webhook_url: None,
            bot_uuid: "recommended-b".to_string(),
            provider_id: "provider-search".to_string(),
            provider_bot_ref: "private-provider-reference".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("insert search provider binding");

    let result = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Collaboration,
            query: Some("  planning help  ".to_string()),
        })
        .await
        .expect("search candidates");

    let commands = candidate_search
        .queries
        .lock()
        .expect("candidate search queries lock")
        .clone();
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].query, "planning help");
    assert_eq!(commands[0].acting_actor_id, "acting");
    assert_eq!(
        commands[0].visibility,
        bcs_service_api::BotCandidateVisibility::Collaboration
    );
    assert_eq!(commands[0].limit, 20);
    assert_eq!(
        result
            .items
            .iter()
            .map(|item| item.bot.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["recommended-b", "recommended-a"]
    );
    assert!(result.items[0].is_friend);
    assert_eq!(result.items[0].score, Some(0.0));
    assert_eq!(result.items[0].short_profile.as_deref(), Some("best match"));
    assert_eq!(result.items[0].bot.kind, BotKind::Bot);
    assert_eq!(result.items[0].bot.name, "recommended-b");
    assert_eq!(
        result.items[0].bot.descriptor.summary,
        "summary-recommended-b"
    );
    assert_eq!(result.items[0].bot.reachability, BotReachability::Reachable);
    assert_eq!(
        result.items[0]
            .bot
            .provider
            .as_ref()
            .map(|provider| provider.name.as_str()),
        Some("Search Provider")
    );
    assert_eq!(
        result.items[0].tags.get("specialty"),
        Some(&serde_json::json!("planning"))
    );
    assert_eq!(result.search_mode, BotCandidateSearchMode::Semantic);
    let serialized = serde_json::to_value(&result).expect("serialize search result");
    assert!(serialized.get("context").is_none());
    assert!(serialized.get("recommend_response").is_none());
}
#[tokio::test]
async fn search_candidates_normalizes_missing_empty_and_whitespace_queries() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    let candidate_search = Arc::new(RecordingCandidateSearch::empty());
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(FriendCore::memory()),
        Arc::new(NoopConnectService),
        candidate_search.clone(),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(
        repo.clone(),
    ));
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;
    repo.register_with_owner_and_token(
        "acting".to_string(),
        BotCapabilities {
            name: Some("acting".to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        "staff-1",
        "token-acting",
    )
    .await
    .expect("register acting bot");
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

    let queries = candidate_search
        .queries
        .lock()
        .expect("candidate search queries lock");
    assert_eq!(queries.len(), 3);
    assert!(queries.iter().all(|query| query.query.is_empty()));
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
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(
        repo.clone(),
    ));
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;
    for bot_id in ["acting", "fallback-a", "fallback-b"] {
        repo.register_with_owner_and_token(
            bot_id.to_string(),
            BotCapabilities {
                name: Some(bot_id.to_string()),
                visibility: if bot_id == "acting" {
                    "private".to_string()
                } else {
                    "public".to_string()
                },
                ..Default::default()
            },
            if bot_id == "acting" {
                "staff-1"
            } else {
                "staff-2"
            },
            &format!("token-{bot_id}"),
        )
        .await
        .expect("register fallback bot");
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
}
#[tokio::test]
async fn search_candidates_omits_enrichment_for_empty_query_mode() {
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
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(
        repo.clone(),
    ));
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;
    for bot_id in ["acting", "unexpected-empty-hit"] {
        repo.register_with_owner_and_token(
            bot_id.to_string(),
            BotCapabilities {
                name: Some(bot_id.to_string()),
                visibility: if bot_id == "acting" {
                    "private".to_string()
                } else {
                    "public".to_string()
                },
                ..Default::default()
            },
            if bot_id == "acting" {
                "staff-1"
            } else {
                "staff-2"
            },
            &format!("token-{bot_id}"),
        )
        .await
        .expect("register empty-mode bot");
    }

    let result = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            query: None,
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
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(
        repo.clone(),
    ));
    repo.register_with_owner_and_token(
        "acting".to_string(),
        BotCapabilities {
            name: Some("acting".to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        "staff-1",
        "token-acting",
    )
    .await
    .expect("register acting bot");
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
async fn search_candidates_denies_unauthorized_perspective_before_core_search() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        providers.clone(),
        providers,
    ));
    let candidate_search = Arc::new(RecordingCandidateSearch::empty());
    let service = BotServiceImpl::new(
        control_plane,
        registry,
        Arc::new(FriendCore::memory()),
        Arc::new(NoopConnectService),
        candidate_search.clone(),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(
        repo.clone(),
    ));
    repo.register_with_owner_and_token(
        "acting".to_string(),
        BotCapabilities {
            name: Some("acting".to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        "staff-1",
        "token-acting",
    )
    .await
    .expect("register acting bot");
    bcs_test_support::grant_owner_edge(&repo, "acting", "staff-1").await;

    let error = service
        .search_candidates(SearchBotCandidates {
            caller: human_caller("staff-2"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            query: Some("planning".to_string()),
        })
        .await
        .expect_err("non-owner search must fail");

    assert_eq!(error.code(), "forbidden");
    assert!(
        candidate_search
            .queries
            .lock()
            .expect("candidate search queries lock")
            .is_empty()
    );
}
