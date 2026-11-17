//! Control the Worker writes into the dispatch: human replies and cancels.
//!
//! A reply continues the same call on the same remote task. It is confirmed
//! only when the agent definitely accepted it, and only as a gap-free prefix;
//! removing delivered inputs is the Worker's job. The Connector never cancels
//! on its own: it carries out the cancel it finds, and keeps "request
//! accepted", "confirmed canceled" and "unknown" apart.

use crate::call::{Call, Flow, Stop, held_write};
use crate::delayed::UnresolvedKind;
use crate::report::Admitted;
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::{INPUT_MAYBE_SENT, UncertainSend};
use bcs_service_api::port::dist_agent::{CancelOutcome, SendOutcome, StableSendRequest};
use bcs_service_api::port::repo::distributed::dispatch::{
    AcceptanceEvidence, HolderError, SendOperation,
};

impl Call {
    /// Forwards the next pending input to the remote task.
    pub async fn send_input(&mut self, input: PendingInput) -> Flow {
        let did = self.did().clone();
        let target = self.spec().target.clone();
        let capabilities = self.spec().capabilities.clone();
        let Some(remote) = self.held.view().execution.remote.clone() else {
            return Ok(());
        };
        let request_id = self.ports.rules.input_request_id(&did, input.seq);
        let operation = SendOperation::Input {
            seq: input.seq,
            request_id: request_id.clone(),
        };
        let resend_allowed = self.ports.rules.uncertain_input(&capabilities) == UncertainSend::Resend;
        match self.held.begin_send(operation.clone()).await {
            Ok(_) => {}
            Err(HolderError::Lost) => return Err(Stop::Lost),
            // A former holder recorded intent for this input: it may already
            // be with the agent. Intent is durable; only an idempotent agent
            // gets it again.
            Err(HolderError::InvalidTransition { code }) if code == INPUT_MAYBE_SENT => {
                if !resend_allowed {
                    return self.block_input(UnresolvedKind::Uncertain, "input_maybe_sent").await;
                }
            }
            Err(error) => {
                tracing::warn!(did = %did.0, ?error, "input send intent not recorded; next tick");
                return Ok(());
            }
        }
        let Some(payload) = self.load(&input.payload).await? else {
            return self.block_input(UnresolvedKind::NotSent, "input_unavailable").await;
        };
        let send = StableSendRequest {
            request_id,
            context_id: remote.context_id.clone(),
            payload,
        };
        match self.ports.agent.send_input(&target, &remote, send).await {
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
                if self.report(AgentEventKind::Final, None, result).await? == Admitted::Yes {
                    self.finish(FinalDispatchOutcome::Completed, "sync_result").await?;
                }
                Ok(())
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
                if resend_allowed {
                    tracing::warn!(did = %did.0, code, "input outcome unknown; resending on the next tick");
                    return Ok(());
                }
                self.block_input(UnresolvedKind::Uncertain, &code).await
            }
        }
    }

    async fn confirm(&mut self, seq: InputSeq, operation: SendOperation, remote: RemoteTaskRef) -> Flow {
        let evidence = AcceptanceEvidence { operation, remote };
        held_write!(self, "input confirmation", self.held.confirm_input(seq, evidence.clone()));
        self.awaiting_input = false;
        self.input_not_sent = 0;
        Ok(())
    }

    /// The input stays pending and unconfirmed; later inputs wait behind it.
    async fn block_input(&mut self, kind: UnresolvedKind, code: &str) -> Flow {
        self.input_blocked = true;
        self.report_unresolved(kind, code).await;
        Ok(())
    }

    /// Carries out a cancel the Worker wrote, once per observation state.
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
        let target = view.spec.target.clone();
        if !view.spec.capabilities.cancel {
            let refused = CancelObservation::Rejected {
                code: "cancel_unsupported".into(),
            };
            held_write!(self, "cancel observation", self.held.record_cancel(refused.clone()));
            return Ok(());
        }
        held_write!(self, "cancel intent", self.held.record_cancel(CancelObservation::Sending));
        let outcome = self.ports.agent.cancel(&target, &remote, intent.request_id.clone()).await;
        let observed = match &outcome {
            CancelOutcome::RequestAccepted => CancelObservation::RequestAccepted,
            CancelOutcome::ConfirmedCanceled => CancelObservation::ConfirmedCanceled,
            CancelOutcome::AlreadyFinished(outcome) => CancelObservation::AlreadyFinished { outcome: *outcome },
            CancelOutcome::Rejected { code } => CancelObservation::Rejected { code: code.clone() },
            CancelOutcome::OutcomeUnknown { .. } => CancelObservation::OutcomeUnknown,
        };
        held_write!(self, "cancel observation", self.held.record_cancel(observed.clone()));
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
    async fn end_already_finished(&mut self, finished: FinalDispatchOutcome) -> Flow {
        if self.spec().capabilities.query_by_task {
            self.last_polled = None;
            self.poll_remote().await?;
        }
        if self.ended {
            return Ok(());
        }
        // The task could not be read: report what the cancel answer said.
        match finished {
            FinalDispatchOutcome::Completed => {
                let payload = Payload::Inline(serde_json::json!({ "state": "completed" }));
                if self.report(AgentEventKind::Final, None, payload).await? == Admitted::Yes {
                    self.finish(finished, "already_finished").await?;
                }
                Ok(())
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
