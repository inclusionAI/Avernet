use axum::{
    Json,
    extract::{Path, Query, State},
    http::{
        HeaderMap, HeaderValue, StatusCode,
        header::{ACCEPT_LANGUAGE, CONTENT_LANGUAGE, CONTENT_TYPE},
    },
    response::{IntoResponse, Response},
};
use bcs_service_api::{
    CollaborationTemplateError, CollaborationTemplateFormat, GetCollaborationTemplateQuery,
    ListCollaborationTemplatesQuery,
};
use serde::Deserialize;

use crate::state::HttpAppState;

#[derive(Debug, Deserialize)]
pub struct ListTemplatesQuery {
    #[serde(default)]
    pub lang: Option<String>,
    #[serde(default)]
    pub tags: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct GetTemplateQuery {
    #[serde(default)]
    pub lang: Option<String>,
    #[serde(default)]
    pub format: Option<String>,
}

pub async fn list_templates(
    State(state): State<HttpAppState>,
    Query(query): Query<ListTemplatesQuery>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, TemplateRouteError> {
    let response = state
        .services
        .collaboration_templates
        .list_templates(ListCollaborationTemplatesQuery {
            requested_language: query.lang,
            accept_language: accept_language(&headers),
            tags: parse_tags(query.tags),
        })
        .await?;

    Ok(Json(response))
}

pub async fn get_template(
    State(state): State<HttpAppState>,
    Path(template_id): Path<String>,
    Query(query): Query<GetTemplateQuery>,
    headers: HeaderMap,
) -> Result<Response, TemplateRouteError> {
    let format = parse_format(query.format)?;
    let detail = state
        .services
        .collaboration_templates
        .get_template(GetCollaborationTemplateQuery {
            template_id,
            requested_language: query.lang,
            accept_language: accept_language(&headers),
            format,
        })
        .await?;

    match format {
        CollaborationTemplateFormat::Yaml => yaml_response(detail.id, detail.lang, detail.yaml),
        CollaborationTemplateFormat::Json => Ok(Json(detail).into_response()),
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

fn parse_format(
    format: Option<String>,
) -> Result<CollaborationTemplateFormat, CollaborationTemplateError> {
    match format.as_deref().unwrap_or("yaml") {
        "yaml" => Ok(CollaborationTemplateFormat::Yaml),
        "json" => Ok(CollaborationTemplateFormat::Json),
        other => Err(CollaborationTemplateError::InvalidFormat(other.to_string())),
    }
}

fn accept_language(headers: &HeaderMap) -> Option<String> {
    headers
        .get(ACCEPT_LANGUAGE)
        .and_then(|value| value.to_str().ok())
        .map(ToString::to_string)
}

fn yaml_response(id: String, lang: String, yaml: String) -> Result<Response, TemplateRouteError> {
    let mut headers = HeaderMap::new();
    headers.insert(
        CONTENT_TYPE,
        HeaderValue::from_static("text/yaml; charset=utf-8"),
    );
    insert_header_value(&mut headers, CONTENT_LANGUAGE, &lang)?;
    insert_header_value(&mut headers, "x-template-id", &id)?;
    insert_header_value(&mut headers, "x-template-lang", &lang)?;
    Ok((headers, yaml).into_response())
}

fn insert_header_value<K>(
    headers: &mut HeaderMap,
    name: K,
    value: &str,
) -> Result<(), TemplateRouteError>
where
    K: axum::http::header::IntoHeaderName,
{
    let value = HeaderValue::from_str(value).map_err(|error| {
        TemplateRouteError(CollaborationTemplateError::Io(format!(
            "invalid response header value: {error}"
        )))
    })?;
    headers.insert(name, value);
    Ok(())
}

#[derive(Debug)]
pub struct TemplateRouteError(CollaborationTemplateError);

impl From<CollaborationTemplateError> for TemplateRouteError {
    fn from(error: CollaborationTemplateError) -> Self {
        Self(error)
    }
}

impl IntoResponse for TemplateRouteError {
    fn into_response(self) -> Response {
        let (status, code) = match &self.0 {
            CollaborationTemplateError::NotFound(_) => {
                (StatusCode::NOT_FOUND, "TEMPLATE_NOT_FOUND")
            }
            CollaborationTemplateError::LanguageNotAvailable { .. } => {
                (StatusCode::NOT_FOUND, "LANGUAGE_NOT_AVAILABLE")
            }
            CollaborationTemplateError::InvalidFormat(_) => {
                (StatusCode::BAD_REQUEST, "INVALID_TEMPLATE_FORMAT")
            }
            CollaborationTemplateError::InvalidTags(_) => {
                (StatusCode::BAD_REQUEST, "INVALID_TEMPLATE_TAGS")
            }
            CollaborationTemplateError::InvalidLanguage(_) => {
                (StatusCode::BAD_REQUEST, "INVALID_TEMPLATE_LANGUAGE")
            }
            CollaborationTemplateError::RegistryInvalid(_) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                "TEMPLATE_REGISTRY_INVALID",
            ),
            CollaborationTemplateError::YamlInvalid(_) => {
                (StatusCode::INTERNAL_SERVER_ERROR, "TEMPLATE_YAML_INVALID")
            }
            CollaborationTemplateError::Io(_) => {
                (StatusCode::INTERNAL_SERVER_ERROR, "TEMPLATE_IO_ERROR")
            }
        };

        (
            status,
            Json(serde_json::json!({
                "error": {
                    "code": code,
                    "message": self.0.to_string(),
                }
            })),
        )
            .into_response()
    }
}
