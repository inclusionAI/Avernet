pub mod actors;
pub mod admin_invocations;
pub mod assets;
pub mod bot_chat;
pub mod bot_events;
pub mod bots;
pub mod channel;
pub mod collaboration_runs;
mod caller;
pub mod discover;
pub mod friends;
pub mod group_messages;
pub mod group_requests;
pub mod groups;
pub mod health;
pub mod invite;
pub mod manifest;
pub mod me;
pub mod messages;
pub mod onboard;
pub mod organizations;
pub mod providers;
pub mod register;
pub mod secret;
pub mod services;
pub mod sessions;
pub mod templates;

use bcs_domain::CollaborationDefinition;

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;

use caller::{
    authenticated_bot_from_headers, bot_id_from_headers, bot_token_from_headers,
    caller_actor_id_from_headers, container_header_matches, require_bot_id_from_headers,
    require_caller_actor_id_from_headers, validate_container_header,
};

pub(crate) fn reject_judge_yaml_when_unavailable(
    state: &HttpAppState,
    yaml: &str,
    field_name: &str,
) -> Result<(), HttpAdapterError> {
    if state.judge_enabled {
        return Ok(());
    }
    let definition: CollaborationDefinition = serde_yaml::from_str(yaml)
        .map_err(|error| HttpAdapterError::BadRequest(format!("invalid {field_name}: {error}")))?;
    reject_judge_definition_when_unavailable(state, &definition)
}

pub(crate) fn reject_judge_definition_value_when_unavailable(
    state: &HttpAppState,
    value: &serde_json::Value,
    field_name: &str,
) -> Result<(), HttpAdapterError> {
    if state.judge_enabled {
        return Ok(());
    }
    let definition: CollaborationDefinition = serde_json::from_value(value.clone())
        .map_err(|error| HttpAdapterError::BadRequest(format!("invalid {field_name}: {error}")))?;
    reject_judge_definition_when_unavailable(state, &definition)
}

pub(crate) fn reject_judge_definition_when_unavailable(
    state: &HttpAppState,
    definition: &CollaborationDefinition,
) -> Result<(), HttpAdapterError> {
    if !state.judge_enabled && definition.uses_judge() {
        return Err(HttpAdapterError::BadRequest(
            "state-machine judge requires llm.type to select an LLM provider".to_string(),
        ));
    }
    Ok(())
}
