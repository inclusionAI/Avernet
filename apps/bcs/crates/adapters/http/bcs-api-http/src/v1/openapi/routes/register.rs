use axum::Router;
use axum::extract::rejection::QueryRejection;
use axum::extract::{Extension, Json, Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use bcs_service_api::application::v1::{AuthenticatedCaller, IssueRegisterToken, RegisterBot};
use bcs_service_api::application::v1::ProviderRegistrationMode;
use serde::Deserialize;

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};

pub fn router() -> Router<ApiState> {
    Router::new().route("/register/token", get(get_register_token))
}

/// Mounted OUTSIDE the `verify_principal` boundary: POST /openapi/v1/collaboration/register is
/// anonymous and the register token is its only credential.
pub fn public_router() -> Router<ApiState> {
    Router::new().route("/openapi/v1/collaboration/register", post(register_bot))
}

#[derive(Debug, Deserialize)]
pub struct RegisterTokenQuery {
    pub provider_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct RegisterQuery {
    pub token: Option<String>,
    #[serde(alias = "bot-name")]
    pub bot_name: Option<String>,
    pub mode: Option<ProviderRegistrationMode>,
    pub provider_bot_ref: Option<String>,
    pub webhook_url: Option<String>,
    pub agent_card_url: Option<String>,
}

#[derive(Default,Deserialize)]
#[serde(deny_unknown_fields)]
struct RegisterBody {
    agent_card:Option<serde_json::Value>,
    agent_card_url:Option<String>,
    #[serde(alias="bot-name")]
    bot_name:Option<String>,
}

async fn get_register_token(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<RegisterTokenQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|_| invalid_request(&request_id, "invalid registration query"))?;
    let result = state
        .register_service
        .issue_register_token(IssueRegisterToken { caller, provider_id: query.provider_id })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        [(header::CACHE_CONTROL, "no-store")],
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn register_bot(
    State(state): State<ApiState>,
    headers: HeaderMap,
    query: Result<Query<RegisterQuery>, QueryRejection>,
    body:axum::body::Bytes,
) -> Result<Response, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let Query(query) = query.map_err(|_| invalid_request(&request_id, "invalid registration query"))?;
    let token = query
        .token
        .filter(|token| !token.is_empty())
        .ok_or_else(|| invalid_request(&request_id, "missing required parameter: token"))?;
    let body:RegisterBody=if body.is_empty() {RegisterBody::default()} else {
        serde_json::from_slice(&body).map_err(|_|invalid_request(&request_id,"invalid registration JSON body"))?
    };
    if (query.agent_card_url.is_some() && body.agent_card_url.is_some()) || (query.bot_name.is_some() && body.bot_name.is_some()) {
        return Err(invalid_request(&request_id,"duplicate registration source or name"));
    }
    let source=bcs_service_api::types::a2a_bot::A2aCardSource::from_parts(query.agent_card_url.or(body.agent_card_url),body.agent_card)
        .map_err(|_|invalid_request(&request_id,"provide exactly one Card URL or Card object"))?;
    let name=query.bot_name.or(body.bot_name);
    let bot_name=match name {
        None if source.is_some()=>String::new(),
        Some(name) if !name.trim().is_empty()=>name.trim().to_owned(),
        _=>return Err(invalid_request(&request_id,"missing required parameter: bot-name")),
    };
    let command = RegisterBot {token,bot_name,mode:query.mode,
        provider_bot_ref:query.provider_bot_ref,webhook_url:query.webhook_url};
    let result = if let Some(source) = source {
        state.register_service.register_a2a_card(command,source).await
    } else { state.register_service.register_bot(command).await }
        .map_err(|error|application_error_response(&request_id,error))?;
    Ok((
        StatusCode::CREATED,
        [(header::CACHE_CONTROL, "no-store")],
        Json(Envelope::success(20_100, "Created", result, request_id.0)),
    )
        .into_response())
}
