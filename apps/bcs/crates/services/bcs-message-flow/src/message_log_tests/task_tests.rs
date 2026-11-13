use super::*;
use crate::message_log_tests::capture;

#[test]
fn coverage_legacy_task_logs_preserve_failed_status_and_missing_manager() {
    use crate::message_log_tests::legacy;
    legacy::isolated(|| {
        log_task_dispatch_created("group", "", "task", "manager", "worker", "你好");
        log_task_complete("group", None, "task", "worker", "completed");
        log_task_complete("group", Some(""), "task", "worker", "failed");
        log_manager_worker_deliver_result("group", None, "task", "worker", Some("manager"), DeliveryType::Send, true, None, None);
        log_manager_worker_deliver_result("group", Some(""), "task", "worker", None, DeliveryType::Inject, false, Some("offline"), Some("send"));
        let rows = legacy::rows(MSG_LOG_TARGET);
        assert_eq!(rows.len(), 5);
        for row in &rows {
            row.assert_field("log_class", "business_key");
            row.assert_field("session_id", "group");
            row.assert_field("task_id", "task");
            row.assert_field("run_id", "task");
        }
        rows[0].assert_field("content", "你好");
        rows[1].assert_field("status", "completed");
        rows[2].assert_field("status", "failed");
        rows[3].assert_field("from_bot_id", "manager");
        rows[4].assert_field("from_bot_id", "");
        rows[4].assert_field("error", "offline");
        assert_eq!(rows[4].level, tracing::log::Level::Warn);
    });
}

#[test]
fn task_logs_correlate_dispatch_completion_and_both_delivery_outcomes() {
    let rows = capture(|| {
        log_task_dispatch_created("group", "session", "task", "manager", "worker", "你好");
        log_task_complete("group", Some("session"), "task", "worker", "completed");
        log_task_complete("group", Some("session"), "task", "worker", "failed");
        log_manager_worker_deliver_result("group", Some("session"), "task", "worker", Some("manager"), DeliveryType::Send, true, None, None);
        log_manager_worker_deliver_result("group", Some("session"), "task", "worker", None, DeliveryType::Inject, false, Some("offline"), Some("send"));
    });
    assert_eq!(rows.len(), 5);
    for row in &rows {
        assert_eq!(row["fields"]["log_class"], "business_key");
        assert_eq!(row["fields"]["session_id"], "session");
        assert_eq!(row["fields"]["task_id"], "task");
        assert_eq!(row["fields"]["run_id"], "task");
    }
    assert_eq!(rows[0]["fields"]["manager_bot_id"], "manager");
    assert_eq!(rows[0]["fields"]["worker_bot_id"], "worker");
    assert_eq!(rows[1]["fields"]["status"], "completed");
    assert_eq!(rows[2]["fields"]["status"], "failed");
    assert_eq!(rows[3]["fields"]["from_bot_id"], "manager");
    assert_eq!(rows[3]["fields"]["error"], "");
    assert_eq!(rows[4]["fields"]["from_bot_id"], "");
    assert_eq!(rows[4]["fields"]["error"], "offline");
    assert_eq!(rows[4]["level"], "WARN");
}
