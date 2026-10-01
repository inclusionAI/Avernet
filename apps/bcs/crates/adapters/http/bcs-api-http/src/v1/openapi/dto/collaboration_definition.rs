use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ValidateCollaborationDefinitionRequest {
    pub definition_yaml: String,
}
