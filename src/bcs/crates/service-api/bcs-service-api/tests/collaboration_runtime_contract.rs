use bcs_service_api::{
    AuthenticatedHumanCaller, CollaborationDefinitionParticipantSlot,
    CollaborationDefinitionValidationOutcome, CollaborationDefinitionValidationSummary,
    CollaborationRuntimeError, CollaborationRuntimeService, HumanResponseSource,
    HumanRunAccessCommand, ListPendingHumanNodesCommand, RespondHumanNodeCommand,
    StateMachineRunAccessCommand, ValidateCollaborationDefinitionYamlCommand,
};
use bcs_test_support::NoopCollaborationRuntimeService;

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
        definition: None,
    };

    let wire = serde_json::to_value(outcome).unwrap();
    assert_eq!(wire["valid"], true);
    assert_eq!(wire["participants"][0]["binding"], "writer");
    assert!(wire.get("definition").is_none());
    assert!(wire.get("errors").is_none());
    assert!(wire.get("warnings").is_none());
}
