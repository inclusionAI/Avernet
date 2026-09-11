//! Managed-delivery admission configuration. Readiness is supplied by the
//! composition root, never by a user-controlled configuration field.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// Durable business policy. Host paths and credentials never enter this value.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliveryPolicy {
    pub flow_enabled: DeliveryFlowSwitches,
    pub defaults: BotDeliveryConfig,
    #[serde(default)]
    pub bots: BTreeMap<String, BotDeliveryOverride>,
    #[serde(default)]
    pub queue_ttl_ms: Option<u64>,
    #[serde(default)]
    pub safe_retry: Option<SafeRetryConfig>,
    #[serde(default)]
    pub pause_dispatch: bool,
    #[serde(default = "default_context_messages")]
    pub max_context_messages: u32,
    #[serde(default = "default_context_bytes")]
    pub max_context_bytes: u64,
}

fn default_context_messages() -> u32 { 24 }
fn default_context_bytes() -> u64 { 131072 }

impl Default for DeliveryPolicy {
    fn default() -> Self {
        Self { flow_enabled: Default::default(), defaults: BotDeliveryConfig {
            mode: BotDeliveryMode::Off, max_running: 1, min_send_interval_ms: 1000, max_queued: 20,
        }, bots: BTreeMap::new(), queue_ttl_ms: None, safe_retry: None, pause_dispatch: false,
            max_context_messages: default_context_messages(), max_context_bytes: default_context_bytes() }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BotDeliveryOverride {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<BotDeliveryMode>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_running: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_send_interval_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_queued: Option<u32>,
}

impl DeliveryPolicy {
    pub fn bot(&self, id: &str) -> BotDeliveryConfig {
        let mut value = self.defaults.clone();
        if let Some(overrides) = self.bots.get(id) {
            if let Some(mode) = overrides.mode { value.mode = mode; }
            if let Some(limit) = overrides.max_running { value.max_running = limit; }
            if let Some(interval) = overrides.min_send_interval_ms { value.min_send_interval_ms = interval; }
            if let Some(limit) = overrides.max_queued { value.max_queued = limit; }
        }
        value
    }

    pub fn manages_group(&self, id: &str) -> bool {
        self.flow_enabled.group && self.bot(id).mode == BotDeliveryMode::Enforce
    }

    pub fn needs_scheduler(&self) -> bool {
        self.flow_enabled.group && (self.defaults.mode == BotDeliveryMode::Enforce
            || self.bots.keys().any(|id| self.bot(id).mode == BotDeliveryMode::Enforce))
    }

    pub fn validate(&self) -> Result<(), MessageDeliveryConfigError> {
        if !(1..=1024).contains(&self.max_context_messages) || !(512..=16_777_216).contains(&self.max_context_bytes) {
            return Err(MessageDeliveryConfigError::InvalidContextPolicy);
        }
        let bots: BTreeMap<_, _> = self.bots.keys().map(|id| (id.clone(), self.bot(id))).collect();
        // Validate defaults independently: a real Bot ID must never shadow the
        // default-policy validation (or vice versa).
        MessageDeliveryConfig { bots: BTreeMap::from([("defaults".into(), self.defaults.clone())]), ..Default::default() }
            .validate_ready_flows(&[])?;
        MessageDeliveryConfig { flow_enabled: self.flow_enabled.clone(), bots,
            queue_ttl_ms: self.queue_ttl_ms, safe_retry: self.safe_retry.clone(),
            pause_dispatch: self.pause_dispatch,
        }.validate_ready_flows(&[DeliveryFlowKey::Group])
    }
}

/// Version zero is a synthetic, disabled policy when no DB row exists.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliveryPolicyRecord {
    pub version: u64,
    pub policy: DeliveryPolicy,
    pub updated_by: String,
    pub updated_at_ms: i64,
}

/// Configuration keys, not a client-supplied classification of a message.
/// Keep this leaf crate independent of domain and service crates. Composition
/// maps its server-classified domain flow to the corresponding config key.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryFlowKey {
    Group,
    DirectA2a,
    Task,
    System,
    StateMachine,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MessageDeliveryConfig {
    /// Absent means no queue expiry. This is not a running-request timeout.
    #[serde(default)]
    pub queue_ttl_ms: Option<u64>,
    /// Opt-in retries only for failures proven to precede transport I/O.
    #[serde(default)]
    pub safe_retry: Option<SafeRetryConfig>,
    /// Operational pause for new sends only. Terminal/cancel/recovery stay live.
    #[serde(default)]
    pub pause_dispatch: bool,
    #[serde(default)]
    pub flow_enabled: DeliveryFlowSwitches,
    #[serde(default)]
    pub bots: BTreeMap<String, BotDeliveryConfig>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SafeRetryConfig {
    pub max_retries: u32,
    pub backoff_ms: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliveryFlowSwitches {
    #[serde(default)]
    pub group: bool,
    #[serde(default)]
    pub direct_a2a: bool,
    #[serde(default)]
    pub task: bool,
    #[serde(default)]
    pub system: bool,
    #[serde(default)]
    pub state_machine: bool,
}

impl DeliveryFlowSwitches {
    pub fn enabled(&self, flow: DeliveryFlowKey) -> bool {
        match flow {
            DeliveryFlowKey::Group => self.group,
            DeliveryFlowKey::DirectA2a => self.direct_a2a,
            DeliveryFlowKey::Task => self.task,
            DeliveryFlowKey::System => self.system,
            DeliveryFlowKey::StateMachine => self.state_machine,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotDeliveryMode {
    #[default]
    Off,
    Enforce,
}

/// Limits are required explicitly for a configured Bot. Initial production
/// values depend on measured engine latency; no unvalidated numeric defaults.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BotDeliveryConfig {
    #[serde(default)]
    pub mode: BotDeliveryMode,
    pub max_running: u32,
    pub min_send_interval_ms: u64,
    pub max_queued: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MessageDeliveryConfigError {
    FlowNotReady(DeliveryFlowKey),
    InvalidBotPolicy,
    InvalidContextPolicy,
}

impl std::fmt::Display for MessageDeliveryConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::FlowNotReady(flow) => write!(f, "queue_flow_not_ready: {flow:?}"),
            Self::InvalidBotPolicy => write!(f, "invalid message_delivery Bot policy"),
            Self::InvalidContextPolicy => write!(f, "invalid context limits: messages must be 1..=1024 and bytes 512..=16777216"),
        }
    }
}

impl std::error::Error for MessageDeliveryConfigError {}

impl MessageDeliveryConfig {
    /// Call at startup and before any configuration replacement. Existing
    /// persisted work is restored independently of this admission policy.
    pub fn validate_ready_flows(
        &self,
        ready: &[DeliveryFlowKey],
    ) -> Result<(), MessageDeliveryConfigError> {
        if self
            .queue_ttl_ms
            .is_some_and(|ttl| ttl == 0 || ttl > i64::MAX as u64)
            || self.safe_retry.as_ref().is_some_and(|retry| {
                retry.max_retries > 10
                    || retry.backoff_ms == 0
                    || retry.backoff_ms > i64::MAX as u64
            })
        {
            return Err(MessageDeliveryConfigError::InvalidBotPolicy);
        }
        for flow in [
            DeliveryFlowKey::Group,
            DeliveryFlowKey::DirectA2a,
            DeliveryFlowKey::Task,
            DeliveryFlowKey::System,
            DeliveryFlowKey::StateMachine,
        ] {
            if self.flow_enabled.enabled(flow) && !ready.contains(&flow) {
                return Err(MessageDeliveryConfigError::FlowNotReady(flow));
            }
        }
        for (bot_id, policy) in &self.bots {
            if bot_id.trim().is_empty()
                || bot_id != bot_id.trim()
                || policy.max_running == 0
                || policy.max_queued == 0
                || policy.min_send_interval_ms > i64::MAX as u64
            {
                return Err(MessageDeliveryConfigError::InvalidBotPolicy);
            }
        }
        Ok(())
    }

    /// Only for *new* server-classified work, never terminal routing, restore,
    /// cancellation or draining a previously admitted delivery.
    pub fn manages_new_delivery(&self, bot_id: &str, flow: DeliveryFlowKey) -> bool {
        self.flow_enabled.enabled(flow)
            && self
                .bots
                .get(bot_id)
                .is_some_and(|bot| bot.mode == BotDeliveryMode::Enforce)
    }
}
