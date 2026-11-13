//! The supported bridge sends legacy log records into BCS's tracing outputs.
#![allow(clippy::unwrap_used)]
use opentelemetry::trace::TracerProvider;

#[test]
fn tracing_and_log_records_reach_the_configured_output_once() {
    let dir = tempfile::tempdir().unwrap();
    let config = serde_json::from_value(serde_json::json!({
        "console": true,
        "outputs": [{
            "name": "bridge", "path": dir.path(), "file": "bridge.log",
            "format": "json", "level": "trace", "targets": ["*"]
        }, {
            "name": "text", "path": dir.path(), "file": "text.log",
            "format": "text", "level": "trace", "targets": ["*"]
        }, {
            "name": "raw", "path": dir.path(), "file": "raw.log",
            "format": "raw", "level": "trace", "targets": ["*"]
        }]
    })).unwrap();
    let provider = opentelemetry_sdk::trace::SdkTracerProvider::builder().build();
    let guard = bcs::logging::init(&config, provider.tracer("log-bridge-fixture"));
    tracing::warn!(target: "native_fixture", event_code = "fixture.native", "native event");
    log::warn!(target: "legacy_fixture", "legacy event");
    // Worker shutdown flushes both events before reading their JSON output.
    drop(guard);
    let rows: Vec<serde_json::Value> = std::fs::read_dir(dir.path()).unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.file_name().unwrap().to_string_lossy().starts_with("bridge"))
        .flat_map(|path| std::fs::read_to_string(path).unwrap().lines()
            .map(|line| serde_json::from_str(line).unwrap()).collect::<Vec<_>>())
        .collect();
    for (message, target) in [("native event", "native_fixture"), ("legacy event", "legacy_fixture")] {
        let matching: Vec<_> = rows.iter().filter(|row| row["message"] == message).collect();
        assert_eq!(matching.len(), 1, "{message}: no loss or duplicate forwarding: {rows:?}");
        assert_eq!(matching[0]["target"], target);
        assert_eq!(matching[0]["level"], "WARN");
    }
    assert!(rows.iter().any(|row| row["event_code"] == "fixture.native"));
    let contents = |prefix: &str| -> String {
        std::fs::read_dir(dir.path()).unwrap().map(|entry| entry.unwrap().path())
            .filter(|path| path.file_name().unwrap().to_string_lossy().starts_with(prefix))
            .map(|path| std::fs::read_to_string(path).unwrap()).collect()
    };
    let text = contents("text.log");
    for message in ["native event", "legacy event"] {
        assert_eq!(text.matches(message).count(), 1, "text output: {text}");
    }
    assert!(text.contains("fixture.native"));
    assert_eq!(contents("raw.log"), "native event\nlegacy event\n");
}
