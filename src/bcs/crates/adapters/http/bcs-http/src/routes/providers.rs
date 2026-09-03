use axum::{
    Json,
    extract::{Path, Query, State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_protocol::{
    BCN_PROVIDER_ID_HEADER, PatchProviderBotRequest, PatchProviderRequest, ProviderAuthModeDto,
    ProviderBotConnectionModeDto, ProviderCoordinationConfigDto, ProviderCoordinationModeDto,
    ProviderInfoResponse, ProviderOrganizationManagementConfigDto, RegisterProviderBotRequest,
    RegisterProviderBotResponse, RegisterProviderRequest, RegisterProviderResponse,
};
use bcs_service_api::application::v1::{
    ApplicationError, BotInternalAttributes, FriendCheckInStrategy, InternalBotAttributesService,
    PatchBotInternalAttributes, UserVisibility,
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotUseCaseError, CoordinationMode, DeleteProviderBotCommand,
    ProviderAuthMode,
    ProviderBotBinding, ProviderBotConnectionMode, ProviderBotRosterItem,
    ProviderBotTaskModesFilter, ProviderCoordinationConfig, ProviderOrganizationManagementConfig,
    ProviderRecord, RegisterProviderBotCommand, RegisterProviderCommand, ServiceError,
    SwitchDeliveryToProviderCommand, SwitchDeliveryToProviderResult, TaskModeMatch,
    UpdateProviderBotCommand, UpdateProviderCommand,
};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value, json};
use tracing::{info, warn};

use crate::mapping::capabilities::{to_core_skill, to_wire_skill};
use crate::state::{HttpAppState, VisibilitySyncRequest};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PatchProviderBotAttributesRequest {
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub visibility: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub user_visibility: Option<UserVisibility>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub friend_ext: Option<Map<String, Value>>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub friend_check_in_strategy: Option<FriendCheckInStrategy>,
}

impl PatchProviderBotAttributesRequest {
    fn is_empty(&self) -> bool {
        self.visibility.is_none()
            && self.user_visibility.is_none()
            && self.friend_ext.is_none()
            && self.friend_check_in_strategy.is_none()
    }

    fn into_command(self, bot_id: String) -> PatchBotInternalAttributes {
        PatchBotInternalAttributes {
            bot_id,
            visibility: self.visibility,
            user_visibility: self.user_visibility,
            friend_ext: self.friend_ext,
            friend_check_in_strategy: self.friend_check_in_strategy,
        }
    }
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
            admin_callback_url: req.admin_callback_url,
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
    Ok(Json(
        provider_to_response(provider).map_err(provider_error)?,
    ))
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
            admin_callback_url: req.admin_callback_url,
            protocol_version: req.protocol_version,
            coordination: req.coordination.map(coordination_from_wire),
            organization_management: req
                .organization_management
                .map(organization_management_from_wire),
        })
        .await
        .map_err(provider_error)?;
    Ok(Json(
        provider_to_response(provider).map_err(provider_error)?,
    ))
}

pub async fn register_provider_bot(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<RegisterProviderBotRequest>,
) -> Result<Json<RegisterProviderBotResponse>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let allowed_switch_provider = state.allowed_switch_provider_ids.contains(&provider_id);
    let connection_mode = req
        .connection_mode
        .unwrap_or(ProviderBotConnectionModeDto::Gateway);
    // plugin mode is accepted only for allow-listed providers (§3.0 admission gate).
    if matches!(connection_mode, ProviderBotConnectionModeDto::Plugin) && !allowed_switch_provider {
        return Err(ProviderRouteError::bad_request(
            "connection_mode plugin requires an allow-listed provider",
        ));
    }
    let plugin_mode = matches!(connection_mode, ProviderBotConnectionModeDto::Plugin);
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
            // Gateway mode over an allow-listed provider rejects a collision where
            // provider_bot_ref is already used as a bot_uuid; plugin mode relaxes
            // this so W-before-P /补注册 over an existing real-token bot proceeds to
            // the token-preserving soft-merge path.
            reject_existing_bot_uuid: allowed_switch_provider && !plugin_mode,
            connection_mode: connection_mode_from_wire(connection_mode),
        })
        .await
        .map_err(provider_error)?;

    if allowed_switch_provider && outcome.created && outcome.actor_kind == ActorKind::Bot {
        match outcome.capabilities.clone() {
            Some(capabilities) => {
                state.dispatch_visibility_sync(VisibilitySyncRequest {
                    bot_uuid: outcome.bot_uuid.clone(),
                    visibility: capabilities.visibility.clone(),
                    capabilities,
                    actor_kind: outcome.actor_kind,
                });
            }
            None => {
                warn!(
                    provider_id = %outcome.provider_id,
                    bot_uuid = %outcome.bot_uuid,
                    "register_provider_bot: allowlisted provider but capabilities missing; \
                     skipping bcs-fuse sync"
                );
            }
        }
    }

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

/// `GET /providers/{provider_id}/bots/by-task-modes` — internal (non-OpenAPI)
/// roster consumed by backend task discovery/dispatch. Admission mirrors
/// `switch_bot_delivery`: the Bearer must authenticate the path provider admin
/// and that `provider_id` must be in `allowed_switch_provider_ids`. The roster
/// is env-scoped — it returns all current-env bots whose control-plane toggles
/// satisfy the filter, and is intentionally not intersected with provider bot
/// bindings.
pub async fn list_provider_bots_by_task_modes(
    State(state): State<HttpAppState>,
    Path(provider_id): Path<String>,
    headers: HeaderMap,
    Query(params): Query<TaskModesQueryParams>,
) -> Result<Json<Value>, ProviderRouteError> {
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

    if !state.allowed_switch_provider_ids.contains(&provider_id) {
        return Err(ProviderRouteError {
            status: StatusCode::FORBIDDEN,
            message: format!(
                "provider '{}' is not allowed to access the task-mode roster",
                provider_id
            ),
        });
    }

    let filter = ProviderBotTaskModesFilter {
        task_claim_mode: parse_task_mode_toggle("task_claim_mode", &params.task_claim_mode)?,
        task_dream_mode: parse_task_mode_toggle("task_dream_mode", &params.task_dream_mode)?,
        visibility: parse_non_empty_query(&params.visibility)?,
        status: parse_actor_status(&params.status)?,
        user_visibility: parse_user_visibility(&params.user_visibility)?,
        match_mode: match params
            .match_mode
            .as_deref()
            .map(str::trim)
            .map(|value| value.eq_ignore_ascii_case("all"))
        {
            Some(true) => TaskModeMatch::All,
            _ => TaskModeMatch::Any,
        },
    };
    let items = state
        .services
        .provider_management
        .list_provider_bots_by_task_modes(filter)
        .await
        .map_err(provider_error)?;
    let items: Vec<Value> = items.into_iter().map(roster_item_to_json).collect();
    Ok(Json(json!({ "items": items })))
}

pub async fn get_provider_bot_attributes(
    State(state): State<HttpAppState>,
    Path((provider_id, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<Json<BotInternalAttributes>, ProviderRouteError> {
    require_provider_bot_attributes_access(&state, &provider_id, &bot_uuid, &headers).await?;
    let attributes = internal_bot_attributes_service(&state)?
        .get(bot_uuid.clone())
        .await
        .map_err(internal_attributes_error)?;
    info!(
        provider_id,
        bot_uuid, "Provider Bot attributes read completed"
    );
    Ok(Json(attributes))
}

pub async fn patch_provider_bot_attributes(
    State(state): State<HttpAppState>,
    Path((provider_id, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
    body: Result<Json<PatchProviderBotAttributesRequest>, JsonRejection>,
) -> Result<Json<BotInternalAttributes>, ProviderRouteError> {
    require_provider_bot_attributes_access(&state, &provider_id, &bot_uuid, &headers).await?;
    let Json(body) = body.map_err(|_| {
        warn!(
            provider_id,
            bot_uuid,
            failure = "invalid_json_body",
            "Provider Bot attributes patch rejected"
        );
        ProviderRouteError::bad_request("request body is invalid")
    })?;
    if body.is_empty() {
        return Err(ProviderRouteError::bad_request(
            "bot attributes patch must contain at least one field",
        ));
    }
    info!(
        provider_id,
        bot_uuid,
        has_visibility = body.visibility.is_some(),
        has_user_visibility = body.user_visibility.is_some(),
        has_friend_ext = body.friend_ext.is_some(),
        friend_ext_key_count = body.friend_ext.as_ref().map(|value| value.len()),
        has_friend_check_in_strategy = body.friend_check_in_strategy.is_some(),
        "Provider Bot attributes patch accepted"
    );
    let attributes = internal_bot_attributes_service(&state)?
        .patch(body.into_command(bot_uuid.clone()))
        .await
        .map_err(internal_attributes_error)?;
    info!(
        provider_id,
        bot_uuid, "Provider Bot attributes patch completed"
    );
    Ok(Json(attributes))
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

pub async fn patch_provider_bot(
    State(state): State<HttpAppState>,
    Path((provider_id, provider_bot_ref)): Path<(String, String)>,
    headers: HeaderMap,
    Json(req): Json<PatchProviderBotRequest>,
) -> Result<Json<Value>, ProviderRouteError> {
    let provider_admin_token = bearer_token(&headers)?;
    let outcome = state
        .services
        .provider_management
        .update_provider_bot(UpdateProviderBotCommand {
            provider_id,
            provider_admin_token,
            provider_bot_ref,
            name: req.name,
            summary: req.summary,
            domains: req.domains,
            skills: req
                .skills
                .map(|skills| skills.into_iter().map(to_core_skill).collect()),
            scopes: req.scopes,
            visibility: req.visibility,
        })
        .await
        .map_err(provider_error)?;

    Ok(Json(json!({
        "bot_uuid": outcome.bot_uuid,
        "provider_id": outcome.provider_id,
        "provider_bot_ref": outcome.provider_bot_ref,
        "name": outcome.name,
        "summary": outcome.summary,
        "domains": outcome.domains,
        "skills": outcome.skills.into_iter().map(to_wire_skill).collect::<Vec<_>>(),
        "scopes": outcome.scopes,
        "visibility": outcome.visibility,
    })))
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
    Ok(Json(
        provider_to_response(provider).map_err(provider_error)?,
    ))
}

fn auth_mode_from_wire(mode: ProviderAuthModeDto) -> ProviderAuthMode {
    match mode {
        ProviderAuthModeDto::StaticBearer => ProviderAuthMode::StaticBearer,
        ProviderAuthModeDto::AgentPass => ProviderAuthMode::AgentPass,
        ProviderAuthModeDto::ProviderAdmin => ProviderAuthMode::ProviderAdmin,
    }
}

fn connection_mode_from_wire(mode: ProviderBotConnectionModeDto) -> ProviderBotConnectionMode {
    match mode {
        ProviderBotConnectionModeDto::Gateway => ProviderBotConnectionMode::Gateway,
        ProviderBotConnectionModeDto::Plugin => ProviderBotConnectionMode::Plugin,
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
        worker_send_task_message_enabled: config.worker_send_task_message_enabled,
        mcp_server: config.mcp_server,
        mcporter_command: config.mcporter_command,
        tool_name_mapping: config.tool_name_mapping,
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

async fn require_provider_bot_attributes_access(
    state: &HttpAppState,
    provider_id: &str,
    bot_uuid: &str,
    headers: &HeaderMap,
) -> Result<(), ProviderRouteError> {
    let provider_admin_token = bearer_token(headers)?;
    state
        .services
        .provider_management
        .get_active_provider(provider_id, &provider_admin_token)
        .await
        .map_err(provider_error)?;

    // COSEC: Attribute access is fail-closed: only an authenticated Provider
    // explicitly listed for backend operations may manage BCS Bot attributes.
    if !state
        .allowed_switch_provider_ids
        .iter()
        .any(|configured_id| configured_id == provider_id)
    {
        warn!(
            provider_id,
            bot_uuid,
            failure = "provider_not_allowed",
            "Provider Bot attributes access rejected"
        );
        return Err(ProviderRouteError {
            status: StatusCode::FORBIDDEN,
            message: "provider is not allowed to manage bot attributes".to_string(),
        });
    }

    Ok(())
}

fn internal_bot_attributes_service(
    state: &HttpAppState,
) -> Result<&std::sync::Arc<dyn InternalBotAttributesService>, ProviderRouteError> {
    state
        .internal_bot_attributes_service
        .as_ref()
        .ok_or_else(|| ProviderRouteError {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            message: "bot attributes service is not configured".to_string(),
        })
}

fn internal_attributes_error(error: ApplicationError) -> ProviderRouteError {
    match error {
        ApplicationError::InvalidInput { message, .. } => ProviderRouteError::bad_request(message),
        ApplicationError::Unauthenticated => {
            ProviderRouteError::unauthorized("authentication is required")
        }
        ApplicationError::Forbidden(message) | ApplicationError::ForbiddenCode { message, .. } => {
            ProviderRouteError {
                status: StatusCode::FORBIDDEN,
                message,
            }
        }
        ApplicationError::NotFound { message, .. } => ProviderRouteError {
            status: StatusCode::NOT_FOUND,
            message,
        },
        ApplicationError::Conflict { message, .. } => ProviderRouteError {
            status: StatusCode::CONFLICT,
            message,
        },
        ApplicationError::Gone { message, .. } => ProviderRouteError {
            status: StatusCode::GONE,
            message,
        },
        ApplicationError::QuotaExceeded { message, .. } => ProviderRouteError {
            status: StatusCode::TOO_MANY_REQUESTS,
            message,
        },
        ApplicationError::PayloadTooLarge { message, .. } => ProviderRouteError {
            status: StatusCode::PAYLOAD_TOO_LARGE,
            message,
        },
        ApplicationError::Unprocessable { message, .. } => ProviderRouteError {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            message,
        },
        ApplicationError::BadGateway { message, .. } => ProviderRouteError {
            status: StatusCode::BAD_GATEWAY,
            message,
        },
        ApplicationError::Internal(message) => ProviderRouteError {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            message,
        },
    }
}

fn deserialize_present_non_null<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

fn bearer_token(headers: &HeaderMap) -> Result<String, ProviderRouteError> {
    bearer_token_with_message(headers, "valid provider admin token is required")
}

fn bearer_token_with_message(
    headers: &HeaderMap,
    message: &'static str,
) -> Result<String, ProviderRouteError> {
    crate::headers::extract_bearer_token(headers)
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
    let admin_callback_url = config
        .get("admin_callback_url")
        .and_then(Value::as_str)
        .map(str::to_string);
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
        admin_callback_url,
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

fn roster_item_to_json(item: ProviderBotRosterItem) -> Value {
    json!({
        "bot_id": item.bot_id,
        "name": item.name,
        "env": item.env,
        "task_claim_mode": item.task_claim_mode,
        "task_dream_mode": item.task_dream_mode,
        "updated_at": item.updated_at,
        "visibility": item.visibility,
        "created_by": item.created_by,
        "status": item.status,
        "user_visibility": item.user_visibility,
    })
}

/// Query params for `GET /providers/{provider_id}/bots/by-task-modes`. Toggles
/// arrive as strings so empty/absent values can be tolerated as "do not filter".
#[derive(Debug, Default, serde::Deserialize)]
pub struct TaskModesQueryParams {
    pub task_claim_mode: Option<String>,
    pub task_dream_mode: Option<String>,
    #[serde(rename = "match")]
    pub match_mode: Option<String>,
    pub visibility: Option<String>,
    pub status: Option<String>,
    pub user_visibility: Option<String>,
}

/// Parse a task-mode toggle query param. `None`/empty => do not filter on this
/// toggle; `true`/`1` => filter for the toggle ON; `false`/`0` => filter OFF.
fn parse_non_empty_query(value: &Option<String>) -> Result<Option<String>, ProviderRouteError> {
    match value.as_deref().map(str::trim) {
        None | Some("") => Ok(None),
        Some(value) => Ok(Some(value.to_string())),
    }
}

fn parse_actor_status(value: &Option<String>) -> Result<Option<ActorStatus>, ProviderRouteError> {
    match value.as_deref().map(str::trim).map(str::to_ascii_lowercase) {
        None => Ok(None),
        Some(value) if value.is_empty() => Ok(None),
        Some(value) => match value.as_str() {
            "online" => Ok(Some(ActorStatus::Online)),
            "hidden" => Ok(Some(ActorStatus::Hidden)),
            other => Err(ProviderRouteError::bad_request(format!(
                "invalid status value '{other}'; expected online|hidden"
            ))),
        },
    }
}

fn parse_user_visibility(
    value: &Option<String>,
) -> Result<Option<UserVisibility>, ProviderRouteError> {
    match value.as_deref().map(str::trim).map(str::to_ascii_lowercase) {
        None => Ok(None),
        Some(value) if value.is_empty() => Ok(None),
        Some(value) => match value.as_str() {
            "public" => Ok(Some(UserVisibility::Public)),
            "protected" => Ok(Some(UserVisibility::Protected)),
            "private" => Ok(Some(UserVisibility::Private)),
            other => Err(ProviderRouteError::bad_request(format!(
                "invalid user_visibility value '{other}'; expected public|protected|private"
            ))),
        },
    }
}

fn parse_task_mode_toggle(
    name: &str,
    value: &Option<String>,
) -> Result<Option<bool>, ProviderRouteError> {
    match value.as_deref().map(str::trim) {
        None | Some("") => Ok(None),
        Some("true") | Some("1") => Ok(Some(true)),
        Some("false") | Some("0") => Ok(Some(false)),
        Some(other) => Err(ProviderRouteError::bad_request(format!(
            "invalid {name} value '{other}'; expected true|false"
        ))),
    }
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

    if !state.allowed_switch_provider_ids.contains(&provider_id) {
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
        BotUseCaseError::ProviderNotReadyForDownlink {
            provider_id,
            reason,
        } => ProviderRouteError {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn internal_attribute_application_errors_keep_their_http_status() {
        let cases = vec![
            (
                ApplicationError::invalid("invalid", "invalid"),
                StatusCode::BAD_REQUEST,
            ),
            (ApplicationError::Unauthenticated, StatusCode::UNAUTHORIZED),
            (
                ApplicationError::forbidden("forbidden"),
                StatusCode::FORBIDDEN,
            ),
            (
                ApplicationError::not_found("missing", "missing"),
                StatusCode::NOT_FOUND,
            ),
            (
                ApplicationError::conflict("conflict", "conflict"),
                StatusCode::CONFLICT,
            ),
            (
                ApplicationError::Gone {
                    code: "gone".into(),
                    message: "gone".into(),
                },
                StatusCode::GONE,
            ),
            (
                ApplicationError::QuotaExceeded {
                    code: "quota".into(),
                    message: "quota".into(),
                },
                StatusCode::TOO_MANY_REQUESTS,
            ),
            (
                ApplicationError::payload_too_large("large", "large"),
                StatusCode::PAYLOAD_TOO_LARGE,
            ),
            (
                ApplicationError::unprocessable("unprocessable", "unprocessable"),
                StatusCode::UNPROCESSABLE_ENTITY,
            ),
            (
                ApplicationError::bad_gateway("upstream", "upstream"),
                StatusCode::BAD_GATEWAY,
            ),
            (
                ApplicationError::internal("internal"),
                StatusCode::INTERNAL_SERVER_ERROR,
            ),
        ];

        for (error, expected_status) in cases {
            assert_eq!(internal_attributes_error(error).status, expected_status);
        }
    }
}
