//! Exercise omission through real application/Core/SQLite writes and deterministic CAS races.
use super::*;
use bcs_service_api::application::a2a_bot::{ConfigureA2aBotCommand,UpdateAgentCommand};
use bcs_service_api::core::a2a_bot::A2aBotCoreService;
use bcs_service_api::types::catalog_labels::{CatalogLabels,CatalogLabelsInput};

fn input(value:serde_json::Value)->CatalogLabelsInput {serde_json::from_value(value).unwrap()}
fn configure(id:&str,labels:Option<CatalogLabelsInput>,expected:Option<u64>)->ConfigureA2aBotCommand {
    ConfigureA2aBotCommand {provider_id:"provider-a".into(),provider_admin_token:"test-admin".into(),
        bot_uuid:id.into(),source:A2aCardSource::Inline(card("LegacyChanged",None)),
        catalog_labels:labels,expected_revision:expected,now_ms:2000}
}

#[tokio::test]
async fn source_and_visibility_omission_preserve_but_explicit_objects_replace() {
    let (chain,service)=public_fixture().await;
    let service=service.with_catalog_label_policy(catalog_labels::policy());
    let mut command=request(card("Before",None));command.catalog_labels=input(json!({"stage":["trial"]}));
    let first=service.register_agent(command).await.unwrap();
    let by_id=service.update_agent_by_id("test-admin",&first.bot_id,A2aCardSource::Inline(card("PublicChanged",None))).await.unwrap();
    assert_eq!(by_id.catalog_labels,first.catalog_labels);
    for expected in [None,Some(3)] {
        let saved=service.configure(configure(&first.bot_id,None,expected)).await.unwrap();
        assert_eq!(saved.catalog_labels,first.catalog_labels);
    }
    let invalid=configure(&first.bot_id,Some(input(json!({"unknown":null}))),None);
    assert!(service.configure(invalid).await.is_err());
    assert_eq!(service.get_agent_by_id("test-admin",&first.bot_id).await.unwrap().catalog_labels,first.catalog_labels);
    for expected in [None,Some(6)] {
        let replaced=service.configure(configure(&first.bot_id,Some(input(json!({"stage":["running"]}))),expected)).await.unwrap();
        assert_eq!(replaced.catalog_labels.as_map()["stage"],["running"]);
        let cleared=service.configure(configure(&first.bot_id,Some(input(json!({}))),None)).await.unwrap();
        assert!(cleared.catalog_labels.is_empty());
    }
    let rows=chain.db.query(DbStatement::with_params("SELECT catalog_labels FROM bcs_bots WHERE bot_uuid=?",vec![first.bot_id.into()])).await.unwrap();
    assert_eq!(rows[0].get_string("catalog_labels").unwrap().as_deref(),Some("{}"));
}

// Competing writes are actual SQLite CAS commits inserted immediately before
// the tested CAS, not timing-dependent sleeps or fabricated conflict errors.
struct RacingCore {
    inner:Arc<A2aBotCore>, remaining:AtomicUsize, attempts:AtomicUsize,
    labels:std::sync::Mutex<Vec<CatalogLabels>>,
}
impl RacingCore {
    async fn race(&self,record:&A2aBotRecord)->ServiceResult<()> {
        self.attempts.fetch_add(1,Ordering::SeqCst);
        self.labels.lock().unwrap().push(record.catalog_labels.clone());
        if self.remaining.fetch_update(Ordering::SeqCst,Ordering::SeqCst,|n|n.checked_sub(1)).is_ok() {
            let current=self.inner.get_card(&record.bot_uuid).await?.unwrap();
            let labels=serde_json::from_value(json!({"stage":["running"]})).unwrap();
            self.inner.commit_card(A2aBotRecord {catalog_labels:labels,revision:current.revision+1,..current.clone()},current.revision).await?;
        }
        Ok(())
    }
}
#[async_trait]
impl A2aBotCoreService for RacingCore {
    async fn provider_binding(&self,p:&str,b:&str)->ServiceResult<bcs_service_api::bot_provider::BotProviderRecord>{self.inner.provider_binding(p,b).await}
    async fn get_card(&self,b:&str)->ServiceResult<Option<A2aBotRecord>>{self.inner.get_card(b).await}
    async fn get_by_name(&self,_:&str,_:&str)->ServiceResult<Option<A2aBotRecord>>{panic!("unused")}
    async fn get_by_provider_ref(&self,_:&str,_:&str)->ServiceResult<Option<A2aBotRecord>>{panic!("unused")}
    async fn cards_for_bots(&self,_:&[String])->ServiceResult<std::collections::BTreeMap<String,serde_json::Value>>{panic!("unused")}
    async fn discovery_cards(&self,_:&[String],_:Option<&str>,_:&[String])->ServiceResult<std::collections::BTreeMap<String,Option<serde_json::Value>>>{panic!("unused")}
    async fn refreshable_cards(&self,_:Option<&str>,_:u32)->ServiceResult<Vec<A2aBotRecord>>{panic!("unused")}
    async fn replace_card(&self,r:A2aBotRecord)->ServiceResult<A2aBotRecord>{self.inner.replace_card(r).await}
    async fn synchronize_card_projection(&self,_:&A2aBotRecord)->ServiceResult<()>{panic!("unused")}
    async fn commit_card(&self,r:A2aBotRecord,e:u64)->ServiceResult<A2aBotRecord>{self.race(&r).await?;self.inner.commit_card(r,e).await}
    async fn commit_card_with_visibility(&self,r:A2aBotRecord,e:u64,v:Option<A2aRegistrationVisibility>)->ServiceResult<A2aBotRecord>{
        self.race(&r).await?;self.inner.commit_card_with_visibility(r,e,v).await
    }
}

#[tokio::test]
async fn public_and_legacy_cas_retry_preserve_latest_labels_without_refetching() {
    for legacy in [false,true] {for explicit in [false,true] {
        let (chain,service,fetcher,_)=fixture().await;
        chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
        let service=service.with_catalog_label_policy(catalog_labels::policy());
        let mut command=request(card("BeforeRace",None));command.catalog_labels=input(json!({"stage":["trial"]}));
        let first=service.register_agent(command).await.unwrap();
        let cards=Arc::new(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()));
        let racing=Arc::new(RacingCore {inner:Arc::new(A2aBotCore::new(cards,chain.provider_repo.clone())),remaining:AtomicUsize::new(1),attempts:AtomicUsize::new(0),labels:Default::default()});
        let service=A2aBotManagement::new(fetcher.providers.clone(),racing.clone(),fetcher.clone()).with_catalog_label_policy(catalog_labels::policy());
        let labels=explicit.then(||input(json!({})));
        let saved=if legacy {
            // Explicit objects retain the existing atomic replacement path;
            // exercise them with a caller revision to force a single fenced write.
            let mut command=configure(&first.bot_id,labels,explicit.then_some(1));
            command.source=A2aCardSource::Url("https://example.org/card.json".into());
            let result=service.configure(command).await;
            if explicit {
                assert!(matches!(result,Err(ServiceError::Conflict(_))));
                assert_eq!(racing.attempts.load(Ordering::SeqCst),1);
                assert_eq!(fetcher.calls.load(Ordering::SeqCst),1);
                continue;
            }
            result.unwrap().catalog_labels
        }else{
            service.update_agent(UpdateAgentCommand {provider_admin_token:"test-admin".into(),bot_id:first.bot_id.clone(),
                source:Some(A2aCardSource::Url("https://example.org/card.json".into())),visibility:None,catalog_labels:labels}).await.unwrap().catalog_labels
        };
        assert_eq!(fetcher.calls.load(Ordering::SeqCst),1,"retry never refetches");
        assert_eq!(racing.attempts.load(Ordering::SeqCst),2);
        if explicit {assert!(saved.is_empty());}else{assert_eq!(saved.as_map()["stage"],["running"]);}
        assert_eq!(service.get_agent_by_id("test-admin",&first.bot_id).await.unwrap().catalog_labels,saved);
        let attempted=racing.labels.lock().unwrap();
        if !explicit {assert_eq!(attempted[0].as_map()["stage"],["trial"]);assert_eq!(attempted[1].as_map()["stage"],["running"]);}
    }}
}

#[tokio::test]
async fn continuous_races_are_bounded_and_expected_revision_never_retries() {
    for expected in [None,Some(1)] {for legacy in [false,true] {
        let (chain,service,fetcher,_)=fixture().await;
        chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
        let first=service.register_agent(request(card("BoundedRace",None))).await.unwrap();
        let cards=Arc::new(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()));
        let racing=Arc::new(RacingCore {inner:Arc::new(A2aBotCore::new(cards,chain.provider_repo.clone())),remaining:AtomicUsize::new(3),attempts:AtomicUsize::new(0),labels:Default::default()});
        let service=A2aBotManagement::new(fetcher.providers.clone(),racing.clone(),fetcher.clone()).with_catalog_label_policy(catalog_labels::policy());
        let result=if legacy {service.configure(configure(&first.bot_id,None,expected)).await.map(|_|())}
            else {service.update_agent_by_id_with_visibility("test-admin",&first.bot_id,None,Some(A2aRegistrationVisibility::Private)).await.map(|_|())};
        assert!(result.is_err());
        assert_eq!(racing.attempts.load(Ordering::SeqCst),if legacy && expected.is_some(){1}else{3});
        assert_eq!(service.get_agent_by_id("test-admin",&first.bot_id).await.unwrap().agent_card,first.agent_card);
    }}
}
