use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthConfig, AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_group_store::MemoryGroupRepo;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotGroupListCommand, BotRegistryCoreService,
    CancelStateMachineRunCommand, CollaborationDefinition,
    CollaborationDefinitionGraphNode, CollaborationDefinitionGraphPreview,
    CollaborationDefinitionParticipantSlot, CollaborationDefinitionValidationOutcome,
    CollaborationDefinitionValidationSummary, CollaborationRuntimeError,
    CollaborationRuntimeService, ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    DmCreateCommand, DmCreateResult, Group, GroupAddMemberCommand, GroupAddMemberResult,
    GroupCoreService, GroupCreateCommand, GroupDeleteCommand, GroupDeleteResult,
    GroupDetailCommand, GroupDetailResult, GroupListCommand, GroupListEntry, GroupListResult,
    GroupManagementService, GroupMessage, GroupParticipantModeResult, GroupParticipantView,
    GroupQueryService, GroupRoutingPolicyCommand, GroupRoutingPolicyResult, GroupStatus,
    GroupStatusCommand, GroupTerminateCommand, GroupUpdateLabelCommand, InitialGroupRun,
    InitialGroupRunActivityKind, InitialGroupRunState,
    HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome, HumanResponseSource,
    ListPendingHumanNodesCommand, Participant, ParticipantKind, ParticipantMode, ParticipantRole,
    PendingHumanNodeView, RespondHumanNodeCommand, RespondHumanNodeOutcome, RoutingMode,
    RerunStateMachineCommand, RerunStateMachineOutcome, RoutingPolicy, SessionHistoryResult,
    SessionStateMachinePermissionCommand,
    SessionStateMachinePermissionView, Skill, StartSessionStateMachineRunCommand,
    StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineDeliveryCorrelation,
    StateMachineAssignee, StateMachineGraphMode, StateMachineNodeKind, StateMachineNodeRun,
    StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus, StateMachineRunView,
    ValidateCollaborationDefinitionYamlCommand,
};
use bcs_service_api::types::OpeningMessage;
use bcs_service_api::{
    CreateOrReactivateCommand, NewSessionParams, SessionKind, SessionManagementService,
};
use bcs_services_container::Services;
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};
use serde_json::Value;
use std::sync::Arc;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

const ONE_SHOT_DEFINITION_YAML: &str = r#"
name: One Shot
participants:
  writer:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      write:
        kind: bot_task
        display_name: Write
        assignee:
          type: bot_binding
          binding: writer
        instruction: Write the final answer.
        final_output: true
"#;

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}


#[derive(Default)]
struct RecordingGroupManagement {
    create_calls: Mutex<Vec<GroupCreateCommand>>,
    latest_running_session_id: Mutex<Option<String>>,
    initial_run: Mutex<Option<InitialGroupRun>>,
    create_dm_calls: Mutex<Vec<DmCreateCommand>>,
    status_calls: Mutex<Vec<GroupStatusCommand>>,
    add_member_calls: Mutex<Vec<GroupAddMemberCommand>>,
    delete_calls: Mutex<Vec<GroupDeleteCommand>>,
    terminate_calls: Mutex<Vec<GroupTerminateCommand>>,
    label_calls: Mutex<Vec<GroupUpdateLabelCommand>>,
    routing_policy_calls: Mutex<Vec<GroupRoutingPolicyCommand>>,
    participant_mode_calls: Mutex<Vec<bcs_service_api::GroupParticipantModeCommand>>,
}


#[derive(Default)]
struct RecordingCollaborationRuntime {
    projection_fixture: Mutex<Option<Value>>,
    queried_node_ids: Mutex<Vec<String>>,
    definitions: Mutex<Vec<CollaborationDefinition>>,
    configure_calls: Mutex<Vec<ConfigureGroupRuntimeCommand>>,
    start_commands: Mutex<Vec<StartStateMachineRunCommand>>,
    permission_commands: Mutex<Vec<SessionStateMachinePermissionCommand>>,
    session_start_commands: Mutex<Vec<StartSessionStateMachineRunCommand>>,
    validation_calls: Mutex<Vec<ValidateCollaborationDefinitionYamlCommand>>,
    pending_human_commands: Mutex<Vec<ListPendingHumanNodesCommand>>,
    respond_human_commands: Mutex<Vec<RespondHumanNodeCommand>>,
    rerun_commands: Mutex<Vec<RerunStateMachineCommand>>,
    upsert_error: Mutex<Option<CollaborationRuntimeError>>,
}

#[async_trait::async_trait]
impl CollaborationRuntimeService for RecordingCollaborationRuntime {
    async fn validate_definition_yaml(
        &self,
        cmd: ValidateCollaborationDefinitionYamlCommand,
    ) -> Result<CollaborationDefinitionValidationOutcome, CollaborationRuntimeError> {
        self.validation_calls.lock().await.push(cmd);
        Ok(CollaborationDefinitionValidationOutcome {
            valid: true,
            errors: Vec::new(),
            warnings: Vec::new(),
            summary: CollaborationDefinitionValidationSummary {
                participants: 1,
                nodes: 1,
                initial_nodes: vec!["answer".to_string()],
                final_output_node: Some("answer".to_string()),
            },
            participants: vec![CollaborationDefinitionParticipantSlot {
                binding: "writer".to_string(),
                display_name: Some("Writer".to_string()),
                description: None,
                required: true,
                assigned: true,
            }],
            graph: Some(CollaborationDefinitionGraphPreview {
                loops: Default::default(),
                graph_mode: StateMachineGraphMode::Acyclic,
                execution_graph_mode: None,
                nodes: vec![CollaborationDefinitionGraphNode {
                    node_id: "answer".to_string(),
                    display_name: "Answer".to_string(),
                    kind: StateMachineNodeKind::BotTask,
                    assignee: Some(StateMachineAssignee::BotBinding {
                        binding: "writer".to_string(),
                    }),
                    final_output: true,
                    judge: false,
                    execution: None,
                }],
                edges: Vec::new(),
            }),
            definition: None,
        })
    }

    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.start_commands.lock().await.push(cmd);
        if let Some(fixture) = self.projection_fixture.lock().await.as_ref() {
            return Ok(StartStateMachineRunOutcome { view: serde_json::from_value(fixture["run"].clone()).unwrap() });
        }
        Err(CollaborationRuntimeError::InvalidRequest(
            "unexpected start_state_machine_run call".to_string(),
        ))
    }

    async fn get_session_state_machine_permission(
        &self,
        cmd: SessionStateMachinePermissionCommand,
    ) -> Result<SessionStateMachinePermissionView, CollaborationRuntimeError> {
        self.permission_commands.lock().await.push(cmd.clone());
        Ok(SessionStateMachinePermissionView {
            session_id: cmd.session_id,
            group_id: "group-1".to_string(),
            caller_bot_id: cmd.caller_bot_id,
            allowed: true,
            reason_code: "allowed".to_string(),
            message: "caller may run a state machine in this session".to_string(),
            policy_version: "session_state_machine_v1".to_string(),
            group_strategy: "chat".to_string(),
            group_owner_bot_id: "driver-bot".to_string(),
            active_run_id: None,
        })
    }

    async fn start_session_state_machine_run(
        &self,
        cmd: StartSessionStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.session_start_commands.lock().await.push(cmd.clone());
        if let Some(fixture) = self.projection_fixture.lock().await.as_ref() {
            return Ok(StartStateMachineRunOutcome { view: serde_json::from_value(fixture["run"].clone()).unwrap() });
        }
        Ok(StartStateMachineRunOutcome {
            view: StateMachineRunView {
                run: StateMachineRun {
                    run_id: "run-one-shot".to_string(),
                    root_run_id: Some("run-one-shot".to_string()),
                    rerun_of: None,
                    definition_id: "definition-one-shot".to_string(),
                    definition_version: 1,
                    group_id: "group-1".to_string(),
                    group_version: 1,
                    session_id: cmd.session_id,
                    session_activation_count: None,
                    created_by: Some(cmd.caller_bot_id),
                    status: StateMachineRunStatus::Running,
                    input: cmd.input,
                    opening_message_override: cmd.opening_message,
                    output: None,
                    error: None,
                    created_at: 1,
                    updated_at: 1,
                    completed_at: None,
                },
                nodes: Vec::new(),
                node_execution_metadata: None,
                judge_outputs: Vec::new(),
            },
        })
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(self.projection_fixture.lock().await.as_ref().map(|fixture| serde_json::from_value(fixture["run"].clone()).unwrap()))
    }

    async fn get_state_machine_run_with_access(&self, cmd: bcs_service_api::StateMachineRunAccessCommand) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        self.get_state_machine_run(&cmd.run_id).await
    }

    async fn get_state_machine_run_graph_with_access(&self, _cmd: bcs_service_api::StateMachineRunAccessCommand) -> Result<Option<bcs_service_api::StateMachineRunGraphView>, CollaborationRuntimeError> {
        Ok(self.projection_fixture.lock().await.as_ref().map(|fixture| serde_json::from_value(fixture["graph"].clone()).unwrap()))
    }

    async fn get_state_machine_node_run_with_access(&self, _cmd: bcs_service_api::StateMachineRunAccessCommand, node_id: &str) -> Result<Option<bcs_service_api::StateMachineNodeRunView>, CollaborationRuntimeError> {
        self.queried_node_ids.lock().await.push(node_id.into());
        Ok(self.projection_fixture.lock().await.as_ref().map(|fixture| serde_json::from_value(fixture["node"].clone()).unwrap()))
    }

    async fn rerun_state_machine_run(
        &self,
        cmd: RerunStateMachineCommand,
    ) -> Result<RerunStateMachineOutcome, CollaborationRuntimeError> {
        self.rerun_commands.lock().await.push(cmd.clone());
        if let Some(fixture) = self.projection_fixture.lock().await.as_ref() {
            return Ok(RerunStateMachineOutcome { view: serde_json::from_value(fixture["run"].clone()).unwrap(), created: true });
        }
        Ok(RerunStateMachineOutcome {
            view: StateMachineRunView {
                run: StateMachineRun {
                    run_id: "run-2".to_string(),
                    root_run_id: Some(cmd.source_run_id.clone()),
                    rerun_of: Some(cmd.source_run_id),
                    definition_id: "definition-1".to_string(),
                    definition_version: 1,
                    group_id: "group-1".to_string(),
                    group_version: 1,
                    session_id: "session-1".to_string(),
                    session_activation_count: None,
                    created_by: Some("human_alice".to_string()),
                    status: StateMachineRunStatus::Running,
                    input: Value::Null,
                    opening_message_override: None,
                    output: None,
                    error: None,
                    created_at: 2,
                    updated_at: 2,
                    completed_at: None,
                },
                nodes: Vec::new(),
                node_execution_metadata: None,
                judge_outputs: Vec::new(),
            },
            created: true,
        })
    }

    async fn list_pending_human_nodes(
        &self,
        cmd: ListPendingHumanNodesCommand,
    ) -> Result<Vec<PendingHumanNodeView>, CollaborationRuntimeError> {
        self.pending_human_commands.lock().await.push(cmd);
        if let Some(fixture) = self.projection_fixture.lock().await.as_ref() {
            return Ok(serde_json::from_value(fixture["pending_later"].clone()).unwrap());
        }
        Ok(vec![PendingHumanNodeView {
            node_id: "human_review".to_string(),
            execution: None,
            display_name: "Human review".to_string(),
            instruction: "Review the draft".to_string(),
            response_ref: "run-1:human_review".to_string(),
            judge_outcomes: vec!["approve".to_string(), "reject".to_string()],
            timeout_deadline_ms: Some(1234),
            loop_context: None,
            upstream_artifacts: Vec::new(),
        }])
    }

    async fn respond_human_node(
        &self,
        cmd: RespondHumanNodeCommand,
    ) -> Result<RespondHumanNodeOutcome, CollaborationRuntimeError> {
        self.respond_human_commands.lock().await.push(cmd.clone());
        Ok(RespondHumanNodeOutcome {
            node: StateMachineNodeRun {
                run_id: cmd.run_id.clone(),
                node_id: cmd.node_id,
                status: StateMachineNodeStatus::Completed,
                attempt: 0,
                node_timeout_ms: Some(60_000),
                timeout_deadline_ms: None,
                max_attempts: 1,
                assignee_bot_id: None,
                outcome: Some("complete".to_string()),
                responded_by: Some(cmd.caller_actor_id),
                delivery_request_id: None,
                bot_delivery_run_id: None,
                artifact_text: Some(cmd.content),
                error: None,
                started_at: Some(1),
                completed_at: Some(2),
            },
            run: StateMachineRun {
                root_run_id: Some(cmd.run_id.clone()),
                run_id: cmd.run_id,
                rerun_of: None,
                definition_id: "definition-1".to_string(),
                definition_version: 1,
                group_id: "group-1".to_string(),
                group_version: 1,
                session_id: "session-1".to_string(),
                session_activation_count: None,
                created_by: Some("human_alice".to_string()),
                status: StateMachineRunStatus::Completed,
                input: Value::Null,
                opening_message_override: None,
                output: None,
                error: None,
                created_at: 1,
                updated_at: 2,
                completed_at: Some(2),
            },
        })
    }

    async fn get_state_machine_session_history(
        &self,
        _session_id: &str,
        _limit: u64,
        _before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn cancel_state_machine_run(
        &self,
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::RunNotFound(cmd.run_id))
    }

    async fn lookup_delivery_correlation(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn register_delivery_alias(
        &self,
        _delivery_request_id: &str,
        _bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn handle_bot_terminal_event(
        &self,
        _cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        Ok(HandleBotTerminalEventOutcome {
            consumed: false,
            view: None,
        })
    }

    async fn upsert_definition(
        &self,
        definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        if let Some(error) = self.upsert_error.lock().await.take() {
            return Err(error);
        }
        self.definitions.lock().await.push(definition);
        Ok(())
    }

    async fn configure_group_runtime(
        &self,
        cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        self.configure_calls.lock().await.push(cmd.clone());
        Ok(ConfigureGroupRuntimeOutcome {
            group_id: cmd.group_id,
            default_definition: cmd.definition_ref,
            auto_start_on_service_invocation: cmd.auto_start_on_service_invocation,
            requires_human_input_channel: false,
        })
    }
}


#[async_trait::async_trait]
impl GroupManagementService for RecordingGroupManagement {
    async fn create_group(
        &self,
        cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, bcs_service_api::GroupUseCaseError> {
        let mut result = detail_from_create(&cmd);
        result.latest_running_session_id = self.latest_running_session_id.lock().await.clone();
        result.initial_run = self.initial_run.lock().await.clone();
        self.create_calls.lock().await.push(cmd);
        Ok(result)
    }

    async fn create_dm(
        &self,
        cmd: DmCreateCommand,
    ) -> Result<DmCreateResult, bcs_service_api::GroupUseCaseError> {
        let mut participants = Vec::new();
        if let Some(caller) = cmd.caller_actor_id.as_deref() {
            participants.push(participant_view(caller, "observer"));
        }
        participants.push(participant_view(&cmd.target_actor_id, "driver"));
        let result = GroupDetailResult {
            group_id: cmd
                .group_id
                .clone()
                .unwrap_or_else(|| "generated-dm".to_string()),
            label: cmd.label.clone(),
            status: GroupStatus::Active,
            driver_bot_id: cmd.target_actor_id.clone(),
            originator: cmd.caller_actor_id.clone(),
            context: cmd.context.clone(),
            opening_message: None,
            participants,
            message_count: 0,
            service_group_uuid: None,
            service_mode: None,
            group_kind: bcs_service_api::GroupKind::Dm,
            dm_pair_key: Some(format!("human_test|{}", cmd.target_actor_id)),
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 10,
            chat_url: None,
            context_injected: 0,
            latest_running_session_id: None,
            initial_run: None,
            visibility: "private".to_string(),
            human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
        };
        self.create_dm_calls.lock().await.push(cmd);
        Ok(DmCreateResult {
            group: result,
            created: true,
        })
    }

    async fn update_status(
        &self,
        cmd: GroupStatusCommand,
    ) -> Result<GroupDetailResult, bcs_service_api::GroupUseCaseError> {
        let status = match cmd.status.as_str() {
            "completed" => GroupStatus::Completed,
            "closed" => GroupStatus::Closed,
            "inactive" => GroupStatus::Inactive,
            "error" => GroupStatus::Error,
            _ => GroupStatus::Active,
        };
        let result = GroupDetailResult {
            group_id: cmd.group_id.clone(),
            label: Some("Updated group".to_string()),
            status,
            driver_bot_id: cmd
                .caller_actor_id
                .clone()
                .unwrap_or_else(|| "driver-bot".to_string()),
            originator: cmd.caller_actor_id.clone(),
            context: None,
            opening_message: None,
            participants: vec![participant_view(
                cmd.caller_actor_id.as_deref().unwrap_or("driver-bot"),
                "driver",
            )],
            message_count: 0,
            service_group_uuid: None,
            service_mode: None,
            group_kind: Default::default(),
            dm_pair_key: None,
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 20,
            chat_url: None,
            context_injected: 0,
            latest_running_session_id: None,
            initial_run: None,
            visibility: "private".to_string(),
            human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
        };
        self.status_calls.lock().await.push(cmd);
        Ok(result)
    }

    async fn add_member(
        &self,
        cmd: GroupAddMemberCommand,
    ) -> Result<GroupAddMemberResult, bcs_service_api::GroupUseCaseError> {
        let result = GroupAddMemberResult {
            group_id: cmd.group_id.clone(),
            member: participant_view(&cmd.bot_id, "consultant"),
        };
        self.add_member_calls.lock().await.push(cmd);
        Ok(result)
    }

    async fn remove_member(
        &self,
        cmd: bcs_service_api::GroupRemoveMemberCommand,
    ) -> Result<bcs_service_api::GroupRemoveMemberResult, bcs_service_api::GroupUseCaseError> {
        Ok(bcs_service_api::GroupRemoveMemberResult {
            group_id: cmd.group_id,
            removed_bot_uuid: cmd.bot_id,
        })
    }

    async fn delete_group(
        &self,
        cmd: GroupDeleteCommand,
    ) -> Result<GroupDeleteResult, bcs_service_api::GroupUseCaseError> {
        let result = GroupDeleteResult {
            group_id: cmd.group_id.clone(),
            deleted: true,
        };
        self.delete_calls.lock().await.push(cmd);
        Ok(result)
    }

    async fn terminate_group(
        &self,
        cmd: GroupTerminateCommand,
    ) -> Result<GroupDetailResult, bcs_service_api::GroupUseCaseError> {
        let result = GroupDetailResult {
            group_id: cmd.group_id.clone(),
            label: Some("Terminated group".to_string()),
            status: GroupStatus::Completed,
            driver_bot_id: cmd.caller_actor_id.clone(),
            originator: Some(cmd.caller_actor_id.clone()),
            context: None,
            opening_message: None,
            participants: vec![participant_view(&cmd.caller_actor_id, "driver")],
            message_count: 0,
            service_group_uuid: None,
            service_mode: None,
            group_kind: Default::default(),
            dm_pair_key: None,
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 30,
            chat_url: None,
            context_injected: 0,
            latest_running_session_id: None,
            initial_run: None,
            visibility: "private".to_string(),
            human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
        };
        self.terminate_calls.lock().await.push(cmd);
        Ok(result)
    }

    async fn update_label(
        &self,
        cmd: GroupUpdateLabelCommand,
    ) -> Result<GroupDetailResult, bcs_service_api::GroupUseCaseError> {
        let result = GroupDetailResult {
            group_id: cmd.group_id.clone(),
            label: cmd.label.clone(),
            status: GroupStatus::Active,
            driver_bot_id: cmd.caller_actor_id.clone(),
            originator: Some(cmd.caller_actor_id.clone()),
            context: None,
            opening_message: None,
            participants: vec![participant_view(&cmd.caller_actor_id, "driver")],
            message_count: 0,
            service_group_uuid: None,
            service_mode: None,
            group_kind: Default::default(),
            dm_pair_key: None,
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 20,
            chat_url: None,
            context_injected: 0,
            latest_running_session_id: None,
            initial_run: None,
            visibility: "private".to_string(),
            human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
        };
        self.label_calls.lock().await.push(cmd);
        Ok(result)
    }

    async fn update_visibility(
        &self,
        _cmd: bcs_service_api::GroupUpdateVisibilityCommand,
    ) -> Result<GroupDetailResult, bcs_service_api::GroupUseCaseError> {
        Err(bcs_service_api::GroupUseCaseError::InvalidProposal(
            "not yet implemented".to_string(),
        ))
    }

    async fn update_routing_policy(
        &self,
        cmd: GroupRoutingPolicyCommand,
    ) -> Result<GroupRoutingPolicyResult, bcs_service_api::GroupUseCaseError> {
        let routing_policy = RoutingPolicy {
            mode: cmd.mode.unwrap_or_default(),
            default_bot_final_delivery: cmd.default_bot_final_delivery.unwrap_or_default(),
            sender_routes: cmd.sender_routes.clone().unwrap_or_default(),
        };
        let result = GroupRoutingPolicyResult {
            group_id: cmd.group_id.clone(),
            routing_policy,
        };
        self.routing_policy_calls.lock().await.push(cmd);
        Ok(result)
    }

    async fn update_participant_mode(
        &self,
        cmd: bcs_service_api::GroupParticipantModeCommand,
    ) -> Result<GroupParticipantModeResult, bcs_service_api::GroupUseCaseError> {
        let result = GroupParticipantModeResult {
            group_id: cmd.group_id.clone(),
            actor_id: cmd.actor_id.clone(),
            mode: cmd.mode,
            message_view_scope: cmd.message_view_scope.unwrap_or_default(),
        };
        self.participant_mode_calls.lock().await.push(cmd);
        Ok(result)
    }

}























async fn test_app() -> (axum::Router, Arc<RecordingGroupManagement>, TempDir) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    register_bot(&registry, "target-bot", "Target").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    group_store
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![
                Participant {
                    bot_uuid: "driver-bot".to_string(),
                    bot_name: Some("Driver".to_string()),
                    kind: None,
                    role: ParticipantRole::Driver,
                    actor_kind: ActorKind::Bot,
                    mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
                    tags: Vec::new(),
                    message_view_scope: bcs_domain::MessageViewScope::Full,
                },
                Participant {
                    bot_uuid: "target-bot".to_string(),
                    bot_name: Some("Target".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: ActorKind::Bot,
                    mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
                    tags: Vec::new(),
                    message_view_scope: bcs_domain::MessageViewScope::Full,
                },
            ],
        ))
        .await
        .unwrap();

    let recorder = Arc::new(RecordingGroupManagement::default());
    let services = Services::builder()
        .registry(registry)
        .group(group_store)
        .group_management(recorder.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));
    (app, recorder, temp_dir)
}

async fn test_app_with_collaboration_runtime() -> (
    axum::Router,
    Arc<RecordingGroupManagement>,
    Arc<RecordingCollaborationRuntime>,
    TempDir,
) {
    test_app_with_collaboration_runtime_identity(false).await
}

async fn test_app_with_collaboration_runtime_and_human_identity() -> (
    axum::Router,
    Arc<RecordingGroupManagement>,
    Arc<RecordingCollaborationRuntime>,
    TempDir,
) {
    test_app_with_collaboration_runtime_identity(true).await
}

async fn test_app_with_collaboration_runtime_identity(
    with_human_identity: bool,
) -> (
    axum::Router,
    Arc<RecordingGroupManagement>,
    Arc<RecordingCollaborationRuntime>,
    TempDir,
) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    register_bot(&registry, "target-bot", "Target").await;
    register_bot(&registry, "observer-bot", "Observer").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    let recorder = Arc::new(RecordingGroupManagement::default());
    let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::default());
    let services = Services::builder()
        .registry(registry)
        .group(group_store)
        .group_management(recorder.clone())
        .collaboration_runtime(collaboration_runtime.clone())
        .build_for_test();
    let state = HttpAppState::new(services);
    let state = if with_human_identity {
        state.with_user_identity(Arc::new(ChainUserIdentityPort::new(static_auth_chain(
            "alice", "Alice",
        ))))
    } else {
        state
    };
    let app = build_router(state);
    (app, recorder, collaboration_runtime, temp_dir)
}

async fn register_bot(registry: &BotCore, bot_id: &str, name: &str) {
    registry
        .register(
            bot_id.to_string(),
            BotCapabilities {
                name: Some(name.to_string()),
                summary: Some(format!("{name} summary")),
                skills: vec![Skill::new("help")],
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
}

fn detail_from_create(cmd: &GroupCreateCommand) -> GroupDetailResult {
    GroupDetailResult {
        group_id: cmd
            .group_id
            .clone()
            .unwrap_or_else(|| "generated-group".to_string()),
        label: cmd.label.clone(),
        status: GroupStatus::Active,
        driver_bot_id: cmd.driver_bot_id.clone(),
        originator: cmd.originator.clone(),
        context: cmd.context.clone(),
        opening_message: cmd.opening_message.clone(),
        participants: cmd
            .participants
            .iter()
            .map(|participant| {
                participant_view(
                    &participant.bot_id,
                    participant.role.as_deref().unwrap_or("consultant"),
                )
            })
            .collect(),
        message_count: 0,
        service_group_uuid: None,
        service_mode: None,
        group_kind: Default::default(),
        dm_pair_key: None,
        group_strategy: cmd.group_strategy.unwrap_or_default(),
        created_at: 10,
        updated_at: 10,
        chat_url: None,
        context_injected: 0,
        latest_running_session_id: None,
        initial_run: None,
        visibility: "private".to_string(),
        human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
    }
}

fn participant_view(bot_id: &str, role: &str) -> GroupParticipantView {
    GroupParticipantView {
        bot_uuid: bot_id.to_string(),
        bot_name: Some(bot_id.to_string()),
        kind: Some(ParticipantKind::Bot),
        role: role.to_string(),
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    }
}

fn group_list_entry(group_id: &str) -> GroupListEntry {
    GroupListEntry {
        group_id: group_id.to_string(),
        label: Some("Query group".to_string()),
        context: None,
        driver_bot_id: "driver-bot".to_string(),
        originator: Some("driver-bot".to_string()),
        participants: vec![participant_view("driver-bot", "driver")],
        participant_count: 1,
        message_count: 0,
        created_at: 10,
        updated_at: 20,
        group_kind: Default::default(),
        group_strategy: Default::default(),
        visibility: "private".to_string(),
        human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
    }
}




#[path = "groups_contract/creation_success.rs"]
mod creation_success;
#[path = "groups_contract/creation_failure.rs"]
mod creation_failure;
#[path = "groups_contract/membership.rs"]
mod membership;
#[path = "groups_contract/metadata.rs"]
mod metadata;
#[path = "groups_contract/visibility.rs"]
mod visibility;
