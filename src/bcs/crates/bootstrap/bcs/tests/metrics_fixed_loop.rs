#![cfg(feature = "prometheus-metrics")]
mod helpers;

use std::sync::Arc;
use bcs::metrics::{MetricsRuntime, MetricsStateMachineLoopHook};
use bcs_service_api::{StateMachineLoopInstrumentationHook, StateMachineLoopMetric as Metric,
    StateMachineLoopOutcome as Outcome};

#[tokio::test]
#[serial_test::serial]
async fn loop_metrics_hook_renders_all_counters_with_bounded_labels() {
    let mut config = bcs::BcsConfig::default();
    config.metrics.enabled = true;
    let runtime = MetricsRuntime::install(&config).unwrap().unwrap();
    let env: Arc<str> = Arc::from("loop-hook-contract");
    let hook = MetricsStateMachineLoopHook::new(env.clone());
    bcs_test_support::contract::port::metrics::state_machine_loop_instrumentation_hook_contract_tests(&hook);
    for index in 0..100 {
        hook.record(Metric::IterationCompleted { route: bcs_domain::StateMachineLoopRouteKind::Break,
            outcome: Outcome::from_outcome(&format!("secret-outcome-{index}")) });
    }
    let text = runtime.render();
    let series: Vec<_> = text.lines().filter(|line| line.starts_with("state_machine_loop_") && line.contains(env.as_ref())).collect();
    assert_eq!(series.len(), 10);
    for (name, label, value) in [
        ("iterations_started", "", 1), ("iterations_completed", "", 107), ("exhausted", "", 1),
        ("break", "outcome=\"other\"", 101),
        ("compile_rejected", "reason=\"invalid_definition\"", 1),
        ("compile_rejected", "reason=\"resource_limit\"", 1),
    ] {
        assert!(series.iter().any(|line| line.starts_with(&format!("state_machine_loop_{name}_total{{"))
            && line.contains(label) && line.ends_with(&format!("}} {value}"))), "{text}");
    }
    for forbidden in ["secret-outcome", "custom-raw-outcome", "run_id=", "node_id=", "loop_id=", "bot_id=", "iteration=", "attempt="] {
        assert!(series.iter().all(|line| !line.contains(forbidden)), "{text}");
    }
    runtime.shutdown().await;
}

#[tokio::test]
#[serial_test::serial]
async fn configured_server_validation_exports_loop_compile_rejections() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = true;
    config.collaboration.fixed_loop_limits.max_fixed_loop_iterations = 1;
    let server = bcs::BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.unwrap();
    let client = reqwest::Client::new();
    let before = client.get(format!("http://{addr}/metrics")).send().await.unwrap().text().await.unwrap();
    let response = client.post(format!("http://{addr}/collaboration/definitions/validate"))
        .json(&serde_json::json!({"definition_yaml": include_str!("../../../services/bcs-collaboration-runtime/tests/fixtures/fixed_loop.yaml")}))
        .send().await.unwrap();
    assert_eq!(response.status(), reqwest::StatusCode::OK);
    let response: serde_json::Value = response.json().await.unwrap();
    assert_eq!(response["valid"], false, "{response}");
    let text = client.get(format!("http://{addr}/metrics")).send().await.unwrap().text().await.unwrap();
    let rejection_count = |text: &str| text.lines()
        .filter(|line| line.starts_with("state_machine_loop_compile_rejected_total{") && line.contains("reason=\"resource_limit\""))
        .map(|line| line.split_whitespace().last().unwrap().parse::<u64>().unwrap()).sum::<u64>();
    assert_eq!(rejection_count(&text), rejection_count(&before) + 1, "{text}");
    handle.abort();
}
