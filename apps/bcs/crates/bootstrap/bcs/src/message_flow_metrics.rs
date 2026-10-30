//! Transparent message-flow metrics decorator, including result publication.
use super::*;

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
    async fn resolve_managed_provider_run(&self, run_id: &str, provider_id: &str, bot_id: &str) -> ServiceResult<Option<bcs_service_api::BotRunContext>> {
        self.inner.resolve_managed_provider_run(run_id, provider_id, bot_id).await
    }
    async fn query_message_deliveries(&self, query: bcs_service_api::application::message_delivery::DeliveryStatusQuery) -> ServiceResult<Vec<bcs_service_api::application::message_delivery::DeliveryStatusView>> {
        self.inner.query_message_deliveries(query).await
    }
    async fn cancel_message_deliveries(&self, command: bcs_service_api::application::message_delivery::CancelMessageDeliveryCommand) -> ServiceResult<Vec<bcs_service_api::application::message_delivery::CancelMessageDeliveryResult>> {
        self.inner.cancel_message_deliveries(command).await
    }
    async fn resolve_message_delivery(&self, command: bcs_service_api::application::message_delivery::ResolveMessageDeliveryCommand) -> ServiceResult<bcs_service_api::application::message_delivery::DeliveryStatusView> {
        self.inner.resolve_message_delivery(command).await
    }
    async fn shutdown_managed_delivery(&self) -> ServiceResult<()> {
        self.inner.shutdown_managed_delivery().await
    }
    async fn get_delivery_policy(&self, caller: bcs_service_api::CallerContext) -> ServiceResult<bcs_config_api::message_delivery::DeliveryPolicyRecord> {
        self.inner.get_delivery_policy(caller).await
    }
    async fn replace_delivery_policy(&self, caller: bcs_service_api::CallerContext, expected: u64, policy: bcs_config_api::message_delivery::DeliveryPolicy) -> ServiceResult<bcs_config_api::message_delivery::DeliveryPolicyRecord> {
        self.inner.replace_delivery_policy(caller, expected, policy).await
    }
    async fn record_delivery_acceptance(
        &self,
        request_id: &str,
        bot_id: &str,
        downstream_run_id: Option<&str>,
    ) -> ServiceResult<()> {
        self.inner.record_delivery_acceptance(request_id, bot_id, downstream_run_id).await
    }

    async fn publish_state_machine_result(
        &self,
        cmd: bcs_service_api::StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        self.inner.publish_state_machine_result(cmd).await
    }

    async fn publish_state_machine_run_result(
        &self,
        payload: bcs_service_api::StateMachineRunResultPayload,
    ) -> ServiceResult<bcs_service_api::StateMachineRunResultOutcome> {
        self.inner.publish_state_machine_run_result(payload).await
    }

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

    async fn cancel_latest_queued_message(
        &self,
        cmd: CancelLatestQueuedMessageCommand,
    ) -> ServiceResult<CancelLatestQueuedMessageOutcome> {
        self.inner.cancel_latest_queued_message(cmd).await
    }

    async fn rebind_channel_source_message(
        &self,
        source_run_id: &str,
        accepted_run_id: &str,
    ) -> ServiceResult<bool> {
        self.inner
            .rebind_channel_source_message(source_run_id, accepted_run_id)
            .await
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
