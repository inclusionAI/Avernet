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
            Some("https://provider.example.com/bcs/webhook".to_string()),
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
                webhook_url: None,
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
            Some("https://provider.example.com/bcs/webhook".to_string()),
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
                webhook_url: None,
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

// ---------------------------------------------------------------------------
// Bot-authority review (P1): the cross-actor status write
//二十年 lifespan the legacy creator relation edge even after
// transfers emptied it — the Human→Bot leg must consult the
// LIVE authority hook; the non-Human caller leg keeps the
// legacy creator-relation contract.
// ---------------------------------------------------------------------------

use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::{ActorStatus, ActorStatusUpdateCommand};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, ManagerEdit, ManagerEditAction, ManagerSync,
    TransferAction, TransferCommand, TransferKey,
};

fn status_command(caller: &str, bot: &str, hidden: bool) -> ActorStatusUpdateCommand {
    ActorStatusUpdateCommand {
        caller_actor_id: caller.to_string(),
        actor_id: bot.to_string(),
        status: if hidden {
            ActorStatus::Hidden
        } else {
            ActorStatus::Online
        },
    }
}

fn authority_mutation(id: &str, user: &str) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: id.to_string(),
        actor: AuthorityActor::Human {
            user_id: user.to_string(),
        },
    }
}

/// Register `status-bot` owned by `owner` (authority edge
/// minted via the trusted registration composite) and hand
/// the memory repo + a built ActorDirectory to the caller.
async fn owner_directory_fixture(
    owner: &str,
) -> (tempfile::TempDir, Arc<MemoryBotRepo>, ActorDirectory) {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    repo.create_registration_if_absent_with_ownership(
        "status-bot".to_string(),
        BotCapabilities {
            name: Some("Status Bot".to_string()),
            ..Default::default()
        },
        owner,
        "status-token",
        bcs_service_api::types::bot_authority::OwnershipInitialization {
            owner_user_id: owner.to_string(),
            mutation: authority_mutation("status-register", owner),
        },
    )
    .await
    .expect("trusted registration");

    let registry: Arc<dyn BotRegistryCoreService> =
        Arc::new(BotCore::with_repo(repo.clone()));
    let directory = directory_with_default_search(
        registry,
        Arc::new(bcs_test_support::NoopFriendCoreService),
        Arc::new(bcs_test_support::NoopRelationCoreService),
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(repo.clone()));
    (temp_dir, repo, directory)
}

async fn transfer_to(repo: &Arc<MemoryBotRepo>, new_owner: &str) {
    for (action, user, tag) in [
        (TransferAction::RequestOrRefresh, "11111111", "request"),
        (TransferAction::Accept, "22222222", "accept"),
    ] {
        repo.change_transfer(TransferCommand {
            key: TransferKey {
                bot_id: "status-bot".to_string(),
                target_owner_actor_id: format!("human_{new_owner}"),
            },
            action,
            mutation: authority_mutation(tag, user),
        })
        .await
        .expect("transfer leg");
    }
}

#[tokio::test]
async fn revoked_creator_cannot_mutate_actor_status() {
    let (_temp, repo, directory) = owner_directory_fixture("11111111").await;
    repo.ensure_human_actor("22222222", "Bob").await.expect("bob actor");
    transfer_to(&repo, "22222222").await;
    // The transferred-out owner keeps nothing: the legacy
    // creator relation edge must not resurrect the write.
    repo.sync_managers(ManagerSync {
        bot_id: "status-bot".to_string(),
        user_ids: vec![],
        mutation: AuthorityMutation {
            mutation_id: "clear-managers".to_string(),
            actor: AuthorityActor::Service { app_id: 1 },
        },
    })
    .await
    .expect("clear managers");
    assert_eq!(
        repo.role("human_11111111", "status-bot").await
        .expect("role"),
        None,
        "precondition: the old creator holds no role"
    );

    let error = directory
        .update_actor_status_for_caller(status_command("human_11111111", "status-bot", true))
        .await
        .expect_err("revoked creator must not mutate status");
    assert!(
        matches!(error, bcs_service_api::ServiceError::Unauthorized(_)),
        "Unauthorized expected, got {error:?}"
    );
}

#[tokio::test]
async fn transferred_in_owner_and_granted_manager_can_mutate() {
    let (_temp, repo, directory) = owner_directory_fixture("11111111").await;
    repo.ensure_human_actor("22222222", "Bob").await.expect("bob actor");
    transfer_to(&repo, "22222222").await;
    // New owner:
    directory
        .update_actor_status_for_caller(status_command("human_22222222", "status-bot", true))
        .await
        .expect("current owner mutates status");
    // Plus a granted manager (service source of truth):
    repo.ensure_human_actor("44444444", "Carol").await.expect("carol actor");
    bcs_test_support::authority_hook_for(repo.clone())
        .edit_manager(ManagerEdit {
            bot_id: "status-bot".to_string(),
            user_id: "44444444".to_string(),
            action: ManagerEditAction::Grant,
            mutation: AuthorityMutation {
                mutation_id: "grant-carol".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "22222222".to_string(),
                },
            },
        })
        .await
        .expect("grant manager");
    directory
        .update_actor_status_for_caller(status_command("human_44444444", "status-bot", true))
        .await
        .expect("granted manager mutates status");
}

#[tokio::test]
async fn non_human_caller_keeps_the_legacy_creator_contract() {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    repo.register("status-bot".to_string(), BotCapabilities::default())
        .await
        .expect("runtime register");
    let registry: Arc<dyn BotRegistryCoreService> =
        Arc::new(BotCore::with_repo(repo.clone()));
    let relation = Arc::new(bcs_relation::RelationCore::memory());
    relation
        .ensure_owner_edges("legacy-tool", "status-bot", &bcs_config::resolve_env_str())
        .await
        .expect("creator relation edge");
    let directory = directory_with_default_search(
        registry,
        Arc::new(bcs_test_support::NoopFriendCoreService),
        relation,
    );
    directory
        .update_actor_status_for_caller(status_command("legacy-tool", "status-bot", true))
        .await
        .expect("legacy creator-relation caller still mutates");
}

#[tokio::test]
async fn unwired_authority_hook_fails_the_human_leg_closed() {
    let (_temp, _repo, directory) = owner_directory_fixture("11111111").await;
    // Rebuild without the hook (ctor default = unwired).
    let temp_dir = tempfile::tempdir().expect("temp dir 2");
    let repo2 = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    repo2
        .register("status-bot".to_string(), BotCapabilities::default())
        .await
        .expect("register");
    let registry2: Arc<dyn BotRegistryCoreService> =
        Arc::new(BotCore::with_repo(repo2));
    let unwired = directory_with_default_search(
        registry2,
        Arc::new(bcs_test_support::NoopFriendCoreService),
        Arc::new(bcs_test_support::NoopRelationCoreService),
    );
    let error = unwired
        .update_actor_status_for_caller(status_command("human_11111111", "status-bot", true))
        .await
        .expect_err("unwired hook fails the Human leg closed");
    assert!(
        matches!(error, bcs_service_api::ServiceError::Unauthorized(_)),
        "Unauthorized expected, got {error:?}"
    );
}
