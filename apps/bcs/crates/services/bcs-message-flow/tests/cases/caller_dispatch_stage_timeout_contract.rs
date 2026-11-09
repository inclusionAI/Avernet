//! Stage-liveness conformance for the protected caller dispatch pipeline
//! (spec 2026-11-03-caller-dispatch-stage-timeouts §4): with the outer
//! window gone, every stage that has no network self-limit must converge
//! inside its own bound with stage-accurate evidence. Tests shrink the
//! stage budget to a hairTrigger and inject one hanging collaborator per
//! stage; the accepted-convergence table is:
//!
//! - handoff claim timeout    → `StageTimeout{handoff_claim}` (conservative;
//!   NEVER `IdentityUnavailable`, which would assert a provable refusal);
//! - ensure timeout           → `StageTimeout{container_ensure}` (conservative);
//! - establish timeout        → `StageTimeout{establish_subject}`;
//! - issue timeout            → `StageTimeout{issue_execution}`;
//! - send timeout             → `OutcomeUnknown` (same evidence as the send
//!   adapter's own callback_timeout_ms limit — the row keeps its lane);
//! - a hanging stage inside the budget never trips the others: the fast
//!   path stays Accepted.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_dispatch_pipeline::{
    CallerDispatchError, CallerDispatchOutcome, CallerDispatchPipeline, CommittedDispatchFacts,
};
use bcs_service_api::application::caller_container::CallerContainerPreparation;
use bcs_service_api::application::caller_handoff::HumanCallerDeliveryHandoffService;
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, CallerExecutionPurpose, CallerExecutionToken,
    CallerProofFormat, CallerSubjectKind, CallerSubjectRef, CallerSubjectRefType,
    HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::port::{
    CallerContainerDescriptor, CallerContainerError, CallerContainerGatewayPort,
    CallerContainerIsolation, CallerDispatchAck, CallerExecutionSendPort, CallerOpaqueRef,
    EnsureCallerContainer, SendCallerExecutionCommand,
};
use bcs_caller_identity_api::{
    CallerIdentityError, CallerIdentityPlugin, EstablishCallerSubject, EstablishedCallerSubject,
    IssueCallerExecution, VerifyCallerExecution, VerifiedCallerExecution,
};

const NOW: i64 = 1_780_000_000_000;
const DIGEST: &str = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn now_clock() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

fn ingress() -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        now_clock(),
        "test",
    )
    .unwrap()
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

async fn seeded_handoff() -> Arc<HumanCallerDeliveryHandoff> {
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let scope = bcs_service_api::application::caller_handoff::HandoffScope {
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
            ingress(),
            bcs_service_api::application::caller_handoff::QueuedAdmissionReceipt::new(
                "delivery-1", 1, now_clock(),
            ),
        )
        .await
        .unwrap();
    handoff
}

/// The stage budget is one scheduler quantum: any real collaborator finishes
/// far inside it; a hanging port never does. `tokio::time::pause`-style
/// virtual clocks are not used because the pipeline under test shares the
/// runtime with the collaborator futures.
const HAIRTRIGGER: Duration = Duration::from_millis(50);

async fn hang() {
    // A future that never resolves: the stage budget is what converges it.
    std::future::pending::<()>().await
}

// ---------------------------------------------------------------------------
// Doubles: fast everywhere except ONE configurable hanging stage.
// ---------------------------------------------------------------------------

struct FastGateway;
#[async_trait]
impl CallerContainerGatewayPort for FastGateway {
    async fn ensure_container(
        &self,
        _target: &BotDeliveryTarget,
        _cred: &RedactedToken,
        request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, CallerContainerError> {
        Ok(CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: request.target_bot_id.clone(),
            caller_subject_ref: request.caller_subject_ref,
            ready: true,
            isolation: CallerContainerIsolation::ContainerBound,
            binding_ref: None,
            runtime_generation: Some(1),
            expires_at_ms: request.deadline_ms,
            observe_target: None,
        })
    }
}

struct HangingGateway;
#[async_trait]
impl CallerContainerGatewayPort for HangingGateway {
    async fn ensure_container(
        &self,
        _target: &BotDeliveryTarget,
        _cred: &RedactedToken,
        _request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, CallerContainerError> {
        hang().await;
        unreachable!("the stage budget must converge a hanging ensure")
    }
}

struct FastIdentity;
#[async_trait]
impl CallerIdentityPlugin for FastIdentity {
    async fn establish_subject(
        &self,
        input: EstablishCallerSubject,
    ) -> Result<EstablishedCallerSubject, CallerIdentityError> {
        let deadline = input.now_ms + 300_000;
        let subject = CallerSubjectRef::new(
            CallerSubjectKind::Human,
            CallerSubjectRefType::BcsEphemeral,
            "alice".into(),
            deadline,
        )
        .map_err(|_| CallerIdentityError::InvalidRequest)?;
        let proof = CallerExecutionToken::new(CallerProofFormat::Jwt, "sp".into(), deadline)
            .map_err(|_| CallerIdentityError::InvalidRequest)?;
        Ok(EstablishedCallerSubject { subject_ref: subject, subject_proof: proof, expires_at_ms: deadline })
    }
    async fn issue_execution(
        &self,
        input: IssueCallerExecution<'_>,
    ) -> Result<CallerExecutionToken, CallerIdentityError> {
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

/// Identity double that hangs at exactly one chosen stage; the other stage
/// succeeds the way FastIdentity does so the dispatch reaches the hang.
struct HangingIdentity {
    hang_establish: bool,
    hang_issue: bool,
}
#[async_trait]
impl CallerIdentityPlugin for HangingIdentity {
    async fn establish_subject(
        &self,
        input: EstablishCallerSubject,
    ) -> Result<EstablishedCallerSubject, CallerIdentityError> {
        if self.hang_establish {
            hang().await;
        }
        let deadline = input.now_ms + 300_000;
        let subject = CallerSubjectRef::new(
            CallerSubjectKind::Human,
            CallerSubjectRefType::BcsEphemeral,
            "alice".into(),
            deadline,
        )
        .map_err(|_| CallerIdentityError::InvalidRequest)?;
        let proof = CallerExecutionToken::new(CallerProofFormat::Jwt, "sp".into(), deadline)
            .map_err(|_| CallerIdentityError::InvalidRequest)?;
        Ok(EstablishedCallerSubject { subject_ref: subject, subject_proof: proof, expires_at_ms: deadline })
    }
    async fn issue_execution(
        &self,
        input: IssueCallerExecution<'_>,
    ) -> Result<CallerExecutionToken, CallerIdentityError> {
        if self.hang_issue {
            hang().await;
        }
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

struct FastSender {
    calls: AtomicUsize,
}
#[async_trait]
impl CallerExecutionSendPort for FastSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _cred: &RedactedToken,
        command: SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(CallerDispatchAck::Accepted { control_revision: 0, request_digest: command.request_digest })
    }
}

struct HangingSender;
#[async_trait]
impl CallerExecutionSendPort for HangingSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _cred: &RedactedToken,
        _command: SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        hang().await;
        unreachable!("the stage budget must converge a hanging send as OutcomeUnknown")
    }
}

/// A handoff service double that parks the claim forever: proves the claim
/// stage itself is budgeted (distinct from `IdentityUnavailable`).
struct HangingHandoff;
#[async_trait]
impl HumanCallerDeliveryHandoffService for HangingHandoff {
    async fn reserve(
        &self,
        _scope: &bcs_service_api::application::caller_handoff::HandoffScope,
        _reserve_ttl_ms: i64,
        _now_ms: i64,
    ) -> Result<(), bcs_service_api::application::caller_handoff::HandoffError> {
        Err(bcs_service_api::application::caller_handoff::HandoffError::NotReserved)
    }
    async fn publish(
        &self,
        _scope: &bcs_service_api::application::caller_handoff::HandoffScope,
        _ingress: AuthenticatedHumanCallerIngress,
        _receipt: bcs_service_api::application::caller_handoff::QueuedAdmissionReceipt,
    ) -> Result<(), bcs_service_api::application::caller_handoff::HandoffError> {
        Err(bcs_service_api::application::caller_handoff::HandoffError::AlreadyPublished)
    }
    async fn release(
        &self,
        _scope: &bcs_service_api::application::caller_handoff::HandoffScope,
    ) -> Result<(), bcs_service_api::application::caller_handoff::HandoffError> {
        Ok(())
    }
    async fn claim(
        &self,
        _scope: &bcs_service_api::application::caller_handoff::HandoffScope,
        _claim: bcs_service_api::application::caller_handoff::HandoffClaim,
    ) -> Result<Arc<AuthenticatedHumanCallerIngress>, bcs_service_api::application::caller_handoff::HandoffError> {
        hang().await;
        unreachable!("the stage budget must converge a hanging claim as StageTimeout")
    }
    async fn identity_readiness(
        &self,
        _scope: &bcs_service_api::application::caller_handoff::HandoffScope,
    ) -> bcs_service_api::application::caller_handoff::HandoffIdentityReadiness {
        bcs_service_api::application::caller_handoff::HandoffIdentityReadiness::Published
    }
    async fn close(&self) {}
}

fn build(
    handoff: Arc<dyn HumanCallerDeliveryHandoffService>,
    gateway: Arc<dyn CallerContainerGatewayPort>,
    identity: Arc<dyn CallerIdentityPlugin>,
    sender: Arc<dyn CallerExecutionSendPort>,
    stage_timeout: Duration,
) -> CallerDispatchPipeline {
    let preparation = Arc::new(CallerContainerPreparation::new(gateway));
    CallerDispatchPipeline::new(handoff, preparation, identity, sender, 3_600_000)
        .with_stage_timeout(stage_timeout)
}

async fn dispatch(pipeline: &CallerDispatchPipeline) -> Result<CallerDispatchOutcome, CallerDispatchError> {
    pipeline
        .dispatch_once(&delivery_target(), &RedactedToken::new("svc"), facts(), now_clock())
        .await
}

// ---------------------------------------------------------------------------
// Convergence table (spec §3.3 + §4).
// ---------------------------------------------------------------------------

#[tokio::test]
async fn a_hanging_handoff_claim_converges_stage_timeout_never_identity_unavailable() {
    let pipeline = build(
        Arc::new(HangingHandoff),
        Arc::new(FastGateway),
        Arc::new(FastIdentity),
        Arc::new(FastSender { calls: AtomicUsize::new(0) }) as Arc<dyn CallerExecutionSendPort>,
        HAIRTRIGGER,
    );
    let error = dispatch(&pipeline).await.unwrap_err();
    // The claim's consumption is unprovable mid-hang: IdentityUnavailable
    // would assert a refusal it cannot. StageTimeout owns the conservative
    // convergence.
    assert_eq!(error, CallerDispatchError::StageTimeout { stage: "handoff_claim" });
}

#[tokio::test]
async fn a_hanging_container_ensure_converges_stage_timeout() {
    let pipeline = build(
        seeded_handoff().await,
        Arc::new(HangingGateway),
        Arc::new(FastIdentity),
        Arc::new(FastSender { calls: AtomicUsize::new(0) }) as Arc<dyn CallerExecutionSendPort>,
        HAIRTRIGGER,
    );
    let error = dispatch(&pipeline).await.unwrap_err();
    assert_eq!(error, CallerDispatchError::StageTimeout { stage: "container_ensure" });
}

#[tokio::test]
async fn a_hanging_subject_establishment_converges_stage_timeout() {
    let pipeline = build(
        seeded_handoff().await,
        Arc::new(FastGateway),
        Arc::new(HangingIdentity { hang_establish: true, hang_issue: false }),
        Arc::new(FastSender { calls: AtomicUsize::new(0) }) as Arc<dyn CallerExecutionSendPort>,
        HAIRTRIGGER,
    );
    let error = dispatch(&pipeline).await.unwrap_err();
    assert_eq!(error, CallerDispatchError::StageTimeout { stage: "establish_subject" });
}

#[tokio::test]
async fn a_hanging_proof_issuance_converges_stage_timeout() {
    let pipeline = build(
        seeded_handoff().await,
        Arc::new(FastGateway),
        Arc::new(HangingIdentity { hang_establish: false, hang_issue: true }),
        Arc::new(FastSender { calls: AtomicUsize::new(0) }) as Arc<dyn CallerExecutionSendPort>,
        HAIRTRIGGER,
    );
    let error = dispatch(&pipeline).await.unwrap_err();
    assert_eq!(error, CallerDispatchError::StageTimeout { stage: "issue_execution" });
}

#[tokio::test]
async fn a_hanging_send_converges_outcome_unknown_exactly_like_the_adapter_limit() {
    let pipeline = build(
        seeded_handoff().await,
        Arc::new(FastGateway),
        Arc::new(FastIdentity),
        Arc::new(HangingSender) as Arc<dyn CallerExecutionSendPort>,
        HAIRTRIGGER,
    );
    // The only honest evidence for an in-flight send: Unknown, keeping the
    // row's lane occupied for the late-callback/cancel arbiter — NOT Failed.
    let outcome = dispatch(&pipeline).await.unwrap();
    assert!(matches!(outcome, CallerDispatchOutcome::OutcomeUnknown));
}

#[tokio::test]
async fn fast_collaborators_inside_the_hairtrigger_stay_accepted() {
    // Regression guard for the budget itself: with a small-but-sane budget
    // every fast stage finishes and the dispatch stays Accepted — the wraps
    // must not mis-fire on healthy dispatches.
    let sender = Arc::new(FastSender { calls: AtomicUsize::new(0) });
    let pipeline = build(
        seeded_handoff().await,
        Arc::new(FastGateway),
        Arc::new(FastIdentity),
        sender.clone() as Arc<dyn CallerExecutionSendPort>,
        Duration::from_secs(30),
    );
    let outcome = dispatch(&pipeline).await.unwrap();
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
    assert_eq!(sender.calls.load(Ordering::SeqCst), 1);
    // Identity is one-shot: the fast path still consumed exactly one claim.
    let second = dispatch(&pipeline).await.unwrap_err();
    assert_eq!(second, CallerDispatchError::IdentityUnavailable);
}

// ---------------------------------------------------------------------------
// Delegate (worker) branch (spec §3.3 delegate counterparts): identity is
// the ROOT's registered runtime lineage, so the fixture mirrors
// delegate.rs's inline tests — a live RootLineageIndex entry — and pins the
// same stage-convergence table for dispatch_delegate_once. Fixes the
// e2e-side unreachable deep path: these are the bot-to-bot handoff lines
// the SQLite e2e stack never produces (e2e_coverage.sh P2 list).
// ---------------------------------------------------------------------------

use bcs_message_flow::caller_dispatch_pipeline::DelegateDispatchError;
use bcs_message_flow::caller_lineage::{RootLineage, RootLineageIndex};

fn delegate_facts() -> bcs_message_flow::caller_dispatch_pipeline::DelegateDispatchFacts {
    bcs_message_flow::caller_dispatch_pipeline::DelegateDispatchFacts {
        environment: "test".into(),
        target_bot_id: "bot-worker".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-w".into(),
        session_id: "session-1".into(),
        group_id: Some("group-1".into()),
        message_turn_id: "turn-1".into(),
        run_id: "task-run-1".into(),
        parent_run_id: "run-parent".into(),
        parent_attempt: 1,
        parent_bot_id: "bot-manager".into(),
        attempt: 1,
        dispatch_id: "dispatch-1".into(),
        request_digest: DIGEST.into(),
        not_after_ms: now_clock() + 280_000,
        caller_task: "delegated task text".into(),
        channel_actor_id: "human_alice".into(),
        channel_actor_name: "Alice".into(),
        control_revision: 0,
        delivery_commit: None,
        history: None,
        history_cutoff_seq: 0,
        shared_content_kind: None,
        // Execution purpose: the worker is caller-enabled, so the hop ENSURES
        // a caller container (spec §8.3). The ensure-timeout test depends on
        // this; DelegationOnly hops never ensure (spec §6.8).
        purpose: bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution,
    }
}

fn live_lineage() -> Arc<RootLineageIndex> {
    let index = Arc::new(RootLineageIndex::new(8));
    assert!(index.register(
        "run-parent",
        RootLineage {
            root_run_id: "root-run".into(),
            environment: "test".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: "turn-1".into(),
            group_id: "group-1".into(),
            session_id: "session-1".into(),
            history_cutoff_seq: 0,
            expires_at_ms: now_clock() + 120_000,
            identity_subject: "root-pseudonym".into(),
            identity_subject_type: CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "root-pseudonym".into(),
            parent_execution: false,
        },
        now_clock(),
    ));
    index
}

fn delegate_pipeline(
    gateway: Arc<dyn CallerContainerGatewayPort>,
    identity: Arc<dyn CallerIdentityPlugin>,
    sender: Arc<dyn CallerExecutionSendPort>,
) -> CallerDispatchPipeline {
    CallerDispatchPipeline::new(
        Arc::new(HumanCallerDeliveryHandoff::new(8)) as Arc<dyn HumanCallerDeliveryHandoffService>,
        Arc::new(CallerContainerPreparation::new(gateway)),
        identity,
        sender,
        3_600_000,
    )
    .with_root_lineage(live_lineage())
    .with_stage_timeout(HAIRTRIGGER)
}

#[tokio::test]
async fn a_hanging_delegate_ensure_converges_stage_timeout() {
    let pipeline = delegate_pipeline(
        Arc::new(HangingGateway),
        Arc::new(FastIdentity),
        Arc::new(FastSender { calls: AtomicUsize::new(0) }) as Arc<dyn CallerExecutionSendPort>,
    );
    let error = pipeline
        .dispatch_delegate_once(&delivery_target(), &RedactedToken::new("svc"), delegate_facts(), now_clock())
        .await
        .unwrap_err();
    assert_eq!(error, DelegateDispatchError::StageTimeout { stage: "container_ensure" });
}

#[tokio::test]
async fn a_hanging_delegate_issuance_converges_stage_timeout() {
    let pipeline = delegate_pipeline(
        Arc::new(FastGateway),
        Arc::new(HangingIdentity { hang_establish: false, hang_issue: true }),
        Arc::new(FastSender { calls: AtomicUsize::new(0) }) as Arc<dyn CallerExecutionSendPort>,
    );
    let error = pipeline
        .dispatch_delegate_once(&delivery_target(), &RedactedToken::new("svc"), delegate_facts(), now_clock())
        .await
        .unwrap_err();
    assert_eq!(error, DelegateDispatchError::StageTimeout { stage: "issue_execution" });
}

#[tokio::test]
async fn a_hanging_delegate_send_converges_outcome_unknown() {
    // Same evidence rule as the root branch: an in-flight delegated send is
    // Unknown, the row keeps its lane — the delegation identity is consumed
    // either way.
    let pipeline = delegate_pipeline(
        Arc::new(FastGateway),
        Arc::new(FastIdentity),
        Arc::new(HangingSender) as Arc<dyn CallerExecutionSendPort>,
    );
    let outcome = pipeline
        .dispatch_delegate_once(&delivery_target(), &RedactedToken::new("svc"), delegate_facts(), now_clock())
        .await
        .unwrap();
    assert!(matches!(outcome, CallerDispatchOutcome::OutcomeUnknown));
}

#[tokio::test]
async fn a_fast_delegate_dispatch_stays_accepted() {
    // The wraps must not mis-fire a healthy Execution delegation: every
    // stage inside the budget, exactly one container ensure (spec §6.8
    // review-VII P1 counting rule) and Accepted.
    let sender = Arc::new(FastSender { calls: AtomicUsize::new(0) });
    let pipeline = CallerDispatchPipeline::new(
        Arc::new(HumanCallerDeliveryHandoff::new(8)) as Arc<dyn HumanCallerDeliveryHandoffService>,
        Arc::new(CallerContainerPreparation::new(Arc::new(FastGateway))),
        Arc::new(FastIdentity),
        sender.clone() as Arc<dyn CallerExecutionSendPort>,
        3_600_000,
    )
    .with_root_lineage(live_lineage())
    .with_stage_timeout(Duration::from_secs(30));
    let outcome = pipeline
        .dispatch_delegate_once(&delivery_target(), &RedactedToken::new("svc"), delegate_facts(), now_clock())
        .await
        .unwrap();
    assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
    assert_eq!(sender.calls.load(Ordering::SeqCst), 1);
}

