//! Read-only task projection for authenticated delivery queries and events.
//! Allowlist the admitted intent; no transport metadata or extra repository reads.
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};

#[derive(Debug, Clone, serde::Serialize)]
pub struct TaskDeliveryView {
    pub task_ref: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub intent_id: Option<String>,
    pub summary: String,
    pub manager_id: String,
    pub worker_id: String,
    pub worker_name: String,
    pub queued_at: i64,
    pub started_at: Option<i64>,
    pub last_activity_at: i64,
    pub finished_at: Option<i64>,
    pub timed_out: bool,
}

impl TaskDeliveryView {
    pub fn from_delivery(row: &PersistedMessageDelivery) -> Option<Self> {
        if row.flow_kind != DeliveryFlowKind::Task { return None; }
        let task = row.semantic_projection_json.get("task")?;
        if task.get("leg")?.as_str()? != "dispatch" { return None; }
        let task_id = task.get("task_id")?.as_str()?;
        let worker_id = task.get("worker")?.as_str()?;
        if task_id.is_empty() || worker_id != row.target_bot_id { return None; }
        let intent = task.get("assignment_intent_id").and_then(|v| v.as_str())
            .and_then(|id| id.strip_prefix("bcs_intent_"))
            .filter(|id| id.len() == 32 && id.bytes().all(|b| b.is_ascii_hexdigit()));
        let short: String = intent.unwrap_or(task_id).chars().filter(|c| *c != '-').take(8).collect();
        Some(Self {
            task_ref: format!("T-{}", short.to_ascii_lowercase()),
            intent_id: intent.map(|id| format!("bcs_intent_{}", id.to_ascii_lowercase())),
            summary: task.get("summary").and_then(|v| v.as_str()).unwrap_or("").into(),
            manager_id: task.get("manager")?.as_str()?.into(),
            worker_id: worker_id.into(),
            worker_name: task.get("worker_name").and_then(|v| v.as_str()).unwrap_or("").into(),
            queued_at: row.created_at_ms,
            started_at: row.accepted_at_ms.or(row.submitted_at_ms),
            last_activity_at: row.updated_at_ms,
            finished_at: row.terminal_at_ms,
            timed_out: row.transport_context_json.as_ref().and_then(|v| v.get("task_timeout"))
                .and_then(|v| v.as_bool()) == Some(true),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn row() -> PersistedMessageDelivery {
        serde_json::from_value(serde_json::json!({
            "delivery_id":"d1", "env":"local", "source_message_id":"m1", "target_bot_id":"worker",
            "session_id":"s1", "group_id":"g1", "source_session_seq":1, "flow_kind":"task",
            "kind":"send", "status":"running", "state_version":2, "may_have_been_sent":true, "wait_reason":null,
            "available_at_ms":1000, "created_at_ms":1000, "updated_at_ms":3000, "attempt_no":1,
            "accepted_at_ms":2000, "terminal_at_ms":null,
            "semantic_projection_json":{"task":{"leg":"dispatch", "task_id":"12345678-abcd",
                "assignment_intent_id":"bcs_intent_3fa9c1d2000000000000000000000000",
                "manager":"manager", "worker":"worker", "worker_name":"Worker", "summary":"Inspect logs"}},
            "transport_context_json":{"private_metadata":"must-not-leak", "task_timeout":true}
        })).unwrap()
    }
    #[test]
    fn task_projection_is_safe_and_uses_canonical_reference_and_server_times() {
        let view = super::super::message_delivery::DeliveryStatusView::from(&row());
        let value = serde_json::to_value(view).unwrap();
        assert_eq!(value["task"]["task_ref"], "T-3fa9c1d2");
        assert_eq!(value["task"]["intent_id"], "bcs_intent_3fa9c1d2000000000000000000000000");
        assert_eq!(value["task"]["queued_at"], 1000);
        assert_eq!(value["task"]["started_at"], 2000);
        assert_eq!(value["task"]["last_activity_at"], 3000);
        assert_eq!(value["task"]["timed_out"], true);
        assert!(!value.to_string().contains("must-not-leak"));
    }
    #[test]
    fn colliding_display_refs_preserve_complete_assignment_identity() {
        let first = TaskDeliveryView::from_delivery(&row()).unwrap();
        let mut other = row();
        other.semantic_projection_json["task"]["assignment_intent_id"] =
            serde_json::json!("bcs_intent_3fa9c1d2FFFFFFFFFFFFFFFFFFFFFFFF");
        let second = TaskDeliveryView::from_delivery(&other).unwrap();
        assert_eq!(first.task_ref, second.task_ref);
        assert_ne!(first.intent_id, second.intent_id);
        assert_eq!(second.intent_id.as_deref(), Some("bcs_intent_3fa9c1d2ffffffffffffffffffffffff"));
        other.semantic_projection_json["task"]["assignment_intent_id"] = serde_json::json!("invalid");
        assert!(TaskDeliveryView::from_delivery(&other).unwrap().intent_id.is_none());
    }
    #[test]
    fn ordinary_and_result_deliveries_do_not_create_tasks() {
        let mut delivery = row();
        delivery.flow_kind = DeliveryFlowKind::Group;
        let value = serde_json::to_value(super::super::message_delivery::DeliveryStatusView::from(&delivery)).unwrap();
        assert!(value.get("task").is_none());
        delivery.flow_kind = DeliveryFlowKind::Task;
        delivery.semantic_projection_json["task"]["leg"] = serde_json::json!("result");
        assert!(TaskDeliveryView::from_delivery(&delivery).is_none());
        delivery.semantic_projection_json["task"]["leg"] = serde_json::json!("dispatch");
        delivery.target_bot_id = "other".into();
        assert!(TaskDeliveryView::from_delivery(&delivery).is_none());
    }
    #[test]
    fn legacy_task_uuid_fallback_matches_bot_task_reference() {
        let mut delivery = row();
        delivery.semantic_projection_json["task"]["assignment_intent_id"] = serde_json::Value::Null;
        let view = TaskDeliveryView::from_delivery(&delivery).unwrap();
        assert_eq!(view.task_ref, "T-12345678");
        assert!(serde_json::to_value(view).unwrap().get("intent_id").is_none());
    }
}
