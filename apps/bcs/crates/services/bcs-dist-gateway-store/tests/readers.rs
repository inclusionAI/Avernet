//! follow: the shared per-session reader, catch-up, filtering and slow subscribers.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

mod common;

use bcs_dist_gateway_store::SUBSCRIBER_LAGGED;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::task_feed::*;
use bcs_store_api::stream::StreamId;
use common::*;
use futures::StreamExt;
use futures::stream::BoxStream;
use std::collections::BTreeMap;
use std::sync::atomic::Ordering;
use std::time::Duration;

type Follow = BoxStream<'static, Result<FollowItem, StoreError>>;

fn cursor(tid: &str, value: &str) -> TaskCursor {
    TaskCursor {
        sid: Sid(SID.into()),
        tid: Tid(tid.into()),
        token: ReplayToken {
            backend: BackendId("tbase".into()),
            generation: LogGeneration("1".into()),
            value: value.into(),
        },
    }
}

async fn next_event(follow: &mut Follow) -> (EventId, String) {
    let item = tokio::time::timeout(Duration::from_secs(5), follow.next())
        .await
        .expect("an event in time")
        .expect("stream still open")
        .expect("no error");
    match item {
        FollowItem::Events { items, next } => {
            assert_eq!(items.len(), 1, "one event per item, so each has its own cursor");
            (items[0].id, next.token.value)
        }
        FollowItem::Gap => panic!("unexpected gap"),
    }
}

async fn ids(follow: &mut Follow, n: usize) -> Vec<String> {
    let mut out = Vec::new();
    for _ in 0..n {
        out.push(next_event(follow).await.1);
    }
    out
}

async fn quiet(follow: &mut Follow) {
    assert!(
        tokio::time::timeout(Duration::from_millis(150), follow.next()).await.is_err(),
        "no further item expected"
    );
}

#[tokio::test]
async fn follow_delivers_later_events_of_the_task_with_their_cursors() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Working, Some(eid(1, 1)), 1)).await;
    f.worker.create(&snapshot(OTHER_TID, TaskStatus::Completed, None, 0)).await;
    f.worker.append(&status_event(TID, eid(1, 1), TaskStatus::Working)).await;
    f.worker.append(&text_event(OTHER_TID, eid(2, 1), "other task")).await;
    f.worker.append(&text_event(TID, eid(2, 2), "a")).await;

    let mut follow = f.reader.follow(scope(), cursor(TID, "1-1"));
    assert_eq!(ids(&mut follow, 1).await, ["2-2"]);
    f.worker.append(&text_event(TID, eid(3, 1), "b")).await;
    f.worker.append(&text_event(OTHER_TID, eid(3, 2), "other task")).await;
    f.worker.append(&status_event(TID, eid(4, 1), TaskStatus::InputRequired)).await;
    assert_eq!(ids(&mut follow, 2).await, ["3-1", "4-1"]);
    quiet(&mut follow).await;
    assert_eq!(f.stream.multi_key_reads.load(Ordering::SeqCst), 0, "one key per blocking read");
}

#[tokio::test]
async fn a_task_without_coverage_is_followed_from_the_first_entry() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Submitted, None, 1)).await;
    f.worker.append(&text_event(OTHER_TID, eid(1, 1), "earlier task")).await;
    f.worker.append(&status_event(TID, eid(2, 1), TaskStatus::Working)).await;
    let mut follow = f.reader.follow(scope(), cursor(TID, "0-0"));
    assert_eq!(ids(&mut follow, 1).await, ["2-1"]);
}

#[tokio::test]
async fn subscribers_of_one_session_share_one_reader() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Working, None, 1)).await;
    let mut a = f.reader.follow(scope(), cursor(TID, "0-0"));
    f.worker.append(&text_event(TID, eid(1, 1), "a")).await;
    assert_eq!(ids(&mut a, 1).await, ["1-1"]);
    assert_eq!(f.reader.served_sessions(), 1);

    let mut b = f.reader.follow(scope(), cursor(TID, "1-1"));
    let mut c = f.reader.follow(scope(), cursor(TID, "0-0"));
    f.worker.append(&text_event(TID, eid(2, 1), "b")).await;
    assert_eq!(ids(&mut a, 1).await, ["2-1"]);
    assert_eq!(ids(&mut b, 1).await, ["2-1"]);
    // The late one catches up on what the reader already passed, without moving it.
    assert_eq!(ids(&mut c, 2).await, ["1-1", "2-1"]);
    assert_eq!(f.reader.served_sessions(), 1, "still one blocking read for the session");
    assert!(f.stream.ranges.load(Ordering::SeqCst) >= 1, "the late subscriber used a catch-up read");
}

#[tokio::test]
async fn a_late_subscriber_sees_no_gap_and_no_duplicate_while_entries_are_written() {
    let f = fixture_with(|c| c.read_batch = std::num::NonZeroU32::new(7).unwrap());
    f.worker.create(&snapshot(TID, TaskStatus::Working, None, 1)).await;
    let mut first = f.reader.follow(scope(), cursor(TID, "0-0"));
    let total = 60u64;
    let mut late = None;
    for n in 1..=total {
        f.worker.append(&text_event(TID, eid(n, 1), "x")).await;
        if n == 20 {
            // Join in the middle, behind the shared reader.
            assert_eq!(next_event(&mut first).await.1, "1-1");
            late = Some(f.reader.follow(scope(), cursor(TID, "3-1")));
        }
        if n % 9 == 0 {
            tokio::task::yield_now().await;
        }
    }
    let mut late = late.unwrap();
    let expected: Vec<String> = (4..=total).map(|n| format!("{n}-1")).collect();
    assert_eq!(ids(&mut late, expected.len()).await, expected);
    quiet(&mut late).await;
    let expected_first: Vec<String> = (2..=total).map(|n| format!("{n}-1")).collect();
    assert_eq!(ids(&mut first, expected_first.len()).await, expected_first);
}

#[tokio::test]
async fn a_slow_subscriber_is_closed_and_the_others_continue() {
    let f = fixture_with(|c| {
        c.subscriber_buffer = 2;
        c.read_batch = std::num::NonZeroU32::new(1).unwrap();
    });
    f.worker.create(&snapshot(TID, TaskStatus::Working, None, 1)).await;
    let mut slow = f.reader.follow(scope(), cursor(TID, "0-0"));
    let mut fast = f.reader.follow(scope(), cursor(TID, "0-0"));
    f.worker.append(&text_event(TID, eid(1, 1), "x")).await;
    // Both are live now.
    assert_eq!(ids(&mut slow, 1).await, ["1-1"]);
    assert_eq!(ids(&mut fast, 1).await, ["1-1"]);
    for n in 2..=12u64 {
        f.worker.append(&text_event(TID, eid(n, 1), "x")).await;
        assert_eq!(ids(&mut fast, 1).await, [format!("{n}-1")]);
    }
    let lagged = tokio::time::timeout(Duration::from_secs(5), slow.next()).await.unwrap().unwrap();
    assert_eq!(
        lagged,
        Err(StoreError::NotApplied {
            code: SUBSCRIBER_LAGGED.into()
        })
    );
    assert!(slow.next().await.is_none(), "the slow stream ends");
    f.worker.append(&text_event(TID, eid(13, 1), "x")).await;
    assert_eq!(ids(&mut fast, 1).await, ["13-1"]);
}

#[tokio::test]
async fn the_reader_stops_after_the_last_subscriber_leaves() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Working, None, 1)).await;
    let mut a = f.reader.follow(scope(), cursor(TID, "0-0"));
    f.worker.append(&text_event(TID, eid(1, 1), "x")).await;
    ids(&mut a, 1).await;
    assert_eq!(f.reader.served_sessions(), 1);
    drop(a);
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert_eq!(f.reader.served_sessions(), 0);
    let reads = f.stream.blocking_reads.load(Ordering::SeqCst);
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert_eq!(f.stream.blocking_reads.load(Ordering::SeqCst), reads, "no read without subscribers");

    // A new subscriber starts a new reader.
    let mut b = f.reader.follow(scope(), cursor(TID, "1-1"));
    f.worker.append(&text_event(TID, eid(2, 1), "y")).await;
    assert_eq!(ids(&mut b, 1).await, ["2-1"]);
}

#[tokio::test]
async fn undecodable_entries_are_skipped_and_the_stream_goes_on() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Working, None, 1)).await;
    let mut follow = f.reader.follow(scope(), cursor(TID, "0-0"));
    let mut newer = BTreeMap::new();
    newer.insert("v".to_owned(), b"2".to_vec());
    newer.insert("tid".to_owned(), TID.as_bytes().to_vec());
    newer.insert("kind".to_owned(), b"future".to_vec());
    newer.insert("body".to_owned(), b"{}".to_vec());
    f.worker.append_raw(StreamId { major: 1, minor: 1 }, newer).await;
    f.worker.append(&text_event(TID, eid(2, 1), "ok")).await;
    assert_eq!(ids(&mut follow, 1).await, ["2-1"]);
}

#[tokio::test]
async fn a_cursor_that_cannot_be_followed_is_a_gap_or_an_error() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Working, None, 1)).await;
    let mut other_backend = cursor(TID, "1-1");
    other_backend.token.backend = BackendId("other".into());
    let mut follow = f.reader.follow(scope(), other_backend);
    assert_eq!(follow.next().await, Some(Ok(FollowItem::Gap)));
    assert!(follow.next().await.is_none());

    let mut foreign = f.reader.follow(other_scope(), cursor(TID, "1-1"));
    assert_eq!(
        foreign.next().await,
        Some(Err(StoreError::Invalid {
            code: "cursor_scope_mismatch".into()
        }))
    );
    assert!(foreign.next().await.is_none());
    assert_eq!(f.reader.served_sessions(), 0, "no reader was started for them");
}

#[tokio::test]
async fn repeated_read_failures_end_the_subscribers_with_the_error() {
    let f = fixture();
    f.worker.create(&snapshot(TID, TaskStatus::Working, None, 1)).await;
    f.stream.fail_reads.store(true, Ordering::SeqCst);
    let mut follow = f.reader.follow(scope(), cursor(TID, "0-0"));
    let item = tokio::time::timeout(Duration::from_secs(5), follow.next()).await.unwrap();
    assert_eq!(
        item,
        Some(Err(StoreError::NotApplied {
            code: "store_not_applied".into()
        }))
    );
    assert!(follow.next().await.is_none());
    assert_eq!(f.reader.served_sessions(), 0);
}
