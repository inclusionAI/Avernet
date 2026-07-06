//! Contract conformance: prove `InMemorySecretAccess` satisfies the
//! `SecretAccessPort` contract harness from `bcs-test-support`.

use bcs_secret_local::InMemorySecretAccess;
use bcs_service_api::port::secret::SecretRecord;
use bcs_test_support::secret_access_contract_tests;

#[tokio::test]
async fn in_memory_access_passes_contract() {
    let plugin = InMemorySecretAccess::new();
    plugin
        .insert("bcs-secret-contract:roundtrip", "svc", "v")
        .await;

    secret_access_contract_tests(&plugin, move || SecretRecord {
        name: "bcs-secret-contract:roundtrip".into(),
        user: "svc".into(),
        value: "v".into(),
    })
    .await;
}
