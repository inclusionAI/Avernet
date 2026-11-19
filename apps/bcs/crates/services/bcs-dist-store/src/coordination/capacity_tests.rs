#![allow(clippy::unwrap_used, reason = "encoded provider capacity assertions")]
use super::*;
use bcs_test_support::store_model::Model;
use serde_json::json;

fn store(model: Arc<Model>, capacity: SessionCheckpointCapacity) -> CoordinationStore {
    CoordinationStore::with_config(
        model.clone(),
        model.clone(),
        model,
        KeyLayout::new("capacity", "test").unwrap(),
        CoordinationStoreConfig {
            checkpoint_capacity: capacity,
            storage_envelope_bytes: 64,
            ..Default::default()
        },
    )
    .unwrap()
}

#[tokio::test]
async fn exact_document_budget_counts_the_storage_wrapper_before_cas() {
    let model = Arc::new(Model::default());
    let data = json!({"checkpoint":{"value":"x".repeat(300 * 1024)}});
    let capacity = SessionCheckpointCapacity {
        max_document_bytes: serde_json::to_vec(&data).unwrap().len() + 64,
        max_envelope_bytes: 64 * 1024,
    };
    let store = store(model.clone(), capacity);
    assert_eq!(store.checkpoint_capacity(), Some(capacity));
    let version = store
        .write("accepted", None, &data, "accepted")
        .await
        .unwrap()
        .unwrap();
    let mut oversized = data.clone();
    oversized["checkpoint"]["value"] = json!("x".repeat(300 * 1024 + 1));
    assert_eq!(
        store
            .write("accepted", Some(version), &oversized, "oversized")
            .await,
        Err(invalid("checkpoint_document_byte_budget"))
    );
    assert_eq!(
        model.read_document("accepted").await.unwrap().unwrap(),
        Versioned {
            value: data,
            version
        }
    );
}

#[tokio::test]
async fn envelope_cannot_spend_the_reserved_business_frame() {
    let model = Arc::new(Model::default());
    let store = store(model.clone(), SessionCheckpointCapacity::default());
    let oversized = json!({"checkpoint":{},"control":"x".repeat(64 * 1024)});
    assert_eq!(
        store.write("rejected", None, &oversized, "rejected").await,
        Err(invalid("checkpoint_envelope_byte_budget"))
    );
    assert!(model.read_document("rejected").await.unwrap().is_none());
}

#[test]
fn startup_rejects_a_wrapper_that_consumes_the_control_envelope() {
    for envelope in [64 * 1024, 128 * 1024] {
        let model = Arc::new(Model::default());
        let capacity = SessionCheckpointCapacity {
            max_document_bytes: 1024 * 1024,
            max_envelope_bytes: envelope,
        };
        assert!(
            CoordinationStore::with_config(
                model.clone(),
                model.clone(),
                model,
                KeyLayout::new("capacity", "test").unwrap(),
                CoordinationStoreConfig {
                    checkpoint_capacity: capacity,
                    storage_envelope_bytes: envelope - 1,
                    ..Default::default()
                }
            )
            .is_err()
        );
    }
}

#[test]
fn maximum_provider_envelope_includes_wrapper_at_both_capacity_endpoints() {
    let maximum = capacity::maximum_envelope_bytes().unwrap();
    println!("maximum provider envelope without storage wrapper={maximum}");
    assert!(maximum + 64 <= 64 * 1024);
    for envelope in [64 * 1024, 128 * 1024] {
        let model = Arc::new(Model::default());
        let config = CoordinationStoreConfig {
            checkpoint_capacity: SessionCheckpointCapacity {
                max_document_bytes: 1024 * 1024,
                max_envelope_bytes: envelope,
            },
            storage_envelope_bytes: envelope - maximum,
            ..Default::default()
        };
        assert!(
            CoordinationStore::with_config(
                model.clone(),
                model.clone(),
                model.clone(),
                KeyLayout::new("capacity", "test").unwrap(),
                config.clone()
            )
            .is_ok()
        );
        assert!(
            CoordinationStore::with_config(
                model.clone(),
                model.clone(),
                model,
                KeyLayout::new("capacity", "test").unwrap(),
                CoordinationStoreConfig {
                    storage_envelope_bytes: config.storage_envelope_bytes + 1,
                    ..config
                }
            )
            .is_err()
        );
    }
}
