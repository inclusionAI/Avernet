//! Control the Worker writes into the dispatch: human replies and cancels.
//!
//! A reply continues the same call on the same remote task. It is confirmed
//! only when the agent definitely accepted it, and only as a gap-free prefix;
//! removing delivered inputs is the Worker's job. The Connector never cancels
//! on its own: it carries out the cancel it finds, and keeps "request
//! accepted", "confirmed canceled" and "unknown" apart.
//!
//! Where the Worker's control lives in `disp`:
//! - `/control/pending_input`: human replies, each with a `seq` (1, 2, …).
//!   "Pending" means "in the Worker's queue", not "unconfirmed": only the
//!   Worker removes entries, so a reply the agent took stays listed until the
//!   Worker cleans up. The holder sends the entry with `seq =
//!   confirmed_input_through + 1` (see `rules::next_input`) to the stored
//!   remote task (B12).
//! - `/execution/confirmed_input_through`: written by the holder when the
//!   agent definitely accepted a reply (`confirm_input`).
//! - `/connector/input_sending`: the holder's durable intent for a reply
//!   (`begin_send(Input)`), written before the network call (B3).
//! - `/control/cancel` (Worker) and `/execution/cancellation` (holder, the
//!   cancel observation): `NotSent` → `Sending` → `RequestAccepted` |
//!   `ConfirmedCanceled` | `AlreadyFinished` | `Rejected` | `OutcomeUnknown`.
//!
//! A failed reply blocks later replies (`input_blocked`) but the call is
//! still followed and can still be canceled. That differs from an unresolved
//! START (`Call::unresolved`), where only a cancel is acted on.

use crate::call::{Call, Flow, Stop, held_write};
use crate::delayed::UnresolvedKind;
use crate::report::Admitted;
use crate::send::RemoteRead;
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::{DedupeRecheck, INPUT_MAYBE_SENT, UncertainSend};
use bcs_service_api::port::dist_agent::{
    CallOperation, CancelOutcome, SendOutcome, StableSendRequest,
};
use bcs_service_api::port::repo::distributed::dispatch::{
    AcceptanceEvidence, HolderError, SendOperation,
};

impl Call {
    /// Forwards the next pending input to the remote task.
    ///
    /// Steps: record the send intent; load the payload (it may be a blob);
    /// send with request id `<did>:in:<seq>` on the stored remote task and
    /// context; on a definite acceptance confirm the input. Called from the
    /// tick, at most once per tick, so a retry waits for the next tick.
    /// Returns without sending when no remote task id is stored yet.
    pub async fn send_input(&mut self, input: PendingInput) -> Flow {
        let did = self.did().clone();
        let spec = self.spec().clone();
        let capabilities = spec.capabilities.clone();
        let Some(remote) = self.held.view().execution.remote.clone() else {
            return Ok(());
        };
        let request_id = self.ports.rules.input_request_id(&did, input.seq);
        let operation = SendOperation::Input {
            seq: input.seq,
            request_id: request_id.clone(),
        };
        let Some(payload) = self.load(&input.payload).await? else {
            return self.block_input(UnresolvedKind::NotSent, "input_unavailable").await;
        };
        let current = self.ports.rules.send_proof(
            &spec,
            ProofOperation::Input {
                seq: input.seq,
                request_id: request_id.clone(),
            },
            &crate::send::canonical_body(&payload),
            self.config.send_fingerprint(&capabilities),
        );
        // A resend after an unknown outcome waits out its backoff.
        if self.input_resends > 0 && self.backing_off() {
            return Ok(());
        }
        match self.held.begin_send(operation.clone(), current.clone()).await {
            Ok(_) => {}
            Err(HolderError::Lost) => return Err(Stop::Lost),
            // A former holder recorded intent for this input and it is not
            // confirmed: it may already be with the agent. Intent and proof
            // are durable; only an agent whose recorded proof still upholds
            // the negotiated dedupe gets it again (then the code falls
            // through and resends it).
            Err(HolderError::InvalidTransition { code }) if code == INPUT_MAYBE_SENT => {
                if !self.dedupe_recheck(&spec, &current, &capabilities) {
                    return self.block_input(UnresolvedKind::Uncertain, "input_maybe_sent").await;
                }
            }
            // A temporary store error or another refusal: nothing was sent,
            // try again on the next tick.
            Err(error) => {
                tracing::warn!(did = %did.0, ?error, "input send intent not recorded; next tick");
                return Ok(());
            }
        }
        let resend_allowed = self.dedupe_recheck(&spec, &current, &capabilities);
        let authority = self.authority_now(CallOperation::Continue, Some(remote.clone()), &spec);
        let send = StableSendRequest {
            request_id,
            context_id: remote.context_id.clone(),
            payload,
            node: spec.node.clone(),
            attempt: spec.attempt,
            frozen_history: None,
        };
        match self.ports.agent.send_input(&authority, &remote, send).await {
            SendOutcome::Accepted { remote, events } => {
                self.confirm(input.seq, operation, remote).await?;
                match events {
                    Some(stream) => self.events = Some(stream),
                    None => self.reattach_due = true,
                }
                Ok(())
            }
            SendOutcome::Completed { result } => {
                self.confirm(input.seq, operation, remote).await?;
                match self.ports.rules.completed_result(&result) {
                    Ok(verified) => {
                        if self.report(AgentEventKind::Final, None, verified).await? == Admitted::Yes {
                            self.finish(FinalDispatchOutcome::Completed, "sync_result").await?;
                        }
                        Ok(())
                    }
                    Err(code) => {
                        self.end_with_error("failed", code, FinalDispatchOutcome::Failed, code).await
                    }
                }
            }
            // The agent definitely did not take it: not confirmed.
            SendOutcome::Rejected { code } => self.block_input(UnresolvedKind::NotSent, &code).await,
            SendOutcome::NotSent { code } => {
                self.input_not_sent += 1;
                if self.input_not_sent > self.config.not_sent_retries {
                    return self.block_input(UnresolvedKind::NotSent, &code).await;
                }
                Ok(()) // retried on the next tick
            }
            SendOutcome::OutcomeUnknown { code } => {
                if !resend_allowed {
                    return self.block_input(UnresolvedKind::Uncertain, &code).await;
                }
                // A dedupe-verified idempotent input is sent again, with
                // backoff and a bound like the start's resend.
                self.input_resends += 1;
                if self.input_resends > self.config.not_sent_retries {
                    return self.block_input(UnresolvedKind::Uncertain, "resend_exhausted").await;
                }
                tracing::warn!(did = %did.0, code, "input outcome unknown; resending after a backoff");
                self.back_off();
                Ok(())
            }
        }
    }

    /// Whether the input this attempt would send may rely on the target's
    /// dedupe for a resend (plan T6): the proof recorded with this input's
    /// send intent — this attempt's own after a fresh write, an earlier
    /// attempt's after an INPUT_MAYBE_SENT refusal — must still uphold the
    /// window, domain, semantics and configuration under the current grants.
    fn dedupe_recheck(
        &self,
        spec: &DispatchSpec,
        current: &SendProof,
        capabilities: &DownstreamCapabilities,
    ) -> bool {
        let resolved_domain = crate::identity::dedupe_domain(&spec.target);
        let now = TimestampMillis(self.config.clock.now());
        let recorded = self.held.view().execution.send_proof.clone();
        let recheck = DedupeRecheck {
            recorded: recorded.as_ref(),
            current,
            now: Some(now),
            resolved_domain: Some(resolved_domain.as_str()),
        };
        self.ports.rules.uncertain_input(capabilities, &recheck) == UncertainSend::Resend
    }

    /// Records that the agent definitely accepted input `seq`
    /// (`confirmed_input_through = seq`, a gap-free prefix) and resets the
    /// per-input counters and the tick backoff. The entry stays in
    /// `/control/pending_input` until the Worker removes it.
    async fn confirm(&mut self, seq: InputSeq, operation: SendOperation, remote: RemoteTaskRef) -> Flow {
        let evidence = AcceptanceEvidence { operation, remote };
        held_write!(self, "input confirmation", self.held.confirm_input(seq, evidence.clone()));
        self.awaiting_input = false;
        self.input_not_sent = 0;
        self.input_resends = 0;
        self.reset_backoff();
        // The polled state is deliberately kept: until a poll observes
        // something else, an identical read is the question just answered
        // (the agent has not processed the reply yet), not a new one. A
        // question the agent really asks again differs, because the adapter
        // passes the agent's message on whole, with its message id.
        Ok(())
    }

    /// The input stays pending and unconfirmed; later inputs wait behind it.
    /// Reports it through the D2 stub (the Worker is not told today). Unlike
    /// `mark_unresolved` the call keeps being followed and canceled, and
    /// `Call::unresolved` is not set.
    async fn block_input(&mut self, kind: UnresolvedKind, code: &str) -> Flow {
        self.input_blocked = true;
        self.report_unresolved(kind, code).await;
        Ok(())
    }

    /// Carries out a cancel the Worker wrote, once per observation state.
    ///
    /// The stored observation decides: open states (`NotSent`, `Sending`,
    /// `OutcomeUnknown`) send the cancel; a definite result a former holder
    /// recorded but did not finish on is finished now; `RequestAccepted` and
    /// `Rejected` are never sent again. An agent without cancel support gets
    /// `Rejected { cancel_unsupported }` recorded and no request. Each attempt
    /// writes `Sending` before
    /// the request and the answer after it. An `OutcomeUnknown` answer is
    /// retried on a later tick, spaced by the tick backoff (`retry_after`), so
    /// the actor does not sleep and keeps serving the call meanwhile.
    pub async fn carry_out_cancel(&mut self, view: &DispatchView) -> Flow {
        let Some(intent) = view.control.cancel.clone() else {
            return Ok(());
        };
        match &view.execution.cancellation {
            CancelObservation::NotSent | CancelObservation::Sending | CancelObservation::OutcomeUnknown => {}
            // A definite result is recorded but the call is not ended: the
            // holder that observed it died before reporting. Finish its work.
            CancelObservation::ConfirmedCanceled => {
                return self
                    .end_with_error("canceled", "agent_canceled", FinalDispatchOutcome::Canceled, "cancel_confirmed")
                    .await;
            }
            CancelObservation::AlreadyFinished { outcome } => return self.end_already_finished(*outcome).await,
            // Accepted (wait for the agent) or refused: no second attempt.
            CancelObservation::RequestAccepted | CancelObservation::Rejected { .. } => return Ok(()),
        }
        let Some(remote) = view.execution.remote.clone() else {
            return Ok(());
        };
        // A cancel whose outcome stayed unknown is tried again, but not on
        // every tick: each attempt costs two dispatch writes and one request
        // to an agent that is already failing.
        if view.execution.cancellation != CancelObservation::NotSent && self.backing_off() {
            return Ok(());
        }
        if !view.spec.capabilities.cancel {
            let refused = CancelObservation::Rejected {
                code: "cancel_unsupported".into(),
            };
            held_write!(self, "cancel observation", self.held.record_cancel(refused.clone()));
            return Ok(());
        }
        held_write!(self, "cancel intent", self.held.record_cancel(CancelObservation::Sending));
        let authority = self.authority_now(CallOperation::Cancel, Some(remote.clone()), &view.spec);
        let outcome = self
            .ports
            .agent
            .cancel(&authority, &remote, intent.request_id.clone())
            .await;
        let observed = match &outcome {
            CancelOutcome::RequestAccepted => CancelObservation::RequestAccepted,
            CancelOutcome::ConfirmedCanceled => CancelObservation::ConfirmedCanceled,
            CancelOutcome::AlreadyFinished(outcome) => CancelObservation::AlreadyFinished { outcome: *outcome },
            CancelOutcome::Rejected { code } => CancelObservation::Rejected { code: code.clone() },
            CancelOutcome::OutcomeUnknown { .. } => CancelObservation::OutcomeUnknown,
        };
        held_write!(self, "cancel observation", self.held.record_cancel(observed.clone()));
        if matches!(outcome, CancelOutcome::OutcomeUnknown { .. }) {
            self.back_off();
        } else {
            self.reset_backoff();
        }
        match outcome {
            CancelOutcome::ConfirmedCanceled => {
                self.end_with_error("canceled", "agent_canceled", FinalDispatchOutcome::Canceled, "cancel_confirmed")
                    .await
            }
            CancelOutcome::AlreadyFinished(finished) => self.end_already_finished(finished).await,
            // Accepted is not canceled: keep following until the agent ends.
            // Unknown is tried again on a later tick; a refusal is final.
            CancelOutcome::RequestAccepted | CancelOutcome::Rejected { .. } | CancelOutcome::OutcomeUnknown { .. } => {
                Ok(())
            }
        }
    }

    /// The agent had already finished: report its own terminal state.
    ///
    /// When the task can be queried, it is read first (at most
    /// `not_sent_retries` + 1 times, spaced by the tick backoff), so the
    /// real result is reported rather than an empty one. If the read ends
    /// the call, done. Otherwise the outcome from the cancel answer is
    /// reported: `completed` as a final event, `failed` / `canceled` as an
    /// error event. `finished_reads = u32::MAX` marks the reads as used up.
    async fn end_already_finished(&mut self, finished: FinalDispatchOutcome) -> Flow {
        if self.spec().capabilities.query_by_task && self.finished_reads <= self.config.not_sent_retries {
            // The task's real result (message, artifacts) is preferred over
            // an empty one under the same identity, so the task is read: a
            // bounded number of times, spaced by the per-call backoff.
            if self.backing_off() {
                return Ok(());
            }
            // A terminal state reported earlier is reported again (same
            // identity); a non-terminal one is not repeated on every read.
            if self.last_polled.as_ref().is_some_and(|(status, _)| {
                !matches!(status, TaskStatus::Submitted | TaskStatus::Working)
            }) {
                self.last_polled = None;
            }
            let read = self.read_remote().await?;
            if self.ended || self.conflicted {
                return Ok(());
            }
            // Read but not terminal yet (the read lags the cancel answer), or
            // not readable this time: try again later, up to the bound. A
            // definite "no such task" is not asked again.
            if read != RemoteRead::Gone {
                self.finished_reads += 1;
                if self.finished_reads <= self.config.not_sent_retries {
                    self.back_off();
                    return Ok(());
                }
            }
            self.finished_reads = u32::MAX;
            self.reset_backoff();
        }
        // No task read, or it did not verify a complete result: the cancel
        // answer said the task completed, but a Completed without a
        // verified product is only a stop fact — the recovery continues
        // until the rules admit a verified Final or the deadline bounds
        // the call (plan T6).
        match finished {
            FinalDispatchOutcome::Completed => {
                let payload = Payload::Inline(serde_json::json!({ "state": "completed" }));
                self.stop_fact("result_not_recovered", payload).await
            }
            FinalDispatchOutcome::Failed => {
                self.end_with_error("failed", "agent_failed", finished, "already_finished").await
            }
            FinalDispatchOutcome::Canceled => {
                self.end_with_error("canceled", "agent_canceled", finished, "already_finished").await
            }
        }
    }
}
