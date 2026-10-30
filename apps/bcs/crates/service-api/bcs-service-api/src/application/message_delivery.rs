//! Internal managed-delivery orchestration. Network-facing adapters must first
//! authorize the caller against the canonical Session; this API is not itself
//! an unauthenticated HTTP endpoint.
use crate::core::message_delivery::DeliveryLifecycleEvent;
use crate::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionResult};
use async_trait::async_trait;
use bcs_domain::message_delivery::PersistedMessageDelivery;

/// Public projection: never expose transport metadata, signed URLs or request
/// nonces. State versions let clients discard reordered best-effort events.
#[derive(Debug, Clone, serde::Serialize)]
pub struct DeliveryStatusView {
    /// Stable admission error code only; never arbitrary transport errors.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub admission_error: Option<&'static str>,
    pub delivery_id: String,
    pub message_id: String,
    pub target_bot_id: String,
    pub flow_kind: bcs_domain::message_delivery::DeliveryFlowKind,
    pub kind: bcs_domain::DeliveryType,
    pub status: bcs_domain::message_delivery::MessageDeliveryStatus,
    pub state_version: u64,
    pub run_id: Option<String>,
    pub wait_reason: Option<bcs_domain::message_delivery::DeliveryWaitReason>,
    /// 消息正文预览（截断至前 200 字符），供前端排队列表展示。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content_preview: Option<String>,
}

impl From<&PersistedMessageDelivery> for DeliveryStatusView {
    fn from(row: &PersistedMessageDelivery) -> Self {
        Self {
            admission_error: (row.last_error_code.as_deref() == Some("delivery_provider_headers_unsupported"))
                .then_some("delivery_provider_headers_unsupported"),
            delivery_id: row.delivery_id.clone(),
            message_id: row.source_message_id.clone(),
            target_bot_id: row.target_bot_id.clone(),
            flow_kind: row.flow_kind,
            kind: row.state.kind,
            status: row.state.status,
            state_version: row.state.state_version,
            run_id: row.run_id.clone(),
            wait_reason: row.wait_reason,
            content_preview: None,
        }
    }
}

impl DeliveryStatusView {
    /// 附带消息正文预览的构造函数。
    pub fn with_content_preview(mut self, content: Option<&serde_json::Value>) -> Self {
        self.content_preview = content.and_then(|c| {
            c.get("text")
                .and_then(|t| t.as_str())
                .map(|s| truncate_utf8(s, 200))
        });
        self
    }
}

/// 在 UTF-8 字符边界安全截断字符串，超出部分以 "…" 结尾。
fn truncate_utf8(s: &str, max_bytes: usize) -> String {
    if s.len() <= max_bytes {
        return s.to_string();
    }
    let mut end = max_bytes;
    while end > 0 && !s.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}…", &s[..end])
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct DeliveryAdmissionView {
    pub message_id: String,
    pub duplicate: bool,
    pub deliveries: Vec<DeliveryStatusView>,
}

/// Read-only status for the authenticated Session participant. Recorded Chat
/// join anchors use the configured history window, not a strict post-join cutoff.
/// Private-message and Human audience checks still apply to every selected source;
/// any forbidden source fails the batch. This does not grant mutation authority.
#[derive(Debug, Clone)]
pub struct DeliveryStatusQuery {
    pub caller: super::CallerContext,
    pub session_id: String,
    pub message_ids: Vec<String>,
    pub client_msg_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct CancelMessageDeliveryCommand {
    pub caller: super::CallerContext,
    pub session_id: String,
    pub message_id: String,
    pub delivery_id: Option<String>,
}

/// Eight-field execution-control location (spec "execution.status /
/// execution.cancel"): exactly the association the dispatch committed —
/// environment, Bot, Provider, provider ref, session, run, attempt and the
/// network idempotency dispatch_id. The control path carries no Human
/// credential, never re-authenticates, never re-ensures and never re-targets
/// a different Provider; queries may run after the execution window ended
/// without extending any authorization.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerExecutionQuery {
    pub environment: String,
    pub target_bot_id: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub session_id: String,
    pub run_id: String,
    pub attempt: u32,
    pub dispatch_id: String,
}

/// v1 status surface: accepted / not_found / unknown only. `NotFound` is
/// NOT proof that nothing can still execute (spec §7.8); a terminal state
/// block is v2 territory and v1 never fabricates one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerExecutionStatus {
    Accepted,
    NotFound,
    Unknown,
}

/// Non-secret control view. No token, no caller subject, no body: the row's
/// execution state, the frozen control revision and the persisted cancel
/// state are the whole story.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerExecutionStatusView {
    pub status: CallerExecutionStatus,
    /// Raw row status wire name ("dispatching"/"running"/"cancelling"/…),
    /// "not_found" when the row cannot be located.
    pub execution_state: String,
    pub control_revision: Option<u64>,
    pub cancel_state: Option<String>,
}

#[derive(Debug, Clone)]
pub struct CallerExecutionCancelCommand {
    pub query: CallerExecutionQuery,
    /// Stable across retries of the SAME command; a different id is a NEW
    /// intent and never overwrites a committed one.
    pub cancel_request_id: String,
    pub reason: CallerCancelReason,
    pub now_ms: i64,
}

/// v1 cancel receipt: `requested` = the durable typed intent landed;
/// `unconfirmed` = the row could not be located (not stop evidence).
/// fenced/stopped need downstream confirmation flows (v2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerExecutionCancelOutcome {
    pub cancel_state: String,
    pub execution_state: String,
    pub last_event_seq: Option<u64>,
}

/// Credential-free execution control (spec §7.8): queries and cancels use
/// only the original eight-field association. Implementations must not
/// ensure containers, re-acquire identities or re-target Providers.
///
/// STATUS (2026-10-20 residue sweep): the message-flow implementation is
/// complete but no HTTP route/adapter mounts it yet — control stays an
/// in-process seam until the route contract is designed (see plan §7.8).
#[async_trait]
pub trait CallerExecutionControlService: Send + Sync {
    async fn execution_status(
        &self,
        query: CallerExecutionQuery,
    ) -> Result<CallerExecutionStatusView, ManagedDeliveryError>;

    async fn execution_cancel(
        &self,
        command: CallerExecutionCancelCommand,
    ) -> Result<CallerExecutionCancelOutcome, ManagedDeliveryError>;
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct CancelMessageDeliveryResult {
    pub delivery: DeliveryStatusView,
    pub error: Option<String>,
}

/// Human attestation, not an instruction to retry or blindly unlock a live run.
#[derive(Debug, Clone, Copy, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryResolution {
    ConfirmedNotSent,
    ConfirmedStopped,
}

#[derive(Debug, Clone)]
pub struct ResolveMessageDeliveryCommand {
    pub caller: super::CallerContext,
    pub session_id: String,
    pub message_id: String,
    pub delivery_id: String,
    pub expected_state_version: u64,
    pub resolution: DeliveryResolution,
    pub reason: String,
}

impl From<&DeliveryAdmissionResult> for DeliveryAdmissionView {
    fn from(result: &DeliveryAdmissionResult) -> Self {
        Self {
            message_id: result.message.message_id.clone(),
            duplicate: result.duplicate,
            deliveries: result
                .deliveries
                .iter()
                .map(|d| DeliveryStatusView::from(d).with_content_preview(Some(&result.message.content)))
                .collect(),
        }
    }
}

/// Non-secret, typed caller dispatch commit payload (wire contract
/// "commit_caller_dispatch"): the canonical digest, the effective deadline and
/// the control revision the trusted producer computed for exactly one attempt.
/// Persisted shape is `{version:1, caller_request_digest, caller_not_after_ms,
/// caller_control_revision}` merged into the row's semantic projection.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerDispatchCommit {
    /// `sha256:` + 64 lowercase hex, computed by `canonical_request_digest`.
    pub request_digest: String,
    /// Effective deadline: min(identity window, container window, policy).
    pub not_after_ms: i64,
    /// Control state version read when scheduling; v1 requires 0.
    pub control_revision: u64,
}

/// Typed commit command (§7.5.1): only existing lifecycle facts, never
/// identity material. The eight-field association comes from the committed
/// StartSend row; the service refuses mismatched or non-caller rows.
#[derive(Debug, Clone)]
pub struct CommitCallerDispatchCommand {
    pub delivery_id: String,
    pub expected_state_version: u64,
    pub now_ms: i64,
    pub dispatch: crate::port::CallerDispatchAssociation,
    pub commit: CallerDispatchCommit,
}

/// Why the cancel was requested. Wire strings per the execution-control
/// contract; closed set, no free-form text.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerCancelReason {
    UserRequested,
    ScopeRevoked,
}

impl CallerCancelReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::UserRequested => "user_requested",
            Self::ScopeRevoked => "scope_revoked",
        }
    }

    pub fn from_wire(value: &str) -> Option<Self> {
        match value {
            "user_requested" => Some(Self::UserRequested),
            "scope_revoked" => Some(Self::ScopeRevoked),
            _ => None,
        }
    }
}

/// Non-secret, typed caller cancel intent (spec §"取消意图与回执"): a stable
/// `cancel_request_id` the caller replays verbatim on retries, the control
/// revision frozen at intent-commit time (recovery never recomputes it), the
/// closed-set reason and the original association's `not_after_ms`. A second
/// intent with a different id never overwrites a committed one. Persisted
/// shape is `{version:1, cancel_request_id, caller_control_revision, reason,
/// not_after_ms, cancel_state:"requested"}` under the `caller_cancel`
/// projection key. Never identity material, never a credential.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerCancelIntent {
    pub cancel_request_id: String,
    pub control_revision: u64,
    pub reason: CallerCancelReason,
    pub not_after_ms: i64,
}

/// Typed cancel command (spec §"取消意图与回执" 1): the intention is durably
/// established in the SAME CAS that moves the row to Cancelling — never
/// deferred to a later scheduling pass. Duplicate replays with the same
/// intent are no-write successes; a different intent conflicts.
#[derive(Debug, Clone)]
pub struct CancelCallerDispatchCommand {
    pub delivery_id: String,
    pub expected_state_version: u64,
    pub now_ms: i64,
    pub dispatch: crate::port::CallerDispatchAssociation,
    pub cancel: CallerCancelIntent,
}

/// Terminal outcome a Provider attests for one attempt (spec "Provider 认证
/// 终态入口"). Control-receipt material only: never a result body, never
/// Caller Context, never a token.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerTerminalOutcome {
    Completed,
    Failed,
    Aborted,
}

impl CallerTerminalOutcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Aborted => "aborted",
        }
    }

    /// Closed wire set — anything else is an invalid request, not a default.
    pub fn from_wire(value: &str) -> Option<Self> {
        match value {
            "completed" => Some(Self::Completed),
            "failed" => Some(Self::Failed),
            "aborted" => Some(Self::Aborted),
            _ => None,
        }
    }
}

/// One Provider terminal observation bound to the original dispatch
/// (spec "终态仲裁"): the outcome, the strictly-advancing event sequence and
/// the control revision the provider saw. Exact duplicates are idempotent;
/// a different observation after a committed terminal is refused — the row's
/// terminal evidence is immutable once set.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CallerTerminalObservation {
    pub outcome: CallerTerminalOutcome,
    pub event_seq: u64,
    pub control_revision: u64,
}

/// Typed terminal receipt command: located strictly by the eight-field
/// association plus the authenticated Provider id (header-carried on the
/// wire). The caller cannot name a delivery_id or a state_version.
#[derive(Debug, Clone)]
pub struct CallerTerminalEventCommand {
    pub provider_id: String,
    pub dispatch: crate::port::CallerDispatchAssociation,
    pub observation: CallerTerminalObservation,
    pub now_ms: i64,
}

/// What the Managed CAS did with the terminal observation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CallerTerminalReceipt {
    /// Observed for the first time: row transitioned, version advanced.
    Applied { delivery_id: String, execution_state: String, state_version: u64 },
    /// Exact duplicate of the committed terminal: no write.
    Duplicate { delivery_id: String, execution_state: String, state_version: u64 },
}

#[derive(Debug, Clone)]
pub struct DeliveryTransitionCommand {
    pub delivery_id: String,
    pub expected_state_version: u64,
    pub event: DeliveryLifecycleEvent,
    pub now_ms: i64,
    /// Match per-attempt transport results before applying lifecycle policy.
    pub request_id: Option<String>,
    pub actor_id: Option<String>,
    pub reply: Option<AdmitMessageDeliveries>,
    /// Send-time recovery metadata, never credentials. Required by the runtime
    /// before it authorizes I/O; absent for ordinary lifecycle observations.
    pub transport_context_json: Option<serde_json::Value>,
    pub deadline_at_ms: Option<i64>,
    /// Typed caller dispatch commit payload; required exactly when the event
    /// is `CommitCallerDispatch` and forbidden otherwise.
    pub caller_dispatch_commit: Option<CallerDispatchCommit>,
    /// Typed caller cancel intent; required exactly when the event is
    /// `CancelCallerDispatch` and forbidden otherwise.
    pub caller_cancel: Option<CallerCancelIntent>,
    /// Typed Provider terminal observation; carried only by the typed
    /// `handle_caller_terminal` service method alongside an ordinary
    /// terminal event, never by the ordinary path.
    pub caller_terminal: Option<CallerTerminalObservation>,
}

#[derive(Debug, thiserror::Error)]
pub enum ManagedDeliveryError {
    #[error(transparent)]
    Repository(#[from] crate::port::repo::message_delivery::MessageDeliveryRepoError),
    #[error(transparent)]
    Lifecycle(#[from] crate::core::message_delivery::DeliveryLifecycleError),
    #[error("delivery not found")]
    NotFound,
    #[error("delivery attempt or context carrier has changed")]
    Conflict,
}

#[async_trait]
pub trait ManagedMessageDeliveryService: Send + Sync {
    async fn admit_batch(&self, _commands: Vec<AdmitMessageDeliveries>) -> Result<Vec<DeliveryAdmissionResult>, ManagedDeliveryError> {
        Err(ManagedDeliveryError::Conflict)
    }
    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<crate::port::repo::message_delivery::BoundDeliveryContexts, ManagedDeliveryError>;
    /// Fixed operation names only; implementations must not record identities
    /// or payloads as metric labels. Observation must never block or fail work.
    fn observe_operation(&self, _operation: &'static str, _seconds: f64, _rows: usize, _success: bool) {}
    async fn lookup(&self, scope: crate::port::repo::message_delivery::DeliveryLookup) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError>;
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, ManagedDeliveryError>;
    /// Typed caller dispatch commit (§7.5.1): CAS-persists the canonical
    /// digest/deadline/revision into a caller row's semantic projection.
    /// Same-version identical resubmission is a no-write success; any digest,
    /// deadline, version or status divergence conflicts. Default refuses —
    /// an un assembled service must never pretend a commit happened.
    async fn commit_caller_dispatch(
        &self,
        _command: CommitCallerDispatchCommand,
    ) -> Result<bool, ManagedDeliveryError> {
        Err(ManagedDeliveryError::Conflict)
    }
    /// Typed caller cancel intent (spec §"取消意图与回执"): durably extends
    /// the committed caller dispatch with a stable cancel_request_id and the
    /// control revision frozen at intent-commit time. Same-intent replay is a
    /// no-write success; a different intent never overwrites a committed one.
    /// Default refuses — an unassembled service must never pretend a cancel
    /// intent was persisted.
    async fn cancel_caller_dispatch(
        &self,
        _command: CancelCallerDispatchCommand,
    ) -> Result<bool, ManagedDeliveryError> {
        Err(ManagedDeliveryError::Conflict)
    }
    /// Typed Provider terminal observation (spec "终态仲裁"): commits the
    /// outcome/event_seq/control_revision evidence onto the located caller
    /// row via one Managed CAS and converges the lifecycle to its terminal.
    /// Exact duplicates replay idempotently; any other observation after a
    /// committed terminal, a stale sequence or an old attempt conflicts.
    /// Default refuses — an unassembled service must never fake a terminal.
    ///
    /// STATUS (2026-10-20 residue sweep): the wire entrance was dropped
    /// (b503e66b3) and this internal typed CAS was deliberately retained.
    /// Production wiring passes `caller_terminal: None` on every transition,
    /// so today this method is reachable only from contract tests. Remove it
    /// together with the `CallerTerminal*` vocabulary if the redesigned
    /// terminal entrance does not land.
    async fn handle_caller_terminal(
        &self,
        _command: CallerTerminalEventCommand,
    ) -> Result<CallerTerminalReceipt, ManagedDeliveryError> {
        Err(ManagedDeliveryError::Conflict)
    }
    async fn active_count(&self, bot: &str) -> Result<u64, ManagedDeliveryError>;
    async fn lane_blocked(&self, row: &PersistedMessageDelivery) -> Result<bool, ManagedDeliveryError>;
    async fn queued_heads(&self, bot: &str, after: &str, limit: usize) -> Result<Vec<crate::core::message_delivery::DeliveryScheduleEntry>, ManagedDeliveryError>;
    async fn work_batch(&self, kind: crate::port::repo::message_delivery::DeliveryWorkBatch, now_ms: i64, after: &str, limit: usize) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError>;
    async fn queue_statistics(&self) -> Result<Vec<crate::port::repo::message_delivery::DeliveryQueueStatistic>, ManagedDeliveryError>;
    async fn unfinished(&self) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError>;
    async fn update_wait_reasons(
        &self,
        waiting: Vec<(String, bcs_domain::message_delivery::DeliveryWaitReason)>,
        now_ms: i64,
    ) -> Result<(), ManagedDeliveryError>;
    /// Correlate a successful transport ACK by the current attempt nonce and
    /// authenticated Bot. Persist its engine run alias before projections.
    async fn accept_run(
        &self,
        request_id: &str,
        bot_id: &str,
        downstream_run_id: Option<&str>,
        now_ms: i64,
    ) -> Result<Option<PersistedMessageDelivery>, ManagedDeliveryError>;

    /// Atomically admit work. Conflict and Repository(Conflict | Invalid)
    /// are definitive rejections: no new work from this call was committed.
    /// Other errors (notably Storage, including a lost COMMIT acknowledgement)
    /// do not prove rollback. Callers must retain potentially committed Run
    /// identities/output permits and must not retry under fresh identities.
    async fn admit(
        &self,
        command: AdmitMessageDeliveries,
    ) -> Result<DeliveryAdmissionResult, ManagedDeliveryError>;
    async fn snapshot(
        &self,
        session_id: Option<&str>,
    ) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError>;
    async fn transition(
        &self,
        command: DeliveryTransitionCommand,
    ) -> Result<PersistedMessageDelivery, ManagedDeliveryError>;
    /// Recovery deliberately ignores new-admission switches.
    async fn recover(&self, now_ms: i64) -> Result<(), ManagedDeliveryError>;
}

pub trait DeliveryInstrumentation: Send + Sync {
    fn operation(&self, operation: &'static str, seconds: f64, rows: usize, success: bool);
    fn event(&self, event: &'static str, delivery: &PersistedMessageDelivery);
}

pub struct PreparedManagedDelivery {
    pub command: crate::port::BotDeliveryCommand,
    pub transport_context_json: serde_json::Value,
}

/// The real dispatch context for one protected caller row (review P1-5):
/// the target's actual Provider delivery target — carrying its
/// Provider-authorized service token — plus the provider routing fields of
/// the wire association. Caller containers only exist behind a Provider
/// webhook (spec §10.8): compositions that cannot resolve this shape keep
/// the runtime guard fail-closed.
pub struct CallerDispatchContext {
    pub target: bcs_domain::BotDeliveryTarget,
    pub service_credential: bcs_domain::RedactedToken,
    pub provider_id: String,
    pub provider_bot_ref: String,
}

/// Application-owned reconstruction and current access/policy checks. This is
/// not implemented by a transport adapter; adapters only perform typed I/O.
#[async_trait]
pub trait ManagedDeliveryPreparationService: Send + Sync {
    async fn is_available(&self, bot_id: &str) -> bool;
    /// Revalidate a prepared target before send-start. False discards the
    /// preparation but retains queued work; it is not a transport failure.
    async fn still_valid(&self, _prepared: &PreparedManagedDelivery) -> bool { true }
    async fn prepare(
        &self,
        delivery: &PersistedMessageDelivery,
    ) -> crate::ServiceResult<PreparedManagedDelivery>;
    /// Resolve the protected dispatch context for one caller row. The
    /// default REFUSES: without a caller-capable resolution the runtime
    /// keeps the anti-downgrade guard (the row converges failed before any
    /// context read) — never a placeholder target or token.
    async fn resolve_caller_dispatch(
        &self,
        _delivery: &PersistedMessageDelivery,
    ) -> Option<CallerDispatchContext> {
        None
    }
    /// Register canonical/current request aliases after send-start commits and
    /// before network I/O, so even an immediate ACK/final can be correlated.
    async fn before_send(
        &self,
        delivery: &PersistedMessageDelivery,
        command: &crate::port::BotDeliveryCommand,
    ) -> crate::ServiceResult<()>;
    /// Must refuse ambiguous Provider scope or changed connection ownership.
    async fn prepare_abort(
        &self,
        delivery: &PersistedMessageDelivery,
    ) -> crate::ServiceResult<crate::port::BotAbortDeliveryCommand>;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncate_utf8_ascii_within_limit() {
        assert_eq!(truncate_utf8("hello", 200), "hello");
    }

    #[test]
    fn truncate_utf8_ascii_at_limit() {
        let s = "a".repeat(200);
        assert_eq!(truncate_utf8(&s, 200), s);
    }

    #[test]
    fn truncate_utf8_ascii_over_limit() {
        let s = "a".repeat(201);
        let result = truncate_utf8(&s, 200);
        assert!(result.ends_with('…'));
        assert_eq!(result.len(), 200 + '…'.len_utf8());
    }

    #[test]
    fn truncate_utf8_multibyte_boundary() {
        // 中文字符占 3 字节，67 个字符 = 201 字节，截断到 200 字节时不能 panic
        let s = "中".repeat(67);
        let result = truncate_utf8(&s, 200);
        assert!(result.ends_with('…'));
        // 198 字节 = 66 个完整中文字符
        assert!(result.starts_with(&"中".repeat(66)));
    }

    #[test]
    fn truncate_utf8_emoji_boundary() {
        // emoji 占 4 字节
        let s = "🎉".repeat(51); // 204 bytes
        let result = truncate_utf8(&s, 200);
        assert!(result.ends_with('…'));
        assert!(result.starts_with(&"🎉".repeat(50))); // 200 bytes = 50 emojis
    }

    #[test]
    fn with_content_preview_extracts_text() {
        let content = serde_json::json!({"text": "hello world"});
        let view = DeliveryStatusView {
            admission_error: None,
            delivery_id: "d1".into(),
            message_id: "m1".into(),
            target_bot_id: "b1".into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            kind: bcs_domain::DeliveryType::Send,
            status: bcs_domain::message_delivery::MessageDeliveryStatus::Queued,
            state_version: 1,
            run_id: None,
            wait_reason: None,
            content_preview: None,
        };
        let view = view.with_content_preview(Some(&content));
        assert_eq!(view.content_preview.as_deref(), Some("hello world"));
    }

    #[test]
    fn with_content_preview_truncates_long_text() {
        let long_text = "中".repeat(100);
        let content = serde_json::json!({"text": long_text});
        let view = DeliveryStatusView {
            admission_error: None,
            delivery_id: "d1".into(),
            message_id: "m1".into(),
            target_bot_id: "b1".into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            kind: bcs_domain::DeliveryType::Send,
            status: bcs_domain::message_delivery::MessageDeliveryStatus::Queued,
            state_version: 1,
            run_id: None,
            wait_reason: None,
            content_preview: None,
        };
        let view = view.with_content_preview(Some(&content));
        let preview = view.content_preview.as_deref().unwrap();
        assert!(preview.ends_with('…'));
        assert!(preview.len() <= 200 + '…'.len_utf8());
    }

    #[test]
    fn with_content_preview_missing_text_field() {
        let content = serde_json::json!({"other": "data"});
        let view = DeliveryStatusView {
            admission_error: None,
            delivery_id: "d1".into(),
            message_id: "m1".into(),
            target_bot_id: "b1".into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            kind: bcs_domain::DeliveryType::Send,
            status: bcs_domain::message_delivery::MessageDeliveryStatus::Queued,
            state_version: 1,
            run_id: None,
            wait_reason: None,
            content_preview: None,
        };
        let view = view.with_content_preview(Some(&content));
        assert!(view.content_preview.is_none());
    }

    #[test]
    fn with_content_preview_none_content() {
        let view = DeliveryStatusView {
            admission_error: None,
            delivery_id: "d1".into(),
            message_id: "m1".into(),
            target_bot_id: "b1".into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            kind: bcs_domain::DeliveryType::Send,
            status: bcs_domain::message_delivery::MessageDeliveryStatus::Queued,
            state_version: 1,
            run_id: None,
            wait_reason: None,
            content_preview: None,
        };
        let view = view.with_content_preview(None);
        assert!(view.content_preview.is_none());
    }
}
