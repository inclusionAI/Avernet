//! Validate failure side effects across complete Card admission paths, not one malformed fixture.
use super::*;
use bcs_service_api::application::a2a_bot::{RegisterNamedA2aBotCommand as NamedCommand, UpdateAgentCommand};

struct ProjectionFetcher { raw:serde_json::Value, calls:AtomicUsize }
#[async_trait]
impl A2aCardPort for ProjectionFetcher {
    async fn validate(&self,raw:serde_json::Value,_:bool)->ServiceResult<ValidatedA2aCard> {
        let interface=select_a2a_interface(&raw,|_,_|true).unwrap();
        Ok(ValidatedA2aCard {raw,interface})
    }
    async fn fetch(&self,_:&str,_:bool)->ServiceResult<ValidatedA2aCard> {
        self.calls.fetch_add(1,Ordering::SeqCst);
        self.validate(self.raw.clone(),false).await
    }
}
async fn provider_config(chain:&RegistrationChain)->Option<String> {
    chain.db.query(DbStatement::new("SELECT config FROM bcs_providers WHERE provider_id='provider-a'")).await.unwrap()[0].get_string("config").unwrap()
}
async fn rename_provider(providers:&ProviderCore) {
    let renamed=providers.update_provider_with_a2a("provider-a","test-admin","11111111",
        None,None,None,None,None,Some("renamed-orders".into()),None,None,None,None).await.unwrap();
    assert_eq!(renamed.slug.as_deref(),Some("renamed-orders"));
}

#[tokio::test]
async fn projection_rejections_never_pin_or_mutate_across_card_entrypoints() {
    for entry in ["register","named","update"] {for url in [false,true] {for field in ["domains","scopes"] {for invalid in [json!([""]),json!([1]),json!(null),json!(false),json!({})] {
        let (chain,service,fetcher,registration)=fixture().await;
        chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
        let original=if entry=="update" {
            Some(service.register_provider_bot(command(),A2aCardSource::Inline(card("Before",None)),1000).await.unwrap())
        }else {None};
        let store=Arc::new(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()));
        let before=match &original {Some(outcome)=>store.get(&outcome.bot_uuid).await.unwrap(),None=>None};
        let config=provider_config(&chain).await;
        let mut raw=card("After",None);raw[field]=invalid;
        a2a_management::validate_management_card(&raw).unwrap();
        let port=Arc::new(ProjectionFetcher {raw:raw.clone(),calls:AtomicUsize::new(0)});
        let service=A2aBotManagement::new(fetcher.providers.clone(),Arc::new(A2aBotCore::new(store.clone(),chain.provider_repo.clone())),port.clone())
            .with_registration(registration,chain.registry_core.clone());
        let source=if url {A2aCardSource::Url("https://example.org/card.json".into())}else {A2aCardSource::Inline(raw)};
        let result=match entry {
            "register"=>{let mut input=request(card("After",None));input.source=source;service.register_agent(input).await.map(|_|())},
            "named"=>service.register_named(NamedCommand {catalog_labels:Default::default(),provider_name:"orders".into(),provider_admin_token:"test-admin".into(),
                provider_bot_ref:"remote".into(),owners:vec!["delegated-owner".into()],summary:None,source,now_ms:1000}).await.map(|_|()),
            "update"=>service.update_agent(UpdateAgentCommand {provider_admin_token:"test-admin".into(),bot_id:original.as_ref().unwrap().bot_uuid.clone(),
                source:Some(source),visibility:None,catalog_labels:None}).await.map(|_|()),
            _=>unreachable!(),
        };
        assert!(result.is_err(),"{entry}/{url}/{field}");
        assert_eq!(provider_config(&chain).await,config,"{entry}/{url}/{field}");
        assert_eq!(port.calls.load(Ordering::SeqCst),usize::from(url));
        if let Some(original)=original {assert_eq!(store.get(&original.bot_uuid).await.unwrap(),before);}else {assert_empty(&chain).await;}
        rename_provider(&fetcher.providers).await;
    }}}}
}

#[tokio::test]
async fn rejected_registration_and_read_requests_leave_slug_mutable() {
    for rejection in ["owner","reference","time","get","find","empty-find","refresh"] {
        let (chain,service,fetcher,_)=fixture().await;
        chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
        let before=provider_config(&chain).await;
        match rejection {
            "get"=>assert!(service.get_agent_by_id("test-admin","missing").await.is_err()),
            "find"=>assert!(service.find_agents_by_provider_ref("test-admin"," invalid ").await.is_err()),
            "empty-find"=>assert!(service.find_agents_by_provider_ref("test-admin","missing").await.unwrap().is_empty()),
            "refresh"=>assert!(service.refresh_agent_by_id("test-admin","missing").await.is_err()),
            _=>{
                let mut input=request(card("Invalid",None));
                match rejection {"owner"=>input.owners=Some(vec!["".into()]),"reference"=>input.provider_bot_ref=" invalid ".into(),"time"=>input.now_ms=-1,_=>unreachable!()}
                assert!(service.register_agent(input).await.is_err());
            },
        }
        assert_eq!(provider_config(&chain).await,before,"{rejection}");
        assert_empty(&chain).await;
        rename_provider(&fetcher.providers).await;
    }
}

#[tokio::test]
async fn named_registration_rejections_leave_slug_mutable() {
    for rejection in ["owner","reference","time","fetch"] {
        let (chain,service,fetcher,_)=fixture().await;
        chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
        let before=provider_config(&chain).await;
        let mut command=NamedCommand {catalog_labels:Default::default(),provider_name:"orders".into(),provider_admin_token:"test-admin".into(),
            provider_bot_ref:"remote".into(),owners:vec!["delegated-owner".into()],summary:None,source:A2aCardSource::Inline(card("Named",None)),now_ms:1000};
        match rejection {
            "owner"=>command.owners.clear(),
            "reference"=>command.provider_bot_ref=" invalid ".into(),
            "time"=>command.now_ms=-1,
            "fetch"=>{command.source=A2aCardSource::Url("https://example.org/card.json".into());fetcher.fail.store(true,Ordering::SeqCst);},
            _=>unreachable!(),
        }
        assert!(service.register_named(command).await.is_err(),"{rejection}");
        assert_eq!(provider_config(&chain).await,before,"{rejection}");
        assert_empty(&chain).await;
        rename_provider(&fetcher.providers).await;
    }
}

#[tokio::test]
async fn valid_admission_still_pins_and_url_rename_is_fenced() {
    for named in [false,true] {for rename in [false,true] {
        let (chain,service,fetcher,registration)=fixture().await;
        chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
        let service=if rename {
            let cards=Arc::new(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()));
            A2aBotManagement::new(fetcher.providers.clone(),Arc::new(A2aBotCore::new(cards,chain.provider_repo.clone())),
                Arc::new(RenamingFetcher(fetcher.providers.clone())))
                .with_registration(registration,chain.registry_core.clone())
        }else {service};
        let result=if named {
            service.register_named(NamedCommand {catalog_labels:Default::default(),provider_name:"orders".into(),provider_admin_token:"test-admin".into(),
                provider_bot_ref:"remote".into(),owners:vec!["delegated-owner".into()],summary:None,source:A2aCardSource::Url("https://example.org/card.json".into()),now_ms:1000}).await.map(|_|())
        }else {
            let mut command=request(card("Public",None));command.source=A2aCardSource::Url("https://example.org/card.json".into());
            service.register_agent(command).await.map(|_|())
        };
        if rename {
            assert!(matches!(result,Err(ServiceError::Conflict(_))));
            assert_empty(&chain).await;
        }else {
            assert!(result.is_ok());
            assert!(matches!(fetcher.providers.update_provider_with_a2a("provider-a","test-admin","11111111",
                None,None,None,None,None,Some("renamed-orders".into()),None,None,None,None).await,Err(ServiceError::Conflict(_))));
        }
    }}
}

struct RenamingFetcher(Arc<ProviderCore>);
#[async_trait]
impl A2aCardPort for RenamingFetcher {
    async fn validate(&self,_:serde_json::Value,_:bool)->ServiceResult<ValidatedA2aCard> {panic!("URL fixture")}
    async fn fetch(&self,_:&str,_:bool)->ServiceResult<ValidatedA2aCard> {
        self.0.update_provider_with_a2a("provider-a","test-admin","11111111",None,None,None,None,None,Some("renamed-orders".into()),None,None,None,None).await?;
        Ok(validated())
    }
}
