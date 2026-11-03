//! P2-C full-chain acceptance (spec §7.5.1 / 终态仲裁 / 取消意图与回执):
//! real Managed admission → real DeliveryRuntime loop → real
//! CallerDispatchPipeline with the wired typed commit (§15.17) and a real
//! one-shot handoff → protected send via a recording gateway/sender →
//! typed cancel / Provider terminal evidence → credential-free execution
//! control. This is the composition contract: each collaborator's own
//! real-chain behavior is locked by its dedicated contract; this file
//! proves the pieces close one loop and never leak identity material
//! into persisted projections.

use std::sync::Arc;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{BotDeliveryTarget, DeliveryType, NewMessage, RedactedToken, SenderType};
use bcs_message_flow::CallerExecutionControl;
use bcs_message_flow::delivery_runtime::{DeliveryRuntime, DeliveryRuntimeConfig, DeliveryRuntimePolicy};
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_dispatch_pipeline::CallerDispatchPipeline;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_container::CallerContainerPreparation;
use bcs_service_api::application::caller_handoff::{HumanCallerDeliveryHandoffService, QueuedAdmissionReceipt};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, CallerExecutionToken, CallerProofFormat, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::port::CallerDispatchAssociation;
use bcs_service_api::port::{
    CallerContainerDescriptor, CallerContainerError, CallerContainerGatewayPort,
    CallerDispatchAck, CallerExecutionSendPort, CallerSendError,
    EnsureCallerContainer,
};
use bcs_caller_identity_api::{CallerIdentityPlugin, IssueCallerExecution, EstablishedCallerSubject, EstablishCallerSubject, VerifyCallerExecution, VerifiedCallerExecution, CallerIdentityError};
use bcs_service_api::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult, ManagedMessageDeliveryService, ServiceResult,
};
use bcs_service_api::{CallerExecutionCancelCommand, CallerExecutionControlService, CallerExecutionStatus};
use bcs_service_api::application::message_delivery::{
    CallerCancelReason, CallerTerminalEventCommand, CallerTerminalObservation, CallerTerminalOutcome,
};
use serde_json::{Value, json};

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

/// Ordinary transport double: the protected path must never touch it.
#[derive(Default)]
struct OrdinaryIo {
    sent: tokio::sync::Mutex<Vec<String>>,
}

#[async_trait]
impl BotDeliveryPort for OrdinaryIo {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.sent.lock().await.push(cmd.target_bot_id().to_string());
        Ok(BotDeliveryResult { target_bot_id: cmd.target_bot_id().to_string(), delivered: true, error: None })
    }
    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        Ok(BotAbortDeliveryResult { target_bot_id: cmd.target_bot_id().to_string(), aborted_run_ids: Vec::new() })
    }
}

#[async_trait]
impl bcs_service_api::ManagedDeliveryPreparationService for OrdinaryIo {
    async fn is_available(&self, _bot_id: &str) -> bool { true }
    async fn prepare(&self, _row: &PersistedMessageDelivery) -> ServiceResult<bcs_service_api::PreparedManagedDelivery> {
        let frame = bcs_protocol::BcsFrame::Request(bcs_protocol::RequestFrame::new("frame", "chat.send", None));
        // The runtime asserts the prepared command matches the row's minted
        // run identity — carry the row's own run id (the store assigns it
        // at admission), never a placeholder.
        let row_run_id = _row.run_id.clone().unwrap_or_else(|| "run".into());
        Ok(bcs_service_api::PreparedManagedDelivery {
            command: BotDeliveryCommand {
                target: BotDeliveryTarget::WebSocket { bot_id: "bot".into() },
                run_id: row_run_id,
                frame,
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: Default::default(),
                provider_bypass_headers: Vec::new(),
            },
            transport_context_json: json!({"provider_route_headers": []}),
        })
    }
    /// Real-context shape for the protected branch (review P1-5): the row's
    /// Provider scope and its Provider-authorized token — the pipeline's
    /// ensure/send receive exactly what production resolves from the
    /// registry. The runtime asserts the wire association carries these
    /// real provider fields, not placeholders.
    async fn resolve_caller_dispatch(
        &self,
        row: &PersistedMessageDelivery,
    ) -> Option<bcs_service_api::CallerDispatchContext> {
        Some(bcs_service_api::CallerDispatchContext {
            target: BotDeliveryTarget::HttpProvider {
                bot_id: row.target_bot_id.clone(),
                provider_id: "provider-e2e".into(),
                provider_bot_ref: format!("ref-{}", row.target_bot_id),
                webhook_url: "http://127.0.0.1:1/webhook".into(),
                bcs_to_provider_token: RedactedToken::new("provider-svc-token"),
                protocol_version: "2.0".into(),
            },
            service_credential: RedactedToken::new("provider-svc-token"),
            provider_id: "provider-e2e".into(),
            provider_bot_ref: format!("ref-{}", row.target_bot_id),
        })
    }
    async fn prepare_abort(&self, row: &PersistedMessageDelivery) -> ServiceResult<BotAbortDeliveryCommand> {
        Ok(BotAbortDeliveryCommand {
            target: BotDeliveryTarget::WebSocket { bot_id: row.target_bot_id.clone() },
            command_id: format!("abort-{}", row.delivery_id),
            group_id: row.group_id.clone(),
            session_id: row.session_id.clone(),
            run_id: row.run_id.clone(),
            provider_bypass_headers: Vec::new(),
            timeout_ms: 1_000,
        })
    }
    async fn before_send(&self, _started: &PersistedMessageDelivery, _cmd: &BotDeliveryCommand) -> ServiceResult<()> {
        Ok(())
    }
}

/// Recording gateway: one ensure per prepared container. `observe: true`
/// attaches a live observe target to the readback (the shape the delegate
/// path's seeding contract needs) — the Default stays observe-less, so
/// existing journeys are unchanged.
#[derive(Default)]
struct RecordingGateway {
    ensures: std::sync::atomic::AtomicUsize,
    observe: bool,
}

#[async_trait]
impl CallerContainerGatewayPort for RecordingGateway {
    async fn ensure_container(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, CallerContainerError> {
        self.ensures.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let request_target_bot_id = request.target_bot_id.clone();
        Ok(CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: request.target_bot_id,
            caller_subject_ref: request.caller_subject_ref,
            ready: true,
            isolation: bcs_service_api::port::CallerContainerIsolation::ContainerBound,
            binding_ref: None,
            runtime_generation: None,
            expires_at_ms: now_ms() + 600_000,
            observe_target: if self.observe {
                Some(bcs_service_api::port::CallerObserveTarget {
                    caller_subject: bcs_service_api::port::CallerOpaqueRef::new("fixture-subject"),
                    caller_ref: Some(bcs_service_api::port::CallerOpaqueRef::new(format!(
                        "worker-ref-{}",
                        request.session_id
                    ))),
                    target_bot_id: request_target_bot_id,
                    session_id: request.session_id,
                    target_epoch: Some(bcs_service_api::port::CallerOpaqueRef::new("worker-epoch-1".to_string())),
                    expires_at_ms: now_ms() + 600_000,
                })
            } else {
                None
            },
        })
    }
}

struct StubIdentity;

#[async_trait]
impl CallerIdentityPlugin for StubIdentity {
    async fn establish_subject(&self, input: EstablishCallerSubject) -> Result<EstablishedCallerSubject, CallerIdentityError> {
        // Subject establishment is owned by the selected identity plugin.
        // The e2e stub derives an advisory subject lifetime from the
        // issuance clock the way the real JWT plugin does.
        let deadline = input.now_ms + 300_000;
        let subject = bcs_service_api::application::caller_identity::CallerSubjectRef::new(
            bcs_service_api::application::caller_identity::CallerSubjectKind::Human,
            bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            input.ingress.actor().actor_id.clone(),
            deadline,
        )
        .map_err(|_| CallerIdentityError::InvalidRequest)?;
        let proof = CallerExecutionToken::new(
            CallerProofFormat::Jwt,
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
    async fn issue_execution(&self, input: IssueCallerExecution<'_>) -> Result<CallerExecutionToken, CallerIdentityError> {
        CallerExecutionToken::new(CallerProofFormat::Jwt, "runtime-proof".into(), input.not_after_ms)
            .map_err(|_| CallerIdentityError::InvalidRequest)
    }
    async fn verify_execution(&self, _input: VerifyCallerExecution) -> Result<VerifiedCallerExecution, CallerIdentityError> {
        Err(CallerIdentityError::TokenInvalid)
    }
}

/// Recording protected sender with a configurable ack.
struct RecordingSender {
    ack: CallerDispatchAck,
    sends: std::sync::atomic::AtomicUsize,
    histories: std::sync::Mutex<Vec<Option<bcs_service_api::port::CallerExecutionHistory>>>,
}

#[async_trait]
impl CallerExecutionSendPort for RecordingSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        command: bcs_service_api::port::SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, CallerSendError> {
        self.sends.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        self.histories.lock().unwrap().push(command.history.clone());
        Ok(self.ack.clone())
    }
}

fn admit_caller_group(id: &str, targets: &[&str]) -> AdmitMessageDeliveries {
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
        targets: targets
            .iter()
            .map(|bot| DeliveryAdmissionTarget {
                run_id: None,
                rejection: None,
                target_bot_id: (*bot).into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: json!({
                    "version": 1,
                    "caller_required": true,
                    "caller_task": "group caller task",
                    "caller_channel": {"actor_id": "human_alice", "actor_name": "Alice"},
                    "caller_not_after_ms": now_ms() + 300_000,
                }),
            })
            .collect(),
    }
}

fn admit_caller(_managed: &ManagedMessageDelivery, id: &str) -> AdmitMessageDeliveries {
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
                run_id: None,
            rejection: None,
            target_bot_id: "bot".into(),
            kind: DeliveryType::Send,
            max_queued: 10,
            semantic_projection_json: json!({
                "version": 1,
                "caller_required": true,
                "caller_task": "runtime caller task",
                "caller_channel": {"actor_id": "human_alice", "actor_name": "Alice"},
                "caller_not_after_ms": now_ms() + 300_000,
            }),
        }],
    }
}

/// Real one-shot handoff carrying the ingress parked by admission.
async fn seeded_handoff(message_id: &str) -> Arc<HumanCallerDeliveryHandoff> {
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    // The memory repo pins row env to "local" (same alignment lesson as
    // §15.14): resolve_env_str() follows the process ENV and would part
    // ways with the row env, making the claim read NotReserved.
    let scope = bcs_service_api::application::caller_handoff::HandoffScope {
        env: "local".to_string(),
        session_id: format!("session-{message_id}"),
        // P3.4: the group scope rides the canonical turn lineage now —
        // seeded with the harness group like the production seam would.
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
                "local".to_string(),
            )
            .unwrap(),
            QueuedAdmissionReceipt::new("delivery-1", 1, now_ms()),
        )
        .await
        .unwrap();
    handoff
}

struct Chain {
    managed: Arc<ManagedMessageDelivery>,
    gateway: Arc<RecordingGateway>,
    sender: Arc<RecordingSender>,
    handoff: Arc<HumanCallerDeliveryHandoff>,
    stop: tokio::sync::watch::Sender<bool>,
    task: tokio::task::JoinHandle<Result<(), bcs_service_api::ManagedDeliveryError>>,
    ordinary: Arc<OrdinaryIo>,
}

async fn boot(message_id: &str, ack: CallerDispatchAck) -> Chain {
    let managed = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    managed
        .admit(admit_caller(managed.as_ref(), message_id))
        .await
        .expect("admit caller row");
    let handoff = seeded_handoff(message_id).await;
    let gateway = Arc::new(RecordingGateway::default());
    let sender = Arc::new(RecordingSender { ack, sends: std::sync::atomic::AtomicUsize::new(0), histories: std::sync::Mutex::new(Vec::new()) });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_managed_commit(managed.clone()),
    );
    let ordinary = Arc::new(OrdinaryIo::default());
    let runtime = DeliveryRuntime {
        policy: None,
        service: managed.clone(),
        preparation: ordinary.clone(),
        transport: ordinary.clone(),
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
        caller_dispatch: Some(pipeline),
    };
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(runtime.run(shutdown));
    Chain { managed, gateway, sender, handoff, stop, task, ordinary }
}

async fn boot_group(message_id: &str, ack: CallerDispatchAck, targets: &[&str]) -> Chain {
    // Group mode: ONE admission with per-target deliveries, per-target
    // handoff slots sharing the same ingress — the runtime converges each
    // target independently (its own attempt, its own dispatch).
    let managed = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    managed
        .admit(admit_caller_group(message_id, targets))
        .await
        .expect("admit group caller rows");
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let ingress = AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        now_ms(),
        "local".to_string(),
    )
    .unwrap();
    targets_publish(managed.as_ref(), message_id, targets, ingress, &handoff)
        .await
        .expect("publish identity across target slots");
    let gateway = Arc::new(RecordingGateway::default());
    let sender = Arc::new(RecordingSender { ack, sends: std::sync::atomic::AtomicUsize::new(0), histories: std::sync::Mutex::new(Vec::new()) });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_managed_commit(managed.clone()),
    );
    let ordinary = Arc::new(OrdinaryIo::default());
    let mut bots = std::collections::BTreeMap::new();
    for target in targets {
        bots.insert(
            (*target).to_string(),
            DeliveryRuntimePolicy { max_running: 2, min_send_interval_ms: 0 },
        );
    }
    let runtime = DeliveryRuntime {
        policy: None,
        service: managed.clone(),
        preparation: ordinary.clone(),
        transport: ordinary.clone(),
        config: DeliveryRuntimeConfig {
            max_safe_retries: 0,
            pause_dispatch: false,
            bots,
            tick: Duration::from_millis(5),
            expiry_tick: Duration::from_millis(50),
            io_timeout: Duration::from_secs(1),
            run_timeout: Duration::from_secs(30),
            cancel_timeout: Duration::from_secs(1),
            startup_recovery_grace: Duration::from_secs(2),
            max_tasks: 4,
            max_abort_tasks: 1,
        },
        caller_dispatch: Some(pipeline),
    };
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(runtime.run(shutdown));
    Chain { managed, gateway, sender, handoff, stop, task, ordinary }
}

/// Reserve+publish per-target slots for a group message; mirrors what the
/// (real) admission seam would do with publish_for_targets.
async fn targets_publish(
    _managed: &ManagedMessageDelivery,
    message_id: &str,
    targets: &[&str],
    ingress: AuthenticatedHumanCallerIngress,
    handoff: &Arc<HumanCallerDeliveryHandoff>,
) -> Result<(), bcs_service_api::application::caller_handoff::HandoffError> {
    use bcs_service_api::application::caller_handoff::HumanCallerDeliveryHandoffService as _Handoff;
    let rows = _managed.snapshot(None).await.unwrap();
    let mut pairs = Vec::new();
    for target in targets {
        let scope = bcs_service_api::application::caller_handoff::HandoffScope {
            env: "local".to_string(),
            session_id: format!("session-{message_id}"),
            group_id: Some("group".to_string()),
            source_message_id: message_id.into(),
            target_bot_id: (*target).to_string(),
        };
        let row = rows
            .iter()
            .find(|row| row.target_bot_id == *target)
            .expect("target delivery row");
        handoff.reserve(&scope, 30_000, now_ms()).await?;
        pairs.push((
            scope,
            QueuedAdmissionReceipt::new(row.delivery_id.clone(), row.state.state_version, now_ms()),
        ));
    }
    _Handoff::publish_for_targets(&**handoff, ingress, &pairs).await
}

fn row_association(row: &PersistedMessageDelivery) -> CallerDispatchAssociation {
    CallerDispatchAssociation {
        environment: row.env.clone(),
        target_bot_id: row.target_bot_id.clone(),
        provider_id: String::new(),
        provider_bot_ref: String::new(),
        session_id: row.session_id.clone(),
        run_id: row.run_id.clone().unwrap(),
        attempt: row.attempt_no,
        dispatch_id: row.request_id.clone().unwrap(),
    }
}

async fn wait_row(
    managed: &ManagedMessageDelivery,
    source: &str,
    predicate: impl Fn(&PersistedMessageDelivery) -> bool,
) -> PersistedMessageDelivery {
    for _ in 0..4000 {
        for row in managed.snapshot(None).await.unwrap() {
            if row.source_message_id == source && predicate(&row) {
                return row.clone();
            }
        }
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
    for row in managed.snapshot(None).await.unwrap() {
        if row.source_message_id == source {
            panic!(
                "row {source} never reached the expected state: status={:?} attempt={} may_have_been_sent={} projection={}",
                row.state.status, row.attempt_no, row.state.may_have_been_sent, row.semantic_projection_json
            );
        }
    }
    panic!("row {source} disappeared");
}

fn dispatched(row: &PersistedMessageDelivery) -> bool {
    matches!(row.state.status, Status::Dispatching | Status::Running | Status::Unknown)
        && row.request_id.is_some()
        && row.semantic_projection_json.get("caller_dispatch").is_some()
}

fn keys_of(value: &Value) -> Vec<&str> {
    let mut keys: Vec<&str> = value.as_object().map(|object| object.keys().map(String::as_str).collect()).unwrap_or_default();
    keys.sort();
    keys
}

fn terminal_for(
    row: &PersistedMessageDelivery,
    outcome: CallerTerminalOutcome,
    event_seq: u64,
) -> CallerTerminalEventCommand {
    CallerTerminalEventCommand {
        provider_id: "provider-1".into(),
        dispatch: row_association(row),
        observation: CallerTerminalObservation { outcome, event_seq, control_revision: 0 },
        now_ms: now_ms(),
    }
}

#[tokio::test]
async fn accepted_chain_commits_wired_digest_protects_send_and_settles_on_terminal() {
    let chain = boot(
        "accepted-e2e",
        CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() },
    )
    .await;
    let row = wait_row(&chain.managed, "accepted-e2e", dispatched).await;

    // Exactly one gateway ensure and one protected send; the ordinary
    // transport is never consulted for this row and the identity is gone
    // after the single consume.
    assert_eq!(chain.gateway.ensures.load(std::sync::atomic::Ordering::SeqCst), 1);
    assert_eq!(chain.sender.sends.load(std::sync::atomic::Ordering::SeqCst), 1);
    assert!(chain.ordinary.sent.lock().await.is_empty(), "protected rows never hit the ordinary transport");

    // The wired typed commit produced the canonical digest in the same
    // CAS as the dispatch; no identity material rode the projection.
    let dispatch = &row.semantic_projection_json["caller_dispatch"];
    assert!(dispatch["caller_request_digest"].as_str().is_some_and(|digest| {
        digest.starts_with("sha256:") && digest.len() == 71 && digest != "sha256:not-the-facts-one"
    }), "wired digest must be the canonical recomputation, got {dispatch}");
    assert_eq!(
        keys_of(dispatch),
        vec!["caller_control_revision", "caller_not_after_ms", "caller_request_digest", "version"],
        "caller_dispatch carries lifecycle facts only"
    );

    // Provider terminal evidence settles the attempt.
    chain
        .managed
        .handle_caller_terminal(terminal_for(&row, CallerTerminalOutcome::Completed, 12))
        .await
        .expect("terminal settles");
    let settled = wait_row(&chain.managed, "accepted-e2e", |row| row.state.status == Status::Completed).await;
    assert_eq!(settled.semantic_projection_json["caller_terminal"]["event_seq"], 12);
    assert_eq!(keys_of(&settled.semantic_projection_json["caller_terminal"]),
        vec!["caller_control_revision", "event_seq", "outcome", "version"]);

    // Credential-free control reads the settled state.
    let control = CallerExecutionControl::new(chain.managed.clone());
    let view = control.execution_status(association_query(&settled)).await.unwrap();
    assert_eq!(view.status, CallerExecutionStatus::Accepted);
    assert_eq!(view.execution_state, "completed");
    assert_eq!(view.cancel_state, None);

    chain.stop.send(true).ok();
    chain.task.await.unwrap().unwrap();
}

#[tokio::test]
async fn cancel_lands_first_and_the_late_completed_outcome_is_recorded_independently() {
    let chain = boot(
        "cancel-e2e",
        CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:x".repeat(1) },
    )
    .await;
    let row = wait_row(&chain.managed, "cancel-e2e", dispatched).await;

    // Typed cancel through the credential-free control path.
    let control = CallerExecutionControl::new(chain.managed.clone());
    let outcome = control
        .execution_cancel(CallerExecutionCancelCommand {
            query: association_query(&row),
            cancel_request_id: "cancel-e2e-1".into(),
            reason: CallerCancelReason::UserRequested,
            now_ms: now_ms(),
        })
        .await
        .expect("cancel lands");
    assert_eq!(outcome.cancel_state, "requested");

    // Terminal evidence for a completed run arrives after the intent: the
    // logical state stays Cancelled while the actual outcome is durable.
    chain
        .managed
        .handle_caller_terminal(terminal_for(&row, CallerTerminalOutcome::Completed, 7))
        .await
        .expect("late terminal applies");
    let settled = wait_row(&chain.managed, "cancel-e2e", |row| row.state.status == Status::Cancelled).await;
    assert_eq!(settled.semantic_projection_json["caller_terminal"]["outcome"], "completed");
    assert_eq!(settled.semantic_projection_json["caller_cancel"]["cancel_request_id"], "cancel-e2e-1");

    let view = control.execution_status(association_query(&settled)).await.unwrap();
    assert_eq!(view.execution_state, "cancelled");
    assert_eq!(view.cancel_state, Some("requested".to_string()));

    chain.stop.send(true).ok();
    chain.task.await.unwrap().unwrap();
}

#[tokio::test]
async fn unknown_ack_freezes_the_attempt_until_evidence_and_old_attempts_cannot_settle_it() {
    let chain = boot("unknown-e2e", CallerDispatchAck::Unknown).await;
    let row = wait_row(&chain.managed, "unknown-e2e", |row| row.state.status == Status::Unknown).await;

    // One send only: an uncertain ACK never re-issues the request.
    assert_eq!(chain.sender.sends.load(std::sync::atomic::Ordering::SeqCst), 1);
    // The uncertainty cannot be expired into a lane release.
    assert_eq!(row.expire_at_ms, None);

    // An old attempt's receipt gains nothing.
    let mut stale = terminal_for(&row, CallerTerminalOutcome::Failed, 3);
    stale.dispatch.attempt = row.attempt_no.saturating_sub(1);
    let error = chain
        .managed
        .handle_caller_terminal(stale)
        .await
        .expect_err("an old attempt never settles a new task");
    assert!(matches!(error, bcs_service_api::ManagedDeliveryError::NotFound));

    // Real evidence converges the attempt.
    chain
        .managed
        .handle_caller_terminal(terminal_for(&row, CallerTerminalOutcome::Failed, 2))
        .await
        .expect("evidence settles");
    let settled = wait_row(&chain.managed, "unknown-e2e", |row| row.state.status == Status::Failed).await;
    assert_eq!(settled.semantic_projection_json["caller_terminal"]["outcome"], "failed");

    chain.stop.send(true).ok();
    chain.task.await.unwrap().unwrap();
}

fn association_query(row: &PersistedMessageDelivery) -> bcs_service_api::CallerExecutionQuery {
    bcs_service_api::CallerExecutionQuery {
        environment: row.env.clone(),
        target_bot_id: row.target_bot_id.clone(),
        provider_id: String::new(),
        provider_bot_ref: String::new(),
        session_id: row.session_id.clone(),
        run_id: row.run_id.clone().unwrap(),
        attempt: row.attempt_no,
        dispatch_id: row.request_id.clone().unwrap(),
    }
}
#[tokio::test]
async fn group_dispatches_both_targets_independently_with_one_identity() {
    // P4.0 (P3 收口): the group-mode runtime converges EVERY caller target
    // independently — its own StartSend/attempt, own wire commit, own
    // protected send — while the identity handoff publishes ONCE across
    // both slots. Two targets, two sends, one actor.
    let chain = boot_group(
        "group-e2e",
        CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha567".into() },
        &["bot-a", "bot-b"],
    )
    .await;

    let a = wait_row(&chain.managed, "group-e2e", |row| {
        row.target_bot_id == "bot-a" && row.semantic_projection_json.get("caller_dispatch").is_some()
    })
    .await;
    let b = wait_row(&chain.managed, "group-e2e", |row| {
        row.target_bot_id == "bot-b" && row.semantic_projection_json.get("caller_dispatch").is_some()
    })
    .await;

    // Independent roots and dispatches per target.
    assert_ne!(a.run_id, b.run_id, "per-target roots must be independent");
    assert_ne!(a.request_id, b.request_id, "per-target dispatch ids must be independent");
    // Canonical turn is shared: same admission, same source message id.
    assert_eq!(a.source_message_id, b.source_message_id, "one canonical turn");

    // Exactly two protected sends; the ordinary transport stays untouched.
    assert_eq!(chain.sender.sends.load(std::sync::atomic::Ordering::SeqCst), 2);
    assert!(chain.ordinary.sent.lock().await.is_empty());

    // P4.2: every protected send of a caller row carries the shared
    // history header — snapshot id + cutoff + digest of an explicit list.
    {
        let histories = chain.sender.histories.lock().unwrap();
        assert_eq!(histories.len(), 2, "one header per protected send");
        for history in histories.iter().flatten() {
            assert!(!history.history_snapshot_id.is_empty());
            assert!(history.manifest_digest.starts_with("sha256:"));
            assert_eq!(history.manifest_digest.len(), "sha256:".len() + 64);
            assert!(history.history_cutoff_seq > 0, "cutoff fixed at admission");
        }
        assert!(histories.iter().all(Option::is_some), "caller rows always carry history");
    }

    // Both slots claimed (their seams sealed); a second claim of either
    // refuses with AlreadyClaimed.
    for bot in ["bot-a", "bot-b"] {
        let scope = bcs_service_api::application::caller_handoff::HandoffScope {
            env: "local".to_string(),
            session_id: "session-group-e2e".to_string(),
            group_id: Some("group".to_string()),
            source_message_id: "group-e2e".into(),
            target_bot_id: bot.into(),
        };
        let claim = bcs_service_api::application::caller_handoff::HandoffClaim::new(
            "anything".to_string(),
            0,
            None,
            1,
            now_ms(),
        );
        let result = chain.handoff.claim(&scope, claim).await.expect_err("claimed slots are sealed");
        assert!(
            matches!(
                result,
                bcs_service_api::application::caller_handoff::HandoffError::AlreadyClaimed
            ),
            "slot for {bot} should be sealed, got {result:?}"
        );
    }

    chain.stop.send(true).ok();
    chain.task.await.unwrap().unwrap();
}

// ---------------------------------------------------------------------------
// P6 identity-outlet canary (review P6.2): one full accepted dispatch's every
// persisted projection/transport field and every captured log line must be
// free of the caller's principal material (staff number, proof token, the
// planned identity subject strings) and any placeholder service credential
// remnants. The canonical sender id of group messages is a legitimate
// business fact (pseudonymized display identity) and is deliberately NOT in
// the banned set; the staff number, the execution proof and credentials
// never have a business reason to persist or print.
// ---------------------------------------------------------------------------
use tracing_subscriber::fmt::MakeWriter;

struct LineSink {
    buf: Vec<u8>,
    out: Arc<std::sync::Mutex<Vec<String>>>,
}

impl std::io::Write for LineSink {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.buf.extend_from_slice(bytes);
        while let Some(index) = self.buf.iter().position(|byte| *byte == b'\n') {
            let line: Vec<u8> = self.buf.drain(..=index).collect();
            self.out
                .lock()
                .expect("canary log sink")
                .push(String::from_utf8_lossy(&line).to_string());
        }
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

struct LineWriter {
    out: Arc<std::sync::Mutex<Vec<String>>>,
}

impl<'a> MakeWriter<'a> for LineWriter {
    type Writer = LineSink;
    fn make_writer(&'a self) -> LineSink {
        LineSink { buf: Vec::new(), out: self.out.clone() }
    }
}

#[tokio::test]
async fn identity_outlets_carry_no_secret_material_canary() {
    // (a) Process-level capture (the "try_init" fix recorded since §15.68):
    // the thread-local set_default raced the runtime task's first log
    // emission — the flake's root cause. ONE global subscriber for the
    // test binary makes the capture deterministic across every worker
    // thread; the banned literals never legitimately appear in ANY other
    // test's logs, so a shared capture keeps the canary honest.
    let sink = Arc::new(std::sync::Mutex::new(Vec::new()));
    let canary_sink = sink.clone();
    let sink_for_writer = canary_sink.clone();
    static CANARY_INIT: std::sync::OnceLock<()> = std::sync::OnceLock::new();
    CANARY_INIT.get_or_init(|| {
        let _ = tracing::subscriber::set_global_default(
            tracing_subscriber::fmt()
                .with_writer(LineWriter { out: sink_for_writer })
                .with_max_level(tracing::Level::DEBUG)
                .with_ansi(false)
                .finish(),
        );
    });

    let chain = boot(
        "canary-e2e",
        CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() },
    )
    .await;
    let row = wait_row(&chain.managed, "canary-e2e", dispatched).await;

    // The banned literals: staff principal material, the execution proof
    // the identity plugin prints nowhere, and the retired placeholder
    // credential that must not resurface anywhere.
    let banned = ["S12345", "proof-token", "runtime-protected"];

    // (b) persistence-outlet canary: every persisted row's recursively
    // serialized surface (full row fields, projections, transport context)
    // must hold none of the banned literals — the durable store keeps
    // non-secret lifecycle facts only.
    let rows = chain.managed.snapshot(None).await.unwrap();
    assert!(!rows.is_empty(), "chain rows exist");
    for row in &rows {
        let serialized = serde_json::to_string(row).expect("row serialization");
        for item in banned {
            assert!(
                !serialized.contains(item),
                "row {item} leaked identity material into persistence: {serialized}"
            );
        }
    }
    // Projection keys stay the closed lifecycle whitelist (§15.16/§15.24
    // carried digest/terminal/cancel facts only).
    let dispatch_key = row.semantic_projection_json.get("caller_dispatch").and_then(Value::as_object);
    let dispatch_keys: Vec<String> = dispatch_key.into_iter().flat_map(|map| map.keys().cloned()).collect();
    for key in dispatch_keys {
        assert!(
            matches!(&key[..], "version" | "caller_request_digest" | "caller_not_after_ms" | "caller_control_revision"),
            // closed whitelistguarded by the sibling full-chain contracts too
            "unexpected caller_dispatch key {key}"
        );
    }

    // (c) log-outlet canary: the captured lines are free of the banned
    // literals — the pipeline's no-body log paths hold. The capture fills
    // asynchronously once the chain's tasks yield; a bounded wait keeps
    // the non-empty guard honest without racing the scheduler.
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if !sink.lock().expect("canary sink").is_empty() {
            break;
        }
        if Instant::now() >= deadline {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    tokio::time::sleep(Duration::from_millis(50)).await;
    let lines = sink.lock().expect("canary sink").clone();
    assert!(!lines.is_empty(), "the dispatch logged observable events");
    for line in &lines {
        for item in banned {
            assert!(
                !line.contains(item),
                "log line leaked identity material ({item}): {line}"
            );
        }
    }

    // (d) the terminal evidence settles without ever re-reading identity:
    // the canonical outlet chain stays closed after the canary too.
    chain
        .managed
        .handle_caller_terminal(terminal_for(&row, CallerTerminalOutcome::Completed, 21))
        .await
        .expect("terminal settle");
    let settled = wait_row(&chain.managed, "canary-e2e", |row| {
        row.state.status == Status::Completed
    })
    .await;
    for item in banned {
        let serialized = serde_json::to_string(&settled).unwrap();
        assert!(!serialized.contains(item), "terminal row leaked {item}");
    }
}

#[path = "support/caller_delegate_e2e.rs"]
mod caller_delegate_e2e;

#[path = "caller_p2c/owner_lineage.rs"]
mod owner_lineage;

#[path = "support/caller_workflow_origin_e2e.rs"]
mod caller_workflow_origin_e2e;
