use std::sync::Arc;
use bcs_bot_store::{MemoryProviderStore, provider::DbProviderStore};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{ProviderBotBinding, ProviderBotBindingRepoPort};

async fn database() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_provider_bot_bindings (
        bot_uuid TEXT NOT NULL, provider_id TEXT NOT NULL, provider_bot_ref TEXT NOT NULL,
        env TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, webhook_url TEXT,
        gmt_create TEXT DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(env, bot_uuid), UNIQUE(env, provider_id, provider_bot_ref)
    )")).await.unwrap();
    db
}

fn binding() -> ProviderBotBinding {
    ProviderBotBinding { bot_uuid: "a".into(), provider_id: "provider".into(),
        provider_bot_ref: "ref-a".into(), webhook_url: None, disabled: false,
        created_at: 1, updated_at: 1 }
}

async fn contract(store: &dyn ProviderBotBindingRepoPort) {
    store.insert_binding(binding()).await.unwrap();
    assert!(store.get_binding_by_bot_uuid("a").await.unwrap().unwrap().webhook_url.is_none());
    assert!(store.update_binding_webhook_url("other", "a", Some("https://evil.example.com"), 2)
        .await.unwrap().is_none());
    let endpoint = "https://a.example.com/webhook";
    store.update_binding_webhook_url("provider", "a", Some(endpoint), 2).await.unwrap().unwrap();
    let by_bot = store.get_binding_by_bot_uuid("a").await.unwrap().unwrap();
    let by_ref = store.get_binding_by_provider_ref("provider", "ref-a").await.unwrap().unwrap();
    let batch = store.list_bindings_by_bot_uuids(&["a".into()]).await.unwrap();
    let list = store.list_bindings_by_provider("provider").await.unwrap();
    for row in [by_bot, by_ref, batch[0].clone(), list[0].clone()] {
        assert_eq!(row.webhook_url.as_deref(), Some(endpoint));
    }
    store.update_binding_webhook_url("provider", "a", None, 3).await.unwrap().unwrap();
    assert!(store.get_binding_by_bot_uuid("a").await.unwrap().unwrap().webhook_url.is_none());
}

#[tokio::test]
async fn memory_and_sqlite_obey_the_same_endpoint_contract() {
    contract(&MemoryProviderStore::new()).await;
    contract(&DbProviderStore::sqlite(database().await)).await;
}

#[tokio::test]
async fn sqlite_reloads_persisted_override_and_propagates_write_errors() {
    let db = database().await;
    let store = DbProviderStore::sqlite(db.clone());
    store.insert_binding(binding()).await.unwrap();
    store.update_binding_webhook_url("provider", "a", Some("https://a.example.com/webhook"), 2)
        .await.unwrap();
    let reloaded = DbProviderStore::sqlite(db.clone());
    assert_eq!(reloaded.get_binding_by_bot_uuid("a").await.unwrap().unwrap().webhook_url,
        Some("https://a.example.com/webhook".into()));
    db.execute(DbStatement::new("DROP TABLE bcs_provider_bot_bindings")).await.unwrap();
    assert!(store.update_binding_webhook_url("provider", "a", None, 3).await.is_err());
}

#[tokio::test]
async fn other_instances_observe_address_changes_after_existing_cache_ttl() {
    let db = database().await;
    let writer = DbProviderStore::sqlite(db.clone());
    let reader = DbProviderStore::sqlite(db);
    writer.insert_binding(binding()).await.unwrap();
    assert!(reader.get_binding_by_bot_uuid("a").await.unwrap().unwrap().webhook_url.is_none());
    writer.update_binding_webhook_url("provider", "a", Some("https://a.example.com/webhook"), 2)
        .await.unwrap();
    assert!(reader.get_binding_by_bot_uuid("a").await.unwrap().unwrap().webhook_url.is_none());
    tokio::time::sleep(std::time::Duration::from_secs(31)).await;
    assert_eq!(reader.get_binding_by_bot_uuid("a").await.unwrap().unwrap().webhook_url,
        Some("https://a.example.com/webhook".into()));
}
