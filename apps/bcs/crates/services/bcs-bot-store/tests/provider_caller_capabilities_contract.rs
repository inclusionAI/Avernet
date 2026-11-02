//! Provider caller-capability storage conformance (P1.4 storage batch).
//!
//! Locks the authoritative, cache-bypassing read of
//! `bcs_providers.config.$.caller_capabilities` (spec §10.7.3): undeclared is
//! `Ok(None)` (never "supports caller"), a disabled provider and broken
//! declarations are closed errors that never echo raw config, and the read
//! observes committed rows even when the ordinary provider cache would still
//! hold an older record.

use std::sync::Arc;

use bcs_bot_store::DbProviderStore;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_service_api::port::repo::ProviderRepoPort;
use bcs_service_api::{CallerTokenVisibility, ProviderRecord, ServiceError};
async fn sqlite() -> Arc<dyn DbPlugin> {
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_providers (
            provider_id TEXT NOT NULL,
            env TEXT NOT NULL,
            name TEXT NOT NULL,
            slug TEXT,
            config TEXT NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL,
            owners TEXT NOT NULL,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (env, provider_id)
        )",
    ))
    .await
    .expect("create bcs_providers");
    db
}

fn provider_record(provider_id: &str, config: &str, disabled: bool) -> ProviderRecord {
    ProviderRecord {
        provider_id: provider_id.to_string(),
        slug: None,
        name: format!("Provider {provider_id}"),
        config: config.to_string(),
        created_by: "staff-1".to_string(),
        owners: "[]".to_string(),
        disabled,
        created_at: 1_780_000_000_000,
        updated_at: 1_780_000_000_000,
    }
}

async fn seeded() -> (DbProviderStore, Arc<dyn DbPlugin>) {
    let db = sqlite().await;
    let store = DbProviderStore::sqlite(db.clone());
    (store, db)
}

#[tokio::test]
async fn undeclared_capabilities_are_none_never_supported() {
    let (store, _db) = seeded().await;
    store
        .insert_provider(provider_record("p-plain", r#"{"downlink":{}}"#, false))
        .await
        .expect("seed");
    assert_eq!(
        store.get_caller_capabilities("p-plain").await.expect("read"),
        None
    );
}

#[tokio::test]
async fn declared_capabilities_round_trip() {
    let (store, _db) = seeded().await;
    store
        .insert_provider(provider_record(
            "p-caller",
            r#"{"caller_capabilities":{"supports_caller":true,"supports_isolation_binding":false,"supports_observe_inject":true,"token_visibility":"runtime_only"}}"#,
            false,
        ))
        .await
        .expect("seed");
    let caps = store
        .get_caller_capabilities("p-caller")
        .await
        .expect("read")
        .expect("declared");
    assert!(caps.supports_caller);
    assert!(!caps.supports_isolation_binding);
    assert!(caps.supports_observe_inject);
    assert_eq!(caps.token_visibility, CallerTokenVisibility::RuntimeOnly);
}

#[tokio::test]
async fn missing_provider_is_provider_not_found() {
    let (store, _db) = seeded().await;
    let error = store.get_caller_capabilities("p-ghost").await.unwrap_err();
    assert!(matches!(error, ServiceError::ProviderNotFound(_)));
}

#[tokio::test]
async fn disabled_provider_is_unavailable_not_undeclared() {
    let (store, _db) = seeded().await;
    store
        .insert_provider(provider_record(
            "p-disabled",
            r#"{"caller_capabilities":{"supports_caller":true,"supports_isolation_binding":true,"supports_observe_inject":true,"token_visibility":"runtime_only"}}"#,
            true,
        ))
        .await
        .expect("seed");
    let error = store.get_caller_capabilities("p-disabled").await.unwrap_err();
    assert!(!matches!(error, ServiceError::ProviderNotFound(_)));
}

#[tokio::test]
async fn broken_declarations_are_closed_errors_without_raw_config() {
    let (store, _db) = seeded().await;
    // Explicit null is not "undeclared".
    store
        .insert_provider(provider_record(
            "p-null",
            r#"{"caller_capabilities":null,"secret":"raw-config-token"}"#,
            false,
        ))
        .await
        .expect("seed null");
    let error = store.get_caller_capabilities("p-null").await.unwrap_err();
    assert!(!error.to_string().contains("raw-config-token"));

    // Malformed declaration: wrong types.
    store
        .insert_provider(provider_record("p-malformed", r#"{"secret":"raw-config-token-2"}"#, false))
        .await
        .expect("seed malformed");
    // config itself is valid JSON without caller object -> undeclared.
    assert_eq!(store.get_caller_capabilities("p-malformed").await.expect("read"), None);
    // A truly malformed caller object is an error.
    let db = sqlite().await;
    let store = DbProviderStore::sqlite(db.clone());
    store
        .insert_provider(provider_record(
            "p-badcaps",
            r#"{"caller_capabilities":{"supports_caller":"yes"},"secret":"raw-config-token-3"}"#,
            false,
        ))
        .await
        .expect("seed bad caps");
    let error = store.get_caller_capabilities("p-badcaps").await.unwrap_err();
    assert!(!error.to_string().contains("raw-config-token-3"));
    assert!(!matches!(error, ServiceError::ProviderNotFound(_)));
}

#[tokio::test]
async fn capability_read_bypasses_the_provider_cache() {
    let (store, db) = seeded().await;
    store
        .insert_provider(provider_record("p-cache", r#"{"downlink":{}}"#, false))
        .await
        .expect("seed old config");
    // Populates the ordinary provider cache with the old record.
    assert!(store.get_provider("p-cache").await.expect("cached read").is_some());
    // Committed change outside the store: the cache must not hide it.
    db.execute(DbStatement::with_params(
        "UPDATE bcs_providers SET config = ? WHERE provider_id = 'p-cache'",
        vec![bcs_db_api::DbValue::from(
            r#"{"caller_capabilities":{"supports_caller":true,"supports_isolation_binding":false,"supports_observe_inject":false,"token_visibility":"runtime_only"}}"#.to_string(),
        )],
    ))
    .await
    .expect("raw update");
    let caps = store
        .get_caller_capabilities("p-cache")
        .await
        .expect("read")
        .expect("declared after commit");
    assert!(caps.supports_caller);
}