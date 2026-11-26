#![allow(clippy::unwrap_used, reason = "public SQLite provider conformance")]
#[allow(dead_code, reason="shared fixture also serves fault/restart suites")]
mod archive_support;
use archive_support::*;
use bcs_dist_store::archive::{DbArchiveStore, blob_reference};
use bcs_test_support::contract::distributed::state::*;
#[tokio::test]
async fn conformance_blob_and_result() {
    let f = Fixture::new().await;
    let port: &DbArchiveStore = &f.store;
    let bytes = b"immutable".to_vec();
    blob_store_port_contract_tests(
        port,
        blob_reference("contract", &bytes),
        bytes,
        b"changed".to_vec(),
    )
    .await
    .unwrap();
    let (source, record) = final_effect();
    f.persist(&source, &record).await;
    result_store_port_contract_tests(port, source, record)
        .await
        .unwrap();
}
