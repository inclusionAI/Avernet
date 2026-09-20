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
