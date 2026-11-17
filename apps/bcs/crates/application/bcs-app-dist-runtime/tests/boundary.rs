#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "application boundary assertions"
)]
use async_trait::async_trait;
use bcs_app_dist_runtime::WorkerRuntimeApplication;
use bcs_dist_model::*;
use bcs_service_api::{
    application::dist_runtime::DistWorkerRuntimeService,
    port::{
        dist_runtime::DistRuntimeControlPort,
        repo::distributed::{common::ShardOwner, ownership::*},
    },
    types::dist_runtime::*,
};
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
struct Control {
    calls: AtomicUsize,
}
fn instance(node: &str) -> Instance {
    Instance {
        node: node.into(),
        incarnation: format!("{node}-1"),
    }
}
fn request() -> HandoffRequest {
    HandoffRequest {
        request_id: RequestId("h1".into()),
        from: ShardClaim {
            shard: ShardId(42),
            owner: ShardOwner {
                instance: instance("a"),
                epoch: WorkerEpoch(1),
            },
        },
        target: instance("b"),
        grace: Duration::from_secs(5),
    }
}
#[async_trait]
impl DistRuntimeControlPort for Control {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        Ok(RuntimeSnapshot {
            instance: instance("b"),
            ready: true,
            draining: false,
            active_work: 0,
            pending_effects: 0,
        })
    }
    async fn drain(&self, _grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        Ok(DrainOutcome::DeadlineReached { remaining: 7 })
    }
    async fn accept_handoff(
        &self,
        _request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(HandoffOutcome::Rejected)
    }
}
#[tokio::test]
async fn source_and_target_are_bound_before_engine_admission() {
    let control = Arc::new(Control {
        calls: AtomicUsize::new(0),
    });
    let app = WorkerRuntimeApplication::new(control.clone());
    assert!(
        app.accept_handoff(instance("intruder"), request())
            .await
            .is_err()
    );
    let mut stale = request();
    stale.target.incarnation = "old".into();
    assert!(app.accept_handoff(instance("a"), stale).await.is_err());
    assert_eq!(control.calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        app.accept_handoff(instance("a"), request()).await.unwrap(),
        HandoffOutcome::Rejected
    );
    assert_eq!(control.calls.load(Ordering::SeqCst), 1);
}
#[tokio::test]
async fn deadline_reached_is_preserved() {
    let app = WorkerRuntimeApplication::new(Arc::new(Control {
        calls: AtomicUsize::new(0),
    }));
    assert_eq!(
        app.drain(Duration::from_secs(1)).await.unwrap(),
        DrainOutcome::DeadlineReached { remaining: 7 }
    );
}
