#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "provider fault tests"
)]
mod common;
mod dispatch_support;
mod task_feed_support;
use bcs_dist_model::*;
use bcs_dist_store::dispatch::DispatchConfig;
use bcs_service_api::port::repo::distributed::{common::*, dispatch::*, session::*};
use bcs_store_api::document::DocumentStorePlugin;
use dispatch_support::*;
use std::{num::NonZeroU32, time::Duration};

#[tokio::test]
async fn every_create_lost_reply_preserves_the_original_identity() {
    for lost in 1..=4 {
        let f = Fixture::new().await;
        let spec = spec();
        let source = f
            .base
            .commit(1, vec![EffectKind::CreateDispatch { spec: spec.clone() }])
            .await
            .remove(0);
        let request = CreateCommittedDispatch { source, spec };
        f.base.storage.lose_write(lost);
        let _ = f.board.create(request.clone()).await;
        assert!(matches!(
            f.reopen().create(request.clone()).await.unwrap(),
            IdempotentWrite::Applied | IdempotentWrite::AlreadyApplied
        ));
        f.base.commit(2, vec![]).await;
        assert_eq!(
            f.reopen().create(request).await.unwrap(),
            IdempotentWrite::AlreadyApplied
        );
    }
}
#[tokio::test]
async fn receipt_recheck_covers_outbox_advance_after_initial_absent_read() {
    let f = Fixture::new().await;
    let spec = spec();
    let source = f
        .base
        .commit(1, vec![EffectKind::CreateDispatch { spec: spec.clone() }])
        .await
        .remove(0);
    let request = CreateCommittedDispatch { source, spec };
    let pause = f.base.storage.pause_after(
        "json.get",
        &f.base.layout.disp(&request.spec.did).unwrap(),
        1,
    );
    let board = f.reopen();
    let pending_request = request.clone();
    let pending = tokio::spawn(async move { board.create(pending_request).await });
    pause.reached().await;
    f.board.create(request).await.unwrap();
    f.base.commit(2, vec![]).await;
    pause.resume();
    assert_eq!(
        pending.await.unwrap().unwrap(),
        IdempotentWrite::AlreadyApplied
    );
}
#[tokio::test]
async fn fabricated_proof_does_not_authorize_a_dispatch() {
    let f = Fixture::new().await;
    let source = f
        .base
        .commit(1, vec![EffectKind::CreateDispatch { spec: spec() }])
        .await
        .remove(0);
    f.base.commit(2, vec![]).await;
    assert!(matches!(
        f.board
            .create(CreateCommittedDispatch {
                source,
                spec: spec()
            })
            .await,
        Err(StoreError::Invalid { .. })
    ));
    assert!(f.board.get(&spec().did).await.unwrap().is_none());
}
#[tokio::test]
async fn cancel_unknown_then_lost_write_replay_keeps_receipt_after_terminal() {
    for lost in 1..=3 {
        let f = Fixture::new().await;
        f.create().await;
        let cancel = f.cancel(2).await;
        f.base.storage.lose_write(lost);
        let _ = f.board.request_cancel(cancel.clone()).await;
        f.reopen().request_cancel(cancel.clone()).await.unwrap();
        let mut h = f.claim().await;
        h.record_cancel(CancelObservation::OutcomeUnknown)
            .await
            .unwrap();
        assert_ne!(
            h.refresh().await.unwrap().execution.phase,
            DispatchPhase::Ended
        );
        h.finish(FinalDispatchOutcome::Completed, "remote-event-5".into())
            .await
            .unwrap();
        f.base.commit(3, vec![]).await;
        assert_eq!(
            f.reopen().request_cancel(cancel).await.unwrap(),
            ControlOutcome::AlreadyApplied
        );
    }
}
#[tokio::test]
async fn command_ack_retries_are_scoped_and_do_not_exhaust_the_feed() {
    let f = Fixture::new().await;
    f.create().await;
    let mut feed = f.queue.notifications(spec().pool, me()).await.unwrap();
    for _ in 0..130 {
        f.board.notify(&spec().pool, &spec().did).await.unwrap();
        let delivery = feed
            .receive(NonZeroU32::MIN, Duration::from_millis(50))
            .await
            .unwrap()
            .remove(0);
        feed.ack(&delivery).await.unwrap();
        feed.ack(&delivery).await.unwrap();
    }
    let mut other = f
        .queue
        .notifications(
            spec().pool,
            Instance {
                incarnation: "two".into(),
                ..me()
            },
        )
        .await
        .unwrap();
    f.board.notify(&spec().pool, &spec().did).await.unwrap();
    let delivery = feed
        .receive(NonZeroU32::MIN, Duration::from_millis(50))
        .await
        .unwrap()
        .remove(0);
    assert!(other.ack(&delivery).await.is_err());
}
#[tokio::test]
async fn receipt_capacity_is_reserved_before_an_intent_and_cancel_has_headroom() {
    let f = Fixture::new().await;
    f.create().await;
    let board = build(
        &f.base,
        f.membership.clone(),
        DispatchConfig {
            max_receipts: 18,
            ..DispatchConfig::default()
        },
    );
    let first = f.input(2, 1).await;
    board.push_input(first.clone()).await.unwrap();
    let second = f.input(3, 2).await;
    assert!(matches!(
        board.push_input(second).await,
        Err(StoreError::NotApplied { .. })
    ));
    let cancel = f.cancel(4).await;
    assert_eq!(
        board.request_cancel(cancel).await.unwrap(),
        ControlOutcome::Applied
    );
    assert_eq!(
        board.push_input(first).await.unwrap(),
        ControlOutcome::AlreadyApplied
    );
    let root = f
        .base
        .storage
        .read_document(&f.base.layout.dispatch_journal(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert!(root.value["pending"].as_array().unwrap().is_empty());
}
#[tokio::test]
async fn independently_scanning_restarts_rotate_past_unreadable_dispatches() {
    let f = Fixture::new().await;
    for seq in 1..=4 {
        let mut spec = spec();
        spec.did = Did(format!("{}.d{seq}", common::TID));
        let source = f
            .base
            .commit(seq, vec![EffectKind::CreateDispatch { spec: spec.clone() }])
            .await
            .remove(0);
        f.board
            .create(CreateCommittedDispatch { source, spec })
            .await
            .unwrap();
    }
    let mut seen = std::collections::BTreeSet::new();
    for _ in 0..12 {
        f.base
            .storage
            .fail_document_read(&f.base.layout.disp(&spec().did).unwrap());
        let page = f
            .reopen()
            .scan_due(
                ShardId(42),
                TimestampMillis(1),
                PageRequest {
                    after: None,
                    limit: NonZeroU32::MIN,
                },
            )
            .await
            .unwrap();
        seen.extend(page.items.into_iter().map(|d| d.0));
    }
    assert_eq!(seen.len(), 3);
}

#[tokio::test]
async fn scan_persists_partial_phase_progress_at_thirty_ms_per_document_io() {
    let f = Fixture::new().await;
    for seq in 1..=5 {
        let mut spec = spec();
        spec.did = Did(format!("{}.d{seq}", common::TID));
        let source = f
            .base
            .commit(seq, vec![EffectKind::CreateDispatch { spec: spec.clone() }])
            .await
            .remove(0);
        f.board
            .create(CreateCommittedDispatch { source, spec })
            .await
            .unwrap();
    }
    let mut seen = std::collections::BTreeSet::new();
    for _ in 0..20 {
        let board = bcs_dist_store::dispatch::StoreDispatch::new(
            bcs_dist_store::dispatch::DispatchStores {
                scan_budget: std::sync::Arc::new(
                    bcs_dist_store::budget::SharedScanBudget::new(
                        bcs_dist_store::budget::ScanBudgetConfig::default(),
                    )
                    .unwrap(),
                ),
                documents: std::sync::Arc::new(SlowDocuments {
                    inner: f.base.storage.clone(),
                    delay: Duration::from_millis(30),
                }),
                streams: f.base.storage.clone(),
                index: f.base.storage.clone(),
                membership: f.membership.clone(),
            },
            f.base.layout.clone(),
            DispatchConfig::default(),
        )
        .unwrap();
        let result = board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap();
        seen.extend(result.items.into_iter().map(|d| d.0));
        if seen.len() == 5 {
            break;
        }
    }
    assert_eq!(
        seen.len(),
        5,
        "bounded slices must not discard a fixed tail on every round"
    );
}

#[tokio::test]
async fn duplicate_notifications_each_ack_their_physical_id() {
    use bcs_store_api::stream::StreamStorePlugin;
    let f = Fixture::new().await;
    f.create().await;
    let mut feed = f.queue.notifications(spec().pool, me()).await.unwrap();
    for _ in 0..3 {
        f.board.notify(&spec().pool, &spec().did).await.unwrap();
    }
    let first = feed
        .receive(NonZeroU32::new(128).unwrap(), Duration::from_millis(50))
        .await
        .unwrap();
    assert_eq!(first.len(), 3);
    assert!(first.iter().all(|delivery| delivery.did == spec().did));
    assert_eq!(
        first
            .iter()
            .map(|delivery| &delivery.opaque)
            .collect::<std::collections::BTreeSet<_>>()
            .len(),
        3
    );
    for delivery in &first {
        feed.ack(delivery).await.unwrap();
    }
    let remaining = f
        .base
        .storage
        .claim_idle(
            &f.base.layout.command(&spec().pool, 0).unwrap(),
            bcs_dist_store::command::GROUP,
            "probe",
            Duration::ZERO,
            None,
            NonZeroU32::new(128).unwrap(),
        )
        .await
        .unwrap();
    assert!(remaining.entries.is_empty());
}

#[tokio::test]
async fn pending_redelivery_does_not_spawn_a_second_local_delivery() {
    let f = Fixture::new().await;
    f.create().await;
    let mut feed = f.queue.notifications(spec().pool, me()).await.unwrap();
    f.board.notify(&spec().pool, &spec().did).await.unwrap();
    let original = feed
        .receive(NonZeroU32::MIN, Duration::from_millis(50))
        .await
        .unwrap();
    f.base.storage.model.advance(Duration::from_secs(31));
    assert!(
        feed.receive(NonZeroU32::MIN, Duration::from_millis(50))
            .await
            .unwrap()
            .is_empty()
    );
    feed.ack(&original[0]).await.unwrap();
}
#[tokio::test]
async fn slow_scan_repairs_an_intent_without_restarting_its_completed_prefix() {
    let f = Fixture::new().await;
    let source = f
        .base
        .commit(1, vec![EffectKind::CreateDispatch { spec: spec() }])
        .await
        .remove(0);
    f.base.storage.fail_write(2);
    assert!(
        f.board
            .create(CreateCommittedDispatch {
                source,
                spec: spec()
            })
            .await
            .is_err()
    );
    let mut found = false;
    for _ in 0..16 {
        let board = bcs_dist_store::dispatch::StoreDispatch::new(
            bcs_dist_store::dispatch::DispatchStores {
                scan_budget: std::sync::Arc::new(
                    bcs_dist_store::budget::SharedScanBudget::new(
                        bcs_dist_store::budget::ScanBudgetConfig::default(),
                    )
                    .unwrap(),
                ),
                documents: std::sync::Arc::new(SlowDocuments {
                    inner: f.base.storage.clone(),
                    delay: Duration::from_millis(30),
                }),
                streams: f.base.storage.clone(),
                index: f.base.storage.clone(),
                membership: f.membership.clone(),
            },
            f.base.layout.clone(),
            DispatchConfig::default(),
        )
        .unwrap();
        if board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap()
            .items
            .contains(&spec().did)
        {
            found = true;
            break;
        }
    }
    assert!(found);
    let root = f
        .base
        .storage
        .read_document(&f.base.layout.dispatch_journal(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert!(root.value["pending"].as_array().unwrap().is_empty());
}

#[tokio::test]
async fn one_permanently_hanging_dispatch_does_not_block_healthy_siblings() {
    let f = Fixture::new().await;
    for seq in 1..=3 {
        let mut spec = spec();
        spec.did = Did(format!("{}.d{seq}", common::TID));
        let source = f
            .base
            .commit(seq, vec![EffectKind::CreateDispatch { spec: spec.clone() }])
            .await
            .remove(0);
        f.board
            .create(CreateCommittedDispatch { source, spec })
            .await
            .unwrap();
    }
    let mut seen = std::collections::BTreeSet::new();
    let board = bcs_dist_store::dispatch::StoreDispatch::new(
            bcs_dist_store::dispatch::DispatchStores {
                scan_budget: std::sync::Arc::new(
                    bcs_dist_store::budget::SharedScanBudget::new(
                        bcs_dist_store::budget::ScanBudgetConfig::default(),
                    )
                    .unwrap(),
                ),
                documents: std::sync::Arc::new(HangingDocument {
                    inner: f.base.storage.clone(),
                    key: f.base.layout.disp(&spec().did).unwrap(),
                }),
                streams: f.base.storage.clone(),
                index: f.base.storage.clone(),
                membership: f.membership.clone(),
            },
            f.base.layout.clone(),
            DispatchConfig::default(),
        )
        .unwrap();
    for _ in 0..20 {
        seen.extend(
            board
                .scan_due(ShardId(42), TimestampMillis(1), page())
                .await
                .unwrap()
                .items
                .into_iter()
                .map(|d| d.0),
        );
        if seen.len() == 2 {
            break;
        }
    }
    assert_eq!(seen.len(), 2);
}
#[tokio::test]
async fn unknown_cursor_save_does_not_skip_the_only_dispatch_forever() {
    let f = Fixture::new().await;
    f.create().await;
    f.base.storage.lose_write(1);
    let _ = f
        .board
        .scan_due(ShardId(42), TimestampMillis(1), page())
        .await;
    let mut found = false;
    for _ in 0..4 {
        if f.reopen()
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap()
            .items
            .contains(&spec().did)
        {
            found = true;
            break;
        }
    }
    assert!(found);
}
#[tokio::test]
async fn rejected_input_does_not_reserve_a_sequence_for_future_valid_input() {
    let f = Fixture::new().await;
    f.create().await;
    let first = f.input(2, 1).await;
    f.board.push_input(first.clone()).await.unwrap();
    let mut conflicting = first.input;
    conflicting.seq = InputSeq(2);
    let source = f
        .base
        .commit(
            3,
            vec![EffectKind::PushInput {
                did: spec().did,
                input: conflicting.clone(),
            }],
        )
        .await
        .remove(0);
    assert_eq!(
        f.board
            .push_input(CommittedDispatchInput {
                source,
                did: spec().did,
                input: conflicting
            })
            .await
            .unwrap(),
        ControlOutcome::ContentConflict
    );
    let valid = f.input(4, 2).await;
    assert_eq!(
        f.board.push_input(valid).await.unwrap(),
        ControlOutcome::Applied
    );
}

#[tokio::test]
async fn lost_or_trimmed_command_hints_do_not_remove_independent_recovery() {
    use bcs_store_api::stream::{StreamStorePlugin, TrimPolicy};
    let f = Fixture::new().await;
    f.create().await;
    f.board.notify(&spec().pool, &spec().did).await.unwrap();
    f.base
        .storage
        .trim(
            &f.base.layout.command(&spec().pool, 0).unwrap(),
            TrimPolicy::MaxLen(0),
        )
        .await
        .unwrap();
    let mut feed = f.queue.notifications(spec().pool, me()).await.unwrap();
    assert!(
        feed.receive(NonZeroU32::MIN, Duration::from_millis(50))
            .await
            .unwrap()
            .is_empty()
    );
    assert!(
        f.reopen()
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap()
            .items
            .contains(&spec().did)
    );
    f.base.storage.fail_write(2);
    assert!(matches!(
        f.board.notify(&spec().pool, &spec().did).await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
}

#[tokio::test]
async fn encoded_remote_and_terminal_evidence_respect_reserved_capacity() {
    let f = Fixture::new().await;
    f.create().await;
    let mut held = f.claim().await;
    held.begin_send(start(), proof(&start())).await.unwrap();
    let mut remote = acceptance(start());
    remote.remote.task_id = "\u{0001}".repeat(1024);
    assert!(held.record_acceptance(remote).await.is_err());
    assert!(
        held.finish(FinalDispatchOutcome::Completed, "\u{0001}".repeat(4096))
            .await
            .is_err()
    );
    held.finish(FinalDispatchOutcome::Completed, "\u{0001}".repeat(400))
        .await
        .unwrap();
    let mut stopped = false;
    for seq in 2..=64 {
        let cancel = f.cancel(seq).await;
        match f.board.request_cancel(cancel).await {
            Ok(ControlOutcome::SupersededTerminal { .. }) => {}
            Err(StoreError::NotApplied { .. }) => {
                stopped = true;
                break;
            }
            other => panic!("unexpected: {other:?}"),
        }
    }
    assert!(stopped);
    let root = f
        .base
        .storage
        .read_document(&f.base.layout.dispatch_journal(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert!(root.value["pending"].as_array().unwrap().is_empty());
}
#[tokio::test]
async fn completed_retry_is_not_reported_not_applied_when_hint_read_fails() {
    let f = Fixture::new().await;
    let request = f.create().await;
    let key = f.base.layout.disp(&spec().did).unwrap();
    let pause = f.base.storage.pause_after(
        "document.read",
        &f.base.layout.dispatch_journal(ShardId(42)),
        1,
    );
    let board = f.reopen();
    let retry = tokio::spawn(async move { board.create(request).await });
    pause.reached().await;
    f.base.storage.fail_document_read(&key);
    pause.resume();
    assert_eq!(
        retry.await.unwrap().unwrap(),
        IdempotentWrite::AlreadyApplied
    );
}


fn slow_scan_board(f: &Fixture, delay: Duration) -> bcs_dist_store::dispatch::StoreDispatch {
    use bcs_dist_store::{budget::SharedScanBudget, dispatch::*};
    use std::sync::Arc;
    StoreDispatch::new(
        DispatchStores {
            scan_budget: Arc::new(SharedScanBudget::new(Default::default()).unwrap()),
            documents: Arc::new(SlowDocuments {
                inner: f.base.storage.clone(),
                delay,
            }),
            streams: f.base.storage.clone(),
            index: f.base.storage.clone(),
            membership: f.membership.clone(),
        },
        f.base.layout.clone(),
        DispatchConfig::default(),
    )
    .unwrap()
}

#[tokio::test]
async fn normal_eighty_millisecond_dispatch_reads_make_scan_progress() {
    let f = Fixture::new().await;
    f.create().await;
    let board = slow_scan_board(&f, Duration::from_millis(80));
    let mut found = false;
    let mut errors = 0;
    for _ in 0..12 {
        let start = tokio::time::Instant::now();
        match board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
        {
            Ok(p) => found |= p.items.contains(&spec().did),
            Err(_) => errors += 1,
        }
        assert!(
            start.elapsed() < Duration::from_millis(140),
            "one cooperative slice must stay bounded"
        );
        if found {
            break;
        }
    }
    assert!(found, "no progress after 12 calls, errors={errors}");
}

#[tokio::test]
async fn canceled_scan_keeps_completed_reads_in_the_same_provider() {
    let f = Fixture::new().await;
    f.create().await;
    f.base.storage.clear();
    let journal = f.base.layout.dispatch_journal(ShardId(42));
    let pause = f.base.storage.pause_after("document.read", &journal, 1);
    {
        let scan = f.board.scan_due(ShardId(42), TimestampMillis(1), page());
        tokio::pin!(scan);
        tokio::select! {
            _ = pause.reached() => {},
            result = &mut scan => panic!("finished before cancellation: {result:?}"),
        }
    }
    pause.resume();
    let mut found = false;
    for _ in 0..4 {
        found |= f
            .board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap()
            .items
            .contains(&spec().did);
        if found {
            break;
        }
    }
    assert!(found);
    let reads = f
        .base
        .storage
        .calls()
        .iter()
        .filter(|c| **c == format!("document.read {journal}"))
        .count();
    assert_eq!(
        reads, 1,
        "canceling the caller must retain its completed journal read"
    );
}

#[tokio::test]
async fn canceling_after_an_item_is_staged_keeps_it_for_the_next_poll() {
    let f = Fixture::new().await;
    for seq in 1..=2 {
        let mut spec = spec();
        spec.did = Did(format!("{}.d{seq}", common::TID));
        let source = f
            .base
            .commit(seq, vec![EffectKind::CreateDispatch { spec: spec.clone() }])
            .await
            .remove(0);
        f.board
            .create(CreateCommittedDispatch { source, spec })
            .await
            .unwrap();
    }
    let second = Did(format!("{}.d2", common::TID));
    let pause =
        f.base
            .storage
            .pause_after("json.get", &f.base.layout.disp(&second).unwrap(), 1);
    {
        let scan = f.board.scan_due(ShardId(42), TimestampMillis(1), page());
        tokio::pin!(scan);
        tokio::select! {
            _ = pause.reached() => {},
            result = &mut scan => panic!("finished before cancellation: {result:?}"),
        }
    }
    pause.resume();
    let result = f
        .board
        .scan_due(ShardId(42), TimestampMillis(1), page())
        .await
        .unwrap();
    assert!(
        result.items.contains(&spec().did),
        "the earlier item survived the later await cancellation"
    );
    assert!(result.items.contains(&second));
}

#[tokio::test]
async fn a_new_provider_resumes_the_durable_selected_dispatch_after_cancellation() {
    let f = Fixture::new().await;
    f.create().await;
    let pause = f.base.storage.pause_after(
        "json.get",
        &f.base.layout.disp(&spec().did).unwrap(),
        1,
    );
    let board = f.reopen();
    {
        let scan = board.scan_due(ShardId(42), TimestampMillis(1), page());
        tokio::pin!(scan);
        tokio::select! {
            _ = pause.reached() => {},
            result = &mut scan => panic!("finished before cancellation: {result:?}"),
        }
    }
    drop(board);
    pause.resume();
    let stored = f
        .base
        .storage
        .read_document(&f.base.layout.dispatch_scan(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(stored.value["work"]["phase"], "Read");
    let result = f
        .reopen()
        .scan_due(ShardId(42), TimestampMillis(1), page())
        .await
        .unwrap();
    assert!(result.items.contains(&spec().did));
}

#[tokio::test]
async fn dropping_a_provider_releases_its_retained_scan_and_io_permit() {
    use bcs_dist_store::{budget::SharedScanBudget, dispatch::*};
    use bcs_service_api::port::repo::distributed::budget::ScanBudgetPort;
    use std::sync::Arc;
    let f = Fixture::new().await;
    f.create().await;
    let documents = Arc::new(SlowDocuments {
        inner: f.base.storage.clone(),
        delay: Duration::from_millis(80),
    });
    let weak = Arc::downgrade(&documents);
    let budget = Arc::new(SharedScanBudget::new(Default::default()).unwrap());
    let board = StoreDispatch::new(
        DispatchStores {
            scan_budget: budget.clone(),
            documents: documents.clone(),
            streams: f.base.storage.clone(),
            index: f.base.storage.clone(),
            membership: f.membership.clone(),
        },
        f.base.layout.clone(),
        DispatchConfig::default(),
    )
    .unwrap();
    drop(documents);
    board
        .scan_due(ShardId(42), TimestampMillis(1), page())
        .await
        .unwrap();
    assert_eq!(budget.io_in_use(), Some(1));
    drop(board);
    assert!(
        weak.upgrade().is_none(),
        "the scheduler and retained future must not own each other"
    );
    assert_eq!(budget.io_in_use(), Some(0));
}

#[tokio::test]
async fn retained_dispatch_scans_have_bounded_jobs_and_reclaim_idle_slots() {
    let f = Fixture::new().await;
    let board = slow_scan_board(&f, Duration::from_millis(80));
    tokio::time::pause();
    for shard in [ShardId(41), ShardId(42)] {
        assert!(
            board
                .scan_due(shard, TimestampMillis(1), page())
                .await
                .unwrap()
                .next
                .is_some()
        );
    }
    f.base.storage.clear();
    let waiting = board
        .scan_due(ShardId(43), TimestampMillis(1), page())
        .await
        .unwrap();
    assert!(waiting.next.is_some());
    assert!(
        f.base.storage.calls().is_empty(),
        "a third retained phase may not allocate more IO"
    );
    tokio::time::advance(Duration::from_secs(5)).await;
    let mut complete = false;
    for _ in 0..4 {
        complete |= board
            .scan_due(ShardId(43), TimestampMillis(1), page())
            .await
            .unwrap()
            .next
            .is_none();
        if complete {
            break;
        }
    }
    assert!(
        complete,
        "abandoned shard polls cannot consume the bounded slots forever"
    );
}

#[tokio::test]
async fn delayed_dispatch_scan_keeps_its_fixed_cut_while_the_journal_grows() {
    let f = Fixture::new().await;
    f.create().await;
    let board = slow_scan_board(&f, Duration::from_millis(80));
    for _ in 0..3 {
        assert!(
            board
                .scan_due(ShardId(42), TimestampMillis(1), page())
                .await
                .unwrap()
                .items
                .is_empty()
        );
    }
    let cursor = f
        .base
        .storage
        .read_document(&f.base.layout.dispatch_scan(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(cursor.value["cut"], 1);
    let mut second = spec();
    second.did = Did(format!("{}.d2", common::TID));
    let source = f
        .base
        .commit(
            2,
            vec![EffectKind::CreateDispatch {
                spec: second.clone(),
            }],
        )
        .await
        .remove(0);
    f.board
        .create(CreateCommittedDispatch {
            source,
            spec: second.clone(),
        })
        .await
        .unwrap();
    let mut seen = vec![];
    let mut complete = false;
    for _ in 0..12 {
        let result = board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap();
        seen.extend(result.items);
        if result.next.is_none() {
            complete = true;
            break;
        }
    }
    assert!(complete);
    assert_eq!(seen, vec![spec().did]);
    let mut next_round = false;
    for _ in 0..20 {
        next_round |= board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap()
            .items
            .contains(&second.did);
        if next_round {
            break;
        }
    }
    assert!(next_round);
}


#[tokio::test]
async fn completed_slow_phase_releases_capacity_for_a_waiting_shard() {
    let f = Fixture::new().await;
    let board = slow_scan_board(&f, Duration::from_millis(80));
    tokio::time::pause();
    for shard in [ShardId(41), ShardId(42)] {
        assert!(
            board
                .scan_due(shard, TimestampMillis(1), page())
                .await
                .unwrap()
                .next
                .is_some()
        );
    }
    assert!(
        board
            .scan_due(ShardId(43), TimestampMillis(1), page())
            .await
            .unwrap()
            .next
            .is_some()
    );
    assert!(
        board
            .scan_due(ShardId(41), TimestampMillis(1), page())
            .await
            .unwrap()
            .next
            .is_none()
    );
    let mut finished = false;
    for _ in 0..3 {
        finished |= board
            .scan_due(ShardId(43), TimestampMillis(1), page())
            .await
            .unwrap()
            .next
            .is_none();
        if finished {
            break;
        }
    }
    assert!(
        finished,
        "the waiting shard obtains a released slot without idle eviction"
    );
}
