#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_bot::{BotServiceConfig, BotServiceImpl};
use bcs_bot::{BotControlPlaneCore, BotCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_friend::FriendCore;
use bcs_service_api::application::v1::{
    ApplicationError, Bot, BotCandidatePurpose, BotCandidateSearchMode, BotDescriptorPatch,
    BotKind, BotPatch, BotReachability, BotService, BotStatus, BotVisibility, GetBot,
    ListBotCandidates, ListMyBots, QueryBots, SearchBotCandidates, UpdateBot,
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateSearchCoreResult, BotCandidateSearchCoreService,
    BotCandidateSearchHit, BotCandidateSearchMode as CoreCandidateSearchMode,
    BotCandidateSearchQuery, BotCapabilities, BotControlPlaneCoreService,
    BotControlPlaneDescriptor, BotControlPlaneRecord, BotRegistryCoreService, BotRepoPort,
    FriendCoreService, LegacyBotCandidateSearchCoreResult, ProviderBotBinding,
    ProviderBotBindingRepoPort, ProviderRecord, ProviderRepoPort, RegisteredBot, ServiceError,
    ServiceResult, Skill,
};
use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};

#[test]
fn v1_bot_commands_expose_the_approved_control_plane_surface() {
    let caller = human_caller("staff-1");

    let _ = ListBotCandidates {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
        purpose: Default::default(),
        name: None,
        offset: 0,
        limit: 20,
    };
    let _ = SearchBotCandidates {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
        purpose: Default::default(),
        query: Some("planning".to_string()),
    };
    let _ = QueryBots {
        caller: caller.clone(),
        bot_ids: vec!["bot-1".to_string()],
    };
    let _ = GetBot {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
    };
    let _ = UpdateBot {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
        patch: BotPatch {
            name: Some("Renamed".to_string()),
            visibility: Some(BotVisibility::Protected),
            status: Some(BotStatus::Online),
            descriptor: Some(BotDescriptorPatch {
                summary: Some("summary".to_string()),
                domains: None,
                skills: None,
                scopes: None,
            }),
            task_claim_mode: None,
            task_dream_mode: None,
        },
    };
    let _ = ListMyBots {
        caller,
        kind: Some(BotKind::Bot),
        name: None,
        status: None,
        reachability: Some(BotReachability::Reachable),
        offset: 0,
        limit: 20,
    };

    let _assert_object_safe: fn(&dyn BotService) = |_| {};
}

struct Fixture {
    service: BotServiceImpl,
    repo: Arc<MemoryBotRepo>,
    friends: Arc<FriendCore>,
    providers: Arc<MemoryProviderStore>,
    _temp: tempfile::TempDir,
}

impl Fixture {
    fn new() -> Self {
        let temp = tempfile::tempdir().expect("temp dir");
        let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
        let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_repo(repo.clone()));
        let friends = Arc::new(FriendCore::memory());
        let providers = Arc::new(MemoryProviderStore::new());
        let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(
            BotControlPlaneCore::new(repo.clone(), providers.clone(), providers.clone()),
        );
        let env = bcs_config::resolve_env_str();
        let service = BotServiceImpl::new(
            control_plane,
            registry,
            friends.clone(),
            Arc::new(RecordingCandidateSearch::empty()),
            BotServiceConfig { env: env.clone() },
        );
        Self {
            service,
            repo,
            friends,
            providers,
            _temp: temp,
        }
    }

    async fn add_bot(&self, bot_id: &str, owner: &str, visibility: &str, status: ActorStatus) {
        self.repo
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    summary: Some(format!("summary-{bot_id}")),
                    domains: vec!["planning".to_string()],
                    skills: vec![Skill::with_description("plan", "Make a plan")],
                    scopes: vec!["workspace".to_string()],
                    visibility: visibility.to_string(),
                    agent_code: Some(format!("agent-{bot_id}")),
                    ..Default::default()
                },
                owner,
                &format!("token-{bot_id}"),
            )
            .await
            .expect("register bot");
        self.repo
            .update_actor_status(bot_id, status)
            .await
            .expect("update actor status");
    }
}

struct RecordingCandidateSearch {
    queries: Mutex<Vec<BotCandidateSearchQuery>>,
    result: BotCandidateSearchCoreResult,
}

impl RecordingCandidateSearch {
    fn new(result: BotCandidateSearchCoreResult) -> Self {
        Self {
            queries: Mutex::new(Vec::new()),
            result,
        }
    }

    fn empty() -> Self {
        Self::new(BotCandidateSearchCoreResult {
            hits: Vec::new(),
            mode: CoreCandidateSearchMode::EmptyQuery,
        })
    }
}

#[async_trait]
impl BotCandidateSearchCoreService for RecordingCandidateSearch {
    async fn search_candidates(
        &self,
        query: BotCandidateSearchQuery,
    ) -> BotCandidateSearchCoreResult {
        self.queries
            .lock()
            .expect("candidate search queries lock")
            .push(query);
        self.result.clone()
    }

    async fn search_candidates_for_legacy(
        &self,
        _query: BotCandidateSearchQuery,
    ) -> LegacyBotCandidateSearchCoreResult {
        panic!("OpenAPI V1 must not call the legacy candidate-search entry point")
    }
}

fn search_hit(
    bot_id: &str,
    is_friend: bool,
    score: Option<f64>,
    short_profile: Option<&str>,
) -> BotCandidateSearchHit {
    BotCandidateSearchHit {
        bot: RegisteredBot {
            bot_uuid: bot_id.to_string(),
            capabilities: BotCapabilities {
                name: Some(bot_id.to_string()),
                summary: Some(format!("summary-{bot_id}")),
                skills: vec![Skill::new("plan")],
                domains: vec!["planning".to_string()],
                scopes: vec!["workspace".to_string()],
                visibility: "public".to_string(),
                ..Default::default()
            },
            dynamic_status: Default::default(),
            env: None,
            created_by: None,
            actor_kind: ActorKind::Bot,
            status: ActorStatus::Online,
        },
        is_friend,
        tags: BTreeMap::from([("specialty".to_string(), serde_json::json!("planning"))]),
        score,
        short_profile: short_profile.map(str::to_string),
    }
}

struct AuthorizationProbeCore {
    record: BotControlPlaneRecord,
}

#[async_trait]
impl BotControlPlaneCoreService for AuthorizationProbeCore {
    async fn get_record(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        Ok(Some(self.record.clone()))
    }

    async fn get(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "Provider hydration must happen after authorization".to_string(),
        ))
    }

    async fn get_by_ids(
        &self,
        _bot_ids: &[String],
        _env: &str,
    ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        unreachable!("not used by authorization-priority test")
    }

    async fn list_candidates(
        &self,
        _query: bcs_service_api::BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<bcs_service_api::BotControlPlaneCandidate>, u64)> {
        unreachable!("not used by authorization-priority test")
    }

    async fn list_by_creator(
        &self,
        _query: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        unreachable!("not used by authorization-priority test")
    }

    async fn patch(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        unreachable!("not used by authorization-priority test")
    }
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
        },
    });
    let service = BotServiceImpl::new(
        control_plane,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        Arc::new(RecordingCandidateSearch::empty()),
        BotServiceConfig { env },
    );

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
        candidate_search.clone(),
        BotServiceConfig { env },
    );

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
        candidate_search.clone(),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );
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
        candidate_search,
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );
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
        candidate_search,
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );
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
        candidate_search,
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );
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
        candidate_search.clone(),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );
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

#[tokio::test]
async fn candidates_require_a_human_owner_and_allow_the_current_human_actor() {
    let fixture = Fixture::new();
    fixture
        .add_bot("acting", "staff-1", "private", ActorStatus::Online)
        .await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");
    fixture
        .repo
        .ensure_human_actor("staff-2", "Other Human")
        .await
        .expect("ensure other human");

    let error = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-2"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect_err("non-owner must fail");
    assert_eq!(error.code(), "forbidden");

    let page = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("current human actor may select candidates");
    assert_eq!(page.total, 0);

    let error = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-2".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect_err("another human actor must fail");
    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn human_actor_collaboration_candidates_include_only_private_friends() {
    let fixture = Fixture::new();
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");
    fixture
        .add_bot("private-friend", "staff-2", "private", ActorStatus::Hidden)
        .await;
    fixture
        .add_bot(
            "private-stranger",
            "staff-3",
            "private",
            ActorStatus::Online,
        )
        .await;
    fixture
        .friends
        .add_friendship("human_staff-1", "private-friend")
        .await
        .expect("add human friendship");

    let page = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            purpose: BotCandidatePurpose::Collaboration,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("list human candidates");

    assert_eq!(page.total, 1);
    assert_eq!(page.items[0].bot.bot_id, "private-friend");
    assert!(page.items[0].is_friend);
}

#[tokio::test]
async fn collaboration_candidates_include_private_friends_without_status_filtering() {
    let fixture = Fixture::new();
    fixture
        .add_bot("acting", "staff-1", "private", ActorStatus::Online)
        .await;
    fixture
        .add_bot("private-friend", "staff-2", "private", ActorStatus::Hidden)
        .await;
    fixture
        .friends
        .add_friendship("acting", "private-friend")
        .await
        .expect("add friendship");

    let page = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Collaboration,
            name: Some(" FRIEND ".to_string()),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("list candidates");
    assert_eq!(page.total, 1);
    assert_eq!(page.items[0].bot.bot_id, "private-friend");
    assert_eq!(page.items[0].bot.status, BotStatus::Hidden);
    assert_eq!(page.items[0].bot.reachability, BotReachability::Unreachable);
    assert!(page.items[0].is_friend);
}

#[tokio::test]
async fn query_preserves_first_occurrence_and_projects_both_kinds_provider_and_reachability() {
    let fixture = Fixture::new();
    fixture
        .add_bot("physical", "staff-1", "private", ActorStatus::Online)
        .await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");
    fixture
        .repo
        .register_streaming_connection("physical".to_string())
        .await
        .expect("connect physical bot");
    fixture
        .providers
        .insert_provider(ProviderRecord {
            provider_id: "provider-1".to_string(),
            name: "Provider One".to_string(),
            config: "{}".to_string(),
            created_by: "staff-1".to_string(),
            owners: "[]".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("insert provider");
    fixture
        .providers
        .insert_binding(ProviderBotBinding {
            bot_uuid: "physical".to_string(),
            provider_id: "provider-1".to_string(),
            provider_bot_ref: "secret-internal-ref".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("insert binding");

    let bots = fixture
        .service
        .query(QueryBots {
            caller: human_caller("staff-2"),
            bot_ids: vec![
                "human_staff-1".to_string(),
                "missing".to_string(),
                "physical".to_string(),
                "human_staff-1".to_string(),
            ],
        })
        .await
        .expect("query bots");
    assert_eq!(
        bots.iter().map(Bot::bot_id).collect::<Vec<_>>(),
        vec!["human_staff-1", "physical"]
    );
    let Bot::Physical(physical) = &bots[1] else {
        panic!("expected physical bot");
    };
    assert_eq!(physical.reachability, BotReachability::Reachable);
    assert_eq!(
        physical.provider.as_ref().map(|p| p.name.as_str()),
        Some("Provider One")
    );
    assert_eq!(physical.agent_code.as_deref(), Some("agent-physical"));
}

#[tokio::test]
async fn update_requires_created_by_and_rejects_descriptor_for_human() {
    let fixture = Fixture::new();
    fixture
        .add_bot("owned", "staff-1", "public", ActorStatus::Online)
        .await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");

    let error = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-2"),
            bot_id: "owned".to_string(),
            patch: BotPatch {
                name: Some("Nope".to_string()),
                ..Default::default()
            },
        })
        .await
        .expect_err("non-owner update");
    assert_eq!(error.code(), "forbidden");

    let error = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            patch: BotPatch {
                descriptor: Some(BotDescriptorPatch {
                    summary: Some("not allowed".to_string()),
                    ..Default::default()
                }),
                ..Default::default()
            },
        })
        .await
        .expect_err("human descriptor update");
    assert_eq!(error.code(), "invalid_bot_kind");

    let error = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            patch: BotPatch {
                task_claim_mode: Some(true),
                ..Default::default()
            },
        })
        .await
        .expect_err("human task-mode update");
    assert_eq!(error.code(), "invalid_bot_kind");

    let updated = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-1"),
            bot_id: "owned".to_string(),
            patch: BotPatch {
                name: Some(" Renamed ".to_string()),
                visibility: Some(BotVisibility::Protected),
                status: Some(BotStatus::Hidden),
                descriptor: Some(BotDescriptorPatch {
                    domains: Some(vec![]),
                    scopes: Some(vec!["new-scope".to_string()]),
                    ..Default::default()
                }),
                task_claim_mode: None,
                task_dream_mode: None,
            },
        })
        .await
        .expect("owner update");
    let Bot::Physical(updated) = updated else {
        panic!("expected physical bot");
    };
    assert_eq!(updated.name, "Renamed");
    assert_eq!(updated.visibility, BotVisibility::Protected);
    assert_eq!(updated.status, BotStatus::Hidden);
    assert!(updated.descriptor.domains.is_empty());
    assert_eq!(updated.descriptor.scopes, vec!["new-scope"]);
}

#[tokio::test]
async fn mine_accepts_tenantless_users_and_callers_without_user_are_forbidden() {
    let fixture = Fixture::new();
    fixture
        .add_bot("reachable", "staff-1", "public", ActorStatus::Online)
        .await;
    fixture
        .add_bot("unreachable", "staff-1", "public", ActorStatus::Online)
        .await;
    fixture
        .repo
        .register_streaming_connection("reachable".to_string())
        .await
        .expect("connect bot");
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");

    let mut tenantless_user = human_caller("staff-1");
    tenantless_user.tenant = None;
    let page = fixture
        .service
        .list_mine(ListMyBots {
            caller: tenantless_user,
            kind: None,
            name: None,
            status: None,
            reachability: Some(BotReachability::Reachable),
            offset: 0,
            limit: 1,
        })
        .await
        .expect("list mine");
    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].bot_id(), "reachable");

    let error = fixture
        .service
        .get(GetBot {
            caller: bot_only_caller("reachable"),
            bot_id: "reachable".to_string(),
        })
        .await
        .expect_err("caller without User must be rejected");
    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn invalid_application_inputs_use_stable_codes() {
    let fixture = Fixture::new();
    let error = fixture
        .service
        .query(QueryBots {
            caller: human_caller("staff-1"),
            bot_ids: (0..101).map(|index| format!("bot-{index}")).collect(),
        })
        .await
        .expect_err("oversized query must fail");
    assert!(matches!(error, ApplicationError::InvalidInput { .. }));
    assert_eq!(error.code(), "invalid_request");
}

fn human_caller(staff_no: &str) -> bcs_service_api::application::v1::AuthenticatedCaller {
    bcs_service_api::application::v1::AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: Some(
            bcs_service_api::application::v1::AuthenticatedUserIdentity {
                id: staff_no.to_string(),
                username: staff_no.to_string(),
                display_name: None,
                full_name: None,
            },
        ),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn bot_only_caller(bot_uuid: &str) -> bcs_service_api::application::v1::AuthenticatedCaller {
    bcs_service_api::application::v1::AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: None,
        bot: Some(bcs_service_api::application::v1::AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: "staff-1".into(),
            app_id: 1,
            agent_code: "agent".into(),
        }),
        app: None,
        access_key: None,
    }
}
