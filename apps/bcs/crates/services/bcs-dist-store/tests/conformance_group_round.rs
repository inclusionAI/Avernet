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

#[tokio::test]
async fn two_gateways_one_round() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let two = rig.gateway();
    let winner = round("msg-a", 1);
    let loser = round("msg-b", 1);

    // Hold the winner exactly at its acceptance CAS: the license write is
    // durable, the reply is not yet delivered. A concurrent Gateway must
    // classify by the durable authority, never by pending intents.
    let pause = rig
        .store
        .pause_after("document.replace", &rig.ckpt_key(), 1);
    let first = tokio::spawn({
        let rounds = one.rounds.clone();
        let winner = winner.clone();
        async move { rounds.admit(&winner).await.unwrap() }
    });
    pause.reached().await;
    // While the winner is unacknowledged, the loser sees the license: Busy,
    // no task, no queue — and the same message retry finds the original.
    assert_eq!(two.rounds.admit(&loser).await.unwrap(), Admission::Busy);
    let identity = GroupRequestIdentity::strict(
        &winner.input.key.scope,
        &winner.input.key.group,
        &winner.request_id,
    );
    assert!(matches!(
        two.rounds.lookup(&identity).await.unwrap(),
        GroupRequestRecord::Accepted(_)
    ));
    pause.resume();
    let admitted = first.await.unwrap();
    assert_eq!(admitted, Admission::New { accepted: winner.clone() });

    // Winner retry through the other connection: the original Tid.
    assert!(matches!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { ref accepted } if accepted.input.key.tid == winner.input.key.tid
    ));

    // Exactly one accepted round; the loser never runs. The count covers the
    // whole admission log above: one New winner, one Busy loser, one Start.
    let mut new_count = 0;
    let mut busy_count = 0;
    for outcome in [
        &admitted,
        &two.rounds.admit(&loser).await.unwrap(),
        &two.rounds.admit(&winner).await.unwrap(),
    ] {
        if matches!(outcome, Admission::New { .. }) {
            new_count += 1;
        }
        if matches!(outcome, Admission::Busy) {
            busy_count += 1;
        }
    }
    // One executable Start reaches the single round's inbox.
    let executable_starts = rig.inbox_entries(&one).await;
    assert_eq!((new_count, busy_count, executable_starts), (1, 1, 1));

    // The busy Session takes no old-RPC write: zero physical writes.
    let writes_before = physical_writes(&rig.store);
    assert!(matches!(
        one.producer.admit(rpc_start()).await,
        Err(StoreError::NotApplied { ref code }) if code == "strict_claim_source"
    ));
    assert_eq!(physical_writes(&rig.store), writes_before);

    // After the previous round retires, the loser admits by its own message
    // id at the next generation; a wrong generation never installs.
    rig.retire_round(&one).await;
    // The free session answers a wrong generation as an error, never installs
    // it silently; the next request admits at the exact expected generation.
    assert!(matches!(
        two.rounds.admit(&round("msg-c", 99)).await,
        Err(StoreError::Invalid { ref code }) if code == "stale_generation"
    ));
    let loser_next = round("msg-b", 2);
    assert_eq!(
        two.rounds.admit(&loser_next).await.unwrap(),
        Admission::New { accepted: loser_next.clone() }
    );
    // The retired round's request is still the first identity of its message.
    assert!(matches!(
        one.rounds.admit(&round("msg-a", 1)).await.unwrap(),
        Admission::Replay { ref accepted } if accepted.input.key.tid == winner.input.key.tid
    ));

    // Cross-group, cross-principal and unknown Sid never create sessions.
    let before = physical_writes(&rig.store);
    let mut other_group = round("cross-a", 1);
    other_group.input.key.group = GroupId("grp-other".into());
    assert!(matches!(
        one.rounds.admit(&other_group).await,
        Err(StoreError::Invalid { ref code }) if code == "group_identity_mismatch"
    ));
    let mut other_principal = round("cross-b", 1);
    other_principal.input.key.scope.principal = PrincipalId("user-002".into());
    assert!(matches!(
        one.rounds.admit(&other_principal).await,
        Err(StoreError::Invalid { ref code }) if code == "group_scope_mismatch"
    ));
    let mut unknown = round("cross-c", 1);
    unknown.input.key.sid = Sid("s0099.unknown.1".into());
    assert!(matches!(
        one.rounds.admit(&unknown).await,
        Err(StoreError::NotApplied { ref code }) if code == "session_not_found"
    ));
    assert_eq!(physical_writes(&rig.store), before);
}

#[tokio::test]
async fn prepared_snapshot_is_not_a_public_task() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let winner = round("msg-a", 1);
    let guessed = winner.input.key.tid.clone();
    let nonexistent = Tid(format!("{SID}.never-admitted"));
    let cursor = |tid: &Tid| TaskCursor {
        sid: Sid(SID.into()),
        tid: tid.clone(),
        token: ReplayToken {
            backend: BackendId("tbase".into()),
            generation: LogGeneration("1".into()),
            value: "0-0".into(),
        },
    };

    // Stop the admitting Gateway between its staged snapshot/index writes
    // and the permission grant: no license yet.
    let pause = rig
        .store
        .pause_after("before.document.replace", &rig.ckpt_key(), 1);
    let pending = tokio::spawn({
        let rounds = one.rounds.clone();
        let winner = winner.clone();
        async move { rounds.admit(&winner).await }
    });
    pause.reached().await;

    // The snapshot and list index physically exist (pre-writes are durable
    // repairs); Get/subscribe/cancel of the guessed Tid behave exactly like a
    // resource that never existed, and List/total exclude it.
    assert!(rig
        .store
        .read_document(&rig.layout.task(&guessed).unwrap())
        .await
        .unwrap()
        .is_some());
    let listed: Vec<String> = ZSetStorePlugin::range(
        rig.store.as_ref(),
        &rig.layout.tasks(&scope()).unwrap(),
        &ZSetRange {
            min: std::ops::Bound::Included(0.0),
            max: std::ops::Bound::Included(0.0),
            after: None,
            limit: NonZeroU32::new(128).unwrap(),
        },
    )
    .await
    .unwrap()
    .into_iter()
    .map(|entry| entry.member)
    .collect();
    assert!(listed.contains(&guessed.0), "the staged pre-write is real");
    assert_eq!(
        rig.feed.restore(&scope(), &guessed).await.unwrap(),
        rig.feed.restore(&scope(), &nonexistent).await.unwrap()
    );
    assert_eq!(
        rig.feed
            .load_snapshot(&scope(), &guessed)
            .await
            .unwrap(),
        rig.feed
            .load_snapshot(&scope(), &nonexistent)
            .await
            .unwrap()
    );
    assert_eq!(
        rig.feed.validate_resume(&scope(), &cursor(&guessed)).await,
        rig.feed
            .validate_resume(&scope(), &cursor(&nonexistent))
            .await
    );
    assert_eq!(
        rig.feed
            .list(
                &scope(),
                TaskFilter {
                    sid: None,
                    statuses: vec![],
                },
                PageRequest {
                    after: None,
                    limit: NonZeroU32::new(16).unwrap(),
                },
            )
            .await
            .unwrap()
            .items
            .len(),
        0
    );
    assert_eq!(one.rounds.get(&winner.input.key).await.unwrap(), None);

    // Old-RPC cancel against the staged session: rejected at the store seam,
    // zero physical writes.
    let writes_before = physical_writes(&rig.store);
    let cancel = AdmitRequest {
        scope: scope(),
        input: InputEnvelope {
            schema_version: 1,
            sid: Sid(SID.into()),
            tid: guessed.clone(),
            input_id: InputId("rpc-cancel".into()),
            business_digest: Digest("rpc-cancel".into()),
            body: InputBody::Cancel { reason: None },
        },
        create_session: None,
        create_task: None,
    };
    assert!(matches!(
        one.producer.admit(cancel).await,
        Err(StoreError::NotApplied { ref code }) if code == "strict_claim_source"
    ));
    assert_eq!(physical_writes(&rig.store), writes_before);

    // The abandoned attempt never gains permission: fail the pending
    // acceptance CAS definitely, restart with a fresh connection, and the
    // staged round stays invisible.
    rig.store.fail_write(1);
    pause.resume();
    assert!(pending.await.unwrap().is_err());
    let two = rig.gateway();
    assert_eq!(
        two.rounds.get(&winner.input.key).await.unwrap(),
        None,
        "an abandoned attempt stays invisible after restart"
    );
    assert_eq!(rig.feed.restore(&scope(), &guessed).await.unwrap(), None);
    assert_eq!(rig.rounds_list_is_empty().await, true);

    // Successful recovery shows exactly one round: one license, one Start.
    assert_eq!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::New { accepted: winner.clone() }
    );
    assert!(two
        .rounds
        .get(&winner.input.key)
        .await
        .unwrap()
        .is_some());
    let starts = rig.inbox_entries(&two).await;
    assert_eq!(starts, 1, "one durable Start entry for the round");
    assert!(matches!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { .. }
    ));
    assert_eq!(rig.inbox_entries(&two).await, 1);
}

impl Rig {
    async fn rounds_list_is_empty(&self) -> bool {
        self.feed
            .list(
                &scope(),
                TaskFilter {
                    sid: None,
                    statuses: vec![],
                },
                PageRequest {
                    after: None,
                    limit: NonZeroU32::new(16).unwrap(),
                },
            )
            .await
            .unwrap()
            .items
            .is_empty()
    }
}


/// Durable strict slot census of the session schedule (physical state).
async fn strict_slots(rig: &Rig) -> (usize, usize) {
    let key = rig.layout.schedule(&Sid(SID.into())).unwrap();
    let Some(raw) = rig.store.read_document(&key).await.unwrap() else {
        return (0, 0);
    };
    let slots = raw.value["slots"].as_object().cloned().unwrap_or_default();
    let strict = slots
        .values()
        .filter(|slot| (*slot)["strict"] == serde_json::json!(true))
        .count();
    (slots.len(), strict)
}

/// A legacy generic Start envelope (old JSON-RPC wire shape) with a stable
/// input id: repeated retries locate the same admission record.
fn legacy_start(input_id: &str) -> AdmitRequest {
    let mut input = user_input(input_id, "old json-rpc start");
    input.tid = Tid(TID.into());
    generic_start(input)
}

#[tokio::test]
async fn stranded_generic_record_is_fenced_by_strict_adoption() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    // (i) A legacy generic Start strands: its admission record is durable,
    // undecided (no decision), and has not yet registered a schedule slot.
    let pause = rig.store.pause_after("document.create", "admission", 1);
    let stranded = tokio::spawn({
        let producer = one.producer.clone();
        let request = legacy_start("rpc-stranded");
        async move { producer.admit(request).await }
    });
    pause.reached().await;
    stranded.abort();
    drop(pause);
    assert!(
        one.producer
            .lookup(&scope(), &InputId("rpc-stranded".into()))
            .await
            .unwrap()
            .is_some(),
        "the legacy record is durable and undecided"
    );

    // (ii) Strict adoption reaches the session: the busy predicate consults
    // the checkpoint queue, which a stranded generic record never touched
    // (inbox admission never writes checkpoint tasks).
    let winner = round("msg-a", 1);
    assert_eq!(
        one.rounds.admit(&winner).await.unwrap(),
        Admission::New { accepted: winner.clone() }
    );

    // (iii) The old-RPC client retries its InputId: the stranded record is
    // fenced with ZERO physical writes and no executable Start is produced.
    let before = physical_writes(&rig.store);
    assert!(matches!(
        one.producer.admit(legacy_start("rpc-stranded")).await,
        Err(StoreError::NotApplied { ref code }) if code == "strict_claim_source"
    ));
    assert_eq!(
        physical_writes(&rig.store),
        before,
        "a fenced old-RPC retry leaves no physical write"
    );
    // Physical absence: exactly the strict round's single Start is in the
    // session stream, and no undecided generic slot sits in the schedule.
    assert_eq!(rig.inbox_entries(&one).await, 1);
    let (total, strict) = strict_slots(&rig).await;
    assert_eq!((total, strict), (1, 1));
    // The strict round keeps working after the fence.
    assert!(matches!(
        one.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { .. }
    ));
}

#[tokio::test]
async fn duel_loser_forfeits_its_staged_claim() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let two = rig.gateway();
    let winner = round("msg-a", 1);
    let loser = round("msg-b", 1);

    // Duel 1: hold the loser between its staged writes and its acceptance
    // CAS; the winner commits first; the loser resumes into the conflict.
    let pause = rig
        .store
        .pause_after("before.document.replace", &rig.ckpt_key(), 1);
    let loser_task = tokio::spawn({
        let rounds = two.rounds.clone();
        let loser = loser.clone();
        async move { rounds.admit(&loser).await }
    });
    pause.reached().await;
    assert_eq!(
        one.rounds.admit(&winner).await.unwrap(),
        Admission::New { accepted: winner.clone() }
    );
    pause.resume();
    assert_eq!(loser_task.await.unwrap(), Ok(Admission::Busy));

    // The loser's staged record is durably fenced (rejections never resume
    // into a Start entry), and its schedule slot is freed: only the
    // winner's sticky claim remains.
    let input_id =
        bcs_dist_store::group_admission::strict_round_input_id(&loser).unwrap();
    assert_eq!(
        two.producer
            .admission_decision(&scope(), &input_id)
            .await
            .unwrap(),
        Some(AdmitOutcome::Quarantined)
    );
    let (total, strict) = strict_slots(&rig).await;
    assert_eq!((total, strict), (1, 1), "no resident loser slot");

    // The forfeited round never resurrects at its OLD generation: after the
    // winner's round retires, re-sending the loser's original gen-1 request
    // is an error, writes nothing and executes nothing (nuance (b)).
    rig.retire_round(&one).await;
    let before = physical_writes(&rig.store);
    assert!(matches!(
        two.rounds.admit(&loser).await,
        Err(StoreError::Invalid { ref code }) if code == "stale_generation"
    ));
    assert_eq!(physical_writes(&rig.store), before);
    assert_eq!(rig.inbox_entries(&one).await, 1, "no new executable Start");

    // Same messageId at the NEW generation re-admits instead of wedging at
    // the forfeited record: supersede → stage → license → one new Start.
    let resubmit = round("msg-b", 2);
    assert_eq!(
        two.rounds.admit(&resubmit).await.unwrap(),
        Admission::New { accepted: resubmit.clone() }
    );
    let input_id =
        bcs_dist_store::group_admission::strict_round_input_id(&resubmit).unwrap();
    assert!(matches!(
        two.producer.admission_decision(&scope(), &input_id).await.unwrap(),
        Some(AdmitOutcome::Duplicate(_))
    ));
    assert_eq!(rig.inbox_entries(&one).await, 2, "exactly one new Start");
    let (total, strict) = strict_slots(&rig).await;
    assert_eq!((total, strict), (2, 2));

    // Duel 2 at the next generation: retire so the session is free, duel
    // again, and prove the slot budget only carries winners — a later
    // install still succeeds, never OutcomeUnknown.
    rig.retire_round(&one).await;
    let winner2 = round("msg-d", 3);
    let loser2 = round("msg-c", 3);
    let pause = rig
        .store
        .pause_after("before.document.replace", &rig.ckpt_key(), 1);
    let loser_task = tokio::spawn({
        let rounds = two.rounds.clone();
        let loser2 = loser2.clone();
        async move { rounds.admit(&loser2).await }
    });
    pause.reached().await;
    assert_eq!(
        one.rounds.admit(&winner2).await.unwrap(),
        Admission::New { accepted: winner2.clone() }
    );
    pause.resume();
    assert_eq!(loser_task.await.unwrap(), Ok(Admission::Busy));
    let input_id2 =
        bcs_dist_store::group_admission::strict_round_input_id(&loser2).unwrap();
    assert_eq!(
        two.producer
            .admission_decision(&scope(), &input_id2)
            .await
            .unwrap(),
        Some(AdmitOutcome::Quarantined)
    );
    let (total, strict) = strict_slots(&rig).await;
    assert_eq!(
        (total, strict),
        (3, 3),
        "duels accumulate winners' claims only"
    );
    // The session keeps working: the winner replays by original identity.
    assert!(matches!(
        one.rounds.admit(&winner2).await.unwrap(),
        Admission::Replay { .. }
    ));
}

#[tokio::test]
async fn lost_acceptance_reply_recovers_the_original_round() {
    let rig = Rig::new().await;
    let one = rig.gateway();
    let two = rig.gateway();
    let winner = round("msg-a", 1);
    // The license CAS is durable but its caller dies before the reply: the
    // admission record itself is unknown to the crashed Gateway. A successor
    // recovers the original Tid through the same message key and delivers the
    // single Start; the loser stays busy. One business round survives.
    let pause = rig
        .store
        .pause_after("document.replace", &rig.ckpt_key(), 1);
    let crashed = tokio::spawn({
        let rounds = one.rounds.clone();
        let winner = winner.clone();
        async move { rounds.admit(&winner).await }
    });
    pause.reached().await;
    crashed.abort();
    drop(pause);
    assert!(matches!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { ref accepted } if accepted.input.key.tid == winner.input.key.tid
    ));
    assert_eq!(rig.inbox_entries(&one).await, 1);
    let loser = round("msg-b", 1);
    assert_eq!(two.rounds.admit(&loser).await.unwrap(), Admission::Busy);
    // Duplicate Start delivery (a successor re-issuing the same round's
    // aftermath) applies once.
    assert!(matches!(
        two.rounds.admit(&winner).await.unwrap(),
        Admission::Replay { .. }
    ));
    assert_eq!(rig.inbox_entries(&one).await, 1);
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
