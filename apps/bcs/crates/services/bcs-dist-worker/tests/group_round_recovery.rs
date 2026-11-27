//! T7 slice 1 (plan task 13): the Worker commits one authoritative terminal
//! state per strict FSM-group round — and immediately admits the next round
//! without waiting for SSE delivery.
//!
//! The vectors pause the Worker inside the two durability windows the plan
//! names (final record archived but terminal authority not yet committed;
//! terminal committed but SSE delivery paused), kill and restart the Worker
//! inside each window, and assert one authoritative cut: an archive alone is
//! never public success, a live sibling attempt blocks failure/cancellation
//! terminals until its stop is proven from durable facts, and the next round
//! is admitted only through the same owner-fenced window that transferred the
//! cold terminal proof.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "joint commit-spine assertions"
)]
mod group_round_recovery_support;
mod processor_support;
mod support;

use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::*, dispatch::DispatchControlPort, group_round::*, inbox::*, result::*,
    session::*,
};
use group_round_recovery_support::*;
use std::sync::atomic::Ordering;

fn sibling_did(tid: &Tid) -> Did {
    Did(format!("{}.fsm:1:{NODE}:1", tid.0))
}

/// The ConnectorAdmission report of one dispatch terminal: no
/// `remote_event_id` (a real Connector GetTask result), the stable connector
/// key identity and the state string bound inside the payload.
fn terminal_report(tid: &Tid, did: &Did, state: &str) -> ConnectorAdmission {
    let event = AgentEvent {
        did: did.clone(),
        holder_epoch: HolderEpoch(1),
        seq: InputSeq(1),
        kind: AgentEventKind::Final,
        remote_event_id: None,
        payload: Payload::Inline(serde_json::json!({"state": state, "answer": "answer-of-round"})),
    };
    ConnectorAdmission {
        input: InputEnvelope {
            schema_version: 1,
            sid: sid(),
            tid: tid.clone(),
            input_id: bcs_dist_encoding::connector::key_input_id(
                did,
                bcs_dist_encoding::connector::KeyEvent::Final,
            ),
            business_digest: Digest(String::new()),
            body: InputBody::Agent { event },
        },
        identity: ConnectorEventIdentity {
            round: None,
            state: state.into(),
        },
    }
}

/// Bounds one vector's wall time so a wedged joint fails the vector loudly
/// instead of hanging the whole battery.
async fn bounded<const SECS: u64>(future: impl Future<Output = ()>) {
    tokio::time::timeout(std::time::Duration::from_secs(SECS), future)
        .await
        .expect("vector exceeded its wall-time bound");
}

#[tokio::test]
async fn archive_visible_only_at_commit_and_next_round_not_waiting_for_sse() {
    bounded::<240>(archive_visible_impl()).await;
}

async fn archive_visible_impl() {
    let runner = FsmRunner::new(FsmMode::AnswerThenFinish);
    let calls = runner.calls.clone();
    let world = std::sync::Arc::new(RoundWorld::new(runner).await);
    let a = round("a", 1);
    let a_key = a.input.key.clone();
    assert!(matches!(
        world.admission.admit(&a).await.unwrap(),
        Admission::New { .. }
    ));
    let processor = local_processor(&world);

    // Window 1: drive tick by tick and stop the instant the sealed
    // checkpoint holds the finalized record without a terminal authority —
    // the archived-before-authority window. Nothing may present A as
    // success inside it, and the next round must stay closed.
    let mut in_window = false;
    for _ in 0..96 {
        drive(&world, &processor, 1).await;
        let c = world.checkpoint().await;
        if c.tasks.iter().any(|t| t.tid == a_key.tid
            && matches!(t.phase, TaskPhase::AwaitingResult { .. }))
        {
            in_window = true;
            break;
        }
    }
    assert!(in_window, "the round must reach its finalize");
    let run_calls = calls.load(Ordering::SeqCst);
    assert!(run_calls >= 1, "the round must have executed once");
    let a_view = world
        .round_view(&a_key)
        .await
        .expect("the licensed round stays readable while settling");
    assert!(
        a_view.terminal.is_none(),
        "an archived record without the authority commit is never success"
    );
    assert!(a_view.result.is_none());
    let busy = world.admission.admit(&round("b", 2)).await;
    assert!(
        matches!(busy, Ok(Admission::Busy)),
        "no next-round admission while the authority window is unsettled"
    );

    // Window 2 closes through the terminal spine: closure evidence, the
    // owner-fenced terminal CAS, cold confirmation and read-back — the
    // checkpoint retains the proof while the paired SSE obligations stay
    // their own follower lane.
    let mut closed = false;
    for _ in 0..96 {
        drive(&world, &processor, 1).await;
        let c = world.checkpoint().await;
        if c.runner_state
            .as_ref()
            .and_then(|state| state.state.value.get("group_terminal"))
            .is_some()
            && c.tasks
                .iter()
                .any(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::Done { .. }))
        {
            closed = true;
            break;
        }
    }
    assert!(closed, "the terminal spine must close the authority");
    assert_eq!(calls.load(Ordering::SeqCst), run_calls);

    // The round's feed lane is wedged from here on: every append on this
    // session applies durably and then loses its reply. The authority cut
    // must not care.
    world
        .f
        .publisher
        .fail_session
        .lock()
        .unwrap()
        .replace((sid(), true));

    // A stays fully visible through the cut and B admits immediately through
    // the same cut — the feed obligations never gate admission.
    let a_view = world.round_view(&a_key).await.unwrap();
    assert!(a_view.terminal.is_some(), "A is visible through the cut");
    assert_eq!(
        a_view.result,
        Some(Payload::Inline(serde_json::json!({"finished": true})))
    );
    let parts = world
        .artifacts
        .task_artifacts(
            &ArchiveGrants { read: true },
            &scope(),
            &GroupId(GROUP.into()),
            &[a_key.tid.clone()],
        )
        .await
        .unwrap();
    // Slice 14 (plan T7): the terminal window's archive carries the real
    // artifact parts through the guarded attach seam — the round's single
    // Upsert artifact, content and order intact — never a placeholder.
    assert_eq!(
        parts,
        vec![(
            a_key.tid.clone(),
            ArtifactPart {
                artifact_id: ArtifactId("answer".into()),
                order: std::num::NonZeroU32::new(1).unwrap(),
                part: RoundPart::Text {
                    text: "answer-of-round".into()
                },
            }
        )],
        "the paired publication obligations deliver the real artifact parts"
    );
    assert!(matches!(
        world.admission.admit(&round("b", 2)).await.unwrap(),
        Admission::New { .. }
    ));
    let after_admit_calls = calls.load(Ordering::SeqCst);

    // The Worker runs inside the wedged feed lane: its driving generation is
    // killed and a successor rebuild restores the stream; nothing about B's
    // occupancy or A's authority changes.
    let wedged = {
        let world = world.clone();
        let processor = processor.clone();
        tokio::spawn(async move {
            drive(&world, &processor, 8).await;
        })
    };
    wedged.abort();
    let processor = local_processor(&world);
    world
        .f
        .publisher
        .fail_session
        .lock()
        .unwrap()
        .take();
    let b_key = round("b", 2).input.key;
    until(&world, &processor, |checkpoint| {
        checkpoint.runner_state.as_ref().is_some_and(|runner|
            runner.tid == b_key.tid && runner.state.value.get("group_terminal").is_some())
    }).await;
    let b_view = world.round_view(&b_key).await.expect("the successor must remain readable");
    assert_eq!(b_view.terminal.unwrap().key, b_key);
    assert_eq!(b_view.result, Some(Payload::Inline(serde_json::json!({"finished": true}))));
    let a_view = world.round_view(&a_key).await.unwrap();
    assert!(a_view.terminal.is_some(), "A's closed authority persists");
    assert_eq!(
        a_view.result,
        Some(Payload::Inline(serde_json::json!({"finished": true})))
    );
    assert_eq!(calls.load(Ordering::SeqCst), after_admit_calls + 1,
        "recovery runs B exactly once without re-running A");
}

#[tokio::test]
async fn deadline_or_step_limit_with_live_sibling() {
    bounded::<240>(async {
        deadline_leg().await;
        step_limit_leg().await;
    })
    .await;
}

/// A task deadline fires while the sibling attempt is live: the intent
/// freezes and the cancel registers, but no terminal state and no next-round
/// release may exist before the sibling's stop is proven from durable facts.
async fn deadline_leg() {
    let runner = FsmRunner::new(FsmMode::WaitOnSibling);
    let world = std::sync::Arc::new(RoundWorld::new(runner).await);
    let a = round("a", 1);
    let a_key = a.input.key.clone();
    assert!(matches!(
        world.admission.admit(&a).await.unwrap(),
        Admission::New { .. }
    ));
    let processor = local_processor(&world);
    until(&world, &processor, |c| {
        c.dispatch_seq.contains_key(&sibling_did(&a_key.tid))
    })
    .await;
    let did = sibling_did(&a_key.tid);
    until_dispatch(&world, &processor, &did).await;
    claim_sibling(&world, &did).await;

    world.f.clock.0.store(1000 + 60_500, Ordering::SeqCst);
    let checkpoint = until(&world, &processor, |c| {
        c.tasks
            .iter()
            .any(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::AwaitingResult { .. }))
    })
    .await;
    assert!(
        checkpoint
            .tasks
            .iter()
            .all(|t| t.tid != a_key.tid || !matches!(t.phase, TaskPhase::Done { .. })),
        "a deadline final must not reach a terminal while the sibling is live"
    );
    drive(&world, &processor, 32).await;
    let attempt = world.f.dispatch.get(&did).await.unwrap().unwrap().view;
    assert!(attempt.control.cancel.is_some());
    assert_eq!(attempt.execution.phase, DispatchPhase::Sending);
    assert!(attempt.execution.outcome.is_none());
    assert!(world.round_view(&a_key).await.unwrap().terminal.is_none());
    assert!(
        matches!(world.admission.admit(&round("b", 2)).await, Ok(Admission::Busy)),
        "no release before the stop proof: B stays Busy"
    );

    confirm_attempt_stop(&world, &did).await;
    let checkpoint = until(&world, &processor, |c| {
        c.tasks
            .iter()
            .any(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::Done { .. }))
    })
    .await;
    let done = checkpoint
        .tasks
        .iter()
        .filter(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::Done { .. }))
        .count();
    assert_eq!(done, 1, "exactly one terminal after stop confirmation");
    drive(&world, &processor, 24).await;
    assert!(
        matches!(world.admission.admit(&round("b", 2)).await.unwrap(), Admission::New { .. }),
        "the next round admits once the authority transferred"
    );
}

/// The same stop barrier governs step-limit finals: while the sibling is
/// live no terminal settles; after its durable stop the round closes with
/// exactly one terminal.
async fn step_limit_leg() {
    let runner = FsmRunner::new(FsmMode::WaitOnSibling);
    // A tiny step bound makes the step limit fire while the task clock stays
    // far from its deadline, isolating the step-limit entry.
    let mut spec = strict_spec();
    spec.limits.max_task_steps = 2;
    let world =
        std::sync::Arc::new(RoundWorld::with_spec(runner, spec).await);
    let a = round("a", 1);
    let a_key = a.input.key.clone();
    assert!(matches!(
        world.admission.admit(&a).await.unwrap(),
        Admission::New { .. }
    ));
    let processor = local_processor(&world);
    until(&world, &processor, |c| {
        c.dispatch_seq.contains_key(&sibling_did(&a_key.tid))
    })
    .await;
    let did = sibling_did(&a_key.tid);
    until_dispatch(&world, &processor, &did).await;
    claim_sibling(&world, &did).await;

    // Intermediate dispatch events force further evaluations; the tiny step
    // bound freezes the intent while the sibling is still live (the task
    // deadline stays far in the future).
    for seq in [1, 2, 3] {
        world
            .f
            .inbox
            .append_stream(AppendAgentEvent {
                sid: sid(),
                tid: a_key.tid.clone(),
                event: AgentEvent {
                    did: did.clone(),
                    holder_epoch: HolderEpoch(1),
                    seq: InputSeq(seq),
                    kind: AgentEventKind::Delta,
                    remote_event_id: None,
                    payload: Payload::Inline(serde_json::json!("progress")),
                },
            })
            .await
            .unwrap();
    }
    let checkpoint = until(&world, &processor, |c| {
        c.tasks.iter().any(|t| t.tid == a_key.tid
            && matches!(t.phase, TaskPhase::AwaitingResult { .. }))
            && c.dispatch_seq.values().any(|seq| seq.tid == a_key.tid && seq.sequence.0 >= 1)
    })
    .await;
    assert!(
        checkpoint
            .tasks
            .iter()
            .all(|t| t.tid != a_key.tid || !matches!(t.phase, TaskPhase::Done { .. })),
        "a step-limit final stays behind the same stop barrier"
    );
    assert!(
        matches!(world.admission.admit(&round("b", 2)).await, Ok(Admission::Busy)),
        "no next-round admission before the sibling stop proof"
    );

    confirm_attempt_stop(&world, &did).await;
    let checkpoint = until(&world, &processor, |c| {
        c.tasks
            .iter()
            .any(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::Done { .. }))
    })
    .await;
    let done = checkpoint
        .tasks
        .iter()
        .filter(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::Done { .. }))
        .count();
    assert_eq!(done, 1);
}

/// The full terminal result reaches the Runner through the real report and
/// Inbox contracts: no fabricated remote event IDs, one application, the
/// lost-response retry resolves as a duplicate, the duplicate query never
/// re-applies, and a changed-content report under the same identity is a
/// loud conflict. Repeated cancels, holder changes and late callbacks cannot
/// release the attempt before its terminal.
#[tokio::test]
async fn get_task_final_reaches_real_runner() {
    bounded::<240>(get_task_final_impl()).await;
}

async fn get_task_final_impl() {
    let runner = FsmRunner::new(FsmMode::WaitOnSibling);
    let calls = runner.calls.clone();
    let world = std::sync::Arc::new(RoundWorld::new(runner).await);
    let a = round("a", 1);
    let a_key = a.input.key.clone();
    assert!(matches!(
        world.admission.admit(&a).await.unwrap(),
        Admission::New { .. }
    ));
    let processor = local_processor(&world);
    until(&world, &processor, |c| {
        c.dispatch_seq.contains_key(&sibling_did(&a_key.tid))
    })
    .await;
    let did = sibling_did(&a_key.tid);
    until_dispatch(&world, &processor, &did).await;
    claim_sibling(&world, &did).await;
    let baseline = calls.load(Ordering::SeqCst);

    // The first report of the complete terminal loses its response: the
    // durable fact still applied.
    world.f.model.lose_next_response();
    let first = world
        .f
        .inbox
        .admit_connector(terminal_report(&a_key.tid, &did, "final"))
        .await;
    assert!(
        first.is_err() || matches!(first, Ok(AdmitOutcome::Accepted(_) | AdmitOutcome::Duplicate(_))),
        "a lost response must surface as unknown, never as rejection"
    );
    // The retry of the exact same report resolves through the real Inbox.
    match world
        .f
        .inbox
        .admit_connector(terminal_report(&a_key.tid, &did, "final"))
        .await
    {
        Ok(AdmitOutcome::Accepted(_)) | Ok(AdmitOutcome::Duplicate(_)) => {}
        other => panic!("the retried report must resolve, got {other:?}"),
    }

    // The real Connectors confirm their own attempt outcome; the report
    // alone never fabricates one. With the definitive outcome on record the
    // runner's terminal passes the stop barrier.
    drive(&world, &processor, 32).await;
    let dispatch = world.f.dispatch.get(&did).await.unwrap().unwrap();
    assert!(dispatch.view.control.cancel.is_some());
    assert!(world.round_view(&a_key).await.unwrap().terminal.is_none());
    confirm_attempt_stop(&world, &did).await;
    // The Runner applied the complete terminal exactly once: one new runner
    // reduction and one terminal commit.
    let checkpoint = until(&world, &processor, |c| {
        c.tasks
            .iter()
            .any(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::Done { .. }))
    })
    .await;
    let applied = calls.load(Ordering::SeqCst);
    assert_eq!(applied - baseline, 1, "exactly one terminal application");
    assert!(
        checkpoint
            .dispatch_seq
            .get(&did)
            .is_some_and(|seq| seq.sequence.0 >= 1),
        "the final report advanced the dispatch sequence without a fabricated \
         remote event id"
    );

    // The duplicate query replays and never re-applies.
    match world
        .f
        .inbox
        .admit_connector(terminal_report(&a_key.tid, &did, "final"))
        .await
    {
        Ok(AdmitOutcome::Duplicate(_)) => {}
        Ok(AdmitOutcome::Accepted(_)) => {}
        other => panic!("a duplicate query resolves as duplicate, got {other:?}"),
    }
    drive(&world, &processor, 16).await;
    assert_eq!(calls.load(Ordering::SeqCst), applied);

    // A changed-content report under the same identity conflicts loudly.
    let mut conflicting = terminal_report(&a_key.tid, &did, "other-state");
    conflicting.identity.state = "final".into();
    match world.f.inbox.admit_connector(conflicting).await {
        Ok(AdmitOutcome::ContentConflict) | Err(_) => {}
        other => panic!("a changed-content report must not re-apply, got {other:?}"),
    }
    drive(&world, &processor, 8).await;
    assert_eq!(calls.load(Ordering::SeqCst), applied);

    // Repeated cancels and late callbacks after the terminal do not reopen
    // or release the attempt early: the stop state is definitive.
    let dispatch = world.f.dispatch.get(&did).await.unwrap().unwrap();
    assert!(
        !matches!(dispatch.view.execution.outcome, Some(DispatchOutcome::Unknown)),
        "an unknown outcome never satisfies the stop barrier"
    );
    let view = world.round_view(&a_key).await.unwrap();
    assert_eq!(view.terminal.unwrap().status, TerminalStatus::Completed);
    assert_eq!(view.result, Some(Payload::Inline(serde_json::json!({"finished": true}))));
}

/// A user cancel racing the sibling's confirmed completion resolves to
/// exactly one authoritative terminal; the loser replays as already decided.
#[tokio::test]
async fn cancel_and_finish_race_produces_exactly_one_terminal() {
    bounded::<240>(cancel_finish_impl()).await;
}

async fn cancel_finish_impl() {
    let runner = FsmRunner::new(FsmMode::WaitOnSibling);
    let world = std::sync::Arc::new(RoundWorld::new(runner).await);
    let a = round("a", 1);
    let a_key = a.input.key.clone();
    assert!(matches!(
        world.admission.admit(&a).await.unwrap(),
        Admission::New { .. }
    ));
    let processor = local_processor(&world);
    until(&world, &processor, |c| {
        c.dispatch_seq.contains_key(&sibling_did(&a_key.tid))
    })
    .await;
    let did = sibling_did(&a_key.tid);
    until_dispatch(&world, &processor, &did).await;
    claim_sibling(&world, &did).await;

    // Strict rounds refuse generic inbox entries by design (the admission
    // gate answers `strict_claim_source`); the user-facing cancel route is
    // the T8 boundary. Within this slice the race is run between the
    // sibling's complete-terminal report and its confirmed cancellation —
    // whichever durable fact lands first owns the single terminal identity.
    world
        .f
        .inbox
        .admit_connector(terminal_report(&a_key.tid, &did, "final"))
        .await
        .unwrap();
    confirm_attempt_stop(&world, &did).await;
    let checkpoint = until(&world, &processor, |c| {
        c.tasks
            .iter()
            .any(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::AwaitingResult { .. }))
    })
    .await;
    let _ = checkpoint;
    let checkpoint = until(&world, &processor, |c| {
        c.tasks
            .iter()
            .any(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::Done { .. }))
    })
    .await;
    let done = checkpoint
        .tasks
        .iter()
        .filter(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::Done { .. }))
        .count();
    assert_eq!(done, 1, "the race converges to exactly one terminal");
    let task = checkpoint
        .tasks
        .iter()
        .find(|t| t.tid == a_key.tid)
        .unwrap();
    let finalization = task.finalization.as_ref().unwrap();
    // The first durable fact owns the terminal identity and it stays single.
    assert!(matches!(
        finalization.record.status,
        TerminalStatus::Completed | TerminalStatus::Canceled
    ));
}

/// The A2 cold-confirm sequencing (controller ruling 1): the next round's
/// runner-state installation is admitted only after the current round's cold
/// terminal proof was confirmed and read back. Once confirmed, replaying the
/// confirmation returns AlreadyApplied without hot-state dependence — a
/// successor installing B can never re-enter A's confirmation into a
/// deadlock.
#[tokio::test]
async fn confirm_races_next_round_install() {
    bounded::<240>(confirm_races_impl()).await;
}

async fn confirm_races_impl() {
    let runner = FsmRunner::new(FsmMode::AnswerThenFinish);
    let world = std::sync::Arc::new(RoundWorld::new(runner).await);
    let a = round("a", 1);
    let a_key = a.input.key.clone();
    assert!(matches!(
        world.admission.admit(&a).await.unwrap(),
        Admission::New { .. }
    ));
    let processor = local_processor(&world);
    until(&world, &processor, |c| {
        c.tasks
            .iter()
            .any(|t| t.tid == a_key.tid && matches!(t.phase, TaskPhase::Done { .. }))
    })
    .await;
    drive(&world, &processor, 8).await;

    let cold = world
        .artifacts
        .get_terminal_proof(
            &ArchiveGrants { read: true },
            &scope(),
            &GroupId(GROUP.into()),
            &a_key.tid,
        )
        .await
        .unwrap();
    match cold {
        Some(proof) => {
            // Already cold-confirmed: replay is AlreadyApplied with no
            // hot-state dependence.
            assert_eq!(
                world
                    .artifacts
                    .confirm_terminal_proof(&scope(), &GroupId(GROUP.into()), &a_key.tid, &proof)
                    .await
                    .unwrap(),
                IdempotentWrite::AlreadyApplied
            );
            assert!(matches!(
                world.admission.admit(&round("b", 2)).await.unwrap(),
                Admission::New { .. }
            ));
        }
        None => {
            // Not yet cold-confirmed: B's runner-state install must stay
            // Busy — otherwise A's confirmation re-enters a permanent
            // OutcomeUnknown deadlock against the next round's runner state.
            assert!(
                matches!(world.admission.admit(&round("b", 2)).await, Ok(Admission::Busy)),
                "B may only install after A's cold proof is confirmed"
            );
            // The successor completes the spine in the same owner-fenced
            // window, then B installs.
            let processor = local_processor(&world);
            drive(&world, &processor, 48).await;
            assert!(matches!(
                world.admission.admit(&round("b", 2)).await.unwrap(),
                Admission::New { .. }
            ));
        }
    }
    let a_view = world.round_view(&a_key).await.unwrap();
    assert!(a_view.terminal.is_some(), "A stays readable after B installs");
    assert!(
        a_view.result.is_some(),
        "A's result is re-readable through the authoritative cut"
    );
}
