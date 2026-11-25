//! Real bootstrap/HTTP regression for the metrics-on legacy Group create path.
#![cfg(feature = "prometheus-metrics")]

mod helpers;

use std::sync::Arc;

use bcs::BcsServer;
use bcs_service_api::types::catalog_labels::{CatalogLabelPolicy, CatalogLabelRule};
use reqwest::StatusCode;
use serde_json::{Value, json};

#[tokio::test]
async fn legacy_group_catalog_labels_match_with_metrics_enabled_and_disabled() {
    let _ = tracing_subscriber::fmt::try_init();
    for metrics_enabled in [false, true] {
        let bots_dir = helpers::create_temp_bots_dir();
        let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
        config.metrics.enabled = metrics_enabled;
        config.eventing.enabled = true;
        config.eventing.webhook.allow_http_loopback = true;
        config.eventing.webhook.allow_non_standard_ports = true;
        config.catalog_label_policy = Arc::new(
            CatalogLabelPolicy::new(
                [(
                    "lifecycle".into(),
                    CatalogLabelRule {
                        allowed_values: vec!["draft".into()],
                        allow_multiple: false,
                    },
                )]
                .into(),
            )
            .expect("valid label dictionary"),
        );
        let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
            .run_on_random_port_with_state()
            .await
            .expect("start server");
        let mut bot = helpers::MockBot::connect(addr).await;
        bot.register("Metrics label driver", &["labels"], addr)
            .await;
        let http = reqwest::Client::new();
        let base = format!("http://{addr}");

        for inline_subscriptions in [false, true] {
            let mut request = json!({
                "driver_bot": bot.bot_id,
                "originator": bot.bot_id,
                "participants": [{"bot_uuid": bot.bot_id, "role": "manager"}],
                "group_strategy": "manager_worker",
                "create_initial_session": inline_subscriptions,
                "catalog_labels": {"lifecycle": ["draft", "draft"]}
            });
            if inline_subscriptions {
                request["event_subscriptions"] = json!([{
                    "name": "label-regression",
                    "event_filters": ["group.*"],
                    "sink": {"type": "webhook", "url": format!("{base}/test-events")}
                }]);
            }
            let response = http
                .post(format!("{base}/groups"))
                .bearer_auth(&bot.token)
                .json(&request)
                .send()
                .await
                .expect("create request");
            let status = response.status();
            let created: Value = response.json().await.expect("create response");
            assert_eq!(
                status,
                StatusCode::OK,
                "metrics={metrics_enabled}, inline_subscriptions={inline_subscriptions}: {created}"
            );
            assert_eq!(created["catalog_labels"], json!({"lifecycle": ["draft"]}));
            let id = created["id"].as_str().expect("created group id");
            let saved: Value = http
                .get(format!("{base}/groups/{id}"))
                .bearer_auth(&bot.token)
                .send()
                .await
                .expect("get request")
                .error_for_status()
                .expect("get status")
                .json()
                .await
                .expect("get response");
            assert_eq!(saved["catalog_labels"], json!({"lifecycle": ["draft"]}));
            if inline_subscriptions {
                assert_eq!(created["event_subscriptions"].as_array().unwrap().len(), 1);
            }

            // Invalid labels must be rejected before either creation/provisioning branch writes.
            let before: Value = http
                .get(format!("{base}/groups"))
                .send()
                .await
                .expect("list request")
                .error_for_status()
                .expect("list status")
                .json()
                .await
                .expect("list response");
            for (labels, field, reason) in [
                (
                    json!({"unknown": []}),
                    "catalog_labels.unknown",
                    "UNKNOWN_KEY",
                ),
                (
                    json!({"lifecycle": ["invalid"]}),
                    "catalog_labels.lifecycle[0]",
                    "UNKNOWN_VALUE",
                ),
            ] {
                request["catalog_labels"] = labels;
                let response = http
                    .post(format!("{base}/groups"))
                    .bearer_auth(&bot.token)
                    .json(&request)
                    .send()
                    .await
                    .expect("invalid create request");
                let status = response.status();
                let error: Value = response.json().await.expect("invalid create response");
                assert_eq!(status, StatusCode::BAD_REQUEST, "{error}");
                assert_eq!(error["params"]["code"], "invalid_request");
                assert_eq!(error["params"]["field"], field);
                assert_eq!(error["params"]["reason"], reason);
            }
            let after: Value = http
                .get(format!("{base}/groups"))
                .send()
                .await
                .expect("list request")
                .error_for_status()
                .expect("list status")
                .json()
                .await
                .expect("list response");
            assert_eq!(
                after["total"], before["total"],
                "invalid labels must not create groups"
            );
        }

        bot.disconnect().await;
        state
            .lifecycle
            .lock()
            .await
            .shutdown_all()
            .await
            .expect("shutdown services");
        handle.abort();
        let _ = handle.await;
    }
}
