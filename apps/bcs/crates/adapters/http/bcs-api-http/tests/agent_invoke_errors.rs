//! Every HTTP operation maps closed application errors independently. Fault
//! wrappers are transport mapping evidence, NOT database rollback/unknown proof.
mod agent_invoke_support;
use agent_invoke_support::*;
use axum::http::StatusCode;
use serde_json::{Value, json};
use std::sync::atomic::Ordering;
const ERRORS: &[(u8, u16, &str)] = &[
    (1, 400, "agent_invoke_invalid_input"),
    (2, 401, "unauthenticated"),
    (3, 403, "agent_invoke_forbidden"),
    (4, 404, "agent_invoke_not_found"),
    (5, 409, "agent_invoke_conflict"),
    (6, 429, "agent_invoke_limit_exceeded"),
    (7, 503, "agent_invoke_unavailable"),
    (8, 503, "agent_invoke_commit_unknown"),
    (9, 503, "agent_invoke_invalid_state"),
];
#[tokio::test]
async fn all_six_write_endpoints_preserve_closed_error_codes_and_never_fabricate_success() {
    let f = Fixture::new().await;
    let request = f.pending("error-pending").await;
    let id = request["current"]["request"]["request_id"]
        .as_str()
        .unwrap();
    let grant = f.grant("http-c", "error-grant").await;
    let edge = grant["current"]["grant"]["edge_id"].as_u64().unwrap();
    let bodies = [
        (
            "/grants".into(),
            json!({"from_bot_id":"http-a","to_bot_id":"http-d","reason":"fixture","expected_version":0}),
        ),
        (
            "/requests".into(),
            json!({"from_bot_id":"http-a","to_bot_id":"http-d","reason":"fixture"}),
        ),
        (
            format!("/grants/{edge}/revoke?bot_id=http-c&direction=inbound"),
            json!({"expected_version":1,"reason":"fixture"}),
        ),
        (
            format!("/requests/{id}/approve?bot_id=http-b&direction=inbound"),
            json!({"expected_version":1,"reason":"fixture"}),
        ),
        (
            format!("/requests/{id}/reject?bot_id=http-b&direction=inbound"),
            json!({"expected_version":1,"reason":"fixture"}),
        ),
        (
            format!("/requests/{id}/cancel?bot_id=http-a&direction=outbound"),
            json!({"expected_version":1,"reason":"fixture"}),
        ),
    ];
    for (path, body) in bodies {
        for &(fault, status, code) in ERRORS {
            f.write_faults.code.store(fault, Ordering::SeqCst);
            let before = f.write_faults.calls.load(Ordering::SeqCst);
            let actor = if path.contains("/cancel") || path == "/requests" {
                "source"
            } else {
                "target"
            };
            let (actual, data) = f
                .request("POST", &path, Some(actor), Some("error-map"), body.clone())
                .await;
            assert_eq!(actual.as_u16(), status, "{path} {data}");
            assert_eq!(data["data"]["error_code"], code);
            assert_eq!(
                f.write_faults.calls.load(Ordering::SeqCst),
                before + 1,
                "actual application port hit"
            );
        }
    }
}
#[tokio::test]
async fn all_six_read_endpoints_propagate_errors_instead_of_empty_or_partial_pages() {
    let f = Fixture::new().await;
    let request = f.pending("read-error-pending").await;
    let id = request["current"]["request"]["request_id"]
        .as_str()
        .unwrap();
    let grant = f.grant("http-c", "read-error-grant").await;
    let edge = grant["current"]["grant"]["edge_id"].as_u64().unwrap();
    for path in [
        "/grants?bot_id=http-a&direction=outbound".into(),
        "/requests?bot_id=http-a&direction=outbound".into(),
        format!("/grants/{edge}?bot_id=http-a&direction=outbound"),
        format!("/requests/{id}?bot_id=http-a&direction=outbound"),
        "/degrees?bot_id=http-a".into(),
        "/audit?bot_id=http-a&peer_bot_id=http-b&direction=outbound".into(),
    ] {
        for &(fault, status, code) in ERRORS {
            f.read_faults.code.store(fault, Ordering::SeqCst);
            let before = f.read_faults.calls.load(Ordering::SeqCst);
            let (actual, data) = f
                .request("GET", &path, Some("source"), None, Value::Null)
                .await;
            assert_eq!(actual.as_u16(), status, "{path} {data}");
            assert_eq!(data["data"]["error_code"], code);
            assert_eq!(f.read_faults.calls.load(Ordering::SeqCst), before + 1);
        }
    }
    f.read_faults.code.store(7, Ordering::SeqCst);
    let before = f.write_faults.calls.load(Ordering::SeqCst);
    let (status, _) = f
        .request(
            "POST",
            &format!("/requests/{id}/approve?bot_id=http-b&direction=inbound"),
            Some("target"),
            Some("no-write"),
            json!({"expected_version":1,"reason":"dependency unavailable"}),
        )
        .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(f.write_faults.calls.load(Ordering::SeqCst), before);
}
