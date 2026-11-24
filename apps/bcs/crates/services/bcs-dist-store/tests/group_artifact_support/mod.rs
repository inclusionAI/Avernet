#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "contract test support shared by several test targets"
)]
//! Shared fixture for the per-task archive contract vectors (plan T4).
//!
//! Reuses the archive support harness (same DbPlugin, same committed-effect
//! evidence, same layout namespace) and adds the GroupArtifactStore under
//! test with an explicit small injected budget. Tests always pass explicit
//! SMALL budgets; D0 frozen values are never used as test configuration.
#[path = "../archive_support/mod.rs"]
mod base;

use bcs_db_local::LocalSqliteDbPlugin;
use bcs_dist_model::*;
use bcs_dist_store::archive::result_blob_reference;
use bcs_dist_store::group_artifact::{GroupArtifactStore, JsonProjectionValidator};
use bcs_service_api::port::repo::distributed::common::*;
use bcs_service_api::port::repo::distributed::group_round::RoundBudgetProfile;
use bcs_service_api::port::repo::distributed::result::*;
use std::num::{NonZeroU32, NonZeroU64};
use std::sync::Arc;



/// Explicitly small injected budget: test-only, never a default. The version
/// is deliberately not 1 so saved projection metadata proves the injected
/// budget was recorded, not the D0 frozen profile.
pub fn small_budget(frame_bytes: u64) -> ArchiveBudget {
    let mut profile = RoundBudgetProfile::v1();
    profile.public_frame_bytes = frame_bytes as usize;
    ArchiveBudget {
        version: 901,
        profile,
    }
}

pub fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("tenant".into()),
        principal: PrincipalId("principal".into()),
    }
}

pub fn group() -> GroupId {
    GroupId("g-coord".into())
}

pub fn grants(read: bool) -> ArchiveGrants {
    ArchiveGrants { read }
}

pub struct GroupFixture {
    pub inner: base::Fixture,
    pub group: GroupArtifactStore,
    pub budget: ArchiveBudget,
}

impl GroupFixture {
    pub async fn new(budget: ArchiveBudget) -> Self {
        let inner = base::Fixture::new().await;
        let group = GroupArtifactStore::new(
            inner.db.clone(),
            inner.documents.clone(),
            base::layout("test"),
            Arc::new(JsonProjectionValidator),
            budget,
        )
        .unwrap();
        Self {
            inner,
            group,
            budget,
        }
    }
    /// Simulate T7's separately committed terminal authority, not archive IO.
    pub async fn commit_terminal(&self, archive: &TaskFinalArchive, proof: &TerminalProof) {
        use bcs_store_api::document::DocumentStorePlugin;
        use bcs_service_api::port::repo::distributed::session::*;
        let key = base::layout("test").ckpt(&archive.round.sid).unwrap();
        let stored = self.inner.documents.read_document(&key).await.unwrap().unwrap();
        let mut envelope: CheckpointEnvelope = serde_json::from_value(stored.value).unwrap();
        envelope.checkpoint.commit_seq = proof.commit_seq;
        envelope.checkpoint.outbox = None;
        envelope.checkpoint.runner_state = Some(TaskRunnerState {
            tid: archive.round.tid.clone(),
            state: RunnerState { format_version: 1,
                value: serde_json::json!({"group_terminal":proof}) },
        });
        envelope.checkpoint.tasks.push_back(TaskProgress {
            steps: 0, tid: archive.round.tid.clone(), phase: TaskPhase::Done,
            input: Payload::Inline(serde_json::Value::Null), result_ref: Some(proof.result_ref.clone()),
            output_limits: PHASE_ONE_OUTPUT_LIMITS, output_usage: OutputUsage::default(),
            publication: Default::default(), finalization: None,
        });
        self.inner.documents.create_document(&proof.closure_ref.key,
            &serde_json::json!({"key":archive.round,"stopped":true})).await.unwrap();
        self.inner.documents.replace_document(&key, stored.version,
            &serde_json::to_value(envelope).unwrap()).await.unwrap();
    }

    pub fn reopen(&self) -> GroupArtifactStore {
        GroupArtifactStore::new(
            self.inner.db.clone(),
            self.inner.documents.clone(),
            base::layout("test"),
            Arc::new(JsonProjectionValidator),
            self.budget,
        )
        .unwrap()
    }
    pub fn reopen_database(&self) -> GroupArtifactStore {
        let db = LocalSqliteDbPlugin::new_file(self.inner.database_path()).unwrap();
        GroupArtifactStore::new(
            Arc::new(db),
            self.inner.documents.clone(),
            base::layout("test"),
            Arc::new(JsonProjectionValidator),
            self.budget,
        )
        .unwrap()
    }
    /// Persist committed SaveResult evidence for one archive request and
    /// return the matching EffectSource.
    pub async fn commit(&self, archive: &TaskFinalArchive) -> EffectSource {
        let commit = CommitReceipt {
            sid: archive.record.sid.clone(),
            owner: ShardOwner {
                instance: Instance {
                    node: "worker".into(),
                    incarnation: "one".into(),
                },
                epoch: WorkerEpoch(1),
            },
            commit_id: CommitId(format!("commit-{}", archive.record.tid.0)),
            content_digest: Digest("checkpoint-digest".into()),
            sequence: CommitSeq(1),
            consumed: None,
            proof: "not-authority".into(),
        };
        let source = base::source_for(&commit, &archive.record);
        self.inner.persist(&source, &archive.record).await;
        source
    }
}

/// One fully consistent archive request bound to a unique session shard
/// (`s<n>.session`) and task Tid. `result_digest` mirrors the stable digest
/// of the exact stored result bytes.
pub fn task_archive(
    n: u32,
    generation: NonZeroU64,
    artifacts: Vec<ArtifactPart>,
) -> TaskFinalArchive {
    let record = final_record(n);
    let round = RoundKey {
        scope: scope(),
        group: group(),
        sid: record.sid.clone(),
        tid: record.tid.clone(),
        generation,
    };
    let result_digest = result_blob_reference(&record).unwrap().digest;
    TaskFinalArchive {
        schema_version: 2,
        round,
        record,
        public_history: Vec::new(),
        result_digest,
        artifacts,
    }
}

/// Archive request with one padded text artifact used as the wire-size knob.
pub fn padded_archive(n: u32, generation: NonZeroU64, pad: usize) -> TaskFinalArchive {
    task_archive(
        n,
        generation,
        vec![text_part("a1", 1, &"x".repeat(pad))],
    )
}

pub fn final_record(n: u32) -> FinalTaskRecord {
    let sid = Sid(format!("s{n:04}.session"));
    let tid = Tid(format!("{}.task", sid.0));
    FinalTaskRecord {
        schema_version: 1,
        scope: scope(),
        sid: sid.clone(),
        tid,
        status: TerminalStatus::Completed,
        snapshot: Payload::Inline(serde_json::json!({"format_version":1,"units":[]})),
        result: Payload::Inline(serde_json::json!({"done":true})),
        source_commit: CommitSeq(1),
        finalized_at: TimestampMillis(42),
    }
}

pub fn text_part(artifact_id: &str, order: u32, text: &str) -> ArtifactPart {
    ArtifactPart {
        artifact_id: ArtifactId(artifact_id.into()),
        order: NonZeroU32::new(order).unwrap(),
        part: RoundPart::Text {
            text: text.to_owned(),
        },
    }
}

pub fn data_part(artifact_id: &str, order: u32, data: serde_json::Value) -> ArtifactPart {
    ArtifactPart {
        artifact_id: ArtifactId(artifact_id.into()),
        order: NonZeroU32::new(order).unwrap(),
        part: RoundPart::Data { data },
    }
}

pub fn proof_for(archive: &TaskFinalArchive, source: &EffectSource) -> TerminalProof {
    TerminalProof {
        schema_version: RoundSchemaVersion::V1,
        key: archive.round.clone(),
        commit_id: CommitId(format!("terminal-{}", source.commit.commit_id.0)),
        commit_seq: CommitSeq(source.commit.sequence.0 + 1),
        status: archive.record.status,
        result_ref: result_blob_reference(&archive.record).unwrap(),
        closure_ref: BlobRef {
            key: format!("closure:{}", archive.round.tid.0),
            digest: bcs_dist_store::identity::digest_json(&serde_json::json!({
                "key": archive.round, "stopped": true,
            })),
        },
    }
}
