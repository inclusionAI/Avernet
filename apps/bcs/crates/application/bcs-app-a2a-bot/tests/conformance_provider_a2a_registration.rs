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
use bcs_service_api::types::bot_profile::AvatarChange;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_test_support::contract::bot_registration_chain::{registration_chain, RegistrationChain,
    owner_relation, committed_profile};
use serde_json::json;

struct Fetcher {calls: AtomicUsize, fail: AtomicBool, providers: Arc<ProviderCore>, revoke: AtomicBool,
    parallel:AtomicBool, barrier:tokio::sync::Barrier}
#[async_trait]
impl A2aCardPort for Fetcher {
    async fn validate(&self,raw:serde_json::Value,_ready:bool)->ServiceResult<ValidatedA2aCard> {
        let interface=select_a2a_interface(&raw,|_,_|true).unwrap();Ok(ValidatedA2aCard {raw,interface})
    }

    async fn fetch(&self, url: &str, ready: bool) -> ServiceResult<ValidatedA2aCard> {
        assert_eq!(url,"https://example.org/card.json"); let _=ready;
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
    ValidatedA2aCard {raw:json!({"name":"Remote", "description":"Card description","version":"1","capabilities":{"streaming":true},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],"supportedInterfaces":[{"url":"https://example.org/agent","tenant":"remote","protocolVersion":"1.0","protocolBinding":"HTTP+JSON"}], "skills":[{"id":"lookup","name":"lookup","tags":[],"description":"完整技能描述","future":42}]}),
        interface:A2aInterface {url:"https://example.org/agent".into(),tenant:Some("remote".into()),protocol_version:"1.0".into(),
            binding:A2aBinding::HttpJson,update_mode:A2aUpdateMode::Stream}}
}
fn command() -> RegisterProviderBotCommand {
    RegisterProviderBotCommand {provider_id:"provider-a".into(),provider_admin_token:"test-admin".into(),
        name:"A2A Provider Bot".into(),provider_bot_ref:"remote".into(),owners:vec!["delegated-owner".into()],
        webhook_url:None,summary:None,domains:vec![],skills:vec![],scopes:vec![],bot_uuid:None,
        reject_existing_bot_uuid:false,connection_mode:ProviderBotConnectionMode::Gateway,
        avatar:AvatarChange::Keep}
}
async fn fixture() -> (RegistrationChain,A2aBotManagement,Arc<Fetcher>,Arc<A2aProviderRegistrationCore>) {
    let mut chain=registration_chain().await;

    chain.db.execute(DbStatement::with_params("UPDATE bcs_providers SET config=? WHERE provider_id='provider-a'",
        vec![json!({"downlink":{"enabled":true,"auth_mode":"static_bearer"}}).to_string().into()])).await.unwrap();
    let credentials=Arc::new(MemoryProviderStore::new());
    for (kind,secret) in [("provider_admin","test-admin"),("downlink_bcs_to_provider","test-downlink")] {
        credentials.insert_credential(ProviderCredential {provider_id:"provider-a".into(),credential_kind:kind.into(),
            secret_value:secret.into(),disabled:false,created_at:0,updated_at:0}).await.unwrap();
    }
    let cards=Arc::new(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()).with_registry(&chain.bot_repo));
    chain.registry_core=Arc::new(bcs_bot::BotCore::with_repo(chain.bot_repo.clone())
        .with_bot_provider_repo(chain.provider_repo.clone()).with_a2a_cards(cards.clone()));
    let providers=Arc::new(ProviderCore::new(chain.provider_repo.clone(),credentials,chain.provider_repo.clone(),chain.registry_core.clone()).with_a2a_anonymous(true));
    let core=Arc::new(A2aBotCore::new(cards,chain.provider_repo.clone()));
    let registration=Arc::new(A2aProviderRegistrationCore::new(providers.clone(),chain.provider_repo.clone()));
    let fetcher=Arc::new(Fetcher {calls:AtomicUsize::new(0),fail:AtomicBool::new(false),providers:providers.clone(),revoke:AtomicBool::new(false),
        parallel:AtomicBool::new(false),barrier:tokio::sync::Barrier::new(2)});
    let service=A2aBotManagement::new(providers,core,fetcher.clone()).with_registration(registration.clone(),chain.registry_core.clone());
    (chain,service,fetcher,registration)
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
    let card=A2aRegistrationCard {
                visibility: Default::default(),agent_card_url:Some("https://example.org/card.json".into()),agent_card:fetched.raw,interface:fetched.interface,
        registered_at_ms:1000,};
    let params=RegisterProviderBotParams {webhook_url:None,bot_name:card.agent_card["name"].as_str().unwrap().into(),summary:c.summary,owners:c.owners,
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
    let initialization=|actor| OwnershipInitialization {initial_manager_user_ids: Vec::new(),
                owner_user_id:"delegated-owner".into(),
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
async fn card_url_registration_uses_card_name_even_when_legacy_alias_is_supplied() {
    for alias in [None,Some("Local alias")] {
        let (chain,service,fetcher,_)=fixture().await;let mut command=command();command.name=alias.unwrap_or("").into();
        let created=service.register_provider_bot(command,"https://example.org/card.json".into(),1000).await.unwrap();
        assert_eq!(created.capabilities.unwrap().name.as_deref(),Some("Remote"));
        assert_eq!(fetcher.calls.load(Ordering::SeqCst),1);
        let saved=DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()).get(&created.bot_uuid).await.unwrap().unwrap();
        assert_eq!(saved.agent_card["name"],"Remote");
    }
}

fn card_sources() -> [A2aCardSource; 2] {
    ["https://example.org/card.json".into(), A2aCardSource::Inline(validated().raw)]
}

async fn stored_avatar(chain: &RegistrationChain, bot: &str) -> Option<String> {
    let rows=chain.db.query(DbStatement::with_params("SELECT avatar_url FROM bcs_bots WHERE bot_uuid=?",
        vec![bot.into()])).await.unwrap();
    assert_eq!(rows.len(),1);
    rows[0].get_string("avatar_url").unwrap()
}

#[tokio::test]
async fn registration_applies_avatar_set_for_url_and_inline_cards() {
    for source in card_sources() {
        let (chain,service,_,_)=fixture().await;
        let mut command=command();command.avatar=AvatarChange::Set("https://example.org/avatar.png".into());
        let created=service.register_provider_bot(command,source,1000).await.unwrap();
        assert_eq!(stored_avatar(&chain,&created.bot_uuid).await.as_deref(),Some("https://example.org/avatar.png"));
        assert_delegated_owner(&chain,&created.bot_uuid).await;
    }
}

#[tokio::test]
async fn registration_applies_avatar_clear_for_url_and_inline_cards() {
    for source in card_sources() {
        let (chain,service,_,_)=fixture().await;
        chain.db.execute(DbStatement::new("CREATE TRIGGER seed_avatar AFTER INSERT ON bcs_bots BEGIN UPDATE bcs_bots SET avatar_url='https://example.org/seed.png' WHERE bot_uuid=NEW.bot_uuid AND env=NEW.env; END")).await.unwrap();
        let mut command=command();command.avatar=AvatarChange::Clear;
        let created=service.register_provider_bot(command,source,1000).await.unwrap();
        assert_eq!(stored_avatar(&chain,&created.bot_uuid).await,None);
        assert_delegated_owner(&chain,&created.bot_uuid).await;
    }
}

#[tokio::test]
async fn registration_avatar_keep_never_writes_for_url_and_inline_cards() {
    for source in card_sources() {
        let (chain,service,_,_)=fixture().await;
        chain.db.execute(DbStatement::new("CREATE TRIGGER seed_avatar AFTER INSERT ON bcs_bots BEGIN UPDATE bcs_bots SET avatar_url='https://example.org/seed.png' WHERE bot_uuid=NEW.bot_uuid AND env=NEW.env; END")).await.unwrap();
        // Seeding may write, but the application must not issue any avatar update.
        chain.db.execute(DbStatement::new("CREATE TRIGGER reject_avatar_write BEFORE UPDATE OF avatar_url ON bcs_bots WHEN OLD.avatar_url IS NOT NULL BEGIN SELECT RAISE(ABORT, 'unexpected avatar write'); END")).await.unwrap();
        let created=service.register_provider_bot(command(),source,1000).await.unwrap();
        assert_eq!(stored_avatar(&chain,&created.bot_uuid).await.as_deref(),Some("https://example.org/seed.png"));
    }
}

#[tokio::test]
async fn registration_avatar_write_failure_propagates_after_card_and_owner_commit() {
    for source in card_sources() {
        let (chain,service,_,_)=fixture().await;
        chain.db.execute(DbStatement::new("CREATE TRIGGER reject_avatar_write BEFORE UPDATE OF avatar_url ON bcs_bots BEGIN SELECT RAISE(ABORT, 'avatar write failure'); END")).await.unwrap();
        let mut command=command();command.bot_uuid=Some("avatar-failure".into());
        command.avatar=AvatarChange::Set("https://example.org/avatar.png".into());
        assert!(service.register_provider_bot(command,source,1000).await.is_err(),"avatar failures must not report success");
        assert_eq!(stored_avatar(&chain,"avatar-failure").await,None);
        assert_delegated_owner(&chain,"avatar-failure").await;
        assert!(committed_profile(&chain,"avatar-failure").await.is_some());
        assert!(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()).get("avatar-failure").await.unwrap().is_some());
    }
}

#[tokio::test]
async fn registration_missing_avatar_target_returns_bot_not_found_without_resurrection() {
    for source in card_sources() {
        let (chain,service,_,_)=fixture().await;
        chain.db.execute(DbStatement::new("CREATE TRIGGER tombstone_after_card AFTER UPDATE OF agent_card ON bcs_bots BEGIN UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid=NEW.bot_uuid AND env=NEW.env; END")).await.unwrap();
        let mut command=command();command.bot_uuid=Some("avatar-missing".into());command.avatar=AvatarChange::Clear;
        assert!(matches!(service.register_provider_bot(command,source,1000).await,
            Err(ServiceError::BotNotFound(bot)) if bot=="avatar-missing"));
        let rows=chain.db.query(DbStatement::new("SELECT is_deleted FROM bcs_bots WHERE bot_uuid='avatar-missing'")).await.unwrap();
        assert_eq!(rows.len(),1);assert_eq!(rows[0].get_i64("is_deleted").unwrap(),Some(1));
        assert_eq!(stored_avatar(&chain,"avatar-missing").await,None);
        // Authority reads hide tombstoned Bots; inspect retained transaction rows.
        for sql in ["SELECT COUNT(*) AS n FROM edge_grants WHERE to_id='avatar-missing' AND from_id='human_delegated-owner' AND grant_kind='owner'",
            "SELECT COUNT(*) AS n FROM permission_profiles WHERE bot_id='avatar-missing'"] {
            let rows=chain.db.query(DbStatement::new(sql)).await.unwrap();
            assert_eq!(rows[0].get_i64("n").unwrap(),Some(1));
        }
    }
}

#[tokio::test]
async fn named_registration_returns_stable_id_for_public_card_management() {
    use bcs_service_api::application::a2a_bot::RegisterNamedA2aBotCommand;
    let (chain,service,_,_)=fixture().await;
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
    let mut raw=validated().raw;raw["name"]=json!("订单助手");
    let result=service.register_named(RegisterNamedA2aBotCommand {provider_bot_ref:"remote".into(),provider_name:"orders".into(),
        provider_admin_token:"test-admin".into(),owners:vec!["delegated-owner".into()],summary:None,
        source:A2aCardSource::Inline(raw.clone()),now_ms:1000}).await.unwrap();
    assert_eq!(result.card.bot_name,"订单助手");assert_eq!(result.card.provider_name,"orders");
    assert_eq!(result.card.agent_card,raw);assert!(result.bot_runtime_token.is_some());
    assert!(!result.card.bot_id.is_empty());
    let id=result.card.bot_id;
    let fetched=service.get_agent_by_id("test-admin",&id).await.unwrap();
    assert_eq!(fetched.agent_card,raw);
    assert!(service.get_agent_by_id("wrong-token",&id).await.is_err());
    raw["name"]=json!("订单查询助手");
    let updated=service.update_agent_by_id("test-admin",&id,A2aCardSource::Inline(raw.clone())).await.unwrap();
    assert_eq!(updated.bot_id,id);
    assert_eq!(updated.agent_name,"订单查询助手");
    assert_eq!(service.get_agent_by_id("test-admin",&id).await.unwrap().agent_card,raw);
}

#[tokio::test]
async fn new_provider_reference_can_register_an_existing_card_name() {
    let (chain,service,_,_)=fixture().await;
    let first=service.register_provider_bot(command(),"https://example.org/card.json".into(),1000).await.unwrap();
    let mut second=command();second.provider_bot_ref="another-reference".into();
    let second=service.register_provider_bot(second,"https://example.org/card.json".into(),2000).await.unwrap();
    assert_ne!(first.bot_uuid,second.bot_uuid);
    let rows=chain.db.query(DbStatement::new("SELECT bot_uuid FROM bcs_bots WHERE actor_kind='bot'")).await.unwrap();
    assert_eq!(rows.len(),2);
    let rows=chain.db.query(DbStatement::new("SELECT COUNT(*) AS n FROM bot_authority_changes")).await.unwrap();
    assert_eq!(rows[0].get_i64("n").unwrap(),Some(2));
}

#[path = "support/public_management.rs"]
mod public_management;

#[tokio::test]
async fn renamed_card_invalidates_warm_metadata_before_partial_provider_patch() {
    use bcs_service_api::{BotRepoPort, ProviderBotCoreService};
    use bcs_service_api::application::a2a_bot::ConfigureA2aBotCommand;
    let (chain,service,fetcher,_)=fixture().await;
    let created=service.register_provider_bot(command(),A2aCardSource::Inline(validated().raw),1000).await.unwrap();
    let id=created.bot_uuid;
    let caps=chain.bot_repo.try_get(&id).await.unwrap().unwrap().capabilities;
    chain.bot_repo.register(id.clone(),caps).await.unwrap();
    assert_eq!(chain.bot_repo.try_get(&id).await.unwrap().unwrap().capabilities.name.as_deref(),Some("Remote"));
    let mut raw=validated().raw;raw["name"]=json!("Renamed Agent");
    service.configure(ConfigureA2aBotCommand {provider_id:"provider-a".into(),provider_admin_token:"test-admin".into(),
        bot_uuid:id.clone(),source:A2aCardSource::Inline(raw),expected_revision:None,now_ms:2000}).await.unwrap();
    let result=fetcher.providers.update_provider_bot("provider-a","test-admin","remote",None,
        Some("New summary".into()),None,None,None,Some("public".into())).await.unwrap();
    assert_eq!(result.capabilities.name.as_deref(),Some("Renamed Agent"));
    assert_eq!(result.capabilities.summary.as_deref(),Some("New summary"));
    assert_eq!(chain.bot_repo.try_get_many(&[id.clone()]).await.unwrap()[0].capabilities.name.as_deref(),Some("Renamed Agent"));
    assert_eq!(chain.bot_repo.get_including_deleted(&id).await.unwrap().capabilities.name.as_deref(),Some("Renamed Agent"));
    assert!(fetcher.providers.update_provider_bot("provider-a","test-admin","remote",Some("Unauthorized rename".into()),
        None,None,None,None,None).await.is_err());
}

#[tokio::test]
async fn provider_a2a_initial_managers_share_atomic_registration() {
    use bcs_service_api::types::bot_authority::BotAccessRelation;
    let (chain,service,_,_)=fixture().await;
    let mut c=command();c.owners=vec![" delegated-owner ".into(),"bob".into(),"bob".into(),"delegated-owner".into()];
    let result=service.register_provider_bot(c,"https://example.org/card.json".into(),1000).await.unwrap();
    assert_delegated_owner(&chain,&result.bot_uuid).await;
    assert_eq!(chain.authority_store.role("human_bob",&result.bot_uuid).await.unwrap(),Some(BotAccessRelation::Manager));
}

#[tokio::test]
async fn provider_a2a_manager_failure_rolls_back_bot_card_binding_owner_and_humans() {
    let (chain,service,_,_)=fixture().await;
    chain.db.execute(DbStatement::new("CREATE TRIGGER reject_manager BEFORE INSERT ON edge_grants WHEN NEW.grant_kind='manager' BEGIN SELECT RAISE(ABORT, 'manager failure'); END")).await.unwrap();
    let mut c=command();c.owners.push("bob".into());
    assert!(service.register_provider_bot(c,"https://example.org/card.json".into(),1000).await.is_err());
    assert_empty(&chain).await;
}
