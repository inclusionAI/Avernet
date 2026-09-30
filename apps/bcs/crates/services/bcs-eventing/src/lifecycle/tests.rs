#![allow(clippy::expect_used, clippy::unwrap_used)]

use super::*;
use std::collections::VecDeque;
use tokio::sync::mpsc;
use tokio::time::Instant;

#[tokio::test(start_paused = true)]
async fn idle_backoff_is_bounded_and_work_or_errors_restore_base_interval() {
    let cancel = CancellationToken::new();
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut outcomes = VecDeque::from([Ok(0), Ok(0), Ok(1), Err("database unavailable"), Ok(0)]);
    let handle = tokio::spawn(worker_loop(
        cancel.clone(), Duration::from_millis(200), Duration::from_millis(1000), "test",
        move || {
            let outcome = outcomes.pop_front().unwrap_or(Ok(0));
            tx.send(Instant::now()).unwrap();
            async move { outcome }
        },
    ));
    let first = rx.recv().await.unwrap();
    let second = rx.recv().await.unwrap();
    let third = rx.recv().await.unwrap();
    let fourth = rx.recv().await.unwrap();
    let fifth = rx.recv().await.unwrap();
    assert!((Duration::from_millis(300)..=Duration::from_millis(400)).contains(&(second - first)));
    assert!((Duration::from_millis(600)..=Duration::from_millis(800)).contains(&(third - second)));
    assert_eq!(fourth - third, Duration::from_millis(200), "work restores base polling");
    assert_eq!(fifth - fourth, Duration::from_millis(200), "an error is not an empty claim");
    let mut previous = fifth;
    for _ in 0..12 {
        let current = rx.recv().await.unwrap();
        assert!(current - previous <= Duration::from_millis(1000));
        previous = current;
    }
    cancel.cancel();
    handle.await.unwrap();
    assert_eq!(Instant::now(), previous, "shutdown cancels an idle wait immediately");
}

#[tokio::test(start_paused = true)]
async fn fixed_polling_keeps_legacy_cadence_and_pending_work_is_processed_after_idle() {
    let cancel = CancellationToken::new();
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut polls = 0;
    let handle = tokio::spawn(worker_loop(
        cancel.clone(), Duration::from_millis(200), Duration::from_millis(200), "test",
        move || {
            polls += 1;
            let count = usize::from(polls == 8);
            tx.send((Instant::now(), count)).unwrap();
            async move { Ok::<_, &'static str>(count) }
        },
    ));
    let (first, _) = rx.recv().await.unwrap();
    for i in 1..10 {
        let (at, count) = rx.recv().await.unwrap();
        assert_eq!(at - first, Duration::from_millis(200 * i));
        assert_eq!(count, usize::from(i == 7));
    }
    cancel.cancel();
    handle.await.unwrap();
}

#[test]
fn jitter_respects_the_ceiling_and_fixed_polling_does_not_jitter() {
    let base = Duration::from_millis(200);
    for sample in [0, 1, 999, 1000, u64::MAX] {
        let mut idle = IdleBackoff::new(base, Duration::from_millis(1000));
        for _ in 0..100 {
            let delay = idle.next_delay(true, sample);
            assert!(delay >= base && delay <= Duration::from_millis(1000));
        }
        assert_eq!(idle.next_delay(false, sample), base);
        let mut fixed = IdleBackoff::new(base, base);
        assert_eq!(fixed.next_delay(true, sample), base);
    }
}
