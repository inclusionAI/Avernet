pub async fn seed_memory_provider(store: &bcs_bot_store::MemoryProviderStore) {
    use bcs_service_api::ProviderRepoPort;
    store.insert_provider(bcs_service_api::ProviderRecord {
        provider_id: "provider-a".into(), slug: None, name: "Fixture".into(), config: "{}".into(),
        created_by: "owner-a".into(), owners: "[]".into(), disabled: false, created_at: 1, updated_at: 1,
    }).await.unwrap();
}
