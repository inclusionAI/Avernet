use std::sync::{Arc, atomic::{AtomicBool, AtomicUsize, Ordering}};
use async_trait::async_trait;
use bcs_app_register::RegisterServiceImpl;
use bcs_bot::core::provider_registration::ProviderRegistrationCore;
use bcs_bot_store::{MemoryProviderStore, a2a::DbA2aBotStore};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{ProviderCredential, ProviderCredentialRepoPort, ServiceError, ServiceResult};
use bcs_service_api::application::v1::{RegisterBot, RegisterService, ProviderRegistrationMode};
use bcs_service_api::port::{a2a_card::{A2aCardPort, ValidatedA2aCard}, repo::a2a_bot::*};
use bcs_test_support::contract::bot_registration_chain::{registration_chain, RegistrationChain, mint_v1_token, mint_v2_token, OWNER, REGISTER_SECRET, owner_relation, committed_profile};
use serde_json::json;

struct Fetcher {calls: AtomicUsize, fail:AtomicBool}
#[async_trait]
impl A2aCardPort for Fetcher {
    async fn validate(&self,raw:serde_json::Value,_ready:bool)->ServiceResult<ValidatedA2aCard> {
        let interface=select_a2a_interface(&raw,|_,_|true).unwrap();Ok(ValidatedA2aCard {raw,interface})
    }

    async fn fetch(&self, url:&str, ready:bool) -> ServiceResult<ValidatedA2aCard> {
        assert_eq!(url,"https://example.org/agent-card.json");assert!(ready);
        self.calls.fetch_add(1,Ordering::SeqCst);
        if self.fail.load(Ordering::SeqCst) {return Err(ServiceError::InvalidOperation {message:"test Card rejected".into(),request_id:None});}
        Ok(ValidatedA2aCard {raw:json!({"name":"Remote", "supportedInterfaces":[{"url":"https://example.org/a2a/bots/order","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}], "skills":[{"id":"lookup","description":"完整说明","future":42}]}),
            interface:A2aInterface {url:"https://example.org/a2a/bots/order".into(),tenant:None,protocol_version:"1.0".into(),
                binding:A2aBinding::HttpJson,update_mode:A2aUpdateMode::Stream}})
    }
}

async fn fixture() -> (RegistrationChain, RegisterServiceImpl, Arc<Fetcher>) {
    let chain=registration_chain().await;
    for sql in bcs_db_api::split_sql_statements(include_str!("../../../../../migrations/sqlite/039_bot_a2a.sql")).into_iter() {
        chain.db.execute(DbStatement::new(sql)).await.unwrap();
    }
    chain.db.execute(DbStatement::with_params("UPDATE bcs_providers SET config=? WHERE provider_id='provider-a'",
        vec![json!({"downlink":{"enabled":true,"auth_mode":"static_bearer"}}).to_string().into()])).await.unwrap();
    let credentials=Arc::new(MemoryProviderStore::new());
    credentials.insert_credential(ProviderCredential {provider_id:"provider-a".into(),credential_kind:"downlink_bcs_to_provider".into(),
        secret_value:"test-downlink-credential".into(),disabled:false,created_at:0,updated_at:0}).await.unwrap();
    let core=Arc::new(ProviderRegistrationCore::new(chain.provider_repo.clone(),credentials,
        chain.provider_repo.clone(),chain.provider_repo.clone(),chain.registry_core.clone(),
        Arc::new(bcs_relation::RelationCore::memory()),bcs_config::resolve_env_str(),vec!["provider-a".into()],
        OutboundUrlGuard::allowing_private_networks_for_tests()));
    let fetcher=Arc::new(Fetcher {calls:AtomicUsize::new(0),fail:AtomicBool::new(false)});
    let service=RegisterServiceImpl::new(REGISTER_SECRET.to_vec()).with_registry(chain.registry_core.clone())
        .with_provider_registration(core).with_a2a_cards(fetcher.clone());
    (chain,service,fetcher)
}

fn command(owner:&str) -> RegisterBot {
    RegisterBot {token:mint_v2_token(owner,"provider-a",REGISTER_SECRET),bot_name:"A2A Bot".into(),
        mode:Some(ProviderRegistrationMode::Gateway),provider_bot_ref:Some("order".into()),webhook_url:None}
}
async fn count(chain:&RegistrationChain,table:&str) -> i64 {
    chain.db.query(DbStatement::new(format!("SELECT COUNT(*) AS n FROM {table}"))).await.unwrap()[0].get_i64("n").unwrap().unwrap()
}

#[tokio::test]
async fn registration_commits_card_binding_owner_profile_and_credential_together() {
    let (chain,service,fetcher)=fixture().await;
    let result=bcs_test_support::contract::a2a_bot::a2a_registration_service_contract_tests(&service,command(OWNER),"https://example.org/agent-card.json").await;
    let card=DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()).get(&result.bot_uuid).await.unwrap().unwrap();
    assert_eq!(card.agent_card["skills"][0]["future"],42);
    assert_eq!(card.revision,1);assert_eq!(card.provider_bot_ref,"order");
    assert!(card.agent_card_url.is_some());
    assert!(result.registration.unwrap().effective_webhook_url.is_none());
    assert_eq!(count(&chain,"bcs_provider_bot_bindings").await,1);
    assert!(owner_relation(&chain,&result.bot_uuid).await.is_some());
    assert!(committed_profile(&chain,&result.bot_uuid).await.is_some());
    use bcs_service_api::port::repo::BotRepoPort;
    assert!(chain.bot_repo.try_load_token(&result.bot_uuid).await.unwrap().as_deref()==Some(result.bot_token.as_str()));
    assert_eq!(count(&chain,"bcs_provider_bot_bindings").await,1);
    assert_eq!(fetcher.calls.load(Ordering::SeqCst),2);
}

#[tokio::test]
async fn every_failed_transaction_stage_leaves_no_bot_card_or_authority() {
    for stage in ["UPDATE bcs_bots SET agent_card", "bcs_provider_bot_bindings", "edge_grants", "permission_profiles", "bot_authority_changes"] {
        let (chain,service,fetcher)=fixture().await;
        chain.db.arm(stage);
        assert!(service.register_a2a_bot(command(OWNER),"https://example.org/agent-card.json".into()).await.is_err(),"{stage}");
        for table in ["bcs_bots","bcs_provider_bot_bindings","edge_grants","permission_profiles","bot_authority_changes"] {
            assert_eq!(count(&chain,table).await,0,"{stage}: {table}");
        }
        assert_eq!(fetcher.calls.load(Ordering::SeqCst),1);
    }
}

#[tokio::test]
async fn rejected_card_and_untrusted_binding_do_not_write_or_fetch_early() {
    let (chain,service,fetcher)=fixture().await;
    let mut legacy=command(OWNER);legacy.token=mint_v1_token(OWNER,REGISTER_SECRET);
    assert!(service.register_a2a_bot(legacy,"https://example.org/agent-card.json".into()).await.is_err());
    assert!(service.register_a2a_bot(command("self-service-other"),"https://example.org/agent-card.json".into()).await.is_err());
    let mut plugin=command(OWNER);plugin.mode=Some(ProviderRegistrationMode::Plugin);
    assert!(service.register_a2a_bot(plugin,"https://example.org/agent-card.json".into()).await.is_err());
    assert_eq!(fetcher.calls.load(Ordering::SeqCst),0);
    fetcher.fail.store(true,Ordering::SeqCst);
    assert!(service.register_a2a_bot(command(OWNER),"https://example.org/agent-card.json".into()).await.is_err());
    assert_eq!(count(&chain,"bcs_bots").await,0);
    assert_eq!(count(&chain,"bcs_provider_bot_bindings").await,0);
}

#[tokio::test]
async fn mid_transaction_failures_roll_back_card_binding_and_ownership() {
    for trigger in [
        "CREATE TRIGGER reject_card BEFORE UPDATE OF agent_card ON bcs_bots BEGIN SELECT RAISE(ABORT, 'test Card write failure'); END",
        "CREATE TRIGGER reject_profile BEFORE INSERT ON permission_profiles BEGIN SELECT RAISE(ABORT, 'test profile write failure'); END",
    ] {
        let (chain,service,_)=fixture().await;
        chain.db.execute(DbStatement::new(trigger)).await.unwrap();
        assert!(service.register_a2a_bot(command(OWNER),"https://example.org/agent-card.json".into()).await.is_err());
        for table in ["bcs_bots","bcs_provider_bot_bindings","edge_grants","permission_profiles","bot_authority_changes"] {
            assert_eq!(count(&chain,table).await,0,"{table}");
        }
    }
}

#[tokio::test]
async fn scoped_inline_card_defaults_name_without_fetching_and_keeps_trusted_owner() {
    let (chain,service,fetcher)=fixture().await;
    let mut cmd=command(OWNER);cmd.bot_name.clear();
    let raw=json!({"name":"Inline agent","supportedInterfaces":[{"url":"https://example.org/a2a","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}],"owners":["untrusted"],"skills":[{"description":"完整描述","unknown":42}]});
    let registered=service.register_a2a_card(cmd,A2aCardSource::Inline(raw.clone())).await.unwrap();
    assert_eq!(registered.bot_name,"Inline agent");assert_eq!(fetcher.calls.load(Ordering::SeqCst),0);
    let saved=DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()).get(&registered.bot_uuid).await.unwrap().unwrap();
    assert_eq!(saved.agent_card,raw);assert!(saved.agent_card_url.is_none());
    assert!(owner_relation(&chain,&registered.bot_uuid).await.is_some());
}
