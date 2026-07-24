use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthConfig, AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotGroupListCommand, BotRegistryCoreService,
    CancelStateMachineRunCommand, CollaborationDefinition,
    CollaborationDefinitionParticipantSlot, CollaborationDefinitionValidationOutcome,
    CollaborationDefinitionValidationSummary, CollaborationRuntimeError,
    CollaborationRuntimeService, ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    DmCreateCommand, DmCreateResult, Group, GroupAddMemberCommand, GroupAddMemberResult,
    GroupCoreService, GroupCreateCommand, GroupDeleteCommand, GroupDeleteResult,
    GroupDetailCommand, GroupDetailResult, GroupListCommand, GroupListEntry, GroupListResult,
    GroupManagementService, GroupMessage, GroupParticipantModeResult, GroupParticipantView,
    GroupQueryService, GroupRoutingPolicyCommand, GroupRoutingPolicyResult, GroupStatus,
    GroupStatusCommand, GroupTerminateCommand, GroupUpdateLabelCommand,
    GroupUpdateWorkspaceCommand, GroupWorkspaceQueryCommand, GroupWorkspaceResult,
    HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome, HumanResponseSource,
    ListPendingHumanNodesCommand, Participant, ParticipantKind, ParticipantMode, ParticipantRole,
    PendingHumanNodeView, RespondHumanNodeCommand, RespondHumanNodeOutcome, RoutingMode,
    RoutingPolicy, SessionHistoryResult, Skill, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineDeliveryCorrelation, StateMachineNodeRun,
    StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus, StateMachineRunView, Workspace,
    ValidateCollaborationDefinitionYamlCommand,
};
use bcs_services_container::Services;
use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};
use serde_json::Value;
use std::sync::Arc;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

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

fn static_bot_auth_chain(bot_uuid: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        bot_uuid: Some(bot_uuid.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(principal))]))
}

#[derive(Default)]
struct RecordingGroupManagement {
    create_calls: Mutex<Vec<GroupCreateCommand>>,
    create_dm_calls: Mutex<Vec<DmCreateCommand>>,
    status_calls: Mutex<Vec<GroupStatusCommand>>,
    add_member_calls: Mutex<Vec<GroupAddMemberCommand>>,
    delete_calls: Mutex<Vec<GroupDeleteCommand>>,
    terminate_calls: Mutex<Vec<GroupTerminateCommand>>,
    label_calls: Mutex<Vec<GroupUpdateLabelCommand>>,
    workspace_calls: Mutex<Vec<GroupUpdateWorkspaceCommand>>,
    routing_policy_calls: Mutex<Vec<GroupRoutingPolicyCommand>>,
    participant_mode_calls: Mutex<Vec<bcs_service_api::GroupParticipantModeCommand>>,
}

#[derive(Default)]
struct RecordingGroupQuery {
    list_calls: Mutex<Vec<GroupListCommand>>,
    detail_calls: Mutex<Vec<GroupDetailCommand>>,
    bot_group_calls: Mutex<Vec<BotGroupListCommand>>,
    workspace_calls: Mutex<Vec<GroupWorkspaceQueryCommand>>,
}

#[derive(Default)]
struct RecordingCollaborationRuntime {
    definitions: Mutex<Vec<CollaborationDefinition>>,
    configure_calls: Mutex<Vec<ConfigureGroupRuntimeCommand>>,
    start_commands: Mutex<Vec<StartStateMachineRunCommand>>,
    validation_calls: Mutex<Vec<ValidateCollaborationDefinitionYamlCommand>>,
    pending_human_commands: Mutex<Vec<ListPendingHumanNodesCommand>>,
    respond_human_commands: Mutex<Vec<RespondHumanNodeCommand>>,
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
            definition: None,
        })
    }

    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.start_commands.lock().await.push(cmd);
        Err(CollaborationRuntimeError::InvalidRequest(
            "unexpected start_state_machine_run call".to_string(),
        ))
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn list_pending_human_nodes(
        &self,
        cmd: ListPendingHumanNodesCommand,
    ) -> Result<Vec<PendingHumanNodeView>, CollaborationRuntimeError> {
        self.pending_human_commands.lock().await.push(cmd);
        Ok(vec![PendingHumanNodeView {
            node_id: "human_review".to_string(),
            display_name: "Human review".to_string(),
            instruction: "Review the draft".to_string(),
            response_ref: "run-1:human_review".to_string(),
            judge_outcomes: vec!["approve".to_string(), "reject".to_string()],
            timeout_deadline_ms: Some(1234),
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
                run_id: cmd.run_id,
                definition_id: "definition-1".to_string(),
                definition_version: 1,
                group_id: "group-1".to_string(),
                group_version: 1,
                session_id: "session-1".to_string(),
                created_by: Some("human_alice".to_string()),
                status: StateMachineRunStatus::Completed,
                input: Value::Null,
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
        })
    }
}

#[async_trait::async_trait]
impl GroupQueryService for RecordingGroupQuery {
    async fn list_groups(
        &self,
        cmd: GroupListCommand,
    ) -> Result<GroupListResult, bcs_service_api::GroupUseCaseError> {
        self.list_calls.lock().await.push(cmd.clone());
        Ok(GroupListResult {
            items: vec![group_list_entry("group-1")],
            total: 1,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }

    async fn get_group(
        &self,
        cmd: GroupDetailCommand,
    ) -> Result<GroupDetailResult, bcs_service_api::GroupUseCaseError> {
        self.detail_calls.lock().await.push(cmd.clone());
        Ok(GroupDetailResult {
            group_id: cmd.group_id,
            label: Some("Queried group".to_string()),
            status: GroupStatus::Active,
            driver_bot_id: "driver-bot".to_string(),
            originator: None,
            context: Some("Shared context".to_string()),
            participants: vec![participant_view("driver-bot", "driver")],
            message_count: 0,
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            group_kind: Default::default(),
            dm_pair_key: None,
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 20,
            chat_url: None,
            context_injected: 0,
            service_spec: None,
            latest_running_session_id: None,
            visibility: "private".to_string(),
        })
    }

    async fn list_bot_groups(
        &self,
        cmd: BotGroupListCommand,
    ) -> Result<GroupListResult, bcs_service_api::GroupUseCaseError> {
        self.bot_group_calls.lock().await.push(cmd.clone());
        Ok(GroupListResult {
            items: vec![group_list_entry("bot-group-1")],
            total: 1,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }

    async fn get_workspace(
        &self,
        cmd: GroupWorkspaceQueryCommand,
    ) -> Result<GroupWorkspaceResult, bcs_service_api::GroupUseCaseError> {
        self.workspace_calls.lock().await.push(cmd.clone());
        Ok(GroupWorkspaceResult {
            group_id: cmd.group_id,
            workspace: Workspace {
                decisions: vec!["use query service".to_string()],
                ..Workspace::default()
            },
        })
    }
}

#[async_trait::async_trait]
impl GroupManagementService for RecordingGroupManagement {
    async fn create_group(
        &self,
        cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, bcs_service_api::GroupUseCaseError> {
        let result = detail_from_create(&cmd);
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
            participants,
            message_count: 0,
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            group_kind: bcs_service_api::GroupKind::Dm,
            dm_pair_key: Some(format!("human_test|{}", cmd.target_actor_id)),
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 10,
            chat_url: None,
            context_injected: 0,
            service_spec: None,
            latest_running_session_id: None,
            visibility: "private".to_string(),
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
            participants: vec![participant_view(
                cmd.caller_actor_id.as_deref().unwrap_or("driver-bot"),
                "driver",
            )],
            message_count: 0,
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            group_kind: Default::default(),
            dm_pair_key: None,
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 20,
            chat_url: None,
            context_injected: 0,
            service_spec: None,
            latest_running_session_id: None,
            visibility: "private".to_string(),
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
            member: participant_view(&cmd.bot_id, cmd.role.as_deref().unwrap_or("consultant")),
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
            participants: vec![participant_view(&cmd.caller_actor_id, "driver")],
            message_count: 0,
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            group_kind: Default::default(),
            dm_pair_key: None,
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 30,
            chat_url: None,
            context_injected: 0,
            service_spec: None,
            latest_running_session_id: None,
            visibility: "private".to_string(),
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
            participants: vec![participant_view(&cmd.caller_actor_id, "driver")],
            message_count: 0,
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            group_kind: Default::default(),
            dm_pair_key: None,
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 20,
            chat_url: None,
            context_injected: 0,
            service_spec: None,
            latest_running_session_id: None,
            visibility: "private".to_string(),
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

    async fn update_workspace(
        &self,
        cmd: GroupUpdateWorkspaceCommand,
    ) -> Result<GroupWorkspaceResult, bcs_service_api::GroupUseCaseError> {
        let result = GroupWorkspaceResult {
            group_id: cmd.group_id.clone(),
            workspace: cmd.workspace.clone(),
        };
        self.workspace_calls.lock().await.push(cmd);
        Ok(result)
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
        };
        self.participant_mode_calls.lock().await.push(cmd);
        Ok(result)
    }

    async fn patch_group_settings(
        &self,
        _: bcs_service_api::GroupPatchSettingsCommand,
    ) -> Result<bcs_service_api::GroupPatchSettingsResult, bcs_service_api::GroupUseCaseError> {
        Err(bcs_service_api::GroupUseCaseError::Service(
            bcs_service_api::ServiceError::InternalError(
                "patch_group_settings not supported by test recorder".to_string(),
            ),
        ))
    }
}

#[tokio::test]
async fn post_collaboration_definition_validate_delegates_to_runtime_service() {
    let (app, _, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/definitions/validate")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "definition_yaml": "name: test"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["valid"], true);
    assert_eq!(json["summary"]["nodes"], 1);
    assert_eq!(json["participants"][0]["binding"], "writer");
    assert!(json.get("definition").is_none());

    let calls = collaboration_runtime.validation_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].definition_yaml, "name: test");
    assert!(!calls[0].judge_available);
}

#[tokio::test]
async fn post_groups_delegates_to_group_management_create_and_preserves_response_shape() {
    let (app, recorder, _temp_dir) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-1",
                        "driver_bot": "driver-bot",
                        "label": "Launch room",
                        "topic": "Launch readiness",
                        "context": "Coordinate the release",
                        "routing_policy": {
                            "mode": "structured",
                            "default_bot_final_delivery": "inject_observers",
                            "sender_routes": {
                                "driver-bot": ["target-bot"]
                            }
                        },
                        "participants": [
                            { "bot_uuid": "driver-bot", "role": "driver" },
                            { "bot_uuid": "target-bot", "role": "consultant" }
                        ]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "group-1");
    assert_eq!(json["driver_bot"], "driver-bot");
    assert_eq!(
        json["participants"],
        serde_json::json!(["driver-bot", "target-bot"])
    );
    assert_eq!(json["context"], "Coordinate the release");
    assert_eq!(json["created"], true);
    assert!(json.get("scene_group_id").is_none());
    assert!(json.get("scene_group_name").is_none());

    let calls = recorder.create_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("driver-bot"));
    assert_eq!(cmd.group_id.as_deref(), Some("group-1"));
    assert_eq!(cmd.driver_bot_id, "driver-bot");
    assert_eq!(cmd.label.as_deref(), Some("Launch room"));
    assert_eq!(cmd.topic.as_deref(), Some("Launch readiness"));
    assert_eq!(cmd.context.as_deref(), Some("Coordinate the release"));
    assert_eq!(
        cmd.participants
            .iter()
            .map(|p| (p.bot_id.as_str(), p.role.as_deref()))
            .collect::<Vec<_>>(),
        vec![
            ("driver-bot", Some("driver")),
            ("target-bot", Some("consultant"))
        ]
    );
    assert_eq!(cmd.member_bot_ids, vec!["driver-bot", "target-bot"]);
    let policy = cmd.routing_policy.as_ref().expect("routing policy");
    assert_eq!(policy.mode, RoutingMode::Structured);
    assert_eq!(
        policy.sender_routes.get("driver-bot"),
        Some(&vec!["target-bot".to_string()])
    );
}

#[tokio::test]
async fn post_groups_with_state_machine_yaml_infers_participants_when_omitted() {
    let (app, recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: SM From YAML Participants
participants:
  speaker_a:
    required: true
  speaker_b:
    required: true
  watcher:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      speak_a:
        kind: bot_task
        display_name: Speak A
        assignee:
          type: bot_binding
          binding: speaker_a
        instruction: Say A.
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-yaml",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "auto_start_on_service_invocation": true,
                        "participant_bindings": {
                            "speaker_a": {
                                "source": "manual",
                                "bot_ids": ["driver-bot"]
                            },
                            "speaker_b": {
                                "source": "manual",
                                "bot_ids": ["target-bot"]
                            },
                            "watcher": {
                                "source": "manual",
                                "bot_ids": ["observer-bot"]
                            }
                        },
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = recorder.create_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.group_id.as_deref(), Some("group-sm-yaml"));
    assert_eq!(cmd.driver_bot_id, "driver-bot");
    assert_eq!(
        cmd.participants
            .iter()
            .map(|p| (p.bot_id.as_str(), p.role.as_deref()))
            .collect::<Vec<_>>(),
        vec![
            ("driver-bot", Some("driver")),
            ("target-bot", Some("consultant")),
            ("observer-bot", Some("consultant")),
        ]
    );
    assert_eq!(
        cmd.member_bot_ids,
        vec!["driver-bot", "target-bot", "observer-bot"]
    );
    assert_eq!(
        cmd.group_strategy,
        Some(bcs_service_api::GroupStrategy::StateMachine)
    );

    let definitions = collaboration_runtime.definitions.lock().await;
    assert_eq!(definitions.len(), 1);
    // The authoring YAML must not carry a top-level id (rejected by
    // `reject_authoring_yaml_identity`); the server assigns the definition id.
    assert!(!definitions[0].id.is_empty());
    drop(definitions);

    let configure_calls = collaboration_runtime.configure_calls.lock().await;
    assert_eq!(configure_calls.len(), 1);
    assert_eq!(configure_calls[0].group_id, "group-sm-yaml");
    assert_eq!(
        configure_calls[0]
            .definition_ref
            .as_ref()
            .map(|r| (!r.id.is_empty(), r.version)),
        Some((true, 1))
    );
    assert!(configure_calls[0].auto_start_on_service_invocation);
    assert_eq!(
        configure_calls[0]
            .participant_bindings
            .get("speaker_b")
            .map(|binding| (binding.source.as_str(), binding.bot_ids.as_slice())),
        Some(("manual", &["target-bot".to_string()][..]))
    );
}

#[tokio::test]
async fn post_groups_with_state_machine_yaml_rejects_judge_when_llm_disabled() {
    let (app, recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Judge Disabled
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
        instruction: Write.
        transitions:
          approved:
            targets: []
        judge:
          type: llm
          criteria:
            - The answer is complete.
          outcomes:
            - approved
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-judge-disabled",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "participant_bindings": {
                            "writer": {
                                "source": "manual",
                                "bot_ids": ["driver-bot"]
                            }
                        },
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body = String::from_utf8(body.to_vec()).unwrap();
    assert!(body.contains("state-machine judge requires llm.type to select an LLM provider"));
    assert!(recorder.create_calls.lock().await.is_empty());
    assert!(collaboration_runtime.definitions.lock().await.is_empty());
    assert!(
        collaboration_runtime
            .configure_calls
            .lock()
            .await
            .is_empty()
    );
}

#[tokio::test]
async fn start_state_machine_run_rejects_inline_judge_yaml_when_llm_disabled() {
    let (app, _recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime_and_human_identity().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
id: sm_inline_judge_disabled
version: 1
name: Inline Judge Disabled
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
        instruction: Write.
        transitions:
          approved:
            targets: []
        judge:
          type: llm
          criteria:
            - The answer is complete.
          outcomes:
            - approved
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-sm-judge-disabled/state-machine-runs")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "definition_yaml": definition_yaml,
                        "input": {"question": "hello"}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body = String::from_utf8(body.to_vec()).unwrap();
    assert!(body.contains("state-machine judge requires llm.type to select an LLM provider"));
    assert!(collaboration_runtime.start_commands.lock().await.is_empty());
}

#[tokio::test]
async fn human_state_machine_routes_forward_authenticated_actor_and_output() {
    let (app, _recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime_and_human_identity().await;

    let pending_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/state-machine-runs/run-1/pending-human-nodes")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(pending_response.status(), StatusCode::OK);
    let pending_body = to_bytes(pending_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let pending_json: Value = serde_json::from_slice(&pending_body).unwrap();
    assert_eq!(pending_json[0]["node_id"], "human_review");
    assert!(pending_json[0].get("response_state").is_none());

    let respond_response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/state-machine-runs/run-1/nodes/human_review/respond")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"content":"looks good"}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(respond_response.status(), StatusCode::OK);

    let pending_commands = collaboration_runtime.pending_human_commands.lock().await;
    assert_eq!(pending_commands.len(), 1);
    assert_eq!(pending_commands[0].run_id, "run-1");
    assert_eq!(pending_commands[0].caller_actor_id, "human_alice");
    drop(pending_commands);
    let respond_commands = collaboration_runtime.respond_human_commands.lock().await;
    assert_eq!(respond_commands.len(), 1);
    assert_eq!(respond_commands[0].run_id, "run-1");
    assert_eq!(respond_commands[0].node_id, "human_review");
    assert_eq!(respond_commands[0].caller_actor_id, "human_alice");
    assert_eq!(respond_commands[0].content, "looks good");
    assert_eq!(respond_commands[0].source, HumanResponseSource::Http);
}

#[tokio::test]
async fn post_groups_with_state_machine_yaml_rejects_multi_bot_assigned_slot_before_create() {
    let (app, recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Multi Bot Assigned Slot
participants:
  driver:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer.
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-multi-bot-assigned-slot",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "participant_bindings": {
                            "driver": {
                                "source": "manual",
                                "bot_ids": ["driver-bot", "target-bot"]
                            }
                        },
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body = String::from_utf8(body.to_vec()).unwrap();
    assert!(body.contains(
        "participant slot driver is assigned to a node and must resolve to exactly one bot in the current runtime"
    ));
    assert!(recorder.create_calls.lock().await.is_empty());
    assert!(collaboration_runtime.definitions.lock().await.is_empty());
    assert!(
        collaboration_runtime
            .configure_calls
            .lock()
            .await
            .is_empty()
    );
}

#[tokio::test]
async fn post_groups_with_state_machine_yaml_rejects_bcs_participant_role() {
    let (app, recorder, _collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
id: sm_role_rejected
version: 1
name: Role Rejected
participants:
  driver:
    bot_id: driver-bot
    bcs_participant_role: driver
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer.
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-role-rejected",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(recorder.create_calls.lock().await.is_empty());
}

#[tokio::test]
async fn post_groups_with_state_machine_yaml_keeps_non_empty_body_participants() {
    let (app, recorder, _collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Body Participants Win
participants:
  speaker_a:
    bot_id: driver-bot
    required: true
  speaker_b:
    bot_id: target-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      speak_a:
        kind: bot_task
        display_name: Speak A
        assignee:
          type: bot_binding
          binding: speaker_a
        instruction: Say A.
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-body",
                        "driver_bot": "driver-bot",
                        "participants": [
                            { "bot_uuid": "driver-bot" }
                        ],
                        "group_strategy": "state_machine",
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = recorder.create_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(
        calls[0]
            .participants
            .iter()
            .map(|p| (p.bot_id.as_str(), p.role.as_deref()))
            .collect::<Vec<_>>(),
        vec![("driver-bot", Some("driver"))]
    );
    assert_eq!(calls[0].member_bot_ids, vec!["driver-bot"]);
}

#[tokio::test]
async fn post_groups_with_state_machine_yaml_definition_conflict_returns_409() {
    let (app, recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    *collaboration_runtime.upsert_error.lock().await = Some(CollaborationRuntimeError::Conflict(
        "CollaborationDefinition 'sm_conflict@1' already exists with different content".to_string(),
    ));
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Conflict Definition
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer.
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-conflict",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CONFLICT);
    assert!(recorder.create_calls.lock().await.is_empty());
}

#[tokio::test]
async fn post_groups_dm_delegates_to_create_dm_with_human_caller() {
    let recorder = Arc::new(RecordingGroupManagement::default());
    let services = Services::builder()
        .group_management(recorder.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "dm-1",
                        "group_kind": "dm",
                        "driver_bot": "target-bot",
                        "target_actor_id": "target-bot",
                        "topic": "Need help",
                        "context": "Human-Bot DM context",
                        "participants": []
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "dm-1");
    assert_eq!(json["driver_bot"], "target-bot");
    assert_eq!(json["group_kind"], "dm");
    assert_eq!(json["dm_pair_key"], "human_test|target-bot");
    assert_eq!(json["created"], true);
    assert_eq!(
        json["participants"],
        serde_json::json!(["human_alice", "target-bot"])
    );

    let create_calls = recorder.create_calls.lock().await;
    assert!(create_calls.is_empty());
    drop(create_calls);
    let dm_calls = recorder.create_dm_calls.lock().await;
    assert_eq!(dm_calls.len(), 1);
    let cmd = &dm_calls[0];
    assert_eq!(cmd.group_id.as_deref(), Some("dm-1"));
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(cmd.driver_bot.as_deref(), Some("target-bot"));
    assert_eq!(cmd.target_actor_id, "target-bot");
    assert_eq!(cmd.topic.as_deref(), Some("Need help"));
    assert_eq!(cmd.context.as_deref(), Some("Human-Bot DM context"));
}

#[tokio::test]
async fn post_groups_dm_leaves_driver_bot_policy_to_group_management() {
    let recorder = Arc::new(RecordingGroupManagement::default());
    let services = Services::builder()
        .group_management(recorder.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "dm-1",
                        "group_kind": "dm",
                        "driver_bot": "legacy-driver",
                        "target_actor_id": "target-bot",
                        "participants": []
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let dm_calls = recorder.create_dm_calls.lock().await;
    assert_eq!(dm_calls.len(), 1);
    assert_eq!(dm_calls[0].driver_bot.as_deref(), Some("legacy-driver"));
}

#[tokio::test]
async fn post_groups_dm_rejects_ambiguous_legacy_participants() {
    let recorder = Arc::new(RecordingGroupManagement::default());
    let services = Services::builder()
        .group_management(recorder.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "group_kind": "dm",
                        "participants": [
                            { "bot_uuid": "target-bot" },
                            { "bot_uuid": "other-bot" }
                        ]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(recorder.create_dm_calls.lock().await.is_empty());
}

#[tokio::test]
async fn group_query_routes_delegate_to_group_query_service() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let list_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/groups?group_kind=all&offset=2&limit=3")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(list_response.status(), StatusCode::OK);
    let body = to_bytes(list_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["items"][0]["id"], "group-1");
    assert_eq!(json["total"], 1);
    assert_eq!(json["offset"], 2);
    assert_eq!(json["limit"], 3);
    assert_eq!(json["group_kind"], "all");

    let detail_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/groups/group-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(detail_response.status(), StatusCode::OK);
    let body = to_bytes(detail_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "group-1");
    assert_eq!(json["driver_bot"], "driver-bot");
    assert_eq!(json["message_count"], 0);

    let bot_groups_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/bots/driver-bot/groups?group_kind=normal&offset=4&limit=5&q=05")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(bot_groups_response.status(), StatusCode::OK);
    let body = to_bytes(bot_groups_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bot_uuid"], "driver-bot");
    assert_eq!(json["items"][0]["group_id"], "bot-group-1");
    assert_eq!(json["total"], 1);

    let workspace_response = app
        .oneshot(
            Request::builder()
                .uri("/groups/group-1/workspace")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(workspace_response.status(), StatusCode::OK);
    let body = to_bytes(workspace_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["decisions"], serde_json::json!(["use query service"]));

    let list_calls = query.list_calls.lock().await;
    assert_eq!(list_calls.len(), 1);
    assert_eq!(list_calls[0].group_kind, None);
    assert_eq!(list_calls[0].offset, 2);
    assert_eq!(list_calls[0].limit, 3);
    drop(list_calls);

    let detail_calls = query.detail_calls.lock().await;
    assert_eq!(detail_calls.len(), 1);
    assert_eq!(detail_calls[0].group_id, "group-1");
    drop(detail_calls);

    let bot_group_calls = query.bot_group_calls.lock().await;
    assert_eq!(bot_group_calls.len(), 1);
    assert_eq!(bot_group_calls[0].bot_id, "driver-bot");
    assert_eq!(bot_group_calls[0].group_kind, Some(Default::default()));
    assert_eq!(bot_group_calls[0].q.as_deref(), Some("05"));
    assert_eq!(bot_group_calls[0].offset, 4);
    assert_eq!(bot_group_calls[0].limit, 5);
    drop(bot_group_calls);

    let workspace_calls = query.workspace_calls.lock().await;
    assert_eq!(workspace_calls.len(), 1);
    assert_eq!(workspace_calls[0].group_id, "group-1");
}

#[tokio::test]
async fn my_groups_resolves_bot_principal_and_preserves_query() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services)
            .with_auth_chain(static_bot_auth_chain("bot-current"), AuthConfig::default())
            .with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my?group_kind=normal&offset=4&limit=5&q=05&include_session_groups=true")
                .header("authorization", "Bearer human-oauth-token")
                .header("X-BCS-Bot-Token", "valid-bot-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["actor_id"], "bot-current");
    assert!(json.get("bot_uuid").is_none());
    assert_eq!(json["items"][0]["group_id"], "bot-group-1");

    let calls = query.bot_group_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].bot_id, "bot-current");
    assert_eq!(calls[0].group_kind, Some(Default::default()));
    assert_eq!(calls[0].q.as_deref(), Some("05"));
    assert_eq!(calls[0].offset, 4);
    assert_eq!(calls[0].limit, 5);
}

#[tokio::test]
async fn my_groups_resolves_human_identity() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my?include_session_groups=false")
                .header("authorization", "Bearer human-oauth-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["actor_id"], "human_alice");

    let calls = query.bot_group_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].bot_id, "human_alice");
}

#[tokio::test]
async fn my_groups_rejects_explicit_invalid_bot_token_instead_of_falling_back_to_human() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my")
                .header("authorization", "Bearer human-oauth-token")
                .header("X-BCS-Bot-Token", "stale-bot-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(query.bot_group_calls.lock().await.is_empty());
}

#[tokio::test]
async fn my_groups_rejects_explicit_bot_token_when_container_validation_fails() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder()
        .group_query(query.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services)
            .with_auth_chain(static_bot_auth_chain("bot-current"), AuthConfig::default())
            .with_user_identity(Arc::new(ChainUserIdentityPort::new(chain)))
            .with_strict_container_validation(true),
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my")
                .header("authorization", "Bearer human-oauth-token")
                .header("X-BCS-Bot-Token", "valid-bot-token")
                .header("x-agentclaw-bolt-id", "different-container")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(query.bot_group_calls.lock().await.is_empty());
}

#[tokio::test]
async fn my_groups_rejects_empty_bot_actor_identity() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder().group_query(query).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services)
            .with_auth_chain(static_bot_auth_chain(""), AuthConfig::default())
            .with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/my")
                .header("authorization", "Bearer human-oauth-token")
                .header("X-BCS-Bot-Token", "valid-bot-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn my_groups_rejects_anonymous_without_shadowing_group_detail() {
    let query = Arc::new(RecordingGroupQuery::default());
    let services = Services::builder().group_query(query).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let my_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/groups/my?include_session_groups=false")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(my_response.status(), StatusCode::UNAUTHORIZED);

    let detail_response = app
        .oneshot(
            Request::builder()
                .uri("/groups/group-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(detail_response.status(), StatusCode::OK);
}

#[tokio::test]
async fn get_group_preserves_legacy_detail_payload_from_query_service() {
    let group_store = Arc::new(GroupStore::new());
    let query = Arc::new(bcs_group::GroupManagement::with_defaults(
        group_store.clone(),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
    ));

    let mut group = Group::new(
        "group-with-detail",
        "driver-bot",
        vec![Participant {
            bot_uuid: "driver-bot".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: Some(ParticipantKind::Bot),
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
        }],
    );
    group.messages = vec![GroupMessage {
        id: "msg-1".to_string(),
        timestamp: 42,
        sender: "driver-bot".to_string(),
        content: "hello".to_string(),
        message_type: Default::default(),
        bot_name: None,
        role: Default::default(),
        history_meta: None,
        metadata: None,
        run_id: String::new(),
    }];
    group.workspace = Workspace {
        decisions: vec!["real decision".to_string()],
        ..Workspace::default()
    };
    group.service_group_uuid = Some("service-group-1".to_string());
    group.service_mode = Some("master_slave".to_string());
    group.dm_pair_key = Some("bot-a|bot-b".to_string());
    group_store.upsert(group).await.unwrap();

    let services = Services::builder().group_query(query).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/group-with-detail")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "group-with-detail");
    assert_eq!(json["message_count"], 1);
    assert_eq!(
        json["workspace"]["decisions"],
        serde_json::json!(["real decision"])
    );
    assert_eq!(json["service_group_uuid"], "service-group-1");
    assert_eq!(json["service_mode"], "master_slave");
    assert_eq!(json["dm_pair_key"], "bot-a|bot-b");
    assert_eq!(json["participants"][0]["type"], "bot");
}

#[tokio::test]
async fn put_group_status_delegates_to_group_management_status_and_preserves_response_shape() {
    let (app, recorder, _temp_dir) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-1/status")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "status": "completed",
                        "reason": "release finished"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["updated"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["status"], "completed");
    assert_eq!(json["reason"], "release finished");
    assert_eq!(json["changed_by"], "driver-bot");

    let calls = recorder.status_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("driver-bot"));
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.status, "completed");
}

#[tokio::test]
async fn post_group_member_delegates_to_group_management_add_member() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let recorder = Arc::new(RecordingGroupManagement::default());
    let services = Services::builder()
        .registry(registry)
        .group_management(recorder.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/members")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "target-bot",
                        "role": "observer"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["added"], true);
    assert_eq!(json["session_id"], "group-1");
    assert_eq!(json["member"]["bot_uuid"], "target-bot");
    assert_eq!(json["member"]["role"], "observer");

    let calls = recorder.add_member_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("driver-bot"));
    assert_eq!(cmd.human_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.bot_id, "target-bot");
    assert_eq!(cmd.role.as_deref(), Some("observer"));
}

#[tokio::test]
async fn delete_group_route_delegates_to_group_management_and_preserves_response_shape() {
    let (app, recorder, _temp_dir) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/groups/group-1?bot_id=driver-bot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["deleted"], true);
    assert_eq!(json["id"], "group-1");

    let calls = recorder.delete_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.caller_actor_id, "driver-bot");
}

#[tokio::test]
async fn group_secondary_routes_delegate_to_group_management() {
    let (app, recorder, _temp_dir) = test_app().await;

    let label_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-1/label")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({ "label": "Renamed group" }).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(label_response.status(), StatusCode::OK);
    let body = to_bytes(label_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["updated"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["label"], "Renamed group");

    let workspace_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-1/workspace")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({ "decisions": ["ship it"] }).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(workspace_response.status(), StatusCode::OK);
    let body = to_bytes(workspace_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        json["workspace"]["decisions"],
        serde_json::json!(["ship it"])
    );

    let routing_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-1/routing-policy")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "mode": "structured",
                        "default_bot_final_delivery": "inject_observers",
                        "sender_routes": {
                            "driver-bot": ["target-bot"]
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(routing_response.status(), StatusCode::OK);
    let body = to_bytes(routing_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["ok"], true);
    assert_eq!(json["routing_policy"]["mode"], "structured");

    let terminate_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/terminate")
                .header("authorization", "Bearer driver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(terminate_response.status(), StatusCode::OK);
    let body = to_bytes(terminate_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["terminated"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["terminated_by"], "driver-bot");

    let label_calls = recorder.label_calls.lock().await;
    assert_eq!(label_calls.len(), 1);
    assert_eq!(label_calls[0].caller_actor_id, "driver-bot");
    assert_eq!(label_calls[0].group_id, "group-1");
    assert_eq!(label_calls[0].label.as_deref(), Some("Renamed group"));
    drop(label_calls);

    let workspace_calls = recorder.workspace_calls.lock().await;
    assert_eq!(workspace_calls.len(), 1);
    assert_eq!(workspace_calls[0].group_id, "group-1");
    assert_eq!(
        workspace_calls[0].workspace.decisions,
        vec!["ship it".to_string()]
    );
    drop(workspace_calls);

    let routing_calls = recorder.routing_policy_calls.lock().await;
    assert_eq!(routing_calls.len(), 1);
    assert_eq!(
        routing_calls[0].caller_actor_id.as_deref(),
        Some("driver-bot")
    );
    assert_eq!(routing_calls[0].group_id, "group-1");
    assert_eq!(routing_calls[0].mode, Some(RoutingMode::Structured));
    assert_eq!(
        routing_calls[0]
            .sender_routes
            .as_ref()
            .and_then(|routes| routes.get("driver-bot")),
        Some(&vec!["target-bot".to_string()])
    );
    drop(routing_calls);

    let terminate_calls = recorder.terminate_calls.lock().await;
    assert_eq!(terminate_calls.len(), 1);
    assert_eq!(terminate_calls[0].caller_actor_id, "driver-bot");
    assert_eq!(terminate_calls[0].group_id, "group-1");
}

#[tokio::test]
async fn participant_mode_route_delegates_to_group_management_and_preserves_envelope() {
    let (app, recorder, _temp_dir) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-1/participants/driver-bot/mode")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({ "mode": "muted" }).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["group_id"], "group-1");
    assert_eq!(json["data"]["actor_id"], "driver-bot");
    assert_eq!(json["data"]["mode"], "muted");

    let calls = recorder.participant_mode_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.caller_actor_id, "driver-bot");
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.actor_id, "driver-bot");
    assert_eq!(cmd.mode, ParticipantMode::Muted);
}

#[tokio::test]
async fn patch_group_settings_writes_route_field_changes_to_store() {
    use bcs_service_api::ServiceSpec;
    let initial = ServiceSpec {
        callback_config: None,
        timeout_seconds: Some(30),
        max_concurrency: Some(2),
    };
    let (app, _recorder, store, _temp_dir) =
        test_app_with_service_spec_and_store(Some(initial)).await;

    let body = serde_json::json!({
        "service_spec": {
            "timeout_seconds": 90,
            "max_concurrency": 8,
        }
    });
    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/groups/group-1/settings")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);

    let group = store.get("group-1").await.expect("group still exists");
    let spec = group
        .service_spec
        .as_ref()
        .expect("service_spec persisted after PATCH");
    assert_eq!(spec.timeout_seconds, Some(90));
    assert_eq!(spec.max_concurrency, Some(8));
}

#[tokio::test]
async fn patch_group_settings_rejects_private_baas_callback_base_url() {
    let (app, _recorder, store, _temp_dir) = test_app_with_service_spec_and_store(None).await;

    let body = serde_json::json!({
        "service_spec": {
            "callback_config": {
                "channels": [{
                    "type": "baas",
                    "base_url": "http://127.0.0.1:8080",
                    "api_key": "sk-test",
                    "bot_id": "default:callback-test"
                }]
            },
            "timeout_seconds": 90,
            "max_concurrency": 8
        }
    });
    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/groups/group-1/settings")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let group = store.get("group-1").await.expect("group still exists");
    assert!(group.service_spec.is_none());
}

async fn test_app_with_service_spec_and_store(
    initial_spec: Option<bcs_service_api::ServiceSpec>,
) -> (
    axum::Router,
    Arc<bcs_group::GroupManagement>,
    Arc<GroupStore>,
    TempDir,
) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    let mut g = Group::new(
        "group-1",
        "driver-bot",
        vec![Participant {
            bot_uuid: "driver-bot".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
        }],
    );
    g.service_spec = initial_spec;
    group_store.upsert(g).await.unwrap();

    // A real `GroupManagement` use case owns the patch validation and persists
    // through the same `GroupStore` core, so the route-level test observes the
    // store write end-to-end. The empty session service reports zero running
    // service-invocation sessions, so route-field changes are allowed.
    let management = Arc::new(bcs_group::GroupManagement::with_defaults(
        group_store.clone(),
        registry.clone(),
        Arc::new(NoopFriendCoreService),
    ));
    let services = Services::builder()
        .registry(registry)
        .group(group_store.clone())
        .group_management(management.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));
    (app, management, group_store, temp_dir)
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
                },
                Participant {
                    bot_uuid: "target-bot".to_string(),
                    bot_name: Some("Target".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: ActorKind::Bot,
                    mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
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
        workspace: Workspace::default(),
        service_group_uuid: None,
        service_mode: None,
        group_kind: Default::default(),
        dm_pair_key: None,
        group_strategy: Default::default(),
        created_at: 10,
        updated_at: 10,
        chat_url: None,
        context_injected: 0,
        service_spec: None,
        latest_running_session_id: None,
        visibility: "private".to_string(),
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
    }
}
