use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_domain::{
    DeliveryType, MessageVisibilityDomain, NewMessage, SenderType,
    message_delivery::DeliveryFlowKind,
};

use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, DeliveryAdmissionTarget,
};
use bcs_service_api::{
    BotEventCommand, BotRunContext, BotRunContextPort, ChatEventState, DeliveryTransitionCommand,
    GroupCoreService, ManagedMessageDeliveryService, MessageFlowService,
};
use serde_json::json;

#[path = "session.rs"]
mod session_support;
#[path = "../../../../test-support/message_flow_contract_support.rs"]
pub(super) mod support;
use session_support::{StaticSessionManagement, test_session};

const SESSION: &str = "group-1:derivation";

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

/// One managed caller-root row for `bot-driver`, driven to a live Started
/// dispatch: the row every reply derivation keys its parent run to.
async fn started_parent_row(
    service: &Arc<ManagedMessageDelivery>,
) -> bcs_domain::message_delivery::PersistedMessageDelivery {
    use bcs_service_api::ManagedMessageDeliveryService as _;
    let now = now_ms();
    let admitted = service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: format!("seed-{}", now),
            flow_kind: DeliveryFlowKind::Group,
            now_ms: now,
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                visibility_domain: MessageVisibilityDomain::Chat,
                audience: None,
                group_id: "group-1".into(),
                session_id: SESSION.into(),
                sender_id: "human_1".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: json!({"text": "root task text"}),
                client_msg_id: None,
                owner_bot_id: None,
                created_at: now as u64,
                run_id: String::new(),
            },
            targets: vec![DeliveryAdmissionTarget {
                run_id: None,
                rejection: None,
                target_bot_id: "bot-driver".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: json!({
                    "version": 1,
                    "caller_required": true,
                    "caller_task": "root task text",
                    "caller_channel": {"actor_id": "human_alice", "actor_name": "Alice"},
                }),
            }],
        })
        .await
        .expect("parent row admits");
    let row = &admitted.deliveries[0];
    service
        .transition(DeliveryTransitionCommand {
            caller_dispatch_commit: None,
            caller_cancel: None,
            caller_terminal: None,
            delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version,
            event: Event::StartSend,
            now_ms: now,
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: Some(json!({
                "version": 1,
                "owner": {"kind": "web_socket"},
                "connection_id": null,
                "downstream_session_key": SESSION,
            })),
            deadline_at_ms: Some(now + 600_000),
        })
        .await
        .expect("parent row starts dispatch")
}


use super::{RecordingInject, StubFactory, seed_container};
use async_trait::async_trait;
use bcs_domain::RedactedToken;
use bcs_service_api::port::ObserveInjectPort;
use bcs_message_flow::caller_dispatch_pipeline::ObserveSyncAssembly;
use bcs_message_flow::observe_index::ObserveTargetIndex;
use std::sync::Mutex;

#[tokio::test]
async fn committed_reply_without_downstream_delivery_still_syncs_once() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    // A SECOND current Human, so the same-Bot fan-out has two live containers.
    let mut group = support.group.get("group-1").await.unwrap();
    if !group.participants.iter().any(|p| p.bot_uuid == "human_2") {
        let mut second = bcs_service_api::Participant::human("human_2", bcs_service_api::ParticipantRole::Observer);
        second.mode = Some(bcs_service_api::ParticipantMode::Present);
        group.participants.push(second);
        support.group.upsert(group.clone()).await.unwrap();
    }
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let index = Arc::new(ObserveTargetIndex::new(8));
    // Roster-real subjects: the review-VI membership prune pauses every
    // departed subject before the reply fan-out, so the container owners
    // are actual CURRENT participants (human_1, human_2) while human_gone
    // certifies the left-member gap the prune closes.
    seed_container(&index, "1", "bot-driver", SESSION, "alice");
    seed_container(&index, "2", "bot-driver", SESSION, "bob");
    seed_container(&index, "2", "bot-observer", SESSION, "other-bot");
    seed_container(&index, "2", "bot-driver", "other-session", "other-session");
    seed_container(&index, "gone", "bot-driver", SESSION, "gone");
    let inject = Arc::new(RecordingInject { calls: Mutex::new(Vec::new()), fail: false });
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing, support.registry,
        support.bot_delivery, support.frontend_delivery,
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_bot_run_context(run_context.clone())
    .with_caller_observe(Arc::new(ObserveSyncAssembly {
        groups: support.group.clone(), sessions: None, delivery_term: None,
        index: index.clone(), injector_factory: Arc::new(StubFactory { port: inject.clone(), no_transport: false }),
    }))
    .with_group_delivery_limits(BTreeMap::new())
    .with_session_management(Arc::new(StaticSessionManagement::new(test_session(
        SESSION, "group-1", group.participants,
    ))));
    let started = started_parent_row(&service).await;
    let parent_run = started.run_id.clone().unwrap();
    run_context.put_context(BotRunContext {
        run_id: parent_run.clone(), bot_id: "bot-driver".into(), group_id: "group-1".into(),
        bcs_session_id: Some(SESSION.into()), deadline_ms: (now_ms() + 600_000) as u64,
        terminal: false,
    }).await;
    let event = BotEventCommand {
        bot_id: "bot-driver".into(), run_id: parent_run, group_id: "group-1".into(),
        event_type: "chat".into(), event_payload: json!({
            "state": "final", "message": {"role": "assistant", "content": [{"type": "text", "text": "completed reply"}]},
        }), bcs_session_id: Some(SESSION.into()), state: ChatEventState::Final,
    };
    flow.handle_bot_event(event.clone()).await.unwrap();
    let rows = service.snapshot(Some(SESSION)).await.unwrap();
    assert_eq!(rows.len(), 1, "no downstream managed delivery; only original run");
    assert_eq!(rows[0].state.status, bcs_domain::message_delivery::MessageDeliveryStatus::Completed);
    // Review-V P2-4: the reply sync is detached + bounded-retried — poll
    // for the fan-out instead of assuming synchronous completion.
    let mut synced = false;
    for _ in 0..200 {
        let calls = inject.calls.lock().unwrap();
        if calls.len() == 2 {
            assert!(calls.iter().all(|c| c.text == "completed reply" && c.target_bot_id == "bot-driver" && c.session_id == SESSION));
            assert_eq!(calls[0].message_id, calls[1].message_id);
            synced = true;
            break;
        }
        drop(calls);
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    assert!(synced, "committed reply reaches both same-Bot containers");
    // The departed member never received the NEW reply: the prune paused
    // their container before the fan-out (tombstone observable).
    assert!(index_current(&index, "gone", "bot-driver", SESSION).is_none(),
        "the departed member's container is paused by the roster prune");
    assert!(index_invalidated(&index, "gone", "bot-driver", SESSION),
        "the departure is tombstoned — not a silent miss");
    flow.handle_bot_event(event).await.unwrap();
    let mut quiescent = true;
    for _ in 0..40 {
        if inject.calls.lock().unwrap().len() > 2 { quiescent = false; break; }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    assert!(quiescent, "duplicate terminal callback never re-fans out");
}


/// One-injector factory: the reply sync resolves the replying Bot's target
/// and asks for its injector; tests share one recorded port.
struct SinglePortFactory {
    port: Arc<FlakyInject>,
}
impl bcs_service_api::port::ObserveInjectorFactory for SinglePortFactory {
    fn injector_for(
        &self,
        _target: &bcs_domain::BotDeliveryTarget,
    ) -> Option<Arc<dyn ObserveInjectPort>> {
        Some(self.port.clone())
    }
}

/// First-call-fails transport: exercises the reply sync's bounded redelivery
/// (the same dedup id makes every retry idempotent).
struct FlakyInject {
    calls: Mutex<Vec<bcs_service_api::port::ObserveInjectCommand>>,
    failed_once: std::sync::atomic::AtomicBool,
}
#[async_trait]
impl ObserveInjectPort for FlakyInject {
    async fn inject_shared_context(
        &self,
        _credential: &RedactedToken,
        command: bcs_service_api::port::ObserveInjectCommand,
    ) -> Result<(), bcs_service_api::port::ObserveInjectError> {
        if !self.failed_once.swap(true, std::sync::atomic::Ordering::SeqCst) {
            return Err(bcs_service_api::port::ObserveInjectError::Unavailable { retryable: true });
        }
        self.calls.lock().unwrap().push(command);
        Ok(())
    }
}

#[tokio::test]
async fn a_transient_reply_sync_failure_is_redelivered_with_the_same_dedup_id() {
    // Review-V P2-4: a transient observe-inject failure no longer drops the
    // reply for one container forever — the same shared reply is re-sent a
    // bounded number of times under the SAME canonical message id, so the
    // container receives exactly once after recovery and duplicate arrivals
    // dedup on the gateway side.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    if !group.participants.iter().any(|p| p.bot_uuid == "human_2") {
        let mut second = bcs_service_api::Participant::human("human_2", bcs_service_api::ParticipantRole::Observer);
        second.mode = Some(bcs_service_api::ParticipantMode::Present);
        group.participants.push(second);
        support.group.upsert(group.clone()).await.unwrap();
    }
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let index = Arc::new(ObserveTargetIndex::new(8));
    // Roster-real subjects (the membership prune pauses departed owners
    // before the fan-out): two CURRENT participants stay live for the
    // per-target isolation/re-delivery exercised below.
    seed_container(&index, "1", "bot-driver", SESSION, "alice");
    seed_container(&index, "2", "bot-driver", SESSION, "bob");
    let inject = Arc::new(FlakyInject { calls: Mutex::new(Vec::new()), failed_once: std::sync::atomic::AtomicBool::new(false) });
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing, support.registry,
        support.bot_delivery, support.frontend_delivery,
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_bot_run_context(run_context.clone())
    .with_caller_observe(Arc::new(ObserveSyncAssembly {
        groups: support.group.clone(), sessions: None, delivery_term: None,
        index,
        injector_factory: Arc::new(SinglePortFactory { port: inject.clone() }),
    }))
    .with_group_delivery_limits(BTreeMap::new())
    .with_session_management(Arc::new(StaticSessionManagement::new(test_session(
        SESSION, "group-1", group.participants,
    ))));
    let started = started_parent_row(&service).await;
    let parent_run = started.run_id.clone().unwrap();
    run_context.put_context(BotRunContext {
        run_id: parent_run.clone(), bot_id: "bot-driver".into(), group_id: "group-1".into(),
        bcs_session_id: Some(SESSION.into()), deadline_ms: (now_ms() + 600_000) as u64,
        terminal: false,
    }).await;
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-driver".into(), run_id: parent_run, group_id: "group-1".into(),
        event_type: "chat".into(), event_payload: json!({
            "state": "final", "message": {"role": "assistant", "content": [{"type": "text", "text": "retry me"}]},
        }), bcs_session_id: Some(SESSION.into()), state: ChatEventState::Final,
    }).await.unwrap();

    // FlakyInject fails exactly the FIRST call: attempt one injects one of
    // the two containers (per-target isolation), the bounded retry
    // re-runs the whole fan-out under the same dedup id and injects BOTH
    // — so the recorded successes total exactly 3.
    let mut recovered = false;
    for _ in 0..400 {
        let calls = inject.calls.lock().unwrap();
        if calls.len() == 3 { recovered = true; break; }
        drop(calls);
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    assert!(recovered, "the transiently failed inject is re-sent and both containers receive the reply");
    let calls = inject.calls.lock().unwrap();
    assert!(calls.iter().all(|c| c.text == "retry me"));
    assert_eq!(calls[0].message_id, calls[1].message_id, "the redelivery carries the SAME dedup id");
}


// Small local aliases over the index BEFORE the tests borrow it away.
fn index_current(index: &ObserveTargetIndex, subject: &str, bot: &str, session: &str) -> Option<bcs_service_api::port::CallerObserveTarget> {
    index.current(subject, bot, session, now_ms())
}
fn index_invalidated(index: &ObserveTargetIndex, subject: &str, bot: &str, session: &str) -> bool {
    index.is_invalidated(subject, bot, session)
}
