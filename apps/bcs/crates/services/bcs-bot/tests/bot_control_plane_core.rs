#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::HashSet;
use std::sync::Arc;

use bcs_bot::BotControlPlaneCore;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::{
    ActorKind, BotCandidateReadQuery, BotCandidateVisibility,
    BotCapabilities, BotControlPlaneCoreService, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotRepoPort, BotSearchCandidateQuery, ProviderBotBinding,
    ProviderBotBindingRepoPort, ProviderRecord, ProviderRepoPort, ServiceError, ServiceResult,
};

struct Fixture {
    core: BotControlPlaneCore,
    repo: Arc<MemoryBotRepo>,
    providers: Arc<MemoryProviderStore>,
    env: String,
    _temp: tempfile::TempDir,
}

impl Fixture {
    fn new() -> Self {
        let temp = tempfile::tempdir().expect("temp dir");
        let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
        let providers = Arc::new(MemoryProviderStore::new());
        let core = BotControlPlaneCore::new(repo.clone(), providers.clone(), providers.clone());
        Self {
            core,
            repo,
            providers,
            env: bcs_config::resolve_env_str(),
            _temp: temp,
        }
    }

    async fn add_bot(&self, bot_id: &str, owner: &str, visibility: &str) {
        self.repo
            .register_with_owner_and_token(
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
            .expect("register bot");
    }

    async fn bind_provider(&self, bot_id: &str) {
        self.providers
            .insert_provider(ProviderRecord {
                provider_id: "provider-1".to_string(),
                slug: None,
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
        self.providers
            .insert_binding(ProviderBotBinding {
                webhook_url: None,
                bot_uuid: bot_id.to_string(),
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "provider-ref".to_string(),
                disabled: false,
                created_at: 1,
                updated_at: 1,
            })
            .await
            .expect("insert binding");
    }
}


struct RejectProviderMetadata;

#[async_trait::async_trait]
impl bcs_service_api::port::repo::bot_provider::BotProviderRepoPort for RejectProviderMetadata {
    async fn validate_provider_bot_name(&self, _: &str, _: &str, _: Option<&str>) -> ServiceResult<()> {
        Err(ServiceError::InternalError("provider metadata must not be read".into()))
    }

    async fn get_provider_bot(&self, _: &str) -> ServiceResult<Option<bcs_service_api::bot_provider::BotProviderRecord>> {
        Err(ServiceError::InternalError("provider metadata must not be read".into()))
    }

    async fn get_provider_bot_by_ref(&self, _: &str, _: &str) -> ServiceResult<Option<bcs_service_api::bot_provider::BotProviderRecord>> {
        Err(ServiceError::InternalError("provider metadata must not be read".into()))
    }

    async fn list_provider_bot_metadata(&self, _: Option<&str>) -> ServiceResult<Vec<bcs_service_api::bot_provider::BotProviderRecord>> {
        Err(ServiceError::InternalError("provider metadata must not be read".into()))
    }

    async fn get_connection_mode(&self, _: &str) -> ServiceResult<Option<bcs_service_api::bot_provider::BotConnectionMode>> {
        Err(ServiceError::InternalError("provider metadata must not be read".into()))
    }

    async fn attach_provider_bot(&self, _: bcs_service_api::bot_provider::BotProviderRecord) -> ServiceResult<()> {
        Err(ServiceError::InternalError("not configured".into()))
    }

    async fn create_provider_bot(
        &self,
        _: bcs_service_api::bot_provider::BotProviderRecord,
        _: BotCapabilities,
        _: &str,
        _: &str,
    ) -> ServiceResult<()> {
        Err(ServiceError::InternalError("not configured".into()))
    }

    async fn update_provider_webhook(
        &self,
        _: &str,
        _: &str,
        _: Option<String>,
        _: u64,
    ) -> ServiceResult<bcs_service_api::bot_provider::BotProviderRecord> {
        Err(ServiceError::InternalError("not configured".into()))
    }

    async fn delete_provider_bot(&self, _: &str, _: &str, _: u64) -> ServiceResult<bool> {
        Err(ServiceError::InternalError("not configured".into()))
    }
}

#[tokio::test]
async fn raw_candidate_search_does_not_hydrate_provider_metadata() {
    let fixture = Fixture::new();
    fixture.add_bot("acting", "staff-1", "private").await;
    fixture.add_bot("candidate", "staff-2", "public").await;
    let core = BotControlPlaneCore::new(
        fixture.repo.clone(),
        fixture.providers.clone(),
        fixture.providers.clone(),
    )
    .with_bot_provider_repo(Arc::new(RejectProviderMetadata));

    let (records, total) = core
        .search_candidate_records(BotSearchCandidateQuery {
            acting_bot_id: "acting".to_string(),
            env: fixture.env.clone(),
            visibility: BotCandidateVisibility::Discovery,
            viewer_kind: Some(ActorKind::Bot),
            friend_ids: HashSet::new(),
            bot_uuids: None,
            name: Some("candidate".to_string()),
            q: None,
            visibility_filter: None,
            user_visibility: None,
            status: None,
            friendship: None,
            tc_bot: None,
            name_only: true,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("raw candidate search");

    assert_eq!(total, 1);
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].bot.bot_id, "candidate");
}

#[tokio::test]
async fn batch_views_preserve_request_order_and_hydrate_physical_provider() {
    let fixture = Fixture::new();
    fixture.add_bot("provider-bot", "staff-1", "public").await;
    fixture.add_bot("local-bot", "staff-1", "protected").await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");
    fixture.bind_provider("provider-bot").await;

    let record = fixture
        .core
        .get_record("provider-bot", &fixture.env)
        .await
        .expect("get control-plane record")
        .expect("provider bot exists");
    assert_eq!(record.bot_id, "provider-bot");

    let views = fixture
        .core
        .get_by_ids(
            &[
                "human_staff-1".to_string(),
                "missing".to_string(),
                "provider-bot".to_string(),
                "local-bot".to_string(),
                "human_staff-1".to_string(),
            ],
            &fixture.env,
        )
        .await
        .expect("query control-plane views");

    assert_eq!(
        views
            .iter()
            .map(|view| view.record.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["human_staff-1", "provider-bot", "local-bot"]
    );
    assert!(views[0].provider.is_none());
    assert_eq!(
        views[1]
            .provider
            .as_ref()
            .map(|provider| (provider.provider_id.as_str(), provider.name.as_str())),
        Some(("provider-1", "Provider One"))
    );
    assert!(views[2].provider.is_none());
}

#[tokio::test]
async fn candidate_views_preserve_repo_total_and_friend_flag() {
    let fixture = Fixture::new();
    fixture.add_bot("acting", "staff-1", "private").await;
    fixture
        .add_bot("private-friend", "staff-2", "private")
        .await;
    fixture.add_bot("public-bot", "staff-2", "public").await;
    fixture.bind_provider("public-bot").await;

    let (candidates, total) = fixture
        .core
        .list_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting".to_string(),
            env: fixture.env.clone(),
            visibility: BotCandidateVisibility::Collaboration,
            viewer_kind: ActorKind::Bot,
            friend_ids: HashSet::from(["private-friend".to_string()]),
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("list candidates");

    assert_eq!(total, 2);
    assert!(candidates.iter().any(|candidate| {
        candidate.bot.record.bot_id == "private-friend" && candidate.is_friend
    }));
    assert!(candidates.iter().any(|candidate| {
        candidate.bot.record.bot_id == "public-bot"
            && !candidate.is_friend
            && candidate.bot.provider.is_some()
    }));
}

#[tokio::test]
async fn owned_views_hydrate_provider_metadata() {
    let fixture = Fixture::new();
    fixture.add_bot("owned", "staff-1", "public").await;
    fixture.add_bot("other", "staff-2", "public").await;
    fixture.bind_provider("owned").await;

    let owned = fixture
        .core
        .list_by_creator(BotControlPlaneOwnedQuery {
            created_by: "staff-1".to_string(),
            env: fixture.env.clone(),
            kind: None,
            name: None,
            status: None,
        })
        .await
        .expect("list owned records");

    assert_eq!(owned.len(), 1);
    assert_eq!(owned[0].record.bot_id, "owned");
    assert_eq!(
        owned[0]
            .provider
            .as_ref()
            .map(|provider| provider.name.as_str()),
        Some("Provider One")
    );
}

#[tokio::test]
async fn patch_returns_the_hydrated_updated_view() {
    let fixture = Fixture::new();
    fixture.add_bot("owned", "staff-1", "public").await;
    fixture.bind_provider("owned").await;

    let updated = fixture
        .core
        .patch(
            "owned",
            &fixture.env,
            BotControlPlanePatch {
                name: Some("Renamed".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("patch record")
        .expect("owned record exists");

    assert_eq!(updated.record.name, "Renamed");
    assert_eq!(
        updated
            .provider
            .as_ref()
            .map(|provider| provider.provider_id.as_str()),
        Some("provider-1")
    );
}
