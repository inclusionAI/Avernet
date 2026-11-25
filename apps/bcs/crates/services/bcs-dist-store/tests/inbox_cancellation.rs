#![allow(
    dead_code,
    clippy::unwrap_used,
    reason = "reader cancellation and stable admission regression assertions"
)]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{budget::*, inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::inbox::*;
use common::*;
use std::{num::NonZeroU32, sync::Arc, time::Duration};
#[tokio::test]
async fn cancellation_after_first_watch_staged_keeps_both_inputs() {
    let s = Arc::new(Recorder::default());
    let l = KeyLayout::new("audit", "multiwatch").unwrap();
    let p = StoreInboxProducer::new(
        Arc::new(SharedScanBudget::new(Default::default()).unwrap()),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        s.clone(),
        s.clone(),
        s.clone(),
        s.clone(),
        l.clone(),
        Arc::new(FixedClock(100)),
        InboxProducerConfig::new(BackendId("model".into())),
    )
    .unwrap();
    let mut requests = vec![];
    for i in 0..2 {
        let sid = Sid(format!("s0042.audit{i}"));
        let tid = Tid(format!("{}.task", sid.0));
        let mut definition = spec("g");
        definition.sid = sid.clone();
        let mut initial = task();
        initial.sid = sid.clone();
        initial.tid = tid.clone();
        let mut input = user_input(&format!("audit{i}"), "hello");
        input.sid = sid.clone();
        input.tid = tid;
        requests.push(AdmitRequest {
            scope: scope(),
            input,
            create_session: Some(definition),
            create_task: Some(initial),
        });
    }
    for request in &requests {
        p.admit(request.clone()).await.unwrap();
    }
    let mut feed = p.open(ShardId(42)).await.unwrap();
    for request in &requests {
        feed.watch(request.input.sid.clone(), None).await.unwrap();
    }
    let key = l
        .admission(&scope(), &requests[1].input.input_id)
        .unwrap();
    let pause = s.pause_after("document.read", &key, 1);
    let limits = ReadLimits {
        max_sessions: NonZeroU32::new(2).unwrap(),
        max_entries: NonZeroU32::new(2).unwrap(),
        max_bytes: NonZeroU32::new(128 * 1024).unwrap(),
    };
    {
        let future = feed.next(limits.clone(), Duration::ZERO);
        tokio::pin!(future);
        tokio::select! { _=pause.reached()=>{}, result=&mut future=>panic!("finished before cancellation: {result:?}") }
    }
    pause.resume();
    let result = feed.next(limits, Duration::ZERO).await.unwrap();
    assert!(result.failures.is_empty(), "{:?}", result.failures);
    assert_eq!(result.sessions.len(), 2);
    let mut got = result
        .sessions
        .iter()
        .flat_map(|s| s.entries.iter().map(|e| e.input_id.0.clone()))
        .collect::<Vec<_>>();
    got.sort();
    assert_eq!(got, vec!["audit0", "audit1"]);
}
struct MutableClock(std::sync::atomic::AtomicI64);
impl Clock for MutableClock {
    fn now(&self) -> TimestampMillis {
        TimestampMillis(self.0.load(std::sync::atomic::Ordering::SeqCst))
    }
}
#[tokio::test]
async fn duplicate_admission_after_clock_advance_keeps_original_success() {
    let s = Arc::new(Recorder::default());
    let l = KeyLayout::new("audit", "timeout-retry").unwrap();
    let clock = Arc::new(MutableClock(std::sync::atomic::AtomicI64::new(100)));
    let p = StoreInboxProducer::new(
        Arc::new(SharedScanBudget::new(Default::default()).unwrap()),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        s.clone(),
        s.clone(),
        s.clone(),
        s.clone(),
        l,
        clock.clone(),
        InboxProducerConfig::new(BackendId("model".into())),
    )
    .unwrap();
    let mut definition = spec("g");
    definition.limits.task_timeout_millis = (i64::MAX - 101) as u64;
    let r = AdmitRequest {
        scope: scope(),
        input: user_input("timeout-retry", "hello"),
        create_session: Some(definition),
        create_task: Some(task()),
    };
    assert!(matches!(
        p.admit(r.clone()).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    clock.0.store(102, std::sync::atomic::Ordering::SeqCst);
    let retry = p.admit(r).await;
    assert!(matches!(retry, Ok(AdmitOutcome::Duplicate(_))), "{retry:?}");
}
