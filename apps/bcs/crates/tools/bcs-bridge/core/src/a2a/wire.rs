//! Binding-specific envelopes only. Both transports invoke the same scoped service.
use std::{convert::Infallible, sync::Arc, time::Duration};
use a2a::{A2AError, SendMessageRequest};
use axum::{body::Bytes, extract::{Path, Query, State, rejection::QueryRejection},
    http::{HeaderMap, StatusCode}, response::{IntoResponse, Response, Sse, sse::{Event, KeepAlive}}, Json};
use futures::{StreamExt, stream::BoxStream};
use serde::{Deserialize, de::DeserializeOwned};
use serde_json::{Value, json};
use super::{Services, service::{Service, validate_common}, task::TaskEntry};

fn authorized(services: &Services, provider: &str, bot: &str, headers: &HeaderMap) -> Result<Arc<Service>, Response> {
    let unauthorized = || (StatusCode::UNAUTHORIZED, [("www-authenticate", "Bearer")],
        Json(json!({"code":16,"message":"Unauthorized"}))).into_response();
    let service = services.get(provider).ok_or_else(unauthorized)?;
    let token = service.state.config.bcs_to_provider_token.as_deref().ok_or_else(unauthorized)?;
    let value = headers.get("authorization").and_then(|value| value.to_str().ok()).unwrap_or("");
    let valid = value.split_once(' ').is_some_and(|(scheme, credential)| scheme.eq_ignore_ascii_case("bearer")
        && credential.as_bytes() == token.as_bytes());
    if !valid { return Err(unauthorized()); }
    if service.state.config.bot(bot).is_none() { return Err(StatusCode::NOT_FOUND.into_response()); }
    Ok(service.clone())
}

fn version(headers: &HeaderMap) -> Result<(), A2AError> {
    if headers.get("a2a-version").is_some_and(|value| value.as_bytes() != b"1.0") {
        return Err(A2AError::version_not_supported("requested version"));
    }
    Ok(())
}
fn decode<T: DeserializeOwned>(value: Value) -> Result<T, A2AError> {
    serde_json::from_value(value).map_err(|_| A2AError::invalid_params("Invalid parameters"))
}
fn parse(headers: &HeaderMap, body: &[u8]) -> Result<Value, A2AError> {
    let json_type = headers.get("content-type").and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.split(';').next().is_some_and(|mime| mime.trim().eq_ignore_ascii_case("application/json")));
    if !json_type { return Err(A2AError::content_type_not_supported()); }
    serde_json::from_slice(body).map_err(|_| A2AError::parse_error("Invalid JSON"))
}

fn rest_error(error: A2AError) -> Response {
    let code = match error.code {
        a2a::error_code::TASK_NOT_FOUND => 5,
        a2a::error_code::TASK_NOT_CANCELABLE | a2a::error_code::VERSION_NOT_SUPPORTED => 9,
        a2a::error_code::UNSUPPORTED_OPERATION | a2a::error_code::METHOD_NOT_FOUND
            | a2a::error_code::PUSH_NOTIFICATION_NOT_SUPPORTED => 12,
        a2a::error_code::INTERNAL_ERROR => 13,
        _ => 3,
    };
    let detail = error.to_jsonrpc_error();
    (StatusCode::from_u16(error.http_status_code()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        Json(json!({"code":code,"message":error.message,"details":detail.data}))).into_response()
}
fn rest(result: Result<Value, A2AError>) -> Response {
    match result { Ok(value) => Json(value).into_response(), Err(error) => rest_error(error) }
}
fn rpc_error(id: Value, error: A2AError) -> Response {
    Json(json!({"jsonrpc":"2.0", "id":id, "error":error.to_jsonrpc_error()})).into_response()
}
fn encode<T: serde::Serialize>(value: T) -> Result<Value, A2AError> {
    serde_json::to_value(value).map_err(|_| A2AError::internal("Response encoding failed"))
}

fn stream(task: &TaskEntry, history: Option<i32>, rpc: Option<Value>) -> Response {
    let events: BoxStream<'static, Result<Event, Infallible>> = Box::pin(task.stream(history).map(move |event| {
        let value = match event {
            Ok(event) => match &rpc {
                Some(id) => json!({"jsonrpc":"2.0", "id":id, "result":event.as_ref()}),
                None => json!(event.as_ref()),
            },
            Err(error) => match &rpc {
                Some(id) => json!({"jsonrpc":"2.0", "id":id, "error":error.to_jsonrpc_error()}),
                None => json!({"error":{"code":13,"message":error.message,"details":error.to_jsonrpc_error().data}}),
            },
        };
        Ok(Event::default().data(value.to_string()))
    }));
    Sse::new(events).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))).into_response()
}

pub(super) async fn send(State(services): State<Services>, Path((provider, bot)): Path<(String, String)>, headers: HeaderMap, body: Bytes) -> Response {
    send_http(services, provider, bot, headers, body, false).await
}
pub(super) async fn send_stream(State(services): State<Services>, Path((provider, bot)): Path<(String, String)>, headers: HeaderMap, body: Bytes) -> Response {
    send_http(services, provider, bot, headers, body, true).await
}
async fn send_http(services: Services, provider: String, bot: String, headers: HeaderMap, body: Bytes, streaming: bool) -> Response {
    let service = match authorized(&services, &provider, &bot, &headers) { Ok(service) => service, Err(response) => return response };
    let request: SendMessageRequest = match version(&headers).and_then(|_| parse(&headers, &body)).and_then(decode) {
        Ok(request) => request, Err(error) => return rest_error(error),
    };
    match send_operation(service, &bot, request, streaming, None).await { Ok(response) => response, Err(error) => rest_error(error) }
}

async fn send_operation(service: Arc<Service>, bot: &str, request: SendMessageRequest, streaming: bool, rpc: Option<Value>) -> Result<Response, A2AError> {
    let history = request.configuration.as_ref().and_then(|config| config.history_length);
    let immediate = request.configuration.as_ref().is_some_and(|config| config.return_immediately == Some(true));
    let task = service.send(bot, request).await?;
    if streaming { return Ok(stream(&task, history, rpc)); }
    let result = if immediate { task.snapshot(history) } else { task.wait(history).await };
    let value = encode(a2a::SendMessageResponse::Task(result))?;
    Ok(Json(match rpc { Some(id) => json!({"jsonrpc":"2.0","id":id,"result":value}), None => value }).into_response())
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct TaskQuery { history_length: Option<i32>, tenant: Option<String> }

pub(super) async fn get_task(State(services): State<Services>, Path((provider, bot, id)): Path<(String, String, String)>,
    headers: HeaderMap, query: Result<Query<TaskQuery>, QueryRejection>) -> Response {
    let service = match authorized(&services, &provider, &bot, &headers) { Ok(service) => service, Err(response) => return response };
    rest(async {
        version(&headers)?;
        let Query(query) = query.map_err(|_| A2AError::invalid_params("Invalid query"))?;
        validate_common(query.tenant.as_deref(), query.history_length)?;
        encode(service.get(&bot, &id).await?.snapshot(query.history_length))
    }.await)
}

pub(super) async fn list(State(services): State<Services>, Path((provider, bot)): Path<(String, String)>,
    headers: HeaderMap, query: Result<Query<a2a::ListTasksRequest>, QueryRejection>) -> Response {
    let service = match authorized(&services, &provider, &bot, &headers) { Ok(service) => service, Err(response) => return response };
    rest(async {
        version(&headers)?;
        let Query(query) = query.map_err(|_| A2AError::invalid_params("Invalid query"))?;
        encode(service.list(&bot, query).await?)
    }.await)
}

pub(super) async fn task_action(State(services): State<Services>, Path((provider, bot, action)): Path<(String, String, String)>,
    headers: HeaderMap, body: Bytes) -> Response {
    let service = match authorized(&services, &provider, &bot, &headers) { Ok(service) => service, Err(response) => return response };
    let result = async {
        version(&headers)?;
        let (id, action) = action.rsplit_once(':').ok_or_else(|| A2AError::unsupported_operation("Unknown Task operation"))?;
        let mut value = if body.is_empty() { json!({}) } else { parse(&headers, &body)? };
        if !value.is_object() || value.get("id").is_some_and(|value| value.as_str() != Some(id)) {
            return Err(A2AError::invalid_params("Path and body Task IDs must match"));
        }
        value["id"] = json!(id);
        match action {
            "cancel" => {
                let request: a2a::CancelTaskRequest = decode(value)?;
                validate_common(request.tenant.as_deref(), None)?;
                Ok(Json(service.cancel(&bot, id).await?).into_response())
            }
            "subscribe" => {
                let request: a2a::SubscribeToTaskRequest = decode(value)?;
                validate_common(request.tenant.as_deref(), None)?;
                Ok(stream(service.get(&bot, id).await?.as_ref(), None, None))
            }
            _ => Err(A2AError::unsupported_operation("Unknown Task operation")),
        }
    }.await;
    match result { Ok(response) => response, Err(error) => rest_error(error) }
}

pub(super) async fn rpc(State(services): State<Services>, Path((provider, bot)): Path<(String, String)>, headers: HeaderMap, body: Bytes) -> Response {
    let service = match authorized(&services, &provider, &bot, &headers) { Ok(service) => service, Err(response) => return response };
    let envelope = match parse(&headers, &body) { Ok(value) => value, Err(error) => return rpc_error(Value::Null, error) };
    let id = envelope.get("id").cloned().unwrap_or(Value::Null);
    if envelope.get("jsonrpc").and_then(Value::as_str) != Some("2.0")
        || !envelope.get("id").is_some_and(|id| id.is_string() || id.is_number() || id.is_null())
        || !envelope.get("method").is_some_and(Value::is_string) {
        return rpc_error(Value::Null, A2AError::invalid_request("Invalid JSON-RPC request"));
    }
    if let Err(error) = version(&headers) { return rpc_error(id, error); }
    let method = envelope["method"].as_str().unwrap_or_default();
    let params = envelope.get("params").cloned().unwrap_or_else(|| json!({}));
    let result = async {
        if !params.is_object() { return Err(A2AError::invalid_params("Named parameters are required")); }
        match method {
            "SendMessage" | "SendStreamingMessage" => send_operation(service, &bot, decode(params)?, method == "SendStreamingMessage", Some(id.clone())).await,
            "GetTask" => {
                let request: a2a::GetTaskRequest = decode(params)?;
                validate_common(request.tenant.as_deref(), request.history_length)?;
                let task = service.get(&bot, &request.id).await?.snapshot(request.history_length);
                Ok(Json(json!({"jsonrpc":"2.0","id":id,"result":task})).into_response())
            }
            "CancelTask" => {
                let request: a2a::CancelTaskRequest = decode(params)?;
                validate_common(request.tenant.as_deref(), None)?;
                let task = service.cancel(&bot, &request.id).await?;
                Ok(Json(json!({"jsonrpc":"2.0","id":id,"result":task})).into_response())
            }
            "SubscribeToTask" => {
                let request: a2a::SubscribeToTaskRequest = decode(params)?;
                validate_common(request.tenant.as_deref(), None)?;
                Ok(stream(service.get(&bot, &request.id).await?.as_ref(), None, Some(id.clone())))
            }
            "ListTasks" => {
                let tasks = service.list(&bot, decode(params)?).await?;
                Ok(Json(json!({"jsonrpc":"2.0","id":id,"result":tasks})).into_response())
            }
            _ => Err(A2AError::method_not_found("unsupported method")),
        }
    }.await;
    match result { Ok(response) => response, Err(error) => rpc_error(id, error) }
}
