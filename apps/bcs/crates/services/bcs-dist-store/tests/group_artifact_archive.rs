//! Per-task immutable final-product archive (plan T4 / Stage 2).
//!
//! Contract vectors: all-parts-or-none archiving, no overwrite of an archived
//! Tid, crash durability around blob puts and the finishing transaction,
//! idempotent tombstone-first reclamation with live-reference retention,
//! bounded IO, the D0-frozen wire-projection size contract, terminal-proof
//! confirmation, and the authorization existence-fence. Archive success never
//! confers public eligibility; uncommitted archives stay recovery-only.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "contract tests")]
mod group_artifact_support;
use bcs_dist_model::*;
use bcs_dist_store::group_artifact::JsonProjectionValidator;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::common::*;
use bcs_service_api::port::repo::distributed::result::PublicProjectionValidator as _;
use bcs_service_api::port::repo::distributed::result::*;
use group_artifact_support::*;
use std::num::{NonZeroU32, NonZeroU64};

fn generation() -> NonZeroU64 {
    NonZeroU64::new(1).unwrap()
}

async fn denial(f: &GroupFixture, tid: &Tid) -> StoreError {
    match f
        .group
        .get_archived(&grants(false), &scope(), &group(), tid)
        .await
    {
        Ok(view) => panic!("unauthorized read returned {view:?}"),
        Err(error) => error,
    }
}

#[tokio::test]
async fn archive_all_parts_or_none() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let archive = task_archive(
        1,
        generation(),
        vec![text_part("intro", 1, "part one"), text_part("table", 2, "part two")],
    );
    let source = f.commit(&archive).await;
    // The pin is its own transaction; part blob inserts are the only
    // executes, so the write #2 fault lands on the second part blob.
    f.inner.db.fail_writes_from(2);
    assert!(f
        .group
        .archive_final(source.clone(), archive.clone())
        .await
        .is_err());
    // Publicly nothing committed; physically no half archive parts.
    assert!(matches!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &archive.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Preparing(_))
    ));
    assert_eq!(
        f.inner
            .db
            .scalar("SELECT COUNT(*) AS c FROM bcs_fsm_task_archives")
            .await,
        1
    );
    assert_eq!(
        f.inner
            .db
            .scalar("SELECT COUNT(*) AS c FROM bcs_fsm_task_artifacts")
            .await,
        0
    );
    // Retry with the fault clears: exactly one committed archive, all parts.
    assert_eq!(
        f.group
            .archive_final(source, archive.clone())
            .await
            .unwrap(),
        SavedArchive::Saved
    );
    let parts = f
        .group
        .task_artifacts(&grants(true), &scope(), &group(), &[archive.round.tid.clone()])
        .await
        .unwrap();
    assert_eq!(parts.len(), 2);
    assert_eq!(parts[0].1.artifact_id, ArtifactId("intro".into()));
    assert_eq!(parts[1].1.artifact_id, ArtifactId("table".into()));
    assert_eq!(
        f.inner
            .db
            .scalar("SELECT COUNT(*) AS c FROM bcs_fsm_task_archives")
            .await,
        1
    );
    assert_eq!(
        f.inner
            .db
            .scalar("SELECT COUNT(*) AS c FROM bcs_fsm_task_artifacts")
            .await,
        2
    );
}

#[tokio::test]
async fn old_tid_not_overwritten() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let a = task_archive(
        1,
        generation(),
        vec![text_part("intro", 1, "A text"), data_part("table", 2, serde_json::json!({"row":"a"}))],
    );
    let b = task_archive(
        2,
        generation(),
        vec![text_part("intro", 1, "B text"), data_part("table", 2, serde_json::json!({"row":"b"}))],
    );
    let source_a = f.commit(&a).await;
    let source_b = f.commit(&b).await;
    assert_eq!(
        f.group.archive_final(source_a.clone(), a.clone()).await.unwrap(),
        SavedArchive::Saved
    );
    assert_eq!(
        f.group.archive_final(source_b, b.clone()).await.unwrap(),
        SavedArchive::Saved
    );
    // Same content under the same Tid replays with identical ids and order.
    assert_eq!(
        f.group.archive_final(source_a.clone(), a.clone()).await.unwrap(),
        SavedArchive::AlreadySaved
    );
    assert_eq!(
        f.inner
            .db
            .scalar("SELECT COUNT(*) AS c FROM bcs_fsm_task_archives")
            .await,
        2
    );
    assert_eq!(
        f.inner
            .db
            .scalar("SELECT COUNT(*) AS c FROM bcs_fsm_task_artifacts")
            .await,
        4
    );
    // Different content under the archived Tid conflicts and never overwrites.
    let mut changed = a.clone();
    changed.artifacts[0] = text_part("intro", 1, "rewritten");
    assert_eq!(
        f.group.archive_final(source_a, changed).await.unwrap(),
        SavedArchive::ContentConflict
    );
    match f
        .group
        .get_archived(&grants(true), &scope(), &group(), &a.round.tid)
        .await
        .unwrap()
    {
        Some(ArchivedTaskView::Committed { archive, .. }) => assert_eq!(archive, a),
        other => panic!("unexpected {other:?}"),
    }
    // Rounds A and B keep their own text/JSON artifacts under their own Tid.
    let parts_a = f
        .group
        .task_artifacts(&grants(true), &scope(), &group(), &[a.round.tid.clone()])
        .await
        .unwrap();
    let parts_b = f
        .group
        .task_artifacts(&grants(true), &scope(), &group(), &[b.round.tid.clone()])
        .await
        .unwrap();
    assert_eq!(parts_a[0].1.part, RoundPart::Text { text: "A text".into() });
    assert_eq!(parts_a[1].1.part, RoundPart::Data { data: serde_json::json!({"row":"a"}) });
    assert_eq!(parts_b[0].1.part, RoundPart::Text { text: "B text".into() });
    assert_eq!(parts_b[1].1.part, RoundPart::Data { data: serde_json::json!({"row":"b"}) });
}

#[tokio::test]
async fn blob_put_then_crash_before_tx() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let archive = task_archive(
        1,
        generation(),
        vec![text_part("intro", 1, "blob one"), text_part("table", 2, "blob two")],
    );
    let source = f.commit(&archive).await;
    // The pin transaction lands, then the finishing transaction dies before
    // applying: the pin is tx #1, the finisher is tx #2.
    f.inner.db.fail_transactions_from(2);
    assert!(f
        .group
        .archive_final(source.clone(), archive.clone())
        .await
        .is_err());
    assert!(matches!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &archive.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Preparing(_))
    ));
    // No proof before commit; orphan part bytes are pinned by the preparing row.
    assert_eq!(
        f.group
            .get_terminal_proof(&grants(true), &scope(), &group(), &archive.round.tid)
            .await,
        Ok(None)
    );
    assert!(
        matches!(
            f.group
                .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &proof_for(&archive, &source))
                .await,
            Err(StoreError::NotApplied { .. })
        ),
        "uncommitted archives never confirm proofs"
    );
    assert_eq!(
        f.inner
            .db
            .scalar("SELECT COUNT(*) AS c FROM bcs_dist_immutable_objects WHERE object_key LIKE 'artifact-part:%'")
            .await,
        2
    );
    // After restart the same request recovers into one committed archive.
    let store = f.reopen();
    assert_eq!(
        store
            .archive_final(source, archive.clone())
            .await
            .unwrap(),
        SavedArchive::Saved
    );
    let parts = store
        .task_artifacts(&grants(true), &scope(), &group(), &[archive.round.tid.clone()])
        .await
        .unwrap();
    assert_eq!(parts.len(), 2);
    assert_eq!(parts[0].1.artifact_id, ArtifactId("intro".into()));
}

#[tokio::test]
async fn delete_reply_lost_and_live_references_retained() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let a = task_archive(
        1,
        generation(),
        vec![text_part("shared", 1, "common blob text"), text_part("only-a", 2, "exclusive a")],
    );
    let b = task_archive(
        2,
        generation(),
        vec![text_part("shared", 1, "common blob text"), text_part("only-b", 2, "exclusive b")],
    );
    let source_a = f.commit(&a).await;
    let source_b = f.commit(&b).await;
    f.group.archive_final(source_a.clone(), a.clone()).await.unwrap();
    f.group.archive_final(source_b, b.clone()).await.unwrap();
    // Expiry marking is independent and idempotent.
    assert_eq!(
        f.group.mark_task_expired(&scope(), &group(), &a.round.tid).await.unwrap(),
        IdempotentWrite::Applied
    );
    assert_eq!(
        f.group.mark_task_expired(&scope(), &group(), &a.round.tid).await.unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    // The reclamation transaction's reply is lost: outcome unknown, retry safe.
    f.inner.db
        .lose_transactions_from(f.inner.db.transactions() + 1);
    assert!(matches!(
        f.group.reclaim_expired(&scope(), &group(), &a.round.tid).await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    // The expired tombstone is readable before — and after — deletion.
    assert!(matches!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &a.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Expired { .. })
    ));
    assert!(matches!(
        f.group
            .task_artifacts(&grants(true), &scope(), &group(), &[a.round.tid.clone()])
            .await,
        Err(StoreError::NotApplied { code, .. }) if code == "archive_expired"
    ));
    let store = f.reopen();
    assert_eq!(
        store.reclaim_expired(&scope(), &group(), &a.round.tid).await.unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    // Deleted: A's own rows and its exclusive blob. Retained: the blob still
    // referenced by live task B, and all of B's artifacts.
    let parts_b = store
        .task_artifacts(&grants(true), &scope(), &group(), &[b.round.tid.clone()])
        .await
        .unwrap();
    assert_eq!(parts_b.len(), 2);
    assert_eq!(parts_b[0].1.part, RoundPart::Text { text: "common blob text".into() });
    assert!(parts_b.iter().any(|(_, p)| p.artifact_id == ArtifactId("only-b".into())));
    // No re-executable gap: the reclaimed Tid refuses to be archived again.
    assert!(store
        .archive_final(source_a, a.clone())
        .await
        .is_err());
    assert_eq!(
        f.inner
            .db
            .scalar("SELECT COUNT(*) AS c FROM bcs_fsm_task_artifacts")
            .await,
        2
    );
}

#[tokio::test]
async fn tombstone_survives_proof_and_result_deletion() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let archive = task_archive(1, generation(), vec![text_part("intro", 1, "terminal text")]);
    let source = f.commit(&archive).await;
    f.group.archive_final(source.clone(), archive.clone()).await.unwrap();
    let proof = proof_for(&archive, &source);
    f.commit_terminal(&archive, &proof).await;
    f.group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &proof)
        .await
        .unwrap();
    assert_eq!(
        f.group.mark_task_expired(&scope(), &group(), &archive.round.tid).await.unwrap(),
        IdempotentWrite::Applied
    );
    assert_eq!(
        f.group.reclaim_expired(&scope(), &group(), &archive.round.tid).await.unwrap(),
        IdempotentWrite::Applied
    );
    // Tombstone readable after proof and result deletion.
    assert!(matches!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &archive.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Expired { .. })
    ));
    assert_eq!(
        f.group
            .get_terminal_proof(&grants(true), &scope(), &group(), &archive.round.tid)
            .await,
        Ok(None)
    );
    assert_eq!(
        f.inner
            .db
            .scalar("SELECT COUNT(*) AS c FROM bcs_dist_immutable_objects WHERE object_key LIKE 'result:%'")
            .await,
        0
    );
    // Idempotent second reclaim resolves without new deletions.
    assert_eq!(
        f.group.reclaim_expired(&scope(), &group(), &archive.round.tid).await.unwrap(),
        IdempotentWrite::AlreadyApplied
    );
}

#[tokio::test]
async fn bounded_io_reads() {
    let f = GroupFixture::new(small_budget(1024 * 1024)).await;
    let mut tids = Vec::new();
    for n in 1..=12u32 {
        let archive = task_archive(
            n,
            generation(),
            vec![
                text_part("p1", 1, "first"),
                text_part("p2", 2, "second"),
                data_part("p3", 3, serde_json::json!({"n":n})),
            ],
        );
        let source = f.commit(&archive).await;
        f.group.archive_final(source, archive.clone()).await.unwrap();
        tids.push(archive.round.tid.clone());
    }
    f.inner.db.clear_log();
    // Listing pages first; never a whole-group scan.
    let page1 = f
        .group
        .list_archived_tasks(&grants(true), &scope(), &group(), 0, NonZeroU32::new(5).unwrap())
        .await
        .unwrap();
    let page2 = f
        .group
        .list_archived_tasks(&grants(true), &scope(), &group(), 5, NonZeroU32::new(5).unwrap())
        .await
        .unwrap();
    let page3 = f
        .group
        .list_archived_tasks(&grants(true), &scope(), &group(), 10, NonZeroU32::new(5).unwrap())
        .await
        .unwrap();
    assert_eq!(page1.len(), 5);
    assert_eq!(page2.len(), 5);
    assert_eq!(page3.len(), 2);
    assert!(page1.iter().all(|tid| tids.contains(tid)));
    // Artifacts for one page: a constant number of bounded statements, no N+1.
    f.inner.db.clear_log();
    let reads_before = f.inner.db.reads();
    let parts = f
        .group
        .task_artifacts(&grants(true), &scope(), &group(), &page1)
        .await
        .unwrap();
    assert_eq!(parts.len(), 15);
    let reads = f.inner.db.reads() - reads_before;
    assert!(reads <= 3, "artifact batch fetch must be bounded queries, got {reads}");
    // Listing statements are LIMIT-bounded and metadata-only.
    let statements = f.inner.db.statements();
    let listing: Vec<&String> = statements
        .iter()
        .filter(|s| s.contains("bcs_fsm_task_archives"))
        .collect();
    assert!(!listing.is_empty());
    assert!(
        listing.iter().all(|s| s.contains("LIMIT")),
        "listing must be LIMIT-bounded: {listing:?}"
    );
    assert!(
        listing.iter().all(|s| !s.contains("archive_json")),
        "listing must not fetch archive bodies: {listing:?}"
    );
    // Object reads start bounded at IO: probe first, substring-limited fetch.
    f.inner.db.clear_log();
    let limit = NonZeroU32::new(37).unwrap();
    let (prefix, full_length) = f
        .group
        .read_task_result(&grants(true), &scope(), &group(), &tids[0], limit)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(prefix.len(), 37);
    assert!(full_length > 37);
    let bounded = f.inner.db.statements();
    assert!(
        bounded.iter().any(|s| s.contains("LENGTH(")),
        "result reads must begin with a LENGTH probe: {bounded:?}"
    );
    assert!(
        bounded.iter().any(|s| s.contains("SUBSTR(")),
        "result fetch must be IO-side length-bounded: {bounded:?}"
    );
}

#[tokio::test]
async fn blob_fits_but_wire_overflows() {
    // The raw part blob stays below B while the full projected wire
    // (envelope + escaping + history + parts) lands on B-1 / B / B+1.
    let budget = small_budget(1_500);
    let frame = budget.profile.public_frame_bytes;
    let validator = JsonProjectionValidator;
    let measure = |archive: &TaskFinalArchive| {
        validator.measure(&budget, archive).unwrap().measured_bytes
    };
    // Calibrate one pad per target byte on this task's own fixture-recorded
    // archive: 'x' text adds exactly one wired byte.
    let calibrate = |task: u32, target: u64| {
        let mut pad = 0usize;
        loop {
            let archive = padded_archive(task, generation(), pad);
            if measure(&archive) >= target {
                assert_eq!(measure(&archive), target);
                return archive;
            }
            pad += 1;
        }
    };
    let cases = [
        (5u32, (frame - 1) as u64, true),
        (6, frame as u64, true),
        (8, (frame + 1) as u64, false),
    ];
    let f = GroupFixture::new(budget).await;
    for (task, target, fits) in cases {
        let archive = calibrate(task, target);
        // The raw blob is smaller than B, but the projection may not fit.
        let blob_len =
            serde_json::to_vec(&archive.artifacts[0].part).unwrap().len() as u64;
        assert!(blob_len < frame as u64);
        let source = f.commit(&archive).await;
        assert_eq!(
            f.group.archive_final(source.clone(), archive.clone()).await.unwrap(),
            SavedArchive::Saved
        );
        let check = match f
            .group
            .get_archived(&grants(true), &scope(), &group(), &archive.record.tid)
            .await
            .unwrap()
        {
            Some(ArchivedTaskView::Committed { archive: stored, check }) => {
                assert_eq!(archive, stored);
                check
            }
            other => panic!("unexpected {other:?}"),
        };
        assert_eq!(check.measured_bytes, target);
        assert_eq!(check.fits, fits);
        // The injected budget version is the one recorded, not the D0 default.
        assert_eq!(check.budget_version, budget.version);
        let proof = proof_for(&archive, &source);
    f.commit_terminal(&archive, &proof).await;
        if fits {
            assert_eq!(
                f.group
                    .confirm_terminal_proof(&scope(), &group(), &archive.record.tid, &proof)
                    .await
                    .unwrap(),
                IdempotentWrite::Applied
            );
        } else {
            // B+1 can only end in a bounded failure finale: never public Completed.
            assert!(matches!(
                f.group
                    .confirm_terminal_proof(&scope(), &group(), &archive.record.tid, &proof)
                    .await,
                Err(StoreError::Invalid { code, .. }) if code == "archive_projection_overflow"
            ));
        }
    }
}

#[tokio::test]
async fn compacted_proof_survives_hot_expiry() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    // A: archived and committed; a committed proof lands per task.
    let a = task_archive(1, generation(), vec![text_part("a-out", 1, "A final text")]);
    let source_a = f.commit(&a).await;
    f.group.archive_final(source_a.clone(), a.clone()).await.unwrap();
    let proof_a = proof_for(&a, &source_a);
    f.commit_terminal(&a, &proof_a).await;
    f.group
        .confirm_terminal_proof(&scope(), &group(), &a.round.tid, &proof_a)
        .await
        .unwrap();
    // A-prime: archived pin only — the finishing transaction dies.
    let aprime = task_archive(2, generation(), vec![text_part("ap", 1, "never committed")]);
    let source_aprime = f.commit(&aprime).await;
    // Pin tx lands, finishing tx dies: arm the second transaction ahead.
    f.inner.db
        .fail_transactions_from(f.inner.db.transactions() + 2);
    assert!(f
        .group
        .archive_final(source_aprime, aprime.clone())
        .await
        .is_err());
    f.inner.db.fail_transactions_from(0);
    // B advances, is hot-expiry swept, reclaimed and compacted away.
    let b = task_archive(3, NonZeroU64::new(2).unwrap(), vec![text_part("b-out", 1, "B final text")]);
    let source_b = f.commit(&b).await;
    f.group.archive_final(source_b.clone(), b.clone()).await.unwrap();
    f.commit_terminal(&b, &proof_for(&b, &source_b)).await;
    f.group
        .confirm_terminal_proof(&scope(), &group(), &b.round.tid, &proof_for(&b, &source_b))
        .await
        .unwrap();
    f.group.mark_task_expired(&scope(), &group(), &b.round.tid).await.unwrap();
    f.group.reclaim_expired(&scope(), &group(), &b.round.tid).await.unwrap();
    // After a restart the cold archive still answers for A, with full equality.
    let store = f.reopen_database();
    match store
        .get_archived(&grants(true), &scope(), &group(), &a.round.tid)
        .await
        .unwrap()
    {
        Some(ArchivedTaskView::Committed { archive, .. }) => assert_eq!(archive, a),
        other => panic!("unexpected {other:?}"),
    }
    let parts = store
        .task_artifacts(&grants(true), &scope(), &group(), &[a.round.tid.clone()])
        .await
        .unwrap();
    assert_eq!(parts[0].1.part, RoundPart::Text { text: "A final text".into() });
    assert_eq!(
        store
            .get_terminal_proof(&grants(true), &scope(), &group(), &a.round.tid)
            .await
            .unwrap(),
        Some(proof_a)
    );
    // The uncommitted control A-prime is never visible as a success.
    assert!(matches!(
        store
            .get_archived(&grants(true), &scope(), &group(), &aprime.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Preparing(_))
    ));
    assert_eq!(
        store
            .get_terminal_proof(&grants(true), &scope(), &group(), &aprime.round.tid)
            .await,
        Ok(None)
    );
    // The reclaimed hot task B answers as an expired tombstone, not as success.
    assert!(matches!(
        store
            .get_archived(&grants(true), &scope(), &group(), &b.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Expired { .. })
    ));
    assert_eq!(
        store
            .get_terminal_proof(&grants(true), &scope(), &group(), &b.round.tid)
            .await,
        Ok(None)
    );
}

#[tokio::test]
async fn existence_fence_and_distinguished_states_for_authorized_reads() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    // Four physical states; the request key alone must not reveal which one.
    let committed = task_archive(1, generation(), vec![text_part("p", 1, "committed")]);
    let prepared = task_archive(2, generation(), vec![text_part("p", 1, "preparing")]);
    let expired = task_archive(3, generation(), vec![text_part("p", 1, "expired")]);
    let source = f.commit(&committed).await;
    f.group.archive_final(source, committed.clone()).await.unwrap();
    let source = f.commit(&expired).await;
    f.group.archive_final(source, expired.clone()).await.unwrap();
    f.group.mark_task_expired(&scope(), &group(), &expired.round.tid).await.unwrap();
    let source = f.commit(&prepared).await;
    // Pin tx lands, finishing tx dies: arm the second transaction ahead.
    f.inner.db
        .fail_transactions_from(f.inner.db.transactions() + 2);
    assert!(f
        .group
        .archive_final(source, prepared.clone())
        .await
        .is_err());
    f.inner.db.fail_transactions_from(0);
    let absent = task_archive(4, generation(), vec![]).record.tid;
    // Unauthorized callers get one uniform denial across all four states —
    // authorization is checked before any expiry or existence disclosure.
    let denials = [
        denial(&f, &absent).await,
        denial(&f, &prepared.round.tid).await,
        denial(&f, &committed.round.tid).await,
        denial(&f, &expired.round.tid).await,
    ];
    for error in &denials {
        assert!(matches!(error, StoreError::NotApplied { code, .. } if code == "archive_not_disclosed"));
    }
    // Authorized callers see the distinct states.
    assert_eq!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &absent)
            .await
            .unwrap(),
        None
    );
    assert!(matches!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &prepared.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Preparing(_))
    ));
    assert!(matches!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &committed.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Committed { .. })
    ));
    assert!(matches!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &expired.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Expired { result_digest, .. })
            if result_digest == expired.result_digest
    ));
    // Denied reads also issue no storage IO at all.
    f.inner.db.clear_log();
    let reads_before = f.inner.db.reads();
    let writes_before = f.inner.db.writes();
    let _ = denial(&f, &committed.round.tid).await;
    assert!(
        f.inner.db.reads() == reads_before && f.inner.db.writes() == writes_before,
        "denied reads must not touch storage"
    );
    assert!(
        f.inner.db.statements().is_empty(),
        "denied reads must issue no statements"
    );
}

#[tokio::test]
async fn terminal_proof_requires_full_field_match() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    let archive = task_archive(1, generation(), vec![text_part("p", 1, "proof text")]);
    let source = f.commit(&archive).await;
    f.group.archive_final(source.clone(), archive.clone()).await.unwrap();
    let proof = proof_for(&archive, &source);
    f.commit_terminal(&archive, &proof).await;
    // Every field must match the committed archive exactly.
    let mut mismatched = proof.clone();
    mismatched.commit_id = CommitId("forged".into());
    assert!(f
        .group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &mismatched)
        .await
        .is_err());
    let mut mismatched = proof.clone();
    mismatched.key.generation = NonZeroU64::new(2).unwrap();
    assert!(f
        .group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &mismatched)
        .await
        .is_err());
    let mut mismatched = proof.clone();
    mismatched.key.tid = Tid("s0099.session.other".into());
    assert!(f
        .group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &mismatched)
        .await
        .is_err());
    let mut mismatched = proof.clone();
    mismatched.key.scope = TaskScope {
        tenant: TenantId("other".into()),
        principal: PrincipalId("principal".into()),
    };
    assert!(f
        .group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &mismatched)
        .await
        .is_err());
    let mut mismatched = proof.clone();
    mismatched.status = TerminalStatus::Failed;
    assert!(f
        .group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &mismatched)
        .await
        .is_err());
    let mut mismatched = proof.clone();
    mismatched.result_ref = BlobRef {
        key: "result:not-the-archived-task".into(),
        digest: proof.result_ref.digest.clone(),
    };
    assert!(f
        .group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &mismatched)
        .await
        .is_err());
    let mut mismatched = proof.clone();
    mismatched.result_ref.digest = Digest(format!("sha256:{}", "0".repeat(64)));
    assert!(f
        .group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &mismatched)
        .await
        .is_err());
    let mut mismatched = proof.clone();
    mismatched.commit_seq = CommitSeq(proof.commit_seq.0 + 1);
    assert!(f
        .group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &mismatched)
        .await
        .is_err());
    // The exact proof confirms once, replays idempotently and survives restart.
    assert_eq!(
        f.group
            .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &proof)
            .await
            .unwrap(),
        IdempotentWrite::Applied
    );
    assert_eq!(
        f.group
            .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &proof)
            .await
            .unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    // A different proof for the same task never replaces the confirmed one.
    let mut second = proof.clone();
    second.closure_ref.key = "closure:second".into();
    assert!(f
        .group
        .confirm_terminal_proof(&scope(), &group(), &archive.round.tid, &second)
        .await
        .is_err());
    let store = f.reopen_database();
    assert_eq!(
        store
            .get_terminal_proof(&grants(true), &scope(), &group(), &archive.round.tid)
            .await
            .unwrap(),
        Some(proof)
    );
}

#[tokio::test]
async fn reclaim_keeps_blobs_a_preparing_archive_still_references() {
    let f = GroupFixture::new(small_budget(64 * 1024)).await;
    // A committed with part blob X.
    let a = task_archive(
        1,
        generation(),
        vec![text_part("shared", 1, "shared blob text")],
    );
    let source_a = f.commit(&a).await;
    f.group.archive_final(source_a, a.clone()).await.unwrap();
    // C preparing, sharing blob X by content-addressed dedup.
    let c = task_archive(
        2,
        generation(),
        vec![
            text_part("shared", 1, "shared blob text"),
            text_part("own", 2, "c exclusive text"),
        ],
    );
    let source_c = f.commit(&c).await;
    // C's pin transaction lands; its finishing transaction dies, so C stays
    // preparing with a live pin over blob X.
    f.inner
        .db
        .fail_transactions_from(f.inner.db.transactions() + 2);
    assert!(f
        .group
        .archive_final(source_c.clone(), c.clone())
        .await
        .is_err());
    f.inner.db.fail_transactions_from(0);
    let x_bytes = serde_json::to_vec(&RoundPart::Text {
        text: "shared blob text".into(),
    })
    .unwrap();
    let x_key = bcs_dist_store::group_artifact::part_blob_reference(&x_bytes).key;
    assert!(matches!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &c.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Preparing(_))
    ));
    // A expires and is reclaimed while C is still preparing: blob X must
    // survive — C's pin references it even though C has no part rows yet.
    f.group
        .mark_task_expired(&scope(), &group(), &a.round.tid)
        .await
        .unwrap();
    f.group
        .reclaim_expired(&scope(), &group(), &a.round.tid)
        .await
        .unwrap();
    assert_eq!(
        f.inner
            .db
            .scalar(&format!(
                "SELECT COUNT(*) AS c FROM bcs_dist_immutable_objects WHERE object_key = '{}'",
                x_key
            ))
            .await,
        1,
        "reclamation must keep blobs a preparing archive still references"
    );
    // C recovers into a fully readable committed archive — not a zombie.
    assert_eq!(
        f.group
            .archive_final(source_c, c.clone())
            .await
            .unwrap(),
        SavedArchive::Saved
    );
    let parts = f
        .group
        .task_artifacts(&grants(true), &scope(), &group(), &[c.round.tid.clone()])
        .await
        .unwrap();
    assert_eq!(parts.len(), 2);
    assert_eq!(parts[0].1.part, x_part(), );
    assert_eq!(parts[1].1.part, RoundPart::Text { text: "c exclusive text".into() });
    assert!(matches!(
        f.group
            .get_archived(&grants(true), &scope(), &group(), &a.round.tid)
            .await
            .unwrap(),
        Some(ArchivedTaskView::Expired { .. })
    ));
    // Re-reclaiming A resolves cleanly and blob X stays intact for C.
    assert_eq!(
        f.group
            .reclaim_expired(&scope(), &group(), &a.round.tid)
            .await
            .unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!(
        f.inner
            .db
            .scalar(&format!(
                "SELECT COUNT(*) AS c FROM bcs_dist_immutable_objects WHERE object_key = '{}'",
                x_key
            ))
            .await,
        1
    );
    let parts = f
        .group
        .task_artifacts(&grants(true), &scope(), &group(), &[c.round.tid.clone()])
        .await
        .unwrap();
    assert_eq!(parts.len(), 2);
}

fn x_part() -> RoundPart {
    RoundPart::Text {
        text: "shared blob text".into(),
    }
}
