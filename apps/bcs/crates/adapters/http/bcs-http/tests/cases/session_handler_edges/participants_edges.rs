//! Store-failure and notifier-failure edge paths of the Session member routes.
//!
//! Split out of `participants.rs` to keep every file under the repository's
//! 1000-line ceiling; the fixture lives in the parent `session_handler_edges`
//! module.

use axum::http::StatusCode;
use bcs_service_api::{GroupStrategy, Participant, ParticipantMode, ParticipantRole};
use serde_json::json;

use super::session_application::{FakeSessionApplication, UpdateOutcome};
use super::{builder, call, empty, env, json_body, Fail};

fn mode_of(body: &serde_json::Value, bot_uuid: &str) -> Option<String> {
    body["participants"]
        .as_array()
        .and_then(|participants| {
            participants
                .iter()
                .find(|p| p["bot_uuid"] == bot_uuid)
        })
        .and_then(|p| p["mode"].as_str().map(str::to_string))
}

fn scope_of(body: &serde_json::Value, bot_uuid: &str) -> Option<String> {
    body["participants"]
        .as_array()
        .and_then(|participants| {
            participants
                .iter()
                .find(|p| p["bot_uuid"] == bot_uuid)
        })
        .and_then(|p| p["message_view_scope"].as_str().map(str::to_string))
}

// ----- remaining store / notifier edge paths ----------------------------

#[tokio::test]
async fn remove_member_denies_when_the_parent_group_is_missing() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;
    env.sessions.sessions.lock().await[0].group_id = "group-missing".to_string();

    let (status, body) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001/members/worker-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(
        body["error"],
        "Caller is not authorized to remove this participant"
    );
}

#[tokio::test]
async fn patch_member_reports_missing_session_after_application_update() {
    let application = FakeSessionApplication::new(UpdateOutcome::Ok);
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .session_application(application.clone())
        .build();
    env.sessions.sessions.lock().await.clear();

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "session_not_found");
    assert_eq!(
        application.update_calls(),
        1,
        "the facade accepted the update before the read-back miss"
    );
}

#[tokio::test]
async fn patch_member_propagates_read_failure_after_application_update() {
    let application = FakeSessionApplication::new(UpdateOutcome::Ok);
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .session_application(application.clone())
        .build();
    *env.sessions.get_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(
        application.update_calls(),
        1,
        "the read failure happens after the facade update"
    );
}

#[tokio::test]
async fn patch_member_propagates_insert_failure_in_application_fallback() {
    let application = FakeSessionApplication::new(UpdateOutcome::ParticipantMissing);
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .session_application(application.clone())
        .build();
    *env.sessions.add_error.lock().await = Some(Fail::Conflict);

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "state conflict");
    assert_eq!(
        application.update_calls(),
        1,
        "the fallback insert starts from the facade's missing-participant signal"
    );
}

#[tokio::test]
async fn patch_member_propagates_insert_failure_in_scope_fallback() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;
    *env.sessions.add_error.lock().await = Some(Fail::Conflict);

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "state conflict");
}

#[tokio::test]
async fn patch_member_tolerates_notification_failure() {
    let notifier = std::sync::Arc::new(super::FailingSystemMessages::new());
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .system_message(notifier.clone())
        .build();
    {
        let mut stored = env.sessions.sessions.lock().await;
        let mut human = Participant::human("human_alice", ParticipantRole::Observer);
        human.mode = Some(ParticipantMode::Absent);
        stored[0].participants.push(human);
    }

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"mode": "present"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(mode_of(&body, "human_alice").as_deref(), Some("present"));
    assert_eq!(
        notifier.calls(),
        1,
        "the mode change must attempt exactly one notification"
    );
}

#[tokio::test]
async fn patch_member_tolerates_notification_failure_on_first_insert() {
    let notifier = std::sync::Arc::new(super::FailingSystemMessages::new());
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .system_message(notifier.clone())
        .build();

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_bob",
            json!({"mode": "present"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(mode_of(&body, "human_bob").as_deref(), Some("present"));
    assert_eq!(
        notifier.calls(),
        1,
        "the first-insert mode change must attempt exactly one notification"
    );
}

#[tokio::test]
async fn patch_member_reports_failed_retry_after_first_insert() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;
    *env.sessions.mode_error.lock().await = Some(Fail::NotFound);

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_bob",
            json!({"mode": "present"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "session store miss");
}

#[tokio::test]
async fn patch_member_reports_failed_first_insert() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;
    *env.sessions.mode_error.lock().await = Some(Fail::NotFound);
    *env.sessions.add_error.lock().await = Some(Fail::Conflict);

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_bob",
            json!({"mode": "present"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "state conflict");
}
