//! Transport-neutral OpenAPI V1 collaboration-definition application contract.
//!
//! Definition validation is a projection of the legacy
//! `CollaborationRuntimeService::validate_definition_yaml` use case. The V1
//! facade reuses the legacy `CollaborationDefinitionValidationOutcome` response
//! type verbatim (byte-compatible projection) and only translates the legacy
//! runtime error vocabulary into the V1 `ApplicationError`. The facade is not
//! caller-scoped: the delivery adapter authenticates the Gateway Principal on
//! the protected boundary, but no per-caller authorization is applied to the
//! validation result. `judge_available` is a server-side runtime configuration
//! injected when the facade implementation is built; it is not a request field.

use async_trait::async_trait;

use super::ApplicationError;
pub use crate::application::collaboration_runtime::CollaborationDefinitionValidationOutcome;

/// Validate an authoring collaboration-definition YAML document.
///
/// `definition_yaml` is the raw YAML to compile and validate. `judge_available`
/// is server-side configuration carried by the facade implementation, not by
/// this command; the command carries only request-originated input.
#[derive(Debug, Clone)]
pub struct ValidateCollaborationDefinition {
    pub definition_yaml: String,
}

/// V1 application facade for collaboration-definition validation.
///
/// Implementations delegate to the legacy `CollaborationRuntimeService` and
/// are not caller-scoped: the delivery adapter authenticates the Gateway
/// Principal on the protected boundary, but no per-caller authorization is
/// applied to the validation result.
#[async_trait]
pub trait CollaborationDefinitionService: Send + Sync {
    async fn validate_definition_yaml(
        &self,
        command: ValidateCollaborationDefinition,
    ) -> Result<CollaborationDefinitionValidationOutcome, ApplicationError>;
}
