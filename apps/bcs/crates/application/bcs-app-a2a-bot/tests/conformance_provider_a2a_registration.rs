use std::sync::{Arc, atomic::{AtomicBool, AtomicUsize, Ordering}};
use async_trait::async_trait;
use bcs_app_a2a_bot::A2aBotManagement;
use bcs_bot::ProviderCore;
use bcs_bot::core::{a2a_bot::A2aBotCore, a2a_provider_registration::A2aProviderRegistrationCore};
use bcs_bot_store::{MemoryProviderStore, a2a::DbA2aBotStore};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_service_api::{ProviderCredential, ProviderCredentialRepoPort, ProviderCoreService,
    ProviderBotConnectionMode, RegisterProviderBotCommand, RegisterProviderBotParams, ServiceError, ServiceResult};
use bcs_service_api::application::a2a_bot::A2aBotManagementService;
use bcs_service_api::port::{a2a_card::{A2aCardPort, ValidatedA2aCard}, repo::a2a_bot::*};
use bcs_service_api::types::a2a_bot::A2aRegistrationCard;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_test_support::contract::bot_registration_chain::{registration_chain, RegistrationChain,
    owner_relation, committed_profile};
use serde_json::json;

struct Fetcher {calls: AtomicUsize, fail: AtomicBool, providers: Arc<dyn ProviderCoreService>, revoke: AtomicBool,
    parallel:AtomicBool, barrier:tokio::sync::Barrier}
#[async_trait]
impl A2aCardPort for Fetcher {
    async fn validate(&self,raw:serde_json::Value,_ready:bool)->ServiceResult<ValidatedA2aCard> {
        let interface=select_a2a_interface(&raw,|_,_|true).unwrap();Ok(ValidatedA2aCard {raw,interface})
    }

    async fn fetch(&self, url: &str, ready: bool) -> ServiceResult<ValidatedA2aCard> {
        assert_eq!(url,"https://example.org/card.json"); assert!(ready);
        self.calls.fetch_add(1,Ordering::SeqCst);
        if self.parallel.load(Ordering::SeqCst) {self.barrier.wait().await;}
        if self.fail.load(Ordering::SeqCst) {return Err(ServiceError::InvalidOperation {message:"Card rejected".into(),request_id:None});}
        if self.revoke.load(Ordering::SeqCst) {
            self.providers.set_provider_disabled("provider-a","test-admin","11111111",true).await?;
        }
        Ok(validated())
    }
}
fn validated() -> ValidatedA2aCard {
    ValidatedA2aCard {raw:json!({"name":"Remote", "description":"Card description","supportedInterfaces":[{"url":"https://example.org/agent","tenant":"remote","protocolVersion":"1.0","protocolBinding":"HTTP+JSON"}], "skills":[{"id":"lookup","description":"完整技能描述","future":42}]}),
        interface:A2aInterface {url:"https://example.org/agent".into(),tenant:Some("remote".into()),protocol_version:"1.0".into(),
            binding:A2aBinding::HttpJson,update_mode:A2aUpdateMode::Stream}}
}
fn command() -> RegisterProviderBotCommand {
    RegisterProviderBotCommand {provider_id:"provider-a".into(),provider_admin_token:"test-admin".into(),
        name:"A2A Provider Bot".into(),provider_bot_ref:"remote".into(),owners:vec!["delegated-owner".into()],
        webhook_url:None,summary:None,domains:vec![],skills:vec![],scopes:vec![],bot_uuid:None,
        reject_existing_bot_uuid:false,connection_mode:ProviderBotConnectionMode::Gateway}
}
async fn fixture() -> (RegistrationChain,A2aBotManagement,Arc<Fetcher>,Arc<A2aProviderRegistrationCore>) {
    let chain=registration_chain().await;
    for sql in bcs_db_api::split_sql_statements(include_str!("../../../../migrations/sqlite/039_bot_a2a.sql")).into_iter() {
        chain.db.execute(DbStatement::new(sql)).await.unwrap();
    }
    chain.db.execute(DbStatement::with_params("UPDATE bcs_providers SET config=? WHERE provider_id='provider-a'",
        vec![json!({"downlink":{"enabled":true,"auth_mode":"static_bearer"}}).to_string().into()])).await.unwrap();
    let credentials=Arc::new(MemoryProviderStore::new());
    for (kind,secret) in [("provider_admin","test-admin"),("downlink_bcs_to_provider","test-downlink")] {
        credentials.insert_credential(ProviderCredential {provider_id:"provider-a".into(),credential_kind:kind.into(),
            secret_value:secret.into(),disabled:false,created_at:0,updated_at:0}).await.unwrap();
    }
    let providers=Arc::new(ProviderCore::new(chain.provider_repo.clone(),credentials,chain.provider_repo.clone(),chain.registry_core.clone()));
    let cards=Arc::new(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()));
    let core=Arc::new(A2aBotCore::new(cards,chain.provider_repo.clone()));
    let registration=Arc::new(A2aProviderRegistrationCore::new(providers.clone(),chain.provider_repo.clone()));
    let fetcher=Arc::new(Fetcher {calls:AtomicUsize::new(0),fail:AtomicBool::new(false),providers:providers.clone(),revoke:AtomicBool::new(false),
        parallel:AtomicBool::new(false),barrier:tokio::sync::Barrier::new(2)});
    (chain,A2aBotManagement::new(providers,core,fetcher.clone()).with_registration(registration.clone()),fetcher,registration)
}
async fn assert_empty(chain:&RegistrationChain) {
    for table in ["bcs_bots","bcs_provider_bot_bindings","edge_grants","permission_profiles","bot_authority_changes"] {
        let rows=chain.db.query(DbStatement::new(format!("SELECT COUNT(*) AS n FROM {table}"))).await.unwrap();
        assert_eq!(rows[0].get_i64("n").unwrap(),Some(0),"{table}");
    }
    let rows=chain.db.query(DbStatement::new("SELECT COUNT(*) AS n FROM bcs_bots WHERE actor_kind='bot'")).await.unwrap();
    assert_eq!(rows[0].get_i64("n").unwrap(),Some(0));
}
async fn assert_delegated_owner(chain:&RegistrationChain,bot:&str) {
    assert_eq!(chain.authority_store.role("human_delegated-owner",bot).await.unwrap(),
        Some(bcs_service_api::types::bot_authority::BotAccessRelation::Owner));
    assert!(owner_relation(chain,bot).await.is_none(),"Provider Human owner is not the delegated Bot owner");
}

#[tokio::test]
async fn card_and_owner_commit_without_any_webhook_and_duplicate_does_not_update() {
    let (chain,service,fetcher,_)=fixture().await;
    let result=service.register_provider_bot(command(),"https://example.org/card.json".into(),1000).await.unwrap();
    let cards=DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str());
    let before=cards.get(&result.bot_uuid).await.unwrap().unwrap();
    assert_eq!(before.agent_card["skills"][0]["description"],"完整技能描述");
    assert_eq!(before.agent_card["skills"][0]["future"],42);
    assert_eq!(before.revision,1);assert!(result.webhook_url.is_none());assert!(result.bot_runtime_token.is_some());
    assert_delegated_owner(&chain,&result.bot_uuid).await;
    assert!(committed_profile(&chain,&result.bot_uuid).await.is_some());
    let audit=chain.db.query(DbStatement::new("SELECT actor_kind,actor_id FROM bot_authority_changes")).await.unwrap();
    assert_eq!(audit[0].get_string("actor_kind").unwrap().as_deref(),Some("provider"));
    assert_eq!(audit[0].get_string("actor_id").unwrap().as_deref(),Some("provider_provider-a"));
    assert!(matches!(service.register_provider_bot(command(),"https://example.org/card.json".into(),2000).await,Err(ServiceError::Conflict(_))));
    assert_eq!(cards.get(&result.bot_uuid).await.unwrap().unwrap(),before);
    assert_eq!(fetcher.calls.load(Ordering::SeqCst),2);
}

#[tokio::test]
async fn invalid_auth_owner_or_mixed_delivery_never_fetches_or_creates() {
    let (chain,service,fetcher,_)=fixture().await;
    let mut bad=command();bad.provider_admin_token="wrong".into();
    assert!(service.register_provider_bot(bad,"https://example.org/card.json".into(),1000).await.is_err());
    for kind in ["plugin","webhook","owner","empty-card"] {
        let mut bad=command();let mut url="https://example.org/card.json".to_owned();
        match kind {"plugin"=>bad.connection_mode=ProviderBotConnectionMode::Plugin,
            "webhook"=>bad.webhook_url=Some("https://example.org/webhook".into()),
            "owner"=>bad.owners.clear(),_=>url.clear()}
        assert!(service.register_provider_bot(bad,url.into(),1000).await.is_err());
    }
    assert_eq!(fetcher.calls.load(Ordering::SeqCst),0);assert_empty(&chain).await;
}

#[tokio::test]
async fn failed_fetch_or_late_provider_revocation_leaves_no_bot() {
    for revoke in [false,true] {
        let (chain,service,fetcher,_)=fixture().await;
        fetcher.fail.store(!revoke,Ordering::SeqCst);fetcher.revoke.store(revoke,Ordering::SeqCst);
        assert!(service.register_provider_bot(command(),"https://example.org/card.json".into(),1000).await.is_err());
        assert_eq!(fetcher.calls.load(Ordering::SeqCst),1);assert_empty(&chain).await;
    }
}

#[tokio::test]
async fn failed_card_or_profile_write_rolls_back_entire_registration() {
    for trigger in [
        "CREATE TRIGGER reject_card BEFORE UPDATE OF agent_card ON bcs_bots BEGIN SELECT RAISE(ABORT, 'card write failure'); END",
        "CREATE TRIGGER reject_profile BEFORE INSERT ON permission_profiles BEGIN SELECT RAISE(ABORT, 'profile write failure'); END",
    ] {
        let (chain,service,_,_)=fixture().await;
        chain.db.execute(DbStatement::new(trigger)).await.unwrap();
        assert!(service.register_provider_bot(command(),"https://example.org/card.json".into(),1000).await.is_err());
        assert_empty(&chain).await;
    }
}

#[tokio::test]
async fn provider_a2a_registration_core_conformance() {
    let (chain,_,_,core)=fixture().await;let c=command();let fetched=validated();
    let card=A2aRegistrationCard {agent_card_url:Some("https://example.org/card.json".into()),agent_card:fetched.raw,interface:fetched.interface,
        registered_at_ms:1000,};
    let params=RegisterProviderBotParams {webhook_url:None,bot_name:c.name,summary:c.summary,owners:c.owners,
        provider_bot_ref:c.provider_bot_ref,domains:c.domains,skills:c.skills,scopes:c.scopes,bot_uuid:None,connection_mode:c.connection_mode,initial_caller:None};
    let created=bcs_test_support::contract::a2a_bot::a2a_provider_registration_core_service_contract_tests(
        core.as_ref(),&c.provider_id,&c.provider_admin_token,params,card).await;
    assert_delegated_owner(&chain,&created.binding.bot_uuid).await;
}

#[tokio::test]
async fn concurrent_same_ref_admission_creates_only_one_bot_and_no_replay_credential() {
    let (chain,service,fetcher,_)=fixture().await;
    fetcher.parallel.store(true,Ordering::SeqCst);
    let (first,second)=tokio::join!(
        service.register_provider_bot(command(),"https://example.org/card.json".into(),1000),
        service.register_provider_bot(command(),"https://example.org/card.json".into(),1000));
    assert_eq!(usize::from(first.is_ok())+usize::from(second.is_ok()),1);
    let failed=if first.is_err() {first} else {second};
    assert!(matches!(failed,Err(ServiceError::Conflict(_))));
    let rows=chain.db.query(DbStatement::new("SELECT COUNT(*) AS n FROM bcs_bots WHERE actor_kind='bot'")).await.unwrap();
    assert_eq!(rows[0].get_i64("n").unwrap(),Some(1));
}

#[tokio::test]
async fn tombstoned_identity_is_not_recreated() {
    let (chain,service,_,_)=fixture().await;
    let result=service.register_provider_bot(command(),"https://example.org/card.json".into(),1000).await.unwrap();
    chain.db.execute(DbStatement::with_params("UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid=?",
        vec![result.bot_uuid.clone().into()])).await.unwrap();
    assert!(matches!(service.register_provider_bot(command(),"https://example.org/card.json".into(),2000).await,
        Err(ServiceError::Conflict(_))));
    let rows=chain.db.query(DbStatement::new("SELECT COUNT(*) AS n FROM bcs_bots WHERE actor_kind='bot'")).await.unwrap();
    assert_eq!(rows[0].get_i64("n").unwrap(),Some(1));
}

#[tokio::test]
async fn human_and_provider_atomic_entrypoints_reject_the_wrong_principal() {
    use bcs_service_api::types::bot_authority::{OwnershipInitialization,AuthorityMutation,AuthorityActor};
    use bcs_service_api::bot_provider::BotProviderRecord;
    let (chain,_,_,_)=fixture().await;
    let bot=BotProviderRecord {bot_uuid:"test-bot".into(),provider_id:"provider-a".into(),provider_bot_ref:"remote".into(),
        connection_mode:ProviderBotConnectionMode::Gateway,webhook_url:None,is_deleted:false};
    let v=validated();
    let card=A2aBotRecord {bot_uuid:bot.bot_uuid.clone(),provider_id:bot.provider_id.clone(),provider_bot_ref:bot.provider_bot_ref.clone(),
        agent_card_url:Some("https://example.org/card.json".into()),agent_card:v.raw,revision:1,};
    let initialization=|actor| OwnershipInitialization {owner_user_id:"delegated-owner".into(),
        mutation:AuthorityMutation {mutation_id:"test-principal-boundary".into(),actor}};
    let provider=initialization(AuthorityActor::Provider {provider_id:"provider-a".into()});
    assert!(chain.provider_repo.create_a2a_provider_bot_with_ownership(bot.clone(),Default::default(),
        "delegated-owner","test-runtime",provider,card.clone()).await.is_err());
    for actor in [AuthorityActor::Human {user_id:"delegated-owner".into()},
        AuthorityActor::Provider {provider_id:"foreign-provider".into()}] {
        assert!(chain.provider_repo.create_provider_admin_a2a_bot_with_ownership(bot.clone(),Default::default(),
            "delegated-owner","test-runtime",initialization(actor),card.clone()).await.is_err());
    }
    assert_empty(&chain).await;
}

#[tokio::test]
async fn inline_registration_defaults_name_and_summary_and_preserves_full_card() {
    let (chain,service,fetcher,_)=fixture().await;let mut command=command();command.name.clear();command.summary=None;
    let raw=validated().raw;
    let created=service.register_provider_bot(command,A2aCardSource::Inline(raw.clone()),1000).await.unwrap();
    let capabilities=created.capabilities.unwrap();assert_eq!(capabilities.name.as_deref(),Some("Remote"));assert_eq!(capabilities.summary.as_deref(),Some("Card description"));
    assert_eq!(fetcher.calls.load(Ordering::SeqCst),0);
    let store=DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str());
    let saved=store.get(&created.bot_uuid).await.unwrap().unwrap();assert_eq!(saved.agent_card,raw);assert!(saved.agent_card_url.is_none());
}

#[tokio::test]
async fn card_url_registration_can_also_default_name_and_preserves_explicit_alias() {
    for alias in [None,Some("Local alias")] {
        let (chain,service,fetcher,_)=fixture().await;let mut command=command();command.name=alias.unwrap_or("").into();
        let created=service.register_provider_bot(command,"https://example.org/card.json".into(),1000).await.unwrap();
        assert_eq!(created.capabilities.unwrap().name.as_deref(),Some(alias.unwrap_or("Remote")));
        assert_eq!(fetcher.calls.load(Ordering::SeqCst),1);
        let saved=DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()).get(&created.bot_uuid).await.unwrap().unwrap();
        assert_eq!(saved.agent_card["name"],"Remote");
    }
}
