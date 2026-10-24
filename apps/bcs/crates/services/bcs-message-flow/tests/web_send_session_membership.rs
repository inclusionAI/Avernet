//! F4 boundary at the queue Session resolution: when the caller
//! omits `session_id`, `resolve_group_session` selects the unique
//! Running Session on the sender's behalf — the entrypoint could
//! only authorize the Group target. Before ANY side effect (status
//! reactivation, routing, persistence, admission, delivery), the
//! resolved Session target must contain the sender as a present
//! participant. An explicit `session_id` stays trusted (the
//! entrypoint's strict target authorization already gated it).

use std::sync::Arc;

use bcs_domain::SessionKind;
use bcs_message_flow::BcsMessageFlow;
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::{
    CallerContext, GroupCoreService, HumanActor, ManagedMessageDeliveryService, MessageFlowService,
    Participant, ParticipantRole, ServiceError, WebSendCommand,
};

#[path = "support/session.rs"]
mod session_support;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
use session_support::{StaticSessionManagement, test_session};

fn web_send(group_id: &str, from: &str, session: Option<&str>, mention: &str) -> WebSendCommand {
    WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".into(),
            staff_no: "1".into(),
        }),
        group_id: group_id.into(),
        session_id: session.map(str::to_string),
        from_actor_id: from.into(),
        from_name: None,
        message: format!("@{mention} hello"),
        mentions: vec![mention.into()],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: vec![],
    }
}

/// Recording event factory: proves whether any `message.created`
/// event preparation happened — the unmanaged write anchor.
#[derive(Default)]
struct RecordingEventFactory(std::sync::Mutex<Vec<bcs_service_api::port::NewEvent>>);

impl bcs_service_api::port::EventRecordFactoryPort for RecordingEventFactory {
    fn prepare(
        &self,
        event: bcs_service_api::port::NewEvent,
    ) -> Result<
        Option<bcs_service_api::port::repo::AppendEventRecord>,
        bcs_service_api::port::EventRecordError,
    > {
        self.0.lock().unwrap().push(event.clone());
        Ok(Some(bcs_service_api::port::repo::AppendEventRecord {
            event,
            env: "local".into(),
            recorded_at: "2026-10-22T00:00:00.000Z".into(),
            retention_until_ms: u64::MAX,
        }))
    }
}

/// UNMANAGED + eventing fixture: no queue admission at all, a real
/// message repo backed by a memory event store, a recording event
/// factory, and real Session management over seeded Sessions.
struct UnmanagedFixture {
    flow: BcsMessageFlow,
    repo: Arc<bcs_message_store::MemoryMessageRepo>,
    factory: Arc<RecordingEventFactory>,
    session_repo: Arc<bcs_session_store::MemorySessionRepo>,
    bot_delivery: Arc<support::RecordingBotDelivery>,
}

async fn unmanaged_fixture(sessions: Vec<(String, Vec<Participant>)>) -> UnmanagedFixture {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_repo = Arc::new(bcs_session_store::MemorySessionRepo::new());
    let group_repo: Arc<dyn bcs_service_api::port::repo::GroupRepoPort> =
        Arc::new(bcs_group::MemoryGroupRepo::new());
    for (id, participants) in sessions {
        session_repo
            .create(
                "group-1",
                bcs_service_api::port::repo::NewSessionParams {
                    id: Some(id.clone()),
                    session_kind: SessionKind::Chat,
                    participants,
                    ..Default::default()
                },
            )
            .await
            .expect("seed session");
    }
    let session_management = Arc::new(
        bcs_session::SessionManagementServiceImpl::new(session_repo.clone(), group_repo),
    );
    let event_store = Arc::new(bcs_event_store::MemoryEventStore::new());
    let factory = Arc::new(RecordingEventFactory::default());
    let repo = Arc::new(
        bcs_message_store::MemoryMessageRepo::new()
            .with_environment("local".into())
            .with_event_store(event_store),
    );
    let authority = bcs_test_support::lazy_authority_chain();
    // The persistent-send caller/sender binding needs a live edge;
    // the workbench human targets these same Bots.
    authority.hook.seed_owner("bot-driver", "1");
    authority.hook.seed_owner("bot-observer", "1");
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone())
    .with_event_record_factory(factory.clone())
    .with_bot_authority_hook(Some(authority.hook))
    .with_session_management(session_management);
    UnmanagedFixture {
        flow,
        repo,
        factory,
        session_repo,
        bot_delivery: support.bot_delivery,
    }
}

async fn history_len(fixture: &UnmanagedFixture, session_id: &str) -> i64 {
    use bcs_service_api::port::repo::MessageRepoPort;
    fixture
        .repo
        .get_current_seq(session_id)
        .await
        .expect("history seq")
}

/// R1 (review): the NON-managed eventing path silently anchored a
/// Group-level send to a concrete Running Session even when the
/// sender was removed from that Session — the sender's membership
/// gate saw nothing. Now the same resolution runs before side
/// effects and rejects.
#[tokio::test]
async fn unmanaged_eventing_rejects_a_sender_missing_from_the_anchored_session() {
    let fixture = unmanaged_fixture(vec![(
        "group-1:ffffffff".into(),
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    )])
    .await;

    let error = fixture
        .flow
        .handle_web_send(web_send("group-1", "bot-observer", None, "bot-driver"))
        .await
        .expect_err("a Group member outside the anchored Session cannot send into it");
    assert!(
        matches!(error, ServiceError::Forbidden(ref message) if message.contains("resolved Session")),
        "unexpected error: {error:?}"
    );
    assert_eq!(history_len(&fixture, "group-1:ffffffff").await, 0, "no persistence");
    assert!(
        fixture.bot_delivery.frames().await.is_empty(),
        "no Bot delivery request"
    );
    assert!(
        fixture.factory.0.lock().unwrap().is_empty(),
        "no message.created event was prepared"
    );
}

#[tokio::test]
async fn unmanaged_eventing_member_send_anchors_the_resolved_session() {
    let fixture = unmanaged_fixture(vec![(
        "group-1:ffffffff".into(),
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            Participant::bot("bot-observer", ParticipantRole::Consultant),
        ],
    )])
    .await;

    let outcome = fixture
        .flow
        .handle_web_send(web_send("group-1", "bot-observer", None, "bot-driver"))
        .await
        .expect("a present Session member sends on the unmanaged eventing path");
    assert!(
        outcome.queue_admission.is_none(),
        "the unmanaged path keeps its direct delivery shape"
    );
    assert!(!fixture.bot_delivery.frames().await.is_empty(), "delivery happens");
    assert_eq!(
        history_len(&fixture, "group-1:ffffffff").await,
        1,
        "the message anchors into the resolved Session"
    );
    assert_eq!(fixture.factory.0.lock().unwrap().len(), 1, "one message.created event");
}

#[tokio::test]
async fn unmanaged_eventing_without_a_running_session_fails_closed_before_side_effects() {
    let session_repo: Arc<dyn SessionRepoPort> =
        Arc::new(bcs_session_store::MemorySessionRepo::new());
    // Zero Sessions exist for the group at all — nothing can be
    // resolved as Running, so the eventing contract fails closed.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let group_repo: Arc<dyn bcs_service_api::port::repo::GroupRepoPort> =
        Arc::new(bcs_group::MemoryGroupRepo::new());
    let session_management: Arc<dyn bcs_service_api::SessionManagementService> =
        Arc::new(bcs_session::SessionManagementServiceImpl::new(session_repo, group_repo));
    let event_store = Arc::new(bcs_event_store::MemoryEventStore::new());
    let factory = Arc::new(RecordingEventFactory::default());
    let repo = Arc::new(
        bcs_message_store::MemoryMessageRepo::new()
            .with_environment("local".into())
            .with_event_store(event_store),
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone())
    .with_event_record_factory(factory.clone())
    .with_session_management(session_management);

    let error = flow
        .handle_web_send(web_send("group-1", "bot-observer", None, "bot-driver"))
        .await
        .expect_err("eventing persistence cannot anchor without a Running Session");
    assert!(
        matches!(error, ServiceError::InvalidOperation { ref message, .. } if message.contains("without a running Session")),
        "unexpected error: {error:?}"
    );
    assert!(support.bot_delivery.frames().await.is_empty(), "no delivery happened");
    assert!(factory.0.lock().unwrap().is_empty(), "no event prepared");
}

/// Rule 4: a TRUE Group-only send (no eventing configured) never
/// anchors to a Session and stays untouched by this gate — the
/// legacy message lands with an empty session id.
#[tokio::test]
async fn unmanaged_without_eventing_keeps_true_group_only_sends() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let session_repo: Arc<dyn SessionRepoPort> =
        Arc::new(bcs_session_store::MemorySessionRepo::new());
    let group_repo: Arc<dyn bcs_service_api::port::repo::GroupRepoPort> =
        Arc::new(bcs_group::MemoryGroupRepo::new());
    let session_management = Arc::new(
        bcs_session::SessionManagementServiceImpl::new(session_repo.clone(), group_repo),
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone())
    .with_session_management(session_management);

    let outcome = flow
        .handle_web_send(web_send("group-1", "bot-observer", None, "bot-driver"))
        .await
        .expect("a Group-only legacy send never needed a Session");
    assert!(outcome.queue_admission.is_none());
    assert!(!support.bot_delivery.frames().await.is_empty());
    use bcs_service_api::port::repo::MessageRepoPort;
    assert_eq!(
        repo.get_current_seq("").await.expect("group-keyed history"),
        1,
        "the legacy message persists under the empty-session slot"
    );
}

/// Multiple Running Sessions: the resolution policy is the same
/// fixed choice persistence makes (the first row of the bounded
/// Running listing); the membership gate applies to THAT Session
/// and never "helpfully" falls through to a later one that would
/// match better.
#[tokio::test]
async fn unmanaged_eventing_with_multiple_running_sessions_anchors_the_first_only() {
    // Memory lists created_at DESC, session_id DESC on ties —
    // 'group-1:zzz-running' always sorts first here.
    let fixture = unmanaged_fixture(vec![
        (
            "group-1:00000001".into(),
            vec![
                Participant::bot("bot-driver", ParticipantRole::Driver),
                Participant::bot("bot-observer", ParticipantRole::Consultant),
            ],
        ),
        (
            "group-1:ffffffff".into(),
            vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
        ),
    ])
    .await;

    // bot-observer IS in the LATER session but NOT the anchored
    // first one: rejected, and nothing lands in either.
    let error = fixture
        .flow
        .handle_web_send(web_send("group-1", "bot-observer", None, "bot-driver"))
        .await
        .expect_err("the sender must be a member of the ANCHORED Session");
    assert!(
        matches!(error, ServiceError::Forbidden(ref message) if message.contains("resolved Session")),
        "unexpected error: {error:?}"
    );
    assert_eq!(history_len(&fixture, "group-1:00000001").await, 0);
    assert_eq!(history_len(&fixture, "group-1:ffffffff").await, 0);

    // The sender in the anchored first Session admits — into the
    // first Session only.
    let outcome = fixture
        .flow
        .handle_web_send(web_send("group-1", "bot-driver", None, "bot-observer"))
        .await
        .expect("the anchored Session's member sends");
    assert!(outcome.queue_admission.is_none());
    assert_eq!(history_len(&fixture, "group-1:ffffffff").await, 1);
    assert_eq!(history_len(&fixture, "group-1:00000001").await, 0);
}

#[tokio::test]
async fn implicit_resolution_rejects_a_sender_missing_from_the_resolved_session() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(
        bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()),
    );
    // Queue-managed Group; the ONLY Running Session holds just
    // the driver — bot-observer keeps its Group membership but
    // was removed from (never added to) the Session.
    let session = test_session(
        "group-1:sole",
        "group-1",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 10)]))
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let error = flow
        .handle_web_send(web_send("group-1", "bot-observer", None, "bot-driver"))
        .await
        .expect_err("a Group member outside the resolved Session cannot send into it");
    assert!(
        matches!(error, ServiceError::Forbidden(ref message) if message.contains("resolved Session")),
        "unexpected error: {error:?}"
    );

    // No persistence, no queue row, no bot request, no delivery.
    assert!(
        service.snapshot(None).await.unwrap().is_empty(),
        "the rejected send admits nothing"
    );
    assert!(
        support.bot_delivery.frames().await.is_empty(),
        "the rejected send reaches no Bot"
    );
}

#[tokio::test]
async fn implicit_resolution_admits_a_present_session_member() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(
        bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()),
    );
    let session = test_session("group-1:both", "group-1", group.participants.clone());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 10)]))
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let outcome = flow
        .handle_web_send(web_send("group-1", "bot-observer", None, "bot-driver"))
        .await
        .expect("a present Session member's implicit-resolution send admits");
    assert!(
        outcome.queue_admission.is_some(),
        "the managed Group admits the member's message into the queue"
    );
}

#[tokio::test]
async fn implicit_resolution_session_read_failure_fails_closed() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(
        bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()),
    );
    let session = test_session("group-1:flaky", "group-1", group.participants.clone());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 10)]))
    .with_session_management(Arc::new(
        StaticSessionManagement::new(session).with_get_failure(),
    ));

    let error = flow
        .handle_web_send(web_send("group-1", "bot-observer", None, "bot-driver"))
        .await
        .expect_err("a failing membership read is an error, never an admit");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "unexpected error: {error:?}"
    );
    assert!(service.snapshot(None).await.unwrap().is_empty());
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn explicit_session_targets_stay_trusted_at_this_boundary() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(
        bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()),
    );
    // The explicit Session does NOT contain bot-observer, but
    // this boundary must NOT re-authorize it: the entrypoint
    // (Workbench HTTP/WS admission) owns explicit-target gating.
    let session = test_session(
        "group-1:explicit",
        "group-1",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 10)]))
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let outcome = flow
        .handle_web_send(web_send(
            "group-1",
            "bot-observer",
            Some("group-1:explicit"),
            "bot-driver",
        ))
        .await
        .expect("an explicit target is re-gated only by the entrypoint");
    assert!(outcome.queue_admission.is_some());
}

/// The legacy persistent-send path resolves the SAME write anchor
/// up front too: a removed sender is rejected before persistence,
/// a present Session member lands in the anchored Session.
#[tokio::test]
async fn persistent_send_unmanaged_eventing_gates_the_anchor_membership() {
    use bcs_domain::{GroupMessageType, MessageRole};
    use bcs_service_api::PersistentGroupSendCommand;

    let fixture = unmanaged_fixture(vec![(
        "group-1:ffffffff".into(),
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            Participant::bot("bot-observer", ParticipantRole::Consultant),
        ],
    )])
    .await;

    let cmd = |sender: &str| PersistentGroupSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".into(),
            staff_no: "1".into(),
        }),
        group_id: "group-1".into(),
        sender: sender.into(),
        content: "persistent hello".into(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::Assistant,
        max_group_messages: 10,
        store_messages: true,
    };

    let outcome = fixture
        .flow
        .handle_persistent_group_send(cmd("bot-observer"))
        .await
        .expect("a present Session member's persistent send lands");
    assert!(outcome.queue_admission.is_none());
    assert_eq!(
        history_len(&fixture, "group-1:ffffffff").await,
        1,
        "the persistent message anchors into the resolved Session"
    );

    // Remove the sender from the anchored Session; the identical
    // request must now be rejected before any write.
    fixture
        .session_repo
        .remove_participant("group-1:ffffffff", "bot-observer")
        .await
        .expect("remove sender from the anchored session");
    let error = fixture
        .flow
        .handle_persistent_group_send(cmd("bot-observer"))
        .await
        .expect_err("the removed sender can no longer land messages in the Session");
    assert!(
        matches!(error, ServiceError::Forbidden(ref message) if message.contains("resolved Session")),
        "unexpected error: {error:?}"
    );
    assert_eq!(
        history_len(&fixture, "group-1:ffffffff").await,
        1,
        "nothing further was persisted"
    );
    assert_eq!(fixture.factory.0.lock().unwrap().len(), 1, "no new event prepared");
}

// ── R2 (review): a persistent send that fails the actual-Session
// admission must never consume quota nor disable the Group. ──────

fn persistent_cmd(sender: &str, max_group_messages: u64) -> bcs_service_api::PersistentGroupSendCommand {
    use bcs_domain::{GroupMessageType, MessageRole};
    bcs_service_api::PersistentGroupSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".into(),
            staff_no: "1".into(),
        }),
        group_id: "group-1".into(),
        sender: sender.into(),
        content: "persistent hello".into(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::Assistant,
        max_group_messages,
        store_messages: true,
    }
}

async fn support_counts(flow: &BcsMessageFlow, group_id: &str) -> (usize, bcs_domain::GroupStatus) {
    let count = flow.group.message_count(group_id).await.expect("count");
    let status = flow.group.get(group_id).await.expect("group").status;
    (count, status)
}

#[tokio::test]
async fn rejected_persistent_send_consumes_no_quota() {
    let fixture = unmanaged_fixture(vec![(
        "group-1:ffffffff".into(),
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    )])
    .await;
    let before = support_counts(&fixture.flow, "group-1").await.0;

    let error = fixture
        .flow
        .handle_persistent_group_send(persistent_cmd("bot-observer", 2))
        .await
        .expect_err("the sender is not in the anchored Session");
    assert!(
        matches!(error, ServiceError::Forbidden(_)),
        "unexpected error: {error:?}"
    );
    let (count, status) = support_counts(&fixture.flow, "group-1").await;
    assert_eq!(count, before, "a rejected request must not consume quota");
    assert_eq!(status, bcs_domain::GroupStatus::Active);
    assert_eq!(history_len(&fixture, "group-1:ffffffff").await, 0);
    assert!(fixture.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn rejected_persistent_retries_never_disable_the_group() {
    let fixture = unmanaged_fixture(vec![(
        "group-1:ffffffff".into(),
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    )])
    .await;

    for attempt in 1..=3 {
        let error = fixture
            .flow
            .handle_persistent_group_send(persistent_cmd("bot-observer", 2))
            .await
            .expect_err(&format!("attempt {attempt} keeps rejecting"));
        assert!(matches!(error, ServiceError::Forbidden(_)));
    }
    let (count, status) = support_counts(&fixture.flow, "group-1").await;
    assert_eq!(count, 1, "three rejected attempts consume nothing");
    assert_eq!(
        status,
        bcs_domain::GroupStatus::Active,
        "rejected attempts must never trip the limit → Inactive path"
    );
    assert!(fixture.bot_delivery.frames().await.is_empty());
    assert!(fixture.factory.0.lock().unwrap().is_empty());
}

#[tokio::test]
async fn quota_boundary_still_authorizes_session_membership_first() {
    let fixture = unmanaged_fixture(vec![(
        "group-1:ffffffff".into(),
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            Participant::bot("bot-observer", ParticipantRole::Consultant),
        ],
    )])
    .await;
    // The fixture counter starts at 1: with max=1 the quota is at
    // the boundary from the start.
    let error = fixture
        .flow
        .handle_persistent_group_send(persistent_cmd("bot-driver", 1))
        .await
        .expect_err("a member at the limit hits the quota rule");
    assert!(
        matches!(error, ServiceError::MessageLimitReached(_)),
        "an ADMITTED sender at the limit keeps the legacy quota behavior: {error:?}"
    );
    assert_eq!(
        fixture.flow.group.get("group-1").await.unwrap().status,
        bcs_domain::GroupStatus::Inactive,
        "the admitted limit hit marks the Group Inactive as before"
    );

    // Re-activate to observe a NON-member at the SAME boundary:
    // session authorization rejects FIRST; it must not re-mark
    // the Group nor surface the limit error.
    fixture
        .flow
        .group
        .update_status("group-1", bcs_domain::GroupStatus::Active)
        .await
        .expect("reactivate");
    fixture
        .session_repo
        .remove_participant("group-1:ffffffff", "bot-observer")
        .await
        .expect("remove observer");
    let error = fixture
        .flow
        .handle_persistent_group_send(persistent_cmd("bot-observer", 1))
        .await
        .expect_err("membership still wins over the quota boundary");
    assert!(
        matches!(error, ServiceError::Forbidden(_)),
        "unexpected error: {error:?}"
    );
    assert_eq!(
        fixture.flow.group.get("group-1").await.unwrap().status,
        bcs_domain::GroupStatus::Active,
        "the rejected request must not have touched the Group status"
    );
    assert_eq!(support_counts(&fixture.flow, "group-1").await.0, 1, "the rejected request consumed nothing");
}

#[tokio::test]
async fn accepted_persistent_send_counts_exactly_once() {
    let fixture = unmanaged_fixture(vec![(
        "group-1:ffffffff".into(),
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            Participant::bot("bot-observer", ParticipantRole::Consultant),
        ],
    )])
    .await;
    let before = support_counts(&fixture.flow, "group-1").await.0;

    fixture
        .flow
        .handle_persistent_group_send(persistent_cmd("bot-observer", 10))
        .await
        .expect("member persistent send");
    assert_eq!(
        support_counts(&fixture.flow, "group-1").await.0,
        before + 1,
        "no double-increment after moving the check"
    );
}

#[tokio::test]
async fn resolution_failure_has_no_quota_side_effects() {
    // Unmanaged + eventing ON + NO Session at all: the resolution
    // fails closed BEFORE quota/writes.
    let fixture = unmanaged_fixture(vec![]).await;
    let before = support_counts(&fixture.flow, "group-1").await.0;
    let error = fixture
        .flow
        .handle_persistent_group_send(persistent_cmd("bot-observer", 2))
        .await
        .expect_err("no Running Session to anchor");
    assert!(matches!(error, ServiceError::InvalidOperation { .. }));
    assert_eq!(support_counts(&fixture.flow, "group-1").await.0, before);
    assert_eq!(support_counts(&fixture.flow, "group-1").await.1, bcs_domain::GroupStatus::Active);

    // Resolution ok but the membership READ fails: same shape.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let event_store = Arc::new(bcs_event_store::MemoryEventStore::new());
    let factory = Arc::new(RecordingEventFactory::default());
    let repo = Arc::new(
        bcs_message_store::MemoryMessageRepo::new()
            .with_environment("local".into())
            .with_event_store(event_store),
    );
    let session = test_session(
        "group-1:ffffffff",
        "group-1",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    );
    let authority = bcs_test_support::lazy_authority_chain();
    authority.hook.seed_owner("bot-observer", "1");
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_event_record_factory(factory)
    .with_bot_authority_hook(Some(authority.hook))
    .with_session_management(Arc::new(
        StaticSessionManagement::new(session).with_get_failure(),
    ));
    let before = flow.group.message_count("group-1").await.unwrap();
    let error = flow
        .handle_persistent_group_send(persistent_cmd("bot-observer", 2))
        .await
        .expect_err("a failing membership read is an error");
    assert!(matches!(error, ServiceError::InternalError(_)));
    assert_eq!(flow.group.message_count("group-1").await.unwrap(), before);
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn managed_branch_rejects_the_removed_sender_before_any_quota_effect() {
    use bcs_service_api::ManagedMessageDeliveryService;
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(
        bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()),
    );
    let session = test_session(
        "group-1:ffffffff",
        "group-1",
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            Participant::bot("bot-observer", ParticipantRole::Consultant),
        ],
    );
    let sessions = Arc::new(StaticSessionManagement::new(session));
    let authority = bcs_test_support::lazy_authority_chain();
    authority.hook.seed_owner("bot-driver", "1");
    authority.hook.seed_owner("bot-observer", "1");
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 10)]))
    .with_bot_authority_hook(Some(authority.hook.clone()))
    .with_session_management(sessions);

    // Sanity: the member send is admitted and counted EXACTLY
    // once on the managed branch (the managed path's pre-existing
    // counter reset inside `handle_web_send` applies; what must
    // hold is that no additional increment sneaks in).
    flow.handle_persistent_group_send(persistent_cmd("bot-observer", 10))
        .await
        .expect("a current member persists through the managed branch");
    assert_eq!(
        flow.group.message_count("group-1").await.unwrap(),
        1,
        "exactly one increment after the managed reset"
    );
    assert_eq!(
        flow.group.get("group-1").await.unwrap().status,
        bcs_domain::GroupStatus::Active
    );

    // The removed-member scenario below: the SAME request now
    // rejects BEFORE quota — even at a quota boundary (max=1
    // against the existing count) it must be the Session error,
    // not the quota error, and the Group must not flip Inactive.
    let repo2 = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service2 = Arc::new(
        bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo2.clone()),
    );
    let session2 = test_session(
        "group-1:ffffffff",
        "group-1",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    );
    let flow2 = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo2)
    .with_managed_deliveries(service2.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 10)]))
    .with_bot_authority_hook(Some(authority.hook.clone()))
    .with_session_management(Arc::new(StaticSessionManagement::new(session2)));
    let error = flow2
        .handle_persistent_group_send(persistent_cmd("bot-observer", 1))
        .await
        .expect_err("membership rejects before quota, even at the boundary");
    assert!(matches!(error, ServiceError::Forbidden(_)), "unexpected: {error:?}");
    assert_eq!(
        flow2.group.get("group-1").await.unwrap().status,
        bcs_domain::GroupStatus::Active
    );
    assert!(service2.snapshot(None).await.unwrap().is_empty());
}

#[tokio::test]
async fn managed_branch_keeps_the_unique_running_session_rule() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(bcs_session_store::MemorySessionRepo::new());
    for id in ["group-1:00000001", "group-1:00000002"] {
        session_repo
            .create(
                "group-1",
                bcs_service_api::port::repo::NewSessionParams {
                    id: Some(id.into()),
                    session_kind: SessionKind::Chat,
                    participants: vec![
                        Participant::bot("bot-driver", ParticipantRole::Driver),
                        Participant::bot("bot-observer", ParticipantRole::Consultant),
                    ],
                    ..Default::default()
                },
            )
            .await
            .expect("seed running session");
    }
    let group_repo: Arc<dyn bcs_service_api::port::repo::GroupRepoPort> =
        Arc::new(bcs_group::MemoryGroupRepo::new());
    let sessions = Arc::new(
        bcs_session::SessionManagementServiceImpl::new(session_repo, group_repo),
    );
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(
        bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()),
    );
    let authority = bcs_test_support::lazy_authority_chain();
    authority.hook.seed_owner("bot-observer", "1");
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 10)]))
    .with_bot_authority_hook(Some(authority.hook.clone()))
    .with_session_management(sessions);

    let before = flow.group.message_count("group-1").await.unwrap();
    let error = flow
        .handle_persistent_group_send(persistent_cmd("bot-observer", 2))
        .await
        .expect_err("two running Sessions keep the managed ambiguity rejection");
    assert!(matches!(error, ServiceError::InvalidOperation { .. }), "unexpected: {error:?}");
    assert_eq!(flow.group.message_count("group-1").await.unwrap(), before);
    assert!(service.snapshot(None).await.unwrap().is_empty());
}
