//! Fault-injection and replay coverage for a StateMachine Run that fails while
//! a player Bot owns the current turn (issue #2610).
//!
//! The happy-path recovery stories live in `failure_recovery.rs` and the
//! terminal-notification restart windows in `terminal_im_recovery.rs`. This
//! module only adds the adversarial paths that a "notify the manager, then
//! retry" implementation is expected to get right:
//!
//! | test | #2610 acceptance criterion |
//! | --- | --- |
//! | `heartbeat_loss_fails_run_and_stops_the_turn` | a player Bot that dies without any message still moves the Run to `failed` |
//! | `restart_between_failed_cas_and_notification_delivers_once` | the failed result is durable and idempotent across a process restart |
//! | `replayed_failure_event_is_consumed_without_a_second_effect` | duplicated / reordered failure events are processed once |
//! | `late_artifact_after_failure_cannot_advance_or_write_chat` | a late artifact for a terminal Run is inert |
//! | `user_aborted_run_is_never_retried` | `aborted` is terminal: no automatic retry of the manager |
//!
//! The harness is the shared `recovery.rs` fixture: `FaultyRuns` injects write
//! failures at exact checkpoints, `install_runtime` rebuilds the runtime to
//! simulate a process restart, and no production code is modified.

use super::*;
use std::sync::atomic::Ordering;
use bcs_service_api::{
    CancelStateMachineRunCommand, StateMachineTerminalStatus,
};

/// Delivers an error terminal event for the Bot delivery at `index`, the shape
/// a player Bot produces when its process is killed mid-turn (the channel
/// reports the loss, no assistant message ever arrives).
async fn lost_heartbeat(
    h: &Harness,
    index: usize,
    run: &StateMachineRun,
) -> Result<bcs_service_api::HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
    let delivery_id = h.delivery.commands.lock().await[index].run_id.clone();
    h.runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".into(),
            run_id: delivery_id.clone(),
            event_type: "chat.event".into(),
            event_payload: json!({
                "run_id": delivery_id,
                "state": "error",
                "error": "player process exited without a final message",
                // The channel carries the reason as the only text block; no
                // assistant artifact ever arrives for the lost turn.
                "message": {"content": [{
                    "type": "text",
                    "text": "player process exited without a final message",
                }]},
            }),
            state: ChatEventState::Error,
            bcs_session_id: Some(run.session_id.clone()),
        })
        .await
}

async fn recover_terminal_im(h: &Harness) {
    let page = h
        .runtime
        .recover_state_machine_terminal_im(None, 32)
        .await
        .unwrap();
    assert!(page.failures.is_empty(), "{:?}", page.failures);
}

#[tokio::test]
async fn heartbeat_loss_fails_run_and_stops_the_turn() {
    let (h, _runs) = harness(&[]).await;
    // One attempt: an exhausted (unrecoverable) failure must fail the Run
    // instead of scheduling a retry.
    let started = h.start(loop_yaml(1, false, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;

    let outcome = lost_heartbeat(&h, 0, run).await.unwrap();

    let view = outcome.view.unwrap();
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(
        view.run.error.as_deref(),
        Some("player process exited without a final message")
    );
    assert!(view.run.completed_at.is_some());

    // The dead turn is closed, not left running for the next scanner sweep.
    let node = h
        .store
        .get_node_run(&run.run_id, &iteration_id(&plan, 1))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(node.status, StateMachineNodeStatus::Failed);
    assert_eq!(node.error.as_deref(), Some("player process exited without a final message"));

    // Exactly one Bot turn was dispatched for the whole Run, and the manager is
    // informed through the terminal channel with a `failed` status.
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    let terminal = h.channel.terminal_events.lock().await;
    assert_eq!(
        terminal
            .iter()
            .filter(|event| event.run_id == run.run_id
                && event.status == StateMachineTerminalStatus::Failed)
            .count(),
        1,
        "exactly one failed notification is published: {terminal:?}"
    );
    drop(terminal);

    // A failed Run must not be revived by the progression scanner.
    recover(&h).await;
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Failed
    );
}

#[tokio::test]
async fn restart_between_failed_cas_and_notification_delivers_once() {
    let (mut h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(1, false, false, 1), false).await;
    let run = &started.view.run;

    // The status CAS to `failed` succeeds, then the process dies before the
    // terminal notification checkpoint is saved. `fail_im_save` sits exactly
    // between those two writes.
    runs.fail_im_save.store(true, Ordering::SeqCst);
    assert!(lost_heartbeat(&h, 0, run).await.is_err());
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Failed,
        "the failed terminal state is committed before the crash"
    );
    assert_eq!(
        h.sessions.get(&run.session_id).await.unwrap().unwrap().status,
        SessionStatus::Running,
        "the session has not been reconciled yet"
    );
    assert!(h.store.get_terminal_im(&run.run_id).await.unwrap().is_none());
    assert!(h.channel.terminal_events.lock().await.is_empty());

    // Simulated restart: a brand new runtime over the same durable stores.
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    install_runtime(&mut h, runs, &[]).await;
    let sessions = h
        .runtime
        .recover_state_machine_sessions(None, 32)
        .await
        .unwrap();
    assert!(sessions.failures.is_empty(), "{:?}", sessions.failures);
    recover_terminal_im(&h).await;
    let delivered = h.channel.terminal_events.lock().await.len();
    assert!(delivered > 0, "the failed result is published after restart");

    // The dedupe key is the run id: replaying the whole recovery cycle after a
    // second restart must not publish the same result again.
    let restarted = Arc::new(FaultyRuns::new(h.store.clone()));
    install_runtime(&mut h, restarted, &[]).await;
    let sessions = h
        .runtime
        .recover_state_machine_sessions(None, 32)
        .await
        .unwrap();
    assert!(sessions.failures.is_empty(), "{:?}", sessions.failures);
    recover_terminal_im(&h).await;
    assert_eq!(
        h.channel.terminal_events.lock().await.len(),
        delivered,
        "the same run failure is never notified twice"
    );
    let saved = h.store.get_terminal_im(&run.run_id).await.unwrap().unwrap();
    assert_eq!(saved.payload.event.status, StateMachineTerminalStatus::Failed);
    assert_eq!(
        h.sessions.get(&run.session_id).await.unwrap().unwrap().status,
        SessionStatus::Completed
    );
    assert_eq!(h.delivery.commands.lock().await.len(), 1);

    // A replayed Bot event for the already terminal turn is a no-op too.
    let outcome = lost_heartbeat(&h, 0, run).await.unwrap();
    assert!(outcome.consumed);
    assert_eq!(h.channel.terminal_events.lock().await.len(), delivered);
}

#[tokio::test]
async fn replayed_failure_event_is_consumed_without_a_second_effect() {
    let (h, _runs) = harness(&[]).await;
    let started = h.start(loop_yaml(1, false, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;

    lost_heartbeat(&h, 0, run).await.unwrap();
    let node_after_first = h
        .store
        .get_node_run(&run.run_id, &iteration_id(&plan, 1))
        .await
        .unwrap()
        .unwrap();
    let notified = h.channel.terminal_events.lock().await.len();

    // Reorder / duplicate: the same failure event arrives a second time.
    let replay = lost_heartbeat(&h, 0, run).await.unwrap();
    assert!(replay.consumed, "a replay is acknowledged so the sender can drop it");
    assert_eq!(
        replay.view.as_ref().unwrap().run.status,
        StateMachineRunStatus::Failed
    );

    let node_after_replay = h
        .store
        .get_node_run(&run.run_id, &iteration_id(&plan, 1))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        serde_json::to_value(&node_after_first).unwrap(),
        serde_json::to_value(&node_after_replay).unwrap(),
        "a replayed event cannot rewrite the failed attempt"
    );
    assert_eq!(
        h.channel.terminal_events.lock().await.len(),
        notified,
        "a replayed event cannot notify the manager twice"
    );
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(
        h.sessions.get(&run.session_id).await.unwrap().unwrap().status,
        SessionStatus::Completed
    );
}

#[tokio::test]
async fn late_artifact_after_failure_cannot_advance_or_write_chat() {
    let (h, _runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    let failed_node = iteration_id(&plan, 1);
    let next_node = iteration_id(&plan, 2);

    lost_heartbeat(&h, 0, run).await.unwrap();
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Failed
    );
    let deliveries = h.delivery.commands.lock().await.len();
    let notified = h.channel.terminal_events.lock().await.len();
    let next_before = h
        .store
        .get_node_run(&run.run_id, &next_node)
        .await
        .unwrap()
        .unwrap();

    // A late artifact produced by the killed Bot finally arrives.
    let recorded = h
        .store
        .record_node_artifact_if_running(&run.run_id, &failed_node, 0, "late artifact".into())
        .await
        .unwrap();
    let completed = h
        .store
        .complete_node_attempt(
            &run.run_id,
            &failed_node,
            0,
            "late outcome".into(),
            "late artifact".into(),
            None,
            bcs_protocol::now_ms(),
        )
        .await
        .unwrap();
    assert!(!recorded, "a terminal Run rejects a late artifact");
    assert!(!completed, "a terminal Run rejects a late completion");

    // The late artifact neither advances the phase nor writes chat.
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Failed
    );
    let failed_after = h
        .store
        .get_node_run(&run.run_id, &failed_node)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(failed_after.status, StateMachineNodeStatus::Failed);
    assert_eq!(failed_after.artifact_text.as_deref(), None);
    let next_after = h
        .store
        .get_node_run(&run.run_id, &next_node)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        serde_json::to_value(&next_before).unwrap(),
        serde_json::to_value(&next_after).unwrap(),
        "the next phase is untouched"
    );
    assert_eq!(h.delivery.commands.lock().await.len(), deliveries);
    assert_eq!(h.channel.terminal_events.lock().await.len(), notified);
    recover(&h).await;
    assert_eq!(
        h.delivery.commands.lock().await.len(),
        deliveries,
        "recovery does not resume a failed Run because of a late artifact"
    );
}

#[tokio::test]
async fn user_aborted_run_is_never_retried() {
    let (h, _runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 3), false).await;
    let run = &started.view.run;
    let plan = h.plan(&run.run_id).await;
    let node_id = iteration_id(&plan, 1);

    h.runtime
        .cancel_state_machine_run(CancelStateMachineRunCommand {
            run_id: run.run_id.clone(),
            reason: Some("user aborted".into()),
        })
        .await
        .unwrap();
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Aborted
    );
    let deliveries = h.delivery.commands.lock().await.len();
    let notified = h.channel.terminal_events.lock().await.len();

    // A user abort is not a failure: the exhausted-turn event for the aborted
    // Run must not schedule the manager retry (`--retry`).
    let replay = lost_heartbeat(&h, 0, run).await.unwrap();
    assert!(replay.consumed);
    assert_eq!(
        replay.view.as_ref().unwrap().run.status,
        StateMachineRunStatus::Aborted
    );
    let node = h
        .store
        .get_node_run(&run.run_id, &node_id)
        .await
        .unwrap()
        .unwrap();
    assert_ne!(
        node.status,
        StateMachineNodeStatus::RetryScheduled,
        "an aborted Run must not schedule a retry"
    );
    assert!(h
        .store
        .get_node_attempt_failure(&run.run_id, &node_id, 0)
        .await
        .unwrap()
        .is_none());

    // Neither the bot-hosted retry nor any recovery sweep re-dispatches a turn.
    recover(&h).await;
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), deliveries);
    assert_eq!(h.channel.terminal_events.lock().await.len(), notified);
    assert_eq!(
        h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
        StateMachineRunStatus::Aborted
    );
}
