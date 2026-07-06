use axum::{
    body::{Body, to_bytes},
    http::{
        header::{CACHE_CONTROL, CONTENT_TYPE},
        Request, StatusCode,
    },
};
use bcs_config_api::{
    ManifestBundleConfig, ManifestBundleSourceType, ManifestConfig,
};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_services_container::Services;
use serde_json::Value;
use tower::ServiceExt;

#[tokio::test]
async fn manifest_route_returns_configured_bundles_and_runtime_env() {
    let manifest = ManifestConfig {
        schema_version: 1,
        bundles: vec![ManifestBundleConfig {
            name: "bcsPanel".to_string(),
            source_type: Some(ManifestBundleSourceType::Url),
            url: Some(
                "https://cdn.example.com/bcs-panel/1.0.0/index.js".to_string(),
            ),
            file: None,
        }],
    };
    let app = build_router(
        HttpAppState::new(Services::noop()).with_manifest_config("pre".to_string(), manifest),
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/manifest")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);

    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body: Value = serde_json::from_slice(&body).unwrap();

    assert_eq!(body["schema_version"], 1);
    assert_eq!(body["env"], "pre");
    assert_eq!(body["bundles"][0]["name"], "bcsPanel");
    assert_eq!(
        body["bundles"][0]["url"],
        "https://cdn.example.com/bcs-panel/1.0.0/index.js"
    );
}

#[tokio::test]
async fn manifest_file_bundle_returns_asset_url_and_serves_file_at_runtime() {
    let temp_dir = tempfile::tempdir().unwrap();
    let bundle_path = temp_dir.path().join("index.umd.js");
    std::fs::write(&bundle_path, "export const ok = true;").unwrap();

    let manifest = ManifestConfig {
        schema_version: 1,
        bundles: vec![ManifestBundleConfig {
            name: "bcsPanel".to_string(),
            source_type: Some(ManifestBundleSourceType::File),
            url: None,
            file: Some(bundle_path.display().to_string()),
        }],
    };
    let app = build_router(
        HttpAppState::new(Services::noop()).with_manifest_config("local".to_string(), manifest),
    );

    let manifest_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/manifest")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let manifest_body = to_bytes(manifest_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let manifest_body: Value = serde_json::from_slice(&manifest_body).unwrap();

    assert_eq!(manifest_body["bundles"][0]["name"], "bcsPanel");
    assert_eq!(
        manifest_body["bundles"][0]["url"],
        "/assets/bcsPanel/index.umd.js"
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/assets/bcsPanel/index.umd.js")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers().get(CONTENT_TYPE).unwrap(),
        "application/javascript; charset=utf-8"
    );
    assert_eq!(response.headers().get(CACHE_CONTROL).unwrap(), "no-cache");

    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    assert_eq!(&body[..], b"export const ok = true;");
}
