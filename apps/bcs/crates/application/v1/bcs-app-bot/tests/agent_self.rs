use std::sync::Arc;
use async_trait::async_trait;
use bcs_app_bot::BotSelfServiceImpl;
use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::application::v1::BotSelfService;
use bcs_service_api::port::agent_identity::{AgentIdentityError, AgentIdentityPort, VerifiedAgentIdentity};

struct Identity;
#[async_trait]
impl AgentIdentityPort for Identity {
    async fn verify(&self, token: &str) -> Result<VerifiedAgentIdentity, AgentIdentityError> {
        if token != "parent.test.jwt" { return Err(AgentIdentityError::InvalidToken); }
        Ok(VerifiedAgentIdentity { agent_code: "parent".into() })
    }
}

#[tokio::test]
async fn agent_me_accepts_zero_or_multiple_bots_and_paginates_without_tokens() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    for sql in [
        "CREATE TABLE bcs_bots (bot_uuid TEXT, env TEXT, agent_code TEXT, name TEXT, bot_info TEXT, session_token TEXT, created_by TEXT, status TEXT DEFAULT 'online', provider_id TEXT, provider_bot_ref TEXT, actor_kind TEXT DEFAULT 'bot', is_deleted INTEGER DEFAULT 0)",
        "CREATE TABLE bcs_provider_bot_bindings (bot_uuid TEXT,env TEXT,provider_id TEXT,provider_bot_ref TEXT,disabled INTEGER DEFAULT 0)",
        "CREATE TABLE bcs_providers (provider_id TEXT,env TEXT,config TEXT,disabled INTEGER DEFAULT 0)",
    ] { db.execute(DbStatement::new(sql)).await.unwrap(); }
    let core = Arc::new(BotCore::with_repo(Arc::new(PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite))));
    let service = BotSelfServiceImpl::new(Arc::new(Identity), core.clone());
    let empty = service.get_agent_me("parent.test.jwt", None, None, 1).await.unwrap();
    assert_eq!(empty.agent_code, "parent"); assert!(empty.bots.is_empty());
    for id in ["bot_A", "bot_B"] {
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,env,agent_code,name,bot_info,status) VALUES (?,?,?,?,?,?)", vec![id.into(), bcs_config::resolve_env_str().into(), "parent".into(), id.into(), r#"{"agent_token":"must-not-escape"}"#.into(), "hidden".into()])).await.unwrap();
    }
    let first = service.get_agent_me("parent.test.jwt", None, None, 1).await.unwrap();
    assert_eq!(first.bots[0].bot.bot_id, "bot_A"); assert_eq!(first.next_cursor.as_deref(), Some("bot_A"));
    assert!(!format!("{first:?}").contains("must-not-escape"));
    let second = service.get_agent_me("parent.test.jwt", None, first.next_cursor.as_deref(), 1).await.unwrap();
    assert_eq!(second.bots[0].bot.bot_id, "bot_B"); assert!(second.next_cursor.is_none());
    assert_eq!(service.get_me_with_actor("parent.test.jwt", Some("bot_B")).await.unwrap().bot.unwrap().bot_id, "bot_B");
    db.execute(DbStatement::new("INSERT INTO bcs_providers (provider_id,env,config) SELECT 'p',env,'{\"downlink\":{\"auth_mode\":\"agentpass-subbots\"}}' FROM bcs_bots LIMIT 1")).await.unwrap();
    db.execute(DbStatement::new("UPDATE bcs_bots SET provider_id='p',provider_bot_ref='parent:b' WHERE bot_uuid='bot_B'")).await.unwrap();
    db.execute(DbStatement::new("UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid='bot_A'")).await.unwrap();
    assert!(service.get_me("parent.test.jwt").await.is_err());
    db.execute(DbStatement::new("UPDATE bcs_bots SET session_token='static-b' WHERE bot_uuid='bot_B'")).await.unwrap();
    assert_eq!(service.get_me("static-b").await.unwrap().bot.unwrap().bot_id,"bot_B");
    assert!(service.get_me_with_actor("static-b",Some("bot_A")).await.is_err());
    assert!(service.get_agent_me("static-token", None, None, 1).await.is_err());
    use bcs_service_api::core::BotRegistryCoreService;
    core.validate_signed_bot("bot_B").await.unwrap();
    db.execute(DbStatement::new("UPDATE bcs_providers SET disabled=1 WHERE provider_id='p'")).await.unwrap();
    assert!(core.validate_signed_bot("bot_B").await.is_err());
    core.validate_signed_bot("unregistered-tc").await.unwrap();
}
