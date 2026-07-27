use std::collections::HashSet;

use axum::{
    Json,
    extract::State,
    http::{HeaderMap, Uri},
};
use bcs_service_api::{BotDiscoveryCommand, BotDiscoveryEntry, BotUseCaseError};
use serde_json::Value;

use crate::error::HttpAdapterError;
use crate::mapping::capabilities::to_wire_capabilities;
use crate::state::HttpAppState;

use super::{bot_id_from_headers, require_caller_actor_id_from_headers};

#[derive(Debug, Default)]
pub struct DiscoverBotsQuery {
    pub q: Option<String>,
    pub skills: Vec<String>,
    pub visibility: Option<String>,
    pub collaborate_bot: Option<String>,
    pub organization_code: Option<String>,
    pub role: Option<String>,
}

impl DiscoverBotsQuery {
    fn parse(raw_query: Option<&str>) -> Result<Self, HttpAdapterError> {
        let mut query = Self::default();
        let mut seen_skills = HashSet::new();

        for (key, value) in
            url::form_urlencoded::parse(raw_query.unwrap_or_default().as_bytes())
        {
            let key = key.into_owned();
            let value = value.into_owned();
            match key.as_str() {
                "q" => set_once(&mut query.q, &key, value)?,
                "skill" => {
                    let skill = value.trim();
                    if skill.is_empty() {
                        return Err(HttpAdapterError::BadRequest(
                            "skill must not be empty".to_string(),
                        ));
                    }
                    if seen_skills.insert(skill.to_ascii_lowercase()) {
                        query.skills.push(skill.to_string());
                    }
                }
                "visibility" => set_once(&mut query.visibility, &key, value)?,
                "collaborate_bot" => set_once(&mut query.collaborate_bot, &key, value)?,
                "organization_code" => set_once(&mut query.organization_code, &key, value)?,
                "role" => set_once(&mut query.role, &key, value)?,
                _ => {
                    return Err(HttpAdapterError::BadRequest(format!(
                        "unknown discover query parameter '{key}'"
                    )));
                }
            }
        }

        Ok(query)
    }
}

fn set_once(
    target: &mut Option<String>,
    key: &str,
    value: String,
) -> Result<(), HttpAdapterError> {
    if target.replace(value).is_some() {
        return Err(HttpAdapterError::BadRequest(format!(
            "discover query parameter '{key}' must not be repeated"
        )));
    }
    Ok(())
}

pub async fn discover_bots(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, HttpAdapterError> {
    let query = DiscoverBotsQuery::parse(uri.query())?;
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
            skills: query.skills,
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
