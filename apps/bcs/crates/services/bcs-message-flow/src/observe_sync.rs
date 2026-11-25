//! Observe-only shared-context sync (spec §6.4/§10.10): routing ONE
//! projected shared message toward ONE caller CONTAINER (Human×Bot×session)
//! through the observe index. The index decides — a live reference routes
//! the inject, an absent/expired/tombstoned one PAUSES that container's
//! sync (no ensure, no identity borrowing). Injects never carry execution
//! material and never trigger tools/children/runs.

use bcs_service_api::port::{
    ObserveInjectCommand, ObserveInjectError, ObserveInjectPort,
};
use bcs_domain::RedactedToken;

use crate::observe_index::ObserveTargetIndex;

/// What happened to one container's sync attempt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObserveSyncOutcome {
    /// The inject was delivered under the current live reference.
    Injected,
    /// No live reference (absent/expired/evicted/tombstoned): the
    /// container's sync is paused until its own next trusted ensure — never
    /// an error, and never resolved by triggering one from here.
    PausedNoLiveTarget,
}

/// Sync one shared-context message toward ONE container of the Bot. The
/// inject is idempotent by message_id on the receiving side; a transport
/// failure is surfaced as a retryable error, never fabricated as success.
pub async fn sync_shared_message(
    index: &ObserveTargetIndex,
    port: &dyn ObserveInjectPort,
    credential: &RedactedToken,
    caller_subject: &str,
    target_bot_id: &str,
    session_id: &str,
    message_id: &str,
    text: &str,
    now_ms: i64,
) -> Result<ObserveSyncOutcome, ObserveInjectError> {
    if message_id.trim().is_empty() || text.is_empty() || now_ms <= 0 {
        return Err(ObserveInjectError::InvalidRequest);
    }
    let Some(target) = index.current(caller_subject, target_bot_id, session_id, now_ms) else {
        return Ok(ObserveSyncOutcome::PausedNoLiveTarget);
    };
    port.inject_shared_context(
        credential,
        ObserveInjectCommand {
            target_bot_id: target_bot_id.to_string(),
            session_id: session_id.to_string(),
            caller_subject: target.caller_subject,
            runtime_target_ref: target.caller_ref,
            runtime_target_epoch: target.target_epoch,
            message_id: message_id.to_string(),
            text: text.to_string(),
            now_ms,
        },
    )
    .await?;
    Ok(ObserveSyncOutcome::Injected)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::observe_index::tests_support::descriptor;
    use async_trait::async_trait;
    use std::sync::Mutex as StdMutex;

    struct RecordingPort {
        calls: StdMutex<Vec<(String, String, String)>>,
        fail: bool,
    }

    #[async_trait]
    impl ObserveInjectPort for RecordingPort {
        async fn inject_shared_context(
            &self,
            _credential: &RedactedToken,
            command: ObserveInjectCommand,
        ) -> Result<(), ObserveInjectError> {
            if self.fail {
                return Err(ObserveInjectError::Unavailable { retryable: true });
            }
            self.calls.lock().unwrap().push((
                command.runtime_target_epoch.as_ref().unwrap().expose().to_string(),
                command.message_id,
                command.text,
            ));
            Ok(())
        }
    }

    const NOW: i64 = 1_780_000_000_000;

    #[tokio::test]
    async fn live_target_routes_and_absent_target_pauses_without_ensure() {
        let index = ObserveTargetIndex::new(4);
        let port = RecordingPort { calls: StdMutex::new(Vec::new()), fail: false };
        // No reference yet: paused, and the port is untouched.
        let outcome = sync_shared_message(
            &index, &port, &RedactedToken::new("svc"), "human-a", "bot", "s", "m1", "hello", NOW,
        )
        .await
        .unwrap();
        assert_eq!(outcome, ObserveSyncOutcome::PausedNoLiveTarget);
        assert!(port.calls.lock().unwrap().is_empty());

        // A trusted ensure seeds the live reference: the same message routes.
        index
            .replace_from_descriptor("human-a", "bot", "s", &descriptor("bot", "s", "g7", NOW + 60_000), NOW)
            .unwrap();
        let outcome = sync_shared_message(
            &index, &port, &RedactedToken::new("svc"), "human-a", "bot", "s", "m1", "hello", NOW,
        )
        .await
        .unwrap();
        assert_eq!(outcome, ObserveSyncOutcome::Injected);
        let calls = port.calls.lock().unwrap();
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].0, "epoch-g7", "routes under the live generation");
        assert_eq!(calls[0].1, "m1");
        drop(calls);

        // A tombstoned container pauses again (invalidate on member leave).
        index.invalidate("human-a", "bot", "s");
        let outcome = sync_shared_message(
            &index, &port, &RedactedToken::new("svc"), "human-a", "bot", "s", "m2", "again", NOW,
        )
        .await
        .unwrap();
        assert_eq!(outcome, ObserveSyncOutcome::PausedNoLiveTarget);

        // Expired reference pauses (never fabricated, never re-ensured).
        index
            .replace_from_descriptor("human-a", "bot", "s", &descriptor("bot", "s", "g8", NOW + 1_000), NOW)
            .unwrap();
        let outcome = sync_shared_message(
            &index, &port, &RedactedToken::new("svc"), "human-a", "bot", "s", "m3", "late", NOW + 2_000,
        )
        .await
        .unwrap();
        assert_eq!(outcome, ObserveSyncOutcome::PausedNoLiveTarget);

        // Another Human's container of the same Bot is a different entry:
        // syncing toward it paused (never borrowing alice's reference).
        let outcome = sync_shared_message(
            &index, &port, &RedactedToken::new("svc"), "human-b", "bot", "s", "m4", "borrow", NOW,
        )
        .await
        .unwrap();
        assert_eq!(outcome, ObserveSyncOutcome::PausedNoLiveTarget);
    }

    #[tokio::test]
    async fn invalid_input_and_transport_failures_never_fabricate_success() {
        let index = ObserveTargetIndex::new(4);
        index
            .replace_from_descriptor("human-a", "bot", "s", &descriptor("bot", "s", "g1", NOW + 60_000), NOW)
            .unwrap();
        let port = RecordingPort { calls: StdMutex::new(Vec::new()), fail: true };
        assert!(
            sync_shared_message(&index, &port, &RedactedToken::new("svc"), "human-a", "bot", "s", "", "x", NOW)
                .await
                .is_err(),
            "empty message id rejects"
        );
        let error = sync_shared_message(
            &index, &port, &RedactedToken::new("svc"), "human-a", "bot", "s", "m9", "x", NOW,
        )
        .await
        .unwrap_err();
        assert_eq!(error, ObserveInjectError::Unavailable { retryable: true });
        assert!(port.calls.lock().unwrap().is_empty());
    }
}
// ---------------------------------------------------------------------------
// Shared-context fan-out (spec §6.4: `同Bot其他Caller容器`): one dispatch
// toward the CURRENT container rides chat.send; every OTHER known valid
// caller container of the SAME Bot in the SAME session receives this same
// message as an observe-only inject through the observe index. A DIFFERENT
// Bot's reception is never decided here — that is the other Bot's own
// delivery/shared projection. Injects never trigger tools/children/runs
// (the wire carries context-only extensions, §15.38).
// ---------------------------------------------------------------------------

/// Fan the shared context toward the other known valid containers of this
/// Bot×session, excluding the dispatching caller's own entry (it received
/// chat.send). Returns how many containers received the inject (each under
/// its own live reference — never the current dispatch's, never a borrowed
/// Human's). No container enumeration beyond the observe index, no
/// re-resolution of anything: containers without a live reference simply
/// are not observers (their sync stays paused until their own next trusted
/// ensure), which is not an error and not reported per container.
pub async fn fanout_shared_context(
    index: &ObserveTargetIndex,
    port: &dyn ObserveInjectPort,
    credential: &RedactedToken,
    current_subject: &str,
    target_bot_id: &str,
    session_id: &str,
    message_id: &str,
    text: &str,
    now_ms: i64,
) -> Result<usize, ObserveInjectError> {
    if message_id.trim().is_empty() || text.is_empty() || now_ms <= 0 {
        return Err(ObserveInjectError::InvalidRequest);
    }
    let others = index.others_for(target_bot_id, session_id, current_subject, now_ms);
    let injected = others.len();
    // Per-target isolation: one container's inject failure never cancels
    // the remaining containers — every target is attempted, then the
    // failure surfaces once. The caller's bounded retry re-runs the whole
    // fan-out safely because the shared dedup id makes re-injects no-ops
    // for the containers that already got the message.
    let mut failure: Option<ObserveInjectError> = None;
    let mut retryable_failure = false;
    for target in others {
        if let Err(error) = port
            .inject_shared_context(
                credential,
                ObserveInjectCommand {
                    target_bot_id: target_bot_id.to_string(),
                    session_id: session_id.to_string(),
                    caller_subject: target.caller_subject,
                    runtime_target_ref: target.caller_ref,
                    runtime_target_epoch: target.target_epoch,
                    message_id: message_id.to_string(),
                    text: text.to_string(),
                    now_ms,
                },
            )
            .await
        {
            if matches!(error, ObserveInjectError::Unavailable { retryable: true }) {
                retryable_failure = true;
            }
            failure = Some(error);
        }
    }
    if let Some(error) = failure {
        // A retryable failure wins over a structural one so the caller's
        // bounded retry actually runs (a lone structural refusal would term
        // it, stranding the transiently-failed containers).
        if retryable_failure {
            return Err(ObserveInjectError::Unavailable { retryable: true });
        }
        return Err(error);
    }
    Ok(injected)
}


/// Fan one Bot's OWN reply (spec §6.4 同Bot容器回复同步) toward every live
/// caller container of the Bot×session — the executing container included:
/// the reply is shared-stream record, and each live container deduplicates
/// by message id. Returns how many containers received the inject. All
/// container-routing invariants of [`fanout_shared_context`] hold: own
/// live reference per container, index misses are not observers, failures
/// surface without fabricated success — and, like the context fan-out,
/// per-target isolation: one container's failure never cancels the
/// remaining containers' injections.
pub async fn fanout_shared_reply(
    index: &ObserveTargetIndex,
    port: &dyn ObserveInjectPort,
    credential: &RedactedToken,
    bot_id: &str,
    session_id: &str,
    message_id: &str,
    text: &str,
    now_ms: i64,
) -> Result<usize, ObserveInjectError> {
    if message_id.trim().is_empty() || text.is_empty() || now_ms <= 0 {
        return Err(ObserveInjectError::InvalidRequest);
    }
    let targets = index.all_for(bot_id, session_id, now_ms);
    let injected = targets.len();
    let mut failure: Option<ObserveInjectError> = None;
    let mut retryable_failure = false;
    for target in targets {
        if let Err(error) = port
            .inject_shared_context(
                credential,
                ObserveInjectCommand {
                    target_bot_id: bot_id.to_string(),
                    session_id: session_id.to_string(),
                    caller_subject: target.caller_subject,
                    runtime_target_ref: target.caller_ref,
                    runtime_target_epoch: target.target_epoch,
                    message_id: message_id.to_string(),
                    text: text.to_string(),
                    now_ms,
                },
            )
            .await
        {
            if matches!(error, ObserveInjectError::Unavailable { retryable: true }) {
                retryable_failure = true;
            }
            failure = Some(error);
        }
    }
    if let Some(error) = failure {
        if retryable_failure {
            return Err(ObserveInjectError::Unavailable { retryable: true });
        }
        return Err(error);
    }
    Ok(injected)
}
