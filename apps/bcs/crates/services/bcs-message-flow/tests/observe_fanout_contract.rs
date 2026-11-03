//! Shared-context fan-out contract (spec §6.4, corrected model): the
//! dispatching caller container receives chat.send and is EXCLUDED from
//! observe inject; every OTHER caller container of the SAME Bot in the SAME
//! session receives the same message as an observe-only inject through the
//! real observe index (`同Bot其他Caller容器`). Another Bot's containers are
//! NEVER touched — that Bot's reception is its own delivery/shared
//! projection. Containers without a live reference simply are not observers
//! (their sync stays paused until their own next trusted ensure); transport
//! failures surface as errors without fabricating success; the fan-out
//! itself never ensures containers and never resolves configuration.

use bcs_domain::RedactedToken;
use bcs_message_flow::observe_index::ObserveTargetIndex;
use bcs_message_flow::observe_sync::fanout_shared_context;
use bcs_service_api::port::{
    CallerContainerDescriptor, CallerContainerIsolation, CallerObserveTarget, CallerOpaqueRef,
    ObserveInjectCommand, ObserveInjectError, ObserveInjectPort,
};

fn descriptor(bot: &str, session: &str, epoch: &str, expires: i64) -> CallerContainerDescriptor {
    CallerContainerDescriptor {
        sandbox_container_id: None,
        target_bot_id: bot.to_string(),
        caller_subject_ref: CallerOpaqueRef::new(format!("observer-{epoch}")),
        ready: true,
        isolation: CallerContainerIsolation::ContainerBound,
        binding_ref: None,
        runtime_generation: Some(1),
        expires_at_ms: expires,
        observe_target: Some(CallerObserveTarget {
            caller_subject: CallerOpaqueRef::new("fixture-subject"),
            caller_ref: Some(CallerOpaqueRef::new(format!("ref-{epoch}"))),
            target_bot_id: bot.to_string(),
            session_id: session.to_string(),
            target_epoch: Some(CallerOpaqueRef::new(format!("epoch-{epoch}"))),
            expires_at_ms: expires,
        }),
    }
}
use async_trait::async_trait;
use std::sync::Mutex as StdMutex;

struct RecordingPort {
    calls: StdMutex<Vec<ObserveInjectCommand>>,
    fail: bool,
    /// When set, only injects whose target epoch is listed here fail (per-target
    /// isolation contract) with the given error kind.
    fail_epochs: Vec<(String, bool)>,
}

#[async_trait]
impl ObserveInjectPort for RecordingPort {
    async fn inject_shared_context(
        &self,
        _credential: &RedactedToken,
        command: ObserveInjectCommand,
    ) -> Result<(), ObserveInjectError> {
        let epoch = command.runtime_target_epoch.as_ref().map(|v| v.expose()).unwrap_or_default().to_string();
        if let Some((_, retryable)) =
            self.fail_epochs.iter().find(|(want, _)| *want == epoch)
        {
            return Err(ObserveInjectError::Unavailable { retryable: *retryable });
        }
        if self.fail {
            return Err(ObserveInjectError::Unavailable { retryable: true });
        }
        self.calls.lock().unwrap().push(command);
        Ok(())
    }
}

const NOW: i64 = 1_780_000_000_000;

#[tokio::test]
async fn the_dispatching_container_is_excluded_and_same_bot_containers_injected() {
    let index = ObserveTargetIndex::new(8);
    let port = RecordingPort { calls: StdMutex::new(Vec::new()), fail: false, fail_epochs: Vec::new() };
    // One group session `s`, one Bot `bot-a`, three Human callers: alice
    // dispatches (chat.send into her own container), bob and carol observe.
    index
        .replace_from_descriptor("human-alice", "bot-a", "s", &descriptor("bot-a", "s", "a1", NOW + 60_000), NOW)
        .unwrap();
    index
        .replace_from_descriptor("human-bob", "bot-a", "s", &descriptor("bot-a", "s", "b1", NOW + 60_000), NOW)
        .unwrap();
    index
        .replace_from_descriptor("human-carol", "bot-a", "s", &descriptor("bot-a", "s", "c1", NOW + 60_000), NOW)
        .unwrap();

    let injected = fanout_shared_context(
        &index,
        &port,
        &RedactedToken::new("svc"),
        "human-alice",
        "bot-a",
        "s",
        "m1",
        "hello",
        NOW,
    )
    .await
    .unwrap();

    assert_eq!(injected, 2, "exactly bob's and carol's containers");
    let calls = port.calls.lock().unwrap();
    assert_eq!(calls.len(), 2, "the executing container never receives an inject");
    // Every inject targets the SAME Bot (containers of one Bot), routed
    // under each observer's OWN live generation — never alice's own ref.
    assert!(calls.iter().all(|call| call.target_bot_id == "bot-a"));
    let epochs: std::collections::BTreeSet<String> = calls
        .iter()
        .map(|call| call.runtime_target_epoch.as_ref().unwrap().expose().to_string())
        .collect();
    assert_eq!(
        epochs,
        std::collections::BTreeSet::from(["epoch-b1".to_string(), "epoch-c1".to_string()]),
        "each observer container routes under its own live reference"
    );
    // All injects carry the same shared message id (per-container dedup key).
    assert!(calls.iter().all(|call| call.message_id == "m1"));
    assert!(calls.iter().all(|call| call.text == "hello"));
}

#[tokio::test]
async fn another_bots_containers_are_never_touched_by_this_fanout() {
    let index = ObserveTargetIndex::new(8);
    let port = RecordingPort { calls: StdMutex::new(Vec::new()), fail: false, fail_epochs: Vec::new() };
    // bob has a container with bot-a (same session) AND with bot-b: only the
    // bot-a one may observe alice's bot-a dispatch — bot-b's reception is
    // bot-b's own delivery fact, never this fan-out's.
    index
        .replace_from_descriptor("human-bob", "bot-a", "s", &descriptor("bot-a", "s", "b1", NOW + 60_000), NOW)
        .unwrap();
    index
        .replace_from_descriptor("human-bob", "bot-b", "s", &descriptor("bot-b", "s", "b2", NOW + 60_000), NOW)
        .unwrap();

    let injected = fanout_shared_context(
        &index,
        &port,
        &RedactedToken::new("svc"),
        "human-alice",
        "bot-a",
        "s",
        "m1",
        "hello",
        NOW,
    )
    .await
    .unwrap();

    assert_eq!(injected, 1);
    {
        // Scope the guard: the second fan-out below re-locks the port, and a
        // held std Mutex on the same thread would self-deadlock.
        let calls = port.calls.lock().unwrap();
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].target_bot_id, "bot-a", "bot-b is never auto-copied");
    }
    // A different session of the same Bot is a different shared stream.
    index
        .replace_from_descriptor("human-bob", "bot-a", "s-2", &descriptor("bot-a", "s-2", "b3", NOW + 60_000), NOW)
        .unwrap();
    let injected = fanout_shared_context(
        &index,
        &port,
        &RedactedToken::new("svc"),
        "human-alice",
        "bot-a",
        "s",
        "m2",
        "again",
        NOW,
    )
    .await
    .unwrap();
    assert_eq!(injected, 1, "only the same-session container observes");
}

#[tokio::test]
async fn containers_without_a_live_reference_are_not_observers() {
    let index = ObserveTargetIndex::new(8);
    let port = RecordingPort { calls: StdMutex::new(Vec::new()), fail: false, fail_epochs: Vec::new() };
    // bob's container of bot-a has NO reference yet; carol's is live.
    index
        .replace_from_descriptor("human-carol", "bot-a", "s", &descriptor("bot-a", "s", "c1", NOW + 60_000), NOW)
        .unwrap();

    let injected = fanout_shared_context(
        &index,
        &port,
        &RedactedToken::new("svc"),
        "human-alice",
        "bot-a",
        "s",
        "m1",
        "hello",
        NOW,
    )
    .await
    .unwrap();
    assert_eq!(injected, 1, "only the live container observes");

    // A tombstoned container is not an observer (member leave pauses it).
    index.invalidate("human-carol", "bot-a", "s");
    let injected = fanout_shared_context(
        &index,
        &port,
        &RedactedToken::new("svc"),
        "human-alice",
        "bot-a",
        "s",
        "m2",
        "again",
        NOW,
    )
    .await
    .unwrap();
    assert_eq!(injected, 0, "tombstoned container paused");
    assert_eq!(port.calls.lock().unwrap().len(), 1);

    // An expired reference is not an observer either: never re-ensured.
    index
        .replace_from_descriptor("human-carol", "bot-a", "s", &descriptor("bot-a", "s", "c2", NOW + 1_000), NOW)
        .unwrap();
    let injected = fanout_shared_context(
        &index,
        &port,
        &RedactedToken::new("svc"),
        "human-alice",
        "bot-a",
        "s",
        "m3",
        "late",
        NOW + 2_000,
    )
    .await
    .unwrap();
    assert_eq!(injected, 0, "expired reference paused");
    assert_eq!(port.calls.lock().unwrap().len(), 1, "no fanout side effect");
}

#[tokio::test]
async fn empty_input_rejects_and_transport_failures_propagate_without_fabrication() {
    let index = ObserveTargetIndex::new(8);
    let port = RecordingPort { calls: StdMutex::new(Vec::new()), fail: false, fail_epochs: Vec::new() };
    // Empty message id / text rejects before any call.
    assert!(
        fanout_shared_context(
            &index, &port, &RedactedToken::new("svc"), "human-alice", "bot-a", "s", "", "x", NOW,
        )
        .await
        .is_err()
    );
    assert!(port.calls.lock().unwrap().is_empty());

    // A lone container for the Bot (the dispatching one) means zero calls.
    index
        .replace_from_descriptor("human-alice", "bot-a", "s", &descriptor("bot-a", "s", "a1", NOW + 60_000), NOW)
        .unwrap();
    assert_eq!(
        fanout_shared_context(
            &index, &port, &RedactedToken::new("svc"), "human-alice", "bot-a", "s", "m9", "x", NOW,
        )
        .await
        .unwrap(),
        0
    );
    assert!(port.calls.lock().unwrap().is_empty());

    // A transport failure surfaces as an error, never fabricated success.
    // Same retryable error for the same Bot's whole fan-out (one Provider
    // webhook behind them all) — the dedup id makes the retry idempotent.
    let failing = RecordingPort { calls: StdMutex::new(Vec::new()), fail: true, fail_epochs: Vec::new() };
    index
        .replace_from_descriptor("human-bob", "bot-a", "s", &descriptor("bot-a", "s", "b1", NOW + 60_000), NOW)
        .unwrap();
    let error = fanout_shared_context(
        &index,
        &failing,
        &RedactedToken::new("svc"),
        "human-alice",
        "bot-a",
        "s",
        "m9",
        "x",
        NOW,
    )
    .await
    .unwrap_err();
    assert_eq!(error, ObserveInjectError::Unavailable { retryable: true });
}

#[tokio::test]
async fn one_targets_failure_never_cancels_the_remaining_containers() {
    // Per-target isolation (review batch-1 item 1.4): bob's container fails,
    // carol's container must STILL receive its inject — the failure surfaces
    // once after every target was attempted, as a retryable error so the
    // caller's bounded retry runs.
    let index = ObserveTargetIndex::new(8);
    let port = RecordingPort {
        calls: StdMutex::new(Vec::new()),
        fail: false,
        fail_epochs: vec![("epoch-b1".to_string(), true)],
    };
    index
        .replace_from_descriptor("human-alice", "bot-a", "s", &descriptor("bot-a", "s", "a1", NOW + 60_000), NOW)
        .unwrap();
    index
        .replace_from_descriptor("human-bob", "bot-a", "s", &descriptor("bot-a", "s", "b1", NOW + 60_000), NOW)
        .unwrap();
    index
        .replace_from_descriptor("human-carol", "bot-a", "s", &descriptor("bot-a", "s", "c1", NOW + 60_000), NOW)
        .unwrap();

    let error = fanout_shared_context(
        &index,
        &port,
        &RedactedToken::new("svc"),
        "human-alice",
        "bot-a",
        "s",
        "m10",
        "hello",
        NOW,
    )
    .await
    .unwrap_err();
    assert_eq!(error, ObserveInjectError::Unavailable { retryable: true });
    // carol got her inject despite bob's failure — the aborting `.await?`
    // would have left carol unsynced.
    let calls = port.calls.lock().unwrap();
    assert_eq!(calls.len(), 1, "the healthy container still received the inject");
    assert_eq!(calls[0].runtime_target_epoch.as_ref().unwrap().expose(), "epoch-c1");
}

#[tokio::test]
async fn reply_fanout_isolates_failures_per_container_too() {
    // Same contract on the reply sync (§6.4): one container's transport
    // failure never cancels its sibling containers' injections.
    use bcs_message_flow::observe_sync::fanout_shared_reply;
    let index = ObserveTargetIndex::new(8);
    let port = RecordingPort {
        calls: StdMutex::new(Vec::new()),
        fail: false,
        fail_epochs: vec![("epoch-b1".to_string(), false)],
    };
    index
        .replace_from_descriptor("human-bob", "bot-a", "s", &descriptor("bot-a", "s", "b1", NOW + 60_000), NOW)
        .unwrap();
    index
        .replace_from_descriptor("human-carol", "bot-a", "s", &descriptor("bot-a", "s", "c1", NOW + 60_000), NOW)
        .unwrap();

    let error = fanout_shared_reply(
        &index,
        &port,
        &RedactedToken::new("svc"),
        "bot-a",
        "s",
        "m11",
        "reply",
        NOW,
    )
    .await
    .unwrap_err();
    // A lone structural refusal surfaces as itself (no retryable masking).
    assert_eq!(error, ObserveInjectError::Unavailable { retryable: false });
    let calls = port.calls.lock().unwrap();
    assert_eq!(calls.len(), 1, "carol still received the reply inject");
    assert_eq!(calls[0].runtime_target_epoch.as_ref().unwrap().expose(), "epoch-c1");
}
#[tokio::test]
async fn subject_only_targets_use_observer_identity_and_keep_bot_session_boundaries() {
    let index = ObserveTargetIndex::new(8);
    let port = RecordingPort { calls: StdMutex::new(Vec::new()), fail: false, fail_epochs: Vec::new() };
    for (human, bot, session) in [("alice", "bot-a", "s"), ("bob", "bot-a", "s"), ("carol", "bot-b", "s"), ("dave", "bot-a", "other")] {
        let mut d = descriptor(bot, session, "unused", NOW+1000);
        let target = d.observe_target.as_mut().unwrap();
        target.caller_ref = None;
        target.target_epoch = None;
        index.replace_from_descriptor(human, bot, session, &d, NOW).unwrap();
    }
    assert_eq!(fanout_shared_context(&index, &port, &RedactedToken::new("svc"), "alice", "bot-a", "s", "m", "hello", NOW).await.unwrap(), 1);
    let calls = port.calls.lock().unwrap();
    assert_eq!(calls[0].caller_subject.expose(), "bob");
    assert!(calls[0].runtime_target_ref.is_none());
    assert!(calls[0].runtime_target_epoch.is_none());
    drop(calls);
    port.calls.lock().unwrap().clear();
    assert_eq!(bcs_message_flow::observe_sync::fanout_shared_reply(
        &index, &port, &RedactedToken::new("svc"), "bot-a", "s", "reply", "answer", NOW,
    ).await.unwrap(), 2);
    let subjects: std::collections::BTreeSet<String> = port.calls.lock().unwrap()
        .iter().map(|call| call.caller_subject.expose().to_string()).collect();
    assert_eq!(subjects, std::collections::BTreeSet::from(["alice".into(), "bob".into()]));
    assert!(index.current("bob", "bot-a", "s", NOW+1000).is_none());
}
