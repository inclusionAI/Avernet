use std::{
    collections::{BTreeMap, HashSet},
    sync::Arc,
    time::{Duration, Instant},
};

use async_trait::async_trait;
use bcs_domain::{
    CollaborationDefinition, CollaborationDefinitionRef, CollaborationRuntimeDefinition, Group,
    GroupMessage, GroupMessageType, GroupRuntimeBinding, GroupStrategy, MessageRole, Participant,
    ParticipantMode, ParticipantRole, ResolvedParticipant, ResolvedParticipantBinding,
    RuntimeParticipantBinding, StateMachineAssignee, StateMachineDeliveryCorrelation,
    StateMachineNodeKind, StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun,
    StateMachineRunStatus,
};
use bcs_protocol::{
    BCS_PROTOCOL_VERSION, BcsFrame, EventFrame, GroupContextInput, GroupContextParticipant,
    build_chat_send_frame,
};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{
    AuthenticatedHumanCaller, BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort,
    BotDeliveryTarget, BotRegistryCoreService, CancelStateMachineRunCommand, ChatEventState,
    CollaborationDefinitionRecord, CollaborationDefinitionValidationOutcome,
    CollaborationEventRepoPort, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome, CreateOrReactivateCommand,
    DefinitionYamlSource, FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort,
    FrontendDeliveryTarget, GroupCollaborationDefinitionView, GroupCoreService,
    GroupRuntimeBindingRepoPort, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
    HandleSessionHumanInputCommand, HandleSessionHumanInputOutcome, HumanInputReadyEvent,
    HumanRunAccessCommand, JudgeArtifact, JudgeEvaluatorPort, JudgeRequest,
    ListPendingHumanNodesCommand, MarkHumanNodeRunningCommand, MessageLogContent,
    MessageLogEventType, MessageLogMode, MessageLogStatus, NewSessionParams,
    PatchGroupCollaborationDefinitionCommand, PendingHumanNodeView, RespondHumanNodeCommand,
    RespondHumanNodeOutcome, RunFallbackDelivery, ServiceError, SessionChannelDeliveryOutcome,
    SessionChannelOutboundPort, SessionHistoryResult, SessionKind, SessionManagementService,
    StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineDefinitionRepoPort,
    StateMachineGraphDefinitionView, StateMachineGraphEdgeView, StateMachineGraphNodeView,
    StateMachineJudgeOutputView, StateMachineNodeRunView, StateMachineNodeSubStatus,
    StateMachineRunAccessCommand, StateMachineRunGraphView, StateMachineRunRepoPort,
    StateMachineRunView, StateMachineTerminalEvent, StateMachineTerminalStatus,
    UpgradeGroupCollaborationDefinitionCommand, ValidateCollaborationDefinitionYamlCommand,
    MAX_COLLABORATION_DEFINITION_YAML_BYTES, MESSAGE_LOG_SCHEMA_VERSION, MSG_LOG_TARGET,
    message_log_json,
};
use serde_json::Value;
use tracing::{info, warn};
use uuid::Uuid;

use crate::definition::{
    CompiledStateMachine, reject_explicit_participant_roles, validate_definition,
};
use crate::validation::validate_authoring_definition_yaml;

const BCS_STATE_MACHINE_BOT_ID: &str = "bcs_state_machine";
const BCS_STATE_MACHINE_BOT_NAME: &str = "BCS State Machine";
const DEFAULT_JUDGE_TIMEOUT_MS: u64 = 90_000;
const MAX_HUMAN_RESPONSE_BYTES: usize = 64 * 1024;

enum JudgeEvaluationResult {
    Outcome(String),
    Failed(String),
}

pub struct CollaborationRuntime {
    definitions: Arc<dyn StateMachineDefinitionRepoPort>,
    bindings: Arc<dyn GroupRuntimeBindingRepoPort>,
    runs: Arc<dyn StateMachineRunRepoPort>,
    events: Arc<dyn CollaborationEventRepoPort>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    bot_delivery: Arc<dyn BotDeliveryPort>,
    bot_registry: Option<Arc<dyn BotRegistryCoreService>>,
    frontend_delivery: Option<Arc<dyn FrontendDeliveryPort>>,
    session_channel_outbound: Option<Arc<dyn SessionChannelOutboundPort>>,
    judge: Arc<dyn JudgeEvaluatorPort>,
    callback_url_guard: OutboundUrlGuard,
}

struct ResolvedDefinition {
    definition: CollaborationDefinition,
    source: ResolvedDefinitionSource,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ResolvedDefinitionSource {
    Inline,
    Stored,
}

impl CollaborationRuntime {
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
            definitions,
            bindings,
            runs,
            events,
            groups,
            sessions,
            bot_delivery,
            bot_registry: None,
            judge,
            frontend_delivery: None,
            session_channel_outbound: None,
            callback_url_guard: OutboundUrlGuard::strict(),
        }
    }

    pub fn with_bot_registry(mut self, bot_registry: Arc<dyn BotRegistryCoreService>) -> Self {
        self.bot_registry = Some(bot_registry);
        self
    }

    pub fn with_frontend_delivery(
        mut self,
        frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    ) -> Self {
        self.frontend_delivery = Some(frontend_delivery);
        self
    }

    pub fn with_session_channel_outbound(
        mut self,
        outbound: Arc<dyn SessionChannelOutboundPort>,
    ) -> Self {
        self.session_channel_outbound = Some(outbound);
        self
    }

    pub fn with_callback_url_guard(mut self, callback_url_guard: OutboundUrlGuard) -> Self {
        self.callback_url_guard = callback_url_guard;
        self
    }

    async fn resolve_definition(
        &self,
        cmd: &StartStateMachineRunCommand,
        group_binding: Option<&GroupRuntimeBinding>,
    ) -> Result<ResolvedDefinition, CollaborationRuntimeError> {
        if let Some(yaml) = &cmd.definition_yaml {
            let definition: CollaborationDefinition = serde_yaml::from_str(yaml)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
            reject_explicit_participant_roles(&definition)?;
            return Ok(ResolvedDefinition {
                definition,
                source: ResolvedDefinitionSource::Inline,
            });
        }
        if let Some(value) = &cmd.definition {
            let definition: CollaborationDefinition = serde_json::from_value(value.clone())
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
            reject_explicit_participant_roles(&definition)?;
            return Ok(ResolvedDefinition {
                definition,
                source: ResolvedDefinitionSource::Inline,
            });
        }
        if let Some(definition_ref) = &cmd.definition_ref {
            let definition = self
                .definitions
                .get(&definition_ref.id, definition_ref.version)
                .await?
                .ok_or_else(|| {
                    CollaborationRuntimeError::DefinitionNotFound(
                        definition_ref.id.clone(),
                        definition_ref.version,
                    )
                })?;
            return Ok(ResolvedDefinition {
                definition,
                source: ResolvedDefinitionSource::Stored,
            });
        }
        if let Some(binding) = group_binding {
            if let Some(definition_ref) = &binding.default_definition {
                let definition = self
                    .definitions
                    .get(&definition_ref.id, definition_ref.version)
                    .await?
                    .ok_or_else(|| {
                        CollaborationRuntimeError::DefinitionNotFound(
                            definition_ref.id.clone(),
                            definition_ref.version,
                        )
                    })?;
                return Ok(ResolvedDefinition {
                    definition,
                    source: ResolvedDefinitionSource::Stored,
                });
            }
        }
        Err(CollaborationRuntimeError::InvalidRequest(
            "group has no default collaboration definition binding; create or bind the group definition before starting a state-machine run".to_string(),
        ))
    }

    async fn run_view(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(run_id).await? else {
            return Ok(None);
        };
        let nodes = self.runs.list_node_runs(run_id).await?;
        let judge_outputs = self.judge_outputs(run_id).await?;
        Ok(Some(StateMachineRunView {
            run,
            nodes,
            judge_outputs,
        }))
    }

    async fn judge_outputs(
        &self,
        run_id: &str,
    ) -> Result<Vec<StateMachineJudgeOutputView>, CollaborationRuntimeError> {
        let events = self
            .events
            .list_events_by_run_and_type(run_id, "state_machine.judge.completed")
            .await?;
        events
            .into_iter()
            .map(|event| {
                let decision = serde_json::from_value(event.payload).map_err(|error| {
                    CollaborationRuntimeError::InvalidRequest(format!(
                        "invalid state_machine.judge.completed payload: {error}"
                    ))
                })?;
                Ok(StateMachineJudgeOutputView {
                    node_id: event.node_id.unwrap_or_default(),
                    attempt: event.attempt.unwrap_or_default(),
                    created_at: event.created_at,
                    decision,
                })
            })
            .collect()
    }

    async fn judge_outputs_for_node(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> Result<Vec<StateMachineJudgeOutputView>, CollaborationRuntimeError> {
        let events = self
            .events
            .list_events_by_run_node_and_type(run_id, node_id, "state_machine.judge.completed")
            .await?;
        events
            .into_iter()
            .map(|event| {
                let decision = serde_json::from_value(event.payload).map_err(|error| {
                    CollaborationRuntimeError::InvalidRequest(format!(
                        "invalid state_machine.judge.completed payload: {error}"
                    ))
                })?;
                Ok(StateMachineJudgeOutputView {
                    node_id: event.node_id.unwrap_or_else(|| node_id.to_string()),
                    attempt: event.attempt.unwrap_or_default(),
                    created_at: event.created_at,
                    decision,
                })
            })
            .collect()
    }

    async fn authorize_human_for_run(
        &self,
        run: &StateMachineRun,
        actor_id: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let belongs = self
            .sessions
            .belongs_to_group(&run.session_id, &run.group_id)
            .await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?;
        if !belongs {
            return Err(CollaborationRuntimeError::Forbidden(
                "run session does not belong to the run group".to_string(),
            ));
        }
        let session = self
            .sessions
            .get(&run.session_id)
            .await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
            .ok_or_else(|| {
                CollaborationRuntimeError::Forbidden("run session is unavailable".to_string())
            })?;
        let authorized = session.participants.iter().any(|participant| {
            participant.bot_uuid == actor_id
                && participant.is_human()
                && participant.effective_mode() == ParticipantMode::Present
        });
        if !authorized {
            return Err(CollaborationRuntimeError::Forbidden(
                "caller is not a present Human participant in the run session".to_string(),
            ));
        }
        Ok(())
    }

    async fn authorize_run_access(
        &self,
        run: &StateMachineRun,
        authenticated_human: Option<&AuthenticatedHumanCaller>,
    ) -> Result<bool, CollaborationRuntimeError> {
        let compiled = validate_definition(self.load_run_definition(run).await?)?;
        // COSEC: unauthenticated legacy access is retained only for Bot-only
        // runs; any run containing HumanInput remains identity-protected.
        if !compiled_has_human_input(&compiled) {
            return Ok(false);
        }
        let human = authenticated_human.ok_or(CollaborationRuntimeError::Unauthenticated)?;
        self.authorize_human_for_run(run, &human.actor_id).await?;
        Ok(true)
    }

    async fn pending_human_node_view(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        node_run: &StateMachineNodeRun,
    ) -> Result<PendingHumanNodeView, CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let node = state_machine.nodes.get(&node_run.node_id).ok_or_else(|| {
            CollaborationRuntimeError::NodeNotFound {
                run_id: run.run_id.clone(),
                node_id: node_run.node_id.clone(),
            }
        })?;
        let mut upstream_artifacts = self
            .judge_upstream_outputs(compiled, &run.run_id, &node_run.node_id)
            .await?;
        upstream_artifacts.sort_by(|left, right| left.node_id.cmp(&right.node_id));
        Ok(PendingHumanNodeView {
            node_id: node_run.node_id.clone(),
            display_name: node.display_name.clone(),
            instruction: node.instruction.clone().unwrap_or_default(),
            response_ref: format!("{}/{}", run.run_id, node_run.node_id),
            judge_outcomes: node
                .judge
                .as_ref()
                .map(|judge| judge.outcomes.clone())
                .unwrap_or_default(),
            timeout_deadline_ms: node_run.timeout_deadline_ms,
            upstream_artifacts,
        })
    }

    async fn activate_human_input(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node_id: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let node = state_machine.nodes.get(node_id).ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition(format!("node not found: {node_id}"))
        })?;
        let node_run = self
            .runs
            .get_node_run(&run.run_id, node_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::NodeNotFound {
                run_id: run.run_id.clone(),
                node_id: node_id.to_string(),
            })?;
        let now = bcs_protocol::now_ms();
        let timeout_ms = node.node_timeout_ms.ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition(format!(
                "human_input node {node_id} requires node_timeout_ms"
            ))
        })?;
        let deadline = now.saturating_add(timeout_ms);
        let marked = self
            .runs
            .mark_human_node_running_if_run_active(MarkHumanNodeRunningCommand {
                run_id: run.run_id.clone(),
                node_id: node_id.to_string(),
                attempt: node_run.attempt,
                started_at_ms: now,
                timeout_deadline_ms: deadline,
            })
            .await?;
        if !marked {
            return Ok(());
        }
        self.publish_state_machine_panel_event(group, run, None)
            .await;
        let Some(notification) = node.notification.as_ref() else {
            return Ok(());
        };
        let assignee_actor_id = match &node.assignee {
            Some(StateMachineAssignee::RuntimeActor { actor }) => actor.clone(),
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(format!(
                    "human_input node {node_id} with notification requires runtime_actor assignee"
                )));
            }
        };
        let human_input_channel = state_machine.human_input_channel.as_ref().ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition(format!(
                "human_input node {node_id} with notification requires state_machine.human_input_channel"
            ))
        })?;
        let Some(outbound) = self.session_channel_outbound.as_ref() else {
            return Ok(());
        };
        let running_node = self
            .runs
            .get_node_run(&run.run_id, node_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::NodeNotFound {
                run_id: run.run_id.clone(),
                node_id: node_id.to_string(),
            })?;
        let pending = self
            .pending_human_node_view(compiled, run, &running_node)
            .await?;
        let event = HumanInputReadyEvent {
            event_id: format!("human-ready:{}:{}", run.run_id, node_id),
            group_id: run.group_id.clone(),
            session_id: run.session_id.clone(),
            run_id: run.run_id.clone(),
            node_id: node_id.to_string(),
            display_name: pending.display_name,
            instruction: pending.instruction,
            assignee_actor_id,
            channel_type: human_input_channel.channel_type.clone(),
            notification_mode: notification.mode,
            fixed_group_conversation_id: human_input_channel
                .fixed_group
                .as_ref()
                .map(|group| group.conversation_id.clone()),
            response_ref: pending.response_ref,
            upstream_artifacts: pending.upstream_artifacts,
            judge_outcomes: pending.judge_outcomes,
            timeout_deadline_ms: pending.timeout_deadline_ms,
        };
        match outbound.publish_human_input_ready(event).await {
            Ok(
                SessionChannelDeliveryOutcome::Delivered
                | SessionChannelDeliveryOutcome::NotApplicable,
            ) => {}
            Err(error) => {
                warn!(
                    run_id = %run.run_id,
                    node_id = %node_id,
                    error = %error,
                    "state_machine: human-ready channel delivery failed"
                );
            }
        }
        Ok(())
    }

    async fn dispatch_node(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node_id: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let node_definition = state_machine.nodes.get(node_id).ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition(format!("node not found: {node_id}"))
        })?;
        match node_definition.kind {
            StateMachineNodeKind::HumanInput => {
                return self
                    .activate_human_input(compiled, group, run, node_id)
                    .await;
            }
            StateMachineNodeKind::BotTask => {}
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(format!(
                    "node {node_id} kind is not supported"
                )));
            }
        }
        let node_run = self
            .runs
            .get_node_run(&run.run_id, node_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))?;
        let attempt = node_run.attempt;
        let assignee_bot_id = node_run
            .assignee_bot_id
            .clone()
            .filter(|bot_id| !bot_id.trim().is_empty())
            .ok_or_else(|| {
                CollaborationRuntimeError::InvalidDefinition(format!(
                    "bot task node {node_id} has no assignee_bot_id"
                ))
            })?;
        let delivery_request_id = format!("smnode-{}-{}-{}", run.run_id, node_id, attempt);
        let marked = self
            .runs
            .mark_node_running_if_run_active(
                &run.run_id,
                node_id,
                attempt,
                delivery_request_id.clone(),
                bcs_protocol::now_ms(),
            )
            .await?;
        if !marked {
            return Ok(());
        }
        self.runs
            .upsert_delivery_correlation(StateMachineDeliveryCorrelation {
                state_machine_run_id: run.run_id.clone(),
                node_id: node_id.to_string(),
                attempt,
                assignee_bot_id: assignee_bot_id.clone(),
                delivery_request_id: delivery_request_id.clone(),
                bot_delivery_run_id: None,
            })
            .await?;

        let prompt = self.build_node_prompt(compiled, run, node_id).await?;
        let group_context = group_context_input(group, &run.session_id);
        log_state_machine_node_dispatch(
            group,
            run,
            node_id,
            attempt,
            &assignee_bot_id,
            &delivery_request_id,
            &prompt,
        );
        info!(
            run_id = %run.run_id,
            group_id = %group.id,
            session_id = %run.session_id,
            node_id = %node_id,
            attempt = attempt,
            assignee_bot_id = %assignee_bot_id,
            delivery_request_id = %delivery_request_id,
            "state_machine: node dispatch started"
        );
        let frame = build_chat_send_frame(
            &delivery_request_id,
            &group.id,
            &group_context,
            &prompt,
            BCS_STATE_MACHINE_BOT_ID,
            BCS_STATE_MACHINE_BOT_NAME,
            &[],
            &assignee_bot_id,
            &None,
            &None,
            false,
            BCS_PROTOCOL_VERSION,
            None,
            Some("state_machine".to_string()),
            Some(&run.session_id),
        );
        let target = if let Some(registry) = self.bot_registry.as_ref() {
            registry.resolve_delivery_target(&assignee_bot_id).await?
        } else {
            BotDeliveryTarget::WebSocket {
                bot_id: assignee_bot_id.clone(),
            }
        };
        let delivery_result = match self
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target,
                run_id: delivery_request_id.clone(),
                frame,
                delivery_kind: BotDeliveryKind::TaskDispatch,
                provider_transport: Default::default(),
            })
            .await
        {
            Ok(result) => result,
            Err(error) => {
                let error_text = error.to_string();
                log_state_machine_delivery_result(
                    group,
                    run,
                    node_id,
                    attempt,
                    &assignee_bot_id,
                    &delivery_request_id,
                    None,
                    false,
                    Some(error_text.as_str()),
                    Some("deliver"),
                );
                warn!(
                    run_id = %run.run_id,
                    group_id = %group.id,
                    session_id = %run.session_id,
                    node_id = %node_id,
                    attempt = attempt,
                    assignee_bot_id = %assignee_bot_id,
                    delivery_request_id = %delivery_request_id,
                    error = %error,
                    "state_machine: node dispatch failed"
                );
                let message = format!(
                    "state-machine node delivery failed for bot '{}': {}",
                    assignee_bot_id, error
                );
                self.fail_dispatched_node(compiled, group, run, node_id, attempt, message)
                    .await?;
                return Err(error.into());
            }
        };
        log_state_machine_delivery_result(
            group,
            run,
            node_id,
            attempt,
            &assignee_bot_id,
            &delivery_request_id,
            Some(&delivery_result.target_bot_id),
            delivery_result.delivered,
            delivery_result
                .error
                .as_ref()
                .map(ToString::to_string)
                .as_deref(),
            if delivery_result.delivered {
                None
            } else {
                Some("deliver")
            },
        );
        info!(
            run_id = %run.run_id,
            group_id = %group.id,
            session_id = %run.session_id,
            node_id = %node_id,
            attempt = attempt,
            assignee_bot_id = %assignee_bot_id,
            delivery_request_id = %delivery_request_id,
            target_bot_id = %delivery_result.target_bot_id,
            delivered = delivery_result.delivered,
            error = ?delivery_result.error,
            "state_machine: node dispatch completed"
        );
        if !delivery_result.delivered {
            let detail = delivery_result
                .error
                .as_ref()
                .map(|error| error.to_string())
                .unwrap_or_else(|| "delivery target did not accept the request".to_string());
            let message = format!(
                "state-machine node delivery failed for bot '{}': {}",
                assignee_bot_id, detail
            );
            warn!(
                run_id = %run.run_id,
                group_id = %group.id,
                session_id = %run.session_id,
                node_id = %node_id,
                attempt = attempt,
                assignee_bot_id = %assignee_bot_id,
                delivery_request_id = %delivery_request_id,
                target_bot_id = %delivery_result.target_bot_id,
                error = %detail,
                "state_machine: node dispatch failed"
            );
            self.fail_dispatched_node(compiled, group, run, node_id, attempt, message.clone())
                .await?;
            return Err(CollaborationRuntimeError::InvalidRequest(message));
        }
        Ok(())
    }

    async fn fail_dispatched_node(
        &self,
        _compiled: &CompiledStateMachine,
        _group: &Group,
        run: &StateMachineRun,
        node_id: &str,
        attempt: i32,
        error: String,
    ) -> Result<(), CollaborationRuntimeError> {
        let now = bcs_protocol::now_ms();
        self.runs
            .fail_node_attempt(&run.run_id, node_id, attempt, error.clone(), now)
            .await?;
        self.fail_run(run, error).await?;
        Ok(())
    }

    async fn publish_state_machine_panel_event(
        &self,
        group: &Group,
        run: &StateMachineRun,
        session_title: Option<&str>,
    ) {
        let Some(frontend_delivery) = self.frontend_delivery.as_ref() else {
            return;
        };
        let content = format_state_machine_panel_message(&run.run_id, session_title);
        let metadata = state_machine_panel_metadata(run);
        let payload = serde_json::json!({
            "run_id": run.run_id.clone(),
            "bcs_group_id": group.id.clone(),
            "bcs_session_id": run.session_id.clone(),
            "state": "final",
            "role": "assistant",
            "sender": BCS_STATE_MACHINE_BOT_ID,
            "content": content.clone(),
            "message_type": "bot",
            "bot_name": BCS_STATE_MACHINE_BOT_NAME,
            "metadata": metadata,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": content}],
                "timestamp": run.created_at,
            },
        });
        let frame = serde_json::json!({
            "type": "event",
            "event": "chat",
            "payload": payload.clone(),
            "group_id": group.id.clone(),
            "bot_uuid": BCS_STATE_MACHINE_BOT_ID,
        });
        let fallback = BcsFrame::Event(EventFrame::new("chat", Some(payload), None));
        let publish_result = tokio::time::timeout(
            Duration::from_millis(500),
            frontend_delivery.publish(FrontendDeliveryCommand {
                target: FrontendDeliveryTarget::Session {
                    session_id: run.session_id.clone(),
                },
                event_json: frame.to_string(),
                delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: Some(RunFallbackDelivery {
                    run_id: run.run_id.clone(),
                    session_id: run.session_id.clone(),
                    event_json: serde_json::to_string(&fallback)
                        .unwrap_or_else(|_| frame.to_string()),
                }),
                exclude_conn_id: None,
            }),
        )
        .await;
        match publish_result {
            Ok(Ok(_)) => {}
            Ok(Err(error)) => {
                warn!(
                    run_id = %run.run_id,
                    session_id = %run.session_id,
                    error = %error,
                    "state_machine: failed to publish panel event to frontend"
                );
            }
            Err(_) => {
                warn!(
                    run_id = %run.run_id,
                    session_id = %run.session_id,
                    "state_machine: timed out publishing panel event to frontend"
                );
            }
        }
    }

    async fn publish_state_machine_bot_event(
        &self,
        run: &StateMachineRun,
        bot_id: &str,
        delivery_run_id: &str,
        event_type: &str,
        event_payload: &Value,
        state: &ChatEventState,
    ) {
        let Some(frontend_delivery) = self.frontend_delivery.as_ref() else {
            return;
        };
        let frontend_event = workbench_event_name(event_type, state);
        let payload = normalize_state_machine_event_payload(
            event_payload,
            &run.group_id,
            &run.session_id,
            delivery_run_id,
            state,
        );
        let frame = serde_json::json!({
            "type": "event",
            "event": frontend_event,
            "payload": payload.clone(),
            "group_id": run.group_id.clone(),
            "bot_uuid": bot_id,
        });
        let fallback =
            BcsFrame::Event(EventFrame::new(event_type.to_string(), Some(payload), None));
        if let Err(error) = frontend_delivery
            .publish(FrontendDeliveryCommand {
                target: FrontendDeliveryTarget::Session {
                    session_id: run.session_id.clone(),
                },
                event_json: frame.to_string(),
                delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: Some(RunFallbackDelivery {
                    run_id: delivery_run_id.to_string(),
                    session_id: run.session_id.clone(),
                    event_json: serde_json::to_string(&fallback)
                        .unwrap_or_else(|_| frame.to_string()),
                }),
                exclude_conn_id: None,
            })
            .await
        {
            warn!(
                run_id = %run.run_id,
                bot_delivery_run_id = %delivery_run_id,
                session_id = %run.session_id,
                bot_id = %bot_id,
                error = %error,
                "state_machine: failed to publish bot event to frontend"
            );
        }
    }

    async fn build_node_prompt(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        node_id: &str,
    ) -> Result<String, CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let node = state_machine.nodes.get(node_id).ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition(format!("node not found: {node_id}"))
        })?;
        let upstream_ids = compiled
            .upstreams
            .get(node_id)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let mut upstream_text = String::new();
        for upstream_id in upstream_ids {
            if let Some(upstream_run) = self.runs.get_node_run(&run.run_id, upstream_id).await? {
                if let Some(text) = upstream_run.artifact_text {
                    upstream_text.push_str(&format!("\n[{upstream_id}]\n{text}\n"));
                }
            }
        }
        let input =
            serde_json::to_string_pretty(&run.input).unwrap_or_else(|_| run.input.to_string());
        Ok(format!(
            "[State Machine Task]\nnode_id: {node_id}\ndisplay_name: {}\n\n[Input]\n{input}\n\n[Upstream Outputs]{}\n\n[Instruction]\n{}",
            node.display_name,
            if upstream_text.is_empty() {
                "\n(none)\n".to_string()
            } else {
                upstream_text
            },
            node.instruction.as_deref().unwrap_or("")
        ))
    }

    async fn session_title(&self, session_id: &str) -> Option<String> {
        match self.sessions.get(session_id).await {
            Ok(Some(session)) => session.session_title,
            Ok(None) => None,
            Err(error) => {
                warn!(
                    session_id = %session_id,
                    error = %error,
                    "state_machine: failed to load session title"
                );
                None
            }
        }
    }

    async fn group_collaboration_definition_view(
        &self,
        group_id: &str,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        self.groups.get(group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {group_id}"))
        })?;
        let binding = self.bindings.get(group_id).await?;
        let participant_bindings = binding
            .as_ref()
            .map(|binding| binding.participant_bindings.clone())
            .unwrap_or_default();
        let default_definition = binding
            .as_ref()
            .and_then(|binding| binding.default_definition.clone());
        let Some(definition_ref) = default_definition.clone() else {
            return Ok(GroupCollaborationDefinitionView {
                group_id: group_id.to_string(),
                default_definition: None,
                definition: None,
                definition_yaml: None,
                yaml_source: DefinitionYamlSource::NoDefinition,
                participant_bindings,
            });
        };
        let record = self
            .definitions
            .get_record(&definition_ref.id, definition_ref.version)
            .await?
            .ok_or_else(|| {
                CollaborationRuntimeError::DefinitionNotFound(
                    definition_ref.id.clone(),
                    definition_ref.version,
                )
            })?;
        let (definition_yaml, yaml_source) = definition_yaml_for_record(&record);
        Ok(GroupCollaborationDefinitionView {
            group_id: group_id.to_string(),
            default_definition,
            definition: Some(record.definition),
            definition_yaml,
            yaml_source,
            participant_bindings,
        })
    }

    async fn load_run_definition(
        &self,
        run: &StateMachineRun,
    ) -> Result<CollaborationDefinition, CollaborationRuntimeError> {
        match self.definitions.get_run_snapshot(&run.run_id).await? {
            Some(definition) => Ok(definition),
            None => self
                .definitions
                .get(&run.definition_id, run.definition_version)
                .await?
                .ok_or_else(|| {
                    CollaborationRuntimeError::DefinitionNotFound(
                        run.definition_id.clone(),
                        run.definition_version,
                    )
                }),
        }
    }

    async fn state_machine_panel_message(&self, run: &StateMachineRun) -> GroupMessage {
        let session_title = self.session_title(&run.session_id).await;
        GroupMessage {
            id: format!("{}:000-panel", run.run_id),
            timestamp: run.created_at,
            sender: BCS_STATE_MACHINE_BOT_ID.to_string(),
            content: format_state_machine_panel_message(&run.run_id, session_title.as_deref()),
            message_type: GroupMessageType::Bot,
            bot_name: Some(BCS_STATE_MACHINE_BOT_NAME.to_string()),
            role: MessageRole::Assistant,
            history_meta: None,
            metadata: Some(state_machine_panel_metadata(run)),
            run_id: String::new(),
        }
    }

    async fn state_machine_messages_from_snapshot(
        &self,
        group: &Group,
        run: &StateMachineRun,
    ) -> Result<Vec<GroupMessage>, CollaborationRuntimeError> {
        let nodes = self.runs.list_node_runs(&run.run_id).await?;
        let session = match self.sessions.get(&run.session_id).await {
            Ok(session) => session,
            Err(error) => {
                warn!(
                    session_id = %run.session_id,
                    error = %error,
                    "state_machine: failed to load session participant names"
                );
                None
            }
        };
        let mut messages = Vec::new();
        messages.push(self.state_machine_panel_message(run).await);
        for node in nodes {
            if let Some(artifact_text) = node.artifact_text.as_ref() {
                let is_human = node.responded_by.is_some();
                let Some(sender) = node
                    .responded_by
                    .clone()
                    .or_else(|| node.assignee_bot_id.clone())
                else {
                    continue;
                };
                messages.push(GroupMessage {
                    id: format!("{}:{}:{}:1-output", run.run_id, node.node_id, node.attempt),
                    timestamp: node.completed_at.unwrap_or(run.updated_at),
                    sender: sender.clone(),
                    content: artifact_text.clone(),
                    // Keep the existing group-history wire shape: user messages
                    // are identified by role, while message_type remains `bot`.
                    // This lets the current Workbench render HumanInput responses
                    // without requiring a frontend protocol change.
                    message_type: GroupMessageType::Bot,
                    bot_name: if is_human {
                        session
                            .as_ref()
                            .and_then(|session| {
                                session
                                    .participants
                                    .iter()
                                    .find(|participant| participant.bot_uuid == sender)
                            })
                            .and_then(|participant| participant.bot_name.clone())
                    } else {
                        bot_display_name(group, &sender)
                    },
                    role: if is_human {
                        MessageRole::User
                    } else {
                        MessageRole::Assistant
                    },
                    history_meta: None,
                    metadata: Some(state_machine_message_metadata(run, &node, "output")),
                    run_id: String::new(),
                });
            }
        }
        Ok(messages)
    }

    async fn dispatch_ready_targets(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        completed_node_id: &str,
        outcome: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => return Ok(()),
        };
        let targets = state_machine
            .nodes
            .get(completed_node_id)
            .and_then(|node| node.transitions.get(outcome))
            .map(|transition| transition.targets.clone())
            .unwrap_or_default();
        log_state_machine_transition(
            run,
            completed_node_id,
            outcome,
            targets.as_slice(),
            MessageLogStatus::Routed,
        );
        for target in targets {
            if self
                .all_upstreams_completed(compiled, &run.run_id, &target)
                .await?
            {
                let Some(target_run) = self.runs.get_node_run(&run.run_id, &target).await? else {
                    continue;
                };
                if matches!(
                    target_run.status,
                    StateMachineNodeStatus::Pending
                        | StateMachineNodeStatus::Ready
                        | StateMachineNodeStatus::RetryScheduled
                ) {
                    self.dispatch_node(compiled, group, run, &target).await?;
                }
            }
        }
        Ok(())
    }

    async fn skip_unselected_targets(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        completed_node_id: &str,
        selected_outcome: &str,
        skipped_at: u64,
    ) -> Result<(), CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => return Ok(()),
        };
        let Some(node) = state_machine.nodes.get(completed_node_id) else {
            return Ok(());
        };
        let selected_targets = node
            .transitions
            .get(selected_outcome)
            .map(|transition| transition.targets.as_slice())
            .unwrap_or(&[]);
        let mut protected_targets = HashSet::new();
        collect_reachable_targets(state_machine, selected_targets, &mut protected_targets);
        for (outcome, transition) in &node.transitions {
            if outcome == selected_outcome {
                continue;
            }
            self.skip_branch_targets(
                state_machine,
                run,
                transition.targets.as_slice(),
                skipped_at,
                &protected_targets,
            )
            .await?;
        }
        Ok(())
    }

    async fn skip_branch_targets(
        &self,
        state_machine: &bcs_domain::StateMachineDefinition,
        run: &StateMachineRun,
        initial_targets: &[String],
        skipped_at: u64,
        protected_targets: &HashSet<String>,
    ) -> Result<(), CollaborationRuntimeError> {
        let mut visited = HashSet::new();
        let mut stack = initial_targets.to_vec();
        while let Some(node_id) = stack.pop() {
            if !visited.insert(node_id.clone()) || protected_targets.contains(&node_id) {
                continue;
            }
            let Some(node_run) = self.runs.get_node_run(&run.run_id, &node_id).await? else {
                continue;
            };
            if !matches!(
                node_run.status,
                StateMachineNodeStatus::Pending
                    | StateMachineNodeStatus::Ready
                    | StateMachineNodeStatus::RetryScheduled
            ) {
                continue;
            }
            self.runs
                .skip_node(&run.run_id, &node_id, skipped_at)
                .await?;
            log_state_machine_transition(run, &node_id, "skipped", &[], MessageLogStatus::Skipped);
            let Some(node) = state_machine.nodes.get(&node_id) else {
                continue;
            };
            for transition in node.transitions.values() {
                for target in &transition.targets {
                    stack.push(target.clone());
                }
            }
        }
        Ok(())
    }

    async fn all_upstreams_completed(
        &self,
        compiled: &CompiledStateMachine,
        run_id: &str,
        node_id: &str,
    ) -> Result<bool, CollaborationRuntimeError> {
        let upstreams = compiled
            .upstreams
            .get(node_id)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        for upstream_id in upstreams {
            let Some(node_run) = self.runs.get_node_run(run_id, upstream_id).await? else {
                return Ok(false);
            };
            if !matches!(
                node_run.status,
                StateMachineNodeStatus::Completed | StateMachineNodeStatus::Skipped
            ) {
                return Ok(false);
            }
        }
        Ok(true)
    }

    async fn complete_run_if_done(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let nodes = self.runs.list_node_runs(&run.run_id).await?;
        if nodes.iter().any(|node| {
            !matches!(
                node.status,
                StateMachineNodeStatus::Completed | StateMachineNodeStatus::Skipped
            )
        }) {
            return Ok(self.run_view(&run.run_id).await?);
        }
        let output = final_output_text(compiled, &nodes).or_else(|| {
            nodes
                .iter()
                .rev()
                .find_map(|node| node.artifact_text.clone())
        });
        let output_len = output.as_ref().map_or(0, String::len);
        let now = bcs_protocol::now_ms();
        self.runs
            .update_run_status(
                &run.run_id,
                StateMachineRunStatus::Completed,
                output.clone(),
                None,
                now,
                Some(now),
            )
            .await?;
        let (session_complete_result, session_transitioned) = match self
            .sessions
            .complete_if_running(&run.session_id, output.clone().map(Value::String), None)
            .await
        {
            Ok(Some(session)) => {
                bcs_callback::dispatch::maybe_dispatch_for_session_with_url_guard(
                    session,
                    self.groups.clone(),
                    self.sessions.clone(),
                    self.callback_url_guard.clone(),
                );
                ("completed", true)
            }
            Ok(None) => ("not_running", false),
            Err(error) => {
                warn!(
                    run_id = %run.run_id,
                    group_id = %run.group_id,
                    session_id = %run.session_id,
                    error = %error,
                    "state_machine: failed to complete session for completed run"
                );
                ("error", false)
            }
        };
        if session_transitioned {
            if let Some(outbound) = self.session_channel_outbound.as_ref() {
                if let Err(error) = outbound
                    .publish_state_machine_terminal(StateMachineTerminalEvent {
                        group_id: run.group_id.clone(),
                        session_id: run.session_id.clone(),
                        run_id: run.run_id.clone(),
                        workflow_name: compiled.definition.name.clone(),
                        status: StateMachineTerminalStatus::Completed,
                        output: output.clone(),
                    })
                    .await
                {
                    warn!(
                        run_id = %run.run_id,
                        error = %error,
                        "state_machine: failed to publish completed IM notification"
                    );
                }
            }
        }
        info!(
            run_id = %run.run_id,
            group_id = %run.group_id,
            session_id = %run.session_id,
            definition_id = %run.definition_id,
            definition_version = run.definition_version,
            node_count = nodes.len(),
            output_len = output_len,
            session_complete_result = %session_complete_result,
            "state_machine: run completed"
        );
        log_state_machine_run_complete(run, nodes.len(), output_len, session_complete_result);
        self.run_view(&run.run_id).await
    }

    async fn fail_run(
        &self,
        run: &StateMachineRun,
        error: String,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let now = bcs_protocol::now_ms();
        self.runs
            .update_run_status(
                &run.run_id,
                StateMachineRunStatus::Failed,
                None,
                Some(error.clone()),
                now,
                Some(now),
            )
            .await?;
        let (session_complete_result, session_transitioned) = match self
            .sessions
            .complete_if_running(&run.session_id, None, Some(error.clone()))
            .await
        {
            Ok(Some(session)) => {
                bcs_callback::dispatch::maybe_dispatch_for_session_with_url_guard(
                    session,
                    self.groups.clone(),
                    self.sessions.clone(),
                    self.callback_url_guard.clone(),
                );
                ("completed", true)
            }
            Ok(None) => ("not_running", false),
            Err(error) => {
                warn!(
                    run_id = %run.run_id,
                    group_id = %run.group_id,
                    session_id = %run.session_id,
                    error = %error,
                    "state_machine: failed to complete session for failed run"
                );
                ("error", false)
            }
        };
        if session_transitioned {
            let workflow_name = self
                .definitions
                .get_run_snapshot(&run.run_id)
                .await?
                .map(|definition| definition.name)
                .unwrap_or_else(|| run.definition_id.clone());
            if let Some(outbound) = self.session_channel_outbound.as_ref() {
                if let Err(publish_error) = outbound
                    .publish_state_machine_terminal(StateMachineTerminalEvent {
                        group_id: run.group_id.clone(),
                        session_id: run.session_id.clone(),
                        run_id: run.run_id.clone(),
                        workflow_name,
                        status: StateMachineTerminalStatus::Failed,
                        output: None,
                    })
                    .await
                {
                    warn!(
                        run_id = %run.run_id,
                        error = %publish_error,
                        "state_machine: failed to publish failed IM notification"
                    );
                }
            }
        }
        warn!(
            run_id = %run.run_id,
            group_id = %run.group_id,
            session_id = %run.session_id,
            definition_id = %run.definition_id,
            definition_version = run.definition_version,
            error = %error,
            session_complete_result = %session_complete_result,
            "state_machine: run failed"
        );
        log_state_machine_run_failed(run, &error, session_complete_result);
        self.run_view(&run.run_id).await
    }

    async fn fail_node_or_schedule_retry(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node_id: &str,
        attempt: i32,
        error: String,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let max_attempts = compiled_node_max_attempts(compiled, node_id);
        if attempt + 1 < max_attempts {
            let next_attempt = attempt + 1;
            let scheduled = self
                .runs
                .schedule_node_retry(&run.run_id, node_id, attempt, next_attempt)
                .await?;
            if scheduled {
                self.events
                    .append_event(
                        &run.run_id,
                        Some(node_id),
                        Some(next_attempt),
                        "state_machine.node.retry_scheduled",
                        serde_json::json!({
                            "run_id": run.run_id.clone(),
                            "node_id": node_id,
                            "failed_attempt": attempt,
                            "next_attempt": next_attempt,
                            "max_attempts": max_attempts,
                            "reason": error,
                        }),
                        bcs_protocol::now_ms(),
                    )
                    .await?;
                info!(
                    run_id = %run.run_id,
                    group_id = %run.group_id,
                    session_id = %run.session_id,
                    node_id = %node_id,
                    attempt = attempt,
                    next_attempt = next_attempt,
                    "state_machine: node retry scheduled"
                );
                self.dispatch_node(compiled, group, run, node_id).await?;
            }
            return self.run_view(&run.run_id).await;
        }
        self.fail_run(run, error).await
    }

    async fn evaluate_node_outcome(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        node_id: &str,
        attempt: i32,
        artifact_text: &str,
    ) -> Result<JudgeEvaluationResult, CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => return Ok(JudgeEvaluationResult::Outcome("complete".to_string())),
        };
        let Some(node) = state_machine.nodes.get(node_id) else {
            return Ok(JudgeEvaluationResult::Outcome("complete".to_string()));
        };
        let Some(judge) = &node.judge else {
            return Ok(JudgeEvaluationResult::Outcome("complete".to_string()));
        };
        let upstream_outputs = self
            .judge_upstream_outputs(compiled, &run.run_id, node_id)
            .await?;
        let judge_type = judge
            .judge_type
            .clone()
            .unwrap_or_else(|| "llm".to_string());
        let allowed_outcomes = judge.outcomes.clone();
        let judge_timeout_ms = node
            .node_timeout_ms
            .or(state_machine.defaults.node_timeout_ms)
            .unwrap_or(DEFAULT_JUDGE_TIMEOUT_MS)
            .max(1);
        info!(
            run_id = %run.run_id,
            node_id = %node_id,
            attempt = attempt,
            judge_type = %judge_type,
            allowed_outcomes = ?allowed_outcomes,
            timeout_ms = judge_timeout_ms,
            "state_machine: judge evaluation started"
        );
        let started_at = Instant::now();
        let decision = match tokio::time::timeout(
            Duration::from_millis(judge_timeout_ms),
            self.judge.judge(JudgeRequest {
                run_id: run.run_id.clone(),
                node_id: node_id.to_string(),
                attempt,
                judge_type: judge_type.clone(),
                criteria: judge.criteria.clone(),
                allowed_outcomes: allowed_outcomes.clone(),
                input: run.input.clone(),
                upstream_outputs,
                artifact_text: artifact_text.to_string(),
            }),
        )
        .await
        {
            Ok(Ok(decision)) => decision,
            Ok(Err(error)) => {
                let elapsed_ms = elapsed_ms(started_at);
                let provider_error = judge_service_error_message(&error);
                let failure =
                    format!("judge failed for node {node_id} attempt {attempt}: {provider_error}");
                warn!(
                    run_id = %run.run_id,
                    node_id = %node_id,
                    attempt = attempt,
                    judge_type = %judge_type,
                    elapsed_ms = elapsed_ms,
                    error = %provider_error,
                    "state_machine: judge evaluation failed"
                );
                self.append_judge_failure_event(
                    run,
                    node_id,
                    attempt,
                    &judge_type,
                    &allowed_outcomes,
                    "judge_failed",
                    &provider_error,
                    None,
                    elapsed_ms,
                )
                .await?;
                return Ok(JudgeEvaluationResult::Failed(failure));
            }
            Err(_) => {
                let elapsed_ms = elapsed_ms(started_at);
                let failure = format!(
                    "judge timed out for node {node_id} attempt {attempt} after {judge_timeout_ms}ms"
                );
                warn!(
                    run_id = %run.run_id,
                    node_id = %node_id,
                    attempt = attempt,
                    judge_type = %judge_type,
                    timeout_ms = judge_timeout_ms,
                    elapsed_ms = elapsed_ms,
                    "state_machine: judge evaluation timed out"
                );
                self.append_judge_failure_event(
                    run,
                    node_id,
                    attempt,
                    &judge_type,
                    &allowed_outcomes,
                    "judge_timeout",
                    &failure,
                    Some(judge_timeout_ms),
                    elapsed_ms,
                )
                .await?;
                return Ok(JudgeEvaluationResult::Failed(failure));
            }
        };
        let elapsed_ms = elapsed_ms(started_at);
        if !allowed_outcomes
            .iter()
            .any(|outcome| outcome == &decision.outcome)
        {
            let failure = format!(
                "judge outcome is not declared by node {node_id}: {}",
                decision.outcome
            );
            warn!(
                run_id = %run.run_id,
                node_id = %node_id,
                attempt = attempt,
                judge_type = %judge_type,
                outcome = %decision.outcome,
                elapsed_ms = elapsed_ms,
                "state_machine: judge evaluation returned invalid outcome"
            );
            self.append_judge_failure_event(
                run,
                node_id,
                attempt,
                &judge_type,
                &allowed_outcomes,
                "judge_invalid_outcome",
                &failure,
                None,
                elapsed_ms,
            )
            .await?;
            return Ok(JudgeEvaluationResult::Failed(failure));
        }
        self.events
            .append_event(
                &run.run_id,
                Some(node_id),
                Some(attempt),
                "state_machine.judge.completed",
                serde_json::to_value(&decision).map_err(|error| {
                    CollaborationRuntimeError::InvalidRequest(error.to_string())
                })?,
                bcs_protocol::now_ms(),
            )
            .await?;
        info!(
            run_id = %run.run_id,
            node_id = %node_id,
            attempt = attempt,
            judge_type = %judge_type,
            outcome = %decision.outcome,
            elapsed_ms = elapsed_ms,
            "state_machine: judge evaluation completed"
        );
        Ok(JudgeEvaluationResult::Outcome(decision.outcome))
    }

    async fn append_judge_failure_event(
        &self,
        run: &StateMachineRun,
        node_id: &str,
        attempt: i32,
        judge_type: &str,
        allowed_outcomes: &[String],
        reason: &str,
        error: &str,
        timeout_ms: Option<u64>,
        elapsed_ms: u64,
    ) -> Result<(), CollaborationRuntimeError> {
        self.events
            .append_event(
                &run.run_id,
                Some(node_id),
                Some(attempt),
                "state_machine.judge.failed",
                serde_json::json!({
                    "run_id": run.run_id.clone(),
                    "node_id": node_id,
                    "attempt": attempt,
                    "reason": reason,
                    "error": error,
                    "judge_type": judge_type,
                    "allowed_outcomes": allowed_outcomes,
                    "timeout_ms": timeout_ms,
                    "elapsed_ms": elapsed_ms,
                }),
                bcs_protocol::now_ms(),
            )
            .await?;
        Ok(())
    }

    async fn judge_upstream_outputs(
        &self,
        compiled: &CompiledStateMachine,
        run_id: &str,
        node_id: &str,
    ) -> Result<Vec<JudgeArtifact>, CollaborationRuntimeError> {
        let upstreams = compiled
            .upstreams
            .get(node_id)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let mut artifacts = Vec::new();
        for upstream_id in upstreams {
            if let Some(node_run) = self.runs.get_node_run(run_id, upstream_id).await? {
                if let Some(text) = node_run.artifact_text {
                    artifacts.push(JudgeArtifact {
                        node_id: upstream_id.clone(),
                        text,
                    });
                }
            }
        }
        Ok(artifacts)
    }

    async fn apply_completed_node_progression(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node_id: &str,
        outcome: &str,
        completed_at_ms: u64,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        self.skip_unselected_targets(compiled, run, node_id, outcome, completed_at_ms)
            .await?;
        self.dispatch_ready_targets(compiled, group, run, node_id, outcome)
            .await?;
        self.complete_run_if_done(compiled, run).await?;
        self.run_view(&run.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))
    }

    async fn validate_human_input_channel_for_group(
        &self,
        group_id: &str,
        compiled: &CompiledStateMachine,
    ) -> Result<(), CollaborationRuntimeError> {
        let CollaborationRuntimeDefinition::StateMachine(state_machine) =
            &compiled.definition.runtime
        else {
            return Ok(());
        };
        let Some(channel) = state_machine.human_input_channel.as_ref() else {
            return Ok(());
        };
        if let Some(outbound) = self.session_channel_outbound.as_ref() {
            outbound
                .validate_human_input_channel(group_id, &channel.channel_type)
                .await?;
        }
        Ok(())
    }
}

#[async_trait]
impl CollaborationRuntimeService for CollaborationRuntime {
    async fn validate_definition_yaml(
        &self,
        cmd: ValidateCollaborationDefinitionYamlCommand,
    ) -> Result<CollaborationDefinitionValidationOutcome, CollaborationRuntimeError> {
        Ok(validate_authoring_definition_yaml(cmd))
    }

    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        let group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;
        let group_binding = self.bindings.get(&cmd.group_id).await?;
        let resolved_definition = self
            .resolve_definition(&cmd, group_binding.as_ref())
            .await?;
        let should_upsert_definition =
            resolved_definition.source == ResolvedDefinitionSource::Inline;
        let compiled = validate_definition(resolved_definition.definition)?;
        let definition = &compiled.definition;
        let authenticated_human = cmd.authenticated_human.clone();
        let has_human_input = compiled_has_human_input(&compiled);
        self.validate_human_input_channel_for_group(&cmd.group_id, &compiled)
            .await?;
        let resolved_participant_bindings =
            resolve_participant_bindings(&group, &compiled, group_binding.as_ref())?;
        if should_upsert_definition {
            self.definitions.upsert(definition.clone()).await?;
        }
        let (session_id, session_title, mut session) = match cmd.session_id {
            Some(session_id) => {
                let session = self
                    .sessions
                    .get(&session_id)
                    .await
                    .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
                    .ok_or_else(|| {
                        CollaborationRuntimeError::InvalidRequest(format!(
                            "session not found: {session_id}"
                        ))
                    })?;
                (session_id, session.session_title.clone(), session)
            }
            None => {
                let mut participants = group.participants.clone();
                if let Some(human) = authenticated_human.as_ref() {
                    participants.retain(|participant| participant.bot_uuid != human.actor_id);
                    let mut participant =
                        Participant::human(human.actor_id.clone(), ParticipantRole::Observer);
                    participant.bot_name = human.display_name.clone();
                    participant.mode = Some(ParticipantMode::Present);
                    participants.push(participant);
                }
                let outcome = self
                    .sessions
                    .create_or_reactivate(CreateOrReactivateCommand {
                        group_id: group.id.clone(),
                        session_id: None,
                        params: NewSessionParams {
                            session_kind: SessionKind::ServiceInvocation,
                            participants,
                            group_version: Some(group.version),
                            caller_id: cmd.caller_id.clone(),
                            input: Some(cmd.input.clone()),
                            session_title: Some(definition.name.clone()),
                            ..Default::default()
                        },
                    })
                    .await
                    .map_err(|error| {
                        CollaborationRuntimeError::InvalidRequest(error.to_string())
                    })?;
                let session = outcome.session;
                (session.id.clone(), session.session_title.clone(), session)
            }
        };
        if has_human_input {
            if session.group_id != group.id {
                return Err(CollaborationRuntimeError::InvalidRequest(
                    "state-machine session does not belong to the target group".to_string(),
                ));
            }
            // COSEC: caller_id is not proof of Human identity. Only a Human
            // established by the server-side authentication port may be
            // materialized into the session.
            if let Some(human) = authenticated_human.as_ref() {
                let existing = session
                    .participants
                    .iter()
                    .find(|participant| participant.bot_uuid == human.actor_id);
                if existing.is_some_and(|participant| !participant.is_human()) {
                    return Err(CollaborationRuntimeError::Forbidden(
                        "authenticated Human actor ID conflicts with a non-Human session participant"
                            .to_string(),
                    ));
                }
                if existing.is_none() {
                    let mut participant =
                        Participant::human(human.actor_id.clone(), ParticipantRole::Observer);
                    participant.bot_name = human.display_name.clone();
                    participant.mode = Some(ParticipantMode::Present);
                    session = self
                        .sessions
                        .add_participant(&session_id, participant)
                        .await
                        .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?;
                } else if existing.is_some_and(|participant| {
                    participant.effective_mode() != ParticipantMode::Present
                }) {
                    session = self
                        .sessions
                        .update_participant_mode(
                            &session_id,
                            &human.actor_id,
                            ParticipantMode::Present,
                        )
                        .await
                        .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?;
                }
            }
            let present_humans = session
                .participants
                .iter()
                .filter(|participant| {
                    participant.is_human()
                        && participant.effective_mode() == ParticipantMode::Present
                })
                .collect::<Vec<_>>();
            if present_humans.is_empty() {
                return Err(CollaborationRuntimeError::InvalidRequest(
                    "state-machine definitions with human_input require a Present Human session participant"
                        .to_string(),
                ));
            }
            let missing_assignees = human_input_assignees(&compiled)
                .into_iter()
                .filter(|assignee| {
                    !present_humans
                        .iter()
                        .any(|participant| participant.bot_uuid == *assignee)
                })
                .collect::<Vec<_>>();
            if !missing_assignees.is_empty() {
                return Err(CollaborationRuntimeError::InvalidRequest(format!(
                    "HumanInput assignees must be Present Human session participants: {}",
                    missing_assignees.join(", ")
                )));
            }
        }
        let run_id = format!("sm-{}", Uuid::new_v4());
        let now = bcs_protocol::now_ms();
        let run = StateMachineRun {
            run_id: run_id.clone(),
            definition_id: definition.id.clone(),
            definition_version: definition.version,
            group_id: group.id.clone(),
            group_version: group.version,
            session_id,
            created_by: cmd.caller_id.clone(),
            status: StateMachineRunStatus::Running,
            input: cmd.input,
            output: None,
            error: None,
            created_at: now,
            updated_at: now,
            completed_at: None,
        };
        let nodes = build_node_runs(&compiled, &run, &resolved_participant_bindings)?;
        let node_count = nodes.len();
        self.runs.create_run(run.clone(), nodes).await?;
        self.definitions
            .save_run_snapshot(
                &run,
                group.version,
                definition,
                Some(&resolved_participant_bindings),
            )
            .await?;
        info!(
            run_id = %run.run_id,
            group_id = %run.group_id,
            group_version = run.group_version,
            session_id = %run.session_id,
            definition_id = %run.definition_id,
            definition_version = run.definition_version,
            created_by = ?run.created_by,
            initial_nodes = ?compiled.initial_nodes,
            node_count = node_count,
            "state_machine: run started"
        );
        self.publish_state_machine_panel_event(&group, &run, session_title.as_deref())
            .await;
        for node_id in &compiled.initial_nodes {
            self.dispatch_node(&compiled, &group, &run, node_id).await?;
        }
        let view = self
            .run_view(&run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run_id.clone()))?;
        Ok(StartStateMachineRunOutcome { view })
    }

    async fn get_state_machine_run(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        self.run_view(run_id).await
    }

    async fn get_state_machine_run_by_session_id(
        &self,
        session_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run_by_session_id(session_id).await? else {
            return Ok(None);
        };
        self.run_view(&run.run_id).await
    }

    async fn handle_session_human_input(
        &self,
        cmd: HandleSessionHumanInputCommand,
    ) -> Result<HandleSessionHumanInputOutcome, CollaborationRuntimeError> {
        let group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;
        if group.group_strategy != GroupStrategy::StateMachine {
            return Ok(HandleSessionHumanInputOutcome::NotStateMachine);
        }

        let session_id = cmd.session_id.as_deref().ok_or_else(|| {
            CollaborationRuntimeError::Conflict(
                "state-machine messages require a session id".to_string(),
            )
        })?;
        let run = self
            .runs
            .get_run_by_session_id(session_id)
            .await?
            .ok_or_else(|| {
                CollaborationRuntimeError::Conflict(
                    "state-machine session has no active run".to_string(),
                )
            })?;
        if run.group_id != cmd.group_id {
            return Err(CollaborationRuntimeError::Conflict(
                "state-machine session does not belong to the target group".to_string(),
            ));
        }

        let pending = self
            .list_pending_human_nodes(ListPendingHumanNodesCommand {
                run_id: run.run_id.clone(),
                caller_actor_id: cmd.caller_actor_id.clone(),
            })
            .await?;
        let node = match pending.as_slice() {
            [node] => node,
            [] => {
                return Err(CollaborationRuntimeError::Conflict(
                    "state machine is not waiting for Human input".to_string(),
                ));
            }
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "multiple pending human_input nodes are not supported".to_string(),
                ));
            }
        };
        let response = self
            .respond_human_node(RespondHumanNodeCommand {
                run_id: run.run_id,
                node_id: node.node_id.clone(),
                caller_actor_id: cmd.caller_actor_id,
                content: cmd.content,
                source: cmd.source,
            })
            .await?;
        Ok(HandleSessionHumanInputOutcome::Consumed { response })
    }

    async fn respond_human_node(
        &self,
        cmd: RespondHumanNodeCommand,
    ) -> Result<RespondHumanNodeOutcome, CollaborationRuntimeError> {
        let content = cmd.content.trim().to_string();
        if content.is_empty() {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "human response must not be empty".to_string(),
            ));
        }
        if content.len() > MAX_HUMAN_RESPONSE_BYTES {
            return Err(CollaborationRuntimeError::InvalidRequest(format!(
                "human response exceeds {MAX_HUMAN_RESPONSE_BYTES} UTF-8 bytes"
            )));
        }

        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        if run.status != StateMachineRunStatus::Running {
            return Err(CollaborationRuntimeError::Conflict(
                "human node is no longer accepting responses".to_string(),
            ));
        }

        let compiled = validate_definition(self.load_run_definition(&run).await?)?;
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let node_definition = state_machine.nodes.get(&cmd.node_id).ok_or_else(|| {
            CollaborationRuntimeError::NodeNotFound {
                run_id: cmd.run_id.clone(),
                node_id: cmd.node_id.clone(),
            }
        })?;
        if node_definition.kind != StateMachineNodeKind::HumanInput {
            return Err(CollaborationRuntimeError::InvalidRequest(format!(
                "node {} is not a human_input node",
                cmd.node_id
            )));
        }
        // COSEC: IM-targeted input is restricted to its frozen assignee. A
        // frontend-only node has no assignee and retains the existing rule
        // that any Present Human participant in the run session may respond.
        let caller_matches_node = match &node_definition.assignee {
            None => true,
            Some(StateMachineAssignee::RuntimeActor { actor }) => actor == &cmd.caller_actor_id,
            Some(StateMachineAssignee::BotBinding { .. }) => false,
        };
        if !caller_matches_node {
            return Err(CollaborationRuntimeError::Forbidden(
                "caller is not the HumanInput node assignee".to_string(),
            ));
        }

        let node = self
            .runs
            .get_node_run(&cmd.run_id, &cmd.node_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::NodeNotFound {
                run_id: cmd.run_id.clone(),
                node_id: cmd.node_id.clone(),
            })?;
        let now = bcs_protocol::now_ms();
        if node.status != StateMachineNodeStatus::Running
            || node
                .timeout_deadline_ms
                .is_none_or(|deadline| deadline <= now)
        {
            return Err(CollaborationRuntimeError::Conflict(
                "human node is no longer accepting responses".to_string(),
            ));
        }

        let group = self.groups.get(&run.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", run.group_id))
        })?;
        if !self
            .runs
            .record_human_response_if_running(
                &run.run_id,
                &cmd.node_id,
                node.attempt,
                content.clone(),
                cmd.caller_actor_id.clone(),
            )
            .await?
        {
            return Err(CollaborationRuntimeError::Conflict(
                "human node is no longer accepting responses".to_string(),
            ));
        }
        match self
            .evaluate_node_outcome(&compiled, &run, &cmd.node_id, node.attempt, &content)
            .await?
        {
            JudgeEvaluationResult::Outcome(outcome) => {
                let completed_at = bcs_protocol::now_ms();
                let completed = self
                    .runs
                    .complete_node_attempt(
                        &run.run_id,
                        &cmd.node_id,
                        node.attempt,
                        outcome.clone(),
                        content,
                        Some(cmd.caller_actor_id),
                        completed_at,
                    )
                    .await?;
                if !completed {
                    return Err(CollaborationRuntimeError::Conflict(
                        "human response lost the completion race".to_string(),
                    ));
                }
                let view = self
                    .apply_completed_node_progression(
                        &compiled,
                        &group,
                        &run,
                        &cmd.node_id,
                        &outcome,
                        completed_at,
                    )
                    .await?;
                let node = self
                    .runs
                    .get_node_run(&run.run_id, &cmd.node_id)
                    .await?
                    .ok_or_else(|| CollaborationRuntimeError::NodeNotFound {
                        run_id: run.run_id.clone(),
                        node_id: cmd.node_id.clone(),
                    })?;
                Ok(RespondHumanNodeOutcome {
                    node,
                    run: view.run,
                })
            }
            JudgeEvaluationResult::Failed(error) => {
                let failed_at = bcs_protocol::now_ms();
                let failed = self
                    .runs
                    .fail_node_attempt(
                        &run.run_id,
                        &cmd.node_id,
                        node.attempt,
                        error.clone(),
                        failed_at,
                    )
                    .await?;
                if !failed {
                    return Err(CollaborationRuntimeError::Conflict(
                        "human response lost the completion race".to_string(),
                    ));
                }
                self.fail_node_or_schedule_retry(
                    &compiled,
                    &group,
                    &run,
                    &cmd.node_id,
                    node.attempt,
                    error.clone(),
                )
                .await?;
                Err(CollaborationRuntimeError::JudgeUnavailable(error))
            }
        }
    }
    async fn list_pending_human_nodes(
        &self,
        cmd: ListPendingHumanNodesCommand,
    ) -> Result<Vec<PendingHumanNodeView>, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or(CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        if run.status != StateMachineRunStatus::Running {
            return Ok(Vec::new());
        }
        let compiled = validate_definition(self.load_run_definition(&run).await?)?;
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => return Ok(Vec::new()),
        };
        let mut pending = Vec::new();
        for node_run in self.runs.list_node_runs(&run.run_id).await? {
            if node_run.status != StateMachineNodeStatus::Running
                || node_run.artifact_text.is_some()
            {
                continue;
            }
            if !state_machine.nodes.get(&node_run.node_id).is_some_and(|node| {
                node.kind == StateMachineNodeKind::HumanInput
                    && match &node.assignee {
                        None => true,
                        Some(StateMachineAssignee::RuntimeActor { actor }) => {
                            actor == &cmd.caller_actor_id
                        }
                        Some(StateMachineAssignee::BotBinding { .. }) => false,
                    }
            })
            {
                continue;
            }
            pending.push(
                self.pending_human_node_view(&compiled, &run, &node_run)
                    .await?,
            );
        }
        pending.sort_by(|left, right| left.node_id.cmp(&right.node_id));
        Ok(pending)
    }

    async fn get_state_machine_run_for_human(
        &self,
        cmd: HumanRunAccessCommand,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        self.run_view(&cmd.run_id).await
    }

    async fn get_state_machine_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_run_access(&run, cmd.authenticated_human.as_ref())
            .await?;
        self.run_view(&cmd.run_id).await
    }

    async fn get_state_machine_node_run_for_human(
        &self,
        cmd: HumanRunAccessCommand,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        self.get_state_machine_node_run(&cmd.run_id, node_id).await
    }

    async fn get_state_machine_node_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_run_access(&run, cmd.authenticated_human.as_ref())
            .await?;
        self.get_state_machine_node_run(&cmd.run_id, node_id).await
    }

    async fn get_state_machine_run_graph_for_human(
        &self,
        cmd: HumanRunAccessCommand,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        self.get_state_machine_run_graph(&cmd.run_id).await
    }

    async fn get_state_machine_run_graph_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_run_access(&run, cmd.authenticated_human.as_ref())
            .await?;
        self.get_state_machine_run_graph(&cmd.run_id).await
    }

    async fn cancel_state_machine_run_for_human(
        &self,
        cmd: HumanRunAccessCommand,
        reason: Option<String>,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        if run.created_by.as_deref() != Some(cmd.caller_actor_id.as_str()) {
            return Err(CollaborationRuntimeError::Forbidden(
                "only the Human who started the run can cancel it".to_string(),
            ));
        }
        self.cancel_state_machine_run(CancelStateMachineRunCommand {
            run_id: cmd.run_id,
            reason,
        })
        .await
    }

    async fn cancel_state_machine_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
        reason: Option<String>,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        let human_access_required = self
            .authorize_run_access(&run, cmd.authenticated_human.as_ref())
            .await?;
        if human_access_required
            && let Some(human) = cmd.authenticated_human.as_ref()
            && run.created_by.as_deref() != Some(human.actor_id.as_str())
        {
            return Err(CollaborationRuntimeError::Forbidden(
                "only the Human who started the run can cancel it".to_string(),
            ));
        }
        self.cancel_state_machine_run(CancelStateMachineRunCommand {
            run_id: cmd.run_id,
            reason,
        })
        .await
    }

    async fn get_state_machine_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        let Some(node) = self.runs.get_node_run(run_id, node_id).await? else {
            return Ok(None);
        };
        let judge_outputs = self.judge_outputs_for_node(run_id, node_id).await?;
        let sub_status = node_sub_status(&node);
        Ok(Some(StateMachineNodeRunView {
            node,
            sub_status,
            judge_outputs,
        }))
    }

    async fn get_state_machine_run_graph(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(run_id).await? else {
            return Ok(None);
        };
        let definition = self.load_run_definition(&run).await?;
        let compiled = validate_definition(definition)?;
        let nodes = self.runs.list_node_runs(run_id).await?;
        Ok(Some(run_graph_view(run, nodes, &compiled)?))
    }

    async fn get_state_machine_session_history(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        if limit == 0 {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "history limit must be greater than 0".to_string(),
            ));
        }
        let Some(run) = self.runs.get_run_by_session_id(session_id).await? else {
            return Ok(None);
        };
        let group = self.groups.get(&run.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", run.group_id))
        })?;
        let mut messages = self
            .state_machine_messages_from_snapshot(&group, &run)
            .await?;
        messages.sort_by(|left, right| {
            left.timestamp
                .cmp(&right.timestamp)
                .then_with(|| left.id.cmp(&right.id))
        });
        let messages = apply_message_window(messages, limit, before);
        let next_before = messages.iter().map(|message| message.timestamp).min();
        Ok(Some(SessionHistoryResult {
            session_id: session_id.to_string(),
            messages,
            limit,
            before,
            next_before,
        }))
    }

    async fn cancel_state_machine_run(
        &self,
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        let now = bcs_protocol::now_ms();
        let reason = cmd.reason.clone();
        self.runs
            .update_run_status(
                &cmd.run_id,
                StateMachineRunStatus::Aborted,
                None,
                cmd.reason,
                now,
                Some(now),
            )
            .await?;
        let completed_session = self
            .sessions
            .complete_if_running(&run.session_id, None, Some("aborted".to_string()))
            .await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?;
        let session_complete_result = if completed_session.is_some() {
            "completed"
        } else {
            "not_running"
        };
        if let Some(session) = completed_session {
            bcs_callback::dispatch::maybe_dispatch_for_session_with_url_guard(
                session,
                self.groups.clone(),
                self.sessions.clone(),
                self.callback_url_guard.clone(),
            );
        }
        info!(
            run_id = %cmd.run_id,
            group_id = %run.group_id,
            session_id = %run.session_id,
            definition_id = %run.definition_id,
            definition_version = run.definition_version,
            reason = ?reason,
            session_complete_result = %session_complete_result,
            "state_machine: run aborted"
        );
        self.run_view(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id))
    }
    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Ok(self.runs.lookup_delivery_correlation(run_id).await?)
    }

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        self.runs
            .register_delivery_alias(delivery_request_id, bot_delivery_run_id)
            .await?;
        Ok(())
    }

    async fn handle_bot_terminal_event(
        &self,
        cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        let Some(correlation) = self.runs.lookup_delivery_correlation(&cmd.run_id).await? else {
            return Ok(HandleBotTerminalEventOutcome {
                consumed: false,
                view: None,
            });
        };
        let run = self
            .runs
            .get_run(&correlation.state_machine_run_id)
            .await?
            .ok_or_else(|| {
                CollaborationRuntimeError::RunNotFound(correlation.state_machine_run_id.clone())
            })?;
        let node = self
            .runs
            .get_node_run(&correlation.state_machine_run_id, &correlation.node_id)
            .await?;
        let current_event = node.as_ref().is_some_and(|node| {
            run.status == StateMachineRunStatus::Running
                && node.status == StateMachineNodeStatus::Running
                && node.attempt == correlation.attempt
                && node.delivery_request_id.as_deref()
                    == Some(correlation.delivery_request_id.as_str())
                && correlation.assignee_bot_id == cmd.bot_id
        });
        if !current_event {
            self.events
                .append_event(
                    &correlation.state_machine_run_id,
                    Some(&correlation.node_id),
                    Some(correlation.attempt),
                    "state_machine.node.event_ignored",
                    serde_json::json!({
                        "run_id": correlation.state_machine_run_id.clone(),
                        "node_id": correlation.node_id.clone(),
                        "attempt": correlation.attempt,
                        "delivery_request_id": correlation.delivery_request_id.clone(),
                        "bot_delivery_run_id": cmd.run_id.clone(),
                        "bot_id": cmd.bot_id.clone(),
                        "event_type": cmd.event_type.clone(),
                        "state": chat_event_state_slug(&cmd.state),
                        "reason": stale_event_reason(&run, node.as_ref(), &correlation, &cmd.bot_id),
                        "payload": cmd.event_payload.clone(),
                    }),
                    bcs_protocol::now_ms(),
                )
                .await?;
            return Ok(HandleBotTerminalEventOutcome {
                consumed: true,
                view: self.run_view(&run.run_id).await?,
            });
        }
        if let Some(payload) = compact_bot_terminal_event_payload(&correlation, &cmd) {
            self.events
                .append_event(
                    &correlation.state_machine_run_id,
                    Some(&correlation.node_id),
                    Some(correlation.attempt),
                    "state_machine.node.bot_event",
                    payload,
                    bcs_protocol::now_ms(),
                )
                .await?;
        }
        let event_text_len = extract_text(&cmd.event_payload).map_or(0, |text| text.len());
        info!(
            run_id = %run.run_id,
            group_id = %run.group_id,
            session_id = %run.session_id,
            node_id = %correlation.node_id,
            attempt = correlation.attempt,
            bot_id = %cmd.bot_id,
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %cmd.run_id,
            event_type = %cmd.event_type,
            state = %chat_event_state_slug(&cmd.state),
            text_len = event_text_len,
            "state_machine: bot terminal event received"
        );
        log_state_machine_bot_event(&run, &correlation, &cmd, event_text_len);
        self.publish_state_machine_bot_event(
            &run,
            &cmd.bot_id,
            &cmd.run_id,
            &cmd.event_type,
            &cmd.event_payload,
            &cmd.state,
        )
        .await;
        let definition = self.load_run_definition(&run).await?;
        let compiled = validate_definition(definition)?;
        let group = self.groups.get(&run.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", run.group_id))
        })?;
        let now = bcs_protocol::now_ms();
        if matches!(cmd.state, ChatEventState::Final)
            && extract_text(&cmd.event_payload).is_none()
        {
            // A message-less final completes the bot attempt without an
            // artifact. It must advance the node into the normal failure/retry
            // path instead of leaving the node Running after an InvalidRequest.
            let error = "bot completed without visible output".to_string();
            let failed = self
                .runs
                .fail_node_attempt(
                    &correlation.state_machine_run_id,
                    &correlation.node_id,
                    correlation.attempt,
                    error.clone(),
                    now,
                )
                .await?;
            let view = if failed {
                warn!(
                    run_id = %correlation.state_machine_run_id,
                    group_id = %run.group_id,
                    session_id = %run.session_id,
                    node_id = %correlation.node_id,
                    attempt = correlation.attempt,
                    error = %error,
                    "state_machine: node failed"
                );
                log_state_machine_node_result(
                    &run,
                    &correlation,
                    MessageLogStatus::Failed,
                    None,
                    Some(&error),
                    None,
                );
                self.fail_node_or_schedule_retry(
                    &compiled,
                    &group,
                    &run,
                    &correlation.node_id,
                    correlation.attempt,
                    error,
                )
                .await?
            } else {
                self.run_view(&run.run_id).await?
            };
            return Ok(HandleBotTerminalEventOutcome {
                consumed: true,
                view,
            });
        }
        let mut view = None;
        match cmd.state {
            ChatEventState::Final => {
                let text = extract_text(&cmd.event_payload).unwrap_or_default();
                let artifact_len = text.len();
                if node_uses_judge(&compiled, &correlation.node_id)
                    && !self
                        .runs
                        .record_node_artifact_if_running(
                            &correlation.state_machine_run_id,
                            &correlation.node_id,
                            correlation.attempt,
                            text.clone(),
                        )
                        .await?
                {
                    return Ok(HandleBotTerminalEventOutcome {
                        consumed: true,
                        view: self.run_view(&run.run_id).await?,
                    });
                }
                let evaluation = self
                    .evaluate_node_outcome(
                        &compiled,
                        &run,
                        &correlation.node_id,
                        correlation.attempt,
                        &text,
                    )
                    .await?;
                match evaluation {
                    JudgeEvaluationResult::Outcome(outcome) => {
                        let completed_at = bcs_protocol::now_ms();
                        if self
                            .runs
                            .complete_node_attempt(
                                &correlation.state_machine_run_id,
                                &correlation.node_id,
                                correlation.attempt,
                                outcome.clone(),
                                text,
                                None,
                                completed_at,
                            )
                            .await?
                        {
                            info!(
                                run_id = %correlation.state_machine_run_id,
                                group_id = %run.group_id,
                                session_id = %run.session_id,
                                node_id = %correlation.node_id,
                                attempt = correlation.attempt,
                                outcome = %outcome,
                                artifact_len = artifact_len,
                                "state_machine: node completed"
                            );
                            log_state_machine_node_result(
                                &run,
                                &correlation,
                                MessageLogStatus::Completed,
                                Some(&outcome),
                                None,
                                Some(artifact_len),
                            );
                            view = Some(
                                self.apply_completed_node_progression(
                                    &compiled,
                                    &group,
                                    &run,
                                    &correlation.node_id,
                                    &outcome,
                                    completed_at,
                                )
                                .await?,
                            );
                        }
                    }
                    JudgeEvaluationResult::Failed(error) => {
                        let failed_at = bcs_protocol::now_ms();
                        if self
                            .runs
                            .fail_node_attempt(
                                &correlation.state_machine_run_id,
                                &correlation.node_id,
                                correlation.attempt,
                                error.clone(),
                                failed_at,
                            )
                            .await?
                        {
                            warn!(
                                run_id = %correlation.state_machine_run_id,
                                group_id = %run.group_id,
                                session_id = %run.session_id,
                                node_id = %correlation.node_id,
                                attempt = correlation.attempt,
                                error = %error,
                                "state_machine: node failed"
                            );
                            log_state_machine_node_result(
                                &run,
                                &correlation,
                                MessageLogStatus::Failed,
                                None,
                                Some(&error),
                                None,
                            );
                            view = self
                                .fail_node_or_schedule_retry(
                                    &compiled,
                                    &group,
                                    &run,
                                    &correlation.node_id,
                                    correlation.attempt,
                                    error,
                                )
                                .await?;
                        } else {
                            view = self.run_view(&run.run_id).await?;
                        }
                    }
                }
            }
            ChatEventState::Error | ChatEventState::Aborted => {
                let error = extract_text(&cmd.event_payload)
                    .unwrap_or_else(|| format!("bot event state {:?}", cmd.state));
                if self
                    .runs
                    .fail_node_attempt(
                        &correlation.state_machine_run_id,
                        &correlation.node_id,
                        correlation.attempt,
                        error.clone(),
                        now,
                    )
                    .await?
                {
                    warn!(
                        run_id = %correlation.state_machine_run_id,
                        group_id = %run.group_id,
                        session_id = %run.session_id,
                        node_id = %correlation.node_id,
                        attempt = correlation.attempt,
                        state = %chat_event_state_slug(&cmd.state),
                        error = %error,
                        "state_machine: node failed"
                    );
                    log_state_machine_node_result(
                        &run,
                        &correlation,
                        MessageLogStatus::Failed,
                        None,
                        Some(&error),
                        None,
                    );
                    view = self
                        .fail_node_or_schedule_retry(
                            &compiled,
                            &group,
                            &run,
                            &correlation.node_id,
                            correlation.attempt,
                            error,
                        )
                        .await?;
                }
            }
            _ => {
                view = self.run_view(&run.run_id).await?;
            }
        }
        Ok(HandleBotTerminalEventOutcome {
            consumed: true,
            view,
        })
    }

    async fn process_expired_node_timeouts(
        &self,
        limit: usize,
        timeout_grace_ms: u64,
    ) -> Result<usize, CollaborationRuntimeError> {
        if limit == 0 {
            return Ok(0);
        }
        let now = bcs_protocol::now_ms();
        let expired_nodes = self
            .runs
            .list_expired_running_node_runs(now, timeout_grace_ms, limit)
            .await?;
        let mut processed = 0usize;
        for node in expired_nodes {
            let Some(run) = self.runs.get_run(&node.run_id).await? else {
                continue;
            };
            if run.status != StateMachineRunStatus::Running {
                continue;
            }
            let definition = self.load_run_definition(&run).await?;
            let compiled = validate_definition(definition)?;
            let group = self.groups.get(&run.group_id).await.ok_or_else(|| {
                CollaborationRuntimeError::InvalidRequest(format!(
                    "group not found: {}",
                    run.group_id
                ))
            })?;
            let timeout_ms = node.node_timeout_ms.unwrap_or_default();
            let deadline_ms = node.timeout_deadline_ms;
            let error = format!(
                "state-machine node '{}' timed out after {}ms",
                node.node_id, timeout_ms
            );
            self.events
                .append_event(
                    &run.run_id,
                    Some(&node.node_id),
                    Some(node.attempt),
                    "state_machine.node.timeout",
                    serde_json::json!({
                        "run_id": run.run_id.clone(),
                        "node_id": node.node_id.clone(),
                        "attempt": node.attempt,
                        "node_timeout_ms": timeout_ms,
                        "timeout_deadline_ms": deadline_ms,
                        "observed_at_ms": now,
                        "timeout_grace_ms": timeout_grace_ms,
                        "delivery_request_id": node.delivery_request_id.clone(),
                        "bot_delivery_run_id": node.bot_delivery_run_id.clone(),
                    }),
                    now,
                )
                .await?;
            let failed = self
                .runs
                .fail_node_attempt(&run.run_id, &node.node_id, node.attempt, error.clone(), now)
                .await?;
            if !failed {
                continue;
            }
            processed += 1;
            log_state_machine_timeout(&run, &node, timeout_ms, deadline_ms, timeout_grace_ms);
            self.fail_node_or_schedule_retry(
                &compiled,
                &group,
                &run,
                &node.node_id,
                node.attempt,
                error,
            )
            .await?;
        }
        Ok(processed)
    }

    async fn upsert_definition(
        &self,
        definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        reject_explicit_participant_roles(&definition)?;
        let compiled = validate_definition(definition)?;
        self.definitions.upsert(compiled.definition).await?;
        Ok(())
    }

    async fn upsert_definition_with_source_yaml(
        &self,
        definition: CollaborationDefinition,
        source_yaml: String,
    ) -> Result<(), CollaborationRuntimeError> {
        reject_authoring_yaml_identity(&source_yaml)?;
        reject_explicit_participant_roles(&definition)?;
        let compiled = validate_definition(definition)?;
        self.definitions
            .upsert_with_source_yaml(compiled.definition, source_yaml)
            .await?;
        Ok(())
    }

    async fn get_group_collaboration_definition(
        &self,
        group_id: &str,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        self.group_collaboration_definition_view(group_id).await
    }

    async fn patch_group_collaboration_definition(
        &self,
        cmd: PatchGroupCollaborationDefinitionCommand,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        let group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;
        let binding = self.bindings.get(&cmd.group_id).await?.ok_or_else(|| {
            CollaborationRuntimeError::Conflict(
                "group has no default collaboration definition binding".to_string(),
            )
        })?;
        if binding.default_definition.as_ref() != Some(&cmd.base_definition) {
            return Err(CollaborationRuntimeError::Conflict(
                "base_definition does not match current group default definition".to_string(),
            ));
        }
        let mut definition = parse_authoring_definition_yaml(&cmd.definition_yaml)?;
        definition.id = cmd.base_definition.id.clone();
        definition.version = cmd.base_definition.version.checked_add(1).ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(
                "base_definition.version is too large to create a next version".to_string(),
            )
        })?;
        reject_explicit_participant_roles(&definition)?;
        let compiled = validate_definition(definition)?;
        let candidate_ref = CollaborationDefinitionRef {
            id: compiled.definition.id.clone(),
            version: compiled.definition.version,
        };
        let final_participant_bindings = cmd
            .participant_bindings
            .unwrap_or_else(|| binding.participant_bindings.clone());
        validate_runtime_participant_bindings(
            &compiled.definition,
            &group,
            &final_participant_bindings,
        )?;
        resolve_participant_bindings(
            &group,
            &compiled,
            Some(&GroupRuntimeBinding {
                group_id: group.id.clone(),
                group_version: group.version,
                default_definition: Some(candidate_ref.clone()),
                participant_bindings: final_participant_bindings.clone(),
                auto_start_on_service_invocation: binding.auto_start_on_service_invocation,
            }),
        )?;
        ensure_candidate_definition_slot_is_compatible(
            self.definitions.as_ref(),
            &candidate_ref,
            &compiled.definition,
            &cmd.definition_yaml,
        )
        .await?;
        self.definitions
            .upsert_with_source_yaml(compiled.definition, cmd.definition_yaml)
            .await?;
        let rebound = self
            .bindings
            .bind_default_definition_if_current(
                &cmd.group_id,
                group.version,
                Some(cmd.base_definition),
                Some(candidate_ref),
                Some(final_participant_bindings),
                binding.auto_start_on_service_invocation,
            )
            .await?;
        if !rebound {
            return Err(CollaborationRuntimeError::Conflict(
                "group default definition changed while applying patch".to_string(),
            ));
        }
        self.group_collaboration_definition_view(&cmd.group_id)
            .await
    }

    async fn upgrade_group_collaboration_definition(
        &self,
        cmd: UpgradeGroupCollaborationDefinitionCommand,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        if cmd.target_definition.id != cmd.base_definition.id {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "target_definition.id must match base_definition.id".to_string(),
            ));
        }
        if cmd.target_definition.version <= cmd.base_definition.version {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "target_definition.version must be greater than base_definition.version"
                    .to_string(),
            ));
        }
        let group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;
        let binding = self.bindings.get(&cmd.group_id).await?.ok_or_else(|| {
            CollaborationRuntimeError::Conflict(
                "group has no default collaboration definition binding".to_string(),
            )
        })?;
        if binding.default_definition.as_ref() != Some(&cmd.base_definition) {
            return Err(CollaborationRuntimeError::Conflict(
                "base_definition does not match current group default definition".to_string(),
            ));
        }
        let definition = self
            .definitions
            .get(&cmd.target_definition.id, cmd.target_definition.version)
            .await?
            .ok_or_else(|| {
                CollaborationRuntimeError::DefinitionNotFound(
                    cmd.target_definition.id.clone(),
                    cmd.target_definition.version,
                )
        })?;
        let compiled = validate_definition(definition)?;
        let final_participant_bindings = cmd
            .participant_bindings
            .unwrap_or_else(|| binding.participant_bindings.clone());
        validate_runtime_participant_bindings(
            &compiled.definition,
            &group,
            &final_participant_bindings,
        )?;
        resolve_participant_bindings(
            &group,
            &compiled,
            Some(&GroupRuntimeBinding {
                group_id: group.id.clone(),
                group_version: group.version,
                default_definition: Some(cmd.target_definition.clone()),
                participant_bindings: final_participant_bindings.clone(),
                auto_start_on_service_invocation: binding.auto_start_on_service_invocation,
            }),
        )?;
        let rebound = self
            .bindings
            .bind_default_definition_if_current(
                &cmd.group_id,
                group.version,
                Some(cmd.base_definition),
                Some(cmd.target_definition),
                Some(final_participant_bindings),
                binding.auto_start_on_service_invocation,
            )
            .await?;
        if !rebound {
            return Err(CollaborationRuntimeError::Conflict(
                "group default definition changed while applying upgrade".to_string(),
            ));
        }
        self.group_collaboration_definition_view(&cmd.group_id)
            .await
    }

    async fn configure_group_runtime(
        &self,
        cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        let group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;

        let participant_bindings = cmd.participant_bindings;
        let mut definition_for_validation = None;
        let default_definition = if let Some(yaml) = cmd.definition_yaml {
            let definition: CollaborationDefinition = serde_yaml::from_str(&yaml)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
            reject_explicit_participant_roles(&definition)?;
            let compiled = validate_definition(definition)?;
            let definition_ref = CollaborationDefinitionRef {
                id: compiled.definition.id.clone(),
                version: compiled.definition.version,
            };
            definition_for_validation = Some(compiled.definition.clone());
            self.definitions
                .upsert_with_source_yaml(compiled.definition, yaml)
                .await?;
            Some(definition_ref)
        } else if let Some(value) = cmd.definition {
            let definition: CollaborationDefinition = serde_json::from_value(value)
                .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
            reject_explicit_participant_roles(&definition)?;
            let compiled = validate_definition(definition)?;
            let definition_ref = CollaborationDefinitionRef {
                id: compiled.definition.id.clone(),
                version: compiled.definition.version,
            };
            definition_for_validation = Some(compiled.definition.clone());
            self.definitions.upsert(compiled.definition).await?;
            Some(definition_ref)
        } else if let Some(definition_ref) = cmd.definition_ref {
            let definition = self
                .definitions
                .get(&definition_ref.id, definition_ref.version)
                .await?
                .ok_or_else(|| {
                    CollaborationRuntimeError::DefinitionNotFound(
                        definition_ref.id.clone(),
                        definition_ref.version,
                    )
                })?;
            let compiled = validate_definition(definition)?;
            definition_for_validation = Some(compiled.definition);
            Some(definition_ref)
        } else {
            None
        };
        if !participant_bindings.is_empty() {
            let definition = definition_for_validation.as_ref().ok_or_else(|| {
                CollaborationRuntimeError::InvalidParticipantBinding(
                    "participant_bindings require a default collaboration definition".to_string(),
                )
            })?;
            validate_runtime_participant_bindings(definition, &group, &participant_bindings)?;
        }

        self.bindings
            .bind_default_definition(
                &cmd.group_id,
                group.version,
                default_definition.clone(),
                Some(participant_bindings),
                cmd.auto_start_on_service_invocation,
            )
            .await?;

        Ok(ConfigureGroupRuntimeOutcome {
            group_id: cmd.group_id,
            default_definition,
            auto_start_on_service_invocation: cmd.auto_start_on_service_invocation,
        })
    }
}

fn resolve_participant_bindings(
    group: &Group,
    compiled: &CompiledStateMachine,
    group_binding: Option<&GroupRuntimeBinding>,
) -> Result<BTreeMap<String, ResolvedParticipantBinding>, CollaborationRuntimeError> {
    let definition = &compiled.definition;
    let runtime_bindings = group_binding
        .map(|binding| &binding.participant_bindings)
        .filter(|bindings| !bindings.is_empty());
    if let Some(bindings) = runtime_bindings {
        for binding_key in bindings.keys() {
            if !definition.participants.contains_key(binding_key) {
                return invalid_participant_binding(format!(
                    "participant_bindings contains undeclared slot: {binding_key}"
                ));
            }
        }
    }

    let referenced_slots = referenced_participant_slots(compiled)?;
    let group_participants = group
        .participants
        .iter()
        .map(|participant| participant.bot_uuid.as_str())
        .collect::<HashSet<_>>();
    let mut resolved = BTreeMap::new();
    for (slot, definition_binding) in &definition.participants {
        let (source, binding_source, bot_ids) = if let Some(runtime_binding) =
            runtime_bindings.and_then(|bindings| bindings.get(slot))
        {
            (
                "group_runtime_binding".to_string(),
                Some(runtime_binding.source.clone()),
                runtime_binding_bot_ids(slot, runtime_binding)?,
            )
        } else if let Some(bot_id) = definition_binding
            .bot_id
            .as_deref()
            .map(str::trim)
            .filter(|bot_id| !bot_id.is_empty())
        {
            (
                "definition_legacy_bot_id".to_string(),
                None,
                vec![bot_id.to_string()],
            )
        } else if definition_binding.required || referenced_slots.contains(slot) {
            return invalid_participant_binding(format!(
                "participant slot {slot} has no runtime binding or legacy bot_id"
            ));
        } else {
            continue;
        };

        for bot_id in &bot_ids {
            if !group_participants.contains(bot_id.as_str()) {
                return invalid_participant_binding(format!(
                    "participant slot {slot} bot_id is not a group participant: {bot_id}"
                ));
            }
        }
        if referenced_slots.contains(slot) && bot_ids.len() != 1 {
            return invalid_participant_binding(format!(
                "participant slot {slot} is assigned to a node and must resolve to exactly one bot in the current runtime"
            ));
        }
        let participants = bot_ids
            .iter()
            .map(|bot_id| ResolvedParticipant {
                bot_id: bot_id.clone(),
                bcs_participant_role: inferred_participant_role(group, bot_id),
            })
            .collect();
        resolved.insert(
            slot.clone(),
            ResolvedParticipantBinding {
                source,
                binding_source,
                bot_ids,
                participants,
                extensions: Default::default(),
            },
        );
    }

    for slot in &referenced_slots {
        if !resolved.contains_key(slot) {
            return invalid_participant_binding(format!(
                "node assignee binding has no resolved participant slot: {slot}"
            ));
        }
    }
    Ok(resolved)
}

fn validate_runtime_participant_bindings(
    definition: &CollaborationDefinition,
    group: &Group,
    participant_bindings: &BTreeMap<String, RuntimeParticipantBinding>,
) -> Result<(), CollaborationRuntimeError> {
    let group_participants = group
        .participants
        .iter()
        .map(|participant| participant.bot_uuid.as_str())
        .collect::<HashSet<_>>();
    for (slot, binding) in participant_bindings {
        if !definition.participants.contains_key(slot) {
            return invalid_participant_binding(format!(
                "participant_bindings contains undeclared slot: {slot}"
            ));
        }
        for bot_id in runtime_binding_bot_ids(slot, binding)? {
            if !group_participants.contains(bot_id.as_str()) {
                return invalid_participant_binding(format!(
                    "participant slot {slot} bot_id is not a group participant: {bot_id}"
                ));
            }
        }
    }
    Ok(())
}

fn referenced_participant_slots(
    compiled: &CompiledStateMachine,
) -> Result<HashSet<String>, CollaborationRuntimeError> {
    let state_machine = match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "runtime.kind must be state_machine".to_string(),
            ));
        }
    };
    let mut slots = HashSet::new();
    for node in state_machine.nodes.values() {
        if let Some(StateMachineAssignee::BotBinding { binding }) = &node.assignee {
            slots.insert(binding.clone());
        }
    }
    Ok(slots)
}

fn runtime_binding_bot_ids(
    slot: &str,
    binding: &RuntimeParticipantBinding,
) -> Result<Vec<String>, CollaborationRuntimeError> {
    if binding.source != "manual" {
        return invalid_participant_binding(format!(
            "participant slot {slot} binding source is unsupported: {}",
            binding.source
        ));
    }
    let mut seen = HashSet::new();
    let mut bot_ids = Vec::new();
    for raw_bot_id in &binding.bot_ids {
        let bot_id = raw_bot_id.trim();
        if bot_id.is_empty() {
            return invalid_participant_binding(format!(
                "participant slot {slot} contains empty bot_id"
            ));
        }
        if !seen.insert(bot_id.to_string()) {
            return invalid_participant_binding(format!(
                "participant slot {slot} contains duplicate bot_id: {bot_id}"
            ));
        }
        bot_ids.push(bot_id.to_string());
    }
    if bot_ids.is_empty() {
        return invalid_participant_binding(format!(
            "participant slot {slot} binding bot_ids must not be empty"
        ));
    }
    Ok(bot_ids)
}

fn inferred_participant_role(group: &Group, bot_id: &str) -> ParticipantRole {
    if bot_id == group.driver_bot {
        ParticipantRole::Driver
    } else {
        ParticipantRole::Consultant
    }
}

fn invalid_participant_binding<T>(
    message: impl Into<String>,
) -> Result<T, CollaborationRuntimeError> {
    Err(CollaborationRuntimeError::InvalidParticipantBinding(
        message.into(),
    ))
}

fn definition_yaml_for_record(
    record: &CollaborationDefinitionRecord,
) -> (Option<String>, DefinitionYamlSource) {
    if let Some(source_yaml) = record
        .yaml_text
        .as_deref()
        .filter(|source_yaml| !source_yaml.is_empty())
    {
        return (
            Some(source_yaml.to_string()),
            DefinitionYamlSource::Original,
        );
    }
    match generated_authoring_yaml(&record.definition) {
        Ok(generated) => (Some(generated), DefinitionYamlSource::GeneratedNormalized),
        Err(_) => (None, DefinitionYamlSource::Unavailable),
    }
}

fn generated_authoring_yaml(
    definition: &CollaborationDefinition,
) -> Result<String, serde_yaml::Error> {
    let mut value = serde_yaml::to_value(definition)?;
    if let Some(mapping) = value.as_mapping_mut() {
        mapping.shift_remove("id");
        mapping.shift_remove("version");
        remove_string_value(mapping, "api_version", "bcs.collaboration/v1");
        mapping.shift_remove("requires");
        cleanup_metadata_authoring_yaml(mapping);
        cleanup_participants_authoring_yaml(mapping);
        cleanup_state_machine_authoring_yaml(mapping);
        remove_empty_mapping(mapping, "extensions");
    }
    serde_yaml::to_string(&value)
}

fn cleanup_metadata_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let remove_metadata = if let Some(metadata) = mapping
        .get_mut("metadata")
        .and_then(serde_yaml::Value::as_mapping_mut)
    {
        remove_empty_mapping(metadata, "labels");
        remove_empty_mapping(metadata, "extensions");
        metadata.is_empty()
    } else {
        false
    };
    if remove_metadata {
        mapping.shift_remove("metadata");
    }
}

fn cleanup_participants_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    if let Some(participants) = mapping
        .get_mut("participants")
        .and_then(serde_yaml::Value::as_mapping_mut)
    {
        for participant in participants.values_mut() {
            if let Some(participant) = participant.as_mapping_mut() {
                participant.shift_remove("bcs_participant_role");
                remove_bool_value(participant, "required", false);
                remove_empty_mapping(participant, "extensions");
            }
        }
    }
    remove_empty_mapping(mapping, "participants");
}

fn cleanup_state_machine_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let Some(runtime) = mapping
        .get_mut("runtime")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    let Some(state_machine) = runtime
        .get_mut("state_machine")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    remove_i64_value(state_machine, "version", 1);
    remove_string_value(state_machine, "graph_mode", "acyclic");
    remove_default_projection(state_machine);
    remove_default_state_machine_defaults(state_machine);
    remove_empty_mapping(state_machine, "variables");
    remove_empty_mapping(state_machine, "events");
    remove_empty_mapping(state_machine, "extensions");
    cleanup_state_machine_nodes_authoring_yaml(state_machine);
}

fn remove_default_projection(mapping: &mut serde_yaml::Mapping) {
    let Some(projection) = mapping
        .get("projection")
        .and_then(serde_yaml::Value::as_mapping)
    else {
        return;
    };
    if projection.len() == 1
        && matches!(
            projection.get("default_visibility"),
            Some(serde_yaml::Value::String(value)) if value == "private"
        )
    {
        mapping.shift_remove("projection");
    }
}

fn remove_default_state_machine_defaults(mapping: &mut serde_yaml::Mapping) {
    let remove_defaults = if let Some(defaults) = mapping
        .get_mut("defaults")
        .and_then(serde_yaml::Value::as_mapping_mut)
    {
        remove_i64_value(defaults, "max_attempts", 1);
        defaults.is_empty()
    } else {
        false
    };
    if remove_defaults {
        mapping.shift_remove("defaults");
    }
}

fn cleanup_state_machine_nodes_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let Some(nodes) = mapping
        .get_mut("nodes")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    for node in nodes.values_mut() {
        let Some(node) = node.as_mapping_mut() else {
            continue;
        };
        remove_bool_value(node, "final_output", false);
        remove_empty_mapping(node, "extensions");
        cleanup_node_transitions_authoring_yaml(node);
        cleanup_judge_authoring_yaml(node);
    }
}

fn cleanup_node_transitions_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let Some(transitions) = mapping
        .get_mut("transitions")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    for transition in transitions.values_mut() {
        let Some(transition) = transition.as_mapping_mut() else {
            continue;
        };
        remove_empty_sequence(transition, "targets");
    }
    remove_empty_mapping(mapping, "transitions");
}

fn cleanup_judge_authoring_yaml(mapping: &mut serde_yaml::Mapping) {
    let Some(judge) = mapping
        .get_mut("judge")
        .and_then(serde_yaml::Value::as_mapping_mut)
    else {
        return;
    };
    remove_empty_mapping(judge, "extensions");
}

fn remove_empty_mapping(mapping: &mut serde_yaml::Mapping, key: &str) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::Mapping(value)) if value.is_empty()
    ) {
        mapping.shift_remove(key);
    }
}

fn remove_empty_sequence(mapping: &mut serde_yaml::Mapping, key: &str) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::Sequence(value)) if value.is_empty()
    ) {
        mapping.shift_remove(key);
    }
}

fn remove_bool_value(mapping: &mut serde_yaml::Mapping, key: &str, expected: bool) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::Bool(value)) if *value == expected
    ) {
        mapping.shift_remove(key);
    }
}

fn remove_string_value(mapping: &mut serde_yaml::Mapping, key: &str, expected: &str) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::String(value)) if value == expected
    ) {
        mapping.shift_remove(key);
    }
}

fn remove_i64_value(mapping: &mut serde_yaml::Mapping, key: &str, expected: i64) {
    if matches!(
        mapping.get(key),
        Some(serde_yaml::Value::Number(value)) if value.as_i64() == Some(expected)
    ) {
        mapping.shift_remove(key);
    }
}

fn parse_authoring_definition_yaml(
    yaml: &str,
) -> Result<CollaborationDefinition, CollaborationRuntimeError> {
    reject_authoring_yaml_identity(yaml)?;
    serde_yaml::from_str(yaml)
        .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))
}

fn reject_authoring_yaml_identity(yaml: &str) -> Result<(), CollaborationRuntimeError> {
    if yaml.as_bytes().len() > MAX_COLLABORATION_DEFINITION_YAML_BYTES {
        return Err(CollaborationRuntimeError::InvalidDefinition(format!(
            "collaboration definition YAML exceeds {} bytes",
            MAX_COLLABORATION_DEFINITION_YAML_BYTES
        )));
    }
    let value: serde_yaml::Value = serde_yaml::from_str(yaml)
        .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?;
    let Some(mapping) = value.as_mapping() else {
        return Ok(());
    };
    for key in mapping.keys() {
        if matches!(key.as_str(), Some("id" | "version")) {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "collaboration definition YAML must not contain top-level id or version"
                    .to_string(),
            ));
        }
    }
    Ok(())
}

async fn ensure_candidate_definition_slot_is_compatible(
    definitions: &dyn StateMachineDefinitionRepoPort,
    candidate_ref: &CollaborationDefinitionRef,
    incoming_definition: &CollaborationDefinition,
    incoming_source_yaml: &str,
) -> Result<(), CollaborationRuntimeError> {
    let Some(existing) = definitions
        .get_record(&candidate_ref.id, candidate_ref.version)
        .await?
    else {
        return Ok(());
    };
    let existing_json = normalized_definition_json(&existing.definition)?;
    let incoming_json = normalized_definition_json(incoming_definition)?;
    if existing_json != incoming_json {
        return Err(CollaborationRuntimeError::Conflict(format!(
            "CollaborationDefinition '{}@{}' already exists with different content",
            candidate_ref.id, candidate_ref.version
        )));
    }
    if let Some(existing_yaml) = existing
        .yaml_text
        .as_deref()
        .filter(|existing_yaml| !existing_yaml.is_empty())
    {
        if existing_yaml != incoming_source_yaml {
            return Err(CollaborationRuntimeError::Conflict(format!(
                "CollaborationDefinition '{}@{}' already exists with different source YAML",
                candidate_ref.id, candidate_ref.version
            )));
        }
    }
    Ok(())
}

fn normalized_definition_json(
    definition: &CollaborationDefinition,
) -> Result<String, CollaborationRuntimeError> {
    serde_json::to_string(definition)
        .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))
}

fn stale_event_reason(
    run: &StateMachineRun,
    node: Option<&StateMachineNodeRun>,
    correlation: &StateMachineDeliveryCorrelation,
    bot_id: &str,
) -> String {
    if run.status != StateMachineRunStatus::Running {
        return format!("run status is {:?}", run.status);
    }
    let Some(node) = node else {
        return "node run not found".to_string();
    };
    if node.status != StateMachineNodeStatus::Running {
        return format!("node status is {:?}", node.status);
    }
    if node.attempt != correlation.attempt {
        return format!(
            "node attempt is {}, correlation attempt is {}",
            node.attempt, correlation.attempt
        );
    }
    if node.delivery_request_id.as_deref() != Some(correlation.delivery_request_id.as_str()) {
        return "delivery_request_id no longer matches node run".to_string();
    }
    if correlation.assignee_bot_id != bot_id {
        return "bot_id does not match correlation assignee".to_string();
    }
    "event no longer matches current node run".to_string()
}

fn compiled_has_human_input(compiled: &CompiledStateMachine) -> bool {
    match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine
            .nodes
            .values()
            .any(|node| node.kind == StateMachineNodeKind::HumanInput),
        _ => false,
    }
}

fn human_input_assignees(compiled: &CompiledStateMachine) -> HashSet<String> {
    match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine
            .nodes
            .values()
            .filter_map(|node| {
                if node.kind != StateMachineNodeKind::HumanInput {
                    return None;
                }
                match &node.assignee {
                    Some(StateMachineAssignee::RuntimeActor { actor }) => Some(actor.clone()),
                    _ => None,
                }
            })
            .collect(),
        _ => HashSet::new(),
    }
}

fn build_node_runs(
    compiled: &CompiledStateMachine,
    run: &StateMachineRun,
    resolved_participant_bindings: &BTreeMap<String, ResolvedParticipantBinding>,
) -> Result<Vec<StateMachineNodeRun>, CollaborationRuntimeError> {
    let state_machine = match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "runtime.kind must be state_machine".to_string(),
            ));
        }
    };
    let default_node_timeout_ms = state_machine.defaults.node_timeout_ms;
    let default_max_attempts = state_machine.defaults.max_attempts.max(1);
    let mut nodes = Vec::new();
    for (node_id, node) in &state_machine.nodes {
        let (assignee_bot_id, node_timeout_ms, max_attempts) = match node.kind {
            StateMachineNodeKind::BotTask => {
                let assignee_bot_id = match &node.assignee {
                    Some(StateMachineAssignee::BotBinding { binding }) => {
                        resolved_participant_bindings
                            .get(binding)
                            .and_then(|binding| binding.bot_ids.first().cloned())
                            .ok_or_else(|| {
                                CollaborationRuntimeError::InvalidParticipantBinding(format!(
                                    "node {node_id} assignee binding {binding} has no resolved bot_id"
                                ))
                            })?
                    }
                    _ => {
                        return Err(CollaborationRuntimeError::InvalidDefinition(format!(
                            "node {node_id} assignee is not supported"
                        )))
                    }
                };
                (
                    Some(assignee_bot_id),
                    node.node_timeout_ms
                        .or(default_node_timeout_ms)
                        .filter(|timeout_ms| *timeout_ms > 0),
                    node.max_attempts.unwrap_or(default_max_attempts).max(1),
                )
            }
            StateMachineNodeKind::HumanInput => (
                None,
                node.node_timeout_ms.filter(|timeout_ms| *timeout_ms > 0),
                1,
            ),
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(format!(
                    "node {node_id} kind is not supported"
                )));
            }
        };
        nodes.push(StateMachineNodeRun {
            run_id: run.run_id.clone(),
            node_id: node_id.clone(),
            status: StateMachineNodeStatus::Pending,
            attempt: 0,
            node_timeout_ms,
            timeout_deadline_ms: None,
            max_attempts,
            assignee_bot_id,
            outcome: None,
            responded_by: None,
            delivery_request_id: None,
            bot_delivery_run_id: None,
            artifact_text: None,
            error: None,
            started_at: None,
            completed_at: None,
        });
    }
    Ok(nodes)
}

fn run_graph_view(
    run: StateMachineRun,
    node_runs: Vec<StateMachineNodeRun>,
    compiled: &CompiledStateMachine,
) -> Result<StateMachineRunGraphView, CollaborationRuntimeError> {
    let state_machine = match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "runtime.kind must be state_machine".to_string(),
            ));
        }
    };
    let node_runs_by_id = node_runs
        .iter()
        .map(|node| (node.node_id.as_str(), node))
        .collect::<BTreeMap<_, _>>();
    let nodes = state_machine
        .nodes
        .iter()
        .map(|(node_id, node)| {
            let run_node = node_runs_by_id.get(node_id.as_str()).copied();
            StateMachineGraphNodeView {
                node_id: node_id.clone(),
                display_name: node.display_name.clone(),
                kind: node.kind,
                assignee: node.assignee.clone(),
                final_output: node.final_output,
                status: run_node.map(|node| node.status),
                attempt: run_node.map(|node| node.attempt),
                assignee_bot_id: run_node.and_then(|node| node.assignee_bot_id.clone()),
                started_at: run_node.and_then(|node| node.started_at),
                completed_at: run_node.and_then(|node| node.completed_at),
                sub_status: run_node.and_then(node_sub_status),
            }
        })
        .collect();
    let mut edges = Vec::new();
    for (source, node) in &state_machine.nodes {
        for (outcome, transition) in &node.transitions {
            for target in &transition.targets {
                edges.push(StateMachineGraphEdgeView {
                    source: source.clone(),
                    outcome: outcome.clone(),
                    target: target.clone(),
                    guard: transition.guard.clone(),
                });
            }
        }
    }
    Ok(StateMachineRunGraphView {
        run,
        definition: StateMachineGraphDefinitionView {
            id: compiled.definition.id.clone(),
            version: compiled.definition.version,
            name: compiled.definition.name.clone(),
            graph_mode: state_machine.graph_mode,
            initial_node: state_machine.initial_node.clone(),
            initial_nodes: compiled.initial_nodes.clone(),
        },
        nodes,
        edges,
    })
}

fn node_uses_judge(compiled: &CompiledStateMachine, node_id: &str) -> bool {
    match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine
            .nodes
            .get(node_id)
            .is_some_and(|node| node.judge.is_some()),
        _ => false,
    }
}

fn node_sub_status(node: &StateMachineNodeRun) -> Option<StateMachineNodeSubStatus> {
    if node.status != StateMachineNodeStatus::Running {
        return None;
    }
    Some(if node.artifact_text.is_some() {
        StateMachineNodeSubStatus::Judging
    } else {
        StateMachineNodeSubStatus::AwaitingResponse
    })
}

fn group_context_input(group: &Group, session_id: &str) -> GroupContextInput {
    GroupContextInput {
        session_id: session_id.to_string(),
        driver_bot: group.driver_bot.clone(),
        originator: group.originator().to_string(),
        participants: group
            .participants
            .iter()
            .map(|participant| GroupContextParticipant {
                id: participant.bot_uuid.clone(),
                name: participant.bot_name.clone(),
                role: Some(participant_role_slug(participant.role).to_string()),
                is_bot: participant.is_bot(),
            })
            .collect(),
        bcs_session_id: Some(session_id.to_string()),
    }
}

fn participant_role_slug(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}

fn log_state_machine_node_dispatch(
    group: &Group,
    run: &StateMachineRun,
    node_id: &str,
    attempt: i32,
    assignee_bot_id: &str,
    delivery_request_id: &str,
    prompt: &str,
) {
    let content = MessageLogContent::from_text(prompt);
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::NodeDispatch.as_str(),
        status = MessageLogStatus::Routed.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %group.id,
        state_machine_run_id = %run.run_id,
        run_id = %delivery_request_id,
        node_id = %node_id,
        attempt = attempt,
        bot_id = %assignee_bot_id,
        assignee_bot_id = %assignee_bot_id,
        delivery_request_id = %delivery_request_id,
        content = %content.content,
        content_length = content.content_length,
        content_truncated = content.content_truncated,
        content_truncated_bytes = content.content_truncated_bytes,
        "node_dispatch"
    );
}

fn log_state_machine_delivery_result(
    group: &Group,
    run: &StateMachineRun,
    node_id: &str,
    attempt: i32,
    assignee_bot_id: &str,
    delivery_request_id: &str,
    target_bot_id: Option<&str>,
    delivered: bool,
    error: Option<&str>,
    failure_phase: Option<&str>,
) {
    let status = if delivered {
        MessageLogStatus::Delivered
    } else {
        MessageLogStatus::Failed
    };
    let bot_id = target_bot_id.unwrap_or(assignee_bot_id);
    if delivered {
        info!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::StateMachine.as_str(),
            session_id = %run.session_id,
            group_id = %group.id,
            state_machine_run_id = %run.run_id,
            run_id = %delivery_request_id,
            node_id = %node_id,
            attempt = attempt,
            bot_id = %bot_id,
            assignee_bot_id = %assignee_bot_id,
            to_bot_id = %bot_id,
            delivery_request_id = %delivery_request_id,
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    } else {
        warn!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::StateMachine.as_str(),
            session_id = %run.session_id,
            group_id = %group.id,
            state_machine_run_id = %run.run_id,
            run_id = %delivery_request_id,
            node_id = %node_id,
            attempt = attempt,
            bot_id = %bot_id,
            assignee_bot_id = %assignee_bot_id,
            to_bot_id = %bot_id,
            delivery_request_id = %delivery_request_id,
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    }
}

fn log_state_machine_bot_event(
    run: &StateMachineRun,
    correlation: &StateMachineDeliveryCorrelation,
    cmd: &HandleBotTerminalEventCommand,
    text_len: usize,
) {
    let text = extract_text(&cmd.event_payload).unwrap_or_default();
    let content = MessageLogContent::from_text(&text);
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::BotEvent.as_str(),
        status = MessageLogStatus::Responded.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %cmd.run_id,
        node_id = %correlation.node_id,
        attempt = correlation.attempt,
        bot_id = %cmd.bot_id,
        assignee_bot_id = %correlation.assignee_bot_id,
        delivery_request_id = %correlation.delivery_request_id,
        bot_delivery_run_id = %cmd.run_id,
        chat_event_type = %cmd.event_type,
        chat_event_state = chat_event_state_slug(&cmd.state),
        text_len = text_len,
        content = %content.content,
        content_length = content.content_length,
        content_truncated = content.content_truncated,
        content_truncated_bytes = content.content_truncated_bytes,
        "bot_event"
    );
}

fn log_state_machine_node_result(
    run: &StateMachineRun,
    correlation: &StateMachineDeliveryCorrelation,
    status: MessageLogStatus,
    outcome: Option<&str>,
    error: Option<&str>,
    artifact_len: Option<usize>,
) {
    let level_error = status == MessageLogStatus::Failed;
    if level_error {
        warn!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::NodeResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::StateMachine.as_str(),
            session_id = %run.session_id,
            group_id = %run.group_id,
            state_machine_run_id = %run.run_id,
            run_id = %correlation.delivery_request_id,
            node_id = %correlation.node_id,
            attempt = correlation.attempt,
            bot_id = %correlation.assignee_bot_id,
            assignee_bot_id = %correlation.assignee_bot_id,
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %correlation.bot_delivery_run_id.as_deref().unwrap_or(""),
            outcome = %outcome.unwrap_or(""),
            artifact_len = artifact_len.unwrap_or(0),
            error = %error.unwrap_or(""),
            "node_result"
        );
    } else {
        info!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::NodeResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::StateMachine.as_str(),
            session_id = %run.session_id,
            group_id = %run.group_id,
            state_machine_run_id = %run.run_id,
            run_id = %correlation.delivery_request_id,
            node_id = %correlation.node_id,
            attempt = correlation.attempt,
            bot_id = %correlation.assignee_bot_id,
            assignee_bot_id = %correlation.assignee_bot_id,
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %correlation.bot_delivery_run_id.as_deref().unwrap_or(""),
            outcome = %outcome.unwrap_or(""),
            artifact_len = artifact_len.unwrap_or(0),
            error = %error.unwrap_or(""),
            "node_result"
        );
    }
}

fn log_state_machine_transition(
    run: &StateMachineRun,
    node_id: &str,
    outcome: &str,
    next_node_ids: &[String],
    status: MessageLogStatus,
) {
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::Transition.as_str(),
        status = status.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %run.run_id,
        node_id = %node_id,
        outcome = %outcome,
        next_node_ids = %message_log_json(&next_node_ids),
        next_node_count = next_node_ids.len(),
        "transition"
    );
}

fn log_state_machine_run_complete(
    run: &StateMachineRun,
    node_count: usize,
    output_len: usize,
    session_complete_result: &str,
) {
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::RunComplete.as_str(),
        status = MessageLogStatus::Completed.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %run.run_id,
        node_count = node_count,
        output_len = output_len,
        session_complete_result = %session_complete_result,
        "run_complete"
    );
}

fn log_state_machine_run_failed(run: &StateMachineRun, error: &str, session_complete_result: &str) {
    warn!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::RunFailed.as_str(),
        status = MessageLogStatus::Failed.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %run.run_id,
        error = %error,
        session_complete_result = %session_complete_result,
        "run_failed"
    );
}

fn log_state_machine_timeout(
    run: &StateMachineRun,
    node: &StateMachineNodeRun,
    timeout_ms: u64,
    deadline_ms: Option<u64>,
    timeout_grace_ms: u64,
) {
    warn!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::Timeout.as_str(),
        status = MessageLogStatus::Timeout.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %node.delivery_request_id.as_deref().unwrap_or(run.run_id.as_str()),
        node_id = %node.node_id,
        attempt = node.attempt,
        bot_id = %node.assignee_bot_id.as_deref().unwrap_or(""),
        assignee_bot_id = %node.assignee_bot_id.as_deref().unwrap_or(""),
        delivery_request_id = %node.delivery_request_id.as_deref().unwrap_or(""),
        bot_delivery_run_id = %node.bot_delivery_run_id.as_deref().unwrap_or(""),
        node_timeout_ms = timeout_ms,
        timeout_deadline_ms = deadline_ms.unwrap_or(0),
        timeout_grace_ms = timeout_grace_ms,
        "timeout"
    );
}

fn extract_text(payload: &Value) -> Option<String> {
    payload
        .get("message")
        .and_then(|message| message.get("content"))
        .and_then(Value::as_array)
        .and_then(|blocks| {
            blocks.iter().find_map(|block| {
                block
                    .get("text")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
        })
        .or_else(|| {
            payload
                .get("data")
                .and_then(|data| data.get("text").or_else(|| data.get("delta")))
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .or_else(|| {
            payload
                .get("text")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .map(|text| text.trim().to_string())
        .filter(|text| !text.is_empty())
}

fn compact_bot_terminal_event_payload(
    correlation: &StateMachineDeliveryCorrelation,
    cmd: &HandleBotTerminalEventCommand,
) -> Option<Value> {
    if matches!(cmd.state, ChatEventState::Delta) {
        return None;
    }
    let text_len = extract_text(&cmd.event_payload).map(|text| text.chars().count());
    let payload_state = cmd
        .event_payload
        .get("state")
        .and_then(Value::as_str)
        .map(str::to_string);
    let stream = cmd
        .event_payload
        .get("stream")
        .and_then(Value::as_str)
        .map(str::to_string);
    Some(serde_json::json!({
        "run_id": correlation.state_machine_run_id.clone(),
        "node_id": correlation.node_id.clone(),
        "attempt": correlation.attempt,
        "delivery_request_id": correlation.delivery_request_id.clone(),
        "bot_delivery_run_id": cmd.run_id.clone(),
        "assignee_bot_id": correlation.assignee_bot_id.clone(),
        "bot_id": cmd.bot_id.clone(),
        "source_event_type": cmd.event_type.clone(),
        "state": chat_event_state_slug(&cmd.state),
        "payload_state": payload_state,
        "stream": stream,
        "text_len": text_len,
    }))
}

fn collect_reachable_targets(
    state_machine: &bcs_domain::StateMachineDefinition,
    initial_targets: &[String],
    reachable: &mut HashSet<String>,
) {
    let mut stack = initial_targets.to_vec();
    while let Some(node_id) = stack.pop() {
        if !reachable.insert(node_id.clone()) {
            continue;
        }
        let Some(node) = state_machine.nodes.get(&node_id) else {
            continue;
        };
        for transition in node.transitions.values() {
            for target in &transition.targets {
                stack.push(target.clone());
            }
        }
    }
}

fn normalize_state_machine_event_payload(
    payload: &Value,
    group_id: &str,
    session_id: &str,
    run_id: &str,
    state: &ChatEventState,
) -> Value {
    let mut payload = payload.clone();
    if let Some(object) = payload.as_object_mut() {
        object
            .entry("run_id".to_string())
            .or_insert_with(|| Value::String(run_id.to_string()));
        object
            .entry("bcs_group_id".to_string())
            .or_insert_with(|| Value::String(group_id.to_string()));
        object
            .entry("bcs_session_id".to_string())
            .or_insert_with(|| Value::String(session_id.to_string()));
        object
            .entry("state".to_string())
            .or_insert_with(|| Value::String(chat_event_state_slug(state).to_string()));
    }
    payload
}

fn workbench_event_name<'a>(event_type: &'a str, state: &ChatEventState) -> &'a str {
    match event_type {
        "agent" => "agent",
        "chat" | "chat.event" => match state {
            ChatEventState::ToolCallStart | ChatEventState::ToolCallEnd => "agent",
            ChatEventState::Delta
            | ChatEventState::Final
            | ChatEventState::Error
            | ChatEventState::Aborted => "chat",
        },
        _ => event_type,
    }
}

fn chat_event_state_slug(state: &ChatEventState) -> &'static str {
    match state {
        ChatEventState::Delta => "delta",
        ChatEventState::Final => "final",
        ChatEventState::Aborted => "aborted",
        ChatEventState::Error => "error",
        ChatEventState::ToolCallStart => "tool_call_start",
        ChatEventState::ToolCallEnd => "tool_call_end",
    }
}

fn bot_display_name(group: &Group, bot_id: &str) -> Option<String> {
    group
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == bot_id)
        .and_then(|participant| participant.bot_name.clone())
}

fn state_machine_message_metadata(
    run: &StateMachineRun,
    node: &StateMachineNodeRun,
    event: &str,
) -> Value {
    serde_json::json!({
        "state_machine": {
            "run_id": run.run_id.clone(),
            "definition_id": run.definition_id.clone(),
            "definition_version": run.definition_version,
            "node_id": node.node_id.clone(),
            "attempt": node.attempt,
            "event": event,
            "status": node.status,
            "assignee_bot_id": node.assignee_bot_id.clone(),
            "delivery_request_id": node.delivery_request_id.clone(),
            "bot_delivery_run_id": node.bot_delivery_run_id.clone(),
        }
    })
}

fn state_machine_panel_metadata(run: &StateMachineRun) -> Value {
    serde_json::json!({
        "state_machine": {
            "run_id": run.run_id.clone(),
            "definition_id": run.definition_id.clone(),
            "definition_version": run.definition_version,
            "event": "panel",
            "component": "bcsPanel.StateMachineRunView",
        }
    })
}

fn format_state_machine_panel_message(run_id: &str, session_title: Option<&str>) -> String {
    let title_suffix = session_title
        .map(str::trim)
        .filter(|title| !title.is_empty())
        .unwrap_or("新会话");
    let title = format!("State Machine - {title_suffix}");
    let tab_json = single_quoted_json_attr(format!(
        "{{\"id\":{},\"title\":{},\"closable\":true}}",
        json_string(&format!("state-machine-run-{run_id}")),
        json_string(&title)
    ));
    let params_json = single_quoted_json_attr(format!("{{\"runId\":{}}}", json_string(run_id)));
    format!(
        "<AixUI\n  type=\"panel\"\n  component=\"bcsPanel.StateMachineRunView\"\n  tab='{tab_json}'\n  params='{params_json}'\n/>"
    )
}

fn json_string(value: &str) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "\"\"".to_string())
}

fn single_quoted_json_attr(value: String) -> String {
    value.replace('\'', "\\u0027")
}

fn apply_message_window(
    messages: Vec<GroupMessage>,
    limit: u64,
    before: Option<u64>,
) -> Vec<GroupMessage> {
    messages
        .into_iter()
        .filter(|message| before.map_or(true, |before| message.timestamp < before))
        .take(limit as usize)
        .collect()
}

fn final_output_text(
    compiled: &CompiledStateMachine,
    nodes: &[StateMachineNodeRun],
) -> Option<String> {
    let state_machine = match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => return None,
    };
    let final_node_id = state_machine.nodes.iter().find_map(|(node_id, node)| {
        if node.final_output {
            Some(node_id)
        } else {
            None
        }
    })?;
    nodes
        .iter()
        .find(|node| &node.node_id == final_node_id)
        .and_then(|node| node.artifact_text.clone())
}

fn compiled_node_max_attempts(compiled: &CompiledStateMachine, node_id: &str) -> i32 {
    match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine
            .nodes
            .get(node_id)
            .and_then(|node| node.max_attempts)
            .unwrap_or(state_machine.defaults.max_attempts)
            .max(1),
        _ => 1,
    }
}

fn judge_service_error_message(error: &ServiceError) -> String {
    match error {
        ServiceError::InternalError(message) => message.clone(),
        _ => error.to_string(),
    }
}

fn elapsed_ms(started_at: Instant) -> u64 {
    started_at
        .elapsed()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_judge_timeout_is_ninety_seconds() {
        assert_eq!(DEFAULT_JUDGE_TIMEOUT_MS, 90_000);
    }

    #[test]
    fn authoring_yaml_rejects_top_level_identity() {
        let yaml = r#"
api_version: bcs.collaboration/v1
id: user-owned
name: Bad Definition
runtime:
  kind: chat
"#;
        let err = reject_authoring_yaml_identity(yaml).unwrap_err();
        assert!(matches!(
            err,
            CollaborationRuntimeError::InvalidDefinition(_)
        ));
    }

    #[test]
    fn build_node_runs_treats_zero_timeout_as_disabled() {
        let yaml = r#"
api_version: bcs.collaboration/v1
id: def-timeout
version: 1
name: Timeout Definition
participants:
  worker:
    bot_id: bot-a
runtime:
  kind: state_machine
  state_machine:
    version: 1
    defaults:
      node_timeout_ms: 0
    nodes:
      step:
        kind: bot_task
        display_name: Step
        assignee:
          type: bot_binding
          binding: worker
        instruction: Do the work.
        final_output: true
"#;
        let definition: CollaborationDefinition = serde_yaml::from_str(yaml).unwrap();
        let compiled = validate_definition(definition).unwrap();
        let run = StateMachineRun {
            run_id: "run-timeout-zero".to_string(),
            definition_id: compiled.definition.id.clone(),
            definition_version: compiled.definition.version,
            group_id: "group".to_string(),
            group_version: 1,
            session_id: "session".to_string(),
            created_by: None,
            status: StateMachineRunStatus::Running,
            input: Value::Null,
            output: None,
            error: None,
            created_at: 1,
            updated_at: 1,
            completed_at: None,
        };
        let mut resolved = BTreeMap::new();
        resolved.insert(
            "worker".to_string(),
            ResolvedParticipantBinding {
                source: "definition_legacy_bot_id".to_string(),
                binding_source: None,
                bot_ids: vec!["bot-a".to_string()],
                participants: vec![ResolvedParticipant {
                    bot_id: "bot-a".to_string(),
                    bcs_participant_role: ParticipantRole::Driver,
                }],
                extensions: Default::default(),
            },
        );
        let nodes = build_node_runs(&compiled, &run, &resolved).unwrap();
        assert_eq!(nodes.len(), 1);
        assert_eq!(nodes[0].node_timeout_ms, None);
        assert_eq!(nodes[0].attempt, 0);
    }
}
