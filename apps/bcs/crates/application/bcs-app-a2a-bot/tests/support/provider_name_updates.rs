//! Rejected public updates must not reserve a previously mutable Provider name.
use super::*;
use bcs_service_api::ProviderRepoPort;
use bcs_service_api::application::a2a_bot::UpdateAgentCommand;

#[tokio::test]
async fn rejected_label_updates_leave_provider_slug_mutable() {
    for rejection in ["unknown-key", "unknown-value", "multiple-values", "missing-target", "invalid-target", "empty-update", "invalid-card", "unauthorized"] {
        let (chain,service,fetcher,_)=fixture().await;
        chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
        let service=service.with_catalog_label_policy(catalog_labels::policy());
        // Provider-ID registration intentionally does not publish/pin a named target.
        let original=service.register_provider_bot_with_labels(command(),A2aCardSource::Inline(card("Before",None)),1000,
            serde_json::from_value(json!({"stage":["trial"]})).unwrap()).await.unwrap();
        let before=chain.db.query(DbStatement::new("SELECT config FROM bcs_providers WHERE provider_id='provider-a'")).await.unwrap()[0].get_string("config").unwrap();
        let record=DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()).get(&original.bot_uuid).await.unwrap().unwrap();
        let mut update=UpdateAgentCommand {provider_admin_token:"test-admin".into(),bot_id:original.bot_uuid.clone(),
            source:None,visibility:None,catalog_labels:Some(serde_json::from_value(json!({"stage":["running"]})).unwrap())};
        match rejection {
            "unknown-key"=>update.catalog_labels=Some(serde_json::from_value(json!({"unknown":null})).unwrap()),
            "unknown-value"=>update.catalog_labels=Some(serde_json::from_value(json!({"stage":["unknown"]})).unwrap()),
            "multiple-values"=>update.catalog_labels=Some(serde_json::from_value(json!({"stage":["trial","running"]})).unwrap()),
            "missing-target"=>update.bot_id="missing".into(),
            "invalid-target"=>update.bot_id=" invalid ".into(),
            "empty-update"=>update.catalog_labels=None,
            "invalid-card"=>update.source=Some(A2aCardSource::Inline(json!({}))),
            "unauthorized"=>update.provider_admin_token="wrong-token".into(),
            _=>unreachable!(),
        }
        assert!(service.update_agent(update).await.is_err(),"{rejection}");
        assert_eq!(fetcher.calls.load(Ordering::SeqCst),0);
        let after=chain.db.query(DbStatement::new("SELECT config FROM bcs_providers WHERE provider_id='provider-a'")).await.unwrap()[0].get_string("config").unwrap();
        assert_eq!(after,before,"{rejection} must not mutate Provider config");
        assert_eq!(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()).get(&original.bot_uuid).await.unwrap().unwrap(),record);
        let renamed=fetcher.providers.update_provider_with_a2a("provider-a","test-admin","11111111",
            None,None,None,None,None,Some("renamed-orders".into()),None,None,None,None).await.unwrap();
        assert_eq!(renamed.slug.as_deref(),Some("renamed-orders"),"{rejection}");
    }
}

#[tokio::test]
async fn successful_label_update_still_pins_provider_slug() {
    let (chain,service,fetcher,_)=fixture().await;
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
    let service=service.with_catalog_label_policy(catalog_labels::policy());
    let original=service.register_provider_bot_with_labels(command(),A2aCardSource::Inline(card("Before",None)),1000,Default::default()).await.unwrap();
    let updated=service.update_agent(UpdateAgentCommand {provider_admin_token:"test-admin".into(),bot_id:original.bot_uuid.clone(),
        source:None,visibility:None,catalog_labels:Some(serde_json::from_value(json!({"stage":["running"]})).unwrap())}).await.unwrap();
    assert_eq!(updated.provider_name,"orders");
    assert_eq!(updated.catalog_labels.as_map()["stage"],["running"]);
    assert!(matches!(fetcher.providers.update_provider_with_a2a("provider-a","test-admin","11111111",
        None,None,None,None,None,Some("renamed-orders".into()),None,None,None,None).await,Err(ServiceError::Conflict(_))));
}

struct SlugChangingFetcher(Arc<ProviderCore>);
#[async_trait]
impl A2aCardPort for SlugChangingFetcher {
    async fn validate(&self,_:serde_json::Value,_:bool)->ServiceResult<ValidatedA2aCard> {panic!("URL test")}
    async fn fetch(&self,_:&str,_:bool)->ServiceResult<ValidatedA2aCard> {
        self.0.update_provider_with_a2a("provider-a","test-admin","11111111",
            None,None,None,None,None,Some("renamed-orders".into()),None,None,None,None).await?;
        Ok(validated())
    }
}

#[tokio::test]
async fn provider_slug_change_during_fetch_is_fenced_without_agent_write() {
    let (chain,service,fetcher,_)=fixture().await;
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
    let original=service.register_provider_bot_with_labels(command(),A2aCardSource::Inline(card("Before",None)),1000,Default::default()).await.unwrap();
    let cards=Arc::new(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()));
    let before=cards.get(&original.bot_uuid).await.unwrap().unwrap();
    let core=Arc::new(A2aBotCore::new(cards.clone(),chain.provider_repo.clone()));
    let service=A2aBotManagement::new(fetcher.providers.clone(),core,Arc::new(SlugChangingFetcher(fetcher.providers.clone())));
    assert!(matches!(service.update_agent_by_id("test-admin",&original.bot_uuid,
        A2aCardSource::Url("https://example.org/card.json".into())).await,Err(ServiceError::Conflict(_))));
    assert_eq!(cards.get(&original.bot_uuid).await.unwrap().unwrap(),before);
    let provider=chain.provider_repo.get_provider("provider-a").await.unwrap().unwrap();
    assert_eq!(provider.slug.as_deref(),Some("renamed-orders"));
    assert!(serde_json::from_str::<serde_json::Value>(&provider.config).unwrap().get("a2a_name_pinned").is_none());
}
