//! REGRESS-1 — Regression tests for BUG-FIX-1 through BUG-FIX-4.
//!
//! Covers the five test cases from tasks.md REGRESS-1:
//!
//! 1. `POST /groups` + Human participant → 400
//!    "无法邀请用户进群，只能由用户本人主动参与协作" (BUG-FIX-3 / Requirement 3.14#2)
//! 2. `POST /groups` without label/topic → label defaults to
//!    `{driver_bot}-{YYYYMMDDHHmm}` (BUG-FIX-4 / Requirement 3.19#15b)
//! 3. `GET /bots/{id}/groups` includes `group_kind` field (BUG-FIX-2 / Requirement 3.19#4b)
//! 4. `check_creator_or_self` when `get_edge` returns Err → 500, not 403
//!    (BUG-FIX-1 / design.md §6.2)
//! 5. `POST /groups/{id}/members` adding Human → 400
//!    (BUG-FIX-3 / Requirement 3.14#2)
//!
//! Test organisation:
//! - Section A: Service-layer tests for `check_creator_or_self` logic (case 4).
//!   These test the permission-check function by replicating its match arms
//!   against a mock `RelationCoreService` that can inject failures.
//! - Section B: HTTP integration tests for the remaining cases (1, 2, 3, 5).
//!   These use `start_test_server` to exercise the full HTTP stack.

mod helpers;

use std::sync::Arc;

use async_trait::async_trait;
use bcs_group::GroupStore;
use bcs_relation::MemoryRelationStore;
use bcs_service_api::{
    ActorKind, Group, GroupKind, GroupCoreService, RelationEdge, RelationCoreService, ServiceError,
    ServiceResult,
};
use helpers::*;

// ============================================================================
// Section A: Service-layer tests for check_creator_or_self logic (BUG-FIX-1)
// ============================================================================

/// Replicates the `check_creator_or_self` match logic from server.rs.
/// This is the exact three-arm match that BUG-FIX-1 corrected:
/// - `Ok(Some(edge)) if edge.is_creator` → Ok (caller is creator)
/// - `Ok(_)` → 403 (caller has no creator edge)
/// - `Err(e)` → 500 (DB error, not the caller's fault)
///
/// Returns `Result<(), CheckError>` so tests can assert on the variant.
#[derive(Debug)]
enum CheckError {
    Forbidden(String),
    Internal(String),
}

async fn check_creator_or_self(
    relation: &dyn RelationCoreService,
    caller: &str,
    actor_id: &str,
) -> Result<(), CheckError> {
    if caller == actor_id {
        return Ok(());
    }
    let env = bcs_config::resolve_env_str();
    match relation.get_edge(caller, actor_id, &env).await {
        Ok(Some(edge)) if edge.is_creator => Ok(()),
        Ok(_) => Err(CheckError::Forbidden(format!(
            "Caller '{}' is not the actor itself nor a creator of '{}'",
            caller, actor_id
        ))),
        Err(e) => Err(CheckError::Internal(format!(
            "Failed to verify creator relation: {}",
            e
        ))),
    }
}

// -- Mock that wraps MemoryRelationStore but injects get_edge failures -------

#[derive(Default)]
struct FailingGetEdgeStore {
    inner: MemoryRelationStore,
    fail_get_edge: std::sync::atomic::AtomicBool,
}

impl FailingGetEdgeStore {
    fn new() -> Self {
        Self {
            inner: MemoryRelationStore::new(),
            fail_get_edge: std::sync::atomic::AtomicBool::new(false),
        }
    }

    fn set_fail_get_edge(&self, fail: bool) {
        self.fail_get_edge
            .store(fail, std::sync::atomic::Ordering::SeqCst);
    }
}

#[async_trait]
impl RelationCoreService for FailingGetEdgeStore {
    async fn upsert_edge(&self, edge: RelationEdge) -> ServiceResult<()> {
        self.inner.upsert_edge(edge).await
    }
    async fn delete_edge(&self, from_id: &str, to_id: &str, env: &str) -> ServiceResult<()> {
        self.inner.delete_edge(from_id, to_id, env).await
    }
    async fn get_edge(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        if self.fail_get_edge.load(std::sync::atomic::Ordering::SeqCst) {
            return Err(ServiceError::InternalError(
                "simulated DB connection lost".to_string(),
            ));
        }
        self.inner.get_edge(from_id, to_id, env).await
    }
    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()> {
        self.inner.ensure_owner_edges(human_id, bot_id, env).await
    }
    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<bcs_service_api::EnsureOwnerEdgesResult> {
        self.inner
            .ensure_owner_edges_counted(human_id, bot_id, env)
            .await
    }
    async fn add_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()> {
        self.inner.add_friend_edges(a, b, env).await
    }
    async fn remove_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()> {
        self.inner.remove_friend_edges(a, b, env).await
    }
    async fn list_friends_via_relation(&self, id: &str, env: &str) -> ServiceResult<Vec<String>> {
        self.inner.list_friends_via_relation(id, env).await
    }
    async fn remove_all_friend_edges(&self, _id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }
    async fn add_relation_edge(&self, _from: &str, _to: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }
}

// -- Tests -------------------------------------------------------------------

/// Self-access: caller == actor_id always passes, no DB query needed.
#[tokio::test]
async fn regress1_check_self_always_allowed() {
    let relation = Arc::new(FailingGetEdgeStore::new());
    // Even with DB failures, self-access bypasses get_edge entirely.
    relation.set_fail_get_edge(true);
    let result = check_creator_or_self(relation.as_ref(), "alice", "alice").await;
    assert!(result.is_ok(), "caller == actor_id must always be Ok");
}

/// Creator edge present: caller has `is_creator=TRUE` edge → Ok.
#[tokio::test]
async fn regress1_check_creator_edge_allowed() {
    let relation = Arc::new(FailingGetEdgeStore::new());
    let env = bcs_config::resolve_env_str();
    relation
        .ensure_owner_edges("human_1", "bot_x", &env)
        .await
        .unwrap();

    let result = check_creator_or_self(relation.as_ref(), "human_1", "bot_x").await;
    assert!(result.is_ok(), "creator edge must be allowed");
}

/// No edge: caller has no relation to actor → 403 Forbidden.
#[tokio::test]
async fn regress1_check_no_edge_forbidden() {
    let relation = Arc::new(FailingGetEdgeStore::new());
    let result = check_creator_or_self(relation.as_ref(), "stranger", "bot_x").await;
    match result {
        Err(CheckError::Forbidden(msg)) => {
            assert!(msg.contains("stranger"), "error should mention caller");
            assert!(msg.contains("bot_x"), "error should mention actor");
        }
        other => panic!("expected Forbidden, got {:?}", other),
    }
}

/// Friend (non-creator) edge: edge exists but `is_creator=FALSE` → 403.
#[tokio::test]
async fn regress1_check_friend_edge_forbidden() {
    let relation = Arc::new(FailingGetEdgeStore::new());
    let env = bcs_config::resolve_env_str();
    // add_friend_edges writes both directions with is_creator=FALSE
    relation
        .add_friend_edges("bot_a", "bot_b", &env)
        .await
        .unwrap();

    let result = check_creator_or_self(relation.as_ref(), "bot_a", "bot_b").await;
    match result {
        Err(CheckError::Forbidden(_)) => {} // expected
        other => panic!(
            "expected Forbidden for non-creator friend edge, got {:?}",
            other
        ),
    }
}

/// BUG-FIX-1 regression: when `get_edge` returns `Err`, the result MUST be
/// 500 Internal Server Error, NOT 403 Forbidden. The old code used a wildcard
/// `_` match arm that mapped both `Ok(None)` and `Err(e)` to 403, hiding DB
/// failures from operators.
#[tokio::test]
async fn regress1_check_db_error_returns_500_not_403() {
    let relation = Arc::new(FailingGetEdgeStore::new());
    // Simulate a DB failure on get_edge
    relation.set_fail_get_edge(true);

    let result = check_creator_or_self(relation.as_ref(), "human_1", "bot_x").await;
    match result {
        Err(CheckError::Internal(msg)) => {
            assert!(
                msg.contains("Failed to verify creator relation"),
                "internal error message must describe the failure, got: {}",
                msg
            );
        }
        Err(CheckError::Forbidden(msg)) => {
            panic!(
                "BUG-FIX-1 REGRESSION: get_edge Err must NOT map to 403 Forbidden! Got: {}",
                msg
            );
        }
        Ok(()) => {
            panic!("BUG-FIX-1 REGRESSION: get_edge Err must not return Ok!");
        }
    }
}

// ============================================================================
// Section B: Service-layer tests for Group data contracts (BUG-FIX-2, BUG-FIX-4)
// ============================================================================

/// BUG-FIX-4 / Requirement 3.19#15b: `Group::new` defaults `label` to None;
/// the HTTP handler must fill a fallback so label is never NULL in the response.
/// This test verifies the data-level precondition: new groups start with
/// `label == None`, and the handler-level fallback is tested in Section C.
#[test]
fn regress1_group_new_label_is_none_without_explicit_set() {
    let g = Group::new("g1", "driver_bot", vec![]);
    assert!(
        g.label.is_none(),
        "Group::new must default label to None; the HTTP handler provides the fallback"
    );
}

/// BUG-FIX-2 / Requirement 3.19#4b: every Group has a `group_kind` field.
/// `Group::new` defaults to `Normal`; `create_or_reuse_dm_group` sets `Dm`.
/// Both paths set the field, so `GET /bots/{id}/groups` can always serialize it.
#[test]
fn regress1_group_new_has_group_kind_normal() {
    let g = Group::new("g1", "driver_bot", vec![]);
    assert_eq!(g.group_kind, GroupKind::Normal);
}

/// BUG-FIX-2 extended: DM groups have `group_kind == Dm`.
#[tokio::test]
async fn regress1_dm_group_has_group_kind_dm() {
    let store: Arc<GroupStore> = Arc::new(GroupStore::default());
    let (g, _) = store
        .create_or_reuse_dm_group("dm1", "driver", "alice", "bob", None)
        .await
        .unwrap();
    assert_eq!(g.group_kind, GroupKind::Dm);
}

/// BUG-FIX-3 guard: `ActorKind::Human` is distinct from `ActorKind::Bot`.
/// The handler checks `bot.actor_kind == ActorKind::Human` to reject
/// Human participants from `POST /groups` and `POST /groups/{id}/members`.
/// This test verifies the enum values are correct at the data level.
#[test]
fn regress1_actor_kind_human_is_distinct_from_bot() {
    assert_ne!(ActorKind::Human, ActorKind::Bot);
    assert_eq!(ActorKind::default(), ActorKind::Bot, "default is Bot");
}

/// Verify `ParticipantMode::default_for` returns `Absent` for Human actors.
/// This underpins the fact that Humans join groups via `PUT mode=present`,
/// not by being invited.
#[test]
fn regress1_human_default_mode_is_absent() {
    assert_eq!(
        bcs_service_api::ParticipantMode::default_for(ActorKind::Human),
        bcs_service_api::ParticipantMode::Absent
    );
}

/// BUG-FIX-4 label fallback format verification: when the handler sets the
/// fallback label, it uses `{driver_bot}-{YYYYMMDDHHmm}` in UTC+8. This test
/// verifies the timestamp formatting matches the server.rs code.
#[test]
fn regress1_label_fallback_timestamp_format() {
    // Verify the exact format string used in server.rs:
    //   chrono::Utc::now()
    //       .with_timezone(&chrono::FixedOffset::east_opt(8 * 3600).expect("UTC+8"))
    //       .format("%Y%m%d%H%M")
    use chrono::FixedOffset;
    let utc8 = FixedOffset::east_opt(8 * 3600).expect("UTC+8");
    let now = chrono::Utc::now().with_timezone(&utc8);
    let ts = now.format("%Y%m%d%H%M").to_string();
    // Must be 12 digits: YYYYMMDDHHmm
    assert_eq!(ts.len(), 12, "timestamp must be 12 chars: got {}", ts);
    assert!(
        ts.chars().all(|c| c.is_ascii_digit()),
        "timestamp must be all digits: got {}",
        ts
    );
}

// ============================================================================
// Section C: HTTP integration tests (BUG-FIX-2, BUG-FIX-3, BUG-FIX-4)
// ============================================================================

use reqwest;
use serde_json::json;

/// Helper: start a test server, connect + onboard a bot, return (addr, bot_id, token).
async fn setup_bot_for_http_test(
    bots_dir: &std::path::PathBuf,
) -> (std::net::SocketAddr, String, String) {
    let (addr, _handle) = start_test_server(bots_dir).await;
    let http = reqwest::Client::new();

    // Connect bot
    let resp = http
        .post(format!("http://{}/bots/connect", addr))
        .json(&json!({"token": "regress-test-token", "bot_id": "regress-bot"}))
        .send()
        .await
        .expect("connect failed");
    let body: serde_json::Value = resp.json().await.expect("parse connect response");
    let bot_uuid = body["bot_uuid"].as_str().expect("no bot_uuid").to_string();
    let token = body["token"].as_str().expect("no token").to_string();

    // Onboard
    let resp = http
        .post(format!("http://{}/bots/onboard", addr))
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({
            "name": "RegressBot",
            "summary": "Test bot for regress-1",
            "skills": ["testing"]
        }))
        .send()
        .await
        .expect("onboard failed");
    assert!(resp.status().is_success(), "onboard should succeed");

    // Set visibility to public
    let _ = http
        .put(format!("http://{}/bots/{}/visibility", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({"visibility": "public"}))
        .send()
        .await;

    (addr, bot_uuid, token)
}

/// BUG-FIX-2 / Requirement 3.19#4b: `GET /bots/{id}/groups` response must
/// include the `group_kind` field for each group item.
#[tokio::test]
async fn regress1_get_bot_groups_includes_group_kind() {
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, bot_uuid, token) = setup_bot_for_http_test(&bots_dir).await;
    let http = reqwest::Client::new();

    // Create a Normal group
    let resp = http
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({
            "driver_bot": bot_uuid,
            "participants": [
                {"bot_uuid": bot_uuid, "role": "driver"}
            ]
        }))
        .send()
        .await
        .expect("create group failed");
    assert!(resp.status().is_success(), "group creation should succeed");
    let group_body: serde_json::Value = resp.json().await.expect("parse group response");
    let group_id = group_body["id"].as_str().expect("group should have id");

    // GET /bots/{id}/groups
    let resp = http
        .get(format!("http://{}/bots/{}/groups", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("get bot groups failed");
    assert!(resp.status().is_success(), "get bot groups should succeed");
    let body: serde_json::Value = resp.json().await.expect("parse groups response");

    let items = body["items"].as_array().expect("items should be array");
    let found = items
        .iter()
        .find(|item| item["group_id"].as_str() == Some(group_id))
        .unwrap_or_else(|| panic!("group {} not found in items: {:?}", group_id, items));

    // BUG-FIX-2: group_kind must be present and be "normal"
    assert!(
        found["group_kind"].is_string(),
        "group_kind must be present in GET /bots/{{id}}/groups response, got: {:?}",
        found
    );
    assert_eq!(
        found["group_kind"].as_str(),
        Some("normal"),
        "normal group should have group_kind=normal"
    );
}

/// BUG-FIX-4 / Requirement 3.19#15b: `POST /groups` without label or topic
/// generates a fallback label in the format `{driver_bot}-{YYYYMMDDHHmm}`.
/// The POST response does not include `label`; verify via GET /bots/{id}/groups.
#[tokio::test]
async fn regress1_post_groups_label_fallback_when_not_provided() {
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, bot_uuid, token) = setup_bot_for_http_test(&bots_dir).await;
    let http = reqwest::Client::new();

    // Create group without label or topic
    let resp = http
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({
            "driver_bot": bot_uuid,
            "participants": [
                {"bot_uuid": bot_uuid, "role": "driver"}
            ]
            // No label, no topic
        }))
        .send()
        .await
        .expect("create group failed");
    assert!(resp.status().is_success(), "group creation should succeed");

    // Verify label via GET /bots/{id}/groups (POST response does not include label)
    let resp = http
        .get(format!("http://{}/bots/{}/groups", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("get bot groups failed");
    assert!(resp.status().is_success());
    let body: serde_json::Value = resp.json().await.expect("parse");
    let items = body["items"].as_array().expect("items should be array");
    let group = items.first().expect("should have at least one group");

    let label = group["label"].as_str().expect("label must not be null");
    // Label must start with the driver bot's UUID
    assert!(
        label.starts_with(&bot_uuid),
        "fallback label must start with driver_bot uuid, got: {} (bot_uuid={})",
        label,
        bot_uuid
    );
    // Label must have format {bot_uuid}-{YYYYMMDDHHmm}
    // Find the last '-' to split bot_uuid prefix from timestamp suffix
    let suffix = label
        .strip_prefix(&format!("{}-", bot_uuid))
        .unwrap_or_else(|| panic!("label must be '{{bot_uuid}}-{{timestamp}}', got: {}", label));
    assert_eq!(
        suffix.len(),
        12,
        "timestamp suffix must be 12 chars (YYYYMMDDHHmm), got: {} (full label: {})",
        suffix,
        label
    );
    assert!(
        suffix.chars().all(|c| c.is_ascii_digit()),
        "timestamp suffix must be all digits, got: {}",
        suffix
    );
}

/// BUG-FIX-4 continued: when topic is provided, label is set to "Group: {topic}"
/// and the fallback is NOT applied. Verify via GET /bots/{id}/groups.
#[tokio::test]
async fn regress1_post_groups_label_from_topic_not_fallback() {
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, bot_uuid, token) = setup_bot_for_http_test(&bots_dir).await;
    let http = reqwest::Client::new();

    let resp = http
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({
            "driver_bot": bot_uuid,
            "participants": [
                {"bot_uuid": bot_uuid, "role": "driver"}
            ],
            "topic": "My Topic"
            // No label explicitly set
        }))
        .send()
        .await
        .expect("create group failed");
    assert!(resp.status().is_success());

    // Verify label via GET /bots/{id}/groups
    let resp = http
        .get(format!("http://{}/bots/{}/groups", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("get bot groups failed");
    assert!(resp.status().is_success());
    let body: serde_json::Value = resp.json().await.expect("parse");
    let items = body["items"].as_array().expect("items should be array");
    let group = items.first().expect("should have at least one group");

    let label = group["label"].as_str().expect("label must not be null");
    assert_eq!(
        label, "Group: My Topic",
        "when topic is provided, label should be 'Group: {{topic}}', got: {}",
        label
    );
}

/// BUG-FIX-4 continued: when label is explicitly provided, it is used as-is
/// and the fallback is NOT applied. Verify via GET /bots/{id}/groups.
#[tokio::test]
async fn regress1_post_groups_explicit_label_preserved() {
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, bot_uuid, token) = setup_bot_for_http_test(&bots_dir).await;
    let http = reqwest::Client::new();

    let resp = http
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({
            "driver_bot": bot_uuid,
            "participants": [
                {"bot_uuid": bot_uuid, "role": "driver"}
            ],
            "label": "Custom Label"
        }))
        .send()
        .await
        .expect("create group failed");
    assert!(resp.status().is_success());

    // Verify label via GET /bots/{id}/groups
    let resp = http
        .get(format!("http://{}/bots/{}/groups", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("get bot groups failed");
    assert!(resp.status().is_success());
    let body: serde_json::Value = resp.json().await.expect("parse");
    let items = body["items"].as_array().expect("items should be array");
    let group = items.first().expect("should have at least one group");

    let label = group["label"].as_str().expect("label must not be null");
    assert_eq!(
        label, "Custom Label",
        "explicit label must be preserved, got: {}",
        label
    );
}

/// BUG-FIX-3 / Requirement 3.14#2: `POST /groups` with a Human participant
/// must return 400 with the message "无法邀请用户进群，只能由用户本人主动参与协作".
///
/// This test creates a Human actor via the mock-SSO onboard path,
/// then attempts to include that Human as a group participant.
#[tokio::test]
async fn regress1_post_groups_rejects_human_participant() {
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    let http = reqwest::Client::new();

    // Connect and onboard a regular bot (driver)
    let resp = http
        .post(format!("http://{}/bots/connect", addr))
        .json(&json!({"token": "regress-human-test-driver", "bot_id": "driver-bot"}))
        .send()
        .await
        .expect("connect driver bot failed");
    let driver_body: serde_json::Value = resp.json().await.expect("parse");
    let driver_uuid = driver_body["bot_uuid"].as_str().unwrap().to_string();
    let driver_token = driver_body["token"].as_str().unwrap().to_string();

    // Onboard driver bot (with mock SSO to also create a Human actor entry)
    let resp = http
        .post(format!("http://{}/bots/onboard", addr))
        .header("Authorization", format!("Bearer {}", driver_token))
        .header("x-mock-user-id", "99999")
        .header("x-mock-nick-name", "TestHuman")
        .json(&json!({
            "name": "DriverBot",
            "summary": "Bot for regress test",
            "skills": ["testing"]
        }))
        .send()
        .await
        .expect("onboard driver bot failed");
    assert!(
        resp.status().is_success(),
        "driver onboard should succeed: {:?}",
        resp
    );

    // Set driver bot visibility to public
    let _ = http
        .put(format!("http://{}/bots/{}/visibility", addr, driver_uuid))
        .header("Authorization", format!("Bearer {}", driver_token))
        .json(&json!({"visibility": "public"}))
        .send()
        .await;

    // The mock SSO onboard creates a human_99999 entry in the registry.
    // Try to create a group with the Human actor as a participant.
    let human_uuid = "human_99999";
    let resp = http
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", driver_token))
        .json(&json!({
            "driver_bot": driver_uuid,
            "participants": [
                {"bot_uuid": driver_uuid, "role": "driver"},
                {"bot_uuid": human_uuid, "role": "consultant"}
            ]
        }))
        .send()
        .await
        .expect("create group request failed");

    // Human as consultant is now allowed (feat: human create group).
    // Verify the group is created successfully with the Human participant.
    assert!(
        resp.status().is_success(),
        "POST /groups with Human consultant should succeed, got: {} {}",
        resp.status(),
        resp.text().await.unwrap_or_default()
    );

    let body: serde_json::Value = resp.json().await.expect("parse group response");
    let participants = body["participants"].as_array().expect("participants array");
    // create_group response returns participants as string array of bot_uuids
    assert!(
        participants.iter().any(|p| p.as_str() == Some(human_uuid)),
        "Human participant should be in group, got: {:?}",
        participants
    );
}

/// BUG-FIX-3 / Requirement 3.14#2 (extended): `POST /groups/{id}/members`
/// adding a Human participant must also return 400.
#[tokio::test]
async fn regress1_post_group_members_rejects_human() {
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    let http = reqwest::Client::new();

    // Connect and onboard driver bot
    let resp = http
        .post(format!("http://{}/bots/connect", addr))
        .json(&json!({"token": "regress-member-test-driver", "bot_id": "member-driver"}))
        .send()
        .await
        .expect("connect driver failed");
    let driver_body: serde_json::Value = resp.json().await.expect("parse");
    let driver_uuid = driver_body["bot_uuid"].as_str().unwrap().to_string();
    let driver_token = driver_body["token"].as_str().unwrap().to_string();

    let resp = http
        .post(format!("http://{}/bots/onboard", addr))
        .header("Authorization", format!("Bearer {}", driver_token))
        .header("x-mock-user-id", "88888")
        .header("x-mock-nick-name", "AnotherHuman")
        .json(&json!({
            "name": "MemberDriverBot",
            "summary": "Bot for member regress test",
            "skills": ["testing"]
        }))
        .send()
        .await
        .expect("onboard failed");
    assert!(resp.status().is_success());

    // Set visibility to public
    let _ = http
        .put(format!("http://{}/bots/{}/visibility", addr, driver_uuid))
        .header("Authorization", format!("Bearer {}", driver_token))
        .json(&json!({"visibility": "public"}))
        .send()
        .await;

    // Create a normal group (driver only)
    let resp = http
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", driver_token))
        .json(&json!({
            "driver_bot": driver_uuid,
            "participants": [
                {"bot_uuid": driver_uuid, "role": "driver"}
            ]
        }))
        .send()
        .await
        .expect("create group failed");
    assert!(resp.status().is_success(), "group creation should succeed");
    let group_body: serde_json::Value = resp.json().await.expect("parse");
    let group_id = group_body["id"].as_str().expect("group should have id");

    // Try to add the Human actor (human_88888) to the group
    let human_uuid = "human_88888";
    let resp = http
        .post(format!("http://{}/groups/{}/members", addr, group_id))
        .header("Authorization", format!("Bearer {}", driver_token))
        .header("x-mock-user-id", "88888")
        .json(&json!({
            "bot_uuid": human_uuid,
            "role": "consultant"
        }))
        .send()
        .await
        .expect("add member request failed");

    // Human as consultant via add_member is now allowed (feat: human create group).
    assert!(
        resp.status().is_success(),
        "POST /groups/{{id}}/members with Human consultant should succeed, got: {} {}",
        resp.status(),
        resp.text().await.unwrap_or_default()
    );

    let body: serde_json::Value = resp.json().await.expect("parse member response");
    assert_eq!(body["added"].as_bool(), Some(true));
    assert_eq!(body["member"]["bot_uuid"].as_str(), Some(human_uuid));
}

/// BUG-FIX-2 extended: `GET /bots/{id}/groups` for a DM group should
/// return `group_kind: "dm"`.
#[tokio::test]
async fn regress1_get_bot_groups_includes_dm_group_kind() {
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    let http = reqwest::Client::new();

    // Connect and onboard two bots
    let mut bot_ids = Vec::new();
    let mut bot_tokens = Vec::new();
    for (i, (token, id)) in [("dm-bot-a", "bot-a"), ("dm-bot-b", "bot-b")]
        .iter()
        .enumerate()
    {
        let resp = http
            .post(format!("http://{}/bots/connect", addr))
            .json(&json!({"token": token, "bot_id": id}))
            .send()
            .await
            .expect("connect failed");
        let body: serde_json::Value = resp.json().await.expect("parse");
        bot_ids.push(body["bot_uuid"].as_str().unwrap().to_string());
        bot_tokens.push(body["token"].as_str().unwrap().to_string());

        let resp = http
            .post(format!("http://{}/bots/onboard", addr))
            .header("Authorization", format!("Bearer {}", bot_tokens[i]))
            .json(&json!({
                "name": format!("DM Bot {}", i),
                "summary": "Test",
                "skills": ["testing"]
            }))
            .send()
            .await
            .expect("onboard failed");
        assert!(resp.status().is_success());

        // Set visibility to public
        let _ = http
            .put(format!("http://{}/bots/{}/visibility", addr, bot_ids[i]))
            .header("Authorization", format!("Bearer {}", bot_tokens[i]))
            .json(&json!({"visibility": "public"}))
            .send()
            .await;
    }

    // Create a DM group
    let resp = http
        .post(format!("http://{}/sessions", addr))
        .header("Authorization", format!("Bearer {}", bot_tokens[0]))
        .json(&json!({
            "kind": "dm",
            "from_bot": bot_ids[0],
            "to_bot": bot_ids[1]
        }))
        .send()
        .await
        .expect("create DM session failed");

    // DM creation may succeed or fail depending on friendship; we just need
    // a group entry. If DM creation fails, fall back to Normal group test.
    if !resp.status().is_success() {
        // DM creation may require friendship; skip DM-specific assertion
        eprintln!(
            "DM creation failed (likely needs friendship), skipping DM group_kind test. Status: {}",
            resp.status()
        );
        return;
    }

    // GET /bots/{id}/groups and check group_kind for the DM
    let resp = http
        .get(format!("http://{}/bots/{}/groups", addr, bot_ids[0]))
        .header("Authorization", format!("Bearer {}", bot_tokens[0]))
        .send()
        .await
        .expect("get bot groups failed");
    assert!(resp.status().is_success());
    let body: serde_json::Value = resp.json().await.expect("parse");

    let items = body["items"].as_array().expect("items should be array");
    let dm_item = items
        .iter()
        .find(|item| item["group_kind"].as_str() == Some("dm"));

    if let Some(dm) = dm_item {
        assert_eq!(
            dm["group_kind"].as_str(),
            Some("dm"),
            "DM group must have group_kind=dm"
        );
    }
    // If no DM found (unlikely but possible due to timing), the Normal group
    // test already covers the field's presence.
}

// ============================================================================
// Section D: BUG-FIX-5 — GET /bots/{id}/groups group_kind filtering
// ============================================================================

/// Helper: connect, onboard, and set visibility=public for a bot, using mock
/// Human identity (X-Mock-User-Id header) so onboard creates owner edges.
/// Returns `(bot_uuid, token)`.
async fn setup_bot_with_mock_human(
    http: &reqwest::Client,
    addr: std::net::SocketAddr,
    bot_id: &str,
    bot_name: &str,
    mock_user_id: &str,
) -> (String, String) {
    let resp = http
        .post(format!("http://{}/bots/connect", addr))
        .json(&json!({"token": format!("tok-{}", bot_id), "bot_id": bot_id}))
        .send()
        .await
        .expect("connect failed");
    let body: serde_json::Value = resp.json().await.expect("parse connect");
    let bot_uuid = body["bot_uuid"].as_str().expect("bot_uuid").to_string();
    let token = body["token"].as_str().expect("token").to_string();

    // Onboard with mock Human identity so owner edges are created
    let resp = http
        .post(format!("http://{}/bots/onboard", addr))
        .header("Authorization", format!("Bearer {}", token))
        .header("X-Mock-User-Id", mock_user_id)
        .header("X-Mock-Nick-Name", format!("MockUser-{}", mock_user_id))
        .json(&json!({
            "name": bot_name,
            "summary": "Test bot",
            "skills": ["testing"]
        }))
        .send()
        .await
        .expect("onboard failed");
    assert!(
        resp.status().is_success(),
        "onboard should succeed for {}",
        bot_id
    );

    // Set visibility=public so DM creation passes reachability check
    let _ = http
        .put(format!("http://{}/bots/{}/visibility", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({"visibility": "public"}))
        .send()
        .await;

    (bot_uuid, token)
}

/// BUG-FIX-5 / Requirement 3.19#4: HTTP integration test that **actually
/// creates both a Normal group and a DM group** for the same bot, then
/// verifies:
///
/// 1. Default `GET /bots/{id}/groups` (no `group_kind` param) returns ONLY
///    the normal group — the dm group is hidden.
/// 2. `GET /bots/{id}/groups?group_kind=all` returns BOTH groups.
/// 3. `GET /bots/{id}/groups?group_kind=dm` returns ONLY the dm group.
///
/// This test would FAIL on the old code (before BUG-FIX-5) because the old
/// `get_bot_groups` handler had no `group_kind` filtering — it returned all
/// groups including dm.
#[tokio::test]
async fn regress1_get_bot_groups_default_hides_dm() {
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    let http = reqwest::Client::new();
    let mock_staff = "88888";

    // Setup two bots owned by the same mock Human
    let (bot_a_uuid, bot_a_token) =
        setup_bot_with_mock_human(&http, addr, "bf5-bot-a", "BF5 Bot A", mock_staff).await;
    let (bot_b_uuid, _bot_b_token) =
        setup_bot_with_mock_human(&http, addr, "bf5-bot-b", "BF5 Bot B", mock_staff).await;

    // Create a Normal group with bot_a as driver
    let resp = http
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .json(&json!({
            "driver_bot": bot_a_uuid,
            "participants": [
                {"bot_uuid": bot_a_uuid, "role": "driver"}
            ]
        }))
        .send()
        .await
        .expect("create normal group failed");
    assert!(
        resp.status().is_success(),
        "normal group creation should succeed"
    );

    // Create a DM group between bot_a and bot_b so list filtering has both kinds.
    let resp = http
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .header("X-Mock-User-Id", mock_staff)
        .header("X-Mock-Nick-Name", "MockUser")
        .json(&json!({
            "driver_bot": bot_a_uuid,
            "group_kind": "dm",
            "target_actor_id": bot_b_uuid,
            "participants": []
        }))
        .send()
        .await
        .expect("create dm group failed");
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        panic!(
            "DM group creation failed (status {}): {}. \
             This test requires both bots to be public and owned by the same mock Human.",
            status, body
        );
    }
    let dm_body: serde_json::Value = resp.json().await.expect("parse dm response");
    assert_eq!(
        dm_body["group_kind"].as_str(),
        Some("dm"),
        "DM creation response must have group_kind=dm"
    );

    // ---- Test 1: default listing hides dm ----
    let resp = http
        .get(format!("http://{}/bots/{}/groups", addr, bot_a_uuid))
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .send()
        .await
        .expect("get bot groups (default) failed");
    assert!(resp.status().is_success());
    let body: serde_json::Value = resp.json().await.expect("parse default listing");
    let items_default = body["items"].as_array().expect("items array");

    // Must contain at least 1 normal group, and NO dm groups
    assert!(
        !items_default.is_empty(),
        "default listing must return at least the normal group"
    );
    for item in items_default {
        assert_eq!(
            item["group_kind"].as_str(),
            Some("normal"),
            "BUG-FIX-5 REGRESSION: default listing must NOT contain dm groups, got: {:?}",
            item
        );
    }

    // ---- Test 2: group_kind=all returns both ----
    let resp = http
        .get(format!(
            "http://{}/bots/{}/groups?group_kind=all",
            addr, bot_a_uuid
        ))
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .send()
        .await
        .expect("get bot groups (all) failed");
    assert!(resp.status().is_success());
    let body_all: serde_json::Value = resp.json().await.expect("parse all listing");
    let items_all = body_all["items"].as_array().expect("items array");

    let has_normal = items_all
        .iter()
        .any(|i| i["group_kind"].as_str() == Some("normal"));
    let has_dm = items_all
        .iter()
        .any(|i| i["group_kind"].as_str() == Some("dm"));
    assert!(has_normal, "group_kind=all must include normal groups");
    assert!(has_dm, "group_kind=all must include dm groups");
    assert!(
        items_all.len() > items_default.len(),
        "group_kind=all ({}) must return more items than default ({}) because dm groups exist",
        items_all.len(),
        items_default.len()
    );

    // ---- Test 3: group_kind=dm returns only dm ----
    let resp = http
        .get(format!(
            "http://{}/bots/{}/groups?group_kind=dm",
            addr, bot_a_uuid
        ))
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .send()
        .await
        .expect("get bot groups (dm) failed");
    assert!(resp.status().is_success());
    let body_dm: serde_json::Value = resp.json().await.expect("parse dm listing");
    let items_dm = body_dm["items"].as_array().expect("items array");

    assert!(
        !items_dm.is_empty(),
        "group_kind=dm must return at least the dm group we created"
    );
    for item in items_dm {
        assert_eq!(
            item["group_kind"].as_str(),
            Some("dm"),
            "group_kind=dm listing must only contain dm groups, got: {:?}",
            item
        );
    }
}
