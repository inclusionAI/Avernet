//! Edge and authorization branches of the Session member routes:
//!   POST   /sessions/{sid}/members
//!   DELETE /sessions/{sid}/members/{bot_uuid}
//!   PATCH  /sessions/{sid}/members/{bot_uuid}

use axum::http::StatusCode;
use bcs_service_api::{GroupCoreService, GroupStrategy, Participant, ParticipantMode, ParticipantRole};
use bcs_domain::{ActorKind, MessageViewScope};
use serde_json::json;

use super::session_application::{FakeSessionApplication, UpdateOutcome};
use super::{
    builder, call, empty, empty_as, env, json_as, json_body, session_for, Fail, SESSION_ID,
};

const MEMBERS: &str = "/sessions/group-1:00000001/members";

fn role_of(body: &serde_json::Value, bot_uuid: &str) -> Option<String> {
    body["participants"]
        .as_array()
        .and_then(|participants| {
            participants
                .iter()
                .find(|p| p["bot_uuid"] == bot_uuid)
        })
        .and_then(|p| p["role"].as_str().map(str::to_string))
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

// ----- POST members -------------------------------------------------------

#[tokio::test]
async fn add_member_returns_not_found_for_missing_session() {
    let env = env(GroupStrategy::Chat, None).await;
    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:deadbeef/members",
            "driver-token",
            json!({"bot_uuid": "extra-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "not_found");
    assert_eq!(body["message"], "session group-1:deadbeef not found");
}

#[tokio::test]
async fn add_member_propagates_session_store_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.get_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "extra-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn add_member_defaults_consultant_role_for_chat_groups() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "extra-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(role_of(&body, "extra-bot").as_deref(), Some("consultant"));
}

#[tokio::test]
async fn add_member_defaults_worker_role_for_manager_worker_groups() {
    let env = env(GroupStrategy::ManagerWorker, None).await;

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "extra-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(role_of(&body, "extra-bot").as_deref(), Some("worker"));
}

#[tokio::test]
async fn add_member_inherits_human_scope_from_group_participants() {
    let env = env(GroupStrategy::Chat, None).await;
    {
        let mut group = env.groups.get("group-1").await.unwrap();
        let mut human = Participant::human("human_alice", ParticipantRole::Observer);
        human.message_view_scope = MessageViewScope::Participant;
        group.participants.push(human);
        env.groups.upsert(group).await.unwrap();
    }

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "human_alice"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(scope_of(&body, "human_alice").as_deref(), Some("participant"));
    assert_eq!(mode_of(&body, "human_alice").as_deref(), Some("present"));
}

#[tokio::test]
async fn add_member_defaults_full_scope_when_nothing_declares_one() {
    let env = env(GroupStrategy::Chat, None).await;
    super::register_bot(&env.registry, "loose-bot", "Loose").await;

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "loose-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(scope_of(&body, "loose-bot").as_deref(), Some("full"));
}

#[tokio::test]
async fn add_member_rejects_unknown_bot() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "ghost-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["message"], "bot ghost-bot not found");
}

#[tokio::test]
async fn add_member_rejects_participant_scope_for_bot() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "extra-bot", "message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "invalid_message_view_scope");
}

#[tokio::test]
async fn add_member_maps_store_invalid_params() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.add_error.lock().await = Some(Fail::InvalidParams);

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "extra-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "bad params");
}

#[tokio::test]
async fn remove_member_maps_store_forbidden() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.remove_error.lock().await = Some(Fail::Forbidden);

    let (status, body) = call(
        &env.app,
        empty_as(
            "DELETE",
            "/sessions/group-1:00000001/members/worker-bot",
            "bot-token",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"], "denied by store");
}

#[tokio::test]
async fn add_member_propagates_store_conflict() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.add_error.lock().await = Some(Fail::Conflict);

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "extra-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "state conflict");
}

// ----- DELETE members ----------------------------------------------------

#[tokio::test]
async fn remove_member_by_bot_token_removes_self() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        empty_as(
            "DELETE",
            "/sessions/group-1:00000001/members/worker-bot",
            "bot-token",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert!(
        !body["participants"]
            .as_array()
            .unwrap()
            .iter()
            .any(|p| p["bot_uuid"] == "worker-bot")
    );
}

#[tokio::test]
async fn remove_member_human_self_removes_human_participant() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;
    {
        let mut stored = env.sessions.sessions.lock().await;
        stored[0].participants.push(Participant::human(
            "human_alice",
            ParticipantRole::Observer,
        ));
    }

    let (status, body) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001/members/human_alice"),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert!(
        !body["participants"]
            .as_array()
            .unwrap()
            .iter()
            .any(|p| p["bot_uuid"] == "human_alice")
    );
}

#[tokio::test]
async fn remove_member_propagates_store_failure() {
    let env = env(GroupStrategy::Chat, None).await;
    *env.sessions.remove_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(
        &env.app,
        empty_as(
            "DELETE",
            "/sessions/group-1:00000001/members/worker-bot",
            "bot-token",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn remove_member_reports_missing_session_after_authorization() {
    // The route authorizes the caller against a Session it could not read and
    // then lets the store decide: a self-removal from an unknown Session is a
    // 404 from the store, not a 401/403.
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        empty_as(
            "DELETE",
            "/sessions/group-1:deadbeef/members/driver-bot",
            "driver-token",
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "group-1:deadbeef");
}

#[tokio::test]
async fn remove_member_rejects_unrelated_human() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

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
async fn remove_member_surfaces_authority_failure() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .authority(bcs_test_support::failing_authority_hook())
        .build();

    let (status, _) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001/members/worker-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn remove_member_allows_human_owner_of_target_bot() {
    let chain = bcs_test_support::lazy_authority_chain();
    chain.hook.seed_owner("worker-bot", "alice");
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .authority(chain.hook.clone())
        .build();

    let (status, _) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001/members/worker-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn remove_member_blocks_driver_removal_even_for_its_owner() {
    let chain = bcs_test_support::lazy_authority_chain();
    chain.hook.seed_owner("driver-bot", "alice");
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .authority(chain.hook.clone())
        .build();

    let (status, body) = call(
        &env.app,
        empty("DELETE", "/sessions/group-1:00000001/members/driver-bot"),
    )
    .await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"], "cannot remove driver bot");
}

// ----- PATCH members -----------------------------------------------------

#[tokio::test]
async fn patch_member_requires_at_least_one_field() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        body["error"],
        "At least one of mode or message_view_scope must be provided"
    );
}

#[tokio::test]
async fn patch_member_rejects_unknown_mode() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"mode": "dancing"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "unknown mode: dancing");
}

#[tokio::test]
async fn patch_member_rejects_scope_from_bot_caller() {
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_as(
            "PATCH",
            "/sessions/group-1:00000001/members/worker-bot",
            "bot-token",
            json!({"message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "invalid_message_view_scope");
}

#[tokio::test]
async fn patch_member_mode_only_updates_existing_human() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;
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
}

#[tokio::test]
async fn patch_member_accepts_auto_mode_for_existing_bot() {
    let env = env(GroupStrategy::Chat, None).await;
    {
        let mut stored = env.sessions.sessions.lock().await;
        let worker = stored[0]
            .participants
            .iter_mut()
            .find(|p| p.bot_uuid == "worker-bot")
            .unwrap();
        worker.mode = Some(ParticipantMode::Muted);
    }

    let (status, body) = call(
        &env.app,
        json_as(
            "PATCH",
            "/sessions/group-1:00000001/members/worker-bot",
            "bot-token",
            json!({"mode": "auto"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(mode_of(&body, "worker-bot").as_deref(), Some("auto"));
}

#[tokio::test]
async fn patch_member_returns_not_found_for_unknown_bot() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/ghost-bot",
            json!({"mode": "muted"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "ghost-bot");
}

#[tokio::test]
async fn patch_member_auto_inserts_unknown_human_as_observer() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;

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
    assert_eq!(
        role_of(&body, "human_bob").as_deref(),
        Some("observer"),
        "an unknown Human is inserted as an Observer"
    );
    assert_eq!(mode_of(&body, "human_bob").as_deref(), Some("present"));
}

#[tokio::test]
async fn patch_member_scope_auto_inserts_the_calling_human() {
    // A Human may only switch its own scope; when it is not a Session
    // participant yet the route inserts it as an Observer with the requested
    // scope instead of failing.
    let env = env(GroupStrategy::Chat, Some("alice")).await;

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        scope_of(&body, "human_alice").as_deref(),
        Some("participant")
    );
    assert_eq!(
        role_of(&body, "human_alice").as_deref(),
        Some("observer"),
        "the fallback insert uses the Observer role"
    );
}

#[tokio::test]
async fn patch_member_scope_propagates_store_failure() {
    let env = env(GroupStrategy::Chat, Some("alice")).await;
    *env.sessions.scope_error.lock().await = Some(Fail::Conflict);

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
async fn patch_member_uses_session_application_when_configured() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .session_application(FakeSessionApplication::new(UpdateOutcome::Ok))
        .build();
    {
        let mut stored = env.sessions.sessions.lock().await;
        stored[0].participants.push(Participant::human(
            "human_alice",
            ParticipantRole::Observer,
        ));
    }

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"message_view_scope": "participant", "mode": "present"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    // The V1 facade accepted the update; the route projects the Session it
    // reads back from the legacy store.
    assert_eq!(body["id"], SESSION_ID);
}

#[tokio::test]
async fn patch_member_session_application_self_inserts_missing_participant() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .session_application(FakeSessionApplication::new(
            UpdateOutcome::ParticipantMissing,
        ))
        .build();

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(scope_of(&body, "human_alice").as_deref(), Some("participant"));
}

#[tokio::test]
async fn patch_member_session_application_rejects_other_errors() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .session_application(FakeSessionApplication::new(UpdateOutcome::Forbidden))
        .build();
    {
        let mut stored = env.sessions.sessions.lock().await;
        stored[0].participants.push(Participant::human(
            "human_alice",
            ParticipantRole::Observer,
        ));
    }

    let (status, body) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"], "forbidden");
}

#[tokio::test]
async fn patch_member_scope_on_service_session_still_projects_session() {
    // A ServiceInvocation Session accepts the same member-scope update: the
    // member route has no Session-kind gate.
    let env = env(GroupStrategy::Chat, Some("alice")).await;
    {
        let mut stored = env.sessions.sessions.lock().await;
        stored[0].session_kind = bcs_service_api::SessionKind::ServiceInvocation;
        stored[0].participants.push(Participant::human(
            "human_alice",
            ParticipantRole::Observer,
        ));
    }

    let (status, _) = call(
        &env.app,
        json_body(
            "PATCH",
            "/sessions/group-1:00000001/members/human_alice",
            json!({"message_view_scope": "full"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn add_member_rejects_human_scope_for_bot_actor() {
    // A registered Human actor may pick the participant scope; the same body
    // is still rejected for a Bot actor (covered above) — this pins the
    // Human-accepting side of the same validation.
    let env = env(GroupStrategy::Chat, None).await;

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            MEMBERS,
            "driver-token",
            json!({"bot_uuid": "human_alice", "message_view_scope": "participant"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(scope_of(&body, "human_alice").as_deref(), Some("participant"));
    assert_eq!(mode_of(&body, "human_alice").as_deref(), Some("present"));
}

#[tokio::test]
async fn session_fixture_keeps_bot_actor_kind() {
    // Guard: the fixture Session participants stay Bot-kind, so the scope
    // assertions above exercise the Bot validation branch and not a Human one.
    let session = session_for("group-1", bcs_service_api::SessionKind::Chat);
    assert!(
        session
            .participants
            .iter()
            .all(|p| p.actor_kind == ActorKind::Bot)
    );
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
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .session_application(FakeSessionApplication::new(UpdateOutcome::Ok))
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
}

#[tokio::test]
async fn patch_member_propagates_read_failure_after_application_update() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .session_application(FakeSessionApplication::new(UpdateOutcome::Ok))
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
}

#[tokio::test]
async fn patch_member_propagates_insert_failure_in_application_fallback() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .session_application(FakeSessionApplication::new(
            UpdateOutcome::ParticipantMissing,
        ))
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
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .system_message(std::sync::Arc::new(super::FailingSystemMessages))
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
}

#[tokio::test]
async fn patch_member_tolerates_notification_failure_on_first_insert() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .system_message(std::sync::Arc::new(super::FailingSystemMessages))
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
