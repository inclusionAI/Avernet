//! BCS client-level tests for the group context API.
//!
//! Tests `BcsClient::group_context_request` against a wiremock server,
//! verifying that request URIs, query parameters, JSON bodies, and
//! authentication headers are constructed correctly.

use serde_json::Value;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use bcs_cli::BcsClient;

// Timestamp in millis used in mock responses.
const TS: i64 = 1_728_374_400_000;

// ─── response builders ──────────────────────────────────────────────────────

fn ok_response() -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_json(serde_json::json!({
        "status": "ok", "error_msg": null
    }))
}

fn list_response(names: Vec<&str>) -> ResponseTemplate {
    let contexts = names
        .into_iter()
        .map(|n| {
            serde_json::json!({
                "name": n,
                "scope_level": "session",
                "scope": "sess-001",
                "valid_from": TS,
                "permission": "R"
            })
        })
        .collect::<Vec<_>>();
    ResponseTemplate::new(200).set_body_json(serde_json::json!({"contexts": contexts}))
}

fn get_response(name: &str) -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_json(serde_json::json!({
        "items": [{
            "name": name,
            "scope_level": "session",
            "content": "你的词语是：香蕉",
            "valid_from": TS,
        }]
    }))
}

/// Capture the last received request body.
type Capture = std::sync::Arc<std::sync::Mutex<Option<Value>>>;

fn make_capture() -> Capture {
    std::sync::Arc::new(std::sync::Mutex::new(None))
}

// ─── helpers ────────────────────────────────────────────────────────────────

/// Build a client pointing at the mock server with a bearer token.
fn make_client(mock_server: &MockServer) -> BcsClient {
    BcsClient::with_token(mock_server.uri(), "test-bearer-token")
}

// ─── tests: request shape ───────────────────────────────────────────────────

#[tokio::test]
async fn add_sends_correct_body_fields() {
    let mock_server = MockServer::start().await;
    let capture = make_capture();

    let capture_clone = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/add"))
        .respond_with(move |req: &wiremock::Request| {
            *capture_clone.lock().unwrap() =
                Some(serde_json::from_slice(&req.body).unwrap_or(Value::Null));
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "add",
            &[("groupId", "game-room-7")],
            serde_json::json!({
                "name": "game_rule",
                "scope_level": "group",
                "content": "规则说明",
                "visible_to": {"userIds": []},
                "collect_from": {"userIds": ["judge_bot"]}
            }),
        )
        .await
        .unwrap();

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(body["name"], "game_rule");
    assert_eq!(body["scope_level"], "group");
    assert_eq!(body["content"], "规则说明");
    assert!(body["visible_to"]["userIds"].as_array().unwrap().is_empty());
    assert_eq!(
        body["collect_from"]["userIds"].as_array().unwrap()[0],
        "judge_bot"
    );
    assert!(
        !body.as_object().unwrap().contains_key("change_reason"),
        "add must NOT send change_reason"
    );
}

#[tokio::test]
async fn add_forwards_valid_fields_when_present() {
    let mock_server = MockServer::start().await;
    let capture = make_capture();

    let capture_clone = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/add"))
        .respond_with(move |req: &wiremock::Request| {
            *capture_clone.lock().unwrap() =
                Some(serde_json::from_slice(&req.body).unwrap_or(Value::Null));
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "add",
            &[("groupId", "game-room-7")],
            serde_json::json!({
                "name": "game_rule",
                "scope_level": "group",
                "content": "规则说明",
                "visible_to": {"userIds": []},
                "collect_from": {"userIds": ["judge_bot"]},
                "valid_from": "2026-10-12T14:56:08+08:00",
                "valid_to": "1791797768000"
            }),
        )
        .await
        .unwrap();

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(body["valid_from"], "2026-10-12T14:56:08+08:00");
    assert_eq!(body["valid_to"], "1791797768000");
    assert!(!body.as_object().unwrap().contains_key("actor_id"));
    assert!(!body.as_object().unwrap().contains_key("actorId"));
}

#[tokio::test]
async fn update_omitted_valid_fields_stay_absent() {
    let mock_server = MockServer::start().await;
    let capture = make_capture();

    let capture_clone = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/update"))
        .respond_with(move |req: &wiremock::Request| {
            *capture_clone.lock().unwrap() =
                Some(serde_json::from_slice(&req.body).unwrap_or(Value::Null));
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "update",
            &[("groupId", "game-room-7"), ("sessionId", "sess-001")],
            serde_json::json!({
                "name": "player_state",
                "scope_level": "session",
                "change_reason": "metadata only"
            }),
        )
        .await
        .unwrap();

    let body = capture.lock().unwrap().take().unwrap();
    let object = body.as_object().unwrap();
    assert!(!object.contains_key("valid_from"));
    assert!(!object.contains_key("valid_to"));
    assert!(!object.contains_key("actor_id"));
    assert!(!object.contains_key("actorId"));
}

#[tokio::test]
async fn add_does_not_send_actor_id_as_query_param() {
    let mock_server = MockServer::start().await;
    let url_capture = std::sync::Arc::new(std::sync::Mutex::new(String::new()));
    let url_clone = url_capture.clone();

    Mock::given(method("POST"))
        .and(path("/groupcontext/add"))
        .respond_with(move |req: &wiremock::Request| {
            *url_clone.lock().unwrap() = req.url.to_string();
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "add",
            &[("groupId", "game-room-7")],
            serde_json::json!({
                "name": "game_rule",
                "scope_level": "group",
                "content": "规则",
                "visible_to": {"userIds": []},
                "collect_from": {"userIds": ["judge_bot"]}
            }),
        )
        .await
        .unwrap();

    let url = url_capture.lock().unwrap();
    assert!(
        !url.contains("actorId"),
        "actorId must not be in URL query. URL was: {}",
        url
    );
}

#[tokio::test]
async fn add_with_session_and_round_in_query() {
    let mock_server = MockServer::start().await;
    let url_capture = std::sync::Arc::new(std::sync::Mutex::new(String::new()));
    let url_clone = url_capture.clone();

    Mock::given(method("POST"))
        .and(path("/groupcontext/add"))
        .respond_with(move |req: &wiremock::Request| {
            *url_clone.lock().unwrap() = req.url.to_string();
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "add",
            &[("groupId", "g1"), ("sessionId", "s1"), ("roundId", "r1")],
            serde_json::json!({
                "name": "r1_policy",
                "scope_level": "round",
                "content": "round policy",
                "visible_to": {"userIds": []},
                "collect_from": {"userIds": ["judge_bot"]}
            }),
        )
        .await
        .unwrap();

    let url = url_capture.lock().unwrap();
    assert!(url.contains("sessionId=s1"));
    assert!(url.contains("roundId=r1"));
}

#[tokio::test]
async fn get_sends_name_in_body() {
    let mock_server = MockServer::start().await;
    let capture = make_capture();

    let capture_clone = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/get"))
        .respond_with(move |req: &wiremock::Request| {
            *capture_clone.lock().unwrap() =
                Some(serde_json::from_slice(&req.body).unwrap_or(Value::Null));
            get_response("player_word_1")
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "get",
            &[("groupId", "game-room-7"), ("sessionId", "sess-001")],
            serde_json::json!({
                "name": "player_word_1",
                "scope_levels": ["session"],
                "limit": 3
            }),
        )
        .await
        .unwrap();

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(body["name"], "player_word_1");
    assert_eq!(body["scope_levels"].as_array().unwrap()[0], "session");
    assert_eq!(body["limit"].as_u64(), Some(3));
}

#[tokio::test]
async fn list_sends_scope_levels_in_body() {
    let mock_server = MockServer::start().await;
    let capture = make_capture();

    let capture_clone = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/list"))
        .respond_with(move |req: &wiremock::Request| {
            *capture_clone.lock().unwrap() =
                Some(serde_json::from_slice(&req.body).unwrap_or(Value::Null));
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "list",
            &[("groupId", "game-room-7"), ("sessionId", "sess-001")],
            serde_json::json!({"scope_levels": ["group", "session"]}),
        )
        .await
        .unwrap();

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(body["scope_levels"].as_array().unwrap().len(), 2);
    assert_eq!(body["scope_levels"][0], "group");
    assert_eq!(body["scope_levels"][1], "session");
}

#[tokio::test]
async fn list_empty_scope_levels_sends_null() {
    let mock_server = MockServer::start().await;
    let capture = make_capture();

    let capture_clone = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/list"))
        .respond_with(move |req: &wiremock::Request| {
            *capture_clone.lock().unwrap() =
                Some(serde_json::from_slice(&req.body).unwrap_or(Value::Null));
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "list",
            &[("groupId", "game-room-7")],
            serde_json::json!({"scope_levels": null}),
        )
        .await
        .unwrap();

    let body = capture.lock().unwrap().take().unwrap();
    assert!(body["scope_levels"].is_null());
}

#[tokio::test]
async fn update_sends_change_reason() {
    let mock_server = MockServer::start().await;
    let capture = make_capture();

    let capture_clone = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/update"))
        .respond_with(move |req: &wiremock::Request| {
            *capture_clone.lock().unwrap() =
                Some(serde_json::from_slice(&req.body).unwrap_or(Value::Null));
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "update",
            &[("groupId", "game-room-7"), ("sessionId", "sess-001")],
            serde_json::json!({
                "name": "player_state",
                "scope_level": "session",
                "content": "张三出局",
                "owners": {"userIds": ["bot-a", "bot-b"]},
                "change_reason": "第一轮投票"
            }),
        )
        .await
        .unwrap();

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(body["name"], "player_state");
    assert_eq!(body["content"], "张三出局");
    assert_eq!(
        body["owners"],
        serde_json::json!({"userIds": ["bot-a", "bot-b"]})
    );
    assert_eq!(body["change_reason"], "第一轮投票");
}

#[tokio::test]
async fn update_optional_fields_sends_null() {
    let mock_server = MockServer::start().await;
    let capture = make_capture();

    let capture_clone = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/update"))
        .respond_with(move |req: &wiremock::Request| {
            *capture_clone.lock().unwrap() =
                Some(serde_json::from_slice(&req.body).unwrap_or(Value::Null));
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    // Only updating content; visible_to and collect_from absent → null
    client
        .group_context_request(
            "update",
            &[("groupId", "game-room-7"), ("sessionId", "sess-001")],
            serde_json::json!({
                "name": "player_state",
                "scope_level": "session",
                "content": "新状态",
                "change_reason": null,
                "visible_to": null,
                "collect_from": null
            }),
        )
        .await
        .unwrap();

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(body["content"], "新状态");
    assert!(body["visible_to"].is_null());
    assert!(body["collect_from"].is_null());
}

// ─── tests: error responses ─────────────────────────────────────────────────

#[tokio::test]
async fn add_conflict_returns_spec_error() {
    let mock_server = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/groupcontext/add"))
        .respond_with(ResponseTemplate::new(409).set_body_json(serde_json::json!({
            "status": "conflict",
            "error_msg": "game_rule already exists in game-room-7"
        })))
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    let result = client
        .group_context_request(
            "add",
            &[("groupId", "game-room-7")],
            serde_json::json!({
                "name": "game_rule",
                "scope_level": "group",
                "content": "规则",
                "visible_to": {"userIds": []},
                "collect_from": {"userIds": ["judge_bot"]}
            }),
        )
        .await;

    assert!(result.is_err());
    let err = result.unwrap_err().to_string();
    assert!(err.contains("conflict") || err.contains("409"));
}

#[tokio::test]
async fn update_not_found_returns_spec_error() {
    let mock_server = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/groupcontext/update"))
        .respond_with(ResponseTemplate::new(404).set_body_json(serde_json::json!({
            "status": "not_found",
            "error_msg": "name=nonexistent and scope=sess-001, group context not found"
        })))
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    let result = client
        .group_context_request(
            "update",
            &[("groupId", "game-room-7"), ("sessionId", "sess-001")],
            serde_json::json!({
                "name": "nonexistent",
                "scope_level": "session",
                "content": "新内容",
                "visible_to": null,
                "collect_from": null,
                "change_reason": null
            }),
        )
        .await;

    assert!(result.is_err());
    let err = result.unwrap_err().to_string();
    assert!(err.contains("not_found") || err.contains("404"));
}

#[tokio::test]
async fn add_permission_denied_returns_error() {
    let mock_server = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/groupcontext/add"))
        .respond_with(ResponseTemplate::new(403).set_body_json(serde_json::json!({
            "status": "permission_denied",
            "error_msg": "actor_id=bad_bot is not allowed"
        })))
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    let result = client
        .group_context_request(
            "add",
            &[("groupId", "game-room-7")],
            serde_json::json!({
                "name": "game_rule",
                "scope_level": "group",
                "content": "规则",
                "visible_to": {"userIds": ["bad_bot"]},
                "collect_from": {"userIds": ["bad_bot"]}
            }),
        )
        .await;

    assert!(result.is_err());
    let err = result.unwrap_err().to_string();
    assert!(err.contains("permission_denied") || err.contains("403"));
}

#[tokio::test]
async fn list_returns_metadata_fields() {
    let mock_server = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/groupcontext/list"))
        .respond_with(list_response(vec![
            "game_rule",
            "player_word_1",
            "player_state",
        ]))
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    let result = client
        .group_context_request(
            "list",
            &[("groupId", "game-room-7"), ("sessionId", "sess-001")],
            serde_json::json!({"scope_levels": ["group"]}),
        )
        .await;

    let response = result.unwrap();
    let contexts = response["contexts"].as_array().unwrap();
    assert_eq!(contexts.len(), 3);
    assert_eq!(contexts[0]["name"], "game_rule");
    assert_eq!(contexts[0]["permission"], "R");
}

#[tokio::test]
async fn get_returns_items_from_response() {
    let mock_server = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/groupcontext/get"))
        .respond_with(get_response("player_word_1"))
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    let result = client
        .group_context_request(
            "get",
            &[("groupId", "game-room-7"), ("sessionId", "sess-001")],
            serde_json::json!({"name": "player_word_1"}),
        )
        .await;

    let response = result.unwrap();
    let items = response["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["name"], "player_word_1");
    assert!(items[0]["content"].is_string());
}

#[tokio::test]
async fn bearer_token_sent_as_authorization_header() {
    let mock_server = MockServer::start().await;
    let auth_capture = std::sync::Arc::new(std::sync::Mutex::new(String::new()));
    let auth_clone = auth_capture.clone();

    Mock::given(method("POST"))
        .and(path("/groupcontext/add"))
        .respond_with(move |req: &wiremock::Request| {
            if let Some(auth) = req.headers.get("Authorization") {
                *auth_clone.lock().unwrap() = auth.to_str().unwrap_or("").to_string();
            }
            ok_response()
        })
        .mount(&mock_server)
        .await;

    let client = make_client(&mock_server);
    client
        .group_context_request(
            "add",
            &[("groupId", "game-room-7")],
            serde_json::json!({
                "name": "game_rule",
                "scope_level": "group",
                "content": "规则",
                "visible_to": {"userIds": []},
                "collect_from": {"userIds": ["judge_bot"]}
            }),
        )
        .await
        .unwrap();

    let auth = auth_capture.lock().unwrap();
    assert!(
        auth.contains("test-bearer-token"),
        "Authorization header should contain bearer token. Auth was: {}",
        auth
    );
}
