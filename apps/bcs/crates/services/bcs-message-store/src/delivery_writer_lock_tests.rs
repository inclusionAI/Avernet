//! Delivery writer-lock unit tests, moved out of `delivery.rs` so the
//! production source stays within the 1,000-line cap (plan Task 12 fix
//! round; suite body unchanged). Included from `delivery.rs` via `#[path]`.

use std::collections::BTreeSet;

use super::*;

#[tokio::test]
async fn sessions_are_independent_and_capacity_keys_remain_shared() {
    let locks = DeliveryWriterLocks::default();
    let a = locks.acquire(BTreeSet::from([(1, "a".into())])).await;
    let b = locks.acquire(BTreeSet::from([(1, "b".into())]));
    let _b = tokio::time::timeout(std::time::Duration::from_secs(1), b).await.unwrap();
    let mut same = Box::pin(locks.acquire(BTreeSet::from([(1, "a".into())])));
    assert!(tokio::time::timeout(std::time::Duration::from_millis(10), &mut same).await.is_err());
    drop(a);
    drop(same.await);
}

#[tokio::test]
async fn cancelling_multi_key_wait_releases_acquired_locks() {
    let locks = DeliveryWriterLocks::default();
    let held = locks.acquire(BTreeSet::from([(1, "b".into())])).await;
    let mut pending = Box::pin(locks.acquire(BTreeSet::from([(1, "a".into()), (1, "b".into())])));
    assert!(
        tokio::time::timeout(std::time::Duration::from_millis(10), &mut pending).await.is_err()
    );
    drop(pending);
    drop(locks.acquire(BTreeSet::from([(1, "a".into())])).await);
    drop(held);
    for i in 0..512 {
        drop(locks.acquire(BTreeSet::from([(1, i.to_string())])).await);
    }
    assert!(locks.directory_len() <= 128);
}