use std::sync::Arc;

use async_trait::async_trait;
use bcs_app_session::SessionFileApplicationServiceImpl;
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_domain::{GroupStrategy, SystemMessageEvent};
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::v1::{
    AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    CompleteSessionFile, ListSessionFiles, PrepareSessionFile, SessionFileApplicationService,
    SessionFileStatus, ShareSessionFile, UploadSessionFileContent,
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

struct Fixture {
    service: SessionFileApplicationServiceImpl,
    bots: Arc<BotCore>,
    groups: Arc<GroupCore>,
    session_repo: Arc<dyn SessionRepoPort>,
    notifications: Arc<RecordingSystemMessage>,
    authority_repo: Arc<MemoryBotRepo>,
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
        let authority_repo = Arc::new(MemoryBotRepo::new());
        let service = SessionFileApplicationServiceImpl::new(
            legacy,
            sessions,
            groups.clone(),
            bots.clone(),
            notifications.clone(),
            Arc::new(CompletionShareProjector),
        )
        .with_bot_authority_hook(
            bcs_test_support::authority_hook_for(authority_repo.clone()),
        );
        Self {
            service,
            bots,
            groups,
            session_repo,
            notifications,
            authority_repo,
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
            // Row 7: the CURRENT owner edge replaces the
            // historical `created_by`.
            bcs_test_support::grant_owner_edge(&self.authority_repo, bot, owner)
                .await;
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

/// `prepare` mapped to its error outcome
/// (callers assert the variant).
async fn prepare_err(
    fixture: &Fixture,
    caller: AuthenticatedCaller,
) -> Option<bcs_service_api::application::v1::ApplicationError> {
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
        .err()
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

/// The Human→Bot share matrix (GPT-review batch C
/// finding #6): `share` passes the session in, so a
/// Human whose membership ran through a controlled
/// participant Bot carries that Bot identity, and the
/// matrix follows the CURRENT edges.
#[tokio::test]
async fn share_carries_the_controlled_participant_identity() {
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
                participants: vec![
                    Participant::bot("bot-a", ParticipantRole::Driver),
                    Participant::bot("bot-b", ParticipantRole::Worker),
                ],
                created_by: Some("bot-a".into()),
                ..Default::default()
            },
        )
        .await
        .expect("store session");
    let caller = bot_caller("bot-a", "alice");
    let file_id = fixture
        .service
        .prepare(PrepareSessionFile {
            caller: caller.clone(),
            session_id: session_id.into(),
            file_name: "report.txt".into(),
            size: 3,
            mime_type: "text/plain".into(),
        })
        .await
        .expect("prepare file")
        .file
        .file_id;
    fixture
        .service
        .upload_content(UploadSessionFileContent {
            caller: caller.clone(),
            session_id: session_id.into(),
            file_id: file_id.clone(),
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
            file_id: file_id.clone(),
        })
        .await
        .expect("complete file");

    // Constrain the scenario first: hand `bot-a` over
    // to carol so alice's ONLY remaining control is the
    // participant `bot-b` — exactly the shape the
    // finding describes (member via controlled
    // participant Bot, own identity alone cannot
    // match the participants intersection).
    bcs_test_support::grant_owner_edge(&fixture.authority_repo, "bot-a", "carol")
        .await;

    // A pure Human whose membership passed through its
    // controlled participant `bot-b` SHARES: the
    // controlled participant identity reaches
    // `can_share`'s participants intersection.
    fixture
        .service
        .share(ShareSessionFile {
            caller: human_caller("alice"),
            session_id: session_id.into(),
            file_id: file_id.clone(),
            ttl_seconds: Some(60),
        })
        .await
        .expect("the controller shares");

    // A→B handover: B's owner grant REPLACES A's; A's
    // membership leg (live `can_manage` on bot-b) is
    // gone → the share is denied fallback-free, and B
    // (the CURRENT controller) shares instead.
    bcs_test_support::grant_owner_edge(&fixture.authority_repo, "bot-b", "bob")
        .await;
    bcs_test_support::revoke_authority_edge(
        &fixture.authority_repo,
        "bot-b",
        "alice",
        bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner,
    )
    .await;
    let denied = fixture
        .service
        .share(ShareSessionFile {
            caller: human_caller("alice"),
            session_id: session_id.into(),
            file_id: file_id.clone(),
            ttl_seconds: Some(60),
        })
        .await
        .expect_err("the revoked controller cannot share");
    assert!(matches!(
        denied,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
    fixture
        .service
        .share(ShareSessionFile {
            caller: human_caller("bob"),
            session_id: session_id.into(),
            file_id: file_id.clone(),
            ttl_seconds: Some(60),
        })
        .await
        .expect("the CURRENT controller shares");

    // Manager polarity: a CURRENT manager grant re-opens
    // the share for its holder.
    bcs_test_support::grant_manager_edge(
        &fixture.authority_repo,
        "bot-b",
        "alice",
    )
    .await;
    fixture
        .service
        .share(ShareSessionFile {
            caller: human_caller("alice"),
            session_id: session_id.into(),
            file_id,
            ttl_seconds: Some(60),
        })
        .await
        .expect("the current manager shares");
}

/// The facade (the internal session-file routes'
/// delegated decision point, GPT-review batch C
/// finding #5) decides the Human→Bot leg through the
/// LIVE edge only: a hybrid credential whose
/// historical `owner_id` claim survives a handover
/// is denied, and a CURRENT manager's hybrid is
/// admitted.
#[tokio::test]
async fn hybrid_file_calls_follow_the_current_edge() {
    let fixture = Fixture::new().await;
    fixture.seed().await;

    // A(owner) hybrid: the current owner edge
    // admits the Bot-lane call.
    prepare(&fixture, human_and_bot_caller("alice", "bot-a"))
        .await;

    // A→B handover (B's grant REPLACES A's; A's
    // edges withdrawn): the same hybrid is
    // fallback-free denied — no claim-local
    // history leaks through.
    bcs_test_support::grant_owner_edge(&fixture.authority_repo, "bot-a", "bob")
        .await;
    bcs_test_support::revoke_authority_edge(
        &fixture.authority_repo,
        "bot-a",
        "alice",
        bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner,
    )
    .await;
    let denied = prepare_err(
        &fixture,
        human_and_bot_caller("alice", "bot-a"),
    )
    .await
    .expect("the revoked hybrid cannot use the Bot lane");
    assert!(matches!(
        denied,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));

    // B (the CURRENT controller) proceeds.
    prepare(&fixture, human_and_bot_caller("bob", "bot-a"))
        .await;

    // Manager polarity: a CURRENT manager's hybrid
    // is admitted (the claim-local pre-check would
    // have wrongly blocked it).
    bcs_test_support::grant_manager_edge(
        &fixture.authority_repo,
        "bot-a",
        "alice",
    )
    .await;
    prepare(&fixture, human_and_bot_caller("alice", "bot-a"))
        .await;
}
