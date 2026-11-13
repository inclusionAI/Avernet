//! Consumer-owned conformance scenarios. Supply an empty, isolated store per scenario.
//! Real backends run these same functions; passing memory tests proves no durability.
use std::sync::Arc;
use bcn_runtime_model::*;
use crate::AtomicStore;

fn scope() -> Scope { Scope::new("test", "tenant", "conformance") }
fn put(key: &str, value: &[u8]) -> Mutation { Mutation::Put { key: key.into(), value: value.to_vec() } }
fn batch(conditions: Vec<Condition>, mutations: Vec<Mutation>) -> AtomicBatch { AtomicBatch { conditions, mutations } }
async fn read(store: &dyn AtomicStore, key: &str) -> Option<VersionedValue> {
    store.read_many(&scope(), "p", &[key.into()]).await.unwrap().remove(0)
}

pub async fn concurrent_cas(store: Arc<dyn AtomicStore>) {
    let condition = Condition { key: "owner".into(), expected: Expected::Absent };
    let sc = scope();
    let one = store.transact(&sc, "p", batch(vec![condition.clone()], vec![put("owner", b"one")]));
    let two = store.transact(&sc, "p", batch(vec![condition], vec![put("owner", b"two")]));
    let (a, b) = futures::join!(one, two);
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    assert!(matches!(a.err().or_else(|| b.err()), Some(StorageError::ConditionFailed { .. })));
    let current = read(store.as_ref(), "owner").await.unwrap();
    let condition = Condition { key: "owner".into(), expected: Expected::Revision(current.revision) };
    let sc = scope();
    let (a, b) = futures::join!(
        store.transact(&sc, "p", batch(vec![condition.clone()], vec![put("owner", b"three")])),
        store.transact(&sc, "p", batch(vec![condition], vec![put("owner", b"four")])));
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
}
pub async fn failed_condition(store: Arc<dyn AtomicStore>) {
    store.transact(&scope(), "p", batch(vec![], vec![put("a", b"before")])).await.unwrap();
    let before = read(store.as_ref(), "a").await.unwrap();
    let failure = store.transact(&scope(), "p", batch(vec![
        Condition { key: "missing".into(), expected: Expected::Absent },
        Condition { key: "a".into(), expected: Expected::Absent },
    ], vec![put("b", b"new"), Mutation::Delete { key: "a".into() }])).await;
    assert!(matches!(failure, Err(StorageError::ConditionFailed { .. })));
    assert_eq!(read(store.as_ref(), "a").await, Some(before));
    assert_eq!(read(store.as_ref(), "b").await, None);
}
pub async fn delete_recreate(store: Arc<dyn AtomicStore>) {
    store.transact(&scope(), "p", batch(vec![], vec![put("key", b"old")])).await.unwrap();
    let old = read(store.as_ref(), "key").await.unwrap();
    let deleted = store.transact(&scope(), "p", batch(vec![], vec![Mutation::Delete { key: "key".into() }])).await.unwrap();
    assert!(deleted.revisions[0].revision > old.revision);
    assert_eq!(read(store.as_ref(), "key").await, None);
    store.transact(&scope(), "p", batch(vec![], vec![put("key", b"new")])).await.unwrap();
    let new = read(store.as_ref(), "key").await.unwrap();
    assert!(new.revision > deleted.revisions[0].revision);
    let stale = store.transact(&scope(), "p", batch(vec![Condition {
        key: "key".into(), expected: Expected::Revision(old.revision),
    }], vec![put("key", b"stale")])).await;
    assert!(matches!(stale, Err(StorageError::ConditionFailed { .. })));
    assert_eq!(read(store.as_ref(), "key").await, Some(new));
}
pub async fn isolation(store: Arc<dyn AtomicStore>) {
    let cases = [(scope(), "p"), (Scope::new("other", "tenant", "conformance"), "p"),
        (Scope::new("test", "other", "conformance"), "p"),
        (Scope::new("test", "tenant", "other"), "p"), (scope(), "other")];
    for (i, (scope, partition)) in cases.iter().enumerate() {
        store.transact(scope, partition, batch(vec![Condition { key: "same".into(), expected: Expected::Absent }],
            vec![put("same", &[i as u8])])).await.unwrap();
    }
    for (i, (scope, partition)) in cases.iter().enumerate() {
        let row = store.read_many(scope, partition, &["same".into()]).await.unwrap();
        assert_eq!(row[0].as_ref().unwrap().value, vec![i as u8]);
    }
}
pub async fn pagination(store: Arc<dyn AtomicStore>) {
    store.transact(&scope(), "p", batch(vec![], vec![put("item/c", b"c"), put("item/a", b"a"),
        put("item/b", b"b"), put("other/a", b"x")])).await.unwrap();
    let first = store.scan_page(&scope(), "p", "item/", None, 2).await.unwrap();
    assert_eq!(first.entries.iter().map(|e| e.key.as_str()).collect::<Vec<_>>(), ["item/a", "item/b"]);
    let cursor = first.next_cursor.unwrap();
    let second = store.scan_page(&scope(), "p", "item/", Some(cursor.clone()), 2).await.unwrap();
    assert_eq!(second.entries.iter().map(|e| e.key.as_str()).collect::<Vec<_>>(), ["item/c"]);
    assert!(second.next_cursor.is_none());
    assert!(matches!(store.scan_page(&scope(), "other", "item/", Some(cursor.clone()), 2).await, Err(StorageError::InvalidCursor)));
    assert!(matches!(store.scan_page(&scope(), "p", "other/", Some(cursor.clone()), 2).await, Err(StorageError::InvalidCursor)));
    assert!(matches!(store.scan_page(&Scope::new("test", "other", "conformance"), "p", "item/", Some(cursor), 2).await, Err(StorageError::InvalidCursor)));
    assert!(store.scan_page(&scope(), "p", "missing/", None, 2).await.unwrap().entries.is_empty());
}
pub async fn limits_and_validation(store: Arc<dyn AtomicStore>) {
    let sc = scope();
    for bad in [Scope::new("test", "tenant", ""), Scope::new("test", "a/b", "conformance"), Scope::new("../x", "tenant", "conformance")] {
        assert!(store.transact(&bad, "p", batch(vec![], vec![put("key", b"x")])).await.is_err());
        assert!(store.read_many(&bad, "p", &["key".into()]).await.is_err());
        assert!(store.scan_page(&bad, "p", "", None, 1).await.is_err());
    }
    assert!(store.transact(&sc, "p/escape", batch(vec![], vec![put("key", b"x")])).await.is_err());
    for bad in [String::new(), "a\0b".into(), "k".repeat(MAX_KEY_BYTES + 1)] {
        assert!(store.transact(&sc, "p", batch(vec![], vec![put(&bad, b"x")])).await.is_err());
        assert!(store.read_many(&sc, "p", &[bad]).await.is_err());
    }
    assert!(matches!(store.read_many(&sc, "p", &vec!["x".into(); MAX_READ_KEYS + 1]).await, Err(StorageError::LimitExceeded(_))));
    for limit in [0, MAX_SCAN_LIMIT + 1] {
        assert!(matches!(store.scan_page(&sc, "p", "", None, limit).await, Err(StorageError::LimitExceeded(_))));
    }
    assert!(matches!(store.transact(&sc, "p", batch(vec![], (0..=MAX_BATCH_MUTATIONS).map(|i| put(&format!("k{i}"), b"x")).collect())).await, Err(StorageError::LimitExceeded(_))));
    assert!(matches!(store.transact(&sc, "p", batch(vec![Condition { key: "x".into(), expected: Expected::Absent }; MAX_BATCH_CONDITIONS + 1], vec![])).await, Err(StorageError::LimitExceeded(_))));
    assert!(matches!(store.transact(&sc, "p", batch(vec![], vec![put("x", &vec![0; MAX_VALUE_BYTES + 1])])).await, Err(StorageError::LimitExceeded(_))));
    assert!(matches!(store.transact(&sc, "p", batch(vec![], vec![put("duplicate", b"one"), put("duplicate", b"two")])).await, Err(StorageError::DuplicateMutation(_))));
    assert!(read(store.as_ref(), "duplicate").await.is_none());
    let keys = (0..MAX_READ_KEYS).map(|i| format!("key{i:03}")).collect::<Vec<_>>();
    store.transact(&sc, "p", batch(vec![], keys.iter().map(|k| put(k, b"value")).collect())).await.unwrap();
    assert_eq!(store.read_many(&sc, "p", &keys).await.unwrap().len(), MAX_READ_KEYS);
    assert_eq!(store.scan_page(&sc, "p", "key", None, MAX_SCAN_LIMIT).await.unwrap().entries.len(), MAX_SCAN_LIMIT);
}

/// Bounded entry counts alone are insufficient: cumulative payload also has a hard bound.
pub async fn total_batch_bytes(store: Arc<dyn AtomicStore>) {
    let mutations = (0..9).map(|i| put(&format!("large{i}"), &vec![0; MAX_VALUE_BYTES])).collect();
    let result = store.transact(&scope(), "p", batch(vec![], mutations)).await;
    assert!(matches!(result, Err(StorageError::LimitExceeded(_))), "oversized aggregate batch must be rejected");
    assert!(read(store.as_ref(), "large0").await.is_none(), "rejected aggregate batch must write nothing");
}

pub async fn ordered_reads_and_validation_atomicity(store: Arc<dyn AtomicStore>) {
    store.transact(&scope(), "p", batch(vec![], vec![put("a", b"a"), put("b", b"b")])).await.unwrap();
    let rows = store.read_many(&scope(), "p", &["b".into(), "missing".into(), "a".into(), "b".into()]).await.unwrap();
    assert_eq!(rows.iter().map(|row| row.as_ref().map(|value| value.value.clone())).collect::<Vec<_>>(),
        vec![Some(b"b".to_vec()), None, Some(b"a".to_vec()), Some(b"b".to_vec())]);
    assert!(store.read_many(&scope(), "p", &[]).await.unwrap().is_empty());
    let a = rows[2].clone().unwrap();
    let no_mutations = store.transact(&scope(), "p", batch(vec![Condition { key: "a".into(), expected: Expected::Revision(a.revision) }], vec![])).await.unwrap();
    assert!(no_mutations.revisions.is_empty());
    assert_eq!(read(store.as_ref(), "a").await.unwrap(), a);
    let rejected = store.transact(&scope(), "p", batch(vec![], vec![put("a", b"changed"), put("invalid\0key", b"invalid")])).await;
    assert!(matches!(rejected, Err(StorageError::InvalidKey(_))));
    assert_eq!(read(store.as_ref(), "a").await.unwrap(), a, "validation cannot leave partial writes");
}
