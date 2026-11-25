//! Plan T7 slice 3 vectors: dispatch directory reclaim reuses capacity, the
//! scan cursor survives reclaim + takeover via versioned re-establishment,
//! retained ended dispatches fence late writes, and unknown or still-obliged
//! entries are never reclaimed.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "reclaim vectors")]
mod common;
mod dispatch_support;
mod task_feed_support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, dispatch::*, session::EffectKind};
use bcs_store_api::document::DocumentStorePlugin;
use dispatch_support::*;
use std::time::Duration;

/// A distinct did (and shard-local directory row) per number; all share the
/// fixture session, so they share `ShardId(42)`.
fn numbered_spec(number: u64) -> DispatchSpec {
    let mut spec = spec();
    spec.tid = Tid(format!("{}.r{number}", common::TID));
    spec.did = Did(format!("{}.r{number}.d1", common::TID));
    spec
}

fn numbered_did(number: u64) -> Did {
    numbered_spec(number).did
}

/// Create the given numbered dids under one committed batch.
async fn create_numbered(f: &Fixture, sequence: u64, numbers: &[u64]) {
    let kinds = numbers
        .iter()
        .map(|n| EffectKind::CreateDispatch {
            spec: numbered_spec(*n),
        })
        .collect();
    let sources = f.base.commit(sequence, kinds).await;
    for (index, n) in numbers.iter().enumerate() {
        let spec = numbered_spec(*n);
        assert_eq!(
            f.board
                .create(CreateCommittedDispatch {
                    source: sources[index].clone(),
                    spec: spec.clone(),
                })
                .await
                .unwrap(),
            IdempotentWrite::Applied,
            "create of r{n} must apply"
        );
    }
}

/// End the dispatch with a definite outcome, retained terminal evidence and
/// a settled (no longer supervised) recheck: reclaimable in every dimension.
async fn settle(f: &Fixture, did: &Did) {
    let mut held = match f
        .queue
        .claim(DispatchClaimRequest {
            did: did.clone(),
            me: me(),
            request_id: RequestId(format!("claim-{}", did.0)),
        })
        .await
        .unwrap()
    {
        ClaimOutcome::Acquired(held) => held,
        _ => panic!("claim of {} failed", did.0),
    };
    held.finish(FinalDispatchOutcome::Completed, format!("completed-{}", did.0))
        .await
        .unwrap();
    let record = f.board.get(did).await.unwrap().unwrap();
    assert_eq!(
        f.board
            .reschedule(did, record.revision, TimestampMillis(1))
            .await
            .unwrap(),
        RecheckOutcome::Ended,
        "settlement must clear the ended dispatch's recheck"
    );
}

/// Poll `scan_due` until the durable scan round completes. The shared scan
/// budget refills from real time; starved polls are spaced out instead of
/// spinning. Every surfaced due did is returned.
async fn drive_scan_to_completion(f: &Fixture) -> Vec<Did> {
    let mut items = vec![];
    for _ in 0..4096 {
        let page = f
            .board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap();
        let advanced = !page.items.is_empty();
        items.extend(page.items);
        if page.next.is_none() {
            return items;
        }
        if !advanced {
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    }
    panic!("scan round did not complete");
}

/// Best-effort bounded round progress for long capacity drives: a few polls
/// per round, no completion requirement (scan tokens arrive over real time).
async fn nudge_scan(f: &Fixture, polls: usize) {
    for _ in 0..polls {
        let page = f
            .board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap();
        if page.next.is_none() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
}

/// The authoritative raw journal document of the shard.
async fn journal(f: &Fixture) -> serde_json::Value {
    f.base
        .storage
        .read_document(&f.base.layout.dispatch_journal(ShardId(42)))
        .await
        .unwrap()
        .unwrap()
        .value
}

/// The reclaim reservation proof: entry count of the shard directory.
async fn directory_len(f: &Fixture) -> usize {
    journal(f)
        .await["directory"]
        .as_array()
        .expect("directory journal")
        .len()
}

/// `dispatch_directory_reuses_capacity`: two new dids per round in one
/// fixed shard, cumulatively past the default 1,024 directory entries, and
/// dispatch stays possible — reclaim reuses the capacity; no cap or TTL was
/// raised and only bounded shard batches run.
#[tokio::test]
async fn dispatch_directory_reuses_capacity() {
    let f = Fixture::new().await;
    let rounds = (1024 / 2) + 2; // cumulative dids past the 1,024 default
    let mut sequence = 0u64;
    let mut created = 0usize;
    for round in 1..=rounds {
        let a = round as u64 * 2 - 1;
        let b = round as u64 * 2;
        sequence += 1;
        create_numbered(&f, sequence, &[a, b]).await;
        settle(&f, &numbered_did(a)).await;
        settle(&f, &numbered_did(b)).await;
        created += 2;
        nudge_scan(&f, 6).await;
    }
    assert!(created > 1024);
    // Drain any lazy arm backlog with complete rounds before the final
    // capacity assertion: reclaim must have removed the ended history.
    for _ in 0..512 {
        if directory_len(&f).await < 128 {
            break;
        }
        let _ = drive_scan_to_completion(&f).await;
    }
    // A dispatch past the cumulative default cap still applies.
    sequence += 1;
    create_numbered(&f, sequence, &[rounds as u64 * 2 + 1]).await;
    // The directory stays drained by bounded shard batches: no unbounded
    // history and no full rescan per batch.
    let retained = directory_len(&f).await;
    assert!(
        retained < 128,
        "ended entries must be reclaimed in bounded batches, directory holds {retained}"
    );
    assert_eq!(
        f.board
            .get(&numbered_did(rounds as u64 * 2 + 1))
            .await
            .unwrap()
            .unwrap()
            .view
            .execution
            .phase,
        DispatchPhase::Created
    );
}

/// `reclaim_before_scan_cursor_survives_takeover`: the scan is paused at the
/// legacy cursor (old cut=100/next=80), the first 30 ended entries are
/// reclaimed, the provider restarts: no rollback, no ended item resurrected,
/// every surviving unfinished item still discovered, and the round resumes
/// under the versioned cursor re-establishment.
#[tokio::test]
async fn reclaim_before_scan_cursor_survives_takeover() {
    let f = Fixture::new().await;
    let numbers: Vec<u64> = (1..=100).collect();
    create_numbered(&f, 1, &numbers).await;
    for number in 1..=30 {
        settle(&f, &numbered_did(number)).await;
    }
    // The paused legacy cursor: mid-round at position 80 of a 100 cut.
    f.base
        .storage
        .create_document(
            &f.base.layout.dispatch_scan(ShardId(42)),
            &serde_json::json!({
                "schema_version": 1,
                "next": 80,
                "cut": 100,
                "pending": 0,
                "round": 9,
                "work": null,
            }),
        )
        .await
        .unwrap();
    // Reclaim the first 30 ended entries in bounded shard batches.
    let mut removed = 0;
    for _ in 0..16 {
        removed += f
            .board
            .reclaim_dispatch_directory(ShardId(42))
            .await
            .unwrap();
        if removed >= 30 {
            break;
        }
    }
    assert!(
        removed >= 30,
        "the 30 ended-and-proved entries must be reclaimable, removed {removed}"
    );
    let reclaimed: Vec<Did> = (1..=30).map(numbered_did).collect();
    let survivors: Vec<Did> = (31..=100).map(numbered_did).collect();
    // Restart (a new provider over the same durable stores).
    let board = f.reopen();
    let mut items = vec![];
    for _ in 0..4096 {
        let page = board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap();
        let advanced = !page.items.is_empty();
        items.extend(page.items);
        if page.next.is_none() {
            break;
        }
        if !advanced {
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    }
    let seen: std::collections::BTreeSet<_> = items.iter().collect();
    for reclaimed_did in &reclaimed {
        assert!(
            !seen.contains(&reclaimed_did),
            "reclaimed dispatch {} must not resurrect",
            reclaimed_did.0
        );
    }
    for survivor in &survivors {
        assert!(
            seen.contains(&survivor),
            "unfinished dispatch {} must not be skipped by the takeover",
            survivor.0
        );
    }
    // The next round still arms and discovers newly added work.
    create_numbered(&f, 2, &[101]).await;
    let mut found = false;
    for _ in 0..256 {
        let page = board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap();
        if page.items.contains(&numbered_did(101)) {
            found = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
    assert!(
        found,
        "a post-takeover round must discover new directory keys"
    );
    assert!(
        directory_len(&f).await <= 71,
        "the reclaimed entries stay reclaimed after the takeover"
    );
}

/// Retained ended dispatches keep fencing late writes and same-did rebuilds
/// after their directory entry is reclaimed — late commands answer from the
/// durable terminal outcome, and a CreateDispatch for the same did never
/// re-adds a discovery row.
#[tokio::test]
async fn reclaimed_dispatches_fence_late_writes_and_same_did_rebuilds() {
    let f = Fixture::new().await;
    create_numbered(&f, 1, &[1, 2]).await;
    let first = numbered_did(1);
    let second = numbered_did(2);
    settle(&f, &first).await;
    settle(&f, &second).await;
    let mut removed = 0;
    for _ in 0..8 {
        removed += f
            .board
            .reclaim_dispatch_directory(ShardId(42))
            .await
            .unwrap();
        if removed >= 2 {
            break;
        }
    }
    assert!(removed >= 2, "both settled entries must be reclaimable");
    let entries_before = journal(&f).await["directory"].as_array().unwrap().len();
    assert!(
        entries_before == 0,
        "the reclaim must have drained both settled rows"
    );
    // The retained document still exposes the terminal evidence.
    let record = f.board.get(&first).await.unwrap().unwrap();
    assert!(record.terminal_evidence.is_some());
    // A late cancel answers with the retained terminal outcome, not an error.
    let cancel = CancelIntent {
        request_id: RequestId("late-cancel".into()),
        reason: Some("late".into()),
    };
    let sources = f
        .base
        .commit(
            2,
            vec![EffectKind::RequestCancel {
                did: first.clone(),
                intent: cancel.clone(),
            }],
        )
        .await;
    assert_eq!(
        f.board
            .request_cancel(CommittedCancelIntent {
                source: sources[0].clone(),
                did: first.clone(),
                intent: cancel,
            })
            .await
            .unwrap(),
        ControlOutcome::SupersededTerminal {
            outcome: FinalDispatchOutcome::Completed,
            evidence: format!("completed-{}", first.0),
        }
    );
    // Same-did rebuild: the retained document keeps it a conflict, and no new
    // directory row may appear.
    let sources = f
        .base
        .commit(
            3,
            vec![EffectKind::CreateDispatch {
                spec: numbered_spec(1),
            }],
        )
        .await;
    assert_eq!(
        f.board
            .create(CreateCommittedDispatch {
                source: sources[0].clone(),
                spec: numbered_spec(1),
            })
            .await
            .unwrap(),
        IdempotentWrite::ContentConflict,
        "a reclaimed did must never rebuild"
    );
    let entries_after = journal(&f).await["directory"].as_array().unwrap().len();
    assert_eq!(
        entries_after, entries_before,
        "a rejected rebuild must not consume a reclaimed directory slot"
    );
}

/// `未知项未删`: entries that are not provably ended-and-transferred are
/// never removed — unfinished dispatches, ended dispatches still under
/// recheck supervision, and dispatches with unresolved obligations stay in
/// the directory.
#[tokio::test]
async fn reclaim_keeps_entries_with_unresolved_obligations_or_unknown_outcomes() {
    let f = Fixture::new().await;
    create_numbered(&f, 1, &[1, 2, 3, 4]).await;
    // 2: ended with evidence but still under recheck supervision (due kept).
    let second = numbered_did(2);
    {
        let mut held = match f
            .queue
            .claim(DispatchClaimRequest {
                did: second.clone(),
                me: me(),
                request_id: RequestId("claim-2".into()),
            })
            .await
            .unwrap()
        {
            ClaimOutcome::Acquired(held) => held,
            _ => panic!("claim failed"),
        };
        held.finish(FinalDispatchOutcome::Completed, "completed-2".into())
            .await
            .unwrap();
    }
    // 3: ended and settled, but still holds an unconfirmed queued input.
    let third = numbered_did(3);
    {
        let input = PendingInput {
            seq: InputSeq(1),
            input_id: InputId("queued-1".into()),
            business_digest: Digest("digest-1".into()),
            payload: Payload::Inline(serde_json::json!({"text": 1})),
        };
        let sources = f
            .base
            .commit(
                2,
                vec![EffectKind::PushInput {
                    did: third.clone(),
                    input: input.clone(),
                }],
            )
            .await;
        f.board
            .push_input(CommittedDispatchInput {
                source: sources[0].clone(),
                did: third.clone(),
                input,
            })
            .await
            .unwrap();
        let mut held = match f
            .queue
            .claim(DispatchClaimRequest {
                did: third.clone(),
                me: me(),
                request_id: RequestId("claim-3".into()),
            })
            .await
            .unwrap()
        {
            ClaimOutcome::Acquired(held) => held,
            _ => panic!("claim failed"),
        };
        held.finish(FinalDispatchOutcome::Completed, "completed-3".into())
            .await
            .unwrap();
        let record = f.board.get(&third).await.unwrap().unwrap();
        assert_eq!(
            f.board
                .reschedule(&third, record.revision, TimestampMillis(9))
                .await
                .unwrap(),
            RecheckOutcome::Ended
        );
    }
    // 4: fully settled.
    settle(&f, &numbered_did(4)).await;
    let mut removed = 0;
    for _ in 0..8 {
        removed += f
            .board
            .reclaim_dispatch_directory(ShardId(42))
            .await
            .unwrap();
    }
    assert_eq!(removed, 1, "only the fully settled entry may be reclaimed");
    let doc = journal(&f).await;
    let dids: Vec<String> = doc["directory"]
        .as_array()
        .unwrap()
        .iter()
        .map(|entry| entry["did"].as_str().unwrap().to_string())
        .collect();
    for kept in [&numbered_did(1), &second, &third] {
        assert!(
            dids.contains(&kept.0),
            "unresolved dispatch {} must never be reclaimed",
            kept.0
        );
    }
    assert!(!dids.contains(&numbered_did(4).0));
}

/// The legacy `cut/next` cursor format is explicitly versioned: on upgrade
/// the scan re-establishes its bounds in the protected section (a fresh
/// round over durable keys) instead of guessing index equivalence, and the
/// re-scan is idempotent.
#[tokio::test]
async fn legacy_cursor_arms_a_fresh_idempotent_round_instead_of_guessing_indexes() {
    let f = Fixture::new().await;
    create_numbered(&f, 1, &[1, 2, 3]).await;
    f.base
        .storage
        .create_document(
            &f.base.layout.dispatch_scan(ShardId(42)),
            &serde_json::json!({
                "schema_version": 1,
                "next": 1,
                "cut": 2,
                "pending": 0,
                "round": 3,
                "work": null,
            }),
        )
        .await
        .unwrap();
    let mut expected = vec![numbered_did(1), numbered_did(2), numbered_did(3)];
    expected.sort();
    let first = drive_scan_to_completion(&f).await;
    let mut sorted = first;
    sorted.sort();
    assert_eq!(sorted, expected, "the re-established round misses nothing");
    let raw = f
        .base
        .storage
        .read_document(&f.base.layout.dispatch_scan(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        raw.value["cursor_version"],
        2,
        "the restarted scan must persist its explicit stable-key cursor version"
    );
    assert_eq!(
        raw.value["upper"], 3,
        "the re-established round carries the durable per-round upper key"
    );
    // Idempotent re-scan: the same round content re-appears without any
    // rollback or duplicated positional assumption.
    let second = drive_scan_to_completion(&f).await;
    let mut sorted = second;
    sorted.sort();
    assert_eq!(sorted, expected);
}
