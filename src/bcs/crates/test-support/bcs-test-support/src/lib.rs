//! Test fixtures for BCS.

pub mod contract;

mod auth_noop;
mod auth_oauth_mock;
mod noop;

pub use auth_noop::{NoopAuthPlugin, NoopUserIdentityPort};
pub use auth_oauth_mock::{
    run_oauth_provider_offline_contract, run_oauth_provider_roundtrip_contract, MockFailure,
    MockOAuthProvider,
};
pub use noop::*;

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use bcs_cache_api::{CachePlugin, CacheSetMode, CacheTtl};
use bcs_db_api::{DbPlugin, DbStatement, DbTransactionStep, DbTransactionStepResult, DbValue};
use bcs_service_api::port::secret::{SecretAccessError, SecretAccessPort, SecretRecord};

/// Contract tests every CachePlugin implementation must pass.
#[derive(Debug, Clone)]
pub struct CachePluginContractOptions {
    pub key_prefix: String,
    pub ttl: Duration,
    pub ttl_wait: Duration,
}

impl CachePluginContractOptions {
    pub fn new(key_prefix: impl Into<String>, ttl: Duration, ttl_wait: Duration) -> Self {
        Self {
            key_prefix: key_prefix.into(),
            ttl,
            ttl_wait,
        }
    }

    fn key(&self, suffix: &str) -> String {
        format!("{}{}", self.key_prefix, suffix)
    }
}

impl Default for CachePluginContractOptions {
    fn default() -> Self {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        Self::new(
            format!("bcs-cache-contract:{}:", unique),
            Duration::from_millis(50),
            Duration::from_millis(100),
        )
    }
}

/// Contract tests every CachePlugin implementation must pass.
///
/// Usage:
/// ```ignore
/// #[tokio::test]
/// async fn my_plugin_contract() {
///     let plugin = MyCachePlugin::new();
///     bcs_test_support::cache_plugin_contract_tests(&plugin).await;
/// }
/// ```
#[allow(
    clippy::expect_used,
    reason = "test harness — panic on failure is the contract"
)]
pub async fn cache_plugin_contract_tests<P: CachePlugin>(plugin: &P) {
    cache_plugin_contract_tests_with_options(plugin, CachePluginContractOptions::default()).await;
}

#[allow(
    clippy::expect_used,
    reason = "test harness — panic on failure is the contract"
)]
pub async fn cache_plugin_contract_tests_with_options<P: CachePlugin>(
    plugin: &P,
    options: CachePluginContractOptions,
) {
    let missing = options.key("nope");
    let k1 = options.key("k1");
    let k2 = options.key("k2");
    let k3 = options.key("k3");
    let h1 = options.key("h1");

    for key in [&missing, &k1, &k2, &k3, &h1] {
        let _ = plugin.delete(key).await;
    }

    assert_eq!(plugin.get_value(&missing).await.expect("get"), None);
    assert!(matches!(
        plugin.ttl(&missing).await.expect("ttl missing"),
        CacheTtl::Missing
    ));
    assert!(
        plugin
            .set_value(&k1, b"v1".to_vec(), None, CacheSetMode::Upsert)
            .await
            .expect("set")
    );
    assert_eq!(
        plugin.get_value(&k1).await.expect("get"),
        Some(b"v1".to_vec())
    );
    assert!(matches!(
        plugin.ttl(&k1).await.expect("ttl persistent"),
        CacheTtl::Persistent
    ));
    assert!(plugin.delete(&k1).await.expect("delete"));
    assert_eq!(plugin.get_value(&k1).await.expect("get after delete"), None);
    plugin
        .set_value(&k2, b"v2".to_vec(), Some(options.ttl), CacheSetMode::Upsert)
        .await
        .expect("set ttl");
    assert!(
        plugin
            .get_value(&k2)
            .await
            .expect("get before expiry")
            .is_some()
    );
    tokio::time::sleep(options.ttl_wait).await;
    assert_eq!(plugin.get_value(&k2).await.expect("get after expiry"), None);

    assert!(
        plugin
            .set_value(&k3, b"first".to_vec(), None, CacheSetMode::InsertOnly)
            .await
            .expect("insert only")
    );
    assert!(
        !plugin
            .set_value(&k3, b"second".to_vec(), None, CacheSetMode::InsertOnly)
            .await
            .expect("insert only existing")
    );
    assert_eq!(
        plugin.get_value(&k3).await.expect("get insert only"),
        Some(b"first".to_vec())
    );
    assert!(
        plugin
            .set_value(&k3, b"second".to_vec(), None, CacheSetMode::UpdateOnly)
            .await
            .expect("update only")
    );
    assert_eq!(
        plugin.get_value(&k3).await.expect("get update only"),
        Some(b"second".to_vec())
    );

    plugin
        .hash_set(&h1, "status", b"online".to_vec())
        .await
        .expect("hash set");
    plugin
        .hash_set(&h1, "load", b"3".to_vec())
        .await
        .expect("hash set second");
    assert_eq!(
        plugin.hash_get(&h1, "status").await.expect("hash get"),
        Some(b"online".to_vec())
    );
    let hash = plugin.hash_get_all(&h1).await.expect("hash get all");
    assert_eq!(hash.get("load"), Some(&b"3".to_vec()));
    assert!(plugin.expire(&h1, options.ttl).await.expect("expire hash"));
    tokio::time::sleep(options.ttl_wait).await;
    assert!(
        plugin
            .hash_get_all(&h1)
            .await
            .expect("hash get all expired")
            .is_empty()
    );

    for key in [&missing, &k1, &k2, &k3, &h1] {
        let _ = plugin.delete(key).await;
    }
}

/// Contract tests every DbPlugin implementation must pass.
///
/// The SQL used here is intentionally limited to a small common subset so both
/// SQLite local plugins and MySQL-compatible plugins can run it. Plugin-specific
/// contract tests may need their own setup when the target backend cannot
/// execute this exact syntax.
#[allow(
    clippy::expect_used,
    reason = "test harness — panic on failure is the contract"
)]
pub async fn db_plugin_contract_tests<P: DbPlugin>(plugin: &P) {
    let health = plugin.health_check().await.expect("health check");
    assert!(health.healthy);

    plugin
        .execute(DbStatement::new(
            "CREATE TABLE IF NOT EXISTS contract_items \
             (id VARCHAR(128) PRIMARY KEY, name VARCHAR(255) NOT NULL, \
             active INTEGER NOT NULL, payload BLOB)",
        ))
        .await
        .expect("create contract table");
    plugin
        .execute(DbStatement::new("DELETE FROM contract_items"))
        .await
        .expect("clear contract table");

    let inserted = plugin
        .execute(DbStatement::with_params(
            "INSERT INTO contract_items (id, name, active, payload) VALUES (?, ?, ?, ?)",
            vec![
                DbValue::from("item-1"),
                DbValue::from("alpha"),
                DbValue::from(1_i64),
                DbValue::from(b"payload-1".to_vec()),
            ],
        ))
        .await
        .expect("insert item");
    assert_eq!(inserted.affected_rows, 1);

    let rows = plugin
        .query(DbStatement::with_params(
            "SELECT id, name, active, payload FROM contract_items WHERE id = ?",
            vec![DbValue::from("item-1")],
        ))
        .await
        .expect("query item");
    assert_eq!(rows.len(), 1);
    let row = &rows[0];
    assert_eq!(
        row.get_string("id").expect("id"),
        Some("item-1".to_string())
    );
    assert_eq!(
        row.get_string("name").expect("name"),
        Some("alpha".to_string())
    );
    assert_eq!(row.get_bool("active").expect("active"), Some(true));
    assert_eq!(
        row.get_bytes("payload").expect("payload"),
        Some(b"payload-1".to_vec())
    );

    let tx_results = plugin
        .transaction(vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO contract_items (id, name, active, payload) VALUES (?, ?, ?, ?)",
                vec![
                    DbValue::from("item-2"),
                    DbValue::from("beta"),
                    DbValue::from(0_i64),
                    DbValue::from(b"payload-2".to_vec()),
                ],
            )),
            DbTransactionStep::Query(DbStatement::with_params(
                "SELECT id, active FROM contract_items WHERE id = ?",
                vec![DbValue::from("item-2")],
            )),
        ])
        .await
        .expect("transaction");

    assert!(matches!(
        tx_results.first(),
        Some(DbTransactionStepResult::Executed(result)) if result.affected_rows == 1
    ));
    match tx_results.get(1) {
        Some(DbTransactionStepResult::Rows(rows)) => {
            assert_eq!(rows.len(), 1);
            assert_eq!(rows[0].get_bool("active").expect("active"), Some(false));
        }
        other => panic!("expected transaction query rows, got {:?}", other),
    }
}

/// Contract suite every `SecretAccessPort` implementation must satisfy.
///
/// `seed` is responsible for ensuring the secret named
/// `bcs-secret-contract:roundtrip` exists in the backing store and returning
/// what the test should observe. For in-memory ports this is a setter; for
/// mist it's a no-op closure pointing at a secret you've pre-provisioned.
#[allow(
    clippy::expect_used,
    reason = "test harness — panic on failure is the contract"
)]
pub async fn secret_access_contract_tests<P, F>(plugin: &P, seed: F)
where
    P: SecretAccessPort,
    F: FnOnce() -> SecretRecord,
{
    let expected = seed();
    let observed = plugin
        .get_secret("bcs-secret-contract:roundtrip")
        .await
        .expect("expected seeded secret");
    assert_eq!(observed.name, expected.name);
    assert_eq!(observed.user, expected.user);
    assert_eq!(observed.value, expected.value);

    let missing = plugin
        .get_secret("bcs-secret-contract:does-not-exist")
        .await;
    assert!(
        matches!(missing, Err(SecretAccessError::NotFound(_))),
        "missing secret must surface as SecretAccessError::NotFound, got {:?}",
        missing
    );

    let empty = plugin.get_secret("").await;
    assert!(
        matches!(empty, Err(SecretAccessError::InvalidInput(_))),
        "empty name must surface as SecretAccessError::InvalidInput, got {:?}",
        empty
    );
}
