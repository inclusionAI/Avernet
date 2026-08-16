use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::{ActorDirectory, BotCandidateSearchCore, BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::core::WorkerProfileCoreService;
use bcs_service_api::{
    ActorDirectoryService, ActorListCommand, ActorSearchCommand, BotCandidateSearchCoreResult,
    BotCandidateSearchCoreService, BotCandidateSearchHit, BotCandidateSearchMode,
    BotCandidateSearchQuery, BotCandidateVisibility, BotCapabilities, BotRegistryCoreService,
    FriendCoreService, LegacyBotCandidateSearchCoreResult, ProviderAuthMode,
    ProviderBotBindingRepoPort, ProviderBotCoreService, ProviderCoreService,
    ProviderCredentialRepoPort, ProviderRepoPort, RegisterProviderBotParams, RelationCoreService,
    Skill,
};
use bcs_test_support::{NoopFriendCoreService, NoopRelationCoreService, NoopWorkerProfileService};

struct RecordingCandidateSearch {
    result: LegacyBotCandidateSearchCoreResult,
    queries: Mutex<Vec<BotCandidateSearchQuery>>,
}

impl RecordingCandidateSearch {
    fn new(result: LegacyBotCandidateSearchCoreResult) -> Self {
        Self {
            result,
            queries: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl BotCandidateSearchCoreService for RecordingCandidateSearch {
    async fn search_candidates(
        &self,
        query: BotCandidateSearchQuery,
    ) -> BotCandidateSearchCoreResult {
        self.queries.lock().unwrap().push(query);
        self.result.result.clone()
    }

    async fn search_candidates_for_legacy(
        &self,
        query: BotCandidateSearchQuery,
    ) -> LegacyBotCandidateSearchCoreResult {
        self.queries.lock().unwrap().push(query);
        self.result.clone()
    }
}

fn directory_with_default_search(
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    relation: Arc<dyn RelationCoreService>,
) -> ActorDirectory {
    let worker_profiles: Arc<dyn WorkerProfileCoreService> =
        Arc::new(NoopWorkerProfileService);
    let candidate_search = Arc::new(BotCandidateSearchCore::new(
        registry.clone(),
        friend.clone(),
        worker_profiles.clone(),
        0.0,
    ));
    ActorDirectory::new(
        registry,
        friend,
        relation,
        worker_profiles,
        candidate_search,
    )
}

#[tokio::test]
async fn list_actors_marks_provider_downlink_bots() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let provider_core = ProviderCore::new(
        provider_repo,
        provider_credentials,
        provider_bindings,
        registry.clone(),
    );

    registry
        .register(
            "current-bot".to_string(),
            BotCapabilities {
                name: Some("Current".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .expect("register current bot");
    registry
        .register(
            "ws-bot".to_string(),
            BotCapabilities {
                name: Some("WebSocket Bot".to_string()),
                visibility: "public".to_string(),
                skills: vec![Skill::new("chat")],
                ..BotCapabilities::default()
            },
        )
        .await
        .expect("register ws bot");
    registry
        .register_streaming_connection("ws-bot".to_string())
        .await
        .expect("connect ws bot");

    let provider = provider_core
        .register_provider(
            "Provider".to_string(),
            "https://provider.example.com/bcs/webhook".to_string(),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider");
    let (binding, _) = provider_core
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Bot".to_string(),
                summary: Some("Provider-managed bot".to_string()),
                owners: vec!["197262".to_string()],
                provider_bot_ref: "provider-bot-ref".to_string(),
                skills: vec![Skill::new("provider")],
                ..RegisterProviderBotParams::default()
            },
        )
        .await
        .expect("register provider bot");

    let directory = directory_with_default_search(
        registry,
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopRelationCoreService),
    );
    let result = directory
        .list_actors(ActorListCommand {
            current_bot_uuid: "current-bot".to_string(),
            cooperatable_only: false,
            offset: 0,
            limit: 10,
            ..ActorListCommand::default()
        })
        .await;

    let ws_bot = result
        .bots
        .iter()
        .find(|bot| bot.bot_uuid == "ws-bot")
        .expect("ws bot in actor list");
    assert!(!ws_bot.is_downlink);
    assert_eq!(serde_json::to_value(ws_bot).unwrap()["is_downlink"], false);
    let provider_bot = result
        .bots
        .iter()
        .find(|bot| bot.bot_uuid == binding.bot_uuid)
        .expect("provider bot in actor list");
    assert!(provider_bot.is_downlink);
    assert_eq!(
        serde_json::to_value(provider_bot).unwrap()["is_downlink"],
        true
    );
}

#[tokio::test]
async fn search_actors_delegates_empty_query_to_core_and_returns_empty_legacy_shape() {
    let candidate_search = Arc::new(RecordingCandidateSearch::new(
        LegacyBotCandidateSearchCoreResult {
            result: BotCandidateSearchCoreResult {
                hits: Vec::new(),
                mode: BotCandidateSearchMode::EmptyQuery,
            },
            recommend_response: None,
        },
    ));
    let directory = ActorDirectory::new(
        Arc::new(BotCore::memory()),
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopRelationCoreService),
        Arc::new(NoopWorkerProfileService),
        candidate_search.clone(),
    );

    let result = directory
        .search_actors(ActorSearchCommand {
            query: "   ".to_string(),
            current_bot_uuid: "current-bot".to_string(),
            cooperatable_only: false,
            limit: 20,
        })
        .await;

    assert!(result.bots.is_empty());
    assert_eq!(result.context.recommend_response, None);
    let queries = candidate_search.queries.lock().unwrap();
    assert_eq!(queries.len(), 1);
    assert_eq!(queries[0].query, "");
    assert_eq!(queries[0].acting_actor_id, "current-bot");
    assert_eq!(queries[0].visibility, BotCandidateVisibility::Discovery);
    assert_eq!(queries[0].limit, 20);
}

#[tokio::test]
async fn search_actors_projects_semantic_order_enrichment_context_status_and_downlink() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let provider_core = ProviderCore::new(
        provider_repo,
        provider_credentials,
        provider_bindings,
        registry.clone(),
    );

    registry
        .register(
            "ws-bot".to_string(),
            BotCapabilities {
                name: Some("WebSocket Bot".to_string()),
                summary: Some("online candidate".to_string()),
                visibility: "public".to_string(),
                skills: vec![Skill::new("chat")],
                ..BotCapabilities::default()
            },
        )
        .await
        .expect("register ws bot");
    registry
        .register_streaming_connection("ws-bot".to_string())
        .await
        .expect("connect ws bot");
    let provider = provider_core
        .register_provider(
            "Provider".to_string(),
            "https://provider.example.com/bcs/webhook".to_string(),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider");
    let (binding, _) = provider_core
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Bot".to_string(),
                summary: Some("downlink candidate".to_string()),
                owners: vec!["197262".to_string()],
                provider_bot_ref: "provider-bot-ref".to_string(),
                skills: vec![Skill::new("provider")],
                ..RegisterProviderBotParams::default()
            },
        )
        .await
        .expect("register provider bot");

    let provider_bot = registry.get(&binding.bot_uuid).await.expect("provider bot");
    let ws_bot = registry.get("ws-bot").await.expect("ws bot");
    let candidate_search = Arc::new(RecordingCandidateSearch::new(
        LegacyBotCandidateSearchCoreResult {
            result: BotCandidateSearchCoreResult {
                hits: vec![
                    BotCandidateSearchHit {
                        bot: provider_bot,
                        is_friend: true,
                        tags: BTreeMap::from([("team".to_string(), serde_json::json!("provider"))]),
                        score: Some(0.91),
                        short_profile: Some("provider profile".to_string()),
                    },
                    BotCandidateSearchHit {
                        bot: ws_bot,
                        is_friend: false,
                        tags: BTreeMap::from([("team".to_string(), serde_json::json!("realtime"))]),
                        score: Some(0.0),
                        short_profile: Some("websocket profile".to_string()),
                    },
                ],
                mode: BotCandidateSearchMode::Semantic,
            },
            recommend_response: Some(serde_json::json!({"trace_id": "legacy-semantic"})),
        },
    ));
    let directory = ActorDirectory::new(
        registry,
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopRelationCoreService),
        Arc::new(NoopWorkerProfileService),
        candidate_search.clone(),
    );

    let result = directory
        .search_actors(ActorSearchCommand {
            query: "  capability  ".to_string(),
            current_bot_uuid: "current-bot".to_string(),
            cooperatable_only: false,
            limit: 20,
        })
        .await;

    assert_eq!(
        result
            .bots
            .iter()
            .map(|bot| bot.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        vec![binding.bot_uuid.as_str(), "ws-bot"]
    );
    assert_eq!(result.bots[0].score, Some(0.91));
    assert_eq!(
        result.bots[0].short_profile.as_deref(),
        Some("provider profile")
    );
    assert_eq!(result.bots[0].tags["team"], serde_json::json!("provider"));
    assert!(result.bots[0].is_friend);
    assert_eq!(result.bots[0].dynamic_status.status, "active");
    assert!(result.bots[0].is_downlink);
    assert_eq!(result.bots[1].score, Some(0.0));
    assert_eq!(result.bots[1].dynamic_status.status, "active");
    assert!(!result.bots[1].is_downlink);
    assert_eq!(
        result.context.recommend_response,
        Some(serde_json::json!({"trace_id": "legacy-semantic"}))
    );
    let queries = candidate_search.queries.lock().unwrap();
    assert_eq!(queries.len(), 1);
    assert_eq!(queries[0].query, "capability");
    assert_eq!(queries[0].acting_actor_id, "current-bot");
    assert_eq!(queries[0].visibility, BotCandidateVisibility::Discovery);
    assert_eq!(queries[0].limit, 20);
}

#[tokio::test]
async fn candidate_search_is_an_explicit_constructor_dependency() {
    let registry = Arc::new(BotCore::memory());
    registry
        .register(
            "shared-core-bot".to_string(),
            BotCapabilities {
                name: Some("Shared Core Bot".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .expect("register shared core bot");
    let shared_bot = registry
        .get("shared-core-bot")
        .await
        .expect("shared core bot");
    let result = LegacyBotCandidateSearchCoreResult {
        result: BotCandidateSearchCoreResult {
            hits: vec![BotCandidateSearchHit {
                bot: shared_bot,
                is_friend: false,
                tags: BTreeMap::new(),
                score: Some(0.73),
                short_profile: Some("shared core profile".to_string()),
            }],
            mode: BotCandidateSearchMode::Semantic,
        },
        recommend_response: Some(serde_json::json!({"source": "shared-core"})),
    };
    let candidate_search = Arc::new(RecordingCandidateSearch::new(result));

    let directory = ActorDirectory::new(
        registry,
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopRelationCoreService),
        Arc::new(NoopWorkerProfileService),
        candidate_search.clone(),
    );

    let command = ActorSearchCommand {
        query: "  semantic query  ".to_string(),
        current_bot_uuid: "current-bot".to_string(),
        cooperatable_only: true,
        limit: 9,
    };
    let result = directory.search_actors(command).await;

    assert_eq!(result.bots.len(), 1);
    assert_eq!(result.bots[0].bot_uuid, "shared-core-bot");
    assert_eq!(result.bots[0].score, Some(0.73));
    assert_eq!(
        result.context.recommend_response,
        Some(serde_json::json!({"source": "shared-core"}))
    );
    let queries = candidate_search.queries.lock().unwrap();
    assert_eq!(queries.len(), 1);
    assert_eq!(queries[0].query, "semantic query");
    assert_eq!(queries[0].acting_actor_id, "current-bot");
    assert_eq!(queries[0].visibility, BotCandidateVisibility::Collaboration);
    assert_eq!(queries[0].limit, 9);
}

#[tokio::test]
async fn search_actors_restores_fallback_score_and_skill_summary() {
    let registry = Arc::new(BotCore::memory());
    registry
        .register(
            "fallback-bot".to_string(),
            BotCapabilities {
                name: Some("Fallback Bot".to_string()),
                visibility: "protected".to_string(),
                skills: vec![Skill::new("review"), Skill::new("deploy")],
                ..BotCapabilities::default()
            },
        )
        .await
        .expect("register fallback bot");
    let fallback_bot = registry.get("fallback-bot").await.expect("fallback bot");
    let candidate_search = Arc::new(RecordingCandidateSearch::new(
        LegacyBotCandidateSearchCoreResult {
            result: BotCandidateSearchCoreResult {
                hits: vec![BotCandidateSearchHit {
                    bot: fallback_bot,
                    is_friend: true,
                    tags: BTreeMap::from([("source".to_string(), serde_json::json!("fallback"))]),
                    score: None,
                    short_profile: None,
                }],
                mode: BotCandidateSearchMode::NameFallback,
            },
            recommend_response: Some(serde_json::json!({"recommendations": []})),
        },
    ));
    let directory = ActorDirectory::new(
        registry,
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopRelationCoreService),
        Arc::new(NoopWorkerProfileService),
        candidate_search.clone(),
    );

    let result = directory
        .search_actors(ActorSearchCommand {
            query: "  Fallback  ".to_string(),
            current_bot_uuid: "current-bot".to_string(),
            cooperatable_only: true,
            limit: 7,
        })
        .await;

    assert_eq!(result.bots.len(), 1);
    assert_eq!(result.bots[0].score, Some(0.0));
    assert_eq!(
        result.bots[0].short_profile.as_deref(),
        Some("bot 能力: review;deploy")
    );
    assert_eq!(result.bots[0].tags["source"], serde_json::json!("fallback"));
    assert_eq!(
        result.context.recommend_response,
        Some(serde_json::json!({"recommendations": []}))
    );
    let queries = candidate_search.queries.lock().unwrap();
    assert_eq!(queries.len(), 1);
    assert_eq!(queries[0].query, "Fallback");
    assert_eq!(queries[0].visibility, BotCandidateVisibility::Collaboration);
    assert_eq!(queries[0].limit, 7);
}
