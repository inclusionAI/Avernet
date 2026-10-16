use std::sync::Arc;
use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, provider::{MemoryBotProviderStore, MemoryProviderStore}};
use bcs_service_api::{BotCapabilities, CoordinationMode, ProviderAuthMode, ProviderCoreService, ProviderRepoPort};
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;

#[tokio::test]
async fn plugin_ownership_is_authoritative_and_never_falls_back_when_invalid() {
    for invalid in ["disabled", "deleted", "malformed", "missing"] {
        let providers = Arc::new(MemoryProviderStore::new());
        let dir = tempfile::tempdir().unwrap();
        let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf()));
        let metadata = Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
        let registry = Arc::new(BotCore::with_provider_repos(bots, providers.clone(), providers.clone(), providers.clone())
            .with_bot_provider_repo(metadata.clone()));
        let core = ProviderCore::new(providers.clone(), providers.clone(), providers.clone(), registry.clone());
        let provider = core.register_provider("QwenWork".into(), None, ProviderAuthMode::StaticBearer,
            "owner".into(), None, Some(serde_json::from_value(serde_json::json!({
                "mode":"native_mcp", "mcp_server":"bcs-mcp",
                "tool_name_mapping":{"mcp__bcs-mcp__bcs_assign_task":"bcs_assign_task"}
            })).unwrap())).await.unwrap().provider;
        metadata.create_provider_bot(BotProviderRecord {
            bot_uuid:"plugin-bot".into(), provider_id:provider.provider_id.clone(),
            provider_bot_ref:"desktop".into(), connection_mode:BotConnectionMode::Plugin,
            webhook_url:None, is_deleted:false,
        }, BotCapabilities::default(), "owner", "test-runtime-token").await.unwrap();
        let surface = registry.provider_coordination_surface("plugin-bot").await.unwrap().unwrap();
        assert_eq!(surface.mode, CoordinationMode::NativeMcp);
        assert_eq!(surface.tool_name_mapping["mcp__bcs-mcp__bcs_assign_task"], "bcs_assign_task");
        match invalid {
            "disabled" => { providers.update_provider_disabled(&provider.provider_id, true, 1).await.unwrap(); }
            "deleted" => { metadata.delete_provider_bot(&provider.provider_id, "plugin-bot", 1).await.unwrap(); }
            "malformed" => { providers.update_provider_metadata(&provider.provider_id, None,
                Some(r#"{"coordination":{"mode":"invalid"}}"#), None, 1).await.unwrap(); }
            "missing" => {
                // A store with the same metadata but no Provider must fail closed.
                let absent = Arc::new(MemoryProviderStore::new());
                let other = BotCore::with_provider_repos(Arc::new(MemoryBotRepo::new()),
                    absent.clone(), absent.clone(), absent).with_bot_provider_repo(metadata);
                assert!(other.provider_coordination_surface("plugin-bot").await.is_err());
                continue;
            }
            _ => unreachable!(),
        }
        assert!(registry.provider_coordination_surface("plugin-bot").await.is_err(), "{invalid}");
    }
}
