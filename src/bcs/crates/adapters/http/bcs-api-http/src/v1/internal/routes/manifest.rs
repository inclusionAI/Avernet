use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use bcs_config_api::{ManifestBundleConfig, ManifestBundleSourceType};
use serde::Serialize;

use crate::v1::common::{ApiState, Envelope, RequestId};

const ASSETS_PREFIX: &str = "/api/v1/collaboration/assets";

pub fn public_router() -> Router<ApiState> {
    Router::new()
        .route("/manifest", get(get_manifest))
        .route(
            "/assets/{bundle_name}/{file_name}",
            get(manifest_asset),
        )
}

#[derive(Debug, Serialize)]
struct ManifestResponse {
    pub schema_version: u32,
    pub env: String,
    pub bundles: Vec<ManifestBundleResponse>,
}

#[derive(Debug, Serialize)]
struct ManifestBundleResponse {
    pub name: String,
    pub url: String,
}

async fn get_manifest(State(state): State<ApiState>, headers: HeaderMap) -> Response {
    let request_id = RequestId::from_headers(&headers);
    let bundles = state
        .manifest
        .bundles
        .iter()
        .filter_map(|bundle| {
            Some(ManifestBundleResponse {
                name: bundle.name.clone(),
                url: manifest_bundle_url(bundle)?,
            })
        })
        .collect();
    let data = ManifestResponse {
        schema_version: state.manifest.schema_version,
        env: state.manifest_env.clone(),
        bundles,
    };
    (
        StatusCode::OK,
        axum::Json(Envelope::success(20_000, "OK", data, request_id.0)),
    )
        .into_response()
}

async fn manifest_asset(
    State(state): State<ApiState>,
    Path((bundle_name, file_name)): Path<(String, String)>,
) -> Response {
    let Some(file_path) = state.manifest.bundles.iter().find_map(|bundle| {
        if bundle.name != bundle_name {
            return None;
        }
        let asset_file_name = local_asset_file_name(bundle)?;
        if asset_file_name == file_name {
            return bundle.file.clone();
        }
        None
    }) else {
        return (StatusCode::NOT_FOUND, "asset not found").into_response();
    };

    let Ok(bytes) = tokio::fs::read(&file_path).await else {
        return (StatusCode::NOT_FOUND, "asset not found").into_response();
    };

    let mut headers = HeaderMap::new();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static(content_type_for(&file_path)),
    );
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    (headers, bytes).into_response()
}

fn manifest_bundle_url(bundle: &ManifestBundleConfig) -> Option<String> {
    if is_file_bundle(bundle) {
        let file_name = local_asset_file_name(bundle)?;
        return Some(format!(
            "{ASSETS_PREFIX}/{}/{}",
            urlencoding::encode(&bundle.name),
            urlencoding::encode(&file_name)
        ));
    }
    bundle.url.clone()
}

fn is_file_bundle(bundle: &ManifestBundleConfig) -> bool {
    match bundle.source_type {
        Some(ManifestBundleSourceType::File) => true,
        Some(ManifestBundleSourceType::Url) => false,
        None => bundle.file.as_deref().is_some() && bundle.url.as_deref().is_none(),
    }
}

fn local_asset_file_name(bundle: &ManifestBundleConfig) -> Option<String> {
    let file = bundle.file.as_deref()?;
    let file_name = std::path::Path::new(file).file_name()?.to_str()?;
    Some(file_name.to_string())
}

fn content_type_for(path: &str) -> &'static str {
    match std::path::Path::new(path).extension().and_then(|ext| ext.to_str()) {
        Some("css") => "text/css; charset=utf-8",
        Some("html") => "text/html; charset=utf-8",
        Some("js") | Some("mjs") => "application/javascript; charset=utf-8",
        Some("json") => "application/json; charset=utf-8",
        Some("map") => "application/json; charset=utf-8",
        Some("svg") => "image/svg+xml",
        _ => "application/octet-stream",
    }
}