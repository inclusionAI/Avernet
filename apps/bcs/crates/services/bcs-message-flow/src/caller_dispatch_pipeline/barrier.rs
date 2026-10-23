//! Same-dispatch barrier redrive (spec §6.8, review-V P1-3 / B3.2): the
//! in-process driver for a protected dispatch the Gateway refused with
//! retryable HISTORY_NOT_READY. The pipeline parks the ORIGINAL send inputs
//! (target, service credential, the exact SendCallerExecutionCommand this
//! attempt issued — same dispatch id, same digest, same attempt, same
//! proof token) plus the snapshot attestation facts, and a detached loop
//! drives the §6.8 order: inject frozen message bodies → history_complete → on explicit readiness
//! re-send the SAME command → converge the row through the converger.
//!
//! Boundary discipline (§1842): never a new attempt — the resend is the
//! wire-bound same dispatch; the proof token may legally have expired by
//! completion time and the wire-time freshness gate refuses it — that
//! converges the barrier the hard way (TransportRejected: an expired
//! identity may not re-issue by itself). Identity material stays in
//! runtime-only memory: the park dies with the process, and the durable
//! HistoryBarrier marker keeps the parked row away from the startup
//! recovery sweep; the run-deadline control remains the final convergence
//! bound when nothing else settles first.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_service_api::application::message_delivery::ManagedMessageDeliveryService;
use bcs_service_api::DeliveryTransitionCommand;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::DeliveryLookup;
use bcs_service_api::port::{
    CallerDispatchAck, CallerExecutionSendPort, HistoryBackfillPort, HistoryCompleteCommand,
};

use crate::caller_dispatch_pipeline::PARK_CAPACITY;


/// Default redrive backoff ladder (ms): give the history replay a start
/// margin, then grow — the deadline control is the real convergence bound,
/// so the ladder caps instead of giving up.
const DEFAULT_BACKOFF_MS: &[u64] = &[5_000, 15_000, 45_000, 120_000, 300_000];

/// One parked same-dispatch redrive: everything the wire saw (never
/// anything new — no re-ensure, no new proof, no new attempt).
pub(crate) struct BarrierPark {
    pub target: bcs_domain::BotDeliveryTarget,
    pub credential: bcs_domain::RedactedToken,
    pub command: bcs_service_api::port::SendCallerExecutionCommand,
    pub snapshot: HistoryCompleteCommand,
    pub attempts: u32,
}

/// What a redrive round decided for the parked row.
enum Convergence {
    /// The Gateway atomically accepted the SAME dispatch: Submission (not
    /// acceptance — a trusted Bot event still settles the row).
    Submitted,
    /// A terminal, non-history refusal or an expired window: converge
    /// closed, never a new attempt.
    Refused,
    /// Unknown post-network outcome: conservative convergence.
    Unknown,
}

/// Row bookkeeping for a settled barrier — a small converger over the
/// managed delivery service (the same CAS-verifying transition machinery
/// the runtime uses; a lost CAS race means something already converged
/// the row and the converger folds into it).
pub struct HistoryBarrierConverger {
    service: Arc<dyn ManagedMessageDeliveryService>,
    expected: Option<bcs_service_api::port::CallerDispatchAssociation>,
    term: Option<Arc<std::sync::atomic::AtomicU64>>,
    at_term: Option<u64>,
}

impl HistoryBarrierConverger {
    pub fn new(service: Arc<dyn ManagedMessageDeliveryService>) -> Self {
        Self { service, expected: None, term: None, at_term: None }
    }

    async fn current_row(
        &self,
        delivery_id: &str,
    ) -> Option<bcs_domain::message_delivery::PersistedMessageDelivery> {
        match self
            .service
            .lookup(DeliveryLookup::Ids(vec![delivery_id.to_string()]))
            .await
        {
            Ok(rows) => rows.into_iter().next().filter(|row| {
                term_current(&self.term, self.at_term) && self.expected.as_ref().is_none_or(|expected|
                    row.run_id.as_deref() == Some(expected.run_id.as_str())
                    && row.request_id.as_deref() == Some(expected.dispatch_id.as_str())
                    && row.attempt_no.max(0) as u32 == expected.attempt)
            }),
            Err(_) => None,
        }
    }

    async fn row_active(&self, delivery_id: &str) -> bool {
        self.current_row(delivery_id)
            .await
            .is_some_and(|row| matches!(row.state.status, Status::Dispatching | Status::Running))
    }

    async fn converge(&self, delivery_id: &str, outcome: Convergence, now_ms: i64) {
        let Some(row) = self.current_row(delivery_id).await else {
            return;
        };
        if !matches!(row.state.status, Status::Dispatching | Status::Running) { return; }
        let event = match outcome {
            Convergence::Submitted => Event::Submitted,
            Convergence::Refused => Event::TransportRejected,
            Convergence::Unknown => Event::TransportUnknown,
        };
        let transition = DeliveryTransitionCommand {
            caller_dispatch_commit: None,
            caller_cancel: None,
            caller_terminal: None,
            delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version,
            event,
            now_ms,
            // Submission keeps the original attempt identity; a refused /
            // unknown convergence retraces the runtime's request evidence.
            request_id: Some(row.request_id.clone().unwrap_or_default()),
            actor_id: None,
            reply: None,
            transport_context_json: None,
            deadline_at_ms: None,
        };
        if let Err(error) = self.service.transition(transition).await {
            tracing::warn!(
                delivery_id = %row.delivery_id,
                %error,
                "history barrier convergence lost a CAS race or failed; the run-deadline control remains the bound"
            );
        }
    }
}

/// Park the send inputs and drive the §6.8 redrive order in a detached
/// task: frozen message bodies, snapshot completion confirmation, then the
/// SAME command re-sent, converging the row through managed transitions.
/// The park is bounded (capacity); a refused park keeps the plain parked
/// semantics (Dispatching + marker + deadline control) — never a worse
/// state than before this driver existed.
pub(crate) fn park_and_drive(
    accepted: super::accepted::AcceptedEffect,
    backfill: Arc<dyn HistoryBackfillPort>,
    messages: Option<Arc<dyn bcs_service_api::port::repo::MessageRepoPort>>,
    sender: Arc<dyn CallerExecutionSendPort>,
    service: Option<Arc<dyn ManagedMessageDeliveryService>>,
    parks: Arc<Mutex<HashMap<String, BarrierPark>>>,
    backoff: Arc<Vec<u64>>,
    delivery_id: String,
    target: bcs_domain::BotDeliveryTarget,
    credential: bcs_domain::RedactedToken,
    command: bcs_service_api::port::SendCallerExecutionCommand,
    snapshot: HistoryCompleteCommand,
    delivery_term: Option<Arc<std::sync::atomic::AtomicU64>>,
    at_term: Option<u64>,
) {
    let expected = command.association.clone();
    {
        let mut parked = parks.lock().expect("barrier parks");
        if parked.len() >= PARK_CAPACITY {
            tracing::warn!(
                delivery_id = %delivery_id,
                "history barrier park at capacity; the row keeps the plain parked (marker + deadline) semantics"
            );
            return;
        }
        if parked.contains_key(&delivery_id) {
            // A duplicate rejection of the one redrive attempt folds into
            // the already-driving loop; the parked inputs stay the originals.
            return;
        }
        parked.insert(
            delivery_id.clone(),
            BarrierPark {
                target,
                credential,
                command,
                snapshot,
                attempts: 0,
            },
        );
    }
    let Some(service) = service else {
        // The converger needs the managed service for row bookkeeping.
        // Without it the parked inputs would go un-collected: refuse.
        parks.lock().expect("barrier parks").remove(&delivery_id);
        return;
    };
    let converger = Arc::new(HistoryBarrierConverger {
        service, expected: Some(expected), term: delivery_term.clone(), at_term,
    });
    tokio::spawn(async move {
        loop {
            // Review-VI P1 term fence: a demoted/old-term park stops BEFORE
            // touching either the completion or the wire — the same-dispatch
            // redrive authority belongs to the CURRENT master's runtime.
            // The row keeps the durable marker + deadline control as the
            // convergence bound; the park itself folds on the term bump.
            if !term_current(&delivery_term, at_term) {
                parks.lock().expect("barrier parks").remove(&delivery_id);
                return;
            }
            let attempt = {
                let mut parked = parks.lock().expect("barrier parks");
                let Some(park) = parked.get_mut(&delivery_id) else { return; };
                park.attempts = park.attempts.saturating_add(1);
                // The backoff ladder positions by attempt count and caps at
                // its top step: convergence is ultimately the deadline
                // control's business, not the ladder's.
                backoff
                    .get((park.attempts as usize).saturating_sub(1))
                    .or_else(|| backoff.last())
                    .copied()
                    .unwrap_or(60_000)
            };
            tokio::time::sleep(std::time::Duration::from_millis(attempt)).await;
            if !converger.row_active(&delivery_id).await || !term_current(&delivery_term, at_term) {
                // Externally converged (cancel/expiry/terminal evidence).
                parks.lock().expect("barrier parks").remove(&delivery_id);
                return;
            }
            // ---- §6.8 order: bodies → confirmed completion → same dispatch. ----
            let (target, credential, command, mut snapshot) = {
                let mut parked = parks.lock().expect("barrier parks");
                let Some(park) = parked.get_mut(&delivery_id) else { return; };
                (
                    park.target.clone(),
                    park.credential.clone(),
                    park.command.clone(),
                    park.snapshot.clone(),
                )
            };
            // No identity renewal: an expired original attempt cannot recover.
            if !identity_current(&command) {
                converger.converge(&delivery_id, Convergence::Refused, chrono_now()).await;
                parks.lock().expect("barrier parks").remove(&delivery_id);
                return;
            }
            let completion = async {
                let bodies = super::history_replay::load_messages(messages.as_ref(), &snapshot,
                    &command.association.target_bot_id).await?;
                for (message_id, text) in bodies {
                    if !term_current(&delivery_term, at_term) || !identity_current(&command) {
                        return Err(bcs_service_api::port::HistoryBackfillError::NotSent);
                    }
                    backfill.inject_history_message(&target, &credential, bcs_service_api::port::ObserveInjectCommand {
                        target_bot_id: command.association.target_bot_id.clone(),
                        session_id: snapshot.session_id.clone(), caller_subject: snapshot.caller_subject.clone(),
                        runtime_target_ref: None, runtime_target_epoch: None,
                        message_id, text, now_ms: chrono_now(),
                    }).await?;
                }
                if !term_current(&delivery_term, at_term) || !converger.row_active(&delivery_id).await || !identity_current(&command) {
                    return Err(bcs_service_api::port::HistoryBackfillError::NotSent);
                }
                snapshot.now_ms = chrono_now();
                backfill.inject_history_complete(&target, &credential, snapshot).await
            }.await;
            if !term_current(&delivery_term, at_term) {
                parks.lock().expect("barrier parks").remove(&delivery_id);
                return;
            }
            match completion {
                Err(bcs_service_api::port::HistoryBackfillError::Unavailable { retryable: true }) => {
                    tracing::info!(
                        delivery_id = %delivery_id,
                        "history barrier: backfill incomplete; the same dispatch stays parked"
                    );
                    continue;
                }
                Err(_) => {
                    tracing::warn!(
                        delivery_id = %delivery_id,
                        "history barrier: backfill refused closed; converging the parked dispatch"
                    );
                    converger.converge(&delivery_id, Convergence::Refused, chrono_now()).await;
                    parks.lock().expect("barrier parks").remove(&delivery_id);
                    return;
                }
                Ok(()) => {}
            }
            // Cancellation/terminal state can change while backfill is in flight.
            if !converger.row_active(&delivery_id).await || !term_current(&delivery_term, at_term) {
                parks.lock().expect("barrier parks").remove(&delivery_id);
                return;
            }
            if !identity_current(&command) {
                converger.converge(&delivery_id, Convergence::Refused, chrono_now()).await;
                parks.lock().expect("barrier parks").remove(&delivery_id);
                return;
            }
            let result = sender.send_caller_execution(&target, &credential, command).await;
            if !term_current(&delivery_term, at_term) {
                parks.lock().expect("barrier parks").remove(&delivery_id);
                return;
            }
            match result {
                Ok(CallerDispatchAck::Accepted { .. }) => {
                    tracing::info!(
                        delivery_id = %delivery_id,
                        "history barrier: the SAME dispatch accepted after backfill; submission recorded"
                    );
                    accepted(chrono_now());
                    converger.converge(&delivery_id, Convergence::Submitted, chrono_now()).await;
                    parks.lock().expect("barrier parks").remove(&delivery_id);
                    return;
                }
                Ok(CallerDispatchAck::Rejected { error_code, retryable }) => {
                    if error_code == "HISTORY_NOT_READY" && retryable {
                        // Still not complete: stay parked, another §6.8 round.
                        tracing::info!(
                            delivery_id = %delivery_id,
                            "history barrier: gateway still not ready; the same dispatch stays parked"
                        );
                        continue;
                    }
                    tracing::warn!(
                        delivery_id = %delivery_id,
                        error_code = %error_code,
                        "history barrier: terminal refusal on redrive; converging"
                    );
                    converger.converge(&delivery_id, Convergence::Refused, chrono_now()).await;
                    parks.lock().expect("barrier parks").remove(&delivery_id);
                    return;
                }
                Ok(CallerDispatchAck::Unknown) | Err(_) => {
                    tracing::warn!(
                        delivery_id = %delivery_id,
                        "history barrier: redrive outcome unknown or the proof expired at wire time; converging conservatively"
                    );
                    converger.converge(&delivery_id, Convergence::Unknown, chrono_now()).await;
                    parks.lock().expect("barrier parks").remove(&delivery_id);
                    return;
                }
            }
        }
    });
}

fn chrono_now() -> i64 {
    chrono::Utc::now().timestamp_millis()
}

pub(crate) fn default_backoff() -> Vec<u64> {
    DEFAULT_BACKOFF_MS.to_vec()
}

fn term_current(term: &Option<Arc<std::sync::atomic::AtomicU64>>, captured: Option<u64>) -> bool {
    term.as_ref().map(|value| value.load(std::sync::atomic::Ordering::SeqCst)) == captured
}

fn identity_current(command: &bcs_service_api::port::SendCallerExecutionCommand) -> bool {
    let now = chrono_now();
    command.execution_token_expires_at_ms > now && command.caller_context_expires_at_ms > now
        && command.not_after_ms > now
}
