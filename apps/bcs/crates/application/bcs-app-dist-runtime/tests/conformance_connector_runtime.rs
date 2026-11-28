//! Conformance entry: `ConnectorRuntimeFacade` against the service harness,
//! over a scripted control port.
#![allow(clippy::unwrap_used, reason = "tests")]
use async_trait::async_trait;
use bcs_app_dist_runtime::ConnectorRuntimeFacade;
use bcs_service_api::application::dist_runtime::{
    DistConnectorRuntimeService, DrainOutcome, RuntimeError, RuntimeSnapshot,
};
use bcs_service_api::port::dist_runtime::DistRuntimeControlPort;
use bcs_test_support::contract::distributed::services::dist_connector_runtime_service_contract_tests;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

#[derive(Default)]
struct Engine {
    draining: AtomicBool,
    held: AtomicU64,
    grace_ms: AtomicU64,
}

#[async_trait]
impl DistRuntimeControlPort for Engine {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        let draining = self.draining.load(Ordering::SeqCst);
        Ok(RuntimeSnapshot {
            instance: bcs_dist_model::Instance { node: "conn-03".into(), incarnation: "1794370950000".into() },
            ready: !draining,
            draining,
            active_work: self.held.load(Ordering::SeqCst),
            pending_effects: 0,
        })
    }
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        self.draining.store(true, Ordering::SeqCst);
        self.grace_ms.store(grace.as_millis() as u64, Ordering::SeqCst);
        Ok(match self.held.load(Ordering::SeqCst) {
            0 => DrainOutcome::Drained,
            remaining => DrainOutcome::DeadlineReached { remaining },
        })
    }
}

#[tokio::test]
async fn conformance_connector_runtime_facade() {
    let facade = ConnectorRuntimeFacade::new(Arc::new(Engine::default()));
    dist_connector_runtime_service_contract_tests(&facade).await.unwrap();
}

#[tokio::test]
async fn drain_passes_the_grace_on_and_reports_leftovers() {
    let engine = Arc::new(Engine::default());
    engine.held.store(2, Ordering::SeqCst);
    let facade = ConnectorRuntimeFacade::new(engine.clone());
    assert_eq!(facade.snapshot().await.unwrap().active_work, 2);
    assert_eq!(
        facade.drain(Duration::from_secs(30)).await.unwrap(),
        DrainOutcome::DeadlineReached { remaining: 2 }
    );
    assert_eq!(engine.grace_ms.load(Ordering::SeqCst), 30_000);
    assert!(facade.snapshot().await.unwrap().draining);
}

#[tokio::test]
async fn an_unbounded_grace_is_refused_before_the_engine_is_touched() {
    let engine = Arc::new(Engine::default());
    let facade = ConnectorRuntimeFacade::new(engine.clone());
    let error = facade.drain(Duration::from_secs(7200)).await.unwrap_err();
    assert_eq!(error.code, "drain_grace_too_long");
    assert!(!engine.draining.load(Ordering::SeqCst));
}

#[tokio::test]
async fn a_handoff_is_forwarded_only_from_the_source_owner_to_this_incarnation() {
    use bcs_dist_model::{Instance, RequestId, ShardId, WorkerEpoch};
    use bcs_service_api::application::dist_runtime::HandoffRequest;
    use bcs_service_api::port::repo::distributed::common::ShardOwner;
    use bcs_service_api::port::repo::distributed::ownership::ShardClaim;
    let instance = |node: &str| Instance { node: node.into(), incarnation: "1794370950000".into() };
    let facade = ConnectorRuntimeFacade::new(Arc::new(Engine::default()));
    let request = |target: &str| HandoffRequest {
        request_id: RequestId("h".into()),
        from: ShardClaim {
            shard: ShardId(3),
            owner: ShardOwner { instance: instance("conn-01"), epoch: WorkerEpoch(1) },
        },
        target: instance(target),
        grace: Duration::from_secs(1),
    };
    let wrong_source = facade.accept_handoff(instance("conn-02"), request("conn-03")).await.unwrap_err();
    assert_eq!(wrong_source.code, "peer_instance_mismatch");
    let wrong_target = facade.accept_handoff(instance("conn-01"), request("conn-09")).await.unwrap_err();
    assert_eq!(wrong_target.code, "target_incarnation_mismatch");
    // Bound correctly: reaches the engine (this scripted one has no handoff).
    let forwarded = facade.accept_handoff(instance("conn-01"), request("conn-03")).await.unwrap_err();
    assert_eq!(forwarded.code, "worker_handoff_unsupported");
}
