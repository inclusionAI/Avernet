use axum::{
    extract::{Query, State},
    http::{header, HeaderMap, Uri},
    Json,
};
use bcs_protocol::{AdminOnboardRequest, OnboardRequest};
use bcs_service_api::{
    ActorKind, AdminBotOnboardCommand, BotOnboardCommand, BotOnboardResult,
    OnboardActorIdentity,
};
use serde::Deserialize;
use serde_json::Value;

use crate::error::HttpAdapterError;
use crate::mapping::capabilities::{to_core_binding_channels, to_core_skill};
use crate::state::HttpAppState;

use super::authenticated_bot_from_headers;

#[derive(Debug, Deserialize)]
pub struct GetOnboardUrlQuery {
    token: String,
    name: String,
    #[serde(default)]
    summary: Option<String>,
    #[serde(default)]
    skills: Option<String>,
    #[serde(default)]
    domains: Option<String>,
    #[serde(default)]
    scopes: Option<String>,
    #[serde(default)]
    binding_channels: Option<String>,
}

pub async fn get_onboard_url(
    State(state): State<HttpAppState>,
    Query(query): Query<GetOnboardUrlQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let botchat_url = state.botchat_url.as_ref().ok_or_else(|| {
        HttpAdapterError::BadRequest("botchat_url not configured on server".to_string())
    })?;

    let mut url = format!(
        "{}{}?token={}&name={}",
        botchat_url.trim_end_matches('/'),
        state.register_path,
        urlencoding::encode(&query.token),
        urlencoding::encode(&query.name),
    );

    if let Some(ref summary) = query.summary {
        url.push_str(&format!("&summary={}", urlencoding::encode(summary)));
    }
    if let Some(ref skills) = query.skills {
        url.push_str(&format!("&skills={}", urlencoding::encode(skills)));
    }
    if let Some(ref domains) = query.domains {
        url.push_str(&format!("&domains={}", urlencoding::encode(domains)));
    }
    if let Some(ref scopes) = query.scopes {
        url.push_str(&format!("&scopes={}", urlencoding::encode(scopes)));
    }
    if let Some(ref binding_channels) = query.binding_channels {
        url.push_str(&format!(
            "&binding_channels={}",
            urlencoding::encode(binding_channels)
        ));
    }

    Ok(Json(serde_json::json!({
        "onboard_url": url
    })))
}

pub async fn onboard_bot(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<OnboardRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let bot_uuid = authenticated_bot_from_headers(&state, &headers).await?;
    let actor_identity = onboard_actor_identity(&state, &headers, &uri).await;
    let agent_code = headers
        .get("x-agentclaw-agent-code")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());
    let agent_token = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());

    let result = state
        .services
        .bot_onboarding
        .onboard_bot(BotOnboardCommand {
            bot_uuid,
            name: req.name,
            summary: req.summary,
            domains: req.domains,
            skills: req.skills.into_iter().map(to_core_skill).collect(),
            scopes: req.scopes,
            binding_channels: req.binding_channels.map(to_core_binding_channels),
            agent_code,
            agent_token,
            actor_identity,
        })
        .await?;

    sync_onboard_result_to_visibility_index(&state, &result);

    Ok(onboard_result_response(result))
}

pub async fn admin_onboard_bot(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<AdminOnboardRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let actor_identity = onboard_actor_identity(&state, &headers, &uri).await;
    let result = state
        .services
        .bot_onboarding
        .admin_onboard_bot(AdminBotOnboardCommand {
            bot_uuid: req.bot_id,
            name: req.name,
            summary: req.summary,
            domains: req.domains,
            skills: req.skills.into_iter().map(to_core_skill).collect(),
            scopes: req.scopes,
            binding_channels: req.binding_channels.map(to_core_binding_channels),
            actor_identity,
        })
        .await?;

    sync_onboard_result_to_visibility_index(&state, &result);

    Ok(onboard_result_response(result))
}

fn onboard_result_response(result: BotOnboardResult) -> Json<Value> {
    if !result.onboarded {
        return Json(serde_json::json!({
            "bot_uuid": result.bot_uuid,
            "onboarded": false,
            "message": result.message.unwrap_or_else(|| "Bot 未在协作网络注册，请尝试重启".to_string())
        }));
    }

    Json(serde_json::json!({
        "bot_uuid": result.bot_uuid,
        "onboarded": true,
        "name": result.name.unwrap_or_default(),
        "binding_results": result.binding_results,
        "unbound": result.unbound
    }))
}

fn sync_onboard_result_to_visibility_index(state: &HttpAppState, result: &BotOnboardResult) {
    if result.actor_kind == ActorKind::Human {
        return;
    }
    if result.capabilities.is_none() {
        return;
    }
    state.dispatch_visibility_sync(result.bot_uuid.clone());
}

async fn onboard_actor_identity(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Option<OnboardActorIdentity> {
    // extract_user_identity handles Authorization stripping internally:
    // it removes non-JWT tokens (e.g. BCS bot session tokens) to prevent the
    // SDK from misparseing them as user identity tokens, while preserving
    // JWT tokens (OAuth user tokens from the office-network gateway).
    let user = state.user_identity.extract(headers, uri).await?;
    let staff_no = user.staff_no.filter(|staff_no| !staff_no.is_empty())?;
    Some(OnboardActorIdentity {
        staff_no,
        nick_name: user.nick_name,
    })
}
