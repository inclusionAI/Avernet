//! Runtime-level protected caller dispatch contract (§7.5.1, P2-B fifth
//! slice). Real Managed admission, real DeliveryRuntime, real handoff; the
//! preparation/sender collaborators are recording stubs whose own real-chain
//! contracts are proven in §15.9–§15.12. Locks: outcome convergence through
//! the ordinary lifecycle events, guard preservation when the pipeline or
//! the committed wire facts are absent, and the pre-network identity-loss
//! DefinitelyNotSent semantics.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{BotDeliveryTarget, DeliveryType, NewMessage, RedactedToken, SenderType};
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_dispatch_pipeline::CallerDispatchPipeline;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::delivery_runtime::{
    DeliveryRuntime, DeliveryRuntimeConfig, DeliveryRuntimePolicy,
};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_container::CallerContainerPreparation;
use bcs_service_api::application::caller_execution::{HumanCallerDeliveryHandoffService, QueuedAdmissionReceipt};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, CallerExecutionToken, CallerProofFormat, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::port::{
    CallerContainerDescriptor, CallerContainerGatewayPort, CallerDispatchAck,
    CallerExecutionSendPort, EnsureCallerContainer,
};
use bcs_caller_identity_api::{CallerIdentityPlugin};
use bcs_service_api::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand,
    BotDeliveryKind, BotDeliveryPort, BotDeliveryResult, ManagedMessageDeliveryService,
    ServiceResult,
};
use serde_json::{Value, json};

const DIGEST: &str = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

/// Recording transport: the protected path must never hand the ordinary
/// transport a caller row.
#[derive(Default)]
struct RecordingIo {
    sent: tokio::sync::Mutex<Vec<String>>,
    prepared: std::sync::atomic::AtomicUsize,
    /// When set, `resolve_caller_dispatch` refuses — the fail-closed
    /// target-resolution guard (review P1-5) without a Provider container.
    refuse_caller_target: bool,
    hang_caller_target: bool,
}

#[async_trait]
impl BotDeliveryPort for RecordingIo {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.sent
            .lock()
            .await
            .push(cmd.target_bot_id().to_string());
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: true,
            error: None,
        })
    }
    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        Ok(BotAbortDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            aborted_run_ids: Vec::new(),
        })
    }
}

#[async_trait]
impl bcs_service_api::ManagedDeliveryPreparationService for RecordingIo {
    async fn is_available(&self, _bot_id: &str) -> bool {
        true
    }
    /// Real dispatch context (review P1-5): production resolves the row's
    /// Provider scope from the registry; the recording harness pins the
    /// same shape so the runtime exercises the real resolution branch.
    async fn resolve_caller_dispatch(
        &self,
        row: &PersistedMessageDelivery,
    ) -> Option<bcs_service_api::CallerDispatchContext> {
        if self.hang_caller_target {
            std::future::pending::<()>().await;
        }
        if self.refuse_caller_target {
            return None;
        }
        Some(bcs_service_api::CallerDispatchContext {
            target: BotDeliveryTarget::HttpProvider {
                bot_id: row.target_bot_id.clone(),
                provider_id: "provider-runtime".into(),
                provider_bot_ref: format!("ref-{}", row.target_bot_id),
                webhook_url: "http://127.0.0.1:1/webhook".into(),
                bcs_to_provider_token: RedactedToken::new("provider-svc-token"),
                protocol_version: "2.0".into(),
            },
            service_credential: RedactedToken::new("provider-svc-token"),
            provider_id: "provider-runtime".into(),
            provider_bot_ref: format!("ref-{}", row.target_bot_id),
        })
    }
    async fn prepare(
        &self,
        _row: &PersistedMessageDelivery,
    ) -> ServiceResult<bcs_service_api::PreparedManagedDelivery> {
        self.prepared
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let frame = bcs_protocol::BcsFrame::Request(bcs_protocol::RequestFrame::new(
            "frame", "chat.send", None,
        ));
        Ok(bcs_service_api::PreparedManagedDelivery {
            command: BotDeliveryCommand {
                target: BotDeliveryTarget::WebSocket { bot_id: "bot".into() },
                run_id: "run".into(),
                frame,
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: Default::default(),
                provider_bypass_headers: Vec::new(),
            },
            transport_context_json: json!({"provider_route_headers": []}),
        })
    }
    async fn prepare_abort(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<BotAbortDeliveryCommand> {
        Ok(BotAbortDeliveryCommand {
            target: BotDeliveryTarget::WebSocket { bot_id: row.target_bot_id.clone() },
            command_id: format!("abort-{}", row.delivery_id),
            group_id: row.group_id.clone(),
            session_id: row.session_id.clone(),
            run_id: row.run_id.clone(),
            provider_bypass_headers: Vec::new(),
            timeout_ms: 1000,
        })
    }
    async fn before_send(
        &self,
        _started: &PersistedMessageDelivery,
        _cmd: &BotDeliveryCommand,
    ) -> ServiceResult<()> {
        Ok(())
    }
}

/// Gateway stub: accepts and returns a container with a far deadline.
struct StubGateway;

#[async_trait]
impl CallerContainerGatewayPort for StubGateway {
    async fn ensure_container(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, bcs_service_api::port::CallerContainerError> {
        Ok(CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: request.target_bot_id,
            caller_subject_ref: request.caller_subject_ref,
            ready: true,
            isolation: bcs_service_api::port::CallerContainerIsolation::ContainerBound,
            binding_ref: None,
            runtime_generation: None,
            expires_at_ms: now_ms() + 600_000,
            observe_target: None,
        })
    }
}

struct StubIdentity;

#[async_trait]
impl CallerIdentityPlugin for StubIdentity {
    async fn establish_subject(
        &self,
        input: bcs_caller_identity_api::EstablishCallerSubject,
    ) -> Result<bcs_caller_identity_api::EstablishedCallerSubject, bcs_caller_identity_api::CallerIdentityError> {
        // Subject establishment is owned by the selected identity plugin.
        // The runtime stub
        // passes the ingress window through — the pipeline issues for the
        // ESTABLISHED subject, never the raw display identity.
        let deadline = input.ingress.expires_at_ms();
        let subject = bcs_service_api::application::caller_identity::CallerSubjectRef::new(
            bcs_service_api::application::caller_identity::CallerSubjectKind::Human,
            bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            input.ingress.actor().actor_id.clone(),
            deadline,
        )
        .map_err(|_| bcs_caller_identity_api::CallerIdentityError::InvalidRequest)?;
        let proof = CallerExecutionToken::new(CallerProofFormat::Jwt, "subject-proof".to_string(), deadline)
            .map_err(|_| bcs_caller_identity_api::CallerIdentityError::InvalidRequest)?;
        Ok(bcs_caller_identity_api::EstablishedCallerSubject {
            subject_ref: subject,
            subject_proof: proof,
            expires_at_ms: deadline,
        })
    }
    async fn issue_execution(
        &self,
        input: bcs_caller_identity_api::IssueCallerExecution<'_>,
    ) -> Result<CallerExecutionToken, bcs_caller_identity_api::CallerIdentityError> {
        CallerExecutionToken::new(
            CallerProofFormat::Jwt,
            "runtime-proof".into(),
            input.not_after_ms,
        )
        .map_err(|_| bcs_caller_identity_api::CallerIdentityError::InvalidRequest)
    }
    async fn verify_execution(
        &self,
        _input: bcs_caller_identity_api::VerifyCallerExecution,
    ) -> Result<bcs_caller_identity_api::VerifiedCallerExecution, bcs_caller_identity_api::CallerIdentityError> {
        Err(bcs_caller_identity_api::CallerIdentityError::TokenInvalid)
    }
}

/// Recording protected sender: enumerates the ack under test.
struct StubSender {
    ack: CallerDispatchAck,
    error: Option<bcs_service_api::port::CallerSendError>,
    histories: std::sync::Mutex<Vec<Option<bcs_service_api::port::CallerExecutionHistory>>>,
}

#[async_trait]
impl CallerExecutionSendPort for StubSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        command: bcs_service_api::port::SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        self.histories
            .lock()
            .unwrap()
            .push(command.history.clone());
        if let Some(error) = &self.error {
            return Err(error.clone());
        }
        Ok(self.ack.clone())
    }
}

fn caller_pipeline(handoff: Arc<HumanCallerDeliveryHandoff>, ack: CallerDispatchAck) -> Arc<CallerDispatchPipeline> {
    caller_pipeline_with_error(handoff, ack, None)
}

fn caller_pipeline_with_error(
    handoff: Arc<HumanCallerDeliveryHandoff>,
    ack: CallerDispatchAck,
    error: Option<bcs_service_api::port::CallerSendError>,
) -> Arc<CallerDispatchPipeline> {
    let preparation = Arc::new(CallerContainerPreparation::new(Arc::new(StubGateway)));
    Arc::new(CallerDispatchPipeline::new(
        handoff as Arc<dyn HumanCallerDeliveryHandoffService>,
        preparation,
        Arc::new(StubIdentity),
        Arc::new(StubSender { ack, error, histories: std::sync::Mutex::new(Vec::new()) }),
        600_000,
    ))
}

fn admission(id: &str, projection: Value) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries {
        display_message: None,
        message_id: id.into(),
        flow_kind: DeliveryFlowKind::Group,
        now_ms: 1,
        expire_at_ms: None,
        event: None,
        message: NewMessage {
            visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
            audience: None,
            group_id: "group".into(),
            session_id: format!("session-{id}"),
            sender_id: "human".into(),
            sender_type: SenderType::Human,
            message_type: "chat".into(),
            content: json!({"text": id}),
            client_msg_id: Some(id.into()),
            owner_bot_id: None,
            created_at: 1,
            run_id: String::new(),
        },
        targets: vec![DeliveryAdmissionTarget {
            rejection: None,
            target_bot_id: "bot".into(),
            kind: DeliveryType::Send,
            max_queued: 10,
            semantic_projection_json: projection,
        }],
    }
}

fn builder_projection(digest: bool, deadline: bool, task: bool) -> Value {
    let mut projection = json!({"version": 1, "caller_required": true});
    if digest {
        projection["caller_request_digest"] = json!(DIGEST);
    }
    if deadline {
        projection["caller_not_after_ms"] = json!(now_ms() + 300_000);
    }
    if task {
        projection["caller_task"] = json!("runtime caller task");
        // Trusted ordinary sender facts the admission seam commits — the
        // envelope `from` zone's source (never the protected subject).
        projection["caller_channel"] = json!({
            "actor_id": "human_alice",
            "actor_name": "Alice",
        });
    }
    projection
}

fn runtime(service: Arc<ManagedMessageDelivery>, io: Arc<RecordingIo>, pipeline: Option<Arc<CallerDispatchPipeline>>) -> DeliveryRuntime {
    DeliveryRuntime {
        policy: None,
        service,
        preparation: io.clone(),
        transport: io,
        config: DeliveryRuntimeConfig {
            max_safe_retries: 0,
            pause_dispatch: false,
            bots: std::collections::BTreeMap::from([(
                "bot".into(),
                DeliveryRuntimePolicy { max_running: 2, min_send_interval_ms: 0 },
            )]),
            tick: Duration::from_millis(5),
            expiry_tick: Duration::from_millis(50),
            io_timeout: Duration::from_secs(1),
            run_timeout: Duration::from_secs(30),
            cancel_timeout: Duration::from_secs(1),
            startup_recovery_grace: Duration::from_secs(2),
            max_tasks: 2,
            max_abort_tasks: 1,
        },
        caller_dispatch: pipeline,
    }
}

async fn seeded_handoff(message_id: &str) -> Arc<HumanCallerDeliveryHandoff> {
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    // P3.4: the group scope routes the canonical turn lineage; the seeded
    // harness mirrors the production seam (group_id = admission's group).
    let scope = bcs_service_api::application::caller_execution::HandoffScope {
        env: "local".to_string(),
        session_id: format!("session-{message_id}"),
        group_id: Some("group".to_string()),
        source_message_id: message_id.into(),
        target_bot_id: "bot".into(),
    };
    handoff.reserve(&scope, 30_000, now_ms()).await.unwrap();
    handoff
        .publish(
            &scope,
            AuthenticatedHumanCallerIngress::new(
                HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
                HumanCallerAuthSource::HttpAuth,
                now_ms(),
                now_ms() + 300_000,
                "local".to_string(),
            )
            .unwrap(),
            QueuedAdmissionReceipt::new("delivery-1", 1, now_ms()),
        )
        .await
        .unwrap();
    handoff
}

async fn run_until_status(
    service: &ManagedMessageDelivery,
    source: &str,
    predicate: impl Fn(&PersistedMessageDelivery) -> bool,
) -> PersistedMessageDelivery {
    for _ in 0..4000 {
        for row in service.snapshot(None).await.unwrap() {
            if row.source_message_id == source && predicate(&row) {
                return row.clone();
            }
        }
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
    for row in service.snapshot(None).await.unwrap() {
        if row.source_message_id == source {
            panic!(
                "row {source} never reached the expected state; last status={:?} attempt={} may_have_been_sent={}",
                row.state.status, row.attempt_no, row.state.may_have_been_sent
            );
        }
    }
    panic!("row {source} disappeared");
}

async fn fixture(ack: CallerDispatchAck) -> (DeliveryRuntime, Arc<RecordingIo>, Arc<ManagedMessageDelivery>, Arc<HumanCallerDeliveryHandoff>) {
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo));
    let io = Arc::new(RecordingIo::default());
    let handoff = seeded_handoff("caller-ok").await;
    let pipeline = caller_pipeline(handoff.clone(), ack);
    (runtime(service.clone(), io.clone(), Some(pipeline)), io, service, handoff)
}

fn start(worker: DeliveryRuntime) -> (tokio::sync::watch::Sender<bool>, tokio::task::JoinHandle<()>) {
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let w = worker;
    let task: tokio::task::JoinHandle<()> = tokio::spawn(async move {
        let _ = w.run(shutdown).await;
    });
    (stop, task)
}

async fn worker_stop(stop: tokio::sync::watch::Sender<bool>, task: tokio::task::JoinHandle<()>) {
    stop.send(true).unwrap();
    let _ = tokio::time::timeout(Duration::from_secs(2), task).await;
}

#[tokio::test]
async fn accepted_ack_converges_submitted_without_touching_the_transport() {
    let (worker, io, service, _handoff) =
        fixture(CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() }).await;
    service
        .admit(admission("caller-ok", builder_projection(true, true, true)))
        .await
        .expect("admit");

    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| {
        matches!(row.state.status, Status::Dispatching | Status::Running | Status::Failed | Status::Unknown)
    })
    .await;
    worker_stop(stop, task).await;
    assert_eq!(row.state.status, Status::Dispatching, "Submitted keeps the interactive status");
    // The ordinary transport never sees a caller row.
    assert!(io.sent.lock().await.is_empty(), "protected path must not send via the ordinary transport");
    assert_eq!(io.prepared.load(std::sync::atomic::Ordering::SeqCst), 0, "no ordinary prepare either");
}

#[tokio::test]
async fn rejected_ack_converges_failed() {
    let (worker, _io, service, _handoff) =
        fixture(CallerDispatchAck::Rejected { error_code: "CANCELLED".into(), retryable: false }).await;
    service
        .admit(admission("caller-ok", builder_projection(true, true, true)))
        .await
        .expect("admit");
    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Failed).await;
    worker_stop(stop, task).await;
    assert_eq!(row.state.status, Status::Failed);
    assert!(row.state.may_have_been_sent, "Rejected keeps the conservative sent marker per wire ACK honesty");
}

#[tokio::test]
async fn unknown_ack_converges_unknown() {
    let (worker, _io, service, _handoff) = fixture(CallerDispatchAck::Unknown).await;
    service
        .admit(admission("caller-ok", builder_projection(true, true, true)))
        .await
        .expect("admit");
    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Unknown).await;
    worker_stop(stop, task).await;
    assert_eq!(row.state.status, Status::Unknown);
    assert!(row.state.may_have_been_sent);
}

#[tokio::test]
async fn definite_non_ack_send_error_converges_failed_and_releases_the_lane() {
    // Wire doc §3.1/§3.2 (the 400 freezing bug): a non-retryable gateway
    // non-2xx is deterministic never-accepted evidence — the row must
    // converge Failed and clear the maybe-sent marker so the lane releases,
    // instead of parking Unknown waiting for a terminal receipt that the
    // never-accepted dispatch can never produce.
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo));
    let io = Arc::new(RecordingIo::default());
    let handoff = seeded_handoff("caller-ok").await;
    let pipeline = caller_pipeline_with_error(
        handoff.clone(),
        CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
        Some(bcs_service_api::port::CallerSendError::GatewayFailed { retryable: false }),
    );
    let worker = runtime(service.clone(), io.clone(), Some(pipeline));
    service
        .admit(admission("caller-ok", builder_projection(true, true, true)))
        .await
        .expect("admit");
    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Failed).await;
    worker_stop(stop, task).await;
    assert_eq!(row.attempt_no, 1);
    assert!(
        !row.state.may_have_been_sent,
        "a 4xx non-ack is provable non-acceptance; the row must not keep the conservative sent marker"
    );
    // The lane (and a follow-up message behind it) is no longer blocked.
    assert!(!service.lane_blocked(&row).await.unwrap(), "failed row must release the lane");
}

#[tokio::test]
async fn retryable_send_error_stays_conservatively_unknown() {
    // A 5xx may still have reached acceptance through an intermediary: the
    // row keeps the uncertain Unknown status with its conservative marker.
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo));
    let io = Arc::new(RecordingIo::default());
    let handoff = seeded_handoff("caller-ok").await;
    let pipeline = caller_pipeline_with_error(
        handoff.clone(),
        CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
        Some(bcs_service_api::port::CallerSendError::GatewayFailed { retryable: true }),
    );
    let worker = runtime(service.clone(), io.clone(), Some(pipeline));
    service
        .admit(admission("caller-ok", builder_projection(true, true, true)))
        .await
        .expect("admit");
    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Unknown).await;
    worker_stop(stop, task).await;
    assert!(row.state.may_have_been_sent, "5xx keeps the conservative maybe-sent marker");
}

#[tokio::test]
async fn no_pipeline_keeps_the_closed_guard() {
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo));
    let io = Arc::new(RecordingIo::default());
    let worker = runtime(service.clone(), io.clone(), None);
    service
        .admit(admission("caller-ok", builder_projection(true, true, true)))
        .await
        .expect("admit");
    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Failed).await;
    worker_stop(stop, task).await;
    assert_eq!(row.state.status, Status::Failed, "§15.4 guard stays for unattached compositions");
    assert!(io.sent.lock().await.is_empty());
}

#[tokio::test]
async fn missing_committed_digest_stays_closed_without_starting_send() {
    let (worker, io, service, _handoff) =
        fixture(CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() }).await;
    service
        .admit(admission("caller-ok", builder_projection(false, true, true)))
        .await
        .expect("admit");
    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Failed).await;
    worker_stop(stop, task).await;
    assert_eq!(row.state.status, Status::Failed);
    assert_eq!(row.attempt_no, 0, "guard rejects before StartSend: no attempt consumed");
    assert!(io.sent.lock().await.is_empty());
}

#[tokio::test]
async fn missing_committed_sender_facts_stay_closed_without_starting_send() {
    // Wire contract: without the row's trusted `caller_channel` facts the
    // envelope cannot carry a validated `from` (a definite Provider 400);
    // the guard refuses before StartSend exactly like the digest/task
    // guards, and the protected subject never substitutes for them.
    let (worker, io, service, _handoff) =
        fixture(CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() }).await;
    let mut projection = builder_projection(true, true, true);
    projection
        .as_object_mut()
        .expect("object projection")
        .remove("caller_channel");
    service
        .admit(admission("caller-ok", projection))
        .await
        .expect("admit");
    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Failed).await;
    worker_stop(stop, task).await;
    assert_eq!(row.state.status, Status::Failed);
    assert_eq!(row.attempt_no, 0, "guard rejects before StartSend: no attempt consumed");
    assert!(!row.state.may_have_been_sent);
    assert!(io.sent.lock().await.is_empty());
}

#[tokio::test]
async fn unresolvable_caller_target_keeps_the_guard_fail_closed() {
    // Review P1-5: without a real Provider dispatch context (registry
    // refuses, WebSocket-only target, unregistered bot) the runtime must
    // converge the row failed BEFORE StartSend — never a placeholder
    // WebSocket target or a fixed credential ("runtime-protected").
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo));
    let io = Arc::new(RecordingIo {
        refuse_caller_target: true,
        ..Default::default()
    });
    let handoff = seeded_handoff("caller-ok").await;
    let pipeline = caller_pipeline(handoff, CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() });
    let worker = runtime(service.clone(), io.clone(), Some(pipeline));
    service
        .admit(admission("caller-ok", builder_projection(true, true, true)))
        .await
        .expect("admit");
    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Failed).await;
    worker_stop(stop, task).await;
    assert_eq!(row.state.status, Status::Failed);
    assert_eq!(row.attempt_no, 0, "resolution guard rejects before StartSend");
    assert!(io.sent.lock().await.is_empty());
}

#[tokio::test]
async fn pre_network_identity_loss_converges_definitely_not_sent() {
    // Pipeline attached and wire facts complete, but this scope never got a
    // parked identity (seeds a different message id).
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo));
    let io = Arc::new(RecordingIo::default());
    let handoff = seeded_handoff("some-other-turn").await;
    let pipeline = caller_pipeline(handoff, CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() });
    let worker = runtime(service.clone(), io, Some(pipeline));
    service
        .admit(admission("caller-ok", builder_projection(true, true, true)))
        .await
        .expect("admit");
    let (stop, task) = start(worker);
    let row = run_until_status(&service, "caller-ok", |row| row.state.status == Status::Failed).await;
    worker_stop(stop, task).await;
    assert_eq!(row.state.status, Status::Failed);
    assert!(!row.state.may_have_been_sent, "pre-network identity loss proves the send never happened");
    assert_eq!(row.attempt_no, 1, "StartSend consumed the real attempt before the claim");
}


#[tokio::test]
async fn a_delegate_row_dispatches_through_the_lineage_issued_address_chain() {
    // P5.9 runtime e2e (plan §15.71): a caller_delegate_parent row rides
    // the DELEGATE variant of the protected branch — the identity comes
    // from the pre-registered root lineage slot (no handoff claim), the
    // issuance is lineage-bounded, and the row converges Submitted with
    // the ordinary transport untouched.
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo));
    let io = Arc::new(RecordingIo::default());
    let _handoff = HumanCallerDeliveryHandoff::new(8); // unused: delegates never claim

    let lineage_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    assert!(lineage_index.register(
        "run-manager",
        bcs_message_flow::caller_lineage::RootLineage {
            root_run_id: "root-run".into(),
            environment: "local".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: "turn-delegate".into(),
            group_id: "group".into(),
            session_id: "session-delegate-e2e".into(),
            history_cutoff_seq: 0,
            expires_at_ms: now_ms() + 600_000,
            identity_subject: "root-pseudonym".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "root-pseudonym".into(),
            parent_execution: false,
        },
        now_ms(),
    ));

    let preparation = Arc::new(CallerContainerPreparation::new(Arc::new(StubGateway)));
    let sender = Arc::new(StubSender {
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
        error: None,
        histories: std::sync::Mutex::new(Vec::new()),
    });
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            Arc::new(HumanCallerDeliveryHandoff::new(8)) as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            600_000,
        )
        .with_root_lineage(lineage_index),
    );
    let worker = runtime(service.clone(), io.clone(), Some(pipeline));

    // The admitted row carries the trust-producer marks exactly as the
    // task_dispatch branch writes them for a delegated assign: caller-
    // required, the digest the ordinary path committed, the full task
    // text, and the delegate-parent correlation — no run-id override.
    let mut projection = json!({"version": 1, "caller_required": true});
    projection["caller_request_digest"] = json!(DIGEST);
    projection["caller_task"] = json!("delegated worker task");
    projection["caller_channel"] = json!({"actor_id": "human_alice", "actor_name": "Alice"});
    projection["caller_delegation"] = json!({"parent_run_id": "run-manager", "parent_attempt": 1, "parent_bot_id": "bot-manager"});
    service.admit(admission("delegate-e2e", projection)).await.unwrap();

    let (stop, task) = start(worker);
    let current = run_until_status(&service, "delegate-e2e", |row| {
        matches!(row.state.status, Status::Dispatching | Status::Running)
    })
    .await;
    worker_stop(stop, task).await;

    assert_eq!(current.state.status, Status::Dispatching, "an accepted delegate dispatch keeps the interactive status");
    assert!(io.sent.lock().await.is_empty(), "the ordinary transport never sees a delegate row");
    assert!(io.prepared.load(std::sync::atomic::Ordering::SeqCst) == 0, "no ordinary prepare either");
    // The delegate's downstream identity came from the Started CAS — the
    // row that reaches Dispatching committed its real run/request ids.
    let real = current.run_id.clone().unwrap_or_default();
    assert!(!real.is_empty(), "the Started CAS committed the real downstream identity");
    // P4 history on the delegate wire (review P1-5 尾段): the worker's
    // protected send carries the SAME four-element history manifest shape
    // the root send does — snapshot id, cutoff, projection version and
    // canonical digest — never a bare None.
    let histories = sender.histories.lock().unwrap();
    assert_eq!(histories.len(), 1, "exactly one protected send");
    let history = histories[0].as_ref().expect("the delegate send carries a history manifest");
    assert_eq!(history.projection_version, 1);
    assert!(history.manifest_digest.starts_with("sha256:"), "canonical digest shape");
    assert!(history.history_snapshot_id.contains(":delegate:"), "delegate-scoped snapshot id");
}

#[path = "caller_protected_dispatch_runtime_contract/watchdog.rs"]
mod watchdog;
