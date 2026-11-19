#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "control protocol tests"
)]
use bcs_dist_model::*;
use bcs_dist_store::{coordination::CoordinationStore, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{common::*, control::*, session::*};
use bcs_store_api::document::DocumentStorePlugin;
use bcs_test_support::store_model::Model;
use std::sync::Arc;
fn sid() -> Sid {
    Sid("s0001.session".into())
}
fn owner() -> SessionOwnership {
    SessionOwnership {
        sid: sid(),
        shard: ShardId(1),
        owner: ShardOwner {
            instance: Instance {
                node: "worker".into(),
                incarnation: "one".into(),
            },
            epoch: WorkerEpoch(1),
        },
    }
}
fn initial() -> Checkpoint {
    Checkpoint {
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
    }
}
async fn setup() -> (Arc<Model>, CoordinationStore, KeyLayout) {
    let model = Arc::new(Model::default());
    let layout = KeyLayout::new("control", "test").unwrap();
    let store = CoordinationStore::new(model.clone(), model.clone(), model.clone(), layout.clone())
        .unwrap();
    let envelope = CheckpointEnvelope {
        owner: owner().owner,
        checkpoint: initial(),
        recovery: RecoveryMetadata {
            takeover_streak: 0,
            last_attempt: None,
            quarantined: false,
            last_active_at: TimestampMillis(0),
        },
    };
    model
        .create_document(
            &layout.ckpt(&sid()).unwrap(),
            &serde_json::to_value(envelope).unwrap(),
        )
        .await
        .unwrap();
    (model, store, layout)
}
fn guard(revision: u64) -> ControlGuard {
    ControlGuard {
        owner: owner(),
        expected_head: CommitSeq(0),
        expected_revision: revision,
    }
}
fn attempt(id: &str) -> AttemptIdentity {
    AttemptIdentity {
        id: RequestId(id.into()),
        head: CommitSeq(0),
        state_digest: Digest("state".into()),
        input_digest: Digest("input".into()),
        context_digest: Digest("context".into()),
        frozen_now: TimestampMillis(123),
    }
}
#[tokio::test]
async fn deterministic_failure_is_atomic_idempotent_isolation_without_business_mutation() {
    let (model, store, layout) = setup().await;
    assert!(matches!(
        store.begin_attempt(guard(0), attempt("a")).await.unwrap(),
        ControlOutcome::Applied(_)
    ));
    let outcome = AttemptOutcome::Deterministic {
        code: "InvalidDecision".into(),
        isolation_id: RequestId("isolate-a".into()),
        safe_to_finalize: true,
    };
    model.lose_next_response();
    assert!(matches!(
        store
            .finish_attempt(guard(1), RequestId("a".into()), outcome.clone())
            .await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    let ControlOutcome::AlreadyApplied(snapshot) = store
        .finish_attempt(guard(1), RequestId("a".into()), outcome)
        .await
        .unwrap()
    else {
        panic!()
    };
    assert!(matches!(
        snapshot.control.mode,
        ControlMode::Quarantined { .. }
    ));
    assert_eq!(snapshot.control.revision, 2);
    let raw = model
        .read_document(&layout.ckpt(&sid()).unwrap())
        .await
        .unwrap()
        .unwrap()
        .value;
    assert_eq!(raw["checkpoint"], serde_json::to_value(initial()).unwrap());
    assert!(matches!(
        store.begin_attempt(guard(2), attempt("b")).await.unwrap(),
        ControlOutcome::NotReady
    ));
}
#[tokio::test]
async fn corrupt_business_body_still_records_blocked_reason_and_preserves_evidence() {
    let (model, store, layout) = setup().await;
    store.begin_attempt(guard(0), attempt("a")).await.unwrap();
    let key = layout.ckpt(&sid()).unwrap();
    let mut raw = model.read_document(&key).await.unwrap().unwrap();
    raw.value["checkpoint"]["runner_state"] = serde_json::json!({"bad":"shape"});
    let checkpoint = raw.value["checkpoint"].clone();
    model
        .replace_document(&key, raw.version, &raw.value)
        .await
        .unwrap();
    let outcome = AttemptOutcome::Deterministic {
        code: "BadState".into(),
        isolation_id: RequestId("isolate-a".into()),
        safe_to_finalize: true,
    };
    let ControlOutcome::Applied(result) = store
        .finish_attempt(guard(1), RequestId("a".into()), outcome)
        .await
        .unwrap()
    else {
        panic!()
    };
    assert!(matches!(result.control.mode, ControlMode::Blocked { .. }));
    assert_eq!(
        model.read_document(&key).await.unwrap().unwrap().value["checkpoint"],
        checkpoint
    );
}
#[tokio::test]
async fn attempt_identity_and_outcome_survive_replacement_and_stale_guards_cannot_mutate() {
    let (_, store, _) = setup().await;
    store.begin_attempt(guard(0), attempt("a")).await.unwrap();
    let success = AttemptOutcome::Succeeded {
        decision_digest: Digest("decision".into()),
    };
    store
        .finish_attempt(guard(1), RequestId("a".into()), success.clone())
        .await
        .unwrap();
    store.begin_attempt(guard(2), attempt("b")).await.unwrap();
    assert_eq!(
        store
            .get_attempt(&sid(), &RequestId("a".into()))
            .await
            .unwrap()
            .unwrap()
            .outcome,
        Some(success)
    );
    let mut stale = guard(3);
    stale.owner.owner.epoch = WorkerEpoch(0);
    assert!(matches!(
        store
            .finish_attempt(
                stale,
                RequestId("b".into()),
                AttemptOutcome::Transient {
                    code: "retry".into()
                }
            )
            .await
            .unwrap(),
        ControlOutcome::Fenced
    ));
    let mut conflict = attempt("b");
    conflict.input_digest = Digest("changed".into());
    assert!(matches!(
        store.begin_attempt(guard(3), conflict).await.unwrap(),
        ControlOutcome::ContentConflict
    ));
}

#[tokio::test]
async fn control_acquisition_seals_corrupt_body_under_a_valid_successor_claim() {
    use bcs_service_api::port::repo::distributed::ownership::*;
    let (model, store, layout) = setup().await;
    let successor = Instance {
        node: "successor".into(),
        incarnation: "two".into(),
    };
    store
        .register(
            successor.clone(),
            RuntimeRole::Worker,
            "http://successor.example.test".into(),
            std::time::Duration::from_secs(3),
        )
        .await
        .unwrap();
    let LeaseClaim::Claimed(claim) = store
        .claim(ClaimShard {
            shard: ShardId(1),
            me: successor,
            expected_epoch: None,
            request_id: RequestId("claim".into()),
        })
        .await
        .unwrap()
    else {
        panic!()
    };
    let key = layout.ckpt(&sid()).unwrap();
    let mut raw = model.read_document(&key).await.unwrap().unwrap();
    raw.value["owner"]["epoch"] = 0.into();
    raw.value["checkpoint"]["format_version"] = 999.into();
    let body = raw.value["checkpoint"].clone();
    model
        .replace_document(&key, raw.version, &raw.value)
        .await
        .unwrap();
    let ControlAcquireOutcome::Acquired(snapshot) =
        store.acquire_control(&sid(), &claim).await.unwrap()
    else {
        panic!()
    };
    assert_eq!(snapshot.owner.owner, claim.owner);
    assert_eq!(
        snapshot.checkpoint_digest,
        bcs_dist_store::identity::digest_json(&body)
    );
    assert_eq!(
        model.read_document(&key).await.unwrap().unwrap().value["checkpoint"],
        body
    );
}

#[tokio::test]
async fn missing_control_acquisitions_do_not_exhaust_local_capacity() {
    use bcs_service_api::port::repo::distributed::ownership::*;
    let model = Arc::new(Model::default());
    let layout = KeyLayout::new("control", "capacity").unwrap();
    let store = CoordinationStore::with_config(
        model.clone(),
        model.clone(),
        model,
        layout,
        bcs_dist_store::coordination::CoordinationStoreConfig {
            max_cached_sessions: 1,
            max_handoff_timers: 1,
        },
    )
    .unwrap();
    let me = owner().owner.instance;
    store
        .register(
            me.clone(),
            RuntimeRole::Worker,
            "http://worker.example.test".into(),
            std::time::Duration::from_secs(3),
        )
        .await
        .unwrap();
    let LeaseClaim::Claimed(claim) = store
        .claim(ClaimShard {
            shard: ShardId(1),
            me,
            expected_epoch: None,
            request_id: RequestId("claim".into()),
        })
        .await
        .unwrap()
    else {
        panic!()
    };
    for index in 0..3 {
        assert!(matches!(
            store
                .acquire_control(&Sid(format!("s0001.missing-{index}")), &claim)
                .await
                .unwrap(),
            ControlAcquireOutcome::Missing
        ));
    }
}

#[tokio::test]
async fn control_acquisition_preserves_known_existence_across_missing_business_data() {
    use bcs_service_api::port::repo::distributed::ownership::*;
    use bcs_store_api::{
        Expiry, WriteCondition,
        json::{JsonMutation, JsonStorePlugin},
    };
    for raw in [false, true] {
        let (model, store, layout) = setup().await;
        let me = owner().owner.instance;
        store
            .register(
                me.clone(),
                RuntimeRole::Worker,
                "http://worker.example.test".into(),
                std::time::Duration::from_secs(3),
            )
            .await
            .unwrap();
        let LeaseClaim::Claimed(claim) = store
            .claim(ClaimShard {
                shard: ShardId(1),
                me,
                expected_epoch: None,
                request_id: RequestId("claim".into()),
            })
            .await
            .unwrap()
        else {
            panic!()
        };
        if raw {
            assert!(matches!(
                store.acquire_control(&sid(), &claim).await.unwrap(),
                ControlAcquireOutcome::Acquired(_)
            ));
        } else {
            assert!(matches!(
                store.acquire(&sid(), &claim, initial()).await.unwrap(),
                AcquireOutcome::Acquired(_)
            ));
        }
        let key = layout.ckpt(&sid()).unwrap();
        model
            .mutate_json(
                &key,
                WriteCondition::Any,
                JsonMutation::Delete,
                Expiry::Persistent,
            )
            .await
            .unwrap();
        assert!(store.acquire_control(&sid(), &claim).await.is_err());
        assert!(store.acquire(&sid(), &claim, initial()).await.is_err());
        assert!(store.acquire(&sid(), &claim, initial()).await.is_err());
        assert!(model.read_document(&key).await.unwrap().is_none());
    }
}

#[tokio::test]
async fn successful_control_write_refreshes_a_previously_cached_business_checkpoint() {
    use bcs_service_api::port::repo::distributed::ownership::*;
    let (_, store, _) = setup().await;
    let me = owner().owner.instance;
    store
        .register(
            me.clone(),
            RuntimeRole::Worker,
            "http://worker.example.test".into(),
            std::time::Duration::from_secs(3),
        )
        .await
        .unwrap();
    let LeaseClaim::Claimed(claim) = store
        .claim(ClaimShard {
            shard: ShardId(1),
            me,
            expected_epoch: None,
            request_id: RequestId("claim".into()),
        })
        .await
        .unwrap()
    else {
        panic!()
    };
    assert!(matches!(
        store.acquire(&sid(), &claim, initial()).await.unwrap(),
        AcquireOutcome::Acquired(_)
    ));
    store.begin_attempt(guard(0), attempt("a")).await.unwrap();
    store
        .finish_attempt(
            guard(1),
            RequestId("a".into()),
            AttemptOutcome::Succeeded {
                decision_digest: Digest("ok".into()),
            },
        )
        .await
        .unwrap();
    let mut candidate = initial();
    candidate.commit_seq = CommitSeq(1);
    candidate.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: CommitId("commit".into()),
        source_commit: CommitSeq(1),
        content_digest: Digest("content".into()),
        effects: vec![],
    });
    assert!(matches!(
        store
            .commit(CommitRequest {
                purpose: CommitPurpose::Ordinary {
                    control_revision: 2
                },
                owner: owner(),
                expected_commit_seq: CommitSeq(0),
                commit_id: CommitId("commit".into()),
                content_digest: Digest("content".into()),
                candidate
            })
            .await
            .unwrap(),
        CommitOutcome::Committed(_)
    ));
}

#[tokio::test]
async fn an_old_unfinished_attempt_cannot_isolate_a_new_business_head_with_a_refreshed_guard() {
    let (_, store, _) = setup().await;
    store.begin_attempt(guard(0), attempt("old")).await.unwrap();
    let mut candidate = initial();
    candidate.commit_seq = CommitSeq(1);
    candidate.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: CommitId("advance".into()),
        source_commit: CommitSeq(1),
        content_digest: Digest("advance".into()),
        effects: vec![],
    });
    store
        .commit(CommitRequest {
            purpose: CommitPurpose::Ordinary {
                control_revision: 1,
            },
            owner: owner(),
            expected_commit_seq: CommitSeq(0),
            commit_id: CommitId("advance".into()),
            content_digest: Digest("advance".into()),
            candidate,
        })
        .await
        .unwrap();
    let mut refreshed = guard(1);
    refreshed.expected_head = CommitSeq(1);
    assert!(matches!(
        store
            .finish_attempt(
                refreshed,
                RequestId("old".into()),
                AttemptOutcome::Deterministic {
                    code: "OldFailure".into(),
                    isolation_id: RequestId("old-isolation".into()),
                    safe_to_finalize: true
                }
            )
            .await
            .unwrap(),
        ControlOutcome::HeadConflict
    ));
    assert!(matches!(
        store
            .load_control(&sid())
            .await
            .unwrap()
            .unwrap()
            .control
            .mode,
        ControlMode::Normal
    ));
}

#[tokio::test]
async fn separate_control_store_cannot_leave_a_legitimate_commit_stuck_on_cached_revision() {
    use bcs_service_api::port::repo::distributed::ownership::*;
    let (model, store, layout) = setup().await;
    let other = CoordinationStore::new(model.clone(), model.clone(), model, layout).unwrap();
    let me = owner().owner.instance;
    store
        .register(
            me.clone(),
            RuntimeRole::Worker,
            "http://worker.example.test".into(),
            std::time::Duration::from_secs(3),
        )
        .await
        .unwrap();
    let LeaseClaim::Claimed(claim) = store
        .claim(ClaimShard {
            shard: ShardId(1),
            me,
            expected_epoch: None,
            request_id: RequestId("claim".into()),
        })
        .await
        .unwrap()
    else {
        panic!()
    };
    store.acquire(&sid(), &claim, initial()).await.unwrap();
    other
        .begin_attempt(guard(0), attempt("external"))
        .await
        .unwrap();
    other
        .finish_attempt(
            guard(1),
            RequestId("external".into()),
            AttemptOutcome::Succeeded {
                decision_digest: Digest("decision".into()),
            },
        )
        .await
        .unwrap();
    assert_eq!(
        store
            .load_control(&sid())
            .await
            .unwrap()
            .unwrap()
            .control
            .revision,
        2
    );
    let mut candidate = initial();
    candidate.commit_seq = CommitSeq(1);
    candidate.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: CommitId("commit".into()),
        source_commit: CommitSeq(1),
        content_digest: Digest("commit".into()),
        effects: vec![],
    });
    assert!(matches!(
        store
            .commit(CommitRequest {
                purpose: CommitPurpose::Ordinary {
                    control_revision: 2
                },
                owner: owner(),
                expected_commit_seq: CommitSeq(0),
                commit_id: CommitId("commit".into()),
                content_digest: Digest("commit".into()),
                candidate
            })
            .await
            .unwrap(),
        CommitOutcome::Committed(_)
    ));
}
