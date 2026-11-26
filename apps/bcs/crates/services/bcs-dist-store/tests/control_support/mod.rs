#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "control provider fixtures"
)]
use crate::common::*;
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::{
    archive::DbArchiveStore, coordination::CoordinationStore, identity::digest_json, inbox::*,
    layout::KeyLayout, task_feed::*,
};
use bcs_service_api::port::repo::distributed::session::Effect;
use bcs_service_api::port::repo::distributed::{
    admission::*, common::*, control::*, inbox::*, result::*, session::*, task_feed::*,
};
use bcs_store_api::document::DocumentStorePlugin;
use std::{num::NonZeroU32, sync::Arc};
pub struct Fixture {
    pub storage: Arc<Recorder>,
    pub layout: KeyLayout,
    pub store: CoordinationStore,
    pub inbox: StoreInboxProducer,
    pub control: StoreAdmissionControl,
    pub archive: Arc<DbArchiveStore>,
    pub feed: Arc<StoreTaskFeed>,
    pub owner: SessionOwnership,
}
impl Fixture {
    pub async fn new() -> Self {
        let storage = Arc::new(Recorder::default());
        let layout = KeyLayout::new("closure", "test").unwrap();
        let store = CoordinationStore::new(
            storage.clone(),
            Arc::new(bcs_test_support::store_model::Model::default()),
            storage.clone(),
            layout.clone(),
        )
        .unwrap();
        let inbox = StoreInboxProducer::new(
            Arc::new(
                bcs_dist_store::budget::SharedScanBudget::new(
                    bcs_dist_store::budget::ScanBudgetConfig::default(),
                )
                .unwrap(),
            ),
            Arc::new(OutputProjectionService),
            storage.clone(),
            storage.clone(),
            storage.clone(),
            storage.clone(),
            layout.clone(),
            Arc::new(FixedClock(100)),
            InboxProducerConfig::new(BackendId("model".into())),
        )
        .unwrap();
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        let archive = Arc::new(DbArchiveStore::new(db, storage.clone(), layout.clone()).unwrap());
        let feed = Arc::new(
            StoreTaskFeed::new(
                TaskFeedStores {
                    documents: storage.clone(),
                    streams: storage.clone(),
                    index: storage.clone(),
                    blobs: archive.clone(),
                    results: archive.clone(),
                },
                Arc::new(OutputProjectionService),
                layout.clone(),
                TaskFeedConfig::default(),
            )
            .unwrap(),
        );
        let control = StoreAdmissionControl::new(inbox.clone(), feed.clone());
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
        storage
            .create_document(
                &layout.ckpt(&owner.sid).unwrap(),
                &serde_json::to_value(CheckpointEnvelope {
                    owner: owner.owner.clone(),
                    checkpoint,
                    recovery: RecoveryMetadata {
                        takeover_streak: 0,
                        last_attempt: None,
                        quarantined: false,
                        last_active_at: TimestampMillis(0),
                    },
                })
                .unwrap(),
            )
            .await
            .unwrap();
        inbox.observe(&owner).await.unwrap();
        Self {
            storage,
            layout,
            store,
            inbox,
            control,
            archive,
            feed,
            owner,
        }
    }
    pub async fn admit(&self, id: &str) -> AdmitOutcome {
        self.inbox.admit(request(id)).await.unwrap()
    }
    pub async fn guard(&self) -> ControlGuard {
        let snapshot = self
            .store
            .load_control(&self.owner.sid)
            .await
            .unwrap()
            .unwrap();
        ControlGuard {
            owner: snapshot.owner,
            expected_head: snapshot.head,
            expected_revision: snapshot.control.revision,
        }
    }
    pub async fn isolate(&self, id: &str) -> AdmissionClosure {
        let guard = self.guard().await;
        let attempt = AttemptIdentity {
            id: RequestId(format!("attempt-{id}")),
            head: guard.expected_head,
            state_digest: Digest("state".into()),
            input_digest: Digest("input".into()),
            context_digest: Digest("context".into()),
            frozen_now: TimestampMillis(100),
        };
        self.store
            .begin_attempt(guard, attempt.clone())
            .await
            .unwrap();
        self.store
            .finish_attempt(
                self.guard().await,
                attempt.id,
                AttemptOutcome::Deterministic {
                    code: "InvalidDecision".into(),
                    isolation_id: RequestId(id.into()),
                    safe_to_finalize: true,
                },
            )
            .await
            .unwrap();
        let generation = self
            .control
            .gate_state(&self.owner)
            .await
            .unwrap()
            .generation;
        let GateCloseOutcome::Closed(closure) = self
            .control
            .close_gate(self.guard().await, generation, RequestId(id.into()))
            .await
            .unwrap()
        else {
            panic!()
        };
        self.store
            .record_closure(self.guard().await, closure.clone())
            .await
            .unwrap();
        closure
    }
    pub async fn terminal(&self, tid: &Tid, operation: &str) {
        let metadata = self
            .feed
            .load_snapshot(&scope(), tid)
            .await
            .unwrap()
            .unwrap();
        let mut checkpoint = self
            .store
            .load(&self.owner.sid)
            .await
            .unwrap()
            .unwrap()
            .checkpoint;
        let sequence = CommitSeq(checkpoint.commit_seq.0 + 1);
        let record = FinalTaskRecord {
            schema_version: 1,
            scope: scope(),
            sid: self.owner.sid.clone(),
            tid: tid.clone(),
            status: TerminalStatus::Rejected,
            snapshot: metadata.content.snapshot.clone(),
            result: Payload::Inline(serde_json::json!({"code":"Isolated"})),
            source_commit: sequence,
            finalized_at: TimestampMillis(100),
        };
        checkpoint.tasks.push_back(TaskProgress {
            steps: 0,
            tid: tid.clone(),
            phase: TaskPhase::AwaitingResult {
                status: record.status,
            },
            input: Payload::Inline(serde_json::Value::Null),
            result_ref: None,
            output_limits: metadata.output_limits,
            output_usage: metadata.output_usage,
            publication: SnapshotProgress::default(),
            finalization: Some(TaskFinalization {
                operation_id: RequestId(format!("finalize-{}", tid.0)),
                record: record.clone(),
                terminal_event_id: None,
                terminal_published: false,
            }),
        });
        let first = self
            .commit(
                checkpoint,
                vec![EffectKind::SaveResult {
                    record: record.clone(),
                }],
                operation,
            )
            .await;
        let source = self.source(&first, 0).await;
        let reference = match self.archive.save_final(source, record).await.unwrap() {
            SavedResult::Saved(r) | SavedResult::AlreadySaved(r) => r,
            _ => panic!(),
        };
        let mut checkpoint = self
            .store
            .load(&self.owner.sid)
            .await
            .unwrap()
            .unwrap()
            .checkpoint;
        let next = CommitSeq(checkpoint.commit_seq.0 + 1);
        let event_id = EventId {
            commit: next,
            index: NonZeroU32::MIN,
        };
        let progress = checkpoint.tasks.iter_mut().find(|t| t.tid == *tid).unwrap();
        progress.phase = TaskPhase::Done;
        progress.result_ref = Some(reference.clone());
        progress.finalization.as_mut().unwrap().terminal_event_id = Some(event_id);
        let event = OutputEvent {
            id: event_id,
            tid: tid.clone(),
            body: OutputBody::Terminal {
                status: TerminalStatus::Rejected,
                result_ref: reference.clone(),
            },
        };
        let content = TaskContent {
            status: TaskStatus::Rejected,
            interactions: vec![],
            snapshot: metadata.content.snapshot.clone(),
            result_ref: Some(reference),
        };
        let snapshot = TaskSnapshot {
            content,
            source_commit: next,
            snapshot_event_id: Some(event_id),
            updated_at: TimestampMillis(101),
            ..metadata
        };
        let previous = checkpoint.last_event_id;
        checkpoint.last_event_id = Some(event_id);
        checkpoint
            .view_drafts
            .insert(tid.clone(), snapshot.content.clone());
        let second = self
            .commit(
                checkpoint,
                vec![
                    EffectKind::AppendEvents {
                        previous,
                        events: vec![event.clone()],
                    },
                    EffectKind::PublishSnapshot {
                        snapshot: snapshot.clone(),
                    },
                ],
                operation,
            )
            .await;
        self.feed
            .append(AppendCommittedEvents {
                source: self.source(&second, 0).await,
                previous,
                events: vec![event],
            })
            .await
            .unwrap();
        self.feed
            .publish(PublishCommittedSnapshot {
                source: self.source(&second, 1).await,
                snapshot,
            })
            .await
            .unwrap();
    }
    async fn commit(
        &self,
        mut checkpoint: Checkpoint,
        kinds: Vec<EffectKind>,
        operation: &str,
    ) -> CommitReceipt {
        let previous = checkpoint.commit_seq;
        checkpoint.commit_seq = CommitSeq(previous.0 + 1);
        let id = CommitId(format!("commit-{}", checkpoint.commit_seq.0));
        let digest = Digest(format!("digest-{}", checkpoint.commit_seq.0));
        checkpoint.outbox = Some(CommittedBatch {
            created_at: None,
            commit_id: id.clone(),
            source_commit: checkpoint.commit_seq,
            content_digest: digest.clone(),
            effects: kinds
                .into_iter()
                .enumerate()
                .map(|(i, kind)| Effect {
                    id: EffectId {
                        sid: self.owner.sid.clone(),
                        commit: checkpoint.commit_seq,
                        index: NonZeroU32::new(i as u32 + 1).unwrap(),
                    },
                    content_digest: digest_json(&serde_json::to_value(&kind).unwrap()),
                    kind,
                })
                .collect(),
        });
        match self
            .store
            .commit(CommitRequest {
                purpose: CommitPurpose::Isolation {
                    operation_id: RequestId(operation.into()),
                },
                owner: self.owner.clone(),
                expected_commit_seq: previous,
                commit_id: id,
                content_digest: digest,
                candidate: checkpoint,
            })
            .await
            .unwrap()
        {
            CommitOutcome::Committed(r) | CommitOutcome::AlreadyCommitted(r) => r,
            other => panic!("{other:?}"),
        }
    }
    async fn source(&self, receipt: &CommitReceipt, index: usize) -> EffectSource {
        let checkpoint = self
            .store
            .load(&self.owner.sid)
            .await
            .unwrap()
            .unwrap()
            .checkpoint;
        let effect = &checkpoint.outbox.unwrap().effects[index];
        EffectSource {
            commit: receipt.clone(),
            effect_id: effect.id.clone(),
            content_digest: effect.content_digest.clone(),
        }
    }
}
pub fn request(id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(id, "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    }
}

pub async fn complete(f: &Fixture, closure: &AdmissionClosure) -> ClosureCertificate {
    for _ in 0..64 {
        match f.control.advance_closure(&f.owner, closure).await {
            Ok(work) => {
                if let Some(certificate) = work.certificate {
                    return certificate;
                }
            }
            Err(StoreError::OutcomeUnknown { .. }) => {}
            other => panic!("unexpected closure response: {other:?}"),
        }
    }
    panic!("closure did not complete")
}
pub async fn resume(f: &Fixture, closure: &AdmissionClosure, id: &str) -> GateRecoveryReceipt {
    let certificate = complete(f, closure).await;
    f.store
        .record_closure_complete(f.guard().await, certificate.clone())
        .await
        .unwrap();
    f.store
        .begin_recovery(RecoveryAuthorization {
            guard: f.guard().await,
            isolation_id: closure.operation_id.clone(),
            completion: certificate,
            request_id: RequestId(id.into()),
        })
        .await
        .unwrap();
    let guard = f.guard().await;
    let GateReopenOutcome::Opened(receipt) = f
        .control
        .reopen_gate(guard.clone(), RequestId(id.into()))
        .await
        .unwrap()
    else {
        panic!()
    };
    f.store
        .confirm_recovery(guard, receipt.clone())
        .await
        .unwrap();
    receipt
}
