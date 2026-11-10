//! Public v0.1 management transport. Provider scope comes only from its token.
use crate::state::HttpAppState;
use axum::{
    Json,
    extract::{Path, Query, Request, State},
    http::{HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
};
use bcs_service_api::{
    ServiceError,
    application::a2a_bot::{ManagedA2aCard, RegisterAgentCommand},
    types::a2a_bot::{
        A2aCardSource,
        a2a_management::{ManagementCode as Code, ManagementError},
    },
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Target {
    bot_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Registration {
    provider_bot_ref: String,
    #[serde(default)]
    visibility: bcs_service_api::types::a2a_bot::A2aRegistrationVisibility,
    owners: Option<Vec<String>>,
    summary: Option<String>,
    agent_card_url: Option<String>,
    agent_card: Option<Value>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Update {
    visibility: Option<bcs_service_api::types::a2a_bot::A2aRegistrationVisibility>,
    agent_card_url: Option<String>,
    agent_card: Option<Value>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceQuery { provider_bot_ref: String }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Refresh {}

#[derive(Serialize)]
#[serde(untagged)]
enum ManagementView {
    Card(ManagedA2aCard),
    Collection { items: Vec<ManagedA2aCard> },
}

fn error(code: Code, field: &str, reason: &'static str) -> ServiceError {
    ManagementError::new(code, field, reason).into()
}
fn message(code: Code) -> &'static str {
    match code {
        Code::InvalidRequest => "请求格式或字段不合法",
        Code::Unauthenticated => "请提供有效的 Provider 管理凭据",
        Code::PermissionDenied => "当前凭据无权执行此操作，或 Provider 已禁用",
        Code::BotNotFound => "当前 Provider 下未找到该 Agent",
        Code::MethodNotAllowed => "此接口不支持该 HTTP 方法",
        Code::NotAcceptable => "此接口仅返回 JSON",
        Code::ProviderBotRefConflict => "同一 Provider 下已存在该平台 Agent 标识",
        Code::BotNameConflict => "同一 Provider 下已存在该 Agent 名称",
        Code::SourceAgentConflict => "同一 Provider 下已存在该来源 Agent ID",
        Code::SourceAgentChangeNotAllowed => "已登记的来源 Agent ID 不可修改或删除",
        Code::TargetChanged => "处理期间目标已发生变化，请重新查询",
        Code::CardUrlRequired => "此 Card 没有来源 URL，请直接提交更新",
        Code::RefreshSuperseded => "Card 已被更新，本次刷新结果未保存",
        Code::RequestTooLarge => "请求体超过大小限制",
        Code::UnsupportedMediaType => "请求体必须使用 application/json",
        Code::InvalidOwner => "所有者标识不合法或不可指定",
        Code::InvalidCard => "AgentCard 的字段或声明不合法",
        Code::CardSourceInvalid => "请提供完整 Card 或有效 Card URL，二者只能选一",
        Code::CardTooLarge => "AgentCard 超过大小限制",
        Code::CardAddressRejected => "Card 或接口地址不符合出站访问策略",
        Code::CardInterfaceUnsupported => "没有可用的 A2A 接口绑定或版本",
        Code::CardContentUnsupported => "Agent 必须支持文本输入和输出",
        Code::CardAuthUnsupported => "当前接入要求允许不携带应用层凭据调用",
        Code::CardExtensionUnsupported => "Agent 声明了不支持的必需扩展",
        Code::RateLimited => "请求过于频繁，请稍后重试",
        Code::CardFetchFailed => "无法获取 AgentCard",
        Code::CardFetchTimeout => "获取或验证 AgentCard 超时",
        Code::ServiceUnavailable => "Agent 管理服务暂不可用",
        Code::InternalError => "Agent 管理操作失败，请提供请求标识联系平台",
    }
}
pub(super) fn status(code: Code) -> StatusCode {
    match code {
        Code::InvalidRequest => StatusCode::BAD_REQUEST,
        Code::Unauthenticated => StatusCode::UNAUTHORIZED,
        Code::PermissionDenied => StatusCode::FORBIDDEN,
        Code::BotNotFound => StatusCode::NOT_FOUND,
        Code::MethodNotAllowed => StatusCode::METHOD_NOT_ALLOWED,
        Code::NotAcceptable => StatusCode::NOT_ACCEPTABLE,
        Code::ProviderBotRefConflict
        | Code::BotNameConflict
        | Code::SourceAgentConflict
        | Code::SourceAgentChangeNotAllowed
        | Code::TargetChanged
        | Code::CardUrlRequired
        | Code::RefreshSuperseded => StatusCode::CONFLICT,
        Code::RequestTooLarge => StatusCode::PAYLOAD_TOO_LARGE,
        Code::UnsupportedMediaType => StatusCode::UNSUPPORTED_MEDIA_TYPE,
        Code::RateLimited => StatusCode::TOO_MANY_REQUESTS,
        Code::CardFetchFailed => StatusCode::BAD_GATEWAY,
        Code::CardFetchTimeout => StatusCode::GATEWAY_TIMEOUT,
        Code::ServiceUnavailable => StatusCode::SERVICE_UNAVAILABLE,
        Code::InternalError => StatusCode::INTERNAL_SERVER_ERROR,
        _ => StatusCode::UNPROCESSABLE_ENTITY,
    }
}
fn failure(failure: ServiceError, id: &str) -> Response {
    let failure = match failure {
        ServiceError::A2aManagement(error) => error,
        ServiceError::Unauthorized(_) => {
            ManagementError::new(Code::Unauthenticated, "", "AUTHENTICATION_REQUIRED")
        }
        ServiceError::Forbidden(_) => {
            ManagementError::new(Code::PermissionDenied, "", "ACCESS_DENIED")
        }
        ServiceError::BotNotFound(_) | ServiceError::BotNotRegistered(_) => {
            ManagementError::new(Code::BotNotFound, "bot_id", "NOT_FOUND")
        }
        ServiceError::Conflict(_) => {
            ManagementError::new(Code::ProviderBotRefConflict, "provider_bot_ref", "ALREADY_EXISTS")
        }
        ServiceError::InvalidOperation { .. } => {
            ManagementError::new(Code::InvalidRequest, "", "INVALID_REQUEST")
        }
        _ => ManagementError::new(Code::InternalError, "", "INTERNAL_ERROR"),
    };
    let details = if failure.field.is_empty() {
        vec![]
    } else {
        vec![json!({"field":failure.field,"reason":failure.reason})]
    };
    let mut response = (
        status(failure.code),
        Json(json!({"error":{"code":failure.code,
        "message":message(failure.code),"request_id":id,"details":details}})),
    )
        .into_response();
    if failure.code == Code::Unauthenticated {
        response
            .headers_mut()
            .insert("www-authenticate", HeaderValue::from_static("Bearer"));
    }
    if failure.code == Code::RateLimited {
        response
            .headers_mut()
            .insert("retry-after", HeaderValue::from_static("1"));
    }
    response
}
fn source(url: Option<String>, card: Option<Value>) -> Result<A2aCardSource, ServiceError> {
    match (url, card) {
        (Some(url), None) => {
            bcs_service_api::types::a2a_bot::a2a_management::validate_card_url(&url)?;
            Ok(A2aCardSource::Url(url))
        }
        (None, Some(card)) => Ok(A2aCardSource::Inline(card)),
        _ => Err(error(
            Code::CardSourceInvalid,
            "agent_card",
            "EXACTLY_ONE_SOURCE_REQUIRED",
        )),
    }
}
fn accepts_json(headers: &HeaderMap) -> bool {
    if !headers.contains_key("accept") {
        return true;
    }
    let mut best: Option<(u8, f32)> = None;
    for header in headers.get_all("accept") {
        let Ok(header) = header.to_str() else {
            return false;
        };
        for part in header.split(',') {
            let mut fields = part.split(';');
            let specificity = match fields
                .next()
                .unwrap_or("")
                .trim()
                .to_ascii_lowercase()
                .as_str()
            {
                "application/json" => 2,
                "application/*" => 1,
                "*/*" => 0,
                _ => continue,
            };
            let mut q = 1.0;
            for field in fields {
                if let Some((key, value)) = field.trim().split_once('=') {
                    if key.eq_ignore_ascii_case("q") {
                        q = value
                            .parse::<f32>()
                            .ok()
                            .filter(|v| (0.0..=1.0).contains(v))
                            .unwrap_or(0.0);
                    }
                }
            }
            if best.is_none_or(|(s, _)| specificity > s) {
                best = Some((specificity, q));
            }
        }
    }
    best.is_some_and(|(_, q)| q > 0.0)
}
fn body<T: serde::de::DeserializeOwned>(bytes: &[u8]) -> Result<T, ServiceError> {
    let value = bcs_service_api::types::a2a_bot::a2a_management::parse_card_json(bytes)
        .map_err(|_| error(Code::InvalidRequest, "", "INVALID_JSON_OR_DUPLICATE_FIELD"))?;
    if ["agent_card_url", "agent_card", "summary"]
        .iter()
        .any(|key| value.get(key).is_some_and(Value::is_null))
    {
        return Err(error(Code::InvalidRequest, "", "NULL_FIELD_NOT_ALLOWED"));
    }
    serde_json::from_value(value).map_err(|_| error(Code::InvalidRequest, "", "INVALID_FIELDS"))
}

async fn execute(
    state: &HttpAppState,
    request: Request,
    bot_id: Option<String>,
) -> Result<(StatusCode, ManagementView), ServiceError> {
    let (parts, request_body) = request.into_parts();
    let token = crate::headers::extract_bearer_token(&parts.headers)
        .ok_or_else(|| error(Code::Unauthenticated, "", "AUTHENTICATION_REQUIRED"))?;
    let service = state.services.a2a_bot_management.as_ref()
        .ok_or_else(|| error(Code::ServiceUnavailable, "", "MANAGEMENT_UNAVAILABLE"))?;
    let bytes = axum::body::to_bytes(request_body, 2 * 1024 * 1024).await
        .map_err(|_| error(Code::RequestTooLarge, "", "BODY_LIMIT_EXCEEDED"))?;
    if parts.method == axum::http::Method::GET {
        if !bytes.is_empty() {return Err(error(Code::InvalidRequest,"","GET_BODY_NOT_ALLOWED"));}
        if let Some(bot_id) = bot_id.as_deref() {
            if parts.uri.query().is_some() {return Err(error(Code::InvalidRequest,"","QUERY_NOT_ALLOWED"));}
            return Ok((StatusCode::OK,ManagementView::Card(service.get_agent_by_id(&token,bot_id).await?)));
        }
        let Query(query) = Query::<SourceQuery>::try_from_uri(&parts.uri)
            .map_err(|_|error(Code::InvalidRequest,"provider_bot_ref","INVALID_QUERY"))?;
        let items = service.find_agents_by_provider_ref(&token,&query.provider_bot_ref).await?;
        return Ok((StatusCode::OK,ManagementView::Collection {items}));
    }
    if parts.uri.query().is_some() {return Err(error(Code::InvalidRequest,"","QUERY_NOT_ALLOWED"));}
    if parts.method == axum::http::Method::POST && bytes.is_empty() {
        if let Some(bot_id) = bot_id.as_deref() {
            return Ok((StatusCode::OK,ManagementView::Card(service.refresh_agent_by_id(&token,bot_id).await?)));
        }
    }
    if !parts.headers.get("content-type").and_then(|v|v.to_str().ok())
        .and_then(|v|v.split(';').next()).is_some_and(|v|v.trim().eq_ignore_ascii_case("application/json")) {
        return Err(error(Code::UnsupportedMediaType,"","JSON_REQUIRED"));
    }
    if parts.uri.path() == "/a2a/agents" {
        let input: Registration = body(&bytes)?;
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|v| v.as_millis().min(i64::MAX as u128) as i64)
            .unwrap_or(0);
        let card = service
            .register_agent(RegisterAgentCommand {
                provider_bot_ref: input.provider_bot_ref,
                visibility: input.visibility,
                provider_admin_token: token,
                owners: input.owners,
                summary: input.summary,
                source: source(input.agent_card_url, input.agent_card)?,
                now_ms,
            })
            .await?;
        Ok((StatusCode::CREATED, ManagementView::Card(card)))
    } else if parts.method == axum::http::Method::PUT {
        let input: Update = body(&bytes)?;
        let bot_id = bot_id.as_deref().ok_or_else(||error(Code::InvalidRequest,"bot_id","MISSING_BOT_ID"))?;
        if input.visibility.is_none() && input.agent_card_url.is_none() && input.agent_card.is_none() {
            return Err(error(Code::InvalidRequest,"","UPDATE_REQUIRED"));
        }
        let source = if input.agent_card_url.is_none() && input.agent_card.is_none() {None}
            else {Some(source(input.agent_card_url,input.agent_card)?)};
        Ok((StatusCode::OK,ManagementView::Card(service.update_agent_by_id_with_visibility(&token,bot_id,source,input.visibility).await?)))
    } else {
        let _: Refresh = body(&bytes)?;
        let bot_id = bot_id.as_deref().ok_or_else(||error(Code::InvalidRequest,"bot_id","MISSING_BOT_ID"))?;
        Ok((StatusCode::OK,ManagementView::Card(service.refresh_agent_by_id(&token,bot_id).await?)))
    }
}

pub async fn handle(State(state): State<HttpAppState>, target: Result<Path<Target>, axum::extract::rejection::PathRejection>, request: Request) -> Response {
    let id = uuid::Uuid::new_v4().to_string();
    let collection = request.uri().path() == "/a2a/agents";
    let target = if collection { Ok(None) } else { target.map(|Path(target)|Some(target.bot_id)) };
    let allowed = if collection {
        "GET, POST"
    } else if request.uri().path().ends_with("/agent-card:refresh") {
        "POST"
    } else {
        "GET, PUT"
    };
    let mut response = if target.is_err() {
        failure(error(Code::InvalidRequest, "bot_id", "INVALID_PATH"), &id)
    } else if !allowed.split(", ").any(|v| v == request.method().as_str()) {
        let mut response = failure(error(Code::MethodNotAllowed, "", "METHOD_NOT_ALLOWED"), &id);
        response
            .headers_mut()
            .insert("allow", HeaderValue::from_static(allowed));
        response
    } else if !accepts_json(request.headers()) {
        failure(error(Code::NotAcceptable, "", "JSON_NOT_ACCEPTED"), &id)
    } else {
        match execute(&state, request, target.unwrap()).await {
            Ok((status, card)) => {
                let location = if let (StatusCode::CREATED, ManagementView::Card(card)) = (status, &card) {
                    Some(format!(
                        "/a2a/agents/{}/agent-card",
                        url::form_urlencoded::byte_serialize(card.bot_id.as_bytes())
                            .collect::<String>()
                    ))
                } else {
                    None
                };
                let mut response = (status, Json(card)).into_response();
                if let Some(location) = location {
                    if let Ok(value) = location.parse() {
                        response.headers_mut().insert("location", value);
                    }
                }
                response
            }
            Err(error) => failure(error, &id),
        }
    };
    response
        .headers_mut()
        .insert("cache-control", HeaderValue::from_static("no-store"));
    if let Ok(value) = HeaderValue::from_str(&id) {
        response.headers_mut().insert("x-request-id", value);
    }
    response
}
