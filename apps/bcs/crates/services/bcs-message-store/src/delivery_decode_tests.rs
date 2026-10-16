use super::*;
use std::collections::BTreeMap;

fn columns() -> BTreeMap<String, DbValue> {
    let mut columns: BTreeMap<_, _> = COLS.iter()
        .map(|name| ((*name).to_owned(), DbValue::Null)).collect();
    for (name, value) in [
        ("delivery_id", "delivery"), ("env", "prod"),
        ("source_message_id", "message"), ("target_bot_id", "bot"),
        ("session_id", "group:session"), ("group_id", "group"),
        ("flow_kind", "group"), ("kind", "send"), ("status", "queued"),
    ] {
        columns.insert(name.into(), value.into());
    }
    for name in ["source_session_seq", "state_version", "available_at_ms",
        "created_at_ms", "updated_at_ms", "attempt_no", "may_have_been_sent"] {
        columns.insert(name.into(), DbValue::I64(0));
    }
    columns.insert("semantic_projection_json".into(), "{}".into());
    columns
}

#[test]
fn delivery_json_bytes_match_existing_string_decoding() {
    let mut strings = columns();
    for (name, json) in [
        ("semantic_projection_json", serde_json::json!({"text": "中文消息", "required_context": true})),
        ("transport_context_json", serde_json::json!({"downstream_run_id": "run"})),
        ("context_selection_json", serde_json::json!({"message_ids": ["message"]})),
    ] {
        strings.insert(name.into(), json.to_string().into());
    }
    let expected = serde_json::to_value(decode_delivery(&DbRow::new(strings.clone())).unwrap()).unwrap();
    for name in ["semantic_projection_json", "transport_context_json", "context_selection_json"] {
        let mut mixed = strings.clone();
        let bytes = strings[name].as_str().unwrap().as_bytes().to_vec();
        mixed.insert(name.into(), DbValue::Bytes(bytes));
        let actual = decode_delivery(&DbRow::new(mixed)).unwrap();
        assert_eq!(serde_json::to_value(actual).unwrap(), expected);
    }
    let mut bytes = strings;
    for name in ["semantic_projection_json", "transport_context_json", "context_selection_json"] {
        let value = bytes[name].as_str().unwrap().as_bytes().to_vec();
        bytes.insert(name.into(), DbValue::Bytes(value));
    }
    let actual = decode_delivery(&DbRow::new(bytes)).unwrap();
    assert_eq!(serde_json::to_value(actual).unwrap(), expected);
}

#[test]
fn delivery_json_nulls_preserve_existing_behavior() {
    let decoded = decode_delivery(&DbRow::new(columns())).unwrap();
    assert_eq!(decoded.semantic_projection_json, serde_json::json!({}));
    assert!(decoded.transport_context_json.is_none());
    assert!(decoded.context_selection_json.is_none());
    assert!(!decoded.state.may_have_been_sent);
}

#[test]
fn delivery_json_invalid_values_still_fail() {
    for name in ["semantic_projection_json", "transport_context_json", "context_selection_json"] {
        for value in [
            DbValue::Bytes(vec![0xff]),
            DbValue::Bytes(b"{invalid".to_vec()),
            DbValue::String("{invalid".into()),
            DbValue::I64(1),
        ] {
            let mut row = columns();
            row.insert(name.into(), value);
            assert!(matches!(decode_delivery(&DbRow::new(row)), Err(MessageDeliveryRepoError::Storage(_))));
        }
    }
}
