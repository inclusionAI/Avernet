//! Caller configuration delivery gate (P1.4 production consumer).
//!
//! Until P2 wires the protected caller send path, no Bot with an enabled
//! caller switch may be content-dispatched: Owner/ordinary transports must
//! never silently execute caller-enabled work. This decorator wraps every
//! `BotDeliveryPort` and consults the authoritative configuration through
//! the control-plane core on every content delivery:
//!
//! - unconfigured or disabled → pass through unchanged;
//! - enabled → `delivered=false` with the closed
//!   `CALLER_EXECUTION_UNAVAILABLE` rejection, without touching the
//!   transport;
//! - unreadable configuration (storage/parse failure) → fail closed the same
//!   way, never an owner dispatch;
//! - typed aborts and availability checks read no configuration: a disabled
//!   or broken configuration must never make cancellation impossible.
//!
//! The ready/ready-vs-blocked computation is the frozen domain view
//! (`BotCallerEffectiveStatus::merge`, spec §10.7.3) with the P1 execution
//! chain marked not ready: P2 replaces the readiness inputs with the real
//! gateway/identity chain instead of a new policy. This is a dispatch-stage
//! protection only — it is not an admission snapshot, not revocation
//! fencing, and it cannot be installed over the memory composition, where
//! the control plane has no caller configuration storage.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{BotCallerEffectiveStatus, BotDeliveryTarget};
use bcs_service_api::port::delivery::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryPort,
    BotDeliveryResult,
};
use bcs_service_api::{BotControlPlaneCoreService, ServiceError};

/// Closed rejection code for caller-enabled work that has no ready
/// execution path yet; never retried as an ordinary failure.
pub const CALLER_EXECUTION_UNAVAILABLE: &str = "CALLER_EXECUTION_UNAVAILABLE";

pub struct CallerConfigurationDeliveryGate {
    inner: Arc<dyn BotDeliveryPort>,
    control_plane: Arc<dyn BotControlPlaneCoreService>,
    env: String,
}

impl CallerConfigurationDeliveryGate {
    /// The env is resolved once per composition; the control plane scopes
    /// every read to it.
    pub fn new(
        inner: Arc<dyn BotDeliveryPort>,
        control_plane: Arc<dyn BotControlPlaneCoreService>,
    ) -> Self {
        Self {
            inner,
            control_plane,
            env: bcs_config::resolve_env_str(),
        }
    }

    fn refusal(target_bot_id: &str) -> BotDeliveryResult {
        BotDeliveryResult {
            target_bot_id: target_bot_id.to_string(),
            delivered: false,
            error: Some(ServiceError::InvalidOperation {
                message: CALLER_EXECUTION_UNAVAILABLE.to_string(),
                request_id: None,
            }),
        }
    }

    /// True when the target must not be content-dispatched on this chain.
    /// Every blocking decision is logged: the caller switch is a control-plane
    /// fact far away from the refused delivery, so the refusal site must
    /// carry both the decision and its reason (closed code, never identity
    /// material) or the failure is undiagnosable from logs alone.
    async fn caller_dispatch_blocked(&self, bot_id: &str) -> bool {
        match self
            .control_plane
            .get_caller_configuration(bot_id, &self.env)
            .await
        {
            Ok(Some(config)) => match config.snapshot {
                None => false,
                Some(snapshot) => {
                    let blocked = !matches!(
                        BotCallerEffectiveStatus::merge(
                            &snapshot.caller_identity,
                            None,
                            // P1: no ready caller execution chain exists yet.
                            false,
                        ),
                        BotCallerEffectiveStatus::Disabled
                    );
                    if blocked {
                        tracing::warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            target_bot_id = %bot_id,
                            code = CALLER_EXECUTION_UNAVAILABLE,
                            "caller delivery gate: caller-enabled bot blocks ordinary content dispatch"
                        );
                    }
                    blocked
                }
            },
            // Unknown targets are the transport's own concern.
            Ok(None) => false,
            // Unreadable configuration is never a silent downgrade.
            Err(error) => {
                tracing::warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    target_bot_id = %bot_id,
                    code = CALLER_EXECUTION_UNAVAILABLE,
                    error = %error,
                    "caller delivery gate: unreadable caller configuration fails closed"
                );
                true
            }
        }
    }
}

#[async_trait]
impl BotDeliveryPort for CallerConfigurationDeliveryGate {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool {
        // Connection state only; never a caller readiness signal.
        self.inner.is_available(target).await
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> bcs_service_api::ServiceResult<BotDeliveryResult> {
        if self.caller_dispatch_blocked(cmd.target_bot_id()).await {
            return Ok(Self::refusal(cmd.target_bot_id()));
        }
        self.inner.deliver(cmd).await
    }

    async fn connection_identity(&self, target: &BotDeliveryTarget) -> Option<String> {
        self.inner.connection_identity(target).await
    }

    async fn deliver_on_connection(
        &self,
        cmd: BotDeliveryCommand,
        connection_id: &str,
    ) -> bcs_service_api::ServiceResult<BotDeliveryResult> {
        if self.caller_dispatch_blocked(cmd.target_bot_id()).await {
            return Ok(Self::refusal(cmd.target_bot_id()));
        }
        self.inner.deliver_on_connection(cmd, connection_id).await
    }

    async fn abort(
        &self,
        cmd: BotAbortDeliveryCommand,
    ) -> bcs_service_api::ServiceResult<BotAbortDeliveryResult> {
        self.inner.abort(cmd).await
    }

    async fn abort_on_connection(
        &self,
        cmd: BotAbortDeliveryCommand,
        connection_id: &str,
    ) -> bcs_service_api::ServiceResult<BotAbortDeliveryResult> {
        self.inner.abort_on_connection(cmd, connection_id).await
    }
}
