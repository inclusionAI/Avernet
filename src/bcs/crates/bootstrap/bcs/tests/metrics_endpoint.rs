mod helpers;

use bcs::BcsServer;
#[cfg(feature = "prometheus-metrics")]
use futures_util::{SinkExt, StreamExt};
#[cfg(feature = "prometheus-metrics")]
use serial_test::serial;
#[cfg(feature = "prometheus-metrics")]
use tokio_tungstenite::tungstenite::Message;

#[tokio::test]
async fn metrics_disabled_endpoint_returns_404() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");

    let response = reqwest::get(format!("http://{addr}/metrics"))
        .await
        .expect("get metrics");

    assert_eq!(response.status(), reqwest::StatusCode::NOT_FOUND);
    handle.abort();
}

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
#[serial]
async fn metrics_enabled_endpoint_renders_prometheus_text() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = true;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");

    let response = reqwest::get(format!("http://{addr}/metrics"))
        .await
        .expect("get metrics");

    assert_eq!(response.status(), reqwest::StatusCode::OK);
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    assert!(content_type.starts_with("text/plain; version=0.0.4"));
    let body = response.text().await.expect("metrics body");
    assert!(body.contains("bcs_build_info"));
    assert!(body.contains("bcs_ws_connections_current"));
    assert!(body.contains("peer=\"bot\""));
    assert!(body.contains("peer=\"frontend\""));
    assert!(body.contains("env=\""));
    assert!(!body.contains("service=\""));
    assert!(!body.contains("job=\""));
    assert!(!body.contains("instance=\""));
    assert!(!body.contains("route=\"/metrics\""));

    handle.abort();
}

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
#[serial]
async fn metrics_ws_bot_lifecycle_is_recorded() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = true;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");

    let (mut ws, _response) =
        tokio_tungstenite::connect_async(format!("ws://{addr}/ws/bot"))
            .await
            .expect("connect bot ws");

    let body = metrics_body(addr).await;
    assert!(body.contains(&ws_metric(
        "bcs_ws_connections_current",
        "peer=\"bot\",endpoint=\"/ws/bot\"",
        Some("1"),
    )));
    assert!(body.contains(&ws_metric(
        "bcs_ws_connection_events_total",
        "peer=\"bot\",endpoint=\"/ws/bot\",event=\"accepted\",result=\"success\"",
        None,
    )));

    ws.close(None).await.expect("close bot ws");

    let body = wait_for_metric(
        addr,
        &ws_metric(
            "bcs_ws_connections_current",
            "peer=\"bot\",endpoint=\"/ws/bot\"",
            Some("0"),
        ),
    )
    .await;
    assert!(body.contains(&ws_metric(
        "bcs_ws_connections_current",
        "peer=\"bot\",endpoint=\"/ws/bot\"",
        Some("0"),
    )));
    assert!(body.contains(&ws_metric(
        "bcs_ws_connection_events_total",
        "peer=\"bot\",endpoint=\"/ws/bot\",event=\"closed\",result=\"success\"",
        None,
    )));
    assert!(body.contains("bcs_ws_connection_duration_seconds_bucket"));

    handle.abort();
}

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
#[serial]
async fn metrics_ws_bot_registration_success_and_rejection_are_recorded() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = true;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");

    let (mut ok_ws, _response) =
        tokio_tungstenite::connect_async(format!("ws://{addr}/ws/bot"))
            .await
            .expect("connect bot ws");
    ok_ws
        .send(Message::Text(
            serde_json::json!({
                "type": "req",
                "id": "connect-ok",
                "method": "bot.connect",
                "params": {}
            })
            .to_string()
            .into(),
        ))
        .await
        .expect("send bot.connect");
    let _ = ok_ws.next().await.expect("connect response");

    let body = wait_for_metric(
        addr,
        &ws_metric(
            "bcs_ws_connection_events_total",
            "peer=\"bot\",endpoint=\"/ws/bot\",event=\"registered\",result=\"success\"",
            None,
        ),
    )
    .await;
    assert!(body.contains(&ws_metric(
        "bcs_ws_connection_events_total",
        "peer=\"bot\",endpoint=\"/ws/bot\",event=\"registered\",result=\"success\"",
        None,
    )));
    ok_ws.close(None).await.expect("close success ws");

    let (mut err_ws, _response) =
        tokio_tungstenite::connect_async(format!("ws://{addr}/ws/bot"))
            .await
            .expect("connect bot ws");
    err_ws
        .send(Message::Text(
            serde_json::json!({
                "type": "req",
                "id": "connect-error",
                "method": "bot.connect",
                "params": { "protocol_version": 999999 }
            })
            .to_string()
            .into(),
        ))
        .await
        .expect("send invalid bot.connect");
    let _ = err_ws.next().await.expect("error response");

    let body = wait_for_metric(
        addr,
        &ws_metric(
            "bcs_ws_connection_events_total",
            "peer=\"bot\",endpoint=\"/ws/bot\",event=\"register_rejected\",result=\"error\"",
            None,
        ),
    )
    .await;
    assert!(body.contains(&ws_metric(
        "bcs_ws_connection_events_total",
        "peer=\"bot\",endpoint=\"/ws/bot\",event=\"register_rejected\",result=\"error\"",
        None,
    )));

    handle.abort();
}

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
#[serial]
async fn metrics_ws_frontend_lifecycle_is_recorded() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = true;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");

    let (mut ws, _response) = tokio_tungstenite::connect_async(format!("ws://{addr}/ws"))
        .await
        .expect("connect frontend ws");

    let body = metrics_body(addr).await;
    assert!(body.contains(&ws_metric(
        "bcs_ws_connections_current",
        "peer=\"frontend\",endpoint=\"/ws\"",
        Some("1"),
    )));
    assert!(body.contains(&ws_metric(
        "bcs_ws_connection_events_total",
        "peer=\"frontend\",endpoint=\"/ws\",event=\"accepted\",result=\"success\"",
        None,
    )));

    ws.close(None).await.expect("close frontend ws");

    let body = wait_for_metric(
        addr,
        &ws_metric(
            "bcs_ws_connections_current",
            "peer=\"frontend\",endpoint=\"/ws\"",
            Some("0"),
        ),
    )
    .await;
    assert!(body.contains(&ws_metric(
        "bcs_ws_connections_current",
        "peer=\"frontend\",endpoint=\"/ws\"",
        Some("0"),
    )));
    assert!(body.contains(&ws_metric(
        "bcs_ws_connection_events_total",
        "peer=\"frontend\",endpoint=\"/ws\",event=\"closed\",result=\"success\"",
        None,
    )));
    assert!(body.contains("bcs_ws_connection_duration_seconds_bucket"));

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

/// Build an expected WS-metric segment using the same env label the server
/// resolves at runtime (`SERVER_ENV`/`REAL_SERVER_ENV`/`ALIPAY_APP_ENV`, default
/// `dev`). The tests check that the metric is recorded, regardless of which
/// environment the process runs under.
#[cfg(feature = "prometheus-metrics")]
fn ws_metric(metric: &str, rest: &str, value: Option<&str>) -> String {
    let env = bcs::resolve_env();
    match value {
        Some(value) => format!("{metric}{{env=\"{env}\",{rest}}} {value}"),
        None => format!("{metric}{{env=\"{env}\",{rest}}}"),
    }
}

#[cfg(feature = "prometheus-metrics")]
async fn wait_for_metric(addr: std::net::SocketAddr, needle: &str) -> String {
    let mut last_body = String::new();
    for _ in 0..20 {
        last_body = metrics_body(addr).await;
        if last_body.contains(needle) {
            return last_body;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    last_body
}
