use std::sync::Arc;

use axum::body::{to_bytes, Body};
use axum::http::{Request, StatusCode};
use bcs_bot::BotCore;
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{BotCapabilities, BotRegistryCoreService};
use bcs_services_container::Services;
use serde_json::{json, Value};
use tempfile::TempDir;
use tower::ServiceExt;

async fn app() -> axum::Router {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, token) in [
        ("judge_bot", "judge-token"),
        ("admin", "admin-token"),
        ("张三", "zhangsan-token"),
        ("other_bot", "other-token"),
    ] {
        registry
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities::default(),
                "test",
                token,
            )
            .await
            .expect("register bot token");
    }
    let services = Services::builder()
        .registry(registry as Arc<dyn BotRegistryCoreService>)
        .build_for_test();
    build_router(HttpAppState::new(services))
}

fn authed_post(uri: &str, token: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .header("authorization", format!("Bearer {token}"))
        .body(Body::from(body.to_string()))
        .expect("request")
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

async fn post_json(uri: &str, token: &str, body: Value) -> (StatusCode, Value) {
    let response = app()
        .await
        .oneshot(authed_post(uri, token, body))
        .await
        .expect("response");
    let status = response.status();
    let body = response_json(response).await;
    (status, body)
}

fn authed_raw_post(uri: &str, token: &str, body: &str) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .header("authorization", format!("Bearer {token}"))
        .body(Body::from(body.to_string()))
        .expect("request")
}

async fn post_raw(uri: &str, token: &str, body: &str) -> (StatusCode, Value) {
    let response = app()
        .await
        .oneshot(authed_raw_post(uri, token, body))
        .await
        .expect("response");
    let status = response.status();
    let body = response_json(response).await;
    (status, body)
}

#[tokio::test]
async fn add_uses_authenticated_bot_actor_and_rejects_duplicate() {
    let app = app().await;
    let request_body = json!({
        "name": "player_state",
        "scope_level": "session",
        "content": "张三出局、李四存活",
        "visible_to": {"userIds": []},
        "collect_from": {"userIds": ["judge_bot"]}
    });

    let response = app
        .clone()
        .oneshot(authed_post(
            "/groupcontext/add?groupId=game-room-7&sessionId=sess-001",
            "judge-token",
            request_body.clone(),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body, json!({"status": "ok", "error_msg": null}));

    let response = app
        .oneshot(authed_post(
            "/groupcontext/add?groupId=game-room-7&sessionId=sess-001",
            "judge-token",
            request_body,
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(body["status"], "conflict");
}

#[tokio::test]
async fn normal_routes_do_not_trust_actor_id_from_query_or_body() {
    let (status, body) = post_json(
        "/groupcontext/add?groupId=game-room-7&sessionId=sess-001&actorId=other_bot",
        "judge-token",
        json!({
            "actor_id": "other_bot",
            "name": "player_state",
            "scope_level": "session",
            "content": "state",
            "visible_to": {"userIds": []},
            "collect_from": {"userIds": ["judge_bot"]}
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], "ok");

    let (status, body) = post_json(
        "/groupcontext/add?groupId=game-room-7&sessionId=sess-001",
        "other-token",
        json!({
            "actor_id": "judge_bot",
            "name": "private_write",
            "scope_level": "session",
            "content": "state",
            "visible_to": {"userIds": []},
            "collect_from": {"userIds": ["judge_bot"]}
        }),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["status"], "permission_denied");
}

#[tokio::test]
async fn route_errors_match_spec_statuses() {
    let (status, body) = post_json(
        "/groupcontext/add?groupId=game-room-7",
        "judge-token",
        json!({
            "name": "missing_session",
            "scope_level": "session",
            "content": "state",
            "visible_to": {"userIds": []},
            "collect_from": {"userIds": ["judge_bot"]}
        }),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["status"], "invalid_param");

    let (status, body) = post_json(
        "/groupcontext/update?groupId=game-room-7&sessionId=sess-001",
        "judge-token",
        json!({
            "name": "missing",
            "scope_level": "session",
            "content": "new"
        }),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["status"], "not_found");

    let (status, body) = post_json(
        "/groupcontext/add?groupId=game-room-7",
        "admin-token",
        json!({
            "name": "too_big",
            "scope_level": "group",
            "content": "x".repeat(4097),
            "visible_to": {"userIds": []},
            "collect_from": {"userIds": ["admin"]}
        }),
    )
    .await;
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(body["status"], "payload_too_large");
}

#[tokio::test]
async fn valid_time_fields_accept_required_wire_formats_and_reject_pure_date() {
    let app = app().await;

    let response = app
        .clone()
        .oneshot(authed_post(
            "/groupcontext/add?groupId=game-room-7&sessionId=sess-001",
            "judge-token",
            json!({
                "name": "time_add",
                "scope_level": "session",
                "content": "initial",
                "visible_to": {"userIds": ["judge_bot"]},
                "collect_from": {"userIds": ["judge_bot"]},
                "valid_from": "2026-10-12T14:56:08+08:00",
                "valid_to": 1791797768000i64
            }),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    let response = app
        .clone()
        .oneshot(authed_post(
            "/groupcontext/update?groupId=game-room-7&sessionId=sess-001",
            "judge-token",
            json!({
                "name": "time_add",
                "scope_level": "session",
                "content": "updated",
                "valid_from": 1791797768i64,
                "valid_to": "2026-10-13T00:00:00+08:00"
            }),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    let response = app
        .clone()
        .oneshot(authed_post(
            "/groupcontext/add?groupId=game-room-7",
            "admin-token",
            json!({
                "name": "time_system",
                "scope_level": "group",
                "content": "system value",
                "visible_to": {"userIds": []},
                "collect_from": {"userIds": ["admin"]},
                "valid_from": "2026-10-13 14:56:08",
                "valid_to": null
            }),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    let response = app
        .clone()
        .oneshot(authed_post(
            "/groupcontext/get?groupId=game-room-7&sessionId=sess-001",
            "judge-token",
            json!({"name": "time_add", "scope_levels": ["session"]}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let item = &body["items"].as_array().expect("items")[0];
    assert_eq!(item["content"], "updated");
    assert_eq!(item["valid_from"], "2026-10-12T09:36:08Z");
    assert_eq!(item["valid_to"], "2026-10-12T16:00:00Z");

    let response = app
        .oneshot(authed_post(
            "/groupcontext/add?groupId=game-room-7&sessionId=sess-001",
            "judge-token",
            json!({
                "name": "bad_date",
                "scope_level": "session",
                "content": "bad",
                "visible_to": {"userIds": []},
                "collect_from": {"userIds": ["judge_bot"]},
                "valid_from": "2026-10-12"
            }),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["status"], "invalid_param");
}

#[tokio::test]
async fn numeric_string_timestamps_roundtrip() {
    let app = app().await;

    // Send valid_from and valid_to as numeric JSON strings (simulating
    // a legacy CLI client that encodes unix timestamps as strings).
    let response = app
        .clone()
        .oneshot(authed_post(
            "/groupcontext/add?groupId=game-room-7&sessionId=sess-num-str",
            "judge-token",
            json!({
                "name": "time_numeric_str",
                "scope_level": "session",
                "content": "sent as numeric strings",
                "visible_to": {"userIds": ["judge_bot"]},
                "collect_from": {"userIds": ["judge_bot"]},
                "valid_from": "1791797768",
                "valid_to": "1791797768000"
            }),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["status"], "ok");

    // Read back — timestamps should have round-tripped.
    let response = app
        .oneshot(authed_post(
            "/groupcontext/get?groupId=game-room-7&sessionId=sess-num-str",
            "judge-token",
            json!({"name": "time_numeric_str", "scope_levels": ["session"]}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let item = &body["items"].as_array().expect("items")[0];
    assert_eq!(item["content"], "sent as numeric strings");
    assert!(
        item["valid_from"].as_str().is_some_and(|s| !s.is_empty()),
        "valid_from should be a non-empty RFC3339 string"
    );
    assert!(
        item["valid_to"].as_str().is_some_and(|s| !s.is_empty()),
        "valid_to should be a non-empty RFC3339 string"
    );
}

#[tokio::test]
async fn malformed_query_and_json_bodies_map_to_invalid_param() {
    let (status, body) = post_raw(
        "/groupcontext/get?groupId=game-room-7&groupId=game-room-8",
        "judge-token",
        r#"{"name":"game_rule"}"#,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["status"], "invalid_param");

    let (status, body) = post_raw(
        "/groupcontext/get?groupId=game-room-7",
        "judge-token",
        "{not-json",
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["status"], "invalid_param");
}

#[tokio::test]
async fn invalid_time_wire_types_are_rejected() {
    let (status, body) = post_json(
        "/groupcontext/add?groupId=game-room-7&sessionId=sess-001",
        "judge-token",
        json!({
            "name": "invalid_time_type",
            "scope_level": "session",
            "content": "state",
            "visible_to": {"userIds": []},
            "collect_from": {"userIds": ["judge_bot"]},
            "valid_from": true
        }),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["status"], "invalid_param");

    let (status, body) = post_json(
        "/groupcontext/add?groupId=game-room-7&sessionId=sess-002",
        "judge-token",
        json!({
            "name": "invalid_time_text",
            "scope_level": "session",
            "content": "state",
            "visible_to": {"userIds": []},
            "collect_from": {"userIds": ["judge_bot"]},
            "valid_from": "not-a-time"
        }),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["status"], "invalid_param");
}

#[tokio::test]
async fn add_list_and_get_follow_visibility_rules() {
    let app = app().await;
    for (name, visible_to, collect_from, content, token) in [
        ("game_rule", json!([]), json!(["admin"]), "规则", "admin-token"),
        (
            "player_word_1",
            json!(["张三", "judge_bot"]),
            json!(["judge_bot"]),
            "香蕉",
            "judge-token",
        ),
        (
            "player_role",
            json!(["judge_bot"]),
            json!(["judge_bot"]),
            "卧底名单",
            "judge-token",
        ),
        (
            "write_only",
            json!(["李四"]),
            json!(["张三"]),
            "只能写",
            "zhangsan-token",
        ),
    ] {
        let response = app
            .clone()
            .oneshot(authed_post(
                "/groupcontext/add?groupId=game-room-7&sessionId=sess-001",
                token,
                json!({
                    "name": name,
                    "scope_level": if name == "game_rule" { "group" } else { "session" },
                    "content": content,
                    "visible_to": {"userIds": visible_to},
                    "collect_from": {"userIds": collect_from},
                    "valid_to": if name == "player_word_1" { json!("2026-10-12T16:00:00+08:00") } else { Value::Null }
                }),
            ))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::OK);
    }

    let response = app
        .clone()
        .oneshot(authed_post(
            "/groupcontext/list?groupId=game-room-7&sessionId=sess-001",
            "zhangsan-token",
            json!({"scope_levels": ["group", "session"]}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let names = body["contexts"]
        .as_array()
        .expect("contexts")
        .iter()
        .map(|item| {
            (
                item["name"].as_str().expect("name"),
                item["permission"].as_str().expect("permission"),
            )
        })
        .collect::<Vec<_>>();
    assert!(names.contains(&("game_rule", "R")));
    assert!(names.contains(&("player_word_1", "R")));
    assert!(names.contains(&("write_only", "W")));
    assert!(!names.iter().any(|(name, _)| *name == "player_role"));
    let player_word_meta = body["contexts"]
        .as_array()
        .expect("contexts")
        .iter()
        .find(|item| item["name"] == "player_word_1")
        .expect("player_word_1 meta");
    assert_eq!(player_word_meta["scope"], "sess-001");
    assert!(player_word_meta.get("permission").is_some());
    assert!(player_word_meta.get("gmt_create").is_some());
    assert!(player_word_meta.get("gmt_modified").is_some());
    assert!(player_word_meta.get("content").is_none());
    assert!(player_word_meta.get("valid_from").is_none());
    assert!(player_word_meta.get("valid_to").is_none());

    let response = app
        .clone()
        .oneshot(authed_post(
            "/groupcontext/get?groupId=game-room-7&sessionId=sess-001",
            "zhangsan-token",
            json!({"name": "player_word_1", "scope_levels": ["session"]}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let item = &body["items"].as_array().expect("items")[0];
    assert_eq!(item["content"], "香蕉");
    assert!(item.get("valid_from").is_some());
    assert_eq!(item["valid_to"], "2026-10-12T08:00:00Z");

    let response = app
        .oneshot(authed_post(
            "/groupcontext/get?groupId=game-room-7&sessionId=sess-001",
            "zhangsan-token",
            json!({"name": "write_only", "scope_levels": ["session"], "limit": 1}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["status"], "permission_denied");
}

#[tokio::test]
async fn systemset_route_is_not_mounted() {
    let app = app().await;
    let response = app
        .oneshot(authed_post(
            "/groupcontext/systemset?groupId=game-room-7",
            "judge-token",
            json!({
                "name": "game_rule",
                "scope_level": "group",
                "content": "state",
                "visible_to": {"userIds": []},
                "collect_from": {"userIds": ["judge_bot"]}
            }),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn unauthenticated_request_returns_401() {
    let response = app()
        .await
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groupcontext/get?groupId=game-room-7")
                .header("content-type", "application/json")
                .body(Body::from(json!({"name": "game_rule"}).to_string()))
                .expect("request"),
        )
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["status"], "invalid_param");
}
