#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{
    admission::*, common::*, control::*, inbox::*, session::*,
};
use bcs_store_api::document::DocumentStorePlugin;
use common::*;
use std::sync::Arc;
fn layout() -> KeyLayout {
    KeyLayout::new("test", "gates").unwrap()
}
fn provider(store: Arc<Recorder>) -> StoreInboxProducer {
    StoreInboxProducer::new(
        Arc::new(
            bcs_dist_store::budget::SharedScanBudget::new(
                bcs_dist_store::budget::ScanBudgetConfig::default(),
            )
            .unwrap(),
        ),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        layout(),
        Arc::new(FixedClock(100)),
        InboxProducerConfig::new(BackendId("model".into())),
    )
    .unwrap()
}
fn request(id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(id, "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    }
}
async fn owner(store: &Recorder, p: &StoreInboxProducer) -> SessionOwnership {
    let owner = SessionOwnership {
        sid: Sid(SID.into()),
        shard: ShardId(42),
        owner: ShardOwner {
            instance: Instance {
                node: "worker".into(),
                incarnation: "one".into(),
            },
            epoch: WorkerEpoch(1),
        },
    };
    let envelope = CheckpointEnvelope {
        owner: owner.owner.clone(),
        checkpoint: Checkpoint {
            format_version: 1,
            commit_seq: CommitSeq(0),
            consumed: None,
            tasks: Default::default(),
            runner_state: None,
            dispatch_seq: Default::default(),
            deadlines: vec![],
            deadline_history: Default::default(),
            view_drafts: Default::default(),
            last_event_id: None,
            outbox: None,
        },
        recovery: RecoveryMetadata {
            takeover_streak: 0,
            last_attempt: None,
            quarantined: false,
            last_active_at: TimestampMillis(100),
        },
    };
    let mut raw = serde_json::to_value(envelope).unwrap();
    let mut control = SessionControl {
        revision: 1,
        ..Default::default()
    };
    control.mode = ControlMode::Quarantined {
        isolation: IsolationRecord {
            operation_id: RequestId("isolate".into()),
            reason: "test".into(),
            head: CommitSeq(0),
            failed_attempt: RequestId("attempt".into()),
            closure: None,
            completion: None,
        },
    };
    raw["control"] = serde_json::to_value(control).unwrap();
    store
        .create_document(&layout().ckpt(&owner.sid).unwrap(), &raw)
        .await
        .unwrap();
    assert!(matches!(
        p.observe(&owner).await.unwrap(),
        ScheduleObservation::Observed(_)
    ));
    owner
}
async fn close(p: &StoreInboxProducer, owner: &SessionOwnership) -> AdmissionClosure {
    match p
        .close_gate(
            ControlGuard {
                owner: owner.clone(),
                expected_head: CommitSeq(0),
                expected_revision: 1,
            },
            1,
            RequestId("isolate".into()),
        )
        .await
        .unwrap()
    {
        GateCloseOutcome::Closed(c) => c,
        x => panic!("{x:?}"),
    }
}
#[tokio::test]
async fn critical_event_is_stable_across_holders_and_survives_a_closed_gate() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    p.admit(request("start")).await.unwrap();
    let o = owner(&store, &p).await;
    close(&p, &o).await;
    let mut r = request("remote-final");
    r.create_session = None;
    r.create_task = None;
    r.input.body = InputBody::Agent {
        event: AgentEvent {
            did: Did(format!("{TID}.d")),
            holder_epoch: HolderEpoch(1),
            seq: InputSeq(7),
            kind: AgentEventKind::Final,
            remote_event_id: Some("upstream-stable".into()),
            payload: Payload::Inline(serde_json::json!("result")),
        },
    };
    r.input.input_id = bcs_dist_store::identity::critical_agent_input_id(
        &Did(format!("{TID}.d")),
        "upstream-stable",
    );
    let mut forged = r.clone();
    forged.input.input_id = InputId("holder-specific-id".into());
    assert!(
        matches!(p.admit(forged).await,Err(StoreError::Invalid{code}) if code=="critical_input_id_mismatch")
    );
    assert!(matches!(
        p.admit(r.clone()).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    if let InputBody::Agent { event } = &mut r.input.body {
        event.holder_epoch = HolderEpoch(9);
        event.seq = InputSeq(999);
    }
    assert!(matches!(
        p.admit(r.clone()).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
    if let InputBody::Agent { event } = &mut r.input.body {
        event.payload = Payload::Inline(serde_json::json!("changed"));
    }
    assert_eq!(p.admit(r).await.unwrap(), AdmitOutcome::ContentConflict);
}
#[tokio::test]
async fn forged_owner_and_old_activation_tokens_do_not_authorize_settle() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    p.admit(request("start")).await.unwrap();
    let o = owner(&store, &p).await;
    let ScheduleObservation::Observed(token) = p.observe(&o).await.unwrap() else {
        panic!()
    };
    p.admit(request("again")).await.unwrap();
    assert_eq!(
        p.settle(SettleSession {
            owner: o.clone(),
            observed: token,
            next_check: None
        })
        .await
        .unwrap(),
        SettleOutcome::NeedsRecheck
    );
    let mut forged = o.clone();
    forged.owner.epoch = WorkerEpoch(500);
    assert_eq!(
        p.observe(&forged).await.unwrap(),
        ScheduleObservation::Fenced
    );
    forged = o;
    forged.owner.instance.incarnation = "wrong".into();
    assert_eq!(
        p.observe(&forged).await.unwrap(),
        ScheduleObservation::Fenced
    );
}
