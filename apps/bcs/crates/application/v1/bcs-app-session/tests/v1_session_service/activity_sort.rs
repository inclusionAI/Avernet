//! Recent-activity sort and `last_activity_at` projection for the V1 session
//! list (design D1/D4/D5/D6): two population paths, one identical field
//! value, viewer scoping first.
//!
//! The session-level D1 activity key is `MAX(bcs_messages.created_at)` per
//! session with the session's OWN `created_at` fallback; it never merges the
//! session-creation artifact the group level uses (a session cannot be later
//! than its own creation, so the fallback already covers the empty case).

use super::*;
use bcs_service_api::application::v1::{ApplicationError, Page, SessionListSort, SessionSummary};
use bcs_service_api::types::bot_profile::{AvatarChange, ProfileWrite};
use bcs_service_api::ServiceError;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};

/// Message timestamps far after every session `created_at` (~now), so an
/// accidental fallback to `created_at` can never pass the value assertions.
const MESSAGE_TIME: u64 = 5_000_000_000_000;

/// The session ids below are explicit (`g1:{8 hex}`) so the `session_id ASC`
/// tie-break is deterministic while `created_at` is still assigned by the
/// repository clock.
const S_A: &str = "g1:0000000a";
const S_B: &str = "g1:0000000b";
const S_C: &str = "g1:0000000c";
const S_D: &str = "g1:0000000d";
const S_E: &str = "g1:0000000e";
const S_HIDDEN: &str = "g1:000000ff";

// ---------------------------------------------------------------------------
// Stub: batched session activity source
// ---------------------------------------------------------------------------

/// Message-side session activity (design D1/D4). Every batched request is
/// recorded so the suite can prove one lookup per call over exactly the
/// intended id set — page-scoped on the default path, all candidates under
/// `sort=last_activity`.
struct StubActivityHistory {
    activity: Mutex<HashMap<String, u64>>,
    requested_ids: Mutex<Vec<Vec<String>>>,
    fail: AtomicBool,
}

impl StubActivityHistory {
    fn new(activity: &[(&str, u64)]) -> Self {
        Self {
            activity: Mutex::new(
                activity
                    .iter()
                    .map(|(session_id, timestamp)| (session_id.to_string(), *timestamp))
                    .collect(),
            ),
            requested_ids: Mutex::new(Vec::new()),
            fail: AtomicBool::new(false),
        }
    }

    fn failing() -> Self {
        let stub = Self::new(&[]);
        stub.fail.store(true, Ordering::SeqCst);
        stub
    }

    fn requests(&self) -> Vec<Vec<String>> {
        self.requested_ids.lock().expect("history lock").clone()
    }
}

#[async_trait]
impl GroupMessageHistoryService for StubActivityHistory {
    async fn get_history(
        &self,
        _cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        panic!("the session list must not read group-history bodies")
    }

    async fn get_session_history(
        &self,
        _cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        panic!("the session list must not read session-history bodies")
    }

    async fn last_activity_by_session_ids(
        &self,
        session_ids: &[String],
    ) -> Result<HashMap<String, u64>, GroupUseCaseError> {
        self.requested_ids
            .lock()
            .expect("history lock")
            .push(session_ids.to_vec());
        if self.fail.load(Ordering::SeqCst) {
            return Err(GroupUseCaseError::Service(ServiceError::InternalError(
                "message activity store unavailable".into(),
            )));
        }
        let activity = self.activity.lock().expect("history lock");
        Ok(session_ids
            .iter()
            .filter_map(|session_id| {
                activity
                    .get(session_id)
                    .map(|timestamp| (session_id.clone(), *timestamp))
            })
            .collect())
    }
}

// ---------------------------------------------------------------------------
// Fixtures and assertions
// ---------------------------------------------------------------------------

fn activity_list(viewer: &str, sort: SessionListSort) -> ListSessions {
    ListSessions {
        caller: bot_principal(viewer),
        group_id: "g1".into(),
        view_bot_id: Some(viewer.into()),
        offset: 0,
        limit: 10,
        status: None,
        sort,
    }
}

/// `SessionService::list` explicitly: `SessionMessageService::list` shares the
/// name on the same facade, so the trait must be named at the call site.
async fn list_sessions(
    fixture: &Fixture,
    command: ListSessions,
) -> Result<Page<SessionSummary>, ApplicationError> {
    SessionService::list(&fixture.service, command).await
}

/// Seed one Session with an explicit id where the View Actor participates,
/// so the repository's `created_at` clock assigns a strictly ordered time
/// when the caller sleeps between seeds.
async fn seed_viewed_session(fixture: &Fixture, session_id: &str, viewer: &str) {
    let group = fixture.groups.get("g1").await.expect("group exists");
    fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                id: Some(session_id.to_string()),
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot(viewer, ParticipantRole::Consultant),
                ],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed viewed Session");
}

/// Seed one Session the View Actor does NOT participate in.
async fn seed_foreign_session(fixture: &Fixture, session_id: &str) {
    let group = fixture.groups.get("g1").await.expect("group exists");
    fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                id: Some(session_id.to_string()),
                session_kind: SessionKind::Chat,
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed foreign Session");
}

async fn seed_viewed_session_later(fixture: &Fixture, session_id: &str, viewer: &str) {
    // The memory store stamps `created_at` in milliseconds; sleep so the
    // seeded order is a strict `created_at` order rather than a tie.
    tokio::time::sleep(Duration::from_millis(6)).await;
    seed_viewed_session(fixture, session_id, viewer).await;
}

fn session_ids(items: &[SessionSummary]) -> Vec<String> {
    items
        .iter()
        .map(|item| item.session_id.clone())
        .collect()
}

fn summary_of<'a>(items: &'a [SessionSummary], session_id: &str) -> &'a SessionSummary {
    items
        .iter()
        .find(|item| item.session_id == session_id)
        .unwrap_or_else(|| panic!("Session '{session_id}' missing from the page"))
}

fn activity_of(items: &[SessionSummary], session_id: &str) -> Option<u64> {
    summary_of(items, session_id).last_activity_at
}

fn sorted_ids(ids: &[String]) -> Vec<String> {
    let mut ids = ids.to_vec();
    ids.sort();
    ids
}

fn expect_internal(result: Result<Page<SessionSummary>, ApplicationError>, context: &str) {
    match result {
        Ok(page) => panic!(
            "{context} must fail the request, served {:?}",
            session_ids(&page.items)
        ),
        Err(ApplicationError::Internal(_)) => {}
        Err(other) => panic!("{context} must be an internal error, got {other:?}"),
    }
}

async fn seeded_fixture(history: &Arc<StubActivityHistory>) -> Fixture {
    let fixture =
        Fixture::new_with_activity_history(Arc::new(BotCore::memory()), history.clone()).await;
    fixture.add_bot("viewer").await;
    fixture.store_group("g1", "driver", None).await;
    fixture
}

// ---------------------------------------------------------------------------
// Default / `sort=created_at`: unchanged ordering, real activity value
// ---------------------------------------------------------------------------

#[tokio::test]
async fn created_at_sort_keeps_todays_order_and_reports_the_real_activity() {
    // The oldest session carries the ONLY message, far after every
    // `created_at`: an ordering that follows activity would put it first.
    let history = Arc::new(StubActivityHistory::new(&[(S_A, MESSAGE_TIME)]));
    let fixture = seeded_fixture(&history).await;
    seed_viewed_session(&fixture, S_A, "viewer").await;
    seed_viewed_session_later(&fixture, S_B, "viewer").await;
    seed_viewed_session_later(&fixture, S_C, "viewer").await;

    let page = list_sessions(&fixture, activity_list("viewer", SessionListSort::CreatedAt))
        .await
        .expect("list Sessions by created_at");

    // Historic contract: `created_at DESC`.
    assert_eq!(session_ids(&page.items), vec![S_C, S_B, S_A]);
    assert_eq!(page.total, 3);
    // Review Focus #6: the field is the real message time, not `created_at`.
    assert_eq!(activity_of(&page.items, S_A), Some(MESSAGE_TIME));
    assert!(
        MESSAGE_TIME > summary_of(&page.items, S_A).created_at,
        "the fixture must keep the message time after the session's created_at"
    );
    // No messages → the session's OWN `created_at` fallback, never `None`.
    for session_id in [S_B, S_C] {
        let summary = summary_of(&page.items, session_id);
        assert_eq!(summary.last_activity_at, Some(summary.created_at));
    }

    // The ordering stays today's order with the default sort value too.
    let mut default_sort = activity_list("viewer", SessionListSort::default());
    default_sort.sort = SessionListSort::default();
    let default_page = list_sessions(&fixture, default_sort)
        .await
        .expect("default sort");
    assert_eq!(session_ids(&default_page.items), vec![S_C, S_B, S_A]);

    // One batched lookup over the PAGE ids only (design D4 access cost).
    let requests = history.requests();
    assert_eq!(requests.len(), 2, "one batched lookup per list call");
    for request in &requests {
        assert_eq!(sorted_ids(request), vec![S_A, S_B, S_C]);
    }
}

#[tokio::test]
async fn created_at_sort_scopes_the_activity_lookup_to_the_page() {
    let history = Arc::new(StubActivityHistory::new(&[
        (S_A, MESSAGE_TIME + 100),
        (S_B, MESSAGE_TIME + 200),
        (S_C, MESSAGE_TIME + 300),
        (S_D, MESSAGE_TIME + 400),
    ]));
    let fixture = seeded_fixture(&history).await;
    seed_viewed_session(&fixture, S_A, "viewer").await;
    seed_viewed_session_later(&fixture, S_B, "viewer").await;
    seed_viewed_session_later(&fixture, S_C, "viewer").await;
    seed_viewed_session_later(&fixture, S_D, "viewer").await;

    let mut command = activity_list("viewer", SessionListSort::CreatedAt);
    command.limit = 2;
    let page = list_sessions(&fixture, command).await.expect("list page 1");

    assert_eq!(session_ids(&page.items), vec![S_D, S_C]);
    assert_eq!(page.total, 4);
    // Sorting and pagination ran first; only the page pays the lookup.
    let requests = history.requests();
    assert_eq!(requests.len(), 1, "the default path must batch one lookup");
    assert_eq!(sorted_ids(&requests[0]), vec![S_C, S_D]);
    assert_eq!(activity_of(&page.items, S_D), Some(MESSAGE_TIME + 400));
    assert_eq!(activity_of(&page.items, S_C), Some(MESSAGE_TIME + 300));
}

// ---------------------------------------------------------------------------
// Cross-sort consistency: same item, same value under every mode
// ---------------------------------------------------------------------------

#[tokio::test]
async fn last_activity_at_is_identical_across_all_sort_modes() {
    let history = Arc::new(StubActivityHistory::new(&[
        (S_A, MESSAGE_TIME + 100),
        (S_B, MESSAGE_TIME + 300),
        (S_C, MESSAGE_TIME + 200),
    ]));
    let fixture = seeded_fixture(&history).await;
    seed_viewed_session(&fixture, S_A, "viewer").await;
    seed_viewed_session_later(&fixture, S_B, "viewer").await;
    seed_viewed_session_later(&fixture, S_C, "viewer").await;

    let mut no_sort_command = activity_list("viewer", SessionListSort::default());
    no_sort_command.sort = SessionListSort::default();
    let no_sort = list_sessions(&fixture, no_sort_command)
        .await
        .expect("list without an explicit sort");
    let created = list_sessions(&fixture, activity_list("viewer", SessionListSort::CreatedAt))
        .await
        .expect("list by created_at");
    let activity = list_sessions(&fixture, activity_list("viewer", SessionListSort::LastActivity))
        .await
        .expect("list by last activity");

    // The modes disagree on the ORDER — and agree on every item's VALUE.
    assert_eq!(session_ids(&no_sort.items), vec![S_C, S_B, S_A]);
    assert_eq!(session_ids(&created.items), vec![S_C, S_B, S_A]);
    assert_eq!(session_ids(&activity.items), vec![S_B, S_C, S_A]);
    for session_id in [S_A, S_B, S_C] {
        let expected = activity_of(&no_sort.items, session_id);
        assert_eq!(activity_of(&created.items, session_id), expected);
        assert_eq!(activity_of(&activity.items, session_id), expected);
    }
    assert_eq!(activity_of(&activity.items, S_B), Some(MESSAGE_TIME + 300));
}

// ---------------------------------------------------------------------------
// `sort=last_activity`: global sort BEFORE pagination
// ---------------------------------------------------------------------------

#[tokio::test]
async fn last_activity_sorts_globally_before_pagination_across_the_page_boundary() {
    // Activity order (s1, s2, s5, s4, s3) is almost the REVERSE of the
    // created_at order (s5, s4, s3, s2, s1): only a global sort can expose
    // this across the page boundary.
    let history = Arc::new(StubActivityHistory::new(&[
        (S_A, MESSAGE_TIME + 500),
        (S_B, MESSAGE_TIME + 400),
        (S_C, MESSAGE_TIME + 100),
        (S_D, MESSAGE_TIME + 200),
        (S_E, MESSAGE_TIME + 300),
    ]));
    let fixture = seeded_fixture(&history).await;
    for (index, session_id) in [S_A, S_B, S_C, S_D, S_E].into_iter().enumerate() {
        if index > 0 {
            tokio::time::sleep(Duration::from_millis(6)).await;
        }
        seed_viewed_session(&fixture, session_id, "viewer").await;
    }

    let mut page_one_command = activity_list("viewer", SessionListSort::LastActivity);
    page_one_command.limit = 2;
    let page_one = list_sessions(&fixture, page_one_command)
        .await
        .expect("list activity page 1");
    let mut page_two_command = activity_list("viewer", SessionListSort::LastActivity);
    page_two_command.offset = 2;
    page_two_command.limit = 2;
    let page_two = list_sessions(&fixture, page_two_command)
        .await
        .expect("list activity page 2");

    assert_eq!(page_one.total, 5);
    assert_eq!(page_two.total, 5);
    assert_eq!(session_ids(&page_one.items), vec![S_A, S_B]);
    assert_eq!(session_ids(&page_two.items), vec![S_E, S_D]);
    // Page 1's tail outranks page 2's head: one global order, not two
    // independently sorted pages.
    assert!(
        activity_of(&page_one.items, S_B) > activity_of(&page_two.items, S_E),
        "page 1 tail must outrank page 2 head under one global activity order"
    );

    // Every `last_activity` call batched ONE lookup over ALL candidates,
    // not the page: the key is needed before pagination.
    let requests = history.requests();
    assert_eq!(requests.len(), 2, "one batched lookup per list call");
    for request in &requests {
        assert_eq!(sorted_ids(request), vec![S_A, S_B, S_C, S_D, S_E]);
    }

    // The created_at mode of the same data is a different sequence, so the
    // assertions above cannot be satisfied by the repository's own order.
    let mut created_page_command = activity_list("viewer", SessionListSort::CreatedAt);
    created_page_command.limit = 2;
    let created_page = list_sessions(&fixture, created_page_command)
        .await
        .expect("list created_at page 1");
    assert_eq!(session_ids(&created_page.items), vec![S_E, S_D]);
}

#[tokio::test]
async fn last_activity_breaks_a_metric_tie_by_session_id_ascending() {
    let history = Arc::new(StubActivityHistory::new(&[
        (S_A, MESSAGE_TIME),
        (S_B, MESSAGE_TIME),
    ]));
    let fixture = seeded_fixture(&history).await;
    seed_viewed_session(&fixture, S_A, "viewer").await;
    seed_viewed_session_later(&fixture, S_B, "viewer").await;

    let page = list_sessions(&fixture, activity_list("viewer", SessionListSort::LastActivity))
        .await
        .expect("list by last activity");

    // `created_at DESC` alone would serve (s-b, s-a); the tie-break is
    // `session_id ASC`, applied in-app over the whole candidate set.
    assert_eq!(session_ids(&page.items), vec![S_A, S_B]);
    assert_eq!(activity_of(&page.items, S_A), Some(MESSAGE_TIME));
}

// ---------------------------------------------------------------------------
// No messages: the session's own `created_at` value AND rank
// ---------------------------------------------------------------------------

#[tokio::test]
async fn session_without_messages_sorts_by_its_created_at_fallback() {
    // The chat's only message is OLDER than any session creation, so the
    // no-message session must win on the fallback key — an implementation
    // that dropped empty sessions from the key would keep the chat first.
    let history = Arc::new(StubActivityHistory::new(&[(S_B, 1_000)]));
    let fixture = seeded_fixture(&history).await;
    seed_viewed_session(&fixture, S_A, "viewer").await;
    seed_viewed_session_later(&fixture, S_B, "viewer").await;

    let activity_page = list_sessions(
        &fixture,
        activity_list("viewer", SessionListSort::LastActivity),
    )
    .await
    .expect("list by last activity");
    let created_page = list_sessions(&fixture, activity_list("viewer", SessionListSort::CreatedAt))
        .await
        .expect("list by created_at");

    assert_eq!(session_ids(&activity_page.items), vec![S_A, S_B]);
    assert_eq!(session_ids(&created_page.items), vec![S_B, S_A]);
    let idle = summary_of(&activity_page.items, S_A);
    assert_eq!(
        idle.last_activity_at,
        Some(idle.created_at),
        "a message-less session reports its own created_at, never None"
    );
    // Same value under the other mode.
    assert_eq!(
        activity_of(&created_page.items, S_A),
        Some(idle.created_at)
    );
}

// ---------------------------------------------------------------------------
// Viewer scope first (design D5): non-participant Sessions never appear
// ---------------------------------------------------------------------------

#[tokio::test]
async fn viewer_scope_excludes_foreign_sessions_from_the_page_and_the_keys() {
    let history = Arc::new(StubActivityHistory::new(&[
        (S_A, MESSAGE_TIME),
        (S_HIDDEN, MESSAGE_TIME + 900),
    ]));
    let fixture = seeded_fixture(&history).await;
    seed_viewed_session(&fixture, S_A, "viewer").await;
    seed_foreign_session(&fixture, S_HIDDEN).await;

    for sort in [SessionListSort::CreatedAt, SessionListSort::LastActivity] {
        let page = list_sessions(&fixture, activity_list("viewer", sort))
            .await
            .expect("list viewer-scoped Sessions");
        assert_eq!(page.total, 1, "only participant Sessions count under {sort:?}");
        assert_eq!(session_ids(&page.items), vec![S_A]);
        // The foreign Session's much higher activity must not even be read:
        // candidate resolution is viewer-scoped before any activity lookup.
        let requests = history.requests();
        let last_request = requests.last().expect("one activity lookup");
        assert_eq!(last_request, &vec![S_A.to_string()]);
    }
}

// ---------------------------------------------------------------------------
// Failure semantics (D6): a lookup error fails BOTH modes
// ---------------------------------------------------------------------------

#[tokio::test]
async fn history_lookup_error_fails_both_sort_modes() {
    let fixture = seeded_fixture(&Arc::new(StubActivityHistory::failing())).await;
    seed_viewed_session(&fixture, S_A, "viewer").await;

    for sort in [SessionListSort::CreatedAt, SessionListSort::LastActivity] {
        expect_internal(
            list_sessions(&fixture, activity_list("viewer", sort)).await,
            &format!("a failing message activity lookup under {sort:?}"),
        );
    }
}

// ---------------------------------------------------------------------------
// Collected gating and the batched avatar projection stay unchanged
// ---------------------------------------------------------------------------

fn avatar_backed_core() -> (tempfile::TempDir, Arc<BotCore>) {
    // Sibling modules do not share private helpers; this mirrors the shape
    // used by the avatars/avatar_budget suites so profile writes stay out of
    // the crate working directory.
    let dir = tempfile::tempdir().expect("temp bots base dir");
    let bots = Arc::new(BotCore::with_base_dir(dir.path().to_path_buf()));
    (dir, bots)
}

fn participant_avatar(value: &Value, actor: &str) -> Value {
    value["participants"]
        .as_array()
        .unwrap_or_else(|| panic!("participants array missing for {actor}"))
        .iter()
        .find(|row| row["actor_id"] == actor)
        .unwrap_or_else(|| panic!("participant row for {actor}"))["avatar_url"]
        .clone()
}

#[tokio::test]
async fn collected_gating_and_avatar_projection_are_unchanged_on_both_paths() {
    let (_bots_dir, bots) = avatar_backed_core();
    let history = Arc::new(StubActivityHistory::new(&[
        (S_A, MESSAGE_TIME + 100),
        (S_B, MESSAGE_TIME + 200),
    ]));
    let fixture = Fixture::new_with_activity_history(bots, history).await;
    fixture.add_bot("viewer").await;
    fixture.store_group("g1", "driver", None).await;
    assert_eq!(
        fixture
            .bots
            .update_avatar_existing("viewer", AvatarChange::Set("https://cdn/viewer.png".into()))
            .await
            .expect("seed avatar"),
        ProfileWrite::Updated
    );
    seed_viewed_session(&fixture, S_A, "viewer").await;
    seed_viewed_session_later(&fixture, S_B, "viewer").await;
    // Collect only the OLDER session, which is also the LAST under the
    // created_at order but the FIRST under the activity order.
    fixture
        .service
        .collect(CollectSession {
            caller: human_principal("viewer"),
            session_id: S_A.into(),
            participant: "viewer".into(),
        })
        .await
        .expect("collect the older Session");

    for sort in [SessionListSort::CreatedAt, SessionListSort::LastActivity] {
        let page = list_sessions(&fixture, activity_list("viewer", sort))
            .await
            .expect("list Sessions");
        let collected = summary_of(&page.items, S_A)
            .collected
            .expect("collected is projected for a named view actor");
        assert!(collected, "the collected Session must report Some(true) under {sort:?}");
        let open = summary_of(&page.items, S_B)
            .collected
            .expect("collected is projected for a named view actor");
        assert!(!open, "the uncollected Session must report Some(false) under {sort:?}");

        // The batched avatar read still fills the page's rows on both paths.
        let value = serde_json::to_value(summary_of(&page.items, S_A)).expect("serialize");
        assert_eq!(
            participant_avatar(&value, "viewer"),
            Value::from("https://cdn/viewer.png")
        );
        // Rows stay the Session's own roster (driver + viewer).
        let rows = value["participants"].as_array().expect("participants array");
        assert_eq!(rows.len(), 2);
    }

    // `collected` stays absent when the request names no view actor.
    let mut anonymous = activity_list("viewer", SessionListSort::LastActivity);
    anonymous.view_bot_id = None;
    let anonymous_page = list_sessions(&fixture, anonymous)
        .await
        .expect("list as the authenticated Human only");
    assert!(
        anonymous_page
            .items
            .iter()
            .all(|item| item.collected.is_none()),
        "collected must be absent when view_bot_id is not specified"
    );
}
