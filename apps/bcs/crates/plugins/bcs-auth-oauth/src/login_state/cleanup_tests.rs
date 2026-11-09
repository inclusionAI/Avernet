#![allow(clippy::expect_used, clippy::unwrap_used)]

use super::*;

const BASE: &str = "https://bcs.example.com/callback";

#[tokio::test]
async fn abandoned_batches_are_reclaimed_without_their_callbacks() {
    let store = MemoryPendingOAuthLoginStore::new();
    let providers = vec!["github".into(), "google".into()];
    for _ in 0..100 {
        store
            .issue_batch(&providers, BASE, "v1", 100)
            .await
            .unwrap();
    }
    let live = store
        .issue_batch(&providers, BASE, "v1", 400)
        .await
        .unwrap();
    store
        .consume(
            &live.provider_states[0].1,
            &live.browser_nonce,
            "github",
            &format!("{BASE}/github"),
            "v1",
            401,
        )
        .await
        .unwrap();
    let inner = store.inner.lock().unwrap();
    assert!(inner.records.is_empty());
    assert!(inner.batches.is_empty());
}

#[tokio::test]
async fn cleanup_is_bounded_and_wrong_browser_keeps_the_live_batch() {
    let store = MemoryPendingOAuthLoginStore::new();
    let providers = vec!["github".into(), "google".into()];
    for _ in 0..EXPIRED_BATCH_CLEANUP_LIMIT + 10 {
        store
            .issue_batch(&providers, BASE, "v1", 100)
            .await
            .unwrap();
    }
    let live = store
        .issue_batch(&providers, BASE, "v1", 400)
        .await
        .unwrap();
    {
        let inner = store.inner.lock().unwrap();
        assert_eq!(
            inner.batches.len(),
            11,
            "only 64 expired batches reclaimed per call"
        );
        assert_eq!(inner.expirations.len(), 11);
        assert_eq!(inner.records.len(), 22);
    }
    assert_eq!(
        store
            .consume(
                &live.provider_states[0].1,
                "wrong-browser",
                "github",
                &format!("{BASE}/github"),
                "v1",
                401
            )
            .await,
        Err(LoginStateError::Invalid)
    );
    {
        let inner = store.inner.lock().unwrap();
        assert_eq!(inner.batches.len(), 1);
        assert_eq!(inner.expirations.len(), 1);
        assert_eq!(inner.records.len(), 2);
    }
    store
        .consume(
            &live.provider_states[1].1,
            &live.browser_nonce,
            "google",
            &format!("{BASE}/google"),
            "v1",
            401,
        )
        .await
        .unwrap();
    let inner = store.inner.lock().unwrap();
    assert!(inner.records.is_empty());
    assert!(inner.batches.is_empty());
    assert!(inner.expirations.is_empty());
}

#[tokio::test]
async fn capacity_failure_is_atomic_and_does_not_evict_live_logins() {
    let store = MemoryPendingOAuthLoginStore::new();
    let providers = vec!["github".into(), "google".into()];
    let first = store
        .issue_batch(&providers, BASE, "v1", 100)
        .await
        .unwrap();
    for _ in 1..MAX_PENDING_STATES / 2 {
        store
            .issue_batch(&providers, BASE, "v1", 100)
            .await
            .unwrap();
    }
    assert_eq!(
        store
            .issue_batch(&providers, BASE, "v1", 101)
            .await
            .unwrap_err(),
        LoginStateError::Unavailable
    );
    {
        let inner = store.inner.lock().unwrap();
        assert_eq!(inner.records.len(), MAX_PENDING_STATES);
        assert_eq!(inner.batches.len(), MAX_PENDING_STATES / 2);
        assert_eq!(inner.expirations.len(), MAX_PENDING_STATES / 2);
    }
    store
        .consume(
            &first.provider_states[0].1,
            &first.browser_nonce,
            "github",
            &format!("{BASE}/github"),
            "v1",
            101,
        )
        .await
        .unwrap();
    store
        .issue_batch(&providers, BASE, "v1", 101)
        .await
        .unwrap();
    assert_eq!(
        store.inner.lock().unwrap().records.len(),
        MAX_PENDING_STATES
    );

    // Expired capacity is reclaimed before deciding whether to reject an issue.
    store
        .issue_batch(&providers, BASE, "v1", 401)
        .await
        .unwrap();
    assert_eq!(
        store.inner.lock().unwrap().records.len(),
        MAX_PENDING_STATES - 2 * EXPIRED_BATCH_CLEANUP_LIMIT + 2
    );
}

#[tokio::test]
async fn oversized_batch_is_rejected_without_partial_insertion() {
    let store = MemoryPendingOAuthLoginStore::new();
    assert_eq!(
        store
            .issue_batch(
                &vec!["github".into(); MAX_PENDING_STATES + 1],
                BASE,
                "v1",
                100
            )
            .await
            .unwrap_err(),
        LoginStateError::Unavailable
    );
    let inner = store.inner.lock().unwrap();
    assert!(inner.records.is_empty());
    assert!(inner.batches.is_empty());
    assert!(inner.expirations.is_empty());
}

#[tokio::test]
async fn successful_consumes_do_not_accumulate_expiry_entries() {
    let store = MemoryPendingOAuthLoginStore::new();
    for _ in 0..128 {
        let batch = store
            .issue_batch(&["github".into()], BASE, "v1", 100)
            .await
            .unwrap();
        store
            .consume(
                &batch.provider_states[0].1,
                &batch.browser_nonce,
                "github",
                &format!("{BASE}/github"),
                "v1",
                101,
            )
            .await
            .unwrap();
    }
    let inner = store.inner.lock().unwrap();
    assert!(inner.records.is_empty());
    assert!(inner.batches.is_empty());
    assert!(inner.expirations.is_empty());
}

#[tokio::test]
async fn expiry_order_does_not_assume_a_monotonic_wall_clock() {
    let store = MemoryPendingOAuthLoginStore::new();
    let providers = vec!["github".into()];
    let live = store
        .issue_batch(&providers, BASE, "v1", 1000)
        .await
        .unwrap();
    store
        .issue_batch(&providers, BASE, "v1", 100)
        .await
        .unwrap();
    assert_eq!(
        store
            .consume(
                "unknown",
                "unknown",
                "github",
                &format!("{BASE}/github"),
                "v1",
                400
            )
            .await,
        Err(LoginStateError::Invalid)
    );
    let inner = store.inner.lock().unwrap();
    assert_eq!(inner.records.len(), 1);
    assert!(inner.records.contains_key(&live.provider_states[0].1));
    assert_eq!(inner.batches.len(), 1);
    assert_eq!(inner.expirations.len(), 1);
}

#[tokio::test]
async fn concurrent_issuance_cannot_exceed_capacity() {
    let store = std::sync::Arc::new(MemoryPendingOAuthLoginStore::new());
    store
        .issue_batch(
            &vec!["github".into(); MAX_PENDING_STATES - 1],
            BASE,
            "v1",
            100,
        )
        .await
        .unwrap();
    let barrier = std::sync::Arc::new(tokio::sync::Barrier::new(8));
    let mut tasks = Vec::new();
    for _ in 0..8 {
        let store = store.clone();
        let barrier = barrier.clone();
        tasks.push(tokio::spawn(async move {
            barrier.wait().await;
            store.issue_batch(&["github".into()], BASE, "v1", 101).await
        }));
    }
    let mut successes = 0;
    for task in tasks {
        match task.await.unwrap() {
            Ok(_) => successes += 1,
            Err(error) => assert_eq!(error, LoginStateError::Unavailable),
        }
    }
    assert_eq!(successes, 1);
    assert_eq!(
        store.inner.lock().unwrap().records.len(),
        MAX_PENDING_STATES
    );
}

#[tokio::test]
async fn expired_target_beyond_cleanup_budget_is_rejected_without_extra_sweeping() {
    let store = MemoryPendingOAuthLoginStore::new();
    for _ in 0..EXPIRED_BATCH_CLEANUP_LIMIT {
        store
            .issue_batch(&["github".into()], BASE, "v1", 100)
            .await
            .unwrap();
    }
    let last = store
        .issue_batch(&["github".into()], BASE, "v1", 101)
        .await
        .unwrap();
    assert_eq!(
        store
            .consume(
                &last.provider_states[0].1,
                &last.browser_nonce,
                "github",
                &format!("{BASE}/github"),
                "v1",
                401
            )
            .await,
        Err(LoginStateError::Invalid)
    );
    {
        let inner = store.inner.lock().unwrap();
        assert_eq!(inner.records.len(), 1);
        assert_eq!(inner.batches.len(), 1);
        assert_eq!(inner.expirations.len(), 1);
    }
    assert_eq!(
        store
            .consume(
                &last.provider_states[0].1,
                &last.browser_nonce,
                "github",
                &format!("{BASE}/github"),
                "v1",
                401
            )
            .await,
        Err(LoginStateError::Invalid)
    );
    assert!(store.inner.lock().unwrap().records.is_empty());
}
