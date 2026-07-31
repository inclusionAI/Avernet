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
use bcs_service_api::port::repo::{MessageRepoPort, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService, GroupStrategy, Participant,
    ParticipantRole,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_session_v1::{SessionServiceConfig, SessionServiceImpl};

struct Fixture {
    service: SessionServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    message_repo: Arc<MemoryMessageRepo>,
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
            session_repo,
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
        let mut group = Group::new(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        );
        group.originator = Some(driver.to_string());
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::Chat;
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
    assert_eq!(expert.mode, BotParticipantMode::Muted);
    assert_eq!(expert.name.as_deref(), Some("expert"));
    let driver = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant");
    assert_eq!(driver.role, ParticipantRole::Driver);
    assert_eq!(driver.mode, BotParticipantMode::Auto);
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
    assert_eq!(added.mode, BotParticipantMode::Auto);

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
    assert_eq!(error.code(), "conflict");

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
    assert_eq!(updated.mode, BotParticipantMode::Muted);

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
    assert_eq!(updated.mode, BotParticipantMode::Muted);

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
