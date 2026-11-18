#![allow(clippy::unwrap_used, clippy::expect_used, reason = "contract assertions")]
use async_trait::async_trait;
use bcs_store_api::{document::DocumentStorePlugin, json::JsonEdit, *};
use bcs_test_support::store_model::Model;
use serde_json::{Value, json};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

#[tokio::test]
async fn projection_keeps_null_missing_and_all_values_at_one_version() {
    let p = Instrumented::default();
    p.create_document("doc", &json!({"value":null,"a/b":{"~":7},"arr":[1,2]})).await.unwrap();
    let read = p.read_document_paths("doc", &[
        "/value".into(), "/missing".into(), "/a~1b/~0".into(), "/arr/1".into(), "".into(),
    ]).await.unwrap().unwrap();
    assert_eq!(read.version, Version(1));
    assert_eq!(&read.values[..4], &[Some(json!(null)), None, Some(json!(7)), Some(json!(2))]);
    assert_eq!(read.values[4].as_ref().unwrap()["arr"], json!([1,2]));
    assert_eq!(p.reads.load(Ordering::SeqCst), 1);
    assert_eq!(p.writes.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn patch_is_ordered_and_rejects_the_whole_candidate() {
    let p = Instrumented::default();
    let value = json!({"arr":[1,2,3],"a/b":{"~":7},"old":null});
    p.create_document("doc", &value).await.unwrap();
    let next = p.patch_document("doc", Version(1), &[
        JsonEdit::Remove { pointer: "/arr/0".into() },
        JsonEdit::Set { pointer: "/arr/1".into(), value: json!(4) },
        JsonEdit::Remove { pointer: "/old".into() },
        JsonEdit::Set { pointer: "/a~1b/~0".into(), value: json!(null) },
    ]).await.unwrap();
    assert_eq!(next, WriteOutcome::Applied { version: Version(2) });
    assert_eq!(p.reads.load(Ordering::SeqCst), 1);
    assert_eq!(p.writes.load(Ordering::SeqCst), 1);
    let before = p.read_document("doc").await.unwrap().unwrap();
    assert_eq!(before.value, json!({"arr":[2,4],"a/b":{"~":null}}));
    assert!(matches!(p.patch_document("doc", Version(2), &[
        JsonEdit::Set { pointer: "/arr/0".into(), value: json!(99) },
        JsonEdit::Remove { pointer: "/missing".into() },
    ]).await, Err(StoreError::InvalidInput(_))));
    assert_eq!(p.read_document("doc").await.unwrap().unwrap(), before);
    assert_eq!(p.writes.load(Ordering::SeqCst), 1, "failed edits never reach storage");
}

#[tokio::test]
async fn intervening_write_is_not_overwritten_by_a_patch() {
    let p = Instrumented::default();
    p.create_document("doc", &json!({"left":0,"right":0})).await.unwrap();
    p.interleave.store(true, Ordering::SeqCst);
    assert_eq!(p.patch_document("doc", Version(1), &[
        JsonEdit::Set { pointer: "/left".into(), value: json!(1) },
    ]).await.unwrap(), WriteOutcome::ConditionNotMet);
    assert_eq!(p.read_document("doc").await.unwrap().unwrap(), Versioned {
        version: Version(2), value: json!({"left":0,"right":1}),
    });
    assert_eq!(p.writes.load(Ordering::SeqCst), 1, "no automatic CAS retry");
}

#[tokio::test]
async fn uncertain_patch_is_not_retried_and_remains_resolvable() {
    let p = Instrumented::default();
    p.create_document("doc", &json!({"request":"old"})).await.unwrap();
    p.model.lose_next_response();
    let edits = [JsonEdit::Set { pointer: "/request".into(), value: json!("new") }];
    assert!(matches!(p.patch_document("doc", Version(1), &edits).await,
        Err(StoreError::OutcomeUnknown(_))));
    assert_eq!(p.writes.load(Ordering::SeqCst), 1);
    assert_eq!(p.read_document("doc").await.unwrap().unwrap(), Versioned {
        version: Version(2), value: json!({"request":"new"}),
    });
    assert_eq!(p.patch_document("doc", Version(1), &edits).await.unwrap(),
        WriteOutcome::ConditionNotMet);
    assert_eq!(p.writes.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn malformed_requests_fail_before_reading_even_when_missing_or_stale() {
    let p = Instrumented::default();
    for pointer in ["not-a-pointer", "/bad~", "/bad~2escape"] {
        assert!(matches!(p.read_document_paths("missing", &[pointer.into()]).await,
            Err(StoreError::InvalidInput(_))));
        assert!(matches!(p.patch_document("missing", Version(1), &[
            JsonEdit::Remove { pointer: pointer.into() },
        ]).await, Err(StoreError::InvalidInput(_))));
    }
    assert!(matches!(p.patch_document("missing", Version(1), &[]).await,
        Err(StoreError::InvalidInput(_))));
    assert!(matches!(p.patch_document("missing", Version(1), &[
        JsonEdit::Remove { pointer: "".into() },
    ]).await, Err(StoreError::InvalidInput(_))));
    assert!(matches!(p.patch_document("missing", Version(0), &[
        JsonEdit::Remove { pointer: "/field".into() },
    ]).await, Err(StoreError::InvalidInput(_))));
    assert_eq!(p.reads.load(Ordering::SeqCst), 0);
    assert_eq!(p.writes.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn unsupported_subset_rejects_without_io() {
    let p = Instrumented { disabled: true, ..Default::default() };
    assert_eq!(p.read_document_paths("doc", &["".into()]).await,
        Err(StoreError::Unsupported(StoreCapability::DocumentCas)));
    assert_eq!(p.patch_document("doc", Version(1), &[
        JsonEdit::Remove { pointer: "/field".into() },
    ]).await, Err(StoreError::Unsupported(StoreCapability::DocumentCas)));
    assert_eq!(p.reads.load(Ordering::SeqCst), 0);
    assert_eq!(p.writes.load(Ordering::SeqCst), 0);
}

#[tokio::test]
#[should_panic(expected = "same-version patches have exactly one winner")]
async fn patch_conformance_rejects_a_nonatomic_replace_provider() {
    let mut p = Model::default();
    p.broken_json_cas = true;
    p.create_document("doc", &json!({"value":0})).await.unwrap();
    bcs_test_support::contract::plugin::store::document::document_patch_concurrency_contract_tests(&p, "doc").await;
}

#[tokio::test]
async fn root_arrays_and_numeric_object_keys_preserve_pointer_semantics() {
    let p = Model::default();
    p.create_document("array", &json!([{"01":null}, 2])).await.unwrap();
    assert_eq!(p.read_document_paths("array", &["/0/01".into(), "/2".into()]).await.unwrap().unwrap().values,
        vec![Some(json!(null)), None]);
    p.patch_document("array", Version(1), &[
        JsonEdit::Set { pointer: "/0/01".into(), value: json!(3) },
        JsonEdit::Remove { pointer: "/1".into() },
    ]).await.unwrap();
    assert_eq!(p.read_document("array").await.unwrap().unwrap(), Versioned {
        version: Version(2), value: json!([{"01":3}]),
    });
}

#[tokio::test]
async fn document_unknown_outcome_conformance() {
    let p = Model::default();
    bcs_test_support::contract::plugin::store::document::document_unknown_outcome_contract_tests(
        &p, "unknown-doc", || p.lose_next_response(),
    ).await;
}

#[derive(Default)]
struct Instrumented {
    model: Model,
    reads: AtomicUsize,
    writes: AtomicUsize,
    interleave: AtomicBool,
    disabled: bool,
}
impl StorePlugin for Instrumented {
    fn supports(&self, capability: StoreCapability) -> bool {
        capability == StoreCapability::DocumentCas && !self.disabled
    }
}
#[async_trait]
impl DocumentStorePlugin for Instrumented {
    async fn read_document(&self, key: &str) -> StoreResult<Option<Versioned<Value>>> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        self.model.read_document(key).await
    }
    async fn create_document(&self, key: &str, value: &Value) -> StoreResult<WriteOutcome> {
        self.model.create_document(key, value).await
    }
    async fn replace_document(&self, key: &str, expected: Version, value: &Value) -> StoreResult<WriteOutcome> {
        self.writes.fetch_add(1, Ordering::SeqCst);
        if self.interleave.swap(false, Ordering::SeqCst) {
            self.model.replace_document(key, expected, &json!({"left":0,"right":1})).await?;
        }
        self.model.replace_document(key, expected, value).await
    }
}
