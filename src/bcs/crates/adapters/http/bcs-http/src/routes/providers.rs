use axum::{
    Json,
    extract::{Path, State},
    http::{HeaderMap, StatusCode, Uri, header},
    response::{IntoResponse, Response},
};
use bcs_protocol::{
    BCN_PROVIDER_ID_HEADER, PatchProviderRequest, ProviderAuthModeDto,
    ProviderCoordinationConfigDto, ProviderCoordinationModeDto, ProviderInfoResponse,
    ProviderOrganizationManagementConfigDto, RegisterProviderBotRequest,
    RegisterProviderBotResponse, RegisterProviderRequest, RegisterProviderResponse,
};
use bcs_service_api::{
    BotUseCaseError, CoordinationMode, DeleteProviderBotCommand, ProviderAuthMode,
    ProviderBotBinding, ProviderCoordinationConfig, ProviderOrganizationManagementConfig,
    ProviderRecord, RegisterProviderBotCommand, RegisterProviderCommand, ServiceError,
    SwitchDeliveryToProviderCommand, SwitchDeliveryToProviderResult, UpdateProviderCommand,
};
use serde_json::{Value, json};
use tracing::info;

use crate::mapping::capabilities::to_core_skill;
use crate::state::HttpAppState;

#[derive(Debug, serde::Deserialize)]
pub struct ProviderStreamGrayRequest {
    pub enabled: Option<bool>,
    pub created_by: Option<Vec<String>>,
}

#[derive(Debug, serde::Serialize)]
pub struct ProviderStreamGrayResponse {
    pub enabled: bool,
    pub created_by: Vec<String>,
}

#[derive(Debug)]
pub struct ProviderRouteError {
    status: StatusCode,
    message: String,
}

impl ProviderRouteError {
    fn unauthorized(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            message: message.into(),
        }
    }

    fn bad_request(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            message: message.into(),
        }
    }
}

impl IntoResponse for ProviderRouteError {
    fn into_response(self) -> Response {
        let status = self.status;
        (
            status,
            Json(json!({
                "error": self.message,
                "status": status.as_u16(),
            })),
        )
            .into_response()
    }
}

pub async fn register_provider(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<RegisterProviderRequest>,
) -> Result<Json<RegisterProviderResponse>, ProviderRouteError> {
    let created_by = require_staff_no(&state, &headers, &uri).await?;
    let outcome = state
        .services
        .provider_management
        .register_provider(RegisterProviderCommand {
            name: req.name,
            webhook_url: req.webhook_url,
            auth_mode: auth_mode_from_wire(req.auth.mode),
            created_by,
            protocol_version: req.protocol_version,
            coordination: req.coordination.map(coordination_from_wire),
        })
        .await
        .map_err(provider_error)?;

    Ok(Json(RegisterProviderResponse {
        provider_id: outcome.provider_id,
        provider_admin_token: outcome.provider_admin_token,
        bcs_to_provider_token: outcome.bcs_to_provider_token,
    }))
}

pub async fn get_provider_stream_gray(
    State(state): State<HttpAppState>,
) -> Result<Json<ProviderStreamGrayResponse>, ProviderRouteError> {
    let snapshot = state.provider_stream_gray_list.snapshot();
    Ok(Json(ProviderStreamGrayResponse {
        enabled: snapshot.enabled,
        created_by: snapshot.created_by,
    }))
}

pub async fn put_provider_stream_gray(
    State(state): State<HttpAppState>,
    Json(req): Json<ProviderStreamGrayRequest>,
) -> Result<Json<ProviderStreamGrayResponse>, ProviderRouteError> {
    let old = state.provider_stream_gray_list.snapshot();
    let updated = state
        .provider_stream_gray_list
        .update(req.enabled, req.created_by);
    info!(
        old_enabled = old.enabled,
        new_enabled = updated.enabled,
        old_created_by = ?old.created_by,
        new_created_by = ?updated.created_by,
        "provider stream gray list updated"
    );
    Ok(Json(ProviderStreamGrayResponse {
        enabled: updated.enabled,
        created_by: updated.created_by,
    }))
}

pub async fn get_provider(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<ProviderInfoResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let provider = state
        .services
        .provider_management
        .get_provider(&provider_id, &provider_admin_token)
        .await
        .map_err(provider_error)?;
    Ok(Json(provider_to_response(provider).map_err(provider_error)?))
}

pub async fn patch_provider(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<PatchProviderRequest>,
) -> Result<Json<ProviderInfoResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let authenticated_staff_id = require_staff_no(&state, &headers, &uri).await?;
    let provider = state
        .services
        .provider_management
        .update_provider(UpdateProviderCommand {
            provider_id,
            provider_admin_token,
            authenticated_staff_id,
            name: req.name,
            webhook_url: req.webhook_url,
            protocol_version: req.protocol_version,
            coordination: req.coordination.map(coordination_from_wire),
            organization_management: req
                .organization_management
                .map(organization_management_from_wire),
        })
        .await
        .map_err(provider_error)?;
    Ok(Json(provider_to_response(provider).map_err(provider_error)?))
}

pub async fn register_provider_bot(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<RegisterProviderBotRequest>,
) -> Result<Json<RegisterProviderBotResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let allowed_switch_provider = state.allowed_switch_provider_ids.contains(&provider_id);
    let bot_uuid = allowed_switch_provider.then(|| req.provider_bot_ref.clone());
    let outcome = state
        .services
        .provider_management
        .register_provider_bot(RegisterProviderBotCommand {
            provider_id,
            provider_admin_token,
            name: req.name,
            summary: req.summary,
            owners: req.owners,
            provider_bot_ref: req.provider_bot_ref,
            domains: req.domains,
            skills: req.skills.into_iter().map(to_core_skill).collect(),
            scopes: req.scopes,
            bot_uuid,
            reject_existing_bot_uuid: allowed_switch_provider,
        })
        .await
        .map_err(provider_error)?;

    Ok(Json(RegisterProviderBotResponse {
        bot_uuid: outcome.bot_uuid,
        provider_id: outcome.provider_id,
        provider_bot_ref: outcome.provider_bot_ref,
        bot_runtime_token: outcome.bot_runtime_token,
        message: outcome.message,
    }))
}

pub async fn list_provider_bots(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let bindings = state
        .services
        .provider_management
        .list_provider_bots(&provider_id, &provider_admin_token)
        .await
        .map_err(provider_error)?;
    let items: Vec<Value> = bindings.into_iter().map(binding_to_json).collect();
    Ok(Json(json!({ "items": items })))
}

pub async fn delete_provider_bot(
    State(state): State<HttpAppState>,
    Path((provider_id, provider_bot_ref)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<Value>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let allow_unbound_owner_suffixed_bot = state.allowed_switch_provider_ids.contains(&provider_id);
    let outcome = match state
        .services
        .provider_management
        .delete_provider_bot(DeleteProviderBotCommand {
            provider_id: provider_id.clone(),
            provider_admin_token,
            provider_bot_ref: provider_bot_ref.clone(),
            allow_unbound_owner_suffixed_bot,
        })
        .await
    {
        Ok(outcome) => outcome,
        Err(ServiceError::BotNotFound(_)) | Err(ServiceError::BotNotRegistered(_)) => {
            return Ok(delete_provider_bot_response(
                provider_id,
                provider_bot_ref,
                None,
                false,
                Some("bot is not registered in BCS"),
            ));
        }
        Err(error) => return Err(provider_error(error)),
    };
    let message = (!outcome.deleted).then_some("bot is not registered in BCS");
    Ok(delete_provider_bot_response(
        outcome.provider_id,
        outcome.provider_bot_ref,
        Some(outcome.bot_uuid),
        outcome.deleted,
        message,
    ))
}

fn delete_provider_bot_response(
    provider_id: String,
    provider_bot_ref: String,
    bot_uuid: Option<String>,
    deleted: bool,
    message: Option<&'static str>,
) -> Json<Value> {
    let mut body = json!({
        "deleted": deleted,
        "provider_bot_ref": provider_bot_ref,
        "provider_id": provider_id,
    });
    if let Some(bot_uuid) = bot_uuid {
        body["bot_uuid"] = json!(bot_uuid);
    }
    if let Some(message) = message {
        body["message"] = json!(message);
    }
    Json(body)
}

pub async fn resolve_agentpass_bot(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, ProviderRouteError> {
    let provider_id = header_required(&headers, BCN_PROVIDER_ID_HEADER)?;
    let token = bearer_token_with_message(&headers, "valid agentpass token is required")?;
    let Some(agent_code) = state
        .bot_runtime_token_resolver
        .resolve_agentpass_agent_code(&token)
        .await
    else {
        return Ok(Json(json!({
            "agent_code": Value::Null,
            "provider_bot_binding": Value::Null,
            "bot": Value::Null,
        })));
    };

    let binding = state
        .services
        .provider_bot_core
        .get_provider_bot_binding_by_ref(&provider_id, &agent_code)
        .await
        .map_err(provider_error)?;
    let bot = if let Some(binding) = binding.as_ref() {
        state.services.registry.get(&binding.bot_uuid).await
    } else {
        None
    };

    Ok(Json(json!({
        "agent_code": agent_code,
        "provider_bot_binding": binding.map(binding_to_json).unwrap_or(Value::Null),
        "bot": bot.map(|bot| json!(bot)).unwrap_or(Value::Null),
    })))
}

pub async fn disable_provider(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<ProviderInfoResponse>, ProviderRouteError> {
    set_provider_disabled(state, provider_id, headers, uri, true).await
}

pub async fn enable_provider(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<ProviderInfoResponse>, ProviderRouteError> {
    set_provider_disabled(state, provider_id, headers, uri, false).await
}

async fn set_provider_disabled(
    state: HttpAppState,
    provider_id: String,
    headers: HeaderMap,
    uri: Uri,
    disabled: bool,
) -> Result<Json<ProviderInfoResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let authenticated_staff_id = require_staff_no(&state, &headers, &uri).await?;
    let provider = state
        .services
        .provider_management
        .set_provider_disabled(
            &provider_id,
            &provider_admin_token,
            &authenticated_staff_id,
            disabled,
        )
        .await
        .map_err(provider_error)?;
    Ok(Json(provider_to_response(provider).map_err(provider_error)?))
}

fn auth_mode_from_wire(mode: ProviderAuthModeDto) -> ProviderAuthMode {
    match mode {
        ProviderAuthModeDto::StaticBearer => ProviderAuthMode::StaticBearer,
        ProviderAuthModeDto::AgentPass => ProviderAuthMode::AgentPass,
        ProviderAuthModeDto::ProviderAdmin => ProviderAuthMode::ProviderAdmin,
    }
}

fn auth_mode_to_wire(mode: &str) -> ProviderAuthModeDto {
    match mode {
        "agentpass" => ProviderAuthModeDto::AgentPass,
        "provider_admin" => ProviderAuthModeDto::ProviderAdmin,
        _ => ProviderAuthModeDto::StaticBearer,
    }
}

fn coordination_from_wire(config: ProviderCoordinationConfigDto) -> ProviderCoordinationConfig {
    ProviderCoordinationConfig {
        mode: match config.mode {
            ProviderCoordinationModeDto::McporterMcp => CoordinationMode::McporterMcp,
            ProviderCoordinationModeDto::NativeMcp => CoordinationMode::NativeMcp,
            ProviderCoordinationModeDto::NativeTool => CoordinationMode::NativeTool,
            ProviderCoordinationModeDto::Disabled => CoordinationMode::Disabled,
        },
        mcp_server: config.mcp_server,
        mcporter_command: config.mcporter_command,
    }
}

fn coordination_to_wire(config: &Value) -> Option<ProviderCoordinationConfigDto> {
    let coordination = config.get("coordination")?;
    serde_json::from_value::<ProviderCoordinationConfigDto>(coordination.clone()).ok()
}

fn organization_management_from_wire(
    config: ProviderOrganizationManagementConfigDto,
) -> ProviderOrganizationManagementConfig {
    ProviderOrganizationManagementConfig {
        authorized_manager_provider_ids: config.authorized_manager_provider_ids,
    }
}

fn organization_management_to_wire(
    config: ProviderOrganizationManagementConfig,
) -> ProviderOrganizationManagementConfigDto {
    ProviderOrganizationManagementConfigDto {
        authorized_manager_provider_ids: config.authorized_manager_provider_ids,
    }
}

fn bearer_token(headers: &HeaderMap) -> Result<String, ProviderRouteError> {
    bearer_token_with_message(headers, "valid provider admin token is required")
}

fn bearer_token_with_message(
    headers: &HeaderMap,
    message: &'static str,
) -> Result<String, ProviderRouteError> {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(str::to_string)
        .ok_or_else(|| ProviderRouteError::unauthorized(message))
}

fn header_required(headers: &HeaderMap, name: &'static str) -> Result<String, ProviderRouteError> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .ok_or_else(|| ProviderRouteError::bad_request(format!("{name} header is required")))
}

async fn require_staff_no(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<String, ProviderRouteError> {
    state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .map(|staff_no| staff_no.trim().to_string())
        .filter(|staff_no| !staff_no.is_empty())
        .ok_or_else(|| ProviderRouteError::unauthorized("valid human identity is required"))
}

fn provider_error(error: ServiceError) -> ProviderRouteError {
    match error {
        ServiceError::Unauthorized(message) => ProviderRouteError {
            status: StatusCode::UNAUTHORIZED,
            message,
        },
        ServiceError::Forbidden(message) => ProviderRouteError {
            status: StatusCode::FORBIDDEN,
            message,
        },
        ServiceError::InvalidOperation { message, .. } => ProviderRouteError::bad_request(message),
        ServiceError::Conflict(message) => ProviderRouteError {
            status: StatusCode::CONFLICT,
            message,
        },
        ServiceError::BotNotFound(bot_id) | ServiceError::BotNotRegistered(bot_id) => {
            ProviderRouteError {
                status: StatusCode::NOT_FOUND,
                message: format!("bot not found: {bot_id}"),
            }
        }
        other => ProviderRouteError {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            message: other.to_string(),
        },
    }
}

fn provider_to_response(provider: ProviderRecord) -> Result<ProviderInfoResponse, ServiceError> {
    let config: Value = serde_json::from_str(&provider.config)?;
    let downlink = config.get("downlink").unwrap_or(&Value::Null);
    let webhook_url = downlink
        .get("webhook_url")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let auth_mode = downlink
        .get("auth_mode")
        .and_then(Value::as_str)
        .map(auth_mode_to_wire)
        .unwrap_or(ProviderAuthModeDto::StaticBearer);
    let coordination = coordination_to_wire(&config);
    let organization_management = config
        .get("organization_management")
        .map(|_| {
            ProviderOrganizationManagementConfig::from_provider_config(&provider.config)
                .map(organization_management_to_wire)
        })
        .transpose()?;

    Ok(ProviderInfoResponse {
        provider_id: provider.provider_id,
        name: provider.name,
        webhook_url,
        auth_mode,
        coordination,
        organization_management,
        disabled: provider.disabled,
        created_at: provider.created_at,
        updated_at: provider.updated_at,
    })
}

fn binding_to_json(binding: ProviderBotBinding) -> Value {
    json!({
        "bot_uuid": binding.bot_uuid,
        "provider_id": binding.provider_id,
        "provider_bot_ref": binding.provider_bot_ref,
        "disabled": binding.disabled,
        "created_at": binding.created_at,
        "updated_at": binding.updated_at,
    })
}

#[derive(Debug, serde::Deserialize)]
pub struct SwitchBotDeliveryRequest {
    pub bot_id: String,
    pub provider_bot_ref: String,
    pub name: Option<String>,
    pub summary: Option<String>,
}

pub async fn switch_bot_delivery(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<SwitchBotDeliveryRequest>,
) -> Result<Json<Value>, ProviderRouteError> {
    if req.provider_bot_ref.trim().is_empty() {
        return Err(ProviderRouteError::bad_request(
            "provider_bot_ref must not be empty: INVALID_PROVIDER_BOT_REF",
        ));
    }

    let token = bearer_token(&headers)?;

    let provider = state
        .services
        .provider_core
        .authenticate_provider_admin(&token)
        .await
        .map_err(provider_error)?;

    if provider.provider_id != provider_id {
        return Err(ProviderRouteError {
            status: StatusCode::FORBIDDEN,
            message: "provider_id_mismatch".to_string(),
        });
    }

    if !state
        .allowed_switch_provider_ids
        .contains(&provider_id)
    {
        return Err(ProviderRouteError {
            status: StatusCode::FORBIDDEN,
            message: format!(
                "provider '{}' is not allowed to switch bot delivery",
                provider_id
            ),
        });
    }

    let result: SwitchDeliveryToProviderResult = state
        .services
        .bot_management
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: req.bot_id,
            provider_id,
            provider_bot_ref: req.provider_bot_ref,
            name: req.name,
            summary: req.summary,
        })
        .await
        .map_err(switch_delivery_error)?;

    Ok(Json(json!({
        "success": true,
        "data": {
            "bot_id": result.bot_id,
            "provider_id": result.provider_id,
            "provider_bot_ref": result.provider_bot_ref,
            "binding_created_at": result.binding_created_at,
            "idempotent_replay": result.idempotent_replay,
            "websocket_kicked": result.websocket_kicked,
        }
    })))
}

fn switch_delivery_error(error: BotUseCaseError) -> ProviderRouteError {
    match error {
        BotUseCaseError::InvalidBotId(msg) => ProviderRouteError::bad_request(msg),
        BotUseCaseError::InvalidProviderBotRef(msg) => ProviderRouteError::bad_request(msg),
        BotUseCaseError::ProviderNotFound(p) => ProviderRouteError {
            status: StatusCode::NOT_FOUND,
            message: format!("Provider '{}' not found", p),
        },
        BotUseCaseError::ProviderNotReadyForDownlink { provider_id, reason } => ProviderRouteError {
            status: StatusCode::CONFLICT,
            message: format!("Provider '{}' downlink not ready: {}", provider_id, reason),
        },
        BotUseCaseError::BotAlreadyBound {
            bot_id,
            existing_provider_id,
            existing_provider_bot_ref,
        } => ProviderRouteError {
            status: StatusCode::CONFLICT,
            message: format!(
                "Bot '{}' already bound to provider '{}' (ref '{}')",
                bot_id, existing_provider_id, existing_provider_bot_ref
            ),
        },
        BotUseCaseError::Service(ServiceError::BotNotFound(id)) => ProviderRouteError {
            status: StatusCode::NOT_FOUND,
            message: format!("Bot '{}' not found", id),
        },
        other => ProviderRouteError {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            message: other.to_string(),
        },
    }
}
