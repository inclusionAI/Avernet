use std::sync::Arc;

use axum::extract::ws::WebSocketUpgrade;
use axum::extract::{RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use bcs_auth_api::AuthPrincipal;
use bcs_service_api::WsLifecycleInstrumentationHook;
use bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress;
use bcs_service_api::application::v1::{
    GroupSessionConnectionError, GroupSessionConnectionService, VerifyGroupSessionConnectionToken,
};
use bcs_service_api::port::GROUP_SESSION_TOKEN_MAX_COMPACT_LEN;
use serde::Serialize;
use tracing::{info, warn};

use super::{
    CallerIngressSkipReason, WebDispatchState, WorkbenchConnectionAuth, handle_client_connection,
    ws_upgrade_caller_ingress,
};

pub const GROUP_SESSION_WS_ENDPOINT: &str = "/openapi/v1/collaboration/messages/ws";

#[derive(Clone)]
struct GroupSessionWebSocketState {
    connections: Arc<dyn GroupSessionConnectionService>,
    dispatch: Arc<WebDispatchState>,
    metrics: Arc<dyn WsLifecycleInstrumentationHook>,
    /// Same auth chain the legacy `/ws` upgrade runs: strict caller-identity
    /// extraction at the handshake. Absent entries keep the old display-only
    /// binding (None) — the chain itself is never optional in assembly.
    auth_chain: Arc<bcs_auth_api::AuthPluginChain>,
}

pub fn group_session_websocket_router(
    connections: Arc<dyn GroupSessionConnectionService>,
    dispatch: Arc<WebDispatchState>,
    metrics: Arc<dyn WsLifecycleInstrumentationHook>,
    auth_chain: Arc<bcs_auth_api::AuthPluginChain>,
) -> Router {
    Router::new()
        .route(GROUP_SESSION_WS_ENDPOINT, get(upgrade))
        .with_state(GroupSessionWebSocketState {
            connections,
            dispatch,
            metrics,
            auth_chain,
        })
}

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or_default()
}

/// Why a session-bound upgrade refused the caller identity. Logged verbatim
/// at the handshake so a missing caller identity is diagnosable from the
/// server log alone.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionBoundIngressSkip {
    /// The auth chain produced no principal at all (no plugin matched the
    /// handshake headers, or the chain itself errored).
    NoPrincipal,
    /// Chain-level refusal: machine credential, missing user id, etc.
    Caller(CallerIngressSkipReason),
    /// The principal names a different user than the token binds: the
    /// binding never upgrades onto someone else's identity.
    UserMismatch,
}

impl SessionBoundIngressSkip {
    /// Stable reason code for logs: grep `session-bound WS upgrade`.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::NoPrincipal => "no_principal",
            Self::Caller(reason) => reason.as_str(),
            Self::UserMismatch => "user_mismatch",
        }
    }
}

/// Strict caller-identity upgrade for the session-bound handshake: the token
/// stays display-only, but the gateway signs the same authenticated principal
/// on this upgrade as on any HTTP route. A human principal that names the
/// SAME user the token binds upgrades the binding into a full caller ingress
/// (the exact `ws_upgrade_caller_ingress` extraction the legacy `/ws` runs;
/// deployment decision 2026-10-24: login windows are advisory, no longer an
/// upgrade keystone). Machine credentials, missing identities and user
/// mismatches are refused with a logged reason — fail-closed, never
/// synthesized (spec §10.13/§10.1.1).
fn session_bound_caller_ingress(
    principal: Option<&AuthPrincipal>,
    bound_user_id: &str,
    at_ms: i64,
) -> Result<AuthenticatedHumanCallerIngress, SessionBoundIngressSkip> {
    let principal = principal.ok_or(SessionBoundIngressSkip::NoPrincipal)?;
    let staff_no = principal
        .user_id
        .as_deref()
        .filter(|user_id| !user_id.is_empty())
        .ok_or(SessionBoundIngressSkip::Caller(
            CallerIngressSkipReason::MissingUserId,
        ))?;
    if staff_no != bound_user_id {
        return Err(SessionBoundIngressSkip::UserMismatch);
    }
    ws_upgrade_caller_ingress(principal, at_ms).map_err(SessionBoundIngressSkip::Caller)
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
    // The V1 connection token itself stays display-only. The gateway, however,
    // signs the same authenticated principal on this handshake as on any HTTP
    // route, so the strict /ws extraction applies here too (deployment
    // decision 2026-10-24: login windows are advisory). Grant or refusal is
    // ALWAYS logged with a stable reason so a missing caller identity is
    // diagnosable from the server log alone.
    let at_ms = now_ms();
    let principal = match state.auth_chain.authenticate(&headers).await {
        Ok(result) => result.principal,
        Err(error) => {
            warn!(
                user_id = %binding.user_id,
                error = %error,
                "session-bound WS upgrade: caller identity not granted (auth_chain_error)"
            );
            None
        }
    };
    let caller_ingress = match session_bound_caller_ingress(principal.as_ref(), &binding.user_id, at_ms) {
        Ok(ingress) => {
            info!(
                user_id = %binding.user_id,
                login_window_ms = ?principal.as_ref().and_then(|p| p.human_expires_at_ms),
                "session-bound WS upgrade: full caller identity bound"
            );
            Some(ingress)
        }
        Err(skip) => {
            warn!(
                user_id = %binding.user_id,
                reason = skip.as_str(),
                "session-bound WS upgrade: caller identity not granted"
            );
            None
        }
    };
    let auth = WorkbenchConnectionAuth::SessionBound {
        tenant: binding.tenant,
        actor_id: format!("human_{}", binding.user_id),
        group_id: binding.group_id,
        session_id: binding.session_id,
        caller_ingress,
    };
    let dispatch = state.dispatch;
    let metrics = state.metrics;

    let log_request_id = bcs_observability::current_request_id();
    ws.on_upgrade(move |socket| bcs_observability::with_request_id(
        log_request_id, handle_client_connection(socket, dispatch, auth, metrics)))
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

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_auth_api::AuthPrincipal;

    const NOW_MS: i64 = 1_780_000_000_000;

    fn human(user_id: &str, expires_at_ms: Option<u64>) -> AuthPrincipal {
        AuthPrincipal {
            source_name: Some("test".to_string()),
            user_id: Some(user_id.to_string()),
            user_name: None,
            avatar: None,
            bot_uuid: None,
            external_bot_id: None,
            owner_id: None,
            owner_name: None,
            token: None,
            human_expires_at_ms: expires_at_ms,
        }
    }

    #[test]
    fn matching_user_upgrades_to_caller_identity_window_or_not() {
        let ingress =
            session_bound_caller_ingress(Some(&human("152819", Some((NOW_MS + 60_000) as u64))), "152819", NOW_MS)
                .expect("matching human principal must carry caller identity");
        assert_eq!(ingress.actor().actor_id, "human_152819");
        assert_eq!(ingress.actor().staff_no, "152819");

        // Deployment decision 2026-10-24: login windows are advisory —
        // window-less (internal identity artifacts) and already-dead
        // windows still upgrade.
        for expires in [None, Some(NOW_MS as u64), Some((NOW_MS - 1) as u64)] {
            assert!(
                session_bound_caller_ingress(Some(&human("152819", expires)), "152819", NOW_MS).is_ok(),
                "windowless/dead windows upgrade (expires={expires:?})"
            );
        }
    }

    #[test]
    fn refusals_carry_a_stable_logged_reason() {
        // The token binds 152819 but the gateway principal names someone else:
        // never upgrade a binding onto another user's identity.
        assert_eq!(
            session_bound_caller_ingress(Some(&human("999999", None)), "152819", NOW_MS).unwrap_err(),
            SessionBoundIngressSkip::UserMismatch
        );
        assert_eq!(
            session_bound_caller_ingress(None, "152819", NOW_MS).unwrap_err(),
            SessionBoundIngressSkip::NoPrincipal
        );
        let mut bot = human("152819", None);
        bot.bot_uuid = Some("bot-1".to_string());
        assert_eq!(
            session_bound_caller_ingress(Some(&bot), "152819", NOW_MS).unwrap_err(),
            SessionBoundIngressSkip::Caller(CallerIngressSkipReason::MachineCredential)
        );
        assert_eq!(
            session_bound_caller_ingress(Some(&human("", None)), "152819", NOW_MS).unwrap_err(),
            SessionBoundIngressSkip::Caller(CallerIngressSkipReason::MissingUserId)
        );
    }
}
