//! Human browser entry: same V1 handlers, with authentication selected by bootstrap.
use axum::{Router, Extension, Json, middleware, routing::{get, post}};
use bcs_service_api::application::v1::{AuthenticatedCaller, require_authenticated_user};
use super::common::{ApiState, Envelope, ErrorResponse, RequestId, application_error_response,
    enforce_invite_code_gate, verify_principal};

/// Native BCS browser hosts have cookie authentication but no Gateway hop.
/// This deliberately exposes only the operations needed to launch a collaboration.
pub fn router(state: ApiState) -> Router {
    use super::openapi::routes::{bot, group};
    Router::new().nest("/collaboration/user", Router::new()
        .route("/me", get(current_user))
        .route("/bots/mine", get(bot::list_mine))
        .route("/bots/{bot_id}/candidates", get(bot::list_candidates))
        .route("/groups", post(group::create_group)))
        .layer(middleware::from_fn_with_state(state.clone(), enforce_invite_code_gate::<ApiState>))
        .layer(middleware::from_fn_with_state(state.clone(), verify_principal::<ApiState>))
        .layer(middleware::from_fn(browser_boundary))
        .with_state(state)
}

async fn browser_boundary(request: axum::extract::Request, next: middleware::Next) -> axum::response::Response {
    use axum::response::IntoResponse;
    let request_id = RequestId::from_headers(request.headers());
    // The non-simple header requires CORS preflight on cross-origin browser writes.
    // It is also compared against the verified Human before any handler runs.
    let mut response = if request.headers().get("sec-fetch-site").is_some_and(|v| v == "cross-site")
        || (request.method() == axum::http::Method::POST && !request.headers().contains_key("x-bcs-expected-user")) {
        application_error_response(&request_id,
            bcs_service_api::application::v1::ApplicationError::forbidden_code(
                "browser_context_required", "Same-origin Human browser context required",
            )).into_response()
    } else { next.run(request).await };
    response.headers_mut().insert("cache-control", "no-store".parse().unwrap());
    response
}

pub async fn current_user(
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
) -> Result<impl axum::response::IntoResponse, ErrorResponse> {
    let user = require_authenticated_user(&caller)
        .map_err(|e| application_error_response(&request_id, e))?;
    Ok(([("cache-control", "no-store")], Json(Envelope::success(20_000, "OK",
        serde_json::json!({ "user_id": user.id,
            "display_name": user.display_name.as_deref().unwrap_or(&user.username) }), request_id.0))))
}
