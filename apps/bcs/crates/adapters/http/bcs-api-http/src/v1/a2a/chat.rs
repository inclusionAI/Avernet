use super::{
    service,
    transport::{bearer, body, reply},
};
use crate::v1::common::{ApiState, RequestId};
use axum::{
    extract::{Path, Query, Request, State},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use bcs_service_api::application::v1::{
    ApplicationError,
    a2a::{ChatResponseMode, QueryA2aRun, StartNamedA2aChat},
};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ChatRequest {
    message: String,
    from: Option<String>,
    #[serde(alias = "timeoutMs")]
    timeout_ms: Option<u64>,
    #[serde(alias = "sessionId")]
    session_id: Option<String>,
    #[serde(default)]
    tags: Vec<String>,
    #[serde(default, alias = "responseMode")]
    response_mode: ChatResponseMode,
    #[serde(alias = "callerWaitMode")]
    caller_wait_mode: Option<String>,
    organization_code: Option<String>,
}
#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct RunQuery {
    #[serde(default)]
    wait_ms: u64,
    #[serde(default)]
    since_version: u64,
}
fn actor(headers: &HeaderMap) -> Result<Option<String>, ApplicationError> {
    bcs_auth_api::expected_actor_id(headers)
        .map(|v| v.map(str::to_owned))
        .map_err(|_| ApplicationError::Unauthenticated)
}

pub async fn start(
    State(state): State<ApiState>,
    Path((provider_name, provider_bot_ref)): Path<(String, String)>,
    request: Request,
) -> Response {
    let id = RequestId::from_headers(request.headers());
    let result = async {
        let (parts, request_body) = request.into_parts();
        let token = bearer(&parts.headers)?;
        let actor = actor(&parts.headers)?;
        let bytes = axum::body::to_bytes(request_body, 2 * 1024 * 1024)
            .await
            .map_err(|_| {
                ApplicationError::payload_too_large(
                    "request_too_large",
                    "Request body exceeds limit",
                )
            })?;
        if parts.uri.query().is_some() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "Query is not allowed",
            ));
        }
        let input: ChatRequest = body(&parts.headers, &bytes)?;
        let provider_bypass_headers = state
            .a2a_provider_bypass_header_names
            .iter()
            .filter_map(|name| {
                parts
                    .headers
                    .get(name)
                    .and_then(|v| v.to_str().ok())
                    .map(|v| (name.as_str().to_owned(), v.to_owned()))
            })
            .collect();
        service(&state)?
            .chat(StartNamedA2aChat {
                token,
                actor,
                provider_name,
                provider_bot_ref,
                message: input.message,
                from: input.from,
                session_id: input.session_id,
                timeout_ms: input.timeout_ms,
                client: parts
                    .headers
                    .get("x-bcs-client")
                    .and_then(|v| v.to_str().ok())
                    .map(str::to_owned),
                tags: input.tags,
                response_mode: input.response_mode,
                caller_wait_mode: input.caller_wait_mode,
                organization_code: input.organization_code,
                provider_bypass_headers,
            })
            .await
    }
    .await;
    reply(&id, StatusCode::ACCEPTED, result)
}

pub async fn get_run(
    State(state): State<ApiState>,
    Path(run_id): Path<String>,
    query: Result<Query<RunQuery>, axum::extract::rejection::QueryRejection>,
    request: Request,
) -> Response {
    let id = RequestId::from_headers(request.headers());
    let result = async {
        let token = bearer(request.headers())?;
        let actor = actor(request.headers())?;
        let Query(query) =
            query.map_err(|_| ApplicationError::invalid("invalid_request", "Invalid run query"))?;
        let bytes = axum::body::to_bytes(request.into_body(), 0)
            .await
            .map_err(|_| ApplicationError::invalid("invalid_request", "GET body is not allowed"))?;
        if !bytes.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "GET body is not allowed",
            ));
        }
        service(&state)?
            .get_run(QueryA2aRun {
                token,
                actor,
                run_id,
                wait_ms: query.wait_ms,
                since_version: query.since_version,
            })
            .await
    }
    .await;
    reply(&id, StatusCode::OK, result)
}

pub async fn cancel_run(
    State(state): State<ApiState>,
    Path(run_id): Path<String>,
    request: Request,
) -> Response {
    let id = RequestId::from_headers(request.headers());
    let result = async {
        let token = bearer(request.headers())?;
        let actor = actor(request.headers())?;
        if request.uri().query().is_some() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "Query is not allowed",
            ));
        }
        let bytes = axum::body::to_bytes(request.into_body(), 0)
            .await
            .map_err(|_| {
                ApplicationError::invalid("invalid_request", "Cancellation body is not allowed")
            })?;
        if !bytes.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "Cancellation body is not allowed",
            ));
        }
        service(&state)?
            .cancel_run(&token, actor.as_deref(), &run_id)
            .await
    }
    .await;
    reply(&id, StatusCode::OK, result)
}
