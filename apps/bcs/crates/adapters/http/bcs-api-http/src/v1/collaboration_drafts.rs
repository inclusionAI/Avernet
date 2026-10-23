//! Capability-only V1 projection. Ownership and revision policy live in the shared service.
use std::sync::Arc;
use axum::{Router, Json, extract::{State, Path, Request, DefaultBodyLimit},
    http::{HeaderMap, StatusCode}, middleware::{self, Next}, response::{Response, IntoResponse},
    routing::{get, post}};
use bcs_service_api::application::collaboration_draft::{CollaborationDraftService, DraftAccess, DraftError};
use super::common::{Envelope, RequestId};

type DraftState = Option<Arc<dyn CollaborationDraftService>>;

pub fn router(service: DraftState) -> Router {
    Router::new()
        .route("/api/v1/collaboration/drafts/{id}/revisions/{revision}", get(read))
        .route("/api/v1/collaboration/drafts/{id}/revisions/{revision}/validate", post(validate))
        .layer(DefaultBodyLimit::max(0))
        .layer(middleware::from_fn(no_store))
        .with_state(service)
}

async fn no_store(request: Request, next: Next) -> Response {
    let id = RequestId::from_headers(request.headers());
    let mut response = next.run(request).await;
    if matches!(response.status(), StatusCode::BAD_REQUEST | StatusCode::UNPROCESSABLE_ENTITY | StatusCode::PAYLOAD_TOO_LARGE) {
        response = failure(&id, DraftError::Invalid);
    }
    response.headers_mut().insert("cache-control", "no-store".parse().unwrap());
    response
}

fn failure(id: &RequestId, error: DraftError) -> Response {
    let (status, code) = match error {
        DraftError::Invalid => (StatusCode::BAD_REQUEST, "DRAFT_INPUT"),
        DraftError::Unavailable => (StatusCode::NOT_FOUND, "DRAFT_REFERENCE_UNAVAILABLE"),
        DraftError::Forbidden => (StatusCode::FORBIDDEN, "DRAFT_FORBIDDEN"),
        DraftError::Conflict => (StatusCode::CONFLICT, "DRAFT_CONFLICT"),
        DraftError::ServiceUnavailable => (StatusCode::SERVICE_UNAVAILABLE, "DRAFT_SERVICE_UNAVAILABLE"),
    };
    (status, Json(Envelope::success(u32::from(status.as_u16()) * 100, code,
        serde_json::json!({"error_code": code}), id.0.clone()))).into_response()
}

fn access(headers: &HeaderMap, id: &RequestId) -> Result<DraftAccess, Response> {
    let values = headers.get_all("x-bcs-draft-read-token");
    if values.iter().count() != 1 { return Err(failure(id, DraftError::Unavailable)); }
    let read_grant = values.iter().next().and_then(|v| v.to_str().ok())
        .ok_or_else(|| failure(id, DraftError::Unavailable))?;
    // No ambient Bot or browser credential may upgrade a share to management.
    Ok(DraftAccess::ReadToken(read_grant.into()))
}

async fn read(State(service): State<DraftState>, Path((draft, revision)): Path<(String, u64)>, headers: HeaderMap) -> Result<Response, Response> {
    let id = RequestId::from_headers(&headers);
    let access = access(&headers, &id)?;
    let service = service.ok_or_else(|| failure(&id, DraftError::ServiceUnavailable))?;
    let result = service.read(access, &draft, revision).await.map_err(|e| failure(&id, e))?;
    Ok(Json(Envelope::success(20_000, "OK", result, id.0)).into_response())
}

async fn validate(State(service): State<DraftState>, Path((draft, revision)): Path<(String, u64)>, headers: HeaderMap, body: axum::body::Bytes) -> Result<Response, Response> {
    let id = RequestId::from_headers(&headers);
    if !body.is_empty() { return Err(failure(&id, DraftError::Invalid)); }
    let access = access(&headers, &id)?;
    let service = service.ok_or_else(|| failure(&id, DraftError::ServiceUnavailable))?;
    let result = service.validate(access, &draft, revision).await.map_err(|e| failure(&id, e))?;
    Ok(Json(Envelope::success(20_000, "OK", result, id.0)).into_response())
}
