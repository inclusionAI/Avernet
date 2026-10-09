mod helpers;

use bcs::BcsServer;
use serial_test::serial;

#[test]
fn metrics_http_status_mapping_treats_5xx_as_error() {
    assert_eq!(
        bcs::metrics::http_status_class(reqwest::StatusCode::INTERNAL_SERVER_ERROR),
        "5xx"
    );
    assert_eq!(
        bcs::metrics::http_result_label(reqwest::StatusCode::INTERNAL_SERVER_ERROR),
        "error"
    );
    assert_eq!(
        bcs::metrics::http_result_label(reqwest::StatusCode::NOT_FOUND),
        "success"
    );
}

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
#[serial]
async fn metrics_http_middleware_uses_safe_route_labels() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = true;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");

    let raw_bot_id = "raw-sensitive-bot-id-123";
    let bot_response = reqwest::get(format!("http://{addr}/bots/{raw_bot_id}"))
        .await
        .expect("get bot");
    assert!(bot_response.status().is_client_error());

    let raw_unmatched = "raw-sensitive-route-456";
    let unmatched_response = reqwest::get(format!("http://{addr}/missing/{raw_unmatched}"))
        .await
        .expect("get unmatched route");
    assert!(unmatched_response.status().is_client_error());

    let body = metrics_body(addr).await;
    let env = bcs::resolve_env();

    assert!(body.contains(&format!(
        "bcs_http_requests_total{{env=\"{env}\",route=\"/bots/{{id}}\",method=\"GET\",status_class=\"4xx\",result=\"success\"}}"
    )));
    assert!(body.contains(&format!(
        "bcs_http_request_duration_seconds_bucket{{env=\"{env}\",route=\"/bots/{{id}}\",method=\"GET\",status_class=\"4xx\""
    )));
    assert!(body.contains(&format!(
        "bcs_http_requests_total{{env=\"{env}\",route=\"unmatched\",method=\"GET\",status_class=\"4xx\",result=\"success\"}}"
    )));
    assert!(!body.contains(raw_bot_id));
    assert!(!body.contains(raw_unmatched));
    assert!(!body.contains("route=\"/missing/"));

    handle.abort();
}

#[cfg(feature = "prometheus-metrics")]
async fn metrics_body(addr: std::net::SocketAddr) -> String {
    reqwest::get(format!("http://{addr}/metrics"))
        .await
        .expect("get metrics")
        .text()
        .await
        .expect("metrics body")
}
