#![allow(clippy::unwrap_used, clippy::expect_used, reason = "contract tests")]
mod archive_support;
use archive_support::*;
use bcs_dist_model::*;
use bcs_dist_store::archive::{DbArchiveStore, blob_reference};
use bcs_service_api::port::repo::distributed::{common::*, result::*};
use std::sync::Arc;

#[tokio::test]
async fn blobs_verify_bytes_and_preserve_identity_after_provider_restart() {
    let f = Fixture::new().await;
    let bytes = b"immutable\0payload".to_vec();
    let reference = blob_reference("candidate:one", &bytes);
    assert_eq!(
        f.store
            .put_immutable(reference.clone(), bytes.clone())
            .await
            .unwrap(),
        IdempotentWrite::Applied
    );
    let restarted = f.reopen();
    assert_eq!(
        f.reopen_database().get(&reference).await.unwrap(),
        Some(bytes.clone())
    );
    assert_eq!(
        restarted
            .put_immutable(reference.clone(), bytes.clone())
            .await
            .unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!(restarted.get(&reference).await.unwrap(), Some(bytes));
    let changed = b"changed".to_vec();
    assert_eq!(
        restarted
            .put_immutable(blob_reference("candidate:one", &changed), changed)
            .await
            .unwrap(),
        IdempotentWrite::ContentConflict
    );
    assert!(matches!(
        restarted
            .put_immutable(reference, b"wrong digest".to_vec())
            .await,
        Err(StoreError::Invalid { .. })
    ));
}

#[tokio::test]
async fn result_requires_persisted_exact_effect_and_replays_after_head_advances() {
    let f = Fixture::new().await;
    let (source, record) = final_effect();
    assert!(matches!(
        f.store.save_final(source.clone(), record.clone()).await,
        Err(StoreError::Invalid { .. })
    ));
    assert_eq!(f.db.writes(), 0);
    f.persist(&source, &record).await;
    let mut forged = source.clone();
    forged.commit.commit_id = CommitId("forged".into());
    assert!(matches!(
        f.store.save_final(forged, record.clone()).await,
        Err(StoreError::Invalid { .. })
    ));
    let reference = match f
        .store
        .save_final(source.clone(), record.clone())
        .await
        .unwrap()
    {
        SavedResult::Saved(reference) => reference,
        other => panic!("unexpected {other:?}"),
    };
    // The same immutable archive is readable through both public ports.
    assert_eq!(
        f.store.get_final(&record.scope, &record.tid).await.unwrap(),
        Some(record.clone())
    );
    assert_eq!(
        serde_json::from_slice::<FinalTaskRecord>(&f.store.get(&reference).await.unwrap().unwrap())
            .unwrap(),
        record
    );
    f.advance_head().await;
    let mut resealed = source.clone();
    resealed.commit.owner.epoch = WorkerEpoch(99);
    resealed.commit.proof = "arbitrary opaque string is not authority".into();
    assert_eq!(
        f.reopen()
            .save_final(resealed, record.clone())
            .await
            .unwrap(),
        SavedResult::AlreadySaved(reference)
    );
    let mut changed = record;
    changed.status = TerminalStatus::Canceled;
    assert_eq!(
        f.store.save_final(source, changed).await.unwrap(),
        SavedResult::ContentConflict
    );
}

#[tokio::test]
async fn ambiguous_insert_resolves_exact_content_without_repeating_write() {
    let f = Fixture::new().await;
    let bytes = b"response lost".to_vec();
    let reference = blob_reference("lost", &bytes);
    f.db.lose_next_write();
    assert_eq!(
        f.store
            .put_immutable(reference.clone(), bytes.clone())
            .await
            .unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!(f.db.writes(), 1);
    assert_eq!(f.reopen().get(&reference).await.unwrap(), Some(bytes));
    let (source, record) = final_effect();
    f.persist(&source, &record).await;
    f.db.lose_next_write();
    assert!(matches!(
        f.store.save_final(source, record).await.unwrap(),
        SavedResult::AlreadySaved(_)
    ));
    assert_eq!(f.db.writes(), 2);
}

#[tokio::test]
async fn unresolved_write_is_unknown_and_retry_is_same_identity() {
    let f = Fixture::new().await;
    let reference = blob_reference("unknown", b"data");
    f.db.lose_write_and_resolution();
    assert!(matches!(
        f.store
            .put_immutable(reference.clone(), b"data".to_vec())
            .await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    assert_eq!(
        f.reopen()
            .put_immutable(reference, b"data".to_vec())
            .await
            .unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!(f.db.writes(), 1);
}

#[tokio::test]
async fn archive_is_scope_and_namespace_isolated_and_rejects_dangling_refs() {
    let f = Fixture::new().await;
    let (source, mut record) = final_effect();
    record.snapshot = Payload::Ref(blob_reference("missing-snapshot", b"{}"));
    let source = source_for(&source.commit, &record);
    f.persist(&source, &record).await;
    assert!(matches!(
        f.store.save_final(source.clone(), record.clone()).await,
        Err(StoreError::NotApplied { .. })
    ));
    assert_eq!(f.db.writes(), 0);
    let Payload::Ref(reference) = &record.snapshot else {
        unreachable!()
    };
    f.store
        .put_immutable(reference.clone(), b"{}".to_vec())
        .await
        .unwrap();
    f.store.save_final(source, record.clone()).await.unwrap();
    let mut other_scope = record.scope.clone();
    other_scope.tenant = TenantId("other".into());
    assert_eq!(
        f.store.get_final(&other_scope, &record.tid).await.unwrap(),
        None
    );
    let other = DbArchiveStore::new(f.db.clone(), f.documents.clone(), layout("other")).unwrap();
    assert_eq!(
        other.get_final(&record.scope, &record.tid).await.unwrap(),
        None
    );
    assert_eq!(other.get(reference).await.unwrap(), None);
}

#[tokio::test]
async fn concurrent_same_identity_has_one_immutable_winner() {
    let f = Fixture::new().await;
    let store = Arc::new(f.reopen());
    let mut handles = Vec::new();
    for i in 0..8 {
        let store = store.clone();
        handles.push(tokio::spawn(async move {
            let bytes = format!("candidate-{i}").into_bytes();
            store
                .put_immutable(blob_reference("race", &bytes), bytes)
                .await
                .unwrap()
        }));
    }
    let mut applied = 0;
    let mut conflicts = 0;
    for handle in handles {
        match handle.await.unwrap() {
            IdempotentWrite::Applied => applied += 1,
            IdempotentWrite::ContentConflict => conflicts += 1,
            other => panic!("unexpected {other:?}"),
        }
    }
    assert_eq!((applied, conflicts), (1, 7));
}

#[tokio::test]
async fn reserved_result_keys_fail_before_database_access() {
    let f = Fixture::new().await;
    let store = f.reopen();
    assert!(matches!(
        store
            .put_immutable(blob_reference("result:forged", b"x"), b"x".to_vec())
            .await,
        Err(StoreError::Invalid { .. })
    ));
    assert_eq!(f.db.reads(), 0);
    assert_eq!(f.db.writes(), 0);
}

#[tokio::test]
async fn absent_row_after_unclassified_failure_is_not_definite_nonapplication() {
    let f = Fixture::new().await;
    let reference = blob_reference("before-write", b"data");
    f.db.fail_before_write();
    assert!(matches!(
        f.store
            .put_immutable(reference.clone(), b"data".to_vec())
            .await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    assert_eq!(f.db.writes(), 1);
    assert_eq!(f.db.reads(), 2);
    assert_eq!(
        f.store
            .put_immutable(reference, b"data".to_vec())
            .await
            .unwrap(),
        IdempotentWrite::Applied
    );
}

#[tokio::test]
async fn large_blob_access_cost_is_constant_for_normal_read_and_retry_paths() {
    let f = Fixture::new().await;
    let bytes = vec![42; 9 * 1024 * 1024];
    let reference = blob_reference("maximum", &bytes);
    assert_eq!(
        f.store
            .put_immutable(reference.clone(), bytes.clone())
            .await
            .unwrap(),
        IdempotentWrite::Applied
    );
    assert_eq!((f.db.reads(), f.db.writes()), (1, 1));
    assert_eq!(
        f.store.put_immutable(reference.clone(), bytes.clone()).await.unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!((f.db.reads(), f.db.writes()), (2, 1));
    assert_eq!(f.reopen().get(&reference).await.unwrap(), Some(bytes));
    assert_eq!((f.db.reads(), f.db.writes()), (3, 1));
}

#[tokio::test]
async fn completion_racing_first_lookup_and_head_verification_resolves_from_archive() {
    let f = Fixture::new().await;
    let (source, record) = final_effect();
    f.persist(&source, &record).await;
    let paused_store = f.reopen();
    let (entered, release) = f.db.pause_next_read();
    let old_source = source.clone();
    let old_record = record.clone();
    let delayed =
        tokio::spawn(async move { paused_store.save_final(old_source, old_record).await });
    entered.notified().await;
    let SavedResult::Saved(reference) = f.store.save_final(source, record).await.unwrap() else {
        panic!("first save")
    };
    f.advance_head().await;
    release.notify_one();
    assert_eq!(
        delayed.await.unwrap().unwrap(),
        SavedResult::AlreadySaved(reference)
    );
    assert_eq!(f.db.writes(), 1);
}

#[tokio::test]
async fn final_record_can_exceed_the_former_archive_byte_quota() {
    let f = Fixture::new().await;
    let (mut source, mut record) = final_effect();
    record.result = Payload::Inline(serde_json::json!("x".repeat(17 * 1024 * 1024)));
    source = source_for(&source.commit, &record);
    f.persist(&source, &record).await;
    let SavedResult::Saved(reference) = f.store.save_final(source.clone(), record.clone()).await.unwrap() else {
        panic!("saved")
    };
    assert_eq!(f.reopen().get_final(&record.scope, &record.tid).await.unwrap(), Some(record.clone()));
    assert_eq!(f.store.save_final(source, record).await.unwrap(), SavedResult::AlreadySaved(reference));
}
