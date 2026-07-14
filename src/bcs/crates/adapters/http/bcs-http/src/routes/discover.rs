use axum::{
    Json,
    extract::{Query, State},
    http::{HeaderMap, Uri},
};
use bcs_service_api::{BotDiscoveryCommand, BotDiscoveryEntry, BotUseCaseError};
use serde::Deserialize;
use serde_json::Value;

use crate::error::HttpAdapterError;
use crate::mapping::capabilities::to_wire_capabilities;
use crate::state::HttpAppState;

use super::{bot_id_from_headers, require_caller_actor_id_from_headers};

#[derive(Debug, Deserialize)]
pub struct DiscoverBotsQuery {
    #[serde(default)]
    pub q: Option<String>,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub skills: Option<String>,
    #[serde(default)]
    pub domains: Option<String>,
    #[serde(default)]
    pub scopes: Option<String>,
    #[serde(default)]
    pub visibility: Option<String>,
    #[serde(default)]
    pub collaborate_bot: Option<String>,
    #[serde(default)]
    pub organization_code: Option<String>,
    #[serde(default)]
    pub role: Option<String>,
}

pub async fn discover_bots(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<DiscoverBotsQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let _caller_actor_id =
        require_caller_actor_id_from_headers(&state, &headers, &uri).await?;
    let requester_bot_id = bot_id_from_headers(&state, &headers).await;
    if query.organization_code.is_some() && requester_bot_id.is_none() {
        return Err(HttpAdapterError::Forbidden(
            "organization discovery requires a bot caller".to_string(),
        ));
    }
    let result = state
        .services
        .bot_discovery
        .discover_bots(BotDiscoveryCommand {
            q: query.q,
            name: query.name,
            skills: query.skills,
            domains: query.domains,
            scopes: query.scopes,
            visibility: query.visibility,
            collaborate_bot: query.collaborate_bot,
            requester_bot_id,
            organization_code: query.organization_code,
            role: query.role,
        })
        .await
        .map_err(bot_use_case_error_to_http)?;

    let bots: Vec<Value> = result.bots.into_iter().map(discover_bot_to_json).collect();

    Ok(Json(serde_json::json!({
        "bots": bots,
        "count": result.count
    })))
}

fn bot_use_case_error_to_http(error: BotUseCaseError) -> HttpAdapterError {
    match error {
        BotUseCaseError::Unauthorized(message) => HttpAdapterError::Unauthorized(message),
        BotUseCaseError::Forbidden(message) => HttpAdapterError::Forbidden(message),
        BotUseCaseError::InvalidVisibility(message) | BotUseCaseError::InvalidBotId(message) => {
            HttpAdapterError::BadRequest(message)
        }
        BotUseCaseError::InvalidProviderBotRef(message) => HttpAdapterError::BadRequest(message),
        BotUseCaseError::ProviderNotFound(p) => {
            HttpAdapterError::NotFound(format!("Provider '{p}' not found"))
        }
        BotUseCaseError::ProviderNotReadyForDownlink { provider_id, reason } => {
            HttpAdapterError::Conflict(format!(
                "Provider '{provider_id}' downlink not ready: {reason}"
            ))
        }
        BotUseCaseError::BotAlreadyBound {
            bot_id,
            existing_provider_id,
            existing_provider_bot_ref,
        } => HttpAdapterError::Conflict(format!(
            "Bot '{bot_id}' already bound to provider '{existing_provider_id}' (ref '{existing_provider_bot_ref}')"
        )),
        BotUseCaseError::Connect(error) => HttpAdapterError::BadRequest(error.to_string()),
        BotUseCaseError::Service(error) => HttpAdapterError::Service(error),
    }
}

fn discover_bot_to_json(bot: BotDiscoveryEntry) -> Value {
    let mut entry = serde_json::json!({
        "bot_uuid": bot.bot_uuid,
        "capabilities": to_wire_capabilities(bot.capabilities),
        "visibility": bot.visibility,
    });
    if let Some(is_friend) = bot.is_friend {
        entry
            .as_object_mut()
            .map(|object| object.insert("is_friend".to_string(), serde_json::json!(is_friend)));
    }
    if let Some(agent_code) = bot.agent_code {
        entry
            .as_object_mut()
            .map(|object| object.insert("agent_code".to_string(), serde_json::json!(agent_code)));
    }
    if let Some(provider_info) = bot.provider_info {
        entry.as_object_mut().map(|object| {
            object.insert(
                "provider_info".to_string(),
                serde_json::json!({
                    "provider_id": provider_info.provider_id,
                    "provider_name": provider_info.provider_name,
                }),
            )
        });
    }
    if let Some(member) = bot.organization_member {
        entry.as_object_mut().map(|object| {
            object.insert(
                "organization_member".to_string(),
                serde_json::json!({
                    "organization_code": member.organization_code,
                    "role": member.role,
                }),
            )
        });
    }
    entry
}
