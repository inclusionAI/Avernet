#![allow(clippy::unwrap_used, reason = "snapshot policy integration assertions")]
mod lifecycle_support;
mod processor_support;
mod support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::task_feed::*;
use lifecycle_support::*;
use processor_support::Fixture;
use std::{sync::atomic::Ordering, time::Duration};

async fn append_and_settle(
    f: &Fixture,
    p: &bcs_dist_worker::WorkerProcessor,
    tid: &Tid,
    seq: u64,
    value: &str,
) {
    delta(f, tid, seq, value).await;
    for _ in 0..200 {
        drive(f, p, 1).await;
        let checkpoint = f.checkpoint().await;
        if checkpoint
            .dispatch_seq
            .get(&dispatch_id(tid))
            .is_some_and(|p| p.sequence == InputSeq(seq))
        {
            drive(f, p, 20).await;
            return;
        }
    }
    panic!("delta {seq} was not committed");
}
fn text(view: TaskView) -> String {
    let Payload::Inline(raw) = view.content.snapshot else {
        panic!("restore must resolve snapshot")
    };
    let display: DisplaySnapshot = serde_json::from_value(raw).unwrap();
    display.units[0].content.as_str().unwrap().to_owned()
}

#[tokio::test(start_paused = true)]
async fn count_policy_publishes_at_128_committed_deltas_without_clock_progress() {
    let runner = Runner::new(Mode::Stream);
    let f = Fixture::new(runner.clone()).await;
    let output = Publisher::new(&f);
    let p = processor(&f, runner.clone(), output.clone(), f.archive.clone());
    let tid = f.admit_for(session(), "count").await;
    f.warm().await;
    until_status(&f, &p, &tid, TaskStatus::Working).await;
    drive(&f, &p, 10).await;
    assert_eq!(output.publications().len(), 1);
    let first_cut = f.snapshot(&tid).await.snapshot_event_id;
    for seq in 1..=127 {
        append_and_settle(&f, &p, &tid, seq, "x").await;
    }
    assert_eq!(runner.calls(), 128);
    let cuts = output.publications();
    assert_eq!(
        cuts.len(),
        1,
        "cuts {:?}",
        cuts.iter()
            .map(|r| (r.source.commit.sequence, r.snapshot.snapshot_event_id))
            .collect::<Vec<_>>()
    );
    let cp = f.checkpoint().await;
    let progress = &cp.tasks.iter().find(|t| t.tid == tid).unwrap().publication;
    assert_eq!(progress.pending_events, 127);
    assert!(progress.pending_bytes < 65_536);
    assert_eq!(f.snapshot(&tid).await.snapshot_event_id, first_cut);
    append_and_settle(&f, &p, &tid, 128, "x").await;
    for _ in 0..100 {
        if output.publications().len() == 2 {
            break;
        }
        drive(&f, &p, 1).await;
    }
    assert_eq!(output.publications().len(), 2);
    assert_eq!(f.snapshot(&tid).await, output.publications()[1].snapshot);
    assert_eq!(f.checkpoint().await.tasks[0].publication.pending_events, 0);
    assert_eq!(f.clock.0.load(Ordering::SeqCst), 1000);
    let Some(RestoredTask::Live { view, .. }) =
        f.feed.restore(&session().scope, &tid).await.unwrap()
    else {
        panic!()
    };
    assert_eq!(text(view), format!("seed{}", "x".repeat(128)));
}

#[tokio::test(start_paused = true)]
async fn encoded_byte_policy_externalizes_large_append_and_publishes_before_count_or_time() {
    let runner = Runner::new(Mode::Stream);
    let f = Fixture::new(runner.clone()).await;
    let output = Publisher::new(&f);
    let p = processor(&f, runner.clone(), output.clone(), f.archive.clone());
    let tid = f.admit_for(session(), "bytes").await;
    f.warm().await;
    until_status(&f, &p, &tid, TaskStatus::Working).await;
    append_and_settle(&f, &p, &tid, 1, &"a".repeat(32_000)).await;
    assert_eq!(output.publications().len(), 1);
    let progress = f.checkpoint().await.tasks[0].publication.clone();
    assert_eq!(progress.pending_events, 1);
    assert!((32_000..65_536).contains(&progress.pending_bytes));
    append_and_settle(&f, &p, &tid, 2, &"b".repeat(34_000)).await;
    let published = output.publications();
    assert_eq!(published.len(), 2);
    assert_eq!(f.snapshot(&tid).await, published[1].snapshot);
    assert!(matches!(
        published[1].snapshot.content.snapshot,
        Payload::Ref(_)
    ));
    assert_eq!(f.checkpoint().await.tasks[0].publication.pending_events, 0);
    assert_eq!(runner.calls(), 3);
    assert_eq!(f.clock.0.load(Ordering::SeqCst), 1000);
    let Some(RestoredTask::Live { view, .. }) =
        f.feed.restore(&session().scope, &tid).await.unwrap()
    else {
        panic!()
    };
    assert_eq!(
        text(view),
        format!("seed{}{}", "a".repeat(32_000), "b".repeat(34_000))
    );
}

#[tokio::test(start_paused = true)]
async fn quiet_tail_deadline_survives_takeover_and_trim_requires_published_coverage() {
    let runner = Runner::new(Mode::Stream);
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit_for(session(), "quiet").await;
    f.warm().await;
    until_status(&f, &f.processor, &tid, TaskStatus::Working).await;
    append_and_settle(&f, &f.processor, &tid, 1, " quiet").await;
    append_and_settle(&f, &f.processor, &tid, 2, " tail").await;
    let before = f.checkpoint().await;
    let last = before.last_event_id.unwrap();
    assert_eq!(
        before.tasks[0]
            .publication
            .flush_deadline
            .as_ref()
            .unwrap()
            .at,
        TimestampMillis(2000)
    );
    let retention = RetentionRequest {
        sid: session().sid,
        keep_from: last,
    };
    assert_eq!(
        f.feed.trim(retention.clone()).await.unwrap(),
        TrimOutcome::NotCovered
    );
    f.model.advance(Duration::from_secs(10));
    let (replacement, p) = f.replacement(runner.clone()).await;
    for _ in 0..4 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        replacement.tick().await.unwrap();
    }
    for _ in 0..30 {
        replacement.tick().await.unwrap();
        p.tick().await.unwrap();
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(
        f.snapshot(&tid).await.snapshot_event_id,
        before.tasks[0].publication.published_through
    );
    f.clock.0.store(1999, Ordering::SeqCst);
    for _ in 0..10 {
        replacement.tick().await.unwrap();
        p.tick().await.unwrap();
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_ne!(f.snapshot(&tid).await.snapshot_event_id, Some(last));
    f.clock.0.store(2000, Ordering::SeqCst);
    for _ in 0..40 {
        replacement.tick().await.unwrap();
        p.tick().await.unwrap();
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(f.snapshot(&tid).await.snapshot_event_id, Some(last));
    assert_eq!(runner.calls(), 3, "snapshot timeout is not a Runner input");
    assert!(matches!(
        f.feed.trim(retention).await.unwrap(),
        TrimOutcome::Trimmed | TrimOutcome::AlreadyTrimmed
    ));
    let Some(RestoredTask::Live { view, .. }) =
        f.feed.restore(&session().scope, &tid).await.unwrap()
    else {
        panic!()
    };
    assert_eq!(text(view), "seed quiet tail");
}
