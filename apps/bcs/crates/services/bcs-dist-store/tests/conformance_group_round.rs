//! Conformance: the T1 prototype harness plus the T2 production strict-group
//! admission vectors over the merged #4245 joints (real store instance:
//! CoordinationStore checkpoint CAS, StoreInboxProducer, SQLite-backed
//! DbArchiveStore/StoreTaskFeed), with crash/response-loss stops injected
//! through the recording store model.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "contract assertion")]
#[path = "group_round_support/mod.rs"]
mod support;
mod common;
#[path = "group_admission_cases/regressions.rs"]
mod regressions;
#[path = "group_admission_cases/public_tasks.rs"]
mod public_tasks;
#[path = "group_admission_cases/inbox_fences.rs"]
mod inbox_fences;
#[path = "group_admission_cases/cancel.rs"]
mod cancel;
#[path = "group_admission_cases/cold_index.rs"]
mod cold_index;

use bcs_test_support::contract::distributed::group_round::group_round_repo_contract_tests;

use bcs_db_local::LocalSqliteDbPlugin;
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::{
    archive::DbArchiveStore,
    budget::{ScanBudgetConfig, SharedScanBudget},
    coordination::CoordinationStore,
    group_admission::{
        GroupAdmissionConfig, StoreGroupRoundAdmission, StrictOutcome,
    },
    inbox::{InboxProducerConfig, StoreInboxProducer},
    layout::KeyLayout,
    task_feed::{StoreTaskFeed, TaskFeedConfig, TaskFeedStores},
};
use bcs_service_api::port::repo::distributed::{
    common::*,
    group_round::*,
    inbox::*,
    ownership::*,
    result::*,
    session::*,
    task_feed::*,
};
use bcs_store_api::{document::DocumentStorePlugin, zset::*};
use common::*;
use std::{
    num::NonZeroU32,
    sync::Arc,
    time::Duration,
};

const NOW: i64 = 1_794_371_200_123;
const GROUP: &str = "grp-round";
const WRITE_PREFIXES: [&str; 9] = [
    "document.create",
    "document.replace",
    "zset.add",
    "zset.remove",
    "zset.update",
    "stream.append",
    "stream.trim",
    "kv.set",
    "kv.put_if_absent",
];

fn physical_writes(store: &Recorder) -> Vec<String> {
    store
        .calls()
        .into_iter()
        .filter(|call| WRITE_PREFIXES.iter().any(|prefix| call.starts_with(prefix)))
        .collect()
}

fn round(rid: &str, generation: u64) -> GroupStartRequest {
    let spec = spec(GROUP);
    RoundRoundMake {
        rid,
        generation,
        spec,
    }
    .make()
}

struct RoundRoundMake<'a> {
    rid: &'a str,
    generation: u64,
    spec: SessionSpec,
}
impl RoundRoundMake<'_> {
    fn make(&self) -> AcceptedRound {
        let sid = Sid(SID.into());
        let tid = Tid(format!("{SID}.{}.g{}", self.rid, self.generation));
        AcceptedRound {
            input: RoundInput {
                schema_version: RoundSchemaVersion::V1,
                source: GroupRoundSource::GroupA2a,
                key: RoundKey {
                    scope: scope(),
                    group: GroupId(GROUP.into()),
                    sid: sid.clone(),
                    tid: tid.clone(),
                    generation: self.generation.try_into().unwrap(),
                },
                ordered_parts: vec![RoundPart::Text {
                    text: self.rid.into(),
                }],
                workflow_ref: self.spec.workflow.clone(),
                history_cut: support::history_blobref(),
                execution_deadline: TimestampMillis(100_000),
            },
            request_id: RequestId(self.rid.into()),
            request_digest: Digest(format!("digest-{}", self.rid)),
            normalization_ref: BlobRef {
                key: "norm-v1".into(),
                digest: Digest("norm-digest".into()),
            },
            return_configuration: serde_json::json!({ "mode": "text" }),
        }
    }
}

/// One Gateway connection: its own coordination cache, producer and group
/// repo over the shared durable store.
struct Gateway {
    rounds: StoreGroupRoundAdmission,
    producer: StoreInboxProducer,
    #[allow(dead_code)]
    coordination: Arc<CoordinationStore>,
}

struct Rig {
    store: Arc<Recorder>,
    coordination: Arc<CoordinationStore>,
    claim: ShardClaim,
    #[allow(dead_code)]
    lease: MembershipLease,
    layout: KeyLayout,
    feed: StoreTaskFeed,
}

impl Rig {
    async fn new() -> Rig {
        let store = Arc::new(Recorder::default());
        let layout = KeyLayout::new("c01", "main").unwrap();
        let coordination = Arc::new(
            CoordinationStore::new(
                store.clone(),
                store.clone(),
                store.clone(),
                layout.clone(),
            )
            .unwrap(),
        );
        let me = Instance {
            node: "worker".into(),
            incarnation: "1".into(),
        };
        let lease = coordination
            .register(
                me.clone(),
                RuntimeRole::Worker,
                "http://worker.example.test".into(),
                Duration::from_secs(600),
            )
            .await
            .unwrap();
        let claim = match coordination
            .claim(ClaimShard {
                shard: ShardId(42),
                me,
                expected_epoch: None,
                request_id: RequestId("claim-1".into()),
            })
            .await
            .unwrap()
        {
            LeaseClaim::Claimed(c) | LeaseClaim::AlreadyClaimed(c) => c,
            other => panic!("{other:?}"),
        };
        let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
        let archive = Arc::new(DbArchiveStore::new(db, store.clone(), layout.clone()).unwrap());
        let feed = StoreTaskFeed::new(
            TaskFeedStores {
                documents: store.clone(),
                streams: store.clone(),
                index: store.clone(),
                blobs: archive.clone(),
                results: archive,
            },
            Arc::new(OutputProjectionService),
            layout.clone(),
            TaskFeedConfig::default(),
        )
        .unwrap();
        let spec = spec(GROUP);
        let outcome = store
            .create_document(
                &layout.spec(&spec.sid).unwrap(),
                &serde_json::to_value(&spec).unwrap(),
            )
            .await
            .unwrap();
        assert!(matches!(outcome, bcs_store_api::WriteOutcome::Applied { .. }));
        Rig {
            store,
            coordination,
            claim,
            lease,
            layout,
            feed,
        }
    }

    /// A fresh independent connection over the same durable store + claim:
    /// independent caches, same single durable authority.
    fn gateway(&self) -> Gateway {
        self.gateway_with_archives(None)
    }

    /// Same connection, optionally joined with the T4 archive repository the
    /// terminal spine confirms cold proofs against.
    fn gateway_with_archives(&self, artifacts: Option<Arc<dyn TaskArchiveRepo>>) -> Gateway {
        let coordination = Arc::new(
            CoordinationStore::new(
                self.store.clone(),
                self.store.clone(),
                self.store.clone(),
                self.layout.clone(),
            )
            .unwrap(),
        );
        let producer = StoreInboxProducer::new(
            Arc::new(SharedScanBudget::new(ScanBudgetConfig::default()).unwrap()),
            Arc::new(OutputProjectionService),
            self.store.clone(),
            self.store.clone(),
            self.store.clone(),
            self.store.clone(),
            self.layout.clone(),
            Arc::new(FixedClock(NOW)),
            InboxProducerConfig::new(BackendId("tbase".into())),
        )
        .unwrap();
        let rounds = match artifacts {
            None => StoreGroupRoundAdmission::new(
                coordination.clone(),
                Arc::new(producer.clone()),
                self.store.clone(),
                self.claim.clone(),
                self.layout.clone(),
                GroupAdmissionConfig::default(),
                Arc::new(FixedClock(NOW)),
            ),
            Some(artifacts) => StoreGroupRoundAdmission::with_artifacts(
                coordination.clone(),
                Arc::new(producer.clone()),
                self.store.clone(),
                self.claim.clone(),
                self.layout.clone(),
                GroupAdmissionConfig::default(),
                Arc::new(FixedClock(NOW)),
                artifacts,
            ),
        }
        .unwrap();
        Gateway {
            rounds,
            producer,
            coordination,
        }
    }

    fn ckpt_key(&self) -> String {
        self.layout.ckpt(&Sid(SID.into())).unwrap()
    }

    async fn inbox_entries(&self, gateway: &Gateway) -> usize {
        let mut feed = gateway.producer.open(ShardId(42)).await.unwrap();
        feed.watch(Sid(SID.into()), None).await.unwrap();
        let batch = feed
            .next(
                ReadLimits {
                    max_sessions: NonZeroU32::new(4).unwrap(),
                    max_entries: NonZeroU32::new(128).unwrap(),
                    max_bytes: NonZeroU32::new(1024 * 1024).unwrap(),
                },
                Duration::ZERO,
            )
            .await
            .unwrap();
        batch
            .sessions
            .iter()
            .flat_map(|session| &session.entries)
            .count()
    }

    /// The T7-shaped terminal state, through the same checkpoint CAS: the
    /// licensed round's task retires from the queue after effect
    /// confirmation, the license stays pinned to the session.
    async fn retire_round(&self, _gateway: &Gateway) {
        let sid = Sid(SID.into());
        let snapshot = match self
            .coordination
            .acquire(&sid, &self.claim, empty_checkpoint())
            .await
            .unwrap()
        {
            AcquireOutcome::Acquired(a) => a,
            other => panic!("{other:?}"),
        };
        let mut candidate = snapshot.envelope.checkpoint.clone();
        candidate.tasks.clear();
        candidate.commit_seq = CommitSeq(snapshot.envelope.checkpoint.commit_seq.0 + 1);
        let commit_id = CommitId(format!("retire-{}", candidate.commit_seq.0));
        let content_digest = Digest(
            serde_json::to_string(&candidate.runner_state).unwrap(),
        );
        candidate.outbox = Some(CommittedBatch {
            created_at: None,
            commit_id: commit_id.clone(),
            source_commit: candidate.commit_seq,
            content_digest: content_digest.clone(),
            effects: vec![],
        });
        assert!(matches!(
            self.coordination
                .commit(CommitRequest {
                    purpose: CommitPurpose::Ordinary { control_revision: 0 },
                    owner: snapshot.ownership,
                    expected_commit_seq: CommitSeq(candidate.commit_seq.0 - 1),
                    commit_id,
                    content_digest,
                    candidate,
                })
                .await
                .unwrap(),
            CommitOutcome::Committed(_)
        ));
    }
}

fn empty_checkpoint() -> Checkpoint {
    Checkpoint {
        format_version: 1,
        commit_seq: CommitSeq(0),
        consumed: None,
        tasks: Default::default(),
        dispatch_seq: Default::default(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: Default::default(),
        runner_state: None,
        last_event_id: None,
        feed_publications: Vec::new(),
        outbox: None,
    }
}

/// The generic sender-side Start envelope (old JSON-RPC wire shape).
fn generic_start(input: InputEnvelope) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input,
        create_session: Some(spec(GROUP)),
        create_task: Some(task()),
    }
}

#[allow(dead_code)]
fn rpc_start() -> AdmitRequest {
    generic_start(user_input("rpc-old", "old json-rpc start"))
}

// ---------------------------------------------------------------------------
// T1 prototype harness (frozen)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn prototype_group_round_repo_conformance() {
    let prototype = support::Prototype::new().await;
    group_round_repo_contract_tests(&prototype, support::request("a", "first", 1))
        .await
        .unwrap();
}

// ---------------------------------------------------------------------------
// T2 production conformance + vectors
// ---------------------------------------------------------------------------

#[tokio::test]
async fn real_store_group_round_repo_conformance() {
    let rig = Rig::new().await;
    let gateway = rig.gateway();
    group_round_repo_contract_tests(&gateway.rounds, round("msg-a", 1))
        .await
        .unwrap();
}

#[test]
fn group_admission_config_is_validated() {
    // The registry-row byte budget is a real bound: zero and oversized
    // configurations never construct (records never reach storage unchecked).
    let layout = KeyLayout::new("c01", "main").unwrap();
    assert!(StoreGroupRoundAdmission::new(
        dummy_sessions(),
        dummy_producer(&layout),
        dummy_documents(),
        dummy_claim(),
        layout.clone(),
        GroupAdmissionConfig {
            max_request_bytes: 0,
        },
        Arc::new(FixedClock(NOW)),
    )
    .is_err());
}

fn dummy_documents() -> Arc<Recorder> {
    Arc::new(Recorder::default())
}

fn dummy_sessions() -> Arc<CoordinationStore> {
    let store = Arc::new(Recorder::default());
    let layout = KeyLayout::new("c01", "main").unwrap();
    Arc::new(
        CoordinationStore::new(
            store.clone(),
            store.clone(),
            store.clone(),
            layout,
        )
        .unwrap(),
    )
}

fn dummy_producer(layout: &KeyLayout) -> Arc<StoreInboxProducer> {
    let store = Arc::new(Recorder::default());
    Arc::new(
        StoreInboxProducer::new(
            Arc::new(SharedScanBudget::new(ScanBudgetConfig::default()).unwrap()),
            Arc::new(OutputProjectionService),
            store.clone(),
            store.clone(),
            store.clone(),
            store.clone(),
            layout.clone(),
            Arc::new(FixedClock(NOW)),
            InboxProducerConfig::new(BackendId("tbase".into())),
        )
        .unwrap(),
    )
}

fn dummy_claim() -> ShardClaim {
    ShardClaim {
        shard: ShardId(42),
        owner: ShardOwner {
            instance: Instance {
                node: "worker".into(),
                incarnation: "1".into(),
            },
            epoch: WorkerEpoch(1),
        },
    }
}

#[allow(dead_code)]
fn strict_outcome_staging() {
    // Compile pin: staged rounds surface the staging contract, not success.
    let _ = StrictOutcome::Staged;
}

/// An error-injected T4 archive joint: every read fails with a definite
/// storage error. Writes fail too — this fixture only pins the read path of
/// the authoritative cut.
struct FaultyArchives;
#[async_trait::async_trait]
impl TaskArchiveRepo for FaultyArchives {
    async fn archive_final(&self, _source: EffectSource, _archive: TaskFinalArchive) -> Result<SavedArchive, StoreError> {
        Err(fault())
    }
    async fn get_archived(&self, _grants: &ArchiveGrants, _scope: &TaskScope, _group: &GroupId, _tid: &Tid) -> Result<Option<ArchivedTaskView>, StoreError> {
        Err(fault())
    }
    async fn list_archived_tasks(&self, _grants: &ArchiveGrants, _scope: &TaskScope, _group: &GroupId, _offset: u32, _limit: NonZeroU32) -> Result<Vec<Tid>, StoreError> {
        Err(fault())
    }
    async fn task_artifacts(&self, _grants: &ArchiveGrants, _scope: &TaskScope, _group: &GroupId, _tids: &[Tid]) -> Result<Vec<(Tid, ArtifactPart)>, StoreError> {
        Err(fault())
    }
    async fn read_task_result(&self, _grants: &ArchiveGrants, _scope: &TaskScope, _group: &GroupId, _tid: &Tid, _limit: NonZeroU32) -> Result<Option<(Vec<u8>, u64)>, StoreError> {
        Err(fault())
    }
    async fn mark_task_expired(&self, _scope: &TaskScope, _group: &GroupId, _tid: &Tid) -> Result<IdempotentWrite, StoreError> {
        Err(fault())
    }
    async fn reclaim_expired(&self, _scope: &TaskScope, _group: &GroupId, _tid: &Tid) -> Result<IdempotentWrite, StoreError> {
        Err(fault())
    }
    async fn confirm_terminal_proof(&self, _scope: &TaskScope, _group: &GroupId, _tid: &Tid, _proof: &TerminalProof) -> Result<IdempotentWrite, StoreError> {
        Err(fault())
    }
    async fn get_terminal_proof(&self, _grants: &ArchiveGrants, _scope: &TaskScope, _group: &GroupId, _tid: &Tid) -> Result<Option<TerminalProof>, StoreError> {
        Err(fault())
    }
}
fn fault() -> StoreError {
    StoreError::NotApplied {
        code: "archive_fault_injected".into(),
    }
}

/// The authoritative cut propagates its storage errors: a failing
/// terminal-proof or archive read of a live licensed round surfaces from
/// `get` as the read error itself, never as a terminal-less success view.
#[tokio::test]
async fn get_propagates_confirmed_view_read_errors() {
    let rig = Rig::new().await;
    let gateway = rig.gateway_with_archives(Some(Arc::new(FaultyArchives)));
    let accepted = round("pin-read", 1);
    assert!(matches!(
        gateway.rounds.admit(&accepted).await.unwrap(),
        Admission::New { .. }
    ));
    // The licensed round is live and its frozen row exists; the injected
    // archive read must surface, not downgrade visibility.
    let observed = gateway.rounds.get(&accepted.input.key).await;
    assert!(
        matches!(&observed, Err(StoreError::NotApplied { code }) if code == "archive_fault_injected"),
        "a read error is never a terminal-less success view, got {observed:?}"
    );
}
