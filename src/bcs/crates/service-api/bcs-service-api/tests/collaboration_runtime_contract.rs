use bcs_service_api::{
    AuthenticatedHumanCaller, CollaborationDefinitionGraphNode,
    CollaborationDefinitionGraphPreview, CollaborationDefinitionParticipantSlot,
    CollaborationDefinitionValidationOutcome, CollaborationDefinitionValidationSummary,
    CollaborationRuntimeError, CollaborationRuntimeService, HumanResponseSource,
    HumanRunAccessCommand, ListPendingHumanNodesCommand, RespondHumanNodeCommand, ServiceResult,
    SessionStateMachinePermissionCommand, StartSessionStateMachineRunCommand, StateMachineAssignee,
    StateMachineGraphMode, StateMachineNodeKind, StateMachineResultPublishCommand,
    StateMachineResultPublisherPort, StateMachineRunAccessCommand,
    ValidateCollaborationDefinitionYamlCommand,
};
use bcs_test_support::{
    NoopCollaborationRuntimeService,
    contract::port::state_machine_result_publisher_port_contract_tests,
};
use serde_json::Value;
use std::{collections::BTreeMap, sync::Mutex};

#[tokio::test]
async fn validation_contract_defaults_to_fail_closed() {
    let service = NoopCollaborationRuntimeService;
    let error = service
        .validate_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
            definition_yaml: "name: test".to_string(),
            judge_available: false,
        })
        .await
        .expect_err("an unconfigured implementation must not claim validation success");

    assert!(matches!(
        error,
        CollaborationRuntimeError::InvalidRequest(_)
    ));
}

#[tokio::test]
async fn session_state_machine_contract_defaults_to_fail_closed() {
    let service = NoopCollaborationRuntimeService;

    let permission_error = service
        .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
            session_id: "session-1".to_string(),
            caller_bot_id: "bot-owner".to_string(),
        })
        .await
        .expect_err("an unconfigured implementation must not grant run permission");
    assert!(matches!(
        permission_error,
        CollaborationRuntimeError::InvalidRequest(_)
    ));

    let start_error = service
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: "session-1".to_string(),
            caller_bot_id: "bot-owner".to_string(),
            definition_yaml: "name: one-shot".to_string(),
            participant_bindings: BTreeMap::new(),
            input: Value::Null,
            judge_available: false,
        })
        .await
        .expect_err("an unconfigured implementation must not start a run");
    assert!(matches!(
        start_error,
        CollaborationRuntimeError::InvalidRequest(_)
    ));
}

#[tokio::test]
async fn human_runtime_defaults_fail_closed_and_preserve_bot_only_fallbacks() {
    let service = NoopCollaborationRuntimeService;
    let human_access = HumanRunAccessCommand {
        run_id: "run-1".to_string(),
        caller_actor_id: "human-1".to_string(),
    };
    let authenticated_access = StateMachineRunAccessCommand {
        run_id: "run-1".to_string(),
        authenticated_human: Some(AuthenticatedHumanCaller {
            actor_id: "human-1".to_string(),
            display_name: Some("Reviewer".to_string()),
        }),
    };

    assert!(
        service
            .get_state_machine_run_by_session_id("session-1")
            .await
            .is_err()
    );
    assert!(
        service
            .respond_human_node(RespondHumanNodeCommand {
                run_id: "run-1".to_string(),
                node_id: "review".to_string(),
                caller_actor_id: "human-1".to_string(),
                content: "approve".to_string(),
                source: HumanResponseSource::Http,
            })
            .await
            .is_err()
    );
    assert!(
        service
            .list_pending_human_nodes(ListPendingHumanNodesCommand {
                run_id: "run-1".to_string(),
                caller_actor_id: "human-1".to_string(),
            })
            .await
            .is_err()
    );
    assert!(
        service
            .get_state_machine_run_for_human(human_access.clone())
            .await
            .is_err()
    );
    assert!(
        service
            .get_state_machine_run_with_access(authenticated_access.clone())
            .await
            .is_err()
    );
    assert!(
        service
            .get_state_machine_run_with_access(StateMachineRunAccessCommand {
                run_id: "run-1".to_string(),
                authenticated_human: None,
            })
            .await
            .expect("Bot-only fallback remains available")
            .is_none()
    );
    assert!(
        service
            .get_state_machine_node_run_for_human(human_access.clone(), "review")
            .await
            .is_err()
    );
    assert!(
        service
            .get_state_machine_node_run_with_access(authenticated_access.clone(), "review")
            .await
            .is_err()
    );
    assert!(
        service
            .get_state_machine_node_run_with_access(
                StateMachineRunAccessCommand {
                    run_id: "run-1".to_string(),
                    authenticated_human: None,
                },
                "review",
            )
            .await
            .expect("Bot-only node fallback remains available")
            .is_none()
    );
    assert!(
        service
            .get_state_machine_run_graph_for_human(human_access.clone())
            .await
            .is_err()
    );
    assert!(
        service
            .get_state_machine_run_graph_with_access(authenticated_access.clone())
            .await
            .is_err()
    );
    assert!(
        service
            .get_state_machine_run_graph_with_access(StateMachineRunAccessCommand {
                run_id: "run-1".to_string(),
                authenticated_human: None,
            })
            .await
            .expect("Bot-only graph fallback remains available")
            .is_none()
    );
    assert!(
        service
            .cancel_state_machine_run_for_human(human_access, None)
            .await
            .is_err()
    );
    assert!(
        service
            .cancel_state_machine_run_with_access(authenticated_access, None)
            .await
            .is_err()
    );
    assert!(
        service
            .cancel_state_machine_run_with_access(
                StateMachineRunAccessCommand {
                    run_id: "run-1".to_string(),
                    authenticated_human: None,
                },
                None,
            )
            .await
            .is_err()
    );
}

#[test]
fn validation_outcome_serializes_without_internal_definition() {
    let outcome = CollaborationDefinitionValidationOutcome {
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
            graph_mode: StateMachineGraphMode::Acyclic,
            nodes: vec![CollaborationDefinitionGraphNode {
                node_id: "answer".to_string(),
                display_name: "Answer".to_string(),
                kind: StateMachineNodeKind::BotTask,
                assignee: Some(StateMachineAssignee::BotBinding {
                    binding: "writer".to_string(),
                }),
                final_output: true,
                judge: false,
            }],
            edges: Vec::new(),
        }),
        definition: None,
    };

    let wire = serde_json::to_value(outcome).unwrap();
    assert_eq!(wire["valid"], true);
    assert_eq!(wire["participants"][0]["binding"], "writer");
    assert_eq!(wire["graph"]["graph_mode"], "acyclic");
    assert_eq!(wire["graph"]["nodes"][0]["node_id"], "answer");
    assert!(wire.get("definition").is_none());
    assert!(wire.get("errors").is_none());
    assert!(wire.get("warnings").is_none());
}

#[test]
fn invalid_validation_outcome_omits_graph() {
    let outcome = CollaborationDefinitionValidationOutcome {
        valid: false,
        errors: vec![
            bcs_service_api::CollaborationDefinitionValidationDiagnostic {
                code: "INVALID_DEFINITION".to_string(),
                path: "$".to_string(),
                message: "invalid definition".to_string(),
                hint: None,
            },
        ],
        warnings: Vec::new(),
        summary: CollaborationDefinitionValidationSummary::default(),
        participants: Vec::new(),
        graph: None,
        definition: None,
    };

    let wire = serde_json::to_value(outcome).unwrap();
    assert_eq!(wire["valid"], false);
    assert!(wire.get("graph").is_none());
}

#[derive(Default)]
struct RecordingResultPublisher {
    commands: Mutex<Vec<StateMachineResultPublishCommand>>,
}

#[async_trait::async_trait]
impl StateMachineResultPublisherPort for RecordingResultPublisher {
    async fn publish_state_machine_result(
        &self,
        cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        self.commands.lock().unwrap().push(cmd);
        Ok(())
    }
}

#[tokio::test]
async fn state_machine_result_publisher_preserves_chat_identity_and_scope() {
    let publisher = RecordingResultPublisher::default();

    state_machine_result_publisher_port_contract_tests(&publisher).await;

    let commands = publisher.commands.lock().unwrap();
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].run_id, "contract-run");
    assert_eq!(commands[0].group_id, "contract-group");
    assert_eq!(commands[0].session_id, "contract-group:00000001");
    assert_eq!(commands[0].sender_bot_id, "contract-initiator");
    assert_eq!(commands[0].content, "contract final result");
}
