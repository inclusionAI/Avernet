mod helpers;

use bcs::BcsServer;
use serial_test::serial;

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
#[serial]
async fn metrics_cardinality_excludes_raw_ids_and_bcn_catalog() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = true;
    let server = BcsServer::new(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");

    let raw_group_id = "group_raw_sensitive_789";
    let _ = reqwest::get(format!("http://{addr}/groups/{raw_group_id}"))
        .await
        .expect("get group");

    let body = metrics_body(addr).await;

    for forbidden in [
        raw_group_id,
        "bot_uuid=",
        "group_id=",
        "run_id=",
        "staff_no=",
        "token=",
        "cookie=",
        "bcn_",
        "route=\"/groups/group_raw_sensitive_789\"",
    ] {
        assert!(
            !body.contains(forbidden),
            "metrics output should not contain high-cardinality or out-of-scope token: {forbidden}"
        );
    }

    assert!(body.contains("route=\"/groups/{id}\""));
    assert!(body.contains("service_mode=\"none\"") || !body.contains("bcs_groups_current"));
    assert!(body.contains("group_strategy=\"chat\"") || !body.contains("bcs_groups_current"));
    assert!(body.contains("error_code=\"none\"") || !body.contains("bcs_message_delivery_total"));

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
