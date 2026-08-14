use std::sync::Arc;

use axum::extract::ws::WebSocketUpgrade;
use axum::extract::{RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use bcs_service_api::WsLifecycleInstrumentationHook;
use bcs_service_api::application::v1::{
    GroupSessionConnectionError, GroupSessionConnectionService, VerifyGroupSessionConnectionToken,
};
use bcs_service_api::port::GROUP_SESSION_TOKEN_MAX_COMPACT_LEN;
use serde::Serialize;

use super::{WebDispatchState, WorkbenchConnectionAuth, handle_client_connection};

pub const GROUP_SESSION_WS_ENDPOINT: &str = "/openapi/v1/collaboration/messages/ws";

#[derive(Clone)]
struct GroupSessionWebSocketState {
    connections: Arc<dyn GroupSessionConnectionService>,
    dispatch: Arc<WebDispatchState>,
    metrics: Arc<dyn WsLifecycleInstrumentationHook>,
}

pub fn group_session_websocket_router(
    connections: Arc<dyn GroupSessionConnectionService>,
    dispatch: Arc<WebDispatchState>,
    metrics: Arc<dyn WsLifecycleInstrumentationHook>,
) -> Router {
    Router::new()
        .route(GROUP_SESSION_WS_ENDPOINT, get(upgrade))
        .with_state(GroupSessionWebSocketState {
            connections,
            dispatch,
            metrics,
        })
}

async fn upgrade(
    State(state): State<GroupSessionWebSocketState>,
    headers: HeaderMap,
    RawQuery(raw_query): RawQuery,
    ws: WebSocketUpgrade,
) -> Response {
    let request_id = request_id(&headers);
    let token = match connection_token(raw_query.as_deref()) {
        Some(token) => token,
        None => return error_response(ConnectionError::Invalid, request_id),
    };
    let binding = match state
        .connections
        .verify_token(VerifyGroupSessionConnectionToken { token })
        .await
    {
        Ok(binding) => binding,
        Err(GroupSessionConnectionError::InvalidConnectionToken)
        | Err(GroupSessionConnectionError::Application(_)) => {
            return error_response(ConnectionError::Invalid, request_id);
        }
        Err(GroupSessionConnectionError::TokenServiceUnavailable)
        | Err(GroupSessionConnectionError::Internal(_)) => {
            return error_response(ConnectionError::Unavailable, request_id);
        }
    };
    let auth = WorkbenchConnectionAuth::SessionBound {
        tenant: binding.tenant,
        actor_id: format!("human_{}", binding.user_id),
        group_id: binding.group_id,
        session_id: binding.session_id,
    };
    let dispatch = state.dispatch;
    let metrics = state.metrics;

    ws.on_upgrade(move |socket| handle_client_connection(socket, dispatch, auth, metrics))
}

fn connection_token(raw_query: Option<&str>) -> Option<String> {
    let query = raw_query?;
    let mut tokens = form_urlencoded::parse(query.as_bytes())
        .filter(|(name, _)| name == "token")
        .map(|(_, value)| value.into_owned());
    let token = tokens.next()?;
    if tokens.next().is_some()
        || token.trim().is_empty()
        || token.len() > GROUP_SESSION_TOKEN_MAX_COMPACT_LEN
    {
        return None;
    }
    Some(token)
}

fn request_id(headers: &HeaderMap) -> String {
    headers
        .get("x-request-id")
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string())
}

#[derive(Clone, Copy)]
enum ConnectionError {
    Invalid,
    Unavailable,
}

#[derive(Serialize)]
struct ErrorEnvelope {
    code: u32,
    message: &'static str,
    data: ErrorData,
    request_id: String,
}

#[derive(Serialize)]
struct ErrorData {
    error_code: &'static str,
}

fn error_response(error: ConnectionError, request_id: String) -> Response {
    let (status, code, error_code, message) = match error {
        ConnectionError::Invalid => (
            StatusCode::UNAUTHORIZED,
            40_100,
            "invalid_connection_token",
            "Connection credential is invalid or expired",
        ),
        ConnectionError::Unavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            50_300,
            "token_service_unavailable",
            "Connection credential verification is unavailable",
        ),
    };
    (
        status,
        Json(ErrorEnvelope {
            code,
            message,
            data: ErrorData { error_code },
            request_id,
        }),
    )
        .into_response()
}
