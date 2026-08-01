//! Integration tests for the V1 Session facade.
//!
//! Exercises the `SessionService` + `SessionMessageService` impls against the
//! real in-memory store stack (GroupCore / BotCore / SessionManagementService
//! / MemorySessionRepo / MemoryMessageRepo), mirroring the sibling
//! `bcs-group-v1` test harness.

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::Duration;

use bcs_bot::BotCore;
use bcs_domain::{NewMessage, SenderType};
use bcs_friend::FriendCore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_message_store::MemoryMessageRepo;
use bcs_relation::RelationCore;
use bcs_service_api::application::v1::{
    AddSessionParticipant, AuthenticatedUser, BotParticipantMode, CompleteSession, CreateSession,
    DeleteSession, DeleteSessionParticipant, GetSession, ListSessionMessages, ListSessions,
    SessionInput, SessionMessageService, SessionParticipantInput, SessionService,
    SessionStatus as V1SessionStatus, UpdateSession, UpdateSessionParticipant,
};
use bcs_service_api::port::repo::{MessageRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotRegistryCoreService, Group, GroupCoreService, GroupStrategy,
    Participant, ParticipantMode, ParticipantRole, SessionKind,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_session_v1::{SessionServiceConfig, SessionServiceImpl};

struct Fixture {
    service: SessionServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    message_repo: Arc<MemoryMessageRepo>,
    session_repo: Arc<dyn SessionRepoPort>,
}

impl Fixture {
    async fn new() -> Self {
        let group_repo: Arc<dyn bcs_service_api::port::repo::GroupRepoPort> =
            Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let bots = Arc::new(BotCore::memory());
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
        let message_repo = Arc::new(MemoryMessageRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            group_repo,
        ));
        let service = SessionServiceImpl::new(
            sessions,
            groups.clone(),
            bots.clone(),
            friends,
            relation,
            session_repo.clone(),
            message_repo.clone(),
            SessionServiceConfig {
                relation_env: "dev".to_string(),
            },
        );
        Self {
            service,
            groups,
            bots,
            message_repo,
            session_repo,
        }
    }

    async fn add_bot(&self, bot_uuid: &str) {
        self.bots
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
    }

    async fn store_group(&self, group_id: &str, driver: &str, context: Option<&str>) {
        self.store_group_with_originator(group_id, driver, driver, context)
            .await;
    }

    /// Like `store_group` but lets the caller name a distinct group
    /// `originator` — used by the Human-view authz tests where the Human must
    /// be a group manager (originator) to pass `can_read_session` before the
    /// `view_bot_id` authz runs.
    async fn store_group_with_originator(
        &self,
        group_id: &str,
        driver: &str,
        originator: &str,
        context: Option<&str>,
    ) {
        let mut group = Group::new(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        );
        group.originator = Some(originator.to_string());
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::Chat;
        group.context = context.map(str::to_string);
        self.groups.upsert(group).await.expect("store group");
    }

    /// Store a ManagerWorker group whose `originator` is the given actor id
    /// and whose roster seeds the driver (Driver) plus each worker (Worker).
    /// Used by the view_bot_id authz tests: the ManagerWorker owner-filter
    /// scoping (`IsNull` public vs `Eq(worker)`) is deterministic in the memory
    /// fixture, unlike the Chat `visible_from_seq` cutoff which needs a
    /// `current_msg_seq` bump only the MySQL store performs.
    async fn store_manager_worker_group_with_originator(
        &self,
        group_id: &str,
        driver: &str,
        workers: &[&str],
        originator: &str,
        context: Option<&str>,
    ) {
        let mut participants = vec![Participant::bot(driver, ParticipantRole::Driver)];
        for &worker in workers {
            participants.push(Participant::bot(worker, ParticipantRole::Worker));
        }
        let mut group = Group::new(group_id, driver, participants);
        group.originator = Some(originator.to_string());
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::ManagerWorker;
        group.context = context.map(str::to_string);
        self.groups.upsert(group).await.expect("store group");
    }
}

fn bot_principal(bot_uuid: &str) -> bcs_service_api::application::v1::Principal {
    bcs_service_api::application::v1::Principal::bot(
        bot_uuid.to_string(),
        "tenant-a".to_string(),
        BTreeSet::new(),
    )
}

fn human_principal(staff_no: &str) -> bcs_service_api::application::v1::Principal {
    bcs_service_api::application::v1::Principal::human(
        AuthenticatedUser {
            id: staff_no.to_string(),
            username: staff_no.to_string(),
            display_name: None,
            full_name: None,
        },
        "tenant-a".to_string(),
        BTreeSet::new(),
    )
}

fn participant_input(bot_uuid: &str, mode: Option<BotParticipantMode>) -> SessionParticipantInput {
    SessionParticipantInput {
        bot_uuid: bot_uuid.to_string(),
        mode,
    }
}

async fn create_session(
    fixture: &Fixture,
    principal: bcs_service_api::application::v1::Principal,
    group_id: &str,
    driver: &str,
    participants: Vec<SessionParticipantInput>,
    input: Option<SessionInput>,
    title: Option<&str>,
) -> bcs_service_api::application::v1::CreateSessionOutcome {
    fixture
        .service
        .create(CreateSession {
            principal,
            group_id: group_id.to_string(),
            driver_bot_uuid: driver.to_string(),
            title: title.map(str::to_string),
            input,
            participants,
        })
        .await
        .expect("create session")
}

/// Append a single message owned by `owner` (Some = worker-private, None =
/// public) into `session_id`. `created_at` is passed through so multi-message
/// DESC ordering is deterministic; the repo assigns `session_seq` in append
/// order (1, 2, 3, ...).
async fn seed_message(fixture: &Fixture, session_id: &str, owner: Option<&str>, created_at: u64) {
    fixture
        .message_repo
        .append_message(NewMessage {
            group_id: "g1".into(),
            session_id: session_id.to_string(),
            sender_id: "driver".into(),
            sender_type: SenderType::Bot,
            message_type: "text".into(),
            content: serde_json::Value::String(format!("msg-{created_at}")),
            client_msg_id: None,
            owner_bot_id: owner.map(str::to_string),
            created_at,
            run_id: String::new(),
        })
        .await
        .expect("append message");
}

#[tokio::test]
async fn create_as_manager_succeeds_and_projects_participants() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", Some("the task")).await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", Some(BotParticipantMode::Muted))],
        None,
        Some("session title"),
    )
    .await;

    assert!(outcome.created);
    let detail = outcome.session;
    assert_eq!(detail.group_id, "g1");
    assert_eq!(detail.status, V1SessionStatus::Running);
    assert_eq!(detail.title.as_deref(), Some("session title"));
    // Input falls back to the parent group's context.
    assert_eq!(
        detail.input,
        Some(SessionInput {
            query: Some("the task".into())
        })
    );
    // Driver (Driver role) + expert (Consultant, Muted).
    assert_eq!(detail.participants.len(), 2);
    let expert = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "expert")
        .expect("expert participant");
    assert_eq!(expert.role, ParticipantRole::Consultant);
    assert_eq!(expert.mode, ParticipantMode::Muted);
    assert_eq!(expert.name.as_deref(), Some("expert"));
    let driver = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant");
    assert_eq!(driver.role, ParticipantRole::Driver);
    assert_eq!(driver.mode, ParticipantMode::Auto);
}

#[tokio::test]
async fn create_with_explicit_input_does_not_fall_back() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", Some("ignored context")).await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        Some(SessionInput {
            query: Some("explicit query".into()),
        }),
        None,
    )
    .await;

    assert_eq!(
        outcome.session.input,
        Some(SessionInput {
            query: Some("explicit query".into())
        })
    );
}

#[tokio::test]
async fn create_as_non_manager_is_forbidden() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let error = fixture
        .service
        .create(CreateSession {
            principal: bot_principal("outsider"),
            group_id: "g1".into(),
            driver_bot_uuid: "driver".into(),
            title: None,
            input: None,
            participants: vec![participant_input("driver", None)],
        })
        .await
        .expect_err("non-manager should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn create_with_unknown_driver_bot_is_not_found() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let error = fixture
        .service
        .create(CreateSession {
            principal: bot_principal("driver"),
            group_id: "g1".into(),
            driver_bot_uuid: "ghost".into(),
            title: None,
            input: None,
            participants: vec![participant_input("driver", None)],
        })
        .await
        .expect_err("unknown driver should 404");
    assert_eq!(error.code(), "bot_not_found");
}

#[tokio::test]
async fn list_sorts_desc_and_reports_total() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    // Create three sessions; sleep briefly so created_at strictly differs
    // and the DESC primary sort (not just the id tie-breaker) is exercised.
    for _ in 0..3 {
        create_session(
            &fixture,
            bot_principal("driver"),
            "g1",
            "driver",
            vec![participant_input("expert", None)],
            None,
            None,
        )
        .await;
        tokio::time::sleep(Duration::from_millis(6)).await;
    }

    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            principal: bot_principal("driver"),
            group_id: "g1".into(),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("list sessions");

    assert_eq!(page.total, 3);
    assert_eq!(page.items.len(), 3);
    // created_at must be non-increasing (DESC).
    for window in page.items.windows(2) {
        assert!(
            window[0].created_at >= window[1].created_at,
            "sessions must be sorted by created_at DESC"
        );
    }
}

#[tokio::test]
async fn list_non_member_is_forbidden() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let error = SessionService::list(
        &fixture.service,
        ListSessions {
            principal: bot_principal("outsider"),
            group_id: "g1".into(),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect_err("non-member should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn get_returns_detail_and_not_found_for_missing() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("title"),
    )
    .await;

    let detail = fixture
        .service
        .get(GetSession {
            principal: bot_principal("driver"),
            session_id: outcome.session.session_id.clone(),
        })
        .await
        .expect("get session");
    assert_eq!(detail.session_id, outcome.session.session_id);
    assert_eq!(detail.title.as_deref(), Some("title"));

    let error = fixture
        .service
        .get(GetSession {
            principal: bot_principal("driver"),
            session_id: "g1:deadbeef".into(),
        })
        .await
        .expect_err("missing session should 404");
    assert_eq!(error.code(), "session_not_found");
}

#[tokio::test]
async fn update_changes_title() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("old title"),
    )
    .await;

    let detail = fixture
        .service
        .update(UpdateSession {
            principal: bot_principal("driver"),
            session_id: outcome.session.session_id.clone(),
            title: Some("new title".into()),
        })
        .await
        .expect("update session");
    assert_eq!(detail.title.as_deref(), Some("new title"));
}

#[tokio::test]
async fn update_requires_a_field() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("title"),
    )
    .await;

    let error = fixture
        .service
        .update(UpdateSession {
            principal: bot_principal("driver"),
            session_id: outcome.session.session_id,
            title: None,
        })
        .await
        .expect_err("empty patch should 400");
    assert_eq!(error.code(), "invalid_request");
}

#[tokio::test]
async fn delete_is_idempotent() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let first = fixture
        .service
        .delete(DeleteSession {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
        })
        .await
        .expect("first delete");
    assert!(first.deleted);

    let second = fixture
        .service
        .delete(DeleteSession {
            principal: bot_principal("driver"),
            session_id,
        })
        .await
        .expect("second delete");
    // Idempotent: a missing session yields deleted=false, not a 404.
    assert!(!second.deleted);
}

#[tokio::test]
async fn complete_is_idempotent() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let first = fixture
        .service
        .complete(CompleteSession {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
        })
        .await
        .expect("first complete");
    assert_eq!(first.status, V1SessionStatus::Completed);
    assert!(first.completed_at > 0);

    let second = fixture
        .service
        .complete(CompleteSession {
            principal: bot_principal("driver"),
            session_id,
        })
        .await
        .expect("second complete (idempotent)");
    assert_eq!(second.status, V1SessionStatus::Completed);
    // Idempotent: same completed_at as the first completion.
    assert_eq!(second.completed_at, first.completed_at);
}

#[tokio::test]
async fn list_messages_returns_descending_with_cursor() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    for i in 0..3u32 {
        fixture
            .message_repo
            .append_message(NewMessage {
                group_id: "g1".into(),
                session_id: session_id.clone(),
                sender_id: "driver".into(),
                sender_type: SenderType::Bot,
                message_type: "text".into(),
                content: serde_json::Value::String(format!("msg-{i}")),
                client_msg_id: None,
                owner_bot_id: None,
                created_at: (i as u64) * 10,
                run_id: String::new(),
            })
            .await
            .expect("append message");
    }

    let page = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            before: None,
            limit: 50,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages");

    assert_eq!(page.messages.len(), 3);
    // cursor-based page: no total/offset/limit round-trip.
    assert!(!page.has_more);
    assert!(page.next_cursor.is_none());
    // created_at DESC, session_seq DESC (legacy direct-read order).
    for window in page.messages.windows(2) {
        assert!(
            window[0].session_seq > window[1].session_seq,
            "messages must be ordered by session_seq DESC"
        );
    }
    assert_eq!(page.messages[0].content, "msg-2");
    assert_eq!(page.messages[2].content, "msg-0");
    assert_eq!(
        page.messages[0].sender_type,
        bcs_service_api::application::v1::MessageSenderKind::Bot
    );
}

#[tokio::test]
async fn list_messages_composite_cursor_no_skip_tied_created_at() {
    // VYQHI regression: messages sharing a created_at at a page boundary must
    // not be skipped when following the opaque composite string cursor.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Seed five messages ALL with the same created_at; session_seq breaks ties.
    for i in 0..5u32 {
        fixture
            .message_repo
            .append_message(NewMessage {
                group_id: "g1".into(),
                session_id: session_id.clone(),
                sender_id: "driver".into(),
                sender_type: SenderType::Bot,
                message_type: "text".into(),
                content: serde_json::Value::String(format!("m{i}")),
                client_msg_id: None,
                owner_bot_id: None,
                created_at: 7_000,
                run_id: String::new(),
            })
            .await
            .expect("append message");
    }

    // Page 1 (limit 2): newest two by (created_at DESC, session_seq DESC) →
    // seqs 5, 4; has_more; opaque next_cursor encodes "7000:4".
    let page = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            before: None,
            limit: 2,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages page 1");
    assert!(page.has_more);
    assert_eq!(page.next_cursor.as_deref(), Some("7000:4"));
    assert_eq!(
        page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
        vec![5, 4]
    );

    // Page 2: pass the opaque next_cursor back as before → seqs 3, 2.
    let page = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            before: page.next_cursor,
            limit: 2,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages page 2");
    assert!(page.has_more);
    assert_eq!(page.next_cursor.as_deref(), Some("7000:2"));
    assert_eq!(
        page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
        vec![3, 2]
    );

    // Page 3: follow again → seq 1, no more.
    let page = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: bot_principal("driver"),
            session_id,
            before: page.next_cursor,
            limit: 2,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages page 3");
    assert!(!page.has_more);
    assert!(page.next_cursor.is_none());
    assert_eq!(
        page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
        vec![1]
    );
}

#[tokio::test]
async fn list_messages_rejects_malformed_before_cursor() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;

    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: bot_principal("driver"),
            session_id: outcome.session.session_id.clone(),
            before: Some("not-a-cursor".to_string()),
            limit: 10,
            view_bot_id: None,
        },
    )
    .await
    .expect_err("malformed cursor should 400");
    assert_eq!(error.code(), "invalid_request");
}

#[tokio::test]
async fn participant_add_update_remove_lifecycle() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Add.
    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            mode: Some(BotParticipantMode::Auto),
        })
        .await
        .expect("add participant");
    assert_eq!(added.actor_id, "newcomer");
    assert_eq!(added.role, ParticipantRole::Consultant);
    assert_eq!(added.mode, ParticipantMode::Auto);

    // Adding twice is a conflict.
    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            mode: None,
        })
        .await
        .expect_err("duplicate add should conflict");
    assert_eq!(error.code(), "participant_already_exists");

    // Update mode.
    let updated = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            mode: BotParticipantMode::Muted,
        })
        .await
        .expect("update participant");
    assert_eq!(updated.mode, ParticipantMode::Muted);

    // Remove.
    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("delete participant");
    assert!(removed.deleted);

    // Remove again is idempotent.
    let again = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            principal: bot_principal("driver"),
            session_id,
            bot_uuid: "newcomer".into(),
        })
        .await
        .expect("idempotent delete participant");
    assert!(!again.deleted);
}

#[tokio::test]
async fn delete_participant_human_owner_succeeds_and_non_owner_forbidden() {
    // VYQHN: a Human principal who owns the target Bot (via created_by) may
    // self-service-remove it from a session they can neither manage nor read
    // otherwise; a Human without ownership is still forbidden.
    let fixture = Fixture::new().await;
    for bot in ["driver", "bot-a"] {
        fixture.add_bot(bot).await;
    }
    // bot-a is owned by Human staff-1 via created_by.
    fixture
        .bots
        .save_created_by("bot-a", "staff-1", true)
        .await
        .expect("save owner");
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("bot-a", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Owner Human removes the owned Bot (not self, not manager, not creator
    // of this session): authorized solely by is_human_owner.
    let removed = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            principal: human_principal("staff-1"),
            session_id: session_id.clone(),
            bot_uuid: "bot-a".into(),
        })
        .await
        .expect("owner human removes owned bot");
    assert!(removed.deleted);

    // Re-add bot-a (the driver is a manager and bot-a is public) so the
    // non-owner scenario starts from the same participant state.
    fixture
        .service
        .add_participant(AddSessionParticipant {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "bot-a".into(),
            mode: Some(BotParticipantMode::Auto),
        })
        .await
        .expect("re-add bot-a");

    // Non-owner Human (staff-2) is forbidden — neither self, owner, nor
    // manager/creator.
    let error = fixture
        .service
        .delete_participant(DeleteSessionParticipant {
            principal: human_principal("staff-2"),
            session_id,
            bot_uuid: "bot-a".into(),
        })
        .await
        .expect_err("non-owner human should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn update_participant_human_owner_succeeds_and_non_owner_forbidden() {
    // VYQHN symmetric: the same Human-owner authorization applies to the
    // participant mode update path.
    let fixture = Fixture::new().await;
    for bot in ["driver", "bot-a"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("bot-a", "staff-1", true)
        .await
        .expect("save owner");
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("bot-a", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Owner Human mutates the owned Bot's participant mode.
    let updated = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            principal: human_principal("staff-1"),
            session_id: session_id.clone(),
            bot_uuid: "bot-a".into(),
            mode: BotParticipantMode::Muted,
        })
        .await
        .expect("owner human updates owned bot");
    assert_eq!(updated.mode, ParticipantMode::Muted);

    // Non-owner Human is forbidden.
    let error = fixture
        .service
        .update_participant(UpdateSessionParticipant {
            principal: human_principal("staff-2"),
            session_id,
            bot_uuid: "bot-a".into(),
            mode: BotParticipantMode::Auto,
        })
        .await
        .expect_err("non-owner human should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn complete_service_invocation_session_rejected() {
    // VaGQN: ServiceInvocation sessions have their own callback/output
    // lifecycle and must not be completed via this V1 endpoint (legacy
    // handler rejects "service sessions cannot be completed via this
    // endpoint"). Gate the CAS with a session_kind check.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    // The V1 facade `create` hardcodes SessionKind::Chat, so seed a
    // ServiceInvocation session directly via the repo.
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed service invocation session");
    assert_eq!(session.session_kind, SessionKind::ServiceInvocation);

    let error = fixture
        .service
        .complete(CompleteSession {
            principal: bot_principal("driver"),
            session_id: session.id,
        })
        .await
        .expect_err("service sessions cannot be completed via V1");

    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Conflict { .. }
        ),
        "expected Conflict, got {error:?}",
    );
    assert_eq!(error.code(), "conflict");
}

#[tokio::test]
async fn session_only_participant_can_list_sessions() {
    // VaGQQ: a Bot that is only a session participant (added to a session
    // but NOT to group.participants) must still be able to list the group's
    // sessions. The sibling bcs-group-v1 facade already permits this via
    // `list_group_ids_by_session_participant`; the session-v1 facade's
    // `can_read_group` must mirror that check.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Add newcomer to the SESSION (not the group). newcomer is public so the
    // collaboration-eligibility check passes; the driver is the manager.
    fixture
        .service
        .add_participant(AddSessionParticipant {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            mode: Some(BotParticipantMode::Auto),
        })
        .await
        .expect("add newcomer to session");

    // Guard: newcomer must be session-only (not in group.participants).
    let group = fixture.groups.get("g1").await.expect("group exists");
    assert!(
        !group
            .participants
            .iter()
            .any(|p| p.bot_uuid == "newcomer"),
        "newcomer must be session-only (not in group.participants)"
    );

    // Session-only participant may list the group's sessions.
    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            principal: bot_principal("newcomer"),
            group_id: "g1".into(),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("session-only participant may list sessions");
    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].group_id, "g1");
}

#[tokio::test]
async fn session_only_participant_list_sessions_scoped() {
    // Vcj5: a session-only Bot (in `session.participants` but NOT in
    // `group.participants`) must see ONLY the sessions it participates in
    // when calling `list_sessions` — not the entire group session pool. The
    // prior VaGQQ fix let a session-only Bot pass `can_read_group`, but the
    // list + count calls still passed `participant_id=None`, surfacing ALL
    // group sessions. The V1 facade now scopes both `list_by_group` and
    // `count_by_group` to `Some(principal.actor_id())` when access derives
    // solely from session membership.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "expert2", "expert3", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    // S1: driver + expert. Add newcomer to S1 only (session-only for
    // newcomer — newcomer is NOT a group.participants member).
    let s1 = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("S1"),
    )
    .await;
    let s1_id = s1.session.session_id.clone();
    fixture
        .service
        .add_participant(AddSessionParticipant {
            principal: bot_principal("driver"),
            session_id: s1_id.clone(),
            bot_uuid: "newcomer".into(),
            mode: Some(BotParticipantMode::Auto),
        })
        .await
        .expect("add newcomer to S1");

    // S2 + S3: driver + a different expert; newcomer is NOT a participant.
    let s2 = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert2", None)],
        None,
        Some("S2"),
    )
    .await;
    let s3 = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert3", None)],
        None,
        Some("S3"),
    )
    .await;

    // Guard: newcomer is session-only (in S1.participants, NOT in
    // group.participants).
    let group = fixture.groups.get("g1").await.expect("group exists");
    assert!(
        !group
            .participants
            .iter()
            .any(|p| p.bot_uuid == "newcomer"),
        "newcomer must be session-only (not in group.participants)"
    );

    // Session-only Bot lists g1 sessions → must see ONLY S1, not S2 / S3.
    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            principal: bot_principal("newcomer"),
            group_id: "g1".into(),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("session-only participant lists scoped sessions");
    assert_eq!(page.total, 1, "total must reflect scoping (only S1)");
    assert_eq!(page.items.len(), 1, "items must contain only S1");
    assert_eq!(page.items[0].session_id, s1_id);
    assert_ne!(page.items[0].session_id, s2.session.session_id);
    assert_ne!(page.items[0].session_id, s3.session.session_id);
}

#[tokio::test]
async fn create_session_duplicate_participant_rejected() {
    // VeHS7: a CreateSession command that lists the same bot twice must be
    // rejected before persistence. The memory store would otherwise silently
    // accept an inflated roster while the MySQL store would surface the
    // `uk_session_participants_env_session_bot` unique constraint as a generic
    // session-ID collision on retry — inconsistent behavior across backends.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    let error = fixture
        .service
        .create(CreateSession {
            principal: bot_principal("driver"),
            group_id: "g1".into(),
            driver_bot_uuid: "driver".into(),
            title: None,
            input: None,
            participants: vec![
                participant_input("expert", None),
                participant_input("expert", Some(BotParticipantMode::Muted)),
            ],
        })
        .await
        .expect_err("duplicate participant should be rejected");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::InvalidInput { .. }
        ),
        "expected InvalidInput, got {error:?}",
    );
    assert_eq!(error.code(), "invalid_request");

    // Guard: no session was materialized for the rejected command.
    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            principal: bot_principal("driver"),
            group_id: "g1".into(),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("list sessions");
    assert_eq!(page.total, 0, "no session should be persisted on rejection");
}

// ── view_bot_id authz (Principal-based visibility scoping) ──────────────
//
// The optional `view_bot_id` query param on `list_session_messages` is
// resolved by the V1 facade into the `Option<&str>` cutoff identity passed
// to the legacy `compute_session_history_query` helper. Authz rules:
// - Bot Principal: omit → self; explicit → must equal self; else forbidden.
// - Human Principal (must be a group manager/originator to read the session):
//   omit → None (manager view); `"human_<self>"` → own view; any other Bot
//   UUID → ownership verified via `is_owned_bot` (`created_by` or creator
//   relation edge), else forbidden.
//
// These tests use a ManagerWorker group + owner-tagged messages so the
// `MessageOwnerFilter` scoping (`IsNull` public vs `Eq(worker)`) is the
// observable signal that the right `view_bot_id` was resolved. The Chat
// `visible_from_seq` cutoff is NOT observable in the memory fixture: the
// MemoryMessageRepo never bumps `session.current_msg_seq` (only the MySQL
// store does), so `compute_visible_from_seq` always returns `None` here.

/// Build the ManagerWorker session used by every view_bot_id authz test: a
/// Chat-kind session seeded with three owner-tagged messages — public at
/// seq 1, worker-a's at seq 2, worker-b's at seq 3. Callers must first store
/// the group (ManagerWorker, with worker-a / worker-b as Worker participants)
/// and register the bots.
async fn setup_manager_worker_session(fixture: &Fixture) -> String {
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::bot("worker-a", ParticipantRole::Worker),
                    Participant::bot("worker-b", ParticipantRole::Worker),
                ],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed manager-worker session");
    let session_id = session.id.clone();
    seed_message(fixture, &session_id, None, 10).await;
    seed_message(fixture, &session_id, Some("worker-a"), 20).await;
    seed_message(fixture, &session_id, Some("worker-b"), 30).await;
    session_id
}

#[tokio::test]
async fn bot_view_session_messages_defaults_to_self() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let page = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: bot_principal("worker-a"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages");

    // Omitted view_bot_id auto-derives self ("worker-a"); as a Worker this
    // resolves to owner_filter=Eq("worker-a") → only worker-a's message.
    assert_eq!(page.messages.len(), 1);
    assert!(!page.has_more);
    assert_eq!(page.messages[0].session_seq, 2);
}

#[tokio::test]
async fn bot_view_session_messages_explicitly_self() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let page = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: bot_principal("worker-a"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("worker-a".to_string()),
        },
    )
    .await
    .expect("list messages");

    // Explicit self == omitted self: same Eq("worker-a") scoping.
    assert_eq!(page.messages.len(), 1);
    assert_eq!(page.messages[0].session_seq, 2);
}

#[tokio::test]
async fn bot_view_session_messages_as_other_bot_forbidden() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "driver",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: bot_principal("worker-a"),
            session_id,
            before: None,
            limit: 100,
            view_bot_id: Some("worker-b".to_string()),
        },
    )
    .await
    .expect_err("bot impersonating another bot should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn human_view_session_messages_god_view_no_cutoff() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let page = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages");

    // Omitted → None → manager god-view: ManagerWorker Public (IsNull) →
    // only the public message (worker-private messages are hidden from the
    // unscoped manager view, distinct from the worker-a bot view above).
    assert_eq!(page.messages.len(), 1);
    assert_eq!(page.messages[0].session_seq, 1);
}

#[tokio::test]
async fn human_view_session_messages_as_self_human() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let page = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("human_staff-1".to_string()),
        },
    )
    .await
    .expect("list messages");

    // `"human_<self>"` → resolved Some; `manager_worker_history_view`
    // special-cases the `human_` prefix to Public → IsNull → public message.
    assert_eq!(page.messages.len(), 1);
    assert_eq!(page.messages[0].session_seq, 1);
}

#[tokio::test]
async fn human_view_session_messages_as_owned_bot() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    // worker-a is owned by Human staff-1 via created_by.
    fixture
        .bots
        .save_created_by("worker-a", "staff-1", true)
        .await
        .expect("save owner");
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let page = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("worker-a".to_string()),
        },
    )
    .await
    .expect("human views as owned bot");

    // Ownership verified → Some("worker-a"); worker-a is a Worker →
    // owner_filter=Eq("worker-a") → worker-a's message.
    assert_eq!(page.messages.len(), 1);
    assert_eq!(page.messages[0].session_seq, 2);
}

#[tokio::test]
async fn human_view_session_messages_as_unowned_bot_forbidden() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker-a", "worker-b"] {
        fixture.add_bot(bot).await;
    }
    // worker-b is registered but NOT owned by staff-1 (created_by stays None).
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker-a", "worker-b"],
            "human_staff-1",
            None,
        )
        .await;
    let session_id = setup_manager_worker_session(&fixture).await;

    let error = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            principal: human_principal("staff-1"),
            session_id,
            before: None,
            limit: 100,
            view_bot_id: Some("worker-b".to_string()),
        },
    )
    .await
    .expect_err("human viewing as unowned bot should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}

#[tokio::test]
async fn get_preserves_human_participant_from_legacy_invitation_join() {
    // Vey7i: the legacy invitation-accept path (`join_session_by_invite`)
    // inserts a Human participant directly into `session.participants` with
    // `actor_kind: Human, mode: Present` (see
    // `bcs-group/src/application/invite.rs`). The V1 facade `get` must surface
    // that participant verbatim — `actor_kind: Human` and `mode: Present` —
    // NOT boot-truncate it to the Bot-only `Auto`. This seeds the same roster
    // shape via the repo (the contract `join_session_by_invite` ultimately
    // writes via `SessionManagementService::add_participant`) and reads it
    // back through the V1 `SessionService::get` projection path.
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant {
                        bot_uuid: "human_staff-1".into(),
                        bot_name: Some("Alice".into()),
                        kind: None,
                        role: ParticipantRole::Consultant,
                        actor_kind: ActorKind::Human,
                        mode: Some(ParticipantMode::Present),
                    },
                ],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session with human participant");

    let detail = fixture
        .service
        .get(GetSession {
            principal: bot_principal("driver"),
            session_id: session.id.clone(),
        })
        .await
        .expect("get session");

    let human = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "human_staff-1")
        .expect("human participant present in V1 projection");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.mode, ParticipantMode::Present);
    assert_eq!(human.role, ParticipantRole::Consultant);

    // The Bot driver is still projected as a Bot with Auto (no regression).
    let driver = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant present");
    assert_eq!(driver.actor_kind, ActorKind::Bot);
    assert_eq!(driver.mode, ParticipantMode::Auto);
}

// ── VfhG3: derive session participant role from parent group ───────────────
//
// create_session / add_participant previously hardcoded
// ParticipantRole::Consultant for every participant, losing the Worker /
// Manager role carried by the parent group's roster. The V1 facade now derives
// the role from group.participants first, then falls back to the strategy
// default (ManagerWorker→Worker, else Consultant), mirroring the legacy
// bcs-http handler which cloned group.participants. add_participant also
// surfaces an explicit `participant_already_exists` 409 (the legacy memory repo
// silently skipped duplicates).

#[tokio::test]
async fn create_session_preserves_manager_worker_worker_role() {
    // VfhG3: when the parent group is ManagerWorker and a participant Bot is a
    // Worker in group.participants, create_session must derive the Worker role
    // for the session participant rather than hardcoding Consultant.
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker"],
            "driver",
            Some("the task"),
        )
        .await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("worker", None)],
        None,
        None,
    )
    .await;

    let worker = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "worker")
        .expect("worker participant projected");
    assert_eq!(
        worker.role,
        ParticipantRole::Worker,
        "VfhG3: ManagerWorker Worker must keep Worker role in session, not Consultant"
    );
    assert_eq!(worker.mode, ParticipantMode::Auto);

    // The driver is still forced to the Driver role regardless of derivation.
    let driver = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant projected");
    assert_eq!(driver.role, ParticipantRole::Driver);
}

#[tokio::test]
async fn create_session_defaults_consultant_for_chat_context() {
    // VfhG3: a Bot NOT present in the parent group.participants (Chat strategy)
    // falls back to the strategy default (Chat → Consultant), preserving the
    // pre-VfhG3 behaviour for the common Chat case.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;

    let expert = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "expert")
        .expect("expert participant projected");
    assert_eq!(
        expert.role,
        ParticipantRole::Consultant,
        "VfhG3: Chat group + non-roster participant defaults to Consultant"
    );
}

#[tokio::test]
async fn add_participant_duplication_rejects_409() {
    // VfhG3: adding the same Bot to a session twice must reject the second call
    // with a 409 Conflict carrying the explicit `participant_already_exists`
    // code (the legacy memory repo silently skipped duplicates).
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // First add of newcomer succeeds.
    fixture
        .service
        .add_participant(AddSessionParticipant {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            mode: Some(BotParticipantMode::Auto),
        })
        .await
        .expect("first add of newcomer");

    // Second add of newcomer is rejected with participant_already_exists.
    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            mode: None,
        })
        .await
        .expect_err("duplicate add should reject with participant_already_exists");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Conflict { ref code, .. }
                if code == "participant_already_exists"
        ),
        "expected Conflict(participant_already_exists), got {error:?}",
    );
    assert_eq!(error.code(), "participant_already_exists");
}

#[tokio::test]
async fn add_participant_derives_worker_role_for_manager_worker() {
    // VfhG3: when the parent group is ManagerWorker and the added Bot is a
    // Worker in group.participants, add_participant must derive the Worker role
    // rather than hardcoding Consultant.
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker"],
            "driver",
            None,
        )
        .await;
    // Seed a session with driver + expert (NOT worker) so worker can be added
    // via add_participant and the derived role is observable.
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            principal: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "worker".into(),
            mode: Some(BotParticipantMode::Auto),
        })
        .await
        .expect("add worker participant");

    assert_eq!(
        added.role,
        ParticipantRole::Worker,
        "VfhG3: ManagerWorker Worker gains Worker role on add_participant, not Consultant"
    );
    assert_eq!(added.mode, ParticipantMode::Auto);
}
