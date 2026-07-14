use std::{
    net::IpAddr,
    time::{SystemTime, UNIX_EPOCH},
};

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use bcs_domain::ProviderRecord;
use bcs_protocol::BCN_PROVIDER_ID_HEADER;
use bcs_route_security::OutboundUrlError;
use bcs_service_api::{
    AsyncA2aChatCommand, BotActor, CallerContext, ChatResponseMode, ChatRunQueryCommand,
    ServiceError,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tracing::{info, warn};
use uuid::Uuid;

use crate::{
    headers::extract_bearer_token,
    state::{AdminInvocationCallback, AdminInvocationRun, HttpAppState},
};

const DEFAULT_TIMEOUT_MS: u64 = 30 * 60 * 1000;
const MAX_TIMEOUT_MS: u64 = 30 * 60 * 1000;
const RETENTION_MS: u64 = 60 * 60 * 1000;

#[derive(Debug, Deserialize)]
pub struct CreateAdminRunRequest {
    pub target_bot_uuid: String,
    pub message: AdminRunMessage,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub detach: bool,
    #[serde(default)]
    pub run_timeout_ms: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub struct AdminRunMessage {
    pub role: String,
    pub content: Vec<AdminRunContent>,
}

#[derive(Debug, Deserialize)]
pub struct AdminRunContent {
    #[serde(rename = "type")]
    pub kind: String,
    pub text: String,
}

#[derive(Debug, Deserialize, Default)]
pub struct GetAdminRunQuery {
    #[serde(default)]
    pub wait_ms: u64,
}

#[derive(Debug, Serialize)]
struct Envelope<T: Serialize> {
    code: u32,
    message: String,
    data: T,
    request_id: String,
}

#[derive(Debug, Serialize)]
struct AdminRunView {
    run_id: String,
    provider_id: String,
    organization_code: String,
    target_bot_uuid: String,
    session_id: String,
    status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<AdminRunError>,
}

#[derive(Debug, Serialize)]
struct AdminRunError {
    code: &'static str,
    message: String,
}

pub async fn create_admin_run(
    State(state): State<HttpAppState>,
    Path(organization_code): Path<String>,
    headers: HeaderMap,
    Json(request): Json<CreateAdminRunRequest>,
) -> Response {
    let request_id = request_id();
    let provider = match authenticate_manager(&state, &headers, &organization_code).await {
        Ok(provider) => provider,
        Err(error) => return error.into_response_with_id(request_id),
    };
    let provider_id = provider.provider_id.clone();
    let text = match validate_message(request.message) {
        Ok(text) => text,
        Err(message) => return response_error(StatusCode::BAD_REQUEST, 40001, message, request_id),
    };
    let timeout_ms = request.run_timeout_ms.unwrap_or(DEFAULT_TIMEOUT_MS);
    if timeout_ms == 0 || timeout_ms > MAX_TIMEOUT_MS {
        return response_error(
            StatusCode::BAD_REQUEST,
            40001,
            format!("run_timeout_ms must be between 1 and {MAX_TIMEOUT_MS}"),
            request_id,
        );
    }
    if let Err(error) = state
        .services
        .organization
        .require_effective_member(&organization_code, &request.target_bot_uuid)
        .await
    {
        return service_error(error, request_id);
    }

    let run_id = new_admin_run_id();
    let session_id = request
        .session_id
        .unwrap_or_else(|| default_admin_session_id(&run_id));
    if !valid_session_id(&session_id) {
        return response_error(
            StatusCode::BAD_REQUEST,
            40001,
            "invalid session_id".to_string(),
            request_id,
        );
    }
    let callback = match callback_snapshot(&state, &provider).await {
        Ok(callback) => callback,
        Err(error) => return service_error(error, request_id),
    };
    let expires_at_ms = now_ms()
        .saturating_add(timeout_ms)
        .saturating_add(RETENTION_MS);
    state.admin_invocation_runs.insert(
        run_id.clone(),
        AdminInvocationRun {
            provider_id: provider_id.clone(),
            organization_code: organization_code.clone(),
            target_bot_uuid: request.target_bot_uuid.clone(),
            session_id: session_id.clone(),
            detach: request.detach,
            expires_at_ms,
            delivery_error: None,
            callback: if request.detach { None } else { callback },
            callback_claimed: false,
        },
    );

    let delivered = state
        .services
        .a2a_chat_runs
        .start_async_chat(AsyncA2aChatCommand {
            // The established run engine only accepts a bot caller. The target bot
            // is used as a synthetic execution principal after this route has
            // performed the manager and effective-membership authorization above.
            caller: CallerContext::Bot(BotActor {
                bot_uuid: request.target_bot_uuid.clone(),
            }),
            target_bot_id: request.target_bot_uuid.clone(),
            message: text,
            from_actor_id: Some("organization-admin".to_string()),
            run_channel_from: None,
            authenticated_staff_id: None,
            run_id: run_id.clone(),
            session_key: session_id.clone(),
            timeout_ms,
            client: Some("organization-admin".to_string()),
            tags: Vec::new(),
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: request.detach.then(|| "detached".to_string()),
            organization_code: Some(organization_code.clone()),
        })
        .await;

    let status = match delivered {
        Ok(accepted) if request.detach => {
            info!(run_id = %run_id, provider_id = %provider_id, "organization admin detached run dispatched");
            if accepted.status == "failed" {
                "delivery_failed"
            } else {
                "dispatched"
            }
            .to_string()
        }
        Ok(accepted) => accepted.status,
        Err(error) => {
            let message = error.to_string();
            state
                .admin_invocation_runs
                .set_delivery_error(&run_id, message);
            if !request.detach {
                notify_terminal_callback(&state, &run_id, "error", "target bot delivery failed");
            }
            "delivery_failed".to_string()
        }
    };
    if !request.detach {
        schedule_timeout_callback(state.clone(), run_id.clone(), timeout_ms);
    }
    let location = format!("/organizations/{organization_code}/admin-runs/{run_id}");
    let mut response = (
        StatusCode::ACCEPTED,
        Json(Envelope {
            code: 20000,
            message: "accepted".to_string(),
            data: AdminRunView {
                run_id,
                provider_id,
                organization_code,
                target_bot_uuid: request.target_bot_uuid,
                session_id,
                status,
                message: None,
                error: None,
            },
            request_id,
        }),
    )
        .into_response();
    if let Ok(location) = axum::http::HeaderValue::from_str(&location) {
        response
            .headers_mut()
            .insert(axum::http::header::LOCATION, location);
    }
    response
}

pub async fn get_admin_run(
    State(state): State<HttpAppState>,
    Path((organization_code, run_id)): Path<(String, String)>,
    Query(query): Query<GetAdminRunQuery>,
    headers: HeaderMap,
) -> Response {
    let request_id = request_id();
    let provider = match authenticate_manager(&state, &headers, &organization_code).await {
        Ok(provider) => provider,
        Err(error) => return error.into_response_with_id(request_id),
    };
    let provider_id = provider.provider_id;
    let Some(run) = state
        .admin_invocation_runs
        .get_for_provider(&run_id, &provider_id)
    else {
        return response_error(
            StatusCode::NOT_FOUND,
            40402,
            "admin run not found".to_string(),
            request_id,
        );
    };
    if run.organization_code != organization_code {
        return response_error(
            StatusCode::NOT_FOUND,
            40402,
            "admin run not found".to_string(),
            request_id,
        );
    }
    if run.detach {
        let (status, error) = match run.delivery_error {
            Some(message) => (
                "delivery_failed".to_string(),
                Some(AdminRunError {
                    code: "ADMIN_INVOCATION_DELIVERY_FAILED",
                    message,
                }),
            ),
            None => ("dispatched".to_string(), None),
        };
        return Json(Envelope {
            code: 20000,
            message: "ok".to_string(),
            data: AdminRunView {
                run_id,
                provider_id,
                organization_code,
                target_bot_uuid: run.target_bot_uuid,
                session_id: run.session_id,
                status,
                message: None,
                error,
            },
            request_id,
        })
        .into_response();
    }
    if let Some(message) = run.delivery_error {
        return Json(Envelope {
            code: 20000,
            message: "ok".to_string(),
            data: AdminRunView {
                run_id,
                provider_id,
                organization_code,
                target_bot_uuid: run.target_bot_uuid,
                session_id: run.session_id,
                status: "failed".to_string(),
                message: None,
                error: Some(AdminRunError {
                    code: "ADMIN_INVOCATION_DELIVERY_FAILED",
                    message,
                }),
            },
            request_id,
        })
        .into_response();
    }
    let result = state
        .services
        .a2a_chat_runs
        .get_run(ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: run.target_bot_uuid.clone(),
            }),
            run_id: run_id.clone(),
            wait_ms: query.wait_ms.min(state.async_chat_poll_wait_max_ms),
            since_version: 0,
        })
        .await;
    let view = match result {
        Ok(result) => run_view_from_a2a(
            run_id,
            provider_id,
            organization_code,
            run,
            result.status,
            result.response,
        ),
        Err(error) => return service_error(error, request_id),
    };
    Json(Envelope {
        code: 20000,
        message: "ok".to_string(),
        data: view,
        request_id,
    })
    .into_response()
}

pub fn notify_terminal_callback(
    state: &HttpAppState,
    run_id: &str,
    terminal_state: &str,
    text: &str,
) {
    if !matches!(terminal_state, "final" | "error" | "aborted") {
        return;
    }
    let Some(run) = state.admin_invocation_runs.claim_callback(run_id) else {
        return;
    };
    let Some(callback) = run.callback else {
        return;
    };
    let body = if terminal_state == "final" {
        json!({ "run_id": run_id, "provider_id": run.provider_id, "status": "completed", "message": { "role": "assistant", "content": [{ "type": "text", "text": text }] } })
    } else {
        json!({ "run_id": run_id, "provider_id": run.provider_id, "status": "failed", "error": { "code": "ADMIN_INVOCATION_TARGET_FAILED", "message": text } })
    };
    let provider_id = run.provider_id;
    let run_id = run_id.to_string();
    let outbound_url_guard = state.outbound_url_guard.clone();
    tokio::spawn(async move {
        let callback_url = callback_url_for_log(&callback.url);
        let guarded_url = match outbound_url_guard.resolve_request_http_url(&callback.url).await {
            Ok(url) => url,
            Err(error) => {
                warn!(
                    run_id = %run_id,
                    provider_id = %provider_id,
                    callback_url = %callback_url,
                    resolved_ip = ?callback_blocked_ip(&error),
                    reason = %error,
                    "organization admin terminal callback blocked by outbound URL policy"
                );
                return;
            }
        };
        let mut client_builder = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none());
        if let Some((host, addresses)) = guarded_url.dns_override() {
            client_builder = client_builder.resolve_to_addrs(host, addresses);
        }
        let client = match client_builder.build() {
            Ok(client) => client,
            Err(error) => {
                warn!(
                    run_id = %run_id,
                    provider_id = %provider_id,
                    callback_url = %callback_url,
                    error = %error,
                    "organization admin terminal callback client creation failed"
                );
                return;
            }
        };
        let response = client
            .post(guarded_url.as_str())
            .header("content-type", "application/json")
            .header(BCN_PROVIDER_ID_HEADER, provider_id)
            .bearer_auth(callback.bearer_token)
            .json(&body)
            .send()
            .await;
        match response {
            Ok(response) if response.status().is_success() => {
                info!(run_id = %run_id, "organization admin terminal callback acknowledged")
            }
            Ok(response) => {
                warn!(run_id = %run_id, status = %response.status(), "organization admin terminal callback was not acknowledged")
            }
            Err(error) => {
                warn!(run_id = %run_id, error = %error, "organization admin terminal callback failed")
            }
        }
    });
}

async fn authenticate_manager(
    state: &HttpAppState,
    headers: &HeaderMap,
    organization_code: &str,
) -> Result<ProviderRecord, AdminRunRouteError> {
    let token = extract_bearer_token(headers).ok_or_else(|| {
        AdminRunRouteError::new(
            StatusCode::UNAUTHORIZED,
            40101,
            "valid provider admin token is required",
        )
    })?;
    let provider = state
        .services
        .provider_core
        .authenticate_provider_admin(&token)
        .await
        .map_err(|_| {
            AdminRunRouteError::new(
                StatusCode::UNAUTHORIZED,
                40101,
                "invalid provider admin token",
            )
        })?;
    let header_provider_id = headers
        .get(BCN_PROVIDER_ID_HEADER)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            AdminRunRouteError::new(
                StatusCode::BAD_REQUEST,
                40001,
                "X-BCN-Provider-Id header is required",
            )
        })?;
    if header_provider_id != provider.provider_id {
        return Err(AdminRunRouteError::new(
            StatusCode::FORBIDDEN,
            40301,
            "provider header does not match token",
        ));
    }
    if provider.disabled {
        return Err(AdminRunRouteError::new(
            StatusCode::FORBIDDEN,
            40302,
            "provider is disabled",
        ));
    }
    let organization = state
        .services
        .organization
        .get_for_manager(&provider.provider_id, organization_code)
        .await
        .map_err(|error| match error {
            ServiceError::InvalidOperation { .. } => {
                AdminRunRouteError::new(StatusCode::NOT_FOUND, 40401, "organization not found")
            }
            ServiceError::Forbidden(_) => AdminRunRouteError::new(
                StatusCode::FORBIDDEN,
                40302,
                "organization manager required",
            ),
            _ => AdminRunRouteError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                50001,
                "organization authorization failed",
            ),
        })?;
    if organization.managing_provider_id != provider.provider_id {
        return Err(AdminRunRouteError::new(
            StatusCode::FORBIDDEN,
            40302,
            "organization manager required",
        ));
    }
    Ok(provider)
}

async fn callback_snapshot(
    state: &HttpAppState,
    provider: &ProviderRecord,
) -> Result<Option<AdminInvocationCallback>, ServiceError> {
    let config: Value =
        serde_json::from_str(&provider.config).map_err(|error| ServiceError::InvalidOperation {
            message: format!("invalid provider config: {error}"),
            request_id: None,
        })?;
    let Some(url) = config
        .get("admin_callback_url")
        .and_then(Value::as_str)
        .map(str::to_string)
    else {
        return Ok(None);
    };
    let credential = state
        .services
        .provider_core
        .get_downlink_credential(&provider.provider_id)
        .await?;
    Ok(Some(AdminInvocationCallback {
        url,
        bearer_token: credential.secret_value,
    }))
}

fn validate_message(message: AdminRunMessage) -> Result<String, String> {
    if message.role != "user" || message.content.len() != 1 || message.content[0].kind != "text" {
        return Err("message must contain exactly one user text content block".to_string());
    }
    let text = message
        .content
        .into_iter()
        .next()
        .expect("checked length")
        .text;
    if text.trim().is_empty() {
        return Err("message text must not be empty".to_string());
    }
    Ok(text)
}

fn valid_session_id(value: &str) -> bool {
    !value.is_empty()
        && value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.' | ':'))
}

fn default_admin_session_id(run_id: &str) -> String {
    format!("admin-{run_id}")
}

fn new_admin_run_id() -> String {
    format!("run-{}", Uuid::new_v4().simple())
}

fn callback_blocked_ip(error: &OutboundUrlError) -> Option<IpAddr> {
    match error {
        OutboundUrlError::UnsafeAddress(address) => Some(*address),
        _ => None,
    }
}

fn callback_url_for_log(callback_url: &str) -> String {
    let Ok(mut url) = reqwest::Url::parse(callback_url) else {
        return "<invalid callback URL>".to_string();
    };
    let _ = url.set_username("");
    let _ = url.set_password(None);
    url.set_query(None);
    url.set_fragment(None);
    url.to_string()
}

#[cfg(test)]
mod tests {
    use std::net::{IpAddr, Ipv4Addr};

    use bcs_route_security::OutboundUrlError;

    use super::{
        callback_blocked_ip, callback_url_for_log, default_admin_session_id, new_admin_run_id,
        valid_session_id,
    };

    #[test]
    fn admin_run_id_uses_hyphenated_prefix() {
        let run_id = new_admin_run_id();

        assert!(run_id.starts_with("run-"));
        assert!(!run_id.contains('_'));
    }

    #[test]
    fn default_session_id_is_valid_and_derived_from_run_id() {
        let run_id = "run-7f3a2b1c";
        let session_id = default_admin_session_id(run_id);

        assert_eq!(session_id, "admin-run-7f3a2b1c");
        assert!(valid_session_id(&session_id));
    }

    #[test]
    fn session_id_accepts_simple_identifier_characters() {
        assert!(valid_session_id("Session_name.1:part-a"));
        assert!(!valid_session_id(""));
        assert!(!valid_session_id("session/name"));
    }

    #[test]
    fn callback_block_log_fields_include_rejected_ip_without_query() {
        let blocked_ip = IpAddr::V4(Ipv4Addr::new(10, 24, 0, 8));
        let error = OutboundUrlError::UnsafeAddress(blocked_ip);

        assert_eq!(callback_blocked_ip(&error), Some(blocked_ip));
        assert_eq!(
            callback_url_for_log(
                "https://bearer-token@provider.example.com/callback?token=secret#fragment",
            ),
            "https://provider.example.com/callback"
        );
    }
}

fn schedule_timeout_callback(state: HttpAppState, run_id: String, timeout_ms: u64) {
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(timeout_ms)).await;
        let Some(run) = state.admin_invocation_runs.get(&run_id) else {
            return;
        };
        let result = state
            .services
            .a2a_chat_runs
            .get_run(ChatRunQueryCommand {
                caller: CallerContext::Bot(BotActor {
                    bot_uuid: run.target_bot_uuid,
                }),
                run_id: run_id.clone(),
                wait_ms: 0,
                since_version: 0,
            })
            .await;
        if let Ok(result) = result {
            if matches!(result.status.as_str(), "timeout" | "timed_out" | "failed") {
                notify_terminal_callback(
                    &state,
                    &run_id,
                    "error",
                    "target bot did not complete the run before timeout",
                );
            }
        }
    });
}

fn run_view_from_a2a(
    run_id: String,
    provider_id: String,
    organization_code: String,
    run: AdminInvocationRun,
    status: String,
    response: Option<Value>,
) -> AdminRunView {
    let terminal_error = matches!(status.as_str(), "failed" | "timed_out" | "timeout");
    let message = response.as_ref().and_then(|value| value.get("content").or_else(|| value.get("message"))).cloned().map(|text| json!({ "role": "assistant", "content": [{ "type": "text", "text": text.as_str().unwrap_or_default() }] }));
    AdminRunView {
        run_id,
        provider_id,
        organization_code,
        target_bot_uuid: run.target_bot_uuid,
        session_id: run.session_id,
        status: if status == "timeout" {
            "timed_out".to_string()
        } else {
            status
        },
        message: if terminal_error { None } else { message },
        error: terminal_error.then(|| AdminRunError {
            code: "ADMIN_INVOCATION_TARGET_FAILED",
            message: "target bot did not complete the run".to_string(),
        }),
    }
}

#[derive(Debug)]
struct AdminRunRouteError {
    status: StatusCode,
    code: u32,
    message: String,
}
impl AdminRunRouteError {
    fn new(status: StatusCode, code: u32, message: impl Into<String>) -> Self {
        Self {
            status,
            code,
            message: message.into(),
        }
    }
    fn into_response_with_id(self, request_id: String) -> Response {
        response_error(self.status, self.code, self.message, request_id)
    }
}

fn service_error(error: ServiceError, request_id: String) -> Response {
    match error {
        ServiceError::Forbidden(_) => response_error(
            StatusCode::FORBIDDEN,
            40303,
            "target bot is not an effective organization member".to_string(),
            request_id,
        ),
        ServiceError::InvalidOperation { message, .. } => {
            response_error(StatusCode::BAD_REQUEST, 40001, message, request_id)
        }
        ServiceError::BotNotFound(_) => response_error(
            StatusCode::NOT_FOUND,
            40402,
            "admin run not found".to_string(),
            request_id,
        ),
        other => response_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            50001,
            other.to_string(),
            request_id,
        ),
    }
}

fn response_error(status: StatusCode, code: u32, message: String, request_id: String) -> Response {
    (
        status,
        Json(Envelope {
            code,
            message,
            data: json!({}),
            request_id,
        }),
    )
        .into_response()
}
fn request_id() -> String {
    format!("req_{}", Uuid::new_v4().simple())
}
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
