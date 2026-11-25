mod agent_invoke_support;
use agent_invoke_support::*;
use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use serde_json::{Value, json};
use tower::ServiceExt;
const ROUTES: &[(&str, &str)] = &[
    ("POST", "/grants"),
    ("POST", "/requests"),
    ("GET", "/grants"),
    ("GET", "/requests"),
    ("GET", "/grants/1"),
    ("GET", "/requests/00000000-0000-0000-0000-000000000001"),
    ("POST", "/grants/1/revoke"),
    (
        "POST",
        "/requests/00000000-0000-0000-0000-000000000001/approve",
    ),
    (
        "POST",
        "/requests/00000000-0000-0000-0000-000000000001/reject",
    ),
    (
        "POST",
        "/requests/00000000-0000-0000-0000-000000000001/cancel",
    ),
    ("GET", "/degrees"),
    ("GET", "/audit"),
];
#[tokio::test]
async fn every_endpoint_requires_authentication_and_unmounted_services_fail_closed() {
    let app = bcs_api_http::router(state());
    for prefix in [PREFIX, MIRROR] {
        for (method, path) in ROUTES {
            let (status, _) = call(
                &app,
                method,
                &format!("{prefix}{path}"),
                None,
                Some("key"),
                Value::Null,
            )
            .await;
            assert_eq!(status, StatusCode::UNAUTHORIZED, "{method} {path}");
            let (status, data) = call(
                &app,
                method,
                &format!("{prefix}{path}"),
                Some("target"),
                Some("key"),
                Value::Null,
            )
            .await;
            assert_eq!(
                status,
                StatusCode::SERVICE_UNAVAILABLE,
                "{method} {path} {data}"
            );
        }
    }
    let (status, data) = call(
        &app,
        "GET",
        &format!("{PREFIX}/degrees?bot_id=http-a"),
        Some("outage"),
        None,
        Value::Null,
    )
    .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(data["data"]["error_code"], "auth_dependency_unavailable");
}
#[tokio::test]
async fn unknown_body_scope_actor_fields_and_missing_or_duplicate_keys_do_not_write() {
    let f = Fixture::new().await;
    let body = json!({"from_bot_id":"http-a","to_bot_id":"http-b","expected_version":0,"reason":"fixture"});
    for field in [
        "env",
        "tenant",
        "principal",
        "created_by",
        "rules",
        "grant_kind",
        "status",
        "originator_policy_type",
        "system",
        "eligible",
    ] {
        let mut body = body.clone();
        body[field] = json!("forged");
        let (status, _) = f
            .request("POST", "/grants", Some("target"), Some("invalid"), body)
            .await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{field}");
    }
    let (status, _) = f
        .request("POST", "/grants", Some("target"), None, body.clone())
        .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let (status, _) = f
        .request(
            "POST",
            "/grants?env=foreign",
            Some("target"),
            Some("bad-scope"),
            body.clone(),
        )
        .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let req = Request::builder()
        .method("POST")
        .uri(format!("{PREFIX}/grants"))
        .header("authorization", "Bearer target")
        .header("content-type", "application/json")
        .header("idempotency-key", "one")
        .header("idempotency-key", "two")
        .body(Body::from(body.to_string()))
        .unwrap();
    assert_eq!(
        f.app.clone().oneshot(req).await.unwrap().status(),
        StatusCode::BAD_REQUEST
    );
    use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
    assert_eq!(f.d.audit_count().await, 0);
}
#[tokio::test]
async fn parser_rejects_invalid_pages_unknown_queries_malformed_body_and_wrong_methods() {
    let f = Fixture::new().await;
    for suffix in [
        "/grants?bot_id=http-a&direction=outbound&limit=0",
        "/requests?bot_id=http-a&direction=outbound&limit=101",
        "/grants?bot_id=http-a&direction=outbound&limit=bad",
        "/grants?bot_id=http-a&bot_id=http-b&direction=outbound",
        "/degrees?bot_id=http-a&cursor=ignored",
        "/audit?bot_id=http-a&peer_bot_id=http-b&direction=outbound&action=unknown",
        "/grants/0?bot_id=http-a&direction=outbound",
        "/requests/not-uuid?bot_id=http-a&direction=outbound",
        "/grants?bot_id=http-a&direction=outbound&cursor=garbage",
        "/grants?bot_id=http-a&direction=outbound&cursor=v1.%7B%7D",
        "/grants?bot_id=http-a&direction=sideways",
    ] {
        let (status, data) = f
            .request("GET", suffix, Some("source"), None, Value::Null)
            .await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{suffix} {data}");
    }
    for (method, suffix) in [
        ("DELETE", "/grants/1"),
        ("GET", "/grants/1/revoke"),
        ("POST", "/degrees"),
        ("POST", "/grants/1"),
    ] {
        let (status, data) = f
            .request(method, suffix, Some("target"), Some("invalid"), json!({}))
            .await;
        assert_eq!(
            status,
            StatusCode::METHOD_NOT_ALLOWED,
            "{method} {suffix} {data}"
        );
    }
    for (content, body) in [
        ("application/json", "{"),
        ("text/plain", "{}"),
        ("application/json", ""),
    ] {
        let req = Request::builder()
            .method("POST")
            .uri(format!("{PREFIX}/requests"))
            .header("authorization", "Bearer source")
            .header("content-type", content)
            .header("idempotency-key", "bad-json")
            .body(Body::from(body))
            .unwrap();
        let response = f.app.clone().oneshot(req).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let data = to_bytes(response.into_body(), 10000).await.unwrap();
        let data: Value = serde_json::from_slice(&data).unwrap();
        assert_eq!(data["data"]["error_code"], "agent_invoke_invalid_input");
    }
    let (status, _) = f
        .request("GET", "/unknown", Some("target"), None, Value::Null)
        .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}
#[tokio::test]
async fn cookie_writes_keep_csrf_bot_owner_metadata_cannot_escalate_and_quota_errors_are_not_empty_success()
 {
    let f = Fixture::new().await;
    let body = json!({"from_bot_id":"http-a","to_bot_id":"http-b","expected_version":0,"reason":"fixture"});
    for actor in ["cookie", "bot", "stranger", "source"] {
        let (status, _) = f
            .request("POST", "/grants", Some(actor), Some("deny"), body.clone())
            .await;
        assert_eq!(status, StatusCode::FORBIDDEN, "{actor}");
    }
    f.grant("http-b", "quota-b").await;
    f.grant("http-c", "quota-c").await;
    let (status,data)=f.request("POST","/grants",Some("target"),Some("quota-d"),json!({"from_bot_id":"http-a","to_bot_id":"http-d","expected_version":0,"reason":"over quota"})).await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(data["data"]["error_code"], "agent_invoke_limit_exceeded");
    let (status, _) = f
        .request(
            "GET",
            "/grants/999999?bot_id=http-a&direction=outbound",
            Some("source"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    f.gates_off().await;
    let (status, data) = f
        .request(
            "POST",
            "/requests",
            Some("source"),
            Some("disabled"),
            json!({"from_bot_id":"http-a","to_bot_id":"http-d","reason":"disabled"}),
        )
        .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(data["data"]["error_code"], "agent_invoke_unavailable");
}
