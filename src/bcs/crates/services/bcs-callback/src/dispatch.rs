//! New callback dispatcher tied to `Session.callback_status`.
//!
//! Fires after a service-invocation [`Session`] completes. Iterates every
//! channel in the group's `service_spec.callback_config.channels`
//! concurrently and updates [`Session::callback_status`] via
//! [`SessionRepoPort::update_callback_status`].
//!
//! Ported from legacy `bcs/src/callback/mod.rs` and adapted to the
//! new architecture — uses `SessionRepoPort` instead of the old
//! monolithic `bcs_services::SessionService`.

use crate::{antding, baas};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::application::session::SessionManagementService;
use bcs_service_api::{
    CallbackChannelConfig, CallbackConfig, GroupCoreService, Session, SessionKind,
};
use futures::future::join_all;
use std::sync::Arc;
use tracing::{info, warn};

/// Aggregate channel result mapped to `Session.callback_status` per
/// spec §9.5.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AggregateStatus {
    /// All channels returned Ok.
    Succeeded,
    /// At least one channel returned Ok and at least one returned Err.
    PartialFailed,
    /// Every channel returned Err.
    Failed,
}

impl AggregateStatus {
    pub fn as_status_string(self) -> &'static str {
        match self {
            Self::Succeeded => "succeeded",
            Self::PartialFailed => "partial_failed",
            Self::Failed => "failed",
        }
    }
}

/// Aggregate the per-channel results into a single status. Empty
/// `results` returns `None` (caller decides whether to leave the
/// session's `callback_status` unchanged).
pub fn aggregate_results(results: &[Result<(), String>]) -> Option<AggregateStatus> {
    if results.is_empty() {
        return None;
    }
    let succeeded = results.iter().filter(|r| r.is_ok()).count();
    let failed = results.iter().filter(|r| r.is_err()).count();
    Some(if succeeded == results.len() {
        AggregateStatus::Succeeded
    } else if failed == results.len() {
        AggregateStatus::Failed
    } else {
        AggregateStatus::PartialFailed
    })
}

/// Build the JSON payload for downstream channels. Mirrors the spec's
/// invocation envelope (§9.5).
pub fn build_payload(
    session: &Session,
    activation_output: Option<&serde_json::Value>,
    activation_error: Option<&str>,
) -> serde_json::Value {
    serde_json::json!({
        "session_id": session.id,
        "group_id": session.group_id,
        "status": session.status,
        "output": activation_output,
        "error": activation_error,
        "activation_count": session.activation_count,
        "completed_at": session.completed_at,
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ChannelFailureLogRecord {
    index: usize,
    channel_type: &'static str,
    target: ChannelFailureTarget,
    error: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum ChannelFailureTarget {
    AntDing {
        robot_code: String,
        user_id_present: bool,
        open_conversation_id_present: bool,
    },
    Baas {
        base_url: String,
        bot_id: String,
    },
}

impl ChannelFailureTarget {
    fn from_channel(channel: &CallbackChannelConfig) -> Self {
        match channel {
            CallbackChannelConfig::AntDing {
                robot_code,
                user_id,
                open_conversation_id,
                ..
            } => Self::AntDing {
                robot_code: robot_code.clone(),
                user_id_present: user_id.as_deref().is_some_and(|value| !value.is_empty()),
                open_conversation_id_present: open_conversation_id
                    .as_deref()
                    .is_some_and(|value| !value.is_empty()),
            },
            CallbackChannelConfig::Baas {
                base_url,
                bot_id,
                ..
            } => Self::Baas {
                base_url: base_url.clone(),
                bot_id: bot_id.clone(),
            },
        }
    }
}

fn channel_failure_log_records(
    channels: &[CallbackChannelConfig],
    results: &[Result<(), String>],
) -> Vec<ChannelFailureLogRecord> {
    channels
        .iter()
        .zip(results.iter())
        .enumerate()
        .filter_map(|(index, (channel, result))| {
            let Err(error) = result else {
                return None;
            };
            Some(ChannelFailureLogRecord {
                index,
                channel_type: channel.type_name(),
                target: ChannelFailureTarget::from_channel(channel),
                error: error.clone(),
            })
        })
        .collect()
}

fn log_channel_failures(
    session: &Session,
    channels: &[CallbackChannelConfig],
    results: &[Result<(), String>],
) {
    for failure in channel_failure_log_records(channels, results) {
        let ChannelFailureLogRecord {
            index,
            channel_type,
            target,
            error,
        } = failure;
        match target {
            ChannelFailureTarget::AntDing {
                robot_code,
                user_id_present,
                open_conversation_id_present,
            } => warn!(
                target: "callback",
                event = "callback.channel_failed",
                session_id = %session.id,
                activation_seq = session.activation_count,
                channel_index = index,
                channel_type = channel_type,
                robot_code = %robot_code,
                user_id_present = user_id_present,
                open_conversation_id_present = open_conversation_id_present,
                error = %error,
            ),
            ChannelFailureTarget::Baas { base_url, bot_id } => warn!(
                target: "callback",
                event = "callback.channel_failed",
                session_id = %session.id,
                activation_seq = session.activation_count,
                channel_index = index,
                channel_type = channel_type,
                base_url = %base_url,
                bot_id = %bot_id,
                error = %error,
            ),
        }
    }
}

/// Dispatch the post-completion callback for a service-invocation
/// session.
///
/// - Concurrently fan out to every entry in `config.channels`.
/// - Aggregate the results into one of `succeeded` / `partial_failed`
///   / `failed`.
/// - Update `Session.callback_status` via
///   [`SessionRepoPort::update_callback_status`].
///
/// Empty channel list is a no-op (callback_status remains as the caller
/// left it — typically `pending` from `Session::create`).
pub async fn dispatch_callback(
    session: Session,
    config: CallbackConfig,
    session_mgmt: Arc<dyn SessionManagementService>,
) {
    dispatch_callback_with_url_guard(session, config, session_mgmt, OutboundUrlGuard::strict())
        .await
}

pub async fn dispatch_callback_with_url_guard(
    session: Session,
    config: CallbackConfig,
    session_mgmt: Arc<dyn SessionManagementService>,
    url_guard: OutboundUrlGuard,
) {
    if config.channels.is_empty() {
        info!(
            target: "callback",
            event = "callback.skipped",
            session_id = %session.id,
            reason = "no channels configured",
        );
        return;
    }

    let payload = build_payload(
        &session,
        session.output.as_ref(),
        session.error_message.as_deref(),
    );

    // The message text sent to the channel is the human-readable output.
    // If output is a plain string, use it directly; if it's a JSON object,
    // pretty-print it. Fall back to error_message, then the full payload.
    let message_text = match &session.output {
        Some(v) => {
            if let Some(s) = v.as_str() {
                s.to_string()
            } else {
                serde_json::to_string_pretty(v).unwrap_or_else(|_| v.to_string())
            }
        }
        None => session
            .error_message
            .clone()
            .unwrap_or_else(|| payload.to_string()),
    };
    let meta = session.meta.clone();

    let tasks: Vec<_> = config
        .channels
        .iter()
        .cloned()
        .map(|ch| {
            let text = message_text.clone();
            let m = meta.clone();
            let guard = url_guard.clone();
            async move { send_one_channel(&ch, &text, m.as_ref(), &guard).await }
        })
        .collect();

    let results = join_all(tasks).await;
    log_channel_failures(&session, &config.channels, &results);

    let status = match aggregate_results(&results) {
        Some(s) => s,
        None => {
            warn!(
                target: "callback",
                event = "callback.no_results",
                session_id = %session.id,
            );
            return;
        }
    };

    let status_str = status.as_status_string();
    if let Err(e) = session_mgmt
        .update_callback_status(&session.id, status_str)
        .await
    {
        warn!(
            target: "callback",
            event = "callback.update_failed",
            session_id = %session.id,
            error = %e,
            attempted_status = status_str,
        );
        return;
    }

    info!(
        target: "callback",
        event = "callback.dispatched",
        session_id = %session.id,
        activation_seq = session.activation_count,
        status = status_str,
        channels = config.channels.len(),
    );
}

async fn send_one_channel(
    channel: &CallbackChannelConfig,
    payload_text: &str,
    meta: Option<&serde_json::Value>,
    url_guard: &OutboundUrlGuard,
) -> Result<(), String> {
    match channel {
        CallbackChannelConfig::AntDing { .. } => {
            antding::send(channel, payload_text, meta).await
        }
        CallbackChannelConfig::Baas { .. } => {
            baas::send_with_url_guard(channel, payload_text, meta, url_guard).await
        }
    }
}

/// Convenience: spawn [`dispatch_callback`] if the group has a
/// `service_spec.callback_config`. Skips when no callback is configured
/// or the session is not a service invocation.
pub fn maybe_dispatch_for_session(
    session: Session,
    group_svc: Arc<dyn GroupCoreService>,
    session_mgmt: Arc<dyn SessionManagementService>,
) {
    maybe_dispatch_for_session_with_url_guard(
        session,
        group_svc,
        session_mgmt,
        OutboundUrlGuard::strict(),
    );
}

pub fn maybe_dispatch_for_session_with_url_guard(
    session: Session,
    group_svc: Arc<dyn GroupCoreService>,
    session_mgmt: Arc<dyn SessionManagementService>,
    url_guard: OutboundUrlGuard,
) {
    if !matches!(session.session_kind, SessionKind::ServiceInvocation) {
        return;
    }

    info!(
        target: "callback",
        event = "callback.maybe_dispatch",
        session_id = %session.id,
        group_id = %session.group_id,
        "dispatching post-completion callback",
    );

    tokio::spawn(async move {
        let group = match group_svc.get(&session.group_id).await {
            Some(g) => g,
            None => {
                warn!(
                    target: "callback",
                    event = "callback.skipped",
                    session_id = %session.id,
                    reason = "group not found",
                );
                return;
            }
        };
        let cb = match group
            .service_spec
            .as_ref()
            .and_then(|s| s.callback_config.clone())
        {
            Some(cb) => cb,
            None => {
                info!(
                    target: "callback",
                    event = "callback.skipped",
                    session_id = %session.id,
                    reason = "no callback_config",
                );
                return;
            }
        };
        dispatch_callback_with_url_guard(session, cb, session_mgmt, url_guard).await;
    });
}

/// Default [`SessionCallbackDispatchPort`] backed by the core group service.
pub struct SessionCallbackDispatcher {
    group_svc: Arc<dyn GroupCoreService>,
    url_guard: OutboundUrlGuard,
}

impl SessionCallbackDispatcher {
    pub fn new(group_svc: Arc<dyn GroupCoreService>, url_guard: OutboundUrlGuard) -> Self {
        Self { group_svc, url_guard }
    }
}

#[async_trait::async_trait]
impl bcs_service_api::SessionCallbackDispatchPort for SessionCallbackDispatcher {
    async fn maybe_dispatch(
        &self,
        session: Session,
        session_management: Arc<dyn SessionManagementService>,
    ) {
        maybe_dispatch_for_session_with_url_guard(
            session,
            self.group_svc.clone(),
            session_management,
            self.url_guard.clone(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn channel_failure_log_records_include_channel_type_target_and_error() {
        let channels = vec![
            CallbackChannelConfig::Baas {
                base_url: "https://baas.example.com".to_string(),
                api_key: "sk-test".to_string(),
                bot_id: "default:151614".to_string(),
                metadata: None,
            },
            CallbackChannelConfig::AntDing {
                access_key_id: "ak-test".to_string(),
                access_key_secret: "secret-test".to_string(),
                robot_code: "robot-test".to_string(),
                user_id: None,
                open_conversation_id: Some("cid-test".to_string()),
            },
        ];
        let results = vec![
            Ok(()),
            Err("antding callback business error: missing user".to_string()),
        ];

        let failures = channel_failure_log_records(&channels, &results);

        assert_eq!(failures.len(), 1);
        assert_eq!(failures[0].index, 1);
        assert_eq!(failures[0].channel_type, "antding");
        assert_eq!(
            failures[0].error,
            "antding callback business error: missing user"
        );
        match &failures[0].target {
            ChannelFailureTarget::AntDing {
                robot_code,
                user_id_present,
                open_conversation_id_present,
            } => {
                assert_eq!(robot_code, "robot-test");
                assert!(!user_id_present);
                assert!(*open_conversation_id_present);
            }
            ChannelFailureTarget::Baas { .. } => panic!("expected antding target details"),
        }
    }
}
