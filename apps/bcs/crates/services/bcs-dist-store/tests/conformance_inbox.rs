#![allow(clippy::unwrap_used, reason = "actual Inbox provider conformance")]
mod common;
mod control_support;
use bcs_dist_model::*;
use bcs_dist_store::inbox::StoreInboxProducer;
use bcs_service_api::port::repo::distributed::inbox::*;
use bcs_test_support::contract::distributed::inbox::*;
use common::*;
use control_support::Fixture;
fn request(id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(id, "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    }
}
#[tokio::test]
async fn conformance_admission_and_schedule() {
    let f = Fixture::new().await;
    let fresh = request("fresh");
    let mut changed = fresh.clone();
    changed.input.body = InputBody::User {
        payload: Payload::Inline(serde_json::json!("different")),
    };
    let port: &StoreInboxProducer = &f.inbox;
    inbox_producer_port_contract_tests(port, fresh, changed)
        .await
        .unwrap();
    inbox_scheduling_port_contract_tests(port, port, &f.owner, request("new-activation"))
        .await
        .unwrap();
}
#[tokio::test]
async fn conformance_real_shard_feed() {
    let f = Fixture::new().await;
    let mut req = request("feed");
    let AdmitOutcome::Accepted(receipt) = f.inbox.admit(req.clone()).await.unwrap() else {
        panic!()
    };
    req.input.business_digest = receipt.business_digest;
    // open returns the real private Feed implementation behind its public port.
    let mut feed = f.inbox.open(ShardId(42)).await.unwrap();
    shard_feed_port_contract_tests(feed.as_mut(), &req.input)
        .await
        .unwrap();
}

#[tokio::test]
async fn conformance_committed_inbox_ack() {
    use bcs_service_api::port::repo::distributed::{common::*, session::*};
    use bcs_store_api::document::DocumentStorePlugin;
    use std::{num::NonZeroU32, time::Duration};
    let f = Fixture::new().await;
    f.admit("ack").await;
    let mut feed = f.inbox.open(ShardId(42)).await.unwrap();
    feed.watch(f.owner.sid.clone(), None).await.unwrap();
    let batch = feed
        .next(
            ReadLimits {
                max_sessions: NonZeroU32::MIN,
                max_entries: NonZeroU32::new(128).unwrap(),
                max_bytes: NonZeroU32::new(1024 * 1024).unwrap(),
            },
            Duration::ZERO,
        )
        .await
        .unwrap()
        .sessions
        .remove(0);
    let key = f.layout.ckpt(&f.owner.sid).unwrap();
    let stored = f.storage.read_document(&key).await.unwrap().unwrap();
    let mut envelope: CheckpointEnvelope = serde_json::from_value(stored.value).unwrap();
    envelope.checkpoint.commit_seq = CommitSeq(1);
    envelope.checkpoint.consumed = Some(batch.read_through.clone());
    envelope.checkpoint.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: CommitId("ack".into()),
        source_commit: CommitSeq(1),
        content_digest: Digest("ack-digest".into()),
        effects: vec![],
    });
    f.storage
        .replace_document(
            &key,
            stored.version,
            &serde_json::to_value(envelope).unwrap(),
        )
        .await
        .unwrap();
    let proof = CommitReceipt {
        sid: f.owner.sid.clone(),
        owner: f.owner.owner.clone(),
        commit_id: CommitId("ack".into()),
        content_digest: Digest("ack-digest".into()),
        sequence: CommitSeq(1),
        consumed: Some(batch.read_through),
        proof: "not-authority".into(),
    };
    let port: &StoreInboxProducer = &f.inbox;
    inbox_consumer_port_contract_tests(port, ShardId(42), &batch.delivery, &proof)
        .await
        .unwrap();
}
