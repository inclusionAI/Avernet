//! Protected caller chat.send port (spec §10.9; wire contract
//! apps/bcs/docs/caller-container-gateway.md "受保护 root-H2A chat.send v1").
//!
//! An independent outbound Service API for executing one already-prepared
//! caller dispatch: NOT the ordinary `BotDeliveryCommand` path, and it never
//! changes it. The carrier is the Provider webhook's chat.send envelope; the
//! caller material rides runtime-only `extensions` zones and must never
//! enter DBs, queues, logs, ordinary request-body recorders or model
//! context.
//!
//! Contract rules frozen here:
//! - inputs are produced from already-committed Managed facts (StartSend's
//!   run/attempt/request) and a trusted preparation result; only plain text
//!   is supported in this subset — no caller material inside the message;
//! - `request_digest` and `control_revision` are BCS-local dispatch facts;
//!   the HTTP adapter constructs Accepted from the in-flight command, not
//!   Provider echoes. They are not serialized into the Provider request;
//! - synchronous `{"ok":true}` means accepted, never completed. Existing
//!   run-correlated bot events own asynchronous results and terminal state;
//! - identity and history isolation constraints remain enforced independently
//!   of ACK shape. This port does not define a second task lifecycle.

use async_trait::async_trait;
use thiserror::Error;

use bcs_domain::{BotDeliveryTarget, RedactedToken};

use crate::port::caller_container::CallerOpaqueRef;

/// Existing dispatch facts committed at StartSend, shared by control and delivery.
/// This is not a Caller lifecycle or a separately serialized extensions.binding.
/// HTTP maps provider/session to the envelope and run/attempt/dispatch to
/// execution.run; environment/target_bot_id belong to execution.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerDispatchAssociation {
    pub environment: String,
    pub target_bot_id: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub session_id: String,
    pub run_id: String,
    pub attempt: u32,
    pub dispatch_id: String,
}

/// Runtime-only protected sender facts. Safe Debug by construction: the two
/// opaque parts never print. Clone exists for the §6.8 barrier park (the
/// SAME command is held for the same-dispatch redrive), never for fan-out.
#[derive(Clone)]
pub struct SendCallerExecutionCommand {
    pub association: CallerDispatchAssociation,
    /// Existing group routing scope; absent for direct Human-to-Bot messages.
    pub group_id: Option<String>,
    /// Caller task material the protected projection already approved; this
    /// subset carries plain text only.
    pub text: String,
    pub caller_task: String,
    pub message_turn_id: String,
    pub root_run_id: String,
    /// Runtime-only subject reference carried in the protected envelope.
    pub caller_subject_ref: CallerOpaqueRef,
    /// Human subject identity species; resolved to v1 human in production.
    pub subject_kind: String,
    pub caller_context_expires_at_ms: i64,
    /// Plugin-issued execution proof: format `jwt|opaque`, lifetime capped
    /// to effective window; never persisted nor logged forward from here.
    pub execution_token: CallerOpaqueRef,
    /// The PROOF's own expiry (spec §10.x, review-V P2-5): the plugin may
    /// issue shorter than the effective window — legal, never extends. The
    /// wire must carry this real value, never the longer caller-context
    /// window, so a strictly validating downstream never receives a token
    /// claimed-valid past its own lifetime.
    pub execution_token_expires_at_ms: i64,
    pub proof_format: String,
    /// Issuer tag of the identity plugin that minted the proof — the
    /// `extensions.caller_execution_token.type` wire label (e.g. `agentpass`).
    /// Encoding tag only: trust comes from the trusted assembly.
    pub proof_type: String,
    /// Version of the dispatch control state read by the trusted producer.
    pub control_revision: u64,
    /// Earliest allowed execution instant mirror: the effective window's cap.
    pub not_after_ms: i64,
    /// `sha256:` + 64 lowercase hex computed by the trusted producer over the
    /// canonicalized non-secret projection; only non-secret facts may
    /// contribute to it.
    pub request_digest: String,
    /// Ordinary sender identity of the ORIGINAL canonical message (the row's
    /// trusted `caller_channel` facts): what the webhook envelope derives its
    /// required `from` field from. This is the safe, ordinary actor id —
    /// NEVER the protected caller subject (which stays confined to the
    /// `caller_context` extension zone) and never identity derived from the
    /// token. An empty actor_id is a contract violation (wire refuses NotSent).
    pub channel_actor_id: String,
    /// Safe display name accompanying `channel_actor_id`; may be empty — the
    /// envelope then falls back to the actor id.
    pub channel_actor_name: String,
    pub cross_group_lineage: Option<String>,
    /// Shared-history facts the gateway must reproduce for group caller
    /// work (spec §6.8/`execution.history`): the snapshot id, cutoff fixed
    /// at admission (strictly before this message), projection version and
    /// manifest digest. None on H2A or history-less tasks.
    pub history: Option<CallerExecutionHistory>,
    /// LOCAL-ONLY synchronous-ACK wait: how long the HTTP adapter waits for
    /// the Provider's response to this request. It must never ride the wire —
    /// the wire `timeout_ms` is the Provider-side execution budget and is
    /// derived from `not_after_ms` (the ordinary chat.send's 3h concept).
    /// Shipping this window on the wire cancels every protected run at its
    /// value (the Provider guardian treats `timeout_ms` as the run budget).
    pub callback_timeout_ms: u64,
    pub now_ms: i64,
}

/// The group-task history header riding a protected send (spec §6.8
/// `execution.history`): non-credential business metadata only.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerExecutionHistory {
    pub history_snapshot_id: String,
    pub history_cutoff_seq: u64,
    pub projection_version: u64,
    pub manifest_digest: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CallerDispatchAck {
    Accepted { control_revision: u64, request_digest: String },
    /// Outcome unknown: the request may have started; downstream must
    /// converge through the status/observation chain.
    Unknown,
    Rejected { error_code: String, retryable: bool },
}

/// Closed send failure set. None of the variants carries URLs, response
/// bodies, subjects or tokens.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum CallerSendError {
    /// The command was structurally invalid, or the request build failed
    /// before any network attempt (including digest mismatch of the exact
    /// emitted bytes). Safe to retry the same dispatch id after re-prepare.
    #[error("invalid protected caller send command")]
    NotSent,
    /// The transport outcome is unknown after the request started: a
    /// mid-flight transport error/timeout, or a 2xx whose ACK body could not
    /// be read/parsed (acceptance cannot be falsified once the Provider
    /// answered success).
    #[error("protected caller send outcome unknown")]
    OutcomeUnknown,
    /// The Provider answered a definite non-2xx. 4xx (`retryable: false`) is
    /// deterministic never-accepted evidence — the dispatch can converge
    /// failed without waiting for stop/terminal observation. 5xx
    /// (`retryable: true`) may come from an intermediary after acceptance, so
    /// callers converge it conservatively as unknown. Retrying this dispatch
    /// needs a new decision by the dispatcher, never automatic here.
    #[error("protected caller send gateway response invalid")]
    GatewayFailed { retryable: bool },
}

/// The port consumed at dispatch time after a prepare succeeded and the
/// execution proof has been issued.
#[async_trait]
pub trait CallerExecutionSendPort: Send + Sync {
    async fn send_caller_execution(
        &self,
        target: &BotDeliveryTarget,
        service_credential: &RedactedToken,
        command: SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, CallerSendError>;
}
// ---------------------------------------------------------------------------
// Observe-only inject (spec §10.10 `chat.inject` observe-only 扩展): group
// shared-context sync toward a NON-executing target container. Never carries
// executable identity or a token — caller_context carries only the trusted
// destination subject; optional runtime_target references refine routing. An inject never
// triggers tools, children, runs or an ensure RPC; it only updates the target's
// shared history.
// ---------------------------------------------------------------------------

/// One observe-only context sync toward another target's container. The
/// caller_context.subject carries the trusted destination subject; optional
/// paired references identify an ensured target (from the observe index).
/// The shared-context message is deduplicated by id on the receiving side.
#[derive(Debug, Clone)]
pub struct ObserveInjectCommand {
    pub target_bot_id: String,
    pub session_id: String,
    /// Destination Human subject, not the message sender or an execution grant.
    pub caller_subject: CallerOpaqueRef,
    pub runtime_target_ref: Option<CallerOpaqueRef>,
    /// The Gateway's observer generation for this target.
    pub runtime_target_epoch: Option<CallerOpaqueRef>,
    /// Shared-context message material, already compliance-projected.
    pub message_id: String,
    pub text: String,
    pub now_ms: i64,
}

/// Closed inject failure set. No URLs, no bodies, no identity material.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum ObserveInjectError {
    /// The request itself was invalid before any network attempt.
    #[error("invalid observe inject request")]
    InvalidRequest,
    /// Transport/Provider failure — outcome may have been applied; the
    /// caller retries idempotently with the same message_id, never
    /// fabricates success.
    #[error("observe inject unavailable")]
    Unavailable { retryable: bool },
}

/// Service-authenticated observe-only inject seam. Implementations must not
/// trigger ensure, must not carry execution material, and must keep at most
/// one network attempt per invocation.
#[async_trait]
pub trait ObserveInjectPort: Send + Sync {
    async fn inject_shared_context(
        &self,
        service_credential: &RedactedToken,
        command: ObserveInjectCommand,
    ) -> Result<(), ObserveInjectError>;
}

// ---------------------------------------------------------------------------
// Shared-context fan-out transport (spec §6.4, L226 wiring slice). The
// observers of a fan-out are the OTHER caller containers of the SAME Bot
// (spec §3 sequence: `O = 同Bot其他Caller容器`): Alice's dispatch to BotA
// rides chat.send into Alice×BotA, and Bob×BotA observes it observe-only
// through the observe index. Whether a DIFFERENT Bot receives anything is
// decided by that Bot's own delivery/shared projection — never by this
// fan-out. All containers of one Bot live behind the SAME Provider
// webhook, so the fan-out reuses the CURRENT dispatch's own target and
// service credential: the composition only builds the injector for it.
// ---------------------------------------------------------------------------

/// Builds the observe injector for the CURRENT dispatch's own Provider
/// target. `None` (no Provider transport for this target shape) pauses the
/// fan-out — same degradation as an observe-index miss, never an error on
/// the already-accepted protected dispatch.
pub trait ObserveInjectorFactory: Send + Sync {
    /// Optional history-completion support on the same Provider transport.
    fn history_backfill(&self) -> Option<std::sync::Arc<dyn super::HistoryBackfillPort>> { None }

    fn injector_for(
        &self,
        target: &BotDeliveryTarget,
    ) -> Option<std::sync::Arc<dyn ObserveInjectPort>>;
}