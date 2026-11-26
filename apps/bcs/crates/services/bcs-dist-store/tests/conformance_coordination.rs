#![allow(clippy::unwrap_used, reason = "actual coordination conformance")]
use bcs_dist_model::*;
use bcs_dist_store::{coordination::CoordinationStore, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{ownership::*, session::*};
use bcs_test_support::contract::distributed::state::*;
use bcs_test_support::store_model::Model;
use std::sync::Arc;
#[tokio::test]
async fn conformance_membership_shard_and_checkpoint() {
    let m = Arc::new(Model::default());
    let port = CoordinationStore::new(
        m.clone(),
        m.clone(),
        m.clone(),
        KeyLayout::new("contract", "coordination").unwrap(),
    )
    .unwrap();
    let me = Instance {
        node: "worker".into(),
        incarnation: "one".into(),
    };
    membership_port_contract_tests(&port, me.clone())
        .await
        .unwrap();
    // Membership harness drains its instance; use an independently registered
    // live owner for shard acquisition.
    let me = Instance {
        incarnation: "two".into(),
        ..me
    };
    port.register(
        me.clone(),
        RuntimeRole::Worker,
        "http://worker.example.test".into(),
        std::time::Duration::from_secs(60),
    )
    .await
    .unwrap();
    shard_lease_port_contract_tests(
        &port,
        ClaimShard {
            shard: ShardId(42),
            me,
            expected_epoch: None,
            request_id: RequestId("claim".into()),
        },
    )
    .await
    .unwrap();
    let claim = port.current(ShardId(42)).await.unwrap().unwrap().claim;
    let checkpoint = Checkpoint {
        format_version: 2,
        commit_seq: CommitSeq(0),
        consumed: None,
        tasks: Default::default(),
        downstream_generation: 0,
        pending_cancel_did: None,
        runner_state: None,
        dispatch_seq: Default::default(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: Default::default(),
        last_event_id: None,
        outbox: None,
    };
    let AcquireOutcome::Acquired(acquired) = port
        .acquire(&Sid("s0042.contract".into()), &claim, checkpoint)
        .await
        .unwrap()
    else {
        panic!()
    };
    let mut candidate = acquired.envelope.checkpoint;
    candidate.commit_seq = CommitSeq(1);
    let id = CommitId("commit".into());
    let digest = Digest("contract".into());
    candidate.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: id.clone(),
        source_commit: CommitSeq(1),
        content_digest: digest.clone(),
        effects: vec![],
    });
    session_store_port_contract_tests(
        &port,
        CommitRequest {
            purpose: CommitPurpose::Ordinary {
                control_revision: 0,
            },
            owner: acquired.ownership,
            expected_commit_seq: CommitSeq(0),
            commit_id: id,
            content_digest: digest,
            candidate,
        },
    )
    .await
    .unwrap();
}
