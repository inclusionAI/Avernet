//! Prometheus metrics runtime and label helpers.

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
    BlockingA2aChatCommand, BlockingA2aChatOutcome, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget, BotEventCommand, BotEventOutcome,
    BotMetricCount, BotMetricsSnapshotPort,
    ChatAbortCommand, ChatAbortOutcome, ChatRunCancelCommand, ChatRunMetricCount, ChatRunQueryCommand,
    DeliveryBlockContext, DeliveryBlockReason, DeliveryMetricKind, DeliveryMetricTarget,
    DeliveryPolicyBlockInstrumentationHook, DirectChatClientKind,
    DirectChatRunEvent, DirectChatRunLifecycleHook, DirectChatRunReason,
    DirectChatRunSnapshotPort, DirectChatRunState, DmCreateCommand, DmCreateResult,
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort, FrontendDeliveryResult,
    GroupAddMemberCommand, GroupAddMemberResult, GroupCallbackCommand, GroupCallbackOutcome,
    GroupChatCommand, GroupChatOutcome, GroupCreateCommand, GroupDeleteCommand, GroupDeleteResult,
    GroupRemoveMemberCommand, GroupRemoveMemberResult,
    GroupDetailResult, GroupKind, GroupManagementService, GroupMetricCount,
    GroupMetricsSnapshotPort, GroupPatchSettingsCommand, GroupPatchSettingsResult,
    GroupParticipantModeCommand, GroupParticipantModeResult,
    GroupRoutingPolicyCommand, GroupRoutingPolicyResult, GroupSessionMetricCount,
    GroupSessionMetricsSnapshotPort, GroupStatus, GroupStatusCommand,
    GroupStrategy, GroupTerminateCommand, GroupUpdateLabelCommand, GroupUpdateVisibilityCommand,
    GroupUpdateWorkspaceCommand,
    GroupUseCaseError, GroupWorkspaceResult, MessageFlowService, MetricsResult,
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
pub struct MetricsGroupManagementService {
    inner: Arc<dyn GroupManagementService>,
    env: Arc<str>,
}

#[cfg(feature = "prometheus-metrics")]
impl MetricsGroupManagementService {
    pub fn new(inner: Arc<dyn GroupManagementService>, env: Arc<str>) -> Self {
        Self { inner, env }
    }

    fn record_event(
        &self,
        event: GroupSessionEvent,
        kind: Option<GroupKind>,
        result: MetricsResult,
    ) {
        record_group_session_event(&self.env, event, kind, result);
    }
}

#[cfg(feature = "prometheus-metrics")]
#[async_trait::async_trait]
impl GroupManagementService for MetricsGroupManagementService {
    async fn create_group(
        &self,
        cmd: GroupCreateCommand,
    ) -> std::result::Result<GroupDetailResult, GroupUseCaseError> {
        let command_kind = cmd.group_kind.unwrap_or(GroupKind::Normal);
        let result = self.inner.create_group(cmd).await;
        let kind = result
            .as_ref()
            .ok()
            .map(|group| group.group_kind)
            .unwrap_or(command_kind);
        self.record_event(
            GroupSessionEvent::Created,
            Some(kind),
            metrics_result_from_bool(result.is_ok()),
        );
        result
    }

    async fn create_dm(
        &self,
        cmd: DmCreateCommand,
    ) -> std::result::Result<DmCreateResult, GroupUseCaseError> {
        let result = self.inner.create_dm(cmd).await;
        match &result {
            Ok(outcome) if outcome.created => {
                self.record_event(
                    GroupSessionEvent::Created,
                    Some(outcome.group.group_kind),
                    MetricsResult::Success,
                );
            }
            Ok(_) => {}
            Err(_) => {
                self.record_event(
                    GroupSessionEvent::Created,
                    Some(GroupKind::Dm),
                    MetricsResult::Error,
                );
            }
        }
        result
    }

    async fn update_status(
        &self,
        cmd: GroupStatusCommand,
    ) -> std::result::Result<GroupDetailResult, GroupUseCaseError> {
        let event = group_status_event(&cmd.status);
        let result = self.inner.update_status(cmd).await;
        let kind = result.as_ref().ok().map(|group| group.group_kind);
        self.record_event(event, kind, metrics_result_from_bool(result.is_ok()));
        result
    }

    async fn add_member(
        &self,
        cmd: GroupAddMemberCommand,
    ) -> std::result::Result<GroupAddMemberResult, GroupUseCaseError> {
        let result = self.inner.add_member(cmd).await;
        self.record_event(
            GroupSessionEvent::MemberAdded,
            None,
            metrics_result_from_bool(result.is_ok()),
        );
        result
    }

    async fn remove_member(
        &self,
        cmd: GroupRemoveMemberCommand,
    ) -> std::result::Result<GroupRemoveMemberResult, GroupUseCaseError> {
        let result = self.inner.remove_member(cmd).await;
        self.record_event(
            GroupSessionEvent::MemberRemoved,
            None,
            metrics_result_from_bool(result.is_ok()),
        );
        result
    }

    async fn delete_group(
        &self,
        cmd: GroupDeleteCommand,
    ) -> std::result::Result<GroupDeleteResult, GroupUseCaseError> {
        let result = self.inner.delete_group(cmd).await;
        self.record_event(
            GroupSessionEvent::Deleted,
            None,
            metrics_result_from_bool(result.is_ok()),
        );
        result
    }

    async fn terminate_group(
        &self,
        cmd: GroupTerminateCommand,
    ) -> std::result::Result<GroupDetailResult, GroupUseCaseError> {
        let result = self.inner.terminate_group(cmd).await;
        let kind = result.as_ref().ok().map(|group| group.group_kind);
        self.record_event(
            GroupSessionEvent::Completed,
            kind,
            metrics_result_from_bool(result.is_ok()),
        );
        result
    }

    async fn update_label(
        &self,
        cmd: GroupUpdateLabelCommand,
    ) -> std::result::Result<GroupDetailResult, GroupUseCaseError> {
        self.inner.update_label(cmd).await
    }

    async fn update_visibility(
        &self,
        cmd: GroupUpdateVisibilityCommand,
    ) -> std::result::Result<GroupDetailResult, GroupUseCaseError> {
        self.inner.update_visibility(cmd).await
    }

    async fn update_workspace(
        &self,
        cmd: GroupUpdateWorkspaceCommand,
    ) -> std::result::Result<GroupWorkspaceResult, GroupUseCaseError> {
        self.inner.update_workspace(cmd).await
    }

    async fn update_routing_policy(
        &self,
        cmd: GroupRoutingPolicyCommand,
    ) -> std::result::Result<GroupRoutingPolicyResult, GroupUseCaseError> {
        self.inner.update_routing_policy(cmd).await
    }

    async fn update_participant_mode(
        &self,
        cmd: GroupParticipantModeCommand,
    ) -> std::result::Result<GroupParticipantModeResult, GroupUseCaseError> {
        self.inner.update_participant_mode(cmd).await
    }

    async fn patch_group_settings(
        &self,
        cmd: GroupPatchSettingsCommand,
    ) -> std::result::Result<GroupPatchSettingsResult, GroupUseCaseError> {
        self.inner.patch_group_settings(cmd).await
    }
}

#[cfg(feature = "prometheus-metrics")]
pub struct InstrumentedMessageFlowService {
    inner: Arc<dyn MessageFlowService>,
    env: Arc<str>,
}

#[cfg(feature = "prometheus-metrics")]
impl InstrumentedMessageFlowService {
    pub fn new(inner: Arc<dyn MessageFlowService>, env: Arc<str>) -> Self {
        Self { inner, env }
    }

    fn record(&self, source: MessageFlowSource, operation: MessageFlowOperation, ok: bool) {
        record_message_flow_request(
            &self.env,
            source,
            operation,
            metrics_result_from_bool(ok),
        );
    }
}

#[cfg(feature = "prometheus-metrics")]
#[async_trait::async_trait]
impl MessageFlowService for InstrumentedMessageFlowService {
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        let result = self.inner.handle_web_send(cmd).await;
        self.record(
            MessageFlowSource::WebWs,
            MessageFlowOperation::WebSend,
            result.is_ok(),
        );
        result
    }

    async fn handle_group_chat(&self, cmd: GroupChatCommand) -> ServiceResult<GroupChatOutcome> {
        let result = self.inner.handle_group_chat(cmd).await;
        self.record(
            MessageFlowSource::Http,
            MessageFlowOperation::GroupChat,
            result.is_ok(),
        );
        result
    }

    async fn handle_persistent_group_send(
        &self,
        cmd: PersistentGroupSendCommand,
    ) -> ServiceResult<PersistentGroupSendOutcome> {
        let result = self.inner.handle_persistent_group_send(cmd).await;
        self.record(
            MessageFlowSource::Http,
            MessageFlowOperation::PersistentGroupSend,
            result.is_ok(),
        );
        result
    }

    async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        let result = self.inner.handle_bot_event(cmd).await;
        self.record(
            MessageFlowSource::BotWs,
            MessageFlowOperation::BotEvent,
            result.is_ok(),
        );
        result
    }

    async fn handle_group_callback(
        &self,
        cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        let result = self.inner.handle_group_callback(cmd).await;
        self.record(
            MessageFlowSource::Http,
            MessageFlowOperation::GroupCallback,
            result.is_ok(),
        );
        result
    }

    async fn handle_chat_abort(&self, cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        let result = self.inner.handle_chat_abort(cmd).await;
        self.record(
            MessageFlowSource::WebWs,
            MessageFlowOperation::ChatAbort,
            result.is_ok(),
        );
        result
    }

    async fn register_task_run_alias(
        &self,
        task_id: &str,
        run_id: &str,
        bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        self.inner.register_task_run_alias(task_id, run_id, bot_id).await
    }

    async fn handle_task_dispatch(
        &self,
        cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        let result = self.inner.handle_task_dispatch(cmd).await;
        self.record(
            MessageFlowSource::BotWs,
            MessageFlowOperation::TaskDispatch,
            result.is_ok(),
        );
        result
    }

    async fn handle_task_message(
        &self,
        cmd: TaskMessageCommand,
    ) -> ServiceResult<TaskMessageOutcome> {
        let result = self.inner.handle_task_message(cmd).await;
        self.record(
            MessageFlowSource::BotWs,
            MessageFlowOperation::TaskMessage,
            result.is_ok(),
        );
        result
    }

    async fn handle_task_complete(
        &self,
        cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        let result = self.inner.handle_task_complete(cmd).await;
        self.record(
            MessageFlowSource::BotWs,
            MessageFlowOperation::TaskComplete,
            result.is_ok(),
        );
        result
    }
}

#[cfg(feature = "prometheus-metrics")]
pub struct InstrumentedA2aChatRunService {
    inner: Arc<dyn A2aChatRunService>,
    env: Arc<str>,
}

#[cfg(feature = "prometheus-metrics")]
impl InstrumentedA2aChatRunService {
    pub fn new(inner: Arc<dyn A2aChatRunService>, env: Arc<str>) -> Self {
        Self { inner, env }
    }

    fn record(&self, ok: bool) {
        record_message_flow_request(
            &self.env,
            MessageFlowSource::Http,
            MessageFlowOperation::DirectChat,
            metrics_result_from_bool(ok),
        );
    }
}

#[cfg(feature = "prometheus-metrics")]
#[async_trait::async_trait]
impl A2aChatRunService for InstrumentedA2aChatRunService {
    async fn run_blocking_chat(
        &self,
        cmd: BlockingA2aChatCommand,
    ) -> ServiceResult<BlockingA2aChatOutcome> {
        let result = self.inner.run_blocking_chat(cmd).await;
        self.record(result.is_ok());
        result
    }

    async fn start_async_chat(
        &self,
        cmd: AsyncA2aChatCommand,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        let result = self.inner.start_async_chat(cmd).await;
        self.record(result.is_ok());
        result
    }

    async fn get_run(&self, cmd: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus> {
        let result = self.inner.get_run(cmd).await;
        self.record(result.is_ok());
        result
    }

    async fn cancel_run(&self, cmd: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus> {
        let result = self.inner.cancel_run(cmd).await;
        self.record(result.is_ok());
        result
    }
}

#[cfg(feature = "prometheus-metrics")]
pub struct MetricsDirectChatRunLifecycleHook {
    env: Arc<str>,
}

#[cfg(feature = "prometheus-metrics")]
impl MetricsDirectChatRunLifecycleHook {
    pub fn new(env: Arc<str>) -> Self {
        Self { env }
    }
}

#[cfg(feature = "prometheus-metrics")]
#[async_trait::async_trait]
impl DirectChatRunLifecycleHook for MetricsDirectChatRunLifecycleHook {
    async fn event(
        &self,
        event: DirectChatRunEvent,
        result: MetricsResult,
        client_kind: DirectChatClientKind,
        reason: DirectChatRunReason,
    ) {
        record_direct_chat_run_event(&self.env, event, result, client_kind, reason);
    }
}

#[cfg(feature = "prometheus-metrics")]
pub struct MetricsDeliveryPolicyBlockHook {
    env: Arc<str>,
}

#[cfg(feature = "prometheus-metrics")]
impl MetricsDeliveryPolicyBlockHook {
    pub fn new(env: Arc<str>) -> Self {
        Self { env }
    }
}

#[cfg(feature = "prometheus-metrics")]
#[async_trait::async_trait]
impl DeliveryPolicyBlockInstrumentationHook for MetricsDeliveryPolicyBlockHook {
    async fn blocked(&self, context: DeliveryBlockContext) {
        record_delivery_count(
            &self.env,
            context.target,
            context.delivery_kind,
            DeliveryOutcome::Blocked,
            delivery_block_reason_code(context.reason),
        );
    }
}

#[cfg(feature = "prometheus-metrics")]
pub struct MetricsBotDeliveryPort {
    inner: Arc<dyn BotDeliveryPort>,
    env: Arc<str>,
}

#[cfg(feature = "prometheus-metrics")]
impl MetricsBotDeliveryPort {
    pub fn new(inner: Arc<dyn BotDeliveryPort>, env: Arc<str>) -> Self {
        Self { inner, env }
    }
}

#[cfg(feature = "prometheus-metrics")]
#[async_trait::async_trait]
impl BotDeliveryPort for MetricsBotDeliveryPort {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool {
        self.inner.is_available(target).await
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let delivery_kind = bot_delivery_metric_kind(&cmd.delivery_kind);
        let start = Instant::now();
        let result = self.inner.deliver(cmd).await;
        let duration = start.elapsed();

        match &result {
            Ok(outcome) if outcome.delivered => {
                record_delivery(
                    &self.env,
                    DeliveryMetricTarget::Bot,
                    delivery_kind,
                    DeliveryOutcome::Delivered,
                    DeliveryErrorCode::None,
                    duration,
                );
            }
            Ok(outcome) => {
                record_delivery(
                    &self.env,
                    DeliveryMetricTarget::Bot,
                    delivery_kind,
                    DeliveryOutcome::Failed,
                    outcome
                        .error
                        .as_ref()
                        .map(service_error_code)
                        .unwrap_or(DeliveryErrorCode::Unknown),
                    duration,
                );
            }
            Err(error) => {
                record_delivery(
                    &self.env,
                    DeliveryMetricTarget::Bot,
                    delivery_kind,
                    DeliveryOutcome::Failed,
                    service_error_code(error),
                    duration,
                );
            }
        }

        result
    }
}

#[cfg(feature = "prometheus-metrics")]
pub struct MetricsFrontendDeliveryPort {
    inner: Arc<dyn FrontendDeliveryPort>,
    env: Arc<str>,
}

#[cfg(feature = "prometheus-metrics")]
impl MetricsFrontendDeliveryPort {
    pub fn new(inner: Arc<dyn FrontendDeliveryPort>, env: Arc<str>) -> Self {
        Self { inner, env }
    }
}

#[cfg(feature = "prometheus-metrics")]
#[async_trait::async_trait]
impl FrontendDeliveryPort for MetricsFrontendDeliveryPort {
    async fn publish(
        &self,
        cmd: FrontendDeliveryCommand,
    ) -> ServiceResult<FrontendDeliveryResult> {
        let delivery_kind = frontend_delivery_metric_kind(&cmd.delivery_kind);
        let start = Instant::now();
        let result = self.inner.publish(cmd).await;
        let duration = start.elapsed();

        match &result {
            Ok(outcome) if outcome.delivered > 0 => {
                record_delivery(
                    &self.env,
                    DeliveryMetricTarget::Frontend,
                    delivery_kind,
                    DeliveryOutcome::Delivered,
                    DeliveryErrorCode::None,
                    duration,
                );
            }
            Ok(_) => {
                record_delivery(
                    &self.env,
                    DeliveryMetricTarget::Frontend,
                    delivery_kind,
                    DeliveryOutcome::NoReceivers,
                    DeliveryErrorCode::None,
                    duration,
                );
            }
            Err(error) => {
                record_delivery(
                    &self.env,
                    DeliveryMetricTarget::Frontend,
                    delivery_kind,
                    DeliveryOutcome::Failed,
                    service_error_code(error),
                    duration,
                );
            }
        }

        result
    }

    async fn unregister_run(&self, run_id: &str) -> ServiceResult<()> {
        self.inner.unregister_run(run_id).await
    }
}

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
        metrics::counter!(
            "bcs_http_requests_total",
            "env" => env.to_string(),
            "route" => route.to_string(),
            "method" => method.to_string(),
            "status_class" => status_class,
            "result" => http_result_label(status),
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

    #[cfg(not(feature = "prometheus-metrics"))]
    {
        let _ = (env, route, method, status, duration);
    }
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

#[cfg(feature = "prometheus-metrics")]
fn metrics_result_from_bool(ok: bool) -> MetricsResult {
    if ok {
        MetricsResult::Success
    } else {
        MetricsResult::Error
    }
}

pub fn http_status_class(status: StatusCode) -> &'static str {
    match status.as_u16() {
        100..=199 => "1xx",
        200..=299 => "2xx",
        300..=399 => "3xx",
        400..=499 => "4xx",
        _ => "5xx",
    }
}

pub fn http_result_label(status: StatusCode) -> &'static str {
    if status.is_server_error() {
        "error"
    } else {
        "success"
    }
}

#[cfg(feature = "prometheus-metrics")]
fn metrics_result_label(result: MetricsResult) -> &'static str {
    match result {
        MetricsResult::Success => "success",
        MetricsResult::Error => "error",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn ws_peer_label(peer: WsPeer) -> &'static str {
    match peer {
        WsPeer::Bot => "bot",
        WsPeer::Frontend => "frontend",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn ws_close_reason_label(reason: WsCloseReason) -> &'static str {
    match reason {
        WsCloseReason::ClientClose => "client_close",
        WsCloseReason::ServerClose => "server_close",
        WsCloseReason::IdleTimeout => "idle_timeout",
        WsCloseReason::SendError => "send_error",
        WsCloseReason::ProtocolError => "protocol_error",
        WsCloseReason::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn ws_event_label(event: WsConnectionEvent) -> &'static str {
    match event {
        WsConnectionEvent::Accepted => "accepted",
        WsConnectionEvent::Registered => "registered",
        WsConnectionEvent::RegisterRejected => "register_rejected",
        WsConnectionEvent::DispatchError => "dispatch_error",
        WsConnectionEvent::ProtocolError => "protocol_error",
        WsConnectionEvent::SendError => "send_error",
        WsConnectionEvent::Closed => "closed",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn bot_delivery_metric_kind(kind: &BotDeliveryKind) -> DeliveryMetricKind {
    match kind {
        BotDeliveryKind::Send => DeliveryMetricKind::Send,
        BotDeliveryKind::Inject => DeliveryMetricKind::Inject,
        BotDeliveryKind::Abort => DeliveryMetricKind::Abort,
        BotDeliveryKind::TaskDispatch => DeliveryMetricKind::TaskDispatch,
        BotDeliveryKind::TaskMessage => DeliveryMetricKind::TaskMessage,
        BotDeliveryKind::TaskResult => DeliveryMetricKind::TaskResult,
    }
}

#[cfg(feature = "prometheus-metrics")]
fn frontend_delivery_metric_kind(kind: &FrontendDeliveryKind) -> DeliveryMetricKind {
    match kind {
        FrontendDeliveryKind::WorkbenchEvent => DeliveryMetricKind::WorkbenchEvent,
        FrontendDeliveryKind::RunEvent => DeliveryMetricKind::RunEvent,
    }
}

#[cfg(feature = "prometheus-metrics")]
fn delivery_metric_target_label(target: DeliveryMetricTarget) -> &'static str {
    match target {
        DeliveryMetricTarget::Bot => "bot",
        DeliveryMetricTarget::Frontend => "frontend",
        DeliveryMetricTarget::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn delivery_metric_kind_label(kind: DeliveryMetricKind) -> &'static str {
    match kind {
        DeliveryMetricKind::Send => "send",
        DeliveryMetricKind::Inject => "inject",
        DeliveryMetricKind::Abort => "abort",
        DeliveryMetricKind::TaskDispatch => "task_dispatch",
        DeliveryMetricKind::TaskMessage => "task_message",
        DeliveryMetricKind::TaskResult => "task_result",
        DeliveryMetricKind::WorkbenchEvent => "workbench_event",
        DeliveryMetricKind::RunEvent => "run_event",
        DeliveryMetricKind::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn delivery_block_reason_code(reason: DeliveryBlockReason) -> DeliveryErrorCode {
    match reason {
        DeliveryBlockReason::PolicyBlocked => DeliveryErrorCode::PolicyBlocked,
        DeliveryBlockReason::Unknown => DeliveryErrorCode::Unknown,
    }
}

#[cfg(feature = "prometheus-metrics")]
fn delivery_outcome_label(result: DeliveryOutcome) -> &'static str {
    match result {
        DeliveryOutcome::Delivered => "delivered",
        DeliveryOutcome::Failed => "failed",
        DeliveryOutcome::NoReceivers => "no_receivers",
        DeliveryOutcome::Blocked => "blocked",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn delivery_error_code_label(error_code: DeliveryErrorCode) -> &'static str {
    match error_code {
        DeliveryErrorCode::None => "none",
        DeliveryErrorCode::NotFound => "not_found",
        DeliveryErrorCode::NotRegistered => "not_registered",
        DeliveryErrorCode::NotConnected => "not_connected",
        DeliveryErrorCode::InvalidOperation => "invalid_operation",
        DeliveryErrorCode::Unauthorized => "unauthorized",
        DeliveryErrorCode::Forbidden => "forbidden",
        DeliveryErrorCode::MessageLimit => "message_limit",
        DeliveryErrorCode::Internal => "internal",
        DeliveryErrorCode::Io => "io",
        DeliveryErrorCode::Json => "json",
        DeliveryErrorCode::CannotAddSelf => "cannot_add_self",
        DeliveryErrorCode::PendingRequestExists => "pending_request_exists",
        DeliveryErrorCode::CannotAcceptRejected => "cannot_accept_rejected",
        DeliveryErrorCode::CannotRejectAccepted => "cannot_reject_accepted",
        DeliveryErrorCode::NotFriends => "not_friends",
        DeliveryErrorCode::PrivateBot => "private_bot",
        DeliveryErrorCode::PolicyBlocked => "policy_blocked",
        DeliveryErrorCode::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn service_error_code(error: &ServiceError) -> DeliveryErrorCode {
    match error {
        ServiceError::BotNotFound(_)
        | ServiceError::GroupNotFound(_)
        | ServiceError::ProposalNotFound(_)
        | ServiceError::ParticipantNotFound(_)
        | ServiceError::FriendRequestNotFound(_) => DeliveryErrorCode::NotFound,
        ServiceError::BotNotRegistered(_) => DeliveryErrorCode::NotRegistered,
        ServiceError::BotNotConnected(_) => DeliveryErrorCode::NotConnected,
        ServiceError::InvalidOperation { .. } => DeliveryErrorCode::InvalidOperation,
        ServiceError::Conflict(_) => DeliveryErrorCode::InvalidOperation,
        ServiceError::Unauthorized(_) => DeliveryErrorCode::Unauthorized,
        ServiceError::Forbidden(_) | ServiceError::BotHidden(_) => DeliveryErrorCode::Forbidden,
        ServiceError::MessageLimitReached(_) => DeliveryErrorCode::MessageLimit,
        ServiceError::InternalError(_) => DeliveryErrorCode::Internal,
        ServiceError::IoError(_) => DeliveryErrorCode::Io,
        ServiceError::JsonError(_) => DeliveryErrorCode::Json,
        ServiceError::CannotAddSelf => DeliveryErrorCode::CannotAddSelf,
        ServiceError::PendingRequestExists { .. } => DeliveryErrorCode::PendingRequestExists,
        ServiceError::CannotAcceptRejected => DeliveryErrorCode::CannotAcceptRejected,
        ServiceError::CannotRejectAccepted => DeliveryErrorCode::CannotRejectAccepted,
        ServiceError::NotFriends(_) => DeliveryErrorCode::NotFriends,
        ServiceError::PrivateBotCannotCollaborate => DeliveryErrorCode::PrivateBot,
        ServiceError::SessionNotFound(_)
        | ServiceError::SessionInvalidParams(_)
        | ServiceError::SessionCallbackPending(_) => DeliveryErrorCode::Internal,
        ServiceError::ProviderNotFound(_) => DeliveryErrorCode::NotFound,
        ServiceError::ProviderNotReadyForDownlink { .. } => DeliveryErrorCode::InvalidOperation,
        ServiceError::BotAlreadyBound { .. } => DeliveryErrorCode::InvalidOperation,
            ServiceError::ExistNonPublicBots { .. } => DeliveryErrorCode::InvalidOperation,
    }
}

#[cfg(feature = "prometheus-metrics")]
fn group_status_label(status: GroupStatus) -> &'static str {
    match status {
        GroupStatus::Active => "active",
        GroupStatus::Completed => "completed",
        GroupStatus::Error => "error",
        GroupStatus::Closed => "closed",
        GroupStatus::Inactive => "inactive",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn group_kind_label(kind: GroupKind) -> &'static str {
    match kind {
        GroupKind::Normal => "normal",
        GroupKind::Dm => "dm",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn group_strategy_label(strategy: GroupStrategy) -> &'static str {
    match strategy {
        GroupStrategy::Chat => "chat",
        GroupStrategy::ManagerWorker => "manager_worker",
        GroupStrategy::StateMachine => "state_machine",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn actor_kind_label(kind: ActorKind) -> &'static str {
    match kind {
        ActorKind::Bot => "bot",
        ActorKind::Human => "human",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn actor_status_label(status: ActorStatus) -> &'static str {
    match status {
        ActorStatus::Online => "online",
        ActorStatus::Hidden => "hidden",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn visibility_label(visibility: Option<&str>) -> &'static str {
    match visibility {
        Some("public") => "public",
        Some("protected") => "protected",
        Some("private") => "private",
        _ => "other",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn session_status_label(status: SessionStatus) -> &'static str {
    match status {
        SessionStatus::Running => "running",
        SessionStatus::Completed => "completed",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn session_kind_label(kind: SessionKind) -> &'static str {
    match kind {
        SessionKind::Chat => "chat",
        SessionKind::ServiceInvocation => "service_invocation",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn group_event_kind_label(kind: Option<GroupKind>) -> &'static str {
    kind.map(group_kind_label).unwrap_or("unknown")
}

#[cfg(feature = "prometheus-metrics")]
fn group_session_event_label(event: GroupSessionEvent) -> &'static str {
    match event {
        GroupSessionEvent::Created => "created",
        GroupSessionEvent::Completed => "completed",
        GroupSessionEvent::Closed => "closed",
        GroupSessionEvent::Deleted => "deleted",
        GroupSessionEvent::MemberAdded => "member_added",
        GroupSessionEvent::MemberRemoved => "member_removed",
        GroupSessionEvent::StatusUpdated => "status_updated",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn group_status_event(status: &str) -> GroupSessionEvent {
    if status.eq_ignore_ascii_case("completed") {
        GroupSessionEvent::Completed
    } else if status.eq_ignore_ascii_case("closed") {
        GroupSessionEvent::Closed
    } else {
        GroupSessionEvent::StatusUpdated
    }
}

#[cfg(feature = "prometheus-metrics")]
fn service_mode_label(mode: Option<&str>) -> &'static str {
    match mode {
        Some("master_slave") => "master_slave",
        Some(_) => "other",
        None => "none",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn message_flow_source_label(source: MessageFlowSource) -> &'static str {
    match source {
        MessageFlowSource::WebWs => "web_ws",
        MessageFlowSource::Http => "http",
        MessageFlowSource::BotWs => "bot_ws",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn message_flow_operation_label(operation: MessageFlowOperation) -> &'static str {
    match operation {
        MessageFlowOperation::WebSend => "web_send",
        MessageFlowOperation::GroupChat => "group_chat",
        MessageFlowOperation::PersistentGroupSend => "persistent_group_send",
        MessageFlowOperation::BotEvent => "bot_event",
        MessageFlowOperation::GroupCallback => "group_callback",
        MessageFlowOperation::ChatAbort => "chat_abort",
        MessageFlowOperation::TaskDispatch => "task_dispatch",
        MessageFlowOperation::TaskMessage => "task_message",
        MessageFlowOperation::TaskComplete => "task_complete",
        MessageFlowOperation::DirectChat => "direct_chat",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn direct_chat_state_label(state: DirectChatRunState) -> &'static str {
    match state {
        DirectChatRunState::Pending => "pending",
        DirectChatRunState::Submitted => "submitted",
        DirectChatRunState::Running => "running",
        DirectChatRunState::Completed => "completed",
        DirectChatRunState::Failed => "failed",
        DirectChatRunState::Cancelled => "cancelled",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn direct_chat_client_kind_label(client_kind: DirectChatClientKind) -> &'static str {
    match client_kind {
        DirectChatClientKind::None => "none",
        DirectChatClientKind::HttpChat => "http_chat",
        DirectChatClientKind::HttpChatAsync => "http_chat_async",
        DirectChatClientKind::BcsCli => "bcs_cli",
        DirectChatClientKind::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn direct_chat_run_event_label(event: DirectChatRunEvent) -> &'static str {
    match event {
        DirectChatRunEvent::Created => "created",
        DirectChatRunEvent::Submitted => "submitted",
        DirectChatRunEvent::Running => "running",
        DirectChatRunEvent::Completed => "completed",
        DirectChatRunEvent::Failed => "failed",
        DirectChatRunEvent::Cancelled => "cancelled",
        DirectChatRunEvent::Expired => "expired",
        DirectChatRunEvent::Dropped => "dropped",
        DirectChatRunEvent::CapacityRejected => "capacity_rejected",
    }
}

#[cfg(feature = "prometheus-metrics")]
fn direct_chat_run_reason_label(reason: DirectChatRunReason) -> &'static str {
    match reason {
        DirectChatRunReason::None => "none",
        DirectChatRunReason::Timeout => "timeout",
        DirectChatRunReason::BotNotConnected => "bot_not_connected",
        DirectChatRunReason::Blocked => "blocked",
        DirectChatRunReason::StoreCapacity => "store_capacity",
        DirectChatRunReason::InternalError => "internal_error",
    }
}

#[cfg(all(test, feature = "prometheus-metrics"))]
mod tests {
    use super::*;

    #[test]
    fn service_mode_label_keeps_unknown_values_low_cardinality() {
        assert_eq!(service_mode_label(None), "none");
        assert_eq!(service_mode_label(Some("master_slave")), "master_slave");
        assert_eq!(service_mode_label(Some("other")), "other");
        assert_eq!(service_mode_label(Some("free-form-mode")), "other");
    }

    #[test]
    fn group_strategy_label_uses_closed_enum_values() {
        assert_eq!(group_strategy_label(GroupStrategy::Chat), "chat");
        assert_eq!(group_strategy_label(GroupStrategy::ManagerWorker), "manager_worker");
        assert_eq!(group_strategy_label(GroupStrategy::StateMachine), "state_machine");
    }

    #[test]
    fn bot_labels_use_closed_sets() {
        assert_eq!(actor_kind_label(ActorKind::Bot), "bot");
        assert_eq!(actor_kind_label(ActorKind::Human), "human");
        assert_eq!(actor_status_label(ActorStatus::Online), "online");
        assert_eq!(actor_status_label(ActorStatus::Hidden), "hidden");
        assert_eq!(visibility_label(Some("public")), "public");
        assert_eq!(visibility_label(Some("protected")), "protected");
        assert_eq!(visibility_label(Some("private")), "private");
        assert_eq!(visibility_label(Some("custom")), "other");
        assert_eq!(visibility_label(None), "other");
    }

    #[test]
    fn http_result_label_treats_only_5xx_as_error() {
        assert_eq!(http_status_class(StatusCode::NOT_FOUND), "4xx");
        assert_eq!(http_result_label(StatusCode::NOT_FOUND), "success");
        assert_eq!(http_status_class(StatusCode::INTERNAL_SERVER_ERROR), "5xx");
        assert_eq!(http_result_label(StatusCode::INTERNAL_SERVER_ERROR), "error");
    }

    #[test]
    fn group_event_labels_use_closed_sets() {
        assert_eq!(group_event_kind_label(Some(GroupKind::Normal)), "normal");
        assert_eq!(group_event_kind_label(Some(GroupKind::Dm)), "dm");
        assert_eq!(group_event_kind_label(None), "unknown");
        assert_eq!(session_status_label(SessionStatus::Running), "running");
        assert_eq!(session_status_label(SessionStatus::Completed), "completed");
        assert_eq!(session_kind_label(SessionKind::Chat), "chat");
        assert_eq!(
            session_kind_label(SessionKind::ServiceInvocation),
            "service_invocation"
        );
        assert_eq!(
            group_session_event_label(group_status_event("completed")),
            "completed"
        );
        assert_eq!(group_session_event_label(group_status_event("closed")), "closed");
        assert_eq!(
            group_session_event_label(group_status_event("active")),
            "status_updated"
        );
        assert_eq!(
            group_session_event_label(GroupSessionEvent::MemberAdded),
            "member_added"
        );
    }

    #[test]
    fn ws_labels_use_closed_sets() {
        assert_eq!(ws_peer_label(WsPeer::Bot), "bot");
        assert_eq!(ws_close_reason_label(WsCloseReason::IdleTimeout), "idle_timeout");
        assert_eq!(ws_event_label(WsConnectionEvent::Registered), "registered");
    }

    #[test]
    fn message_flow_labels_use_closed_sets() {
        assert_eq!(message_flow_source_label(MessageFlowSource::WebWs), "web_ws");
        assert_eq!(
            message_flow_operation_label(MessageFlowOperation::PersistentGroupSend),
            "persistent_group_send"
        );
        assert_eq!(
            message_flow_operation_label(MessageFlowOperation::DirectChat),
            "direct_chat"
        );
    }

    #[test]
    fn direct_chat_client_labels_use_closed_sets() {
        assert_eq!(
            direct_chat_client_kind_label(DirectChatClientKind::None),
            "none"
        );
        assert_eq!(
            direct_chat_client_kind_label(DirectChatClientKind::HttpChat),
            "http_chat"
        );
        assert_eq!(
            direct_chat_client_kind_label(DirectChatClientKind::HttpChatAsync),
            "http_chat_async"
        );
        assert_eq!(
            direct_chat_client_kind_label(DirectChatClientKind::BcsCli),
            "bcs_cli"
        );
        assert_eq!(
            direct_chat_client_kind_label(DirectChatClientKind::Unknown),
            "unknown"
        );
        assert_eq!(
            direct_chat_run_event_label(DirectChatRunEvent::CapacityRejected),
            "capacity_rejected"
        );
        assert_eq!(
            direct_chat_run_reason_label(DirectChatRunReason::StoreCapacity),
            "store_capacity"
        );
    }

    #[test]
    fn delivery_block_labels_use_closed_sets() {
        assert_eq!(delivery_metric_target_label(DeliveryMetricTarget::Bot), "bot");
        assert_eq!(
            delivery_metric_target_label(DeliveryMetricTarget::Unknown),
            "unknown"
        );
        assert_eq!(delivery_metric_kind_label(DeliveryMetricKind::Send), "send");
        assert_eq!(
            delivery_metric_kind_label(DeliveryMetricKind::TaskDispatch),
            "task_dispatch"
        );
        assert_eq!(
            delivery_error_code_label(delivery_block_reason_code(
                DeliveryBlockReason::PolicyBlocked
            )),
            "policy_blocked"
        );
        assert_eq!(delivery_outcome_label(DeliveryOutcome::NoReceivers), "no_receivers");
        assert_eq!(
            delivery_error_code_label(DeliveryErrorCode::NotConnected),
            "not_connected"
        );
    }

    #[test]
    fn histogram_buckets_are_seconds_and_monotonic() {
        for buckets in [
            HTTP_DURATION_BUCKETS_SECONDS,
            WS_CONNECTION_DURATION_BUCKETS_SECONDS,
            DELIVERY_DURATION_BUCKETS_SECONDS,
        ] {
            assert!(!buckets.is_empty());
            assert!(buckets.iter().all(|bucket| *bucket > 0.0));
            assert!(buckets.windows(2).all(|pair| pair[0] < pair[1]));
        }
        assert_eq!(HTTP_DURATION_BUCKETS_SECONDS[0], 0.005);
        assert_eq!(DELIVERY_DURATION_BUCKETS_SECONDS[0], 0.001);
        assert_eq!(WS_CONNECTION_DURATION_BUCKETS_SECONDS[0], 1.0);
    }

    #[test]
    fn runtime_info_labels_follow_config_sources() {
        let mut config = BcsConfig::default();
        assert_eq!(cache_label(&config), "memory");
        assert_eq!(storage_label(&config), "sqlite");

        config.cache.cache_type = "redis".to_string();
        assert_eq!(cache_label(&config), "redis");

        config.database.database_type = DatabaseType::Mysql;
        assert_eq!(storage_label(&config), "mysql");

        config.database.database_type = DatabaseType::Sqlite;
        assert_eq!(storage_label(&config), "sqlite");
    }
}

#[cfg(feature = "prometheus-metrics")]
fn install_recorder_once() -> Result<PrometheusHandle> {
    if let Some(handle) = PROMETHEUS_HANDLE.get() {
        return Ok(handle.clone());
    }

    let builder = PrometheusBuilder::new()
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
