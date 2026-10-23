//! Protected caller dispatch pipeline (spec §7.5.1/§7.8, P2-B fourth slice).
//!
//! Composes the P2-B components into the single seam the delivery runtime
//! will call once StartSend committed the attempt: claim the one-shot
//! handoff identity → ensure+validate the container → issue the execution
//! proof → protected send → structured dispatch outcome. Nothing here
//! touches Managed state, DB or the ordinary transport: convergence and
//! retry policy stay with the runtime caller (this keeps the pipeline
//! deterministic, clock-explicit and convention-testable).
//!
//! Fail-closed invariants:
//! - every stage failure consumes the identity: the claim is one-shot and
//!   NEVER returned, so a failed preparation loses the identity — the human
//!   re-submits (spec B5/B6);
//! - identity deadline caps every later stage's effective window; the
//!   pipeline re-reads the explicit clock after each await-relevant stage;
//! - the pipeline never fabricates a digest or deadline: it carries what the
//!   trusted producer committed (the runtime caller supplies them from the
//!   committed Managed row).

use std::sync::Arc;

use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_service_api::application::caller_container::{
    CallerContainerPreparation, CallerPreparationError, PrepareOutcome,
};
use bcs_service_api::application::caller_execution::{HandoffClaim, HandoffScope, HumanCallerDeliveryHandoffService};
use bcs_service_api::application::caller_identity::CallerExecutionToken as ProofToken;
use bcs_service_api::port::{
    CallerContainerMinimum, CallerDispatchAck, CallerDispatchAssociation, CallerExecutionSendPort, CallerOpaqueRef, ContainerRunAssociation,
    EnsureCallerContainer, EnsureCreateMode,
    SendCallerExecutionCommand,
};
use bcs_caller_identity_api::{CallerIdentityError, CallerIdentityPlugin, IssueCallerExecution, IssueCallerExecutionSource};

/// Canonical v1 request digest (wire contract "commit_caller_dispatch").
/// Pure SHA-256, no I/O: domain separator, then length-prefixed UTF-8 fields
/// (environment, provider_id, provider_bot_ref, target_bot_id, session_id,
/// run_id, dispatch_id, group_id, message_turn_id, text), then attempt,
/// control_revision and not_after_ms big-endian. Reads no identity material.
pub fn canonical_request_digest(
    environment: &str,
    provider_id: &str,
    provider_bot_ref: &str,
    target_bot_id: &str,
    session_id: &str,
    run_id: &str,
    dispatch_id: &str,
    group_id: Option<&str>,
    message_turn_id: &str,
    text: &str,
    attempt: u32,
    control_revision: u64,
    not_after_ms: i64,
) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(b"bcs.caller.root-h2a.dispatch.v1\0");
    for field in [
        environment,
        provider_id,
        provider_bot_ref,
        target_bot_id,
        session_id,
        run_id,
        dispatch_id,
        group_id.unwrap_or(""),
        message_turn_id,
        text,
    ] {
        hasher.update((field.len() as u64).to_be_bytes());
        hasher.update(field.as_bytes());
    }
    hasher.update(attempt.to_be_bytes());
    hasher.update(control_revision.to_be_bytes());
    hasher.update((not_after_ms as u64).to_be_bytes());
    let digest = hasher.finalize();
    let mut encoded = String::with_capacity(71);
    encoded.push_str("sha256:");
    for byte in digest {
        encoded.push_str(&format!("{byte:02x}"));
    }
    encoded
}

/// Where the typed caller dispatch commit must CAS-persist the canonical
/// digest: the committed StartSend row identity and the state version the
/// commit is allowed to replace.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliveryCommitOrigin {
    pub delivery_id: String,
    pub expected_state_version: u64,
}

/// The observe fan-out assembly for this pipeline (spec §6.4/L226 wiring):
/// the shared runtime-only observe index (seeded from every successful
/// ensure readback) plus the composition's injector factory for the CURRENT
/// dispatch's own Provider target — the fan-out observes are the OTHER
/// caller containers of the SAME Bot, which live behind the same Provider
/// webhook as the executing container. `None` (not attached) keeps pure
/// H2A shapes with no observe sync.
#[derive(Clone)]
pub struct ObserveSyncAssembly {
    pub groups: Arc<dyn bcs_service_api::GroupCoreService>,
    pub sessions: Option<Arc<dyn bcs_service_api::SessionManagementService>>,
    pub delivery_term: Option<Arc<std::sync::atomic::AtomicU64>>,
    pub index: Arc<crate::observe_index::ObserveTargetIndex>,
    pub injector_factory: Arc<dyn bcs_service_api::port::ObserveInjectorFactory>,
}

/// Facts the runtime committed at StartSend; nothing here re-derives them.
#[derive(Debug, Clone)]
pub struct CommittedDispatchFacts {
    pub environment: String,
    pub target_bot_id: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub session_id: String,
    pub group_id: Option<String>,
    pub message_turn_id: String,
    pub root_run_id: String,
    pub run_id: String,
    pub attempt: u32,
    pub dispatch_id: String,
    /// `sha256:`+64 hex committed by the trusted producer (with the Managed
    /// CAS); sent verbatim, never recomputed here.
    pub request_digest: String,
    /// Not-after committed with the digest; identity window already enters
    /// this value upstream (it only shrinks here).
    pub not_after_ms: i64,
    /// When the pipeline carries a managed commit handle, this origin points
    /// at the delivery row whose semantic projection must receive the
    /// canonical digest via the typed `commit_caller_dispatch` CAS before
    /// any protected send. The pipeline is then the trusted digest producer
    /// and facts.request_digest is recomputed (never trusted as preexisting).
    pub delivery_commit: Option<DeliveryCommitOrigin>,
    /// Caller task text already protected-projected by admission.
    pub caller_task: String,
    pub control_revision: u64,
    /// Shared-history header for group caller work (spec §6.8
    /// `execution.history`): fixed by the runtime from the row's cutoff and
    /// its bounded-context selection. None on history-less tasks.
    pub history: Option<bcs_service_api::port::CallerExecutionHistory>,
    /// Shared-stream content classification (spec §6.4/L225): the admitted
    /// row's flow kind decides the eligibility surface at fan-out
    /// (`shared_kind_for_delivery`). None = unclassified content that never
    /// broadcasts; Some(kind) fans out with per-observer eligibility.
    pub shared_content_kind: Option<bcs_domain::shared_stream::SharedContentKind>,
}

/// Structured outcome for the runtime to converge on. `identity_consumed` is
/// always true once a claim fired: retries never see identity again.
#[derive(Debug)]
pub enum CallerDispatchOutcome {
    /// Protected send accepted by the Provider/Gateway (受理, not success).
    Accepted,
    /// Provider explicitly rejected: terminal for this dispatch attempt.
    Rejected { error_code: String, retryable: bool },
    /// Transport outcome unknown after the network attempt started.
    OutcomeUnknown,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum CallerDispatchError {
    /// No identity parked for this scope (admission never published, or
    /// already claimed/expired). Fail closed; never owner-dispatch.
    #[error("caller identity unavailable")]
    IdentityUnavailable,
    /// The identity window ended while the pipeline was in flight.
    #[error("caller identity expired during dispatch")]
    IdentityExpired,
    /// Container preparation refused (not ready, linkage corrupt, isolation
    /// below requirement). Identity already consumed by the claim.
    #[error("caller container preparation failed")]
    PreparationFailed,
    /// The identity plugin refused to issue (gate: bad input, expired
    /// subject, misconfigured keys).
    #[error("caller identity plugin refused to issue")]
    IssueFailed,
    /// Structural input invalid (attempt 0, empty facts, inverted clocks).
    #[error("invalid caller dispatch input")]
    InvalidInput,
    /// The identity plugin refused the subject establishment (the
    /// ingress→trusted-subject exchange failed — trusted ingress refused,
    /// a misconfigured profile, or a trust window that already ended).
    /// Identity already consumed by the claim — the human re-submits.
    #[error("caller subject establishment failed")]
    EstablishFailed,
    /// The typed `commit_caller_dispatch` CAS refused the canonical digest
    /// (association drift, stale version, status divergence). Network has
    /// already been touched at preparation, so callers must converge this
    /// conservatively; the one-shot identity is consumed.
    #[error("caller dispatch commit refused")]
    CommitFailed,
}

/// The runtime-facing seam. All collaborators are injected ports/services;
/// composition root owns their assembly.
#[derive(Clone)]
pub struct CallerDispatchPipeline {
    handoff: Arc<dyn HumanCallerDeliveryHandoffService>,
    preparation: Arc<CallerContainerPreparation>,
    identity: Arc<dyn CallerIdentityPlugin>,
    registry: Option<Arc<dyn bcs_service_api::core::BotRegistryCoreService>>,
    sender: Arc<dyn CallerExecutionSendPort>,
    /// Optional typed commit CAS service (the same Managed instance the
    /// admission seam uses). Present = the pipeline computes the canonical
    /// digest after container preparation and persists it via
    /// `commit_caller_dispatch` before any protected send.
    managed_commit:
        Option<Arc<dyn bcs_service_api::application::message_delivery::ManagedMessageDeliveryService>>,
    /// Optional observe fan-out assembly (spec §6.4/L226): present = the
    /// pipeline seeds the shared observe index from every successful ensure
    /// readback and, after an ACCEPTED protected send in a group, syncs the
    /// same message observe-only to the other caller-enabled members.
    observe: Option<ObserveSyncAssembly>,
    /// Optional root-lineage registry (spec §8.3, L243-A): present = an
    /// ACCEPTED group dispatch registers the run's delegation-only
    /// lineage facts (root/turn/group/cutoff/identity window) for the
    /// coordination derivation gate. Runtime-only; absent keeps the
    /// composition without derivation.
    root_lineage: Option<Arc<crate::caller_lineage::RootLineageIndex>>,
    /// Optional binding translator: when the prepare readback carries a
    /// technical binding ref, the runtime may pass it into the proof via
    /// this constructor from the opaque ref (never materializing it).
    #[allow(dead_code)]
    container_ttl_ms: i64,
    /// §6.8 same-dispatch barrier redrive (review-V B3.2): the backfill
    /// port + the parked inputs + the backoff ladder. The parks carry
    /// runtime-only identity material (the ORIGINAL issued command), so
    /// they live exactly with this in-process pipeline and die with it —
    /// the durable HistoryBarrier marker keeps the row parked across
    /// restarts and the run-deadline control converges what the loop
    /// cannot.
    history_messages: Option<Arc<dyn bcs_service_api::port::repo::MessageRepoPort>>,
    history_backfill: Option<Arc<dyn bcs_service_api::port::HistoryBackfillPort>>,
    barrier_parks: Arc<std::sync::Mutex<std::collections::HashMap<String, barrier::BarrierPark>>>,
    barrier_backoff_ms: Arc<Vec<u64>>,
    /// Review-VI P1 master-term fence: a monotonically bumped epoch token
    /// the bootstrap bumps at every delivery-master term boundary. Every
    /// detached effect this pipeline spawns (barrier redrive loops, observe
    /// fan-outs, accepted-effect lineage writes) captures the token at
    /// spawn and verifies it is STILL current before touching the wire or
    /// the indexes — a demoted/old-term task stops instead of sending or
    /// re-registering into the freshly sealed incarnation.
    delivery_term: Option<Arc<std::sync::atomic::AtomicU64>>,
}

impl CallerDispatchPipeline {
    pub fn new(
        handoff: Arc<dyn HumanCallerDeliveryHandoffService>,
        preparation: Arc<CallerContainerPreparation>,
        identity: Arc<dyn CallerIdentityPlugin>,
        sender: Arc<dyn CallerExecutionSendPort>,
        container_ttl_ms: i64,
    ) -> Self {
        Self {
            handoff,
            preparation,
            identity,
            registry: None,
            sender,
            managed_commit: None,
            observe: None,
            root_lineage: None,
            history_messages: None,
            history_backfill: None,
            barrier_parks: Arc::new(std::sync::Mutex::new(std::collections::HashMap::new())),
            barrier_backoff_ms: Arc::new(barrier::default_backoff()),
            delivery_term: None,
            container_ttl_ms,
        }
    }

    /// Production uses the authoritative Bot registry; no bot_id/owner fallback.
    pub fn with_bot_registry(mut self, registry: Arc<dyn bcs_service_api::core::BotRegistryCoreService>) -> Self {
        self.registry = Some(registry);
        self
    }

    async fn target_agent_code(&self, bot_id: &str) -> Option<String> {
        self.registry.as_ref()?.get_agent_credentials(bot_id).await?
            .agent_code.filter(|code| !code.trim().is_empty())
    }

    /// Enables the typed digest commit step (wire contract §7.5.1). The
    /// composition root passes the SAME Managed service the admission seam
    /// uses. With this handle present, `dispatch_once` recomputes the
    /// canonical digest after preparation and CAS-persists it into the
    /// caller row's projection before sending.
    pub fn with_managed_commit(
        mut self,
        managed: Arc<dyn bcs_service_api::application::message_delivery::ManagedMessageDeliveryService>,
    ) -> Self {
        self.managed_commit = Some(managed);
        self
    }

    /// Attaches the observe fan-out assembly (spec §6.4/L226): the shared
    /// observe index this pipeline seeds on every successful ensure, and
    /// the composition's injector factory for the dispatch's own Provider
    /// target (the same-bot fan-out rides the same webhook/credential).
    pub fn with_observe_sync(mut self, assembly: ObserveSyncAssembly) -> Self {
        self.observe = Some(assembly);
        self
    }

    /// Attaches the root-lineage registry (spec §8.3, L243-A): an ACCEPTED
    /// group dispatch publishes this run's delegation-only lineage for
    /// the coordination derivation gate — the ONLY trusted producer.
    pub fn with_root_lineage(mut self, index: Arc<crate::caller_lineage::RootLineageIndex>) -> Self {
        self.root_lineage = Some(index);
        self
    }

    /// Attaches the §6.8 history-backfill port (review-V B3.2): a
    /// retryable HISTORY_NOT_READY refusal then parks the ORIGINAL send
    /// inputs and drives the barrier loop (complete → same-dispatch
    /// resend → row convergence). Without the port the refusal keeps the
    /// plain parked semantics (marker + deadline control) — the runtime
    /// classification is unchanged either way.
    pub fn with_history_backfill(
        mut self,
        port: Arc<dyn bcs_service_api::port::HistoryBackfillPort>,
    ) -> Self {
        self.history_backfill = Some(port);
        self
    }

    /// Canonical batch body source for a frozen history manifest.
    pub fn with_history_messages(mut self, repo: Arc<dyn bcs_service_api::port::repo::MessageRepoPort>) -> Self {
        self.history_messages = Some(repo);
        self
    }

    /// Review-VI P1: attach the master-term token this pipeline's detached
    /// effects fence against. Absent (test/dev compositions) every effect
    /// runs un-fenced — production wiring always supplies it.
    pub fn with_delivery_term(mut self, token: Arc<std::sync::atomic::AtomicU64>) -> Self {
        self.delivery_term = Some(token);
        self
    }

    /// Whether the captured term is still the CURRENT master term. A None
    /// token composition is "fence absent" only when the capture is also None.
    pub fn delivery_term_is_current(&self, captured: Option<u64>) -> bool {
        self.delivery_term.as_ref().map(|token| token.load(std::sync::atomic::Ordering::SeqCst)) == captured
    }

    /// Test-only: shrink the redrive backoff ladder so contracts observe
    /// convergence in milliseconds instead of the production ladder.
    #[doc(hidden)]
    pub fn with_barrier_backoff_for_tests(mut self, ladder_ms: Vec<u64>) -> Self {
        if !ladder_ms.is_empty() {
            self.barrier_backoff_ms = Arc::new(ladder_ms);
        }
        self
    }

    /// §7.2.1 delegation-only root registration (review batch-3 §3.1): an
    /// ORDINARY Started row carrying the trusted `caller_lineage` marker
    /// (a caller-DISABLED routed target of a lease-admitted Human turn in
    /// a caller group) registers its Owner delegation-only association
    /// here. The identity comes from the handoff slot the admission parked
    /// for this exact scope and is consumed by the claim — one-way, like
    /// every identity in this pipeline. Deliberately BEST-EFFORT: the
    /// ordinary Owner execution never depends on the association (a later
    /// `bcs_assign_task` from this run simply refuses to inherit when the
    /// registration is missing), so every failure logs and returns
    /// instead of failing the row. No container ensure, no proof issued:
    /// the Owner Bot's own tools can never consume this association as
    /// Human execution material.
    pub async fn register_delegation_only_lineage(
        &self,
        started: &bcs_domain::message_delivery::PersistedMessageDelivery,
        now_ms: i64,
    ) {
        let at_term = self.delivery_term.as_ref().map(|v| v.load(std::sync::atomic::Ordering::SeqCst));
        let Some(index) = self.root_lineage.as_ref() else {
            tracing::warn!(
                delivery_id = %started.delivery_id,
                "delegation-only lineage marker without a lineage registry (CALLER_LINEAGE_UNSUPPORTED); derivation stays refused"
            );
            return;
        };
        let Some(run_id) = started.run_id.clone().filter(|run| !run.is_empty()) else {
            tracing::warn!(
                delivery_id = %started.delivery_id,
                "delegation-only lineage marker without a committed run id; derivation stays refused"
            );
            return;
        };
        if now_ms <= 0 {
            return;
        }
        // The scope mirrors the admission's make_scope exactly (the same
        // env resolution, session, group, canonical message id and target).
        let scope = HandoffScope {
            env: bcs_config::resolve_env_str(),
            session_id: started.session_id.clone(),
            group_id: Some(started.group_id.clone()),
            source_message_id: started.source_message_id.clone(),
            target_bot_id: started.target_bot_id.clone(),
        };
        // Idempotence for ordinary retries: an AlreadyClaimed slot with the
        // association already registered is a retry's converged end state.
        if index.current(&run_id, now_ms).is_some() {
            tracing::debug!(
                run_id = %run_id,
                "delegation-only lineage already registered for this run"
            );
            return;
        }
        let claim = HandoffClaim::new(
            started.delivery_id.clone(),
            0,
            Some(run_id.clone()),
            started.attempt_no.max(1),
            now_ms,
        );
        let ingress = match self.handoff.claim(&scope, claim).await {
            Ok(ingress) => ingress,
            Err(bcs_service_api::application::caller_execution::HandoffError::AlreadyClaimed) => {
                // The identity is gone but the association never landed:
                // hard loss, derivation refused, ordinary run unaffected.
                tracing::warn!(
                    run_id = %run_id,
                    delivery_id = %started.delivery_id,
                    "delegation-only identity already consumed without a registered lineage; derivation stays refused"
                );
                return;
            }
            Err(_) => {
                tracing::warn!(
                    run_id = %run_id,
                    delivery_id = %started.delivery_id,
                    "delegation-only identity unavailable for this scope; derivation stays refused"
                );
                return;
            }
        };
        if ingress.is_expired(now_ms) {
            tracing::warn!(
                run_id = %run_id,
                delivery_id = %started.delivery_id,
                "delegation-only identity expired before registration; derivation stays refused"
            );
            return;
        }
        // §7.2.1 association facts: this Human, this run/attempt, the
        // group/session/turn boundary, the admission-fixed history ceiling
        // (spec §6.8: the cutoff ceiling binds at admission — the same
        // fact the root manifest would carry) and the trusted identity
        // window that bounds all later derivation.
        let established = match self.identity.establish_subject(bcs_caller_identity_api::EstablishCallerSubject {
            ingress: (*ingress).clone(), now_ms,
        }).await {
            Ok(value) => value,
            Err(_) => return,
        };
        let registered = index.register_in_term(self.delivery_term.as_deref(), at_term, || index.register(
            &run_id,
            crate::caller_lineage::RootLineage {
                root_run_id: run_id.clone(),
                environment: started.env.clone(),
                delegation_depth: 0,
                attempt: started.attempt_no.max(1),
                message_turn_id: started.source_message_id.clone(),
                group_id: started.group_id.clone(),
                session_id: started.session_id.clone(),
                history_cutoff_seq: started.source_session_seq.max(0) as u64,
                expires_at_ms: ingress.expires_at_ms().min(established.expires_at_ms),
                // The same delegation-only material the caller root
                // registers (§8.3): the claimed ingress's actor id — the
                // delegate dispatch issues bounded proofs for THIS
                // subject, never the raw principal, and the Owner's own
                // tools never see it.
                identity_subject: established.subject_ref.expose_ref().to_string(),
                identity_subject_type: established.subject_ref.ref_type(),
                delegate_subject: ingress.actor().staff_no.clone(),
                parent_execution: false,
            },
            now_ms,
        )).unwrap_or(false);
        if !registered {
            tracing::warn!(
                run_id = %run_id,
                delivery_id = %started.delivery_id,
                "delegation-only lineage refused by the registry (capacity); derivation stays refused until re-registration"
            );
        }
    }

    /// True when the pipeline performs the typed digest commit itself; the
    /// runtime uses this to drop the preexisting-digest precondition.
    pub fn managed_commit_wired(&self) -> bool {
        self.managed_commit.is_some()
    }

    /// Run one protected dispatch attempt for a committed caller dispatch.
    /// `now_ms` clocks pre-claim checks; each network stage result is re
    /// checked against the committed not-after before proceeding.
    pub async fn dispatch_once(
        &self,
        target: &BotDeliveryTarget,
        service_credential: &RedactedToken,
        facts: CommittedDispatchFacts,
        now_ms: i64,
    ) -> Result<CallerDispatchOutcome, CallerDispatchError> {
        let at_term = self.delivery_term.as_ref().map(|token| token.load(std::sync::atomic::Ordering::SeqCst));
        // ---- Input validity (before touching the identity). ----
        if facts.attempt == 0
            || facts.run_id.is_empty()
            || facts.dispatch_id.is_empty()
            || facts.root_run_id.is_empty()
            || facts.message_turn_id.is_empty()
            || facts.session_id.is_empty()
            || facts.environment.is_empty()
            || facts.target_bot_id.is_empty()
            || facts.caller_task.is_empty()
            || (facts.delivery_commit.is_none() && facts.request_digest.len() != 71)
            || now_ms <= 0
        {
            return Err(CallerDispatchError::InvalidInput);
        }

        // ---- One-shot identity claim (consumes on any later failure). ----
        let scope = HandoffScope {
            env: facts.environment.clone(),
            session_id: facts.session_id.clone(),
            group_id: facts.group_id.clone(),
            source_message_id: facts.message_turn_id.clone(),
            target_bot_id: facts.target_bot_id.clone(),
        };
        let claim = HandoffClaim::new(
            facts.dispatch_id.clone(),
            0,
            Some(facts.run_id.clone()),
            facts.attempt,
            now_ms,
        );
        tracing::debug!(dispatch_id = %facts.dispatch_id, stage = "handoff_claim", "caller protected dispatch stage");
        let ingress = match self.handoff.claim(&scope, claim).await {
            Ok(ingress) => ingress,
            // A parked identity whose window already ended is a hard loss
            // (B5/B6: the human re-submits); everything else means no
            // identity was ever available for this scope.
            Err(bcs_service_api::application::caller_execution::HandoffError::Expired) => {
                return Err(CallerDispatchError::IdentityExpired)
            }
            Err(_) => return Err(CallerDispatchError::IdentityUnavailable),
        };
        // The parked identity's window must cover the whole protected path.
        if ingress.is_expired(now_ms) {
            return Err(CallerDispatchError::IdentityExpired);
        }
        let identity_deadline = ingress.expires_at_ms();
        let effective_not_after = facts.not_after_ms.min(identity_deadline);
        if effective_not_after <= now_ms {
            return Err(CallerDispatchError::IdentityExpired);
        }
        // The dispatching caller's stable subject: identifies THIS
        // Human×Bot×session container in the observe index (the same key
        // the ensure request's subject ref carries).
        let caller_subject = ingress.actor().staff_no.clone();

        // ---- Container ensure (at most one). ----
        let ensure = EnsureCallerContainer {
            caller_subject_ref: opaque_from(ingress.actor()),
            target_bot_id: facts.target_bot_id.clone(),
            session_id: facts.session_id.clone(),
            environment: facts.environment.clone(),
            minimum_isolation: CallerContainerMinimum::CallerContainer,
            idempotency_key: format!(
                "ensure:{}:{}:{}",
                facts.target_bot_id, facts.session_id, facts.run_id
            ),
            run: ContainerRunAssociation {
                run_id: facts.run_id.clone(),
                root_run_id: facts.root_run_id.clone(),
                parent_run_id: None,
                message_turn_id: facts.message_turn_id.clone(),
                // The child inherits the root's cutoff ceiling (spec §6.8):
                // lineage binds the boundary — never the current watermark.
                history_cutoff_seq: facts
                    .history
                    .as_ref()
                    .map(|history| history.history_cutoff_seq)
                    .unwrap_or(0),
            },
            create_mode: EnsureCreateMode::Lazy,
            container_ttl_ms: self.container_ttl_ms,
            deadline_ms: effective_not_after,
            now_ms,
        };
        tracing::debug!(dispatch_id = %facts.dispatch_id, stage = "container_prepare", "caller protected dispatch stage");
        let prepared = match self.preparation.prepare(target, service_credential, ensure, now_ms).await {
            Ok(PrepareOutcome::Prepared { descriptor, effective_deadline_ms }) => {
                // Container expiry only shrinks the effective window.
                let effective = effective_deadline_ms.min(effective_not_after);
                if effective <= now_ms {
                    return Err(CallerDispatchError::IdentityExpired);
                }
                (descriptor, effective)
            }
            Err(CallerPreparationError::Gateway { unknown_outcome: true, .. }) => {
                // Ambiguous container preparation after a network attempt:
                // executing further is premature; identity is consumed.
                return Err(CallerDispatchError::PreparationFailed);
            }
            Err(_) => return Err(CallerDispatchError::PreparationFailed),
        };
        let (descriptor, effective_deadline) = prepared;
        // Observe index seeding (spec §6.9/§10.8 replacement rule): every
        // successful ensure readback publishes THIS caller container's live
        // observe reference — a fresh one replaces the generation AND
        // reopens a paused container; another Human's container of the same
        // Bot is a separate entry this never touches. A capacity refusal or
        // corrupted material never fails the dispatch itself: this
        // container's observer sync pauses until its next trusted ensure
        // (the index's designed degradation).
        if let Some(observe) = self.observe.as_ref() {
            match observe.index.replace_from_descriptor(
                &caller_subject,
                &facts.target_bot_id,
                &facts.session_id,
                &descriptor,
                now_ms,
            ) {
                Ok(_) => {}
                Err(()) => {
                    tracing::warn!(
                        bot_id = %facts.target_bot_id,
                        session_id = %facts.session_id,
                        "observe index refused the ensure readback; observer sync pauses until next ensure"
                    );
                }
            }
        }

        // ---- Subject establishment, then proof issuance (plugin). ----
        // Spec §10.13.2 / review batch-2: the PLUGIN owns the
        // ingress→trusted-subject exchange (the JWT default derives the
        // stable pseudonym; external plugins establish their own opaque handle).
        // The dispatch issues for the ESTABLISHED subject — never the raw
        // display identity — and the established window caps every later
        // stage (only shrinks, never extends).
        tracing::debug!(dispatch_id = %facts.dispatch_id, stage = "establish_subject", "caller protected dispatch stage");
        let established = self
            .identity
            .establish_subject(bcs_caller_identity_api::EstablishCallerSubject {
                ingress: (*ingress).clone(),
                now_ms,
            })
            .await
            .map_err(|_| CallerDispatchError::EstablishFailed)?;
        if established.expires_at_ms <= now_ms {
            return Err(CallerDispatchError::IdentityExpired);
        }
        let identity_deadline = identity_deadline.min(established.expires_at_ms);
        if identity_deadline <= now_ms {
            return Err(CallerDispatchError::IdentityExpired);
        }
        let identity_subject = established.subject_ref.expose_ref().to_string();
        let identity_subject_type = established.subject_ref.ref_type();
        let subject_ref = established.subject_ref;
        let issue_input = IssueCallerExecution {
            staff_no: opaque_from(ingress.actor()),
            agent_code: self.target_agent_code(&facts.target_bot_id).await,
            sandbox_container_id: descriptor.sandbox_container_id.clone(),
            subject: subject_ref,
            run: bcs_caller_identity_api::CallerRunBinding {
                run_id: facts.run_id.clone(),
                root_run_id: facts.root_run_id.clone(),
                parent_run_id: None,
                message_turn_id: Some(facts.message_turn_id.clone()),
                attempt: facts.attempt,
            },
            scope: bcs_caller_identity_api::CallerProofScope {
                env: facts.environment.clone(),
                target_bot_id: facts.target_bot_id.clone(),
                group_id: facts.group_id.clone(),
                session_id: facts.session_id.clone(),
            },
            delegation: bcs_caller_identity_api::CallerDelegationBounds { depth: 0, max_depth: if facts.group_id.is_some() { 2 } else { 0 } },
            purpose: bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution,
            not_after_ms: effective_deadline,
            isolation: descriptor
                .binding_ref
                .as_ref()
                .map(|binding| bcs_caller_identity_api::CallerIsolationConstraint {
                    binding_ref: binding.expose().to_string(),
                    generation: descriptor.runtime_generation,
                }),
            source: IssueCallerExecutionSource::HumanOriginHandle,
            now_ms,
        };
        tracing::debug!(dispatch_id = %facts.dispatch_id, stage = "issue_execution", "caller protected dispatch stage");
        let proof: ProofToken = match self.identity.issue_execution(issue_input).await {
            Ok(token) => token,
            Err(CallerIdentityError::TokenExpired) => return Err(CallerDispatchError::IdentityExpired),
            Err(_) => return Err(CallerDispatchError::IssueFailed),
        };
        if proof.expires_at_ms() < effective_deadline {
            // Plugin issued shorter than needed: still legal (never extends),
            // send uses the proof's own window; if already past, hard fail.
            if proof.expires_at_ms() <= now_ms {
                return Err(CallerDispatchError::IdentityExpired);
            }
        }

        tracing::debug!(dispatch_id = %facts.dispatch_id, stage = "digest_commit", "caller protected dispatch stage");
        // ---- Canonical digest + typed commit CAS (wire contract §7.5.1). ----
        // When the dispatch carries a commit origin and the pipeline holds the
        // managed handle, the pipeline IS the trusted digest producer: it
        // recomputes the canonical digest over the committed facts and the
        // effective window, CAS-persists it into the caller row's semantic
        // projection, and only then performs the protected send. Any commit
        // refusal converges this stage conservatively (network already hit at
        // preparation) — identity consumed, never owner-dispatched.
        let mut committed_digest = facts.request_digest.clone();
        if let (Some(managed), Some(origin)) =
            (self.managed_commit.as_ref(), facts.delivery_commit.as_ref())
        {
            committed_digest = canonical_request_digest(
                &facts.environment,
                &facts.provider_id,
                &facts.provider_bot_ref,
                &facts.target_bot_id,
                &facts.session_id,
                &facts.run_id,
                &facts.dispatch_id,
                facts.group_id.as_deref(),
                &facts.message_turn_id,
                &facts.caller_task,
                facts.attempt,
                facts.control_revision,
                effective_deadline,
            );
            let commit_command =
                bcs_service_api::application::message_delivery::CommitCallerDispatchCommand {
                    delivery_id: origin.delivery_id.clone(),
                    expected_state_version: origin.expected_state_version,
                    now_ms: now_ms,
                    dispatch: CallerDispatchAssociation {
                        environment: facts.environment.clone(),
                        target_bot_id: facts.target_bot_id.clone(),
                        provider_id: facts.provider_id.clone(),
                        provider_bot_ref: facts.provider_bot_ref.clone(),
                        session_id: facts.session_id.clone(),
                        run_id: facts.run_id.clone(),
                        attempt: facts.attempt,
                        dispatch_id: facts.dispatch_id.clone(),
                    },
                    commit: bcs_service_api::application::message_delivery::CallerDispatchCommit {
                        request_digest: committed_digest.clone(),
                        not_after_ms: effective_deadline,
                        control_revision: facts.control_revision,
                    },
                };
            managed
                .commit_caller_dispatch(commit_command)
                .await
                .map_err(|_| CallerDispatchError::CommitFailed)?;
        }

        // ---- Protected send. ----
        let command = SendCallerExecutionCommand {
            association: CallerDispatchAssociation {
                environment: facts.environment.clone(),
                target_bot_id: facts.target_bot_id.clone(),
                provider_id: facts.provider_id.clone(),
                provider_bot_ref: facts.provider_bot_ref.clone(),
                session_id: facts.session_id.clone(),
                run_id: facts.run_id.clone(),
                attempt: facts.attempt,
                dispatch_id: facts.dispatch_id.clone(),
            },
            group_id: facts.group_id.clone(),
            text: facts.caller_task.clone(),
            caller_task: facts.caller_task.clone(),
            message_turn_id: facts.message_turn_id.clone(),
            root_run_id: facts.root_run_id.clone(),
            caller_subject_ref: opaque_from_ref(ingress.actor()),
            subject_kind: "human".to_string(),
            caller_context_expires_at_ms: identity_deadline,
            execution_token: opaque_from_proof(&proof),
            // Review-V P2-5: the wire carries the PROOF's own lifetime —
            // the plugin may issue shorter than the effective window and
            // no downstream may be told a longer token validity.
            execution_token_expires_at_ms: proof.expires_at_ms(),
            proof_format: proof_format_string(proof.format()),
            proof_type: self.identity.proof_type().to_string(),
            control_revision: facts.control_revision,
            not_after_ms: effective_deadline,
            request_digest: committed_digest.clone(),
            history: facts.history.clone(),
            cross_group_lineage: None,
            callback_timeout_ms: 30_000,
            now_ms,
        };
        // §6.8 B3.2: the ORIGINAL command stays alive at the barrier arm —
        // the send takes a clone, the park keeps the binding.
        let accepted_lineage = facts.group_id.clone().filter(|group| !group.is_empty()).map(|group_id| crate::caller_lineage::RootLineage {
                            root_run_id: facts.root_run_id.clone(),
                            environment: facts.environment.clone(),
                            delegation_depth: 0,
                            attempt: facts.attempt,
                            message_turn_id: facts.message_turn_id.clone(),
                            group_id,
                            session_id: facts.session_id.clone(),
                            history_cutoff_seq: facts
                                .history
                                .as_ref()
                                .map(|history| history.history_cutoff_seq)
                                .unwrap_or(0),
                            expires_at_ms: effective_deadline,
                            // spec §8.3 delegation-only material: the root's
                            // stable pseudonymous subject, runtime-only,
                            // from the claimed ingress actor — the delegate
                            // dispatch issues bounded DelegationOnly proofs
                            // for THIS subject, never the raw principal.
                            identity_subject: identity_subject.clone(),
                            identity_subject_type,
                            delegate_subject: ingress.actor().staff_no.clone(),
                            parent_execution: true,
                        });
        let accepted = self.accepted_effect(target, service_credential, &command, accepted_lineage,
            facts.shared_content_kind.clone(), proof, at_term);
        if !self.delivery_term_is_current(at_term) { return Err(CallerDispatchError::CommitFailed); }
        tracing::debug!(dispatch_id = %facts.dispatch_id, stage = "send", "caller protected dispatch stage");
        let send_result = self.sender.send_caller_execution(target, service_credential, command.clone()).await;
        tracing::debug!(dispatch_id = %facts.dispatch_id, stage = "send_returned", "caller protected dispatch stage");
        match send_result {
            Ok(CallerDispatchAck::Accepted { .. }) => {
                accepted(now_ms);
                Ok(CallerDispatchOutcome::Accepted)
            }
            Ok(CallerDispatchAck::Unknown) => Ok(CallerDispatchOutcome::OutcomeUnknown),
            Ok(CallerDispatchAck::Rejected { error_code, retryable }) => {
                if error_code == "HISTORY_NOT_READY" && retryable {
                    self.park_root_barrier(target, service_credential, &command, &facts, accepted, at_term).await;
                }
                Ok(CallerDispatchOutcome::Rejected { error_code, retryable })
            }
            Err(_) => Ok(CallerDispatchOutcome::OutcomeUnknown),
        }
    }

    /// §6.8 same-dispatch barrier park (review-V B3.2, root branch): with
    /// the backfill port attached and the wired-commit facts present, a
    /// retryable HISTORY_NOT_READY parks the ORIGINAL send inputs (same
    /// dispatch id, digest, attempt and proof token) together with the
    /// snapshot attestation and drives the detached redrive loop. The
    /// attestation's id list is rebound here — the wire manifest carried
    /// only its digest.
    async fn park_root_barrier(
        &self,
        target: &BotDeliveryTarget,
        service_credential: &RedactedToken,
        command: &SendCallerExecutionCommand,
        facts: &CommittedDispatchFacts,
        accepted: accepted::AcceptedEffect,
        at_term: Option<u64>,
    ) {
        let (Some(backfill), Some(origin), Some(history), Some(managed)) = (
            self.history_backfill.clone(),
            facts.delivery_commit.as_ref(),
            facts.history.clone(),
            self.managed_commit.clone(),
        ) else {
            // Plain parked semantics (marker + deadline control) when the
            // composition cannot drive the same-dispatch redrive.
            return;
        };
        let attestation_ids: Vec<String> = match managed
            .bounded_contexts(&origin.delivery_id, 25)
            .await
        {
            Ok(contexts) => contexts
                .rows
                .iter()
                .filter(|row| (row.source_session_seq.max(0) as u64) <= history.history_cutoff_seq)
                .map(|row| row.source_message_id.clone())
                .collect(),
            // A read failure refuses the park: never attest a list we
            // cannot vouch for (fail closed mirrors the root manifest).
            Err(_) => return,
        };
        if bcs_domain::history_manifest::canonical_manifest_digest(&facts.target_bot_id,
            &facts.session_id, history.history_cutoff_seq, history.projection_version, &attestation_ids)
            != history.manifest_digest { return; }
        barrier::park_and_drive(
            accepted,
            backfill,
            self.history_messages.clone(),
            self.sender.clone(),
            Some(managed),
            self.barrier_parks.clone(),
            self.barrier_backoff_ms.clone(),
            origin.delivery_id.clone(),
            target.clone(),
            service_credential.clone(),
            command.clone(),
            bcs_service_api::port::HistoryCompleteCommand {
                session_id: facts.session_id.clone(),
                caller_subject: command.caller_subject_ref.clone(),
                snapshot_id: history.history_snapshot_id,
                history_cutoff_seq: history.history_cutoff_seq,
                projection_version: history.projection_version,
                message_ids: attestation_ids,
                manifest_digest: history.manifest_digest,
                now_ms: chrono::Utc::now().timestamp_millis(),
            },
            self.delivery_term.clone(),
            at_term,
        );
    }


}

/// Bound on concurrently parked same-dispatch redrives (§6.8): a refusal
/// to park keeps the plain parked semantics (Dispatching + marker +
/// deadline control) — never a worse state.
pub(crate) const PARK_CAPACITY: usize = 64;

mod orchestration;
mod barrier;
mod history_replay;
mod observe;
mod accepted;
mod delegate;
pub use barrier::HistoryBarrierConverger;
pub use delegate::{DelegateDispatchError, DelegateDispatchFacts};

// ---- small adapters (opaque wrapping only; no secret materialization) ----

fn opaque_from(actor: &bcs_service_api::application::principal::HumanActor) -> CallerOpaqueRef {
    CallerOpaqueRef::new(actor.staff_no.clone())
}

fn opaque_from_ref(actor: &bcs_service_api::application::principal::HumanActor) -> CallerOpaqueRef {
    CallerOpaqueRef::new(actor.staff_no.clone())
}

fn opaque_from_proof(proof: &ProofToken) -> CallerOpaqueRef {
    CallerOpaqueRef::new(proof.expose_token().to_string())
}

fn proof_format_string(format: bcs_service_api::application::caller_identity::CallerProofFormat) -> String {
    match format {
        bcs_service_api::application::caller_identity::CallerProofFormat::Jwt => "jwt".to_string(),
        bcs_service_api::application::caller_identity::CallerProofFormat::Opaque => "opaque".to_string(),
    }
}
