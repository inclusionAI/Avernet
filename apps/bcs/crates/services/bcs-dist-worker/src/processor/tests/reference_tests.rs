#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "real archive processor tests"
)]
use super::{
    references::References,
    tests::{spec, task},
};
use bcs_dist_model::*;
use bcs_dist_store::{archive::DbArchiveStore, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{common::*, result::*, session::*};
use std::{
    collections::{BTreeMap, VecDeque},
    num::NonZeroU32,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

struct FaultBlob {
    inner: DbArchiveStore,
    lose: AtomicBool,
    fail_read: AtomicBool,
}
#[async_trait::async_trait]
impl BlobStorePort for FaultBlob {
    async fn put_immutable(
        &self,
        identity: BlobRef,
        bytes: Vec<u8>,
    ) -> Result<IdempotentWrite, StoreError> {
        let outcome = self.inner.put_immutable(identity.clone(), bytes).await?;
        if self.lose.swap(false, Ordering::SeqCst) {
            return Err(StoreError::OutcomeUnknown {
                operation_id: identity.key,
            });
        }
        Ok(outcome)
    }
    async fn get(&self, reference: &BlobRef) -> Result<Option<Vec<u8>>, StoreError> {
        if self.fail_read.swap(false, Ordering::SeqCst) {
            return Err(StoreError::NotApplied {
                code: "injected".into(),
            });
        }
        self.inner.get(reference).await
    }
}
async fn references(lose: bool, fail_read: bool) -> References {
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let docs = Arc::new(bcs_test_support::store_model::Model::default());
    References {
        timeout: Duration::from_secs(1),
        envelope_reserve: 64 * 1024,
        blobs: Arc::new(FaultBlob {
            inner: DbArchiveStore::new(db, docs, KeyLayout::new("worker", "references").unwrap())
                .unwrap(),
            lose: AtomicBool::new(lose),
            fail_read: AtomicBool::new(fail_read),
        }),
    }
}
pub(super) fn candidate() -> Checkpoint {
    candidate_with_text(1_048_574)
}
fn candidate_with_text(length: usize) -> Checkpoint {
    let display = DisplaySnapshot {
        format_version: 1,
        units: vec![DisplayUnit {
            node: NodeId("node".into()),
            artifact_id: ArtifactId("answer".into()),
            class: OutputClass::Output,
            content: serde_json::json!("x".repeat(length)),
        }],
    };
    let content = TaskContent {
        status: TaskStatus::Working,
        interactions: vec![],
        snapshot: Payload::Inline(serde_json::to_value(&display).unwrap()),
        result_ref: None,
    };
    let snapshot = TaskSnapshot {
        schema_version: 1,
        scope: spec().scope,
        sid: spec().sid,
        tid: task().tid,
        content: content.clone(),
        output_limits: task().output_limits,
        output_usage: OutputUsage {
            cumulative_bytes: 1_048_576,
        },
        snapshot_event_id: Some(EventId {
            commit: CommitSeq(1),
            index: NonZeroU32::MIN,
        }),
        source_commit: CommitSeq(1),
        created_at: TimestampMillis(0),
        updated_at: TimestampMillis(1),
    };
    let kinds = vec![
        EffectKind::AppendEvents {
            previous: None,
            events: vec![OutputEvent {
                id: snapshot.snapshot_event_id.unwrap(),
                tid: task().tid,
                body: OutputBody::Artifact {
                    node: display.units[0].node.clone(),
                    artifact_id: display.units[0].artifact_id.clone(),
                    class: OutputClass::Output,
                    update: ArtifactUpdate::Upsert {
                        content: Payload::Inline(display.units[0].content.clone()),
                    },
                },
            }],
        },
        EffectKind::PublishSnapshot { snapshot },
    ];
    Checkpoint {
        format_version: 1,
        commit_seq: CommitSeq(1),
        consumed: None,
        tasks: VecDeque::from([task()]),
        runner_state: None,
        dispatch_seq: BTreeMap::new(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: BTreeMap::from([(task().tid, content)]),
        last_event_id: None,
        feed_publications: Vec::new(),
        outbox: Some(CommittedBatch {
            created_at: None,
            commit_id: CommitId("candidate".into()),
            source_commit: CommitSeq(1),
            content_digest: Digest("pending".into()),
            effects: kinds
                .into_iter()
                .enumerate()
                .map(|(i, kind)| Effect {
                    id: EffectId {
                        sid: spec().sid,
                        commit: CommitSeq(1),
                        index: NonZeroU32::new(i as u32 + 1).unwrap(),
                    },
                    content_digest: Digest("pending".into()),
                    kind,
                })
                .collect(),
        }),
    }
}

#[tokio::test]
async fn large_event_and_snapshot_are_durable_refs_before_checkpoint_submission() {
    let refs = references(true, false).await;
    let mut candidate = candidate_with_text(9 * 1024 * 1024);
    complete_externalization(&refs, &mut candidate).await;
    let snapshot = &candidate.view_drafts[&task().tid].snapshot;
    assert!(matches!(snapshot, Payload::Ref(_)));
    let batch = candidate.outbox.as_ref().unwrap();
    let EffectKind::PublishSnapshot {
        snapshot: published,
    } = &batch.effects[1].kind
    else {
        panic!()
    };
    assert_eq!(published.content.snapshot, *snapshot);
    let EffectKind::AppendEvents { events, .. } = &batch.effects[0].kind else {
        panic!()
    };
    let OutputBody::Artifact {
        update: ArtifactUpdate::Upsert { content },
        ..
    } = &events[0].body
    else {
        panic!()
    };
    assert!(matches!(content, Payload::Ref(_)));
    let resolved = refs.resolve_output(content).await.unwrap();
    assert_eq!(resolved.as_str().unwrap().len(), 9 * 1024 * 1024);
    assert!(refs.resolve(content, PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES).await.is_err());
    assert!(serde_json::to_vec(batch).unwrap().len() < 196_608);
}

#[tokio::test]
async fn unknown_blob_without_confirmation_does_not_install_a_reference() {
    let refs = references(true, true).await;
    let mut candidate = candidate();
    assert!(matches!(
        refs.externalize_candidate(&spec().sid, &mut candidate, false)
            .await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    assert!(matches!(
        candidate.view_drafts[&task().tid].snapshot,
        Payload::Inline(_)
    ));
    complete_externalization(&refs, &mut candidate).await;
    assert!(matches!(
        candidate.view_drafts[&task().tid].snapshot,
        Payload::Ref(_)
    ));
}

#[tokio::test]
async fn second_externalization_pass_reserves_the_actual_provider_envelope() {
    use super::commit::seal_candidate;
    use bcs_service_api::port::repo::distributed::common::SessionOwnership;
    let references = references(false, false).await;
    let mut candidate = candidate();
    candidate.outbox.as_mut().unwrap().effects.clear();
    candidate.view_drafts.clear();
    candidate.tasks.clear();
    for i in 0..18 {
        candidate.tasks.push_back(TaskProgress {
            tid: Tid(format!("{}.queued-{i}", spec().sid.0)),
            phase: TaskPhase::Queued,
            input: Payload::Inline(serde_json::json!("x".repeat(51_200))),
            ..task()
        });
    }
    let before = serde_json::to_vec(&candidate).unwrap().len();
    assert!(before > 896 * 1024 && before < 960 * 1024, "{before}");
    complete_externalization(&references, &mut candidate).await;
    assert!(
        candidate
            .tasks
            .iter()
            .any(|t| matches!(t.input, Payload::Ref(_)))
    );
    let owner = SessionOwnership {
        sid: spec().sid,
        shard: ShardId(1),
        owner: ShardOwner {
            instance: Instance {
                node: "w".into(),
                incarnation: "one".into(),
            },
            epoch: WorkerEpoch(1),
        },
    };
    seal_candidate(
        owner,
        CommitSeq(0),
        CommitId("candidate".into()),
        candidate,
        false,
        64 * 1024,
    )
    .unwrap();
}

#[tokio::test]
async fn externalization_includes_the_ids_and_digests_added_when_sealing() {
    use super::commit::seal_candidate;
    use bcs_service_api::port::repo::distributed::common::SessionOwnership;
    let references = references(false, false).await;
    let mut candidate = candidate();
    candidate.outbox.as_mut().unwrap().effects.clear();
    candidate.view_drafts.clear();
    candidate.tasks.clear();
    for i in 0..18 {
        candidate.tasks.push_back(TaskProgress {
            tid: Tid(format!("{}.q-{i}", spec().sid.0)),
            phase: TaskPhase::Queued,
            input: Payload::Inline(serde_json::json!("x".repeat(51200))),
            ..task()
        });
    }
    let before = serde_json::to_vec(&candidate).unwrap().len();
    let budget = 896 * 1024;
    let shrink = before - (budget - 32);
    candidate.tasks[0].input = Payload::Inline(serde_json::json!("x".repeat(51200 - shrink)));
    assert_eq!(serde_json::to_vec(&candidate).unwrap().len(), budget - 32);
    complete_externalization(&references, &mut candidate).await;
    let owner = SessionOwnership {
        sid: spec().sid,
        shard: ShardId(1),
        owner: ShardOwner {
            instance: Instance {
                node: "w".into(),
                incarnation: "one".into(),
            },
            epoch: WorkerEpoch(1),
        },
    };
    seal_candidate(
        owner,
        CommitSeq(0),
        CommitId("a-valid-commit-identity".into()),
        candidate,
        false,
        64 * 1024,
    )
    .unwrap();
}

async fn complete_externalization(refs: &References, candidate: &mut Checkpoint) {
    for _ in 0..256 {
        if refs
            .externalize_candidate(&spec().sid, candidate, false)
            .await
            .unwrap()
        {
            return;
        }
    }
    panic!("bounded externalization did not complete");
}
