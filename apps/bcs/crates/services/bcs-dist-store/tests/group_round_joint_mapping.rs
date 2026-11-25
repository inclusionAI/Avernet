//! S1.4 joint-version contract-mapping tests. T1 evidence only: this target
//! wires the real merged #4245 joints (CoordinationStore checkpoint CAS,
//! EffectVerifier-authorized outbox, committed TaskFeed, SQLite archive, real
//! Inbox producer) behind one KeyLayout and walks the prototype's selected
//! atomic scope over them — admit CAS -> terminal CAS -> durable readback ->
//! dual feed permits — WITHOUT multi-key transactions and without the S1.1
//! test fixtures. Unmappable seams are asserted as documented gaps (T7), not
//! papered over: a committed-but-never-applied feed permit cannot be verified
//! after the checkpoint head advances, and a fabricated remote event id is
//! never needed because `admit_connector` derives key-event identity from
//! (did, KeyEvent, state).
#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "joint contract-mapping assertions"
)]

mod common;

use bcs_db_local::LocalSqliteDbPlugin;
use bcs_dist_encoding::connector::{self, KeyEvent};
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::{
    archive::DbArchiveStore,
    budget::{ScanBudgetConfig, SharedScanBudget},
    coordination::CoordinationStore,
    identity::digest_json,
    inbox::{InboxProducerConfig, StoreInboxProducer},
    layout::KeyLayout,
    task_feed::{StoreTaskFeed, TaskFeedConfig, TaskFeedStores},
};
use bcs_service_api::port::repo::distributed::{
    common::*, inbox::*, ownership::*, result::*, session::*, task_feed::*,
};
use bcs_test_support::store_model::Model;
use common::{SID, TID, FixedClock, scope, spec, task, user_input};
use std::{
    collections::VecDeque,
    num::NonZeroU32,
    sync::Arc,
    time::Duration,
};

/// Logical clock for the real Inbox producer, mirroring the admission tests.
const NOW: i64 = 1_794_371_200_123;

fn kind_digest(value: &impl serde::Serialize) -> Digest {
    digest_json(&serde_json::to_value(value).unwrap())
}

fn empty_checkpoint() -> Checkpoint {
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
        feed_publications: Vec::new(),
        outbox: None,
    }
}

fn sid() -> Sid {
    Sid(SID.into())
}
fn tid() -> Tid {
    Tid(TID.into())
}

/// The real #4245 joints behind one KeyLayout and one shared store model.
struct Joint {
    model: Arc<Model>,
    store: CoordinationStore,
    lease: MembershipLease,
    claim: ShardClaim,
    feed: StoreTaskFeed,
    inbox: StoreInboxProducer,
    archive: Arc<DbArchiveStore>,
    layout: KeyLayout,
}

impl Joint {
    async fn new() -> Self {
        Self::at_epoch(Arc::new(Model::default()), None, None).await
    }

    /// A new owner generation over the SAME model/db: sealed-successor
    /// semantics, not a fresh namespace.
    async fn takeover(&self) -> Joint {
        self.store.retire(&self.lease).await.unwrap();
        let previous = self.store.current(ShardId(42)).await.unwrap().map(|record| record.claim.owner.epoch.0);
        Self::at_epoch(self.model.clone(), previous, Some((self.feed.clone(), self.archive.clone(), self.inbox.clone()))).await
    }

    async fn at_epoch(
        model: Arc<Model>,
        previous_epoch: Option<u64>,
        reuse: Option<(StoreTaskFeed, Arc<DbArchiveStore>, StoreInboxProducer)>,
    ) -> Joint {
        let layout = KeyLayout::new("s14", "joint").unwrap();
        let store = CoordinationStore::new(
            model.clone(),
            model.clone(),
            model.clone(),
            layout.clone(),
        )
        .unwrap();
        let (lease, claim) = Self::claim_shard(&store, previous_epoch).await;
        let (feed, archive, inbox) = match reuse {
            Some(parts) => parts,
            None => {
                let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
                bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
                let archive = Arc::new(
                    DbArchiveStore::new(db, model.clone(), layout.clone()).unwrap(),
                );
                let feed = StoreTaskFeed::new(
                    TaskFeedStores {
                        documents: model.clone(),
                        streams: model.clone(),
                        index: model.clone(),
                        blobs: archive.clone(),
                        results: archive.clone(),
                    },
                    Arc::new(OutputProjectionService),
                    layout.clone(),
                    TaskFeedConfig::default(),
                )
                .unwrap();
                let inbox = StoreInboxProducer::new(
                    Arc::new(
                        SharedScanBudget::new(ScanBudgetConfig::default()).unwrap(),
                    ),
                    Arc::new(OutputProjectionService),
                    model.clone(),
                    model.clone(),
                    model.clone(),
                    model.clone(),
                    layout.clone(),
                    Arc::new(FixedClock(NOW)),
                    InboxProducerConfig::new(BackendId("joint".into())),
                )
                .unwrap();
                (feed, archive, inbox)
            }
        };
        Joint { model, store, lease, claim, feed, inbox, archive, layout }
    }

    async fn claim_shard(store: &CoordinationStore, previous_epoch: Option<u64>) -> (MembershipLease, ShardClaim) {
        let epoch = previous_epoch.map_or(1, |previous| previous + 1);
        let me = Instance { node: "worker".into(), incarnation: epoch.to_string() };
        let lease = store
            .register(
                me.clone(),
                RuntimeRole::Worker,
                "http://worker.example.test".into(),
                Duration::from_secs(600),
            )
            .await
            .unwrap();
        let claim = match store
            .claim(ClaimShard {
                shard: ShardId(42),
                me,
                expected_epoch: (epoch > 1).then(|| WorkerEpoch(epoch - 1)),
                request_id: RequestId(format!("claim-{epoch}")),
            })
            .await
            .unwrap()
        {
            LeaseClaim::Claimed(c) | LeaseClaim::AlreadyClaimed(c) => c,
            other => panic!("{other:?}"),
        };
        (lease, claim)
    }

    async fn acquire(&self) -> SessionOwnership {
        match self.store.acquire(&sid(), &self.claim, empty_checkpoint()).await.unwrap() {
            AcquireOutcome::Acquired(acquired) => acquired.ownership,
            _ => panic!("fenced at acquisition"),
        }
    }

    /// One checkpoint exact-version CAS. `content_digest` mirrors the S1.1
    /// prototype (runner-state content); effects carry their own kind digests.
    async fn commit(&self, owner: &SessionOwnership, candidate: Checkpoint) -> CommitOutcome {
        let id = format!("c{}", candidate.commit_seq.0);
        self.commit_as(owner, candidate, id).await
    }

    /// Same CAS with an explicit commit id: a competing writer uses its own id,
    /// reply-loss retries reuse the winner's id.
    async fn commit_as(
        &self,
        owner: &SessionOwnership,
        mut candidate: Checkpoint,
        id: String,
    ) -> CommitOutcome {
        let sequence = candidate.commit_seq.0;
        let commit_id = CommitId(id);
        let digest = digest_json(&serde_json::to_value(&candidate.runner_state).unwrap());
        candidate.outbox = Some(CommittedBatch {
            created_at: None,
            commit_id: commit_id.clone(),
            source_commit: CommitSeq(sequence),
            content_digest: digest.clone(),
            effects: candidate.outbox.map(|batch| batch.effects).unwrap_or_default(),
        });
        self.store
            .commit(CommitRequest {
                purpose: CommitPurpose::Ordinary { control_revision: 0 },
                owner: owner.clone(),
                expected_commit_seq: CommitSeq(sequence - 1),
                commit_id,
                content_digest: digest,
                candidate,
            })
            .await
            .unwrap()
    }

    async fn admit_session(&self) -> InputEnvelope {
        let request = AdmitRequest {
            scope: scope(),
            input: user_input("joint-round-1", "hello"),
            create_session: Some(spec("joint-group")),
            create_task: Some(task()),
        };
        match self.inbox.admit(request.clone()).await.unwrap() {
            AdmitOutcome::Accepted(receipt) | AdmitOutcome::Duplicate(receipt) => {
                let mut input = request.input;
                input.business_digest = receipt.business_digest.clone();
                input
            }
            outcome => panic!("{outcome:?}"),
        }
    }
}

fn running_task() -> TaskProgress {
    TaskProgress {
        steps: 1,
        tid: tid(),
        phase: TaskPhase::Running,
        input: Payload::Inline(serde_json::json!({"text": "hello", "request": "joint-round-1"})),
        result_ref: None,
        output_limits: PHASE_ONE_OUTPUT_LIMITS,
        output_usage: OutputUsage::default(),
        publication: Default::default(),
        finalization: None,
    }
}

fn display_snapshot() -> serde_json::Value {
    serde_json::to_value(DisplaySnapshot {
        format_version: 1,
        units: vec![DisplayUnit {
            node: NodeId("answer".into()),
            artifact_id: ArtifactId("answer".into()),
            class: OutputClass::Output,
            content: serde_json::json!("final answer"),
        }],
    })
    .unwrap()
}

fn final_record(sequence: u64) -> FinalTaskRecord {
    FinalTaskRecord {
        schema_version: 1,
        scope: scope(),
        sid: sid(),
        tid: tid(),
        status: TerminalStatus::Completed,
        snapshot: Payload::Inline(display_snapshot()),
        result: Payload::Inline(serde_json::json!({"finished": true})),
        source_commit: CommitSeq(sequence),
        finalized_at: TimestampMillis(NOW as i64),
    }
}

fn terminal_event(commit: u64, reference: BlobRef) -> OutputEvent {
    OutputEvent {
        id: EventId { commit: CommitSeq(commit), index: NonZeroU32::MIN },
        tid: tid(),
        body: OutputBody::Terminal { status: TerminalStatus::Completed, result_ref: reference },
    }
}

/// Published terminal snapshot; mirrors processor transitions::published.
fn published_snapshot(commit: u64, reference: BlobRef) -> TaskSnapshot {
    let mut snapshot = task();
    snapshot.content = TaskContent {
        status: TaskStatus::Completed,
        interactions: vec![],
        snapshot: Payload::Inline(display_snapshot()),
        result_ref: Some(reference),
    };
    snapshot.output_usage = OutputUsage { cumulative_bytes: 32 };
    snapshot.source_commit = CommitSeq(commit);
    snapshot.snapshot_event_id = Some(EventId { commit: CommitSeq(commit), index: NonZeroU32::MIN });
    snapshot.updated_at = TimestampMillis(NOW as i64);
    snapshot
}

#[tokio::test]
async fn joint_admit_terminal_feed_readback_survives_head_advance_and_takeover() {
    let joint = Joint::new().await;
    // Merged canonical bootstrap: real Inbox admit seeds spec + initial task.
    let input = joint.admit_session().await;
    assert_eq!(input.tid, tid());

    // --- admit CAS: one round installed by the checkpoint exact-version CAS ---
    let owner = joint.acquire().await;
    let mut admitted = empty_checkpoint();
    admitted.commit_seq = CommitSeq(1);
    admitted.tasks = VecDeque::from([running_task()]);
    admitted.runner_state = Some(TaskRunnerState {
        tid: tid(),
        state: RunnerState {
            format_version: 1,
            value: serde_json::json!({"round": 1, "generation": 1, "request": "joint-round-1"}),
        },
    });
    let admitted_state = admitted.runner_state.clone();
    assert!(matches!(
        joint.commit(&owner, admitted.clone()).await,
        CommitOutcome::Committed(_)
    ));

    // A competing admission CAS at the same head loses; losers never auto-run.
    // Reusing the winner's commit id with different content would instead read
    // back as ContentConflict (the reply-anomaly signal).
    let mut loser = admitted;
    loser.runner_state.as_mut().unwrap().state.value = serde_json::json!({"round": 1, "generation": 1, "request": "competing"});
    assert_eq!(
        joint.commit_as(&owner, loser, "c1-competing".into()).await,
        CommitOutcome::CommitSeqConflict
    );
    let mut retry = empty_checkpoint();
    retry.commit_seq = CommitSeq(1);
    retry.runner_state = Some(TaskRunnerState {
        tid: tid(),
        state: RunnerState {
            format_version: 1,
            value: serde_json::json!({"round": 1, "generation": 1, "request": "changed-on-retry"}),
        },
    });
    assert_eq!(
        joint.commit_as(&owner, retry, "c1".into()).await,
        CommitOutcome::ContentConflict
    );

    // --- terminal CAS, archive first: finalization frozen in commit 2 ---
    let record = final_record(2);
    let mut finalizing = empty_checkpoint();
    finalizing.commit_seq = CommitSeq(2);
    let mut task = running_task();
    task.phase = TaskPhase::AwaitingResult { status: TerminalStatus::Completed };
    task.finalization = Some(TaskFinalization {
        operation_id: RequestId("finalize-1".into()),
        record: record.clone(),
        terminal_event_id: None,
        terminal_published: false,
    });
    finalizing.tasks = VecDeque::from([task]);
    finalizing.runner_state = admitted_state.clone();
    finalizing.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: CommitId("c2".into()),
        source_commit: CommitSeq(2),
        content_digest: Digest("unused".into()),
        effects: vec![Effect {
            id: EffectId { sid: sid(), commit: CommitSeq(2), index: NonZeroU32::MIN },
            content_digest: kind_digest(&EffectKind::SaveResult { record: record.clone() }),
            kind: EffectKind::SaveResult { record: record.clone() },
        }],
    });
    let receipt2 = match joint.commit(&owner, finalizing).await {
        CommitOutcome::Committed(receipt) => receipt,
        outcome => panic!("{outcome:?}"),
    };
    // Lost commit replies resolve by identity on the real joint.
    assert_eq!(
        joint.store.resolve_commit(&owner, &receipt2.commit_id, &receipt2.content_digest).await.unwrap(),
        CommitResolution::Committed(receipt2.clone())
    );

    // The merged dedupe applies the archive exactly once under stable provenance.
    let source2 = EffectSource {
        commit: receipt2.clone(),
        effect_id: EffectId { sid: sid(), commit: CommitSeq(2), index: NonZeroU32::MIN },
        content_digest: kind_digest(&EffectKind::SaveResult { record: record.clone() }),
    };
    let SavedResult::Saved(reference) = joint.archive.save_final(source2.clone(), record.clone()).await.unwrap()
    else { panic!("save_final") };

    // --- terminal commit: Done + the dual feed permits in ONE checkpoint CAS ---
    let mut done = empty_checkpoint();
    done.commit_seq = CommitSeq(3);
    let mut task = running_task();
    task.phase = TaskPhase::Done;
    task.result_ref = Some(reference.clone());
    task.finalization = Some(TaskFinalization {
        operation_id: RequestId("finalize-1".into()),
        record: record.clone(),
        terminal_event_id: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }),
        terminal_published: false,
    });
    task.publication = SnapshotProgress {
        published_through: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }),
        ..Default::default()
    };
    done.tasks = VecDeque::from([task]);
    done.runner_state = admitted_state.clone();
    done.last_event_id = Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN });
    let events = vec![terminal_event(3, reference.clone())];
    let snapshot = published_snapshot(3, reference.clone());
    done.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: CommitId("c3".into()),
        source_commit: CommitSeq(3),
        content_digest: Digest("unused".into()),
        effects: vec![
            Effect {
                id: EffectId { sid: sid(), commit: CommitSeq(3), index: NonZeroU32::MIN },
                content_digest: kind_digest(&EffectKind::AppendEvents { previous: None, events: events.clone() }),
                kind: EffectKind::AppendEvents { previous: None, events: events.clone() },
            },
            Effect {
                id: EffectId { sid: sid(), commit: CommitSeq(3), index: NonZeroU32::new(2).unwrap() },
                content_digest: kind_digest(&EffectKind::PublishSnapshot { snapshot: snapshot.clone() }),
                kind: EffectKind::PublishSnapshot { snapshot: snapshot.clone() },
            },
        ],
    });
    let effect_ids: Vec<EffectId> = done.outbox.as_ref().unwrap().effects.iter().map(|effect| effect.id.clone()).collect();
    let receipt3 = match joint.commit(&owner, done).await {
        CommitOutcome::Committed(receipt) => receipt,
        outcome => panic!("{outcome:?}"),
    };
    let sources: Vec<_> = effect_ids.iter().map(|effect_id| EffectSource {
        commit: receipt3.clone(),
        effect_id: effect_id.clone(),
        content_digest: match effect_id.index.get() {
            1 => kind_digest(&EffectKind::AppendEvents { previous: None, events: events.clone() }),
            _ => kind_digest(&EffectKind::PublishSnapshot { snapshot: snapshot.clone() }),
        },
    }).collect();

    // --- the real feed enforces append-before-publish across the permits ---
    assert!(matches!(
        joint.feed.publish(PublishCommittedSnapshot { source: sources[1].clone(), snapshot: snapshot.clone() }).await,
        Err(StoreError::NotApplied { code }) if code.contains("snapshot_append_incomplete")
    ));
    assert_eq!(
        joint.feed.append(AppendCommittedEvents { source: sources[0].clone(), previous: None, events: events.clone() }).await.unwrap(),
        AppendOutcome::Appended
    );
    // Lost-append replay applies once (idempotent completion evidence).
    assert_eq!(
        joint.feed.append(AppendCommittedEvents { source: sources[0].clone(), previous: None, events: events.clone() }).await.unwrap(),
        AppendOutcome::AlreadyApplied
    );
    assert_eq!(
        joint.feed.publish(PublishCommittedSnapshot { source: sources[1].clone(), snapshot: snapshot.clone() }).await.unwrap(),
        PublishOutcome::Published
    );
    assert_eq!(
        joint.feed.publish(PublishCommittedSnapshot { source: sources[1].clone(), snapshot: snapshot.clone() }).await.unwrap(),
        PublishOutcome::AlreadyApplied
    );

    // --- durable readback independent of the current checkpoint head ---
    assert_eq!(joint.archive.get_final(&scope(), &tid()).await.unwrap(), Some(record.clone()));
    let RestoredTask::Final(view) = joint.feed.restore(&scope(), &tid()).await.unwrap().unwrap()
    else { panic!("final") };
    assert_eq!(view.content.status, TaskStatus::Completed);
    assert_eq!(view.content.result_ref, Some(reference.clone()));
    let RestoredTask::Final(readback) = joint.feed.restore(&scope(), &tid()).await.unwrap().unwrap()
    else { panic!("final") };
    assert_eq!(readback, view);

    // --- next round on the same Sid admits immediately; no second unlock ---
    let mut next = empty_checkpoint();
    next.commit_seq = CommitSeq(4);
    let mut task = running_task();
    task.phase = TaskPhase::Done;
    task.result_ref = Some(reference.clone());
    task.finalization = Some(TaskFinalization {
        operation_id: RequestId("finalize-1".into()),
        record: record.clone(),
        terminal_event_id: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }),
        terminal_published: true,
    });
    task.publication = SnapshotProgress {
        published_through: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }),
        ..Default::default()
    };
    next.tasks = VecDeque::from([task]);
    next.runner_state = Some(TaskRunnerState {
        tid: tid(),
        state: RunnerState {
            format_version: 1,
            value: serde_json::json!({"round": 2, "generation": 2, "request": "joint-round-2"}),
        },
    });
    assert!(matches!(joint.commit(&owner, next).await, CommitOutcome::Committed(_)));

    // Sealed successor replays the already-applied permits via completion
    // evidence; owner equality is not the authorization rule.
    let successor = joint.takeover().await;
    assert_eq!(
        successor.feed.publish(PublishCommittedSnapshot { source: sources[1].clone(), snapshot: snapshot.clone() }).await.unwrap(),
        PublishOutcome::AlreadyApplied
    );
    assert_eq!(
        successor.archive.get_final(&scope(), &tid()).await.unwrap(),
        Some(record.clone())
    );
    let RestoredTask::Final(still) = successor.feed.restore(&scope(), &tid()).await.unwrap().unwrap()
    else { panic!("final after takeover") };
    assert_eq!(still.content.status, TaskStatus::Completed);

    // The successor seals the session; the old owner's late journal write is
    // then fenced. Sealing happens at acquire, not at lease retirement.
    let owner2 = successor.acquire().await;
    let mut late = empty_checkpoint();
    late.commit_seq = CommitSeq(5);
    late.runner_state = Some(TaskRunnerState {
        tid: tid(),
        state: RunnerState { format_version: 1, value: serde_json::json!({"round": 2, "generation": 2, "late": true}) },
    });
    assert_eq!(joint.commit(&owner, late).await, CommitOutcome::Fenced);
    let mut round2 = empty_checkpoint();
    round2.commit_seq = CommitSeq(5);
    round2.tasks = later_round_tasks(&record, &reference);
    round2.runner_state = Some(TaskRunnerState {
        tid: tid(),
        state: RunnerState { format_version: 1, value: serde_json::json!({"round": 2, "generation": 2}) },
    });
    assert!(matches!(successor.commit(&owner2, round2).await, CommitOutcome::Committed(_)));

    // --- documented gap (T7): a committed-but-never-applied feed permit is
    // head-scoped. After advancing, current-outbox verification rejects it; a
    // strict-group paired first-write permit must be added to task_feed by T7.
    let mut orphan = empty_checkpoint();
    orphan.commit_seq = CommitSeq(6);
    orphan.tasks = later_round_tasks(&record, &reference);
    orphan.runner_state = Some(TaskRunnerState {
        tid: tid(),
        state: RunnerState { format_version: 1, value: serde_json::json!({"round": 2, "generation": 2, "note": "orphan"}) },
    });
    let orphan_events = vec![OutputEvent {
        id: EventId { commit: CommitSeq(6), index: NonZeroU32::MIN },
        tid: tid(),
        body: OutputBody::Status { status: TaskStatus::Working, interactions: vec![] },
    }];
    orphan.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: CommitId("c6".into()),
        source_commit: CommitSeq(6),
        content_digest: Digest("unused".into()),
        effects: vec![Effect {
            id: EffectId { sid: sid(), commit: CommitSeq(6), index: NonZeroU32::MIN },
            content_digest: kind_digest(&EffectKind::AppendEvents { previous: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }), events: orphan_events.clone() }),
            kind: EffectKind::AppendEvents { previous: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }), events: orphan_events.clone() },
        }],
    });
    let receipt6 = match successor.commit(&owner2, orphan).await {
        CommitOutcome::Committed(receipt) => receipt,
        outcome => panic!("{outcome:?}"),
    };
    let orphan_source = EffectSource {
        commit: receipt6.clone(),
        effect_id: EffectId { sid: sid(), commit: CommitSeq(6), index: NonZeroU32::MIN },
        content_digest: kind_digest(&EffectKind::AppendEvents { previous: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }), events: orphan_events.clone() }),
    };
    let mut advanced = empty_checkpoint();
    advanced.commit_seq = CommitSeq(7);
    advanced.runner_state = Some(TaskRunnerState {
        tid: tid(),
        state: RunnerState { format_version: 1, value: serde_json::json!({"round": 2, "generation": 2, "advanced": true}) },
    });
    assert!(matches!(successor.commit(&owner2, advanced).await, CommitOutcome::Committed(_)));
    assert!(matches!(
        successor.feed.append(AppendCommittedEvents {
            source: orphan_source,
            previous: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }),
            events: orphan_events,
        }).await,
        Err(StoreError::Invalid { code }) if code.contains("unproven_effect")
    ));
    // Already-applied permits, in contrast, stay replayable after head advance
    // through their retained completion documents (T7 pairs repeat this for
    // never-applied strict-group sources instead).
    assert_eq!(
        successor.feed.append(AppendCommittedEvents {
            source: sources[0].clone(),
            previous: None,
            events: events,
        }).await.unwrap(),
        AppendOutcome::AlreadyApplied
    );
}

/// Later-round candidates must retain the earlier terminal round's frozen
/// result (preserve_frozen_results); the mapping keeps the Done task verbatim.
fn later_round_tasks(record: &FinalTaskRecord, reference: &BlobRef) -> VecDeque<TaskProgress> {
    let mut done = running_task();
    done.phase = TaskPhase::Done;
    done.result_ref = Some(reference.clone());
    done.finalization = Some(TaskFinalization {
        operation_id: RequestId("finalize-1".into()),
        record: record.clone(),
        terminal_event_id: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }),
        terminal_published: true,
    });
    done.publication = SnapshotProgress {
        published_through: Some(EventId { commit: CommitSeq(3), index: NonZeroU32::MIN }),
        ..Default::default()
    };
    VecDeque::from([done])
}

/// The merged Connector admission delivers a GetTask completion that has no
/// remote event id: identity is derived from (did, KeyEvent, state) by the
/// real Inbox, so nothing is fabricated. Re-reports land once; a different
/// terminal state under the same key is a conflict, never a second result.
#[tokio::test]
async fn gettask_final_without_remote_event_id_reaches_the_real_inbox_once() {
    let joint = Joint::new().await;
    joint.admit_session().await;
    let did = Did(format!("{TID}.dispatch"));
    let state = "completed";
    let input_id = connector::key_input_id(&did, KeyEvent::Final);
    let business_digest = connector::key_digest(&did, KeyEvent::Final, state);
    let admission = |payload: serde_json::Value, holder_epoch: u64| ConnectorAdmission {
        input: InputEnvelope {
            schema_version: 1,
            sid: sid(),
            tid: tid(),
            input_id: input_id.clone(),
            business_digest: business_digest.clone(),
            body: InputBody::Agent {
                event: AgentEvent {
                    did: did.clone(),
                    holder_epoch: HolderEpoch(holder_epoch),
                    seq: InputSeq(1),
                    kind: AgentEventKind::Final,
                    remote_event_id: None, // GetTask results carry no remote id.
                    payload: Payload::Inline(payload),
                },
            },
        },
        identity: ConnectorEventIdentity { round: None, state: state.to_owned() },
    };
    let first = admission(
        serde_json::json!({"state": "completed", "artifacts": [{"artifactId": "answer", "content": "final answer"}]}),
        1,
    );
    let AdmitOutcome::Accepted(receipt) = joint.inbox.admit_connector(first.clone()).await.unwrap()
    else { panic!("accepted") };
    assert_eq!(receipt.input_id, input_id);
    assert_eq!(receipt.business_digest, business_digest);

    // A takeover re-reports the same completion with different bytes and a new
    // holder epoch: one semantic key event, applied once.
    let second = admission(
        serde_json::json!({"state": "completed", "artifacts": [{"artifactId": "answer", "content": "final answer", "extra": true}]}),
        2,
    );
    let AdmitOutcome::Duplicate(replay) = joint.inbox.admit_connector(second).await.unwrap()
    else { panic!("duplicate") };
    assert_eq!(replay, receipt);

    // The same key with a different terminal state is a content conflict.
    let mut conflicting = admission(
        serde_json::json!({"state": "failed"}),
        3,
    );
    conflicting.identity.state = "failed".into();
    conflicting.input.business_digest = connector::key_digest(&did, KeyEvent::Final, "failed");
    assert_eq!(
        joint.inbox.admit_connector(conflicting).await.unwrap(),
        AdmitOutcome::ContentConflict
    );

    // The runner-visible inbox stream carries exactly one entry for this key.
    let mut feed = joint.inbox.open(ShardId(42)).await.unwrap();
    feed.watch(sid(), None).await.unwrap();
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
        .unwrap();
    let key_entries: Vec<_> = batch
        .sessions
        .iter()
        .flat_map(|session| &session.entries)
        .filter(|entry| entry.input_id == input_id)
        .collect();
    assert_eq!(key_entries.len(), 1, "duplicate physical reports apply once");
}
