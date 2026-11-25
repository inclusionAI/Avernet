//! Operation scope conformance, independent of any concrete exporter.
#![allow(clippy::unwrap_used)]
use std::sync::{Arc, Mutex};
use bcs_tracing_api::*;

#[derive(Default)]
struct Spy { parents: Mutex<Vec<Option<String>>>, finishes: Mutex<Vec<(Outcome, String)>> }
struct Provider(Arc<Spy>);
struct Completion(Arc<Spy>);
impl Record for Completion {
    fn finish(self: Box<Self>, outcome: Outcome, _: Option<u64>) {
        let id = current_context().unwrap().downcast_ref::<String>().unwrap().clone();
        self.0.finishes.lock().unwrap().push((outcome, id));
    }
}
impl NetworkTracer for Provider {
    fn supports(&self, _: Kind) -> bool { true }
    fn start(&self, info: &RequestInfo<'_>, incoming: &Carrier, parent: ParentContext<'_>) -> StartedTrace {
        self.0.parents.lock().unwrap().push(parent.local.and_then(|p| p.downcast_ref::<String>().cloned()));
        let id = if matches!(info.kind, Kind::GrpcServer { .. }) { incoming[0].1.clone() } else { "child".into() };
        StartedTrace { context: Arc::new(id), headers: Vec::new(), remove_headers: Vec::new(), record: Box::new(Completion(self.0.clone())) }
    }
}
fn info(kind: Kind) -> RequestInfo<'static> {
    RequestInfo { kind, method: "Execute", endpoint: "fixture.Service", route: None, target_app: "peer", request_size: None }
}

#[tokio::test]
async fn captured_server_scope_survives_spawn_and_restores_outer_context() {
    let spy = Arc::new(Spy::default());
    let server = with_provider(Some(Arc::new(Provider(spy.clone()))), async {
        Call::start(&info(Kind::GrpcServer { server_streaming: true }), &vec![("parent".into(), "remote".into())])
    }).await;
    let scope = server.scope();
    with_context(Some(Arc::new(String::from("unrelated"))), async {
        tokio::spawn(async move {
            scope.scope(async {
                assert_eq!(current_context().unwrap().downcast_ref::<String>().unwrap(), "remote");
                Call::start(&info(Kind::HttpClient), &Vec::new()).finish(Outcome::Http(200), None);
            }).await;
            assert!(current_context().is_none());
        }).await.unwrap();
        assert_eq!(current_context().unwrap().downcast_ref::<String>().unwrap(), "unrelated");
    }).await;
    server.finish(Outcome::Rpc(0), None);
    assert_eq!(*spy.parents.lock().unwrap(), vec![None, Some("remote".into())]);
    assert_eq!(*spy.finishes.lock().unwrap(), vec![(Outcome::Http(200), "child".into()), (Outcome::Rpc(0), "remote".into())]);
}

#[tokio::test]
async fn explicit_parent_wins_but_suppression_cannot_be_bypassed() {
    let spy = Arc::new(Spy::default());
    let captured = with_provider(Some(Arc::new(Provider(spy.clone()))),
        with_context(Some(Arc::new(String::from("operation-a"))), async { CapturedContext::capture() })).await;
    with_context(Some(Arc::new(String::from("operation-b"))), async {
        let child = Call::start_with_parent(&info(Kind::GrpcClient { server_streaming: false }), &Vec::new(), Some(&captured));
        drop(child);
        with_provider(None, async {
            let suppressed = Call::start_with_parent(&info(Kind::GrpcClient { server_streaming: false }), &Vec::new(), Some(&captured));
            assert!(suppressed.context().is_none());
            assert!(suppressed.headers().is_empty());
        }).await;
        assert_eq!(current_context().unwrap().downcast_ref::<String>().unwrap(), "operation-b");
    }).await;
    assert_eq!(*spy.parents.lock().unwrap(), vec![Some("operation-a".into())]);
    assert_eq!(*spy.finishes.lock().unwrap(), vec![(Outcome::Cancelled, "child".into())]);
}

#[tokio::test]
async fn legacy_provider_does_not_receive_new_server_kind() {
    struct Legacy;
    impl NetworkTracer for Legacy {
        fn start(&self, _: &RequestInfo<'_>, _: &Carrier, _: ParentContext<'_>) -> StartedTrace {
            panic!("unsupported server kind must not reach a legacy provider")
        }
    }
    with_provider(Some(Arc::new(Legacy)), async {
        let server = Call::start(&info(Kind::GrpcServer { server_streaming: false }), &Vec::new());
        assert!(server.context().is_none());
        assert!(server.headers().is_empty());
        server.finish(Outcome::Rpc(0), None);
    }).await;
}
