//! Shared input synchronization for both root and delegated accepted sends.
use super::*;

pub(super) struct SharedDispatchInput<'a> {
    pub target_bot_id: &'a str,
    pub group_id: Option<&'a str>,
    pub session_id: &'a str,
    pub message_turn_id: &'a str,
    pub caller_task: &'a str,
    pub shared_content_kind: Option<&'a bcs_domain::shared_stream::SharedContentKind>,
}

impl CallerDispatchPipeline {
    /// Fan the accepted shared context toward the same Bot's other caller
    /// containers. No-ops without the observe assembly, for unclassified
    /// content, or when the shared-stream policy denies THIS Bot's stream
    /// (L225): directed/private/unlinked material never reaches another
    /// Human's container of this Bot.
    pub(super) fn schedule_observe_fanout(
        &self,
        facts: SharedDispatchInput<'_>,
        target: &BotDeliveryTarget,
        caller_subject: &str,
        service_credential: &RedactedToken,
        at_term: Option<u64>,
    ) {
        let Some(observe) = self.observe.as_ref().cloned() else {
            return;
        };
        let Some(kind) = facts.shared_content_kind else {
            tracing::debug!(
                bot_id = %facts.target_bot_id,
                "observe fanout skipped: unclassified shared content never broadcasts"
            );
            return;
        };
        // The eligibility check is evaluated for THIS Bot's shared stream
        // once: every observer of this fan-out is a container of the same
        // Bot, so a denial closes the whole fan-out (L225).
        if bcs_domain::shared_stream::shared_stream_eligibility(kind, facts.target_bot_id).is_err() {
            tracing::debug!(
                bot_id = %facts.target_bot_id,
                "observe fanout skipped: shared-stream policy denies this content on this Bot's stream"
            );
            return;
        }
        let target = target.clone();
        let credential = service_credential.clone();
        let term_token = self.delivery_term.clone();
        let Some(group_id) = facts.group_id.map(str::to_owned) else { return; };
        let subject = caller_subject.to_string();
        let bot_id = facts.target_bot_id.to_string();
        let session_id = facts.session_id.to_string();
        let message_turn_id = facts.message_turn_id.to_string();
        let text = facts.caller_task.to_string();
        tokio::spawn(async move {
            // All containers of one Bot live behind the same Provider
            // webhook: the injector reuses the CURRENT dispatch's own
            // target and credential. No transport shape pauses the fan-out.
            let Some(port) = observe.injector_factory.injector_for(&target) else {
                tracing::debug!(
                    bot_id = %bot_id,
                    "observe fanout: no observe transport for this target shape; sync paused"
                );
                return;
            };
            // Transient transport failures retry a bounded times within this
            // detached task (p1-3②): the shared message id makes every retry
            // idempotent. Structural fix for cross-RESTART losses is the
            // Gateway-side history replay on the next trusted ensure —
            // observe refs are runtime-only by the §1 boundary and cannot
            // live in any durable redelivery queue.
            let mut attempts_left = 3u32;
            loop {
                // Review-VI P1 term fence: a fan-out belonging to a demoted
                // master term stops before its next inject — the observe
                // directory it captured is the sealed incarnation's.
                if let (Some(token), Some(captured)) = (term_token.as_ref(), at_term) {
                    if token.load(std::sync::atomic::Ordering::SeqCst) != captured {
                        tracing::info!(
                            bot_id = %bot_id,
                            message_turn_id = %message_turn_id,
                            "observe fanout folded: master term changed mid-sync"
                        );
                        return;
                    }
                }
                // Review-VI P2: each attempt reads the FRESH clock for the
                // index's eligibility — a retry must never judge a reference
                // live against a stale captured time when its window has
                // meanwhile passed, and an expired reference must simply
                // stop being an observer in the next round.
                let round_now_ms = chrono::Utc::now().timestamp_millis();
                let qualified = observe.qualified_port(port.clone(), &group_id, &bot_id, &session_id, at_term).await;
                let result = match qualified {
                    Ok(Some(qualified)) => crate::observe_sync::fanout_shared_context(
                    &observe.index,
                    qualified.as_ref(),
                    &credential,
                    &subject,
                    &bot_id,
                    &session_id,
                    &message_turn_id,
                    &text,
                    round_now_ms,
                )
                .await,
                    Ok(None) => return,
                    Err(error) => Err(error),
                };
                match result {
                    Ok(injected) => {
                        tracing::debug!(
                            bot_id = %bot_id,
                            message_turn_id = %message_turn_id,
                            injected,
                            "observe fanout: shared context synced to other known containers"
                        );
                        break;
                    }
                    Err(error) => {
                        let retryable = matches!(error, bcs_service_api::port::ObserveInjectError::Unavailable { retryable: true });
                        attempts_left = attempts_left.saturating_sub(1);
                        if retryable && attempts_left > 0 {
                            tracing::warn!(
                                bot_id = %bot_id,
                                message_turn_id = %message_turn_id,
                                %error,
                                remaining_attempts = attempts_left,
                                "observe fanout: transient inject failure; retrying with the same dedup id"
                            );
                            tokio::time::sleep(std::time::Duration::from_millis(200)).await;
                            continue;
                        }
                        tracing::warn!(
                            bot_id = %bot_id,
                            message_turn_id = %message_turn_id,
                            %error,
                            "observe fanout: inject finally failed; the container's sync pauses until its next trusted ensure"
                        );
                        break;
                    }
                }
            }
        });
    }
}
