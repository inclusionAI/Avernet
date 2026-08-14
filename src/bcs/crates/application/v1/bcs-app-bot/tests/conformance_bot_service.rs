#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use async_trait::async_trait;
use bcs_app_bot::{BotServiceConfig, BotServiceImpl};
use bcs_bot::BotControlPlaneCore;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::{
    BotCandidateSearchCoreResult, BotCandidateSearchCoreService, BotCandidateSearchMode,
    BotCandidateSearchQuery, BotControlPlaneCoreService, LegacyBotCandidateSearchCoreResult,
};
use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};

struct EmptyCandidateSearch;

#[async_trait]
impl BotCandidateSearchCoreService for EmptyCandidateSearch {
    async fn search_candidates(
        &self,
        _query: BotCandidateSearchQuery,
    ) -> BotCandidateSearchCoreResult {
        BotCandidateSearchCoreResult {
            hits: Vec::new(),
            mode: BotCandidateSearchMode::EmptyQuery,
        }
    }

    async fn search_candidates_for_legacy(
        &self,
        _query: BotCandidateSearchQuery,
    ) -> LegacyBotCandidateSearchCoreResult {
        panic!("V1 conformance must not call the legacy candidate-search entry point")
    }
}

#[tokio::test]
async fn bot_service_impl_passes_the_v1_bot_service_contract() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> =
        Arc::new(BotControlPlaneCore::new(repo, providers.clone(), providers));
    let service = BotServiceImpl::new(
        control_plane,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        Arc::new(EmptyCandidateSearch),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );

    bcs_test_support::contract::application::bot_service_contract_tests(&service).await;
}
