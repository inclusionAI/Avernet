//! Independent Agent invocation management delivery. Authentication is shared
//! with V1; resource policy stays in the injected application services.
mod dto;
mod reads;
mod writes;
use super::common::{ApiState, Envelope, RequestId};
use axum::{
    Json, Router,
    extract::{MatchedPath, Path, Request, State},
    http::{HeaderValue, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
};
use bcs_service_api::application::{agent_invoke::*, v1::identity::AuthenticatedCaller};
use std::{collections::BTreeMap, sync::Arc};

/// Composition-only capability. No incoming header/body can select a scope,
/// tenant or governance mapping, and no runtime writer is enabled by this type.
pub struct AgentInvokeHttpServices {
    pub scope: AgentInvokeScope,
    pub tenant: Option<String>,
    /// Exact verified numeric App -> durable governance principal. An entry
    /// still needs current pair/action delegation in the application/store.
    pub governance_apps: BTreeMap<i64, String>,
    pub management: Arc<dyn AgentInvokeGrantManagementService>,
    pub queries: Arc<dyn AgentInvokeGovernanceQueryService>,
}
pub(crate) fn protected_router(state: ApiState) -> Router<ApiState> {
    let mut result = Router::new();
    for prefix in [
        "/api/v1/collaboration/agent-invoke",
        "/openapi/v1/collaboration/agent-invoke",
    ] {
        let mut r = Router::new();
        for suffix in [
            "/grants",
            "/requests",
            "/grants/{edge_id}",
            "/requests/{request_id}",
            "/grants/{edge_id}/revoke",
            "/requests/{request_id}/approve",
            "/requests/{request_id}/reject",
            "/requests/{request_id}/cancel",
            "/degrees",
            "/audit",
        ] {
            r = r.route(suffix, axum::routing::any(handle));
        }
        r = r.fallback(|req: Request| async move {
            failure(
                &RequestId::from_headers(req.headers()),
                AgentInvokeError::NotFound,
            )
        });
        result = result.nest(prefix, r);
    }
    result
        .layer(middleware::from_fn_with_state(
            state,
            super::common::verify_principal::<ApiState>,
        ))
        .layer(middleware::from_fn(boundary))
}
async fn boundary(req: Request, next: Next) -> Response {
    let id = RequestId::from_headers(req.headers());
    let mut response = next.run(req).await;
    // All handler errors already use closed envelopes; framework errors never
    // echo malformed user-controlled URL/JSON or credential strings.
    if response.status().is_client_error() && response.headers().get("content-type").is_none() {
        response = failure(&id, AgentInvokeError::InvalidInput);
    }
    response
        .headers_mut()
        .insert("cache-control", HeaderValue::from_static("no-store"));
    if let Ok(value) = HeaderValue::from_str(&id.0) {
        response.headers_mut().insert("x-request-id", value);
    }
    response
}
pub(super) fn failure(id: &RequestId, error: AgentInvokeError) -> Response {
    let (status, code, name) = match error {
        AgentInvokeError::InvalidInput => {
            (StatusCode::BAD_REQUEST, 40000, "agent_invoke_invalid_input")
        }
        AgentInvokeError::Unauthenticated => (StatusCode::UNAUTHORIZED, 40100, "unauthenticated"),
        AgentInvokeError::Forbidden => (StatusCode::FORBIDDEN, 40300, "agent_invoke_forbidden"),
        AgentInvokeError::NotFound => (StatusCode::NOT_FOUND, 40400, "agent_invoke_not_found"),
        AgentInvokeError::Conflict => (StatusCode::CONFLICT, 40900, "agent_invoke_conflict"),
        AgentInvokeError::LimitExceeded => (
            StatusCode::TOO_MANY_REQUESTS,
            42900,
            "agent_invoke_limit_exceeded",
        ),
        AgentInvokeError::Unavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            50300,
            "agent_invoke_unavailable",
        ),
        AgentInvokeError::CommitUnknown => (
            StatusCode::SERVICE_UNAVAILABLE,
            50300,
            "agent_invoke_commit_unknown",
        ),
        AgentInvokeError::InvalidState => (
            StatusCode::SERVICE_UNAVAILABLE,
            50300,
            "agent_invoke_invalid_state",
        ),
    };
    (
        status,
        Json(Envelope::success(
            code,
            error.to_string(),
            serde_json::json!({"error_code":name}),
            &id.0,
        )),
    )
        .into_response()
}
pub(super) fn success(id: &RequestId, status: StatusCode, data: serde_json::Value) -> Response {
    (
        status,
        Json(Envelope::success(
            if status == StatusCode::CREATED {
                20100
            } else {
                20000
            },
            "OK",
            data,
            &id.0,
        )),
    )
        .into_response()
}
fn principal(
    c: &AuthenticatedCaller,
    s: &AgentInvokeHttpServices,
) -> AgentInvokeResult<AgentInvokePrincipal> {
    if c.tenant != s.tenant {
        return Err(AgentInvokeError::NotFound);
    }
    // A Bot's owner metadata cannot turn its credential into a Human operator.
    if let Some(bot) = &c.bot {
        return Ok(AgentInvokePrincipal::Agent(bot.bot_uuid.clone()));
    }
    if let Some(user) = &c.user {
        if user.id.trim().is_empty() {
            return Err(AgentInvokeError::Unauthenticated);
        }
        return Ok(AgentInvokePrincipal::Human(format!("human_{}", user.id)));
    }
    if let Some(app) = &c.app {
        if let Some(id) = s
            .governance_apps
            .get(&app.app_id)
            .filter(|id| !id.trim().is_empty())
        {
            return Ok(AgentInvokePrincipal::Governance(id.clone()));
        }
    }
    Err(AgentInvokeError::Forbidden)
}
async fn handle(
    State(state): State<ApiState>,
    path: Result<Path<BTreeMap<String, String>>, axum::extract::rejection::PathRejection>,
    req: Request,
) -> Response {
    let id = req
        .extensions()
        .get::<RequestId>()
        .cloned()
        .unwrap_or_else(|| RequestId::from_headers(req.headers()));
    let result = async {
        let c = req
            .extensions()
            .get::<AuthenticatedCaller>()
            .ok_or(AgentInvokeError::Unauthenticated)?;
        let services = state
            .agent_invoke
            .as_ref()
            .ok_or(AgentInvokeError::Unavailable)?;
        let principal = principal(c, services)?;
        let Path(path) = path.map_err(|_| AgentInvokeError::InvalidInput)?;
        let route = req
            .extensions()
            .get::<MatchedPath>()
            .ok_or(AgentInvokeError::InvalidState)?
            .as_str()
            .to_string();
        let tail = route
            .rsplit('/')
            .next()
            .ok_or(AgentInvokeError::InvalidInput)?;
        let read = matches!(
            tail,
            "grants" | "requests" | "{edge_id}" | "{request_id}" | "degrees" | "audit"
        );
        let write = matches!(
            tail,
            "grants" | "requests" | "approve" | "reject" | "cancel" | "revoke"
        );
        if !(req.method() == axum::http::Method::GET && read
            || req.method() == axum::http::Method::POST && write)
        {
            return Ok((
                StatusCode::METHOD_NOT_ALLOWED,
                Json(Envelope::success(
                    40500,
                    "Method not allowed",
                    serde_json::json!({"error_code":"method_not_allowed"}),
                    &id.0,
                )),
            )
                .into_response());
        }
        let params = dto::params(req.uri().query())?;
        match req.method().as_str() {
            "GET" => reads::handle(services, principal, &route, &path, params, &id).await,
            "POST" => writes::handle(services, principal, &route, &path, params, req, &id).await,
            _ => Ok((
                StatusCode::METHOD_NOT_ALLOWED,
                Json(Envelope::success(
                    40500,
                    "Method not allowed",
                    serde_json::json!({"error_code":"method_not_allowed"}),
                    &id.0,
                )),
            )
                .into_response()),
        }
    }
    .await;
    result.unwrap_or_else(|error| failure(&id, error))
}
