#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "transport test assertions"
)]
use async_trait::async_trait;
use axum::Extension;
use bcs_dist_model::*;
use bcs_dist_worker_client::HttpWorkerPeer;
use bcs_dist_worker_http::*;
use bcs_service_api::{
    application::dist_runtime::*,
    port::{
        dist_runtime::DistWorkerPeerPort,
        repo::distributed::{common::ShardOwner, ownership::*},
    },
};
use std::{
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::Duration,
};
struct App {
    calls: AtomicUsize,
    unavailable: AtomicBool,
    done: Mutex<Vec<(Instance, ShardClaim, RequestId)>>,
    wakes: Mutex<Vec<(Instance, ShardId)>>,
}
fn instance(name: &str) -> Instance {
    Instance {
        node: name.into(),
        incarnation: format!("{name}-1"),
    }
}
fn claim(name: &str, epoch: u64) -> ShardClaim {
    ShardClaim {
        shard: ShardId(42),
        owner: ShardOwner {
            instance: instance(name),
            epoch: WorkerEpoch(epoch),
        },
    }
}
fn request() -> HandoffRequest {
    HandoffRequest {
        request_id: RequestId("h".into()),
        from: claim("a", 1),
        target: instance("b"),
        grace: Duration::from_secs(1),
    }
}
#[async_trait]
impl DistWorkerRuntimeService for App {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        Ok(RuntimeSnapshot {
            instance: instance("b"),
            ready: true,
            draining: false,
            active_work: 0,
            pending_effects: 0,
        })
    }
    async fn drain(&self, _: Duration) -> Result<DrainOutcome, RuntimeError> {
        Ok(DrainOutcome::Drained)
    }
    async fn accept_handoff(
        &self,
        source: Instance,
        _: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        assert_eq!(source, instance("a"));
        self.calls.fetch_add(1, Ordering::SeqCst);
        if self.unavailable.load(Ordering::SeqCst) {
            return Err(RuntimeError {
                code: "backend_unknown".into(),
            });
        }
        Ok(HandoffOutcome::Accepted(ShardRecord {
            claim: claim("b", 2),
            handoff: None,
        }))
    }
    async fn handoff_done(
        &self,
        source: Instance,
        target: ShardClaim,
        id: RequestId,
    ) -> Result<bool, RuntimeError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.done.lock().unwrap().push((source, target, id));
        if self.unavailable.load(Ordering::SeqCst) {
            return Err(RuntimeError {
                code: "backend_unknown".into(),
            });
        }
        Ok(true)
    }
    async fn wake(&self, source: Instance, shard: ShardId) -> Result<(), RuntimeError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.wakes.lock().unwrap().push((source, shard));
        if self.unavailable.load(Ordering::SeqCst) {
            return Err(RuntimeError {
                code: "backend_unknown".into(),
            });
        }
        Ok(())
    }
}
async fn serve(auth: bool) -> (String, Arc<App>, tokio::task::JoinHandle<()>) {
    let app = Arc::new(App {
        calls: AtomicUsize::new(0),
        unavailable: AtomicBool::new(false),
        done: Mutex::default(),
        wakes: Mutex::default(),
    });
    let mut router = worker_router(app.clone());
    if auth {
        router = router.layer(Extension(instance("a")));
    }
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = format!("http://{}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    (address, app, task)
}
#[tokio::test]
async fn missing_authenticated_instance_is_rejected_even_with_identity_in_body() {
    let (url, app, task) = serve(false).await;
    let response = reqwest::Client::new()
        .post(format!("{url}/internal/bcn/worker/handoff"))
        .json(&request())
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 401);
    assert_eq!(app.calls.load(Ordering::SeqCst), 0);
    task.abort();
}
#[tokio::test]
async fn authenticated_peer_roundtrip_and_incarnation_binding() {
    let (url, app, task) = serve(true).await;
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert("x-test-identity", "test".parse().unwrap());
    let client = HttpWorkerPeer::new(instance("a"), headers, Duration::from_secs(1)).unwrap();
    let target = MemberRecord {
        instance: instance("b"),
        role: RuntimeRole::Worker,
        control_address: url.clone(),
        draining: false,
    };
    assert!(matches!(
        client.handoff(&target, request()).await.unwrap(),
        HandoffOutcome::Accepted(_)
    ));
    let mut forged = request();
    forged.from.owner.instance = instance("other");
    assert!(client.handoff(&target, forged).await.is_err());
    let response = reqwest::Client::new()
        .post(format!("{url}/internal/bcn/worker/source-done"))
        .json(&HandoffDoneRequest {
            source: instance("other"),
            target: claim("b", 2),
            request_id: RequestId("h".into()),
        })
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 403);
    let response = reqwest::Client::new()
        .post(format!("{url}/internal/bcn/worker/wake"))
        .json(&WakeRequest {
            target: instance("old-b"),
            shard: ShardId(42),
        })
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 409);
    assert_eq!(app.calls.load(Ordering::SeqCst), 1);
    task.abort();
}

#[tokio::test]
async fn completion_and_wake_forward_exact_authenticated_identity_and_request() {
    let (url, app, task) = serve(true).await;
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert("x-test-identity", "test".parse().unwrap());
    let client = HttpWorkerPeer::new(instance("a"), headers, Duration::from_secs(1)).unwrap();
    let target = MemberRecord {
        instance: instance("b"),
        role: RuntimeRole::Worker,
        control_address: url.clone(),
        draining: false,
    };
    assert!(
        client
            .source_done(&target, instance("a"), claim("b", 2), RequestId("h".into()))
            .await
            .unwrap()
    );
    assert_eq!(
        app.done.lock().unwrap().as_slice(),
        &[(instance("a"), claim("b", 2), RequestId("h".into()))]
    );
    let response = reqwest::Client::new()
        .post(format!("{url}/internal/bcn/worker/wake"))
        .json(&WakeRequest {
            target: instance("b"),
            shard: ShardId(42),
        })
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 204);
    assert_eq!(
        app.wakes.lock().unwrap().as_slice(),
        &[(instance("a"), ShardId(42))]
    );
    assert_eq!(app.calls.load(Ordering::SeqCst), 2);
    task.abort();
}

#[tokio::test]
async fn application_failures_are_not_acknowledged_as_success() {
    let (url, app, task) = serve(true).await;
    app.unavailable.store(true, Ordering::SeqCst);
    let client = reqwest::Client::new();
    let handoff = client
        .post(format!("{url}/internal/bcn/worker/handoff"))
        .json(&request())
        .send()
        .await
        .unwrap();
    assert_eq!(handoff.status(), 503);
    let done = client
        .post(format!("{url}/internal/bcn/worker/source-done"))
        .json(&HandoffDoneRequest {
            source: instance("a"),
            target: claim("b", 2),
            request_id: RequestId("h".into()),
        })
        .send()
        .await
        .unwrap();
    assert_eq!(done.status(), 503);
    let wake = client
        .post(format!("{url}/internal/bcn/worker/wake"))
        .json(&WakeRequest {
            target: instance("b"),
            shard: ShardId(42),
        })
        .send()
        .await
        .unwrap();
    assert_eq!(wake.status(), 503);
    assert_eq!(app.calls.load(Ordering::SeqCst), 3);
    task.abort();
}

#[tokio::test]
async fn oversized_control_request_is_rejected_before_application_admission() {
    let (url, app, task) = serve(true).await;
    let mut oversized = request();
    oversized.request_id = RequestId("x".repeat(16_384));
    let response = reqwest::Client::new()
        .post(format!("{url}/internal/bcn/worker/handoff"))
        .json(&oversized)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 413);
    assert_eq!(app.calls.load(Ordering::SeqCst), 0);
    task.abort();
}

struct Connector;
#[async_trait]
impl DistConnectorRuntimeService for Connector {
    async fn accept_handoff(&self, source: Instance, request: HandoffRequest) -> Result<HandoffOutcome, RuntimeError> {
        assert_eq!(source, request.from.owner.instance);
        Ok(HandoffOutcome::Accepted(ShardRecord {
            claim: ShardClaim {
                shard: request.from.shard,
                owner: ShardOwner {
                    instance: request.target,
                    epoch: WorkerEpoch(request.from.owner.epoch.0 + 1),
                },
            },
            handoff: None,
        }))
    }
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        unreachable!("not used by the route")
    }
    async fn drain(&self, _: Duration) -> Result<DrainOutcome, RuntimeError> {
        unreachable!("not used by the route")
    }
}

#[tokio::test]
async fn the_connector_route_takes_a_partition_offer_from_a_bound_peer() {
    let router = connector_router(Arc::new(Connector)).layer(Extension(instance("a")));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert("x-test-identity", "test".parse().unwrap());
    let client = HttpWorkerPeer::connector(instance("a"), headers, Duration::from_secs(1)).unwrap();
    let target = MemberRecord {
        instance: instance("b"),
        role: RuntimeRole::Connector,
        control_address: url.clone(),
        draining: false,
    };
    let HandoffOutcome::Accepted(record) = client.handoff(&target, request()).await.unwrap() else {
        panic!("not accepted");
    };
    assert_eq!(record.claim, claim("b", 2));
    // The Worker routes are not served here.
    let response = reqwest::Client::new()
        .post(format!("{url}/internal/bcn/worker/handoff"))
        .json(&request())
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 404);
    task.abort();
}
