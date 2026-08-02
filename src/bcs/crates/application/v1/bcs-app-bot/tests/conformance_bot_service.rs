#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use bcs_app_bot::{BotServiceConfig, BotServiceImpl};
use bcs_bot::BotControlPlaneCore;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::BotControlPlaneCoreService;
use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};

#[tokio::test]
async fn bot_service_impl_passes_the_v1_bot_service_contract() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo,
        providers.clone(),
        providers,
    ));
    let service = BotServiceImpl::new(
        control_plane,
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );

    bcs_test_support::contract::application::bot_service_contract_tests(&service).await;
}
