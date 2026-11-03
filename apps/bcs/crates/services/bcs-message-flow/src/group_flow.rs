use bcs_service_api::port::CoordinationIntentPort;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Arc, OnceLock};

use async_trait::async_trait;
use bcs_domain::{Attachment, MessageAudience, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_protocol::{
    Attachment as WireAttachment, BcsFrame, ChannelInfo, ChannelSource, RequestFrame,
    apply_channel_info, build_chat_inject_frame, build_chat_send_frame,
    build_direct_chat_inject_frame, build_direct_chat_send_frame, now_ms,
};
use bcs_service_api::{
    ActiveBotRunContext, ActorKind, ActorStatus, BotAbortDeliveryCommand, BotDeliveryCommand,
    BotDeliveryKind, BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget, BotEventCommand,
    BotEventOutcome, BotRegistryCoreService, BotRunContext, BotRunContextPort, BotRunScope,
    BotRunTransportOwner, BotTerminalObserverPort, CallerContext, ChannelService, ChatAbortCommand,
    CancelLatestQueuedMessageCommand, CancelLatestQueuedMessageOutcome, ChatAbortFailure,
    ChatAbortOutcome, ChatAbortScope, ChatEventState, DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
    DeliveryBlockContext, DeliveryBlockReason, DeliveryBlockSurface, DeliveryMetricKind,
    DeliveryMetricTarget, DeliveryType, FrontendDeliveryCommand, FrontendDeliveryKind,
    FrontendDeliveryPort, FrontendDeliveryResult, FrontendDeliveryTarget, Group,
    GroupCallbackCommand, GroupCallbackOutcome, GroupChatCommand, GroupChatOutcome,
    GroupCoreService, GroupKind, GroupMessage, GroupMessageType, GroupStatus, GroupStrategy,
    HiddenMentionInfo, MESSAGE_LOG_SCHEMA_VERSION, MessageDeliveryResult, MessageFlowService,
    MessageLogContent, MessageLogEventType, MessageLogMode, MessageLogStatus,
    MessageLogTargetSummary, MessageRole, NoopBotTerminalObserver, Participant, ParticipantMode,
    ParticipantRole, PersistentGroupSendCommand, PersistentGroupSendOutcome,
    ProviderStreamGrayList, RouteParticipantOverlay, RoutingCoreService, RoutingDecision,
    RoutingTarget, ServiceError, ServiceResult, SessionManagementService, SessionStatus,
    SystemMessageEvent, SystemMessageService, TaskCompleteCommand, TaskCompleteOutcome,
    TaskDispatchCommand, TaskDispatchOutcome, TaskMessageCommand, TaskMessageOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome, backfill_bot_names,
    interceptor::{
        BlockReason, InterceptorChain, InterceptorDecision, MessageInterceptor, OutboundMessage,
    },
    message_log_json,
    port::repo::{AppendMessageWithEvent, MessageRepoPort},
    port::{EventRecordFactoryPort, EventRecorderPort, HumanMentionNotifyPort, NewEvent},
    types::{EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventScope, EventSubject},
};
use chrono::{SecondsFormat, TimeZone, Utc};
use futures::{StreamExt, stream};
use regex::Regex;
use serde_json::Value;
use tracing::{info, warn, Instrument};

use crate::MSG_LOG_TARGET;
use crate::protocol_context::{group_context_input, group_type_wire};
use crate::task_store::TaskStore;

/// Live Human→Bot control judgment (bot-authority plan
/// Task 6.5, spec §8): the legacy `created_by` comparison
/// is replaced by the authority hook. A failing authority
/// read is an internal error — never a deny-as-verdict.
pub(crate) async fn human_controls_bot(
    flow: &BcsMessageFlow,
    staff_no: &str,
    bot_id: &str,
) -> ServiceResult<bool> {
    match flow.authority()?.can_manage(staff_no, bot_id).await {
        Ok(controlled) => Ok(controlled),
        Err(error) => Err(ServiceError::InternalError(format!(
            "bot authority lookup failed: {error}"
        ))),
    }
}

pub struct BcsMessageFlow {
    pub execution_cache: Option<Arc<dyn bcs_cache_api::CachePlugin>>,
    pub direct_chat: Option<Arc<crate::a2a_chat::A2aChat>>,
    pub group: Arc<dyn GroupCoreService>,
    pub routing: Arc<dyn RoutingCoreService>,
    pub registry: Arc<dyn BotRegistryCoreService>,
    pub bot_delivery: Arc<dyn BotDeliveryPort>,
    pub frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    pub task_store: Arc<TaskStore>,
    pub bot_relay_turn_limit: i64,
    pub interceptors: Arc<InterceptorChain>,
    pub session_management: Option<Arc<dyn SessionManagementService>>,
    pub(crate) coordination_intents: Option<Arc<dyn CoordinationIntentPort>>,
    pub bot_run_context: Option<Arc<dyn BotRunContextPort>>,
    pub provider_chat_run_timeout_ms: u64,
    pub system_message: Option<Arc<dyn SystemMessageService>>,
    pub message_repo: Option<Arc<dyn MessageRepoPort>>,
    /// Read-only delivery queries share the configured Chat history window.
    /// Bootstrap overrides this with message_history.new_participant_visible_limit.
    pub new_participant_visible_limit: u64,
    pub event_record_factory: Option<Arc<dyn EventRecordFactoryPort>>,
    pub event_recorder: Option<Arc<dyn EventRecorderPort>>,
    pub message_tracker: Arc<crate::message_tracker::MessageTracker>,
    /// Deprecated compatibility setting. Transport selection is owned by the
    /// HTTP Provider adapter and this value is no longer consulted.
    pub provider_stream_gray_list: Option<Arc<ProviderStreamGrayList>>,
    pub channel: Arc<OnceLock<Arc<dyn ChannelService>>>,
    pub bot_terminal_observer: Arc<dyn BotTerminalObserverPort>,
    pub human_mention_notify: Option<Arc<dyn HumanMentionNotifyPort>>,
    pub managed_deliveries: Option<Arc<dyn bcs_service_api::application::message_delivery::ManagedMessageDeliveryService>>,
    /// Narrow bot-authority hook consulted for Human→Bot control
    /// judgments (bot-authority plan Task 6.5). Unwired flows fail
    /// closed at any judgment that needs live facts.
    pub bot_authority: Option<Arc<dyn bcs_service_api::port::bot_authority::BotAuthorityPort>>,
    /// Output-permit admission for accepted business operations
    /// (bot-authority spec §8.2): installed by composition, consulted
    /// by `handle_web_send_with_result` / `handle_persistent_group_send`
    /// BEFORE their first side effect. A `None` flow (unit fixtures)
    /// simply skips the output-admission boundary.
    pub output_admission: Option<Arc<dyn bcs_service_api::OutputAdmissionPort>>,
    /// Composition supplies only Bots enabled for new Group admissions.
    pub group_delivery_limits: BTreeMap<String, u32>,
    pub delivery_policy: Option<Arc<crate::delivery_policy::LiveDeliveryPolicy>>,
    pub delivery_queue_ttl_ms: Option<i64>,
    /// Speaking-lease idle window for workbench submissions (§15.99,
    /// `[speaking_lease] idle_timeout_ms`): both the submission check and
    /// the post-acceptance renewal use this exact configured value.
    pub speaking_lease_idle_timeout_ms: i64,
    pub queue_persistable_headers: Vec<String>,
    /// Retained policy for replies from already-admitted Group runs during drain.
    pub group_reply_delivery_limits: BTreeMap<String, u32>,
    pub delivery_shutdown: OnceLock<(tokio::sync::watch::Sender<bool>, tokio::sync::watch::Receiver<bool>)>,
    pub(crate) delivery_event_locks: tokio::sync::Mutex<BTreeMap<String, std::sync::Weak<tokio::sync::Mutex<()>>>>,
    /// Caller admission seam dependencies (spec §7.5.1). `None` keeps the
    /// ordinary web_send behavior untouched; the composition root opts in.
    pub caller_admission: Option<Arc<crate::caller_admission::CallerAdmissionDeps>>,
    /// Speaking-lease store (spec §6.3, P3): injected only where the
    /// composition provides one. `None` keeps ordinary groups untouched;
    /// a caller group without a store fails closed instead of un-limiting
    /// Human concurrency.
    pub speaking_leases: Option<
        Arc<dyn bcs_service_api::port::repo::session_speaking_lease::SpeakingLeaseRepoPort>,
    >,
    /// Caller root-lineage registry (spec §8.3, L243-A): runtime-only, the
    /// accepted protected dispatch is the single trusted producer; the
    /// coordination derivation gate is the single reader. `None` keeps the
    /// composition without caller derivations entirely.
    pub caller_root_lineage: Option<Arc<crate::caller_lineage::RootLineageIndex>>,
    /// Shared observe assembly (spec §6.4, L243③ wire): the runtime-only
    /// observe index plus the composition's injector factory — the reply
    /// sync's collaborators. Set by the caller wiring epoch together with
    /// the runtime pipeline; `None` keeps reply syncs off.
    pub caller_observe: Option<Arc<crate::caller_dispatch_pipeline::ObserveSyncAssembly>>,
    /// §15.83 S3/S4 (review-VI P1): the protected dispatch pipeline this
    /// composition built — the composition root hands the SAME instance to
    /// other application services (the collaboration runtime's orchestration
    /// node dispatch + instance registrar). Absent in test/dev compositions.
    pub caller_dispatch_pipeline: Option<Arc<crate::caller_dispatch_pipeline::CallerDispatchPipeline>>,
    /// Owner→caller identity relay (spec L62 direction, opt-in): the ledger
    /// captures the session's current human ingress at admission and the
    /// trusted task-assign edge redeems it onto an allowlisted caller
    /// target's handoff scope. None of it travels through the middle bot;
    /// `None` keeps every session fail-closed to today's behavior.
    pub caller_relay: Option<crate::caller_relay::CallerRelayState>,
    pub(crate) terminal_owner: OnceLock<std::sync::Weak<BcsMessageFlow>>,
    terminal_slots: Arc<tokio::sync::Semaphore>,
    system_queue: Arc<crate::queued_system::QueuedSystemAdmission>,
}

impl BcsMessageFlow {
    pub fn new(
        group: Arc<dyn GroupCoreService>,
        routing: Arc<dyn RoutingCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        bot_delivery: Arc<dyn BotDeliveryPort>,
        frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    ) -> Self {
        Self {
            execution_cache: None,
            direct_chat: None,
            group,
            routing,
            registry,
            bot_delivery,
            frontend_delivery,
            task_store: Arc::new(TaskStore::new()),
            bot_relay_turn_limit: 0,
            interceptors: Arc::new(InterceptorChain::new()),
            session_management: None,
            coordination_intents: None,
            bot_run_context: None,
            provider_chat_run_timeout_ms: DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
            system_message: None,
            message_repo: None,
            new_participant_visible_limit: 100,
            event_record_factory: None,
            event_recorder: None,
            message_tracker: Arc::new(crate::message_tracker::MessageTracker::new()),
            provider_stream_gray_list: None,
            channel: Arc::new(OnceLock::new()),
            bot_terminal_observer: Arc::new(NoopBotTerminalObserver),
            human_mention_notify: None,
            bot_authority: None,
            output_admission: None,
            managed_deliveries: None,
            group_delivery_limits: BTreeMap::new(),
            delivery_policy: None,
            delivery_queue_ttl_ms: None,
            speaking_lease_idle_timeout_ms: 60_000,
            group_reply_delivery_limits: BTreeMap::new(),
            queue_persistable_headers: Vec::new(),
            delivery_shutdown: OnceLock::new(),
            delivery_event_locks: Default::default(),
            terminal_owner: OnceLock::new(),
            terminal_slots: Arc::new(tokio::sync::Semaphore::new(64)),
            system_queue: Arc::new(crate::queued_system::QueuedSystemAdmission::default()),
            caller_admission: None,
            speaking_leases: None,
            caller_root_lineage: None,
            caller_observe: None,
            caller_dispatch_pipeline: None,
            caller_relay: None,
        }
    }

    pub fn with_caller_relay(mut self, relay: crate::caller_relay::CallerRelayState) -> Self {
        self.caller_relay = Some(relay);
        self
    }

    pub fn with_execution_cache(mut self, cache: Arc<dyn bcs_cache_api::CachePlugin>) -> Self {
        self.execution_cache = Some(cache);
        self
    }

    pub fn with_direct_chat(mut self, direct: Arc<crate::a2a_chat::A2aChat>) -> Self { self.direct_chat = Some(direct); self }

    /// Wire the narrow bot-authority hook (Task 6.5). A flow
    /// without one fails closed at its Human→Bot control
    /// judgments instead of consulting legacy `created_by` data.
    pub fn with_bot_authority_hook(
        mut self,
        hook: Option<Arc<dyn bcs_service_api::port::bot_authority::BotAuthorityPort>>,
    ) -> Self {
        self.bot_authority = hook;
        self
    }

    /// Wire the output-admission port (spec §8.2): every accepted
    /// business operation freezes per-connection delivery permits
    /// through it before its first side effect, so later output of
    /// THAT operation needs no fresh authority read and is never
    /// retroactively revoked.
    pub fn with_output_admission(
        mut self,
        port: Option<Arc<dyn bcs_service_api::OutputAdmissionPort>>,
    ) -> Self {
        self.output_admission = port;
        self
    }

    /// The authority hook; failing to wire one is an error, never
    /// a deny-as-Ok nor a deny-as-forbidden.
    pub(crate) fn authority(
        &self,
    ) -> Result<&dyn bcs_service_api::port::bot_authority::BotAuthorityPort, ServiceError> {
        self.bot_authority
            .as_deref()
            .ok_or_else(|| ServiceError::InternalError("bot authority hook is not wired".into()))
    }

    pub fn with_coordination_intents(mut self, port: Option<Arc<dyn CoordinationIntentPort>>) -> Self {
        self.coordination_intents = port;
        self
    }

    pub fn channel_slot(&self) -> Arc<OnceLock<Arc<dyn ChannelService>>> {
        self.channel.clone()
    }

    /// Composition installs a weak self-reference so accepted terminal work
    /// can survive a disconnected caller without retaining the service forever.
    pub fn retain_terminal_events(self: &Arc<Self>) {
        let _ = self.terminal_owner.set(Arc::downgrade(self));
        self.system_queue.bind(self);
        if let Some(direct) = &self.direct_chat { direct.bind_queue(self); }
    }

    pub fn system_queue_port(&self) -> Arc<dyn bcs_service_api::application::system_message::SystemMessageQueueService> {
        self.system_queue.clone()
    }

    pub fn with_managed_deliveries(mut self, service: Arc<dyn bcs_service_api::application::message_delivery::ManagedMessageDeliveryService>) -> Self {
        self.managed_deliveries = Some(service);
        self
    }

    pub fn with_speaking_leases(
        mut self,
        leases: Arc<dyn bcs_service_api::port::repo::session_speaking_lease::SpeakingLeaseRepoPort>,
    ) -> Self {
        self.speaking_leases = Some(leases);
        self
    }

    /// Attach the caller derivation collaborators (spec §8.3/§7.2, L243-A):
    /// both must be present — the runtime-only lineage registry the accepted
    /// protected dispatch produces, and the durable child repo the
    /// coordination edge registers derived children under. Partial
    /// assemblies stay absent entirely (fail-closed, no phantom store).
    /// Attach the shared observe assembly for the reply-side sync
    /// (spec §6.4 Bot 共享回复同步): the same index the accepted dispatch
    /// seeds, plus the injector factory for the replying bot's own
    /// Provider target.
    pub fn with_caller_observe(
        mut self,
        observe: Arc<crate::caller_dispatch_pipeline::ObserveSyncAssembly>,
    ) -> Self {
        self.caller_observe = Some(observe);
        self
    }

    pub fn with_caller_derivation(
        mut self,
        root_lineage: Arc<crate::caller_lineage::RootLineageIndex>,
    ) -> Self {
        self.caller_root_lineage = Some(root_lineage);
        self
    }

    pub fn with_group_delivery_limits(mut self, limits: BTreeMap<String, u32>) -> Self {
        self.group_reply_delivery_limits = limits.clone();
        self.group_delivery_limits = limits;
        self
    }

    pub fn pending_message_port(
        &self,
    ) -> ServiceResult<Arc<dyn bcs_service_api::PendingGroupMessagePort>> {
        let run_context = self.bot_run_context.clone().ok_or_else(|| {
            ServiceError::InternalError(
                "pending message reader requires BotRunContextPort".to_string(),
            )
        })?;
        Ok(Arc::new(crate::pending_message::PendingMessageReader::new(
            self.message_tracker.clone(),
            run_context,
        )))
    }

    pub fn with_bot_terminal_observer(
        mut self,
        observer: Arc<dyn BotTerminalObserverPort>,
    ) -> Self {
        self.bot_terminal_observer = observer;
        self
    }

    pub fn with_system_message(mut self, system_message: Arc<dyn SystemMessageService>) -> Self {
        self.system_message = Some(system_message);
        self
    }

    pub fn with_human_mention_notify(
        mut self,
        human_mention_notify: Arc<dyn HumanMentionNotifyPort>,
    ) -> Self {
        self.human_mention_notify = Some(human_mention_notify);
        self
    }

    pub fn with_bot_relay_turn_limit(mut self, bot_relay_turn_limit: i64) -> Self {
        self.bot_relay_turn_limit = bot_relay_turn_limit;
        self
    }

    pub fn with_session_management(
        mut self,
        session_management: Arc<dyn SessionManagementService>,
    ) -> Self {
        self.session_management = Some(session_management);
        self
    }

    pub fn with_bot_run_context(mut self, run_context: Arc<dyn BotRunContextPort>) -> Self {
        self.bot_run_context = Some(run_context);
        self
    }

    /// Share the producer registry used by ALL context writers (A2A, system,
    /// queue and group sends). Composition passes this same port to producers.
    pub fn with_tracked_run_context(mut self, context: Arc<crate::TrackedBotRunContext>) -> Self {
        self.message_tracker = context.tracker();
        self.bot_run_context = Some(context);
        self
    }

    pub fn with_provider_chat_run_timeout_ms(mut self, timeout_ms: u64) -> Self {
        self.provider_chat_run_timeout_ms = timeout_ms;
        self
    }

    pub fn with_task_store(mut self, task_store: Arc<TaskStore>) -> Self {
        self.task_store = task_store;
        self
    }

    pub fn with_message_repo(mut self, message_repo: Arc<dyn MessageRepoPort>) -> Self {
        self.message_repo = Some(message_repo);
        self
    }

    pub fn with_event_record_factory(
        mut self,
        event_record_factory: Arc<dyn EventRecordFactoryPort>,
    ) -> Self {
        self.event_record_factory = Some(event_record_factory);
        self
    }

    pub fn with_event_recorder(mut self, event_recorder: Arc<dyn EventRecorderPort>) -> Self {
        self.event_recorder = Some(event_recorder);
        self
    }

    pub fn with_provider_stream_gray_list(
        mut self,
        gray_list: Arc<ProviderStreamGrayList>,
    ) -> Self {
        self.provider_stream_gray_list = Some(gray_list);
        self
    }

    pub fn with_interceptor<I>(mut self, interceptor: I) -> Self
    where
        I: MessageInterceptor + 'static,
    {
        let mut chain = InterceptorChain::new();
        chain.push(interceptor);
        self.interceptors = Arc::new(chain);
        self
    }

    pub fn with_interceptors(mut self, interceptors: Arc<InterceptorChain>) -> Self {
        self.interceptors = interceptors;
        self
    }

    pub(crate) async fn register_send_context(
        &self,
        delivery_type: DeliveryType,
        delivery_target: &BotDeliveryTarget,
        frame: &BcsFrame,
        run_id: &str,
        bot_id: &str,
        group_id: &str,
        bcs_session_id: Option<&str>,
        provider_bypass_headers: &[(String, String)],
    ) -> ServiceResult<()> {
        if delivery_type != DeliveryType::Send {
            return Ok(());
        }
        let timeout_ms = self.task_store.get(run_id).await.and_then(|task| task.timeout_s)
            .map(|seconds| seconds.saturating_mul(1000).saturating_add(300_000).max(self.provider_chat_run_timeout_ms))
            .unwrap_or(self.provider_chat_run_timeout_ms);
        let deadline_ms = now_ms().saturating_add(timeout_ms);
        if let Some(run_context) = &self.bot_run_context {
            let session_id = bcs_session_id.unwrap_or(group_id).to_string();
            run_context
                .put_context(BotRunContext {
                    run_id: run_id.to_string(),
                    bot_id: bot_id.to_string(),
                    group_id: group_id.to_string(),
                    bcs_session_id: Some(session_id.clone()),
                    deadline_ms,
                    terminal: false,
                })
                .await;
            let transport_owner = BotRunTransportOwner::from_target(delivery_target);
            let provider_bypass_headers = if delivery_target.is_http_provider() { provider_bypass_headers.to_vec() } else { Vec::new() };
            run_context
                .register_active_run(ActiveBotRunContext {
                    canonical_run_id: run_id.to_string(),
                    downstream_run_id: run_id.to_string(),
                    downstream_session_key: request_session_key(frame),
                    scope: BotRunScope {
                        group_id: group_id.to_string(),
                        session_id,
                        bot_id: bot_id.to_string(),
                    },
                    transport_owner,
                    provider_bypass_headers,
                    deadline_ms,
                })
                .await?;
        }
        // Explicitly distinguish live internal/relay Runs from a retired Run
        // whose operation binding was cleaned up. Never recreate this at ingress.
        self.message_tracker.register_independent_output_run(run_id, deadline_ms).await;
        Ok(())
    }

    pub(crate) async fn discard_send_context(&self, run_id: &str) -> ServiceResult<()> {
        self.message_tracker.cleanup_output_run(run_id).await;
        let Some(run_context) = self.bot_run_context.as_ref() else {
            return Ok(());
        };
        let Some(context) = run_context.find_active_run(run_id).await? else {
            return Ok(());
        };
        let _ = run_context.mark_terminal(run_id).await;
        run_context
            .remove_active_run(&context.scope, &context.canonical_run_id)
            .await?;
        Ok(())
    }

    /// Explicitly release runs that can no longer produce output
    /// (confirmed rejection or an unsent target): drop their operation
    /// bindings so stray events stop tagging the operation, and
    /// tell the delivery adapter — an explicit end-state that lets
    /// the operation complete on its surviving streams (spec §8.2).
    pub(crate) async fn release_output_runs(&self, operation_id: &str, run_ids: &[String]) {
        for run_id in run_ids {
            self.message_tracker.cleanup_output_run(run_id).await;
        }
        if let Some(output_admission) = self.output_admission.as_ref() {
            let _ = output_admission
                .release_operation_runs(operation_id, run_ids.to_vec())
                .await;
        }
    }

    /// A durable terminal for a never-sent Send has no Bot callback to close
    /// its stream. No TTL: queued/unknown/running work retains its permit.
    pub(crate) async fn release_unsent_output(&self, row: &bcs_domain::message_delivery::PersistedMessageDelivery) {
        if row.state.kind != DeliveryType::Send || row.state.may_have_been_sent || !row.state.status.is_terminal() {
            return;
        }
        let Some(run) = &row.run_id else { return; };
        if let Some(operation) = self.message_tracker.operation_of_run(run).await {
            self.release_output_runs(&operation, std::slice::from_ref(run)).await;
        }
    }

    pub(crate) async fn complete_send_context(&self, run_id: &str) -> ServiceResult<()> {
        let Some(run_context) = self.bot_run_context.as_ref() else {
            return Ok(());
        };
        let Some(context) = run_context.find_active_run(run_id).await? else {
            return Ok(());
        };
        let _ = run_context.mark_terminal(&context.canonical_run_id).await;
        run_context
            .remove_active_run(&context.scope, &context.canonical_run_id)
            .await?;
        Ok(())
    }
}

pub(crate) fn request_session_key(frame: &BcsFrame) -> Option<String> {
    let BcsFrame::Request(request) = frame else {
        return None;
    };
    request
        .params
        .as_ref()
        .and_then(|params| params.get("session_key"))
        .and_then(Value::as_str)
        .map(str::to_string)
}

#[async_trait]
impl MessageFlowService for BcsMessageFlow {
    async fn get_delivery_policy(&self, caller: CallerContext) -> ServiceResult<bcs_config_api::message_delivery::DeliveryPolicyRecord> {
        self.delivery_policy.as_ref().ok_or_else(|| ServiceError::InternalError("delivery policy unavailable".into()))?.get(caller).await
    }
    async fn replace_delivery_policy(&self, caller: CallerContext, expected: u64, policy: bcs_config_api::message_delivery::DeliveryPolicy) -> ServiceResult<bcs_config_api::message_delivery::DeliveryPolicyRecord> {
        self.delivery_policy.as_ref().ok_or_else(|| ServiceError::InternalError("delivery policy unavailable".into()))?.replace(caller, expected, policy).await
    }
    async fn resolve_managed_provider_run(&self, run_id: &str, provider_id: &str, bot_id: &str) -> ServiceResult<Option<BotRunContext>> {
        let Some(service) = &self.managed_deliveries else { return Ok(None); };
        for row in service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Run { bot: bot_id.into(), alias: run_id.into() }).await.map_err(|_| ServiceError::InternalError("managed Provider run lookup failed".into()))? {
            if !matches!(row.state.status, bcs_domain::message_delivery::MessageDeliveryStatus::Dispatching | bcs_domain::message_delivery::MessageDeliveryStatus::Running | bcs_domain::message_delivery::MessageDeliveryStatus::Unknown | bcs_domain::message_delivery::MessageDeliveryStatus::Cancelling | bcs_domain::message_delivery::MessageDeliveryStatus::CancelUnknown) { continue; }
            if !row.state.may_have_been_sent || row.target_bot_id != bot_id { continue; }
            let Some(metadata) = row.transport_context_json.as_ref() else { continue; };
            if metadata.get("owner").and_then(|v| v.get("provider_id")).and_then(Value::as_str) != Some(provider_id) { continue; }
            if row.run_id.as_deref() != Some(run_id) && row.request_id.as_deref() != Some(run_id)
                && metadata.get("downstream_run_id").and_then(Value::as_str) != Some(run_id) { continue; }
            let owner: BotRunTransportOwner = serde_json::from_value(metadata.get("owner").cloned().unwrap_or(Value::Null))?;
            if !provider_owner_matches_target(&owner, &self.registry.resolve_delivery_target(bot_id).await?) {
                return Err(ServiceError::Forbidden("original Provider binding changed".into()));
            }
            return Ok(Some(BotRunContext {
                run_id: row.run_id.unwrap_or_default(), bot_id: row.target_bot_id, group_id: row.group_id,
                bcs_session_id: Some(row.session_id), deadline_ms: u64::MAX, terminal: false,
            }));
        }
        Ok(None)
    }
    async fn task_status(&self, refs: Vec<String>) -> ServiceResult<Vec<bcs_service_api::TaskHandleView>> {
        crate::task_handle::status(self, refs).await
    }
    async fn cancel_task(&self, task_ref: String, reason: String) -> ServiceResult<bcs_service_api::TaskHandleView> {
        crate::task_handle::cancel(self, task_ref, reason).await
    }
    async fn query_message_deliveries(&self, query: bcs_service_api::application::message_delivery::DeliveryStatusQuery) -> ServiceResult<Vec<bcs_service_api::application::message_delivery::DeliveryStatusView>> {
        crate::delivery_control::query(self, query).await
    }
    async fn cancel_message_deliveries(&self, command: bcs_service_api::application::message_delivery::CancelMessageDeliveryCommand) -> ServiceResult<Vec<bcs_service_api::application::message_delivery::CancelMessageDeliveryResult>> {
        crate::delivery_control::cancel(self, command).await
    }
    async fn resolve_message_delivery(&self, command: bcs_service_api::application::message_delivery::ResolveMessageDeliveryCommand) -> ServiceResult<bcs_service_api::application::message_delivery::DeliveryStatusView> {
        crate::delivery_control::resolve(self, command).await
    }
    async fn shutdown_managed_delivery(&self) -> ServiceResult<()> {
        if let Some((sender, completion)) = self.delivery_shutdown.get() {
            let _ = sender.send(true);
            let mut completion = completion.clone();
            tokio::time::timeout(std::time::Duration::from_secs(60), completion.wait_for(|done| *done)).await
                .map_err(|_| ServiceError::InternalError("queue shutdown timed out".into()))?
                .map_err(|_| ServiceError::InternalError("queue shutdown completion unavailable".into()))?;
        }
        Ok(())
    }
    async fn record_delivery_acceptance(&self, request_id: &str, bot_id: &str, downstream_run_id: Option<&str>) -> ServiceResult<()> {
        if let Some(service) = &self.managed_deliveries {
            service.accept_run(request_id, bot_id, downstream_run_id, Utc::now().timestamp_millis())
                .await.map_err(|_| ServiceError::InternalError("managed delivery ACK persistence failed".into()))?;
        }
        Ok(())
    }
    async fn publish_state_machine_result(
        &self,
        cmd: bcs_service_api::StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        state_machine_result::publish(self, cmd).await
    }

    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        handle_web_send(self, cmd).await
    }

    async fn handle_group_chat(&self, cmd: GroupChatCommand) -> ServiceResult<GroupChatOutcome> {
        handle_group_chat(self, cmd).await
    }

    async fn handle_persistent_group_send(
        &self,
        cmd: PersistentGroupSendCommand,
    ) -> ServiceResult<PersistentGroupSendOutcome> {
        handle_persistent_group_send(self, cmd).await
    }

    async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        if self.managed_deliveries.is_some()
            && matches!(cmd.state, ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted)
            && matches!(cmd.event_type.as_str(), "chat" | "chat.event")
            && let Some(owner) = self.terminal_owner.get().and_then(std::sync::Weak::upgrade)
        {
            // Bound retained payloads. While full, apply backpressure instead
            // of spawning an unbounded terminal retry queue.
            let permit = self.terminal_slots.clone().acquire_owned().await
                .map_err(|_| ServiceError::InternalError("terminal processing unavailable".into()))?;
            return tokio::spawn(async move {
                let _permit = permit;
                let result = crate::bot_event::handle_bot_event(&owner, cmd).await;
                if result.is_err() { tracing::warn!("retained terminal processing failed; delivery state remains authoritative"); }
                result
            }.in_current_span()).await.map_err(|error| ServiceError::InternalError(format!("terminal processing task failed: {error}")))?;
        }
        crate::bot_event::handle_bot_event(self, cmd).await
    }

    async fn handle_group_callback(
        &self,
        cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        handle_group_callback(self, cmd).await
    }

    async fn handle_chat_abort(&self, cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        handle_chat_abort(self, cmd).await
    }

    async fn cancel_latest_queued_message(
        &self,
        cmd: CancelLatestQueuedMessageCommand,
    ) -> ServiceResult<CancelLatestQueuedMessageOutcome> {
        crate::delivery_control::cancel_latest_queued(self, cmd).await
    }

    async fn resolve_chat_abort_scope(
        &self,
        group_id: &str,
        run_id: &str,
    ) -> ServiceResult<Option<ChatAbortScope>> {
        let Some(run_context) = self.bot_run_context.as_ref() else {
            return Ok(None);
        };
        let Some(context) = run_context.find_active_run(run_id).await? else {
            return Ok(None);
        };
        if context.scope.group_id != group_id {
            return Ok(None);
        }
        Ok(Some(ChatAbortScope {
            group_id: context.scope.group_id,
            session_id: context.scope.session_id,
            bot_id: context.scope.bot_id,
        }))
    }

    async fn rebind_channel_source_message(
        &self,
        source_run_id: &str,
        accepted_run_id: &str,
    ) -> ServiceResult<bool> {
        // All tracked aliases normalize to their source Run at event ingress;
        // keep its IM destination under that same canonical identity.
        if self.message_tracker.output_binding(source_run_id).await.is_some() {
            return Ok(self.message_tracker.channel_source_message_id(source_run_id).await.is_some());
        }
        Ok(self
            .message_tracker
            .rebind_channel_source_message_id(source_run_id, accepted_run_id)
            .await)
    }

    async fn register_task_run_alias(
        &self,
        task_id: &str,
        run_id: &str,
        bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        let task = self.task_store.get(task_id).await;
        if task.as_ref().is_some_and(|entry| entry.target_bot != bot_id
            || entry.status != crate::task_store::TaskLedgerStatus::Dispatched)
            || self.task_store.resolve_task_id(run_id).await.is_some_and(|id| id != task_id) {
            return Ok(TaskRunAliasRegistration::Rejected);
        }
        if self.message_tracker.output_binding(task_id).await.is_some() {
            let scope = if let Some(entry) = task {
                Some(BotRunScope {
                    session_id: entry.session_id.unwrap_or_else(|| entry.group_id.clone()),
                    group_id: entry.group_id, bot_id: entry.target_bot,
                })
            } else if let Some(contexts) = &self.bot_run_context {
                match contexts.find_active_run(task_id).await? {
                    Some(context) => Some(context.scope),
                    None => contexts.get_context(task_id).await
                        .filter(|context| !context.terminal && context.deadline_ms > now_ms())
                        .map(|context| BotRunScope {
                            session_id: context.bcs_session_id.unwrap_or_default(),
                            group_id: context.group_id, bot_id: context.bot_id,
                        }),
                }
            } else { None };
            let Some(scope) = scope.filter(|scope| scope.bot_id == bot_id) else {
                return Ok(TaskRunAliasRegistration::Rejected);
            };
            if !self.message_tracker.register_output_alias(task_id, run_id, scope).await {
                return Ok(TaskRunAliasRegistration::Rejected);
            }
        }
        let result = self.task_store.register_alias_for_dispatched_target(task_id, run_id, bot_id).await;
        Ok(match result {
            Some(true) => TaskRunAliasRegistration::Registered,
            Some(false) => TaskRunAliasRegistration::Rejected,
            None => TaskRunAliasRegistration::NotTask,
        })
    }

    async fn handle_task_dispatch(
        &self,
        cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        crate::task_flow::handle_task_dispatch(self, cmd).await
    }

    async fn handle_task_message(
        &self,
        cmd: TaskMessageCommand,
    ) -> ServiceResult<TaskMessageOutcome> {
        crate::task_flow::handle_task_message(self, cmd).await
    }

    async fn handle_task_complete(
        &self,
        cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        crate::task_flow::handle_task_complete(self, cmd).await
    }
}

pub(crate) async fn manager_worker_self_owner(
    flow: &BcsMessageFlow,
    group_id: &str,
    session_id: Option<&str>,
    sender_id: &str,
) -> Option<String> {
    let group = flow.group.get(group_id).await?;
    if group.group_strategy == GroupStrategy::ManagerWorker {
        if let (Some(session_id), Some(session_mgmt)) =
            (session_id, flow.session_management.as_ref())
        {
            if let Ok(Some(session)) = session_mgmt.get(session_id).await {
                if session.group_id == group_id {
                    if let Some(participant) = session
                        .participants
                        .iter()
                        .find(|participant| participant.bot_uuid == sender_id)
                    {
                        return (participant.is_bot()
                            && participant.role == ParticipantRole::Worker)
                            .then(|| sender_id.to_string());
                    }
                }
            }
        }

        if let Some(participant) = group.get_participant(sender_id) {
            if participant.is_bot() && participant.role == ParticipantRole::Worker {
                return Some(sender_id.to_string());
            }
        }
    } else {
        return None;
    }
    None
}

pub(crate) async fn apply_session_participant_scope(
    flow: &BcsMessageFlow,
    group: &mut Group,
    session_id: Option<&str>,
) -> ServiceResult<()> {
    let (Some(session_id), Some(session_mgmt)) = (session_id, flow.session_management.as_ref())
    else {
        return Ok(());
    };
    let session = session_mgmt
        .get(session_id)
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
    if session.group_id != group.id {
        return Err(ServiceError::InvalidOperation {
            message: format!(
                "session '{}' does not belong to group '{}'",
                session_id, group.id
            ),
            request_id: None,
        });
    }
    if session.participants.is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: format!("session '{}' has no participants", session_id),
            request_id: None,
        });
    }
    group.participants = session.participants;
    Ok(())
}

mod persistence;
pub(crate) use persistence::{try_persist_group_message, persisted_message_visibility};
use persistence::{persisted_inbound_content};
mod state_machine_result;
mod web_send;
pub use web_send::{handle_web_send};
mod group_send;
pub use group_send::{handle_group_chat, handle_persistent_group_send};
mod interceptors;
pub use interceptors::{apply_a2a_interceptors, apply_task_interceptors};
pub(crate) use interceptors::{apply_outbound_interceptors, apply_notify_outbound_policy};
mod callback_abort;
pub use callback_abort::{handle_group_callback, handle_chat_abort};
use callback_abort::{provider_owner_matches_target};
mod routing;
pub(crate) use routing::{apply_overlay_to_decision};
use routing::{resolve_group_chat_sender, verify_group_chat_caller_access, verify_group_chat_sender, verify_http_group_message_sender, is_human_bot_dm, verify_http_group_message_caller_access, sender_name_for_chat, build_route_overlay, build_explicit_mention_decision, callback_routable_message, mentions_all, delivery_result_summary, sender_display_name, preferred_sender_display_name, from_bot_owner};
mod delivery;
pub use delivery::{build_chat_abort_frame};
pub(crate) use delivery::{ContextProjection, context_projection_for_delivery, frame_protocol_version, frontend_domain_for_group};
use delivery::{frame_for_target, bot_delivery_kind, delivery_metric_kind, publish_web_user_message, publish_group_callback_event, publish_chat_abort_event, route_source_for_web_send, log_message_received, log_routing_digest, log_bot_deliver_result};
mod output_facts;
pub(crate) use output_facts::freeze_run_output_facts;
use output_facts::session_snapshot_for_freeze;
