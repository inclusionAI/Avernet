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

struct Fixture {
    service: SessionFileApplicationServiceImpl,
    bots: Arc<BotCore>,
    groups: Arc<GroupCore>,
    session_repo: Arc<dyn SessionRepoPort>,
    notifications: Arc<RecordingSystemMessage>,
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
        let service = SessionFileApplicationServiceImpl::new(
            legacy,
            sessions,
            groups.clone(),
            bots.clone(),
            notifications.clone(),
        );
        Self {
            service,
            bots,
            groups,
            session_repo,
            notifications,
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
            notification_content_url: "https://gateway.test/openapi/v1/collaboration/content".into(),
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
            assert!(message.contains("gateway.test"));
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
            notification_content_url: "http://legacy.test/content".into(),
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
