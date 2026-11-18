#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, common::*, inbox::*, session::*};
use bcs_store_api::document::DocumentStorePlugin;
use common::*;
use std::{num::NonZeroU32, sync::Arc};
fn layout() -> KeyLayout {
    KeyLayout::new("test", "gates").unwrap()
}
fn provider(store: Arc<Recorder>) -> StoreInboxProducer {
    StoreInboxProducer::new(
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
    store
        .create_document(
            &layout().ckpt(&owner.sid).unwrap(),
            &serde_json::to_value(envelope).unwrap(),
        )
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
        .close_gate(owner, 1, RequestId("isolate".into()))
        .await
        .unwrap()
    {
        GateCloseOutcome::Closed(c) => c,
        x => panic!("{x:?}"),
    }
}
async fn simulate_authorized_reopen(store: &Recorder) {
    // Task 6 owns authorized checkpoint control + archive verification. This test
    // fixture only simulates the resulting durable next generation to exercise
    // admission fencing; it is deliberately not a provider recovery API.
    let key = layout().schedule(&Sid(SID.into())).unwrap();
    let mut d = store.read_document(&key).await.unwrap().unwrap();
    d.value["generation"] = 2.into();
    d.value["closed"] = serde_json::Value::Null;
    store
        .replace_document(&key, d.version, &d.value)
        .await
        .unwrap();
}
#[tokio::test]
async fn accepted_activation_survives_close_and_lost_confirmation() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    let o = owner(&store, &p).await;
    let pause = store.pause_after("document.replace", ":schedule:", 2);
    let task = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("accepted")).await }
    });
    pause.reached().await;
    let closure = close(&p, &o).await;
    let obligations = p
        .closure_obligations(
            &closure,
            PageRequest {
                after: None,
                limit: NonZeroU32::new(128).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(obligations.items.len(), 1);
    assert!(obligations.items[0].accepted);
    assert!(obligations.items[0].may_have_created_task);
    pause.resume();
    assert!(matches!(
        task.await.unwrap().unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    assert!(matches!(
        p.admit(request("accepted")).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
}
#[tokio::test]
async fn appended_but_unactivated_is_rejected_with_a_durable_finalization_obligation() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    let o = owner(&store, &p).await;
    let pause = store.pause_after("stream.append", ":inbox:", 1);
    let task = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("pending")).await }
    });
    pause.reached().await;
    let closure = close(&p, &o).await;
    let obligations = p
        .closure_obligations(
            &closure,
            PageRequest {
                after: None,
                limit: NonZeroU32::new(128).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(obligations.items.len(), 1);
    assert!(!obligations.items[0].accepted);
    assert!(obligations.items[0].may_have_created_task);
    pause.resume();
    assert_eq!(task.await.unwrap().unwrap(), AdmitOutcome::Quarantined);
    simulate_authorized_reopen(&store).await;
    assert_eq!(
        p.admit(request("pending")).await.unwrap(),
        AdmitOutcome::Quarantined
    );
}
#[tokio::test]
async fn old_canonical_never_rebinds_after_reopening() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    let o = owner(&store, &p).await;
    let pause = store.pause_after("document.create", ":canonical:", 1);
    let task = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("old")).await }
    });
    pause.reached().await;
    let closure = close(&p, &o).await;
    let obligations = p
        .closure_obligations(
            &closure,
            PageRequest {
                after: None,
                limit: NonZeroU32::new(128).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(obligations.items.len(), 1);
    assert!(!obligations.items[0].may_have_created_task);
    assert!(
        store
            .read_document(&layout().task(&Tid(TID.into())).unwrap())
            .await
            .unwrap()
            .is_none()
    );
    simulate_authorized_reopen(&store).await;
    pause.resume();
    assert_eq!(task.await.unwrap().unwrap(), AdmitOutcome::Quarantined);
    assert_eq!(
        p.admit(request("old")).await.unwrap(),
        AdmitOutcome::Quarantined
    );
    assert!(matches!(
        p.admit(request("fresh")).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
}
#[tokio::test]
async fn log_only_old_contender_can_converge_to_new_canonical_generation() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    let o = owner(&store, &p).await;
    let before = store.pause_after("before.document.create", ":admission-root", 1);
    let task = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("race")).await }
    });
    before.reached().await;
    let closure = close(&p, &o).await;
    assert_eq!(closure.journal_cut, 0);
    simulate_authorized_reopen(&store).await;
    let pause = store.pause_after("document.create", ":admission-root", 1);
    before.resume();
    pause.reached().await;
    assert!(matches!(
        p.admit(request("race")).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    pause.resume();
    assert!(matches!(
        task.await.unwrap().unwrap(),
        AdmitOutcome::Accepted(_) | AdmitOutcome::Duplicate(_)
    ));
    assert!(matches!(
        p.admit(request("race")).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
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

#[tokio::test]
async fn closure_skips_other_sessions_before_reading_their_corrupt_canonical() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    let mut foreign = request("foreign");
    let sid = Sid("s0042.foreign".into());
    let tid = Tid("s0042.foreign.task".into());
    foreign.input.sid = sid.clone();
    foreign.input.tid = tid.clone();
    foreign.create_session.as_mut().unwrap().sid = sid.clone();
    let task = foreign.create_task.as_mut().unwrap();
    task.sid = sid;
    task.tid = tid;
    p.admit(foreign).await.unwrap();
    p.admit(request("owned")).await.unwrap();
    let o = owner(&store, &p).await;
    let key = layout()
        .canonical_admission(&scope(), &InputId("foreign".into()))
        .unwrap();
    let old = store.model.read_document(&key).await.unwrap().unwrap();
    store
        .model
        .replace_document(&key, old.version, &serde_json::json!({"schema_version":2}))
        .await
        .unwrap();
    let closure = close(&p, &o).await;
    let page = p
        .closure_obligations(
            &closure,
            PageRequest {
                after: None,
                limit: NonZeroU32::new(128).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].request.input.input_id.0, "owned");
    assert!(page.items[0].accepted);
}

#[tokio::test]
async fn closure_includes_registered_creator_paused_before_task_exists() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    let o = owner(&store, &p).await;
    let pause = store.pause_after("before.document.create", ":task:", 1);
    let creator = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("late-create")).await }
    });
    pause.reached().await;
    let task_key = layout().task(&Tid(TID.into())).unwrap();
    assert!(
        store
            .model
            .read_document(&task_key)
            .await
            .unwrap()
            .is_none()
    );
    let closure = close(&p, &o).await;
    let obligations = p
        .closure_obligations(
            &closure,
            PageRequest {
                after: None,
                limit: NonZeroU32::new(128).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(obligations.items.len(), 1);
    assert!(!obligations.items[0].accepted);
    assert!(obligations.items[0].may_have_created_task);
    // Task 6 owns proof of archival/publication. This fixture represents its
    // create-once terminal row and checks only late admission cannot replace it.
    let mut terminal = task();
    terminal.content.status = TaskStatus::Rejected;
    terminal.source_commit = CommitSeq(2);
    store
        .model
        .create_document(&task_key, &serde_json::to_value(&terminal).unwrap())
        .await
        .unwrap();
    pause.resume();
    // The resumed producer may have retained its pre-close view. Its physical
    // continuation is stopped by canonical decision confirmation and is unknown
    // until the same identity is queried again; it cannot recreate Submitted.
    assert!(matches!(
        creator.await.unwrap(),
        Ok(AdmitOutcome::Quarantined) | Err(StoreError::OutcomeUnknown { .. })
    ));
    assert_eq!(
        p.admit(request("late-create")).await.unwrap(),
        AdmitOutcome::Quarantined
    );
    let actual: TaskSnapshot = serde_json::from_value(
        store
            .model
            .read_document(&task_key)
            .await
            .unwrap()
            .unwrap()
            .value,
    )
    .unwrap();
    assert_eq!(actual, terminal);
}

#[tokio::test]
async fn early_closure_rejection_releases_full_precanonical_reservations() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    let o = owner(&store, &p).await;
    for index in 0..8 {
        store.lose_write(1);
        assert!(
            p.admit(request(&format!("early-close-{index}")))
                .await
                .is_err()
        );
    }
    let closure = close(&p, &o).await;
    let mut after = None;
    let mut obligations = 0;
    for _ in 0..20 {
        let page = p
            .closure_obligations(
                &closure,
                PageRequest {
                    after,
                    limit: NonZeroU32::new(128).unwrap(),
                },
            )
            .await
            .unwrap();
        obligations += page.items.len();
        let Some(next) = page.next else { break };
        after = Some(next);
    }
    assert_eq!(obligations, 8);
    for _ in 0..8 {
        let _ = p.repair(ShardId(42)).await;
    }
    let root = store
        .model
        .read_document(&layout().admission_root(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert!(root.value["pending"].as_object().unwrap().is_empty());
}
