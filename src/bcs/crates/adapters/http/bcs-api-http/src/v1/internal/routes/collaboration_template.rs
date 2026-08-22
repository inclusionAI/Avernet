use std::sync::Arc;

use axum::Router;
use axum::extract::rejection::{PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, CollaborationTemplateService,
    GetCollaborationTemplate, ListCollaborationTemplates,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::collaboration_template::{
    GetCollaborationTemplateQuery, ListCollaborationTemplatesQuery,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/templates", get(list_templates))
        .route("/templates/{template_id}", get(get_template))
}

fn service(state: &ApiState, request_id: &RequestId) -> Result<Arc<dyn CollaborationTemplateService>, ErrorResponse> {
    state.collaboration_template_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("V1 Collaboration Template service is not configured"),
        )
    })
}

async fn list_templates(
    State(state): State<ApiState>,
    Extension(_caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    headers: HeaderMap,
    query: Result<Query<ListCollaborationTemplatesQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .list_templates(ListCollaborationTemplates {
            requested_language: query.lang,
            accept_language: accept_language(&headers),
            tags: parse_tags(query.tags),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn get_template(
    State(state): State<ApiState>,
    Extension(_caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    headers: HeaderMap,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<GetCollaborationTemplateQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(template_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let format = query.format.into();
    let detail = service(&state, &request_id)?
        .get_template(GetCollaborationTemplate {
            template_id,
            requested_language: query.lang,
            accept_language: accept_language(&headers),
            format,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;

    match format {
        bcs_service_api::CollaborationTemplateFormat::Yaml => {
            yaml_response(&request_id, detail.id, detail.lang, detail.yaml)
        }
        bcs_service_api::CollaborationTemplateFormat::Json => Ok((
            StatusCode::OK,
            Json(Envelope::success(20_000, "OK", detail, request_id.0)),
        )
            .into_response()),
    }
}

fn parse_tags(tags: Option<String>) -> Vec<String> {
    tags.unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|tag| !tag.is_empty())
        .map(ToString::to_string)
        .collect()
}

fn accept_language(headers: &HeaderMap) -> Option<String> {
    headers
        .get(header::ACCEPT_LANGUAGE)
        .and_then(|value| value.to_str().ok())
        .map(ToString::to_string)
}

fn yaml_response(
    request_id: &RequestId,
    id: String,
    lang: String,
    yaml: String,
) -> Result<Response, ErrorResponse> {
    let mut response_headers = HeaderMap::new();
    response_headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/yaml; charset=utf-8"),
    );
    insert_header_value(&mut response_headers, header::CONTENT_LANGUAGE, &lang, request_id)?;
    insert_header_value(&mut response_headers, "x-template-id", &id, request_id)?;
    insert_header_value(&mut response_headers, "x-template-lang", &lang, request_id)?;
    Ok((StatusCode::OK, response_headers, yaml).into_response())
}

fn insert_header_value<K>(
    headers: &mut HeaderMap,
    name: K,
    value: &str,
    request_id: &RequestId,
) -> Result<(), ErrorResponse>
where
    K: header::IntoHeaderName,
{
    let value = HeaderValue::from_str(value).map_err(|error| {
        application_error_response(
            request_id,
            ApplicationError::internal(format!("invalid response header value: {error}")),
        )
    })?;
    headers.insert(name, value);
    Ok(())
}