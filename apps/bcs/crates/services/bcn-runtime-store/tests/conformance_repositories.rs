mod common;
use common::*;
use std::sync::Arc;
use bcn_runtime_api::port::repo::InboxPort;
use bcn_runtime_model::*;
use bcn_runtime_store::RuntimeStore;
use bcs_test_support::contract::bcn_runtime::{ownership_port_contract_tests, inbox_port_contract_tests, node_liveness_port_contract_tests};
#[tokio::test]
async fn conformance_ownership() {
 let (repo, _, _) = fixture(); let repo: Arc<RuntimeStore> = Arc::new(repo);
 ownership_port_contract_tests(repo, ShardId { scope: scope(), number: 4 }, node("one"), node("two")).await;
}
#[tokio::test]
async fn conformance_inbox() {
 let (repo, _, req) = fixture(); let cp = submitted(&repo, &req).await;
 repo.advance(&req.spec.session_id, &cp.fence, 1).await.unwrap();
 inbox_port_contract_tests(Arc::new(repo), req.spec.session_id, SessionInput { event_id: EventId::new(scope(), "timer"), at_ms: 1000, kind: SessionInputKind::TimerFired { timer_id: TimerId::new(scope(), "deadline"), output: vec![] } }).await;
}
#[tokio::test]
async fn conformance_liveness() {
 let (repo, _, _) = fixture(); node_liveness_port_contract_tests(Arc::new(repo), node("one")).await;
}
