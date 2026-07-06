use bcs_bot_store::provider::MemoryProviderStore;
use bcs_service_api::{
    ProviderBotBinding, ProviderBotBindingRepoPort, ProviderBotDiscoverySelector, ProviderCredential,
    ProviderCredentialRepoPort, ProviderRecord, ProviderRepoPort,
};

fn provider(provider_id: &str) -> ProviderRecord {
    ProviderRecord {
        provider_id: provider_id.to_string(),
        name: format!("Provider {provider_id}"),
        config: r#"{"downlink":{"enabled":true,"webhook_url":"https://provider.example.com/bcs/webhook","auth_mode":"static_bearer","protocol_version":"1.0"}}"#.to_string(),
        created_by: "11111111".to_string(),
        owners: r#"["11111111"]"#.to_string(),
        disabled: false,
        created_at: 1,
        updated_at: 1,
    }
}

#[tokio::test]
async fn memory_provider_store_round_trips_provider_and_credentials() {
    let store = MemoryProviderStore::new();
    store.insert_provider(provider("provider-1")).await.unwrap();
    store
        .insert_credential(ProviderCredential {
            provider_id: "provider-1".to_string(),
            credential_kind: "provider_admin".to_string(),
            secret_value: "bcs_pa_test".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .unwrap();

    let by_id = store.get_provider("provider-1").await.unwrap().unwrap();
    assert_eq!(by_id.provider_id, "provider-1");

    let by_secret = store
        .get_credential_by_secret("provider_admin", "bcs_pa_test")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(by_secret.provider_id, "provider-1");
}

#[tokio::test]
async fn memory_provider_store_round_trips_binding_indexes() {
    let store = MemoryProviderStore::new();
    store
        .insert_binding(ProviderBotBinding {
            bot_uuid: "bot-1".to_string(),
            provider_id: "provider-1".to_string(),
            provider_bot_ref: "reviewer-v2".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .unwrap();

    assert_eq!(
        store
            .get_binding_by_bot_uuid("bot-1")
            .await
            .unwrap()
            .unwrap()
            .provider_bot_ref,
        "reviewer-v2"
    );
    assert_eq!(
        store
            .get_binding_by_provider_ref("provider-1", "reviewer-v2")
            .await
            .unwrap()
            .unwrap()
            .bot_uuid,
        "bot-1"
    );
}

#[tokio::test]
async fn memory_provider_store_batch_queries_by_ids() {
    let store = MemoryProviderStore::new();
    store.insert_provider(provider("provider-1")).await.unwrap();
    store.insert_provider(provider("provider-2")).await.unwrap();
    for provider_id in ["provider-1", "provider-2"] {
        store
            .insert_credential(ProviderCredential {
                provider_id: provider_id.to_string(),
                credential_kind: "downlink_bcs_to_provider".to_string(),
                secret_value: format!("secret-{provider_id}"),
                disabled: false,
                created_at: 1,
                updated_at: 1,
            })
            .await
            .unwrap();
    }
    for (bot_uuid, provider_id) in [("bot-1", "provider-1"), ("bot-2", "provider-2")] {
        store
            .insert_binding(ProviderBotBinding {
                bot_uuid: bot_uuid.to_string(),
                provider_id: provider_id.to_string(),
                provider_bot_ref: format!("ref-{bot_uuid}"),
                disabled: false,
                created_at: 1,
                updated_at: 1,
            })
            .await
            .unwrap();
    }

    let providers = store
        .list_providers_by_ids(&["provider-2".to_string(), "missing".to_string()])
        .await
        .unwrap();
    assert_eq!(providers.len(), 1);
    assert_eq!(providers[0].provider_id, "provider-2");

    let credentials = store
        .list_credentials_by_kind_for_providers(
            &["provider-1".to_string(), "missing".to_string()],
            "downlink_bcs_to_provider",
        )
        .await
        .unwrap();
    assert_eq!(credentials.len(), 1);
    assert_eq!(credentials[0].provider_id, "provider-1");

    let bindings = store
        .list_bindings_by_bot_uuids(&["bot-2".to_string(), "missing".to_string()])
        .await
        .unwrap();
    assert_eq!(bindings.len(), 1);
    assert_eq!(bindings[0].bot_uuid, "bot-2");
}

#[tokio::test]
async fn memory_provider_store_lists_discoverable_provider_bot_records() {
    let store = MemoryProviderStore::new();
    store.insert_provider(provider("provider-1")).await.unwrap();
    let mut disabled_provider = provider("provider-disabled");
    disabled_provider.disabled = true;
    store.insert_provider(disabled_provider).await.unwrap();

    for (bot_uuid, provider_id, disabled) in [
        ("bot-1", "provider-1", false),
        ("bot-disabled", "provider-1", true),
        ("bot-provider-disabled", "provider-disabled", false),
    ] {
        store
            .insert_binding(ProviderBotBinding {
                bot_uuid: bot_uuid.to_string(),
                provider_id: provider_id.to_string(),
                provider_bot_ref: format!("ref-{bot_uuid}"),
                disabled,
                created_at: 1,
                updated_at: 1,
            })
            .await
            .unwrap();
    }

    let records = store
        .list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::All)
        .await
        .unwrap();

    assert_eq!(records.len(), 1);
    assert_eq!(records[0].bot_uuid, "bot-1");
    assert_eq!(records[0].provider_id, "provider-1");
    assert_eq!(records[0].provider_name, "Provider provider-1");
}
