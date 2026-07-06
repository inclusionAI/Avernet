use std::sync::Arc;

use bcs_bot::{ActorDirectory, BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::{
    ActorDirectoryService, ActorListCommand, BotCapabilities, BotRegistryCoreService,
    ProviderAuthMode, ProviderBotBindingRepoPort, ProviderBotCoreService,
    ProviderCoreService, ProviderCredentialRepoPort, ProviderRepoPort,
    RegisterProviderBotParams, Skill,
};
use bcs_test_support::{NoopFriendCoreService, NoopRelationCoreService};

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

    let directory = ActorDirectory::new(
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
