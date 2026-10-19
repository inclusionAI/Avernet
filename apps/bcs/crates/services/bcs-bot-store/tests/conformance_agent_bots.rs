use std::sync::Arc;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement};
use bcs_service_api::{BotRepoPort, types::agent_registration::AgentBotQuery};

#[path = "common/bot_provider.rs"] mod database;

async fn fixture() -> (Arc<bcs_db_local::LocalSqliteDbPlugin>, PersistentBotRepo) {
    let db = database::sqlite().await;
    db.execute(DbStatement::new("ALTER TABLE bcs_providers ADD COLUMN config TEXT DEFAULT '{\"downlink\":{\"auth_mode\":\"agentpass-subbots\"}}'" )).await.unwrap();
    db.execute(DbStatement::new("ALTER TABLE bcs_providers ADD COLUMN disabled INTEGER DEFAULT 0")).await.unwrap();
    let env = bcs_config::resolve_env_str();
    for (id, token) in [("bot_A", "token_A"), ("bot_B", "token_B")] {
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,env,name,agent_code,session_token,created_by,provider_id,provider_bot_ref,status) VALUES (?,?,?,?,?,?,?,?,?)", vec![id.into(), env.clone().into(), id.into(), "parent".into(), token.into(), "owner".into(), "provider-a".into(), format!("parent:{id}").into(), "hidden".into()])).await.unwrap();
    }
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite);
    (db, repo)
}

fn query() -> AgentBotQuery { AgentBotQuery { agent_code: "parent".into(), bot_uuid: None, provider_id: None, cursor: None, limit: 2, implicit_only: false } }

#[tokio::test]
async fn siblings_are_read_with_mode_and_persisted_actor_status() {
    let (_, repo) = fixture().await;
    bcs_test_support::contract::agent_bots::agent_bot_query_contract_tests(&repo,&query(),&["bot_A","bot_B"]).await;
    let bots = repo.query_agent_bots(&query()).await.unwrap();
    assert_eq!(bots.len(), 2);
    assert_eq!(bots[0].bot.bot_id, "bot_A");
    assert_eq!(bots[0].status, "hidden");
    assert_eq!(bots[0].provider_auth_mode, Some(bcs_service_api::ProviderAuthMode::AgentPassSubbots));
    let mut q = query(); q.bot_uuid = Some("bot_B".into());
    assert_eq!(repo.query_agent_bots(&q).await.unwrap()[0].bot.bot_id, "bot_B");
    q.agent_code = "other".into();
    assert!(repo.query_agent_bots(&q).await.unwrap().is_empty());
}

#[tokio::test]
async fn subbots_are_not_implicit_candidates_and_static_token_matches_actor() {
    let (_, repo) = fixture().await;
    let mut q = query(); q.implicit_only = true;
    assert!(repo.query_agent_bots(&q).await.unwrap().is_empty());
    assert!(repo.lookup_bot_token("token_A", Some("bot_B")).await.unwrap().is_none());
    assert_eq!(repo.lookup_bot_token("token_A", None).await.unwrap().unwrap().bot.bot_id, "bot_A");
}

#[tokio::test]
async fn undeleted_list_is_bounded_and_cursor_continues_it() {
    let (db, repo) = fixture().await;
    let mut q = query(); q.limit = 1;
    assert_eq!(repo.query_agent_bots(&q).await.unwrap()[0].bot.bot_id, "bot_A");
    q.cursor = Some("bot_A".into());
    assert_eq!(repo.query_agent_bots(&q).await.unwrap()[0].bot.bot_id, "bot_B");
    db.execute(DbStatement::new("UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid='bot_B'")).await.unwrap();
    assert!(repo.query_agent_bots(&q).await.unwrap().is_empty());
}

#[tokio::test]
async fn fresh_streaming_bot_can_authenticate_before_onboarding() {
    let (_, repo) = fixture().await;
    let token = repo.connect_or_promote_streaming("fresh-bot".into()).await.unwrap();
    for actor in [None, Some("fresh-bot")] {
        let identity = repo.lookup_bot_token(&token, actor).await.unwrap()
            .expect("new WS credentials must work before onboard persists the Bot");
        assert_eq!(identity.bot.bot_id, "fresh-bot");
        assert!(identity.bot.provider_id.is_none());
    }
    assert!(repo.lookup_bot_token(&token, Some("another-bot")).await.unwrap().is_none());
    assert!(repo.lookup_bot_token("unknown-token", None).await.unwrap().is_none());
    repo.disconnect_streaming("fresh-bot").await;
    assert!(repo.lookup_bot_token(&token, None).await.unwrap().is_some(),
        "pending credentials must also allow reconnecting before onboard");
}

#[tokio::test]
async fn bootstrap_token_cannot_override_persisted_credentials_or_deletion() {
    let (db, repo) = fixture().await;
    let token = repo.connect_or_promote_streaming("fresh-bot".into()).await.unwrap();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid,env,name,session_token) VALUES (?,?,?,?)",
        vec!["fresh-bot".into(), bcs_config::resolve_env_str().into(), "Fresh".into(), "rotated-token".into()],
    )).await.unwrap();
    assert!(repo.lookup_bot_token(&token, None).await.unwrap().is_none());
    assert!(repo.lookup_bot_token("rotated-token", None).await.unwrap().is_some());
    db.execute(DbStatement::new("UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid='fresh-bot'")).await.unwrap();
    assert!(repo.lookup_bot_token(&token, None).await.unwrap().is_none());
    assert!(repo.lookup_bot_token("rotated-token", None).await.unwrap().is_none());
}

#[tokio::test]
async fn bootstrap_token_cannot_bypass_a_provider_binding() {
    let (db, repo) = fixture().await;
    let token = repo.connect_or_promote_streaming("fresh-bot".into()).await.unwrap();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_provider_bot_bindings (bot_uuid,env,provider_id,provider_bot_ref,disabled) VALUES (?,?,?,?,1)",
        vec!["fresh-bot".into(), bcs_config::resolve_env_str().into(), "provider-a".into(), "parent:fresh".into()],
    )).await.unwrap();
    assert!(repo.lookup_bot_token(&token, None).await.unwrap().is_none());
}
