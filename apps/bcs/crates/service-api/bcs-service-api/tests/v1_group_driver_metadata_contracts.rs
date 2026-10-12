use bcs_service_api::application::v1::{GroupDetail, GroupSummary};
use serde_json::{Value, json};

fn legacy_summary() -> Value {
    json!({
        "kind": "normal", "group_id": "group", "version": 1,
        "status": "active", "visibility": "public", "membership": "none",
        "originator_actor_id": "human_reader", "participant_count": 1,
        "driver_bot_uuid": "driver", "strategy": "chat",
        "human_mention_notify_mode": "all", "created_at": 1, "updated_at": 2
    })
}

fn legacy_detail() -> Value {
    json!({
        "kind": "normal", "group_id": "group", "version": 1,
        "status": "active", "visibility": "public",
        "originator_actor_id": "human_reader", "participants": [],
        "driver_bot_uuid": "driver", "collaboration": {
            "strategy": "chat", "delivery_policy": {"bot_final_delivery": "send_to_driver"}
        },
        "human_mention_notify_mode": "all", "created_at": 1, "updated_at": 2
    })
}

#[test]
fn old_summary_deserializes_and_emits_explicit_null_driver_name() {
    let summary: GroupSummary = serde_json::from_value(legacy_summary()).expect("old summary");
    let value = serde_json::to_value(summary).expect("serialize summary");
    assert_eq!(value.get("driver_bot_name"), Some(&Value::Null));
}

#[test]
fn old_detail_deserializes_and_emits_explicit_null_owner_fields() {
    let detail: GroupDetail = serde_json::from_value(legacy_detail()).expect("old detail");
    let value = serde_json::to_value(detail).expect("serialize detail");
    assert_eq!(value.get("driver_bot_owner"), Some(&Value::Null));
    assert_eq!(value.get("driver_bot_owner_name"), Some(&Value::Null));
}

#[test]
fn summary_and_detail_round_trip_populated_metadata() {
    let mut summary = legacy_summary();
    summary["driver_bot_name"] = json!("Driver Bot");
    let typed: GroupSummary = serde_json::from_value(summary.clone()).expect("summary");
    assert_eq!(serde_json::to_value(typed).expect("summary value"), summary);

    let mut detail = legacy_detail();
    detail["driver_bot_owner"] = json!("human_owner");
    detail["driver_bot_owner_name"] = json!("Owner Name");
    let typed: GroupDetail = serde_json::from_value(detail.clone()).expect("detail");
    assert_eq!(serde_json::to_value(typed).expect("detail value"), detail);
}
