//! HTTP route handlers for the Group Context API.
//!
//! Origin parameters (tenant_id, group_id, session_id, run_id) are expected
//! from the JSON request body. The `actor_id` is resolved from the auth header
//! by BCS infrastructure (`bot_uuid_from_headers`) and cannot be overridden
//! by the request body — this is the critical security property that makes
//! origin non-forgeable.

use axum::{extract::State, http::HeaderMap, Json};
use serde::Deserialize;

use bcs_service_api::ServiceError;

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;

// ── Request shapes ─────────────────────────────────────────────────────────

/// Request body for POST /groupcontext/status.
///
/// `actor_id` is intentionally NOT accepted from the body — it always comes
/// from `bot_uuid_from_headers`. This prevents a caller from forging origin.
#[derive(Debug, Deserialize)]
pub struct StatusBody {
    pub tenant_id: String,
    pub group_id: String,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub run_id: Option<String>,
    #[serde(default)]
    pub domain: Option<String>,
    #[serde(default = "default_status_limit")]
    pub limit: u32,
}

fn default_status_limit() -> u32 {
    20
}

// ── Handlers ───────────────────────────────────────────────────────────────

/// `POST /groupcontext/status`
///
/// Returns the active contexts and available policy templates visible to the
/// calling actor within the given scope.
pub async fn status(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    Json(body): Json<StatusBody>,
) -> Result<Json<serde_json::Value>, HttpAdapterError> {
    let application = state.group_context_application.as_ref().ok_or_else(|| {
        HttpAdapterError::Service(ServiceError::InvalidOperation {
            message: "group context service is not configured".to_string(),
            request_id: None,
        })
    })?;

    // actor_id is resolved from auth — never from the request body.
    let actor_id = state
        .bot_uuid_from_headers(&headers)
        .await
        .ok_or_else(|| HttpAdapterError::Unauthorized("valid bot token is required".to_string()))?;

    let request = bcs_service_api::application::group_context::StatusRequest {
        tenant_id: body.tenant_id,
        group_id: body.group_id,
        session_id: body.session_id,
        run_id: body.run_id,
        actor_id,
        domain: body.domain,
        limit: Some(body.limit),
    };

    let response = application.status(request).await.map_err(|e| {
        HttpAdapterError::Service(ServiceError::InvalidOperation {
            message: format!("group context status failed: {e}"),
            request_id: None,
        })
    })?;

    Ok(Json(serde_json::to_value(response).unwrap_or_default()))
}