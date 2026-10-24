//! Protected dispatch pipeline conformance (§7.5.1, P2-B fourth slice).
//! Real handoff (identity semantics are one-shot and load-bearing); the
//! preparation/gateway, identity plugin and sender are recording doubles —
//! each is independently proven by its own real-chain contract (§15.9/§15.10
//! preparation, §15.5 JWT plugin, §15.11 send wire).

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_dispatch_pipeline::{
    CallerDispatchError, CallerDispatchOutcome, CallerDispatchPipeline, CommittedDispatchFacts,
};
use bcs_service_api::application::caller_container::CallerContainerPreparation;
use bcs_service_api::application::caller_execution::HumanCallerDeliveryHandoffService;
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, CallerExecutionPurpose, CallerExecutionToken, CallerProofFormat,
    CallerSubjectKind, CallerSubjectRef, CallerSubjectRefType, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::port::{
    CallerContainerDescriptor, CallerContainerError, CallerContainerGatewayPort,
    CallerContainerIsolation, CallerDispatchAck, CallerExecutionSendPort, CallerOpaqueRef,
    EnsureCallerContainer, SendCallerExecutionCommand,
};
use bcs_caller_identity_api::{CallerIdentityError, CallerIdentityPlugin, EstablishCallerSubject, EstablishedCallerSubject, IssueCallerExecution, VerifyCallerExecution, VerifiedCallerExecution};

const NOW: i64 = 1_780_000_000_000;
const CONTAINER_DEADLINE: i64 = NOW + 120_000;
const DIGEST: &str = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn now_clock() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as i64
}

fn ingress() -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        now_clock(),
        now_clock() + 300_000,
        "test",
    )
    .unwrap()
}

struct StubGateway {
    calls: AtomicUsize,
    fail_with: Option<CallerContainerError>,
    runs: std::sync::Mutex<Vec<bcs_service_api::port::caller_container::ContainerRunAssociation>>,
}

#[async_trait]
impl CallerContainerGatewayPort for StubGateway {
    async fn ensure_container(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, CallerContainerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.runs.lock().unwrap().push(request.run.clone());
        if let Some(error) = &self.fail_with {
            return Err(error.clone());
        }
        Ok(CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: "bot-b".into(),
            caller_subject_ref: CallerOpaqueRef::new("S12345".to_string()),
            ready: true,
            isolation: CallerContainerIsolation::ContainerBound,
            binding_ref: Some(CallerOpaqueRef::new("bind-1".to_string())),
            runtime_generation: Some(2),
            expires_at_ms: now_clock() + CONTAINER_DEADLINE - NOW,
            observe_target: None,
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
        // The recording stub passes the ingress
        // window through and derives the subject the way the pipeline used
        // to construct directly.
        use bcs_service_api::application::caller_identity::{
            CallerSubjectKind, CallerSubjectRef, CallerSubjectRefType,
            CallerExecutionToken, CallerProofFormat,
        };
        let deadline = input.ingress.expires_at_ms();
        let subject = CallerSubjectRef::new(
            CallerSubjectKind::Human,
            CallerSubjectRefType::BcsEphemeral,
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
    async fn issue_execution(
        &self,
        input: IssueCallerExecution<'_>,
    ) -> Result<CallerExecutionToken, CallerIdentityError> {
        let _ = CallerSubjectRef::new(
            CallerSubjectKind::Human,
            CallerSubjectRefType::BcsEphemeral,
            "x".into(),
            input.not_after_ms,
        );
        CallerExecutionToken::new(CallerProofFormat::Jwt, "proof-token".into(), input.not_after_ms)
            .map_err(|_| CallerIdentityError::InvalidRequest)
    }
    async fn verify_execution(
        &self,
        _input: VerifyCallerExecution,
    ) -> Result<VerifiedCallerExecution, CallerIdentityError> {
        Err(CallerIdentityError::TokenInvalid)
    }
}

struct RecordingSender {
    calls: AtomicUsize,
    ack: CallerDispatchAck,
    error: Option<bcs_service_api::port::CallerSendError>,
}

#[async_trait]
impl CallerExecutionSendPort for RecordingSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        _command: SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if let Some(error) = &self.error {
            return Err(error.clone());
        }
        Ok(self.ack.clone())
    }
}

fn facts() -> CommittedDispatchFacts {
    CommittedDispatchFacts {
        environment: "test".into(),
        target_bot_id: "bot-b".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-b".into(),
        session_id: "session-1".into(),
        group_id: None,
        message_turn_id: "turn-1".into(),
        root_run_id: "run-1".into(),
        run_id: "run-1".into(),
        attempt: 1,
        dispatch_id: "dispatch-1".into(),
        request_digest: DIGEST.into(),
        delivery_commit: None,
        not_after_ms: now_clock() + 280_000,
        caller_task: "root h2a task".into(),
        channel_actor_id: "human_alice".into(),
        channel_actor_name: "Alice".into(),
        control_revision: 0,
        history: None,
        shared_content_kind: None,
    }
}

fn pipeline(
    gateway: Arc<StubGateway>,
    sender: Arc<RecordingSender>,
    handoff: Arc<HumanCallerDeliveryHandoff>,
) -> CallerDispatchPipeline {
    let preparation = Arc::new(CallerContainerPreparation::new(gateway));
    CallerDispatchPipeline::new(
        handoff as Arc<dyn HumanCallerDeliveryHandoffService>,
        preparation,
        Arc::new(StubIdentity),
        sender as Arc<dyn CallerExecutionSendPort>,
        3_600_000,
    )
}

async fn seeded_handoff(scope_source_id: &str) -> (Arc<HumanCallerDeliveryHandoff>, ()) {
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    // reserve then publish as the dispatcher's caller_admission would
    let scope = bcs_service_api::application::caller_execution::HandoffScope {
        env: "test".into(),
        session_id: "session-1".into(),
        group_id: None,
        source_message_id: scope_source_id.into(),
        target_bot_id: "bot-b".into(),
    };
    handoff.reserve(&scope, 30_000, now_clock()).await.unwrap();
    handoff
        .publish(
            &scope,
            ingress(),
            bcs_service_api::application::caller_execution::QueuedAdmissionReceipt::new(
                "delivery-1", 1, now_clock(),
            ),
        )
        .await
        .unwrap();
    (handoff, ())
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

#[tokio::test]
async fn accepted_path_claims_prepares_issues_and_sends_exactly_once() {
    let (handoff, _) = seeded_handoff("turn-1").await;
    let gateway = Arc::new(StubGateway { calls: AtomicUsize::new(0), fail_with: None, runs: std::sync::Mutex::new(Vec::new()) });
    let sender = Arc::new(RecordingSender {
        calls: AtomicUsize::new(0),
        error: None,
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
    });
    let pipeline = pipeline(gateway.clone(), sender.clone(), handoff.clone());

    let outcome = pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
        .await
        .expect("accepted");
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 1);
    assert_eq!(sender.calls.load(Ordering::SeqCst), 1);

    // Identity consumed exactly once: a second call fails unavailable and
    // never reaches gateway/sender again.
    let second = pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
        .await
        .unwrap_err();
    assert_eq!(second, CallerDispatchError::IdentityUnavailable);
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 1);
    assert_eq!(sender.calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn rejected_and_unknown_acks_enumerate_without_a_second_send() {
    for ack in [
        CallerDispatchAck::Rejected { error_code: "CANCELLED".into(), retryable: false },
        CallerDispatchAck::Unknown,
    ] {
        let (handoff, _) = seeded_handoff("turn-1").await;
        let gateway = Arc::new(StubGateway { calls: AtomicUsize::new(0), fail_with: None, runs: std::sync::Mutex::new(Vec::new()) });
        let sender = Arc::new(RecordingSender { calls: AtomicUsize::new(0), ack, error: None });
        let pipeline = pipeline(gateway.clone(), sender.clone(), handoff.clone());
        let outcome = pipeline
            .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
            .await
            .expect("ack outcome");
        match (&outcome, &sender.ack) {
            (CallerDispatchOutcome::Rejected { error_code, retryable }, CallerDispatchAck::Rejected { error_code: want, retryable: want_r }) => {
                assert_eq!(error_code, want);
                assert_eq!(retryable, want_r);
            }
            (CallerDispatchOutcome::OutcomeUnknown, _) => {}
            other => panic!("unexpected pairing {other:?}"),
        }
        assert_eq!(sender.calls.load(Ordering::SeqCst), 1);
    }
}

#[tokio::test]
async fn send_error_taxonomy_converges_not_sent_only_on_definite_non_acceptance() {
    // Wire doc §3.1/§3.2: NotSent (pre-wire) and a non-retryable gateway
    // non-2xx are deterministic never-accepted evidence — DefinitelyNotSent,
    // so the row can converge failed instead of parking Unknown forever.
    // Ambiguous failures (mid-flight transport, retryable 5xx) stay unknown.
    use bcs_service_api::port::CallerSendError;
    for (error, definitely) in [
        (CallerSendError::NotSent, true),
        (CallerSendError::GatewayFailed { retryable: false }, true),
        (CallerSendError::OutcomeUnknown, false),
        (CallerSendError::GatewayFailed { retryable: true }, false),
    ] {
        let (handoff, _) = seeded_handoff("turn-1").await;
        let gateway = Arc::new(StubGateway { calls: AtomicUsize::new(0), fail_with: None, runs: std::sync::Mutex::new(Vec::new()) });
        let sender = Arc::new(RecordingSender {
            calls: AtomicUsize::new(0),
            ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
            error: Some(error.clone()),
        });
        let pipeline = pipeline(gateway.clone(), sender.clone(), handoff.clone());
        let outcome = pipeline
            .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
            .await
            .expect("send error outcome");
        assert_eq!(
            matches!(outcome, CallerDispatchOutcome::DefinitelyNotSent),
            definitely,
            "wrong classification for {error:?}"
        );
        assert_eq!(sender.calls.load(Ordering::SeqCst), 1);
    }
}

#[tokio::test]
async fn preparation_failure_consumes_the_identity_fail_closed() {
    let (handoff, _) = seeded_handoff("turn-1").await;
    let gateway = Arc::new(StubGateway {
        calls: AtomicUsize::new(0),
        fail_with: Some(CallerContainerError::GatewayRejected { retryable: false }),
        runs: std::sync::Mutex::new(Vec::new()),
    });
    let sender = Arc::new(RecordingSender {
        calls: AtomicUsize::new(0),
        error: None,
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
    });
    let pipeline = pipeline(gateway.clone(), sender.clone(), handoff.clone());

    let error = pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
        .await
        .unwrap_err();
    assert_eq!(error, CallerDispatchError::PreparationFailed);
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 1);
    assert_eq!(sender.calls.load(Ordering::SeqCst), 0, "never send on preparation refusal");

    // The refused pipeline consumed the identity: a retry cannot re-enter.
    let retry = pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
        .await
        .unwrap_err();
    assert_eq!(retry, CallerDispatchError::IdentityUnavailable);
}

#[tokio::test]
async fn no_identity_for_this_scope_fails_unavailable_with_zero_side_effects() {
    // A different turn scope: the admission never parked anything here.
    let (handoff, _) = seeded_handoff("turn-OTHER").await;
    let gateway = Arc::new(StubGateway { calls: AtomicUsize::new(0), fail_with: None, runs: std::sync::Mutex::new(Vec::new()) });
    let sender = Arc::new(RecordingSender {
        calls: AtomicUsize::new(0),
        error: None,
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
    });
    let pipeline = pipeline(gateway.clone(), sender.clone(), handoff.clone());
    let error = pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
        .await
        .unwrap_err();
    assert_eq!(error, CallerDispatchError::IdentityUnavailable);
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 0);
    assert_eq!(sender.calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn structurally_invalid_facts_reject_before_any_consumption() {
    let (handoff, _) = seeded_handoff("turn-1").await;
    let gateway = Arc::new(StubGateway { calls: AtomicUsize::new(0), fail_with: None, runs: std::sync::Mutex::new(Vec::new()) });
    let sender = Arc::new(RecordingSender {
        calls: AtomicUsize::new(0),
        error: None,
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
    });
    let pipeline = pipeline(gateway.clone(), sender.clone(), handoff.clone());

    type Mutate = Box<dyn FnMut(&mut CommittedDispatchFacts)>;
    let mutants: [Mutate; 5] = [
        Box::new(|f| f.attempt = 0),
        Box::new(|f| f.run_id = String::new()),
        Box::new(|f| f.dispatch_id = String::new()),
        Box::new(|f| f.request_digest = "short".into()),
        Box::new(|f| f.caller_task = String::new()),
    ];
    for mut mutate in mutants {
        let mut broken = facts();
        mutate(&mut broken);
        let error = pipeline
            .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), broken, now_clock())
            .await
            .unwrap_err();
        assert_eq!(error, CallerDispatchError::InvalidInput);
    }
    // Identity untouched by invalid-input refusals: a valid dispatch still runs.
    let outcome = pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
        .await
        .expect("valid dispatch still runs");
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
}

#[tokio::test]
async fn identity_window_capped_by_container_deadline_loss_rejects() {
    // Park an identity whose window expires almost immediately.
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let scope = bcs_service_api::application::caller_execution::HandoffScope {
        env: "test".into(),
        session_id: "session-1".into(),
        group_id: None,
        source_message_id: "turn-1".into(),
        target_bot_id: "bot-b".into(),
    };
    handoff.reserve(&scope, 30_000, now_clock()).await.unwrap();
    handoff
        .publish(
            &scope,
            AuthenticatedHumanCallerIngress::new(
                HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
                HumanCallerAuthSource::HttpAuth,
                now_clock(),
                now_clock() + 10,
                "test",
            )
            .unwrap(),
            bcs_service_api::application::caller_execution::QueuedAdmissionReceipt::new("d", 1, now_clock()),
        )
        .await
        .unwrap();
    // The parked window (10ms) cannot cover the pipeline clock: hard loss.
    std::thread::sleep(std::time::Duration::from_millis(20));
    let gateway = Arc::new(StubGateway { calls: AtomicUsize::new(0), fail_with: None, runs: std::sync::Mutex::new(Vec::new()) });
    let sender = Arc::new(RecordingSender {
        calls: AtomicUsize::new(0),
        error: None,
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
    });
    let pipeline = pipeline(gateway.clone(), sender.clone(), handoff.clone());
    let error = pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
        .await
        .unwrap_err();
    assert_eq!(error, CallerDispatchError::IdentityExpired);
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 0, "no ensure after window loss");
}

// Silence unused-warning for proof objects referenced to keep types honest.
#[allow(dead_code)]
fn _witness(_: Option<EstablishedCallerSubject>) {}
#[allow(dead_code)]
fn _witness2(_: Option<VerifiedCallerExecution>) {}
#[allow(dead_code)]
fn _witness4(_: Option<CallerExecutionPurpose>) {}
// ---------------------------------------------------------------------------
// §15.17 wired-commit path: the pipeline is the trusted digest producer.
// ---------------------------------------------------------------------------

use bcs_service_api::application::message_delivery::ManagedMessageDeliveryService;
use bcs_service_api::application::message_delivery::CommitCallerDispatchCommand;
use bcs_service_api::application::message_delivery::DeliveryTransitionCommand;
use bcs_service_api::port::repo::message_delivery::BoundDeliveryContexts;
use bcs_service_api::port::repo::message_delivery::DeliveryLookup;
use bcs_service_api::application::message_delivery::ManagedDeliveryError;

struct RecordingManaged;

#[async_trait]
impl ManagedMessageDeliveryService for RecordingManaged {
    async fn bounded_contexts(&self, _carrier: &str, _limit: usize) -> Result<BoundDeliveryContexts, ManagedDeliveryError> {
        Err(ManagedDeliveryError::Conflict)
    }
    async fn lookup(&self, _scope: DeliveryLookup) -> Result<Vec<PersistedRow>, ManagedDeliveryError> {
        Ok(Vec::new())
    }
    async fn queued_bots(&self, _after: &str, _limit: usize) -> Result<Vec<String>, ManagedDeliveryError> {
        Ok(Vec::new())
    }
    async fn queued_heads(&self, _bot: &str, _after: &str, _limit: usize) -> Result<Vec<bcs_service_api::core::message_delivery::DeliveryScheduleEntry>, ManagedDeliveryError> {
        Ok(Vec::new())
    }
    async fn active_count(&self, _bot: &str) -> Result<u64, ManagedDeliveryError> {
        Ok(0)
    }
    async fn lane_blocked(&self, _row: &PersistedRow) -> Result<bool, ManagedDeliveryError> {
        Ok(false)
    }
    async fn transition(&self, _command: DeliveryTransitionCommand) -> Result<PersistedRow, ManagedDeliveryError> {
        Err(ManagedDeliveryError::Conflict)
    }
    async fn commit_caller_dispatch(&self, command: CommitCallerDispatchCommand) -> Result<bool, ManagedDeliveryError> {
        commit_calls().lock().expect("commit calls").push(command);
        Ok(true)
    }
    fn observe_operation(&self, _operation: &'static str, _seconds: f64, _rows: usize, _success: bool) {}
    async fn work_batch(&self, _kind: bcs_service_api::port::repo::message_delivery::DeliveryWorkBatch, _now_ms: i64, _after: &str, _limit: usize) -> Result<Vec<PersistedRow>, ManagedDeliveryError> {
        Ok(Vec::new())
    }
    async fn queue_statistics(&self) -> Result<Vec<bcs_service_api::port::repo::message_delivery::DeliveryQueueStatistic>, ManagedDeliveryError> {
        Ok(Vec::new())
    }
    async fn unfinished(&self) -> Result<Vec<PersistedRow>, ManagedDeliveryError> {
        Ok(Vec::new())
    }
    async fn update_wait_reasons(&self, _waiting: Vec<(String, bcs_domain::message_delivery::DeliveryWaitReason)>, _now_ms: i64) -> Result<(), ManagedDeliveryError> {
        Ok(())
    }
    async fn accept_run(&self, _request_id: &str, _bot_id: &str, _downstream_run_id: Option<&str>, _now_ms: i64) -> Result<Option<PersistedRow>, ManagedDeliveryError> {
        Ok(None)
    }
    async fn admit(&self, _command: bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries) -> Result<bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult, ManagedDeliveryError> {
        Err(ManagedDeliveryError::Conflict)
    }
    async fn snapshot(&self, _session_id: Option<&str>) -> Result<Vec<PersistedRow>, ManagedDeliveryError> {
        Ok(Vec::new())
    }
    async fn recover(&self, _now_ms: i64) -> Result<(), ManagedDeliveryError> {
        Ok(())
    }
}

type PersistedRow = bcs_domain::message_delivery::PersistedMessageDelivery;

static COMMIT_CALLS: std::sync::OnceLock<std::sync::Mutex<Vec<CommitCallerDispatchCommand>>> =
    std::sync::OnceLock::new();

fn commit_calls() -> &'static std::sync::Mutex<Vec<CommitCallerDispatchCommand>> {
    COMMIT_CALLS.get_or_init(|| std::sync::Mutex::new(Vec::new()))
}

struct StubGateway2;

#[async_trait]
impl CallerContainerGatewayPort for StubGateway2 {
    async fn ensure_container(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, CallerContainerError> {
        Ok(CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: request.target_bot_id,
            caller_subject_ref: request.caller_subject_ref,
            ready: true,
            isolation: CallerContainerIsolation::ContainerBound,
            binding_ref: None,
            runtime_generation: None,
            expires_at_ms: now_clock() + 600_000,
            observe_target: None,
        })
    }
}

struct DigestRecordingSender {
    calls: AtomicUsize,
    sent_digests: std::sync::Mutex<Vec<String>>,
}

#[async_trait]
impl CallerExecutionSendPort for DigestRecordingSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        command: SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.sent_digests
            .lock()
            .expect("sent digests")
            .push(command.request_digest);
        Ok(CallerDispatchAck::Accepted {
            control_revision: 0,
            request_digest: "sha256:recomputed-shape-not-the-facts-one".to_string(),
        })
    }
}

#[tokio::test]
async fn wired_commit_produces_recomputes_and_cas_persists_the_digest() {
    commit_calls().lock().expect("reset").clear();
    let (handoff, _) = seeded_handoff("turn-1").await;
    let gateway = Arc::new(StubGateway2);
    let sender = Arc::new(DigestRecordingSender {
        calls: AtomicUsize::new(0),
        sent_digests: std::sync::Mutex::new(Vec::new()),
    });
    let recording = sender.clone();
    let preparation = Arc::new(CallerContainerPreparation::new(gateway));
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_managed_commit(Arc::new(RecordingManaged)),
    );
    let mut wired = facts();
    wired.delivery_commit = Some(
        bcs_message_flow::caller_dispatch_pipeline::DeliveryCommitOrigin {
            delivery_id: "delivery-origin-1".into(),
            expected_state_version: 4,
        },
    );
    wired.request_digest = String::new(); // wired path must not trust the caller-form digest
    let outcome = pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), wired, now_clock())
        .await
        .expect("wired path succeeds");
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));

    let calls = commit_calls().lock().expect("calls");
    assert_eq!(calls.len(), 1, "exactly one typed commit");
    let recorded = &calls[0];
    assert_eq!(recorded.delivery_id, "delivery-origin-1");
    assert_eq!(recorded.expected_state_version, 4);
    assert_eq!(recorded.commit.request_digest.len(), 71, "canonical digest shape");
    assert!(recorded.commit.request_digest.starts_with("sha256:"));
    assert_eq!(recorded.commit.control_revision, 0);
    assert_eq!(recording.calls.load(Ordering::SeqCst), 1, "exactly one protected send");
    let sent = recording.sent_digests.lock().expect("sent digests");
    assert_eq!(sent.len(), 1);
    assert_eq!(
        sent[0], recorded.commit.request_digest,
        "the digest sent to the Provider must be the digest CAS-committed before the send",
    );
    drop(sent);
}

#[tokio::test]
async fn ensure_carries_the_inherited_history_cutoff_ceiling() {
    // Spec §6.8: a child/derived ensure inherits the ROOT's cutoff ceiling —
    // lineage binds the boundary, never the derivation-time watermark. The
    // pipeline must pass facts.history's cutoff into the container ensure.
    let (handoff, _) = seeded_handoff("turn-1").await;
    let gateway = Arc::new(StubGateway {
        calls: AtomicUsize::new(0),
        fail_with: None,
        runs: std::sync::Mutex::new(Vec::new()),
    });
    let sender = Arc::new(RecordingSender {
        calls: AtomicUsize::new(0),
        error: None,
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
    });
    let mut root_facts = facts();
    root_facts.history = Some(bcs_service_api::port::CallerExecutionHistory {
        history_snapshot_id: "snap-1".into(),
        history_cutoff_seq: 41,
        projection_version: 1,
        manifest_digest: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into(),
    });
    let outcome = pipeline(gateway.clone(), sender, handoff).dispatch_once(
        &delivery_target(),
        &RedactedToken::new("svc"),
        root_facts.clone(),
        now_clock(),
    ).await.unwrap();
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
    let runs = gateway.runs.lock().unwrap();
    assert_eq!(runs.len(), 1);
    assert_eq!(runs[0].history_cutoff_seq, 41, "the ensure inherits the root cutoff");

    // A history-less task (plain H2A) carries no ceiling: 0 stays 0.
    let (handoff, _) = seeded_handoff("turn-1").await;
    let gateway = Arc::new(StubGateway {
        calls: AtomicUsize::new(0),
        fail_with: None,
        runs: std::sync::Mutex::new(Vec::new()),
    });
    let sender = Arc::new(RecordingSender {
        calls: AtomicUsize::new(0),
        error: None,
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() },
    });
    pipeline(gateway.clone(), sender, handoff).dispatch_once(
        &delivery_target(),
        &RedactedToken::new("svc"),
        facts(),
        now_clock(),
    ).await.unwrap();
    let runs = gateway.runs.lock().unwrap();
    assert_eq!(runs[0].history_cutoff_seq, 0, "no shared history means no ceiling");
}

/// An identity-only provider may return a credential that outlives BCS execution.
/// Consumers must carry BOTH windows, not silently extend or relabel either one.
#[tokio::test]
async fn identity_relay_credential_expiry_does_not_extend_dispatch_authorization() {
    struct RelayIdentity { expiry: i64, calls: AtomicUsize }
    #[async_trait]
    impl CallerIdentityPlugin for RelayIdentity {
        async fn establish_subject(&self, input: EstablishCallerSubject) -> Result<EstablishedCallerSubject, CallerIdentityError> {
            StubIdentity.establish_subject(input).await
        }
        async fn issue_execution(&self, _: IssueCallerExecution<'_>) -> Result<CallerExecutionToken, CallerIdentityError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            Ok(CallerExecutionToken::new(CallerProofFormat::Jwt, "upstream-identity-token".into(), self.expiry).unwrap())
        }
        async fn verify_execution(&self, _: VerifyCallerExecution) -> Result<VerifiedCallerExecution, CallerIdentityError> {
            Err(CallerIdentityError::ExecutionVerificationUnsupported)
        }
        fn credential_profile(&self) -> bcs_caller_identity_api::CallerCredentialProfile {
            bcs_caller_identity_api::CallerCredentialProfile::IdentityRelay
        }
    }
    struct WindowSender { expiry: i64, deadline: i64, calls: AtomicUsize }
    #[async_trait]
    impl CallerExecutionSendPort for WindowSender {
        async fn send_caller_execution(&self, _: &BotDeliveryTarget, _: &RedactedToken, command: SendCallerExecutionCommand)
            -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            assert_eq!(command.execution_token_expires_at_ms, self.expiry);
            assert_eq!(command.not_after_ms, self.deadline);
            assert!(command.caller_context_expires_at_ms < self.expiry);
            Ok(CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() })
        }
    }
    let (handoff, _) = seeded_handoff("turn-1").await;
    let gateway = Arc::new(StubGateway { calls: AtomicUsize::new(0), fail_with: None, runs: std::sync::Mutex::new(Vec::new()) });
    let expiry = now_clock() + 3_600_000;
    let deadline = now_clock() + 30_000;
    let identity = Arc::new(RelayIdentity { expiry, calls: AtomicUsize::new(0) });
    let sender = Arc::new(WindowSender { expiry, deadline, calls: AtomicUsize::new(0) });
    let pipeline = CallerDispatchPipeline::new(handoff, Arc::new(CallerContainerPreparation::new(gateway)), identity.clone(), sender.clone(), 3_600_000);
    let mut request = facts();
    request.not_after_ms = deadline;
    let outcome = pipeline.dispatch_once(&delivery_target(), &RedactedToken::new("svc"), request, now_clock()).await.unwrap();
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
    assert_eq!(identity.calls.load(Ordering::SeqCst), 1);
    assert_eq!(sender.calls.load(Ordering::SeqCst), 1);
}
