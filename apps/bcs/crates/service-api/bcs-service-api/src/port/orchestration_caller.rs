//! Orchestration caller dispatch (spec §8.5 / review-V P1-1, §15.83 S1):
//! the seam one COLLABORATION runtime's node dispatch enters when the
//! assignee needs caller execution. The node keeps its OWN run/attempt/
//! dispatch lifecycle (checkpoint, claim, send marker — all unchanged);
//! this port only performs the trusted identity work the ordinary wire
//! cannot: validate the workflow instance's delegation-only association,
//! bound the window, consume the instance's turn budget, ensure the
//! assignee's container under the instance's subject, issue the bounded
//! proof and send over the protected envelope.
//!
//! Boundary discipline (§8.5): parent_kind=orchestration — the node's
//! identity parent is the WORKFLOW INSTANCE (never the last completed
//! data predecessor); the instance association is delegation-only by
//! construction; no fake source Bot, no re-attaching completed-node
//! lineage; node depth derives from the approved topology (registered
//! here at instance-depth+1 so an embedded Bot A2A continues to
//! increment under the §7.2 depth rules).

use async_trait::async_trait;

/// Facts the orchestration runtime supplies for one node dispatch. The
/// node's own lifecycle (chat.send wire run id, node attempt) is REUSED
/// as-is — this port never mints a second run or attempt.
#[derive(Debug, Clone)]
pub struct OrchestrationNodeDispatch {
    pub environment: String,
    pub group_id: String,
    pub session_id: String,
    /// The workflow INSTANCE's run id — the identity parent
    /// (parent_kind=orchestration). Its live delegation-only lineage (§8.5)
    /// is the ONLY authorization source; a missing or expired association
    /// refuses closed, never falls back to a completed predecessor or the
    /// latest watermark.
    pub workflow_run_id: String,
    pub node_id: String,
    /// §8.5: a node retry is a NEW attempt — a fresh invocation
    /// reservation per attempt, never a shared one.
    pub node_attempt: i32,
    /// The node's existing chat.send wire run id (the dispatch payload's
    /// delivery_request_id): becomes the wire association's run id.
    pub node_run_id: String,
    pub assignee_bot_id: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    /// The node's original deadline — bounds the identity window (only
    /// shrinks against the instance association's window).
    pub not_after_ms: i64,
    /// The wire text: the assignee's task material (the very message the
    /// persisted dispatch request carries — non-secret by construction).
    pub caller_task: String,
    pub control_revision: u64,
    pub now_ms: i64,
}

/// Why an orchestration node dispatch refused. Closed vocabulary, no
/// upstream bodies.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum OrchestrationDispatchError {
    /// Structurally invalid node facts (attempt ≤ 0, empty identity
    /// fields, inverted clocks) — refused before any side effect.
    #[error("orchestration dispatch rejected: structurally invalid node facts")]
    InvalidInput,
    /// The instance association is absent/expired/out-of-scope (group or
    /// session mismatch), its turn budget is exhausted, or the node depth
    /// exceeds the approved chain: closed inheritance refusal. §8.5: 关联
    /// 丢失或过期则等待或失败 — never re-attested from completed nodes.
    #[error("orchestration dispatch refused: lineage unavailable or exhausted")]
    LineageUnavailable,
    /// The container ensure failed — closed, no proof was issued.
    #[error("orchestration dispatch refused: container preparation failed")]
    PreparationFailed,
    /// The identity plugin refused to issue the bounded proof.
    #[error("orchestration dispatch refused: proof issuance failed")]
    IssueFailed,
}

/// The assignee's config-decided issuance policy (spec §8.3 mirror): the
/// composition resolves this from the caller control plane; the runtime
/// never guesses.
pub enum OrchestrationAssigneePolicy {
    /// Caller-enabled assignee: an execution identity bound to the node.
    Execution,
    /// Owner middle-hop: a delegation-only proof — the assignee's own
    /// tools may never consume it as human execution material.
    DelegationOnly,
}

#[async_trait]
pub trait OrchestrationCallerDispatchPort: Send + Sync {
    /// Route one node dispatch through the protected caller wire
    /// (§15.83 S1): the assignee's container is ensured under the
    /// instance's delegation-only subject, the proof is bounded by
    /// min(instance window, node deadline), the SAME node run/attempt
    /// rides the wire, and the node run registers at the instance depth+1
    /// so the assignee's embedded Bot A2A continues to increment (§8.5).
    /// The ack is the protected-send ack verbatim — accepted only means
    /// 受理, node terminal state still flows through the existing
    /// bot-event evidence chain.
    async fn dispatch_orchestration_node(
        &self,
        target: &bcs_domain::BotDeliveryTarget,
        service_credential: &bcs_domain::RedactedToken,
        policy: OrchestrationAssigneePolicy,
        dispatch: OrchestrationNodeDispatch,
    ) -> Result<crate::port::CallerDispatchAck, OrchestrationDispatchError>;
}

/// The composition's assignee resolution (review-V §15.83 S3): caller-enabled
/// assignees route the node through the protected wire; owner assignees keep
/// the ordinary direct delivery — the runtime never guesses.
pub enum OrchestrationAssigneeResolution {
    /// A caller-enabled assignee: node dispatch routes through the port
    /// with an execution-bound proof.
    Protected(OrchestrationAssigneePolicy),
    /// An owner (caller-disabled) assignee: the ordinary direct wire the
    /// collaboration runtime always used.
    Ordinary,
    /// Refuse-closed (review-VI P1): the assignee IS caller-enabled but the
    /// composition cannot route it to the protected wire — an unreadable
    /// configuration or a missing protected assembly. The node dispatch
    /// refuses CLOSED (never a silent ordinary-wire degradation of a
    /// caller-enabled target; the closed world must be observable).
    UnknownClosed,
}

#[async_trait]
pub trait OrchestrationCallerPolicyResolver: Send + Sync {
    async fn resolve(&self, assignee_bot_id: &str, environment: &str)
        -> OrchestrationAssigneeResolution;
}

/// §15.83 S2 (review-V P1-1): the trusted registrar a composition injects
/// into the collaboration runtime: a Human-triggered caller-aware workflow
/// registers its INSTANCE-level delegation-only association at submission
/// time (spec §8.5): the workflow run id, the group/session scope, the
/// submission turn, the identity's own window and the history ceiling
/// fixed at the triggering turn. Bot-only compositions leave the registrar
/// unset and no instance association is ever born — caller-enabled nodes
/// then keep the closed-world transport-gate behavior.
#[derive(Debug, Clone)]
pub struct OrchestrationInstanceAssociation {
    pub workflow_run_id: String,
    /// Identity trust-domain boundary. A local-node proof can never inherit
    /// an association from another environment.
    pub environment: String,
    pub group_id: String,
    pub session_id: String,
    /// The mega-turn identity of the triggering submission (the opening
    /// message id / canonical turn) — every node budget and lineage fact
    /// rides this one turn.
    pub message_turn_id: String,
    /// The submission-time history ceiling: nothing AFTER the triggering
    /// turn ever enters a node's shared-history attestation.
    pub history_cutoff_seq: u64,
    /// Trusted authenticated ingress; runtime-only, never persisted with the workflow.
    pub ingress: crate::application::caller_identity::AuthenticatedHumanCallerIngress,
    /// The trusted identity window (the ingress's own): bounds the whole
    /// instance — every node's proof窗口 only shrinks against it.
    pub expires_at_ms: i64,
    pub now_ms: i64,
}

#[async_trait]
pub trait OrchestrationInstanceRegistrar: Send + Sync {
    /// Register one instance's delegation-only association (§8.5): the
    /// composition resolves the trusted Human subject material — the
    /// association carries only the bounded facts (group/session/turn/
    /// ceiling/window), never the credential material. Registration is
    /// idempotent per workflow run id (a retried submission refreshes the
    /// same window); result is false only when the association was
    /// refused (a full index, an inverted window) — the run either keeps
    /// proceeding caller-closed (logged) or the submission itself fails,
    /// by the composition's choice.
    async fn register_instance(
        &self,
        association: OrchestrationInstanceAssociation,
    ) -> Result<bool, OrchestrationDispatchError>;
}
