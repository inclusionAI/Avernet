use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    sync::Arc,
    time::{Duration, Instant},
};

use async_trait::async_trait;
use bcs_domain::state_machine_history::{merge_state_machine_history, physical_message_id, project_state_machine_message};
use bcs_domain::{
    BCS_STATE_MACHINE_MESSAGE_SENDER, BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
    CollaborationDefinition, CollaborationDefinitionRef, CollaborationRuntimeDefinition, Group,
    GroupKind, GroupMessage, GroupMessageType, GroupRuntimeBinding, GroupStatus, GroupStrategy,
    HumanMessageView, MessageAudience, MessageOwnerFilter, MessageQuery, MessageRole,
    MessageVisibilityDomain, NewMessage, OpeningMessage, OpeningMessageRenderContext,
    OpeningMessageScope, Participant, ParticipantMode, ParticipantRole, RenderedOpeningMessage,
    ResolvedParticipant, ResolvedParticipantBinding, RuntimeParticipantBinding,
    STATE_MACHINE_PANEL_MESSAGE_TYPE, SenderType,
    Session, StateMachineAssignee, StateMachineDeliveryCorrelation, StateMachineNodeKind,
    StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus,
};
use bcs_protocol::{
    BCS_PROTOCOL_VERSION, BcsFrame, EventFrame, GroupContextInput, GroupContextParticipant,
    build_chat_send_frame,
};
use bcs_service_api::port::repo::{MessageRepoPort, StateMachineEventfulTransition};
use bcs_service_api::port::{EventRecordFactoryPort, NewEvent};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EventActor, EventActorType, EventScope, EventSubject,
};
use bcs_service_api::{
    AuthenticatedHumanCaller, BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort,
    BotDeliveryTarget, BotRegistryCoreService, BotRunContext, BotRunContextPort,
    CancelStateMachineRunCommand, ChatEventState, CollaborationDefinitionRecord,
    CollaborationDefinitionValidationOutcome, CollaborationEventRepoPort,
    CollaborationRuntimeError, CollaborationRuntimeService, ConfigureGroupRuntimeCommand,
    ConfigureGroupRuntimeOutcome, CreateOrReactivateCommand, DefinitionYamlSource,
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort, FrontendDeliveryTarget,
    GroupCollaborationDefinitionView, GroupCoreService, GroupRuntimeBindingRepoPort,
    HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome, HandleSessionHumanInputCommand,
    HandleSessionHumanInputOutcome, HumanInputReadyEvent, HumanRunAccessCommand, JudgeArtifact,
    JudgeEvaluatorPort, JudgeRequest, ListPendingHumanNodesCommand,
    MAX_COLLABORATION_DEFINITION_YAML_BYTES, MESSAGE_LOG_SCHEMA_VERSION, MSG_LOG_TARGET,
    MarkHumanNodeRunningCommand, MessageLogContent, MessageLogEventType, MessageLogMode,
    MessageLogStatus, NewSessionParams, PatchGroupCollaborationDefinitionCommand,
    PendingHumanNodeView, RerunStateMachineCommand, RerunStateMachineOutcome,
    RespondHumanNodeCommand, RespondHumanNodeOutcome, RunFallbackDelivery, ServiceError,
    SessionChannelOutboundPort, SessionHistoryResult, SessionKind,
    SessionManagementService, SessionStateMachinePermissionCommand,
    SessionStateMachinePermissionView, SessionStatus, SessionUseCaseError,
    StartSessionStateMachineRunCommand, StartStateMachineRunCommand, StartStateMachineRunOutcome,
    StateMachineDefinitionRepoPort, StateMachineGraphDefinitionView, StateMachineGraphEdgeView,
    StateMachineGraphNodeView, StateMachineJudgeOutputView, StateMachineNodeRunView,
    StateMachineNodeSubStatus, StateMachineResultPublishCommand, StateMachineResultPublisherPort,
    StateMachineRunAccessCommand, StateMachineRunGraphView, StateMachineRunRepoPort,
    StateMachineRunView, StateMachineTerminalEvent, StateMachineTerminalStatus,
    UpgradeGroupCollaborationDefinitionCommand, ValidateCollaborationDefinitionYamlCommand,
    message_log_json,
};
use bcs_service_api::{CreateStateMachineRerun, CreateStateMachineRerunOutcome, FailStateMachineNodeAttempt, StateMachineFailureAction};
use chrono::{SecondsFormat, TimeZone, Utc};
use serde_json::Value;
use tracing::{info, warn};
use uuid::Uuid;

const RUNTIME_CLEANUP_SESSION_LIMIT: u64 = i64::MAX as u64;

/// Runtime-instance budget for a caller orchestration association (audit P1
/// 2026-10-25): the runtime-only lineage ledger is bounded (1024 shared
/// slots), so an association may not outlive its workflow run
/// indefinitely — terminal release is the primary reclaim, this budget is
/// the age-out safety net when a terminal path misses (mirrors how the
/// non-caller active-run indexes age out at the run deadline). 24h.
const CALLER_INSTANCE_ASSOCIATION_BUDGET_MS: i64 = 24 * 60 * 60 * 1000;

use crate::definition::{
    CompiledStateMachine, project_definition_graph, reject_explicit_participant_roles,
    validate_definition,
};
use crate::validation::validate_authoring_definition_yaml_with_instrumentation;

const DEFAULT_JUDGE_TIMEOUT_MS: u64 = 90_000;
const MAX_HUMAN_RESPONSE_BYTES: usize = 64 * 1024;
const SESSION_STATE_MACHINE_POLICY_VERSION: &str = "session_state_machine_v1";

#[path = "runtime_judge.rs"]
mod judge_recovery;
use judge_recovery::JudgingProgress;
#[path = "runtime_recovery_gap.rs"]
mod runtime_recovery_gap;
#[path = "runtime_terminal_im.rs"]
mod terminal_im;

#[path = "runtime_publication.rs"]
mod publication;

#[path = "runtime_opening.rs"]
mod opening_recovery;

#[path = "runtime_dispatch.rs"]
mod dispatch_recovery;

#[path = "runtime_metadata.rs"]
mod metadata_projection;
#[path = "runtime_observability.rs"]
mod loop_observability;
use metadata_projection::{message_history_error, output_message_id, output_message_key, stored_output_message};

enum JudgeEvaluationResult {
    Outcome(String, Option<bcs_service_api::JudgeDecision>),
    Failed(String, Value),
}

#[path = "runtime_message_history.rs"]
mod message_history;

#[path = "runtime_history_query.rs"]
mod history_query;

pub struct CollaborationRuntime {
    history_persistence_enabled: bool,
    history_cutoff_timestamp: u64,
    loop_instrumentation: Option<Arc<dyn bcs_service_api::StateMachineLoopInstrumentationHook>>,
    fixed_loop_limits: bcs_config_api::FixedLoopLimits,
    loop_execution_enabled: bool,
    nested_loop_execution_enabled: bool,
    definitions: Arc<dyn StateMachineDefinitionRepoPort>,
    bindings: Arc<dyn GroupRuntimeBindingRepoPort>,
    runs: Arc<dyn StateMachineRunRepoPort>,
    events: Arc<dyn CollaborationEventRepoPort>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    bot_delivery: Arc<dyn BotDeliveryPort>,
    bot_registry: Option<Arc<dyn BotRegistryCoreService>>,
    bot_run_context: Option<Arc<dyn BotRunContextPort>>,
    provider_chat_run_timeout_ms: u64,
    frontend_delivery: Option<Arc<dyn FrontendDeliveryPort>>,
    message_repo: Option<Arc<dyn MessageRepoPort>>,
    session_channel_outbound: Option<Arc<dyn SessionChannelOutboundPort>>,
    result_publisher: Option<Arc<dyn StateMachineResultPublisherPort>>,
    event_record_factory: Option<Arc<dyn EventRecordFactoryPort>>,
    judge: Arc<dyn JudgeEvaluatorPort>,
    /// §15.83 S3 (spec §8.5 / review-V P1-1): when BOTH are present, a node
    /// whose assignee resolves Protected routes through the caller wire
    /// (reusing the node's own run/attempt); an Ordinary resolution keeps
    /// the historical direct delivery untouched.
    caller_node_dispatch: Option<Arc<dyn bcs_service_api::port::OrchestrationCallerDispatchPort>>,
    caller_policy_resolver: Option<Arc<dyn bcs_service_api::port::OrchestrationCallerPolicyResolver>>,
    /// §15.83 S2: the composition's instance-association registrar. A
    /// Human-triggered caller-aware run registers its delegation-only
    /// association here at submission; without a registrar the run is
    /// Bot-only caller-closed by construction.
    caller_instance_registrar: Option<Arc<dyn bcs_service_api::port::OrchestrationInstanceRegistrar>>,
    /// Owner→caller relay (spec L62 direction, design 2026-10-30): the
    /// submission-time redemption seam for BOT-submitted caller-aware runs
    /// whose `caller_association` the trusted adapter could not build. The
    /// redeemer owns the deployment allowlist (the submitting bot plus
    /// EVERY Execution-assignee edge) and the session's captured ingress;
    /// None refuses the whole submission fail-closed. Absent = every bot
    /// submission stays caller-closed exactly as today.
    caller_relay_redeemer: Option<Arc<dyn bcs_service_api::port::CallerRelayRedeemerPort>>,
}

struct ResolvedDefinition {
    definition: CollaborationDefinition,
    source: ResolvedDefinitionSource,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ResolvedDefinitionSource {
    Inline,
    Stored,
}impl CollaborationRuntime {
    pub fn with_history_persistence(mut self, enabled: bool) -> Self { self.history_persistence_enabled = enabled; self }

    pub fn with_history_cutoff_timestamp(mut self, timestamp: u64) -> Self {
        self.history_cutoff_timestamp = timestamp;
        self
    }

    pub fn new(
        definitions: Arc<dyn StateMachineDefinitionRepoPort>,
        bindings: Arc<dyn GroupRuntimeBindingRepoPort>,
        runs: Arc<dyn StateMachineRunRepoPort>,
        events: Arc<dyn CollaborationEventRepoPort>,
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionManagementService>,
        bot_delivery: Arc<dyn BotDeliveryPort>,
        judge: Arc<dyn JudgeEvaluatorPort>,
    ) -> Self {
        Self {
            history_persistence_enabled: false,
            history_cutoff_timestamp: 0,
            loop_instrumentation: None,
            fixed_loop_limits: bcs_config_api::FixedLoopLimits::default(),
            loop_execution_enabled: false,
            nested_loop_execution_enabled: false,
            definitions,
            bindings,
            runs,
            events,
            groups,
            sessions,
            bot_delivery,
            bot_registry: None,
            bot_run_context: None,
            provider_chat_run_timeout_ms: bcs_service_api::DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
            judge,
            frontend_delivery: None,
            message_repo: None,
            session_channel_outbound: None,
            result_publisher: None,
            event_record_factory: None,
            caller_node_dispatch: None,
            caller_policy_resolver: None,
            caller_instance_registrar: None,
            caller_relay_redeemer: None,
        }
    }

    /// §15.83 S3: attach the protected orchestration node dispatch seam.
    /// Both pieces must be present — a port without the policy resolver
    /// (or vice versa) keeps every node on the ordinary wire.
    /// §15.83 S2: attach the instance-association registrar (the
    /// composition's Human-triggered delegation-only registration seam).
    pub fn with_caller_instance_registrar(
        mut self,
        registrar: Arc<dyn bcs_service_api::port::OrchestrationInstanceRegistrar>,
    ) -> Self {
        self.caller_instance_registrar = Some(registrar);
        self
    }

    /// Owner→caller relay (design 2026-10-30): attach the submission-time
    /// redeemer. The full-shape caller assembly (registrar + resolver)
    /// must already be wired — a redeemer without the bridge it feeds is a
    /// misassembly and stays refused here so a relay capture can never
    /// register into a composition that could not consume it.
    pub fn with_caller_relay_redeemer(
        mut self,
        redeemer: Arc<dyn bcs_service_api::port::CallerRelayRedeemerPort>,
    ) -> Self {
        if self.caller_instance_registrar.is_some()
            && self.caller_policy_resolver.is_some()
        {
            self.caller_relay_redeemer = Some(redeemer);
        }
        self
    }

    /// §15.83 S4 (review-VI P1): the production composition's single
    /// orchestration mount — a control-plane-backed policy resolver ALWAYS
    /// (an ILL-configured composition is caught in the resolver: the
    /// transport gate on caller-enabled assignees keeps the closed world),
    /// and the protected dispatch port + instance registrar when the
    /// pipeline exists. One seam, three consistent lifetimes.
    pub fn with_caller_orchestration(
        mut self,
        control_plane: Option<Arc<dyn bcs_service_api::BotControlPlaneCoreService>>,
        pipeline: Option<
            Arc<dyn bcs_service_api::port::OrchestrationCallerDispatchPort>,
        >,
        registrar: Option<Arc<dyn bcs_service_api::port::OrchestrationInstanceRegistrar>>,
    ) -> Self {
        if let Some(plane) = control_plane {
            if let (Some(pipeline), Some(registrar)) = (pipeline, registrar) {
                self.caller_node_dispatch = Some(pipeline);
                self.caller_policy_resolver = Some(Arc::new(
                    ControlPlaneOrchestrationResolver { plane },
                ));
                self.caller_instance_registrar = Some(registrar);
            } else {
                // No protected assembly but a caller control plane present:
                // the resolver ALONE refuses caller-enabled assignees closed
                // at node dispatch — never a silent ordinary-wire
                // degradation.
                self.caller_policy_resolver = Some(Arc::new(
                    RefusingOrchestrationResolver { plane },
                ));
                self.caller_node_dispatch = None;
                self.caller_instance_registrar = None;
            }
        }
        self
    }

    pub fn with_caller_node_dispatch(
        mut self,
        port: Arc<dyn bcs_service_api::port::OrchestrationCallerDispatchPort>,
        resolver: Arc<dyn bcs_service_api::port::OrchestrationCallerPolicyResolver>,
    ) -> Self {
        self.caller_node_dispatch = Some(port);
        self.caller_policy_resolver = Some(resolver);
        self
    }

    pub fn with_bot_registry(mut self, bot_registry: Arc<dyn BotRegistryCoreService>) -> Self {
        self.bot_registry = Some(bot_registry);
        self
    }

    pub fn with_fixed_loop_limits(mut self, limits: bcs_config_api::FixedLoopLimits) -> Self {
        self.fixed_loop_limits = limits;
        self
    }

    pub fn with_loop_instrumentation(
        mut self, hook: Option<Arc<dyn bcs_service_api::StateMachineLoopInstrumentationHook>>,
    ) -> Self {
        self.loop_instrumentation = hook;
        self
    }

    /// Explicitly enable fixed Loop execution.
    pub fn with_loop_execution(self) -> Self {
        self.with_loop_execution_enabled(true)
    }

    /// Bootstrap injects the same capability for validation and every execution entrypoint.
    pub fn with_loop_execution_enabled(mut self, enabled: bool) -> Self {
        self.loop_execution_enabled = enabled;
        self
    }

    /// Controls admission of new nested Loop Runs; recovery never consults this switch.
    pub fn with_nested_loop_execution_enabled(mut self, enabled: bool) -> Self {
        self.nested_loop_execution_enabled = enabled;
        self
    }

    pub fn with_bot_run_context(mut self, bot_run_context: Arc<dyn BotRunContextPort>) -> Self {
        self.bot_run_context = Some(bot_run_context);
        self
    }

    pub fn with_provider_chat_run_timeout_ms(mut self, timeout_ms: u64) -> Self {
        self.provider_chat_run_timeout_ms = timeout_ms;
        self
    }

    pub fn with_frontend_delivery(
        mut self,
        frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    ) -> Self {
        self.frontend_delivery = Some(frontend_delivery);
        self
    }

    pub fn with_message_repo(mut self, message_repo: Arc<dyn MessageRepoPort>) -> Self {
        self.message_repo = Some(message_repo);
        self
    }

    pub fn with_session_channel_outbound(
        mut self,
        outbound: Arc<dyn SessionChannelOutboundPort>,
    ) -> Self {
        self.session_channel_outbound = Some(outbound);
        self
    }

    pub fn with_result_publisher(
        mut self,
        publisher: Arc<dyn StateMachineResultPublisherPort>,
    ) -> Self {
        self.result_publisher = Some(publisher);
        self
    }

    pub fn with_event_record_factory(
        mut self,
        event_record_factory: Arc<dyn EventRecordFactoryPort>,
    ) -> Self {
        self.event_record_factory = Some(event_record_factory);
        self
    }
}



// ---------------------------------------------------------------------------
// §15.83 S4 (review-VI P1): the control-plane-backed orchestration resolvers.
// ---------------------------------------------------------------------------

/// The full-shape composition: caller-enabled assignee → Protected with an
/// execution-bound proof; absent/disabled → Ordinary (the historical direct
/// wire). A control-plane READ failure resolves Ordinary-CLOSED too — never
/// a guess: provider 必须自行降级判定。
pub(super) struct ControlPlaneOrchestrationResolver {
    pub plane: Arc<dyn bcs_service_api::BotControlPlaneCoreService>,
}

#[async_trait::async_trait]
impl bcs_service_api::port::OrchestrationCallerPolicyResolver for ControlPlaneOrchestrationResolver {
    async fn resolve(
        &self,
        assignee_bot_id: &str,
        environment: &str,
    ) -> bcs_service_api::port::OrchestrationAssigneeResolution {
        match self.plane.get_caller_configuration(assignee_bot_id, environment).await {
            Ok(Some(config)) if config
                .snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.caller_identity.enabled) =>
            {
                bcs_service_api::port::OrchestrationAssigneeResolution::Protected(
                    bcs_service_api::port::OrchestrationAssigneePolicy::Execution,
                )
            }
            Ok(Some(_)) => bcs_service_api::port::OrchestrationAssigneeResolution::Ordinary,
            // An UNREADABLE configuration on a caller-aware workflow is a
            // closed world verdict — the instance's association namespace is
            // not guessed at node dispatch granularity.
            Ok(None) | Err(_) => bcs_service_api::port::OrchestrationAssigneeResolution::UnknownClosed,
        }
    }
}

/// The refuse-closed fallback composition: a caller control plane exists but
/// the protected pipeline does not — every assignee with caller ENABLED
/// refuses closed; the ordinary wire never runs caller-enabled nodes.
pub(super) struct RefusingOrchestrationResolver {
    pub plane: Arc<dyn bcs_service_api::BotControlPlaneCoreService>,
}

#[async_trait::async_trait]
impl bcs_service_api::port::OrchestrationCallerPolicyResolver for RefusingOrchestrationResolver {
    async fn resolve(
        &self,
        assignee_bot_id: &str,
        environment: &str,
    ) -> bcs_service_api::port::OrchestrationAssigneeResolution {
        match self.plane.get_caller_configuration(assignee_bot_id, environment).await {
            Ok(Some(config)) if config
                .snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.caller_identity.enabled) =>
            {
                bcs_service_api::port::OrchestrationAssigneeResolution::UnknownClosed
            }
            Ok(Some(_)) => bcs_service_api::port::OrchestrationAssigneeResolution::Ordinary,
            Ok(None) | Err(_) => bcs_service_api::port::OrchestrationAssigneeResolution::UnknownClosed,
        }
    }
}
const RUNTIME_LOG_TARGET: &str = module_path!();

#[cfg(test)]
use opening::default_state_machine_opening_message;
#[cfg(test)]
use start::build_node_runs;

mod access;

mod authoring;
use authoring::{
    definition_yaml_for_record, parse_authoring_definition_yaml, validate_v2_definition_input, reject_authoring_yaml_identity, normalized_definition_json,
};

mod bindings;
use bindings::{
    resolve_participant_bindings, validate_runtime_participant_bindings, session_state_machine_permission_view,
};

mod caller;

mod completion;

mod definition;

mod events;
use events::{
    event_timestamp, content_value, chat_event_state_slug, bot_display_name, state_machine_message_metadata, state_machine_human_input_prompt_metadata, state_machine_panel_metadata,
};

mod human_input;
use human_input::{
    compiled_has_human_input, human_input_assignees, human_input_prompt_actor_ids,
};

mod logging;
use logging::{
    log_state_machine_node_dispatch, log_state_machine_delivery_result, log_state_machine_bot_event, log_state_machine_node_result, log_state_machine_transition, log_state_machine_run_complete, log_state_machine_run_failed, log_state_machine_timeout, elapsed_ms,
};

mod node_execution;
use node_execution::{
    predecessor_node_ids, group_context_input,
};

mod opening;
use opening::{
    render_state_machine_opening_message,
};

mod progression;

mod recovery;

mod rerun;

mod service;

mod start;

mod terminal_event;
use terminal_event::{
    extract_text,
};

#[cfg(test)]
mod tests;

mod views;
use views::{
    run_graph_view, node_uses_judge,
};
