use std::sync::Arc;

use async_trait::async_trait;
use bcs_app_session::SessionFileApplicationServiceImpl;
use bcs_bot::BotCore;
use bcs_domain::{GroupStrategy, SystemMessageEvent};
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::v1::{
    AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    CompleteSessionFile, ListSessionFiles, PrepareSessionFile, SessionFileApplicationService,
    SessionFileStatus, UploadSessionFileContent,
};
use bcs_service_api::application::session_files::SessionFileService;
use bcs_service_api::port::repo::{
    GroupRepoPort, NewSessionParams, SessionFileRepoPort, SessionRepoPort,
};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService, Participant,
    ParticipantRole, ServiceResult, SessionKind, SystemMessageService,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_file::{SessionFileServiceConfig, SessionFileServiceImpl};
use bcs_session_file_store::MemorySessionFileRepo;
use bcs_session_store::MemorySessionRepo;
use bcs_storage_api::{StorageCapabilities, byte_stream_from_bytes, fake::FakeStoragePlugin};
use bytes::Bytes;
use tokio::sync::Mutex;

#[derive(Default)]
struct RecordingSystemMessage {
    events: Mutex<Vec<SystemMessageEvent>>,
}

#[async_trait]
impl SystemMessageService for RecordingSystemMessage {
    async fn notify(
        &self,
        _group_id: &str,
        event: SystemMessageEvent,
        _session_id: &str,
        _session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.events.lock().await.push(event);
        Ok(1)
    }
}

/// Test no-auth shared-content URL projector for upload-completion notifications.
struct CompletionShareProjector;
impl bcs_service_api::application::v1::SessionFileInternalContentUrlProjector
    for CompletionShareProjector
{
    fn shared_content_url(&self, token: &str) -> String {
        format!("http://share.test/sessions/shared-file/content?token={token}")
    }
}

/// Mine-union double for the file-facade tests: `list_my_bots` answers
/// from the SAME live control facts as [`SeededAuthority`], so the facade's
/// identity projection and its per-Bot authority questions agree by
/// construction.
struct SeededMine {
    controlled: std::sync::Mutex<BTreeMap<String, Vec<String>>>,
}

impl SeededMine {
    fn empty() -> Self {
        Self {
            controlled: std::sync::Mutex::new(BTreeMap::new()),
        }
    }

    fn control(&self, user_id: &str, bot_id: &str) {
        self.controlled
            .lock()
            .unwrap()
            .entry(user_id.to_string())
            .or_default()
            .push(bot_id.to_string());
    }
}

#[async_trait::async_trait]
impl bcs_service_api::BotQueryService for SeededMine {
    async fn list_bots(
        &self,
        _command: bcs_service_api::BotListCommand,
    ) -> Result<bcs_service_api::BotListResult, bcs_service_api::BotUseCaseError> {
        Err(bcs_service_api::BotUseCaseError::Service(
            bcs_service_api::ServiceError::InternalError("not configured".to_string()),
        ))
    }

    async fn get_bot(
        &self,
        _command: bcs_service_api::BotDetailCommand,
    ) -> Result<bcs_service_api::BotDetailResult, bcs_service_api::BotUseCaseError> {
        Err(bcs_service_api::BotUseCaseError::Service(
            bcs_service_api::ServiceError::InternalError("not configured".to_string()),
        ))
    }

    async fn get_visibility(
        &self,
        _command: bcs_service_api::BotVisibilityQueryCommand,
    ) -> Result<bcs_service_api::BotVisibilityQueryResult, bcs_service_api::BotUseCaseError> {
        Err(bcs_service_api::BotUseCaseError::Service(
            bcs_service_api::ServiceError::InternalError("not configured".to_string()),
        ))
    }

    async fn list_my_bots(
        &self,
        command: bcs_service_api::MyBotsCommand,
    ) -> Result<bcs_service_api::BotPagedListResult, bcs_service_api::BotUseCaseError> {
        let controlled = self
            .controlled
            .lock()
            .unwrap()
            .get(command.staff_no.as_str())
            .cloned()
            .unwrap_or_default();
        Ok(bcs_service_api::BotPagedListResult {
            total: controlled.len() as u64,
            items: controlled
                .iter()
                .map(|bot_id| bcs_service_api::BotQueryEntry {
                    bot_uuid: bot_id.clone(),
                    capabilities: Default::default(),
                    visibility: "public".to_string(),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    env: Some("local".to_string()),
                    dynamic_status: bcs_service_api::DynamicStatusResponse {
                        status: "active".to_string(),
                    },
                    created_by: None,
                    user_visibility: "protected".to_string(),
                    friend_ext: Default::default(),
                    friend_check_in_strategy: String::new(),
                    is_friend: None,
                    access_relation: Some("owner".to_string()),
                })
                .collect(),
            offset: command.offset,
            limit: command.limit,
        })
    }
}

struct Fixture {
    service: SessionFileApplicationServiceImpl,
    bots: Arc<BotCore>,
    groups: Arc<GroupCore>,
    session_repo: Arc<dyn SessionRepoPort>,
    notifications: Arc<RecordingSystemMessage>,
    authority: Arc<SeededAuthority>,
    mine: Arc<SeededMine>,
}

impl Fixture {
    async fn new() -> Self {
        let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            group_repo,
        ));
        let bots = Arc::new(BotCore::memory());
        let file_repo: Arc<dyn SessionFileRepoPort> = Arc::new(MemorySessionFileRepo::new());
        let storage = Arc::new(FakeStoragePlugin::new(StorageCapabilities {
            supports_presign_put: false,
            supports_presign_download: false,
            supports_stream_put: true,
            supports_stream_get: true,
            supports_inline_view: true,
            max_object_size: 1024 * 1024,
        }));
        let legacy: Arc<dyn SessionFileService> = Arc::new(SessionFileServiceImpl::new(
            SessionFileServiceConfig {
                storage,
                repo: file_repo,
                session_repo: session_repo.clone(),
                env: "test".into(),
                max_size: 1024 * 1024,
                multipart_threshold: 1024,
                bcs_base_url: "http://legacy.test".into(),
                share_secret: b"test-secret".to_vec(),
                share_default_ttl: 3600,
                share_link_ttl: 3600,
                share_base_url: None,
            },
        ));
        let notifications = Arc::new(RecordingSystemMessage::default());
        // File-facade authority: the map-backed recording double keeps the
        // live-fact semantics (fail-closed, never `created_by`) and lets the
        // parity tests seed owner/manager edges (spec §8/§12.2).
        let authority_hook = Arc::new(SeededAuthority::empty());
        let mine = Arc::new(SeededMine::empty());
        let service = SessionFileApplicationServiceImpl::new(
            legacy,
            sessions,
            groups.clone(),
            bots.clone(),
            authority_hook.clone(),
            mine.clone(),
            notifications.clone(),
            Arc::new(CompletionShareProjector),
        );
        Self {
            service,
            bots,
            groups,
            session_repo,
            notifications,
            authority: authority_hook,
            mine,
        }
    }

    async fn seed(&self) {
        for (bot, owner) in [("bot-a", "alice"), ("bot-b", "alice"), ("bot-c", "carol")] {
            self.bots
                .register(
                    bot.into(),
                    BotCapabilities {
                        name: Some(bot.into()),
                        visibility: "public".into(),
                        ..Default::default()
                    },
                )
                .await
                .expect("register bot");
            self.bots
                .save_created_by(bot, owner, true)
                .await
                .expect("save Bot creator");
            // Live-fact seeding (spec §12.2): the facade resolves the same
            // ownership through the authority hook, not `created_by`.
            self.seed_authority_owner(bot, owner).await;
        }
        let participants = vec![
            Participant::bot("bot-a", ParticipantRole::Driver),
            Participant::bot("bot-b", ParticipantRole::Worker),
        ];
        let mut group = Group::new("group-1", "bot-a", participants.clone());
        group.originator = Some("human_alice".into());
        group.group_strategy = GroupStrategy::Chat;
        self.groups.upsert(group).await.expect("store group");
        self.session_repo
            .create(
                "group-1",
                NewSessionParams {
                    id: Some("group-1:abcd1234".into()),
                    session_kind: SessionKind::Chat,
                    participants,
                    created_by: Some("bot-a".into()),
                    ..Default::default()
                },
            )
            .await
            .expect("store session");
    }

    /// Seed a live owner fact so mixed-identity and owner-eligibility checks
    /// resolve through the authority hook, never `created_by`.
    async fn seed_authority_owner(&self, bot_id: &str, owner_staff_no: &str) {
        self.authority.seed_owner(bot_id, owner_staff_no).await;
        self.mine.control(owner_staff_no, bot_id);
    }

    /// Seed a live manager fact (spec §8 owner/manager parity).
    async fn seed_authority_manager(&self, bot_id: &str, manager_staff_no: &str) {
        self.authority.seed_manager(bot_id, manager_staff_no).await;
        self.mine.control(manager_staff_no, bot_id);
    }
}

fn bot_caller(bot_uuid: &str, owner_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: owner_id.into(),
            app_id: 1,
            agent_code: "agent".into(),
        }),
        app: None,
        access_key: None,
    }
}

fn human_caller(user_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: user_id.into(),
            username: user_id.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn human_and_bot_caller(user_id: &str, bot_uuid: &str) -> AuthenticatedCaller {
    let mut caller = bot_caller(bot_uuid, user_id);
    caller.user = human_caller(user_id).user;
    caller
}

async fn prepare(
    fixture: &Fixture,
    caller: AuthenticatedCaller,
) -> bcs_service_api::application::v1::PrepareSessionFileResult {
    fixture
        .service
        .prepare(PrepareSessionFile {
            caller,
            session_id: "group-1:abcd1234".into(),
            file_name: "report.txt".into(),
            size: 3,
            mime_type: "text/plain".into(),
        })
        .await
        .expect("prepare file")
}

#[tokio::test]
async fn user_plus_owned_bot_prepares_as_the_bot_owner() {
    let fixture = Fixture::new().await;
    fixture.seed().await;

    let result = prepare(&fixture, human_and_bot_caller("alice", "bot-a")).await;

    assert_eq!(result.file.owner.actor_kind, bcs_service_api::application::v1::SessionFileActorKind::Bot);
    assert_eq!(result.file.owner.actor_id, "bot-a");
}

#[tokio::test]
async fn sibling_bot_cannot_upload_another_bots_file() {
    let fixture = Fixture::new().await;
    fixture.seed().await;
    let prepared = prepare(&fixture, bot_caller("bot-a", "alice")).await;

    let error = fixture
        .service
        .upload_content(UploadSessionFileContent {
            caller: bot_caller("bot-b", "alice"),
            session_id: "group-1:abcd1234".into(),
            file_id: prepared.file.file_id,
            part_number: None,
            body: byte_stream_from_bytes(Bytes::from_static(b"abc")),
            content_length: Some(3),
        })
        .await
        .expect_err("a sibling Bot is not the file owner");

    assert_eq!(error.code(), "file_upload_owner_mismatch");
}

#[tokio::test]
async fn human_creator_can_upload_and_complete_an_owned_bots_file() {
    let fixture = Fixture::new().await;
    fixture.seed().await;
    let prepared = prepare(&fixture, bot_caller("bot-a", "alice")).await;
    let file_id = prepared.file.file_id;

    let accepted = fixture
        .service
        .upload_content(UploadSessionFileContent {
            caller: human_caller("alice"),
            session_id: "group-1:abcd1234".into(),
            file_id: file_id.clone(),
            part_number: None,
            body: byte_stream_from_bytes(Bytes::from_static(b"abc")),
            content_length: Some(3),
        })
        .await
        .expect("owner Human uploads");
    assert_eq!(accepted.status, SessionFileStatus::Pending);

    let completed = fixture
        .service
        .complete(CompleteSessionFile {
            caller: human_caller("alice"),
            session_id: "group-1:abcd1234".into(),
            file_id,
        })
        .await
        .expect("owner Human completes");

    assert_eq!(completed.status, SessionFileStatus::Ready);
    let events = fixture.notifications.events.lock().await;
    assert_eq!(events.len(), 1);
    match &events[0] {
        SystemMessageEvent::GenericNotification {
            message, receivers, ..
        } => {
            assert!(message.starts_with("用户 alice 上传了一个文件 report.txt"));
            assert!(
                message.contains("http://share.test/sessions/shared-file/content?token="),
                "expected a share link in the notification, got: {message}",
            );
            let mut receiver_ids = receivers
                .iter()
                .map(|participant| participant.bot_uuid.as_str())
                .collect::<Vec<_>>();
            receiver_ids.sort_unstable();
            assert_eq!(receiver_ids, vec!["bot-a", "bot-b"]);
        }
        event => panic!("expected GenericNotification, got {event:?}"),
    }
}

#[tokio::test]
async fn completion_skips_notification_when_uploader_is_the_only_bot() {
    let fixture = Fixture::new().await;
    fixture.seed().await;
    let session_id = "group-1:00000002";
    fixture
        .session_repo
        .create(
            "group-1",
            NewSessionParams {
                id: Some(session_id.into()),
                session_kind: SessionKind::Chat,
                participants: vec![Participant::bot("bot-a", ParticipantRole::Driver)],
                created_by: Some("bot-a".into()),
                ..Default::default()
            },
        )
        .await
        .expect("store single-Bot session");
    let caller = bot_caller("bot-a", "alice");
    let prepared = fixture
        .service
        .prepare(PrepareSessionFile {
            caller: caller.clone(),
            session_id: session_id.into(),
            file_name: "solo.txt".into(),
            size: 3,
            mime_type: "text/plain".into(),
        })
        .await
        .expect("prepare file");
    fixture
        .service
        .upload_content(UploadSessionFileContent {
            caller: caller.clone(),
            session_id: session_id.into(),
            file_id: prepared.file.file_id.clone(),
            part_number: None,
            body: byte_stream_from_bytes(Bytes::from_static(b"abc")),
            content_length: Some(3),
        })
        .await
        .expect("upload file");
    fixture
        .service
        .complete(CompleteSessionFile {
            caller,
            session_id: session_id.into(),
            file_id: prepared.file.file_id,
        })
        .await
        .expect("complete file");

    assert!(fixture.notifications.events.lock().await.is_empty());
}

#[tokio::test]
async fn non_member_cannot_list_session_files() {
    let fixture = Fixture::new().await;
    fixture.seed().await;

    let error = fixture
        .service
        .list(ListSessionFiles {
            caller: bot_caller("bot-c", "carol"),
            session_id: "group-1:abcd1234".into(),
            prefix: None,
            status: None,
            limit: 100,
            offset: 0,
        })
        .await
        .expect_err("non-member is forbidden");

    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn direct_human_participant_can_list_session_files() {
    let fixture = Fixture::new().await;
    fixture.seed().await;
    let session_id = "group-1:00000003";
    fixture
        .session_repo
        .create(
            "group-1",
            NewSessionParams {
                id: Some(session_id.into()),
                session_kind: SessionKind::Chat,
                participants: vec![Participant::human(
                    "human_alice",
                    ParticipantRole::Consultant,
                )],
                created_by: Some("human_alice".into()),
                ..Default::default()
            },
        )
        .await
        .expect("store Human session");

    let page = fixture
        .service
        .list(ListSessionFiles {
            caller: human_caller("alice"),
            session_id: session_id.into(),
            prefix: None,
            status: None,
            limit: 100,
            offset: 0,
        })
        .await
        .expect("direct Human participant is admitted");

    assert!(page.items.is_empty());
}

#[tokio::test]
async fn participant_id_collision_does_not_cross_actor_kinds() {
    let fixture = Fixture::new().await;
    fixture.seed().await;
    let session_id = "group-1:00000004";
    fixture
        .session_repo
        .create(
            "group-1",
            NewSessionParams {
                id: Some(session_id.into()),
                session_kind: SessionKind::Chat,
                participants: vec![Participant::human(
                    "bot-a",
                    ParticipantRole::Consultant,
                )],
                created_by: Some("bot-a".into()),
                ..Default::default()
            },
        )
        .await
        .expect("store colliding Human participant");

    let error = fixture
        .service
        .list(ListSessionFiles {
            caller: bot_caller("bot-a", "alice"),
            session_id: session_id.into(),
            prefix: None,
            status: None,
            limit: 100,
            offset: 0,
        })
        .await
        .expect_err("Bot must not match a Human participant with the same identifier");

    assert_eq!(error.code(), "forbidden");

    let error = fixture
        .service
        .list(ListSessionFiles {
            caller: human_caller("alice"),
            session_id: session_id.into(),
            prefix: None,
            status: None,
            limit: 100,
            offset: 0,
        })
        .await
        .expect_err("Human must not inherit ownership through a Human participant ID collision");

    assert_eq!(error.code(), "forbidden");
}

/// Shared authority double for the file-facade tests: answers from seeded
/// live facts, fail-closed otherwise, never `created_by`.
use std::collections::BTreeMap;
struct SeededAuthority {
    roles: std::sync::Mutex<BTreeMap<(String, String), &'static str>>,
}

impl SeededAuthority {
    fn empty() -> Self {
        Self {
            roles: std::sync::Mutex::new(BTreeMap::new()),
        }
    }

    async fn seed_owner(&self, bot_id: &str, owner_staff_no: &str) {
        self.roles
            .lock()
            .unwrap()
            .insert((owner_staff_no.to_string(), bot_id.to_string()), "owner");
    }

    async fn seed_manager(&self, bot_id: &str, manager_staff_no: &str) {
        self.roles.lock().unwrap().insert(
            (manager_staff_no.to_string(), bot_id.to_string()),
            "manager",
        );
    }
}

#[async_trait::async_trait]
impl bcs_service_api::application::v1::BotAuthorityHook for SeededAuthority {
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        Ok(self
            .roles
            .lock()
            .unwrap()
            .contains_key(&(user_id.to_string(), bot_id.to_string())))
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        match self
            .roles
            .lock()
            .unwrap()
            .get(&(user_id.to_string(), bot_id.to_string()))
        {
            Some(&"owner") => Ok(()),
            _ => Err(bcs_service_api::ServiceError::Forbidden(format!(
                "user '{user_id}' is not the owner of bot '{bot_id}'"
            ))),
        }
    }
}
