//! Query-budget and failure surfacing for the V1 participant avatars
//! (bot-avatar plan Task 6).
//!
//! A delegating registry double wraps the real `BotCore` and counts the two
//! observable entry families: the batched `avatar_urls` display read (with the
//! exact submitted ids) and the pre-existing name-read entries
//! (`get_including_deleted`). Design §5.3: the ONLY new read per list page is
//! one batched avatar query over the page's visible, deduplicated Bot ids; the
//! new `participants` summary rows add no registry name reads. Avatar read
//! failures surface as service errors, and a caller with no authorized
//! relation reads no avatar batch at all.

use super::*;
use bcs_service_api::application::v1::{AddGroupParticipant, Page, UpdateGroupParticipant};
use bcs_service_api::types::bot_profile::{AvatarChange, ProfileWrite};
use bcs_service_api::{
    ActorStatus, AgentCredentials, EnsureHumanResult, ParticipantMode, RegisteredBot,
};
use serde_json::{Value, json};

/// The `avatars` submodule's temp-backed core keeps this suite's private
/// helper shape: stored-profile writes must not pollute the crate directory.
/// It is duplicated here (sibling modules do not share private items), in the
/// same spirit as the parallel `Fixture`/`normal_group` helpers of the parent
/// suites.
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

fn find_summary_json<'a>(
    page: &'a Page<GroupSummary>,
    group_id: &str,
) -> (&'a GroupSummary, Value) {
    let summary = page
        .items
        .iter()
        .find(|item| match item {
            GroupSummary::Normal(summary) => summary.group_id == group_id,
            GroupSummary::DirectMessage(summary) => summary.group_id == group_id,
        })
        .expect("summary for group");
    let value = serde_json::to_value(summary).expect("serialize summary");
    (summary, value)
}

fn participant_row<'a>(value: &'a Value, actor: &str) -> &'a Value {
    value["participants"]
        .as_array()
        .expect("participants array")
        .iter()
        .find(|item| item["actor_id"] == actor)
        .expect("participant row")
}

fn list_command(viewer: &str) -> ListGroups {
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
    }
}


// ---------------------------------------------------------------------------
// Read-failure and unauthorized-caller surfacing (design §5.3)
// ---------------------------------------------------------------------------

struct CountingRegistry {
    inner: Arc<dyn BotRegistryCoreService>,
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
    service: GroupServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    counting: Arc<CountingRegistry>,
    authority_repo: Arc<MemoryBotRepo>,
    _bots_dir: tempfile::TempDir,
}

async fn counting_fixture() -> CountingFixture {
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
    let (bots_dir, bots) = avatar_backed_core();
    let counting = Arc::new(CountingRegistry::new(bots.clone()));
    let relation = Arc::new(RelationCore::memory());
    let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
    let sessions = Arc::new(SessionManagementServiceImpl::new(
        Arc::new(MemorySessionRepo::new()),
        group_repo.clone(),
    ));
    let system_message: Arc<dyn SystemMessageService> = Arc::new(NoopSystemMessageService);
    let authority_repo = Arc::new(MemoryBotRepo::new());
    let management = Arc::new(
        GroupManagement::new(
            groups.clone(),
            bots.clone(),
            friends.clone(),
            relation.clone(),
            GroupConfig::default(),
            sessions.clone(),
            system_message,
        )
        .with_bot_authority(bcs_test_support::authority_hook_for(
            authority_repo.clone(),
        ))
        .for_v1_openapi(),
    );
    let service = GroupServiceImpl::new(
        groups.clone(),
        counting.clone(),
        friends.clone(),
        sessions.clone(),
        management,
    )
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(
        authority_repo.clone(),
    ));
    CountingFixture {
        service,
        groups,
        bots,
        counting,
        authority_repo,
        _bots_dir: bots_dir,
    }
}

async fn register_public_bot(fixture: &CountingFixture, bot_uuid: &str) {
    let capabilities = BotCapabilities {
        name: Some(bot_uuid.to_string()),
        visibility: "public".into(),
        ..Default::default()
    };
    fixture
        .bots
        .register(bot_uuid.to_string(), capabilities)
        .await
        .expect("register bot");
    fixture
        .bots
        .save_created_by(bot_uuid, bot_uuid, true)
        .await
        .expect("assign test Bot owner");
    bcs_test_support::grant_owner_edge(&fixture.authority_repo, bot_uuid, bot_uuid).await;
}

#[tokio::test]
async fn group_list_avatar_query_budget_is_one_batch_with_zero_new_name_reads() {
    let fixture = counting_fixture().await;
    for bot in ["budget-viewer", "budget-driver", "budget-a", "budget-b"] {
        register_public_bot(&fixture, bot).await;
    }
    seed_avatar(&fixture.bots, "budget-driver", "https://cdn.example.com/budget.png").await;

    // Two groups share the driver, so the page's participant rows repeat it.
    let mut shared = normal_group(
        "budget-shared",
        "budget-driver",
        vec![
            Participant::bot("budget-driver", ParticipantRole::Driver),
            Participant::bot("budget-viewer", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        30,
    );
    // Every member carries a valid snapshot, so the pre-existing name backfill
    // reads nothing on this page.
    for participant in &mut shared.participants {
        participant.bot_name = Some(format!("{}-snapshot", participant.bot_uuid));
    }
    fixture.groups.upsert(shared).await.expect("store shared");

    let mut missing = normal_group(
        "budget-missing",
        "budget-driver",
        vec![
            Participant::bot("budget-driver", ParticipantRole::Driver),
            Participant::bot("budget-a", ParticipantRole::Consultant),
            Participant::bot("budget-viewer", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        20,
    );
    for participant in &mut missing.participants {
        participant.bot_name = Some(format!("{}-snapshot", participant.bot_uuid));
    }
    fixture.groups.upsert(missing).await.expect("store missing");

    let mut second = normal_group(
        "budget-second",
        "budget-driver",
        vec![
            Participant::bot("budget-driver", ParticipantRole::Driver),
            Participant::bot("budget-b", ParticipantRole::Consultant),
            Participant::bot("budget-viewer", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        10,
    );
    for participant in &mut second.participants {
        participant.bot_name = Some(format!("{}-snapshot", participant.bot_uuid));
    }
    fixture.groups.upsert(second).await.expect("store second");

    let name_reads_before = fixture.counting.name_reads.load(Ordering::SeqCst);
    let page = fixture
        .service
        .list_groups(list_command("budget-viewer"))
        .await
        .expect("list groups");
    assert_eq!(page.total, 3);
    assert_eq!(page.items.len(), 3);

    // The ONLY new read is one batched avatar query for the whole page; the
    // shared driver appears once in the submitted batch.
    assert_eq!(fixture.counting.avatar_calls(), 1);
    let mut submitted = fixture.counting.submitted_ids();
    submitted.sort();
    assert_eq!(
        submitted,
        ["budget-a", "budget-b", "budget-driver", "budget-viewer"]
    );

    // Names: all snapshots are valid on this page, so the existing backfill
    // (records 0 reads here) stays untouched by the new summary rows — not
    // even the extra participant row for each group adds a name read.
    assert_eq!(
        fixture.counting.name_reads.load(Ordering::SeqCst),
        name_reads_before
    );
    assert_eq!(fixture.counting.name_reads.load(Ordering::SeqCst), 0);

    // The fetched values actually populate the rows.
    let (_summary, value) = find_summary_json(&page, "budget-shared");
    assert_eq!(
        participant_row(&value, "budget-driver")["avatar_url"],
        json!("https://cdn.example.com/budget.png")
    );

    // One member name missing => exactly the pre-existing single backfill
    // read for that member on the next page; the summary rows themselves
    // still add no name read, and the whole page stays one avatar batch.
    let mut missing = normal_group(
        "budget-missing",
        "budget-driver",
        vec![
            Participant::bot("budget-driver", ParticipantRole::Driver),
            Participant::bot("budget-a", ParticipantRole::Consultant),
            Participant::bot("budget-viewer", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        20,
    );
    missing.participants[0].bot_name = None;
    missing.participants[1].bot_name = Some("budget-a-snapshot".to_string());
    missing.participants[2].bot_name = Some("budget-viewer-snapshot".to_string());
    fixture
        .groups
        .upsert(missing)
        .await
        .expect("store missing with a name gap");
    let name_reads_before = fixture.counting.name_reads.load(Ordering::SeqCst);
    let page = fixture
        .service
        .list_groups(list_command("budget-viewer"))
        .await
        .expect("re-list groups");
    assert_eq!(page.items.len(), 3);
    assert_eq!(
        fixture.counting.name_reads.load(Ordering::SeqCst) - name_reads_before,
        1
    );
    assert_eq!(fixture.counting.avatar_calls(), 2);
    let mut submitted = fixture.counting.submitted_ids();
    submitted.sort();
    assert_eq!(
        submitted,
        ["budget-a", "budget-b", "budget-driver", "budget-viewer"]
    );
}

#[tokio::test]
async fn avatar_read_failure_surfaces_as_service_error() {
    let fixture = counting_fixture().await;
    for bot in ["failing-viewer", "failing-driver"] {
        register_public_bot(&fixture, bot).await;
    }
    fixture
        .groups
        .upsert(normal_group(
            "failing-group",
            "failing-driver",
            vec![
                Participant::bot("failing-driver", ParticipantRole::Driver),
                Participant::bot("failing-viewer", ParticipantRole::Consultant),
            ],
            GroupStrategy::Chat,
            10,
        ))
        .await
        .expect("store group");

    fixture
        .counting
        .fail_avatar
        .store(true, Ordering::SeqCst);
    let list_error = fixture
        .service
        .list_groups(list_command("failing-viewer"))
        .await
        .expect_err("list must surface the avatar read failure");
    assert!(matches!(list_error, ApplicationError::Internal(_)));
    let detail_error = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("failing-viewer"),
            group_id: "failing-group".into(),
        })
        .await
        .expect_err("detail must surface the avatar read failure");
    assert!(matches!(detail_error, ApplicationError::Internal(_)));

    // The same fixture succeeds once the backend recovers, proving the error
    // came from the avatar read rather than the wiring.
    fixture.counting.fail_avatar.store(false, Ordering::SeqCst);
    let page = fixture
        .service
        .list_groups(list_command("failing-viewer"))
        .await
        .expect("list after recovery");
    assert_eq!(page.items.len(), 1);
}

#[tokio::test]
async fn unauthorized_caller_gets_no_avatar_rows() {
    let fixture = counting_fixture().await;
    for bot in ["outside-viewer", "private-driver"] {
        register_public_bot(&fixture, bot).await;
    }
    fixture
        .groups
        .upsert(normal_group(
            "private-group",
            "private-driver",
            vec![Participant::bot("private-driver", ParticipantRole::Driver)],
            GroupStrategy::Chat,
            10,
        ))
        .await
        .expect("store private group");

    // A caller with no membership and no Session relation sees an empty page
    // and a Forbidden detail — and no avatar batch is read at all.
    let unauthorized = ListGroups {
        caller: bot_principal("outside-viewer"),
        view_bot_id: Some("outside-viewer".into()),
        offset: 0,
        limit: 10,
        q: None,
        visibility: None,
        membership: MembershipFilter::All,
        kind: GroupKindFilter::All,
        strategy: None,
    };
    let page = fixture
        .service
        .list_groups(unauthorized)
        .await
        .expect("list groups");
    assert_eq!(page.items.len(), 0);
    let detail_error = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("outside-viewer"),
            group_id: "private-group".into(),
        })
        .await
        .expect_err("detail read must stay forbidden");
    assert!(matches!(detail_error, ApplicationError::Forbidden(_)));
    assert_eq!(fixture.counting.avatar_calls(), 0);
}

#[tokio::test]
async fn public_page_avatar_query_budget_is_one_batch_and_human_rows_are_never_submitted() {
    let fixture = counting_fixture().await;
    for bot in ["pub-driver", "pub-member"] {
        register_public_bot(&fixture, bot).await;
    }
    seed_avatar(&fixture.bots, "pub-driver", "https://cdn.example.com/pub-driver.png").await;
    // The Human member exists in the registry with NO avatar; its row stays
    // visible in the public participant summary but never joins the Bot
    // display read.
    fixture
        .bots
        .ensure_human_actor("pub-member", "Human Member")
        .await
        .expect("registry Human row");

    let mut group = normal_group(
        "pub-budget",
        "pub-driver",
        vec![
            Participant::bot("pub-driver", ParticipantRole::Driver),
            Participant::bot("pub-member", ParticipantRole::Consultant),
            Participant::human("human_pub-member", ParticipantRole::Observer),
        ],
        GroupStrategy::Chat,
        10,
    );
    group.visibility = "public".into();
    for participant in &mut group.participants {
        participant.bot_name = Some(format!("{}-snapshot", participant.bot_uuid));
    }
    fixture.groups.upsert(group).await.expect("store group");

    let name_reads_before = fixture.counting.name_reads.load(Ordering::SeqCst);
    let page = fixture
        .service
        .list_public_groups(ListPublicGroups {
            offset: 0,
            limit: 10,
            q: None,
            strategy: None,
        })
        .await
        .expect("list public groups");
    assert_eq!(page.items.len(), 1);

    // ONE batched avatar read serves the whole public page; only the Bot
    // member ids are submitted — the Human row resolves null on the wire
    // without ever reaching the display-profile read.
    assert_eq!(fixture.counting.avatar_calls(), 1);
    let mut submitted = fixture.counting.submitted_ids();
    submitted.sort();
    assert_eq!(submitted, ["pub-driver", "pub-member"]);
    assert_eq!(
        fixture.counting.name_reads.load(Ordering::SeqCst),
        name_reads_before
    );
    assert_eq!(fixture.counting.name_reads.load(Ordering::SeqCst), 0);

    let (_summary, value) = find_summary_json(&page, "pub-budget");
    assert_eq!(
        participant_row(&value, "human_pub-member")["avatar_url"],
        Value::Null
    );
    assert_eq!(
        participant_row(&value, "pub-driver")["avatar_url"],
        json!("https://cdn.example.com/pub-driver.png")
    );
    assert_eq!(
        participant_row(&value, "pub-member")["avatar_url"],
        Value::Null
    );
}

#[tokio::test]
async fn avatar_read_failure_on_add_participant_is_internal_after_commit() {
    let fixture = counting_fixture().await;
    for bot in ["add-driver", "add-worker"] {
        register_public_bot(&fixture, bot).await;
    }
    fixture.groups.upsert(normal_group(
        "add-failure-group", "add-driver",
        vec![Participant::bot("add-driver", ParticipantRole::Driver)],
        GroupStrategy::Chat, 10,
    )).await.expect("store group");
    fixture.counting.fail_avatar.store(true, Ordering::SeqCst);
    let error = fixture.service.add_participant(AddGroupParticipant {
        caller: human_principal_with_profile("add-driver", "add-driver", None, None),
        group_id: "add-failure-group".into(),
        actor_id: "add-worker".into(),
        message_view_scope: None,
    }).await.expect_err("add response must surface the avatar read failure");
    assert!(matches!(error, ApplicationError::Internal(_)), "expected Internal, got {error:?}");
    assert_eq!(error.code(), "internal_error");
    assert_eq!(fixture.counting.avatar_calls(), 1);
    assert_eq!(fixture.counting.submitted_ids(), vec!["add-worker"]);
    let stored = fixture.groups.get("add-failure-group").await.expect("stored group");
    assert_eq!(stored.participants.iter().filter(|p| p.bot_uuid == "add-worker").count(), 1,
        "the participant addition commits before response projection fails");

    fixture.counting.fail_avatar.store(false, Ordering::SeqCst);
    let detail = fixture.service.get(GetGroup {
        caller: bot_principal("add-driver"), group_id: "add-failure-group".into(),
    }).await.expect("detail after recovery");
    let detail = serde_json::to_value(detail).expect("serialize detail");
    assert!(detail["participants"].as_array().unwrap().iter().any(|p| p["actor_id"] == "add-worker"));
}

#[tokio::test]
async fn avatar_read_failure_on_update_participant_is_internal_after_commit() {
    let fixture = counting_fixture().await;
    for bot in ["update-driver", "update-worker"] {
        register_public_bot(&fixture, bot).await;
    }
    fixture.groups.upsert(normal_group(
        "update-failure-group", "update-driver",
        vec![Participant::bot("update-driver", ParticipantRole::Driver),
            Participant::bot("update-worker", ParticipantRole::Worker)],
        GroupStrategy::Chat, 10,
    )).await.expect("store group");
    fixture.counting.fail_avatar.store(true, Ordering::SeqCst);
    let update = || UpdateGroupParticipant {
        caller: human_principal_with_profile("update-worker", "update-worker", None, None),
        group_id: "update-failure-group".into(),
        actor_id: "update-worker".into(),
        mode: Some(ParticipantMode::Muted),
        message_view_scope: None,
    };
    let error = fixture.service.update_participant(update()).await
        .expect_err("update response must surface the avatar read failure");
    assert!(matches!(error, ApplicationError::Internal(_)), "expected Internal, got {error:?}");
    assert_eq!(error.code(), "internal_error");
    assert_eq!(fixture.counting.avatar_calls(), 1);
    assert_eq!(fixture.counting.submitted_ids(), vec!["update-worker"]);
    let stored = fixture.groups.get("update-failure-group").await.expect("stored group");
    let participant = stored.participants.iter().find(|p| p.bot_uuid == "update-worker")
        .expect("stored participant");
    assert_eq!(participant.mode, Some(ParticipantMode::Muted),
        "the mode update commits before response projection fails");

    fixture.counting.fail_avatar.store(false, Ordering::SeqCst);
    let updated = fixture.service.update_participant(update()).await.expect("update after recovery");
    assert_eq!(updated.mode, ParticipantMode::Muted);
    assert!(updated.avatar_url.is_none());
}
