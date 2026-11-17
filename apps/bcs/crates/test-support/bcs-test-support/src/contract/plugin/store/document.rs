//! Mount this suite for each DocumentCas transport with a fresh persistent key.
use super::applied;
use bcs_store_api::{document::DocumentStorePlugin, *};
use serde_json::json;

pub async fn document_store_contract_tests<P: DocumentStorePlugin + ?Sized>(p: &P, key: &str) {
    assert!(p.supports(StoreCapability::DocumentCas));
    assert!(p.read_document(key).await.unwrap().is_none());
    let v = applied(
        p.create_document(key, &json!({"old":1,"nested":{"a":1,"b":2}}))
            .await
            .unwrap(),
    );
    assert_eq!(
        p.create_document(key, &json!("overwrite")).await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let value = json!({"nested":{"a":null},"_store":{"version":"business"},"rev":null});
    let next = applied(p.replace_document(key, v, &value).await.unwrap());
    assert!(next > v);
    assert_eq!(
        p.read_document(key).await.unwrap().unwrap(),
        Versioned {
            value,
            version: next
        }
    );
    assert_eq!(
        p.replace_document(key, v, &json!(0)).await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let null = applied(p.replace_document(key, next, &json!(null)).await.unwrap());
    assert_eq!(
        p.read_document(key).await.unwrap().unwrap(),
        Versioned {
            value: json!(null),
            version: null
        }
    );
}
