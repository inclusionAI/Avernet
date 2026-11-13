use super::*;
use bcs_service_api::{core::a2a_bot::A2aBotCoreService, port::CatalogChangePort};
use bcs_service_api::application::a2a_bot::ConfigureA2aBotCommand;
use bcs_service_api::types::a2a_bot::{A2aCardSource, A2aRegistrationVisibility};

#[derive(Default)]
struct Changes(std::sync::Mutex<Vec<String>>);
impl CatalogChangePort for Changes {
    fn agent_changed(&self, id: &str) { self.0.lock().unwrap().push(id.into()); }
    fn group_changed(&self, _: &str) { panic!("not a Group mutation") }
}
impl Changes {
    fn take(&self) -> Vec<String> { std::mem::take(&mut *self.0.lock().unwrap()) }
}

#[tokio::test]
async fn writes_notify_once_and_failures_never_notify() {
    let changes = Arc::new(Changes::default());
    let (chain, service, fetcher, _) = fixture_with_catalog(Some(changes.clone())).await;
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
    let mut invalid_card = bcs_service_api::application::a2a_bot::RegisterAgentCommand {
        provider_bot_ref:"invalid".into(), visibility:Default::default(), provider_admin_token:"test-admin".into(),
        owners:Some(vec!["delegated-owner".into()]), summary:None, source:A2aCardSource::Inline(json!({})), now_ms:1000,
    };
    assert!(service.register_agent(invalid_card.clone()).await.is_err());
    invalid_card.source=A2aCardSource::Inline(validated().raw);
    invalid_card.owners=Some(vec!["human_bad-owner".into()]);
    assert!(service.register_agent(invalid_card).await.is_err());
    assert!(changes.take().is_empty());
    let mut invalid = command(); invalid.provider_admin_token = "wrong".into();
    assert!(service.register_provider_bot(invalid, "https://example.org/card.json".into(), 1000).await.is_err());
    chain.db.arm("INSERT INTO bcs_bots");
    assert!(service.register_provider_bot(command(), "https://example.org/card.json".into(), 1000).await.is_err());
    chain.db.disarm_all();
    assert!(changes.take().is_empty());
    let saved = service.register_provider_bot(command(), "https://example.org/card.json".into(), 1000).await.unwrap();
    let id = saved.bot_uuid;
    assert_eq!(changes.take(), vec![id.clone()]);
    assert!(service.register_provider_bot(command(), "https://example.org/card.json".into(), 1000).await.is_err());
    assert!(changes.take().is_empty());

    service.update_agent_by_id_with_visibility("test-admin", &id, None, Some(A2aRegistrationVisibility::Public)).await.unwrap();
    assert_eq!(changes.take(), vec![id.clone()]);
    // Explicit refresh of unchanged content still commits and notifies.
    service.refresh_agent_by_id("test-admin", &id).await.unwrap();
    assert_eq!(changes.take(), vec![id.clone()]);
    // Legacy/background no-write refresh retains the optimization.
    service.refresh("provider-a", "test-admin", &id, 1000).await.unwrap();
    assert!(changes.take().is_empty());
    fetcher.fail.store(true, Ordering::SeqCst);
    assert!(service.refresh_agent_by_id("test-admin", &id).await.is_err());
    assert!(changes.take().is_empty());
    fetcher.fail.store(false, Ordering::SeqCst);

    service.configure(ConfigureA2aBotCommand { provider_id:"provider-a".into(), provider_admin_token:"test-admin".into(),
        bot_uuid:id.clone(), source:A2aCardSource::Inline(validated().raw), expected_revision:None, now_ms:1000 }).await.unwrap();
    assert_eq!(changes.take(), vec![id.clone()]);
    assert!(service.refresh_agent_by_id("test-admin", &id).await.is_err(), "inline Card cannot refresh");
    assert!(changes.take().is_empty());

    let cards = Arc::new(DbA2aBotStore::sqlite(chain.db.clone(), bcs_config::resolve_env_str()));
    let core = A2aBotCore::new(cards.clone(), chain.provider_repo.clone()).with_catalog_changes(Some(changes.clone()));
    let old = cards.get(&id).await.unwrap().unwrap();
    let next = A2aBotRecord { revision:old.revision+1, ..old.clone() };
    core.commit_card(next.clone(), old.revision).await.unwrap();
    assert_eq!(changes.take(), vec![id.clone()]);
    assert!(core.commit_card(next.clone(), old.revision).await.is_err());
    assert!(core.commit_card_with_visibility(next, old.revision, Some(A2aRegistrationVisibility::Private)).await.is_err());
    assert!(changes.take().is_empty());
    chain.db.arm("UPDATE bcs_bots");
    assert!(service.update_agent_by_id("test-admin", &id, A2aCardSource::Inline(validated().raw)).await.is_err());
    assert!(changes.take().is_empty());
}

struct ReadbackFailure;
#[async_trait]
impl A2aBotRepoPort for ReadbackFailure {
    async fn get(&self, _: &str) -> ServiceResult<Option<A2aBotRecord>> {
        Err(ServiceError::InternalError("injected post-commit read failure".into()))
    }
    async fn cards_for_bots(&self, _: &[String]) -> ServiceResult<std::collections::BTreeMap<String,serde_json::Value>> { panic!("unused") }
    async fn discovery_cards(&self, _: &[String], _: Option<&str>, _: &[String]) -> ServiceResult<std::collections::BTreeMap<String,Option<serde_json::Value>>> { panic!("unused") }
    async fn availability_many(&self, _: &[String]) -> ServiceResult<Vec<A2aBotAvailability>> { panic!("unused") }
    async fn compare_and_set(&self, _: &A2aBotRecord, _: u64) -> ServiceResult<bool> { panic!("unused") }
    async fn replace(&self, _: &A2aBotRecord) -> ServiceResult<A2aBotRecord> { panic!("unused") }
    async fn get_by_name(&self, _: &str, _: &str) -> ServiceResult<Option<A2aBotRecord>> { panic!("unused") }
    async fn get_by_provider_ref(&self, _: &str, _: &str) -> ServiceResult<Option<A2aBotRecord>> { panic!("unused") }
    async fn refreshable(&self, _: Option<&str>, _: u32) -> ServiceResult<Vec<A2aBotRecord>> { panic!("unused") }
}

#[tokio::test]
async fn committed_registration_notifies_even_if_response_readback_fails() {
    let changes=Arc::new(Changes::default());
    let (chain,_,fetcher,registration)=fixture_with_catalog(Some(changes.clone())).await;
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'")).await.unwrap();
    let service=A2aBotManagement::new(fetcher.providers.clone(),
        Arc::new(A2aBotCore::new(Arc::new(ReadbackFailure),chain.provider_repo.clone())),fetcher)
        .with_registration(registration,chain.registry_core.clone());
    let result=service.register_agent(bcs_service_api::application::a2a_bot::RegisterAgentCommand {
        provider_bot_ref:"committed".into(),visibility:Default::default(),provider_admin_token:"test-admin".into(),
        owners:Some(vec!["delegated-owner".into()]),summary:None,source:A2aCardSource::Inline(validated().raw),now_ms:1000,
    }).await;
    assert!(result.is_err());
    let notifications=changes.take();
    assert_eq!(notifications.len(),1);
    assert!(DbA2aBotStore::sqlite(chain.db.clone(),bcs_config::resolve_env_str()).get(&notifications[0]).await.unwrap().is_some());
}
