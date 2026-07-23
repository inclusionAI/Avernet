use axum::{
    Json,
    extract::State,
    response::{IntoResponse, Response},
};
use bcs_service_api::ValidateCollaborationDefinitionYamlCommand;
use serde::Deserialize;

use super::collaboration_runs::collaboration_error_to_response;
use crate::state::HttpAppState;

#[derive(Debug, Deserialize)]
pub struct ValidateCollaborationDefinitionYamlRequest {
    #[serde(alias = "yaml")]
    pub definition_yaml: String,
}

pub async fn validate_collaboration_definition_yaml(
    State(state): State<HttpAppState>,
    Json(body): Json<ValidateCollaborationDefinitionYamlRequest>,
) -> Response {
    match state
        .services
        .collaboration_runtime
        .validate_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
            definition_yaml: body.definition_yaml,
            judge_available: state.judge_enabled,
        })
        .await
    {
        Ok(outcome) => Json(outcome).into_response(),
        Err(error) => collaboration_error_to_response(error),
    }
}
