use super::*;

#[tokio::test]
async fn health_route_is_served_by_http_adapter_router() {
    let app = build_router(HttpAppState::new(Services::noop()));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
}
