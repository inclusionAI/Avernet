//! Query-budget and failure surfacing for the V1 Session participant
//! avatars (bot-avatar plan Task 7).
//!
//! A delegating registry double wraps the real `BotCore` and counts the
//! observable entry families separately: the pre-existing authorization/
//! view-actor reads (`try_get`), the pre-existing detail name reads
//! (`get_including_deleted`), and the new batched `avatar_urls` display read
//! (with the exact submitted ids). Design §5.3: the ONLY new read per session
//! list page is one batched avatar query over the page's own visible, dedup'd
//! Bot ids; the `participants` summary rows add no registry name reads at all.
//! Avatar read failures surface as service errors, and an unauthorized caller
//! reads no avatar batch.

use super::*;
use bcs_service_api::types::bot_profile::{AvatarChange, ProfileWrite};
use bcs_service_api::{AgentCredentials, EnsureHumanResult, RegisteredBot, ServiceError};
use bcs_test_support::{
    NoopCollaborationRuntimeService, NoopGroupMessageHistoryService, NoopSystemMessageService,
};
use serde_json::{Value, json};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

/// The `avatars` submodule's temp-backed core keeps this suite's private
/// helper shape: stored-profile writes must not pollute the crate directory.
/// It is duplicated here (sibling modules do not share private items), in the
/// same spirit as the group suite's parallel helpers.
fn avatar_backed_core() -> (tempfile::TempDir, Arc<BotCore>) {
    let dir = tempfile::tempdir().expect("temp bots base dir");
    let bots = Arc::new(BotCore::with_base_dir(dir.path().to_path_buf()));
    (dir, bots)
}

async fn seed_avatar(bots: &BotCore, bot_id: &str, url: &str) {
    assert_eq!(
        bots.update_avatar_existing(bot_id, AvatarChange::Set(url.to_string()))
            .await
            .expect("seed avatar"),
        ProfileWrite::Updated
    );
}

fn participant_row<'a>(value: &'a Value, actor: &str) -> &'a Value {
    value["participants"]
        .as_array()
        .unwrap_or_else(|| panic!("participants array missing for {actor}"))
        .iter()
        .find(|item| item["actor_id"] == actor)
        .unwrap_or_else(|| panic!("participant row for {actor}"))
}

fn list_viewing(viewer: &str) -> ListSessions {
    ListSessions {
        caller: bot_principal(viewer),
        group_id: "g1".into(),
        view_bot_id: Some(viewer.into()),
        offset: 0,
        limit: 10,
        status: None,
    }
}

// ---------------------------------------------------------------------------
// Registry double: separate counters for the read families
// ---------------------------------------------------------------------------

struct CountingRegistry {
    inner: Arc<BotCore>,
    name_reads: AtomicUsize,
    get_reads: AtomicUsize,
    view_actor_reads: AtomicUsize,
    avatar_calls: AtomicUsize,
    submitted_avatar_ids: Mutex<Vec<Vec<String>>>,
    fail_avatar: AtomicBool,
}

impl CountingRegistry {
    fn new(inner: Arc<BotCore>) -> Self {
        Self {
            inner,
            name_reads: AtomicUsize::new(0),
            get_reads: AtomicUsize::new(0),
            view_actor_reads: AtomicUsize::new(0),
            avatar_calls: AtomicUsize::new(0),
            submitted_avatar_ids: Mutex::new(Vec::new()),
            fail_avatar: AtomicBool::new(false),
        }
    }

    fn avatar_calls(&self) -> usize {
        self.avatar_calls.load(Ordering::SeqCst)
    }

    fn submitted_ids(&self) -> Vec<String> {
        self.submitted_avatar_ids
            .lock()
            .expect("avatar ids lock")
            .last()
            .cloned()
            .unwrap_or_default()
    }
}

#[async_trait]
impl BotRegistryCoreService for CountingRegistry {
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.inner.register(bot_id, capabilities).await
    }

    async fn update_status(&self, bot_id: &str) -> bool {
        self.inner.update_status(bot_id).await
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.get_reads.fetch_add(1, Ordering::SeqCst);
        self.inner.get(bot_id).await
    }

    async fn try_get(&self, bot_id: &str) -> ServiceResult<Option<RegisteredBot>> {
        self.view_actor_reads.fetch_add(1, Ordering::SeqCst);
        self.inner.try_get(bot_id).await
    }

    async fn get_including_deleted(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.name_reads.fetch_add(1, Ordering::SeqCst);
        self.inner.get_including_deleted(bot_id).await
    }

    async fn get_agent_credentials(&self, bot_id: &str) -> Option<AgentCredentials> {
        self.inner.get_agent_credentials(bot_id).await
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.inner.list_active().await
    }

    async fn list_uninitialized_bots_by_creator(
        &self,
        created_by: &str,
        limit: usize,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        self.inner
            .list_uninitialized_bots_by_creator(created_by, limit)
            .await
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.inner.list_bots_by_creator(created_by).await
    }

    async fn discover(&self, query: &str) -> Vec<RegisteredBot> {
        self.inner.discover(query).await
    }

    async fn find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot> {
        self.inner.find_by_skills(skills).await
    }

    async fn find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot> {
        self.inner.find_by_domains(domains).await
    }

    async fn find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot> {
        self.inner.find_by_scopes(scopes).await
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.inner.unregister(bot_id).await
    }

    async fn cleanup_expired(&self) {
        self.inner.cleanup_expired().await
    }

    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        self.inner.load_from_storage(bot_id).await
    }

    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
        self.inner.save_to_storage(bot_id, caps).await
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        self.inner.update_visibility(bot_id, visibility).await
    }

    async fn update_actor_status(&self, bot_id: &str, status: ActorStatus) -> ServiceResult<()> {
        self.inner.update_actor_status(bot_id, status).await
    }

    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        self.inner.ensure_human_actor(staff_no, nick_name).await
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.inner.has_been_onboarded(bot_id).await
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        self.inner
            .save_created_by(bot_id, created_by, overwrite)
            .await
    }

    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        self.inner.save_token(bot_id, token).await
    }

    async fn load_token(&self, bot_id: &str) -> Option<String> {
        self.inner.load_token(bot_id).await
    }

    async fn find_bot_by_token(&self, token: &str) -> Option<String> {
        self.inner.find_bot_by_token(token).await
    }

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
        self.inner.register_streaming_connection(bot_id).await
    }

    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        self.inner.reconnect_streaming(existing_token).await
    }

    async fn disconnect_streaming(&self, bot_id: &str) {
        self.inner.disconnect_streaming(bot_id).await
    }

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.inner.is_connected(bot_id).await
    }

    async fn send_frame(&self, bot_id: &str, frame: String) -> Result<(), ()> {
        self.inner.send_frame(bot_id, frame).await
    }

    async fn list_connected(&self) -> Vec<String> {
        self.inner.list_connected().await
    }

    async fn store_token_mapping(&self, token: String, bot_id: String) {
        self.inner.store_token_mapping(token, bot_id).await
    }

    async fn register_http_connection(&self, bot_id: String, token: String) -> String {
        self.inner.register_http_connection(bot_id, token).await
    }

    async fn update_profile_existing(
        &self,
        bot_id: &str,
        update: bcs_service_api::types::bot_profile::ExistingProfileUpdate,
    ) -> ServiceResult<ProfileWrite> {
        self.inner.update_profile_existing(bot_id, update).await
    }

    async fn update_avatar_existing(
        &self,
        bot_id: &str,
        avatar: AvatarChange,
    ) -> ServiceResult<ProfileWrite> {
        self.inner.update_avatar_existing(bot_id, avatar).await
    }

    async fn avatar_urls(
        &self,
        bot_ids: &[String],
    ) -> ServiceResult<bcs_service_api::types::bot_profile::BotAvatarMap> {
        self.avatar_calls.fetch_add(1, Ordering::SeqCst);
        self.submitted_avatar_ids
            .lock()
            .expect("avatar ids lock")
            .push(bot_ids.to_vec());
        if self.fail_avatar.load(Ordering::SeqCst) {
            return Err(ServiceError::InternalError(
                "avatar backend unavailable".to_string(),
            ));
        }
        self.inner.avatar_urls(bot_ids).await
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()> {
        self.inner.set_hidden(bot_id, hidden).await
    }
}

struct CountingFixture {
    service: SessionServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    session_repo: Arc<MemorySessionRepo>,
    counting: Arc<CountingRegistry>,
    authority_repo: Arc<MemoryBotRepo>,
    _bots_dir: tempfile::TempDir,
}

async fn counting_fixture() -> CountingFixture {
    let group_repo: Arc<dyn bcs_service_api::port::repo::GroupRepoPort> =
        Arc::new(MemoryGroupRepo::new());
    let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
    let (bots_dir, bots) = avatar_backed_core();
    let counting = Arc::new(CountingRegistry::new(bots.clone()));
    let relation = Arc::new(RelationCore::memory());
    let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
    let session_repo: Arc<MemorySessionRepo> = Arc::new(MemorySessionRepo::new());
    let sessions = Arc::new(SessionManagementServiceImpl::new(
        session_repo.clone(),
        group_repo.clone(),
    ));
    let authority_repo = Arc::new(MemoryBotRepo::new());
    let authority_hook = bcs_test_support::authority_hook_for(authority_repo.clone());
    let launch = Arc::new(
        SessionLaunchApplication::new(
            bots.clone(),
            groups.clone(),
            sessions.clone(),
            Arc::new(NoopCollaborationRuntimeService),
            Arc::new(NoopSystemMessageService),
        )
        .with_bot_authority_hook(authority_hook.clone()),
    );
    let service = SessionServiceImpl::new(
        launch,
        sessions,
        groups.clone(),
        counting.clone(),
        friends,
        session_repo.clone(),
        Arc::new(NoopGroupMessageHistoryService),
        Arc::new(NoopCollaborationRuntimeService),
        Arc::new(NoopSystemMessageService),
    )
    .with_bot_authority_hook(authority_hook)
    .with_session_authorization_core(Arc::new(
        bcs_session::SessionAuthorizationCoreImpl::new(session_repo.clone()),
    ));
    CountingFixture {
        service,
        groups,
        bots,
        session_repo,
        counting,
        authority_repo,
        _bots_dir: bots_dir,
    }
}

async fn register_public_bot(fixture: &CountingFixture, bot_uuid: &str) {
    fixture
        .bots
        .register(
            bot_uuid.to_string(),
            BotCapabilities {
                name: Some(bot_uuid.to_string()),
                visibility: "public".into(),
                ..Default::default()
            },
        )
        .await
        .expect("register bot");
    fixture
        .bots
        .save_created_by(bot_uuid, bot_uuid, true)
        .await
        .expect("assign test Bot owner");
    bcs_test_support::grant_owner_edge(&fixture.authority_repo, bot_uuid, bot_uuid).await;
}

async fn store_group_with_originator(fixture: &CountingFixture, driver: &str, originator: &str) {
    let mut group = Group::new("g1", driver, vec![Participant::bot(driver, ParticipantRole::Driver)]);
    group.originator = Some(originator.to_string());
    group.label = Some("g1".to_string());
    group.group_strategy = GroupStrategy::Chat;
    fixture
        .groups
        .upsert(group)
        .await
        .expect("store group");
}

async fn seed_session(fixture: &CountingFixture, participants: Vec<Participant>) -> String {
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants,
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    session.id
}

// ---------------------------------------------------------------------------
// One batched avatar query per page, zero new name reads
// ---------------------------------------------------------------------------

#[tokio::test]
async fn session_list_avatar_query_budget_is_one_batch_with_zero_new_name_reads() {
    let fixture = counting_fixture().await;
    for bot in ["budget-viewer", "budget-driver", "budget-a", "budget-b"] {
        register_public_bot(&fixture, bot).await;
    }
    store_group_with_originator(&fixture, "budget-driver", "human_budget-driver").await;
    seed_avatar(&fixture.bots, "budget-driver", "https://cdn.example.com/budget.png").await;

    let mut snapshot_row = Participant::bot("budget-a", ParticipantRole::Worker);
    snapshot_row.bot_name = Some("budget-a-snapshot".to_string());
    // S1: driver + a named-snapshot member + the viewer + a Human participant.
    seed_session(
        &fixture,
        vec![
            Participant::bot("budget-driver", ParticipantRole::Driver),
            snapshot_row,
            Participant::bot("budget-viewer", ParticipantRole::Worker),
            Participant::human("human_bob", ParticipantRole::Observer),
        ],
    )
    .await;
    // S2 shares driver and viewer with S1; budget-b has NO name snapshot, so
    // any name backfill for the new summary rows would be observable.
    seed_session(
        &fixture,
        vec![
            Participant::bot("budget-driver", ParticipantRole::Driver),
            Participant::bot("budget-b", ParticipantRole::Worker),
            Participant::bot("budget-viewer", ParticipantRole::Worker),
        ],
    )
    .await;

    let name_reads_before = fixture.counting.name_reads.load(Ordering::SeqCst);
    let view_actor_reads_before = fixture.counting.view_actor_reads.load(Ordering::SeqCst);
    let page = SessionService::list(&fixture.service, list_viewing("budget-viewer"))
        .await
        .expect("list sessions");
    assert_eq!(page.total, 2);
    assert_eq!(page.items.len(), 2);

    // The ONLY new read is ONE batched avatar query for the whole page: the
    // shared driver and viewer are submitted once each, and the Human
    // participant never joins a Bot display read.
    assert_eq!(fixture.counting.avatar_calls(), 1);
    let mut submitted = fixture.counting.submitted_ids();
    submitted.sort();
    assert_eq!(
        submitted,
        ["budget-a", "budget-b", "budget-driver", "budget-viewer"]
    );

    // Zero registry NAME reads for the list summaries — the missing snapshot
    // and every extra row add none (a session list never backfills names).
    assert_eq!(
        fixture.counting.name_reads.load(Ordering::SeqCst),
        name_reads_before
    );
    assert_eq!(fixture.counting.name_reads.load(Ordering::SeqCst), 0);
    // The pre-existing authorization read stays one view-actor resolution per
    // list call; the avatar fill adds no registry read of its own.
    assert_eq!(
        fixture.counting.view_actor_reads.load(Ordering::SeqCst) - view_actor_reads_before,
        1
    );
    assert_eq!(fixture.counting.get_reads.load(Ordering::SeqCst), 0);

    // The fetched values actually populate the rows, the Human participant
    // resolves null, and S2's missing name snapshot stays null (a session list
    // never backfills the summary `name` column).
    let items_value = serde_json::to_value(&page.items).expect("serialize page items");
    let s1_value = items_value
        .as_array()
        .expect("page items")
        .iter()
        .find(|item| {
            item["participants"]
                .as_array()
                .is_some_and(|rows| rows.iter().any(|row| row["actor_id"] == "budget-a"))
        })
        .expect("S1 summary");
    assert_eq!(
        participant_row(s1_value, "budget-driver")["avatar_url"],
        json!("https://cdn.example.com/budget.png")
    );
    assert!(participant_row(s1_value, "human_bob")["avatar_url"].is_null());
    let s2_value = items_value
        .as_array()
        .expect("page items")
        .iter()
        .find(|item| {
            item["participants"]
                .as_array()
                .is_some_and(|rows| rows.iter().any(|row| row["actor_id"] == "budget-b"))
        })
        .expect("S2 summary");
    assert!(participant_row(s2_value, "budget-b")["name"].is_null());

    // Re-listing reads exactly ONE more batch for the new page and still adds
    // zero name reads: the budget is per page, not per process.
    let page = SessionService::list(&fixture.service, list_viewing("budget-viewer"))
        .await
        .expect("re-list sessions");
    assert_eq!(page.items.len(), 2);
    assert_eq!(fixture.counting.avatar_calls(), 2);
    assert_eq!(fixture.counting.name_reads.load(Ordering::SeqCst), 0);
}

// ---------------------------------------------------------------------------
// Read failures surface as service errors (design §5.3)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn avatar_read_failure_surfaces_as_service_error() {
    let fixture = counting_fixture().await;
    for bot in ["failing-viewer", "failing-driver", "failing-guest"] {
        register_public_bot(&fixture, bot).await;
    }
    store_group_with_originator(&fixture, "failing-driver", "human_failing-driver").await;
    let session_id = seed_session(
        &fixture,
        vec![
            Participant::bot("failing-driver", ParticipantRole::Driver),
            Participant::bot("failing-viewer", ParticipantRole::Worker),
        ],
    )
    .await;

    fixture
        .counting
        .fail_avatar
        .store(true, Ordering::SeqCst);
    let list_error = SessionService::list(&fixture.service, list_viewing("failing-viewer"))
        .await
        .expect_err("list must surface the avatar read failure");
    assert!(matches!(
        list_error,
        bcs_service_api::application::v1::ApplicationError::Internal(_)
    ));
    let detail_error = fixture
        .service
        .get(GetSession {
            caller: bot_principal("failing-viewer"),
            session_id: session_id.clone(),
        })
        .await
        .expect_err("detail must surface the avatar read failure");
    assert!(matches!(
        detail_error,
        bcs_service_api::application::v1::ApplicationError::Internal(_)
    ));
    let mutation_error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("failing-driver"),
            session_id: session_id.clone(),
            bot_uuid: "failing-guest".into(),
            message_view_scope: None,
        })
        .await
        .expect_err("mutation response must surface the avatar read failure");
    assert!(matches!(
        mutation_error,
        bcs_service_api::application::v1::ApplicationError::Internal(_)
    ));

    // The three rejected surfaces each attempted their one batched read
    // before failing — the error came from the read, not from a skip.
    assert_eq!(fixture.counting.avatar_calls(), 3);

    // The same fixture succeeds once the backend recovers, proving the error
    // came from the avatar read rather than the wiring.
    fixture
        .counting
        .fail_avatar
        .store(false, Ordering::SeqCst);
    let page = SessionService::list(&fixture.service, list_viewing("failing-viewer"))
        .await
        .expect("list after recovery");
    assert_eq!(page.items.len(), 1);
    assert_eq!(fixture.counting.avatar_calls(), 4);
}

// ---------------------------------------------------------------------------
// A committed mode update surfaces the avatar failure as Internal (never 404)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn avatar_read_failure_on_update_participant_is_internal_not_not_found() {
    let fixture = counting_fixture().await;
    for bot in ["update-driver", "update-worker"] {
        register_public_bot(&fixture, bot).await;
    }
    store_group_with_originator(&fixture, "update-driver", "human_update-driver").await;
    let session_id = seed_session(
        &fixture,
        vec![
            Participant::bot("update-driver", ParticipantRole::Driver),
            Participant::bot("update-worker", ParticipantRole::Worker),
        ],
    )
    .await;

    // The registry avatar read fails from now on. The response projection
    // of an already-committed mode update must surface that Internal error
    // and must not disguise it as a `participant_not_found` 404 (design
    // §5.3: map service errors; never masquerade).
    fixture
        .counting
        .fail_avatar
        .store(true, Ordering::SeqCst);
    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("update-worker"),
            session_id: session_id.clone(),
            bot_uuid: "update-worker".into(),
            mode: Some(ParticipantMode::Muted),
            message_view_scope: None,
        })
        .await
        .expect_err("update response must surface the avatar read failure");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Internal(_)
        ),
        "expected Internal, got {error:?}",
    );
    assert_eq!(error.code(), "internal_error");

    // Fail-after-commit, honestly documented: the mode change itself is
    // durable even though the caller saw an error response.
    let stored = fixture
        .session_repo
        .get(&session_id)
        .await
        .expect("re-read stored session");
    let stored_mode = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "update-worker")
        .expect("stored participant")
        .mode;
    assert_eq!(stored_mode, Some(ParticipantMode::Muted));

    // Once the avatar backend recovers, the same update flow returns the
    // committed participant row.
    fixture
        .counting
        .fail_avatar
        .store(false, Ordering::SeqCst);
    let updated = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            caller: human_principal("update-worker"),
            session_id,
            bot_uuid: "update-worker".into(),
            mode: Some(ParticipantMode::Muted),
            message_view_scope: None,
        })
        .await
        .expect("update after recovery");
    assert_eq!(updated.mode, ParticipantMode::Muted);
    assert!(updated.avatar_url.is_none());
}

// ---------------------------------------------------------------------------
// Scope denial reads no avatar batch at all
// ---------------------------------------------------------------------------

#[tokio::test]
async fn unauthorized_caller_gets_no_avatar_batch() {
    let fixture = counting_fixture().await;
    for bot in ["outsider", "private-driver"] {
        register_public_bot(&fixture, bot).await;
    }
    store_group_with_originator(&fixture, "private-driver", "human_private-driver").await;
    let session_id = seed_session(
        &fixture,
        vec![Participant::bot("private-driver", ParticipantRole::Driver)],
    )
    .await;
    seed_avatar(&fixture.bots, "private-driver", "https://cdn.example.com/private.png").await;

    // An explicit View Actor the caller does not control is rejected before
    // any avatar read.
    let wrong_actor = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_principal("outsider"),
            group_id: "g1".into(),
            view_bot_id: Some("private-driver".into()),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect_err("foreign view actor must be rejected");
    assert!(matches!(
        wrong_actor,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    // A scoped list with no membership returns an empty page without reads.
    let page = SessionService::list(&fixture.service, list_viewing("outsider"))
        .await
        .expect("list for non-member");
    assert_eq!(page.total, 0);
    assert_eq!(page.items.len(), 0);

    // A detail read for a non-participant stays forbidden.
    let detail_error = fixture
        .service
        .get(GetSession {
            caller: bot_principal("outsider"),
            session_id: session_id.clone(),
        })
        .await
        .expect_err("detail must stay forbidden");
    assert!(matches!(
        detail_error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
    assert_eq!(fixture.counting.avatar_calls(), 0);

    // The same fixture succeeds for the authorized participant, proving the
    // zero above came from the authorization boundary, not the wiring.
    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("private-driver"),
            session_id: session_id.clone(),
        })
        .await
        .expect("participant reads the detail");
    let detail_value = serde_json::to_value(&detail).expect("serialize detail");
    assert_eq!(
        participant_row(&detail_value, "private-driver")["avatar_url"],
        json!("https://cdn.example.com/private.png")
    );
    assert_eq!(fixture.counting.avatar_calls(), 1);
}
