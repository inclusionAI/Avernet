//! Prometheus metrics runtime and label helpers.

#[cfg(feature = "prometheus-metrics")]
#[path = "state_machine_metrics.rs"]
mod state_machine_metrics;
#[cfg(feature = "prometheus-metrics")]
pub use state_machine_metrics::MetricsStateMachineLoopHook;

use std::sync::Arc;
use std::time::Duration;

#[cfg(feature = "prometheus-metrics")]
use tracing::warn;

use crate::config::BcsConfig;
#[cfg(feature = "prometheus-metrics")]
use crate::config::DatabaseType;
use crate::{BcsError, Result};
use axum::http::StatusCode;
#[cfg(feature = "prometheus-metrics")]
use bcs_ws::{bot::BOT_WS_ENDPOINT, web::FRONTEND_WS_ENDPOINT};
#[cfg(feature = "prometheus-metrics")]
use bcs_service_api::{
    A2aChatRunService, A2aRunStatus, ActorKind, ActorStatus, AsyncA2aChatAccepted, AsyncA2aChatCommand,
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget, BotEventCommand, BotEventOutcome,
    BotMetricCount, BotMetricsSnapshotPort,
    CancelLatestQueuedMessageCommand, CancelLatestQueuedMessageOutcome, ChatAbortCommand,
    ChatAbortOutcome, ChatRunCancelCommand, ChatRunMetricCount, ChatRunQueryCommand,
    DeliveryBlockContext, DeliveryBlockReason, DeliveryMetricKind, DeliveryMetricTarget,
    DeliveryPolicyBlockInstrumentationHook, DirectChatClientKind,
    DirectChatRunEvent, DirectChatRunLifecycleHook, DirectChatRunReason,
    DirectChatRunSnapshotPort, DirectChatRunState, DmCreateCommand, DmCreateResult,
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort, FrontendDeliveryResult,
    GroupAddMemberCommand, GroupAddMemberResult, GroupCallbackCommand, GroupCallbackOutcome,
    GroupChatCommand, GroupChatOutcome, GroupCreateCommand, GroupDeleteCommand, GroupDeleteResult,
    GroupRemoveMemberCommand, GroupRemoveMemberResult,
    GroupDetailResult, GroupKind, GroupManagementService, GroupMetricCount,
    GroupMetricsSnapshotPort, GroupParticipantModeCommand, GroupParticipantModeResult,
    GroupRoutingPolicyCommand, GroupRoutingPolicyResult, GroupSessionMetricCount,
    GroupSessionMetricsSnapshotPort, GroupStatus, GroupStatusCommand,
    GroupStrategy, GroupTerminateCommand, GroupUpdateLabelCommand, GroupUpdateVisibilityCommand,
    GroupUseCaseError, MessageFlowService, MetricsResult,
    PersistentGroupSendCommand, PersistentGroupSendOutcome, ServiceError, ServiceResult,
    SessionKind, SessionStatus, TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand,
    TaskDispatchOutcome, TaskMessageCommand, TaskMessageOutcome, TaskRunAliasRegistration,
    WebSendCommand, WebSendOutcome,
    WsCloseReason, WsErrorKind, WsLifecycleInstrumentationHook, WsPeer,
};

pub const HTTP_DURATION_BUCKETS_SECONDS: &[f64] = &[
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
];
pub const WS_CONNECTION_DURATION_BUCKETS_SECONDS: &[f64] = &[
    1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0, 7200.0, 21600.0,
];
pub const DELIVERY_DURATION_BUCKETS_SECONDS: &[f64] = &[
    0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
];

#[cfg(feature = "prometheus-metrics")]
use std::sync::{Mutex as StdMutex, OnceLock};
#[cfg(feature = "prometheus-metrics")]
use std::time::Instant;
#[cfg(feature = "prometheus-metrics")]
use tokio::sync::{Mutex as TokioMutex, watch};
#[cfg(feature = "prometheus-metrics")]
use tokio::task::JoinHandle;

#[cfg(feature = "prometheus-metrics")]
use metrics_exporter_prometheus::{Matcher, PrometheusBuilder, PrometheusHandle};

#[cfg(feature = "prometheus-metrics")]
static PROMETHEUS_HANDLE: OnceLock<PrometheusHandle> = OnceLock::new();

#[cfg(feature = "prometheus-metrics")]
struct SnapshotCache<T> {
    last_refresh: Option<Instant>,
    last_counts: Vec<T>,
}

#[cfg(feature = "prometheus-metrics")]
impl<T> Default for SnapshotCache<T> {
    fn default() -> Self {
        Self {
            last_refresh: None,
            last_counts: Vec::new(),
        }
    }
}

#[cfg(feature = "prometheus-metrics")]
impl<T> SnapshotCache<T> {
    fn is_fresh(&self, ttl: Duration) -> bool {
        self.last_refresh
            .map(|last| last.elapsed() < ttl)
            .unwrap_or(false)
    }
}

pub struct MetricsRuntime {
    pub env: Arc<str>,
    pub endpoint_path: String,
    #[cfg(feature = "prometheus-metrics")]
    handle: PrometheusHandle,
    #[cfg(feature = "prometheus-metrics")]
    group_snapshot: TokioMutex<SnapshotCache<GroupMetricCount>>,
    #[cfg(feature = "prometheus-metrics")]
    group_session_snapshot: TokioMutex<SnapshotCache<GroupSessionMetricCount>>,
    #[cfg(feature = "prometheus-metrics")]
    bot_snapshot: TokioMutex<SnapshotCache<BotMetricCount>>,
    #[cfg(feature = "prometheus-metrics")]
    direct_chat_snapshot: TokioMutex<SnapshotCache<ChatRunMetricCount>>,
    #[cfg(feature = "prometheus-metrics")]
    shutdown_tx: watch::Sender<bool>,
    #[cfg(feature = "prometheus-metrics")]
    upkeep_task: StdMutex<Option<JoinHandle<()>>>,
}

impl MetricsRuntime {
    pub fn install(config: &BcsConfig) -> Result<Option<Arc<Self>>> {
        config
            .validate_metrics()
            .map_err(BcsError::InvalidConfig)?;
        if !config.metrics.enabled {
            return Ok(None);
        }

        #[cfg(not(feature = "prometheus-metrics"))]
        {
            Err(BcsError::InvalidConfig(
                "metrics.enabled=true requires the bcs prometheus-metrics Cargo feature"
                    .to_string(),
            ))
        }

        #[cfg(feature = "prometheus-metrics")]
        {
            let handle = install_recorder_once()?;
            let (shutdown_tx, shutdown_rx) = watch::channel(false);
            let upkeep_task = spawn_upkeep_task(handle.clone(), shutdown_rx);
            let runtime = Self {
                env: Arc::from(bcs_config::resolve_env_str()),
                endpoint_path: config.metrics.endpoint_path.clone(),
                handle,
                group_snapshot: TokioMutex::new(SnapshotCache::default()),
                group_session_snapshot: TokioMutex::new(SnapshotCache::default()),
                bot_snapshot: TokioMutex::new(SnapshotCache::default()),
                direct_chat_snapshot: TokioMutex::new(SnapshotCache::default()),
                shutdown_tx,
                upkeep_task: StdMutex::new(Some(upkeep_task)),
            };
            runtime.initialize_static_gauges();
            Ok(Some(Arc::new(runtime)))
        }
    }

    pub async fn refresh_on_scrape(&self, state: &crate::server::BcsServerState) {
        #[cfg(feature = "prometheus-metrics")]
        {
            self.record_runtime_info(&state.config);
            let is_leader = match state.leader_election.is_leader().await {
                Ok(value) => value,
                Err(error) => {
                    warn!(error = %error, "failed to resolve leader status for metrics");
                    false
                }
            };
            metrics::gauge!("bcs_is_leader_current", "env" => self.env.to_string())
                .set(if is_leader { 1.0 } else { 0.0 });
            self.refresh_group_snapshot(state.group_metrics_snapshot.as_ref())
                .await;
            self.refresh_group_session_snapshot(state.group_session_metrics_snapshot.as_ref())
                .await;
            self.refresh_bot_snapshot(state.bot_metrics_snapshot.as_ref())
                .await;
            self.refresh_direct_chat_snapshot(state.direct_chat_run_snapshot.as_ref())
                .await;
        }

        #[cfg(not(feature = "prometheus-metrics"))]
        {
            let _ = state;
        }
    }

    pub fn render(&self) -> String {
        #[cfg(feature = "prometheus-metrics")]
        {
            return self.handle.render();
        }

        #[cfg(not(feature = "prometheus-metrics"))]
        {
            String::new()
        }
    }

    pub async fn shutdown(&self) {
        #[cfg(feature = "prometheus-metrics")]
        {
            let _ = self.shutdown_tx.send(true);
            let task = self
                .upkeep_task
                .lock()
                .ok()
                .and_then(|mut guard| guard.take());
            if let Some(task) = task {
                let _ = task.await;
            }
        }
    }

    #[cfg(feature = "prometheus-metrics")]
    fn initialize_static_gauges(&self) {
        self.record_build_info();
        metrics::gauge!(
            "bcs_ws_connections_current",
            "env" => self.env.to_string(),
            "peer" => "bot",
            "endpoint" => BOT_WS_ENDPOINT,
        )
        .set(0.0);
        metrics::gauge!(
            "bcs_ws_connections_current",
            "env" => self.env.to_string(),
            "peer" => "frontend",
            "endpoint" => FRONTEND_WS_ENDPOINT,
        )
        .set(0.0);
    }

    #[cfg(feature = "prometheus-metrics")]
    fn record_build_info(&self) {
        metrics::gauge!(
            "bcs_build_info",
            "env" => self.env.to_string(),
            "version" => env!("CARGO_PKG_VERSION"),
            "commit" => option_env!("GIT_COMMIT_HASH").unwrap_or("unknown"),
        )
        .set(1.0);
    }

    #[cfg(feature = "prometheus-metrics")]
    fn record_runtime_info(&self, config: &BcsConfig) {
        metrics::gauge!(
            "bcs_runtime_info",
            "env" => self.env.to_string(),
            "cache" => cache_label(config),
            "storage" => storage_label(config),
            "metrics_mode" => "pull",
        )
        .set(1.0);
    }

    #[cfg(feature = "prometheus-metrics")]
    async fn refresh_group_snapshot(&self, port: &dyn GroupMetricsSnapshotPort) {
        self.refresh_group_snapshot_inner(port, false).await;
    }

    #[cfg(all(feature = "prometheus-metrics", any(test, feature = "test-utils")))]
    pub async fn refresh_group_snapshot_for_test(
        &self,
        port: &dyn GroupMetricsSnapshotPort,
    ) {
        self.refresh_group_snapshot_inner(port, true).await;
    }

    #[cfg(feature = "prometheus-metrics")]
    async fn refresh_group_snapshot_inner(
        &self,
        port: &dyn GroupMetricsSnapshotPort,
        force: bool,
    ) {
        let mut cache = self.group_snapshot.lock().await;
        if !force && cache.is_fresh(Duration::from_secs(10)) {
            return;
        }

        match port.group_counts().await {
            Ok(counts) => {
                for previous in &cache.last_counts {
                    self.record_group_count(previous, 0.0);
                }
                for count in &counts {
                    self.record_group_count(count, count.count as f64);
                }
                cache.last_counts = counts;
                cache.last_refresh = Some(Instant::now());
            }
            Err(error) => {
                warn!(error = %error, "failed to refresh group metrics snapshot");
            }
        }
    }

    #[cfg(feature = "prometheus-metrics")]
    async fn refresh_group_session_snapshot(&self, port: &dyn GroupSessionMetricsSnapshotPort) {
        self.refresh_group_session_snapshot_inner(port, false).await;
    }

    #[cfg(all(feature = "prometheus-metrics", any(test, feature = "test-utils")))]
    pub async fn refresh_group_session_snapshot_for_test(
        &self,
        port: &dyn GroupSessionMetricsSnapshotPort,
    ) {
        self.refresh_group_session_snapshot_inner(port, true).await;
    }

    #[cfg(feature = "prometheus-metrics")]
    async fn refresh_group_session_snapshot_inner(
        &self,
        port: &dyn GroupSessionMetricsSnapshotPort,
        force: bool,
    ) {
        let mut cache = self.group_session_snapshot.lock().await;
        if !force && cache.is_fresh(Duration::from_secs(10)) {
            return;
        }

        match port.group_session_counts().await {
            Ok(counts) => {
                for previous in &cache.last_counts {
                    self.record_group_session_count(previous, 0.0);
                }
                for count in &counts {
                    self.record_group_session_count(count, count.count as f64);
                }
                cache.last_counts = counts;
                cache.last_refresh = Some(Instant::now());
            }
            Err(error) => {
                warn!(error = %error, "failed to refresh group session metrics snapshot");
            }
        }
    }

    #[cfg(feature = "prometheus-metrics")]
    async fn refresh_bot_snapshot(&self, port: &dyn BotMetricsSnapshotPort) {
        self.refresh_bot_snapshot_inner(port, false).await;
    }

    #[cfg(all(feature = "prometheus-metrics", any(test, feature = "test-utils")))]
    pub async fn refresh_bot_snapshot_for_test(
        &self,
        port: &dyn BotMetricsSnapshotPort,
    ) {
        self.refresh_bot_snapshot_inner(port, true).await;
    }

    #[cfg(feature = "prometheus-metrics")]
    async fn refresh_bot_snapshot_inner(
        &self,
        port: &dyn BotMetricsSnapshotPort,
        force: bool,
    ) {
        let mut cache = self.bot_snapshot.lock().await;
        if !force && cache.is_fresh(Duration::from_secs(10)) {
            return;
        }

        match port.bot_counts().await {
            Ok(counts) => {
                for previous in &cache.last_counts {
                    self.record_bot_count(previous, 0.0);
                }
                for count in &counts {
                    self.record_bot_count(count, count.count as f64);
                }
                cache.last_counts = counts;
                cache.last_refresh = Some(Instant::now());
            }
            Err(error) => {
                warn!(error = %error, "failed to refresh bot metrics snapshot");
            }
        }
    }

    #[cfg(feature = "prometheus-metrics")]
    async fn refresh_direct_chat_snapshot(&self, port: &dyn DirectChatRunSnapshotPort) {
        self.refresh_direct_chat_snapshot_inner(port, false).await;
    }

    #[cfg(all(feature = "prometheus-metrics", any(test, feature = "test-utils")))]
    pub async fn refresh_direct_chat_snapshot_for_test(
        &self,
        port: &dyn DirectChatRunSnapshotPort,
    ) {
        self.refresh_direct_chat_snapshot_inner(port, true).await;
    }

    #[cfg(feature = "prometheus-metrics")]
    async fn refresh_direct_chat_snapshot_inner(
        &self,
        port: &dyn DirectChatRunSnapshotPort,
        force: bool,
    ) {
        let mut cache = self.direct_chat_snapshot.lock().await;
        if !force && cache.is_fresh(Duration::from_secs(5)) {
            return;
        }

        match port.direct_chat_run_counts().await {
            Ok(counts) => {
                for previous in &cache.last_counts {
                    self.record_direct_chat_count(previous, 0.0);
                }
                for count in &counts {
                    self.record_direct_chat_count(count, count.count as f64);
                }
                cache.last_counts = counts;
                cache.last_refresh = Some(Instant::now());
            }
            Err(error) => {
                warn!(error = %error, "failed to refresh direct chat run metrics snapshot");
            }
        }
    }

    #[cfg(feature = "prometheus-metrics")]
    fn record_group_count(&self, count: &GroupMetricCount, value: f64) {
        metrics::gauge!(
            "bcs_groups_current",
            "env" => self.env.to_string(),
            "status" => group_status_label(count.status),
            "kind" => group_kind_label(count.kind),
            "group_strategy" => group_strategy_label(count.group_strategy),
            "service_mode" => service_mode_label(count.service_mode.as_deref()),
        )
        .set(value);
    }

    #[cfg(feature = "prometheus-metrics")]
    fn record_group_session_count(&self, count: &GroupSessionMetricCount, value: f64) {
        metrics::gauge!(
            "bcs_group_sessions_current",
            "env" => self.env.to_string(),
            "status" => session_status_label(count.status),
            "session_kind" => session_kind_label(count.session_kind),
        )
        .set(value);
    }

    #[cfg(feature = "prometheus-metrics")]
    fn record_bot_count(&self, count: &BotMetricCount, value: f64) {
        metrics::gauge!(
            "bcs_bots_current",
            "env" => self.env.to_string(),
            "actor_kind" => actor_kind_label(count.actor_kind),
            "status" => actor_status_label(count.status),
            "visibility" => visibility_label(count.visibility.as_deref()),
        )
        .set(value);
    }

    #[cfg(feature = "prometheus-metrics")]
    fn record_direct_chat_count(&self, count: &ChatRunMetricCount, value: f64) {
        metrics::gauge!(
            "bcs_direct_chat_runs_current",
            "env" => self.env.to_string(),
            "state" => direct_chat_state_label(count.state),
            "client_kind" => direct_chat_client_kind_label(count.client_kind),
        )
        .set(value);
    }

    pub fn record_http_request(
        &self,
        route: &str,
        method: &str,
        status: StatusCode,
        duration: Duration,
    ) {
        record_http_request(&self.env, route, method, status, duration);
    }

    pub fn record_http_request_cancelled(
        &self,
        route: &str,
        method: &str,
        duration: Duration,
    ) {
        record_http_request_cancelled(&self.env, route, method, duration);
    }
}

#[cfg(feature = "prometheus-metrics")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum GroupSessionEvent {
    Created,
    Completed,
    Closed,
    Deleted,
    MemberAdded,
    MemberRemoved,
    StatusUpdated,
}

#[cfg(feature = "prometheus-metrics")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MessageFlowSource {
    WebWs,
    Http,
    BotWs,
}

#[cfg(feature = "prometheus-metrics")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MessageFlowOperation {
    WebSend,
    GroupChat,
    PersistentGroupSend,
    BotEvent,
    GroupCallback,
    ChatAbort,
    TaskDispatch,
    TaskMessage,
    TaskComplete,
    DirectChat,
}

#[cfg(feature = "prometheus-metrics")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DeliveryOutcome {
    Delivered,
    Failed,
    NoReceivers,
    Blocked,
}

#[cfg(feature = "prometheus-metrics")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DeliveryErrorCode {
    None,
    NotFound,
    NotRegistered,
    NotConnected,
    InvalidOperation,
    Unauthorized,
    Forbidden,
    MessageLimit,
    Internal,
    Io,
    Json,
    CannotAddSelf,
    PendingRequestExists,
    CannotAcceptRejected,
    CannotRejectAccepted,
    NotFriends,
    PrivateBot,
    PolicyBlocked,
    Unknown,
}

#[cfg(feature = "prometheus-metrics")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum WsConnectionEvent {
    Accepted,
    Registered,
    RegisterRejected,
    DispatchError,
    ProtocolError,
    SendError,
    Closed,
}

#[cfg(feature = "prometheus-metrics")]
#[path = "metrics/wrappers.rs"]
mod wrappers;
#[cfg(feature = "prometheus-metrics")]
pub use wrappers::*;

impl Drop for MetricsRuntime {
    fn drop(&mut self) {
        #[cfg(feature = "prometheus-metrics")]
        {
            let _ = self.shutdown_tx.send(true);
            if let Ok(mut task) = self.upkeep_task.lock() {
                if let Some(task) = task.take() {
                    // Drop cannot await the graceful path. BcsServer calls
                    // shutdown() during normal teardown; abort is the fallback
                    // for panic/test-drop paths after signaling the task.
                    task.abort();
                }
            }
        }
    }
}

#[cfg(feature = "prometheus-metrics")]
#[async_trait::async_trait]
impl WsLifecycleInstrumentationHook for MetricsRuntime {
    async fn accepted(&self, peer: WsPeer, endpoint: &'static str) {
        self.record_ws_event(
            peer,
            endpoint,
            WsConnectionEvent::Accepted,
            MetricsResult::Success,
        );
        self.record_ws_current_delta(peer, endpoint, 1.0);
    }

    async fn registered(&self, peer: WsPeer, endpoint: &'static str) {
        self.record_ws_event(
            peer,
            endpoint,
            WsConnectionEvent::Registered,
            MetricsResult::Success,
        );
    }

    async fn error(&self, peer: WsPeer, endpoint: &'static str, kind: WsErrorKind) {
        let event = match kind {
            WsErrorKind::RegisterRejected => WsConnectionEvent::RegisterRejected,
            WsErrorKind::DispatchError => WsConnectionEvent::DispatchError,
            WsErrorKind::ProtocolError => WsConnectionEvent::ProtocolError,
            WsErrorKind::SendError => WsConnectionEvent::SendError,
        };
        self.record_ws_event(peer, endpoint, event, MetricsResult::Error);
    }

    async fn closed(
        &self,
        peer: WsPeer,
        endpoint: &'static str,
        close_reason: WsCloseReason,
        duration: Duration,
    ) {
        self.record_ws_event(
            peer,
            endpoint,
            WsConnectionEvent::Closed,
            MetricsResult::Success,
        );
        self.record_ws_current_delta(peer, endpoint, -1.0);
        metrics::histogram!(
            "bcs_ws_connection_duration_seconds",
            "env" => self.env.to_string(),
            "peer" => ws_peer_label(peer),
            "endpoint" => endpoint,
            "close_reason" => ws_close_reason_label(close_reason),
        )
        .record(duration.as_secs_f64());
    }
}

#[cfg(feature = "prometheus-metrics")]
impl MetricsRuntime {
    fn record_ws_event(
        &self,
        peer: WsPeer,
        endpoint: &'static str,
        event: WsConnectionEvent,
        result: MetricsResult,
    ) {
        metrics::counter!(
            "bcs_ws_connection_events_total",
            "env" => self.env.to_string(),
            "peer" => ws_peer_label(peer),
            "endpoint" => endpoint,
            "event" => ws_event_label(event),
            "result" => metrics_result_label(result),
        )
        .increment(1);
    }

    fn record_ws_current_delta(&self, peer: WsPeer, endpoint: &'static str, delta: f64) {
        metrics::gauge!(
            "bcs_ws_connections_current",
            "env" => self.env.to_string(),
            "peer" => ws_peer_label(peer),
            "endpoint" => endpoint,
        )
        .increment(delta);
    }
}

fn record_http_request(
    env: &str,
    route: &str,
    method: &str,
    status: StatusCode,
    duration: Duration,
) {
    #[cfg(feature = "prometheus-metrics")]
    {
        let status_class = http_status_class(status);
        record_http_request_outcome(
            env,
            route,
            method,
            status_class,
            http_result_label(status),
            duration,
        );
    }

    #[cfg(not(feature = "prometheus-metrics"))]
    {
        let _ = (env, route, method, status, duration);
    }
}

fn record_http_request_cancelled(
    env: &str,
    route: &str,
    method: &str,
    duration: Duration,
) {
    #[cfg(feature = "prometheus-metrics")]
    {
        // A cancelled request has no application response status. Classify
        // the proxy-499 equivalent as a failed 4xx terminal outcome.
        record_http_request_outcome(env, route, method, "4xx", "error", duration);
        metrics::counter!(
            "bcs_http_request_cancellations_total",
            "env" => env.to_string(),
            "route" => route.to_string(),
            "method" => method.to_string(),
        )
        .increment(1);
    }

    #[cfg(not(feature = "prometheus-metrics"))]
    {
        let _ = (env, route, method, duration);
    }
}

#[cfg(feature = "prometheus-metrics")]
fn record_http_request_outcome(
    env: &str,
    route: &str,
    method: &str,
    status_class: &'static str,
    result: &'static str,
    duration: Duration,
) {
    metrics::counter!(
        "bcs_http_requests_total",
        "env" => env.to_string(),
        "route" => route.to_string(),
        "method" => method.to_string(),
        "status_class" => status_class,
        "result" => result,
    )
    .increment(1);
    metrics::histogram!(
        "bcs_http_request_duration_seconds",
        "env" => env.to_string(),
        "route" => route.to_string(),
        "method" => method.to_string(),
        "status_class" => status_class,
    )
    .record(duration.as_secs_f64());
}

#[cfg(feature = "prometheus-metrics")]
fn record_delivery(
    env: &str,
    target: DeliveryMetricTarget,
    delivery_kind: DeliveryMetricKind,
    result: DeliveryOutcome,
    error_code: DeliveryErrorCode,
    duration: Duration,
) {
    record_delivery_count(env, target, delivery_kind, result, error_code);
    metrics::histogram!(
        "bcs_message_delivery_duration_seconds",
        "env" => env.to_string(),
        "target" => delivery_metric_target_label(target),
        "delivery_kind" => delivery_metric_kind_label(delivery_kind),
        "result" => delivery_outcome_label(result),
    )
    .record(duration.as_secs_f64());
}

#[cfg(feature = "prometheus-metrics")]
fn record_delivery_count(
    env: &str,
    target: DeliveryMetricTarget,
    delivery_kind: DeliveryMetricKind,
    result: DeliveryOutcome,
    error_code: DeliveryErrorCode,
) {
    metrics::counter!(
        "bcs_message_delivery_total",
        "env" => env.to_string(),
        "target" => delivery_metric_target_label(target),
        "delivery_kind" => delivery_metric_kind_label(delivery_kind),
        "result" => delivery_outcome_label(result),
        "error_code" => delivery_error_code_label(error_code),
    )
    .increment(1);
}

#[cfg(feature = "prometheus-metrics")]
fn record_group_session_event(
    env: &str,
    event: GroupSessionEvent,
    kind: Option<GroupKind>,
    result: MetricsResult,
) {
    metrics::counter!(
        "bcs_group_session_events_total",
        "env" => env.to_string(),
        "event" => group_session_event_label(event),
        "kind" => group_event_kind_label(kind),
        "result" => metrics_result_label(result),
    )
    .increment(1);
}

#[cfg(feature = "prometheus-metrics")]
fn record_message_flow_request(
    env: &str,
    source: MessageFlowSource,
    operation: MessageFlowOperation,
    result: MetricsResult,
) {
    metrics::counter!(
        "bcs_message_flow_requests_total",
        "env" => env.to_string(),
        "source" => message_flow_source_label(source),
        "operation" => message_flow_operation_label(operation),
        "result" => metrics_result_label(result),
    )
    .increment(1);
}

#[cfg(feature = "prometheus-metrics")]
fn record_direct_chat_run_event(
    env: &str,
    event: DirectChatRunEvent,
    result: MetricsResult,
    client_kind: DirectChatClientKind,
    reason: DirectChatRunReason,
) {
    metrics::counter!(
        "bcs_direct_chat_run_events_total",
        "env" => env.to_string(),
        "event" => direct_chat_run_event_label(event),
        "result" => metrics_result_label(result),
        "client_kind" => direct_chat_client_kind_label(client_kind),
        "reason" => direct_chat_run_reason_label(reason),
    )
    .increment(1);
}

#[path = "metrics/labels.rs"]
mod labels;
use labels::*;
pub use labels::{http_status_class,http_result_label};

#[cfg(all(test, feature = "prometheus-metrics"))]
#[path = "metrics/tests.rs"]
mod tests;

#[cfg(feature = "prometheus-metrics")]
fn install_recorder_once() -> Result<PrometheusHandle> {
    if let Some(handle) = PROMETHEUS_HANDLE.get() {
        return Ok(handle.clone());
    }

    let builder = PrometheusBuilder::new()
        .set_buckets_for_metric(Matcher::Full("bcs_delivery_queue_wait_seconds".into()), &[0.1, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0])
        .map_err(|e| BcsError::InvalidConfig(format!("invalid queue metrics buckets: {e}")))?
        .set_buckets_for_metric(Matcher::Full("bcs_delivery_run_seconds".into()), &[0.1, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0])
        .map_err(|e| BcsError::InvalidConfig(format!("invalid queue metrics buckets: {e}")))?
        .set_buckets_for_metric(Matcher::Full("bcs_delivery_worker_batch_rows".into()), &[0.0, 1.0, 8.0, 32.0, 100.0, 200.0])
        .map_err(|e| BcsError::InvalidConfig(format!("invalid queue metrics buckets: {e}")))?
        .set_buckets_for_metric(
            Matcher::Full("bcs_http_request_duration_seconds".to_string()),
            HTTP_DURATION_BUCKETS_SECONDS,
        )
        .map_err(|e| BcsError::InvalidConfig(format!("invalid HTTP metrics buckets: {e}")))?
        .set_buckets_for_metric(
            Matcher::Full("bcs_ws_connection_duration_seconds".to_string()),
            WS_CONNECTION_DURATION_BUCKETS_SECONDS,
        )
        .map_err(|e| BcsError::InvalidConfig(format!("invalid WS metrics buckets: {e}")))?
        .set_buckets_for_metric(
            Matcher::Full("bcs_message_delivery_duration_seconds".to_string()),
            DELIVERY_DURATION_BUCKETS_SECONDS,
        )
        .map_err(|e| BcsError::InvalidConfig(format!("invalid delivery metrics buckets: {e}")))?;

    let handle = builder
        .install_recorder()
        .map_err(|e| BcsError::InvalidConfig(format!("failed to install metrics recorder: {e}")))?;
    let _ = PROMETHEUS_HANDLE.set(handle.clone());
    Ok(PROMETHEUS_HANDLE
        .get()
        .cloned()
        .unwrap_or(handle))
}

#[cfg(feature = "prometheus-metrics")]
fn spawn_upkeep_task(
    handle: PrometheusHandle,
    mut shutdown_rx: watch::Receiver<bool>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(5));
        loop {
            tokio::select! {
                _ = interval.tick() => handle.run_upkeep(),
                changed = shutdown_rx.changed() => {
                    if changed.is_err() || *shutdown_rx.borrow() {
                        break;
                    }
                }
            }
        }
    })
}

#[cfg(feature = "prometheus-metrics")]
fn cache_label(config: &BcsConfig) -> &'static str {
    match config.cache.cache_type.as_str() {
        "redis" => "redis",
        "memory" => "memory",
        _ => "external",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn storage_label(config: &BcsConfig) -> &'static str {
    match &config.database.database_type {
        DatabaseType::Sqlite => "sqlite",
        DatabaseType::Mysql => "mysql",
        DatabaseType::Other(_) => "external",
    }
}
