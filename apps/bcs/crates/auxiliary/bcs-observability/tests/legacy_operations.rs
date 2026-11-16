#[path = "../../../test-support/legacy_log_capture.rs"]
mod legacy;

use bcs_observability::{observe_key_result, observe_result, with_request_id};
use std::time::Duration;

#[test]
fn coverage_legacy_operations_preserve_errors_cancellation_and_one_stall() {
    legacy::isolated(|| {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            tokio::time::pause();
            with_request_id("legacy-request".into(), async {
                assert_eq!(observe_key_result("legacy.success", async { Ok::<_, &str>(42) }).await, Ok(42));
                assert_eq!(observe_key_result("legacy.error", async { Err::<(), _>("original error") }).await, Err("original error"));
                assert_eq!(observe_result("legacy.phase", async { Err::<(), _>("phase error") }).await, Err("phase error"));
                assert!(tokio::time::timeout(Duration::from_secs(12), observe_key_result(
                    "legacy.cancel", std::future::pending::<Result<(), ()>>()
                )).await.is_err());
            }).await;
            assert!(bcs_observability::current_request_id().is_empty());
        });
        let rows = legacy::rows("bcs_observation");
        assert_eq!(rows.len(), 5, "success, two errors, one stall and cancellation: {rows:?}");
        for row in &rows { row.assert_field("request_id", "legacy-request"); }
        let errors: Vec<_> = rows.iter().filter(|row| row.message.contains("outcome=\"error\"")).collect();
        assert_eq!(errors.len(), 2);
        errors[0].assert_field("log_class", "business_key");
        assert!(!errors[1].message.contains("log_class="), "ordinary phases stay ordinary");
        let stalls: Vec<_> = rows.iter().filter(|row| row.message.contains("bcs.operation.stalled")).collect();
        assert_eq!(stalls.len(), 1, "a pending call warns only once");
        stalls[0].assert_field("log_class", "business_key");
        let cancelled = rows.iter().find(|row| row.message.contains("outcome=\"cancelled\"")).unwrap();
        cancelled.assert_field("log_class", "business_key");
        assert_eq!(cancelled.level, tracing::log::Level::Warn);
        assert!(rows.iter().all(|row| !row.message.contains("original error") && !row.message.contains("phase error")), "errors remain redacted");
    });
}
