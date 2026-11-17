#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "contract assertions"
)]
#[path = "support/store/mod.rs"]
mod store;
use bcs_store_api::{kv::KvStorePlugin, *};
use bcs_test_support::contract::plugin::store::{
    self as contract, json::*, kv::*, stream::*, zset::*,
};
use std::time::Duration;
use store::Model;

#[tokio::test]
async fn conformance_kv() {
    let p = Model::default();
    contract::store_plugin_contract_tests(&p);
    kv_store_plugin_contract_tests(&p, "kv").await;
}
#[tokio::test]
async fn conformance_conditional_kv() {
    kv_conditional_contract_tests(&Model::default(), "cas").await;
}
#[tokio::test]
async fn conformance_expiry_preserves_version() {
    let p = Model::default();
    kv_expiry_contract_tests(&p, "expiry", || async {
        p.advance(Duration::from_secs(2));
    })
    .await;
}
#[tokio::test]
async fn conformance_unknown_write_is_resolvable() {
    let p = Model::default();
    kv_unknown_outcome_contract_tests(&p, "unknown", || p.lose_next_response()).await;
}
#[tokio::test]
async fn conformance_json() {
    json_store_plugin_contract_tests(&Model::default(), "json").await;
}
#[tokio::test]
async fn conformance_zset() {
    z_set_store_plugin_contract_tests(&Model::default(), "zset").await;
}
#[tokio::test]
async fn conformance_versioned_zset() {
    versioned_zset_contract_tests(&Model::default(), "vzset").await;
}
#[tokio::test]
async fn conformance_stream() {
    stream_store_plugin_contract_tests(&Model::default(), "stream").await;
}
#[tokio::test]
async fn conformance_stream_groups() {
    stream_group_contract_tests(&Model::default(), "group").await;
}
#[tokio::test]
async fn unsupported_conditional_write_does_not_fall_back() {
    let mut p = Model::default();
    p.conditional_disabled = true;
    assert!(!p.supports(StoreCapability::ConditionalKv));
    p.set("k", b"old", Expiry::Persistent).await.unwrap();
    assert_eq!(
        p.set_if_newer("k", Version(9), b"new", Expiry::Persistent)
            .await,
        Err(StoreError::Unsupported(StoreCapability::MonotonicKv))
    );
    assert_eq!(p.get("k").await.unwrap(), Some(b"old".to_vec()));
}
#[tokio::test]
#[should_panic(expected = "CAS must have one winner")]
async fn harness_rejects_non_atomic_cas() {
    let mut p = Model::default();
    p.broken_cas = true;
    kv_conditional_contract_tests(&p, "broken").await;
}
#[tokio::test]
#[should_panic(expected = "failed JSON patch must roll back every edit")]
async fn harness_rejects_partial_json_patch() {
    let mut p = Model::default();
    p.broken_json = true;
    json_store_plugin_contract_tests(&p, "broken").await;
}
#[tokio::test]
#[should_panic(expected = "same-score activation must advance collection version")]
async fn harness_rejects_lost_activation() {
    let mut p = Model::default();
    p.broken_zset_noop = true;
    versioned_zset_contract_tests(&p, "broken").await;
}

#[tokio::test]
async fn conformance_stream_blocking() {
    stream_blocking_contract_tests(&Model::default(), "blocking").await;
}

#[tokio::test]
async fn fixture_never_rounds_ttl_down() {
    let p = Model::default();
    p.set(
        "fractional",
        b"alive",
        Expiry::After(Duration::from_micros(1500)),
    )
    .await
    .unwrap();
    p.advance(Duration::from_millis(1));
    assert_eq!(p.get("fractional").await.unwrap(), Some(b"alive".to_vec()));
    p.advance(Duration::from_millis(1));
    assert_eq!(p.get("fractional").await.unwrap(), None);
}

#[tokio::test]
async fn json_harness_accepts_provider_generated_large_versions() {
    use bcs_store_api::json::{JsonMutation, JsonStorePlugin};
    let p = Model::default();
    p.mutate_json(
        "high:json",
        WriteCondition::Newer(Version(1000)),
        JsonMutation::Replace(serde_json::json!({})),
        Expiry::Persistent,
    )
    .await
    .unwrap();
    p.mutate_json(
        "high:json",
        WriteCondition::Version(Version(1000)),
        JsonMutation::Delete,
        Expiry::Preserve,
    )
    .await
    .unwrap();
    json_store_plugin_contract_tests(&p, "high").await;
}

#[tokio::test]
#[should_panic(expected = "JSON CAS must have one winner")]
async fn harness_rejects_interleaved_json_check_write() {
    let mut p = Model::default();
    p.broken_json_cas = true;
    json_store_plugin_contract_tests(&p, "interleaved").await;
}
#[tokio::test]
#[should_panic(expected = "ZSet CAS must have one winner")]
async fn harness_rejects_interleaved_zset_check_write() {
    let mut p = Model::default();
    p.broken_zset_cas = true;
    versioned_zset_contract_tests(&p, "interleaved").await;
}

#[tokio::test]
async fn basic_cas_harness_does_not_require_supplied_versions() {
    let mut p = Model::default();
    p.basic_only = true;
    kv_conditional_contract_tests(&p, "basic").await;
}

#[tokio::test]
async fn conformance_monotonic_kv() {
    kv_monotonic_contract_tests(&Model::default(), "monotonic").await;
}
#[tokio::test]
async fn basic_stream_harness_accepts_unavailable_recovery_evidence() {
    let mut p = Model::default();
    p.basic_only = true;
    stream_store_plugin_contract_tests(&p, "basic-stream").await;
    stream_group_contract_tests(&p, "basic-groups").await;
}
