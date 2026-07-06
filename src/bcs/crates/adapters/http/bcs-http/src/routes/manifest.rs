use axum::{extract::State, Json};
use serde::Serialize;

use crate::routes::assets::manifest_bundle_url;
use crate::state::HttpAppState;

#[derive(Debug, Serialize)]
pub struct ManifestResponse {
    pub schema_version: u32,
    pub env: String,
    pub bundles: Vec<ManifestBundleResponse>,
}

#[derive(Debug, Serialize)]
pub struct ManifestBundleResponse {
    pub name: String,
    pub url: String,
}

pub async fn get_manifest(State(state): State<HttpAppState>) -> Json<ManifestResponse> {
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

    Json(ManifestResponse {
        schema_version: state.manifest.schema_version,
        env: state.manifest_env,
        bundles,
    })
}
