use axum::{
    extract::{Path, State},
    http::{
        HeaderMap, HeaderValue, StatusCode,
        header::{CACHE_CONTROL, CONTENT_TYPE},
    },
    response::{IntoResponse, Response},
};
use bcs_config_api::{ManifestBundleConfig, ManifestBundleSourceType};

use crate::state::HttpAppState;

pub fn manifest_bundle_url(bundle: &ManifestBundleConfig) -> Option<String> {
    if is_file_bundle(bundle) {
        return local_asset_url(bundle);
    }

    bundle.url.clone()
}

pub async fn manifest_asset(
    State(state): State<HttpAppState>,
    Path((bundle_name, file_name)): Path<(String, String)>,
) -> Response {
    let Some(file_path) = state.manifest.bundles.iter().find_map(|bundle| {
        if bundle.name != bundle_name {
            return None;
        }

        let asset_file_name = local_asset_file_name(bundle)?;
        if asset_file_name == file_name {
            return local_asset_path(bundle);
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
        CONTENT_TYPE,
        HeaderValue::from_static(content_type_for(&file_path)),
    );
    headers.insert(
        CACHE_CONTROL,
        HeaderValue::from_static("no-cache"),
    );

    (headers, bytes).into_response()
}

fn is_file_bundle(bundle: &ManifestBundleConfig) -> bool {
    match bundle.source_type {
        Some(ManifestBundleSourceType::File) => true,
        Some(ManifestBundleSourceType::Url) => false,
        None => bundle.file.as_deref().is_some() && bundle.url.as_deref().is_none(),
    }
}

fn local_asset_url(bundle: &ManifestBundleConfig) -> Option<String> {
    let file_name = local_asset_file_name(bundle)?;
    Some(format!(
        "/assets/{}/{}",
        urlencoding::encode(&bundle.name),
        urlencoding::encode(&file_name)
    ))
}

fn local_asset_path(bundle: &ManifestBundleConfig) -> Option<String> {
    bundle.file.clone()
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
