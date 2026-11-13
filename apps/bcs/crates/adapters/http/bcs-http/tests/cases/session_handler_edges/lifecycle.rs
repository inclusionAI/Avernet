//! Edge branches of the Session lifecycle and query routes:
//!   PATCH  /sessions/{sid}
//!   POST   /sessions/{sid}/complete
//!   DELETE /sessions/{sid}
//!   GET    /sessions/{sid}
//!   GET    /groups/{id}/sessions
//!   POST   /sessions/{sid}/collect
//!   DELETE /sessions/{sid}/collect

use axum::http::StatusCode;
use bcs_domain::{ActorKind, MessageViewScope};
use bcs_service_api::{
    GroupStrategy, SessionKind, SessionStatus,
};
use serde_json::json;

use super::{builder, call, empty, empty_as, env, get, get_as, json_as, json_body, session_for, Fail, SESSION_ID};

const SESSION: &str = "/sessions/group-1:00000001";

// ----- PATCH /sessions/{sid} --------------------------------------------

#[tokio::test]
async fn patch_session_updates_title() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_body("PATCH", SESSION, json!({"session_title": "Renamed"})),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["session_title"], "Renamed");
}

#[tokio::test]
async fn patch_session_propagates_title_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.title_error.lock().await = Some(Fail::Conflict);

    let (status, body) = call(
        &env.app,
        json_body("PATCH", SESSION, json!({"session_title": "Renamed"})),
    )
    .await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "state conflict");
}

#[tokio::test]
async fn patch_session_without_fields_returns_current_session() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(&env.app, json_body("PATCH", SESSION, json!({}))).await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["session_id"], SESSION_ID);
}

#[tokio::test]
async fn patch_session_without_fields_returns_not_found() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_body("PATCH", "/sessions/group-1:deadbeef", json!({})),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "session not found");
}

#[tokio::test]
async fn patch_session_without_fields_propagates_store_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.get_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(&env.app, json_body("PATCH", SESSION, json!({}))).await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

// ----- POST /sessions/{sid}/complete ------------------------------------

#[tokio::test]
async fn complete_session_returns_not_found_for_missing_session() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:deadbeef/complete",
            "driver-token",
            json!({}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["message"], "session group-1:deadbeef not found");
}

#[tokio::test]
async fn complete_session_propagates_store_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.complete_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/complete",
            "driver-token",
            json!({}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn complete_session_propagates_read_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.get_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/complete",
            "driver-token",
            json!({}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn complete_session_surfaces_authority_failure_for_a_human() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .authority(bcs_test_support::failing_authority_hook())
        .build();

    let (status, _) = call(
        &env.app,
        json_body(
            "POST",
            "/sessions/group-1:00000001/complete",
            json!({}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn complete_session_requires_the_parent_group() {
    let env = env(GroupStrategy::Chat, None).await;
    env.sessions.sessions.lock().await[0].group_id = "group-missing".to_string();

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/complete",
            "driver-token",
            json!({}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["message"], "parent group not found");
}

#[tokio::test]
async fn complete_session_allows_human_controlling_the_driver() {
    let chain = bcs_test_support::lazy_authority_chain();
    chain.hook.seed_owner("driver-bot", "alice");
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .authority(chain.hook.clone())
        .build();

    let (status, body) = call(
        &env.app,
        json_body(
            "POST",
            "/sessions/group-1:00000001/complete",
            json!({"output": {"done": true}}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], "completed");
    assert_eq!(body["output"]["done"], true);
}

#[tokio::test]
async fn complete_session_reports_already_completed() {
    let env = env(GroupStrategy::Chat, None).await;
    env.sessions.sessions.lock().await[0].status = SessionStatus::Completed;

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/complete",
            "driver-token",
            json!({}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["already_completed"], true);
}

// ----- DELETE /sessions/{sid} -------------------------------------------

#[tokio::test]
async fn delete_session_returns_not_found_for_missing_session() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:deadbeef?bot_id=driver-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["message"], "session group-1:deadbeef not found");
}

#[tokio::test]
async fn delete_session_propagates_read_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.get_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001?bot_id=driver-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn delete_session_authorizes_the_driver_bot() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001?bot_id=driver-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["deleted"], true);
}

#[tokio::test]
async fn delete_session_authorizes_the_session_creator() {
    let env = env(GroupStrategy::Chat, None).await;
    env.sessions.sessions.lock().await[0].created_by = Some("worker-bot".to_string());

    let (status, _) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001?bot_id=worker-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn delete_session_rejects_unrelated_caller() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001?bot_id=worker-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(
        body["message"],
        "caller worker-bot is not authorized to delete this session"
    );
}

#[tokio::test]
async fn delete_session_allows_human_controlling_the_creator() {
    let chain = bcs_test_support::lazy_authority_chain();
    chain.hook.seed_owner("worker-bot", "alice");
    let env = builder(GroupStrategy::Chat, None)
        .await
        .authority(chain.hook.clone())
        .build();
    env.sessions.sessions.lock().await[0].created_by = Some("worker-bot".to_string());

    let (status, _) = call(
        &env.app,
        empty(
            "DELETE",
            "/sessions/group-1:00000001?bot_id=human_alice",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn delete_session_allows_human_controlling_the_driver_when_creator_is_absent() {
    let chain = bcs_test_support::lazy_authority_chain();
    chain.hook.seed_owner("driver-bot", "alice");
    let env = builder(GroupStrategy::Chat, None)
        .await
        .authority(chain.hook.clone())
        .build();

    let (status, _) = call(
        &env.app,
        empty(
            "DELETE",
            "/sessions/group-1:00000001?bot_id=human_alice",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn delete_session_surfaces_authority_failure() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .authority(bcs_test_support::failing_authority_hook())
        .build();
    env.sessions.sessions.lock().await[0].created_by = Some("worker-bot".to_string());

    let (status, _) = call(
        &env.app,
        empty(
            "DELETE",
            "/sessions/group-1:00000001?bot_id=human_alice",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn delete_session_reports_not_found_when_store_says_no() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.delete_result.lock().await = false;

    let (status, body) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001?bot_id=driver-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["message"], "session not found");
}

#[tokio::test]
async fn delete_session_propagates_delete_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.delete_error.lock().await = Some(Fail::Conflict);

    let (status, body) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001?bot_id=driver-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "state conflict");
}

// ----- GET /sessions/{sid} ----------------------------------------------

#[tokio::test]
async fn get_session_returns_not_found() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(&env.app, get("/sessions/group-1:deadbeef")).await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "session not found");
}

#[tokio::test]
async fn get_session_propagates_store_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.get_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(&env.app, get(SESSION)).await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

// ----- GET /groups/{id}/sessions ----------------------------------------

#[tokio::test]
async fn list_sessions_requires_participant_for_collected_filter() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        get_as("/groups/group-1/sessions?collected=true", "driver-token"),
    )
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["message"], "collected filter requires participant");
}

#[tokio::test]
async fn list_sessions_requires_auth_for_collected_filter() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, _) = call(
        &env.app,
        get("/groups/group-1/sessions?collected=true&participant=worker-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn list_sessions_propagates_collection_store_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.collected_list_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(
        &env.app,
        get_as(
            "/groups/group-1/sessions?collected=true&participant=worker-bot",
            "bot-token",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn list_sessions_propagates_list_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.list_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(&env.app, get_as("/groups/group-1/sessions", "driver-token")).await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn list_sessions_keeps_items_for_unauthenticated_callers() {
    // NOTE: pins current unauthenticated behavior; flagged for owner review.
    // No identity and no Bot token: the route still lists (caller-scoped
    // visibility only filters when a caller resolved).
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(&env.app, get("/groups/group-1/sessions")).await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["items"].as_array().unwrap().len(), 1);
}

#[tokio::test]
async fn list_sessions_filters_to_temp_participant_sessions() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

    let (status, body) = call(
        &env.app,
        get("/groups/group-1/sessions"),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    // alice is not a group participant and controls no Bot, so she only sees
    // sessions she participates in — none.
    assert_eq!(body["items"].as_array().unwrap().len(), 0);
}

#[tokio::test]
async fn list_sessions_surfaces_authority_failure() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .authority(bcs_test_support::failing_authority_hook())
        .build();

    let (status, _) = call(&env.app, get("/groups/group-1/sessions")).await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn list_sessions_marks_participant_collection_state() {
    let env = env(GroupStrategy::Chat, None).await;
    let (status, _) = call(
        &env.app,
        json_as("POST", "/sessions/group-1:00000001/collect", "bot-token", json!({})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    let (status, body) = call(
        &env.app,
        get_as(
            "/groups/group-1/sessions?participant=worker-bot",
            "bot-token",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    let item = &body["items"][0];
    assert_eq!(item["collected"], true);
    assert_eq!(item["collected_at"], 42);
}

#[tokio::test]
async fn list_sessions_marks_uncollected_participant_sessions() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        get_as(
            "/groups/group-1/sessions?participant=worker-bot",
            "bot-token",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["items"][0]["collected"], false);
    assert!(body["items"][0].get("collected_at").is_none());
}

#[tokio::test]
async fn list_collected_sessions_by_bot() {
    let env = env(GroupStrategy::Chat, None).await;
    call(
        &env.app,
        json_as("POST", "/sessions/group-1:00000001/collect", "bot-token", json!({})),
    )
    .await;

    let (status, body) = call(
        &env.app,
        get_as(
            "/groups/group-1/sessions?collected=true&participant=worker-bot",
            "bot-token",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    let items = body["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["collected"], true);
}

#[tokio::test]
async fn list_sessions_for_a_missing_group_is_empty_not_an_error() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

    let (status, body) = call(&env.app, get("/groups/group-missing/sessions")).await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["items"].as_array().unwrap().len(), 0);
}

// ----- POST/DELETE /sessions/{sid}/collect ------------------------------

#[tokio::test]
async fn collect_session_rejects_unauthenticated_caller() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_body("POST", "/sessions/group-1:00000001/collect", json!({})),
    )
    .await;

    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"], "unauthorized");
}

#[tokio::test]
async fn collect_session_by_bot_records_collection() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/collect",
            "bot-token",
            json!({}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["collected"], true);
}

#[tokio::test]
async fn collect_session_for_own_human_actor() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

    let (status, body) = call(
        &env.app,
        json_body(
            "POST",
            "/sessions/group-1:00000001/collect",
            json!({"participant": "human_alice"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["collected"], true);
    // The Human self-right is decided on the actor identity itself, so the
    // collection row must be keyed by `human_alice` (not the caller's staff id).
    let collected = env.sessions.collected.lock().await;
    assert_eq!(
        collected.get(&(SESSION_ID.to_string(), "human_alice".to_string())),
        Some(&42),
        "the self branch stores the caller's own Human actor"
    );
    assert!(
        !collected.contains_key(&(SESSION_ID.to_string(), "alice".to_string())),
        "the bare staff id is never used as the collection actor"
    );
}

#[tokio::test]
async fn collect_session_rejects_another_human_actor() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

    let (status, body) = call(
        &env.app,
        json_body(
            "POST",
            "/sessions/group-1:00000001/collect",
            json!({"participant": "human_bob"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["message"], "caller does not own bot human_bob");
}

#[tokio::test]
async fn collect_session_surfaces_authority_failure() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .authority(bcs_test_support::failing_authority_hook())
        .build();

    let (status, _) = call(
        &env.app,
        json_body(
            "POST",
            "/sessions/group-1:00000001/collect",
            json!({"participant": "worker-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn uncollect_session_rejects_unauthenticated_caller() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, _) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001/collect"),
    )
    .await;

    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn uncollect_session_propagates_store_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.uncollect_error.lock().await = Some(Fail::Conflict);

    let (status, body) = call(
        &env.app,
        empty_as(
            "DELETE",
            "/sessions/group-1:00000001/collect",
            "bot-token",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "state conflict");
}

#[tokio::test]
async fn fixture_session_is_running_chat_kind() {
    // Fixture guard (not a route contract).
    let session = session_for("group-1", SessionKind::Chat);
    assert_eq!(session.status, SessionStatus::Running);
    assert_eq!(session.session_kind, SessionKind::Chat);
    assert_eq!(session.participants[0].actor_kind, ActorKind::Bot);
    assert_eq!(
        session.participants[0].message_view_scope,
        MessageViewScope::Full
    );
}
