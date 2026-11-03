use async_trait::async_trait;
use bcs_domain::{BotDeliveryTarget, MessageAudience, MessageVisibilityDomain};
use bcs_protocol::BcsFrame;

use crate::{ServiceError, ServiceResult};

pub use bcs_protocol::{BotDeliveryKind, FrontendDeliveryKind, FrontendDeliveryTarget};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ProviderTransportPreference {
    #[default]
    SseFirst,
    Callback,
}

#[derive(Clone)]
pub struct BotDeliveryCommand {
    pub target: BotDeliveryTarget,
    /// Canonical logical run identity. HTTP Provider chat.send uses this as
    /// its wire id and callback/SSE correlation key, not the attempt frame id.
    pub run_id: String,
    /// The frame id may identify a queue attempt and differ from run_id.
    /// WebSocket delivery preserves it; HTTP Provider normalizes chat.send
    /// to run_id while preserving ids for non-running requests.
    pub frame: BcsFrame,
    pub delivery_kind: BotDeliveryKind,
    /// Request-scoped transport preference for HTTP Provider `chat.send`.
    /// Non-Provider transports ignore this field.
    pub provider_transport: ProviderTransportPreference,
    /// Opaque inbound HTTP headers explicitly allowlisted by BCS configuration
    /// for forwarding to HTTP provider webhooks. Empty for non-HTTP ingress.
    pub provider_bypass_headers: Vec<(String, String)>,
}

impl std::fmt::Debug for BotDeliveryCommand {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BotDeliveryCommand").field("target", &self.target)
            .field("run_id", &self.run_id).field("delivery_kind", &self.delivery_kind)
            .field("frame", &"<request payload redacted>").finish_non_exhaustive()
    }
}

impl BotDeliveryCommand {
    pub fn target_bot_id(&self) -> &str {
        self.target.bot_id()
    }
}

#[derive(Debug)]
pub struct BotDeliveryResult {
    pub target_bot_id: String,
    /// `true` means the transport accepted the request. `false` is a complete,
    /// explicit downstream rejection and is terminal for this attempt; it is
    /// not a transport-uncertain error. Safe retry still requires an attached
    /// `ServiceError::DeliveryNotSent { retryable: true, .. }`.
    pub delivered: bool,
    /// A transport-neutral diagnostic for an explicit rejection. Callers must
    /// not infer retry safety from arbitrary error text.
    pub error: Option<ServiceError>,
}

#[derive(Debug, Clone)]
pub struct BotAbortDeliveryCommand {
    pub target: BotDeliveryTarget,
    pub command_id: String,
    pub group_id: String,
    pub session_id: String,
    /// Exact downstream run id for Bot WebSocket/plugin delivery. `None`
    /// denotes one Provider Bot/Session scope request.
    pub run_id: Option<String>,
    /// Routing headers captured from the original Provider `chat.send`.
    /// Non-Provider transports ignore this field.
    pub provider_bypass_headers: Vec<(String, String)>,
    pub timeout_ms: u64,
}

impl BotAbortDeliveryCommand {
    pub fn target_bot_id(&self) -> &str {
        self.target.bot_id()
    }
}

#[derive(Debug)]
pub struct BotAbortDeliveryResult {
    pub target_bot_id: String,
    pub aborted_run_ids: Vec<String>,
}

#[async_trait]
pub trait BotDeliveryPort: Send + Sync {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool;
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult>;

    /// Opaque identity of the current connection, not an authentication token.
    /// Reconnection must create a new identity. None means pinning unavailable.
    async fn connection_identity(&self, _target: &BotDeliveryTarget) -> Option<String> {
        None
    }

    /// Select the connection and check its identity atomically. Never fall back
    /// to a replacement connection if the original connection has disappeared.
    async fn deliver_on_connection(
        &self,
        cmd: BotDeliveryCommand,
        _connection_id: &str,
    ) -> ServiceResult<BotDeliveryResult> {
        Err(ServiceError::InvalidOperation {
            message: "connection-pinned delivery is not supported".to_string(),
            request_id: Some(cmd.run_id),
        })
    }

    async fn abort_on_connection(
        &self,
        cmd: BotAbortDeliveryCommand,
        _connection_id: &str,
    ) -> ServiceResult<BotAbortDeliveryResult> {
        Err(ServiceError::InvalidOperation {
            message: "connection-pinned abort is not supported".to_string(),
            request_id: Some(cmd.command_id),
        })
    }

    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        Err(ServiceError::InvalidOperation {
            message: "typed chat.abort delivery is not supported by this transport".to_string(),
            request_id: Some(cmd.command_id),
        })
    }
}

#[derive(Debug, Clone)]
pub struct FrontendDeliveryCommand {
    pub target: FrontendDeliveryTarget,
    pub event_json: String,
    pub delivery_kind: FrontendDeliveryKind,
    pub run_fallback: Option<RunFallbackDelivery>,
    pub exclude_conn_id: Option<u64>,
    pub visibility_domain: MessageVisibilityDomain,
    pub audience: Option<MessageAudience>,
    /// Trusted producer metadata (bot-authority spec §8.2): the
    /// application-assigned identity of the business operation this
    /// frame belongs to. `Some` = admitted-operation output — the
    /// delivery adapter checks the per-connection permit frozen at
    /// the operation's admission (no fresh authorization, no
    /// retroactive revocation). `None` = an independent finite
    /// resource event (member/metadata/system notification) — the
    /// adapter authorizes THIS event once per candidate against
    /// current facts. Never parse an operation identity out of
    /// `event_json`; producers pass it here.
    pub operation_id: Option<String>,
    /// Whether this frame terminates its stream. A terminal belongs
    /// to the stream named by [`Self::run_id`]: it closes THAT
    /// stream's new frames only — sibling streams of the same
    /// operation keep flowing. The operation's output closes when
    /// EVERY stream registered at admission time is terminal.
    /// Terminal frames are typed and order-preserved at the
    /// per-connection queue: a full slow-consumer queue takes the
    /// bounded backpressure budget, and a terminal lost past that
    /// budget retires the saturated connection (an observable
    /// transport failure the client recovers from by reconnecting),
    /// never a silent live stream without a final.
    pub terminal: bool,
    /// Trusted producer metadata (bot-authority spec §8.2): the
    /// canonical run/stream identity this frame belongs to — the
    /// same durable run id the operation registered at admission
    /// time. `Some` scopes the terminal semantics and post-terminal
    /// leakage guard to THIS stream; `None` marks an operation-level
    /// frame without a stream identity (e.g. the user echo) or an
    /// independent event frame. Never parse a run identity out of
    /// `event_json`; producers pass it here.
    pub run_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RunFallbackDelivery {
    pub run_id: String,
    pub session_id: String,
    pub event_json: String,
}

#[derive(Debug, Clone)]
pub struct FrontendDeliveryResult {
    pub target: FrontendDeliveryTarget,
    pub delivered: usize,
}

#[async_trait]
pub trait FrontendDeliveryPort: Send + Sync {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult>;

    async fn unregister_run(&self, run_id: &str) -> ServiceResult<()>;
}

/// The output-admission facts of ONE accepted business operation
/// (bot-authority spec §8.2). The identity is assigned by the
/// application layer BEFORE any side effect of the operation, and
/// the SAME identity is later carried on the operation's frames via
/// [`FrontendDeliveryCommand::operation_id`].
#[derive(Debug, Clone)]
pub struct OutputOperationAdmission {
    /// Application-assigned operation identity (NOT a client value).
    pub operation_id: String,
    /// The canonical Group the operation writes into.
    pub group_id: String,
    /// The canonical Session when the operation is session-anchored.
    pub session_id: Option<String>,
}

/// Narrowest outbound port through which the application/core layer
/// installs read-only output permits at business-accept time
/// (spec §8.2: 许可在副作用前建立，active_run_ids 不再承担授权注册).
///
/// The implementing delivery adapter enumerates the connections
/// currently subscribed to the operation's scope, resolves each
/// candidate's delivery view against CURRENT facts and freezes the
/// result as the per-connection permit for THIS operation only.
/// Admission denial of a passive recipient never rolls back the
/// business operation; an internal admission failure yields zero
/// permits for this operation (logged) and leaves every earlier
/// permit untouched.
#[async_trait]
pub trait OutputAdmissionPort: Send + Sync {
    async fn admit_output_operation(
        &self,
        admission: OutputOperationAdmission,
    ) -> ServiceResult<()>;

    /// Register the operation's COMPLETE output-stream set (every
    /// durable run the operation will produce) BEFORE any downstream
    /// callback or transport I/O can reference one of them (spec §8.2
    /// Run terminal ≠ operation complete). The delivery adapter
    /// closes the operation's output only when EVERY registered
    /// stream reported terminal; a single stream's terminal never
    /// retires the whole operation's admitted output. Registrations
    /// are idempotent unions and tolerate terminal frames that
    /// already arrived (fast managed callbacks). A NO-OP default
    /// keeps adapters without per-stream bookkeeping working: their
    /// operations simply stay open until the explicit end-states
    /// sweep them.
    async fn register_operation_runs(
        &self,
        _operation_id: &str,
        _run_ids: Vec<String>,
    ) -> ServiceResult<()> {
        Ok(())
    }

    /// Explicitly release streams the application proved will never
    /// produce output — a transport delivery failure, an
    /// admission-time rejection, or an aborted send. Released
    /// streams refuse later frames and no longer hold the operation
    /// open; the operation completes once nothing registered can
    /// still produce. This is the explicit end-state counterpart of
    /// stream terminals (the permit store never reclaims accepted
    /// operations by age). Idempotent; a NO-OP default keeps
    /// adapters without per-stream bookkeeping working.
    async fn release_operation_runs(
        &self,
        _operation_id: &str,
        _run_ids: Vec<String>,
    ) -> ServiceResult<()> {
        Ok(())
    }
}
