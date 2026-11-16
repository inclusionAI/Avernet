//! Instrumentation wrappers for application and delivery contracts.
use super::*;
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
}

#[cfg(feature = "prometheus-metrics")]
#[path = "../message_flow_metrics.rs"]
mod message_flow_metrics;
#[cfg(feature = "prometheus-metrics")]
pub use message_flow_metrics::InstrumentedMessageFlowService;

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
    async fn connection_identity(&self, target: &BotDeliveryTarget) -> Option<String> {
        self.inner.connection_identity(target).await
    }

    async fn deliver_on_connection(
        &self,
        cmd: BotDeliveryCommand,
        connection_id: &str,
    ) -> ServiceResult<BotDeliveryResult> {
        let kind = bot_delivery_metric_kind(&cmd.delivery_kind);
        let start = Instant::now();
        let result = self.inner.deliver_on_connection(cmd, connection_id).await;
        let (outcome, code) = match &result {
            Ok(r) if r.delivered => (DeliveryOutcome::Delivered, DeliveryErrorCode::None),
            Ok(r) => (
                DeliveryOutcome::Failed,
                r.error
                    .as_ref()
                    .map(service_error_code)
                    .unwrap_or(DeliveryErrorCode::Unknown),
            ),
            Err(e) => (DeliveryOutcome::Failed, service_error_code(e)),
        };
        record_delivery(
            &self.env,
            DeliveryMetricTarget::Bot,
            kind,
            outcome,
            code,
            start.elapsed(),
        );
        result
    }

    async fn abort_on_connection(
        &self,
        cmd: BotAbortDeliveryCommand,
        connection_id: &str,
    ) -> ServiceResult<BotAbortDeliveryResult> {
        let start = Instant::now();
        let result = self.inner.abort_on_connection(cmd, connection_id).await;
        let (outcome, code) = match &result {
            Ok(_) => (DeliveryOutcome::Delivered, DeliveryErrorCode::None),
            Err(e) => (DeliveryOutcome::Failed, service_error_code(e)),
        };
        record_delivery(
            &self.env,
            DeliveryMetricTarget::Bot,
            DeliveryMetricKind::Abort,
            outcome,
            code,
            start.elapsed(),
        );
        result
    }

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

    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        let start = Instant::now();
        let result = self.inner.abort(cmd).await;
        let duration = start.elapsed();

        match &result {
            Ok(_) => record_delivery(
                &self.env,
                DeliveryMetricTarget::Bot,
                DeliveryMetricKind::Abort,
                DeliveryOutcome::Delivered,
                DeliveryErrorCode::None,
                duration,
            ),
            Err(error) => record_delivery(
                &self.env,
                DeliveryMetricTarget::Bot,
                DeliveryMetricKind::Abort,
                DeliveryOutcome::Failed,
                service_error_code(error),
                duration,
            ),
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
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
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
