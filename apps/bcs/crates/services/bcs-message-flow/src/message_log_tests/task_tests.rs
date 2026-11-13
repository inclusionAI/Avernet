use super::*;
use crate::message_log_tests::capture;

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
