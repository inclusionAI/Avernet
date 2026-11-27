//! The actor cannot treat frozen capabilities and dispatch deadlines as a
//! trusted downstream dedupe domain and guarantee window.
#![allow(clippy::unwrap_used, reason = "test assertions")]
mod rig;

use bcs_dist_connector::rules::DistConnectorRules;
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::DistConnectorCoreService;
use bcs_service_api::port::dist_agent::QueryOutcome;
use bcs_service_api::port::repo::distributed::ownership::Liveness;
use rig::*;
use serde_json::json;
use std::sync::Arc;

fn declared_dedupe() -> DispatchSpec {
    let mut spec = dispatch_spec(&did());
    spec.capabilities.persistent_dedupe = true;
    spec.capabilities.idempotent_start = true;
    spec.capabilities.idempotent_input = true;
    spec
}

fn legacy_proof(rig: &Rig, spec: &DispatchSpec, operation: ProofOperation, payload: &Payload) -> SendProof {
    let Payload::Inline(body) = payload else { panic!("inline fixture") };
    let mut proof = DistConnectorRules.send_proof(
        spec, operation, &serde_json::to_vec(body).unwrap(), rig.config.send_fingerprint(&spec.capabilities),
    );
    // Old records treated the frozen target tuple and task deadline as evidence.
    proof.dedupe_domain = bcs_dist_encoding::connector::dedupe_domain(&spec.target);
    proof.guarantee_deadline = spec.deadline;
    proof
}

async fn retained(rig: &Rig, running: &Running) {
    until("handoff to retained verification", || async {
        rig.doc().await["execution"]["verification"].is_object()
    }).await;
    until("ordinary connector slot is free", || async { running.active().await == 0 }).await;
    let execution = rig.doc().await["execution"].clone();
    assert_eq!(execution["verification"]["key"]["kind"], "retained");
    assert_eq!(execution["verification"]["method"], "retain");
    assert_eq!(execution["outcome"], serde_json::Value::Null);
}

#[tokio::test]
async fn fresh_start_is_not_resent_on_a_frozen_dedupe_claim() {
    let mut rig = Rig::new().await;
    rig.config.max_verifications = 0;
    rig.worker.create_spec(declared_dedupe()).await;
    rig.agent.on_start(Reply::Unknown);
    let _events = rig.agent.start_stream();
    rig.worker.notify().await;
    let running = rig.start();
    retained(&rig, &running).await;
    assert_eq!(rig.agent.count("start"), 1);
    assert_eq!(rig.agent.count("query_task"), 0, "there is no remote task id");
}

#[tokio::test]
async fn recovered_start_does_not_trust_an_old_synthetic_proof() {
    let mut rig = Rig::new().await;
    rig.config.max_verifications = 0;
    let spec = declared_dedupe();
    rig.worker.create_spec(spec.clone()).await;
    let mut old = acquire(&queue(&rig.store), &instance("conn-01")).await;
    let proof = legacy_proof(&rig, &spec, ProofOperation::Start {
        request_id: RequestId(format!("{}:start", DID)),
    }, &spec.request);
    old.begin_send(start_op(), proof).await.unwrap();
    rig.membership = Arc::new(FixedLiveness(Liveness::Dead));
    let _events = rig.agent.start_stream();
    rig.worker.notify().await;
    let running = rig.start();
    retained(&rig, &running).await;
    assert_eq!(rig.agent.count("start"), 0);
}

#[tokio::test]
async fn fresh_input_is_not_resent_on_a_frozen_dedupe_claim() {
    let rig = Rig::new().await;
    let _events = rig.agent.start_stream();
    rig.worker.create_spec(declared_dedupe()).await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    rig.agent.on_input(Reply::Unknown);
    rig.agent.on_input(Reply::Accepted);
    rig.worker.push(pending(1)).await;
    until("ambiguous reply blocked", || async { running.pending_effects().await == 1 }).await;
    assert_eq!(rig.agent.count("input"), 1);
    assert_eq!(rig.doc().await["execution"]["confirmed_input_through"], serde_json::Value::Null);
}

#[tokio::test]
async fn recovered_input_does_not_trust_an_old_synthetic_proof() {
    let mut rig = Rig::new().await;
    let spec = declared_dedupe();
    rig.worker.create_spec(spec.clone()).await;
    let mut old = acquire(&queue(&rig.store), &instance("conn-01")).await;
    old.begin_send(start_op(), proof(&start_op())).await.unwrap();
    old.record_acceptance(accepted(start_op())).await.unwrap();
    let input = pending(1);
    rig.worker.push(input.clone()).await;
    let input_proof = legacy_proof(&rig, &spec, ProofOperation::Input {
        seq: InputSeq(1), request_id: RequestId(format!("{}:in:1", DID)),
    }, &input.payload);
    old.begin_send(input_op(1), input_proof).await.unwrap();
    rig.membership = Arc::new(FixedLiveness(Liveness::Dead));
    rig.agent.on_task(found(TaskStatus::InputRequired, json!({"state": "input_required"})));
    rig.agent.on_input(Reply::Accepted);
    rig.worker.notify().await;
    let running = rig.start();
    until("ambiguous reply blocked", || async { running.pending_effects().await == 1 }).await;
    assert_eq!(rig.agent.count("input"), 0);
    assert_eq!(rig.doc().await["execution"]["confirmed_input_through"], serde_json::Value::Null);
}

#[tokio::test]
async fn uncertain_reply_on_an_unobservable_remote_hands_off_for_cancel_only() {
    let mut rig = Rig::new().await;
    rig.config.max_verifications = 0;
    let mut spec = declared_dedupe();
    spec.capabilities.query_by_request = true;
    spec.capabilities.query_by_task = false;
    spec.capabilities.subscribe = false;
    rig.worker.create_spec(spec).await;
    rig.agent.on_start(Reply::Accepted);
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    let original = rig.doc().await["execution"]["remote"].clone();
    rig.agent.on_input(Reply::Unknown);
    rig.worker.push(pending(1)).await;
    until("known-remote handoff", || async {
        rig.doc().await["execution"]["verification"].is_object()
    }).await;
    until("ordinary connector slot is free", || async { running.active().await == 0 }).await;
    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["verification"]["key"]["kind"], "by_task");
    assert_eq!(doc["execution"]["verification"]["key"]["remote"], original);
    assert_eq!(doc["execution"]["verification"]["method"], "cancel_only");
    assert_eq!(rig.agent.count("input"), 1);
}

#[tokio::test]
async fn unsupported_request_lookup_is_not_armed_as_a_query() {
    let mut rig = Rig::new().await;
    rig.config.max_verifications = 0;
    let mut spec = declared_dedupe();
    spec.capabilities.persistent_dedupe = false;
    spec.capabilities.query_by_request = true;
    rig.worker.create_spec(spec).await;
    rig.agent.on_start(Reply::Unknown);
    rig.agent.on_request(QueryOutcome::Unsupported);
    rig.worker.notify().await;
    let running = rig.start();
    retained(&rig, &running).await;
    assert_eq!(rig.agent.count("start"), 1);
    assert_eq!(rig.agent.count("query_request"), 1);
}
