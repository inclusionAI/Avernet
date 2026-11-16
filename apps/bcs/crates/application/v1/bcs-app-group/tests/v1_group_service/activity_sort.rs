//! Recent-activity list sort and `last_activity_at` projection (design
//! D1/D4/D5/D6): two population paths, one identical field value.

use super::*;

// ---------------------------------------------------------------------------
// Stubs: message-side and session-creation-side activity sources
// ---------------------------------------------------------------------------

/// Message-side activity source (design D1/D4). Every batched request is
/// recorded so the suite can prove one lookup per call, covering exactly the
/// intended id set (no per-item reads).
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
                    .map(|(group_id, timestamp)| (group_id.to_string(), *timestamp))
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
        panic!("the group list must not read message-history bodies")
    }

    async fn get_session_history(
        &self,
        _cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        panic!("the group list must not read session-history bodies")
    }

    async fn last_activity_by_group_ids(
        &self,
        group_ids: &[String],
    ) -> Result<HashMap<String, u64>, GroupUseCaseError> {
        self.requested_ids
            .lock()
            .expect("history lock")
            .push(group_ids.to_vec());
        if self.fail.load(Ordering::SeqCst) {
            return Err(GroupUseCaseError::Service(ServiceError::InternalError(
                "message activity store unavailable".into(),
            )));
        }
        let activity = self.activity.lock().expect("history lock");
        Ok(group_ids
            .iter()
            .filter_map(|group_id| {
                activity
                    .get(group_id)
                    .map(|timestamp| (group_id.clone(), *timestamp))
            })
            .collect())
    }
}

fn stub_session_unsupported<T>(operation: &str) -> Result<T, SessionUseCaseError> {
    Err(SessionUseCaseError::Internal(ServiceError::InternalError(
        format!("stub session service does not implement {operation}"),
    )))
}

/// Session-management double: serves the membership query and the batched
/// session-creation activity key (design D1 Q2/D5), and records activity
/// requests the same way the history stub does.
struct StubSessionActivity {
    session_group_ids: Vec<String>,
    created_at: Mutex<HashMap<String, u64>>,
    requested_ids: Mutex<Vec<Vec<String>>>,
    fail: AtomicBool,
}

impl StubSessionActivity {
    fn new(created_at: &[(&str, u64)]) -> Self {
        Self {
            session_group_ids: Vec::new(),
            created_at: Mutex::new(
                created_at
                    .iter()
                    .map(|(group_id, timestamp)| (group_id.to_string(), *timestamp))
                    .collect(),
            ),
            requested_ids: Mutex::new(Vec::new()),
            fail: AtomicBool::new(false),
        }
    }

    fn with_session_group_ids(mut self, group_ids: &[&str]) -> Self {
        self.session_group_ids = group_ids.iter().map(|id| (*id).to_string()).collect();
        self
    }

    fn failing() -> Self {
        let stub = Self::new(&[]);
        stub.fail.store(true, Ordering::SeqCst);
        stub
    }

    fn requests(&self) -> Vec<Vec<String>> {
        self.requested_ids.lock().expect("session lock").clone()
    }
}

#[async_trait]
impl SessionManagementService for StubSessionActivity {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        stub_session_unsupported("create_or_reactivate")
    }

    async fn get(
        &self,
        _session_id: &str,
    ) -> Result<Option<bcs_service_api::Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn belongs_to_group(
        &self,
        _session_id: &str,
        _group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<bcs_service_api::Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<bcs_service_api::Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<bcs_service_api::Session, SessionUseCaseError> {
        stub_session_unsupported("add_participant")
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<bcs_service_api::Session, SessionUseCaseError> {
        stub_session_unsupported("remove_participant")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<bcs_service_api::Session, SessionUseCaseError> {
        stub_session_unsupported("update_participant_mode")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<bcs_service_api::Session, SessionUseCaseError> {
        stub_session_unsupported("update_title")
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(self.session_group_ids.clone())
    }

    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }

    async fn max_created_at_by_group_ids(
        &self,
        group_ids: &[String],
    ) -> Result<HashMap<String, u64>, SessionUseCaseError> {
        self.requested_ids
            .lock()
            .expect("session lock")
            .push(group_ids.to_vec());
        if self.fail.load(Ordering::SeqCst) {
            return Err(SessionUseCaseError::Internal(ServiceError::InternalError(
                "session activity store unavailable".into(),
            )));
        }
        let created_at = self.created_at.lock().expect("session lock");
        Ok(group_ids
            .iter()
            .filter_map(|group_id| {
                created_at
                    .get(group_id)
                    .map(|timestamp| (group_id.clone(), *timestamp))
            })
            .collect())
    }
}

// ---------------------------------------------------------------------------
// Fixtures and assertions
// ---------------------------------------------------------------------------

fn activity_list_command(viewer: &str, sort: GroupListSort) -> ListGroups {
    ListGroups {
        caller: bot_principal(viewer),
        view_bot_id: Some(viewer.into()),
        offset: 0,
        limit: 50,
        q: None,
        visibility: None,
        membership: MembershipFilter::All,
        kind: GroupKindFilter::All,
        strategy: None,
        sort,
    }
}

async fn seed_viewer_chat_group(fixture: &Fixture, group_id: &str, created_at: u64) {
    fixture
        .groups
        .upsert(normal_group(
            group_id,
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("viewer", ParticipantRole::Consultant),
            ],
            GroupStrategy::Chat,
            created_at,
        ))
        .await
        .expect("store Group");
}

fn summary_group_id(summary: &GroupSummary) -> &str {
    match summary {
        GroupSummary::Normal(summary) => &summary.group_id,
        GroupSummary::DirectMessage(summary) => &summary.group_id,
    }
}

fn summary_activity(summary: &GroupSummary) -> Option<u64> {
    match summary {
        GroupSummary::Normal(summary) => summary.last_activity_at,
        GroupSummary::DirectMessage(summary) => summary.last_activity_at,
    }
}

fn summary_membership(summary: &GroupSummary) -> Membership {
    match summary {
        GroupSummary::Normal(summary) => summary.membership,
        GroupSummary::DirectMessage(summary) => summary.membership,
    }
}

fn summary_ids(items: &[GroupSummary]) -> Vec<String> {
    items
        .iter()
        .map(|item| summary_group_id(item).to_string())
        .collect()
}

fn activity_of(items: &[GroupSummary], group_id: &str) -> Option<u64> {
    let summary = items
        .iter()
        .find(|item| summary_group_id(item) == group_id)
        .unwrap_or_else(|| panic!("Group '{group_id}' missing from the page"));
    summary_activity(summary)
}

fn sorted_ids(ids: &[String]) -> Vec<String> {
    let mut ids = ids.to_vec();
    ids.sort();
    ids
}

fn expect_internal(result: Result<Page<GroupSummary>, ApplicationError>, context: &str) {
    match result {
        Ok(page) => panic!(
            "{context} must fail the request, served {:?}",
            summary_ids(&page.items)
        ),
        Err(ApplicationError::Internal(_)) => {}
        Err(other) => panic!("{context} must be an internal error, got {other:?}"),
    }
}

// ---------------------------------------------------------------------------
// `sort=last_activity`: merged map, created_at fallback, id tie-break
// ---------------------------------------------------------------------------

#[tokio::test]
async fn last_activity_sort_uses_merged_map_created_at_fallback_and_id_tie_break() {
    let history = Arc::new(StubActivityHistory::new(&[("g-a", 900), ("g-c", 900)]));
    let sessions = Arc::new(StubSessionActivity::new(&[("g-b", 1_000)]));
    let fixture =
        Fixture::new_with_activity_services(Some(history.clone()), sessions.clone()).await;
    fixture.add_public_bot("viewer").await;
    for (group_id, created_at) in [("g-a", 100u64), ("g-b", 200), ("g-c", 300), ("g-d", 400)] {
        seed_viewer_chat_group(&fixture, group_id, created_at).await;
    }

    let page = fixture
        .service
        .list_groups(activity_list_command("viewer", GroupListSort::LastActivity))
        .await
        .expect("list Groups by last activity");

    assert_eq!(page.total, 4);
    // g-b wins on session creation (1000); g-a/g-c tie at the message max and
    // break by `group_id ASC`; g-d has no activity row and falls back to its
    // own `created_at`.
    assert_eq!(
        summary_ids(&page.items),
        vec!["g-b", "g-a", "g-c", "g-d"]
    );
    assert_eq!(activity_of(&page.items, "g-b"), Some(1_000));
    assert_eq!(activity_of(&page.items, "g-a"), Some(900));
    assert_eq!(activity_of(&page.items, "g-c"), Some(900));
    assert_eq!(activity_of(&page.items, "g-d"), Some(400));

    // One batched lookup per side over ALL filtered ids on this path.
    let history_requests = history.requests();
    assert_eq!(history_requests.len(), 1, "message lookup must be batched");
    assert_eq!(
        sorted_ids(&history_requests[0]),
        vec!["g-a", "g-b", "g-c", "g-d"]
    );
    let session_requests = sessions.requests();
    assert_eq!(session_requests.len(), 1, "session lookup must be batched");
    assert_eq!(
        sorted_ids(&session_requests[0]),
        vec!["g-a", "g-b", "g-c", "g-d"]
    );
}

#[tokio::test]
async fn last_activity_sort_paginates_after_the_global_sort_and_keeps_total() {
    let history = Arc::new(StubActivityHistory::new(&[
        ("g-a", 500),
        ("g-b", 400),
        ("g-c", 300),
        ("g-d", 200),
        ("g-e", 100),
    ]));
    let sessions = Arc::new(StubSessionActivity::new(&[]));
    let fixture =
        Fixture::new_with_activity_services(Some(history), sessions).await;
    fixture.add_public_bot("viewer").await;
    for (group_id, created_at) in [
        ("g-a", 10u64),
        ("g-b", 20),
        ("g-c", 30),
        ("g-d", 40),
        ("g-e", 50),
    ] {
        seed_viewer_chat_group(&fixture, group_id, created_at).await;
    }

    let mut command = activity_list_command("viewer", GroupListSort::LastActivity);
    command.offset = 1;
    command.limit = 2;
    let page = fixture.service.list_groups(command).await.expect("list page 2");

    // The created_at order (e, d, c, b, a) is a different sequence entirely;
    // the slice proves the sort ran globally before pagination.
    assert_eq!(page.total, 5);
    assert_eq!(page.offset, 1);
    assert_eq!(page.limit, 2);
    assert_eq!(summary_ids(&page.items), vec!["g-b", "g-c"]);
}

// ---------------------------------------------------------------------------
// default / `sort=created_at`: unchanged ordering, page-scoped population
// ---------------------------------------------------------------------------

#[tokio::test]
async fn created_at_sort_keeps_todays_order_and_projects_the_same_activity_values() {
    let history = Arc::new(StubActivityHistory::new(&[("g-a", 900), ("g-c", 900)]));
    let sessions = Arc::new(StubSessionActivity::new(&[("g-b", 1_000)]));
    let fixture =
        Fixture::new_with_activity_services(Some(history.clone()), sessions.clone()).await;
    fixture.add_public_bot("viewer").await;
    for (group_id, created_at) in [("g-a", 100u64), ("g-b", 200), ("g-c", 300), ("g-d", 400)] {
        seed_viewer_chat_group(&fixture, group_id, created_at).await;
    }

    // No `sort` on the wire deserializes to the default, which must remain
    // today's ordering.
    assert_eq!(GroupListSort::default(), GroupListSort::CreatedAt);
    let created_page = fixture
        .service
        .list_groups(activity_list_command("viewer", GroupListSort::CreatedAt))
        .await
        .expect("list Groups by created_at");
    let activity_page = fixture
        .service
        .list_groups(activity_list_command("viewer", GroupListSort::LastActivity))
        .await
        .expect("list Groups by last activity");

    // Historic contract: `created_at DESC, group_id ASC`.
    assert_eq!(
        summary_ids(&created_page.items),
        vec!["g-d", "g-c", "g-b", "g-a"]
    );
    // Same item, same field value under every sort mode.
    for group_id in ["g-a", "g-b", "g-c", "g-d"] {
        assert_eq!(
            activity_of(&created_page.items, group_id),
            activity_of(&activity_page.items, group_id),
            "last_activity_at for '{group_id}' must not depend on the sort mode"
        );
    }
}

#[tokio::test]
async fn created_at_sort_scopes_the_activity_lookup_to_the_page() {
    let history = Arc::new(StubActivityHistory::new(&[
        ("g-a", 900),
        ("g-b", 800),
        ("g-c", 700),
        ("g-d", 600),
    ]));
    let sessions = Arc::new(StubSessionActivity::new(&[("g-a", 1_000)]));
    let fixture =
        Fixture::new_with_activity_services(Some(history.clone()), sessions.clone()).await;
    fixture.add_public_bot("viewer").await;
    for (group_id, created_at) in [("g-a", 100u64), ("g-b", 200), ("g-c", 300), ("g-d", 400)] {
        seed_viewer_chat_group(&fixture, group_id, created_at).await;
    }

    let mut command = activity_list_command("viewer", GroupListSort::CreatedAt);
    command.limit = 2;
    let page = fixture.service.list_groups(command).await.expect("list page 1");
    assert_eq!(summary_ids(&page.items), vec!["g-d", "g-c"]);

    // The default path sorts and paginates first, then pays one batched read
    // over the page ids only (design D4 access cost).
    let history_requests = history.requests();
    assert_eq!(history_requests.len(), 1, "message lookup must be batched");
    assert_eq!(sorted_ids(&history_requests[0]), vec!["g-c", "g-d"]);
    let session_requests = sessions.requests();
    assert_eq!(session_requests.len(), 1, "session lookup must be batched");
    assert_eq!(sorted_ids(&session_requests[0]), vec!["g-c", "g-d"]);

    // The page-scoped map still yields the real activity values: g-d's own
    // message max and g-a's merged session-created key would be identical
    // under `sort=last_activity`.
    assert_eq!(activity_of(&page.items, "g-d"), Some(600));
    assert_eq!(activity_of(&page.items, "g-c"), Some(700));
}

// ---------------------------------------------------------------------------
// Cross-sort consistency (Review Focus #6) and unified semantics (#7)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn last_activity_at_is_identical_across_all_three_sort_modes() {
    // The message is far later than the Group's `created_at`, so an accidental
    // fallback to `created_at` cannot pass this case.
    let history = Arc::new(StubActivityHistory::new(&[("g-hot", 5_000_000)]));
    let sessions = Arc::new(StubSessionActivity::new(&[]));
    let fixture =
        Fixture::new_with_activity_services(Some(history), sessions).await;
    fixture.add_public_bot("viewer").await;
    seed_viewer_chat_group(&fixture, "g-hot", 100).await;

    let mut no_sort_command = activity_list_command("viewer", GroupListSort::CreatedAt);
    no_sort_command.sort = GroupListSort::default();
    let no_sort = fixture
        .service
        .list_groups(no_sort_command)
        .await
        .expect("list without an explicit sort");
    let created = fixture
        .service
        .list_groups(activity_list_command("viewer", GroupListSort::CreatedAt))
        .await
        .expect("list by created_at");
    let activity = fixture
        .service
        .list_groups(activity_list_command("viewer", GroupListSort::LastActivity))
        .await
        .expect("list by last activity");

    for page in [&no_sort, &created, &activity] {
        assert_eq!(activity_of(&page.items, "g-hot"), Some(5_000_000));
    }
}

#[tokio::test]
async fn zero_message_group_ranks_by_its_newest_session_created_at() {
    let history = Arc::new(StubActivityHistory::new(&[("g-chat", 500)]));
    let sessions = Arc::new(StubSessionActivity::new(&[("g-empty", 8_000)]));
    let fixture =
        Fixture::new_with_activity_services(Some(history), sessions).await;
    fixture.add_public_bot("viewer").await;
    // The zero-message Group is the OLDER one by `created_at`; only its
    // session-creation artifact makes it active.
    seed_viewer_chat_group(&fixture, "g-empty", 100).await;
    seed_viewer_chat_group(&fixture, "g-chat", 200).await;

    let page = fixture
        .service
        .list_groups(activity_list_command("viewer", GroupListSort::LastActivity))
        .await
        .expect("list by last activity");

    assert_eq!(summary_ids(&page.items), vec!["g-empty", "g-chat"]);
    assert_eq!(activity_of(&page.items, "g-empty"), Some(8_000));
    assert_eq!(activity_of(&page.items, "g-chat"), Some(500));
}

#[tokio::test]
async fn merged_map_prefers_a_later_session_created_at_over_an_earlier_message_max() {
    let history = Arc::new(StubActivityHistory::new(&[("g-merged", 2_000), ("g-msg", 5_000)]));
    let sessions = Arc::new(StubSessionActivity::new(&[("g-merged", 9_000)]));
    let fixture =
        Fixture::new_with_activity_services(Some(history), sessions).await;
    fixture.add_public_bot("viewer").await;
    seed_viewer_chat_group(&fixture, "g-merged", 100).await;
    seed_viewer_chat_group(&fixture, "g-msg", 200).await;

    let page = fixture
        .service
        .list_groups(activity_list_command("viewer", GroupListSort::LastActivity))
        .await
        .expect("list by last activity");

    // The merge is `max(message max, session created_at)`: 9000 beats both
    // g-merged's 2000 message max and g-msg's 5000.
    assert_eq!(summary_ids(&page.items), vec!["g-merged", "g-msg"]);
    assert_eq!(activity_of(&page.items, "g-merged"), Some(9_000));
}

#[tokio::test]
async fn zero_message_zero_session_group_falls_back_to_its_created_at() {
    let history = Arc::new(StubActivityHistory::new(&[]));
    let sessions = Arc::new(StubSessionActivity::new(&[]));
    let fixture =
        Fixture::new_with_activity_services(Some(history), sessions).await;
    fixture.add_public_bot("viewer").await;
    seed_viewer_chat_group(&fixture, "g-idle", 700).await;

    for sort in [GroupListSort::CreatedAt, GroupListSort::LastActivity] {
        let page = fixture
            .service
            .list_groups(activity_list_command("viewer", sort))
            .await
            .expect("list idle Group");
        assert_eq!(
            activity_of(&page.items, "g-idle"),
            Some(700),
            "the {sort:?} path must fall back to the Group's created_at"
        );
    }
}

// ---------------------------------------------------------------------------
// Summary shapes: normal, DM, session-only membership
// ---------------------------------------------------------------------------

#[tokio::test]
async fn last_activity_at_is_populated_on_normal_and_dm_summaries() {
    let history = Arc::new(StubActivityHistory::new(&[("g-normal", 7_000), ("g-dm", 6_000)]));
    let sessions = Arc::new(StubSessionActivity::new(&[]));
    let fixture =
        Fixture::new_with_activity_services(Some(history), sessions).await;
    fixture.add_public_bot("viewer").await;
    seed_viewer_chat_group(&fixture, "g-normal", 100).await;
    let mut dm_group = normal_group(
        "g-dm",
        "peer",
        vec![
            Participant::bot("viewer", ParticipantRole::Driver),
            Participant::bot("peer", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        200,
    );
    dm_group.group_kind = bcs_service_api::GroupKind::Dm;
    fixture.groups.upsert(dm_group).await.expect("store DM Group");

    let page = fixture
        .service
        .list_groups(activity_list_command("viewer", GroupListSort::LastActivity))
        .await
        .expect("list Groups by last activity");

    assert_eq!(summary_ids(&page.items), vec!["g-normal", "g-dm"]);
    assert!(matches!(page.items[0], GroupSummary::Normal(_)));
    assert!(matches!(page.items[1], GroupSummary::DirectMessage(_)));
    assert_eq!(activity_of(&page.items, "g-normal"), Some(7_000));
    assert_eq!(activity_of(&page.items, "g-dm"), Some(6_000));
}

#[tokio::test]
async fn session_only_membership_group_participates_and_carries_the_field() {
    let history = Arc::new(StubActivityHistory::new(&[("g-session-only", 8_500)]));
    let sessions = Arc::new(
        StubSessionActivity::new(&[("g-session-only", 9_500)])
            .with_session_group_ids(&["g-session-only"]),
    );
    let fixture =
        Fixture::new_with_activity_services(Some(history), sessions).await;
    fixture.add_public_bot("viewer").await;
    // The viewer is NOT a direct participant: it reaches the Group only
    // through a session (membership=session_only).
    fixture
        .groups
        .upsert(normal_group(
            "g-session-only",
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("someone-else", ParticipantRole::Consultant),
            ],
            GroupStrategy::Chat,
            100,
        ))
        .await
        .expect("store session-only Group");
    seed_viewer_chat_group(&fixture, "g-direct", 9_000).await;

    let page = fixture
        .service
        .list_groups(activity_list_command("viewer", GroupListSort::LastActivity))
        .await
        .expect("list Groups by last activity");

    // The session-only Group outranks the newer direct Group on activity.
    assert_eq!(
        summary_ids(&page.items),
        vec!["g-session-only", "g-direct"]
    );
    let session_only = &page.items[0];
    assert_eq!(summary_membership(session_only), Membership::SessionOnly);
    assert_eq!(summary_activity(session_only), Some(9_500));
}

// ---------------------------------------------------------------------------
// Failure semantics (D6) and unwired degradation (D4/D6)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn message_activity_lookup_error_fails_both_sort_modes() {
    let fixture = Fixture::new_with_activity_services(
        Some(Arc::new(StubActivityHistory::failing())),
        Arc::new(StubSessionActivity::new(&[])),
    )
    .await;
    fixture.add_public_bot("viewer").await;
    seed_viewer_chat_group(&fixture, "g-a", 100).await;

    for sort in [GroupListSort::CreatedAt, GroupListSort::LastActivity] {
        expect_internal(
            fixture
                .service
                .list_groups(activity_list_command("viewer", sort))
                .await,
            &format!("a failing message activity lookup under {sort:?}"),
        );
    }
}

#[tokio::test]
async fn session_activity_lookup_error_fails_both_sort_modes() {
    let fixture = Fixture::new_with_activity_services(
        Some(Arc::new(StubActivityHistory::new(&[("g-a", 500)]))),
        Arc::new(StubSessionActivity::failing()),
    )
    .await;
    fixture.add_public_bot("viewer").await;
    seed_viewer_chat_group(&fixture, "g-a", 100).await;

    for sort in [GroupListSort::CreatedAt, GroupListSort::LastActivity] {
        expect_internal(
            fixture
                .service
                .list_groups(activity_list_command("viewer", sort))
                .await,
            &format!("a failing session activity lookup under {sort:?}"),
        );
    }
}

#[tokio::test]
async fn unwired_history_service_degrades_to_the_created_at_fallback() {
    // No `with_group_message_history` call: the facade must keep today's
    // ordering under `sort=last_activity` and report `created_at`, never fail
    // and never emit `None`.
    let fixture = Fixture::new_with_activity_services(
        None,
        Arc::new(StubSessionActivity::new(&[])),
    )
    .await;
    fixture.add_public_bot("viewer").await;
    for (group_id, created_at) in [("g-a", 100u64), ("g-b", 200), ("g-c", 300)] {
        seed_viewer_chat_group(&fixture, group_id, created_at).await;
    }

    for sort in [GroupListSort::CreatedAt, GroupListSort::LastActivity] {
        let page = fixture
            .service
            .list_groups(activity_list_command("viewer", sort))
            .await
            .expect("an unwired facade must still serve the list");
        assert_eq!(
            summary_ids(&page.items),
            vec!["g-c", "g-b", "g-a"],
            "an unwired facade keeps the created_at order under {sort:?}"
        );
        assert_eq!(activity_of(&page.items, "g-a"), Some(100));
        assert_eq!(activity_of(&page.items, "g-b"), Some(200));
        assert_eq!(activity_of(&page.items, "g-c"), Some(300));
    }
}

// ---------------------------------------------------------------------------
// Public discovery catalog: no activity read, no field (D4/Review Focus #5)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn list_public_groups_keeps_its_order_omits_the_field_and_reads_no_activity() {
    let history = Arc::new(StubActivityHistory::new(&[("pub-new", 9_000)]));
    let sessions = Arc::new(StubSessionActivity::new(&[("pub-new", 9_500)]));
    let fixture =
        Fixture::new_with_activity_services(Some(history.clone()), sessions.clone()).await;
    fixture.add_public_bot("viewer").await;
    // `updated_at DESC` is the catalog order; `created_at` deliberately
    // disagrees so a created_at sort cannot pass this case.
    for (group_id, created_at, updated_at) in [
        ("pub-new", 100u64, 30u64),
        ("pub-mid", 200, 20),
        ("pub-old", 300, 10),
    ] {
        let mut group = normal_group(
            group_id,
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            created_at,
        );
        group.updated_at = updated_at;
        group.visibility = "public".into();
        fixture.groups.upsert(group).await.expect("store public Group");
    }

    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 20,
            q: None,
            strategy: None,
        })
        .await
        .expect("list public Groups");

    assert_eq!(summary_ids(&page.items), vec!["pub-new", "pub-mid", "pub-old"]);
    for item in &page.items {
        assert_eq!(summary_activity(item), None);
        assert_eq!(summary_membership(item), Membership::None);
    }
    assert!(
        history.requests().is_empty(),
        "the public catalog must not read message activity"
    );
    assert!(
        sessions.requests().is_empty(),
        "the public catalog must not read session activity"
    );
}
