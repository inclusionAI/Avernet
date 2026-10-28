use crate::state::HttpAppState;
use axum::{
    Json, Router,
    extract::Request,
    extract::{DefaultBodyLimit, Path, State},
    http::{HeaderMap, StatusCode, header::CACHE_CONTROL},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post, put},
};
use bcs_service_api::application::collaboration_draft::*;
use serde::Deserialize;
use std::sync::Arc;

pub fn router() -> Router<HttpAppState> {
    Router::new()
        .route(
            "/collaboration/drafts/{id}",
            put(create).get(metadata).delete(delete),
        )
        .route("/collaboration/drafts/{id}/revisions", post(update))
        .route("/collaboration/drafts/{id}/revisions/{revision}", get(read))
        .route(
            "/collaboration/drafts/{id}/revisions/{revision}/validate",
            post(validate),
        )
        .route(
            "/collaboration/drafts/{id}/revisions/{revision}/reference",
            get(reference),
        )
        .route(
            "/collaboration/drafts/{id}/revisions/{revision}/share",
            axum::routing::delete(revoke),
        )
        // JSON escaping may expand a legal 64 KiB source up to sixfold.
        .layer(DefaultBodyLimit::max(400_000))
        .layer(middleware::from_fn(no_store))
}

async fn no_store(request: Request, next: Next) -> Response {
    let mut response = next.run(request).await;
    // Extractor errors must not echo unknown field names or submitted values.
    if matches!(
        response.status(),
        StatusCode::BAD_REQUEST | StatusCode::UNPROCESSABLE_ENTITY | StatusCode::PAYLOAD_TOO_LARGE
    ) {
        response = (
            response.status(),
            Json(serde_json::json!({"error":"DRAFT_INPUT"})),
        )
            .into_response();
    }
    response
        .headers_mut()
        .insert(CACHE_CONTROL, "no-store".parse().unwrap());
    response
}

fn failure(e: DraftError) -> Response {
    let (status, code) = match e {
        DraftError::Invalid => (StatusCode::BAD_REQUEST, "DRAFT_INPUT"),
        DraftError::Unavailable => (StatusCode::NOT_FOUND, "DRAFT_REFERENCE_UNAVAILABLE"),
        DraftError::Forbidden => (StatusCode::FORBIDDEN, "DRAFT_FORBIDDEN"),
        DraftError::Conflict => (StatusCode::CONFLICT, "DRAFT_CONFLICT"),
        DraftError::ServiceUnavailable => {
            (StatusCode::SERVICE_UNAVAILABLE, "DRAFT_SERVICE_UNAVAILABLE")
        }
    };
    (status, Json(serde_json::json!({ "error": code }))).into_response()
}

fn service(state: &HttpAppState) -> Result<Arc<dyn CollaborationDraftService>, Response> {
    state
        .services
        .collaboration_drafts
        .clone()
        .ok_or_else(|| failure(DraftError::ServiceUnavailable))
}

async fn owner(state: &HttpAppState, headers: &HeaderMap) -> Result<String, Response> {
    match state.bot_uuid_from_headers(headers).await {
        Ok(Some(id)) => Ok(id),
        _ => Err((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"error":"DRAFT_BOT_AUTH_REQUIRED"})),
        )
            .into_response()),
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ContentBody {
    definition_yaml: String,
    title: Option<String>,
}
impl From<ContentBody> for DraftContent {
    fn from(c: ContentBody) -> Self {
        Self {
            definition_yaml: c.definition_yaml,
            title: c.title,
        }
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UpdateBody {
    definition_yaml: String,
    title: Option<String>,
    expected_revision: u64,
}

async fn create(
    State(s): State<HttpAppState>,
    Path(id): Path<String>,
    h: HeaderMap,
    Json(c): Json<ContentBody>,
) -> Result<Response, Response> {
    let owner = owner(&s, &h).await?;
    let saved = service(&s)?
        .create(&owner, &id, c.into())
        .await
        .map_err(failure)?;
    Ok((
        if saved.created {
            StatusCode::CREATED
        } else {
            StatusCode::OK
        },
        Json(saved.content),
    )
        .into_response())
}
async fn update(
    State(s): State<HttpAppState>,
    Path(id): Path<String>,
    h: HeaderMap,
    Json(c): Json<UpdateBody>,
) -> Result<Response, Response> {
    let owner = owner(&s, &h).await?;
    let result = service(&s)?
        .update(
            &owner,
            &id,
            c.expected_revision,
            DraftContent {
                definition_yaml: c.definition_yaml,
                title: c.title,
            },
        )
        .await
        .map_err(failure)?;
    Ok(Json(result).into_response())
}
async fn metadata(
    State(s): State<HttpAppState>,
    Path(id): Path<String>,
    h: HeaderMap,
) -> Result<Response, Response> {
    let owner = owner(&s, &h).await?;
    Ok(Json(service(&s)?.metadata(&owner, &id).await.map_err(failure)?).into_response())
}
async fn read(
    State(s): State<HttpAppState>,
    Path((id, revision)): Path<(String, u64)>,
) -> Result<Response, Response> {
    Ok(Json(
        service(&s)?
            .read(&id, revision)
            .await
            .map_err(failure)?,
    )
    .into_response())
}
async fn validate(
    State(s): State<HttpAppState>,
    Path((id, revision)): Path<(String, u64)>,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    if !body.is_empty() {
        return Err(failure(DraftError::Invalid));
    }
    Ok(Json(
        service(&s)?
            .validate(&id, revision)
            .await
            .map_err(failure)?,
    )
    .into_response())
}
async fn reference(
    State(s): State<HttpAppState>,
    Path((id, revision)): Path<(String, u64)>,
    h: HeaderMap,
) -> Result<Response, Response> {
    let owner = owner(&s, &h).await?;
    let reference = service(&s)?.reference(&owner, &id, revision).await.map_err(failure)?;
    // Deployed CLI versions require a canonical base64url value decoding to 32 bytes.
    // This constant is only a wire-format placeholder: never stored or authenticated.
    Ok(Json(serde_json::json!({
        "schemaVersion": reference.schema_version,
        "draftId": reference.draft_id,
        "revision": reference.revision,
        "readToken": "A".repeat(43),
    })).into_response())
}
async fn revoke(
    State(s): State<HttpAppState>,
    Path((id, revision)): Path<(String, u64)>,
    h: HeaderMap,
) -> Result<Response, Response> {
    let owner = owner(&s, &h).await?;
    service(&s)?
        .revoke(&owner, &id, revision)
        .await
        .map_err(failure)?;
    Ok(StatusCode::NO_CONTENT.into_response())
}
async fn delete(
    State(s): State<HttpAppState>,
    Path(id): Path<String>,
    h: HeaderMap,
) -> Result<Response, Response> {
    let owner = owner(&s, &h).await?;
    service(&s)?.delete(&owner, &id).await.map_err(failure)?;
    Ok(StatusCode::NO_CONTENT.into_response())
}
