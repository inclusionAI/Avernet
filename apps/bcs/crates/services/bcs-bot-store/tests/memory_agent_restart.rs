use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::{BotCapabilities, BotRepoPort, types::agent_registration::AgentBotQuery};

#[tokio::test]
async fn offline_parent_lookup_survives_restart() {
    let dir = tempfile::tempdir().unwrap();
    let first = MemoryBotRepo::with_base_dir(dir.path().into());
    first.register_with_owner_and_token("bot-a".into(), BotCapabilities { agent_code: Some("parent".into()), ..Default::default() }, "owner", "static").await.unwrap();
    let restarted = MemoryBotRepo::with_base_dir(dir.path().into());
    let bots = restarted.query_agent_bots(&AgentBotQuery { agent_code:"parent".into(),
        bot_uuid:None, provider_id:None, cursor:None, limit:2, implicit_only:false }).await.unwrap();
    assert_eq!(bots.len(), 1);
    assert_eq!(bots[0].bot.bot_id, "bot-a");
}

#[tokio::test]
async fn provider_affiliation_cannot_become_an_implicit_bot_after_restart() {
    use std::sync::Arc;
    use bcs_bot_store::MemoryProviderStore;
    use bcs_bot_store::provider::MemoryBotProviderStore;
    use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
    use bcs_service_api::bot_provider::{BotProviderRecord, BotConnectionMode};
    let dir = tempfile::tempdir().unwrap();
    let first = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    let metadata = MemoryBotProviderStore::new(first, Arc::new(MemoryProviderStore::new()));
    metadata.create_provider_bot(BotProviderRecord {
        bot_uuid:"child".into(), provider_id:"p".into(), provider_bot_ref:"parent:a".into(),
        connection_mode:BotConnectionMode::Plugin, webhook_url:None, is_deleted:false,
    }, BotCapabilities {agent_code:Some("parent".into()),..Default::default()}, "owner","static").await.unwrap();
    let restarted = MemoryBotRepo::with_base_dir(dir.path().into());
    let snapshot = restarted.query_agent_bots(&AgentBotQuery {
        agent_code:"parent".into(),bot_uuid:None,provider_id:None,cursor:None,limit:2,implicit_only:true,
    }).await.unwrap();
    assert_eq!(snapshot[0].bot.provider_id.as_deref(),Some("p"));
    assert!(snapshot[0].provider_auth_mode.is_none());
}
