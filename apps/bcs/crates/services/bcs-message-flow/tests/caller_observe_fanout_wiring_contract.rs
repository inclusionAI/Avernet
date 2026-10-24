//! Observe fan-out production wiring (spec §6.4, L225/L226, corrected
//! model): with the observe assembly attached, one ACCEPTED group dispatch
//! must (a) seed the shared observe index with the DISPATCHING CALLER'S OWN
//! container (Human×Bot×session, §6.9), and (b) sync the same projected
//! text observe-only to the OTHER known valid caller containers of the SAME
//! Bot in the SAME session — never to other Bots, never across sessions,
//! never to an unclassified or policy-denied stream (L225). The injector is
//! built once from the dispatch's OWN Provider target: all containers of
//! one Bot live behind the same webhook. Real handoff + real observe index;
//! gateway/identity/sender are the recording doubles proven by their own
//! real-chain contracts (§15.9/§15.5/§15.11) and the injector wire itself
//! is proven by §15.39.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_domain::{BotDeliveryTarget, RedactedToken, shared_stream::SharedContentKind};
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_dispatch_pipeline::{
    CallerDispatchOutcome, CallerDispatchPipeline, CommittedDispatchFacts, ObserveSyncAssembly,
};
use bcs_message_flow::observe_index::ObserveTargetIndex;
use bcs_service_api::application::caller_container::CallerContainerPreparation;
use bcs_service_api::application::caller_execution::{
    HumanCallerDeliveryHandoffService, QueuedAdmissionReceipt,
};
use bcs_service_api::port::{
    CallerContainerDescriptor, CallerContainerError, CallerContainerGatewayPort,
    CallerContainerIsolation, CallerDispatchAck, CallerExecutionSendPort, CallerObserveTarget, CallerOpaqueRef,
    EnsureCallerContainer, ObserveInjectCommand, ObserveInjectError,
    ObserveInjectPort, ObserveInjectorFactory, SendCallerExecutionCommand,
};
use bcs_caller_identity_api::{CallerIdentityError, CallerIdentityPlugin, EstablishCallerSubject, IssueCallerExecution, VerifyCallerExecution, EstablishedCallerSubject, VerifiedCallerExecution};

fn now_clock() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

fn ingress() -> bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress {
    bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress::new(
        bcs_service_api::application::principal::HumanActor {
            actor_id: "human_alice".into(),
            staff_no: "S12345".into(),
        },
        bcs_service_api::application::caller_identity::HumanCallerAuthSource::HttpAuth,
        now_clock(),
        now_clock() + 300_000,
        "test",
    )
    .unwrap()
}

/// Gateway double: returns a container ready with an observe_target for the
/// requested container's bot (the shape a group-capable Provider gives).
struct ObservingGateway;

#[async_trait]
impl CallerContainerGatewayPort for ObservingGateway {
    async fn ensure_container(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, CallerContainerError> {
        let target_bot_id = request.target_bot_id.clone();
        let session_id = request.session_id.clone();
        Ok(CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: target_bot_id.clone(),
            caller_subject_ref: request.caller_subject_ref,
            ready: true,
            isolation: CallerContainerIsolation::ContainerBound,
            binding_ref: None,
            runtime_generation: Some(1),
            expires_at_ms: now_clock() + 600_000,
            observe_target: Some(CallerObserveTarget {
                caller_subject: CallerOpaqueRef::new("fixture-subject"),
                caller_ref: Some(CallerOpaqueRef::new(format!("own-ref-{target_bot_id}"))),
                target_bot_id,
                target_epoch: Some(CallerOpaqueRef::new(format!("epoch-{session_id}"))),
                session_id,
                expires_at_ms: now_clock() + 600_000,
            }),
        })
    }
}

struct StubIdentity;
#[async_trait]
impl CallerIdentityPlugin for StubIdentity {
    async fn establish_subject(
        &self,
        input: EstablishCallerSubject,
    ) -> Result<EstablishedCallerSubject, CallerIdentityError> {
        // Subject establishment is owned by the selected identity plugin.
        // The observe
        // wiring stub passes the ingress window through — the pipeline
        // issues for the ESTABLISHED subject, never the raw display
        // identity.
        use bcs_service_api::application::caller_identity::{
            CallerSubjectKind, CallerSubjectRef, CallerSubjectRefType,
        };
        let deadline = input.ingress.expires_at_ms();
        let subject = CallerSubjectRef::new(
            CallerSubjectKind::Human,
            CallerSubjectRefType::BcsEphemeral,
            input.ingress.actor().actor_id.clone(),
            deadline,
        )
        .map_err(|_| CallerIdentityError::InvalidRequest)?;
        let proof = bcs_service_api::application::caller_identity::CallerExecutionToken::new(
            bcs_service_api::application::caller_identity::CallerProofFormat::Jwt,
            "subject-proof".to_string(),
            deadline,
        )
        .map_err(|_| CallerIdentityError::InvalidRequest)?;
        Ok(EstablishedCallerSubject {
            subject_ref: subject,
            subject_proof: proof,
            expires_at_ms: deadline,
        })
    }
    async fn issue_execution(
        &self,
        input: IssueCallerExecution<'_>,
    ) -> Result<bcs_service_api::application::caller_identity::CallerExecutionToken, CallerIdentityError> {
        bcs_service_api::application::caller_identity::CallerExecutionToken::new(
            bcs_service_api::application::caller_identity::CallerProofFormat::Jwt,
            "proof-token".into(),
            input.not_after_ms,
        )
        .map_err(|_| CallerIdentityError::InvalidRequest)
    }
    async fn verify_execution(
        &self,
        _input: VerifyCallerExecution,
    ) -> Result<VerifiedCallerExecution, CallerIdentityError> {
        Err(CallerIdentityError::TokenInvalid)
    }
}

struct AcceptingSender {
    calls: AtomicUsize,
}
#[async_trait]
impl CallerExecutionSendPort for AcceptingSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        command: SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(CallerDispatchAck::Accepted {
            control_revision: 0,
            request_digest: command.request_digest,
        })
    }
}

/// One recording inject port shared by every container of the Bot (the
/// production shape: one injector per Provider webhook).
struct RecordingInject {
    calls: Mutex<Vec<ObserveInjectCommand>>,
    fail: bool,
}
#[async_trait]
impl ObserveInjectPort for RecordingInject {
    async fn inject_shared_context(
        &self,
        _credential: &RedactedToken,
        command: ObserveInjectCommand,
    ) -> Result<(), ObserveInjectError> {
        if self.fail {
            return Err(ObserveInjectError::Unavailable { retryable: true });
        }
        self.calls.lock().unwrap().push(command);
        Ok(())
    }
}

struct StubFactory {
    port: Arc<RecordingInject>,
    no_transport: bool,
}

impl ObserveInjectorFactory for StubFactory {
    fn injector_for(&self, _target: &BotDeliveryTarget) -> Option<Arc<dyn ObserveInjectPort>> {
        if self.no_transport {
            None
        } else {
            Some(self.port.clone())
        }
    }
}

fn group_facts(kind: Option<SharedContentKind>) -> CommittedDispatchFacts {
    CommittedDispatchFacts {
        environment: "test".into(),
        target_bot_id: "bot-b".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-b".into(),
        session_id: "session-1".into(),
        group_id: Some("group-1".into()),
        message_turn_id: "turn-1".into(),
        root_run_id: "run-1".into(),
        run_id: "run-1".into(),
        attempt: 1,
        dispatch_id: "dispatch-1".into(),
        request_digest: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef".into(),
        delivery_commit: None,
        not_after_ms: now_clock() + 280_000,
        caller_task: "group chat text".into(),
        channel_actor_id: "human_alice".into(),
        channel_actor_name: "Alice".into(),
        control_revision: 0,
        history: None,
        shared_content_kind: kind,
    }
}

async fn seeded_handoff(session_id: &str) -> Arc<HumanCallerDeliveryHandoff> {
    seeded_handoff_for(session_id, Some("group-1".into())).await
}

async fn seeded_handoff_for(
    session_id: &str,
    group_id: Option<String>,
) -> Arc<HumanCallerDeliveryHandoff> {
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let scope = bcs_service_api::application::caller_execution::HandoffScope {
        env: "test".into(),
        session_id: session_id.into(),
        group_id,
        source_message_id: "turn-1".into(),
        target_bot_id: "bot-b".into(),
    };
    handoff.reserve(&scope, 30_000, now_clock()).await.unwrap();
    handoff
        .publish(
            &scope,
            ingress(),
            QueuedAdmissionReceipt::new("delivery-1", 1, now_clock()),
        )
        .await
        .unwrap();
    handoff
}

/// Pipeline with the observe assembly attached (index + injector factory).
async fn pipeline_with_observe(
    index: Arc<ObserveTargetIndex>,
    factory: Arc<StubFactory>,
    handoff: Arc<HumanCallerDeliveryHandoff>,
) -> Arc<CallerDispatchPipeline> {
    pipeline_with_root_lineage(index, factory, handoff, None).await
}

async fn pipeline_with_root_lineage(
    index: Arc<ObserveTargetIndex>,
    factory: Arc<StubFactory>,
    handoff: Arc<HumanCallerDeliveryHandoff>,
    root_lineage: Option<Arc<bcs_message_flow::caller_lineage::RootLineageIndex>>,
) -> Arc<CallerDispatchPipeline> {
    let preparation = Arc::new(CallerContainerPreparation::new(Arc::new(ObservingGateway)));
    let pipeline = CallerDispatchPipeline::new(
        handoff as Arc<dyn HumanCallerDeliveryHandoffService>,
        preparation,
        Arc::new(StubIdentity),
        Arc::new(AcceptingSender { calls: AtomicUsize::new(0) }) as Arc<dyn CallerExecutionSendPort>,
        3_600_000,
    )
    .with_observe_sync(ObserveSyncAssembly {
        groups: observe_groups().await, sessions: None, delivery_term: None,
        index,
        injector_factory: factory,
    });
    let pipeline = match root_lineage {
        Some(index) => pipeline.with_root_lineage(index),
        None => pipeline,
    };
    Arc::new(pipeline)
}

fn delivery_target() -> BotDeliveryTarget {
    BotDeliveryTarget::HttpProvider {
        bot_id: "bot-b".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-b".into(),
        webhook_url: "http://127.0.0.1:1/webhook".into(),
        bcs_to_provider_token: RedactedToken::new("svc"),
        protocol_version: "2.0".into(),
    }
}

/// Seed one caller container's observe reference directly (the shape a
/// previous dispatch of that Human produced — trusted ensure material).
fn seed_container(
    index: &ObserveTargetIndex,
    subject: &str,
    bot: &str,
    session: &str,
    epoch: &str,
) {
    index
        .replace_from_descriptor(
            subject,
            bot,
            session,
            &CallerContainerDescriptor {
                sandbox_container_id: None,
                target_bot_id: bot.to_string(),
                caller_subject_ref: CallerOpaqueRef::new("subject".to_string()),
                ready: true,
                isolation: CallerContainerIsolation::ContainerBound,
                binding_ref: None,
                runtime_generation: Some(1),
                expires_at_ms: now_clock() + 600_000,
                observe_target: Some(CallerObserveTarget {
                    caller_subject: CallerOpaqueRef::new("fixture-subject"),
                    caller_ref: Some(CallerOpaqueRef::new(format!("ref-{epoch}"))),
                    target_bot_id: bot.to_string(),
                    session_id: session.to_string(),
                    target_epoch: Some(CallerOpaqueRef::new(format!("epoch-{epoch}"))),
                    expires_at_ms: now_clock() + 600_000,
                }),
            },
            now_clock(),
        )
        .unwrap();
}

/// Fan-out runs detached: wait until the recording port observes the
/// expected activity (or prove its absence by timeout).
async fn until<F: Fn() -> bool>(condition: F) {
    let _ = tokio::time::timeout(std::time::Duration::from_secs(5), async {
        while !condition() {
            tokio::task::yield_now().await;
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }
    })
    .await;
}

async fn settle() {
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
}

#[tokio::test]
async fn accepted_dispatch_seeds_own_container_and_same_bot_containers_observe() {
    let handoff = seeded_handoff("session-1").await;
    let index = Arc::new(ObserveTargetIndex::new(8));
    // bob's container of bot-b exists (his own earlier ensure); carol's does
    // not — only known valid containers may observe.
    seed_container(&index, "human-bob", "bot-b", "session-1", "bob-g1");
    let inject = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false });
    let pipeline = pipeline_with_observe(
        index.clone(),
        Arc::new(StubFactory { port: inject.clone(), no_transport: false }),
        handoff,
    ).await;

    let outcome = pipeline
        .dispatch_once(
            &delivery_target(),
            &RedactedToken::new("svc"),
            group_facts(Some(SharedContentKind::OpenChat)),
            now_clock(),
        )
        .await
        .expect("accepted");
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));

    // Seeding: the dispatching caller's OWN container is live in the index.
    until(|| index.current("S12345", "bot-b", "session-1", now_clock()).is_some()).await;
    assert!(
        index.current("S12345", "bot-b", "session-1", now_clock()).is_some(),
        "the ensure readback seeds the dispatching caller's own container"
    );

    // Fan-out: exactly bob's container observed the same projected text,
    // under his OWN live generation; carol (no container) observed nothing.
    until(|| inject.calls.lock().unwrap().len() == 1).await;
    let calls = inject.calls.lock().unwrap();
    assert_eq!(calls.len(), 1, "one same-bot container is live: exactly one inject");
    assert_eq!(calls[0].message_id, "turn-1", "dedup id is the canonical turn");
    assert_eq!(calls[0].text, "group chat text");
    assert_eq!(calls[0].target_bot_id, "bot-b");
    assert_eq!(calls[0].session_id, "session-1");
    assert_eq!(calls[0].runtime_target_epoch.as_ref().unwrap().expose(), "epoch-bob-g1");
}

#[tokio::test]
async fn other_bots_and_other_sessions_are_never_auto_copied() {
    let handoff = seeded_handoff("session-1").await;
    let index = Arc::new(ObserveTargetIndex::new(8));
    // bob also has a container with ANOTHER Bot, and with the same Bot in
    // ANOTHER session: neither may observe alice's bot-b/session-1 dispatch.
    seed_container(&index, "human-bob", "bot-b", "session-1", "same");
    seed_container(&index, "human-bob", "bot-c", "session-1", "other-bot");
    seed_container(&index, "human-bob", "bot-b", "session-2", "other-session");
    let inject = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false });
    let pipeline = pipeline_with_observe(
        index.clone(),
        Arc::new(StubFactory { port: inject.clone(), no_transport: false }),
        handoff,
    ).await;
    pipeline
        .dispatch_once(
            &delivery_target(),
            &RedactedToken::new("svc"),
            group_facts(Some(SharedContentKind::OpenChat)),
            now_clock(),
        )
        .await
        .unwrap();
    until(|| inject.calls.lock().unwrap().len() == 1).await;
    settle().await;
    let calls = inject.calls.lock().unwrap();
    assert_eq!(calls.len(), 1, "only the same Bot, same session container");
    assert_eq!(calls[0].runtime_target_epoch.as_ref().unwrap().expose(), "epoch-same");
}

#[tokio::test]
async fn policy_and_classification_gate_before_any_fanout() {
    // (a) A directed task addressed to ANOTHER Bot never rides bot-b's
    // stream: L225 denies the whole fan-out.
    let handoff = seeded_handoff("session-1").await;
    let index = Arc::new(ObserveTargetIndex::new(8));
    seed_container(&index, "human-bob", "bot-b", "session-1", "b1");
    let inject = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false });
    let pipeline = pipeline_with_observe(
        index.clone(),
        Arc::new(StubFactory { port: inject.clone(), no_transport: false }),
        handoff,
    ).await;
    let directed = group_facts(Some(SharedContentKind::DirectedTask {
        addressed_bot_ids: vec!["bot-z".into()],
    }));
    pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), directed, now_clock())
        .await
        .unwrap();
    settle().await;
    assert!(
        inject.calls.lock().unwrap().is_empty(),
        "content denied on this Bot's shared stream never fans out"
    );

    // (b) Unclassified content never broadcasts even with live containers.
    let handoff = seeded_handoff("session-1").await;
    let index2 = Arc::new(ObserveTargetIndex::new(8));
    seed_container(&index2, "human-bob", "bot-b", "session-1", "b1");
    let inject2 = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false });
    let pipeline2 = pipeline_with_observe(
        index2.clone(),
        Arc::new(StubFactory { port: inject2.clone(), no_transport: false }),
        handoff,
    ).await;
    pipeline2
        .dispatch_once(
            &delivery_target(),
            &RedactedToken::new("svc"),
            group_facts(None),
            now_clock(),
        )
        .await
        .unwrap();
    settle().await;
    assert!(
        inject2.calls.lock().unwrap().is_empty(),
        "unclassified content never broadcasts"
    );

    // (c) A directed task ADDRESSED to this Bot rides its own stream: the
    // other Human's container may observe it.
    let handoff = seeded_handoff("session-1").await;
    let index3 = Arc::new(ObserveTargetIndex::new(8));
    seed_container(&index3, "human-bob", "bot-b", "session-1", "b1");
    let inject3 = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false });
    let pipeline3 = pipeline_with_observe(
        index3.clone(),
        Arc::new(StubFactory { port: inject3.clone(), no_transport: false }),
        handoff,
    ).await;
    let directed_here = group_facts(Some(SharedContentKind::DirectedTask {
        addressed_bot_ids: vec!["bot-b".into()],
    }));
    pipeline3
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), directed_here, now_clock())
        .await
        .unwrap();
    until(|| !inject3.calls.lock().unwrap().is_empty()).await;
}

#[tokio::test]
async fn a_failing_inject_isolates_and_never_touches_the_outcome() {
    let handoff = seeded_handoff("session-1").await;
    let index = Arc::new(ObserveTargetIndex::new(8));
    seed_container(&index, "human-bob", "bot-b", "session-1", "b1");
    let inject = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: true });
    let pipeline = pipeline_with_observe(
        index.clone(),
        Arc::new(StubFactory { port: inject.clone(), no_transport: false }),
        handoff,
    ).await;
    let outcome = pipeline
        .dispatch_once(
            &delivery_target(),
            &RedactedToken::new("svc"),
            group_facts(Some(SharedContentKind::OpenChat)),
            now_clock(),
        )
        .await
        .unwrap();
    assert!(
        matches!(outcome, CallerDispatchOutcome::Accepted),
        "a failed observe inject never re-judges the accepted dispatch"
    );

    // No transport shape pauses the fan-out the same way: still Accepted.
    let handoff = seeded_handoff("session-1").await;
    let index2 = Arc::new(ObserveTargetIndex::new(8));
    seed_container(&index2, "human-bob", "bot-b", "session-1", "b1");
    let inject2 = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false });
    let pipeline2 = pipeline_with_observe(
        index2.clone(),
        Arc::new(StubFactory { port: inject2, no_transport: true }),
        handoff,
    ).await;
    let outcome = pipeline2
        .dispatch_once(
            &delivery_target(),
            &RedactedToken::new("svc"),
            group_facts(Some(SharedContentKind::OpenChat)),
            now_clock(),
        )
        .await
        .unwrap();
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
}

#[tokio::test]
async fn an_accepted_group_dispatch_registers_the_root_lineage_window() {
    // L243-A (spec §8.3): the ACCEPTED root publishes its delegation-only
    // lineage — root run, canonical turn, group/session boundary and the
    // cutoff ceiling,窗口 capped by the effective identity deadline. A
    // DM-shaped dispatch (no group scope) never registers.
    let handoff = seeded_handoff("session-1").await;
    let observe_index = Arc::new(ObserveTargetIndex::new(8));
    let root_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    let pipeline = pipeline_with_root_lineage(
        observe_index,
        Arc::new(StubFactory {
            port: Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false }),
            no_transport: false,
        }),
        handoff,
        Some(root_index.clone()),
    ).await;
    pipeline
        .dispatch_once(
            &delivery_target(),
            &RedactedToken::new("svc"),
            group_facts(Some(SharedContentKind::OpenChat)),
            now_clock(),
        )
        .await
        .unwrap();
    until(|| root_index.current("run-1", now_clock()).is_some()).await;
    let lineage = root_index.current("run-1", now_clock()).expect("lineage registered");
    assert_eq!(lineage.root_run_id, "run-1");
    assert_eq!(lineage.message_turn_id, "turn-1");
    assert_eq!(lineage.group_id, "group-1");
    assert_eq!(lineage.session_id, "session-1");
    assert!(lineage.expires_at_ms > now_clock(), "the identity window bounds derivability");

    // A DM-shaped dispatch (no group) never registers — in-group
    // derivations need the boundary.
    let dm_handoff = seeded_handoff_for("session-1", None).await;
    let dm_observe = Arc::new(ObserveTargetIndex::new(8));
    let dm_root = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    let dm_pipeline = pipeline_with_root_lineage(
        dm_observe,
        Arc::new(StubFactory {
            port: Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false }),
            no_transport: false,
        }),
        dm_handoff,
        Some(dm_root.clone()),
    ).await;
    let mut dm = group_facts(Some(SharedContentKind::OpenChat));
    dm.group_id = None;
    dm_pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), dm, now_clock())
        .await
        .unwrap();
    settle().await;
    assert!(
        dm_root.len() == 0 && dm_root.current("run-1", now_clock()).is_none(),
        "DM dispatches never publish derivable lineage"
    );
}

#[path = "support/caller_observe_reply_case.rs"]
mod reply_case;

async fn delegate_sync_case(kind: Option<SharedContentKind>, ack: CallerDispatchAck, expected: bool) {
    use bcs_message_flow::caller_dispatch_pipeline::DelegateDispatchFacts;
    use bcs_message_flow::caller_lineage::{RootLineage, RootLineageIndex};
    let index = Arc::new(ObserveTargetIndex::new(8));
    seed_container(&index, "human_bob", "bot-b", "session-1", "same");
    seed_container(&index, "human_bob", "bot-c", "session-1", "other-bot");
    seed_container(&index, "human_bob", "bot-b", "session-2", "other-session");
    let root = Arc::new(RootLineageIndex::new(8));
    assert!(root.register("parent-run", RootLineage {
        root_run_id: "root-run".into(), delegation_depth: 0,
        attempt: 1,
        environment: "test".into(),
        message_turn_id: "root-turn".into(), group_id: "group-1".into(),
        session_id: "session-1".into(), history_cutoff_seq: 0,
        identity_subject: "opaque-test-subject".into(),
        identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
        expires_at_ms: now_clock() + 280_000, delegate_subject: "human_alice".into(),
            parent_execution: false,
    }, now_clock()));
    let inject = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false });
    struct FixedSender(CallerDispatchAck);
    #[async_trait]
    impl CallerExecutionSendPort for FixedSender {
        async fn send_caller_execution(&self, _: &BotDeliveryTarget, _: &RedactedToken, _: SendCallerExecutionCommand)
            -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
            Ok(self.0.clone())
        }
    }
    let accepted = matches!(ack, CallerDispatchAck::Accepted { .. });
    let pipeline = CallerDispatchPipeline::new(
        seeded_handoff("session-1").await,
        Arc::new(CallerContainerPreparation::new(Arc::new(ObservingGateway))),
        Arc::new(StubIdentity), Arc::new(FixedSender(ack)), 3_600_000,
    ).with_observe_sync(ObserveSyncAssembly {
        groups: observe_groups().await, sessions: None, delivery_term: None,
        index, injector_factory: Arc::new(StubFactory { port: inject.clone(), no_transport: false }),
    }).with_root_lineage(root);
    let facts = DelegateDispatchFacts {
        environment: "test".into(), target_bot_id: "bot-b".into(),
        provider_id: "provider-a".into(), provider_bot_ref: "runtime-b".into(),
        session_id: "session-1".into(), group_id: Some("group-1".into()),
        message_turn_id: "child-message".into(), run_id: "child-run".into(),
        parent_run_id: "parent-run".into(), parent_attempt: 1,
        parent_bot_id: "bot-a".into(), attempt: 1, dispatch_id: "child-dispatch".into(),
        request_digest: group_facts(None).request_digest,
        not_after_ms: now_clock() + 280_000, caller_task: "child public input".into(),
        channel_actor_id: "human_alice".into(), channel_actor_name: "Alice".into(),
        control_revision: 0, delivery_commit: None, history: None, history_cutoff_seq: 0,
        shared_content_kind: kind,
        purpose: bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution,
    };
    let outcome = pipeline.dispatch_delegate_once(&delivery_target(), &RedactedToken::new("svc"), facts, now_clock()).await.unwrap();
    assert_eq!(matches!(outcome, CallerDispatchOutcome::Accepted), accepted);
    if expected { until(|| !inject.calls.lock().unwrap().is_empty()).await; }
    settle().await;
    let calls = inject.calls.lock().unwrap();
    if !expected {
        assert!(calls.is_empty(), "unaccepted or non-shared input never fans out");
        return;
    }
    assert_eq!(calls.len(), 1, "same Bot/session only; executing caller excluded");
    assert_eq!(calls[0].message_id, "child-message", "never dedup by root turn");
    assert_eq!(calls[0].text, "child public input");
    assert_eq!(calls[0].runtime_target_epoch.as_ref().unwrap().expose(), "epoch-same");
}

#[tokio::test]
async fn accepted_group_delegate_syncs_its_own_message_to_same_bot_observers() {
    delegate_sync_case(Some(SharedContentKind::OpenChat), CallerDispatchAck::Accepted {
        control_revision: 0, request_digest: group_facts(None).request_digest,
    }, true).await;
}

#[tokio::test]
async fn delegate_unaccepted_or_nonshared_inputs_never_broadcast() {
    for kind in [None, Some(SharedContentKind::PrivateMcpContext), Some(SharedContentKind::DirectedTask {
        addressed_bot_ids: vec!["other-bot".into()],
    })] {
        delegate_sync_case(kind, CallerDispatchAck::Accepted {
            control_revision: 0, request_digest: group_facts(None).request_digest,
        }, false).await;
    }
    for ack in [CallerDispatchAck::Unknown, CallerDispatchAck::Rejected {
        error_code: "NOT_READY".into(), retryable: false,
    }] {
        delegate_sync_case(Some(SharedContentKind::OpenChat), ack, false).await;
    }
}

#[tokio::test]
async fn accepted_input_does_not_sync_to_a_departed_human() {
    let index = Arc::new(ObserveTargetIndex::new(8));
    seed_container(&index, "departed", "bot-b", "session-1", "left");
    let inject = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false });
    let pipeline = pipeline_with_observe(index.clone(),
        Arc::new(StubFactory { port: inject.clone(), no_transport: false }),
        seeded_handoff("session-1").await).await;
    let outcome = pipeline.dispatch_once(&delivery_target(), &RedactedToken::new("svc"),
        group_facts(Some(SharedContentKind::OpenChat)), now_clock()).await.unwrap();
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
    settle().await;
    assert!(inject.calls.lock().unwrap().is_empty(), "departed Human must not receive accepted input");
}

async fn observe_groups() -> Arc<dyn bcs_service_api::GroupCoreService> {
    use bcs_service_api::{GroupCoreService, Group, Participant, ParticipantRole};
    let groups = Arc::new(reply_case::support::FakeGroupCoreService::default());
    let mut participants = vec![Participant::bot("bot-b", ParticipantRole::Driver)];
    for subject in ["S12345", "human-bob", "human_bob", "human-carol", "human_alice"] {
        participants.push(Participant::human(format!("human_{subject}"), ParticipantRole::Observer));
    }
    groups.upsert(Group::new("group-1", "bot-b", participants)).await.unwrap();
    groups
}

struct StopAfterFirstInject {
    calls: AtomicUsize,
    term: Arc<std::sync::atomic::AtomicU64>,
    index: Arc<ObserveTargetIndex>,
    invalidate: bool,
}
#[async_trait]
impl ObserveInjectPort for StopAfterFirstInject {
    async fn inject_shared_context(&self, _: &RedactedToken, _: ObserveInjectCommand) -> Result<(), ObserveInjectError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if self.invalidate {
            self.index.invalidate_bot_in_session("bot-b", "session-1");
        } else {
            self.term.fetch_add(1, Ordering::SeqCst);
        }
        Ok(())
    }
}
struct GuardTestFactory(Arc<StopAfterFirstInject>);
impl ObserveInjectorFactory for GuardTestFactory {
    fn injector_for(&self, _: &BotDeliveryTarget) -> Option<Arc<dyn ObserveInjectPort>> { Some(self.0.clone()) }
}

#[tokio::test]
async fn a_term_change_or_invalidation_between_targets_stops_the_next_inject() {
    for invalidate in [false, true] {
        let term = Arc::new(std::sync::atomic::AtomicU64::new(1));
        let index = Arc::new(ObserveTargetIndex::new(8));
        seed_container(&index, "human-bob", "bot-b", "session-1", "bob");
        seed_container(&index, "human-carol", "bot-b", "session-1", "carol");
        let port = Arc::new(StopAfterFirstInject { calls: AtomicUsize::new(0), term: term.clone(), index: index.clone(), invalidate });
        let pipeline = CallerDispatchPipeline::new(seeded_handoff("session-1").await,
            Arc::new(CallerContainerPreparation::new(Arc::new(ObservingGateway))), Arc::new(StubIdentity),
            Arc::new(AcceptingSender { calls: AtomicUsize::new(0) }), 3_600_000)
            .with_delivery_term(term.clone())
            .with_observe_sync(ObserveSyncAssembly {
                groups: observe_groups().await, sessions: None, delivery_term: Some(term),
                index, injector_factory: Arc::new(GuardTestFactory(port.clone())),
            });
        pipeline.dispatch_once(&delivery_target(), &RedactedToken::new("svc"),
            group_facts(Some(SharedContentKind::OpenChat)), now_clock()).await.unwrap();
        until(|| port.calls.load(Ordering::SeqCst) > 0).await;
        settle().await;
        assert_eq!(port.calls.load(Ordering::SeqCst), 1, "a previously copied target list is not authority to send");
    }
}
