#![allow(clippy::expect_used, reason = "snapshot publication fault assertions")]
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, result::*, task_feed::*};
use crate::lifecycle_support::*;
use crate::processor_support::Fixture;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};

struct CountedBlobs {
    inner: Arc<dyn BlobStorePort>,
    full_snapshot_writes: Mutex<Vec<(BlobRef, usize, bool)>>,
    lose_full_reply: AtomicBool,
    lose_confirmation: Mutex<Option<BlobRef>>,
    pending_retry: Mutex<Option<BlobRef>>,
    failed_confirmations: AtomicUsize,
}
#[async_trait]
impl BlobStorePort for CountedBlobs {
    async fn put_immutable(
        &self,
        identity: BlobRef,
        bytes: Vec<u8>,
    ) -> Result<IdempotentWrite, StoreError> {
        let full = serde_json::from_slice::<DisplaySnapshot>(&bytes).is_ok();
        if full {
            let retry = {
                let mut pending = self.pending_retry.lock().unwrap();
                if pending.as_ref() == Some(&identity) {
                    pending.take();
                    true
                } else {
                    false
                }
            };
            self.full_snapshot_writes
                .lock()
                .unwrap()
                .push((identity.clone(), bytes.len(), retry));
        }
        let result = self.inner.put_immutable(identity.clone(), bytes).await?;
        if full && self.lose_full_reply.swap(false, Ordering::SeqCst) {
            *self.lose_confirmation.lock().unwrap() = Some(identity.clone());
            return Err(StoreError::OutcomeUnknown {
                operation_id: identity.key,
            });
        }
        Ok(result)
    }
    async fn get(&self, reference: &BlobRef) -> Result<Option<Vec<u8>>, StoreError> {
        let fail = {
            let mut pending = self.lose_confirmation.lock().unwrap();
            if pending.as_ref() == Some(reference) {
                pending.take();
                true
            } else {
                false
            }
        };
        if fail {
            self.failed_confirmations.fetch_add(1, Ordering::SeqCst);
            *self.pending_retry.lock().unwrap() = Some(reference.clone());
            return Err(StoreError::OutcomeUnknown {
                operation_id: reference.key.clone(),
            });
        }
        self.inner.get(reference).await
    }
}
struct GatedPublisher {
    inner: Arc<dyn TaskPublisherPort>,
    gate: AtomicBool,
    entered: tokio::sync::Notify,
    resume: tokio::sync::Notify,
    lose_reply: AtomicBool,
    requests: Mutex<Vec<PublishCommittedSnapshot>>,
}
#[async_trait]
impl TaskPublisherPort for GatedPublisher {
    async fn append(&self, r: AppendCommittedEvents) -> Result<AppendOutcome, StoreError> {
        self.inner.append(r).await
    }
    async fn publish(&self, r: PublishCommittedSnapshot) -> Result<PublishOutcome, StoreError> {
        self.requests.lock().unwrap().push(r.clone());
        if self.gate.swap(false, Ordering::SeqCst) {
            self.entered.notify_one();
            self.resume.notified().await;
        }
        let result = self.inner.publish(r.clone()).await?;
        if self.lose_reply.swap(false, Ordering::SeqCst) {
            return Err(StoreError::OutcomeUnknown {
                operation_id: format!("{:?}", r.source.effect_id),
            });
        }
        Ok(result)
    }
    async fn trim(&self, r: RetentionRequest) -> Result<TrimOutcome, StoreError> {
        self.inner.trim(r).await
    }
}

#[tokio::test(start_paused = true)]
async fn slow_publication_and_lost_replies_keep_trim_safe_and_count_full_snapshot_retry_bytes() {
    let runner = Runner::new(Mode::Stream);
    let f = Fixture::new(runner.clone()).await;
    let blobs = Arc::new(CountedBlobs {
        inner: f.archive.clone(),
        full_snapshot_writes: Mutex::new(vec![]),
        lose_full_reply: AtomicBool::new(true),
        lose_confirmation: Mutex::new(None),
        pending_retry: Mutex::new(None),
        failed_confirmations: AtomicUsize::new(0),
    });
    let output = Arc::new(GatedPublisher {
        inner: f.feed.clone(),
        gate: AtomicBool::new(false),
        entered: tokio::sync::Notify::new(),
        resume: tokio::sync::Notify::new(),
        lose_reply: AtomicBool::new(false),
        requests: Mutex::new(vec![]),
    });
    let p = processor_with_blob(
        &f,
        runner.clone(),
        output.clone(),
        f.archive.clone(),
        f.dispatch.clone(),
        blobs.clone(),
    );
    let tid = f.admit_for(session(), "snapshot-race").await;
    f.warm().await;
    until_status(&f, &p, &tid, TaskStatus::Working).await;
    delta(&f, &tid, 1, &"a".repeat(33_000)).await;
    drive(&f, &p, 60).await;
    let initial = f.snapshot(&tid).await;
    output.gate.store(true, Ordering::SeqCst);
    output.lose_reply.store(true, Ordering::SeqCst);
    delta(&f, &tid, 2, &"b".repeat(33_000)).await;
    let mut overlap = None;
    for _ in 0..200 {
        f.runtime.tick().await.unwrap();
        let tick = p.tick();
        tokio::pin!(tick);
        tokio::select! {
            _ = output.entered.notified() => {
                let cut = f.checkpoint().await.last_event_id.unwrap();
                assert_ne!(initial.snapshot_event_id, Some(cut));
                let retention = RetentionRequest { sid: session().sid, keep_from: cut };
                assert_eq!(f.feed.trim(retention.clone()).await.unwrap(), TrimOutcome::NotCovered, "append is durable but the blocked publish has not covered it");
                output.resume.notify_one();
                assert!(tick.await.is_err(), "applied publication reply must actually be lost");
                assert_eq!(f.snapshot(&tid).await.snapshot_event_id, Some(cut));
                assert!(matches!(f.feed.trim(retention).await.unwrap(), TrimOutcome::Trimmed | TrimOutcome::AlreadyTrimmed), "durable publication is proof even while Worker has not received its reply");
                overlap = Some(cut);
                break;
            },
            result = &mut tick => { if let Err(error) = result { assert!(error.code.contains("unknown"), "{error:?}"); } },
        }
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    }
    let cut = overlap.expect("must overlap trim with an actually blocked publish");
    drive(&f, &p, 120).await;
    assert_eq!(
        runner.calls(),
        3,
        "neither Blob nor publication retries rerun reduction"
    );
    let requests = output.requests.lock().unwrap().clone();
    let retried: Vec<_> = requests
        .iter()
        .filter(|r| r.snapshot.snapshot_event_id == Some(cut))
        .collect();
    assert!(retried.len() >= 2);
    assert!(
        retried.iter().all(|r| **r == *retried[0]),
        "publication retries preserve the complete source and snapshot"
    );
    let snapshot = f.snapshot(&tid).await;
    assert_eq!(snapshot.snapshot_event_id, Some(cut));
    assert_eq!(
        snapshot.output_usage.cumulative_bytes, 66_010,
        "encoded seed and two deltas charged once"
    );
    let Some(RestoredTask::Live { view, .. }) =
        f.feed.restore(&session().scope, &tid).await.unwrap()
    else {
        panic!()
    };
    let Payload::Inline(raw) = view.content.snapshot else {
        panic!()
    };
    let display: DisplaySnapshot = serde_json::from_value(raw).unwrap();
    assert_eq!(
        display.units[0].content,
        serde_json::json!(format!("seed{}{}", "a".repeat(33_000), "b".repeat(33_000)))
    );
    let writes = blobs.full_snapshot_writes.lock().unwrap().clone();
    let mut unique: Vec<(BlobRef, usize)> = vec![];
    for write in &writes {
        if let Some(previous) = unique.iter().find(|(id, _)| id == &write.0) {
            assert_eq!(previous.1, write.1);
        } else {
            unique.push((write.0.clone(), write.1));
        }
    }
    let attempted: usize = writes.iter().map(|(_, bytes, _)| bytes).sum();
    let unique_bytes: usize = unique.iter().map(|(_, bytes)| bytes).sum();
    assert!(
        unique_bytes > 65_536,
        "count actual full DisplaySnapshot bytes, never Ref encoding"
    );
    let fault_retry_bytes: usize = writes
        .iter()
        .filter(|(_, _, retry)| *retry)
        .map(|(_, bytes, _)| bytes)
        .sum();
    assert_eq!(blobs.failed_confirmations.load(Ordering::SeqCst), 1);
    assert!(
        fault_retry_bytes > 65_536,
        "failed put confirmation must lead to a later actual same-identity retry"
    );
    assert!(attempted >= unique_bytes + fault_retry_bytes);
    println!(
        "full snapshot Blob bytes: attempted={attempted}, unique={unique_bytes}, fault_retry={fault_retry_bytes}, other_idempotent={}",
        attempted - unique_bytes - fault_retry_bytes
    );
}
