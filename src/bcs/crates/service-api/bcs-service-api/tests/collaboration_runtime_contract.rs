use bcs_service_api::{
    CollaborationDefinitionParticipantSlot, CollaborationDefinitionValidationOutcome,
    CollaborationDefinitionValidationSummary, CollaborationRuntimeError,
    CollaborationRuntimeService, ValidateCollaborationDefinitionYamlCommand,
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
