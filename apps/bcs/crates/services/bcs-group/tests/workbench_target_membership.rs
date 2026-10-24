//! F4: Workbench connect/send admission gates on the ACTUAL
//! target resource, read through the strict authorization
//! snapshots (spec §8.3).
//!
//! - an explicit Session target requires the sender IN the
//!   Session participants (Group membership never backstops it);
//! - a missing, foreign, or Absent membership is rejected the
//!   same way;
//! - a Group-level send keeps checking Group membership;
//! - the facts are read through `read_authorization_snapshot`
//!   (strict master read) — a poisoned display cache or an
//!   independent store instance can never re-admit a removed
//!   member, and a storage read failure never fails open.

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_db_api::{DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep, DbTransactionStepResult};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService, Participant, ParticipantMode,
    ParticipantRole, ServiceError, ServiceResult, SessionKind, WorkbenchChatAuthorizationCommand,
    WorkbenchConnectCommand, WorkbenchSessionService, WorkbenchUseCaseError,
};
use bcs_service_api::types::authorization_snapshot::SessionAuthorizationSnapshot;
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

const ENV: &str = "dev";
const GROUP: &str = "group-f4";
const SESSION: &str = "group-f4:abcd1234";

/// Counting snapshot Core: pins that one admission request reads
/// the Session authorization facts AT MOST once, and that a
/// Group-level request performs no Session read at all.
struct CountingSessionAuthorization {
    inner: Arc<dyn SessionAuthorizationCoreService>,
    reads: Arc<AtomicUsize>,
}

#[async_trait]
impl SessionAuthorizationCoreService for CountingSessionAuthorization {
    async fn read_authorization_snapshot(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<SessionAuthorizationSnapshot>> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        self.inner.read_authorization_snapshot(session_id).await
    }
}

/// Memory-backed fixture: real Bot registry + authority hook,
/// memory Group/Session repos, and the strict snapshot Core.
struct MemoryFixture {
    workbench: Arc<dyn WorkbenchSessionService>,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    session_repo: Arc<dyn SessionRepoPort>,
    authority_repo: Arc<MemoryBotRepo>,
    session_reads: Arc<AtomicUsize>,
}

impl MemoryFixture {
    fn new() -> Self {
        let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
        let session_management = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            group_repo,
        ));
        let bots = Arc::new(BotCore::memory());
        let authority_repo = Arc::new(MemoryBotRepo::new());
        let session_reads = Arc::new(AtomicUsize::new(0));
        let session_authorization = Arc::new(CountingSessionAuthorization {
            inner: Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(
                session_repo.clone(),
            )),
            reads: session_reads.clone(),
        });
        let workbench = Arc::new(
            GroupManagement::new(
                groups.clone(),
                bots.clone(),
                Arc::new(FriendCore::memory()),
                Arc::new(RelationCore::memory()),
                GroupConfig::default(),
                session_management,
                Arc::new(NoopSystemMessageService),
            )
            .with_bot_authority(bcs_test_support::authority_hook_for(authority_repo.clone()))
            .with_session_authorization(session_authorization),
        );
        Self {
            workbench,
            groups,
            bots,
            session_repo,
            authority_repo,
            session_reads,
        }
    }

    /// Group {driver `b1`, consultant `b2`}, owned by alice;
    /// Session {b1, b2} by default.
    async fn seed_standard(&self) {
        for (bot, owner) in [("b1", "alice"), ("b2", "alice")] {
            bcs_test_support::grant_owner_edge(&self.authority_repo, bot, owner).await;
            // Bots must exist in the registry for the sender gate.
            self.bots
                .register(bot.into(), BotCapabilities::default())
                .await
                .expect("register bot");
        }
        let group = Group::new(
            GROUP,
            "b1",
            vec![
                Participant::bot("b1", ParticipantRole::Driver),
                Participant::bot("b2", ParticipantRole::Consultant),
            ],
        );
        self.groups.upsert(group).await.expect("store group");
        self.session_repo
            .create(
                GROUP,
                NewSessionParams {
                    id: Some(SESSION.into()),
                    session_kind: SessionKind::Chat,
                    participants: vec![
                        Participant::bot("b1", ParticipantRole::Driver),
                        Participant::bot("b2", ParticipantRole::Consultant),
                    ],
                    created_by: Some("b1".into()),
                    ..Default::default()
                },
            )
            .await
            .expect("store session");
    }

    fn send_cmd(&self, from: &str, session: Option<&str>) -> WorkbenchChatAuthorizationCommand {
        WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".into()),
            group_id: GROUP.into(),
            from_actor_id: from.into(),
            session_id: session.map(str::to_string),
        }
    }

    fn connect_cmd(&self, view: Option<&str>, session: Option<&str>) -> WorkbenchConnectCommand {
        WorkbenchConnectCommand {
            bound_actor_id: Some("human_alice".into()),
            group_id: GROUP.into(),
            session_id: session.map(str::to_string),
            view_actor_id: view.map(str::to_string),
        }
    }
}

#[tokio::test]
async fn bot_removed_from_session_cannot_send_or_view_connect() {
    let fixture = MemoryFixture::new();
    fixture.seed_standard().await;
    fixture.session_repo
        .remove_participant(SESSION, "b2")
        .await
        .expect("remove b2 from the session");

    // Send as b2 -> the explicit Session target rejects even
    // though b2 KEEPS its Group membership and its manager edge.
    let send = fixture
        .workbench
        .authorize_chat_send(fixture.send_cmd("b2", Some(SESSION)))
        .await
        .expect_err("the removed Bot can no longer send into the Session");
    assert!(matches!(send, WorkbenchUseCaseError::SenderNotInGroup));

    // Connect with an explicit b2 view -> no Group backstop.
    let connect = fixture
        .workbench
        .connect(fixture.connect_cmd(Some("b2"), Some(SESSION)))
        .await
        .expect_err("the removed Bot can no longer hold a Session view");
    assert!(matches!(connect, WorkbenchUseCaseError::ForbiddenGroupAccess));

    // Its Group-level send remains intact: the removal did not
    // revoke unrelated authorization.
    fixture
        .workbench
        .authorize_chat_send(fixture.send_cmd("b2", None))
        .await
        .expect("the Bot still sends at Group level");

    // A still-member Bot keeps working after the same removal.
    fixture
        .workbench
        .authorize_chat_send(fixture.send_cmd("b1", Some(SESSION)))
        .await
        .expect("remaining Session members are unaffected");
}

#[tokio::test]
async fn absent_session_member_cannot_send_or_view_connect() {
    let fixture = MemoryFixture::new();
    fixture.seed_standard().await;
    fixture
        .session_repo
        .update_participant_mode(SESSION, "b2", ParticipantMode::Absent)
        .await
        .expect("switch b2 to Absent");

    let send = fixture
        .workbench
        .authorize_chat_send(fixture.send_cmd("b2", Some(SESSION)))
        .await
        .expect_err("an Absent Session member cannot send");
    assert!(matches!(send, WorkbenchUseCaseError::ParticipantAbsent));

    let connect = fixture
        .workbench
        .connect(fixture.connect_cmd(Some("b2"), Some(SESSION)))
        .await
        .expect_err("an Absent Session member cannot view-connect");
    assert!(matches!(connect, WorkbenchUseCaseError::ParticipantAbsent));
}

#[tokio::test]
async fn missing_or_foreign_session_never_falls_back_to_group_membership() {
    let fixture = MemoryFixture::new();
    fixture.seed_standard().await;

    // b2 IS a Group member; the explicit Session ids below are
    // missing / belong to another Group. Pre-F4 these admitted
    // the send through the Group fallback.
    fixture
        .session_repo
        .create(
            "foreign-group",
            NewSessionParams {
                id: Some("foreign-group:beef0001".into()),
                session_kind: SessionKind::Chat,
                participants: vec![Participant::bot("b2", ParticipantRole::Consultant)],
                created_by: Some("b2".into()),
                ..Default::default()
            },
        )
        .await
        .expect("store foreign session");

    for bogus in ["group-f4:deadbeef", "foreign-group:beef0001"] {
        let send = fixture
            .workbench
            .authorize_chat_send(fixture.send_cmd("b2", Some(bogus)))
            .await
            .expect_err(&format!("explicit Session '{bogus}' must not backstop via Group"));
        assert!(
            matches!(send, WorkbenchUseCaseError::SenderNotInGroup),
            "bogus session '{bogus}': {send:?}"
        );
        let connect = fixture
            .workbench
            .connect(fixture.connect_cmd(Some("b2"), Some(bogus)))
            .await
            .expect_err("no Group fallback for the view either");
        assert!(matches!(connect, WorkbenchUseCaseError::ForbiddenGroupAccess));
    }

    // Sanity: the genuine Session still admits.
    fixture
        .workbench
        .authorize_chat_send(fixture.send_cmd("b2", Some(SESSION)))
        .await
        .expect("the genuine Session target admits");
}

#[tokio::test]
async fn session_only_human_member_can_connect_and_send() {
    let fixture = MemoryFixture::new();
    fixture.seed_standard().await;
    // human_delta participates ONLY in the Session — no Group
    // membership, no controlled Group Bot.
    let mut human = Participant::human("human_delta", ParticipantRole::Observer);
    human.mode = Some(ParticipantMode::Present);
    fixture
        .session_repo
        .add_participant(SESSION, human)
        .await
        .expect("add Session-only human");

    let send = fixture
        .workbench
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_delta".into()),
            group_id: GROUP.into(),
            from_actor_id: "human_delta".into(),
            session_id: Some(SESSION.into()),
        })
        .await
        .expect("a Session-only member sends in their Session");
    assert_eq!(send, ());

    fixture
        .workbench
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_delta".into()),
            group_id: GROUP.into(),
            session_id: Some(SESSION.into()),
            view_actor_id: Some("human_delta".into()),
        })
        .await
        .expect("a Session-only member connects");
}

#[tokio::test]
async fn admission_reads_session_facts_at_most_once_per_request() {
    let fixture = MemoryFixture::new();
    fixture.seed_standard().await;

    fixture.session_reads.store(0, Ordering::SeqCst);
    fixture
        .workbench
        .authorize_chat_send(fixture.send_cmd("b1", Some(SESSION)))
        .await
        .expect("member send");
    assert_eq!(
        fixture.session_reads.load(Ordering::SeqCst),
        1,
        "one admission request shares ONE strict Session snapshot read"
    );

    fixture.session_reads.store(0, Ordering::SeqCst);
    fixture
        .workbench
        .authorize_chat_send(fixture.send_cmd("b1", None))
        .await
        .expect("group-level send");
    assert_eq!(
        fixture.session_reads.load(Ordering::SeqCst),
        0,
        "a Group-level admission performs no Session read"
    );

    fixture.session_reads.store(0, Ordering::SeqCst);
    fixture
        .workbench
        .connect(fixture.connect_cmd(Some("b1"), Some(SESSION)))
        .await
        .expect("member connect");
    assert_eq!(fixture.session_reads.load(Ordering::SeqCst), 1);
}

/// Latchable DB failure decorator: when armed, every read fails —
/// admission must fail CLOSED, never fail open.
struct FailableDb {
    inner: Arc<dyn DbPlugin>,
    fail_reads: std::sync::atomic::AtomicBool,
}

#[async_trait]
impl DbPlugin for FailableDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if self.fail_reads.load(Ordering::SeqCst) {
            return Err(bcs_db_api::DbError::Backend("forced read failure".into()));
        }
        self.inner.query(statement).await
    }

    async fn execute(
        &self,
        statement: DbStatement,
    ) -> DbResult<bcs_db_api::DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<bcs_db_api::DbHealth> {
        self.inner.health_check().await
    }
}

/// Two INDEPENDENT SQLite-backed store instances over the same
/// database: the Workbench service reads through `reader` (cache-
/// poisoned deliberately); the writer flips facts behind it. The
/// strict snapshot read must still reject (spec §8.3 freshness;
/// F4's "reader caches Group / writer removes member" case).
#[tokio::test]
async fn strict_reads_reject_removals_despite_a_poisoned_display_cache() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let writer: Arc<dyn GroupRepoPort> =
        Arc::new(bcs_group::MySqlGroupStore::sqlite(db.clone(), ENV.to_string()));
    let reader: Arc<dyn GroupRepoPort> =
        Arc::new(bcs_group::MySqlGroupStore::sqlite(db.clone(), ENV.to_string()));

    let bots = Arc::new(BotCore::memory());
    for bot in ["b1", "b2"] {
        bots.register(bot.into(), BotCapabilities::default())
            .await
            .expect("register bot");
    }
    let authority_repo = Arc::new(MemoryBotRepo::new());
    bcs_test_support::grant_owner_edge(&authority_repo, "b1", "alice").await;
    bcs_test_support::grant_owner_edge(&authority_repo, "b2", "alice").await;

    let group = Group::new(
        GROUP,
        "b1",
        vec![
            Participant::bot("b1", ParticipantRole::Driver),
            Participant::bot("b2", ParticipantRole::Consultant),
            Participant::human("human_carol", ParticipantRole::Observer),
        ],
    );
    writer.upsert(group.clone()).await.expect("seed via writer");

    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let session_management = Arc::new(SessionManagementServiceImpl::new(
        session_repo.clone(),
        reader.clone(),
    ));
    session_repo
        .create(
            GROUP,
            NewSessionParams {
                id: Some(SESSION.into()),
                session_kind: SessionKind::Chat,
                participants: group.participants.clone(),
                created_by: Some("b1".into()),
                ..Default::default()
            },
        )
        .await
        .expect("store session");

    let groups = Arc::new(GroupCore::with_repo(reader.clone()));
    let workbench = Arc::new(
        GroupManagement::new(
            groups,
            bots,
            Arc::new(FriendCore::memory()),
            Arc::new(RelationCore::memory()),
            GroupConfig::default(),
            session_management,
            Arc::new(NoopSystemMessageService),
        )
        .with_bot_authority(bcs_test_support::authority_hook_for(authority_repo))
        .with_session_authorization(Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(
            session_repo,
        ))),
    ) as Arc<dyn WorkbenchSessionService>;

    let send_as_b2 = || WorkbenchChatAuthorizationCommand {
        bound_actor_id: Some("human_alice".into()),
        group_id: GROUP.into(),
        from_actor_id: "b2".into(),
        session_id: None,
    };
    workbench
        .authorize_chat_send(send_as_b2())
        .await
        .expect("a current Group member sends at Group level");
    // human_carol participates directly and controls nothing:
    // her current admission rests ONLY on her Group row.
    workbench
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_carol".into()),
            group_id: GROUP.into(),
            session_id: None,
            view_actor_id: None,
        })
        .await
        .expect("a direct Human participant connects");

    // Poison the READER's ordinary read path, then remove b2 and
    // human_carol through the WRITER instance.
    let _ = reader.try_get(GROUP).await.expect("poison the reader cache");
    let mut shrunk = group;
    shrunk.participants.retain(|p| p.bot_uuid == "b1");
    writer.upsert(shrunk).await.expect("writer removes b2 and carol");

    // The display read stays poisoned (the cache really exists).
    let stale = reader.try_get(GROUP).await.expect("stale group read").expect("group");
    assert!(
        stale.participants.iter().any(|p| p.bot_uuid == "b2"),
        "the ordinary read must be the poisoned one for this test to mean anything"
    );

    // The strict admission read rejects immediately (b2 no
    // longer a Group member), and carol lost her own Group
    // access as well.
    let send = workbench
        .authorize_chat_send(send_as_b2())
        .await
        .expect_err("the removed member is rejected through the poisoned reader");
    assert!(matches!(send, WorkbenchUseCaseError::SenderNotInGroup));
    let connect = workbench
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_carol".into()),
            group_id: GROUP.into(),
            session_id: None,
            view_actor_id: None,
        })
        .await
        .expect_err("the removed Human loses Group access");
    assert!(matches!(connect, WorkbenchUseCaseError::ForbiddenGroupAccess));
}

#[tokio::test]
async fn a_snapshot_read_failure_fails_closed() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let failable = Arc::new(FailableDb {
        inner: db.clone(),
        fail_reads: std::sync::atomic::AtomicBool::new(false),
    });
    let writer: Arc<dyn GroupRepoPort> =
        Arc::new(bcs_group::MySqlGroupStore::sqlite(failable.clone(), ENV.to_string()));
    writer
        .upsert(Group::new(
            GROUP,
            "b1",
            vec![Participant::bot("b1", ParticipantRole::Driver)],
        ))
        .await
        .expect("seed group");

    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let session_management = Arc::new(SessionManagementServiceImpl::new(
        session_repo.clone(),
        writer.clone(),
    ));
    let bots = Arc::new(BotCore::memory());
    bots.register("b1".into(), BotCapabilities::default())
        .await
        .expect("register b1");
    let authority_repo = Arc::new(MemoryBotRepo::new());
    bcs_test_support::grant_owner_edge(&authority_repo, "b1", "alice").await;
    let workbench = Arc::new(
        GroupManagement::new(
            Arc::new(GroupCore::with_repo(writer.clone())),
            bots,
            Arc::new(FriendCore::memory()),
            Arc::new(RelationCore::memory()),
            GroupConfig::default(),
            session_management,
            Arc::new(NoopSystemMessageService),
        )
        .with_bot_authority(bcs_test_support::authority_hook_for(authority_repo))
        .with_session_authorization(Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(
            session_repo,
        ))),
    ) as Arc<dyn WorkbenchSessionService>;

    // The same request PASSES with a healthy read (sanity).
    workbench
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".into()),
            group_id: GROUP.into(),
            from_actor_id: "b1".into(),
            session_id: None,
        })
        .await
        .expect("healthy read admits the member");

    // A storage-level read failure is an ERROR, never an admit.
    failable.fail_reads.store(true, Ordering::SeqCst);
    let error = workbench
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".into()),
            group_id: GROUP.into(),
            from_actor_id: "b1".into(),
            session_id: None,
        })
        .await
        .expect_err("a failing strict read fails closed");
    assert!(
        matches!(error, WorkbenchUseCaseError::Service(ServiceError::InternalError(_)) | WorkbenchUseCaseError::Group(_)),
        "unexpected error shape: {error:?}"
    );
}
