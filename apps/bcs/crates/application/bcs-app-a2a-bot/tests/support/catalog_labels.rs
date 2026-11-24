//! Full application -> atomic SQLite store regression, not helper-only coverage.
use super::*;
use bcs_service_api::application::a2a_bot::UpdateAgentCommand;
use bcs_service_api::types::catalog_labels::{CatalogLabelPolicy,CatalogLabelRule,CatalogLabelsInput};
pub(super) fn policy() -> Arc<CatalogLabelPolicy> {
    Arc::new(CatalogLabelPolicy::new([("stage".into(),CatalogLabelRule {
        allowed_values:vec!["trial".into(),"running".into(),"offline".into()],allow_multiple:false,
    })].into()).unwrap())
}
fn input(value:serde_json::Value)->CatalogLabelsInput {serde_json::from_value(value).unwrap()}
fn update(id:&str,labels:Option<CatalogLabelsInput>)->UpdateAgentCommand {
    UpdateAgentCommand {provider_admin_token:"test-admin".into(),bot_id:id.into(),source:None,visibility:None,catalog_labels:labels}
}
#[tokio::test]
async fn register_get_find_replace_clear_and_refresh_persist_labels() {
    let (chain,service)=public_fixture().await;
    let service=service.with_catalog_label_policy(policy());
    let mut command=request(card("Labels",None));
    let reference=command.provider_bot_ref.clone();
    command.catalog_labels=input(json!({"stage":["trial","trial"]}));
    command.source=A2aCardSource::Url("https://example.org/card.json".into());
    let registered=service.register_agent(command).await.unwrap();
    assert_eq!(registered.catalog_labels.as_map()["stage"],["trial"]);
    assert!(registered.agent_card.get("catalog_labels").is_none());
    assert_eq!(service.get_agent_by_id("test-admin",&registered.bot_id).await.unwrap().catalog_labels,registered.catalog_labels);
    assert_eq!(service.find_agents_by_provider_ref("test-admin",&reference).await.unwrap()[0].catalog_labels,registered.catalog_labels);
    assert_eq!(service.refresh_agent_by_id("test-admin",&registered.bot_id).await.unwrap().catalog_labels,registered.catalog_labels);
    assert_eq!(service.refresh("provider-a","test-admin",&registered.bot_id,5000).await.unwrap().catalog_labels,registered.catalog_labels);
    service.refresh_due(0).await.unwrap();service.refresh_due(300_000).await.unwrap();
    assert_eq!(service.get_agent_by_id("test-admin",&registered.bot_id).await.unwrap().catalog_labels,registered.catalog_labels);
    let legacy=service.configure_catalog_labels("provider-a","test-admin",&registered.bot_id,input(json!({"stage":["running"]})),None).await.unwrap();
    assert_eq!(legacy.agent_card_url,registered.agent_card_url);
    assert_eq!(legacy.catalog_labels.as_map()["stage"],["running"]);
    assert!(service.configure_catalog_labels("foreign","test-admin",&registered.bot_id,input(json!({})),None).await.is_err());
    assert!(service.configure_catalog_labels("provider-a","test-admin",&registered.bot_id,input(json!({})),Some(0)).await.is_err());
    let replaced=service.update_agent(update(&registered.bot_id,Some(input(json!({"stage":["running"]}))))).await.unwrap();
    assert_eq!(replaced.catalog_labels.as_map()["stage"],["running"]);
    for labels in [json!({}),json!({"stage":null}),json!({"stage":[]})] {
        assert!(service.update_agent(update(&registered.bot_id,Some(input(labels)))).await.unwrap().catalog_labels.is_empty());
        service.update_agent(update(&registered.bot_id,Some(input(json!({"stage":["trial"]}))))).await.unwrap();
    }
    // Legacy visibility-only update preserves labels; label-only absence remains invalid.
    assert!(service.update_agent(update(&registered.bot_id,None)).await.is_err());
    assert_eq!(service.update_agent_by_id_with_visibility("test-admin",&registered.bot_id,None,Some(Default::default())).await.unwrap().catalog_labels.as_map()["stage"],["trial"]);
    let rows=chain.db.query(DbStatement::with_params("SELECT catalog_labels,bot_info FROM bcs_bots WHERE bot_uuid=?",vec![registered.bot_id.clone().into()])).await.unwrap();
    assert_eq!(rows[0].get_string("catalog_labels").unwrap().as_deref(),Some("{\"stage\":[\"trial\"]}"));
    assert!(!rows[0].get_string("bot_info").unwrap().unwrap().contains("catalog_labels"));
    // Model a remote URL whose next declaration changes: refresh owns the Card,
    // not the independently persisted labels, for both manual and scheduled paths.
    for background in [false,true] {
        let mut command=update(&registered.bot_id,Some(input(json!({"stage":["running"]}))));
        command.source=Some(A2aCardSource::Inline(card("BeforeChangedRefresh",None)));
        service.update_agent(command).await.unwrap();
        chain.db.execute(DbStatement::with_params("UPDATE bcs_bots SET agent_card_url='https://example.org/card.json' WHERE bot_uuid=?",vec![registered.bot_id.clone().into()])).await.unwrap();
        if background {service.refresh_due(600_000).await.unwrap();}else{service.refresh_agent_by_id("test-admin",&registered.bot_id).await.unwrap();}
        let refreshed=service.get_agent_by_id("test-admin",&registered.bot_id).await.unwrap();
        assert_eq!(refreshed.agent_name,"Remote");assert_eq!(refreshed.catalog_labels.as_map()["stage"],["running"]);
    }
    chain.db.execute(DbStatement::with_params("UPDATE bcs_bots SET catalog_labels=NULL WHERE bot_uuid=?",vec![registered.bot_id.clone().into()])).await.unwrap();
    assert!(service.get_agent_by_id("test-admin",&registered.bot_id).await.unwrap().catalog_labels.is_empty());
    chain.db.execute(DbStatement::with_params("UPDATE bcs_bots SET catalog_labels='null' WHERE bot_uuid=?",vec![registered.bot_id.into()])).await.unwrap();
    assert!(service.find_agents_by_provider_ref("test-admin",&reference).await.is_err());
}
#[tokio::test]
async fn invalid_mixed_updates_and_storage_failure_are_atomic() {
    let (chain,service)=public_fixture().await;
    let service=service.with_catalog_label_policy(policy());
    let original=service.register_agent(request(card("Original",None))).await.unwrap();
    for labels in [json!({"unknown":null}),json!({"unknown":[]}),json!({"stage":["TRIAL"]}),json!({"stage":["trial","running"]})] {
        let mut command=update(&original.bot_id,Some(input(labels)));
        command.source=Some(A2aCardSource::Inline(card("MustNotSave",None)));
        command.visibility=Some(A2aRegistrationVisibility::Private);
        assert!(service.update_agent(command).await.is_err());
        assert_eq!(service.get_agent_by_id("test-admin",&original.bot_id).await.unwrap().agent_card,original.agent_card);
    }
    chain.db.execute(DbStatement::new("CREATE TRIGGER reject_labels BEFORE UPDATE OF catalog_labels ON bcs_bots BEGIN SELECT RAISE(ABORT,'label write failed'); END")).await.unwrap();
    let mut command=update(&original.bot_id,Some(input(json!({"stage":["running"]}))));
    command.source=Some(A2aCardSource::Inline(card("MustNotSave",None)));
    assert!(service.update_agent(command).await.is_err());
    let saved=service.get_agent_by_id("test-admin",&original.bot_id).await.unwrap();
    assert_eq!(saved.agent_card,original.agent_card);assert!(saved.catalog_labels.is_empty());
}
#[tokio::test]
async fn invalid_labels_precede_network_access_and_registration_mutation() {
    let (chain,service,fetcher,_)=fixture().await;
    let service=service.with_catalog_label_policy(policy());
    let mut command=command();
    command.provider_bot_ref="labels-invalid".into();
    for labels in [json!({"unknown":null}),json!({"stage":["trial","running"]})] {
        assert!(service.register_provider_bot_with_labels(command.clone(),A2aCardSource::Url("https://example.org/card.json".into()),0,input(labels)).await.is_err());
    }
    assert_eq!(fetcher.calls.load(Ordering::SeqCst),0);assert_empty(&chain).await;
}

#[tokio::test]
async fn named_and_provider_admin_entries_store_the_same_labels() {
    let (_,service)=public_fixture().await;let service=service.with_catalog_label_policy(policy());
    let named=service.register_named(bcs_service_api::application::a2a_bot::RegisterNamedA2aBotCommand {
        provider_name:"orders".into(),provider_admin_token:"test-admin".into(),provider_bot_ref:"named-labels".into(),
        owners:vec!["delegated-owner".into()],summary:None,source:A2aCardSource::Inline(card("NamedLabels",None)),now_ms:1000,
        catalog_labels:input(json!({"stage":["running"]})),
    }).await.unwrap();assert_eq!(named.card.catalog_labels.as_map()["stage"],["running"]);
    assert_eq!(service.get_agent_by_id("test-admin",&named.card.bot_id).await.unwrap().catalog_labels,named.card.catalog_labels);
    let created=service.register_provider_bot_with_labels(command(),A2aCardSource::Inline(validated().raw),1000,input(json!({"stage":["trial"]}))).await.unwrap();
    assert_eq!(service.get_agent_by_id("test-admin",&created.bot_uuid).await.unwrap().catalog_labels.as_map()["stage"],["trial"]);
}

#[tokio::test]
async fn label_only_notifications_follow_commit_and_never_follow_invalid_or_failed_writes() {
    #[derive(Default)]struct Notifications(std::sync::Mutex<Vec<String>>);
    impl bcs_service_api::port::CatalogChangePort for Notifications {
        fn agent_changed(&self,id:&str){self.0.lock().unwrap().push(id.into());}
        fn group_changed(&self,_:&str){panic!("Agent test");}
    }
    let notices=Arc::new(Notifications::default());
    let (chain,service,_,_)=fixture_with_catalog(Some(notices.clone())).await;
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
    let service=service.with_catalog_label_policy(policy());
    let saved=service.register_agent(request(card("Notifications",None))).await.unwrap();
    assert_eq!(std::mem::take(&mut *notices.0.lock().unwrap()),vec![saved.bot_id.clone()]);
    service.update_agent(update(&saved.bot_id,Some(input(json!({"stage":["running"]}))))).await.unwrap();
    assert_eq!(std::mem::take(&mut *notices.0.lock().unwrap()),vec![saved.bot_id.clone()]);
    assert!(service.update_agent(update(&saved.bot_id,Some(input(json!({"unknown":null}))))).await.is_err());
    assert!(notices.0.lock().unwrap().is_empty());
    chain.db.arm("UPDATE bcs_bots");
    assert!(service.update_agent(update(&saved.bot_id,Some(input(json!({}))))).await.is_err());chain.db.disarm_all();
    assert!(notices.0.lock().unwrap().is_empty());
    assert_eq!(service.get_agent_by_id("test-admin",&saved.bot_id).await.unwrap().catalog_labels.as_map()["stage"],["running"]);
}
