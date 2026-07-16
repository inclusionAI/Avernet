use axum::{
    Json, body::{Body, Bytes},
    extract::{Path, Query, State},
    http::{HeaderMap, Method, Response, Uri},
};
use bcs_channel_api::{
    ChannelHttpMethod, ChannelHttpRequest,
};
use bcs_domain::{
    BindingTarget, ChannelBinding, ChannelConfig, ChannelType, GroupChatScope, Visibility,
};
use bcs_service_api::{ChannelUseCaseError, CreateBindingCommand, ServiceError};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;

#[derive(Debug, Deserialize)]
pub struct CreateBindingRequest {
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub target: BindingTarget,
    #[serde(default)]
    pub group_chat_scope: Option<GroupChatScope>,
    pub outbound_visibility: Visibility,
    #[serde(default)]
    pub env: Option<String>,
    pub config: ChannelConfig,
}

#[derive(Debug, Serialize)]
pub struct BindingResponse {
    pub id: String,
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub target: BindingTarget,
    pub group_chat_scope: Option<GroupChatScope>,
    pub outbound_visibility: Visibility,
    pub env: String,
    pub status: bcs_domain::BindingStatus,
    pub created_by: Option<String>,
    pub config: ChannelConfig,
}

impl From<ChannelBinding> for BindingResponse {
    fn from(binding: ChannelBinding) -> Self {
        Self {
            id: binding.id,
            channel_type: binding.channel_type,
            account_ref: binding.account_ref,
            target: binding.target,
            group_chat_scope: binding.group_chat_scope,
            outbound_visibility: binding.outbound_visibility,
            env: binding.env,
            status: binding.status,
            created_by: binding.created_by,
            config: binding.config,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct BindingListResponse {
    pub items: Vec<BindingResponse>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BindingTargetType {
    Bot,
    Group,
}

#[derive(Debug, Deserialize)]
pub struct ListBindingsByTargetQuery {
    pub target_type: BindingTargetType,
    pub target_id: String,
    pub channel_type: Option<ChannelType>,
}

pub async fn provider_http_ingress(
    State(state): State<HttpAppState>,
    method: Method,
    headers: HeaderMap,
    uri: Uri,
    body: Bytes,
) -> Result<Response<Body>, HttpAdapterError> {
    let Some(ingress) = state.channel_http_ingress.as_ref() else {
        return Err(HttpAdapterError::NotFound(
            "channel HTTP ingress is disabled".to_string(),
        ));
    };
    let Some(method) = channel_http_method(&method) else {
        return Err(HttpAdapterError::BadRequest(
            "unsupported channel HTTP method".to_string(),
        ));
    };
    let request = ChannelHttpRequest {
        method,
        path: uri.path().to_string(),
        query: uri.query().map(str::to_string),
        headers: headers
            .iter()
            .filter_map(|(name, value)| {
                value
                    .to_str()
                    .ok()
                    .map(|value| (name.as_str().to_string(), value.to_string()))
            })
            .collect(),
        body,
    };
    let Some(response) = ingress.handle_http(request).await else {
        return Err(HttpAdapterError::NotFound(
            "channel HTTP ingress route is not registered".to_string(),
        ));
    };
    channel_http_response(response)
}

pub async fn create_binding(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<CreateBindingRequest>,
) -> Result<Json<BindingResponse>, HttpAdapterError> {
    let created_by = require_staff_no(&state, &headers, &uri).await?;
    let binding = state
        .services
        .channel
        .create_binding(CreateBindingCommand {
            channel_type: req.channel_type,
            account_ref: req.account_ref,
            target: req.target,
            group_chat_scope: req.group_chat_scope,
            outbound_visibility: req.outbound_visibility,
            env: req.env.unwrap_or_default(),
            created_by: Some(created_by),
            config: req.config,
        })
        .await
        .map_err(channel_error)?;
    Ok(Json(binding.into()))
}

pub async fn list_bindings(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<BindingListResponse>, HttpAdapterError> {
    let _staff_no = require_staff_no(&state, &headers, &uri).await?;
    let items = state
        .services
        .channel
        .list_bindings()
        .await
        .map_err(channel_error)?;
    let items = items.into_iter().map(BindingResponse::from).collect();
    Ok(Json(BindingListResponse { items }))
}

pub async fn list_bindings_by_target(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<ListBindingsByTargetQuery>,
) -> Result<Json<BindingListResponse>, HttpAdapterError> {
    let _staff_no = require_staff_no(&state, &headers, &uri).await?;
    let (target, channel_type) = normalize_target_query(query)?;
    let items = state
        .services
        .channel
        .list_bindings_by_target(target, channel_type)
        .await
        .map_err(channel_error)?;
    let items = items.into_iter().map(BindingResponse::from).collect();
    Ok(Json(BindingListResponse { items }))
}

pub async fn set_binding_status(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<UpdateBindingRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let _staff_no = require_staff_no(&state, &headers, &uri).await?;
    let has_active = req.active.is_some();
    let has_config = req.config.is_some();
    if !has_active && !has_config {
        return Err(HttpAdapterError::BadRequest(
            "active or config is required".to_string(),
        ));
    }
    if has_active && has_config {
        return Err(HttpAdapterError::BadRequest(
            "update active and config separately".to_string(),
        ));
    }
    if let Some(active) = req.active {
        state
            .services
            .channel
            .set_binding_status(&id, active)
            .await
            .map_err(channel_error)?;
    }
    if let Some(config) = req.config {
        state
            .services
            .channel
            .update_binding_config(&id, config)
            .await
            .map_err(channel_error)?;
    }
    Ok(Json(serde_json::json!({ "ok": true })))
}

#[derive(Debug, Deserialize)]
pub struct UpdateBindingRequest {
    pub active: Option<bool>,
    pub config: Option<Value>,
}

pub async fn delete_binding(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, HttpAdapterError> {
    let _staff_no = require_staff_no(&state, &headers, &uri).await?;
    state
        .services
        .channel
        .delete_binding(&id)
        .await
        .map_err(channel_error)?;
    Ok(Json(serde_json::json!({ "ok": true })))
}

fn channel_http_method(method: &Method) -> Option<ChannelHttpMethod> {
    match *method {
        Method::GET => Some(ChannelHttpMethod::Get),
        Method::POST => Some(ChannelHttpMethod::Post),
        Method::PUT => Some(ChannelHttpMethod::Put),
        Method::PATCH => Some(ChannelHttpMethod::Patch),
        Method::DELETE => Some(ChannelHttpMethod::Delete),
        _ => None,
    }
}

fn channel_http_response(
    response: bcs_channel_api::ChannelHttpResponse,
) -> Result<Response<Body>, HttpAdapterError> {
    let mut builder = Response::builder().status(response.status);
    for (name, value) in response.headers {
        builder = builder.header(name, value);
    }
    builder
        .body(Body::from(response.body))
        .map_err(|error| HttpAdapterError::Service(ServiceError::InternalError(error.to_string())))
}

async fn require_staff_no(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<String, HttpAdapterError> {
    state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .map(|staff_no| staff_no.trim().to_string())
        .filter(|staff_no| !staff_no.is_empty())
        .ok_or_else(|| {
            HttpAdapterError::Unauthorized("valid human identity is required".to_string())
        })
}

fn channel_error(error: ChannelUseCaseError) -> HttpAdapterError {
    match error {
        ChannelUseCaseError::NotFound(id) => HttpAdapterError::NotFound(id),
        ChannelUseCaseError::InvalidParams(message) => HttpAdapterError::BadRequest(message),
        ChannelUseCaseError::Internal(error) => match error {
            ServiceError::Conflict(message) => HttpAdapterError::Conflict(message),
            other => HttpAdapterError::Service(other),
        },
    }
}

fn normalize_target_query(
    query: ListBindingsByTargetQuery,
) -> Result<(BindingTarget, Option<ChannelType>), HttpAdapterError> {
    let target_id = query.target_id.trim();
    if target_id.is_empty() {
        return Err(HttpAdapterError::BadRequest(
            "target_id is required".to_string(),
        ));
    }
    let channel_type = query
        .channel_type
        .map(|channel_type| channel_type.trim().to_string())
        .filter(|channel_type| !channel_type.is_empty());
    let target = match query.target_type {
        BindingTargetType::Bot => BindingTarget::Bot {
            bot_id: target_id.to_string(),
        },
        BindingTargetType::Group => BindingTarget::Group {
            group_id: target_id.to_string(),
        },
    };
    Ok((target, channel_type))
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{BindingStatus, GroupChatScope};

    #[test]
    fn binding_response_uses_service_redacted_config() {
        let response = BindingResponse::from(ChannelBinding {
            id: "binding_1".to_string(),
            channel_type: "dingtalk".to_string(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Bot {
                bot_id: "bot_1:user_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "prod".to_string(),
            status: BindingStatus::Active,
            created_by: Some("creator".to_string()),
            config: serde_json::json!({
                "robot_code": "robot_1",
                "client_id": "robot_1",
                "client_secret": "<redacted>",
                "send_mode": {
                    "mode": "streaming_card",
                    "card_template_id": "card_tpl_123"
                }
            }),
        });

        let json = serde_json::to_value(response).expect("serialize response");

        assert_eq!(json["channel_type"], "dingtalk");
        assert!(json.get("created_at").is_none());
        assert_eq!(json["config"]["send_mode"]["mode"], "streaming_card");
        assert_eq!(
            json["config"]["send_mode"]["card_template_id"],
            "card_tpl_123"
        );
        assert_eq!(json["config"]["client_secret"], "<redacted>");
    }

    #[test]
    fn create_binding_request_accepts_missing_env() {
        let request: CreateBindingRequest = serde_json::from_value(serde_json::json!({
            "channel_type": "dingtalk",
            "account_ref": "robot_1",
            "target": { "bot": { "bot_id": "bot_1" } },
            "outbound_visibility": "full_transcript",
            "config": {}
        }))
        .expect("create binding request should not require client env");

        assert!(request.env.is_none());
    }

    #[test]
    fn target_query_builds_trimmed_bot_target_and_channel_filter() {
        let (target, channel_type) = normalize_target_query(ListBindingsByTargetQuery {
            target_type: BindingTargetType::Bot,
            target_id: " bot_1:user_1 ".to_string(),
            channel_type: Some(" dingtalk ".to_string()),
        })
        .expect("valid target query");

        assert_eq!(
            target,
            BindingTarget::Bot {
                bot_id: "bot_1:user_1".to_string()
            }
        );
        assert_eq!(channel_type.as_deref(), Some("dingtalk"));
    }

    #[test]
    fn target_query_rejects_blank_target_id() {
        let error = normalize_target_query(ListBindingsByTargetQuery {
            target_type: BindingTargetType::Group,
            target_id: "   ".to_string(),
            channel_type: None,
        })
        .expect_err("blank target id must fail");

        assert!(matches!(
            error,
            HttpAdapterError::BadRequest(message) if message == "target_id is required"
        ));
    }
}
