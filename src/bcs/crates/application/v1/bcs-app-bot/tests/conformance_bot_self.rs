use std::sync::{Arc, Mutex};
use async_trait::async_trait;
use bcs_app_bot::BotSelfServiceImpl;
use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::application::v1::{ApplicationError, BotRegistrationStatus, BotSelfService, BotSelfView};
use bcs_service_api::port::agent_identity::{AgentIdentityError, AgentIdentityPort, VerifiedAgentIdentity};
use bcs_test_support::contract::bot_self::bot_self_service_contract_tests;

struct Identity {
    result: Result<VerifiedAgentIdentity, AgentIdentityError>,
    seen: Mutex<Vec<String>>,
}

#[async_trait]
impl AgentIdentityPort for Identity {
    async fn verify(&self, token: &str) -> Result<VerifiedAgentIdentity, AgentIdentityError> {
        self.seen.lock().unwrap().push(token.into());
        self.result.clone()
    }
}

async fn fixture(result: Result<VerifiedAgentIdentity, AgentIdentityError>) -> (Arc<LocalSqliteDbPlugin>, Arc<Identity>, BotSelfServiceImpl) {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    let identity = Arc::new(Identity { result, seen: Mutex::new(Vec::new()) });
    let core = Arc::new(BotCore::with_repo(Arc::new(PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite))));
    let service = BotSelfServiceImpl::new(identity.clone(), core);
    (db, identity, service)
}

fn verified() -> Result<VerifiedAgentIdentity, AgentIdentityError> {
    Ok(VerifiedAgentIdentity { agent_code: "agent-001".into() })
}

async fn schema(db: &dyn DbPlugin) {
    for sql in [
        "CREATE TABLE bcs_bots (bot_uuid TEXT, env TEXT, agent_code TEXT, name TEXT, bot_info TEXT, provider_id TEXT, provider_bot_ref TEXT, actor_kind TEXT DEFAULT 'bot', is_deleted INTEGER DEFAULT 0)",
        "CREATE TABLE bcs_provider_bot_bindings (bot_uuid TEXT,env TEXT,provider_id TEXT,provider_bot_ref TEXT)",
    ] { db.execute(DbStatement::new(sql)).await.unwrap(); }
}

#[tokio::test]
async fn verified_identity_is_required_before_any_registration_read() {
    for (error, expected) in [
        (AgentIdentityError::InvalidToken, "unauthenticated"),
        (AgentIdentityError::NotAgent, "forbidden"),
        (AgentIdentityError::Unavailable, "agent_identity_unavailable"),
    ] {
        // There is intentionally no schema: a DB read would fail differently.
        let (_, identity, service) = fixture(Err(error)).await;
        assert_eq!(service.get_me("credential").await.unwrap_err().code(), expected);
        assert_eq!(*identity.seen.lock().unwrap(), ["credential"]);
    }
}

#[tokio::test]
async fn empty_token_never_reaches_identity_provider() {
    let (_, identity, service) = fixture(verified()).await;
    assert!(matches!(service.get_me("").await, Err(ApplicationError::Unauthenticated)));
    assert!(identity.seen.lock().unwrap().is_empty());
}

#[tokio::test]
async fn authenticated_unregistered_identity_then_persisted_registration_survives_token_refresh() {
    let (db, identity, service) = fixture(verified()).await;
    schema(db.as_ref()).await;
    bot_self_service_contract_tests(&service, "old-token", BotSelfView {
        registration_status: BotRegistrationStatus::Unregistered, agent_code: "agent-001".into(), bot: None,
    }).await;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid,env,agent_code,name,bot_info,provider_id,provider_bot_ref) VALUES ('bot-001',?,'agent-001','Poolab Assistant','{\"summary\":\"Agent\",\"agent_token\":\"secret\"}','provider-poolab','agent-001')",
        vec![bcs_config::resolve_env_str().into()],
    )).await.unwrap();
    let before = service.get_me("old-token").await.unwrap();
    let after = service.get_me("refreshed-token").await.unwrap();
    assert_eq!(before, after);
    assert_eq!(after.registration_status, BotRegistrationStatus::Registered);
    assert_eq!(after.bot.unwrap().bot_id, "bot-001");
    assert_eq!(*identity.seen.lock().unwrap(), ["old-token", "old-token", "refreshed-token"]);
}

#[tokio::test]
async fn database_failure_and_ambiguous_mapping_never_return_unregistered() {
    let (db, _, service) = fixture(verified()).await;
    assert_eq!(service.get_me("token").await.unwrap_err().code(), "internal_error");
    schema(db.as_ref()).await;
    for id in ["bot-a", "bot-b"] {
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,env,agent_code,bot_info) VALUES (?,?,'agent-001','{}')", vec![id.into(),bcs_config::resolve_env_str().into()])).await.unwrap();
    }
    assert_eq!(service.get_me("token").await.unwrap_err().code(), "agent_registration_conflict");
}

#[tokio::test]
async fn broken_identity_provider_cannot_query_blank_agent_code() {
    let (_, _, service) = fixture(Ok(VerifiedAgentIdentity { agent_code: "  ".into() })).await;
    assert_eq!(service.get_me("token").await.unwrap_err().code(), "agent_identity_unavailable");
}
