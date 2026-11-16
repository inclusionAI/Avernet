//! Fallible-branch contracts for the SQL row-mapping helpers (`mapping.rs`).
//!
//! The store promises callers either a typed enum/struct or an explicit
//! internal data error; these tests pin the persisted spellings, the
//! conversion fallbacks and the malformed-value rejections that the happy-path
//! sqlite fixtures never hit.

use super::*;
use std::collections::BTreeMap;

fn row(pairs: Vec<(&str, DbValue)>) -> DbRow {
    DbRow::new(
        pairs
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect::<BTreeMap<_, _>>(),
    )
}

fn expect_internal(result: ServiceResult<impl std::fmt::Debug>, needle: &str) {
    match result {
        Err(ServiceError::InternalError(message)) => {
            assert!(
                message.contains(needle),
                "internal error must mention {needle:?}, got: {message}"
            );
        }
        other => panic!("expected internal data error mentioning {needle:?}, got {other:?}"),
    }
}

#[test]
fn request_kind_parses_all_persisted_spellings_and_rejects_unknown() {
    assert!(matches!(parse_request_kind("connect"), Ok(RequestKind::Connect)));
    assert!(matches!(
        parse_request_kind("permission_profile"),
        Ok(RequestKind::PermissionProfile)
    ));
    assert!(matches!(parse_request_kind("rules"), Ok(RequestKind::Rules)));
    assert!(matches!(parse_request_kind("revoke"), Ok(RequestKind::Revoke)));
    expect_internal(parse_request_kind("bump"), "unknown request_kind: bump");
}

#[test]
fn request_status_parses_cancelled_and_rejects_unknown() {
    assert!(matches!(parse_request_status("pending"), Ok(RequestStatus::Pending)));
    assert!(matches!(parse_request_status("approved"), Ok(RequestStatus::Approved)));
    assert!(matches!(parse_request_status("rejected"), Ok(RequestStatus::Rejected)));
    assert!(matches!(parse_request_status("cancelled"), Ok(RequestStatus::Cancelled)));
    expect_internal(parse_request_status("expired"), "unknown request status: expired");
}

#[test]
fn profile_row_rejects_unparseable_rules_template() {
    let row = row(vec![
        ("id", DbValue::from(7_u64)),
        ("bot_id", DbValue::from("x:bot")),
        ("env", DbValue::from("dev")),
        ("name", DbValue::from("default")),
        ("description", DbValue::Null),
        ("rules_template", DbValue::from("{not json")),
        ("revision", DbValue::from(1_i64)),
        ("digest", DbValue::from("ab")),
        ("is_default", DbValue::from(1_i64)),
        ("status", DbValue::from("active")),
        ("created_by", DbValue::from("human_1")),
        ("updated_by", DbValue::Null),
    ]);
    expect_internal(
        row_to_permission_profile(&row),
        "permission_profiles json parse",
    );
}

#[test]
fn grant_row_rejects_unknown_originator_policy_type() {
    let row = row(vec![
        ("id", DbValue::from(3_u64)),
        ("env", DbValue::from("dev")),
        ("from_id", DbValue::from("human_1")),
        ("to_id", DbValue::from("x:bot")),
        ("grant_kind", DbValue::from("permission_profile")),
        ("grant_ref_id", DbValue::from(9_u64)),
        ("rules", DbValue::Null),
        ("status", DbValue::from("approved")),
        ("originator_policy_type", DbValue::from("group")),
        ("originator_policy_data", DbValue::Null),
    ]);
    expect_internal(
        row_to_edge_grant(&row),
        "unknown originator_policy_type: group",
    );
}

#[test]
fn string_columns_report_missing_and_wrong_types_explicitly() {
    expect_internal(
        required_string(&row(vec![]), "from_id"),
        "missing edge_grants column from_id",
    );
    // MySQL TEXT arrives as bytes through some drivers: bytes decode as UTF-8.
    let bytes = row(vec![("name", DbValue::from(b"visor".to_vec()))]);
    assert_eq!(
        optional_string(&bytes, "name").expect("bytes decode"),
        Some("visor".to_string())
    );
    // A typed non-string column is a data error, never a silent None.
    let typed = row(vec![("name", DbValue::from(7_i64))]);
    expect_internal(optional_string(&typed, "name"), "is not a string");
}

#[test]
fn timestamp_columns_reject_non_string_values() {
    let typed = row(vec![("decided_at", DbValue::from(42_i64))]);
    expect_internal(
        optional_timestamp_text(&typed, "decided_at"),
        "is not a timestamp string",
    );
    // A present-but-unparseable required timestamp is an internal data error.
    let broken = row(vec![("gmt_create", DbValue::from("tomorrow"))]);
    expect_internal(
        required_timestamp_epoch_ms(&broken, "gmt_create"),
        "invalid edge_permission timestamp gmt_create: tomorrow",
    );
}

#[test]
fn u64_columns_reject_negative_values() {
    let negative = row(vec![("id", DbValue::from(-1_i64))]);
    expect_internal(
        required_u64(&negative, "id"),
        "negative edge_permission column id",
    );
    // Optional u64 degrades a negative to None instead of erroring.
    assert_eq!(optional_u64(&negative, "id").expect("optional read"), None);
}

#[test]
fn json_opt_parses_empty_as_none_and_rejects_invalid_json() {
    assert_eq!(parse_json_opt(&None).expect("none"), None);
    assert_eq!(parse_json_opt(&Some(String::new())).expect("empty"), None);
    expect_internal(
        parse_json_opt(&Some("{oops".to_string())),
        "edge_grants json parse",
    );
}

#[test]
fn grant_kind_parses_owner_and_manager_and_rejects_unknown() {
    assert!(matches!(parse_grant_kind("owner"), Ok(GrantKind::Owner)));
    assert!(matches!(parse_grant_kind("manager"), Ok(GrantKind::Manager)));
    expect_internal(parse_grant_kind("auditor"), "unknown grant_kind: auditor");
}

#[test]
fn timestamp_parse_rejects_malformed_shapes() {
    // Missing day / missing seconds / out-of-range month / pre-epoch dates
    // all map to None (treated like NULL upstream), never a panic.
    assert_eq!(parse_timestamp_epoch_ms("2026-01"), None);
    assert_eq!(parse_timestamp_epoch_ms("2026-01-02 03:04"), None);
    assert_eq!(parse_timestamp_epoch_ms("2026-13-01 00:00:00"), None);
    assert_eq!(parse_timestamp_epoch_ms("1960-01-01 00:00:00"), None);
}

#[test]
fn edge_status_parses_revoked_and_rejects_unknown() {
    assert!(matches!(parse_edge_status("approved"), Ok(EdgeStatus::Approved)));
    assert!(matches!(parse_edge_status("revoked"), Ok(EdgeStatus::Revoked)));
    expect_internal(parse_edge_status("blocked"), "unknown edge status: blocked");
}

#[test]
fn originator_policy_type_parses_all_spellings_and_stringifies() {
    assert!(matches!(
        parse_originator_policy_type("any"),
        Ok(OriginatorPolicyType::Any)
    ));
    assert!(matches!(
        parse_originator_policy_type("same_as_from"),
        Ok(OriginatorPolicyType::SameAsFrom)
    ));
    assert!(matches!(
        parse_originator_policy_type("specific"),
        Ok(OriginatorPolicyType::Specific)
    ));
    assert!(matches!(
        parse_originator_policy_type("owner"),
        Ok(OriginatorPolicyType::Owner)
    ));
    expect_internal(
        parse_originator_policy_type("everyone"),
        "unknown originator_policy_type: everyone",
    );
    assert_eq!(
        originator_policy_type_str(OriginatorPolicyType::Specific),
        "specific"
    );
    assert_eq!(originator_policy_type_str(OriginatorPolicyType::Owner), "owner");
}

#[test]
fn profile_status_parses_deleted_and_rejects_unknown() {
    assert!(matches!(parse_profile_status("active"), Ok(ProfileStatus::Active)));
    assert!(matches!(parse_profile_status("deleted"), Ok(ProfileStatus::Deleted)));
    expect_internal(parse_profile_status("archived"), "unknown profile status: archived");
}
