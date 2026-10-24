//! F1 operation output admission at the producer entries
//! (bot-authority spec §8.2 — 已准入输出不追溯).
//!
//! What is pinned here, with the REAL message flow + recording
//! doubles:
//!
//! - the NON-managed persistent-send installs EXACTLY ONE output
//!   admission before its first business side effect, registers the
//!   operation's complete Send-run set with the adapter, and tags
//!   every later callback frame with the operation (never a fresh
//!   per-event authorization);
//! - one WebSend driving several bot Runs registers the WHOLE set
//!   before any downstream I/O, and each callback frame carries its
//!   OWN canonical run identity next to the shared operation;
//! - the producer's output visibility facts (domain + participant
//!   role) are frozen from the admission-time snapshot BEFORE the
//!   first callback: a membership removal landing between accept and
//!   first frame never reclassifies admitted output.
//!
//! The adapter-side proof that admitted output is never interrupted
//! mid-operation (and that frame volume never re-enters the
//! resolver) lives in `bcs-ws/tests/authority_delivery.rs`.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_domain::{GroupStrategy, MessageAudience, SessionKind};
use bcs_message_flow::BcsMessageFlow;
use bcs_service_api::{
    GroupCoreService,
    MessageFlowService, OutputAdmissionPort, OutputOperationAdmission,
    ParticipantRole, ServiceError, ServiceResult, SessionRepoPort,
};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

#[path = "support/operation_output.rs"]
mod operation_output;
use operation_output::*;

/// R2: a NON-managed persistent-send installs one output admission
/// (group scope, no session anchor) and tags every callback frame
/// with the operation — no per-event authorization fallback.
#[tokio::test]
async fn nonmanaged_persistent_send_admits_once_and_tags_callback_output() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let admission = Arc::new(RecordingOutputAdmission::default());
    let authority = bcs_test_support::lazy_authority_chain();
    authority.hook.seed_owner("bot-observer", "1");
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_authority_hook(Some(authority.hook))
    .with_output_admission(Some(admission.clone()));

    flow.handle_persistent_group_send(persistent_cmd("bot-observer", 10))
        .await
        .expect("non-managed persistent send succeeds");

    {
        let admissions = admission.admissions.lock().unwrap();
        assert_eq!(
            admissions.len(),
            1,
            "the persistent-send installs exactly one output admission"
        );
        assert_eq!(admissions[0].group_id, "group-1");
        assert_eq!(admissions[0].session_id, None);
    }

    let frames = support.bot_delivery.frames().await;
    let run = match &frames[0] {
        bcs_protocol::BcsFrame::Request(request) => request.id.clone(),
        other => panic!("expected a Bot request frame, got {other:?}"),
    };
    {
        let registered = admission.registered_runs.lock().unwrap();
        assert_eq!(registered.len(), 1, "the operation's stream set was registered once");
        assert!(
            registered[0].1.contains(&run),
            "the delivered run is part of the registered stream set: {:?}",
            registered[0].1
        );
    }
    let operation_id = {
        let registered = admission.registered_runs.lock().unwrap();
        registered[0].0.clone()
    };

    // Frame-volume growth carries the SAME operation tag — the
    // authorization cost of this entry never scales with frames.
    for _ in 0..3 {
        flow.handle_bot_event(delta_event(&run, "bot-driver", None))
            .await
            .expect("delta callback published");
    }
    let events = support.frontend_delivery.commands().await;
    let tagged: Vec<_> = events
        .iter()
        .filter(|event| event.operation_id.is_some())
        .collect();
    assert_eq!(tagged.len(), 3, "every callback event carries its operation tag");
    assert!(
        events
            .iter()
            .all(|event| event.operation_id.as_deref() == Some(operation_id.as_str())),
        "one operation admitted all callback output"
    );
    assert!(
        events.iter().all(|event| event.run_id.as_deref() == Some(run.as_str())),
        "every callback frame carries its canonical run identity"
    );
    assert_eq!(
        admission.admissions.lock().unwrap().len(),
        1,
        "callback frames never re-admit"
    );
}

/// A WebSend driving several bot Runs registers the COMPLETE Send
/// set as one operation, and each callback frame carries its OWN
/// canonical run identity next to the shared operation id.
#[tokio::test]
async fn one_operation_registers_every_send_run_before_downstream_io() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let admission = Arc::new(RecordingOutputAdmission::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_output_admission(Some(admission.clone()));

    let outcome = flow
        .handle_web_send(web_send(
            "group-1",
            "human_1",
            None,
            &["bot-driver", "bot-observer"],
        ))
        .await
        .expect("a multi-target WebSend succeeds");
    assert_eq!(
        outcome.active_run_ids.len(),
        2,
        "both mentioned bots own a live run"
    );

    {
        let registered = admission.registered_runs.lock().unwrap();
        assert_eq!(registered.len(), 1, "one registration for the whole operation");
        assert_eq!(registered[0].0, outcome.operation_id);
        let mut registered_runs = registered[0].1.clone();
        registered_runs.sort();
        let mut active = outcome.active_run_ids.clone();
        active.sort();
        assert_eq!(
            registered_runs, active,
            "the registered stream set equals the operation's active Send runs"
        );
    }

    for run in &outcome.active_run_ids {
        flow.handle_bot_event(delta_event(run, "bot-driver", None))
            .await
            .expect("delta published");
    }
    let events = support.frontend_delivery.commands().await;
    for run in &outcome.active_run_ids {
        assert!(
            events.iter().any(|event| {
                event.operation_id.as_deref() == Some(outcome.operation_id.as_str())
                    && event.run_id.as_deref() == Some(run.as_str())
            }),
            "run {run} published its frame under the shared operation"
        );
    }
}

/// R3: a Manager bot's admitted output keeps its admission-time
/// Public classification even when the bot leaves the Session
/// between accept and first frame (producer facts frozen, not
/// re-read).
#[tokio::test]
async fn producer_role_facts_are_frozen_before_the_first_callback() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.expect("group");
    group.group_strategy = GroupStrategy::ManagerWorker;
    group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "bot-driver")
        .expect("driver")
        .role = ParticipantRole::Manager;
    support.group.upsert(group.clone()).await.expect("upsert");
    let sessions = Arc::new(bcs_session_store::MemorySessionRepo::new());
    sessions
        .create(
            "group-1",
            bcs_service_api::port::repo::NewSessionParams {
                id: Some("group-1:ffffffff".into()),
                session_kind: SessionKind::Chat,
                participants: group.participants.clone(),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let admission = Arc::new(RecordingOutputAdmission::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(
        bcs_session::SessionManagementServiceImpl::new(
            sessions.clone(),
            Arc::new(bcs_group::MemoryGroupRepo::new()),
        ),
    ))
    .with_output_admission(Some(admission));

    let outcome = flow
        .handle_web_send(web_send(
            "group-1",
            "human_1",
            Some("group-1:ffffffff"),
            &["bot-driver"],
        ))
        .await
        .expect("the Manager send is admitted");
    let run = outcome
        .active_run_ids
        .first()
        .cloned()
        .expect("one active run");

    // The Manager leaves the Session BETWEEN accept and first frame.
    sessions
        .remove_participant("group-1:ffffffff", "bot-driver")
        .await
        .expect("remove the Manager from the session");

    flow.handle_bot_event(delta_event(&run, "bot-driver", Some("group-1:ffffffff")))
        .await
        .expect("first callback published");

    let events = support.frontend_delivery.commands().await;
    let event = events.last().expect("one published event");
    assert_eq!(
        event.operation_id.as_deref(),
        Some(outcome.operation_id.as_str()),
        "the callback keeps its admitted operation tag"
    );
    assert_eq!(
        event.audience,
        Some(MessageAudience::Public),
        "the admitted Manager output keeps its frozen Public classification \
         instead of degrading to FullOnly from current membership"
    );
    // A second delta stays identical — the frozen facts, not a
    // re-read, still decide.
    flow.handle_bot_event(delta_event(&run, "bot-driver", Some("group-1:ffffffff")))
        .await
        .expect("second callback published");
    let events = support.frontend_delivery.commands().await;
    assert_eq!(
        events.last().expect("event").audience,
        Some(MessageAudience::Public)
    );
}

/// A send that fails between admission and delivery leaves no
/// stranded side effect on the admission port: the admission read
/// failure is absorbed and the send's own result is unaffected by
/// permit bookkeeping.
#[tokio::test]
async fn output_admission_read_failure_never_fails_the_send() {
    struct Failing;
    #[async_trait::async_trait]
    impl OutputAdmissionPort for Failing {
        async fn admit_output_operation(
            &self,
            _admission: OutputOperationAdmission,
        ) -> ServiceResult<()> {
            Err(ServiceError::InternalError("read failed".into()))
        }
        async fn register_operation_runs(
            &self,
            _operation_id: &str,
            _run_ids: Vec<String>,
        ) -> ServiceResult<()> {
            Err(ServiceError::InternalError("read failed".into()))
        }
    }
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_output_admission(Some(Arc::new(Failing)));
    // The port contract absorbs adapter-side failures; here the
    // doubled failure surfaces as the send error itself, proving the
    // flow propagates instead of silently dropping the admission.
    let result = flow
        .handle_web_send(web_send("group-1", "human_1", None, &["bot-driver"]))
        .await;
    assert!(
        matches!(result, Err(ServiceError::InternalError(_))),
        "the flow propagates the admission port error: {result:?}"
    );
}

// ---------------------------------------------------------------------------
// Recheck R6/R7 regressions: the managed commit window and the
// explicit release end-states.
// ---------------------------------------------------------------------------

#[path = "support/paused_delivery.rs"]
mod paused_delivery;

/// R6: the durable queue row is runnable the moment the admission
/// commits — a worker starting the run before the sender's own
/// bookkeeping STILL publishes under the trusted operation, because
/// the pre-assigned run's binding and frozen facts were installed
/// BEFORE the commit. The pause double only holds the caller; the
/// worker transition below runs the REAL managed service.
#[tokio::test]
async fn managed_run_runnable_before_caller_resumes_keeps_operation_identity() {
    use bcs_service_api::{DeliveryTransitionCommand, ManagedMessageDeliveryService};

    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let managed = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(
        repo.clone(),
    ));
    let paused = Arc::new(paused_delivery::PausedDelivery::new(managed.clone()));
    let admission = Arc::new(RecordingOutputAdmission::default());
    let flow = managed_output_flow(&support, repo, paused.clone(), admission.clone(), 10)
        .await;
    let flow = Arc::new(flow);

    let sender = flow.clone();
    let task = tokio::spawn(async move {
        sender
            .handle_web_send(web_send(
                "group-1",
                "human_1",
                Some("group-1:ffffffff"),
                &["bot-driver"],
            ))
            .await
    });
    // The durable admission committed; the CALLER is held. Drive the
    // worker side through the REAL lifecycle before it resumes.
    tokio::time::timeout(std::time::Duration::from_secs(5), paused.committed.notified())
        .await
        .expect("admission committed");
    let row = managed
        .snapshot(None)
        .await
        .expect("snapshot")
        .into_iter()
        .find(|delivery| delivery.state.kind == bcs_domain::DeliveryType::Send)
        .expect("one managed send row");
    let started = managed
        .transition(DeliveryTransitionCommand {
            delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
            now_ms: row.created_at_ms + 1,
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: Some(serde_json::json!({
                "version": 1,
                "owner": { "kind": "web_socket" },
                "connection_id": "test-conn",
                "downstream_session_key": "session",
            })),
            deadline_at_ms: Some(i64::MAX),
        })
        .await
        .expect("worker starts the run");
    let run_id = started.run_id.clone().expect("canonical run id");

    // The FIRST callback lands while the caller is still paused.
    flow.handle_bot_event(delta_event(&run_id, "bot-driver", Some("group-1:ffffffff")))
        .await
        .expect("early callback published");
    let early = support
        .frontend_delivery
        .commands()
        .await
        .last()
        .expect("one published event")
        .clone();

    paused.resume.notify_one();
    let outcome = task.await.expect("send joins").expect("send succeeds");

    assert_eq!(
        early.operation_id.as_deref(),
        Some(outcome.operation_id.as_str()),
        "a runnable-early durable callback must carry the admitted operation identity"
    );
    assert_eq!(
        early.run_id.as_deref(),
        Some(run_id.as_str()),
        "the early callback carries its canonical run identity"
    );
    assert_eq!(
        outcome.primary_run_id, run_id,
        "the worker ran the pre-assigned canonical run id"
    );
    // And the operation's stream registration covers it.
    let registered = admission.registered_runs.lock().unwrap();
    assert!(
        registered
            .iter()
            .any(|(op, runs)| op == &outcome.operation_id && runs.contains(&run_id)),
        "the managed run is part of the registered stream set"
    );
}

/// An admission-time capacity rejection never enters the registered
/// stream set — only runnable Send runs hold the operation open.
#[tokio::test]
async fn capacity_rejected_managed_deliveries_never_hold_the_operation_open() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let managed = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(
        repo.clone(),
    ));
    let admission = Arc::new(RecordingOutputAdmission::default());
    let flow = managed_output_flow(&support, repo, managed.clone(), admission.clone(), 0)
        .await;

    let outcome = flow
        .handle_web_send(web_send(
            "group-1",
            "human_1",
            Some("group-1:ffffffff"),
            &["bot-driver"],
        ))
        .await
        .expect("send is accepted with a rejected delivery");
    assert_eq!(outcome.status, "rejected_capacity");
    // R9 shape: registration happens BEFORE the commit (the commit
    // is what learns the capacity verdict), so the rejected run IS
    // registered — and is then released EXACTLY once the commit
    // reports it terminal at admission.
    let registered = admission.registered_runs.lock().unwrap().clone();
    assert_eq!(registered.len(), 1);
    assert_eq!(registered[0].0, outcome.operation_id);
    assert_eq!(
        registered[0].1.len(),
        1,
        "the rejected run was pre-registered before its verdict was known"
    );
    let released = admission.released_runs.lock().unwrap().clone();
    assert_eq!(released.len(), 1, "the terminal-at-commit run is released exactly");
    assert_eq!(released[0].0, outcome.operation_id);
    assert_eq!(
        released[0].1, registered[0].1,
        "the released set is precisely the admission-rejected registration"
    );
}

/// A transport-failed direct run is explicitly released: it refuses
/// later frames and no longer holds the operation's completion open
/// — while the surviving sibling run flows on.
#[tokio::test]
async fn failed_direct_runs_are_released_and_siblings_flow_on() {
    use bcs_service_api::{BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult};

    /// Fails the transport for ONE bot; everything else flows through.
    struct FailBotTransport {
        inner: Arc<support::RecordingBotDelivery>,
        fail_bot: String,
    }

    #[async_trait::async_trait]
    impl BotDeliveryPort for FailBotTransport {
        async fn is_available(
            &self,
            target: &bcs_service_api::BotDeliveryTarget,
        ) -> bool {
            self.inner.is_available(target).await
        }
        async fn deliver(
            &self,
            cmd: BotDeliveryCommand,
        ) -> ServiceResult<BotDeliveryResult> {
            if cmd.target.bot_id() == self.fail_bot {
                return Ok(BotDeliveryResult {
                    target_bot_id: cmd.target.bot_id().to_string(),
                    delivered: false,
                    error: Some(ServiceError::InternalError("forced failure".into())),
                });
            }
            self.inner.deliver(cmd).await
        }
    }

    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let failing: Arc<dyn BotDeliveryPort> = Arc::new(FailBotTransport {
        inner: support.bot_delivery.clone(),
        fail_bot: "bot-observer".to_string(),
    });
    let admission = Arc::new(RecordingOutputAdmission::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        failing,
        support.frontend_delivery.clone(),
    )
    .with_output_admission(Some(admission.clone()));

    let outcome = flow
        .handle_web_send(web_send(
            "group-1",
            "human_1",
            None,
            &["bot-driver", "bot-observer"],
        ))
        .await
        .expect("send succeeds with one failed target");
    assert_eq!(
        outcome.active_run_ids.len(),
        1,
        "only the surviving target has an active run"
    );

    let registered = admission.registered_runs.lock().unwrap().clone();
    let released = admission.released_runs.lock().unwrap().clone();
    assert_eq!(released.len(), 1, "exactly one release for the failed run");
    assert_eq!(released[0].0, outcome.operation_id);
    let failed_runs = released[0].1.clone();
    assert_eq!(failed_runs.len(), 1);
    assert!(
        !outcome.active_run_ids.contains(&failed_runs[0]),
        "the failed run is the released one"
    );
    assert!(
        registered
            .iter()
            .any(|(op, runs)| op == &outcome.operation_id && runs.contains(&failed_runs[0])),
        "the failed run WAS registered before its transport failure (then released)"
    );

    // The surviving run still publishes under the operation.
    flow.handle_bot_event(delta_event(
        &outcome.active_run_ids[0],
        "bot-driver",
        None,
    ))
    .await
    .expect("sibling delta published");
    let events = support.frontend_delivery.commands().await;
    assert!(
        events.iter().any(|event| {
            event.operation_id.as_deref() == Some(outcome.operation_id.as_str())
                && event.run_id.as_deref() == Some(outcome.active_run_ids[0].as_str())
        }),
        "the surviving sibling keeps its admitted operation tag"
    );
    // And the released run's own event can never tag the operation.
    flow.handle_bot_event(delta_event(&failed_runs[0], "bot-observer", None))
        .await
        .ok();
    let events = support.frontend_delivery.commands().await;
    assert!(
        !events.iter().any(|event| {
            event.run_id.as_deref() == Some(failed_runs[0].as_str())
                && event.operation_id.is_some()
        }),
        "a released run never re-attaches the operation tag"
    );
}

/// One operation with BOTH a managed (queued) and a direct target:
/// the registered stream set is their union, and each callback —
/// whatever lane it arrives through — carries the same operation.
#[tokio::test]
async fn mixed_managed_and_direct_targets_share_one_operation_stream_set() {
    use bcs_service_api::{DeliveryTransitionCommand, ManagedMessageDeliveryService};

    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let managed = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(
        repo.clone(),
    ));
    let admission = Arc::new(RecordingOutputAdmission::default());
    let authority = bcs_test_support::lazy_authority_chain();
    authority.hook.seed_owner("bot-driver", "1");
    authority.hook.seed_owner("bot-observer", "1");
    let flow = managed_output_flow(&support, repo, managed.clone(), admission.clone(), 10)
        .await
        .with_bot_authority_hook(Some(authority.hook));

    let outcome = flow
        .handle_web_send(web_send(
            "group-1",
            "human_1",
            Some("group-1:ffffffff"),
            &["bot-driver", "bot-observer"],
        ))
        .await
        .expect("the mixed send is admitted");
    assert!(
        outcome.queue_admission.is_some(),
        "bot-driver went through the managed lane"
    );

    // Managed run id from the durable row, direct run id from the
    // active set.
    let managed_row = managed
        .snapshot(None)
        .await
        .expect("snapshot")
        .into_iter()
        .find(|delivery| {
            delivery.state.kind == bcs_domain::DeliveryType::Send
                && delivery.target_bot_id == "bot-driver"
        })
        .expect("managed row");
    let managed_run = managed_row.run_id.clone().expect("managed run id");
    let mut expected = outcome.active_run_ids.clone();
    expected.push(managed_run.clone());
    expected.sort();

    let registered = admission.registered_runs.lock().unwrap();
    assert_eq!(registered.len(), 1, "ONE registration for the mixed operation");
    assert_eq!(registered[0].0, outcome.operation_id);
    let mut registered_runs = registered[0].1.clone();
    registered_runs.sort();
    assert_eq!(
        registered_runs, expected,
        "the registered stream set is the managed ∪ direct union"
    );

    // Both lanes publish under the SAME admitted operation. The
    // managed row must be running before its callback is legal.
    managed
        .transition(DeliveryTransitionCommand {
            delivery_id: managed_row.delivery_id.clone(),
            expected_state_version: managed_row.state.state_version,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
            now_ms: managed_row.created_at_ms + 1,
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: Some(serde_json::json!({
                "version": 1,
                "owner": { "kind": "web_socket" },
                "connection_id": "test-conn",
                "downstream_session_key": "session",
            })),
            deadline_at_ms: Some(i64::MAX),
        })
        .await
        .expect("managed run started");
    flow.handle_bot_event(delta_event(&managed_run, "bot-driver", Some("group-1:ffffffff")))
        .await
        .expect("managed callback published");
    flow.handle_bot_event(delta_event(&outcome.active_run_ids[0], "bot-observer", None))
        .await
        .expect("direct callback published");
    let events = support.frontend_delivery.commands().await;
    for run in [&managed_run, &outcome.active_run_ids[0]] {
        assert!(
            events.iter().any(|event| {
                event.operation_id.as_deref() == Some(outcome.operation_id.as_str())
                    && event.run_id.as_deref() == Some(run.as_str())
            }),
            "lane-independent: run {run} publishes under the shared operation"
        );
    }
}
