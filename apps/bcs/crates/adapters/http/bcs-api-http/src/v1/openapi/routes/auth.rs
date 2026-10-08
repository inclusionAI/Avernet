use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use bcs_service_api::application::{RequestAuthHeaders};
use bcs_service_api::application::v1::{
    ApplicationError, BuildLoginUrls, CompleteOAuthLogin, LogoutSession, ReadCurrentUser,
    RefreshSession,
};
use serde::Deserialize;
use serde_json::json;

use crate::v1::common::{
    application_error_response, ApiState, Envelope, ErrorResponse, RequestId,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/url", get(login_urls))
        .route("/callback/{provider}", get(callback))
        .route("/user", get(current_user))
        .route("/refresh", post(refresh))
        .route("/logout", post(logout))
}

#[derive(Deserialize)]
struct CallbackParams {
    code: Option<String>,
    auth_code: Option<String>,
    state: String,
}

async fn login_urls(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let auth_service = auth_service(&state, &request_id)?;

    let callback_base_url = callback_base_url(&state);
    let result = auth_service
        .login_urls(BuildLoginUrls { callback_base_url })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;

    Ok(Json(Envelope::success(20_000, "OK", result, request_id.0)).into_response())
}

async fn callback(
    State(state): State<ApiState>,
    Path(provider): Path<String>,
    Query(params): Query<CallbackParams>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let auth_service = auth_service(&state, &request_id)?;
    let redirect = auth_service
        .complete_login(CompleteOAuthLogin {
            provider,
            code: params.code,
            auth_code: params.auth_code,
            state: params.state,
            callback_base_url: callback_base_url(&state),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;

    Ok((
        StatusCode::FOUND,
        [
            ("location", redirect.location),
            ("set-cookie", redirect.set_cookie),
        ],
    )
        .into_response())
}

async fn current_user(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let auth_service = auth_service(&state, &request_id)?;
    let result = auth_service
        .current_user(ReadCurrentUser {
            headers: request_auth_headers(&headers),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;

    Ok(Json(Envelope::success(20_000, "OK", result, request_id.0)).into_response())
}

async fn refresh(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let auth_service = auth_service(&state, &request_id)?;
    let result = auth_service
        .refresh_session(RefreshSession {
            headers: request_auth_headers(&headers),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;

    Ok((
        StatusCode::OK,
        [("set-cookie", result.set_cookie)],
        Json(Envelope::success(20_000, "OK", json!({}), request_id.0)),
    )
        .into_response())
}

async fn logout(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let auth_service = auth_service(&state, &request_id)?;
    let result = auth_service
        .logout(LogoutSession {
            headers: request_auth_headers(&headers),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;

    Ok((
        StatusCode::OK,
        [("set-cookie", result.set_cookie)],
        Json(Envelope::success(20_000, "OK", json!({}), request_id.0)),
    )
        .into_response())
}

fn auth_service<'a>(
    state: &'a ApiState,
    request_id: &RequestId,
) -> Result<&'a std::sync::Arc<dyn bcs_service_api::application::v1::AuthService>, ErrorResponse> {
    state.auth_service.as_ref().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::not_found("auth_not_configured", "OAuth is not configured"),
        )
    })
}

fn callback_base_url(state: &ApiState) -> String {
    format!(
        "{}/callback",
        state.auth_public_base_url.trim_end_matches('/')
    )
}

fn request_auth_headers(headers: &HeaderMap) -> RequestAuthHeaders {
    let header = |name: axum::http::header::HeaderName| {
        headers
            .get(name)
            .and_then(|value| value.to_str().ok())
            .map(str::to_string)
    };
    RequestAuthHeaders {
        authorization: header(axum::http::header::AUTHORIZATION),
        cookie: header(axum::http::header::COOKIE),
        forwarded_headers: headers
            .iter()
            .filter_map(|(name, value)| {
                let name = name.as_str();
                if name.starts_with("x-") || name.starts_with("forwarded") {
                    value
                        .to_str()
                        .ok()
                        .map(|value| (name.to_string(), value.to_string()))
                } else {
                    None
                }
            })
            .collect(),
    }
}
