use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_service_api::{
    BotActor, CallerContext, DeliveryType, GroupCallbackCommand, GroupChatCommand,
    GroupDetailCommand, GroupHistoryCommand, GroupMessage, GroupMessageType, GroupUseCaseError,
    HumanActor, MessageDeliveryResult, MessageRole, PersistentGroupSendCommand, ServiceError,
};
use serde::Deserialize;
use serde_json::Value;

use crate::state::HttpAppState;


#[derive(Debug, Deserialize)]
pub struct ChatRequest {
    pub message: String,
    #[serde(default)]
    pub from: Option<String>,
    #[serde(default, alias = "sessionId")]
    pub session_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct CallbackRequest {
    pub message: String,
    #[serde(default)]
    pub mentions: Option<Vec<String>>,
    #[serde(default)]
    pub metadata: Option<Value>,
}

#[derive(Debug, Deserialize)]
pub struct SendMessageRequest {
    pub sender: String,
    pub content: String,
    #[serde(default)]
    pub message_type: Option<GroupMessageType>,
    #[serde(default)]
    pub role: Option<MessageRole>,
}

#[derive(Debug, Deserialize, Default)]
pub struct GetMessagesQuery {
    #[serde(default)]
    pub view_bot_id: Option<String>,
    #[serde(default)]
    pub limit: Option<u64>,
    #[serde(default)]
    pub before: Option<u64>,
}

#[derive(Debug)]
pub struct LegacyGroupError {
    status: StatusCode,
    message: String,
}

impl LegacyGroupError {
    fn new(status: StatusCode, message: impl Into<String>) -> Self {
        Self {
            status,
            message: message.into(),
        }
    }

    fn unauthorized(message: impl Into<String>) -> Self {
        Self::new(StatusCode::UNAUTHORIZED, message)
    }

    fn forbidden(message: impl Into<String>) -> Self {
        Self::new(StatusCode::FORBIDDEN, message)
    }

    fn bad_request(message: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, message)
    }

    fn not_found(message: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_FOUND, message)
    }
}

impl IntoResponse for LegacyGroupError {
    fn into_response(self) -> Response {
        let status = self.status;
        (
            status,
            Json(serde_json::json!({
                "error": self.message,
                "status": status.as_u16(),
            })),
        )
            .into_response()
    }
}

pub(crate) struct HttpGroupCaller {
    pub(crate) actor_id: String,
    pub(crate) staff_no: String,
    pub(crate) nick_name: Option<String>,
}

pub(crate) enum GroupChatCaller {
    Bot { bot_uuid: String },
    Human(HttpGroupCaller),
}

pub async fn group_chat(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<ChatRequest>,
) -> Result<Json<Value>, LegacyGroupError> {
    ensure_group_message_group_exists(&state, &group_id).await?;
    let caller = resolve_group_chat_caller(&state, &headers, &uri).await?;
    let outcome = state
        .services
        .message_flow
        .handle_group_chat(GroupChatCommand {
            caller: group_chat_caller_context(&caller),
            group_id: group_id.clone(),
            requested_sender_id: req.from,
            message: req.message,
            session_id: req.session_id,
        })
        .await
        .map_err(service_error_to_legacy)?;

    Ok(Json(serde_json::json!({
        "delivered": outcome.delivered_count > 0,
        "group_id": outcome.group_id,
        "driver_bot": outcome.driver_bot_id,
        "delivered_count": outcome.delivered_count,
        "failed_count": outcome.failed_count,
        "delivery_results": delivery_results_json(&outcome.delivery_results),
        "mentions": outcome.mentions,
    })))
}

pub async fn group_callback(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    Json(req): Json<CallbackRequest>,
) -> Result<Json<Value>, LegacyGroupError> {
    let group = state
        .services
        .group_query
        .get_group(GroupDetailCommand {
            group_id: group_id.clone(),
        })
        .await
        .map_err(group_use_case_error_to_legacy)?;

    let outcome = state
        .services
        .message_flow
        .handle_group_callback(GroupCallbackCommand {
            group_id: group_id.clone(),
            message: req.message,
            mentions: req.mentions.unwrap_or_default(),
            metadata: req.metadata,
            store_message: state.store_messages,
        })
        .await
        .map_err(service_error_to_legacy)?;

    Ok(Json(serde_json::json!({
        "delivered": outcome.delivered_count > 0,
        "group_id": group_id,
        "driver_bot": group.driver_bot_id,
        "delivered_count": outcome.delivered_count,
        "failed_count": outcome.failed_count,
        "delivery_results": delivery_results_json(&outcome.delivery_results),
        "mentions": outcome.mentions,
    })))
}

pub async fn send_message(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<SendMessageRequest>,
) -> Result<Json<Value>, LegacyGroupError> {
    ensure_group_message_group_exists(&state, &id).await?;
    let caller = human_caller_from_identity(&state, &headers, &uri).await?;
    let outcome = state
        .services
        .message_flow
        .handle_persistent_group_send(PersistentGroupSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: caller.actor_id,
                staff_no: caller.staff_no,
            }),
            group_id: id,
            sender: req.sender,
            content: req.content,
            message_type: req.message_type.unwrap_or_default(),
            role: req.role.unwrap_or_default(),
            max_group_messages: state.max_group_messages,
            store_messages: state.store_messages,
        })
        .await
        .map_err(service_error_to_legacy)?;

    Ok(Json(serde_json::json!({
        "message_id": outcome.message_id,
        "routed_to": outcome.routed_to,
        "mentions": outcome.mentions,
    })))
}

pub async fn get_messages(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<GetMessagesQuery>,
) -> Result<Json<Vec<GroupMessage>>, LegacyGroupError> {
    let caller = human_caller_from_identity(&state, &headers, &uri).await?;
    let result = state
        .services
        .group_message_history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: caller.actor_id,
                staff_no: caller.staff_no,
            }),
            group_id: id,
            view_bot_id: query.view_bot_id,
            limit: query.limit.unwrap_or(u64::MAX),
            before: query.before,
        })
        .await
        .map_err(group_use_case_error_to_legacy)?;

    Ok(Json(result.messages))
}

async fn ensure_group_message_group_exists(
    state: &HttpAppState,
    group_id: &str,
) -> Result<(), LegacyGroupError> {
    state
        .services
        .group_query
        .get_group(GroupDetailCommand {
            group_id: group_id.to_string(),
        })
        .await
        .map(|_| ())
        .map_err(group_use_case_error_to_legacy)
}

pub(crate) fn group_chat_caller_context(caller: &GroupChatCaller) -> CallerContext {
    match caller {
        GroupChatCaller::Bot { bot_uuid } => CallerContext::Bot(BotActor {
            bot_uuid: bot_uuid.clone(),
        }),
        GroupChatCaller::Human(human) => CallerContext::Human(HumanActor {
            actor_id: human.actor_id.clone(),
            staff_no: human.staff_no.clone(),
        }),
    }
}

pub(crate) fn delivery_results_json(results: &[MessageDeliveryResult]) -> Vec<Value> {
    results
        .iter()
        .map(|result| {
            let mut json = serde_json::json!({
                "bot_uuid": result.bot_uuid,
                "delivery_type": delivery_slug(result.delivery_type),
                "success": result.success,
            });
            if let Some(error) = &result.error {
                json["error"] = Value::String(error.clone());
            }
            json
        })
        .collect()
}

fn service_error_to_legacy(error: ServiceError) -> LegacyGroupError {
    match error {
        ServiceError::GroupNotFound(group_id) => {
            LegacyGroupError::not_found(format!("Group not found: {group_id}"))
        }
        ServiceError::Unauthorized(message) => LegacyGroupError::forbidden(message),
        ServiceError::Forbidden(message) => LegacyGroupError::forbidden(message),
        ServiceError::InvalidOperation { message, .. }
        | ServiceError::MessageLimitReached(message) => LegacyGroupError::bad_request(message),
        other => LegacyGroupError::new(StatusCode::INTERNAL_SERVER_ERROR, other.to_string()),
    }
}

fn group_use_case_error_to_legacy(error: GroupUseCaseError) -> LegacyGroupError {
    match error {
        GroupUseCaseError::Unauthorized(message) => LegacyGroupError::unauthorized(message),
        GroupUseCaseError::Forbidden(message) => LegacyGroupError::forbidden(message),
        GroupUseCaseError::InvalidGroupId(message)
        | GroupUseCaseError::InvalidGroupStatus(message)
        | GroupUseCaseError::InvalidProposal(message) => LegacyGroupError::bad_request(message),
        GroupUseCaseError::InvalidHistoryLimit(limit) => {
            LegacyGroupError::bad_request(format!("invalid history limit: {limit}"))
        }
        GroupUseCaseError::ActorNotFound(actor_id) => {
            LegacyGroupError::not_found(format!("Actor '{}' not found", actor_id))
        }
        GroupUseCaseError::ProposalNotFound(proposal_id)
        | GroupUseCaseError::ProposalExpired(proposal_id) => {
            LegacyGroupError::not_found(format!("Proposal '{}' not found or expired", proposal_id))
        }
        GroupUseCaseError::InvalidParticipantMode { mode, actor_kind } => {
            LegacyGroupError::bad_request(format!(
                "mode '{:?}' is not valid for actor_kind '{:?}'",
                mode, actor_kind
            ))
        }
        GroupUseCaseError::Conflict(message) => {
            LegacyGroupError::new(StatusCode::CONFLICT, message)
        }
        GroupUseCaseError::Service(error) => service_error_to_legacy(error),
    }
}

pub(crate) async fn resolve_group_chat_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<GroupChatCaller, LegacyGroupError> {
    if let Some(bot_uuid) = state.bot_uuid_from_headers(headers).await {
        return Ok(GroupChatCaller::Bot { bot_uuid });
    }

    let human = human_caller_from_identity(state, headers, uri).await?;
    Ok(GroupChatCaller::Human(human))
}

async fn human_caller_from_identity(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<HttpGroupCaller, LegacyGroupError> {
    let identity = state.user_identity.extract(headers, uri).await;
    let (staff_no, nick_name) = match identity {
        Some(id) => {
            let sn = id.staff_no.filter(|s| !s.is_empty());
            let nn = id.nick_name
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty());
            (sn, nn)
        }
        None => (None, None),
    };
    let staff_no = staff_no.ok_or_else(|| {
        LegacyGroupError::unauthorized(
            "valid Human cookie is required for this group message request",
        )
    })?;
    Ok(HttpGroupCaller {
        actor_id: format!("human_{}", staff_no),
        staff_no,
        nick_name,
    })
}

fn delivery_slug(delivery_type: DeliveryType) -> &'static str {
    match delivery_type {
        DeliveryType::Send => "send",
        DeliveryType::Inject => "inject",
    }
}
