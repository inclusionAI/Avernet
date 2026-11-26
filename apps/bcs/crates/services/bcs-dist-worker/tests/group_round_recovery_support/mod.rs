//! Shared joints of the T7 commit-spine vectors (plan task 13):
//!
//! a real strict-group round admitted through the checkpoint-CAS
//! `StoreGroupRoundAdmission`, executed by a real WorkerProcessor over the
//! shared processor harness, archived through the real T4
//! `GroupArtifactStore`, and read back through the same authoritative cut
//! that admits the next round. Fault injection reuses the existing
//! `FaultSessions` (commit pauses, lost replies) and `FaultPublisher` (SSE
//! pauses) seams; nothing here stubs the stores.
//!
//! The processors built in this fixture are deliberately local: dropping one
//! and rebuilding a successor over the same persistent joints is how the
//! vectors kill and restart a Worker without touching durable state.
#![allow(dead_code, clippy::unwrap_used, reason = "commit-spine recovery fixtures")]
use crate::processor_support::{self, Directory, Fixture};
use bcs_dist_model::*;
use bcs_service_api::{
    core::dist_runner::*,
    port::{
        repo::distributed::{
            dispatch::HeldDispatchPort,
            group_round::*,
            inbox::*,
            result::*,
            session::*,
        },
    },
};
use bcs_dist_store::{
    group_artifact::{GroupArtifactStore, JsonProjectionValidator},
    group_admission::{GroupAdmissionConfig, StoreGroupRoundAdmission},
    layout::KeyLayout,
};
use bcs_service_api::port::repo::distributed::common::SessionOwnership;
use bcs_service_api::port::repo::distributed::ownership::*;
use bcs_store_api::document::DocumentStorePlugin;
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};

pub const SCOPE_TENANT: &str = "tenant";
pub const SCOPE_PRINCIPAL: &str = "caller";
pub const GROUP: &str = "g-coord";
pub const NODE: &str = "n2";

pub fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId(SCOPE_TENANT.into()),
        principal: PrincipalId(SCOPE_PRINCIPAL.into()),
    }
}

pub fn sid() -> Sid {
    Sid("s0001.session".into())
}

/// A strict round request: generation is explicit, the Tid lives inside the
/// session, the workflow ref matches the seeded spec.
pub fn round(message: &str, generation: u64) -> GroupStartRequest {
    let empty_history = serde_json::json!({"entries": []});
    AcceptedRound {
        input: RoundInput {
            schema_version: RoundSchemaVersion::V1,
            source: GroupRoundSource::GroupA2a,
            key: RoundKey {
                scope: scope(),
                group: GroupId(GROUP.into()),
                sid: sid(),
                tid: Tid(format!("{}.{}", sid().0, message)),
                generation: generation.try_into().unwrap(),
            },
            ordered_parts: vec![RoundPart::Text {
                text: message.into(),
            }],
            workflow_ref: WorkflowRef {
                definition_id: "wf".into(),
                revision: "1".into(),
                digest: Digest("wf-digest".into()),
            },
            history_cut: BlobRef {
                key: "round-history".into(),
                digest: bcs_dist_encoding::digest_json(&empty_history),
            },
            execution_deadline: TimestampMillis(100_000),
        },
        request_id: RequestId(message.into()),
        request_digest: Digest(format!("digest-{message}")),
        normalization_ref: BlobRef {
            key: "norm-v1".into(),
            digest: Digest("norm-digest".into()),
        },
        return_configuration: serde_json::json!({"mode":"text"}),
    }
}

/// The FSM runner of one strict round: it shows the round's answer and, per
/// mode, finishes immediately or only after its dispatched node confirmed.
pub struct FsmRunner {
    pub mode: FsmMode,
    pub calls: Arc<AtomicUsize>,
}

#[derive(Clone, Copy, PartialEq)]
pub enum FsmMode {
    /// Show the answer and FinishTask in the first reduction.
    AnswerThenFinish,
    /// StartDispatch to the sibling node and yield; finish only on the
    /// sibling's Final report.
    WaitOnSibling,
    /// Show one over-threshold inline artifact and FinishTask in the first
    /// reduction: the registered obligation pins externalization of
    /// retained publication payloads (fix round I1).
    HugeAnswer,
    /// Upsert "a" then AppendText " b" on one artifact, then FinishTask:
    /// pins the per-artifact amendment rule in both part derivations
    /// (fix round I3).
    AmendAnswer,
}

impl FsmRunner {
    pub fn new(mode: FsmMode) -> Arc<Self> {
        Arc::new(Self {
            mode,
            calls: Arc::new(AtomicUsize::new(0)),
        })
    }
}

fn dispatch_spec_for(tid: &Tid, spec: &SessionSpec) -> DispatchSpec {
    let binding = spec
        .participants
        .iter()
        .find(|binding| binding.node.0 == NODE)
        .expect("sibling binding seeded");
    DispatchSpec {
        schema_version: 1,
        sid: spec.sid.clone(),
        tid: tid.clone(),
        did: Did(format!("{}.fsm:1:{NODE}:1", tid.0)),
        node: binding.node.clone(),
        attempt: 1,
        target: binding.target.clone(),
        pool: binding.pool.clone(),
        capabilities: binding.capabilities.clone(),
        request: Payload::Inline(serde_json::json!({"question":"node"})),
        deadline: TimestampMillis(600_000),
    }
}

impl DistRunnerCoreService for FsmRunner {
    fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        Ok(RunnerState {
            format_version: 1,
            value: serde_json::json!({"step":0}),
        })
    }
    fn reduce(
        &self,
        c: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let mut actions = Vec::new();
        // The T9 joint assembly hands strict rounds to the runner as
        // `GroupStart` (the licensed frozen RoundInput), so the fixture
        // reduces the same round facts it always did.
        if let RunnerInput::GroupStart { input } = &inputs[0] {
            let tid = input.key.tid.clone();
            if self.mode == FsmMode::AnswerThenFinish {
                actions.push(show(&tid));
                actions.push(finish(&tid));
            } else if self.mode == FsmMode::HugeAnswer {
                actions.push(show_update(
                    &tid,
                    ArtifactUpdate::Upsert {
                        content: Payload::Inline(serde_json::json!("x".repeat(80_000))),
                    },
                ));
                actions.push(finish(&tid));
            } else if self.mode == FsmMode::AmendAnswer {
                actions.push(show_update(
                    &tid,
                    ArtifactUpdate::Upsert {
                        content: Payload::Inline(serde_json::json!("a")),
                    },
                ));
                actions.push(show_update(
                    &tid,
                    ArtifactUpdate::AppendText {
                        delta: Payload::Inline(serde_json::json!(" b")),
                    },
                ));
                actions.push(finish(&tid));
            } else {
                actions.push(ActionIntent::StartDispatch(dispatch_spec_for(&tid, c.spec)));
            }
        } else if let RunnerInput::Agent { event, .. } = &inputs[0] {
            if matches!(event.kind, AgentEventKind::Final)
                && let Some(tid) = event_tid(event)
            {
                actions.push(show(&tid));
                actions.push(finish(&tid));
            }
        }
        // Deadlines and intermediate deltas do not change this runner's
        // policy; the terminal decision stays with the stop barrier.
        let step = state
            .value
            .get("step")
            .and_then(|v| v.as_u64())
            .unwrap_or(0)
            + 1;
        Ok(Decision {
            next_state: RunnerState {
                format_version: 1,
                value: serde_json::json!({"step": step}),
            },
            actions,
            next_deadlines: vec![],
        })
    }
}

fn show(tid: &Tid) -> ActionIntent {
    show_update(
        tid,
        ArtifactUpdate::Upsert {
            content: Payload::Inline(serde_json::json!("answer-of-round")),
        },
    )
}
fn show_update(tid: &Tid, update: ArtifactUpdate) -> ActionIntent {
    ActionIntent::Show {
        tid: tid.clone(),
        node: NodeId(NODE.into()),
        artifact_id: ArtifactId("answer".into()),
        class: OutputClass::Output,
        update,
    }
}

fn event_tid(event: &AgentEvent) -> Option<Tid> {
    event
        .did
        .0
        .rsplit_once(".fsm:")
        .map(|(tid, _)| Tid(tid.into()))
}

fn finish(tid: &Tid) -> ActionIntent {
    ActionIntent::FinishTask {
        tid: tid.clone(),
        status: TerminalStatus::Completed,
        result: Payload::Inline(serde_json::json!({"finished":true})),
    }
}

/// The assembled commit-spine joints.
pub struct RoundWorld {
    pub f: Fixture,
    pub admission: Arc<StoreGroupRoundAdmission>,
    pub artifacts: Arc<GroupArtifactStore>,
    /// The live FSM runner shared by every processor generation, so the
    /// vectors can prove a killed Worker never re-ran the round.
    pub runner: Arc<dyn DistRunnerCoreService>,
}

impl RoundWorld {
    /// Builds every real joint over the shared processor fixture: the strict
    /// round admission claims shard 1 with the runtime's own claim, and the
    /// T4 artifact store shares the checkpoint layout of the same model.
    pub async fn new(runner: Arc<dyn DistRunnerCoreService>) -> Self {
        Self::with_spec(runner, strict_spec()).await
    }

    /// Same joints with a session-spec knob (step/deadline bounds): the
    /// immutable spec document is seeded exactly like production admission.
    pub async fn with_spec(
        runner: Arc<dyn DistRunnerCoreService>,
        spec: SessionSpec,
    ) -> Self {
        let f = Fixture::new(runner.clone()).await;
        // The inbox producer validation requires an existing SessionSpec for
        // the round's session. Seed the immutable spec document first: the
        // strict admission binds scope/group/workflow against it.
        let mut spec = spec;
        spec.sid = sid();
        spec.shard = KeyLayout::shard_of(&sid()).unwrap();
        f.model
            .create_document(&f.layout.spec(&sid()).unwrap(), &serde_json::to_value(&spec).unwrap())
            .await
            .unwrap();
        // The runtime claims its shard on the first tick; the round admission
        // must share that claim or every CAS would fence.
        f.runtime.tick().await.unwrap();
        // Shard claims require the worker lease ladder (heartbeat renew, then
        // `lease_ttl` of settled liveness) which is real wall-clock time even
        // under tokio's paused clock: poll with real sleeps, bounded.
        for _ in 0..200 {
            if let Some(record) = f.coordination.current(ShardId(1)).await.unwrap() {
                let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
                bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
                let artifacts = Arc::new(
                    GroupArtifactStore::new(
                        db,
                        f.model.clone(),
                        f.layout.clone(),
                        Arc::new(JsonProjectionValidator),
                        archive_budget(),
                    )
                    .unwrap(),
                );
                // The joint fixture assembles the full T7 spine: the strict
                // round admission and the T4 archive join, so every
                // commit path (worker happy path and gate repair) closes
                // through the same owner-fenced window.
                let admission = Arc::new(StoreGroupRoundAdmission::with_artifacts(
                    f.sessions.clone(),
                    f.inbox.clone(),
                    f.model.clone(),
                    record.claim,
                    f.layout.clone(),
                    GroupAdmissionConfig::default(),
                    f.clock.clone(),
                    artifacts.clone(),
                )
                .unwrap());
                return Self {
                    f,
                    admission,
                    artifacts,
                    runner,
                };
            }
            f.runtime.tick().await.unwrap();
            std::thread::sleep(Duration::from_millis(50));
        }
        panic!("worker runtime did not claim shard 1");
    }

    /// The retained checkpoint of the strict session.
    pub async fn checkpoint(&self) -> Checkpoint {
        let envelope = self
            .f
            .coordination
            .load(&sid())
            .await
            .unwrap()
            .expect("session envelope");
        envelope.checkpoint
    }

    /// The terminal view of one round, through the same authoritative cut.
    pub async fn round_view(&self, key: &RoundKey) -> Option<GroupRoundView> {
        self.admission.get(key).await.unwrap()
    }
}

/// A fresh Worker processor over the same persistent joints: dropping an
/// older handle and rebuilding this one is how the vectors kill and restart
/// a Worker without touching durable state.
pub fn local_processor(
    world: &Arc<RoundWorld>,
) -> Arc<bcs_dist_worker::WorkerProcessor> {
    use bcs_dist_worker::*;
    use bcs_dist_store::inbox::StoreAdmissionControl;
    Arc::new(
        WorkerProcessor::new(
            world.f.runtime.clone(),
            ProcessorPorts {
                scan_budget: world.f.scan_budget.clone(),
                control: world.f.control.clone(),
                admission: Arc::new(StoreAdmissionControl::new(
                    (*world.f.inbox).clone(),
                    world.f.feed.clone(),
                )),
                runner: runner_of(world),
                projection: Arc::new(bcs_dist_projection::OutputProjectionService),
                directory: Arc::new(Directory),
                tasks: world.f.feed.clone(),
                output: world.f.publisher.clone(),
                blobs: world.f.blobs.clone(),
                results: world.f.archive.clone(),
                dispatch: world.f.dispatch.clone(),
                // The spine joints follow the fixture's own admission and
                // archive stores, exactly the way the production assembly
                // wires them (the Worker never fabricates authority on its
                // own).
                rounds: Some(Arc::clone(&world.admission) as Arc<dyn GroupRoundRepo>),
                archives: Some(Arc::clone(&world.artifacts) as Arc<dyn TaskArchiveRepo>),
                now: {
                    let clock = world.f.clock.clone();
                    Arc::new(move || TimestampMillis(clock.0.load(Ordering::SeqCst)))
                },
            },
            ProcessorConfig::default(),
        )
        .unwrap(),
    )
}

fn runner_of(world: &Arc<RoundWorld>) -> Arc<dyn DistRunnerCoreService> {
    // The fixture's live runner is shared with the Fixture-built processor;
    // rebuilding the processor must observe the very same Arc so a killed
    // successor never re-runs the round from zero.
    world.runner.clone()
}

/// Drives until the checkpoint satisfies the predicate or fails loudly; each
/// attempt ticks the runtime and the given processor.
pub async fn until(
    world: &Arc<RoundWorld>,
    processor: &Arc<bcs_dist_worker::WorkerProcessor>,
    mut predicate: impl FnMut(&Checkpoint) -> bool,
) -> Checkpoint {
    for _ in 0..400 {
        drive(world, processor, 1).await;
        let checkpoint = world.checkpoint().await;
        if predicate(&checkpoint) {
            return checkpoint;
        }
    }
    panic!(
        "checkpoint predicate never held: {:?}",
        world.checkpoint().await
    );
}


pub fn archive_budget() -> ArchiveBudget {
    // The D0 profile, never a test-invented value.
    ArchiveBudget {
        version: 1,
        profile: RoundBudgetProfile::v1(),
    }
}

/// The immutable session spec of the strict rounds; the FSM consumes inside
/// its single licensed round.
pub fn strict_spec() -> SessionSpec {
    let mut spec = processor_support::spec();
    spec.kind = SessionKind::FsmGroup;
    spec.scope = scope();
    spec.group = GroupId(GROUP.into());
    spec.participants = vec![ParticipantBinding {
        node: NodeId(NODE.into()),
        target: AgentBinding {
            agent: AgentId("agent".into()),
            binding_version: BindingVersion("1".into()),
            credential_ref: CredentialRef("fixture".into()),
        },
        pool: PoolId("pool".into()),
        capabilities: DownstreamCapabilities {
            idempotent_start: true,
            idempotent_input: true,
            query_by_request: true,
            query_by_task: true,
            subscribe: true,
            resume_events: true,
            cancel: true,
            isolated_memory: true,
            persistent_dedupe: true,
        },
    }];
    spec
}

/// The killed Worker generation's local hand-off: the quiescent resident
/// session is released exactly as a real crash's successor would find it,
/// so the acquisition machinery (deferred retries, pending scans) can
/// re-claim the work the durable publication register still owns.
pub async fn kill_generation(world: &Arc<RoundWorld>) {
    let claim = world
        .f
        .coordination
        .current(ShardId(1))
        .await
        .unwrap()
        .expect("shard 1 claim")
        .claim;
    let _ = world
        .f
        .runtime
        .release_session(&SessionOwnership {
            sid: sid(),
            shard: ShardId(1),
            owner: claim.owner,
        })
        .await;
}


// ---------------------------------------------------------------------------
// Shared strict-round feed publication vector helpers (plan T7 slice 2 and
// its fix rounds): wedges, takeovers, feed reads and register accessors.
// ---------------------------------------------------------------------------

pub fn sibling_did(tid: &Tid) -> Did {
    Did(format!("{}.fsm:1:{NODE}:1", tid.0))
}

pub async fn bounded<const SECS: u64>(future: impl Future<Output = ()>) {
    tokio::time::timeout(Duration::from_secs(SECS), future)
        .await
        .expect("vector exceeded its wall-time bound");
}

/// The ConnectorAdmission report of one dispatch terminal: no
/// `remote_event_id`, the stable connector key identity and the state bound
/// inside the payload.
pub fn terminal_report(tid: &Tid, did: &Did, state: &str) -> ConnectorAdmission {
    let event = AgentEvent {
        did: did.clone(),
        holder_epoch: HolderEpoch(1),
        seq: InputSeq(1),
        kind: AgentEventKind::Final,
        remote_event_id: None,
        payload: Payload::Inline(serde_json::json!({"state": state, "answer": "answer-of-round"})),
    };
    ConnectorAdmission {
        input: InputEnvelope {
            schema_version: 1,
            sid: sid(),
            tid: tid.clone(),
            input_id: bcs_dist_encoding::connector::key_input_id(
                did,
                bcs_dist_encoding::connector::KeyEvent::Final,
            ),
            business_digest: Digest(String::new()),
            body: InputBody::Agent { event },
        },
        identity: ConnectorEventIdentity {
            round: None,
            state: state.into(),
        },
    }
}

pub fn wedge_feed(world: &RoundWorld) {
    world
        .f
        .publisher
        .fail_session
        .lock()
        .unwrap()
        .replace((sid(), false));
}
pub fn release_feed(world: &RoundWorld) {
    world.f.publisher.fail_session.lock().unwrap().take();
}
pub fn wedge_publish(world: &RoundWorld) {
    world
        .f
        .publisher
        .fail_publish
        .lock()
        .unwrap()
        .replace(sid());
}
pub fn release_publish(world: &RoundWorld) {
    world.f.publisher.fail_publish.lock().unwrap().take();
}
pub fn wedge_conflict(world: &RoundWorld) {
    world
        .f
        .publisher
        .conflict_session
        .lock()
        .unwrap()
        .replace(sid());
}
pub fn release_conflict(world: &RoundWorld) {
    world.f.publisher.conflict_session.lock().unwrap().take();
}

/// The ordered public events of the session feed, decoded from the durable
/// stream the publisher writes — the only claim of "published" this suite
/// accepts.
pub async fn feed_events(world: &RoundWorld) -> Vec<OutputEvent> {
    use bcs_store_api::{
        stream::{ReadOptions, StreamRead, StreamStorePlugin},
        StoreCapability, StorePlugin,
    };
    assert!(
        world.f.model.supports(StoreCapability::Stream),
        "the fixture model is the session feed's stream provider"
    );
    let key = world.f.layout.ev(&sid()).unwrap();
    let mut events = Vec::new();
    let mut after = None;
    for _ in 0..32 {
        let mut page = world
            .f
            .model
            .read(
                &[StreamRead {
                    key: key.clone(),
                    after,
                }],
                ReadOptions {
                    max_entries: std::num::NonZeroU32::new(256).unwrap(),
                    block: None,
                },
            )
            .await
            .unwrap()
            .pop()
            .unwrap()
            .page;
        let count = page.entries.len();
        if let Some(last) = page.entries.last() {
            after = Some(last.id);
        }
        for entry in std::mem::take(&mut page.entries) {
            let bytes = entry
                .fields
                .get("event")
                .expect("feed entries carry their event frame");
            events.push(serde_json::from_slice::<OutputEvent>(bytes).unwrap());
        }
        if count == 0 {
            return events;
        }
    }
    panic!("feed page loop did not terminate");
}

/// Drives A through the sibling leg (report + durable stop fact) so the round
/// holds its finalized record at the AwaitingResult boundary.
pub async fn settle_a_round(
    world: &Arc<RoundWorld>,
    processor: &Arc<bcs_dist_worker::WorkerProcessor>,
    tid: &Tid,
) {
    let did = sibling_did(tid);
    until_dispatch(world, processor, &did).await;
    claim_sibling(world, &did).await;
    world
        .f
        .inbox
        .admit_connector(terminal_report(tid, &did, "final"))
        .await
        .unwrap();
    confirm_attempt_stop(world, &did).await;
}

pub async fn publications(world: &RoundWorld) -> Vec<FeedPublication> {
    world.checkpoint().await.feed_publications
}

pub fn find_publication<'p>(
    publications: &'p [FeedPublication],
    effect: &EffectId,
) -> Option<&'p FeedPublication> {
    publications.iter().find(|p| &p.effect == effect)
}

pub fn artifact_index(events: &[OutputEvent], tid: &Tid) -> Option<usize> {
    events.iter().position(|e| {
        e.tid == *tid
            && matches!(
                &e.body,
                OutputBody::Artifact {
                    artifact_id,
                    update: ArtifactUpdate::Upsert { .. },
                    ..
                } if *artifact_id == ArtifactId("answer".into())
            )
    })
}

pub fn terminal_index(events: &[OutputEvent], tid: &Tid) -> Option<usize> {
    events
        .iter()
        .position(|e| e.tid == *tid && matches!(&e.body, OutputBody::Terminal { .. }))
}

/// Wedge the shared feed; A must still close its authority, retire, and B
/// must actually dispatch its first node while every feed write is refused;
/// afterwards the restored lane delivers A's events with the artifact before
/// the terminal, exactly once, from the retained predecessor.

/// Drives the inbox/scheduling runtime and then the processor, the same
/// heartbeat the lifecycle targets use.
pub async fn drive(
    world: &Arc<RoundWorld>,
    processor: &Arc<bcs_dist_worker::WorkerProcessor>,
    n: usize,
) {
    for _ in 0..n {
        world.f.runtime.tick().await.unwrap();
        // The commit spine deliberately introduces an out-of-band fenced
        // writer (the round admission's terminal CAS), so the processor's
        // own next-candidate commits can lose the head race and retry the
        // next tick. Terminal assertions live in the checkpoints and
        // views, not in transient tick errors.
        let _ = processor.tick().await;
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}

/// Waits until the dispatch record is durable (its CreateDispatch effect
/// applied), so a claim runs against real claimable state. Driving continues
/// while waiting: the effect lands only on a processor visit.
pub async fn until_dispatch(
    world: &Arc<RoundWorld>,
    processor: &Arc<bcs_dist_worker::WorkerProcessor>,
    did: &Did,
) {
    use bcs_service_api::port::repo::distributed::dispatch::DispatchControlPort;
    for _ in 0..200 {
        drive(world, processor, 1).await;
        if world
            .f
            .dispatch
            .get(did)
            .await
            .unwrap()
            .is_some_and(|record| matches!(record.view.execution.phase, DispatchPhase::Created))
        {
            return;
        }
    }
    panic!("dispatch {:?} never became durable", did);
}

/// Claims the live sibling attempt through the real connector-facing queue
/// and records its start send intent (with the durable T6 send proof), so
/// remote work may genuinely be in flight.
pub async fn claim_sibling(world: &Arc<RoundWorld>, did: &Did) {
    use bcs_service_api::port::repo::distributed::dispatch::*;
    let mut held = held(world, did).await;
    held.begin_send(
        SendOperation::Start {
            request_id: RequestId("sibling-start".into()),
        },
        SendProof {
            dedupe_domain: "fixture".into(),
            guarantee_deadline: TimestampMillis(1_000_000),
            operation: ProofOperation::Start {
                request_id: RequestId("sibling-start".into()),
            },
            body_digest: Digest("body".into()),
            config_digest: Digest("config".into()),
        },
    )
    .await
    .unwrap();
}

/// The durable stop fact of the sibling attempt, written through the real
/// holder path: the claimed attempt finishes with a definitive remote
/// cancellation outcome. Uncertainty never satisfies the barrier.
pub async fn confirm_attempt_stop(world: &Arc<RoundWorld>, did: &Did) {
    use bcs_service_api::port::repo::distributed::dispatch::{
        ClaimOutcome, DispatchQueuePort, DispatchControlPort, ResumeOwnedDispatch,
    };
    let record = world
        .f
        .dispatch
        .get(did)
        .await
        .unwrap()
        .expect("sibling dispatch record");
    let previous = record
        .view
        .execution
        .holder
        .clone()
        .expect("the sibling attempt must be held by its claimant");
    let outcome = world
        .f
        .queue
        .resume_owned(ResumeOwnedDispatch {
            did: did.clone(),
            previous,
            request_id: RequestId("stop-confirm".into()),
        })
        .await
        .unwrap();
    let ClaimOutcome::Acquired(mut held) = outcome else {
        panic!("sibling stop confirmation was not resumable");
    };
    held.finish(
        FinalDispatchOutcome::Canceled,
        "sibling stop confirmed".into(),
    )
    .await
    .unwrap();
}

async fn held(world: &Arc<RoundWorld>, did: &Did) -> Box<dyn HeldDispatchPort> {
    use bcs_service_api::port::repo::distributed::dispatch::*;
    match world
        .f
        .queue
        .claim(DispatchClaimRequest {
            did: did.clone(),
            me: Instance {
                node: "conn".into(),
                incarnation: "one".into(),
            },
            request_id: RequestId("sibling-probe".into()),
        })
        .await
        .unwrap()
    {
        ClaimOutcome::Acquired(held) => held,
        _ => panic!("dispatch was not claimable for its stop fact"),
    }
}
