//! Versioned collaboration-definition application facade for the BCN V1 API.
//!
//! The V1 facade delegates validation to the legacy
//! `CollaborationRuntimeService::validate_definition_yaml` and translates the
//! legacy runtime error vocabulary into the V1 `ApplicationError`. It performs
//! no per-caller authorization. `judge_available` is fixed at construction from
//! the same configuration source as the legacy `state.judge_enabled`. The
//! underlying legacy implementation never returns `Err` for validation — a
//! document that fails to compile or violates a rule is returned as a
//! `valid: false` outcome; the error mapping below is defense-in-depth to keep
//! the trait complete if a future implementation introduces a failure path.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::collaboration_runtime::{
    CollaborationRuntimeError, CollaborationRuntimeService as LegacyCollaborationRuntimeService,
    ValidateCollaborationDefinitionYamlCommand,
};
use bcs_service_api::application::v1::{
    ApplicationError, CollaborationDefinitionService as V1CollaborationDefinitionService,
    ValidateCollaborationDefinition,
};
use bcs_service_api::CollaborationDefinitionValidationOutcome;

pub struct CollaborationDefinitionServiceImpl {
    legacy: Arc<dyn LegacyCollaborationRuntimeService>,
    judge_available: bool,
}

impl CollaborationDefinitionServiceImpl {
    pub fn new(legacy: Arc<dyn LegacyCollaborationRuntimeService>, judge_available: bool) -> Self {
        Self { legacy, judge_available }
    }
}

#[async_trait]
impl V1CollaborationDefinitionService for CollaborationDefinitionServiceImpl {
    async fn validate_definition_yaml(
        &self,
        command: ValidateCollaborationDefinition,
    ) -> Result<CollaborationDefinitionValidationOutcome, ApplicationError> {
        self.legacy
            .validate_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
                definition_yaml: command.definition_yaml,
                judge_available: self.judge_available,
            })
            .await
            .map_err(map_runtime_error)
    }
}

fn map_runtime_error(error: CollaborationRuntimeError) -> ApplicationError {
    match error {
        CollaborationRuntimeError::InvalidDefinition(message) => {
            ApplicationError::invalid("invalid_definition", message)
        }
        CollaborationRuntimeError::InvalidRequest(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        CollaborationRuntimeError::InvalidParticipantBinding(message) => {
            ApplicationError::invalid("invalid_participant_binding", message)
        }
        CollaborationRuntimeError::RunNotFound(id) => {
            ApplicationError::not_found("not_found", format!("state machine run not found: {id}"))
        }
        CollaborationRuntimeError::NodeNotFound { run_id, node_id } => {
            ApplicationError::not_found(
                "not_found",
                format!("state machine node not found: {run_id}/{node_id}"),
            )
        }
        CollaborationRuntimeError::DefinitionNotFound(id, version) => ApplicationError::not_found(
            "not_found",
            format!("collaboration definition not found: {id}@{version}"),
        ),
        CollaborationRuntimeError::Unauthenticated => ApplicationError::Unauthenticated,
        CollaborationRuntimeError::Forbidden(message) => {
            ApplicationError::forbidden_code("forbidden", message)
        }
        CollaborationRuntimeError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        // validate_definition_yaml never returns Err in the production
        // implementation; these branches preserve trait completeness.
        CollaborationRuntimeError::JudgeUnavailable(message) => {
            ApplicationError::internal(format!("judge unavailable: {message}"))
        }
        CollaborationRuntimeError::Internal(detail) => ApplicationError::internal(detail.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use bcs_service_api::application::collaboration_runtime::CollaborationRuntimeError;

    use super::*;

    #[test]
    fn maps_invalid_definition_to_invalid_input() {
        let error =
            map_runtime_error(CollaborationRuntimeError::InvalidDefinition("bad".to_string()));
        assert!(matches!(error, ApplicationError::InvalidInput { .. }));
        assert_eq!(error.code(), "invalid_definition");
    }

    #[test]
    fn maps_invalid_request_to_invalid_input() {
        let error =
            map_runtime_error(CollaborationRuntimeError::InvalidRequest("shaped".to_string()));
        assert!(matches!(error, ApplicationError::InvalidInput { .. }));
        assert_eq!(error.code(), "invalid_request");
    }

    #[test]
    fn maps_invalid_participant_binding_to_invalid_input() {
        let error = map_runtime_error(CollaborationRuntimeError::InvalidParticipantBinding(
            "bad binding".to_string(),
        ));
        assert!(matches!(error, ApplicationError::InvalidInput { .. }));
        assert_eq!(error.code(), "invalid_participant_binding");
    }

    #[test]
    fn maps_forbidden_with_forbidden_code() {
        let error = map_runtime_error(CollaborationRuntimeError::Forbidden("nope".to_string()));
        assert!(matches!(error, ApplicationError::ForbiddenCode { .. }));
        assert_eq!(error.code(), "forbidden");
    }

    #[test]
    fn maps_conflict() {
        let error = map_runtime_error(CollaborationRuntimeError::Conflict("dup".to_string()));
        assert!(matches!(error, ApplicationError::Conflict { .. }));
        assert_eq!(error.code(), "conflict");
    }

    #[test]
    fn maps_unauthenticated() {
        assert!(matches!(
            map_runtime_error(CollaborationRuntimeError::Unauthenticated),
            ApplicationError::Unauthenticated
        ));
    }

    #[test]
    fn maps_judge_unavailable_to_internal() {
        let error = map_runtime_error(CollaborationRuntimeError::JudgeUnavailable("down".to_string()));
        assert!(matches!(error, ApplicationError::Internal(_)));
        assert_eq!(error.code(), "internal_error");
    }
}
